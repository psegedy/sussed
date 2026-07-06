from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta

from sussed.feed.models import FeedContext, FeedData, FeedPost
from sussed.feed.renderer import render_feed


def _sample_feed(title: str = "Sussed <Gallery> & Picks") -> tuple[FeedData, FeedContext]:
    generated_at = datetime(2026, 7, 6, 14, 22, 12, tzinfo=UTC)
    reviewed = FeedPost(
        id="reviewed-1",
        external_id="123",
        source="sreality",
        url="https://www.sreality.cz/detail/123",
        title="Killer flat </script><script>alert(1)</script> & <b>cheap</b>",
        listing_type="sale",
        property_category="apartment",
        apartment_type="2+kk",
        area_m2=56.5,
        floor=3,
        total_floors=6,
        city="Brno",
        district="Královo Pole",
        address="Somewhere 1",
        price_czk=6_200_000,
        price_per_m2=109_735,
        initial_price=6_500_000,
        last_change_amount=-300_000,
        last_change_percent=-4.6,
        change_direction="decrease",
        price_change_count=1,
        first_seen_at=generated_at - timedelta(days=1),
        source_updated_at=generated_at - timedelta(hours=5),
        ai_reviewed_at=generated_at,
        image_urls=["https://images.example.test/flat.jpg"],
        image_count=1,
        has_floor_plan=True,
        has_video=True,
        score=820,
        is_reviewed=True,
        vibe="PEAK",
        summary="Looks annoyingly good for the money.",
        recommendation="Go see it.",
        confidence=0.88,
        pros=["below market", "balcony"],
        cons_red=["busy street"],
        cons_yellow=["older kitchen"],
        hidden_costs={"fond_oprav": "4500 Kč"},
        parking_price=350_000,
        parking_included=False,
        usable_area_m2=54.0,
        agency_name="Brno Homes",
    )
    hunt_only = FeedPost(
        id="fresh-1",
        external_id="456",
        source="sreality",
        url="https://www.sreality.cz/detail/456",
        title="Fresh unreviewed garden",
        listing_type="rent",
        property_category="garden",
        city="Brno",
        district="Žabovřesky",
        price_czk=18_000,
        is_poa=False,
        dropped_to_poa=False,
        first_seen_at=generated_at - timedelta(hours=8),
        image_urls=[],
        image_count=0,
        score=None,
        is_reviewed=False,
        agency_name="Sreality",
    )
    feed_data = FeedData(
        posts={reviewed.id: reviewed, hunt_only.id: hunt_only},
        ai_picks=[reviewed.id],
        fresh=[reviewed.id, hunt_only.id],
    )
    context = FeedContext(
        title=title,
        generated_at=generated_at,
        fresh_days=8,
        ai_picks_count=1,
        fresh_count=2,
        filters={"city": "Brno"},
    )
    return feed_data, context


def _embedded_json(html: str) -> str:
    match = re.search(
        r'<script id="feed-data" type="application/json">(.*?)</script>', html, re.DOTALL
    )
    assert match is not None
    return match.group(1)


def test_render_feed_inlines_assets_and_hardens_json() -> None:
    feed_data, context = _sample_feed()

    html = render_feed(feed_data, context)

    assert isinstance(html, str)
    assert "<!DOCTYPE html>" in html
    assert 'id="feed-data"' in html
    assert "--accent" in html
    assert 'document.getElementById("feed-data")' in html
    assert "<title>Sussed &lt;Gallery&gt; &amp; Picks</title>" in html

    embedded = _embedded_json(html)
    assert "</script>" not in embedded
    assert "\\u003c/script\\u003e" in embedded
    assert "\\u0026" in embedded
    assert "\\u003cb\\u003echeap\\u003c/b\\u003e" in embedded

    for sentinel in (
        "__PAGE_TITLE__",
        "__STYLES__",
        "__SCRIPT__",
        "__FEED_DATA_JSON__",
        "__FRESH_DAYS__",
        "__GENERATED_AT__",
    ):
        assert sentinel not in html

    parsed = json.loads(embedded)
    assert parsed["ai_picks"] == ["reviewed-1"]
    assert parsed["fresh"] == ["reviewed-1", "fresh-1"]
    assert sorted(parsed["posts"]) == ["fresh-1", "reviewed-1"]
    assert parsed["posts"]["reviewed-1"]["title"].startswith("Killer flat </script>")
    assert parsed["context"]["title"] == context.title


def test_empty_feed_renders_tab_structure() -> None:
    _feed_data, context = _sample_feed("Empty & <Safe>")
    html = render_feed(FeedData(), context)

    assert "<!DOCTYPE html>" in html
    assert "AI Picks" in html
    assert "Fresh" in html
    assert 'class="feed"' in html
    assert json.loads(_embedded_json(html))["posts"] == {}
