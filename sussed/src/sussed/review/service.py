"""Review workflow service helpers."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
from loguru import logger
from sqlalchemy import or_, select

from sussed.db.models import Listing, ListingReview, ListingStatus, PriceHistory, VibeCheck
from sussed.review.models import PreparedListingReview, ReviewResultInput, ReviewVibe

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

DEFAULT_STALE_AFTER_DAYS = 30
POA_PRICE_THRESHOLD = 10  # Anything ≤ this CZK is treated as "Price on Request"


def _utcnow() -> datetime:
    """Return a naive UTC timestamp for existing database columns."""
    return datetime.now(UTC).replace(tzinfo=None)


def map_review_vibe(vibe: ReviewVibe) -> VibeCheck:
    """Map review payload vibe to the database enum."""
    return {
        ReviewVibe.PEAK: VibeCheck.PEAK,
        ReviewVibe.VALID: VibeCheck.VALID,
        ReviewVibe.MID: VibeCheck.MID,
        ReviewVibe.SUS: VibeCheck.SUS,
    }[vibe]


def candidate_priority(
    listing: Listing, stale_after_days: int = DEFAULT_STALE_AFTER_DAYS
) -> tuple[int, int, int, int]:
    """Return a sortable review priority tuple where lower values are reviewed first."""
    stale_cutoff = _utcnow() - timedelta(days=stale_after_days)

    if listing.ai_reviewed_at is None:
        review_bucket = 0
    elif listing.last_price_change_at and listing.last_price_change_at > listing.ai_reviewed_at:
        review_bucket = 1
    elif listing.ai_reviewed_at < stale_cutoff:
        review_bucket = 2
    else:
        review_bucket = 3

    description_bucket = 0 if listing.description else 1
    price_bucket = listing.price_per_m2 or 999_999_999
    photo_bucket = -listing.image_count
    return (review_bucket, description_bucket, price_bucket, photo_bucket)


def _stable_input_hash(data: dict[str, Any]) -> str:
    """Hash review input so repeated reviews can be traced to their exact inputs."""
    encoded = json.dumps(data, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _derive_price_drop_signals(
    current_price: int, price_history: list[dict[str, Any]]
) -> tuple[int | None, int | None, bool]:
    """Compute initial/original/dropped-to-POA signals from price history.

    Returns:
        (initial_price, original_price, price_dropped_to_poa).
        - initial_price: first recorded price (chronologically).
        - original_price: last non-POA price before current — set only when
          the current price is POA.
        - price_dropped_to_poa: True iff current is POA and a prior non-POA
          price existed (a strong "seller hiding new price" signal).
    """
    if not price_history:
        return None, None, False

    # price_history from `get_price_history_payload` is sorted DESC by recorded_at.
    # We need the chronologically-first entry for `initial_price`.
    ordered = sorted(price_history, key=lambda h: h.get("recorded_at") or "")
    initial = ordered[0].get("price_czk") if ordered else None

    current_is_poa = current_price <= POA_PRICE_THRESHOLD
    original = None
    if current_is_poa:
        for entry in reversed(ordered):
            price = entry.get("price_czk")
            if price is not None and price > POA_PRICE_THRESHOLD:
                original = price
                break

    return initial, original, current_is_poa and original is not None


def prepare_review_payload_from_listing(
    listing: Listing,
    image_paths: list[str],
    detail_items: list[dict[str, Any]],
    price_history: list[dict[str, Any]],
) -> PreparedListingReview:
    """Build the JSON payload consumed by the AI review skill."""
    initial_price, original_price, dropped_to_poa = _derive_price_drop_signals(
        listing.price_czk, price_history
    )
    base_data: dict[str, Any] = {
        "listing_id": listing.id,
        "external_id": listing.external_id,
        "source": listing.source,
        "title": listing.title,
        "url": listing.url,
        "price_czk": listing.price_czk,
        "price_per_m2": listing.price_per_m2,
        "initial_price": initial_price,
        "original_price": original_price,
        "price_dropped_to_poa": dropped_to_poa,
        "listing_type": listing.listing_type.value if listing.listing_type else None,
        "city": listing.city,
        "district": listing.district,
        "address": listing.address,
        "apartment_type": listing.apartment_type,
        "area_m2": float(listing.area_m2) if listing.area_m2 is not None else None,
        "floor": listing.floor,
        "total_floors": listing.total_floors,
        "description": listing.description,
        "detail_items": detail_items,
        "features": listing.features or {},
        "raw_labels": listing.raw_labels or [],
        "image_urls": listing.image_urls or [],
        "image_paths": image_paths,
        "image_count": listing.image_count,
        "has_floor_plan": listing.has_floor_plan,
        "has_video": listing.has_video,
        "has_3d_tour": listing.has_3d_tour,
        "price_history": price_history,
        "current_ai_score": listing.ai_score,
        "current_ai_reviewed_at": listing.ai_reviewed_at,
        "heuristic_notes": [],
    }
    base_data["input_hash"] = _stable_input_hash(base_data)
    return PreparedListingReview.model_validate(base_data)


async def get_review_candidates(
    session: AsyncSession,
    limit: int,
    city: str | None = None,
    stale_after_days: int = DEFAULT_STALE_AFTER_DAYS,
    max_age_days: int | None = None,
    min_quick_score: int | None = None,
    order_by_recent: bool = False,
) -> list[Listing]:
    """Get active listings ordered by review priority.

    If ``max_age_days`` is set, only include listings whose ``first_seen_at`` is within
    that many days.

    If ``min_quick_score`` is set, only include listings whose hunt quick score
    (``ai_analysis->>'score'``) is at least that value.

    If ``order_by_recent`` is True, sort by ``first_seen_at DESC`` instead of the default
    review-priority heuristic.
    """
    if limit <= 0:
        return []

    from sqlalchemy import cast, desc
    from sqlalchemy.types import Integer

    conditions = [Listing.status == ListingStatus.ACTIVE]
    if city:
        conditions.append(Listing.city.ilike(f"%{city}%"))
    if max_age_days is not None:
        freshness_cutoff = _utcnow() - timedelta(days=max_age_days)
        conditions.append(Listing.first_seen_at >= freshness_cutoff)
    if min_quick_score is not None:
        conditions.append(
            cast(Listing.ai_analysis.op("->>")("score"), Integer) >= min_quick_score
        )

    stale_cutoff = _utcnow() - timedelta(days=stale_after_days)
    conditions.append(
        or_(
            Listing.ai_reviewed_at.is_(None),
            Listing.last_price_change_at > Listing.ai_reviewed_at,
            Listing.ai_reviewed_at < stale_cutoff,
        )
    )

    stmt = select(Listing).where(*conditions)
    if order_by_recent:
        stmt = stmt.order_by(desc(Listing.first_seen_at)).limit(limit)
        result = await session.execute(stmt)
        return list(result.scalars().all())

    result = await session.execute(stmt.limit(limit * 5))
    listings = list(result.scalars().all())
    listings.sort(
        key=lambda listing: candidate_priority(listing, stale_after_days=stale_after_days)
    )
    return listings[:limit]


async def get_reviewed_picks(
    session: AsyncSession,
    *,
    include_unreviewed: bool = False,
    district: str | None = None,
    min_score: int | None = None,
    max_age_days: int | None = None,
    limit: int = 20,
) -> list[Listing]:
    """Get active listings, optionally filtered by AI review status."""
    from datetime import timedelta

    conditions = [Listing.status == ListingStatus.ACTIVE]

    if not include_unreviewed:
        conditions.append(Listing.ai_reviewed_at.isnot(None))

    if district:
        conditions.append(Listing.district.ilike(f"%{district}%"))

    if min_score is not None:
        conditions.append(Listing.ai_score >= min_score)

    if max_age_days is not None:
        cutoff = _utcnow() - timedelta(days=max_age_days)
        conditions.append(Listing.first_seen_at >= cutoff)

    stmt = select(Listing).where(*conditions)
    stmt = stmt.order_by(
        Listing.ai_score.desc().nulls_last(),
        Listing.price_per_m2.asc(),
    ).limit(limit)

    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_price_history_payload(
    session: AsyncSession, listing_id: UUID
) -> list[dict[str, Any]]:
    """Return listing price history as JSON-safe dictionaries."""
    result = await session.execute(
        select(PriceHistory)
        .where(PriceHistory.listing_id == listing_id)
        .order_by(PriceHistory.recorded_at.desc())
    )
    return [
        {
            "price_czk": row.price_czk,
            "price_per_m2": row.price_per_m2,
            "change_type": row.change_type,
            "change_amount": row.change_amount,
            "change_percent": float(row.change_percent) if row.change_percent is not None else None,
            "recorded_at": row.recorded_at.isoformat(),
        }
        for row in result.scalars().all()
    ]


async def download_listing_images(
    image_urls: list[str],
    destination_dir: Path | str,
    limit: int = 5,
) -> list[str]:
    """Download listing images for review and return local file paths.

    Each image is written atomically: bytes are first streamed to a ``*.tmp``
    sibling, then ``Path.replace`` swaps it into the final ``image-N.<ext>``
    name in a single filesystem operation. Concurrent ``enrich``/``hunt``
    runs against the same listing therefore never observe a half-written
    cache entry, and a network or disk failure mid-write leaves no stale
    ``.tmp`` file behind.
    """
    destination_path = Path(destination_dir)
    destination_path.mkdir(parents=True, exist_ok=True)
    saved_paths: list[str] = []

    async with httpx.AsyncClient(timeout=30.0) as client:
        for index, url in enumerate(image_urls[:limit], start=1):
            suffix = Path(url.split("?", 1)[0]).suffix or ".jpg"
            path = destination_path / f"image-{index}{suffix}"
            if path.exists() and path.stat().st_size > 0:
                saved_paths.append(str(path))
                continue

            response = await client.get(url)
            response.raise_for_status()

            tmp_path = destination_path / f"image-{index}{suffix}.tmp"
            try:
                tmp_path.write_bytes(response.content)
                tmp_path.replace(path)
            finally:
                tmp_path.unlink(missing_ok=True)
            saved_paths.append(str(path))

    return saved_paths


def _natural_sort_key(path: Path) -> tuple[Any, ...]:
    """Return a key that sorts ``image-2.jpg`` before ``image-10.jpg``."""
    parts = re.split(r"(\d+)", path.name)
    return tuple(int(part) if part.isdigit() else part for part in parts)


def list_cached_image_paths(
    cache_root: Path | str,
    listing_id: UUID | str,
    limit: int,
) -> list[str]:
    """Return cached image paths for a listing, sorted naturally.

    Args:
        cache_root: Root directory holding per-listing image subdirectories.
        listing_id: Listing UUID (or its string form) used as the subdirectory name.
        limit: Maximum number of paths to return. ``0`` returns an empty list.

    Returns:
        A deterministic, natural-sorted list of absolute string paths to existing
        non-empty image files. If the cache directory does not exist, returns ``[]``.
    """
    if limit <= 0:
        return []

    listing_dir = Path(cache_root) / str(listing_id)
    if not listing_dir.is_dir():
        return []

    candidates = sorted(
        (entry for entry in listing_dir.iterdir() if entry.is_file()),
        key=_natural_sort_key,
    )
    paths: list[str] = []
    for entry in candidates:
        try:
            if entry.stat().st_size <= 0:
                continue
        except OSError:
            continue
        paths.append(str(entry))
        if len(paths) >= limit:
            break
    return paths


async def save_listing_review(
    session: AsyncSession,
    listing: Listing,
    review_input: ReviewResultInput,
) -> ListingReview:
    """Insert a versioned review and update denormalized latest-review fields."""
    vibe = map_review_vibe(review_input.vibe)
    review = ListingReview(
        listing_id=listing.id,
        reviewer_type="skill",
        reviewer_name=review_input.reviewer_name,
        reviewer_model=review_input.reviewer_model,
        reviewer_session=review_input.reviewer_session,
        score=review_input.score,
        vibe=vibe,
        confidence=Decimal(str(review_input.confidence)),
        recommendation=review_input.recommendation,
        score_reason=review_input.score_reason,
        summary=review_input.summary,
        red_flags=review_input.red_flags,
        yellow_flags=review_input.yellow_flags,
        highlights=review_input.highlights,
        hidden_costs=review_input.hidden_costs,
        parking_price=review_input.parking_price,
        parking_included=review_input.parking_included,
        usable_area_m2=(
            Decimal(str(review_input.usable_area_m2))
            if review_input.usable_area_m2 is not None
            else None
        ),
        photo_observations=review_input.photo_observations,
        input_hash=review_input.input_hash,
        raw_review=review_input.raw_review,
        reviewed_at=review_input.reviewed_at,
    )
    session.add(review)
    await session.flush()

    listing.ai_score = review.score
    listing.ai_vibe = review.vibe
    listing.ai_summary = review.summary
    listing.ai_reviewed_at = review.reviewed_at
    listing.ai_review_id = review.id
    listing.vibe_check = review.vibe
    listing.ai_analysis = {
        "score": review.score,
        "vibe": review.vibe.value,
        "confidence": float(review.confidence) if review.confidence is not None else None,
        "recommendation": review.recommendation,
        "score_reason": review.score_reason,
        "summary": review.summary,
        "red_flags": review.red_flags or [],
        "yellow_flags": review.yellow_flags or [],
        "highlights": review.highlights or [],
        "hidden_costs": review.hidden_costs or {},
        "parking_price": review.parking_price,
        "parking_included": review.parking_included,
        "usable_area_m2": float(review.usable_area_m2)
        if review.usable_area_m2 is not None
        else None,
        "photo_observations": review.photo_observations or [],
        "reviewed_at": review.reviewed_at.isoformat(),
        "review_id": str(review.id),
        "reviewer_name": review.reviewer_name,
        "reviewer_model": review.reviewer_model,
    }
    listing.updated_at = _utcnow()
    session.add(listing)
    logger.info(f"Saved AI review for listing {listing.id}: {review.score} ({review.vibe.value})")
    return review
