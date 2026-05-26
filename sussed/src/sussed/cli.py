"""
sussed CLI - The command center for apartment hunting 🎮
"""

import asyncio
from typing import Optional

import typer
from loguru import logger
from rich.console import Console
from rich.table import Table

app = typer.Typer(
    name="sussed",
    help="🏠 AI-powered real estate agent that susses out the market",
    add_completion=False,
)
console = Console()


def setup_logging(verbose: bool = False) -> None:
    """Configure loguru logging."""
    import sys
    logger.remove()
    level = "DEBUG" if verbose else "INFO"
    logger.add(sys.stderr, level=level, format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{message}</cyan>")


@app.command()
def cook(
    config: str = typer.Option(
        "config.yaml",
        "--config",
        "-c",
        help="Path to your dream home configuration",
    ),
    vibe_check: bool = typer.Option(
        False,
        "--vibe-check",
        "-v",
        help="Run AI analysis on listings",
    ),
) -> None:
    """Start cooking - let the agent find your dream apartment 👨‍🍳🔥"""
    console.print("[bold green]sussed[/bold green] is starting to cook... 🍳")
    console.print(f"Config: {config}")
    console.print(f"Vibe check: {'enabled' if vibe_check else 'disabled'}")
    # TODO: Implement the actual cooking logic
    console.print("[yellow]Not implemented yet - we're building this shit![/yellow]")


@app.command()
def scrape(
    city: str = typer.Option(
        "brno",
        "--city",
        "-c",
        help="City to scrape (brno, praha, ostrava, etc.)",
    ),
    listing_type: str = typer.Option(
        "sale",
        "--type",
        "-t",
        help="Listing type: sale or rent",
    ),
    property_type: str = typer.Option(
        "apartment",
        "--property",
        "-p",
        help="Property type: apartment or house",
    ),
    max_age: Optional[str] = typer.Option(
        None,
        "--age",
        "-a",
        help="Filter by listing age: day, week, month, or number of days (e.g. 14)",
    ),
    max_pages: Optional[int] = typer.Option(
        None,
        "--max-pages",
        "-m",
        help="Maximum pages to scrape (default: all)",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Enable verbose logging",
    ),
) -> None:
    """Scrape listings from real estate portals 🕷️"""
    setup_logging(verbose)
    
    # Validate max_age option
    if max_age and max_age not in ("day", "week", "month") and not max_age.isdigit():
        console.print(f"[red]Invalid --age value: {max_age}. Use: day, week, month, or number of days[/red]")
        raise typer.Exit(1)
    
    console.print(f"[bold blue]🕷️ Scraping sreality.cz[/bold blue]")
    console.print(f"   City: [green]{city}[/green]")
    console.print(f"   Type: [green]{listing_type}[/green]")
    console.print(f"   Property: [green]{property_type}[/green]")
    if max_age:
        age_display = f"{max_age} days" if max_age.isdigit() else max_age
        console.print(f"   Max age: [green]{age_display}[/green]")
    if max_pages:
        console.print(f"   Max pages: [green]{max_pages}[/green]")
    console.print()
    
    from sussed.scrapers.runner import scrape_sync
    
    try:
        stats = scrape_sync(
            city=city,
            listing_type=listing_type,
            property_type=property_type,
            max_pages=max_pages,
            max_age=max_age,
        )
        
        # Show results table
        table = Table(title="Scrape Results 🎉")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="green")
        
        table.add_row("Listings Found", str(stats["listings_found"]))
        table.add_row("New Listings", str(stats["listings_new"]))
        table.add_row("Updated", str(stats["listings_updated"]))
        table.add_row("Price Changes", str(stats["price_changes"]))
        table.add_row("Errors", str(stats["errors"]))
        
        console.print(table)
        
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@app.command()
def db(
    action: str = typer.Argument(
        ...,
        help="Database action: init, migrate, or status",
    ),
) -> None:
    """Database management commands 🗄️"""
    
    async def _init_db():
        from sussed.db.connection import init_db
        await init_db()
    
    async def _check_db():
        from sussed.db.connection import get_session
        from sqlalchemy import text
        async with get_session() as session:
            result = await session.execute(text("SELECT 1"))
            return result.scalar()
    
    match action:
        case "init":
            console.print("[blue]Initializing database...[/blue]")
            try:
                asyncio.run(_init_db())
                console.print("[green]✅ Database initialized![/green]")
            except Exception as e:
                console.print(f"[red]Error: {e}[/red]")
                raise typer.Exit(1)
                
        case "status":
            console.print("[blue]Checking database connection...[/blue]")
            try:
                asyncio.run(_check_db())
                console.print("[green]✅ Database connection OK![/green]")
            except Exception as e:
                console.print(f"[red]❌ Database connection failed: {e}[/red]")
                raise typer.Exit(1)
                
        case _:
            console.print(f"[red]Unknown action: {action}[/red]")
            console.print("Available actions: init, status")
            raise typer.Exit(1)


