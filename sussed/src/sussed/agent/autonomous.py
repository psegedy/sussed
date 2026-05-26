"""
Autonomous Agent Runner 🤖

This is where the magic happens. The agent processes listings based on
your config file, scores them, and spits out the results you want.

The 1 Kč handling is key - these are "Price on Request" listings, not scams!
We evaluate them on description/features alone without price calculations.

NOW WITH ACTUAL AI! 🧠 Uses LLM (Claude/GPT) to analyze descriptions.
"""

import asyncio
import json
from datetime import datetime
from decimal import Decimal
from typing import Any

from loguru import logger
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
from rich.table import Table

from sussed.agent.config import (
    SearchConfig,
    OutputMode,
    generate_example_config,
)

console = Console()


class ListingGoneError(Exception):
    """Raised when a listing returns 410 Gone (sold/removed)."""

    def __init__(self, listing_id: str, external_id: str) -> None:
        self.listing_id = listing_id
        self.external_id = external_id
        super().__init__(f"Listing {external_id} is gone (410)")


class AutonomousRunner:
    """
    The autonomous agent runner - processes listings based on config.
    
    Workflow:
    1. Load config
    2. Query DB for matching listings
    3. Quick score all listings (no API calls)
    4. Fetch descriptions for top candidates
    5. LLM analysis for top candidates (the REAL AI!)
    6. Output results in requested format
    """
    
    # 1 Kč = Price on Request threshold
    POA_PRICE_THRESHOLD = 10  # Anything under 10 CZK is POA
    
    def __init__(self, config: SearchConfig):
        self.config = config
        self.stats = {
            "total_processed": 0,
            "descriptions_fetched": 0,
            "llm_analyzed": 0,
            "scored": 0,
            "poa_listings": 0,
            "skipped": 0,
            "errors": 0,
        }
        self._llm_analyzer = None
        
        # Initialize LLM analyzer if enabled
        if config.agent.use_llm:
            self._init_llm_analyzer()
    
    def _init_llm_analyzer(self) -> None:
        """Initialize the LLM analyzer for description analysis."""
        try:
            from sussed.agent.llm_analyzer import get_llm_analyzer
            
            self._llm_analyzer = get_llm_analyzer(
                model_provider=self.config.agent.llm_provider,
                model_id=self.config.agent.llm_model,
            )
            
            if self._llm_analyzer.is_available:
                console.print(f"[green]✓ LLM analyzer ready ({self.config.agent.llm_provider})[/green]")
            else:
                console.print(f"[yellow]⚠ LLM not available: {self._llm_analyzer.initialization_error}[/yellow]")
                console.print("[dim]  Set ANTHROPIC_API_KEY or OPENAI_API_KEY in .env[/dim]")
                self._llm_analyzer = None
        except Exception as e:
            logger.warning(f"Failed to initialize LLM analyzer: {e}")
            console.print(f"[yellow]⚠ LLM analyzer failed to initialize: {e}[/yellow]")
            self._llm_analyzer = None
    
    def is_poa_listing(self, price: int) -> bool:
        """Check if this is a 'Price on Request' listing (1 Kč, etc.)"""
        return price <= self.POA_PRICE_THRESHOLD
    
    async def run(self) -> list[dict[str, Any]]:
        """
        Run the autonomous processing pipeline.
        
        Full workflow:
        0. Auto-scrape fresh data (if enabled)
        1. Quick score all listings based on metadata
        2. Fetch descriptions for top candidates
        3. LLM analysis for top candidates (the REAL AI!)
        4. Market insights (price changes, trends)
        
        Returns list of processed/scored listings.
        """
        console.print(f"\n[bold blue]🤖 Starting autonomous hunt:[/bold blue] {self.config.name}")
        console.print(f"   [dim]{self.config.description or 'No description'}[/dim]\n")
        
        # Step 0: Auto-scrape if enabled
        scrape_stats = None
        if self.config.agent.auto_scrape:
            scrape_stats = await self._auto_scrape()
        
        # Step 1: Get matching listings from DB
        listings = await self._get_matching_listings()
        
        if not listings:
            console.print("[yellow]No listings found matching criteria. Try scraping first![/yellow]")
            console.print("   Run: [cyan]uv run sussed scrape -c brno -m 5[/cyan]")
            return []
        
        console.print(f"[green]Found {len(listings)} listings matching criteria[/green]\n")
        
        # Step 2: First pass - quick score all listings (no API calls)
        processed = []
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            console=console,
        ) as progress:
            task = progress.add_task("Pass 1: Quick scoring...", total=len(listings))
            
            for listing in listings:
                try:
                    result = await self._process_listing(listing, fetch_description=False)
                    if result:
                        processed.append(result)
                        self.stats["scored"] += 1
                except Exception as e:
                    logger.error(f"Error processing {listing['id']}: {e}")
                    self.stats["errors"] += 1
                
                self.stats["total_processed"] += 1
                progress.update(task, advance=1)
        
        # Step 3: Second pass - fetch descriptions for top candidates
        if self.config.agent.fetch_descriptions and processed:
            # Sort by score, get top N for description fetching
            processed.sort(key=lambda x: x.get("score", 0), reverse=True)
            
            # Fetch for at least as many as the output limit (--best N), minimum 5
            output_limit = self.config.output.limit or 5
            fetch_count = min(
                max(output_limit, 5),
                len(processed),
            )
            top_candidates = processed[:fetch_count]
            
            console.print(f"\n[cyan]Pass 2: Fetching descriptions for top {fetch_count} candidates...[/cyan]")
            
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                console=console,
            ) as progress:
                task = progress.add_task("Fetching descriptions...", total=fetch_count)
                
                sold_ids: set[str] = set()
                for i, listing in enumerate(top_candidates):
                    needs_desc = not listing.get("description")
                    needs_date = not listing.get("listed_at")
                    if needs_desc or needs_date:
                        try:
                            description, source_date = await self._fetch_description(listing["id"], listing["external_id"])
                            if source_date and not listing.get("listed_at"):
                                listing["listed_at"] = source_date
                            if description and needs_desc:
                                listing["description"] = description
                                self.stats["descriptions_fetched"] += 1
                                
                                # Re-score with description
                                is_poa = self.is_poa_listing(listing["price_czk"])
                                new_score = await self._score_listing(listing, is_poa)
                                listing["score"] = new_score["score"]
                                listing["analysis"] = new_score
                                
                                # Save updated score to DB
                                await self._save_score(listing["id"], new_score)
                                
                                # Also save description to DB
                                await self._save_description(listing["id"], description)
                                
                        except ListingGoneError:
                            sold_ids.add(listing["id"])
                            console.print(f"  [magenta]💀 {listing['title'][:40]}... SOLD/GONE[/magenta]")
                        except Exception as e:
                            logger.warning(f"Failed to fetch description: {e}")
                    
                    progress.update(task, advance=1)
                    
                    # Rate limiting - don't hammer the API
                    import asyncio
                    await asyncio.sleep(0.5)
            
            # Remove sold listings from results
            if sold_ids:
                processed = [p for p in processed if p["id"] not in sold_ids]
                console.print(f"  [magenta]Removed {len(sold_ids)} sold/gone listing(s) from results[/magenta]")
            
            # Re-sort after re-scoring
            processed.sort(key=lambda x: x.get("score", 0), reverse=True)
        
        # Step 4: Third pass - LLM analysis for top candidates (THE REAL AI!)
        if self._llm_analyzer and self._llm_analyzer.is_available and processed:
            llm_count = min(
                self.config.agent.llm_analyze_top_n,
                len(processed),
            )
            
            # Only analyze listings that have descriptions
            candidates_for_llm = [
                l for l in processed[:llm_count * 2]  # Look at more than we need
                if l.get("description")
            ][:llm_count]
            
            if candidates_for_llm:
                console.print(f"\n[bold magenta]Pass 3: 🧠 LLM analysis for top {len(candidates_for_llm)} candidates...[/bold magenta]")
                
                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    BarColumn(),
                    TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                    console=console,
                ) as progress:
                    task = progress.add_task("Analyzing with AI...", total=len(candidates_for_llm))
                    
                    for listing in candidates_for_llm:
                        try:
                            llm_result = await self._llm_analyze(listing)
                            
                            if llm_result:
                                # Apply LLM score adjustment
                                old_score = listing.get("score", 500)
                                new_score = max(0, min(1000, old_score + llm_result.score_adjustment))
                                
                                listing["score"] = new_score
                                listing["llm_analysis"] = {
                                    "score_adjustment": llm_result.score_adjustment,
                                    "confidence": llm_result.confidence,
                                    "red_flags": llm_result.red_flags,
                                    "yellow_flags": llm_result.yellow_flags,
                                    "highlights": llm_result.highlights,
                                    "hidden_costs": llm_result.hidden_costs,
                                    "true_area_m2": llm_result.true_usable_area_m2,
                                    "renovation_needed": llm_result.renovation_needed,
                                    "one_liner": llm_result.one_liner,
                                    "recommendation": llm_result.recommendation,
                                }
                                
                                # Merge LLM findings into main analysis
                                if "analysis" in listing:
                                    listing["analysis"]["red_flags"].extend(llm_result.red_flags)
                                    listing["analysis"]["highlights"].extend(llm_result.highlights)
                                    listing["analysis"]["llm_one_liner"] = llm_result.one_liner
                                    listing["analysis"]["llm_recommendation"] = llm_result.recommendation
                                    listing["analysis"]["score"] = new_score
                                
                                # Save updated analysis to DB
                                await self._save_score(listing["id"], listing.get("analysis", {}))
                                
                                self.stats["llm_analyzed"] += 1
                                logger.info(f"LLM: {listing['title'][:30]}... score {old_score}→{new_score} ({llm_result.recommendation})")
                                
                        except Exception as e:
                            logger.warning(f"LLM analysis failed for {listing['id']}: {e}")
                        
                        progress.update(task, advance=1)
                        
                        # Rate limit LLM calls (they're expensive!)
                        await asyncio.sleep(1.0)
                
                # Final re-sort after LLM adjustments
                processed.sort(key=lambda x: x.get("score", 0), reverse=True)
        
        # Step 5: Sort and filter based on output config
        results = self._prepare_output(processed)
        
        # Step 6: Final validation loop - fetch descriptions for results that
        # bubbled up after sold listings were removed. Keeps going until we have
        # a clean set with no unchecked listings (or we run out of candidates).
        if self.config.agent.fetch_descriptions and results:
            max_validation_rounds = 5  # Safety limit to avoid infinite loops
            validation_round = 0
            total_gone = 0
            
            while validation_round < max_validation_rounds:
                needs_fetch = [r for r in results if not r.get("description") or not r.get("listed_at")]
                if not needs_fetch:
                    break
                
                validation_round += 1
                console.print(f"\n[cyan]Validation pass {validation_round}: Checking {len(needs_fetch)} listings...[/cyan]")
                
                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    BarColumn(),
                    TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                    console=console,
                ) as progress:
                    task = progress.add_task("Fetching descriptions...", total=len(needs_fetch))
                    
                    gone_ids: set[str] = set()
                    for listing in needs_fetch:
                        try:
                            had_desc = bool(listing.get("description"))
                            description, source_date = await self._fetch_description(listing["id"], listing["external_id"])
                            if source_date and not listing.get("listed_at"):
                                listing["listed_at"] = source_date
                            if description and not had_desc:
                                listing["description"] = description
                                self.stats["descriptions_fetched"] += 1
                                
                                # Re-score with description
                                is_poa = self.is_poa_listing(listing["price_czk"])
                                new_score = await self._score_listing(listing, is_poa)
                                listing["score"] = new_score["score"]
                                listing["analysis"] = new_score
                                
                                await self._save_score(listing["id"], new_score)
                                await self._save_description(listing["id"], description)
                                
                        except ListingGoneError:
                            gone_ids.add(listing["id"])
                            console.print(f"  [magenta]💀 {listing['title'][:40]}... SOLD/GONE[/magenta]")
                        except Exception as e:
                            logger.warning(f"Failed to fetch description: {e}")
                        
                        progress.update(task, advance=1)
                        await asyncio.sleep(0.5)
                
                if not gone_ids:
                    break  # All clean, no more ghosts
                
                # Remove gone listings and backfill from processed
                total_gone += len(gone_ids)
                results = [r for r in results if r["id"] not in gone_ids]
                processed = [p for p in processed if p["id"] not in gone_ids]
                
                # Backfill: grab more from processed to replace the removed ones
                # Skip auto-rejected listings (score -1) during backfill
                result_ids = {r["id"] for r in results}
                for p in processed:
                    if len(results) >= (self.config.output.limit or 10):
                        break
                    if p["id"] not in result_ids and p.get("score", 0) != -1:
                        results.append(p)
                        result_ids.add(p["id"])
                
                results.sort(key=lambda x: x.get("score", 0), reverse=True)
            
            if total_gone:
                console.print(f"  [magenta]Removed {total_gone} sold/gone listing(s) total across {validation_round} pass(es)[/magenta]")
        
        # Step 7: Display results
        self._display_results(results)
        
        # Step 8: Show market insights 📊
        await self._display_market_insights(scrape_stats)
        
        # Step 9: Show stats
        self._display_stats()
        
        return results
    
    async def _auto_scrape(self) -> dict[str, Any]:
        """
        Run the scraper before processing - fresh data is best data! 🕷️
        
        Returns scrape stats for market insights.
        """
        from sussed.scrapers.runner import run_scrape
        
        console.print("[bold cyan]🕷️ Auto-scraping fresh data...[/bold cyan]")
        
        criteria = self.config.criteria
        
        # Map config to scraper params
        city = criteria.city.lower() if criteria.city else "brno"
        listing_type = criteria.listing_type or "sale"
        property_type = criteria.property_type or "apartment"
        max_pages = self.config.agent.scrape_max_pages
        max_age = criteria.max_listing_age  # day, week, or month
        
        try:
            stats = await run_scrape(
                city=city,
                listing_type=listing_type,
                property_type=property_type,
                max_pages=max_pages,
                max_age=max_age,
            )
            
            console.print(f"[green]✅ Scraped {stats['listings_found']} listings "
                         f"({stats['listings_new']} new, {stats['price_changes']} price changes)[/green]\n")
            
            return stats
        except Exception as e:
            logger.error(f"Auto-scrape failed: {e}")
            console.print(f"[red]Auto-scrape failed: {e}[/red]")
            console.print("[yellow]Continuing with existing data...[/yellow]\n")
            return {}
    
    async def _display_market_insights(self, scrape_stats: dict[str, Any] | None) -> None:
        """
        Display juicy market insights! 📊
        
        Shows:
        - Price change summary (drops = opportunities!)
        - New listings summary
        - Market temperature (hot/cold)
        - Notable deals
        """
        from sussed.db.connection import get_session
        from sussed.db.models import PriceHistory
        from sqlmodel import select, desc
        from datetime import datetime, timedelta
        
        console.print("\n[bold magenta]📊 Market Insights[/bold magenta]")
        
        # If we just scraped, show those stats
        if scrape_stats:
            new_count = scrape_stats.get('listings_new', 0)
            changes_count = scrape_stats.get('price_changes', 0)
            if new_count > 0 or changes_count > 0:
                console.print(f"\n[cyan]From this scrape:[/cyan]")
                if new_count > 0:
                    console.print(f"   • {new_count} fresh listings just hit the market 🆕")
                if changes_count > 0:
                    console.print(f"   • {changes_count} price changes detected 💰")
        
        # Get recent price changes from DB
        try:
            async with get_session() as session:
                week_ago = datetime.utcnow() - timedelta(days=7)
                
                result = await session.execute(
                    select(PriceHistory)
                    .where(PriceHistory.recorded_at >= week_ago)
                    .order_by(desc(PriceHistory.recorded_at))
                    .limit(100)
                )
                recent_changes = result.scalars().all()
                
                if recent_changes:
                    # Use change_type to determine direction (change_amount is always positive)
                    price_drops = [c for c in recent_changes if c.change_type == "decrease" and c.change_amount]
                    price_increases = [c for c in recent_changes if c.change_type == "increase" and c.change_amount]
                    
                    # Big drops (>5%)
                    big_drops = [c for c in price_drops if c.change_percent and abs(float(c.change_percent)) > 5]
                    
                    console.print(f"\n[cyan]Last 7 days:[/cyan]")
                    
                    if price_drops:
                        total_drop = sum(c.change_amount for c in price_drops if c.change_amount)
                        avg_drop_pct = sum(abs(float(c.change_percent)) for c in price_drops if c.change_percent) / len(price_drops)
                        console.print(f"   • [green]{len(price_drops)} price drops[/green] 📉 (avg -{avg_drop_pct:.1f}%)")
                        
                        if big_drops:
                            console.print(f"   • [bold green]{len(big_drops)} BIG drops (>5%)[/bold green] - check these out! 🎯")
                            
                            # Show top 3 biggest drops by percentage
                            big_drops.sort(key=lambda c: abs(float(c.change_percent or 0)), reverse=True)
                            for drop in big_drops[:3]:
                                drop_pct = abs(float(drop.change_percent or 0))
                                drop_czk = drop.change_amount or 0
                                console.print(f"     → -{drop_pct:.1f}% (-{drop_czk:,.0f} Kč) on listing {drop.listing_id}")
                    
                    if price_increases:
                        avg_inc_pct = sum(float(c.change_percent or 0) for c in price_increases if c.change_percent) / len(price_increases)
                        console.print(f"   • [yellow]{len(price_increases)} price increases[/yellow] 📈 (avg +{avg_inc_pct:.1f}%)")
                    
                    # Market temperature
                    if len(price_drops) > len(price_increases) * 2:
                        console.print(f"\n   [bold green]🥶 BUYER'S MARKET[/bold green] - sellers are getting desperate!")
                    elif len(price_increases) > len(price_drops) * 2:
                        console.print(f"\n   [bold red]🔥 SELLER'S MARKET[/bold red] - prices going up, act fast!")
                    else:
                        console.print(f"\n   [dim]😐 Market is balanced[/dim]")
                else:
                    console.print("\n   [dim]No price changes in the last week. Scrape more to track trends![/dim]")
        except Exception as e:
            logger.warning(f"Failed to get market insights: {e}")
            console.print(f"\n   [dim]Could not fetch market insights: {e}[/dim]")
    
    async def _get_matching_listings(self) -> list[dict[str, Any]]:
        """Query DB for listings matching config criteria."""
        from sqlalchemy.orm import selectinload
        from sqlmodel import select, and_, or_
        from sussed.db.connection import get_session
        from sussed.db.models import Listing, ListingStatus, PriceHistory
        
        criteria = self.config.criteria
        agent = self.config.agent
        
        logger.debug(f"Query criteria: city={criteria.city}, types={criteria.apartment_types}, min_photos={criteria.min_photos}")
        logger.debug(f"Agent config: skip_scored={agent.skip_already_scored}, max_process={agent.max_listings_to_process}")
        
        async with get_session() as session:
            conditions = [Listing.status == ListingStatus.ACTIVE]
            
            # City
            if criteria.city:
                conditions.append(Listing.city.ilike(f"%{criteria.city}%"))
            
            # Apartment types
            if criteria.apartment_types:
                conditions.append(Listing.apartment_type.in_(criteria.apartment_types))
            
            # Price range (but include POA listings!)
            if criteria.min_price:
                # Include POA listings OR listings >= min_price
                conditions.append(
                    or_(
                        Listing.price_czk <= self.POA_PRICE_THRESHOLD,
                        Listing.price_czk >= criteria.min_price,
                    )
                )
            
            if criteria.max_price:
                # Include POA listings OR listings <= max_price
                conditions.append(
                    or_(
                        Listing.price_czk <= self.POA_PRICE_THRESHOLD,
                        Listing.price_czk <= criteria.max_price,
                    )
                )
            
            # Area
            if criteria.min_area_m2:
                conditions.append(Listing.area_m2 >= Decimal(str(criteria.min_area_m2)))
            
            if criteria.max_area_m2:
                conditions.append(Listing.area_m2 <= Decimal(str(criteria.max_area_m2)))
            
            # Floor
            if criteria.avoid_ground_floor or criteria.reject_ground_floor:
                conditions.append(or_(Listing.floor.is_(None), Listing.floor > 0))
            
            if criteria.min_floor is not None:
                conditions.append(or_(Listing.floor.is_(None), Listing.floor >= criteria.min_floor))
            
            if criteria.max_floor is not None:
                conditions.append(or_(Listing.floor.is_(None), Listing.floor <= criteria.max_floor))
            
            # Districts
            if criteria.districts:
                district_conditions = [Listing.district.ilike(f"%{d}%") for d in criteria.districts]
                conditions.append(or_(*district_conditions))
            
            if criteria.exclude_districts:
                for district in criteria.exclude_districts:
                    conditions.append(~Listing.district.ilike(f"%{district}%"))
            
            # Photos
            if criteria.min_photos:
                conditions.append(Listing.image_count >= criteria.min_photos)
            
            if criteria.require_floor_plan:
                conditions.append(Listing.has_floor_plan == True)
            
            # Listing age filter
            if criteria.max_listing_age:
                from datetime import timedelta
                age_days_map = {"day": 2, "week": 8, "month": 31}
                age_str = criteria.max_listing_age
                if isinstance(age_str, int) or (isinstance(age_str, str) and age_str.isdigit()):
                    days = int(age_str)
                else:
                    days = age_days_map.get(age_str, 31)
                cutoff = datetime.utcnow() - timedelta(days=days)
                conditions.append(Listing.first_seen_at >= cutoff)
                logger.debug(f"Age filter: listings from last {days} days (since {cutoff.date()})")
            
            # Skip already scored?
            if agent.skip_already_scored:
                conditions.append(Listing.ai_analysis.is_(None))
            
            logger.debug(f"Built {len(conditions)} query conditions")
            
            stmt = (
                select(Listing)
                .where(and_(*conditions))
                .options(selectinload(Listing.price_history))
                .limit(agent.max_listings_to_process)
                .order_by(Listing.first_seen_at.desc())  # Newest first
            )
            
            result = await session.execute(stmt)
            listings = result.scalars().all()
            
            logger.debug(f"Query returned {len(listings)} listings")
            
            # Convert to dicts for processing
            processed = []
            for listing in listings:
                # Build price history summary
                history = sorted(listing.price_history, key=lambda h: h.recorded_at)
                initial_price = history[0].price_czk if history else listing.price_czk
                
                # For POA listings, find the last real (non-POA) price
                original_price = None
                if listing.price_czk <= self.POA_PRICE_THRESHOLD:
                    for h in reversed(history):
                        if h.price_czk > self.POA_PRICE_THRESHOLD:
                            original_price = h.price_czk
                            break
                
                # Price change from initial to current
                price_changes = [
                    {
                        "type": h.change_type,
                        "amount": h.change_amount,
                        "percent": float(h.change_percent) if h.change_percent else None,
                        "price": h.price_czk,
                        "date": h.recorded_at.strftime("%Y-%m-%d"),
                    }
                    for h in history
                    if h.change_type != "initial"
                ]
                
                processed.append({
                    "id": str(listing.id),
                    "external_id": listing.external_id,
                    "title": listing.title,
                    "description": listing.description,
                    "price_czk": listing.price_czk,
                    "price_per_m2": listing.price_per_m2,
                    "initial_price": initial_price,
                    "original_price": original_price,  # Last real price for POA listings
                    "price_changes": price_changes,
                    "area_m2": float(listing.area_m2) if listing.area_m2 else None,
                    "apartment_type": listing.apartment_type,
                    "city": listing.city,
                    "district": listing.district,
                    "address": listing.address,
                    "floor": listing.floor,
                    "total_floors": listing.total_floors,
                    "features": listing.features,
                    "raw_labels": listing.raw_labels,
                    "image_count": listing.image_count,
                    "has_floor_plan": listing.has_floor_plan,
                    "has_video": listing.has_video,
                    "url": listing.url,
                    "first_seen_at": listing.first_seen_at.isoformat() if listing.first_seen_at else None,
                    "last_seen_at": listing.last_seen_at.isoformat() if listing.last_seen_at else None,
                    "listed_at": listing.updated_at_source.isoformat() if listing.updated_at_source else None,
                })
            return processed
    
    async def _process_listing(self, listing: dict[str, Any], fetch_description: bool = True) -> dict[str, Any] | None:
        """
        Process a single listing: optionally fetch description, then score it.
        
        Args:
            listing: The listing dict
            fetch_description: Whether to fetch description from API (set False for first pass)
        """
        listing_id = listing["id"]
        is_poa = self.is_poa_listing(listing["price_czk"])
        
        if is_poa:
            self.stats["poa_listings"] += 1
            logger.debug(f"POA listing detected: {listing_id}")
        
        # Fetch description/date if needed, enabled, and requested
        needs_desc = not listing.get("description")
        needs_date = not listing.get("listed_at")
        if fetch_description and (needs_desc or needs_date) and self.config.agent.fetch_descriptions:
            description, source_date = await self._fetch_description(listing_id, listing["external_id"])
            if source_date and not listing.get("listed_at"):
                listing["listed_at"] = source_date
            if description and needs_desc:
                listing["description"] = description
                self.stats["descriptions_fetched"] += 1
                await self._save_description(listing_id, description)
        
        # Skip if no description and agent says skip
        if not listing.get("description"):
            if self.config.agent.poa_evaluation_mode == "skip" and is_poa:
                self.stats["skipped"] += 1
                return None
        
        # Score the listing
        score_result = await self._score_listing(listing, is_poa)
        
        # Save to database
        await self._save_score(listing_id, score_result)
        
        return {
            **listing,
            "score": score_result["score"],
            "analysis": score_result,
            "is_poa": is_poa,
        }
    
    async def _fetch_description(self, listing_id: str, external_id: str) -> tuple[str | None, str | None]:
        """Fetch description from sreality API.
        
        Also extracts and saves the "Aktualizace" (updated) date if available.
        
        Returns:
            Tuple of (description, aktualizace_date_iso) - either can be None.
        
        Raises:
            ListingGoneError: If the listing returns 410 Gone (sold/removed).
        """
        import httpx
        from sussed.scrapers.sreality import SrealityScraper
        
        try:
            scraper = SrealityScraper()
            async with httpx.AsyncClient() as client:
                hash_id = int(external_id)
                details = await scraper.fetch_listing_details(client, hash_id)
                
                if not details:
                    return None, None
                
                # Extract "Aktualizace" date from items
                source_date: str | None = None
                for item in details.get("items", []):
                    if item.get("type") == "edited" and item.get("name") == "Aktualizace" and item.get("value"):
                        source_date = item["value"]
                        await self._save_source_date(listing_id, source_date)
                        break
                
                # Convert to ISO for the listing dict
                source_date_iso: str | None = None
                if source_date:
                    try:
                        from datetime import datetime as dt
                        source_date_iso = dt.strptime(source_date, "%d.%m.%Y").isoformat()
                    except ValueError:
                        pass
                
                # Extract description
                description = None
                if "text" in details and "value" in details["text"]:
                    description = details["text"]["value"]
                
                return description, source_date_iso
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 410:
                await self._mark_listing_sold(listing_id)
                raise ListingGoneError(listing_id, external_id) from e
            logger.warning(f"Failed to fetch description for {listing_id}: {e}")
            return None, None
        except Exception as e:
            logger.warning(f"Failed to fetch description for {listing_id}: {e}")
            return None, None
    
    async def _save_description(self, listing_id: str, description: str) -> None:
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

    async def _save_source_date(self, listing_id: str, date_str: str) -> None:
        """Save 'Aktualizace' date from sreality detail API.
        
        Keeps the oldest date we've ever seen as a rough lower bound
        for listing age (sreality doesn't expose 'Vloženo'/created date).
        """
        from datetime import datetime as dt
        from sqlmodel import select
        from sussed.db.connection import get_session
        from sussed.db.models import Listing
        
        try:
            api_date = dt.strptime(date_str, "%d.%m.%Y")
        except ValueError:
            return
        
        async with get_session() as session:
            stmt = select(Listing).where(Listing.id == listing_id)
            result = await session.execute(stmt)
            listing = result.scalar_one_or_none()
            
            if listing:
                # Keep the oldest date as best approximation
                if listing.updated_at_source is None or api_date < listing.updated_at_source:
                    listing.updated_at_source = api_date
                    session.add(listing)
                    await session.commit()
                    logger.debug(f"Saved source date {date_str} for {listing_id}")

    async def _mark_listing_sold(self, listing_id: str) -> None:
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
    
    async def _llm_analyze(self, listing: dict[str, Any]) -> "ListingAnalysis | None":
        """
        Analyze listing with LLM for deep natural language understanding.
        
        THIS IS THE REAL AI! 🧠
        
        Returns ListingAnalysis with score adjustment and insights.
        """
        if not self._llm_analyzer or not listing.get("description"):
            return None
        
        from sussed.agent.llm_analyzer import ListingAnalysis
        
        try:
            result = await self._llm_analyzer.analyze_listing(
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
    
    async def _score_listing(self, listing: dict[str, Any], is_poa: bool) -> dict[str, Any]:
        """
        Score a listing based on config criteria.
        
        For POA (1 Kč) listings, we skip price calculations entirely!
        """
        criteria = self.config.criteria
        scoring = self.config.scoring
        
        score = 500  # Start at average
        reasons = []
        red_flags = []
        highlights = []
        
        # === DESCRIPTION BONUS ===
        # Having a description at all is valuable for analysis
        has_description = bool(listing.get("description"))
        if has_description:
            score += 20  # Bonus for having analyzable description
            highlights.append("Has description")
        
        # === PRICE ANALYSIS (skip for POA) ===
        if is_poa:
            reasons.append("Price on Request - evaluated on features only")
            # Don't penalize, don't reward - neutral on price
        else:
            # Price vs market average
            if listing.get("price_per_m2"):
                price_per_m2 = listing["price_per_m2"]
                
                # Get market stats for comparison
                market_avg = await self._get_market_average(listing.get("city"), listing.get("apartment_type"))
                
                if market_avg:
                    price_ratio = price_per_m2 / market_avg
                    
                    if price_ratio < 0.8:
                        score += 150
                        highlights.append(f"Below market avg ({price_ratio:.0%} of avg)")
                    elif price_ratio < 0.95:
                        score += 75
                        highlights.append(f"Good price ({price_ratio:.0%} of avg)")
                    elif price_ratio > 1.2:
                        score -= 100
                        red_flags.append(f"Above market ({price_ratio:.0%} of avg)")
                    elif price_ratio > 1.1:
                        score -= 50
                        reasons.append(f"Slightly overpriced ({price_ratio:.0%} of avg)")
            
            # Max price per m² check
            if criteria.max_price_per_m2 and listing.get("price_per_m2"):
                if listing["price_per_m2"] > criteria.max_price_per_m2:
                    score -= 100
                    red_flags.append(f"Over max price/m² ({listing['price_per_m2']:,} > {criteria.max_price_per_m2:,})")
                else:
                    score += 25
                    highlights.append(f"Within price/m² target")
        
        # === AREA ANALYSIS ===
        if listing.get("area_m2"):
            area = listing["area_m2"]
            
            if criteria.min_area_m2 and area >= criteria.min_area_m2:
                score += 25
                highlights.append(f"Meets min area ({area} m²)")
            
            # Bonus for spacious
            if area > 70:
                score += 25
                highlights.append("Spacious")
        
        # === LOCATION ===
        district = listing.get("district", "").lower()
        address = listing.get("address", "").lower()
        location_text = f"{district} {address}"  # Check both!
        
        if self.config.preferred_districts:
            for i, pref in enumerate(self.config.preferred_districts):
                if pref.lower() in location_text:
                    bonus = max(50 - (i * 10), 10)  # First choice = 50pts, decreasing
                    score += bonus
                    highlights.append(f"Preferred district: {pref}")
                    break
        
        if self.config.avoid_districts:
            for avoid in self.config.avoid_districts:
                if avoid.lower() in location_text:
                    score -= 100
                    red_flags.append(f"Avoided district: {avoid}")
        
        # Check known bad locations (streets like Cejl, Bratislavská, etc.)
        if self.config.known_bad_locations:
            for bad_loc in self.config.known_bad_locations:
                if bad_loc.lower() in location_text:
                    score -= 150  # Heavy penalty for known problem areas
                    red_flags.append(f"⚠️ Bad location: {bad_loc}")
                    break  # Only penalize once
        
        # === FEATURES ===
        labels = listing.get("raw_labels", []) or []
        labels_flat = [str(l).lower() for l in labels]
        
        # Check for required features
        has_parking = any("park" in l or "garáž" in l for l in labels_flat)
        has_balcony = any("balk" in l or "lodž" in l or "tera" in l for l in labels_flat)
        has_cellar = any("sklep" in l for l in labels_flat)
        has_elevator = any("výtah" in l for l in labels_flat)
        
        if criteria.require_parking:
            if has_parking:
                score += 50
                highlights.append("Has parking ✓")
            else:
                score += scoring.penalty_no_parking
                red_flags.append("Missing parking")
        elif has_parking:
            score += 25
            highlights.append("Parking available")
        
        if criteria.require_balcony:
            if has_balcony:
                score += 50
                highlights.append("Has balcony/loggia ✓")
            else:
                score -= 50
                red_flags.append("Missing balcony")
        elif has_balcony:
            score += 20
            highlights.append("Has outdoor space")
        
        if has_elevator:
            score += 20
            highlights.append("Elevator available")
        
        if has_cellar:
            score += 10
            highlights.append("Has cellar/storage")
        
        # === LISTING QUALITY ===
        image_count = listing.get("image_count", 0)
        
        if image_count >= 15:
            score += 25
            highlights.append(f"Many photos ({image_count})")
        elif image_count >= 8:
            score += 10
        elif image_count < criteria.min_photos:
            score -= 30
            red_flags.append(f"Too few photos ({image_count})")
        
        if listing.get("has_floor_plan"):
            score += 30
            highlights.append("Floor plan available")
        elif criteria.require_floor_plan:
            score -= 50
            red_flags.append("Missing floor plan")
        
        if listing.get("has_video"):
            score += 15
            highlights.append("Video tour")
        
        if listing.get("has_3d_tour"):
            score += 20
            highlights.append("3D tour available")
        
        # === FLOOR ===
        floor = listing.get("floor")
        total_floors = listing.get("total_floors")
        
        if floor is not None:
            if floor == 0 and (criteria.avoid_ground_floor or criteria.reject_ground_floor):
                score -= 75
                red_flags.append("Ground floor")
            
            if total_floors and floor == total_floors and criteria.avoid_top_floor:
                score -= 50
                red_flags.append("Top floor (potential roof issues)")
            
            if 1 <= floor <= 3:
                score += 10
                highlights.append(f"Good floor ({floor})")
        
        # === DESCRIPTION ANALYSIS ===
        description = listing.get("description", "")
        if description:
            desc_lower = description.lower()
            
            # === HARD REJECT: exclude_description_keywords ===
            # If ANY of these keywords appear, listing is auto-rejected (score = -1 = SUS)
            exclude_kws = criteria.get_exclude_keywords()
            if exclude_kws:
                for keyword in exclude_kws:
                    if keyword in desc_lower:
                        return {
                            "score": -1,
                            "reasons": [f"Auto-rejected: description contains '{keyword}'"],
                            "highlights": [],
                            "red_flags": [f"🚫 Excluded keyword: '{keyword}'"],
                        }
            
            # Red flag keywords - things that suggest problems or overselling
            sketchy_keywords: dict[str, int] = {
                "investic": -40,      # "investment opportunity" = overpriced or needs work
                "potenciál": -30,     # "potential" = needs work
                "příležitost": -30,   # "opportunity" = catch
                "k rekonstrukci": -50, # needs reconstruction
                "před rekonstrukc": -40,  # before reconstruction
                "nutná rekonstrukce": -60,  # reconstruction needed
                "vhodné k": -20,      # "suitable for" investment = problematic
                "ideální pro": -10,   # "ideal for" often overselling
                "rušná": -30,         # "busy" street
                "frekventovan": -25,  # frequented = noisy
                "suterén": -40,       # basement
                "bez výtahu": -20,    # no elevator (bad for higher floors)
            }
            
            # Merge user-defined penalty keywords from config (lowercased for matching)
            if scoring.penalize_description_keywords:
                sketchy_keywords.update(
                    {k.lower(): v for k, v in scoring.penalize_description_keywords.items()}
                )
            
            for word, penalty in sketchy_keywords.items():
                if word in desc_lower:
                    score += penalty  # penalty is negative
                    red_flags.append(f"'{word}' ({penalty})")
            
            # Positive keywords - things that add value
            good_keywords: dict[str, int] = {
                "po rekonstrukci": 40,    # after reconstruction = good!
                "kompletní rekonstrukce": 50,  # complete reconstruction
                "nový": 15,
                "moderní": 15,
                "zateplení": 25,          # insulation = lower bills
                "plastová okna": 20,      # plastic windows
                "klimatizac": 30,         # AC
                "podlahové topení": 25,   # floor heating
                "krbov": 15,              # fireplace
                "lodžie": 15,             # loggia
                "balkon": 15,             # balcony
                "terasa": 20,             # terrace
                "zahrad": 20,             # garden
                "garáž": 25,              # garage
                "parkovací stání": 20,    # parking spot
                "sklep": 10,              # cellar
                "komora": 10,             # storage room
                "tichá": 20,              # quiet location
                "klidná": 20,             # peaceful location
                "výhled": 15,             # view
                "slunný": 15,             # sunny
                "světlý": 10,             # bright
                "cihlový": 15,            # brick building (not panel)
                "nízké poplatky": 20,     # low fees
                "nízké náklady": 20,      # low costs
            }
            
            # Merge user-defined bonus keywords from config (lowercased for matching)
            if scoring.bonus_description_keywords:
                good_keywords.update(
                    {k.lower(): v for k, v in scoring.bonus_description_keywords.items()}
                )
            
            found_good = set()  # Avoid duplicates
            for word, bonus in good_keywords.items():
                if word in desc_lower and word not in found_good:
                    score += bonus
                    highlights.append(f"'{word}' (+{bonus})")
                    found_good.add(word)
            
            # Energy rating detection
            energy_ratings = {
                "energetická třída a": 40,
                "energetická třída b": 30,
                "energetická třída c": 15,
                "penb a": 40,
                "penb b": 30,
                "penb c": 15,
                "penb g": -30,  # Worst rating
                "penb f": -20,
            }
            
            for rating, points in energy_ratings.items():
                if rating in desc_lower:
                    if points > 0:
                        highlights.append(f"Energy rating: {rating.upper()} (+{points})")
                    else:
                        red_flags.append(f"Poor energy rating: {rating.upper()} ({points})")
                    score += points
                    break  # Only count one rating
            
            # Parking price detection (important for true cost calculation)
            import re
            parking_patterns = [
                r'parkov\w*\s*(\d+)\s*(?:tis|000)',  # "parking 500 tis" or "parking 500000"
                r'garáž\w*\s*(\d+)\s*(?:tis|000)',
                r'stání\s*(\d+)\s*(?:tis|000)',
            ]
            
            for pattern in parking_patterns:
                match = re.search(pattern, desc_lower)
                if match:
                    parking_price = int(match.group(1))
                    if parking_price < 100:  # Probably in thousands
                        parking_price *= 1000
                    red_flags.append(f"Separate parking: ~{parking_price:,} Kč")
                    # Don't penalize, just note it for awareness
                    break
        
        # === FINAL ADJUSTMENTS ===
        
        # Clamp score to valid range
        if score >= 900:
            # Potential gem - but let's not auto-assign 9999
            reasons.append("HIGH SCORE - potential gem! 💎")
        
        if score < 0:
            score = 0
        elif score > 1000:
            score = 1000
        
        # Determine category
        if len(red_flags) >= 3:
            reasons.append("Multiple red flags present")
        
        if len(highlights) >= 5:
            reasons.append("Many positive features")
        
        return {
            "score": score,
            "reasons": reasons,
            "red_flags": red_flags,
            "highlights": highlights,
            "is_poa": is_poa,
            "scored_at": datetime.utcnow().isoformat(),
        }
    
    async def _get_market_average(self, city: str | None, apartment_type: str | None) -> int | None:
        """Get market average price per m² for comparison."""
        from sqlalchemy import func
        from sqlmodel import select, and_
        from sussed.db.connection import get_session
        from sussed.db.models import Listing, ListingStatus
        
        async with get_session() as session:
            conditions = [
                Listing.status == ListingStatus.ACTIVE,
                Listing.price_czk > self.POA_PRICE_THRESHOLD,  # Exclude POA
                Listing.price_per_m2.isnot(None),
            ]
            
            if city:
                conditions.append(Listing.city.ilike(f"%{city}%"))
            
            if apartment_type:
                conditions.append(Listing.apartment_type == apartment_type)
            
            stmt = select(func.avg(Listing.price_per_m2)).where(and_(*conditions))
            result = await session.execute(stmt)
            avg = result.scalar()
            
            return int(avg) if avg else None
    
    async def _save_score(self, listing_id: str, score_result: dict[str, Any]) -> None:
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
            
            listing.vibe_check = vibe
            listing.ai_analysis = score_result
            
            session.add(listing)
            await session.commit()
    
    def _prepare_output(self, listings: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Sort and filter based on output config."""
        output = self.config.output
        
        # Filter by mode
        if output.mode == OutputMode.GEMS:
            listings = [l for l in listings if l.get("score", 0) >= 900]
        elif output.mode == OutputMode.SUS:
            listings = [l for l in listings if l.get("score", 0) < 200 or l.get("score", 0) == -1]
        elif output.mode == OutputMode.TRASH:
            listings = [l for l in listings if l.get("score", 0) < 500]
        elif output.mode in (OutputMode.BEST, OutputMode.ALL):
            # Exclude auto-rejected listings (score -1) from best/all views
            listings = [l for l in listings if l.get("score", 0) != -1]
        
        # Sort
        if output.mode in (OutputMode.BEST, OutputMode.GEMS, OutputMode.ALL):
            listings.sort(key=lambda x: x.get("score", 0), reverse=True)
        else:  # TRASH, SUS - show worst first
            listings.sort(key=lambda x: x.get("score", 0))
        
        # Limit
        return listings[:output.limit]
    
    def _display_results(self, results: list[dict[str, Any]]) -> None:
        """Display results in requested format."""
        output = self.config.output
        
        if not results:
            console.print("\n[yellow]No results matching output criteria[/yellow]")
            return
        
        console.print(f"\n[bold green]Results ({len(results)} listings):[/bold green]\n")
        
        if output.format == "json":
            console.print_json(json.dumps(results, indent=2, ensure_ascii=False, default=str))
        elif output.format == "markdown":
            self._display_markdown(results)
        else:
            self._display_table(results)
        
        # Save to file if requested
        if output.save_to_file:
            self._save_results(results)
    
    def _display_table(self, results: list[dict[str, Any]]) -> None:
        """Display results as Rich table."""
        table = Table(show_header=True, header_style="bold blue")
        
        table.add_column("Score", style="cyan", justify="right", width=6)
        table.add_column("Type", width=6)
        table.add_column("Price", justify="right", width=12)
        table.add_column("m²", justify="right", width=5)
        table.add_column("Location", width=20)
        table.add_column("Highlights", width=35)
        
        for r in results:
            score = r.get("score", 0)
            
            # Color score based on value
            if score >= 800:
                score_str = f"[bold green]{score}[/bold green]"
            elif score >= 500:
                score_str = f"[green]{score}[/green]"
            elif score >= 300:
                score_str = f"[yellow]{score}[/yellow]"
            else:
                score_str = f"[red]{score}[/red]"
            
            # Price display (handle POA)
            if r.get("is_poa"):
                if r.get("original_price"):
                    price_str = f"[dim]POA[/dim] ({r['original_price']:,})"
                else:
                    price_str = "[dim]POA[/dim]"
            else:
                price = r.get("price_czk", 0)
                initial = r.get("initial_price", price)
                if initial and initial > 10 and initial != price:
                    diff = price - initial
                    arrow = "↓" if diff < 0 else "↑"
                    price_str = f"{price:,} {arrow}"
                else:
                    price_str = f"{price:,}"
            
            # Highlights (truncated)
            highlights = r.get("analysis", {}).get("highlights", [])
            highlights_str = ", ".join(highlights[:3])
            if len(highlights) > 3:
                highlights_str += "..."
            
            location = r.get("city", "")
            if r.get("district"):
                location = f"{r.get('district')}, {location}"
            
            table.add_row(
                score_str,
                r.get("apartment_type", "-"),
                price_str,
                str(int(r.get("area_m2", 0))) if r.get("area_m2") else "-",
                location[:20],
                highlights_str[:35],
            )
        
        console.print(table)
        
        # Show details for ALL results
        if self.config.output.include_analysis and results:
            console.print("\n[bold]━━━ Detailed Analysis ━━━[/bold]")
            
            for i, r in enumerate(results, 1):
                analysis = r.get("analysis", {})
                score = r.get("score", 0)
                
                # Score color
                if score >= 800:
                    score_style = "bold green"
                elif score >= 500:
                    score_style = "green"
                elif score >= 300:
                    score_style = "yellow"
                else:
                    score_style = "red"
                
                console.print(f"\n[bold cyan]#{i}. {r.get('title', 'Unknown')}[/bold cyan]")
                console.print(f"   [{score_style}]Score: {score}[/{score_style}]")
                console.print(f"   [link={r.get('url', '')}]{r.get('url', 'N/A')}[/link]")
                
                # Price info
                if r.get("is_poa"):
                    if r.get("original_price"):
                        orig = r["original_price"]
                        area = r.get("area_m2")
                        if area and area > 0:
                            console.print(f"   💰 Price: [dim]POA[/dim] (was {orig:,} Kč / {int(orig/area):,} Kč/m²)")
                        else:
                            console.print(f"   💰 Price: [dim]POA[/dim] (was {orig:,} Kč)")
                    else:
                        console.print("   💰 Price: [dim]POA (Price on Request)[/dim]")
                else:
                    price = r.get("price_czk", 0)
                    area = r.get("area_m2")
                    if area and area > 0:
                        console.print(f"   💰 Price: {price:,} Kč ({int(price/area):,} Kč/m²)")
                    else:
                        console.print(f"   💰 Price: {price:,} Kč")
                
                # Price change info
                price_changes = r.get("price_changes", [])
                if price_changes:
                    initial = r.get("initial_price", 0)
                    current = r.get("price_czk", 0)
                    # Show total change from initial to current (skip if POA on either end)
                    if initial > self.POA_PRICE_THRESHOLD and current > self.POA_PRICE_THRESHOLD:
                        diff = current - initial
                        if diff != 0:
                            pct = (diff / initial) * 100
                            arrow = "📉" if diff < 0 else "📈"
                            color = "green" if diff < 0 else "red"
                            console.print(f"   {arrow} [{color}]Price change: {diff:+,} Kč ({pct:+.1f}%) from {initial:,} Kč[/{color}]")
                
                # Listing dates — sreality only exposes "Aktualizace" (modified),
                # not "Vloženo" (created). Show approximate listed date from the
                # older of source date / first scraped, plus updated if different.
                date_parts = []
                listed_at = r.get("listed_at")  # "Aktualizace" from source
                first_seen = r.get("first_seen_at")  # When we first scraped it
                
                aktualizace = listed_at[:10] if listed_at else None
                first = first_seen[:10] if first_seen else None
                
                if aktualizace and first:
                    listed_date = min(aktualizace, first)
                    date_parts.append(f"listed ~{listed_date}")
                    # Only show "updated" if it differs from listed
                    if aktualizace != listed_date:
                        date_parts.append(f"updated {aktualizace}")
                elif aktualizace:
                    date_parts.append(f"updated {aktualizace}")
                elif first:
                    date_parts.append(f"listed ~{first}")
                
                if date_parts:
                    console.print(f"   [dim]📅 {' · '.join(date_parts)}[/dim]")
                
                if analysis.get("highlights"):
                    console.print(f"   [green]✅ {', '.join(analysis['highlights'][:6])}[/green]")
                
                if analysis.get("red_flags"):
                    console.print(f"   [red]🚩 {', '.join(analysis['red_flags'][:4])}[/red]")
                
                # Show LLM insights if available
                llm = r.get("llm_analysis")
                if llm:
                    console.print(f"   [bold magenta]🧠 AI:[/bold magenta] {llm.get('one_liner', 'N/A')}")
                    console.print(f"   [bold]Recommendation: {llm.get('recommendation', 'N/A')}[/bold]")
                    if llm.get("hidden_costs"):
                        costs = ", ".join(f"{k}: {v:,} Kč" for k, v in llm["hidden_costs"].items() if v)
                        if costs:
                            console.print(f"   [yellow]💰 Hidden costs: {costs}[/yellow]")
                
                console.print("   [dim]─────────────────────────────────────────[/dim]")
    
    def _display_markdown(self, results: list[dict[str, Any]]) -> None:
        """Display results as markdown."""
        lines = [
            f"# {self.config.name} - Results",
            "",
            f"**Total results:** {len(results)}",
            f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "",
            "---",
            "",
        ]
        
        for i, r in enumerate(results, 1):
            score = r.get("score", 0)
            analysis = r.get("analysis", {})
            
            # Price display
            if r.get("is_poa"):
                price_display = "POA"
            else:
                price_display = f"{r.get('price_czk', 0):,} Kč"
            
            lines.extend([
                f"## {i}. Score: {score} - {r.get('title', 'Unknown')}",
                "",
                f"- **Price:** {price_display}",
                f"- **Type:** {r.get('apartment_type', 'Unknown')}",
                f"- **Area:** {r.get('area_m2', 'Unknown')} m²",
                f"- **Location:** {r.get('district', '')}, {r.get('city', '')}",
                f"- **URL:** {r.get('url', 'N/A')}",
                "",
            ])
            
            if analysis.get("highlights"):
                lines.append("**Highlights:**")
                for h in analysis["highlights"]:
                    lines.append(f"- ✅ {h}")
                lines.append("")
            
            if analysis.get("red_flags"):
                lines.append("**Red flags:**")
                for f in analysis["red_flags"]:
                    lines.append(f"- 🚩 {f}")
                lines.append("")
            
            lines.extend(["---", ""])
        
        console.print("\n".join(lines))
    
    def _save_results(self, results: list[dict[str, Any]]) -> None:
        """Save results to file."""
        output = self.config.output
        path = output.save_to_file
        
        if not path:
            return
        
        if output.format == "json":
            content = json.dumps(results, indent=2, ensure_ascii=False, default=str)
        else:
            # Default to simple text
            lines = []
            for r in results:
                lines.append(f"{r.get('score', 0)}\t{r.get('title', '')}\t{r.get('url', '')}")
            content = "\n".join(lines)
        
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        
        console.print(f"\n[green]✅ Saved to {path}[/green]")
    
    def _display_stats(self) -> None:
        """Display processing stats."""
        console.print("\n[bold]Processing stats:[/bold]")
        
        table = Table(show_header=False, box=None)
        table.add_column("Metric", style="dim")
        table.add_column("Value", style="cyan")
        
        table.add_row("Total processed", str(self.stats["total_processed"]))
        table.add_row("Descriptions fetched", str(self.stats["descriptions_fetched"]))
        table.add_row("🧠 LLM analyzed", str(self.stats["llm_analyzed"]))
        table.add_row("Scored", str(self.stats["scored"]))
        table.add_row("POA listings (1 Kč)", str(self.stats["poa_listings"]))
        table.add_row("Skipped", str(self.stats["skipped"]))
        table.add_row("Errors", str(self.stats["errors"]))
        
        console.print(table)


async def run_autonomous(config_path: str | None = None, config: SearchConfig | None = None) -> list[dict[str, Any]]:
    """
    Main entry point for autonomous mode.
    
    Args:
        config_path: Path to YAML config file
        config: SearchConfig object (if already loaded)
    
    Returns:
        List of processed/scored listings
    """
    if config is None:
        if config_path:
            config = SearchConfig.from_yaml(config_path)
        else:
            # Use example config if none provided
            console.print("[yellow]No config provided, using example config[/yellow]")
            config = SearchConfig.example()
    
    runner = AutonomousRunner(config)
    return await runner.run()


def run_autonomous_sync(config_path: str | None = None, config: SearchConfig | None = None) -> list[dict[str, Any]]:
    """Sync wrapper for run_autonomous."""
    return asyncio.run(run_autonomous(config_path=config_path, config=config))
