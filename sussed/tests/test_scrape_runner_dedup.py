"""Tests for _dedup_new_listing integration in the scrape runner."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from sqlmodel import delete, select

from sussed.db.connection import close_db, get_session, init_db
from sussed.db.models import Listing, ListingStatus, ListingType, PropertyCategory
from sussed.scrapers.runner import _dedup_new_listing


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
    )


async def _clean_source(source: str) -> None:
    await close_db()
    await init_db()
    async with get_session() as session:
        await session.execute(delete(Listing).where(Listing.source == source))
        await session.commit()


@pytest.mark.asyncio
async def test_dedup_new_listing_flags_duplicate_and_increments_counter() -> None:
    """Happy path: newer listing matching an older removed one gets flagged."""
    source = "pytest-runner-dedup-happy"
    await _clean_source(source)

    old_time = datetime(2026, 1, 1, 10, 0, 0)
    new_time = datetime(2026, 1, 5, 10, 0, 0)
    description = "Krásný byt 2+kk v Brně s balkonem, sklepem a výbornou dostupností do centra."

    async with get_session() as session:
        older = _listing(
            source=source,
            external_id="runner-dedup-1",
            description=description,
            status=ListingStatus.REMOVED,
            first_seen_at=old_time,
        )
        newer = _listing(
            source=source,
            external_id="runner-dedup-2",
            description=f"{description} Volný ihned.",
            first_seen_at=new_time,
        )
        session.add_all([older, newer])
        await session.commit()

        mock_scraper = AsyncMock()
        mock_scraper.fetch_listing_details = AsyncMock(return_value=None)
        mock_client = AsyncMock()
        stats: dict[str, int] = {"duplicates_flagged": 0}

        await _dedup_new_listing(session, newer, mock_scraper, mock_client, stats)

    assert stats["duplicates_flagged"] == 1
    assert newer.duplicate_of_id == older.id
    assert newer.duplicate_status is not None


@pytest.mark.asyncio
async def test_dedup_new_listing_error_isolation() -> None:
    """check_listing raising must not propagate and must leave the session usable."""
    source = "pytest-runner-dedup-error"
    await _clean_source(source)

    async with get_session() as session:
        listing = _listing(source=source, external_id="runner-dedup-err-1")
        session.add(listing)
        await session.commit()

        mock_scraper = AsyncMock()
        mock_client = AsyncMock()
        stats: dict[str, int] = {"duplicates_flagged": 0}

        with patch("sussed.scrapers.runner.check_listing", side_effect=RuntimeError("boom")):
            await _dedup_new_listing(session, listing, mock_scraper, mock_client, stats)

        # Must not have raised; counter stays at 0
        assert stats["duplicates_flagged"] == 0

        # Session must still be usable after the savepoint rollback
        result = await session.execute(select(Listing).limit(1))
        assert result.scalars().first() is not None