@app.command()
def listings(
    city: str = typer.Option(
        None,
        "--city",
        "-c",
        help="Filter by city",
    ),
    max_price: Optional[int] = typer.Option(
        None,
        "--max-price",
        help="Maximum price in CZK",
    ),
    apartment_type: Optional[str] = typer.Option(
        None,
        "--type",
        "-t",
        help="Apartment type (e.g., 2+kk, 3+1)",
    ),
    limit: int = typer.Option(
        20,
        "--limit",
        "-l",
        help="Number of results to show",
    ),
    output_format: str = typer.Option(
        "table",
        "--format",
        "-f",
        help="Output format: table or md (markdown for AI agent)",
    ),
    output_file: Optional[str] = typer.Option(
        None,
        "--output",
        "-o",
        help="Write output to file (default: stdout)",
    ),
) -> None:
    """Show listings from database 📋"""
    
    async def _get_listings():
        from sussed.db.connection import get_session
        from sussed.db.operations import get_listings
        
        async with get_session() as session:
            types = [apartment_type] if apartment_type else None
            return await get_listings(
                session,
                city=city,
                max_price=max_price,
                apartment_types=types,
                limit=limit,
            )
    
    def _format_features(features: dict | None) -> str:
        """Format features dict into human-readable string."""
        if not features:
            return "None listed"
        positive = [k.replace("_", " ").title() for k, v in features.items() if v]
        return ", ".join(positive) if positive else "None listed"
    
    def _generate_markdown(listings_data) -> str:
        """Generate markdown format for AI agent consumption."""
        lines = [
            "# Real Estate Listings for AI Analysis",
            "",
            f"**Total listings:** {len(listings_data)}",
            f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "",
            "---",
            "",
        ]
        
        for i, listing in enumerate(listings_data, 1):
            location = listing.city or ""
            if listing.district:
                location = f"{location}, {listing.district}"
            
            # Calculate price per m²
            price_per_m2 = ""
            if listing.area_m2 and listing.price_czk > 1:
                ppm2 = int(listing.price_czk / float(listing.area_m2))
                price_per_m2 = f" ({ppm2:,} Kč/m²)"
            
            lines.extend([
                f"## {i}. {listing.title}",
                "",
                f"**ID:** `{listing.id}`",
                "",
                "### Basic Info",
                f"- **Type:** {listing.apartment_type or 'Unknown'}",
                f"- **Price:** {listing.price_czk:,} Kč{price_per_m2}",
                f"- **Area:** {listing.area_m2} m²" if listing.area_m2 else "- **Area:** Unknown",
                f"- **Location:** {location}",
                f"- **Address:** {listing.address or 'Not specified'}",
                "",
            ])
            
            # GPS if available
            if listing.latitude and listing.longitude:
                lines.append(f"- **GPS:** {listing.latitude}, {listing.longitude}")
                lines.append("")
            
            # Features
            lines.extend([
                "### Features",
                f"{_format_features(listing.features)}",
                "",
            ])
            
            # Raw labels from sreality
            if listing.raw_labels:
                lines.extend([
                    "### Labels (from source)",
                    f"{', '.join(listing.raw_labels)}",
                    "",
                ])
            
            # Description if we have it
            if listing.description:
                lines.extend([
                    "### Description",
                    listing.description,
                    "",
                ])
            
            # Media info
            media_info = []
            if listing.image_count:
                media_info.append(f"{listing.image_count} photos")
            if listing.has_floor_plan:
                media_info.append("floor plan")
            if listing.has_video:
                media_info.append("video")
            if listing.has_3d_tour:
                media_info.append("3D tour")
            
            if media_info:
                lines.extend([
                    "### Media",
                    f"{', '.join(media_info)}",
                    "",
                ])
            
            # Agency
            if listing.agency_name:
                lines.extend([
                    "### Agency",
                    f"{listing.agency_name}",
                    "",
                ])
            
            # URL
            lines.extend([
                "### Link",
                f"[View on sreality.cz]({listing.url})",
                "",
                "---",
                "",
            ])
        
        return "\n".join(lines)
    
    try:
        from datetime import datetime
        results = asyncio.run(_get_listings())
        
        if not results:
            console.print("[yellow]No listings found matching your criteria[/yellow]")
            return
        
        # Markdown format for AI agent
        if output_format.lower() == "md":
            md_content = _generate_markdown(results)
            
            if output_file:
                with open(output_file, "w", encoding="utf-8") as f:
                    f.write(md_content)
                console.print(f"[green]✅ Written {len(results)} listings to {output_file}[/green]")
            else:
                console.print(md_content)
            return
        
        # Table format (default)
        table = Table(title=f"Listings ({len(results)} results)")
        table.add_column("ID", style="dim")
        table.add_column("Type", style="cyan")
        table.add_column("Price", style="yellow", justify="right")
        table.add_column("m²", justify="right")
        table.add_column("Location", style="green")
        
        for listing in results:
            # Show first 8 chars of UUID for brevity
            short_id = str(listing.id)[:8]
            # Combine city and district for location
            location = listing.city or ""
            if listing.district:
                location = f"{location}, {listing.district}"
            table.add_row(
                short_id,
                listing.apartment_type or "-",
                f"{listing.price_czk:,} Kč",
                str(int(listing.area_m2)) if listing.area_m2 else "-",
                location or "-",
            )
        
        console.print(table)
        console.print("\n[dim]💡 Use 'sussed url <ID>' to get the full URL for a listing[/dim]")
        console.print("[dim]💡 Use '--format md' for AI-friendly markdown output[/dim]")
        
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@app.command()
def url(
    listing_id: str = typer.Argument(
        ...,
        help="Listing ID (or partial ID) to get URL for",
    ),
) -> None:
    """Get URL for a listing by ID 🔗"""
    
    async def _get_listing_url():
        from sussed.db.connection import get_session
        from sussed.db.models import Listing
        from sqlmodel import select
        from sqlalchemy import text
        
        async with get_session() as session:
            # Use text() for the LIKE comparison with UUID cast
            stmt = select(Listing).where(
                text(f"CAST(id AS TEXT) LIKE '{listing_id}%'")
            )
            result = await session.execute(stmt)
            listings = result.scalars().all()
            return listings
    
    try:
        results = asyncio.run(_get_listing_url())
        
        if not results:
            console.print(f"[red]No listing found with ID starting with '{listing_id}'[/red]")
            raise typer.Exit(1)
        
        if len(results) > 1:
            console.print(f"[yellow]Multiple listings match '{listing_id}':[/yellow]")
            for listing in results:
                console.print(f"  [dim]{listing.id}[/dim] - {listing.title[:40]}")
            console.print("\n[dim]Be more specific with the ID[/dim]")
            return
        
        listing = results[0]
        console.print(f"\n[bold]{listing.title}[/bold]")
        console.print(f"[dim]ID: {listing.id}[/dim]")
        console.print(f"\n[blue]{listing.url}[/blue]\n")
        
    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@app.command()
