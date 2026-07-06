"""DB-aware duplicate/relisting detection orchestration."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from math import cos, inf, radians
from typing import TYPE_CHECKING

import httpx
from loguru import logger
from sqlmodel import select

from sussed.db.models import Listing, ListingStatus, PropertyCategory
from sussed.dedup.matcher import DedupConfig, DedupListing, DuplicateMatch, haversine_m, score_pair
from sussed.scrapers.sreality import parse_v1_source_date, set_features_from_v1_detail

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.ext.asyncio import AsyncSession

    from sussed.models.sreality import SrealityV1Detail
    from sussed.scrapers.sreality import SrealityScraper


def _utcnow() -> datetime:
    """Return naive UTC datetime matching the rest of the app's DB timestamps."""
    return datetime.now(UTC).replace(tzinfo=None)


def _enum_value(value: object) -> str:
    """Return enum value or plain string for SQLModel enum fields."""
    return value.value if hasattr(value, "value") else str(value)


def _float_or_none(value: Decimal | int | float | None) -> float | None:
    """Convert numeric DB values to floats for the pure matcher."""
    return None if value is None else float(value)


def listing_to_dedup(listing: Listing) -> DedupListing:
    """Map a database ``Listing`` to the pure matcher input shape."""
    return DedupListing(
        external_id=listing.external_id,
        source=listing.source,
        listing_type=_enum_value(listing.listing_type),
        property_category=_enum_value(listing.property_category),
        apartment_type=listing.apartment_type,
        city=listing.city,
        latitude=_float_or_none(listing.latitude),
        longitude=_float_or_none(listing.longitude),
        area_m2=_float_or_none(listing.area_m2),
        floor=listing.floor,
        title=listing.title,
        description=listing.description,
        image_urls=tuple(listing.image_urls or []),
        new_building=bool((listing.features or {}).get("new_building", False)),
        agency_name=listing.agency_name,
        price_czk=listing.price_czk,
        status=_enum_value(listing.status),
    )


async def find_candidates(
    session: AsyncSession,
    listing: Listing,
    config: DedupConfig | None = None,
    max_candidates: int = 8,
    older_than: Listing | None = None,
) -> list[Listing]:
    """Find cheaply-blocked candidate listings and rank likely relistings first.

    If ``older_than`` is given, only rows strictly older than that listing
    (by ``_older_key``) survive the filter before ranking and the cap are applied.
    When ``older_than`` is ``None`` the behaviour is unchanged: all blocked rows
    are ranked and capped.
    """
    dedup_config = config or DedupConfig()
    conditions = [
        Listing.id != listing.id,
        Listing.source == listing.source,
        Listing.listing_type == listing.listing_type,
        Listing.property_category == listing.property_category,
    ]

    if _is_apartment(listing) and listing.apartment_type:
        conditions.append(Listing.apartment_type == listing.apartment_type)
    if listing.city:
        conditions.append(Listing.city == listing.city)

    lat = _float_or_none(listing.latitude)
    lon = _float_or_none(listing.longitude)
    if lat is not None and lon is not None:
        dlat = dedup_config.gps_veto_m / 111_320
        cos_lat = cos(radians(lat))
        dlon = 180.0 if abs(cos_lat) < 1e-6 else dedup_config.gps_veto_m / (111_320 * abs(cos_lat))
        conditions.extend(
            [
                Listing.latitude.between(lat - dlat, lat + dlat),
                Listing.longitude.between(lon - dlon, lon + dlon),
            ]
        )

    result = await session.execute(select(Listing).where(*conditions))
    candidates = list(result.scalars().all())
    if older_than is not None:
        ref_key = _older_key(older_than)
        candidates = [c for c in candidates if _older_key(c) < ref_key]
    candidates.sort(key=lambda candidate: _candidate_rank_key(listing, candidate))
    return candidates[:max_candidates]


def _candidate_rank_key(
    listing: Listing, candidate: Listing
) -> tuple[float, int, float, int, datetime, uuid.UUID]:
    """Return cheap ranking signals for blocked candidates."""
    listing_lat = _float_or_none(listing.latitude)
    listing_lon = _float_or_none(listing.longitude)
    candidate_lat = _float_or_none(candidate.latitude)
    candidate_lon = _float_or_none(candidate.longitude)
    if listing_lat is None or listing_lon is None or candidate_lat is None or candidate_lon is None:
        distance = inf
    else:
        distance = haversine_m(listing_lat, listing_lon, candidate_lat, candidate_lon)

    # Among candidates at the same GPS, sort likely-twins (same floor) before known-different units.
    # When either floor is None, floor_incompatible=0 so behavior is unchanged vs no-floor data.
    floor_incompatible = (
        1
        if (
            listing.floor is not None
            and candidate.floor is not None
            and listing.floor != candidate.floor
        )
        else 0
    )

    listing_area = _float_or_none(listing.area_m2)
    candidate_area = _float_or_none(candidate.area_m2)
    area_diff = (
        inf
        if listing_area is None or candidate_area is None
        else abs(listing_area - candidate_area)
    )
    is_active = 1 if _enum_value(candidate.status) == ListingStatus.ACTIVE.value else 0
    return (distance, floor_incompatible, area_diff, is_active, candidate.first_seen_at, candidate.id)


