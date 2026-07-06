from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from sussed.db.models import Listing, ListingStatus, ListingType, PropertyCategory, VibeCheck
from sussed.feed import builder
from sussed.feed.builder import build_feed_data, build_feed_post


def utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def make_listing(**overrides: object) -> Listing:
    now = utcnow_naive()
    data = {
        "id": uuid4(),
        "source": "sreality",
        "external_id": "123",
        "url": "https://example.com/123",
        "title": "Prodej bytu 2+kk 52 m²",
        "description": "Nice flat with balcony and parking available.",
        "price_czk": 5_800_000,
        "price_per_m2": 111_538,
        "listing_type": ListingType.SALE,
        "city": "Brno",
        "district": "Královo Pole",
        "address": "Example street",
        "property_category": PropertyCategory.APARTMENT,
        "apartment_type": "2+kk",
        "area_m2": Decimal("52"),
        "floor": 3,
        "total_floors": 6,
        "features": {"balcony": True, "parking": True},
        "raw_labels": ["Balkon", "Parkování"],
        "image_urls": ["https://example.com/1.jpg"],
        "image_count": 1,
        "has_floor_plan": True,
        "has_video": False,
        "has_3d_tour": False,
        "agency_name": "Example Reality",
        "status": ListingStatus.ACTIVE,
        "first_seen_at": now - timedelta(days=3),
        "last_seen_at": now,
        "last_price_change_at": None,
        "updated_at_source": now - timedelta(days=2),
        "created_at": now,
        "updated_at": now,
    }
    data.update(overrides)
    return Listing(**data)


def test_build_feed_post_reviewed_listing_uses_ai_review_fields() -> None:
    reviewed_at = utcnow_naive()
    listing = make_listing(
        ai_reviewed_at=reviewed_at,
        ai_score=842,
        ai_vibe=VibeCheck.PEAK,
        ai_summary="Strong listing with a parking caveat.",
        ai_analysis={
            "score": 100,
            "vibe": "sus",
            "confidence": 0.88,
            "recommendation": "CONSIDER",
            "highlights": ["Bright", "Good layout"],
            "red_flags": ["Parking extra"],
            "yellow_flags": ["Verify HOA fees"],
            "hidden_costs": {"parking": 450_000},
            "parking_price": 450_000,
            "parking_included": False,
            "usable_area_m2": 52.4,
        },
    )

    post = build_feed_post(listing, [])

    assert post.is_reviewed is True
    assert post.score == 842
    assert post.vibe == "peak"
    assert post.summary == "Strong listing with a parking caveat."
    assert post.pros == ["Bright", "Good layout"]
    assert post.cons_red == ["Parking extra"]
    assert post.cons_yellow == ["Verify HOA fees"]
    assert post.recommendation == "CONSIDER"
    assert post.confidence == 0.88
    assert post.hidden_costs == {"parking": 450_000}
    assert post.parking_price == 450_000
    assert post.parking_included is False
    assert post.usable_area_m2 == 52.4


def test_build_feed_post_hunt_only_uses_analysis_score_and_reason_summary() -> None:
    listing = make_listing(
        ai_reviewed_at=None,
        ai_score=None,
        ai_analysis={
            "score": 677,
            "reasons": ["Good price", "Has balcony"],
            "red_flags": ["Older building"],
            "highlights": ["Below market"],
            "is_poa": False,
            "scored_at": utcnow_naive().isoformat(),
        },
    )

    post = build_feed_post(listing, [])

    assert post.is_reviewed is False
    assert post.score == 677
    assert post.summary == "Good price; Has balcony"
    assert post.pros == ["Below market"]
    assert post.cons_red == ["Older building"]
    assert post.cons_yellow == []
    assert post.recommendation is None
    assert post.hidden_costs == {}


def test_build_feed_post_handles_missing_analysis_without_crashing() -> None:
    listing = make_listing(ai_score=None, ai_analysis=None, image_urls=None)

    post = build_feed_post(listing, [])

    assert post.score is None
    assert post.pros == []
    assert post.cons_red == []
    assert post.cons_yellow == []
    assert post.summary is None
    assert post.image_urls == []


def test_build_feed_post_marks_poa_listing() -> None:
    listing = make_listing(price_czk=1)

    post = build_feed_post(listing, [])

    assert post.is_poa is True