def version() -> None:
    """Show version info ℹ️"""
    from sussed import __version__
    console.print(f"[bold]sussed[/bold] v{__version__}")


@app.command()
def enrich(
    limit: int = typer.Option(
        10,
        "--limit",
        "-l",
        help="Number of listings to enrich (rate limited!)",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Re-fetch even if description exists",
    ),
) -> None:
    """Fetch full descriptions for listings 📝
    
    This fetches individual listing details from sreality API
    to get descriptions that aren't in the list endpoint.
    
    ⚠️  Rate limited to ~1 req/sec - be patient!
    """
    
    async def _enrich_listings():
        import httpx
        from datetime import datetime as dt
        from sussed.db.connection import get_session
        from sussed.db.models import Listing, ListingStatus
        from sussed.scrapers.sreality import SrealityScraper
        from sqlmodel import select
        
        scraper = SrealityScraper()
        enriched = 0
        sold = 0
        errors = 0
        
        async with get_session() as session:
            # Get active listings without descriptions (or all active if force)
            if force:
                stmt = select(Listing).where(
                    Listing.source == "sreality",
                    Listing.status == ListingStatus.ACTIVE,
                ).limit(limit)
            else:
                stmt = select(Listing).where(
                    Listing.source == "sreality",
                    Listing.status == ListingStatus.ACTIVE,
                    Listing.description.is_(None)
                ).limit(limit)
            
            result = await session.execute(stmt)
            listings = result.scalars().all()
            
            if not listings:
                console.print("[yellow]No listings need enriching[/yellow]")
                return {"enriched": 0, "sold": 0, "errors": 0}
            
            console.print(f"[blue]Enriching {len(listings)} listings...[/blue]")
            
            async with httpx.AsyncClient() as client:
                for i, listing in enumerate(listings, 1):
                    try:
                        # External ID is the hash_id
                        hash_id = int(listing.external_id)
                        
                        console.print(f"  [{i}/{len(listings)}] {listing.title[:40]}...", end=" ")
                        
                        details = await scraper.fetch_listing_details(client, hash_id)
                        
                        if details and "text" in details:
                            # Extract description
                            text_data = details.get("text", {})
                            description_parts = []
                            
                            # Main description
                            if "value" in text_data:
                                description_parts.append(text_data["value"])
                            
                            # Parse items array for additional data
                            for item in details.get("items", []):
                                item_type = item.get("type")
                                item_name = item.get("name", "")
                                item_value = item.get("value")
                                
                                # Extract "Aktualizace" (last update/modified date from source)
                                # Format: "DD.MM.YYYY" e.g., "15.01.2026"
                                #
                                # NOTE: Sreality doesn't expose "Vloženo" (created) via API,
                                # only "Aktualizace" (modified). We store the oldest value
                                # we've ever seen as a rough lower bound for the listing age.
                                if item_type == "edited" and item_name == "Aktualizace" and item_value:
                                    try:
                                        api_date = dt.strptime(item_value, "%d.%m.%Y")
                                        # Keep the oldest "Aktualizace" as best approximation
                                        if listing.updated_at_source is None or api_date < listing.updated_at_source:
                                            listing.updated_at_source = api_date
                                    except ValueError:
                                        pass  # Skip if date format is unexpected
                                
                                # Additional text items for description
                                elif item_type == "text" and item_value:
                                    description_parts.append(f"{item_name}: {item_value}")
                            
                            if description_parts:
                                listing.description = "\n\n".join(description_parts)
                                session.add(listing)
                                await session.commit()
                                enriched += 1
                                console.print("[green]✓[/green]")
                            else:
                                console.print("[yellow]no description[/yellow]")
                        else:
                            console.print("[yellow]no data[/yellow]")
                            
                    except httpx.HTTPStatusError as e:
                        if e.response.status_code == 410:
                            # Listing is gone - mark as sold! 💀
                            listing.status = ListingStatus.SOLD
                            session.add(listing)
                            await session.commit()
                            sold += 1
                            console.print("[magenta]SOLD/GONE 💀[/magenta]")
                        else:
                            console.print(f"[red]HTTP {e.response.status_code}[/red]")
                            errors += 1
                    except Exception as e:
                        console.print(f"[red]error: {e}[/red]")
                        errors += 1
        
        return {"enriched": enriched, "sold": sold, "errors": errors}
    
    try:
        stats = asyncio.run(_enrich_listings())
        console.print(f"\n[green]✅ Enriched {stats['enriched']} listings[/green]")
        if stats['sold']:
            console.print(f"[magenta]💀 {stats['sold']} listings marked as sold/removed[/magenta]")
        if stats['errors']:
            console.print(f"[yellow]⚠️  {stats['errors']} errors[/yellow]")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@app.command()
