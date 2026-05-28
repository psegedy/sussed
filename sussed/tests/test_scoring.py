from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

import pytest

from sussed.hunt.config import ScoringWeights, SearchConfig, SearchCriteria
from sussed.hunt.scorer import score_listing

PREFERRED_DISTRICTS = ["Žabovřesky", "Veveří", "Sadová", "Královo Pole", "Černá Pole"]


@pytest.fixture
def search_config() -> SearchConfig:
    """Create an isolated config for style-vs-location scoring tests."""
    return SearchConfig(
        criteria=SearchCriteria(require_parking=True),
        scoring=ScoringWeights(
            bonus_new_building=300,
            bonus_reconstruction=150,
            bonus_very_good_condition=120,
            penalty_no_parking=-200,
            penalty_panel=-100,
        ),
        preferred_districts=PREFERRED_DISTRICTS,
        known_bad_locations=[],
    )


@pytest.fixture
def listing() -> Callable[[str, dict[str, Any]], dict[str, Any]]:
    """Create a 2+kk listing with no incidental scoring noise."""

    def build(district: str, features: dict[str, Any]) -> dict[str, Any]:
        return {
            "apartment_type": "2+kk",
            "district": district,
            "address": None,
            "description": None,
            "raw_labels": [],
            "image_count": 5,
            "features": {**features, "building_condition": None},
        }

    return build


@pytest.mark.asyncio
async def test_style_trumps_location_for_new_building_with_parking(
    search_config: SearchConfig,
    listing: Callable[[str, dict[str, Any]], dict[str, Any]],
) -> None:
    listing_a = listing(
        "Líšeň",
        {
            "new_building": True,
            "parking": True,
            "elevator": True,
            "balcony": True,
        },
    )
    listing_b = listing(
        "Žabovřesky",
        {
            "new_building": False,
            "parking": False,
        },
    )

    score_a = await score_listing(search_config, listing_a, is_poa=True, poa_price_threshold=1)
    score_b = await score_listing(search_config, listing_b, is_poa=True, poa_price_threshold=1)

    assert score_a["score"] > score_b["score"]


@pytest.mark.asyncio
async def test_required_parking_matters_more_than_top_preferred_district(
    search_config: SearchConfig,
    listing: Callable[[str, dict[str, Any]], dict[str, Any]],
) -> None:
    neutral_with_parking = listing("Líšeň", {"parking": True})
    preferred_without_parking = listing("Žabovřesky", {"parking": False})

    score_a = await score_listing(
        search_config,
        neutral_with_parking,
        is_poa=True,
        poa_price_threshold=1,
    )
    score_b = await score_listing(
        search_config,
        preferred_without_parking,
        is_poa=True,
        poa_price_threshold=1,
    )

    assert score_a["score"] == 475
    assert score_b["score"] == 225
    assert score_a["score"] > score_b["score"]


@pytest.mark.asyncio
async def test_preferred_district_bonus_uses_half_strength_ladder(
    search_config: SearchConfig,
    listing: Callable[[str, dict[str, Any]], dict[str, Any]],
) -> None:
    search_config.criteria.require_parking = False

    results = [
        await score_listing(
            search_config,
            listing(district, {}),
            is_poa=True,
            poa_price_threshold=1,
        )
        for district in PREFERRED_DISTRICTS
    ]

    assert [result["score"] for result in results] == [425, 420, 415, 410, 405]


def _sort_for_hunt(
    listings: list[dict[str, Any]], config: SearchConfig
) -> list[dict[str, Any]]:
    """Sort listings the same way the hunt runner does."""
    from sussed.hunt.runner import sort_key

    return sorted(listings, key=lambda item: sort_key(item, config), reverse=True)


def test_sort_tiebreaker_prefers_new_build_over_preferred_district(
    search_config: SearchConfig,
    listing: Callable[[str, dict[str, Any]], dict[str, Any]],
) -> None:
    """New build beats preferred district when scores are capped and tied."""
    new_build = {**listing("Líšeň", {"new_building": True}), "score": 1000}
    preferred_old = {**listing("Žabovřesky", {}), "score": 1000}

    sorted_listings = _sort_for_hunt([preferred_old, new_build], search_config)

    assert sorted_listings == [new_build, preferred_old]


