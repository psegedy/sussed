"""Tests for run_refresh orchestration."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlmodel import delete, select

from sussed.db.connection import close_db, get_session, init_db
from sussed.db.models import Listing, ListingStatus, ListingType, PriceHistory, PropertyCategory
from sussed.models.sreality import SrealityV1Detail, SrealityV1Locality
from sussed.scrapers.refresh import run_refresh


def _gone(status: int) -> httpx.HTTPStatusError:
    req = httpx.Request("GET", "https://example.test/x")
    return httpx.HTTPStatusError("gone", request=req, response=httpx.Response(status, request=req))


def _detail(price_czk: float = 4_500_000.0, price_czk_m2: int = 82_000) -> SrealityV1Detail:
    return SrealityV1Detail(
        hash_id=12345,
        advert_name="Prodej bytu 2+kk 55 m2",
        price_czk=price_czk,
        price_czk_m2=price_czk_m2,
        advert_description="Updated description",
        since="2026-01-01",
        locality=SrealityV1Locality(),
    )


def _seed(source: str, external_id: str, **kw: object) -> Listing:
    base: dict = {
        "source": source,
        "external_id": external_id,
        "url": f"https://example.test/{external_id}",
        "title": "Prodej bytu 2+kk 55 m2",
        "price_czk": 5_000_000,
        "price_per_m2": 90_000,
        "listing_type": ListingType.SALE,
        "city": "Brno",
        "property_category": PropertyCategory.APARTMENT,
        "features": {},
        "raw_labels": [],
        "image_urls": [],
        "status": ListingStatus.ACTIVE,
        "last_seen_at": datetime(2026, 1, 1, 12, 0, 0),
    }
    base.update(kw)
    return Listing(**base)


async def _cleanup(source: str) -> None:
    """Delete all listings (and FK dependencies) for a test source."""
    from sussed.db.models import ListingReview

    async with get_session() as session:
        listing_ids = (
            await session.execute(select(Listing.id).where(Listing.source == source))
        ).scalars().all()
        if listing_ids:
            await session.execute(
                delete(ListingReview).where(ListingReview.listing_id.in_(listing_ids))
            )
            await session.execute(
                delete(PriceHistory).where(PriceHistory.listing_id.in_(listing_ids))
            )
        await session.execute(delete(Listing).where(Listing.source == source))


@pytest.mark.asyncio
async def test_refresh_marks_404_and_410_removed(monkeypatch: pytest.MonkeyPatch) -> None:
    await close_db()
    await init_db()
    source = "pytest-refresh-gone"
    await _cleanup(source)

    async with get_session() as session:
        session.add_all([_seed(source, "1404"), _seed(source, "1410")])

    scraper = SimpleNamespace(
        _rate_limit_wait=AsyncMock(),
        fetch_listing_details=AsyncMock(side_effect=[_gone(404), _gone(410)]),
    )
    monkeypatch.setattr("sussed.scrapers.refresh.SrealityScraper", lambda: scraper)

    stats = await run_refresh(source=source, limit=10)
    assert stats["removed"] == 2
    assert stats["checked"] == 2
    async with get_session() as session:
        rows = (
            await session.execute(select(Listing).where(Listing.source == source))
        ).scalars().all()
        assert all(r.status == ListingStatus.REMOVED for r in rows)


@pytest.mark.asyncio
async def test_refresh_price_change_updates_listing(monkeypatch: pytest.MonkeyPatch) -> None:
    await close_db()
    await init_db()
    source = "pytest-refresh-price"
    await _cleanup(source)

    async with get_session() as session:
        session.add(_seed(source, "1001", price_czk=5_000_000))

    scraper = SimpleNamespace(
        _rate_limit_wait=AsyncMock(),
        fetch_listing_details=AsyncMock(return_value=_detail(price_czk=4_500_000)),
    )
    monkeypatch.setattr("sussed.scrapers.refresh.SrealityScraper", lambda: scraper)

    stats = await run_refresh(source=source, limit=10)
    assert stats["price_changes"] == 1
    assert stats["updated"] == 1

    async with get_session() as session:
        listing = (
            await session.execute(select(Listing).where(Listing.source == source))
        ).scalar_one()
        assert listing.price_czk == 4_500_000
        rows = (
            await session.execute(
                select(PriceHistory).where(PriceHistory.listing_id == listing.id)
            )
        ).scalars().all()
        assert any(r.change_type == "decrease" and r.price_czk == 4_500_000 for r in rows)


@pytest.mark.asyncio
async def test_refresh_request_error_leaves_active(monkeypatch: pytest.MonkeyPatch) -> None:
    await close_db()
    await init_db()
    source = "pytest-refresh-neterr"
    await _cleanup(source)

    async with get_session() as session:
        session.add(_seed(source, "1002"))

    scraper = SimpleNamespace(
        _rate_limit_wait=AsyncMock(),
        fetch_listing_details=AsyncMock(
            side_effect=httpx.RequestError(
                "network down", request=httpx.Request("GET", "https://example.test/")
            )
        ),
    )
    monkeypatch.setattr("sussed.scrapers.refresh.SrealityScraper", lambda: scraper)

    stats = await run_refresh(source=source, limit=10)
    assert stats["errors"] == 1
    async with get_session() as session:
        row = (
            await session.execute(select(Listing).where(Listing.source == source))
        ).scalar_one()
        assert row.status == ListingStatus.ACTIVE


@pytest.mark.asyncio
async def test_refresh_dry_run_persists_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    await close_db()
    await init_db()
    source = "pytest-refresh-dryrun"
    await _cleanup(source)

    async with get_session() as session:
        session.add(_seed(source, "1003"))

    scraper = SimpleNamespace(
        _rate_limit_wait=AsyncMock(),
        fetch_listing_details=AsyncMock(side_effect=[_gone(404)]),
    )
    monkeypatch.setattr("sussed.scrapers.refresh.SrealityScraper", lambda: scraper)

    stats = await run_refresh(source=source, limit=10, dry_run=True)
    assert stats["removed"] == 1  # counted in stats

    async with get_session() as session:
        row = (
            await session.execute(select(Listing).where(Listing.source == source))
        ).scalar_one()
        assert row.status == ListingStatus.ACTIVE  # not persisted


@pytest.mark.asyncio
async def test_refresh_bumps_last_seen_at(monkeypatch: pytest.MonkeyPatch) -> None:
    await close_db()
    await init_db()
    source = "pytest-refresh-seen"
    old_seen_at = datetime(2026, 1, 1, 12, 0, 0)
    await _cleanup(source)

    async with get_session() as session:
        session.add(_seed(source, "1004", last_seen_at=old_seen_at))

    scraper = SimpleNamespace(
        _rate_limit_wait=AsyncMock(),
        fetch_listing_details=AsyncMock(return_value=_detail()),
    )
    monkeypatch.setattr("sussed.scrapers.refresh.SrealityScraper", lambda: scraper)

    before = datetime.utcnow()
    await run_refresh(source=source, limit=10)

    async with get_session() as session:
        row = (
            await session.execute(select(Listing).where(Listing.source == source))
        ).scalar_one()
        assert row.last_seen_at > old_seen_at
        assert row.last_seen_at >= before


@pytest.mark.asyncio
async def test_refresh_stale_days_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    await close_db()
    await init_db()
    source = "pytest-refresh-stale"
    now = datetime.utcnow()
    await _cleanup(source)

    async with get_session() as session:
        session.add_all([
            _seed(source, "1005", last_seen_at=now - timedelta(days=30)),
            _seed(source, "1006", last_seen_at=now),
        ])

    scraper = SimpleNamespace(
        _rate_limit_wait=AsyncMock(),
        fetch_listing_details=AsyncMock(return_value=_detail()),
    )
    monkeypatch.setattr("sussed.scrapers.refresh.SrealityScraper", lambda: scraper)

    stats = await run_refresh(source=source, limit=10, stale_days=14)
    assert stats["checked"] == 1


@pytest.mark.asyncio
async def test_refresh_limit_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    await close_db()
    await init_db()
    source = "pytest-refresh-limit"
    await _cleanup(source)

    async with get_session() as session:
        for i in range(3):
            session.add(_seed(source, f"200{i}", last_seen_at=datetime(2026, 1, i + 1, 0, 0)))

    scraper = SimpleNamespace(
        _rate_limit_wait=AsyncMock(),
        fetch_listing_details=AsyncMock(return_value=_detail()),
    )
    monkeypatch.setattr("sussed.scrapers.refresh.SrealityScraper", lambda: scraper)

    stats = await run_refresh(source=source, limit=2)
    assert stats["checked"] == 2


@pytest.mark.asyncio
async def test_refresh_price_drop_to_poa_zero_is_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    """A real price -> POA/0 must be persisted and recorded (not skipped as falsy)."""
    await close_db()
    await init_db()
    source = "pytest-refresh-poa"
    await _cleanup(source)

    async with get_session() as session:
        session.add(_seed(source, "1007", price_czk=5_000_000))

    scraper = SimpleNamespace(
        _rate_limit_wait=AsyncMock(),
        fetch_listing_details=AsyncMock(return_value=_detail(price_czk=0.0, price_czk_m2=0)),
    )
    monkeypatch.setattr("sussed.scrapers.refresh.SrealityScraper", lambda: scraper)

    stats = await run_refresh(source=source, limit=10)
    assert stats["price_changes"] == 1
    async with get_session() as session:
        row = (
            await session.execute(select(Listing).where(Listing.source == source))
        ).scalar_one()
        assert row.price_czk == 0


@pytest.mark.asyncio
async def test_refresh_skips_non_integer_external_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """A malformed external_id is counted as an error and skipped, but the run
    continues and still processes the other (valid) listings."""
    await close_db()
    await init_db()
    source = "pytest-refresh-badid"
    await _cleanup(source)

    async with get_session() as session:
        session.add_all([
            _seed(source, "not-an-int", last_seen_at=datetime(2026, 1, 1, 0, 0)),
            _seed(source, "3001", last_seen_at=datetime(2026, 1, 2, 0, 0)),
        ])

    scraper = SimpleNamespace(
        _rate_limit_wait=AsyncMock(),
        fetch_listing_details=AsyncMock(return_value=_detail()),
    )
    monkeypatch.setattr("sussed.scrapers.refresh.SrealityScraper", lambda: scraper)

    stats = await run_refresh(source=source, limit=10)
    assert stats["errors"] == 1
    assert stats["updated"] == 1
    assert stats["checked"] == 2
    # The bad-id listing was never fetched; only the valid one.
    assert scraper.fetch_listing_details.await_count == 1
