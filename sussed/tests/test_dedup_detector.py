from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlmodel import delete

from sussed.db.connection import close_db, get_session, init_db
from sussed.db.models import Listing, ListingStatus, ListingType, PropertyCategory
from sussed.dedup.detector import check_listing, find_candidates
from sussed.models.sreality import SrealityV1Detail, SrealityV1DetailResponse

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _listing(
    *,
    source: str,
    external_id: str,
    title: str = "Prodej bytu 2+kk 55 m²",
    description: str | None = "Krásný byt s dispozicí 2+kk a výbornou dostupností v Brně.",
    listing_type: ListingType = ListingType.SALE,
    property_category: PropertyCategory = PropertyCategory.APARTMENT,
    apartment_type: str | None = "2+kk",
    city: str = "Brno",
    latitude: Decimal | None = Decimal("49.2000000"),
    longitude: Decimal | None = Decimal("16.6000000"),
    area_m2: Decimal | None = Decimal("55"),
    floor: int | None = 3,
    status: ListingStatus = ListingStatus.ACTIVE,
    first_seen_at: datetime | None = None,
    created_at: datetime | None = None,
    duplicate_of_id=None,
) -> Listing:
    seen_at = first_seen_at or datetime(2026, 1, 1, 12, 0, 0)
    return Listing(
        source=source,
        external_id=external_id,
        url=f"https://example.test/{external_id}",
        title=title,
        description=description,
        price_czk=5_500_000,
        listing_type=listing_type,
        city=city,
        district="Brno-město",
        property_category=property_category,
        apartment_type=apartment_type,
        latitude=latitude,
        longitude=longitude,
        area_m2=area_m2,
        floor=floor,
        features={},
        raw_labels=[],
        image_urls=["https://img.example.test/shared.jpg"],
        image_count=1,
        has_floor_plan=False,
        has_video=False,
        has_3d_tour=False,
        agency_name="Test Reality",
        status=status,
        first_seen_at=seen_at,
        last_seen_at=seen_at,
        created_at=created_at or seen_at,
        updated_at=created_at or seen_at,
        duplicate_of_id=duplicate_of_id,
    )


async def _clean_source(source: str) -> None:
    await close_db()
    await init_db()
    async with get_session() as session:
        await session.execute(delete(Listing).where(Listing.source == source))


def _detail_fixture() -> SrealityV1Detail:
    data = json.loads((FIXTURE_DIR / "sreality_v1_detail_sample.json").read_text())
    return SrealityV1DetailResponse.model_validate(data).result


def _gone_error() -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://example.test/gone")
    response = httpx.Response(410, request=request)
    return httpx.HTTPStatusError("gone", request=request, response=response)


@pytest.mark.asyncio
async def test_find_candidates_blocks_and_ranks_same_listing_shape() -> None:
    source = "pytest-dedup-find-candidates"
    await _clean_source(source)

    async with get_session() as session:
        target = _listing(source=source, external_id="100")
        colocated = _listing(source=source, external_id="101", status=ListingStatus.REMOVED)
        different_type = _listing(source=source, external_id="102", listing_type=ListingType.RENT)
        different_apartment = _listing(source=source, external_id="103", apartment_type="3+kk")
        far_away = _listing(
            source=source,
            external_id="104",
            latitude=Decimal("49.3000000"),
            longitude=Decimal("16.7000000"),
        )
        session.add_all([target, colocated, different_type, different_apartment, far_away])
        await session.commit()

        candidates = await find_candidates(session, target)

    assert [candidate.external_id for candidate in candidates] == ["101"]


@pytest.mark.asyncio
async def test_check_listing_flags_true_relisting_and_is_idempotent() -> None:
    source = "pytest-dedup-true-relisting"
    await _clean_source(source)
    old_time = datetime(2026, 1, 1, 10, 0, 0)
    new_time = datetime(2026, 1, 3, 10, 0, 0)
    description = "Krásný byt 2+kk v Brně s balkonem, sklepem a výbornou dostupností do centra."

    async with get_session() as session:
        candidate = _listing(
            source=source,
            external_id="200",
            description=description,
            status=ListingStatus.REMOVED,
            first_seen_at=old_time,
        )
        listing = _listing(
            source=source,
            external_id="201",
            description=f"{description} Volný ihned.",
            first_seen_at=new_time,
        )
        session.add_all([candidate, listing])
        await session.commit()

        match = await check_listing(session, listing, allow_fetch=False)
        second_match = await check_listing(session, listing, allow_fetch=False)

    assert match is not None
    assert listing.duplicate_status == "duplicate"
    assert listing.duplicate_of_id == candidate.id
    assert listing.duplicate_confidence is not None
    assert second_match is None