def test_sort_tiebreaker_uses_parking_when_style_matches(
    search_config: SearchConfig,
    listing: Callable[[str, dict[str, Any]], dict[str, Any]],
) -> None:
    """Parking breaks ties after score, new-build, condition, and district match."""
    with_parking = {
        **listing("Líšeň", {"new_building": True, "parking": True}),
        "score": 1000,
    }
    without_parking = {**listing("Líšeň", {"new_building": True}), "score": 1000}

    sorted_listings = _sort_for_hunt([without_parking, with_parking], search_config)

    assert sorted_listings == [with_parking, without_parking]


def test_sort_tiebreaker_ranks_preferred_districts_in_config_order(
    search_config: SearchConfig,
    listing: Callable[[str, dict[str, Any]], dict[str, Any]],
) -> None:
    """Earlier preferred districts win over later and non-preferred districts."""
    first_preferred = {**listing("Žabovřesky", {}), "score": 1000}
    second_preferred = {**listing("Veveří", {}), "score": 1000}
    non_preferred = {**listing("Líšeň", {}), "score": 1000}

    sorted_listings = _sort_for_hunt(
        [non_preferred, second_preferred, first_preferred], search_config
    )

    assert sorted_listings == [first_preferred, second_preferred, non_preferred]


def test_sort_tiebreaker_keeps_score_as_primary_order(
    search_config: SearchConfig,
    listing: Callable[[str, dict[str, Any]], dict[str, Any]],
) -> None:
    """A genuinely higher score wins regardless of quality tiebreaker fields."""
    same_features = {"new_building": True, "parking": True}
    lower_score_with_every_bonus = {
        **listing("Žabovřesky", same_features),
        "score": 500,
        "image_count": 99,
        "price_per_m2": 1,
    }
    higher_score = {**listing("Líšeň", same_features), "score": 700}

    sorted_listings = _sort_for_hunt(
        [lower_score_with_every_bonus, higher_score], search_config
    )

    assert sorted_listings == [higher_score, lower_score_with_every_bonus]


@pytest.mark.asyncio
async def test_avoid_district_penalty_drops_below_score_cap(
    listing: Callable[[str, dict[str, Any]], dict[str, Any]],
) -> None:
    """Avoid-district penalty must be large enough to drop a capped listing below 1000."""
    config = SearchConfig(
        criteria=SearchCriteria(require_parking=False),
        scoring=ScoringWeights(bonus_new_building=300),
        preferred_districts=[],
        avoid_districts=["Žebětín"],
        known_bad_locations=[],
    )

    avoided = listing("Žebětín", {"new_building": True, "parking": True, "elevator": True})
    avoided["area_m2"] = 80
    avoided["has_floor_plan"] = True

    result = await score_listing(config, avoided, is_poa=False, poa_price_threshold=10)

    assert result["score"] < 1000, "Avoid penalty must demote a capped listing below the cap"
    assert any("Žebětín" in flag for flag in result["red_flags"])


@pytest.mark.asyncio
async def test_known_bad_location_penalty_drops_below_score_cap(
    listing: Callable[[str, dict[str, Any]], dict[str, Any]],
) -> None:
    """Known-bad-location penalty must also drop a capped listing below 1000."""
    config = SearchConfig(
        criteria=SearchCriteria(require_parking=False),
        scoring=ScoringWeights(bonus_new_building=300),
        preferred_districts=[],
        avoid_districts=[],
        known_bad_locations=["Cejl"],
    )

    bad = listing("Zábrdovice", {"new_building": True, "parking": True, "elevator": True})
    bad["address"] = "Cejl 12"
    bad["area_m2"] = 80
    bad["has_floor_plan"] = True

    result = await score_listing(config, bad, is_poa=False, poa_price_threshold=10)

    assert result["score"] < 1000
    assert any("Cejl" in flag for flag in result["red_flags"])
