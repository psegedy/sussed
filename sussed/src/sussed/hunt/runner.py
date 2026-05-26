"""Autonomous hunt runner orchestration 🎯."""

from __future__ import annotations

import asyncio
from datetime import datetime
from decimal import Decimal
from typing import Any

from loguru import logger
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn

from sussed.hunt.config import SearchConfig
from sussed.hunt.fetcher import (
    fetch_description,
    llm_analyze,
    save_description,
    save_score,
)
from sussed.hunt.formatter import (
    display_market_insights,
    display_results,
    display_stats,
    prepare_output,
)
from sussed.hunt.scorer import score_listing

console = Console()


def sort_key(listing: dict[str, Any], config: SearchConfig) -> tuple[Any, ...]:
    """Sort-key tuple for ranking processed listings.

    All components are ordered so higher = better (use with reverse=True).
    Tiebreakers kick in when the headline score is clamped at the 1000 cap,
    which happens often for high-quality listings. Returned tuple, in order:

    1. score (clamped, primary)
    2. new_building bonus (1 if features.new_building else 0)
    3. very-good condition bonus (1 if condition contains "velmi dobr" else 0)
    4. preferred-district rank (higher = better; 0 if not in list)
    5. parking presence (1 if any parking signal else 0)
    6. image count (proxy for listing quality)
    7. negative price_per_m2 (cheaper wins; 0 for POA / missing)
    """
    score = listing.get("score", 0)
    features = listing.get("features") or {}
    has_new_build = 1 if features.get("new_building") else 0
    cond = (features.get("building_condition") or "").lower()
    very_good = 1 if "velmi dobr" in cond else 0

    district = (listing.get("district") or "").lower()
    address = (listing.get("address") or "").lower()
    location_text = f"{district} {address}"

    preferred = config.preferred_districts or []
    pref_rank = 0
    for i, name in enumerate(preferred):
        if name.lower() in location_text:
            pref_rank = len(preferred) - i
            break

    parking_signal = (
        1
        if features.get("parking") or features.get("garage") or features.get("parking_lots")
        else 0
    )

    image_count = listing.get("image_count") or 0
    price_per_m2 = listing.get("price_per_m2") or 0
    neg_ppm2 = -price_per_m2

    return (score, has_new_build, very_good, pref_rank, parking_signal, image_count, neg_ppm2)


class ListingGoneError(Exception):
    """Raised when a listing returns 410 Gone (sold/removed)."""

    def __init__(self, listing_id: str, external_id: str) -> None:
        self.listing_id = listing_id
        self.external_id = external_id
        super().__init__(f"Listing {external_id} is gone (410)")