@pytest.mark.asyncio
async def test_check_listing_marks_checked_when_no_candidate() -> None:
    source = "pytest-dedup-no-candidate"
    await _clean_source(source)

    async with get_session() as session:
        listing = _listing(source=source, external_id="300")
        session.add(listing)
        await session.commit()

        match = await check_listing(session, listing, allow_fetch=False)

    assert match is None
    assert listing.duplicate_checked_at is not None
    assert listing.duplicate_of_id is None


@pytest.mark.asyncio
async def test_check_listing_caps_same_run_active_duplicates_to_suspected() -> None:
    source = "pytest-dedup-same-run"
    await _clean_source(source)
    old_time = datetime(2026, 1, 1, 10, 0, 0)
    new_time = old_time + timedelta(hours=2)
    description = "Krásný byt 2+kk v Brně s balkonem, sklepem a výbornou dostupností do centra."

    async with get_session() as session:
        candidate = _listing(
            source=source, external_id="400", description=description, first_seen_at=old_time
        )
        listing = _listing(
            source=source,
            external_id="401",
            description=description,
            first_seen_at=new_time,
        )
        session.add_all([candidate, listing])
        await session.commit()

        match = await check_listing(session, listing, allow_fetch=False)

    assert match is not None
    assert listing.duplicate_status == "suspected"
    assert any("same scrape window" in reason for reason in (listing.duplicate_reasons or []))


@pytest.mark.asyncio
async def test_check_listing_fetches_missing_detail_and_marks_410_sold() -> None:
    source = "pytest-dedup-confirm-on-demand"
    await _clean_source(source)
    old_time = datetime(2026, 1, 1, 10, 0, 0)
    new_time = datetime(2026, 1, 3, 10, 0, 0)
    scraper = SimpleNamespace(
        fetch_listing_details=AsyncMock(side_effect=[_detail_fixture(), _gone_error()])
    )

    async with get_session() as session:
        candidate = _listing(
            source=source,
            external_id="500",
            description=None,
            floor=None,
            first_seen_at=old_time,
        )
        listing = _listing(
            source=source,
            external_id="501",
            description=None,
            floor=None,
            first_seen_at=new_time,
        )
        session.add_all([candidate, listing])
        await session.commit()

        async with httpx.AsyncClient() as client:
            await check_listing(session, listing, scraper=scraper, client=client, allow_fetch=True)

    assert scraper.fetch_listing_details.await_count == 2
    assert listing.description is not None
    assert listing.floor is not None
    assert candidate.status == ListingStatus.SOLD


@pytest.mark.asyncio
async def test_check_listing_resolves_duplicate_chain_root() -> None:
    source = "pytest-dedup-root-resolution"
    await _clean_source(source)
    old_time = datetime(2026, 1, 1, 10, 0, 0)
    mid_time = datetime(2026, 1, 2, 10, 0, 0)
    new_time = datetime(2026, 1, 3, 10, 0, 0)
    description = "Krásný byt 2+kk v Brně s balkonem, sklepem a výbornou dostupností do centra."

    async with get_session() as session:
        root = _listing(
            source=source,
            external_id="600",
            apartment_type="3+kk",
            first_seen_at=old_time,
        )
        candidate = _listing(
            source=source,
            external_id="601",
            description=description,
            first_seen_at=mid_time,
            duplicate_of_id=root.id,
        )
        listing = _listing(
            source=source,
            external_id="602",
            description=description,
            first_seen_at=new_time,
        )
        session.add_all([root, candidate, listing])
        await session.commit()

        match = await check_listing(session, listing, allow_fetch=False)

    assert match is not None
    assert listing.duplicate_of_id == root.id
    assert listing.duplicate_of_id != candidate.id


