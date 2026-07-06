from __future__ import annotations

import json
import re
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from typer.testing import CliRunner

from sussed.cli import app
from sussed.feed.models import FeedContext, FeedData, FeedPost

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

runner = CliRunner()


@asynccontextmanager
async def _fake_session() -> AsyncIterator[object]:
    yield object()


def _sample_post() -> FeedPost:
    return FeedPost(
        id="abc-1",
        external_id="1",
        source="sreality",
        url="https://www.sreality.cz/detail/1",
        title="Prodej bytu 2+kk 55 m²",
        listing_type="sale",
        property_category="apartment",
        city="Brno",
        district="Střed",
        price_czk=5_500_000,
        score=815,
        is_reviewed=True,
        pros=["below market"],
    )


def _make_build(ai_count: int, fresh_count: int, captured: dict[str, Any] | None = None) -> Any:
    async def _fake_build_feed_data(
        session: object,  # noqa: ARG001
        *,
        title: str,
        **kwargs: Any,
    ) -> tuple[FeedData, FeedContext]:
        if captured is not None:
            captured.update(kwargs)
            captured["title"] = title
        post = _sample_post()
        posts = {post.id: post} if (ai_count or fresh_count) else {}
        feed_data = FeedData(
            posts=posts,
            ai_picks=[post.id] * ai_count if ai_count else [],
            fresh=[post.id] * fresh_count if fresh_count else [],
        )
        context = FeedContext(
            title=title,
            generated_at=datetime(2026, 7, 6, tzinfo=UTC),
            fresh_days=kwargs.get("fresh_days", 7),
            ai_picks_count=ai_count,
            fresh_count=fresh_count,
        )
        return feed_data, context

    return _fake_build_feed_data


def _patch(monkeypatch: Any, build: Any) -> None:
    monkeypatch.setattr("sussed.db.connection.get_session", _fake_session)
    monkeypatch.setattr("sussed.feed.builder.build_feed_data", build)


def test_feed_writes_self_contained_html(monkeypatch: Any, tmp_path: Path) -> None:
    captured: dict[str, Any] = {}
    _patch(monkeypatch, _make_build(ai_count=1, fresh_count=1, captured=captured))
    out = tmp_path / "feed.html"

    result = runner.invoke(
        app,
        ["feed", "-o", str(out), "--limit", "10", "--fresh-days", "14", "-p", "apartment"],
    )

    assert result.exit_code == 0, result.output
    assert out.exists()
    html = out.read_text(encoding="utf-8")
    assert "<!DOCTYPE html>" in html
    assert 'id="feed-data"' in html
    # Options are forwarded to the builder.
    assert captured["limit"] == 10
    assert captured["fresh_days"] == 14
    assert captured["property_type"] == "apartment"
    assert captured["include_unreviewed_in_picks"] is False
    # Embedded payload round-trips.
    match = re.search(
        r'<script id="feed-data" type="application/json">(.*?)</script>', html, re.DOTALL
    )
    assert match is not None
    payload = json.loads(match.group(1))
    assert payload["context"]["ai_picks_count"] == 1


def test_feed_all_flag_includes_unreviewed(monkeypatch: Any, tmp_path: Path) -> None:
    captured: dict[str, Any] = {}
    _patch(monkeypatch, _make_build(ai_count=1, fresh_count=0, captured=captured))
    out = tmp_path / "feed.html"

    result = runner.invoke(app, ["feed", "-o", str(out), "--all"])

    assert result.exit_code == 0, result.output
    assert captured["include_unreviewed_in_picks"] is True


def test_feed_empty_shows_hint(monkeypatch: Any, tmp_path: Path) -> None:
    _patch(monkeypatch, _make_build(ai_count=0, fresh_count=0))
    out = tmp_path / "feed.html"

    result = runner.invoke(app, ["feed", "-o", str(out)])

    assert result.exit_code == 0, result.output
    assert out.exists()
    assert "No listings" in result.output


def test_feed_creates_missing_parent_dir(monkeypatch: Any, tmp_path: Path) -> None:
    _patch(monkeypatch, _make_build(ai_count=1, fresh_count=0))
    out = tmp_path / "nested" / "deep" / "feed.html"

    result = runner.invoke(app, ["feed", "-o", str(out)])

    assert result.exit_code == 0, result.output
    assert out.exists()


def test_feed_value_error_exits_nonzero(monkeypatch: Any, tmp_path: Path) -> None:
    async def _boom(session: object, *, title: str, **kwargs: Any) -> tuple[FeedData, FeedContext]:  # noqa: ARG001
        raise ValueError("Unknown property_type 'castle'. Valid: apartment, house")

    _patch(monkeypatch, _boom)
    out = tmp_path / "feed.html"

    result = runner.invoke(app, ["feed", "-o", str(out), "-p", "castle"])

    assert result.exit_code == 1
    assert "Unknown property_type" in result.output
    assert not out.exists()