class AutonomousRunner:
    """The autonomous hunt runner - processes listings based on config."""

    # 1 Kč = Price on Request threshold
    POA_PRICE_THRESHOLD = 10  # Anything under 10 CZK is POA

    # Photo cache shared with `sussed review prepare`
    IMAGE_CACHE_DIR = ".sussed/image-cache"
    IMAGE_CACHE_LIMIT = 5

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
            from sussed.hunt.llm_analyzer import get_llm_analyzer

            self._llm_analyzer = get_llm_analyzer(
                model_provider=self.config.agent.llm_provider,
                model_id=self.config.agent.llm_model,
            )

            if self._llm_analyzer.is_available:
                console.print(
                    f"[green]✓ LLM analyzer ready ({self.config.agent.llm_provider})[/green]"
                )
            else:
                console.print(
                    f"[yellow]⚠ LLM not available: {self._llm_analyzer.initialization_error}[/yellow]"
                )
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
            console.print(
                "[yellow]No listings found matching criteria. Try scraping first![/yellow]"
            )
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
            # Sort by score and quality tiebreakers, get top N for description fetching
            processed.sort(key=lambda item: sort_key(item, self.config), reverse=True)

            fetch_count = min(self.config.agent.enrich_top_n, len(processed))
            top_candidates = processed[:fetch_count]

            console.print(
                f"\n[cyan]Pass 2: Fetching descriptions for top {fetch_count} candidates...[/cyan]"
            )

            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                console=console,
            ) as progress:
                task = progress.add_task("Fetching descriptions...", total=fetch_count)

                sold_ids: set[str] = set()
                for listing in top_candidates:
                    # Skip listings that already have a description from a
                    # previous hunt/scrape — no need to re-fetch.
                    if listing.get("description"):
                        progress.update(task, advance=1)
                        continue
                    try:
                        description, source_date = await self._fetch_description(
                            listing["id"],
                            listing["external_id"],
                            image_urls=listing.get("image_urls") or [],
                        )
                        if source_date and not listing.get("listed_at"):
                            listing["listed_at"] = source_date
                        if description:
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
                        console.print(
                            f"  [magenta]💀 {listing['title'][:40]}... SOLD/GONE[/magenta]"
                        )
                    except Exception as e:
                        logger.warning(f"Failed to fetch description: {e}")

                    progress.update(task, advance=1)

                    # Rate limiting - don't hammer the API
                    import asyncio

                    await asyncio.sleep(0.5)

            # Remove sold listings from results
            if sold_ids:
                processed = [p for p in processed if p["id"] not in sold_ids]
                console.print(
                    f"  [magenta]Removed {len(sold_ids)} sold/gone listing(s) from results[/magenta]"
                )

            # Re-sort after re-scoring
            processed.sort(key=lambda item: sort_key(item, self.config), reverse=True)

        # Step 4: Third pass - LLM analysis for top candidates (THE REAL AI!)
        if self._llm_analyzer and self._llm_analyzer.is_available and processed:
            llm_count = min(
                self.config.agent.llm_analyze_top_n,
                len(processed),
            )

            # Only analyze listings that have descriptions
            candidates_for_llm = [
                candidate
                for candidate in processed[: llm_count * 2]  # Look at more than we need
                if candidate.get("description")
            ][:llm_count]

            if candidates_for_llm:
                console.print(
                    f"\n[bold magenta]Pass 3: 🧠 LLM analysis for top {len(candidates_for_llm)} candidates...[/bold magenta]"
                )

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
                                new_score = max(
                                    0, min(1000, old_score + llm_result.score_adjustment)
                                )

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
                                    listing["analysis"]["llm_recommendation"] = (
                                        llm_result.recommendation
                                    )
                                    listing["analysis"]["score"] = new_score

                                # Save updated analysis to DB
                                await self._save_score(listing["id"], listing.get("analysis", {}))

                                self.stats["llm_analyzed"] += 1
                                logger.info(
                                    f"LLM: {listing['title'][:30]}... score {old_score}→{new_score} ({llm_result.recommendation})"
                                )

                        except Exception as e:
                            logger.warning(f"LLM analysis failed for {listing['id']}: {e}")

                        progress.update(task, advance=1)

                        # Rate limit LLM calls (they're expensive!)
                        await asyncio.sleep(1.0)

                # Final re-sort after LLM adjustments
                processed.sort(key=lambda item: sort_key(item, self.config), reverse=True)

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
                # Only re-fetch listings without a description. If description
                # is already cached from a previous hunt/scrape, skip the API
                # call entirely (we accept missing listed_at as the trade-off).
                needs_fetch = [r for r in results if not r.get("description")]
                if not needs_fetch:
                    break

                validation_round += 1
                console.print(
                    f"\n[cyan]Validation pass {validation_round}: Checking {len(needs_fetch)} listings...[/cyan]"
                )

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
                            description, source_date = await self._fetch_description(
                                listing["id"],
                                listing["external_id"],
                                image_urls=listing.get("image_urls") or [],
                            )
                            if source_date and not listing.get("listed_at"):
                                listing["listed_at"] = source_date
                            if description:
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
                            console.print(
                                f"  [magenta]💀 {listing['title'][:40]}... SOLD/GONE[/magenta]"
                            )
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

                results.sort(key=lambda item: sort_key(item, self.config), reverse=True)

            if total_gone:
                console.print(
                    f"  [magenta]Removed {total_gone} sold/gone listing(s) total across {validation_round} pass(es)[/magenta]"
                )

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

            console.print(
                f"[green]✅ Scraped {stats['listings_found']} listings "
                f"({stats['listings_new']} new, {stats['price_changes']} price changes)[/green]\n"
            )

            return stats
        except Exception as e:
            logger.error(f"Auto-scrape failed: {e}")
            console.print(f"[red]Auto-scrape failed: {e}[/red]")
            console.print("[yellow]Continuing with existing data...[/yellow]\n")
            return {}

    async def _get_matching_listings(self) -> list[dict[str, Any]]:
        """Query DB for listings matching config criteria."""
        from sqlalchemy.orm import selectinload
        from sqlmodel import and_, or_, select

        from sussed.db.connection import get_session
        from sussed.db.models import Listing, ListingStatus

        criteria = self.config.criteria
        agent = self.config.agent

        logger.debug(
            f"Query criteria: city={criteria.city}, types={criteria.apartment_types}, min_photos={criteria.min_photos}"
        )
        logger.debug(
            f"Agent config: skip_scored={agent.skip_already_scored}, max_process={agent.max_listings_to_process}"
        )

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
                conditions.append(Listing.has_floor_plan.is_(True))

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

                processed.append(
                    {
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
                        "image_urls": list(listing.image_urls) if listing.image_urls else [],
                        "image_count": listing.image_count,
                        "has_floor_plan": listing.has_floor_plan,
                        "has_video": listing.has_video,
                        "url": listing.url,
                        "first_seen_at": listing.first_seen_at.isoformat()
                        if listing.first_seen_at
                        else None,
                        "last_seen_at": listing.last_seen_at.isoformat()
                        if listing.last_seen_at
                        else None,
                        "listed_at": listing.updated_at_source.isoformat()
                        if listing.updated_at_source
                        else None,
                    }
                )
            return processed

    async def _process_listing(
        self, listing: dict[str, Any], fetch_description: bool = True
    ) -> dict[str, Any] | None:
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

        # Fetch description/date if needed, enabled, and requested.
        # If we already have a description from a previous hunt/scrape, skip the
        # API call entirely — even if listed_at is missing — to avoid redundant
        # network requests.
        needs_desc = not listing.get("description")
        if (
            fetch_description
            and needs_desc
            and self.config.agent.fetch_descriptions
        ):
            description, source_date = await self._fetch_description(
                listing_id,
                listing["external_id"],
                image_urls=listing.get("image_urls") or [],
            )
            if source_date and not listing.get("listed_at"):
                listing["listed_at"] = source_date
            if description:
                listing["description"] = description
                self.stats["descriptions_fetched"] += 1
                await self._save_description(listing_id, description)

        # Skip if no description and agent says skip
        if (
            not listing.get("description")
            and self.config.agent.poa_evaluation_mode == "skip"
            and is_poa
        ):
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

    async def _fetch_description(
        self,
        listing_id: str,
        external_id: str,
        image_urls: list[str] | None = None,
    ) -> tuple[str | None, str | None]:
        """Fetch description from sreality API and pre-warm the photo cache."""
        return await fetch_description(
            listing_id,
            external_id,
            image_urls=image_urls,
            image_cache_dir=self.IMAGE_CACHE_DIR,
            image_cache_limit=self.IMAGE_CACHE_LIMIT,
            gone_error_cls=ListingGoneError,
        )

    async def _save_description(self, listing_id: str, description: str) -> None:
        """Save fetched description to database."""
        await save_description(listing_id, description)

    async def _llm_analyze(self, listing: dict[str, Any]) -> Any | None:
        """Analyze listing with LLM for deep natural language understanding."""
        return await llm_analyze(self._llm_analyzer, listing)

    async def _score_listing(self, listing: dict[str, Any], is_poa: bool) -> dict[str, Any]:
        """Score a listing based on config criteria."""
        return await score_listing(self.config, listing, is_poa, self.POA_PRICE_THRESHOLD)

    async def _save_score(self, listing_id: str, score_result: dict[str, Any]) -> None:
        """Save the score to database."""
        await save_score(listing_id, score_result)

    def _prepare_output(self, listings: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Sort and filter based on output config."""
        return prepare_output(self.config, listings)

    def _display_results(self, results: list[dict[str, Any]]) -> None:
        """Display results in requested format."""
        display_results(self.config, results, self.POA_PRICE_THRESHOLD)

    async def _display_market_insights(self, scrape_stats: dict[str, Any] | None) -> None:
        """Display market insights."""
        await display_market_insights(scrape_stats)

    def _display_stats(self) -> None:
        """Display processing stats."""
        display_stats(self.stats)


async def run_autonomous(
    config_path: str | None = None, config: SearchConfig | None = None
) -> list[dict[str, Any]]:
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


def run_autonomous_sync(
    config_path: str | None = None, config: SearchConfig | None = None
) -> list[dict[str, Any]]:
    """Sync wrapper for run_autonomous."""
    return asyncio.run(run_autonomous(config_path=config_path, config=config))


async def run_hunt(
    config_path: str | None = None, config: SearchConfig | None = None
) -> list[dict[str, Any]]:
    """Main entry point for hunt mode."""
    return await run_autonomous(config_path=config_path, config=config)


def run_hunt_sync(
    config_path: str | None = None, config: SearchConfig | None = None
) -> list[dict[str, Any]]:
    """Sync wrapper for run_hunt."""
    return run_autonomous_sync(config_path=config_path, config=config)
