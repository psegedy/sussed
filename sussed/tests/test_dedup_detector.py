from __future__ import annotations

import json
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlmodel import delete

from sussed.db.connection import close_db, get_session, init_db
from sussed.db.models import Listing, ListingStatus, ListingType, PropertyCategory
from sussed.dedup.detector import check_listing, find_candidates
from sussed.models.sreality import SrealityV1Detail, SrealityV1DetailResponse

FIXTURE_DIR = Path(__file__).parent / "fixtures"


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
    )


async def _clean_source(source: str) -> None:
    await close_db()
    await init_db()
    async with get_session() as session:
        await session.execute(delete(Listing).where(Listing.source == source))


def _detail_fixture() -> SrealityV1Detail:
    data = json.loads((FIXTURE_DIR / "sreality_v1_detail_sample.json").read_text())
    return SrealityV1DetailResponse.model_validate(data).result


def _gone_error() -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://example.test/gone")
    response = httpx.Response(410, request=request)
    return httpx.HTTPStatusError("gone", request=request, response=response)


@pytest.mark.asyncio
async def test_find_candidates_blocks_and_ranks_same_listing_shape() -> None:
    source = "pytest-dedup-find-candidates"
    await _clean_source(source)

    async with get_session() as session:
        target = _listing(source=source, external_id="100")
        colocated = _listing(source=source, external_id="101", status=ListingStatus.REMOVED)
        different_type = _listing(source=source, external_id="102", listing_type=ListingType.RENT)
        different_apartment = _listing(source=source, external_id="103", apartment_type="3+kk")
        far_away = _listing(
            source=source,
            external_id="104",
            latitude=Decimal("49.3000000"),
            longitude=Decimal("16.7000000"),
        )
        session.add_all([target, colocated, different_type, different_apartment, far_away])
        await session.commit()

        candidates = await find_candidates(session, target)

    assert [candidate.external_id for candidate in candidates] == ["101"]


@pytest.mark.asyncio
async def test_check_listing_flags_true_relisting_and_is_idempotent() -> None:
    source = "pytest-dedup-true-relisting"
    await _clean_source(source)
    old_time = datetime(2026, 1, 1, 10, 0, 0)
    new_time = datetime(2026, 1, 3, 10, 0, 0)
    description = "Krásný byt 2+kk v Brně s balkonem, sklepem a výbornou dostupností do centra."

    async with get_session() as session:
        candidate = _listing(
            source=source,
            external_id="200",
            description=description,
            status=ListingStatus.REMOVED,
            first_seen_at=old_time,
        )
        listing = _listing(
            source=source,
            external_id="201",
            description=f"{description} Volný ihned.",
            first_seen_at=new_time,
        )
        session.add_all([candidate, listing])
        await session.commit()

        match = await check_listing(session, listing, allow_fetch=False)
        second_match = await check_listing(session, listing, allow_fetch=False)

    assert match is not None
    assert listing.duplicate_status == "duplicate"
    assert listing.duplicate_of_id == candidate.id
    assert listing.duplicate_confidence is not None
    assert second_match is None


@pytest.mark.asyncio
async def test_check_listing_marks_checked_when_no_candidate() -> None:
    source = "pytest-dedup-no-candidate"
    await _clean_source(source)

    async with get_session() as session:
        listing = _listing(source=source, external_id="300")
        session.add(listing)
        await session.commit()

        match = await check_listing(session, listing, allow_fetch=False)

    assert match is None
    assert listing.duplicate_checked_at is not None
    assert listing.duplicate_of_id is None


@pytest.mark.asyncio
async def test_check_listing_caps_same_run_active_duplicates_to_suspected() -> None:
    source = "pytest-dedup-same-run"
    await _clean_source(source)
    old_time = datetime(2026, 1, 1, 10, 0, 0)
    new_time = old_time + timedelta(hours=2)
    description = "Krásný byt 2+kk v Brně s balkonem, sklepem a výbornou dostupností do centra."

    async with get_session() as session:
        candidate = _listing(
            source=source, external_id="400", description=description, first_seen_at=old_time
        )
        listing = _listing(
            source=source,
            external_id="401",
            description=description,
            first_seen_at=new_time,
        )
        session.add_all([candidate, listing])
        await session.commit()

        match = await check_listing(session, listing, allow_fetch=False)

    assert match is not None
    assert listing.duplicate_status == "suspected"
    assert any("same scrape window" in reason for reason in (listing.duplicate_reasons or []))


@pytest.mark.asyncio
async def test_check_listing_fetches_missing_detail_and_marks_410_sold() -> None:
    source = "pytest-dedup-confirm-on-demand"
    await _clean_source(source)
    old_time = datetime(2026, 1, 1, 10, 0, 0)
    new_time = datetime(2026, 1, 3, 10, 0, 0)
    scraper = SimpleNamespace(
        fetch_listing_details=AsyncMock(side_effect=[_detail_fixture(), _gone_error()])
    )

    async with get_session() as session:
        candidate = _listing(
            source=source,
            external_id="500",
            description=None,
            floor=None,
            first_seen_at=old_time,
        )
        listing = _listing(
            source=source,
            external_id="501",
            description=None,
            floor=None,
            first_seen_at=new_time,
        )
        session.add_all([candidate, listing])
        await session.commit()

        async with httpx.AsyncClient() as client:
            await check_listing(session, listing, scraper=scraper, client=client, allow_fetch=True)

    assert scraper.fetch_listing_details.await_count == 2
    assert listing.description is not None
    assert listing.floor is not None
    assert candidate.status == ListingStatus.SOLD


@pytest.mark.asyncio
async def test_check_listing_resolves_duplicate_chain_root() -> None:
    source = "pytest-dedup-root-resolution"
    await _clean_source(source)
    old_time = datetime(2026, 1, 1, 10, 0, 0)
    mid_time = datetime(2026, 1, 2, 10, 0, 0)
    new_time = datetime(2026, 1, 3, 10, 0, 0)
    description = "Krásný byt 2+kk v Brně s balkonem, sklepem a výbornou dostupností do centra."

    async with get_session() as session:
        root = _listing(
            source=source,
            external_id="600",
            apartment_type="3+kk",
            first_seen_at=old_time,
        )
        candidate = _listing(
            source=source,
            external_id="601",
            description=description,
            first_seen_at=mid_time,
            duplicate_of_id=root.id,
        )
        listing = _listing(
            source=source,
            external_id="602",
            description=description,
            first_seen_at=new_time,
        )
        session.add_all([root, candidate, listing])
        await session.commit()

        match = await check_listing(session, listing, allow_fetch=False)

    assert match is not None
    assert listing.duplicate_of_id == root.id
    assert listing.duplicate_of_id != candidate.id
