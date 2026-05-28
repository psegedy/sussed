"""Output formatting helpers for apartment hunts."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger
from rich.console import Console
from rich.table import Table

from sussed.hunt.config import OutputMode, SearchConfig

console = Console()


async def display_market_insights(scrape_stats: dict[str, Any] | None) -> None:
    """
    Display juicy market insights! 📊

    Shows:
    - Price change summary (drops = opportunities!)
    - New listings summary
    - Market temperature (hot/cold)
    - Notable deals
    """
    from datetime import datetime, timedelta

    from sqlmodel import desc, select

    from sussed.db.connection import get_session
    from sussed.db.models import PriceHistory

    console.print("\n[bold magenta]📊 Market Insights[/bold magenta]")

    # If we just scraped, show those stats
    if scrape_stats:
        new_count = scrape_stats.get("listings_new", 0)
        changes_count = scrape_stats.get("price_changes", 0)
        if new_count > 0 or changes_count > 0:
            console.print("\n[cyan]From this scrape:[/cyan]")
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
                price_drops = [
                    c for c in recent_changes if c.change_type == "decrease" and c.change_amount
                ]
                price_increases = [
                    c for c in recent_changes if c.change_type == "increase" and c.change_amount
                ]

                # Big drops (>5%)
                big_drops = [
                    c for c in price_drops if c.change_percent and abs(float(c.change_percent)) > 5
                ]

                console.print("\n[cyan]Last 7 days:[/cyan]")

                if price_drops:
                    avg_drop_pct = sum(
                        abs(float(c.change_percent)) for c in price_drops if c.change_percent
                    ) / len(price_drops)
                    console.print(
                        f"   • [green]{len(price_drops)} price drops[/green] 📉 (avg -{avg_drop_pct:.1f}%)"
                    )

                    if big_drops:
                        console.print(
                            f"   • [bold green]{len(big_drops)} BIG drops (>5%)[/bold green] - check these out! 🎯"
                        )

                        # Show top 3 biggest drops by percentage
                        big_drops.sort(
                            key=lambda c: abs(float(c.change_percent or 0)), reverse=True
                        )
                        for drop in big_drops[:3]:
                            drop_pct = abs(float(drop.change_percent or 0))
                            drop_czk = drop.change_amount or 0
                            console.print(
                                f"     → -{drop_pct:.1f}% (-{drop_czk:,.0f} Kč) on listing {drop.listing_id}"
                            )

                if price_increases:
                    avg_inc_pct = sum(
                        float(c.change_percent or 0) for c in price_increases if c.change_percent
                    ) / len(price_increases)
                    console.print(
                        f"   • [yellow]{len(price_increases)} price increases[/yellow] 📈 (avg +{avg_inc_pct:.1f}%)"
                    )

                # Market temperature
                if len(price_drops) > len(price_increases) * 2:
                    console.print(
                        "\n   [bold green]🥶 BUYER'S MARKET[/bold green] - sellers are getting desperate!"
                    )
                elif len(price_increases) > len(price_drops) * 2:
                    console.print(
                        "\n   [bold red]🔥 SELLER'S MARKET[/bold red] - prices going up, act fast!"
                    )
                else:
                    console.print("\n   [dim]😐 Market is balanced[/dim]")
            else:
                console.print(
                    "\n   [dim]No price changes in the last week. Scrape more to track trends![/dim]"
                )
    except Exception as e:
        logger.warning(f"Failed to get market insights: {e}")
        console.print(f"\n   [dim]Could not fetch market insights: {e}[/dim]")


def prepare_output(config: SearchConfig, listings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort and filter based on output config."""
    output = config.output

    # Filter by mode
    if output.mode == OutputMode.GEMS:
        listings = [item for item in listings if item.get("score", 0) >= 900]
    elif output.mode == OutputMode.SUS:
        listings = [
            item for item in listings if item.get("score", 0) < 200 or item.get("score", 0) == -1
        ]
    elif output.mode == OutputMode.TRASH:
        listings = [item for item in listings if item.get("score", 0) < 500]
    elif output.mode in (OutputMode.BEST, OutputMode.ALL):
        # Exclude auto-rejected listings (score -1) from best/all views
        listings = [item for item in listings if item.get("score", 0) != -1]

    # Sort
    if output.mode in (OutputMode.BEST, OutputMode.GEMS, OutputMode.ALL):
        from sussed.hunt.runner import sort_key

        listings.sort(key=lambda item: sort_key(item, config), reverse=True)
    else:  # TRASH, SUS - show worst first
        listings.sort(key=lambda x: x.get("score", 0))

    # Limit
    return listings[: output.limit]


def display_results(
    config: SearchConfig, results: list[dict[str, Any]], poa_price_threshold: int
) -> None:
    """Display results in requested format."""
    output = config.output

    if not results:
        console.print("\n[yellow]No results matching output criteria[/yellow]")
        return

    console.print(f"\n[bold green]Results ({len(results)} listings):[/bold green]\n")

    if output.format == "json":
        console.print_json(json.dumps(results, indent=2, ensure_ascii=False, default=str))
    elif output.format == "markdown":
        display_markdown(config, results)
    else:
        display_table(config, results, poa_price_threshold)

    # Save to file if requested
    if output.save_to_file:
        save_results(config, results)


