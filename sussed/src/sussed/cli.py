"""
sussed CLI - The command center for apartment hunting 🎮
"""

import asyncio
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

import httpx
import typer
from loguru import logger
from rich.console import Console
from rich.table import Table

from sussed.review.service import download_listing_images

if TYPE_CHECKING:
    from sussed.db.models import Listing

app = typer.Typer(
    name="sussed",
    help="🏠 AI-powered real estate agent that susses out the market",
    add_completion=False,
)
console = Console()
review_app = typer.Typer(help="AI review workflow commands 🧠")
app.add_typer(review_app, name="review")
service_app = typer.Typer(help="Scheduled daily service management 🕐")
app.add_typer(service_app, name="service")
dedup_app = typer.Typer(help="Duplicate / relisting detection commands 🔍")
app.add_typer(dedup_app, name="dedup")

UUID_PREFIX_RE = re.compile(r"^[0-9a-fA-F]{4,36}$")


def _validate_partial_uuid_prefix(value: str) -> str:
    """Validate a user-provided UUID prefix before using it in SQL LIKE."""
    if not UUID_PREFIX_RE.fullmatch(value):
        raise ValueError("Listing ID prefix must be 4-36 hexadecimal characters")
    return value.lower()


class _ImageCacheResult(TypedDict):
    saved: int
    status: str


async def _cache_listing_images_for_enrich(
    listing: Listing,
    cache_root: Path,
    image_limit: int,
) -> _ImageCacheResult:
    """Pre-warm the per-listing image cache used by ``sussed review prepare``.

    Image-download failures are intentionally NOT allowed to escape: they
    must not be confused with the outer ``HTTPStatusError`` handler on the
    listing-details fetch (which treats a 410 as "listing sold"), and they
    must not fall through to the generic ``except Exception`` and bypass
    the friendly ``📷 partial`` UX.

    We catch ``httpx.HTTPError`` (covers ``HTTPStatusError`` plus network /
    timeout errors) and ``OSError`` (covers disk failures). Anything else
    is a real bug and is allowed to bubble up.

    Returns:
        A dict with ``saved`` (image count) and ``status`` (``ok``,
        ``skipped``, or ``partial``) so callers can render the right UX
        without duplicating the catch logic.
    """
    if image_limit <= 0 or not listing.image_urls:
        return {"saved": 0, "status": "skipped"}

    try:
        saved = await download_listing_images(
            image_urls=listing.image_urls,
            destination_dir=cache_root / str(listing.id),
            limit=image_limit,
        )
    except (httpx.HTTPError, OSError) as err:
        logger.warning(f"Image cache download failed for {listing.id}: {err}")
        return {"saved": 0, "status": "partial"}

    return {"saved": len(saved), "status": "ok"}


