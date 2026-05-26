"""
Database operations for listings 📝

CRUD operations and queries for the listings table.
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sussed.db.models import (
    Listing,
    ListingStatus,
    ListingType,
    PriceHistory,
    PropertyCategory,
    ScrapeRun,
    VibeCheck,
)
from sussed.models.sreality import SrealityEstate, get_apartment_type
from sussed.scrapers.sreality import extract_area_from_title, parse_city_from_locality


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


async def upsert_listing_from_sreality(
    session: AsyncSession,
    estate: SrealityEstate,
    city_override: str | None = None,
) -> tuple[Listing, bool, bool]:
    """
    Create or update a listing from sreality data.
    
    Args:
        session: Database session
        estate: Parsed sreality estate data
        city_override: Override city detection (e.g., "Brno" when scraping Brno)
    
    Returns:
        Tuple of (listing, is_new, price_changed)
    """
    external_id = str(estate.hash_id)
    source = "sreality"
    
    # Check if listing exists
    existing = await get_listing_by_external_id(session, source, external_id)
    
    # Parse location
    parsed_city, district = parse_city_from_locality(estate.locality)
    # Prefer the actual city from the listing's locality string.
    # Only fall back to city_override if we couldn't parse anything.
    city = parsed_city or city_override or "Unknown"
    
    # Parse area from title
    area = extract_area_from_title(estate.name)
    
    # Determine listing type
    listing_type = ListingType.SALE if estate.type == 1 else ListingType.RENT
    
    # Determine property category
    category_map = {
        1: PropertyCategory.APARTMENT,
        2: PropertyCategory.HOUSE,
        3: PropertyCategory.LAND,
        4: PropertyCategory.COMMERCIAL,
    }
    property_category = category_map.get(estate.category, PropertyCategory.OTHER)
    
    # Get apartment type from category code
    apartment_type = get_apartment_type(estate.seo.category_sub_cb)
    
    # Build features dict from labels
    feature_labels = estate.features
    features = {
        "garage": "garage" in feature_labels,
        "parking": "parking_lots" in feature_labels,
        "balcony": "balcony" in feature_labels,
        "loggia": "loggia" in feature_labels,
        "terrace": "terrace" in feature_labels,
        "cellar": "cellar" in feature_labels,
        "elevator": "elevator" in feature_labels,
        "new_building": "new_building" in feature_labels,
        "brick": "brick" in feature_labels,
        "panel": "panel" in feature_labels,
        "furnished": "furnished" in feature_labels,
        "partly_furnished": "partly_furnished" in feature_labels,
        "reconstructed": "after_reconstruction" in feature_labels,
    }
    
    # Build URL
    url = f"https://www.sreality.cz/detail/prodej/byt/{apartment_type or 'x'}/{estate.seo.locality or 'x'}/{estate.hash_id}"
    
    now = datetime.utcnow()
    is_new = existing is None
    price_changed = False
    
    if existing:
        # Update existing listing
        listing = existing
        
        # Check for price change
        if listing.price_czk != estate.price:
            old_price = listing.price_czk
            
            # Bounce detection: sreality's list API can return different
            # prices for the same listing on different pages/runs. Detect
            # A→B→A pattern and ignore the flip-flop.
            is_bounce = False
            recent_prices_stmt = (
                select(PriceHistory.price_czk)
                .where(PriceHistory.listing_id == listing.id)
                .order_by(PriceHistory.recorded_at.desc())
                .limit(2)
            )
            recent_result = await session.execute(recent_prices_stmt)
            recent_prices = [row[0] for row in recent_result.all()]
            
            # If last 2 records are [current, previous] and new price == previous,
            # this is a bounce: previous→current→previous
            if len(recent_prices) == 2 and estate.price == recent_prices[1]:
                is_bounce = True
                logger.info(
                    f"Price bounce detected for {estate.hash_id}: "
                    f"{recent_prices[1]:,} → {recent_prices[0]:,} → {estate.price:,} CZK — ignoring"
                )
            
            if not is_bounce:
                price_changed = True
                
                # Record price history
                change_amount = estate.price - old_price
                change_percent = Decimal(change_amount) / Decimal(old_price) * 100 if old_price else None
                
                price_record = PriceHistory(
                    listing_id=listing.id,
                    price_czk=estate.price,
                    price_per_m2=estate.price_per_m2,
                    change_type="increase" if change_amount > 0 else "decrease",
                    change_amount=abs(change_amount),
                    change_percent=abs(change_percent) if change_percent else None,
                )
                session.add(price_record)
                
                listing.price_czk = estate.price
                listing.price_per_m2 = estate.price_per_m2
                listing.last_price_change_at = now
                
                logger.info(
                    f"Price change for {estate.hash_id}: {old_price:,} -> {estate.price:,} CZK "
                    f"({change_amount:+,} / {change_percent:+.1f}%)"
                )
        
        # Update fields that might change
        listing.title = estate.name
        listing.last_seen_at = now
        listing.status = ListingStatus.ACTIVE
        listing.image_count = estate.advert_images_count
        listing.has_floor_plan = bool(estate.has_floor_plan)
        listing.has_video = estate.has_video
        listing.has_3d_tour = estate.has_matterport_url
        listing.updated_at = now
        
    else:
        # Create new listing
        listing = Listing(
            source=source,
            external_id=external_id,
            url=url,
            title=estate.name,
            price_czk=estate.price,
            price_per_m2=estate.price_per_m2,
            listing_type=listing_type,
            city=city,
            district=district,
            address=estate.locality,
            latitude=estate.gps.lat if estate.gps else None,
            longitude=estate.gps.lon if estate.gps else None,
            property_category=property_category,
            apartment_type=apartment_type,
            area_m2=area,
            features=features,
            raw_labels=estate.labels,
            image_urls=estate.image_urls[:10],  # Store first 10 images
            image_count=estate.advert_images_count,
            has_floor_plan=bool(estate.has_floor_plan),
            has_video=estate.has_video,
            has_3d_tour=estate.has_matterport_url,
            agency_name=estate.agency_name,
            agency_id=estate.agency_id,
            vibe_check=VibeCheck.UNKNOWN,
            status=ListingStatus.ACTIVE,
            first_seen_at=now,
            last_seen_at=now,
        )
        session.add(listing)
        
        # Record initial price in history
        price_record = PriceHistory(
            listing_id=listing.id,
            price_czk=estate.price,
            price_per_m2=estate.price_per_m2,
            change_type="initial",
        )
        session.add(price_record)
        
        logger.debug(f"New listing: {estate.name} - {estate.price:,} CZK")
    
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
    await session.flush()  # Get the ID
    return run


async def update_scrape_run(
    session: AsyncSession,
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
    run.duration_seconds = int(
        (run.finished_at - run.started_at).total_seconds()
    )
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
    
    # Note: JSONB filtering for garage would be:
    # stmt = stmt.where(Listing.features["garage"].as_boolean() == True)
    # But this requires proper JSONB column type setup
    
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