def test_build_feed_post_price_drop_history_has_signed_recent_decrease() -> None:
    now = utcnow_naive()
    listing = make_listing(price_czk=5_500_000, last_price_change_at=now)
    price_history: list[dict[str, Any]] = [
        {
            "price_czk": 5_500_000,
            "price_per_m2": 105_769,
            "change_type": "decrease",
            "change_amount": 500_000,
            "change_percent": 8.33,
            "recorded_at": now,
        },
        {
            "price_czk": 6_000_000,
            "price_per_m2": 115_385,
            "change_type": "initial",
            "change_amount": None,
            "change_percent": None,
            "recorded_at": now - timedelta(days=10),
        },
    ]

    post = build_feed_post(listing, price_history)

    assert post.change_direction == "decrease"
    assert post.last_change_amount == 500_000
    assert post.last_change_percent is not None
    assert post.last_change_percent < 0
    assert post.price_change_count == 1
    assert post.initial_price == 6_000_000


def test_build_feed_post_empty_price_history_has_empty_change_block() -> None:
    listing = make_listing()

    post = build_feed_post(listing, [])

    assert post.initial_price is None
    assert post.original_price is None
    assert post.dropped_to_poa is False
    assert post.change_direction is None
    assert post.last_change_amount is None
    assert post.last_change_percent is None
    assert post.price_change_count == 0


def test_build_feed_post_maps_sale_and_rent_listing_types() -> None:
    sale = build_feed_post(make_listing(listing_type=ListingType.SALE), [])
    rent = build_feed_post(make_listing(listing_type=ListingType.RENT), [])

    assert sale.listing_type == "sale"
    assert rent.listing_type == "rent"


@pytest.mark.asyncio
async def test_build_feed_data_dedupes_posts_and_counts_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared = make_listing(ai_score=900, ai_reviewed_at=utcnow_naive())
    ai_only = make_listing(ai_score=800, ai_reviewed_at=utcnow_naive(), external_id="ai")
    fresh_only = make_listing(
        ai_score=None,
        ai_analysis={"score": 700, "reasons": ["Fresh and decent"]},
        external_id="fresh",
    )
    calls: dict[str, Any] = {}

    async def fake_get_reviewed_picks(_session: object, **kwargs: Any) -> list[Listing]:
        calls["picks"] = kwargs
        return [shared, ai_only]

    async def fake_get_recent_scored_listings(_session: object, **kwargs: Any) -> list[Listing]:
        calls["fresh"] = kwargs
        return [shared, fresh_only]

    async def fake_get_price_histories_for_listings(
        _session: object, listing_ids: list[object]
    ) -> dict[str, list[dict[str, Any]]]:
        calls["history_ids"] = listing_ids
        return {str(shared.id): []}

    monkeypatch.setattr(builder, "get_reviewed_picks", fake_get_reviewed_picks)
    monkeypatch.setattr(builder, "get_recent_scored_listings", fake_get_recent_scored_listings)
    monkeypatch.setattr(
        builder, "get_price_histories_for_listings", fake_get_price_histories_for_listings
    )

    feed_data, context = await build_feed_data(
        object(),  # type: ignore[arg-type]
        title="Brno Feed",
        limit=10,
        fresh_days=7,
        district="Pole",
        min_score=650,
        property_type="apartment",
        include_unreviewed_in_picks=True,
    )

    shared_id = str(shared.id)
    assert len(feed_data.posts) == 3
    assert feed_data.ai_picks == [shared_id, str(ai_only.id)]
    assert feed_data.fresh == [shared_id, str(fresh_only.id)]
    assert shared_id in feed_data.posts
    assert calls["history_ids"] == [shared.id, ai_only.id, fresh_only.id]
    assert calls["picks"] == {
        "include_unreviewed": True,
        "district": "Pole",
        "min_score": 650,
        "property_type": "apartment",
        "limit": 10,
    }
    assert "max_age_days" not in calls["picks"]
    assert calls["fresh"] == {
        "max_age_days": 7,
        "limit": 10,
        "district": "Pole",
        "min_score": 650,
        "property_type": "apartment",
    }
    assert context.title == "Brno Feed"
    assert context.fresh_days == 7
    assert context.ai_picks_count == 2
    assert context.fresh_count == 2
    assert context.filters == {
        "district": "Pole",
        "min_score": 650,
        "property_type": "apartment",
        "limit": 10,
        "include_unreviewed_in_picks": True,
    }
    assert context.generated_at.tzinfo is None
