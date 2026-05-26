"""
SussedTools - The toolkit for our AI real estate agent 🔧

These tools let the agent:
1. Pre-filter listings using DB data (no API calls!)
2. Fetch descriptions for promising candidates
3. Score and save analysis
4. Get market stats for comparison
"""

import asyncio
from datetime import datetime
from decimal import Decimal
from typing import Any

from agno.tools import Toolkit
from loguru import logger


class SussedTools(Toolkit):
    """
    Tools for the sussed real estate agent.
    
    The agent uses these to autonomously:
    - Find promising listings matching criteria
    - Fetch descriptions for candidates worth investigating
    - Score and classify listings
    - Compare against market averages
    """
    
    name = "sussed_tools"
    
    def __init__(self):
        super().__init__(name=self.name)
        # Register tools
        self.register(self.pre_filter_listings)
        self.register(self.fetch_listing_description)
        self.register(self.score_listing)
        self.register(self.get_market_stats)
        self.register(self.get_unscored_listings)
    
    def _run_async(self, coro):
        """Helper to run async code in sync context."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # We're in an async context, create a task
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    return pool.submit(asyncio.run, coro).result()
            else:
                return loop.run_until_complete(coro)
        except RuntimeError:
            return asyncio.run(coro)
    
    def pre_filter_listings(
        self,
        city: str = "Brno",
        apartment_type: str | None = None,
        max_price: int | None = None,
        min_area_m2: float | None = None,
        require_garage: bool = False,
        require_balcony: bool = False,
        limit: int = 20,
    ) -> str:
        """
        Pre-filter listings using structured database data only.
        
        This is the FIRST step - no API calls needed! Uses existing DB data
        to find candidates matching user criteria.
        
        Args:
            city: City name (e.g., "Brno", "Praha")
            apartment_type: Type like "2+kk", "3+1", etc. (None = any)
            max_price: Maximum price in CZK (None = no limit)
            min_area_m2: Minimum area in m² (None = no limit)
            require_garage: Must have garage/parking label
            require_balcony: Must have balcony/loggia/terrace
            limit: Max results to return
        
        Returns:
            JSON string with matching listings (id, title, price, area, features)
        """
        import json
        
        async def _query():
            from sqlmodel import select, and_
            from sussed.db.connection import get_session
            from sussed.db.models import Listing, ListingStatus
            
            async with get_session() as session:
                # Build query
                conditions = [Listing.status == ListingStatus.ACTIVE]
                
                if city:
                    conditions.append(Listing.city.ilike(f"%{city}%"))
                
                if apartment_type:
                    conditions.append(Listing.apartment_type == apartment_type)
                
                if max_price:
                    conditions.append(Listing.price_czk <= max_price)
                
                if min_area_m2:
                    conditions.append(Listing.area_m2 >= Decimal(str(min_area_m2)))
                
                stmt = select(Listing).where(and_(*conditions)).limit(limit)
                result = await session.execute(stmt)
                listings = result.scalars().all()
                
                # Filter by features (stored in raw_labels)
                filtered = []
                for listing in listings:
                    labels = listing.raw_labels or []
                    labels_flat = [l.lower() for sublist in labels for l in (sublist if isinstance(sublist, list) else [sublist])]
                    
                    if require_garage:
                        if not any(l in labels_flat for l in ["garage", "parking_lots", "garáž", "parkovací"]):
                            continue
                    
                    if require_balcony:
                        if not any(l in labels_flat for l in ["balcony", "loggia", "terrace", "balkón", "lodžie", "terasa"]):
                            continue
                    
                    filtered.append({
                        "id": str(listing.id),
                        "external_id": listing.external_id,
                        "title": listing.title,
                        "price_czk": listing.price_czk,
                        "price_per_m2": listing.price_per_m2,
                        "area_m2": float(listing.area_m2) if listing.area_m2 else None,
                        "apartment_type": listing.apartment_type,
                        "city": listing.city,
                        "district": listing.district,
                        "has_description": listing.description is not None,
                        "score": None,  # TODO: add score field
                        "labels": listing.raw_labels,
                    })
                
                return filtered
        
        results = self._run_async(_query())
        logger.info(f"Pre-filter found {len(results)} listings matching criteria")
        return json.dumps(results, indent=2, ensure_ascii=False)
    
    def get_unscored_listings(
        self,
        city: str = "Brno",
        limit: int = 10,
        with_description_only: bool = True,
    ) -> str:
        """
        Get listings that haven't been scored yet.
        
        Use this to find listings that need analysis.
        
        Args:
            city: Filter by city
            limit: Max results
            with_description_only: Only return listings that already have descriptions
        
        Returns:
            JSON string with unscored listings
        """
        import json
        
        async def _query():
            from sqlmodel import select, and_
            from sussed.db.connection import get_session
            from sussed.db.models import Listing, ListingStatus
            
            async with get_session() as session:
                conditions = [
                    Listing.status == ListingStatus.ACTIVE,
                    Listing.ai_analysis.is_(None),  # Not yet analyzed
                ]
                
                if city:
                    conditions.append(Listing.city.ilike(f"%{city}%"))
                
                if with_description_only:
                    conditions.append(Listing.description.isnot(None))
                
                stmt = select(Listing).where(and_(*conditions)).limit(limit)
                result = await session.execute(stmt)
                listings = result.scalars().all()
                
                return [{
                    "id": str(listing.id),
                    "title": listing.title,
                    "price_czk": listing.price_czk,
                    "area_m2": float(listing.area_m2) if listing.area_m2 else None,
                    "description": listing.description[:500] + "..." if listing.description and len(listing.description) > 500 else listing.description,
                    "labels": listing.raw_labels,
                } for listing in listings]
        
        results = self._run_async(_query())
        logger.info(f"Found {len(results)} unscored listings")
        return json.dumps(results, indent=2, ensure_ascii=False)
    
    def fetch_listing_description(self, listing_id: str) -> str:
        """
        Fetch full description for a listing from sreality API.
        
        Use this for promising candidates that passed pre-filtering.
        This makes an API call, so use sparingly!
        
        Args:
            listing_id: UUID of the listing in our database
        
        Returns:
            JSON with listing details including full description
        """
        import json
        import httpx
        
        async def _fetch():
            from sqlmodel import select
            from sussed.db.connection import get_session
            from sussed.db.models import Listing
            from sussed.scrapers.sreality import SrealityScraper
            from datetime import datetime as dt
            
            async with get_session() as session:
                # Get listing from DB
                stmt = select(Listing).where(Listing.id == listing_id)
                result = await session.execute(stmt)
                listing = result.scalar_one_or_none()
                
                if not listing:
                    return {"error": f"Listing {listing_id} not found"}
                
                # If already has description, return it
                if listing.description:
                    return {
                        "id": str(listing.id),
                        "title": listing.title,
                        "description": listing.description,
                        "price_czk": listing.price_czk,
                        "cached": True,
                    }
                
                # Fetch from API
                scraper = SrealityScraper()
                async with httpx.AsyncClient() as client:
                    hash_id = int(listing.external_id)
                    details = await scraper.fetch_listing_details(client, hash_id)
                    
                    if not details:
                        return {"error": "Failed to fetch from API"}
                    
                    # Extract description
                    description_parts = []
                    if "text" in details and "value" in details["text"]:
                        description_parts.append(details["text"]["value"])
                    
                    # Parse items for additional data
                    for item in details.get("items", []):
                        item_type = item.get("type")
                        item_name = item.get("name", "")
                        item_value = item.get("value")
                        
                        # Extract update date (use as publish date approximation)
                        if item_type == "edited" and item_name == "Aktualizace" and item_value:
                            try:
                                api_date = dt.strptime(item_value, "%d.%m.%Y")
                                if listing.updated_at_source is None or api_date < listing.updated_at_source:
                                    listing.updated_at_source = api_date
                            except ValueError:
                                pass
                    
                    # Save description
                    if description_parts:
                        listing.description = "\n\n".join(description_parts)
                        session.add(listing)
                        await session.commit()
                    
                    return {
                        "id": str(listing.id),
                        "title": listing.title,
                        "description": listing.description,
                        "price_czk": listing.price_czk,
                        "items": details.get("items", []),
                        "cached": False,
                    }
        
        result = self._run_async(_fetch())
        logger.info(f"Fetched description for listing {listing_id}")
        return json.dumps(result, indent=2, ensure_ascii=False)
    
    def score_listing(
        self,
        listing_id: str,
        score: int,
        reason: str,
        red_flags: list[str] | None = None,
        highlights: list[str] | None = None,
        parking_price: int | None = None,
        parking_included: bool | None = None,
        usable_area_m2: float | None = None,
    ) -> str:
        """
        Save agent's analysis and score for a listing.
        
        Scoring guide:
        - 0-200: Trash tier, don't waste time
        - 200-400: Below average, significant issues
        - 400-600: Average, nothing special
        - 600-800: Good deal, worth considering
        - 800-1000: Excellent, move fast!
        - 9999: ABSOLUTE GEM - underpriced, perfect location, etc.
        - -1: SUS - likely scam, fake listing, or major red flag
        
        Args:
            listing_id: UUID of the listing
            score: Integer score (0-1000, 9999 for gem, -1 for sus)
            reason: Brief explanation of the score
            red_flags: List of concerns found
            highlights: List of positive aspects
            parking_price: Extracted parking price if separate (CZK)
            parking_included: Whether parking is included in price
            usable_area_m2: Actual usable living area (excluding cellar, balcony)
        
        Returns:
            Confirmation message
        """
        import json
        
        async def _score():
            from sqlmodel import select
            from sussed.db.connection import get_session
            from sussed.db.models import Listing, VibeCheck
            
            async with get_session() as session:
                stmt = select(Listing).where(Listing.id == listing_id)
                result = await session.execute(stmt)
                listing = result.scalar_one_or_none()
                
                if not listing:
                    return {"error": f"Listing {listing_id} not found"}
                
                # Map score to vibe check
                if score == -1:
                    vibe = VibeCheck.SUS
                elif score == 9999:
                    vibe = VibeCheck.PEAK
                elif score >= 700:
                    vibe = VibeCheck.PEAK
                elif score >= 400:
                    vibe = VibeCheck.VALID
                else:
                    vibe = VibeCheck.MID
                
                # Store analysis
                listing.vibe_check = vibe
                listing.ai_analysis = {
                    "score": score,
                    "reason": reason,
                    "red_flags": red_flags or [],
                    "highlights": highlights or [],
                    "parking_price": parking_price,
                    "parking_included": parking_included,
                    "usable_area_m2": usable_area_m2,
                    "analyzed_at": datetime.utcnow().isoformat(),
                }
                
                session.add(listing)
                await session.commit()
                
                return {
                    "success": True,
                    "listing_id": listing_id,
                    "score": score,
                    "vibe_check": vibe.value,
                }
        
        result = self._run_async(_score())
        logger.info(f"Scored listing {listing_id}: {score} ({result.get('vibe_check', 'unknown')})")
        return json.dumps(result, indent=2, ensure_ascii=False)
    
    def get_market_stats(
        self,
        city: str = "Brno",
        apartment_type: str | None = None,
    ) -> str:
        """
        Get market statistics for price comparison.
        
        Use this to understand if a listing is overpriced or a good deal.
        
        Args:
            city: City to get stats for
            apartment_type: Specific type like "2+kk" (None = all types)
        
        Returns:
            JSON with avg price, price/m², price ranges
        """
        import json
        
        async def _stats():
            from sqlalchemy import func
            from sqlmodel import select, and_
            from sussed.db.connection import get_session
            from sussed.db.models import Listing, ListingStatus
            
            async with get_session() as session:
                conditions = [
                    Listing.status == ListingStatus.ACTIVE,
                    Listing.price_czk > 0,
                ]
                
                if city:
                    conditions.append(Listing.city.ilike(f"%{city}%"))
                
                if apartment_type:
                    conditions.append(Listing.apartment_type == apartment_type)
                
                # Get aggregates
                stmt = select(
                    func.count(Listing.id).label("count"),
                    func.avg(Listing.price_czk).label("avg_price"),
                    func.min(Listing.price_czk).label("min_price"),
                    func.max(Listing.price_czk).label("max_price"),
                    func.avg(Listing.price_per_m2).label("avg_price_per_m2"),
                ).where(and_(*conditions))
                
                result = await session.execute(stmt)
                row = result.one()
                
                return {
                    "city": city,
                    "apartment_type": apartment_type or "all",
                    "count": row.count,
                    "avg_price": int(row.avg_price) if row.avg_price else None,
                    "min_price": row.min_price,
                    "max_price": row.max_price,
                    "avg_price_per_m2": int(row.avg_price_per_m2) if row.avg_price_per_m2 else None,
                }
        
        result = self._run_async(_stats())
        logger.info(f"Market stats for {city}: {result.get('count', 0)} listings")
        return json.dumps(result, indent=2, ensure_ascii=False)
