"""
Main scraper orchestration 🎯

Ties together the scraper, database, and logging.
"""

import asyncio
from datetime import datetime

from loguru import logger

from sussed.db.connection import get_session, init_db
from sussed.db.operations import (
    create_scrape_run,
    update_scrape_run,
    upsert_listing_from_sreality,
)
from sussed.scrapers.sreality import SrealityScraper


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
        property_type: "apartment" or "house"
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
    }

    scraper = SrealityScraper()

    async with get_session() as session:
        # Create scrape run record
        run = await create_scrape_run(session, "sreality", city)
        await session.commit()

        try:
            async for estate in scraper.scrape(
                city=city,
                listing_type=listing_type,
                property_type=property_type,
                max_pages=max_pages,
                max_age=max_age,
            ):
                try:
                    _listing, is_new, price_changed = await upsert_listing_from_sreality(
                        session,
                        estate,
                        city_override=city.title(),  # Normalize city name
                    )

                    stats["listings_found"] += 1
                    if is_new:
                        stats["listings_new"] += 1
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
        f"   Errors: {stats['errors']}"
    )

    return stats


def scrape_sync(
    city: str = "brno",
    listing_type: str = "sale",
    property_type: str = "apartment",
    max_pages: int | None = None,
    max_age: str | None = None,
) -> dict:
    """Synchronous wrapper for run_scrape."""
    return asyncio.run(run_scrape(city, listing_type, property_type, max_pages, max_age))