def hunt(
    config: str = typer.Option(
        "search_config.yaml",
        "--config",
        "-c",
        help="Path to search config YAML file",
    ),
    best: Optional[int] = typer.Option(
        None,
        "--best",
        "-b",
        help="Show top N highest scored listings",
    ),
    trash: Optional[int] = typer.Option(
        None,
        "--trash",
        "-t",
        help="Show bottom N listings (overpriced/sus)",
    ),
    gems: bool = typer.Option(
        False,
        "--gems",
        "-g",
        help="Show only gem listings (score >= 900)",
    ),
    format_output: str = typer.Option(
        "table",
        "--format",
        "-f",
        help="Output format: table, json, or markdown",
    ),
    save: Optional[str] = typer.Option(
        None,
        "--save",
        "-s",
        help="Save results to this file",
    ),
    generate_config: bool = typer.Option(
        False,
        "--generate-config",
        help="Generate example config file and exit",
    ),
    rescore: bool = typer.Option(
        False,
        "--rescore",
        "-r",
        help="Re-score ALL listings (ignore skip_already_scored)",
    ),
    scrape: bool = typer.Option(
        False,
        "--scrape",
        help="Scrape fresh data before hunting (recommended!)",
    ),
    scrape_pages: int = typer.Option(
        5,
        "--scrape-pages",
        "-p",
        help="Max pages to scrape when using --scrape",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Enable verbose logging",
    ),
) -> None:
    """Autonomous apartment hunting 🎯
    
    Run the AI agent to score and rank listings based on your config.
    
    The config file defines:
    - What you're looking for (price, size, location, features)
    - How to weight different factors in scoring
    - Output preferences
    
    Special handling for 1 Kč listings:
    - These are "Price on Request" listings, NOT scams!
    - Agent evaluates them on description/features only
    - No price calculations applied
    
    Examples:
        # Generate example config
        uv run sussed hunt --generate-config
        
        # Run with config
        uv run sussed hunt -c my_search.yaml
        
        # Scrape fresh data + hunt (recommended!)
        uv run sussed hunt -c my_search.yaml --scrape
        
        # Scrape more pages
        uv run sussed hunt --scrape --scrape-pages 10
        
        # Show top 5 best picks
        uv run sussed hunt -c my_search.yaml --best 5
        
        # Show trash/sus listings
        uv run sussed hunt -c my_search.yaml --trash 10
        
        # Show only gems
        uv run sussed hunt --gems
        
        # Save results as JSON
        uv run sussed hunt --best 10 -f json -s results.json
    """
    setup_logging(verbose)
    
    # Generate config mode
    if generate_config:
        from sussed.agent.config import generate_example_config
        output_path = "search_config.yaml"
        generate_example_config(output_path)
        console.print(f"[green]✅ Generated example config: {output_path}[/green]")
        console.print("Edit it to match your preferences, then run:")
        console.print(f"   [cyan]uv run sussed hunt -c {output_path}[/cyan]")
        return
    
    # Load config
    from pathlib import Path
    from sussed.agent.config import SearchConfig, OutputMode
    from sussed.agent.autonomous import run_autonomous_sync
    
    config_path = Path(config)
    if not config_path.exists():
        console.print(f"[red]Config file not found: {config}[/red]")
        console.print("Generate one with: [cyan]uv run sussed hunt --generate-config[/cyan]")
        raise typer.Exit(1)
    
    try:
        search_config = SearchConfig.from_yaml(config_path)
        
        # Override output settings from CLI args
        if best:
            search_config.output.mode = OutputMode.BEST
            search_config.output.limit = best
        elif trash:
            search_config.output.mode = OutputMode.TRASH
            search_config.output.limit = trash
        elif gems:
            search_config.output.mode = OutputMode.GEMS
            search_config.output.limit = 50  # Show all gems
        
        if format_output:
            search_config.output.format = format_output
        
        if save:
            search_config.output.save_to_file = save
        
        # Rescore all listings if requested
        if rescore:
            search_config.agent.skip_already_scored = False
            console.print("[yellow]🔄 Rescore mode: will re-score all listings[/yellow]")
        
        # Enable auto-scrape if requested via CLI
        if scrape:
            search_config.agent.auto_scrape = True
            search_config.agent.scrape_max_pages = scrape_pages
            console.print(f"[cyan]🕷️ Will scrape up to {scrape_pages} pages of fresh data first[/cyan]")
        
        # Run the hunt!
        results = run_autonomous_sync(config=search_config)
        
        if not results:
            console.print("\n[yellow]No results. Try:[/yellow]")
            console.print("   1. Scrape some listings first: [cyan]uv run sussed scrape -c brno -m 5[/cyan]")
            console.print("   2. Adjust your config criteria")
    
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        if verbose:
            import traceback
            console.print(traceback.format_exc())
        raise typer.Exit(1)


@app.command()
def agent(
    server: bool = typer.Option(
        False,
        "--server",
        "-s",
        help="Run as MCP server (for VSCode Copilot integration)",
    ),
    port: int = typer.Option(
        7777,
        "--port",
        "-p",
        help="Port for MCP server",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Enable debug logging",
    ),
) -> None:
    """Start the AI agent 🤖
    
    Run in server mode for VSCode Copilot integration,
    or interactive CLI mode for direct queries.
    
    Examples:
        # Start MCP server (connect Copilot to http://localhost:7777/mcp)
        uv run sussed agent --server
        
        # Interactive CLI mode
        uv run sussed agent
    """
    setup_logging(verbose)
    
    from sussed.agent.server import run_server, run_cli_mode
    
    if server:
        console.print(f"[bold blue]🤖 Starting sussed agent MCP server on port {port}[/bold blue]")
        console.print(f"[green]Connect VSCode Copilot to: http://localhost:{port}/mcp[/green]")
        run_server(port=port)
    else:
        console.print("[bold blue]🤖 Starting sussed agent in CLI mode[/bold blue]")
        run_cli_mode()


if __name__ == "__main__":
    app()
