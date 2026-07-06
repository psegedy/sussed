"""Tests for `sussed dedup list` and `sussed dedup scan` CLI commands."""

from __future__ import annotations

import asyncio
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlmodel import delete, select
from typer.testing import CliRunner

from sussed.cli import app
from sussed.db.connection import close_db, get_session, init_db
from sussed.db.models import Listing, ListingStatus, ListingType, PropertyCategory

if TYPE_CHECKING:
    import pytest

runner = CliRunner()


def _listing(
    *,
    source: str,
    external_id: str,
    title: str = "Prodej bytu 2+kk 55 m²",
    description: str | None = "Krásný byt s dispozicí 2+kk a výbornou dostupností v Brně.",
    listing_type: ListingType = ListingType.SALE,
    property_category: PropertyCategory = PropertyCategory.APARTMENT,
    apartment_type: str | None = "2+kk",
    city: str = "Brno",
    latitude: Decimal | None = Decimal("49.2000000"),
    longitude: Decimal | None = Decimal("16.6000000"),
    area_m2: Decimal | None = Decimal("55"),
    floor: int | None = 3,
    status: ListingStatus = ListingStatus.ACTIVE,
    first_seen_at: datetime | None = None,
    created_at: datetime | None = None,
    duplicate_of_id=None,
    duplicate_status: str | None = None,
    duplicate_confidence: Decimal | None = None,
    duplicate_reasons: list[str] | None = None,
) -> Listing:
    seen_at = first_seen_at or datetime(2026, 1, 1, 12, 0, 0)
    return Listing(
        source=source,
        external_id=external_id,
        url=f"https://example.test/{external_id}",
        title=title,
        description=description,
        price_czk=5_500_000,
        listing_type=listing_type,
        city=city,
        district="Brno-město",
        property_category=property_category,
        apartment_type=apartment_type,
        latitude=latitude,
        longitude=longitude,
        area_m2=area_m2,
        floor=floor,
        features={},
        raw_labels=[],
        image_urls=["https://img.example.test/shared.jpg"],
        image_count=1,
        has_floor_plan=False,
        has_video=False,
        has_3d_tour=False,
        agency_name="Test Reality",
        status=status,
        first_seen_at=seen_at,
        last_seen_at=seen_at,
        created_at=created_at or seen_at,
        updated_at=created_at or seen_at,
        duplicate_of_id=duplicate_of_id,
        duplicate_status=duplicate_status,
        duplicate_confidence=duplicate_confidence,
        duplicate_reasons=duplicate_reasons,
    )


def test_dedup_scan_flags_duplicate_pair() -> None:
    """scan end-to-end: older removed + newer active with same GPS/area get flagged."""
    source = "pytest-cli-dedup-scan-happy"
    older_id_box: list = []

    async def _setup() -> None:
        await close_db()
        await init_db()
        async with get_session() as session:
            await session.execute(delete(Listing).where(Listing.source == source))
        older_seen = datetime(2026, 1, 1, 10, 0, 0)
        newer_seen = datetime(2026, 2, 1, 10, 0, 0)
        async with get_session() as session:
            older = _listing(
                source=source,
                external_id="OLD001",
                status=ListingStatus.REMOVED,
                first_seen_at=older_seen,
                created_at=older_seen,
            )
            newer = _listing(
                source=source,
                external_id="NEW001",
                status=ListingStatus.ACTIVE,
                first_seen_at=newer_seen,
                created_at=newer_seen,
            )
            session.add(older)
            session.add(newer)
            await session.flush()
            older_id_box.append(older.id)
        # Dispose pool so CLI can create its own in a fresh loop
        await close_db()

    asyncio.run(_setup())

    result = runner.invoke(app, ["dedup", "scan", "--source", source])
    assert result.exit_code == 0, result.output
    assert "Scanned" in result.output
    assert "Flagged 1" in result.output

    async def _verify() -> None:
        await close_db()
        await init_db()
        async with get_session() as session:
            row = (
                await session.execute(
                    select(Listing).where(
                        Listing.source == source, Listing.external_id == "NEW001"
                    )
                )
            ).scalars().first()
            assert row is not None
            assert row.duplicate_of_id == older_id_box[0]

    asyncio.run(_verify())


def test_dedup_scan_dry_run_does_not_persist() -> None:
    """--dry-run must not save anything to the DB."""
    source = "pytest-cli-dedup-scan-dryrun"

    async def _setup() -> None:
        await close_db()
        await init_db()
        async with get_session() as session:
            await session.execute(delete(Listing).where(Listing.source == source))
        older_seen = datetime(2026, 1, 1, 10, 0, 0)
        newer_seen = datetime(2026, 2, 1, 10, 0, 0)
        async with get_session() as session:
            session.add(
                _listing(
                    source=source,
                    external_id="OLD002",
                    status=ListingStatus.REMOVED,
                    first_seen_at=older_seen,
                    created_at=older_seen,
                )
            )
            session.add(
                _listing(
                    source=source,
                    external_id="NEW002",
                    status=ListingStatus.ACTIVE,
                    first_seen_at=newer_seen,
                    created_at=newer_seen,
                )
            )
        # Dispose pool so CLI can create its own in a fresh loop
        await close_db()

    asyncio.run(_setup())

    result = runner.invoke(app, ["dedup", "scan", "--source", source, "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "DRY RUN" in result.output

    async def _verify() -> None:
        await close_db()
        await init_db()
        async with get_session() as session:
            row = (
                await session.execute(
                    select(Listing).where(
                        Listing.source == source, Listing.external_id == "NEW002"
                    )
                )
            ).scalars().first()
            assert row is not None
            assert row.duplicate_of_id is None

    asyncio.run(_verify())


def test_dedup_list_shows_flagged_pair(monkeypatch: pytest.MonkeyPatch) -> None:
    """`dedup list` renders a flagged listing row with ids, title, and confidence."""
    source = "pytest-cli-dedup-list"
    ids: list = []

    async def _setup() -> None:
        await close_db()
        await init_db()
        async with get_session() as session:
            await session.execute(delete(Listing).where(Listing.source == source))
        async with get_session() as session:
            older = _listing(
                source=source,
                external_id="OLD003",
                title="Starší byt 2+kk",
                status=ListingStatus.REMOVED,
                first_seen_at=datetime(2026, 1, 1, 10, 0, 0),
            )
            session.add(older)
            await session.flush()
            older_id = older.id
            newer = _listing(
                source=source,
                external_id="NEW003",
                title="Novější byt 2+kk",
                status=ListingStatus.ACTIVE,
                first_seen_at=datetime(2026, 2, 1, 10, 0, 0),
                duplicate_of_id=older_id,
                duplicate_status="duplicate",
                duplicate_confidence=Decimal("0.920"),
                duplicate_reasons=["same GPS", "same area"],
            )
            session.add(newer)
            await session.flush()
            ids.extend([newer.id, older_id])
        # Dispose pool so CLI can create its own in a fresh loop
        await close_db()

    asyncio.run(_setup())

    monkeypatch.setattr("sussed.cli.console", __import__("rich.console", fromlist=["Console"]).Console(width=200, color_system=None))
    result = runner.invoke(app, ["dedup", "list"])
    assert result.exit_code == 0, result.output
    newer_id, older_id = ids
    assert str(newer_id)[:8] in result.output
    assert str(older_id)[:8] in result.output
    assert "0.92" in result.output