def display_table(
    config: SearchConfig, results: list[dict[str, Any]], poa_price_threshold: int
) -> None:
    """Display results as Rich table."""
    table = Table(show_header=True, header_style="bold blue")

    table.add_column("Score", style="cyan", justify="right", width=6)
    table.add_column("Type", width=6)
    table.add_column("Price", justify="right", width=22)
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

        # Price display (handle POA + price drops)
        price = r.get("price_czk", 0)
        initial = r.get("initial_price")
        original = r.get("original_price")
        if r.get("is_poa"):
            if original:
                price_str = f"[dim]POA[/dim] [red]↓[/red] {original:,}"
            else:
                price_str = "[dim]POA[/dim]"
        elif initial and initial > poa_price_threshold and initial != price:
            diff = price - initial
            arrow = "[green]↓[/green]" if diff < 0 else "[red]↑[/red]"
            price_str = f"{price:,} {arrow} {initial:,}"
        else:
            price_str = f"{price:,}"

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
    if config.output.include_analysis and results:
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
                orig = r.get("original_price")
                if orig:
                    area = r.get("area_m2")
                    if area and area > 0:
                        console.print(
                            f"   💰 Price: [dim]POA[/dim] [bold red]⚠ dropped from {orig:,} Kč[/bold red] ({int(orig / area):,} Kč/m²)"
                        )
                    else:
                        console.print(
                            f"   💰 Price: [dim]POA[/dim] [bold red]⚠ dropped from {orig:,} Kč[/bold red]"
                        )
                else:
                    console.print("   💰 Price: [dim]POA (Price on Request)[/dim]")
            else:
                price = r.get("price_czk", 0)
                area = r.get("area_m2")
                if area and area > 0:
                    console.print(f"   💰 Price: {price:,} Kč ({int(price / area):,} Kč/m²)")
                else:
                    console.print(f"   💰 Price: {price:,} Kč")

            # Price change info — show the journey from initial to current
            price_changes = r.get("price_changes", [])
            if price_changes:
                initial = r.get("initial_price", 0)
                current = r.get("price_czk", 0)
                current_is_poa = current <= poa_price_threshold
                initial_is_poa = initial <= poa_price_threshold

                if not current_is_poa and not initial_is_poa and initial != current:
                    # Standard price change between two real prices
                    diff = current - initial
                    pct = (diff / initial) * 100
                    arrow = "📉" if diff < 0 else "📈"
                    color = "green" if diff < 0 else "red"
                    console.print(
                        f"   {arrow} [{color}]Price change: {diff:+,} Kč ({pct:+.1f}%) from {initial:,} Kč[/{color}]"
                    )
                elif current_is_poa and not initial_is_poa:
                    # Dropped to POA — this is a strong "negotiation mode" / "sus" signal
                    console.print(
                        f"   📉 [bold yellow]Switched to POA from {initial:,} Kč — seller hiding new price[/bold yellow]"
                    )
                elif not current_is_poa and initial_is_poa:
                    # Was POA, now has a real price
                    console.print(
                        f"   📢 [cyan]Price revealed: {current:,} Kč (was POA)[/cyan]"
                    )

                # Show count of changes when more than one
                if len(price_changes) > 1:
                    console.print(
                        f"   [dim]({len(price_changes)} price changes recorded)[/dim]"
                    )

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
                console.print(
                    f"   [bold magenta]🧠 AI:[/bold magenta] {llm.get('one_liner', 'N/A')}"
                )
                console.print(f"   [bold]Recommendation: {llm.get('recommendation', 'N/A')}[/bold]")
                if llm.get("hidden_costs"):
                    costs = ", ".join(f"{k}: {v:,} Kč" for k, v in llm["hidden_costs"].items() if v)
                    if costs:
                        console.print(f"   [yellow]💰 Hidden costs: {costs}[/yellow]")

            console.print("   [dim]─────────────────────────────────────────[/dim]")


def display_markdown(config: SearchConfig, results: list[dict[str, Any]]) -> None:
    """Display results as markdown."""
    lines = [
        f"# {config.name} - Results",
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

        # Price display with drop awareness
        price = r.get("price_czk", 0)
        initial = r.get("initial_price")
        original = r.get("original_price")
        if r.get("is_poa"):
            if original:
                price_display = f"**POA** (⚠ dropped from {original:,} Kč)"
            else:
                price_display = "**POA** (Price on Request)"
        elif initial and initial > 10 and initial != price:
            diff = price - initial
            arrow = "↓" if diff < 0 else "↑"
            pct = (diff / initial) * 100
            price_display = f"{price:,} Kč {arrow} (was {initial:,} Kč, {pct:+.1f}%)"
        else:
            price_display = f"{price:,} Kč"

        lines.extend(
            [
                f"## {i}. Score: {score} - {r.get('title', 'Unknown')}",
                "",
                f"- **Price:** {price_display}",
                f"- **Type:** {r.get('apartment_type', 'Unknown')}",
                f"- **Area:** {r.get('area_m2', 'Unknown')} m²",
                f"- **Location:** {r.get('district', '')}, {r.get('city', '')}",
                f"- **URL:** {r.get('url', 'N/A')}",
                "",
            ]
        )

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


def save_results(config: SearchConfig, results: list[dict[str, Any]]) -> None:
    """Save results to file."""
    output = config.output
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

    with Path(path).open("w", encoding="utf-8") as f:
        f.write(content)

    console.print(f"\n[green]✅ Saved to {path}[/green]")


def display_stats(stats: dict[str, int]) -> None:
    """Display processing stats."""
    console.print("\n[bold]Processing stats:[/bold]")

    table = Table(show_header=False, box=None)
    table.add_column("Metric", style="dim")
    table.add_column("Value", style="cyan")

    table.add_row("Total processed", str(stats["total_processed"]))
    table.add_row("Descriptions fetched", str(stats["descriptions_fetched"]))
    table.add_row("🧠 LLM analyzed", str(stats["llm_analyzed"]))
    table.add_row("Scored", str(stats["scored"]))
    table.add_row("POA listings (1 Kč)", str(stats["poa_listings"]))
    table.add_row("Skipped", str(stats["skipped"]))
    table.add_row("Errors", str(stats["errors"]))

    console.print(table)
