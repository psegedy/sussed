"""Fetching and persistence helpers for apartment hunts."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from sussed.hunt.llm_analyzer import ListingAnalysis


async def fetch_description(
    listing_id: str,
    external_id: str,
    image_urls: list[str] | None = None,
    *,
    image_cache_dir: str,
    image_cache_limit: int,
    gone_error_cls: type[Exception],
) -> tuple[str | None, str | None]:
    """Fetch v1 description/detail metadata and pre-warm the photo cache.

    Returns:
        Tuple of (description, source_date_iso) - either can be None.

    Raises:
        ListingGoneError: If the listing returns 410 Gone (sold/removed).
    """
    import httpx

    from sussed.scrapers.sreality import SrealityScraper, parse_v1_source_date

    try:
        scraper = SrealityScraper()
        async with httpx.AsyncClient() as client:
            hash_id = int(external_id)
            detail = await scraper.fetch_listing_details(client, hash_id)

            if not detail:
                return None, None

            api_date = parse_v1_source_date(detail)
            source_date_iso = api_date.isoformat() if api_date else None
            await save_v1_detail_metadata(listing_id, detail, api_date)

            if image_urls:
                await cache_listing_images(
                    listing_id,
                    image_urls,
                    image_cache_dir=image_cache_dir,
                    image_cache_limit=image_cache_limit,
                )

            return detail.advert_description, source_date_iso
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 410:
            await mark_listing_sold(listing_id)
            raise gone_error_cls(listing_id, external_id) from e
        logger.warning(f"Failed to fetch description for {listing_id}: {e}")
        return None, None
    except Exception as e:
        logger.warning(f"Failed to fetch description for {listing_id}: {e}")
        return None, None


async def cache_listing_images(
    listing_id: str,
    image_urls: list[str],
    *,
    image_cache_dir: str,
    image_cache_limit: int,
) -> None:
    """Download photos into the shared image cache, swallowing per-listing errors.

    Errors are logged but never abort the hunt. The cache layout matches
    ``sussed review prepare``: ``.sussed/image-cache/<listing-id>/image-N<ext>``.
    """
    from pathlib import Path

    from sussed.review.service import download_listing_images

    destination = Path(image_cache_dir) / str(listing_id)
    try:
        saved = await download_listing_images(
            image_urls=image_urls,
            destination_dir=destination,
            limit=image_cache_limit,
        )
        if saved:
            logger.debug(f"Cached {len(saved)} image(s) for {listing_id}")
    except Exception as err:  # log and move on, don't kill the hunt
        logger.warning(f"Failed to cache images for {listing_id}: {err}")


async def save_description(listing_id: str, description: str) -> None:
    """Save fetched description to database."""
    from sqlmodel import select

    from sussed.db.connection import get_session
    from sussed.db.models import Listing

    async with get_session() as session:
        stmt = select(Listing).where(Listing.id == listing_id)
        result = await session.execute(stmt)
        listing = result.scalar_one_or_none()

        if listing:
            listing.description = description
            session.add(listing)
            await session.commit()
            logger.debug(f"Saved description for {listing_id}")


async def save_source_date(listing_id: str, date_str: str) -> None:
    """Save a source date, accepting legacy Czech or v1 ISO date strings."""
    from datetime import datetime as dt

    api_date = None
    for date_format in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%d.%m.%Y"):
        try:
            api_date = dt.strptime(date_str, date_format)
            break
        except ValueError:
            continue
    if api_date is None:
        return

    await save_v1_detail_metadata(listing_id, None, api_date)


async def save_v1_detail_metadata(
    listing_id: str,
    detail: object | None,
    api_date: object | None,
) -> None:
    """Save v1 detail-only listing fields into the database."""
    from datetime import datetime as dt
    from typing import TYPE_CHECKING, cast

    from sqlmodel import select

    from sussed.db.connection import get_session
    from sussed.db.models import Listing
    from sussed.scrapers.sreality import set_features_from_v1_detail

    if TYPE_CHECKING:
        from sussed.models.sreality import SrealityV1Detail

    async with get_session() as session:
        stmt = select(Listing).where(Listing.id == listing_id)
        result = await session.execute(stmt)
        listing = result.scalar_one_or_none()

        if not listing:
            return

        if isinstance(api_date, dt) and (
            listing.updated_at_source is None or api_date < listing.updated_at_source
        ):
            listing.updated_at_source = api_date
            logger.debug(f"Saved source date {api_date.date()} for {listing_id}")

        if detail is not None:
            set_features_from_v1_detail(listing, cast("SrealityV1Detail", detail))

        session.add(listing)
        await session.commit()


async def mark_listing_sold(listing_id: str) -> None:
    """Mark a listing as sold/removed in the database."""
    from sqlmodel import select

    from sussed.db.connection import get_session
    from sussed.db.models import Listing, ListingStatus

    async with get_session() as session:
        stmt = select(Listing).where(Listing.id == listing_id)
        result = await session.execute(stmt)
        listing = result.scalar_one_or_none()

        if listing:
            listing.status = ListingStatus.SOLD
            session.add(listing)
            await session.commit()
            logger.info(f"Marked listing {listing_id} as SOLD")


async def llm_analyze(llm_analyzer: Any, listing: dict[str, Any]) -> ListingAnalysis | None:
    """
    Analyze listing with LLM for deep natural language understanding.

    THIS IS THE REAL AI! 🧠

    Returns ListingAnalysis with score adjustment and insights.
    """
    if not llm_analyzer or not listing.get("description"):
        return None
    try:
        result = await llm_analyzer.analyze_listing(
            title=listing.get("title", "Unknown"),
            description=listing["description"],
            price_czk=listing.get("price_czk", 0),
            area_m2=listing.get("area_m2"),
            apartment_type=listing.get("apartment_type"),
            district=listing.get("district"),
            features=listing.get("features"),
        )
        return result
    except Exception as e:
        logger.warning(f"LLM analysis failed: {e}")
        return None


async def save_score(listing_id: str, score_result: dict[str, Any]) -> None:
    """Save the score to database."""
    from sqlmodel import select

    from sussed.db.connection import get_session
    from sussed.db.models import Listing, VibeCheck

    async with get_session() as session:
        stmt = select(Listing).where(Listing.id == listing_id)
        result = await session.execute(stmt)
        listing = result.scalar_one_or_none()

        if not listing:
            return

        score = score_result["score"]

        # Map score to vibe check
        if score >= 800:
            vibe = VibeCheck.PEAK
        elif score >= 500:
            vibe = VibeCheck.VALID
        elif score >= 300:
            vibe = VibeCheck.MID
        else:
            vibe = VibeCheck.SUS

        # Hunt and AI review share the `ai_analysis` JSONB column. Once an AI
        # review has populated `ai_analysis` (signalled by `ai_reviewed_at`),
        # do NOT overwrite it with the lightweight hunt score — the AI review
        # carries far richer structured data (flags, hidden costs, photo notes,
        # etc.). Hunt's `vibe_check` (cheap, derivative) is still refreshed so
        # listings react to scoring config changes, but the full analysis is
        # preserved. Hunt's quick score is also kept under `_hunt_score` for
        # later filtering (e.g. `--min-quick-score`).
        if listing.ai_reviewed_at is None:
            listing.ai_analysis = score_result
        else:
            existing = dict(listing.ai_analysis or {})
            existing["_hunt_score"] = score
            existing["_hunt_scored_at"] = score_result.get("scored_at")
            listing.ai_analysis = existing

        listing.vibe_check = vibe

        session.add(listing)
        await session.commit()