async def check_listing(
    session: AsyncSession,
    listing: Listing,
    *,
    scraper: SrealityScraper | None = None,
    client: httpx.AsyncClient | None = None,
    config: DedupConfig | None = None,
    allow_fetch: bool = True,
    force: bool = False,
    same_run_window: timedelta = timedelta(hours=6),
) -> DuplicateMatch | None:
    """Check one listing for an older duplicate/relisting and flag it non-destructively."""
    if listing.duplicate_checked_at is not None and not force:
        return None

    now = _utcnow()
    dedup_config = config or DedupConfig()
    older_candidates = await find_candidates(session, listing, dedup_config, older_than=listing)

    if not older_candidates:
        listing.duplicate_of_id = None
        listing.duplicate_status = None
        listing.duplicate_confidence = None
        listing.duplicate_reasons = None
        listing.duplicate_checked_at = now
        return None

    if allow_fetch and scraper is not None and client is not None:
        if _needs_enrichment(listing):
            await _ensure_enriched(listing, scraper, client, now)
        for candidate in older_candidates:
            if (
                _needs_enrichment(candidate)
                and _enum_value(candidate.status) == ListingStatus.ACTIVE.value
            ):
                await _ensure_enriched(candidate, scraper, client, now)

    best_match: DuplicateMatch | None = None
    best_candidate: Listing | None = None
    current = listing_to_dedup(listing)
    for candidate in older_candidates:
        match = score_pair(current, listing_to_dedup(candidate), dedup_config)
        if match.status is None:
            continue
        if best_match is None or match.confidence > best_match.confidence:
            best_match = match
            best_candidate = candidate

    listing.duplicate_checked_at = now
    if best_match is None or best_candidate is None:
        listing.duplicate_of_id = None
        listing.duplicate_status = None
        listing.duplicate_confidence = None
        listing.duplicate_reasons = None
        return None

    final_status = best_match.status
    reasons = list(best_match.reasons)
    if (
        final_status == "duplicate"
        and _enum_value(listing.status) == ListingStatus.ACTIVE.value
        and _enum_value(best_candidate.status) == ListingStatus.ACTIVE.value
        and abs(listing.first_seen_at - best_candidate.first_seen_at) <= same_run_window
    ):
        final_status = "suspected"
        reasons.append("both first seen within same scrape window → capped to suspected")

    # duplicate_of_id always points directly to the chain root, so one hop is enough.
    root_id = best_candidate.duplicate_of_id or best_candidate.id
    listing.duplicate_of_id = root_id
    listing.duplicate_confidence = Decimal(str(round(best_match.confidence, 3)))
    listing.duplicate_status = final_status
    listing.duplicate_reasons = reasons

    return DuplicateMatch(status=final_status, confidence=best_match.confidence, reasons=reasons)


def _older_key(listing: Listing) -> tuple[datetime, datetime, uuid.UUID]:
    """Return deterministic relisting age key; smaller means older."""
    return (listing.first_seen_at, listing.created_at, listing.id)


def _is_apartment(listing: Listing) -> bool:
    """Return whether a listing is an apartment, tolerating enum or string values."""
    return _enum_value(listing.property_category) == PropertyCategory.APARTMENT.value


def _needs_enrichment(listing: Listing) -> bool:
    """Return whether scoring would benefit from sreality detail data."""
    return listing.description is None or (_is_apartment(listing) and listing.floor is None)


async def _ensure_enriched(
    row: Listing,
    scraper: SrealityScraper,
    client: httpx.AsyncClient,
    now: datetime,
) -> None:
    """Fetch sreality detail data for a row when available, swallowing network failures."""
    try:
        detail = await scraper.fetch_listing_details(client, int(row.external_id))
    except httpx.HTTPStatusError as err:
        if err.response.status_code == 410:
            row.status = ListingStatus.SOLD
            return
        logger.warning(f"HTTP error enriching listing {row.external_id}: {err}")
        return
    except httpx.RequestError as err:
        logger.warning(f"Network error enriching listing {row.external_id}: {err}")
        return

    if detail is None:
        return

    _apply_detail(row, detail, now)


def _apply_detail(row: Listing, detail: SrealityV1Detail, now: datetime) -> None:
    """Apply fetched sreality detail fields to a listing row."""
    if detail.advert_description:
        row.description = detail.advert_description

    source_date = parse_v1_source_date(detail)
    if source_date is not None:
        row.updated_at_source = (
            source_date
            if row.updated_at_source is None
            else min(row.updated_at_source, source_date)
        )

    set_features_from_v1_detail(row, detail)
    row.updated_at = now
