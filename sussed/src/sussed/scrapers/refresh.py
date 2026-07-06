"""Refresh existing active listings: detect removals + track updates."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import httpx
from loguru import logger
from sqlmodel import select

from sussed.db.connection import get_session, init_db
from sussed.db.models import Listing, ListingStatus
from sussed.db.operations import apply_price_change
from sussed.scrapers.sreality import (
    SrealityScraper,
    parse_v1_source_date,
    set_features_from_v1_detail,
)

_GONE_STATUSES = {404, 410}


async def run_refresh(
    *,
    source: str = "sreality",
    city: str | None = None,
    limit: int = 100,
    stale_days: int | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    """Re-check active listings: mark gone (404/410) as removed, refresh price/details.

    Args:
        source: Source to refresh (e.g., ``"sreality"``).
        city: Optional city filter (case-insensitive, substring match).
        limit: Maximum number of listings to re-check.
        stale_days: Only listings whose ``last_seen_at`` is older than this many days.
        dry_run: If True, roll back all changes at the end (nothing is persisted).

    Returns:
        Stats dict with keys ``checked``, ``removed``, ``price_changes``, ``updated``,
        ``errors``.
    """
    await init_db()
    now = datetime.utcnow()
    stats: dict[str, int] = {
        "checked": 0,
        "removed": 0,
        "price_changes": 0,
        "updated": 0,
        "errors": 0,
    }

    async with get_session() as session:
        stmt = select(Listing).where(
            Listing.status == ListingStatus.ACTIVE,
            Listing.source == source,
        )
        if city:
            stmt = stmt.where(Listing.city.ilike(f"%{city}%"))
        if stale_days is not None:
            stmt = stmt.where(Listing.last_seen_at <= now - timedelta(days=stale_days))
        stmt = stmt.order_by(Listing.last_seen_at.asc()).limit(limit)
        listings = list((await session.execute(stmt)).scalars().all())

        scraper = SrealityScraper()
        async with httpx.AsyncClient() as client:
            for listing in listings:
                try:
                    detail = await scraper.fetch_listing_details(
                        client, int(listing.external_id), raise_on_gone=True
                    )
                except httpx.HTTPStatusError as err:
                    if err.response.status_code in _GONE_STATUSES:
                        listing.status = ListingStatus.REMOVED
                        listing.updated_at = now
                        stats["removed"] += 1
                    else:
                        logger.warning(f"HTTP error refreshing {listing.external_id}: {err}")
                        stats["errors"] += 1
                    stats["checked"] += 1
                    continue
                except httpx.RequestError as err:
                    logger.warning(f"Network error refreshing {listing.external_id}: {err}")
                    stats["errors"] += 1
                    stats["checked"] += 1
                    continue

                stats["checked"] += 1
                if detail is None:
                    stats["errors"] += 1
                    continue

                new_price = detail.price_czk if detail.price_czk is not None else detail.price
                new_ppm2 = int(detail.price_czk_m2) if detail.price_czk_m2 is not None else None
                if new_price and await apply_price_change(
                    session, listing, int(new_price), new_ppm2
                ):
                    stats["price_changes"] += 1

                if detail.advert_description:
                    listing.description = detail.advert_description
                api_date = parse_v1_source_date(detail)
                if api_date and (
                    listing.updated_at_source is None or api_date < listing.updated_at_source
                ):
                    listing.updated_at_source = api_date
                set_features_from_v1_detail(listing, detail)
                listing.last_seen_at = now
                listing.updated_at = now
                stats["updated"] += 1

                if stats["checked"] % 50 == 0 and not dry_run:
                    await session.commit()

        if dry_run:
            await session.rollback()
        else:
            await session.commit()

    return stats


def refresh_sync(**kwargs: object) -> dict[str, int]:
    """Synchronous wrapper for ``run_refresh``.

    Args:
        **kwargs: Passed through to :func:`run_refresh`.

    Returns:
        Stats dict from :func:`run_refresh`.
    """
    return asyncio.run(run_refresh(**kwargs))  # type: ignore[arg-type]