def setup_logging(verbose: bool = False) -> None:
    """Configure loguru logging."""
    import sys

    logger.remove()
    level = "DEBUG" if verbose else "INFO"
    logger.add(
        sys.stderr,
        level=level,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{message}</cyan>",
    )


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
        help="Property type: apartment, house, cottage, or garden",
    ),
    max_age: str | None = typer.Option(
        None,
        "--age",
        "-a",
        help="Filter by listing age: day, week, month, or number of days (e.g. 14)",
    ),
    max_pages: int | None = typer.Option(
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
    """Scrape apartments, houses, cottages, or gardens from real estate portals 🕷️"""
    setup_logging(verbose)

    valid_property_types = {"apartment", "house", "cottage", "garden"}
    property_type = property_type.lower().strip()
    if property_type not in valid_property_types:
        console.print(
            "[red]Invalid --property value: "
            f"{property_type}. Use: apartment, house, cottage, or garden[/red]"
        )
        raise typer.Exit(1)

    # Validate max_age option
    if max_age and max_age not in ("day", "week", "month") and not max_age.isdigit():
        console.print(
            f"[red]Invalid --age value: {max_age}. Use: day, week, month, or number of days[/red]"
        )
        raise typer.Exit(1)

    console.print("[bold blue]🕷️ Scraping sreality.cz[/bold blue]")
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
        raise typer.Exit(1) from e


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
        from sqlalchemy import text

        from sussed.db.connection import get_session

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
                raise typer.Exit(1) from e

        case "status":
            console.print("[blue]Checking database connection...[/blue]")
            try:
                asyncio.run(_check_db())
                console.print("[green]✅ Database connection OK![/green]")
            except Exception as e:
                console.print(f"[red]❌ Database connection failed: {e}[/red]")
                raise typer.Exit(1) from e

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
    max_price: int | None = typer.Option(
        None,
        "--max-price",
        help="Maximum price in CZK",
    ),
    apartment_type: str | None = typer.Option(
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
    output_file: str | None = typer.Option(
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

            lines.extend(
                [
                    f"## {i}. {listing.title}",
                    "",
                    f"**ID:** `{listing.id}`",
                    "",
                    "### Basic Info",
                    f"- **Type:** {listing.apartment_type or 'Unknown'}",
                    f"- **Price:** {listing.price_czk:,} Kč{price_per_m2}",
                    f"- **Area:** {listing.area_m2} m²"
                    if listing.area_m2
                    else "- **Area:** Unknown",
                    f"- **Location:** {location}",
                    f"- **Address:** {listing.address or 'Not specified'}",
                    "",
                ]
            )

            # GPS if available
            if listing.latitude and listing.longitude:
                lines.append(f"- **GPS:** {listing.latitude}, {listing.longitude}")
                lines.append("")

            # Features
            lines.extend(
                [
                    "### Features",
                    f"{_format_features(listing.features)}",
                    "",
                ]
            )

            # Raw labels from sreality
            if listing.raw_labels:
                lines.extend(
                    [
                        "### Labels (from source)",
                        f"{', '.join(listing.raw_labels)}",
                        "",
                    ]
                )

            # Description if we have it
            if listing.description:
                lines.extend(
                    [
                        "### Description",
                        listing.description,
                        "",
                    ]
                )

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
                lines.extend(
                    [
                        "### Media",
                        f"{', '.join(media_info)}",
                        "",
                    ]
                )

            # Agency
            if listing.agency_name:
                lines.extend(
                    [
                        "### Agency",
                        f"{listing.agency_name}",
                        "",
                    ]
                )

            # URL
            lines.extend(
                [
                    "### Link",
                    f"[View on sreality.cz]({listing.url})",
                    "",
                    "---",
                    "",
                ]
            )

        return "\n".join(lines)

    try:
        from datetime import datetime
        from pathlib import Path

        results = asyncio.run(_get_listings())

        if not results:
            console.print("[yellow]No listings found matching your criteria[/yellow]")
            return

        # Markdown format for AI agent
        if output_format.lower() == "md":
            md_content = _generate_markdown(results)

            if output_file:
                with Path(output_file).open("w", encoding="utf-8") as f:
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
        raise typer.Exit(1) from e


@app.command()
def drops(
    limit: int = typer.Option(
        20,
        "--limit",
        "-l",
        help="Max number of listings to show",
    ),
    days: int | None = typer.Option(
        None,
        "--days",
        "-d",
        help="Only show drops from the last N days",
    ),
    city: str | None = typer.Option(
        None,
        "--city",
        "-c",
        help="Filter by city",
    ),
    apartment_type: str | None = typer.Option(
        None,
        "--type",
        "-t",
        help="Filter by apartment type (e.g. 2+kk)",
    ),
    to_poa_only: bool = typer.Option(
        False,
        "--to-poa",
        help="Only show listings that dropped to POA / 1 Kč (seller hiding new price)",
    ),
) -> None:
    """Show listings where price has dropped 📉

    Lists every active listing that has recorded a price decrease, sorted by
    most recent drop first. Includes drops to POA (Price on Request), which
    usually signal an in-progress negotiation or a hidden price hike.
    """

    async def _get_drops() -> list[dict]:
        from datetime import datetime, timedelta

        from sqlalchemy import and_, desc, func, select
        from sqlalchemy.orm import aliased
        from sqlmodel.ext.asyncio.session import AsyncSession  # noqa: F401

        from sussed.db.connection import get_session
        from sussed.db.models import Listing, ListingStatus, PriceHistory

        async with get_session() as session:
            # Subquery: most-recent decrease per listing
            ph_alias = aliased(PriceHistory)
            latest_drop_sq = (
                select(
                    ph_alias.listing_id.label("lid"),
                    func.max(ph_alias.recorded_at).label("max_recorded"),
                )
                .where(ph_alias.change_type == "decrease")
                .group_by(ph_alias.listing_id)
                .subquery()
            )

            conditions = [
                Listing.id == latest_drop_sq.c.lid,
                Listing.status == ListingStatus.ACTIVE,
                PriceHistory.listing_id == latest_drop_sq.c.lid,
                PriceHistory.recorded_at == latest_drop_sq.c.max_recorded,
            ]
            if city:
                conditions.append(Listing.city.ilike(f"%{city}%"))
            if apartment_type:
                conditions.append(Listing.apartment_type == apartment_type)
            if days:
                cutoff = datetime.utcnow() - timedelta(days=days)
                conditions.append(PriceHistory.recorded_at >= cutoff)
            if to_poa_only:
                conditions.append(Listing.price_czk <= 10)

            stmt = (
                select(Listing, PriceHistory)
                .select_from(latest_drop_sq)
                .join(Listing)
                .join(PriceHistory)
                .where(and_(*conditions))
                .order_by(desc(latest_drop_sq.c.max_recorded))
                .limit(limit)
            )
            result = await session.execute(stmt)

            # For each listing, also find the price right before the drop so
            # we can show before/after — that's the price_czk of the next-older
            # PriceHistory entry (or the "initial" entry if there's only one).
            rows: list[dict] = []
            for listing, drop in result.all():
                before_stmt = (
                    select(PriceHistory.price_czk)
                    .where(
                        PriceHistory.listing_id == listing.id,
                        PriceHistory.recorded_at < drop.recorded_at,
                    )
                    .order_by(desc(PriceHistory.recorded_at))
                    .limit(1)
                )
                before_result = await session.execute(before_stmt)
                before_price = before_result.scalar_one_or_none()
                # Fallback: derive from change_amount if no prior row exists.
                if before_price is None and drop.change_amount:
                    before_price = drop.price_czk + drop.change_amount

                rows.append(
                    {
                        "listing": listing,
                        "before_price": before_price,
                        "after_price": drop.price_czk,
                        "change_amount": drop.change_amount,
                        "change_percent": float(drop.change_percent)
                        if drop.change_percent is not None
                        else None,
                        "drop_date": drop.recorded_at,
                    }
                )
            return rows

    try:
        results = asyncio.run(_get_drops())
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1) from e

    if not results:
        console.print("[yellow]No price drops found matching those filters.[/yellow]")
        return

    console.print(f"\n[bold green]📉 {len(results)} price drops[/bold green]\n")

    for i, r in enumerate(results, 1):
        listing = r["listing"]
        before = r["before_price"]
        after = r["after_price"]

        after_str = "[bold yellow]POA[/bold yellow]" if after <= 10 else f"{after:,} Kč"
        before_str = f"{before:,} Kč" if before else "—"

        if before and before > 10 and after <= 10:
            delta_str = "[bold yellow](seller hiding new price)[/bold yellow]"
        elif r["change_percent"] is not None:
            delta_str = f"[green]-{r['change_percent']:.1f}%[/green]"
        elif r["change_amount"]:
            delta_str = f"[green]-{r['change_amount']:,} Kč[/green]"
        else:
            delta_str = ""

        location = listing.city or ""
        if listing.district:
            location = f"{listing.district}, {location}"

        drop_date = r["drop_date"].strftime("%Y-%m-%d")
        posted = listing.first_seen_at.strftime("%Y-%m-%d") if listing.first_seen_at else "—"
        apt = listing.apartment_type or "-"

        console.print(f"[bold cyan]#{i}[/bold cyan] [bold]{apt}[/bold] · {location}")
        console.print(f"    [dim]Price:[/dim] {before_str} [bold]→[/bold] {after_str}  {delta_str}")
        console.print(f"    [dim]Drop: {drop_date} · Posted: {posted}[/dim]")
        console.print(f"    [blue]{listing.url}[/blue]")
        console.print()


@app.command()
def url(
    listing_id: str = typer.Argument(
        ...,
        help="Listing ID (or partial ID) to get URL for",
    ),
) -> None:
    """Get URL for a listing by ID 🔗"""

    async def _get_listing_url():
        from sqlalchemy import text
        from sqlmodel import select

        from sussed.db.connection import get_session
        from sussed.db.models import Listing

        async with get_session() as session:
            try:
                safe_prefix = _validate_partial_uuid_prefix(listing_id)
            except ValueError as exc:
                raise ValueError(str(exc)) from exc

            # Use text() for the LIKE comparison with UUID cast
            stmt = (
                select(Listing)
                .where(text("CAST(id AS TEXT) LIKE :prefix"))
                .params(prefix=f"{safe_prefix}%")
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
        raise typer.Exit(1) from e


@app.command()
def version() -> None:
    """Show version info."""
    from sussed import __version__

    console.print(f"[bold]sussed[/bold] v{__version__}")


@app.command()
def feed(
    output: str = typer.Option(
        "sussed-feed.html", "--output", "-o", help="Path to write the HTML feed"
    ),
    limit: int = typer.Option(50, "--limit", "-l", min=1, max=500, help="Max posts per tab"),
    fresh_days: int = typer.Option(
        31, "--fresh-days", min=1, help="Age window in days for the Fresh tab"
    ),
    min_score: int | None = typer.Option(
        None, "--min-score", "-m", help="Minimum effective score for both tabs"
    ),
    district: str | None = typer.Option(
        None, "--district", "-d", help="Filter by district name (fuzzy)"
    ),
    property_type: str | None = typer.Option(
        None, "--property-type", "-p", help="apartment, house, cottage, or garden"
    ),
    all_picks: bool = typer.Option(
        False,
        "--all",
        help="Include unreviewed listings in AI Picks (note: --min-score only "
        "matches reviewed listings on that tab)",
    ),
    title: str = typer.Option("sussed · best picks", "--title", help="Page title"),
    open_browser: bool = typer.Option(
        False, "--open/--no-open", help="Open the generated file in a browser"
    ),
) -> None:
    """Generate a self-contained HTML feed of best listings 📸

    Reads the best listings straight from the database and writes ONE static HTML
    file — no server, no API. Two tabs: AI-reviewed picks (ranked by review score)
    and Fresh (recent listings ranked by effective score, i.e. AI score if reviewed
    else the hunt quick-score). Filter/sort happen client-side in the browser.

    Examples:
        # Top picks + fresh from the last month
        uv run sussed feed

        # Cheap apartments in one district, open it right away
        uv run sussed feed -p apartment -d "Královo Pole" --open

        # Wider net: include unreviewed listings in AI Picks, last 60 days fresh
        uv run sussed feed --all --fresh-days 60 -o /tmp/brno.html
    """

    async def _run() -> tuple[str, object]:
        from sussed.db.connection import get_session
        from sussed.feed.builder import build_feed_data
        from sussed.feed.renderer import render_feed

        async with get_session() as session:
            feed_data, context = await build_feed_data(
                session,
                title=title,
                limit=limit,
                fresh_days=fresh_days,
                district=district,
                min_score=min_score,
                property_type=property_type,
                include_unreviewed_in_picks=all_picks,
            )
        return render_feed(feed_data, context), context

    try:
        html, context = asyncio.run(_run())
    except ValueError as exc:
        console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(1) from exc

    output_path = Path(output).expanduser()
    try:
        if output_path.parent and not output_path.parent.exists():
            output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(html, encoding="utf-8")
    except OSError as exc:
        console.print(f"[red]Could not write {output_path}: {exc}[/red]")
        raise typer.Exit(1) from exc

    ai_count = getattr(context, "ai_picks_count", 0)
    fresh_count = getattr(context, "fresh_count", 0)
    console.print(
        f"\n[bold green]📸 Feed generated:[/bold green] {output_path} "
        f"([cyan]{ai_count}[/cyan] AI picks · [cyan]{fresh_count}[/cyan] fresh)"
    )
    if ai_count == 0 and fresh_count == 0:
        console.print(
            "[yellow]No listings matched — try `sussed hunt` / `sussed review`, "
            "or loosen filters (--all, --fresh-days, --min-score).[/yellow]"
        )
    if open_browser:
        import webbrowser

        webbrowser.open(output_path.resolve().as_uri())


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
    cache_dir: str = typer.Option(
        ".sussed/image-cache",
        "--cache-dir",
        help="Root directory for the per-listing image cache",
    ),
    image_limit: int = typer.Option(
        5,
        "--image-limit",
        min=0,
        max=20,
        help="Max images to download per listing into the cache (0 disables)",
    ),
) -> None:
    """Fetch full descriptions for listings 📝

    Also pre-warms the photo cache under ``--cache-dir`` (default
    ``.sussed/image-cache/<listing-id>/``) so ``sussed review prepare`` can
    consume images without doing its own HTTP fetches. Pass ``--image-limit 0``
    to skip the image download.

    This fetches individual listing details from sreality API
    to get descriptions that aren't in the list endpoint.

    ⚠️  Rate limited to ~1 req/sec - be patient!
    """

    async def _enrich_listings():
        from datetime import datetime as dt

        from sqlmodel import select

        from sussed.db.connection import get_session
        from sussed.db.models import Listing, ListingStatus
        from sussed.scrapers.sreality import (
            SrealityScraper,
            parse_v1_source_date,
            set_features_from_v1_detail,
        )

        scraper = SrealityScraper()
        enriched = 0
        sold = 0
        errors = 0
        images_downloaded = 0
        cache_root = Path(cache_dir)

        async with get_session() as session:
            # Get active listings without descriptions (or all active if force)
            if force:
                stmt = (
                    select(Listing)
                    .where(
                        Listing.source == "sreality",
                        Listing.status == ListingStatus.ACTIVE,
                    )
                    .limit(limit)
                )
            else:
                stmt = (
                    select(Listing)
                    .where(
                        Listing.source == "sreality",
                        Listing.status == ListingStatus.ACTIVE,
                        Listing.description.is_(None),
                    )
                    .limit(limit)
                )

            result = await session.execute(stmt)
            listings = result.scalars().all()

            if not listings:
                console.print("[yellow]No listings need enriching[/yellow]")
                return {"enriched": 0, "sold": 0, "errors": 0, "images": 0}

            console.print(f"[blue]Enriching {len(listings)} listings...[/blue]")

            async with httpx.AsyncClient() as client:
                for i, listing in enumerate(listings, 1):
                    try:
                        hash_id = int(listing.external_id)

                        console.print(f"  [{i}/{len(listings)}] {listing.title[:40]}...", end=" ")

                        detail = await scraper.fetch_listing_details(client, hash_id)

                        if detail:
                            description = detail.advert_description
                            api_date = parse_v1_source_date(detail)
                            if api_date and (
                                listing.updated_at_source is None
                                or api_date < listing.updated_at_source
                            ):
                                listing.updated_at_source = api_date

                            set_features_from_v1_detail(listing, detail)
                            listing.updated_at = dt.utcnow()

                            if description:
                                listing.description = description
                                enriched += 1
                                console.print("[green]✓[/green]", end="")
                            else:
                                console.print("[yellow]no description[/yellow]", end="")

                            session.add(listing)
                            await session.commit()

                            # Pre-warm photo cache so `review prepare` is offline.
                            # The helper catches its own HTTP/OS errors so an
                            # image failure does NOT escape into the outer
                            # ``HTTPStatusError`` handler (which special-cases
                            # 410 as "sold") or the generic ``Exception``
                            # handler — that would skip the friendly partial
                            # UX rendered below.
                            cache_result = await _cache_listing_images_for_enrich(
                                listing=listing,
                                cache_root=cache_root,
                                image_limit=image_limit,
                            )
                            if cache_result["status"] == "ok" and cache_result["saved"]:
                                images_downloaded += cache_result["saved"]
                                console.print(f" [cyan]📷 {cache_result['saved']}[/cyan]")
                            elif cache_result["status"] == "partial":
                                console.print(" [yellow]📷 partial[/yellow]")
                            else:
                                console.print()
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

        return {
            "enriched": enriched,
            "sold": sold,
            "errors": errors,
            "images": images_downloaded,
        }

    try:
        stats = asyncio.run(_enrich_listings())
        console.print(f"\n[green]✅ Enriched {stats['enriched']} listings[/green]")
        if stats.get("images"):
            console.print(f"[cyan]📷 Cached {stats['images']} images[/cyan]")
        if stats["sold"]:
            console.print(f"[magenta]💀 {stats['sold']} listings marked as sold/removed[/magenta]")
        if stats["errors"]:
            console.print(f"[yellow]⚠️  {stats['errors']} errors[/yellow]")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1) from e


@app.command()
def refresh(
    source: str = typer.Option("sreality", "--source", help="Source to refresh"),
    city: str | None = typer.Option(None, "--city", "-c", help="Filter by city"),
    limit: int = typer.Option(100, "--limit", "-l", min=1, help="Max listings to re-check"),
    stale_days: int | None = typer.Option(
        None, "--stale-days", min=0, help="Only listings not seen in the last N days"
    ),
    dry_run: bool = typer.Option(False, "--dry-run", is_flag=True, help="Preview without saving"),
) -> None:
    """Re-check existing active listings: mark gone (404/410) as removed, refresh price/details 🔄"""
    if source != "sreality":
        console.print(
            f"[red]refresh currently supports only --source sreality (got {source!r})[/red]"
        )
        raise typer.Exit(2)

    from sussed.scrapers.refresh import run_refresh

    async def _run() -> None:
        stats = await run_refresh(
            source=source, city=city, limit=limit, stale_days=stale_days, dry_run=dry_run
        )
        if dry_run:
            console.print("[yellow]DRY RUN — no changes saved[/yellow]")
        console.print(
            f"Checked [bold]{stats['checked']}[/bold] · "
            f"[magenta]{stats['removed']} removed[/magenta] · "
            f"[cyan]{stats['price_changes']} price changes[/cyan] · "
            f"[green]{stats['updated']} updated[/green] · "
            f"[red]{stats['errors']} errors[/red]"
        )

    try:
        asyncio.run(_run())
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1) from e