@pytest.mark.asyncio
async def test_find_candidates_older_than_filters_before_cap() -> None:
    """Regression: older-than filter must happen BEFORE the cap, not after.

    Without the fix, two newer listings with distance=0 fill max_candidates=1
    and the true older predecessor (slightly off GPS → distance > 0) is dropped.
    With the fix, newer listings are filtered out first, so the older one survives.
    """
    source = "pytest-dedup-older-than-cap"
    await _clean_source(source)

    # Reference listing
    ref_time = datetime(2026, 1, 15, 12, 0, 0)
    ref_lat = Decimal("49.2000000")
    ref_lon = Decimal("16.6000000")

    # Older predecessor: ~14 m off — still within gps_veto_m=150 m
    old_time = datetime(2025, 6, 1, 12, 0, 0)
    old_lat = Decimal("49.2001000")
    old_lon = Decimal("16.6001000")

    # Newer listings: exact same GPS → distance=0, so they rank ABOVE the older one
    newer_time_1 = datetime(2026, 1, 20, 12, 0, 0)
    newer_time_2 = datetime(2026, 1, 22, 12, 0, 0)

    async with get_session() as session:
        reference = _listing(
            source=source,
            external_id="700",
            latitude=ref_lat,
            longitude=ref_lon,
            first_seen_at=ref_time,
        )
        older_predecessor = _listing(
            source=source,
            external_id="701",
            latitude=old_lat,
            longitude=old_lon,
            first_seen_at=old_time,
            status=ListingStatus.REMOVED,
        )
        newer_1 = _listing(
            source=source,
            external_id="702",
            latitude=ref_lat,
            longitude=ref_lon,
            first_seen_at=newer_time_1,
        )
        newer_2 = _listing(
            source=source,
            external_id="703",
            latitude=ref_lat,
            longitude=ref_lon,
            first_seen_at=newer_time_2,
        )
        session.add_all([reference, older_predecessor, newer_1, newer_2])
        await session.commit()

        # With older_than: cap=1 must return the older predecessor, not a newer one
        filtered = await find_candidates(session, reference, max_candidates=1, older_than=reference)
        assert len(filtered) == 1, "expected exactly one older candidate after cap"
        assert filtered[0].external_id == "701", (
            f"expected older predecessor '701', got '{filtered[0].external_id}'"
        )

        # Without older_than: cap=1 returns a newer one (distance=0 ranks first)
        unfiltered = await find_candidates(session, reference, max_candidates=1, older_than=None)
        assert len(unfiltered) == 1
        assert unfiltered[0].external_id in {"702", "703"}, (
            "without older_than filter a distance=0 newer listing should win"
        )


@pytest.mark.asyncio
async def test_check_listing_clears_stale_fields_when_no_candidate() -> None:
    """Fix 1: stale duplicate metadata must be cleared when no older candidate qualifies."""
    source = "pytest-dedup-stale-clear"
    await _clean_source(source)

    async with get_session() as session:
        listing = _listing(source=source, external_id="800")
        # Seed with stale duplicate metadata (simulates a corrected false positive)
        listing.duplicate_of_id = uuid.uuid4()
        listing.duplicate_status = "suspected"
        listing.duplicate_confidence = Decimal("0.750")
        listing.duplicate_reasons = ["stale reason from previous run"]
        listing.duplicate_checked_at = datetime(2026, 1, 1, 10, 0, 0)
        session.add(listing)
        await session.commit()

        # force=True bypasses idempotency guard; no older candidate exists → no-match branch
        match = await check_listing(session, listing, allow_fetch=False, force=True)

    assert match is None
    assert listing.duplicate_of_id is None
    assert listing.duplicate_status is None
    assert listing.duplicate_confidence is None
    assert listing.duplicate_reasons is None
    assert listing.duplicate_checked_at is not None
    assert listing.duplicate_checked_at > datetime(2026, 1, 1, 10, 0, 0)


@pytest.mark.asyncio
async def test_find_candidates_floor_compatible_ranks_before_cap() -> None:
    """Fix 2: floor-compatible twin survives the cap over an older floor-incompatible candidate.

    Without Fix 2 the older (earlier first_seen_at) different-floor candidate wins because
    first_seen_at sorts before floor data in the ranking tuple.  With Fix 2 the
    floor_incompatible term pushes the different-floor candidate down so the same-floor twin
    ranks first and survives max_candidates=1.
    """
    source = "pytest-dedup-floor-rank"
    await _clean_source(source)

    ref_time = datetime(2026, 3, 1, 12, 0, 0)
    # diff-floor listing is OLDER (would rank first without Fix 2 due to first_seen_at)
    diff_floor_time = datetime(2026, 1, 1, 12, 0, 0)
    # same-floor twin is more recent (would lose cap without Fix 2)
    same_floor_time = datetime(2026, 2, 1, 12, 0, 0)

    lat = Decimal("49.2000000")
    lon = Decimal("16.6000000")

    async with get_session() as session:
        reference = _listing(
            source=source,
            external_id="900",
            floor=3,
            latitude=lat,
            longitude=lon,
            first_seen_at=ref_time,
        )
        same_floor_twin = _listing(
            source=source,
            external_id="901",
            floor=3,
            latitude=lat,
            longitude=lon,
            first_seen_at=same_floor_time,
            status=ListingStatus.REMOVED,
        )
        diff_floor_candidate = _listing(
            source=source,
            external_id="902",
            floor=5,
            latitude=lat,
            longitude=lon,
            first_seen_at=diff_floor_time,
            status=ListingStatus.REMOVED,
        )
        session.add_all([reference, same_floor_twin, diff_floor_candidate])
        await session.commit()

        candidates = await find_candidates(session, reference, max_candidates=1, older_than=reference)

    assert len(candidates) == 1, "expected exactly one older candidate after cap"
    assert candidates[0].external_id == "901", (
        f"expected same-floor twin '901' but got '{candidates[0].external_id}'; "
        "floor_incompatible must rank before first_seen_at in the sort key"
    )
