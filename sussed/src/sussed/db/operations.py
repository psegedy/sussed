"""
Database operations for listings 📝

CRUD operations and queries for the listings table.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID  # noqa: TC003

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002

from sussed.db.models import (
    Listing,
    ListingStatus,
    ListingType,
    PriceHistory,
    PropertyCategory,
    ScrapeRun,
    VibeCheck,
)
from sussed.models.sreality import (
    SREALITY_COTTAGE_SUBCATEGORY_CODES,
    SREALITY_GARDEN_SUBCATEGORY_CODES,
    SrealityEstate,
    get_apartment_type,
)
from sussed.scrapers.sreality import (
    _build_listing_url,
    extract_area_from_title,
    normalize_sreality_image_url,
)


async def get_listing_by_external_id(
    session: AsyncSession,
    source: str,
    external_id: str,
) -> Listing | None:
    """Get a listing by its source and external ID."""
    stmt = select(Listing).where(
        Listing.source == source,
        Listing.external_id == external_id,
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


def _listing_type_from_sreality(estate: SrealityEstate) -> ListingType:
    """Map sreality category type to internal sale/rent enum."""
    category_type = estate.category_type_cb.int_value
    if category_type == 1:
        return ListingType.SALE
    if category_type == 2:
        return ListingType.RENT
    # v1 also returns type 4 for shares; this app is sale/rent-focused, so
    # unknown values fall back to SALE instead of crashing the scrape.
    return ListingType.SALE


def _property_category_from_sreality(estate: SrealityEstate) -> PropertyCategory:
    """Map sreality category main/sub codes to internal property category."""
    category_main = estate.category_main_cb.int_value
    category_sub = estate.category_sub_cb.int_value
    if category_main == 2 and category_sub in SREALITY_COTTAGE_SUBCATEGORY_CODES:
        return PropertyCategory.COTTAGE
    if category_main == 3 and category_sub in SREALITY_GARDEN_SUBCATEGORY_CODES:
        return PropertyCategory.GARDEN

    category_map = {
        1: PropertyCategory.APARTMENT,
        2: PropertyCategory.HOUSE,
        3: PropertyCategory.LAND,
        4: PropertyCategory.COMMERCIAL,
    }
    return category_map.get(category_main, PropertyCategory.OTHER)


def _apartment_type_from_sreality(
    estate: SrealityEstate, property_category: PropertyCategory
) -> str | None:
    """Map sreality subtype to apartment layout only for apartment listings."""
    if property_category != PropertyCategory.APARTMENT:
        return None
    return get_apartment_type(estate.category_sub_cb.int_value)


def _address_from_sreality(estate: SrealityEstate) -> str | None:
    """Build a compact address from v1 locality parts."""
    street = estate.locality.street
    number = estate.locality.streetnumber or estate.locality.housenumber
    return " ".join(part for part in (street, number) if part) or None


def _decimal_from_float(value: float | None) -> Decimal | None:
    """Convert API float coordinates to Decimal for DB storage."""
    return Decimal(str(value)) if value is not None else None


def _price_czk_from_sreality(estate: SrealityEstate) -> int:
    """Return integer CZK price from v1 price fields."""
    price = estate.price_czk if estate.price_czk is not None else estate.price
    return int(price or 0)


def _price_per_m2_from_sreality(estate: SrealityEstate) -> int | None:
    """Return integer price per m² from v1 price fields."""
    return int(estate.price_czk_m2) if estate.price_czk_m2 is not None else None


def _image_urls_from_sreality(estate: SrealityEstate) -> list[str]:
    """Return first 10 validated, fully-qualified image URLs from v1 search results."""
    urls = [normalize_sreality_image_url(url) for url in (estate.advert_images or [])]
    return [url for url in urls if url is not None][:10]


def _image_count_from_sreality(estate: SrealityEstate) -> int:
    """Return total image count from v1 search result metadata."""
    return len(estate.advert_images_all or estate.advert_images or [])


async def apply_price_change(
    session: AsyncSession,
    listing: Listing,
    new_price: int,
    new_price_per_m2: int | None,
) -> bool:
    """Record a price change (with bounce detection) and update the listing.

    Args:
        session: Database session.
        listing: Listing object to update.
        new_price: New price in CZK.
        new_price_per_m2: New price per m², or None if unavailable.

    Returns:
        True if a real (non-bounce) price change was applied.
    """
    if listing.price_czk == new_price:
        return False

    old_price = listing.price_czk

    recent_prices_stmt = (
        select(PriceHistory.price_czk)
        .where(PriceHistory.listing_id == listing.id)
        .order_by(PriceHistory.recorded_at.desc())
        .limit(2)
    )
    recent_result = await session.execute(recent_prices_stmt)
    recent_prices = [row[0] for row in recent_result.all()]
    if len(recent_prices) == 2 and new_price == recent_prices[1]:
        logger.info(
            f"Price bounce detected for {listing.external_id}: "
            f"{recent_prices[1]:,} -> {recent_prices[0]:,} -> {new_price:,} CZK - ignoring"
        )
        return False

    change_amount = new_price - old_price
    change_percent = (
        Decimal(change_amount) / Decimal(old_price) * 100 if old_price else None
    )
    session.add(
        PriceHistory(
            listing_id=listing.id,
            price_czk=new_price,
            price_per_m2=new_price_per_m2,
            change_type="increase" if change_amount > 0 else "decrease",
            change_amount=abs(change_amount),
            change_percent=abs(change_percent) if change_percent else None,
        )
    )
    listing.price_czk = new_price
    listing.price_per_m2 = new_price_per_m2
    listing.last_price_change_at = datetime.utcnow()
    pct_str = f" / {change_percent:+.1f}%" if change_percent is not None else ""
    logger.info(
        f"Price change for {listing.external_id}: {old_price:,} -> {new_price:,} CZK "
        f"({change_amount:+,}{pct_str})"
    )
    return True


async def upsert_listing_from_sreality(
    session: AsyncSession,
    estate: SrealityEstate,
    city_override: str | None = None,
) -> tuple[Listing, bool, bool]:
    """
    Create or update a listing from sreality v1 data.

    Args:
        session: Database session
        estate: Parsed sreality estate data
        city_override: Override city detection (e.g., "Brno" when scraping Brno)

    Returns:
        Tuple of (listing, is_new, price_changed)
    """
    external_id = str(estate.hash_id)
    source = "sreality"
    existing = await get_listing_by_external_id(session, source, external_id)

    new_price = _price_czk_from_sreality(estate)
    new_price_per_m2 = _price_per_m2_from_sreality(estate)
    area = extract_area_from_title(estate.advert_name)
    image_count = _image_count_from_sreality(estate)
    property_category = _property_category_from_sreality(estate)
    apartment_type = _apartment_type_from_sreality(estate, property_category)

    now = datetime.utcnow()
    is_new = existing is None
    price_changed = False

    if existing:
        listing = existing

        price_changed = await apply_price_change(session, listing, new_price, new_price_per_m2)

        listing.url = _build_listing_url(estate)
        listing.title = estate.advert_name
        listing.city = estate.locality.city or city_override or "Unknown"
        listing.district = estate.locality.citypart or None
        listing.address = _address_from_sreality(estate)
        listing.latitude = _decimal_from_float(estate.locality.gps_lat)
        listing.longitude = _decimal_from_float(estate.locality.gps_lon)
        listing.property_category = property_category
        listing.apartment_type = apartment_type
        if listing.area_m2 is None or listing.area_m2 == 0:
            listing.area_m2 = area
        if listing.features is None:
            listing.features = {}
        if listing.raw_labels is None:
            listing.raw_labels = []
        listing.image_urls = _image_urls_from_sreality(estate)
        listing.image_count = image_count
        listing.has_floor_plan = False
        listing.has_video = bool(estate.has_video)
        listing.has_3d_tour = bool(estate.has_matterport_url)
        listing.agency_id = str(estate.premise_id) if estate.premise_id else None
        listing.last_seen_at = now
        listing.status = ListingStatus.ACTIVE
        listing.updated_at = now

    else:
        listing = Listing(
            source=source,
            external_id=external_id,
            url=_build_listing_url(estate),
            title=estate.advert_name,
            price_czk=new_price,
            price_per_m2=new_price_per_m2,
            listing_type=_listing_type_from_sreality(estate),
            city=estate.locality.city or city_override or "Unknown",
            district=estate.locality.citypart or None,
            address=_address_from_sreality(estate),
            latitude=_decimal_from_float(estate.locality.gps_lat),
            longitude=_decimal_from_float(estate.locality.gps_lon),
            property_category=property_category,
            apartment_type=apartment_type,
            area_m2=area,
            features={},
            raw_labels=[],
            image_urls=_image_urls_from_sreality(estate),
            image_count=image_count,
            has_floor_plan=False,
            has_video=bool(estate.has_video),
            has_3d_tour=bool(estate.has_matterport_url),
            agency_name=None,
            agency_id=str(estate.premise_id) if estate.premise_id else None,
            vibe_check=VibeCheck.UNKNOWN,
            status=ListingStatus.ACTIVE,
            first_seen_at=now,
            last_seen_at=now,
        )
        session.add(listing)

        price_record = PriceHistory(
            listing_id=listing.id,
            price_czk=new_price,
            price_per_m2=new_price_per_m2,
            change_type="initial",
        )
        session.add(price_record)

        logger.debug(f"New listing: {estate.advert_name} - {new_price:,} CZK")

    return listing, is_new, price_changed


async def create_scrape_run(
    session: AsyncSession,
    source: str,
    city: str | None = None,
) -> ScrapeRun:
    """Create a new scrape run record."""
    run = ScrapeRun(
        source=source,
        city=city,
        status="running",
    )
    session.add(run)
    await session.flush()
    return run


async def update_scrape_run(
    session: AsyncSession,  # noqa: ARG001
    run: ScrapeRun,
    listings_found: int = 0,
    listings_new: int = 0,
    listings_updated: int = 0,
    price_changes: int = 0,
    errors: int = 0,
    status: str = "completed",
) -> None:
    """Update scrape run with results."""
    run.finished_at = datetime.utcnow()
    run.duration_seconds = int((run.finished_at - run.started_at).total_seconds())
    run.listings_found = listings_found
    run.listings_new = listings_new
    run.listings_updated = listings_updated
    run.price_changes_detected = price_changes
    run.errors_count = errors
    run.status = status


async def get_listings(
    session: AsyncSession,
    city: str | None = None,
    listing_type: ListingType | None = None,
    min_price: int | None = None,
    max_price: int | None = None,
    has_garage: bool | None = None,
    apartment_types: list[str] | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[Listing]:
    """
    Query listings with filters.

    Args:
        session: Database session
        city: Filter by city
        listing_type: Filter by sale/rent
        min_price: Minimum price
        max_price: Maximum price
        has_garage: Filter by garage availability
        apartment_types: Filter by apartment types (e.g., ["2+kk", "3+kk"])
        limit: Max results
        offset: Pagination offset

    Returns:
        List of matching listings
    """
    stmt = select(Listing).where(Listing.status == ListingStatus.ACTIVE)

    if city:
        stmt = stmt.where(Listing.city.ilike(f"%{city}%"))

    if listing_type:
        stmt = stmt.where(Listing.listing_type == listing_type)

    if min_price is not None:
        stmt = stmt.where(Listing.price_czk >= min_price)

    if max_price is not None:
        stmt = stmt.where(Listing.price_czk <= max_price)

    if apartment_types:
        stmt = stmt.where(Listing.apartment_type.in_(apartment_types))

    if has_garage is not None:
        stmt = stmt.where(Listing.features["garage"].as_boolean() == has_garage)

    stmt = stmt.order_by(Listing.price_czk).limit(limit).offset(offset)

    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_price_history(
    session: AsyncSession,
    listing_id: UUID,
) -> list[PriceHistory]:
    """Get price history for a listing."""
    stmt = (
        select(PriceHistory)
        .where(PriceHistory.listing_id == listing_id)
        .order_by(PriceHistory.recorded_at.desc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())