@app.command()
def hunt(
    config: str = typer.Option(
        "search_config.yaml",
        "--config",
        "-c",
        help="Path to search config YAML file",
    ),
    best: int | None = typer.Option(
        None,
        "--best",
        "-b",
        help="Show top N highest scored listings",
    ),
    trash: int | None = typer.Option(
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
    save: str | None = typer.Option(
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
        from sussed.hunt.config import generate_example_config

        output_path = "search_config.yaml"
        generate_example_config(output_path)
        console.print(f"[green]✅ Generated example config: {output_path}[/green]")
        console.print("Edit it to match your preferences, then run:")
        console.print(f"   [cyan]uv run sussed hunt -c {output_path}[/cyan]")
        return

    # Load config
    from pathlib import Path

    from sussed.hunt.config import OutputMode, SearchConfig
    from sussed.hunt.runner import run_hunt_sync

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
            search_config.runner.skip_already_scored = False
            console.print("[yellow]🔄 Rescore mode: will re-score all listings[/yellow]")

        # Enable auto-scrape if requested via CLI
        if scrape:
            search_config.runner.auto_scrape = True
            search_config.runner.scrape_max_pages = scrape_pages
            console.print(
                f"[cyan]🕷️ Will scrape up to {scrape_pages} pages of fresh data first[/cyan]"
            )

        # Run the hunt!
        results = run_hunt_sync(config=search_config)

        if not results:
            console.print("\n[yellow]No results. Try:[/yellow]")
            console.print(
                "   1. Scrape some listings first: [cyan]uv run sussed scrape -c brno -m 5[/cyan]"
            )
            console.print("   2. Adjust your config criteria")

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        if verbose:
            import traceback

            console.print(traceback.format_exc())
        raise typer.Exit(1) from e


@review_app.command("candidates")
def review_candidates(
    limit: int = typer.Option(5, "--limit", "-l", min=1, max=100),
    city: str | None = typer.Option(None, "--city", "-c"),
    stale_after_days: int = typer.Option(30, "--stale-after-days", min=1),
    max_age_days: int | None = typer.Option(
        None, "--max-age-days", min=1, help="Only include listings first seen within N days"
    ),
    min_quick_score: int | None = typer.Option(
        None, "--min-quick-score", help="Only include listings with hunt quick score >= N"
    ),
    order_by_recent: bool = typer.Option(
        False, "--recent", help="Sort by first_seen_at DESC (latest first)"
    ),
    property_type: str | None = typer.Option(
        None,
        "--property-type",
        "-p",
        help="Filter by property type: apartment, house, cottage, or garden",
    ),
) -> None:
    """Print review candidates as JSON."""

    async def _run() -> list[dict[str, object]]:
        from sussed.db.connection import get_session
        from sussed.review.service import get_review_candidates

        async with get_session() as session:
            listings = await get_review_candidates(
                session=session,
                limit=limit,
                city=city,
                stale_after_days=stale_after_days,
                max_age_days=max_age_days,
                min_quick_score=min_quick_score,
                order_by_recent=order_by_recent,
                property_type=property_type,
            )
            return [
                {
                    "id": str(listing.id),
                    "external_id": listing.external_id,
                    "title": listing.title,
                    "price_czk": listing.price_czk,
                    "price_per_m2": listing.price_per_m2,
                    "city": listing.city,
                    "district": listing.district,
                    "property_category": (
                        listing.property_category.value if listing.property_category else None
                    ),
                    "ai_score": listing.ai_score,
                    "quick_score": (
                        listing.ai_analysis.get("score") if listing.ai_analysis else None
                    ),
                    "ai_reviewed_at": (
                        listing.ai_reviewed_at.isoformat() if listing.ai_reviewed_at else None
                    ),
                    "first_seen_at": (
                        listing.first_seen_at.isoformat() if listing.first_seen_at else None
                    ),
                    "last_price_change_at": (
                        listing.last_price_change_at.isoformat()
                        if listing.last_price_change_at
                        else None
                    ),
                    "has_description": bool(listing.description),
                    "image_count": listing.image_count,
                }
                for listing in listings
            ]

    console.print_json(json.dumps(asyncio.run(_run()), ensure_ascii=False, default=str))


@review_app.command("prepare")
def review_prepare(
    listing_id: str = typer.Argument(...),
    output: str | None = typer.Option(None, "--output", "-o"),
    image_limit: int = typer.Option(5, "--image-limit", min=0, max=20),
    cache_dir: str = typer.Option(".sussed/image-cache", "--cache-dir"),
) -> None:
    """Prepare one listing for skill-based AI review.

    Reads already-cached photos from ``--cache-dir`` (default
    ``.sussed/image-cache``). Run ``sussed enrich`` first to populate the cache;
    this command never downloads anything.
    """

    async def _run() -> dict[str, object]:
        from pathlib import Path

        from sqlalchemy import text
        from sqlmodel import select

        from sussed.db.connection import get_session
        from sussed.db.models import Listing
        from sussed.review.service import (
            get_price_history_payload,
            list_cached_image_paths,
            prepare_review_payload_from_listing,
        )

        async with get_session() as session:
            stmt = (
                select(Listing)
                .where(text("CAST(id AS TEXT) LIKE :prefix"))
                .params(prefix=f"{_validate_partial_uuid_prefix(listing_id)}%")
            )
            result = await session.execute(stmt)
            listings = list(result.scalars().all())
            if not listings:
                raise ValueError(f"No listing found for prefix {listing_id}")
            if len(listings) > 1:
                raise ValueError(f"Multiple listings match prefix {listing_id}; use a longer ID")

            listing = listings[0]
            image_paths = list_cached_image_paths(
                cache_root=Path(cache_dir),
                listing_id=listing.id,
                limit=image_limit,
            )
            price_history = await get_price_history_payload(session, listing.id)
            payload = prepare_review_payload_from_listing(
                listing=listing,
                image_paths=image_paths,
                detail_items=[],
                price_history=price_history,
            )
            return payload.model_dump(mode="json")

    payload = asyncio.run(_run())
    content = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    if output:
        from pathlib import Path

        Path(output).write_text(content, encoding="utf-8")
        console.print_json(
            json.dumps(
                {"output": output, "listing_id": payload["listing_id"]},
                ensure_ascii=False,
                default=str,
            )
        )
    else:
        console.print_json(content)


@review_app.command("prepare-batch")
def review_prepare_batch(
    count: int = typer.Option(
        10, "--count", "-n", min=1, max=100, help="Number of listings to prepare"
    ),
    image_limit: int = typer.Option(5, "--image-limit", min=0, max=20),
    cache_dir: str = typer.Option(".sussed/image-cache", "--cache-dir"),
    city: str | None = typer.Option(None, "--city", "-c"),
    stale_after_days: int = typer.Option(30, "--stale-after-days", min=1),
    max_age_days: int | None = typer.Option(
        None, "--max-age-days", min=1, help="Only prepare listings first seen within N days"
    ),
    min_quick_score: int | None = typer.Option(
        None, "--min-quick-score", help="Only prepare listings with hunt quick score >= N"
    ),
    order_by_recent: bool = typer.Option(
        False, "--recent", help="Sort by first_seen_at DESC (latest first)"
    ),
    property_type: str | None = typer.Option(
        None,
        "--property-type",
        "-p",
        help="Filter by property type: apartment, house, cottage, or garden",
    ),
) -> None:
    """Prepare multiple listings for batch AI review.

    Selects top candidates from the review queue and prepares each one,
    writing JSON payloads to the cache directory.

    Examples:
        # Prepare 20 apartments
        uv run sussed review prepare-batch -n 20

        # Prepare cottages only
        uv run sussed review prepare-batch -n 20 -p cottage

        # Prepare with more photos per listing
        uv run sussed review prepare-batch -n 10 --image-limit 15
    """

    async def _run() -> list[dict[str, object]]:
        from pathlib import Path

        from sussed.db.connection import get_session
        from sussed.review.service import (
            get_price_history_payload,
            get_review_candidates,
            list_cached_image_paths,
            prepare_review_payload_from_listing,
        )

        results: list[dict[str, object]] = []
        async with get_session() as session:
            candidates = await get_review_candidates(
                session=session,
                limit=count,
                city=city,
                stale_after_days=stale_after_days,
                max_age_days=max_age_days,
                min_quick_score=min_quick_score,
                order_by_recent=order_by_recent,
                property_type=property_type,
            )
            if not candidates:
                return results

            for listing in candidates:
                prefix = str(listing.id)[:8]
                output_path = f"{cache_dir}/{prefix}-prepared.json"
                image_paths = list_cached_image_paths(
                    cache_root=Path(cache_dir),
                    listing_id=listing.id,
                    limit=image_limit,
                )
                price_history = await get_price_history_payload(session, listing.id)
                payload = prepare_review_payload_from_listing(
                    listing=listing,
                    image_paths=image_paths,
                    detail_items=[],
                    price_history=price_history,
                )
                content = json.dumps(
                    payload.model_dump(mode="json"), ensure_ascii=False, indent=2, default=str
                )
                Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                Path(output_path).write_text(content, encoding="utf-8")
                results.append(
                    {
                        "prefix": prefix,
                        "listing_id": str(listing.id),
                        "district": listing.district,
                        "title": listing.title,
                        "output": output_path,
                    }
                )

        return results

    prepared = asyncio.run(_run())
    if not prepared:
        console.print("[yellow]No candidates to prepare.[/yellow]")
        return

    console.print_json(json.dumps(prepared, ensure_ascii=False, default=str))
    console.print(f"\n[green]✅ Prepared {len(prepared)} listings for review[/green]")


@review_app.command("validate")
def review_validate(
    input_file: str = typer.Argument(..., help="Path to a review JSON file"),
) -> None:
    """Validate a review JSON payload without saving it 🧪

    Returns exit code 0 when the file matches the ``ReviewResultInput`` schema.
    Exits with code 1 and prints field-level errors when validation fails.

    Examples:
        # Validate one file
        uv run sussed review validate .sussed/reviews-garden/abcdef12-review.json

        # Validate every file in a directory
        for f in .sussed/reviews-garden/*-review.json; do
            uv run sussed review validate "$f" || break
        done
    """
    from pathlib import Path

    from pydantic import ValidationError

    from sussed.review.models import ReviewResultInput

    path = Path(input_file)
    if not path.exists():
        console.print(f"[red]❌ File not found:[/red] {input_file}")
        raise typer.Exit(code=1)

    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        console.print(f"[red]❌ Cannot read file:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    try:
        json.loads(raw_text)
    except json.JSONDecodeError as exc:
        console.print(f"[red]❌ Invalid JSON in {input_file}:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    try:
        ReviewResultInput.model_validate_json(raw_text)
    except ValidationError as exc:
        console.print(f"[red]❌ Schema validation failed for {input_file}:[/red]")
        for err in exc.errors():
            loc = ".".join(str(part) for part in err["loc"]) or "<root>"
            console.print(f"  • [yellow]{loc}[/yellow]: {err['msg']}")
        raise typer.Exit(code=1) from exc

    console.print(f"[green]✓ valid:[/green] {input_file}")


@review_app.command("save")
def review_save(
    listing_id: str = typer.Argument(...),
    input_file: str = typer.Option(..., "--input", "-i"),
) -> None:
    """Save a structured AI review JSON payload.

    The JSON is validated against the review schema before any database
    write. On schema errors, the listing is left untouched and field-level
    errors are printed so callers can fix the file and retry.
    """

    async def _run() -> dict[str, object]:
        from pathlib import Path

        from pydantic import ValidationError
        from sqlalchemy import text
        from sqlmodel import select

        from sussed.db.connection import get_session
        from sussed.db.models import Listing
        from sussed.review.models import ReviewResultInput
        from sussed.review.service import save_listing_review

        path = Path(input_file)
        if not path.exists():
            raise FileNotFoundError(f"Review file not found: {input_file}")

        raw_text = path.read_text(encoding="utf-8")
        try:
            review_input = ReviewResultInput.model_validate_json(raw_text)
        except ValidationError as exc:
            lines = [f"Review JSON failed validation ({input_file}):"]
            for err in exc.errors():
                loc = ".".join(str(part) for part in err["loc"]) or "<root>"
                lines.append(f"  - {loc}: {err['msg']}")
            raise ValueError("\n".join(lines)) from exc

        async with get_session() as session:
            stmt = (
                select(Listing)
                .where(text("CAST(id AS TEXT) LIKE :prefix"))
                .params(prefix=f"{_validate_partial_uuid_prefix(listing_id)}%")
            )
            result = await session.execute(stmt)
            listings = list(result.scalars().all())
            if not listings:
                raise ValueError(f"No listing found for prefix {listing_id}")
            if len(listings) > 1:
                raise ValueError(f"Multiple listings match prefix {listing_id}; use a longer ID")

            review = await save_listing_review(session, listings[0], review_input)
            await session.commit()
            return {
                "review_id": str(review.id),
                "listing_id": str(review.listing_id),
                "score": review.score,
                "vibe": review.vibe.value,
                "reviewed_at": review.reviewed_at.isoformat(),
            }

    try:
        result = asyncio.run(_run())
    except (ValueError, FileNotFoundError) as exc:
        console.print(f"[red]❌ {exc}[/red]")
        raise typer.Exit(code=1) from exc

    console.print_json(json.dumps(result, ensure_ascii=False, default=str))


@review_app.command("status")
def review_status() -> None:
    """Show review queue status."""

    async def _run() -> dict[str, int]:
        from sqlalchemy import func
        from sqlmodel import select

        from sussed.db.connection import get_session
        from sussed.db.models import Listing, ListingStatus

        async with get_session() as session:
            active = await session.scalar(
                select(func.count())
                .select_from(Listing)
                .where(Listing.status == ListingStatus.ACTIVE)
            )
            reviewed = await session.scalar(
                select(func.count())
                .select_from(Listing)
                .where(Listing.status == ListingStatus.ACTIVE, Listing.ai_reviewed_at.isnot(None))
            )
            return {
                "active": int(active or 0),
                "reviewed": int(reviewed or 0),
                "unreviewed": int((active or 0) - (reviewed or 0)),
            }

    console.print_json(json.dumps(asyncio.run(_run()), ensure_ascii=False))


@review_app.command("picks")
def review_picks(
    all_listings: bool = typer.Option(False, "--all", "-a", help="Include unreviewed listings too"),
    district: str | None = typer.Option(
        None, "--district", "-d", help="Filter by district name (fuzzy)"
    ),
    min_score: int | None = typer.Option(None, "--min-score", "-m", help="Minimum AI score"),
    max_age_days: int | None = typer.Option(
        None,
        "--max-age-days",
        help="Only include listings first seen within N days",
    ),
    property_type: str | None = typer.Option(
        None,
        "--property-type",
        "-p",
        help="Filter by property type: apartment, house, cottage, or garden",
    ),
    limit: int = typer.Option(20, "--limit", "-l", min=1, max=200, help="Max results"),
    format_output: str = typer.Option(
        "table", "--format", "-f", help="Output format: table or json"
    ),
) -> None:
    """Show AI-reviewed picks 🏆

    Display scored listings ranked by AI review score.
    By default shows only AI-reviewed listings. Use --all to include unreviewed.

    Examples:
        # Show top reviewed picks
        uv run sussed review picks

        # Include unreviewed listings
        uv run sussed review picks --all

        # Filter by district
        uv run sussed review picks -d "Královo Pole"

        # Only high scorers
        uv run sussed review picks --min-score 700

        # Only listings posted in the last week
        uv run sussed review picks --max-age-days 7

        # Only cottages or gardens
        uv run sussed review picks -p cottage
        uv run sussed review picks -p garden

        # JSON output
        uv run sussed review picks -f json
    """

    async def _run() -> list[dict[str, object]]:
        from sussed.db.connection import get_session
        from sussed.review.service import get_reviewed_picks

        async with get_session() as session:
            listings = await get_reviewed_picks(
                session,
                include_unreviewed=all_listings,
                district=district,
                min_score=min_score,
                max_age_days=max_age_days,
                property_type=property_type,
                limit=limit,
            )
            return [
                {
                    "id": str(listing.id),
                    "external_id": listing.external_id,
                    "title": listing.title,
                    "district": listing.district,
                    "price_czk": listing.price_czk,
                    "price_per_m2": listing.price_per_m2,
                    "area_m2": float(listing.area_m2) if listing.area_m2 else None,
                    "apartment_type": listing.apartment_type,
                    "floor": listing.floor,
                    "ai_score": listing.ai_score,
                    "ai_vibe": listing.ai_vibe.value if listing.ai_vibe else None,
                    "ai_summary": listing.ai_summary,
                    "parking_price": (
                        listing.ai_analysis.get("parking_price") if listing.ai_analysis else None
                    ),
                    "parking_included": (
                        listing.ai_analysis.get("parking_included") if listing.ai_analysis else None
                    ),
                    "usable_area_m2": (
                        listing.ai_analysis.get("usable_area_m2") if listing.ai_analysis else None
                    ),
                    "updated_at_source": listing.updated_at_source.isoformat()
                    if listing.updated_at_source
                    else None,
                    "first_seen_at": listing.first_seen_at.isoformat()
                    if listing.first_seen_at
                    else None,
                    "url": listing.url,
                }
                for listing in listings
            ]

    results = asyncio.run(_run())

    if format_output == "json":
        console.print_json(json.dumps(results, ensure_ascii=False, default=str))
        return

    if not results:
        console.print("[yellow]No picks found. Try --all or adjust filters.[/yellow]")
        return

    def score_emoji(score: int | None) -> str:
        if score is None:
            return "❓"
        if score == 9999:
            return "🦄"
        if score >= 800:
            return "🔥"
        if score >= 600:
            return "✅"
        if score >= 400:
            return "🤔"
        if score >= 200:
            return "😐"
        if score >= 0:
            return "👎"
        return "🚩"

    def vibe_emoji(vibe: str | None) -> str:
        return {"peak": "🔥", "valid": "✅", "mid": "🤔", "sus": "🚩"}.get(vibe or "", "❓")

    def parking_label(row: dict[str, object]) -> str:
        if row.get("parking_included") is True:
            return "[green]✓ incl[/green]"
        if row.get("parking_price"):
            return f"{row['parking_price']:,}"
        return "[dim]—[/dim]"

    table = Table(title="🏆 AI Review Picks", show_lines=False)
    table.add_column("Score", justify="right", style="bold", min_width=5)
    table.add_column("Vibe", justify="center", min_width=4)
    table.add_column("Title", style="cyan", no_wrap=True, max_width=18)
    table.add_column("District", style="green", max_width=10)
    table.add_column("Price", justify="right", max_width=9)
    table.add_column("CZK/m²", justify="right", max_width=7)
    table.add_column("m²", justify="right", max_width=3)
    table.add_column("Parking", justify="right", min_width=7, max_width=7)
    table.add_column("Source Date", justify="right", min_width=11, max_width=11)
    table.add_column("URL", style="blue", no_wrap=True)

    for row in results:
        score = row["ai_score"]
        emoji = score_emoji(score if isinstance(score, int) else None)
        vibe = vibe_emoji(
            row.get("ai_vibe") if isinstance(row.get("ai_vibe"), str | None) else None
        )
        price_fmt = f"{row['price_czk']:,}" if row["price_czk"] else "N/A"

        # If AI computed a corrected usable area, show TRUE price/m² and mark with "*"
        usable = row.get("usable_area_m2")
        parking = row.get("parking_price") or 0
        if usable and row["price_czk"]:
            true_price_per_m2 = int((row["price_czk"] + parking) / float(usable))
            m2_fmt = f"[bold]{true_price_per_m2:,}*[/bold]"
            area_fmt = f"[bold]{float(usable):.0f}*[/bold]"
        else:
            m2_fmt = f"{row['price_per_m2']:,}" if row.get("price_per_m2") else "N/A"
            area_fmt = f"{row['area_m2']:.0f}" if row.get("area_m2") else "N/A"

        source_date = row.get("updated_at_source") or row.get("first_seen_at")
        source_date_fmt = source_date[:10] if isinstance(source_date, str) else "—"
        score_str = f"{emoji} {score}" if score is not None else f"{emoji} —"

        table.add_row(
            score_str,
            vibe,
            row["title"] or "N/A",
            row["district"] or "N/A",
            price_fmt,
            m2_fmt,
            area_fmt,
            parking_label(row),
            source_date_fmt,
            row["url"] or "N/A",
        )

    console.print(table)
    console.print(
        f"\n[dim]Showing {len(results)} listings. "
        "Bold * = AI-corrected usable area (incl. parking in price/m²)[/dim]"
    )


@service_app.command("install")
def service_install(
    time: str = typer.Option(
        "10:00",
        "--time",
        "-t",
        help="Daily run time in HH:MM format (24h)",
    ),
    config: str = typer.Option(
        "search_config.yaml",
        "--config",
        "-c",
        help="Path to search config YAML file",
    ),
) -> None:
    """Install the daily sussed service 🚀

    Sets up a scheduled service that runs daily to:
    1. Scrape fresh listings with sussed hunt
    2. AI-review top candidates via Copilot CLI
    3. Generate a daily report with top picks
    4. Send a desktop notification when done

    On macOS: installs a launchd agent (also runs at login if today's run
    was missed). On Linux: installs a systemd user timer with Persistent=true
    (catches up after boot).

    Run this from the sussed project directory (where pyproject.toml lives).

    Examples:
        # Install with defaults (10:00 AM daily)
        uv run sussed service install

        # Custom time
        uv run sussed service install --time 07:30

        # Custom config
        uv run sussed service install -c my_search.yaml -t 09:00
    """
    from sussed.service import install_service

    try:
        install_service(time_str=time, config_path=config)
    except (FileNotFoundError, ValueError, RuntimeError, OSError) as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1) from e


@service_app.command("uninstall")
def service_uninstall() -> None:
    """Uninstall the daily sussed service 🗑️

    Stops and removes the scheduled service. Keeps logs and data in
    ~/.sussed/ so you don't lose history.

    Examples:
        uv run sussed service uninstall
    """
    from sussed.service import uninstall_service

    try:
        uninstall_service()
    except (RuntimeError, OSError) as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1) from e


@service_app.command("status")
def service_status() -> None:
    """Show daily service status 📊

    Displays whether the service is installed, the last run date, and
    recent report files.

    Examples:
        uv run sussed service status
    """
    from sussed.service import show_service_status

    try:
        show_service_status()
    except (RuntimeError, OSError) as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1) from e


@dedup_app.command("list")
def list_duplicates(
    status: str | None = typer.Option(
        None,
        "--status",
        "-s",
        help="Filter by duplicate_status: duplicate or suspected",
    ),
    city: str | None = typer.Option(None, "--city", "-c", help="Filter by city (case-insensitive)"),
    source: str | None = typer.Option(None, "--source", help="Filter by listing source"),
    limit: int = typer.Option(50, "--limit", "-l", help="Max results to show"),
) -> None:
    """Show flagged duplicate / relisting pairs 🔍

    Displays listings that have been flagged as duplicates or suspected
    relistings of an older twin, ordered by confidence descending.

    Examples:
        uv run sussed dedup list
        uv run sussed dedup list --status duplicate
        uv run sussed dedup list --city brno --limit 20
    """

    async def _run() -> None:
        from sqlmodel import select

        from sussed.db.connection import get_session
        from sussed.db.models import Listing

        async with get_session() as session:
            stmt = select(Listing).where(Listing.duplicate_of_id.is_not(None))
            if status:
                stmt = stmt.where(Listing.duplicate_status == status)
            if city:
                stmt = stmt.where(Listing.city.ilike(f"%{city}%"))
            if source:
                stmt = stmt.where(Listing.source == source)
            stmt = stmt.order_by(Listing.duplicate_confidence.desc().nulls_last()).limit(limit)

            result = await session.execute(stmt)
            newer_rows = list(result.scalars().all())

            if not newer_rows:
                console.print("[yellow]No duplicates flagged. Run `dedup scan` first.[/yellow]")
                return

            twin_ids = [r.duplicate_of_id for r in newer_rows if r.duplicate_of_id]
            twin_map = {}
            if twin_ids:
                twin_result = await session.execute(
                    select(Listing).where(Listing.id.in_(twin_ids))
                )
                twin_map = {t.id: t for t in twin_result.scalars().all()}

            table = Table(title="🔍 Flagged Duplicates / Relistings", show_lines=True)
            table.add_column("Newer ID", style="cyan", min_width=8, no_wrap=True)
            table.add_column("Newer Title", style="cyan", max_width=22, no_wrap=True)
            table.add_column("Status", min_width=9)
            table.add_column("Conf", justify="right", min_width=5)
            table.add_column("Price Δ", justify="right", min_width=16)
            table.add_column("Older ID", style="green", min_width=8, no_wrap=True)
            table.add_column("Older Title", style="green", max_width=22, no_wrap=True)
            table.add_column("Reasons", max_width=36)
            table.add_column("Newer URL", style="blue", no_wrap=True)
            table.add_column("Older URL", style="blue", no_wrap=True)

            for row in newer_rows:
                conf_str = f"{float(row.duplicate_confidence):.2f}" if row.duplicate_confidence else "—"
                status_str = row.duplicate_status or "—"
                newer_id = str(row.id)[:8]
                newer_title = (row.title or "")[:22]

                twin = twin_map.get(row.duplicate_of_id) if row.duplicate_of_id else None
                if twin:
                    older_id = str(twin.id)[:8]
                    older_title = (twin.title or "")[:22]
                    older_url = twin.url or "—"
                    if twin.price_czk and twin.price_czk > 0:
                        delta = (row.price_czk or 0) - twin.price_czk
                        pct = delta / twin.price_czk * 100
                        sign = "+" if delta >= 0 else ""
                        delta_str = f"{sign}{delta:,} ({sign}{pct:.1f}%)"
                    else:
                        delta_str = "—"
                else:
                    older_id = str(row.duplicate_of_id)[:8] if row.duplicate_of_id else "—"
                    older_title = "[dim]not found[/dim]"
                    older_url = "—"
                    delta_str = "—"

                reasons = row.duplicate_reasons or []
                reasons_str = "; ".join(reasons[:3])
                if len(reasons) > 3:
                    reasons_str += f" (+{len(reasons) - 3})"

                table.add_row(
                    newer_id,
                    newer_title,
                    status_str,
                    conf_str,
                    delta_str,
                    older_id,
                    older_title,
                    reasons_str,
                    row.url or "—",
                    older_url,
                )

            console.print(table)
            console.print(f"\n[dim]Showing {len(newer_rows)} flagged listing(s).[/dim]")

    asyncio.run(_run())


@dedup_app.command("scan")
def dedup_scan(
    source: str = typer.Option("sreality", "--source", help="Source to scan"),
    city: str | None = typer.Option(None, "--city", "-c", help="Filter by city (case-insensitive)"),
    since: int | None = typer.Option(
        None, "--since", help="Only listings first_seen_at within the last N days"
    ),
    unchecked: bool = typer.Option(
        False, "--unchecked", is_flag=True, help="Only listings not yet checked"
    ),
    limit: int = typer.Option(0, "--limit", "-l", help="Max listings to scan (0 = no cap)"),
    dry_run: bool = typer.Option(False, "--dry-run", is_flag=True, help="Preview without saving"),
    force: bool = typer.Option(
        False, "--force", is_flag=True, help="Re-check even already-checked listings"
    ),
) -> None:
    """Backfill duplicate detection over stored listings (no API calls) 🔍

    Runs dedup detection using stored fields only — no sreality API calls are
    made. Fresh scrapes already run detection at ingest; use this command to
    backfill older listings or re-score with updated logic.

    Examples:
        uv run sussed dedup scan --source sreality --city brno
        uv run sussed dedup scan --unchecked --limit 500
        uv run sussed dedup scan --dry-run --since 30
        uv run sussed dedup scan --force --limit 100
    """

    async def _run() -> None:
        from datetime import UTC, datetime, timedelta

        from loguru import logger
        from sqlmodel import select

        from sussed.db.connection import get_session
        from sussed.db.models import Listing
        from sussed.dedup.detector import check_listing

        async with get_session() as session:
            stmt = select(Listing).where(Listing.source == source)
            if city:
                stmt = stmt.where(Listing.city.ilike(f"%{city}%"))
            if since is not None:
                cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=since)
                stmt = stmt.where(Listing.first_seen_at >= cutoff)
            if unchecked:
                stmt = stmt.where(Listing.duplicate_checked_at.is_(None))
            stmt = stmt.order_by(Listing.first_seen_at.asc())
            if limit > 0:
                stmt = stmt.limit(limit)

            result = await session.execute(stmt)
            listings = list(result.scalars().all())

            scanned = 0
            flagged_counts: dict[str, int] = {"duplicate": 0, "suspected": 0}

            for listing in listings:
                try:
                    match = await check_listing(
                        session, listing, allow_fetch=False, force=force
                    )
                except Exception as exc:
                    logger.warning(f"Error checking listing {listing.id}: {exc}")
                    continue

                scanned += 1
                if match is not None and match.status:
                    flagged_counts[match.status] = flagged_counts.get(match.status, 0) + 1

                if not dry_run and scanned % 100 == 0:
                    await session.commit()

            if dry_run:
                await session.rollback()
                console.print("[yellow]DRY RUN — no changes saved[/yellow]")
            else:
                await session.commit()

            total_flagged = sum(flagged_counts.values())
            dup = flagged_counts.get("duplicate", 0)
            sus = flagged_counts.get("suspected", 0)
            console.print(
                f"Scanned [bold]{scanned}[/bold] listing(s). "
                f"Flagged [bold]{total_flagged}[/bold] "
                f"([green]{dup} duplicate[/green], [yellow]{sus} suspected[/yellow])."
            )

    asyncio.run(_run())


if __name__ == "__main__":
    app()
