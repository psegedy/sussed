"""Build normalized data for the static Instagram-style feed."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sussed.feed.models import FeedContext, FeedData, FeedPost
from sussed.review.service import (
    _derive_price_drop_signals,
    get_price_histories_for_listings,
    get_recent_scored_listings,
    get_reviewed_picks,
)

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from sussed.db.models import Listing


def _utcnow() -> datetime:
    """Return a naive UTC timestamp for feed metadata."""
    return datetime.now(UTC).replace(tzinfo=None)


def _enum_value(value: Any) -> str | None:
    """Return enum ``.value`` when available, otherwise a string-ish value."""
    if value is None:
        return None
    enum_value = getattr(value, "value", value)
    return str(enum_value) if enum_value is not None else None


def _decimal_to_float(value: Any) -> float | None:
    """Convert Decimal-ish values to floats without exploding on missing values."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    """Coerce numeric JSON values to int, returning None for junk."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return int(stripped)
        except ValueError:
            return None
    return None


def _safe_float(value: Any) -> float | None:
    """Coerce numeric JSON values to float, returning None for junk."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_list(value: Any) -> list[str]:
    """Return a list of strings from JSON-ish data, or an empty list."""
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None]


def _safe_dict(value: Any) -> dict[str, Any]:
    """Return a dictionary from JSON-ish data, or an empty dict."""
    return value if isinstance(value, dict) else {}


def _summary_from_analysis(ai_analysis: dict[str, Any]) -> str | None:
    """Derive hunt-only summary from explicit summary or joined score reasons."""
    summary = ai_analysis.get("summary")
    if isinstance(summary, str) and summary:
        return summary

    reasons = _safe_list(ai_analysis.get("reasons"))
    return "; ".join(reasons) if reasons else None


def build_feed_post(listing: Listing, price_history: list[dict[str, Any]]) -> FeedPost:
    """Build one feed post from a listing and its price history.

    Args:
        listing: Database listing object. No database access is performed.
        price_history: Price history dictionaries sorted descending by ``recorded_at``.

    Returns:
        A normalized :class:`FeedPost` ready for JSON serialization.
    """
    ai_analysis = _safe_dict(listing.ai_analysis)
    is_reviewed = listing.ai_reviewed_at is not None
    initial_price, original_price, dropped_to_poa = _derive_price_drop_signals(
        listing.price_czk, price_history
    )

    change_direction = None
    last_change_amount = None
    last_change_percent = None
    if price_history and price_history[0].get("change_type") in ("increase", "decrease"):
        most_recent = price_history[0]
        change_direction = str(most_recent["change_type"])
        last_change_amount = _safe_int(most_recent.get("change_amount"))
        percent = _safe_float(most_recent.get("change_percent"))
        if percent is not None:
            last_change_percent = -percent if change_direction == "decrease" else percent

    score = (
        listing.ai_score if listing.ai_score is not None else _safe_int(ai_analysis.get("score"))
    )
    vibe = None
    if is_reviewed and listing.ai_vibe is not None:
        vibe = _enum_value(listing.ai_vibe)
    elif isinstance(ai_analysis.get("vibe"), str):
        vibe = ai_analysis.get("vibe")

    summary = listing.ai_summary if is_reviewed else _summary_from_analysis(ai_analysis)

    return FeedPost(
        id=str(listing.id),
        external_id=listing.external_id,
        source=listing.source,
        url=listing.url,
        title=listing.title,
        listing_type=_enum_value(listing.listing_type),
        property_category=_enum_value(listing.property_category),
        apartment_type=listing.apartment_type,
        area_m2=_decimal_to_float(listing.area_m2),
        floor=listing.floor,
        total_floors=listing.total_floors,
        city=listing.city,
        district=listing.district,
        address=listing.address,
        price_czk=listing.price_czk,
        price_per_m2=listing.price_per_m2,
        is_poa=listing.price_czk <= 10,
        initial_price=_safe_int(initial_price),
        original_price=_safe_int(original_price),
        last_change_amount=last_change_amount,
        last_change_percent=last_change_percent,
        change_direction=change_direction,
        dropped_to_poa=dropped_to_poa,
        price_change_count=sum(
            1 for entry in price_history if entry.get("change_type") in ("increase", "decrease")
        ),
        first_seen_at=listing.first_seen_at,
        source_updated_at=listing.updated_at_source,
        last_price_change_at=listing.last_price_change_at,
        ai_reviewed_at=listing.ai_reviewed_at,
        image_urls=_safe_list(listing.image_urls),
        image_count=listing.image_count,
        has_floor_plan=listing.has_floor_plan,
        has_video=listing.has_video,
        has_3d_tour=listing.has_3d_tour,
        score=score,
        is_reviewed=is_reviewed,
        vibe=vibe,
        summary=summary,
        recommendation=ai_analysis.get("recommendation"),
        confidence=_safe_float(ai_analysis.get("confidence")),
        pros=_safe_list(ai_analysis.get("highlights")),
        cons_red=_safe_list(ai_analysis.get("red_flags")),
        cons_yellow=_safe_list(ai_analysis.get("yellow_flags")),
        hidden_costs=_safe_dict(ai_analysis.get("hidden_costs")),
        parking_price=_safe_int(ai_analysis.get("parking_price")),
        parking_included=ai_analysis.get("parking_included"),
        usable_area_m2=_safe_float(ai_analysis.get("usable_area_m2")),
        agency_name=listing.agency_name,
    )


async def build_feed_data(
    session: AsyncSession,
    *,
    title: str,
    limit: int,
    fresh_days: int,
    district: str | None = None,
    min_score: int | None = None,
    property_type: str | None = None,
    include_unreviewed_in_picks: bool = False,
) -> tuple[FeedData, FeedContext]:
    """Build normalized feed data and generation context from database listings.

    Args:
        session: Async database session.
        title: Human-readable feed title.
        limit: Per-tab maximum listing count.
        fresh_days: Age window for the Fresh tab.
        district: Optional district filter.
        min_score: Optional minimum effective score.
        property_type: Optional property category filter.
        include_unreviewed_in_picks: Include hunt-only listings in AI Picks.

    Returns:
        Tuple of feed payload and metadata context.
    """
    ai_picks_listings = await get_reviewed_picks(
        session,
        include_unreviewed=include_unreviewed_in_picks,
        district=district,
        min_score=min_score,
        property_type=property_type,
        limit=limit,
    )
    fresh_listings = await get_recent_scored_listings(
        session,
        max_age_days=fresh_days,
        limit=limit,
        district=district,
        min_score=min_score,
        property_type=property_type,
    )

    listings_by_id: dict[str, Listing] = {}
    union_ids: list[UUID] = []
    for listing in [*ai_picks_listings, *fresh_listings]:
        listing_id = str(listing.id)
        if listing_id not in listings_by_id:
            listings_by_id[listing_id] = listing
            union_ids.append(listing.id)

    histories = await get_price_histories_for_listings(session, union_ids)
    posts = {
        listing_id: build_feed_post(listing, histories.get(listing_id, []))
        for listing_id, listing in listings_by_id.items()
    }

    feed_data = FeedData(
        posts=posts,
        ai_picks=[str(listing.id) for listing in ai_picks_listings],
        fresh=[str(listing.id) for listing in fresh_listings],
    )
    context = FeedContext(
        title=title,
        generated_at=_utcnow(),
        fresh_days=fresh_days,
        ai_picks_count=len(ai_picks_listings),
        fresh_count=len(fresh_listings),
        filters={
            "district": district,
            "min_score": min_score,
            "property_type": property_type,
            "limit": limit,
            "include_unreviewed_in_picks": include_unreviewed_in_picks,
        },
    )
    return feed_data, context
