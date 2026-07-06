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
                stats["checked"] += 1

                try:
                    hash_id = int(listing.external_id)
                except (TypeError, ValueError):
                    logger.warning(
                        f"Skipping {listing.id}: non-integer external_id {listing.external_id!r}"
                    )
                    stats["errors"] += 1
                    continue

                try:
                    detail = await scraper.fetch_listing_details(
                        client, hash_id, raise_on_gone=True
                    )
                except httpx.HTTPStatusError as err:
                    if err.response.status_code in _GONE_STATUSES:
                        listing.status = ListingStatus.REMOVED
                        listing.updated_at = now
                        stats["removed"] += 1
                    else:
                        logger.warning(f"HTTP error refreshing {listing.external_id}: {err}")
                        stats["errors"] += 1
                    continue
                except httpx.RequestError as err:
                    logger.warning(f"Network error refreshing {listing.external_id}: {err}")
                    stats["errors"] += 1
                    continue

                if detail is None:
                    stats["errors"] += 1
                    continue

                # Isolate each listing's DB mutations in a SAVEPOINT so one
                # malformed detail (bad price/features) can neither abort the run
                # nor leave a half-applied row in the batch commit.
                try:
                    async with session.begin_nested():
                        # Mirror upsert's price normalization: missing/falsy -> 0.
                        # 0 is a meaningful "dropped to POA" value and MUST persist.
                        raw_price = (
                            detail.price_czk if detail.price_czk is not None else detail.price
                        )
                        new_price = int(raw_price or 0)
                        new_ppm2 = (
                            int(detail.price_czk_m2) if detail.price_czk_m2 is not None else None
                        )
                        price_changed = await apply_price_change(
                            session, listing, new_price, new_ppm2
                        )
                        if detail.advert_description:
                            listing.description = detail.advert_description
                        api_date = parse_v1_source_date(detail)
                        if api_date and (
                            listing.updated_at_source is None
                            or api_date < listing.updated_at_source
                        ):
                            listing.updated_at_source = api_date
                        set_features_from_v1_detail(listing, detail)
                        listing.last_seen_at = now
                        listing.updated_at = now
                except Exception as exc:  # one bad row must not abort the whole run
                    logger.warning(f"Failed to apply refresh for {listing.external_id}: {exc}")
                    stats["errors"] += 1
                    continue

                stats["updated"] += 1
                if price_changed:
                    stats["price_changes"] += 1

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
