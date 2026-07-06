"""
Main scraper orchestration 🎯

Ties together the scraper, database, and logging.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING

import httpx
from loguru import logger

from sussed.db.connection import get_session, init_db
from sussed.db.operations import (
    create_scrape_run,
    update_scrape_run,
    upsert_listing_from_sreality,
)
from sussed.dedup.detector import check_listing
from sussed.scrapers.sreality import SrealityScraper

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from sussed.db.models import Listing


async def run_scrape(
    city: str = "brno",
    listing_type: str = "sale",
    property_type: str = "apartment",
    max_pages: int | None = None,
    max_age: str | None = None,
) -> dict:
    """
    Run a full scrape and store results in database.

    Args:
        city: City to scrape (e.g., "brno", "praha")
        listing_type: "sale" or "rent"
        property_type: "apartment", "house", "cottage", or "garden"
        max_pages: Maximum pages to scrape (None = all)
        max_age: Filter by listing age - "day", "week", "month" or None for all

    Returns:
        Dict with scrape statistics
    """
    age_info = f", max_age={max_age}" if max_age else ""
    logger.info(f"🕷️ Starting scrape for {city} - {listing_type} {property_type}s{age_info}")
    start_time = datetime.utcnow()

    # Initialize database
    await init_db()

    # Stats
    stats = {
        "listings_found": 0,
        "listings_new": 0,
        "listings_updated": 0,
        "price_changes": 0,
        "errors": 0,
        "duplicates_flagged": 0,
    }

    scraper = SrealityScraper()

    async with get_session() as session:
        # Create scrape run record
        run = await create_scrape_run(session, "sreality", city)
        await session.commit()

        try:
            async with httpx.AsyncClient() as dedup_client:
                async for estate in scraper.scrape(
                    city=city,
                    listing_type=listing_type,
                    property_type=property_type,
                    max_pages=max_pages,
                    max_age=max_age,
                ):
                    try:
                        listing, is_new, price_changed = await upsert_listing_from_sreality(
                            session,
                            estate,
                            city_override=city.title(),  # Normalize city name
                        )

                        stats["listings_found"] += 1
                        if is_new:
                            stats["listings_new"] += 1
                            await _dedup_new_listing(session, listing, scraper, dedup_client, stats)
                        else:
                            stats["listings_updated"] += 1
                        if price_changed:
                            stats["price_changes"] += 1

                        # Commit every 50 listings to avoid huge transactions
                        if stats["listings_found"] % 50 == 0:
                            await session.commit()
                            logger.info(f"Progress: {stats['listings_found']} listings processed")

                    except Exception as e:
                        logger.error(f"Error processing listing {estate.hash_id}: {e}")
                        stats["errors"] += 1

            # Final commit
            await session.commit()

            # Update scrape run
            await update_scrape_run(
                session,
                run,
                listings_found=stats["listings_found"],
                listings_new=stats["listings_new"],
                listings_updated=stats["listings_updated"],
                price_changes=stats["price_changes"],
                errors=stats["errors"],
                status="completed",
            )
            await session.commit()

        except Exception as e:
            logger.error(f"Scrape failed: {e}")
            await update_scrape_run(session, run, errors=1, status="failed")
            await session.commit()
            raise

    duration = (datetime.utcnow() - start_time).total_seconds()

    logger.info(
        f"✅ Scrape completed in {duration:.1f}s!\n"
        f"   Found: {stats['listings_found']}\n"
        f"   New: {stats['listings_new']}\n"
        f"   Updated: {stats['listings_updated']}\n"
        f"   Price changes: {stats['price_changes']}\n"
        f"   Duplicates flagged: {stats['duplicates_flagged']}\n"
        f"   Errors: {stats['errors']}"
    )

    return stats


async def _dedup_new_listing(
    session: AsyncSession,
    listing: Listing,
    scraper: SrealityScraper,
    client: httpx.AsyncClient,
    stats: dict[str, int],
) -> None:
    """Run non-destructive duplicate detection for a newly-ingested listing.

    Uses a SAVEPOINT so any dedup failure (network, DB) rolls back ONLY the
    dedup work and never poisons the surrounding scrape transaction.

    The flush is intentionally OUTSIDE the dedup-isolation try so real
    ingest/flush errors propagate to the caller's per-listing handler rather
    than being masked as a harmless "dedup failed" warning.

    Args:
        session: The active database session.
        listing: The newly-inserted listing to check.
        scraper: SrealityScraper instance (rate limiter is shared).
        client: Shared httpx client for detail fetches.
        stats: Mutable stats dict; ``duplicates_flagged`` is incremented on match.
    """
    external_id = listing.external_id  # capture before a savepoint rollback can expire the ORM object
    # CRITICAL: flush the new listing INSERT into the OUTER transaction before
    # the savepoint. This is ingest work, so let its errors propagate to the
    # caller's per-listing handler rather than masking them as a dedup failure.
    await session.flush()
    try:
        async with session.begin_nested():
            match = await check_listing(
                session, listing, scraper=scraper, client=client, allow_fetch=True
            )
        if match is not None:
            stats["duplicates_flagged"] += 1
    except Exception as exc:  # dedup must never abort a scrape
        logger.warning(f"Dedup check failed for listing {external_id}: {exc}")


def scrape_sync(
    city: str = "brno",
    listing_type: str = "sale",
    property_type: str = "apartment",
    max_pages: int | None = None,
    max_age: str | None = None,
) -> dict:
    """Synchronous wrapper for run_scrape."""
    return asyncio.run(run_scrape(city, listing_type, property_type, max_pages, max_age))
