from __future__ import annotations

import pytest
from sqlmodel import delete, select

from sussed.db.connection import close_db, get_session, init_db
from sussed.db.models import Listing, ListingStatus, ListingType, PriceHistory, PropertyCategory
from sussed.db.operations import apply_price_change


def _listing(source: str) -> Listing:
    return Listing(
        source=source,
        external_id="P1",
        url="https://example.test/p1",
        title="Prodej bytu 2+kk 55 m2",
        price_czk=5_000_000,
        price_per_m2=90_000,
        listing_type=ListingType.SALE,
        city="Brno",
        property_category=PropertyCategory.APARTMENT,
        features={},
        raw_labels=[],
        image_urls=[],
        status=ListingStatus.ACTIVE,
    )


@pytest.mark.asyncio
async def test_apply_price_change_records_history_on_change() -> None:
    await close_db()
    await init_db()
    source = "pytest-price-change"
    async with get_session() as session:
        # Clean up price_history first (FK constraint prevents deleting listings directly)
        listing_ids = (
            await session.execute(select(Listing.id).where(Listing.source == source))
        ).scalars().all()
        if listing_ids:
            await session.execute(
                delete(PriceHistory).where(PriceHistory.listing_id.in_(listing_ids))
            )
        await session.execute(delete(Listing).where(Listing.source == source))
        listing = _listing(source)
        session.add(listing)
        await session.flush()
        changed = await apply_price_change(session, listing, 4_500_000, 82_000)
        await session.commit()
        assert changed is True
        assert listing.price_czk == 4_500_000
        assert listing.last_price_change_at is not None
        rows = (
            await session.execute(
                select(PriceHistory).where(PriceHistory.listing_id == listing.id)
            )
        ).scalars().all()
        assert any(r.change_type == "decrease" and r.price_czk == 4_500_000 for r in rows)


@pytest.mark.asyncio
async def test_apply_price_change_noop_on_same_price() -> None:
    await close_db()
    await init_db()
    source = "pytest-price-same"
    async with get_session() as session:
        await session.execute(delete(Listing).where(Listing.source == source))
        listing = _listing(source)
        session.add(listing)
        await session.flush()
        changed = await apply_price_change(session, listing, 5_000_000, 90_000)
        assert changed is False
