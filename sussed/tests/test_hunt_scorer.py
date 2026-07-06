from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from sqlmodel import delete

from sussed.db.connection import close_db, get_session, init_db
from sussed.db.models import Listing, ListingStatus, ListingType, PropertyCategory
from sussed.hunt.config import RunnerConfig, ScoringWeights, SearchConfig, SearchCriteria
from sussed.hunt.runner import AutonomousRunner
from sussed.hunt.scorer import score_listing


@pytest.mark.asyncio
async def test_score_listing_handles_none_district_and_address() -> None:
    config = SearchConfig(
        preferred_districts=["Královo Pole"],
        avoid_districts=["Cejl"],
    )
    listing = {
        "district": None,
        "address": None,
        "description": None,
        "raw_labels": [],
        "image_count": 5,
    }

    result = await score_listing(
        config,
        listing,
        is_poa=True,
        poa_price_threshold=1,
    )

    assert isinstance(result["score"], int)
    assert result["score"] >= 0


def _minimal_listing(features: dict[str, Any] | None = None) -> dict[str, Any]:
    """Create a listing with no incidental scoring noise."""
    return {
        "district": None,
        "address": None,
        "description": None,
        "raw_labels": [],
        "image_count": 5,
        "features": features or {},
    }


@pytest.mark.asyncio
async def test_cottage_with_electricity_and_water_scores_higher_than_without() -> None:
    config = SearchConfig(
        criteria=SearchCriteria(
            property_type="cottage",
            require_electricity=True,
            require_water=True,
        ),
        known_bad_locations=[],
    )

    with_utilities = await score_listing(
        config,
        _minimal_listing({"electricity": True, "water": True}),
        is_poa=True,
        poa_price_threshold=1,
    )
    without_utilities = await score_listing(
        config,
        _minimal_listing({"electricity": False, "water": False}),
        is_poa=True,
        poa_price_threshold=1,
    )

    assert with_utilities["score"] > without_utilities["score"]
    assert "Electricity available (+30)" in with_utilities["highlights"]
    assert "Water available (+30)" in with_utilities["highlights"]
    assert "Missing electricity (required) (-150)" in without_utilities["red_flags"]
    assert "Missing water (required) (-150)" in without_utilities["red_flags"]


@pytest.mark.asyncio
async def test_garden_with_fenced_and_personal_ownership_scores_higher() -> None:
    config = SearchConfig(
        criteria=SearchCriteria(property_type="garden"),
        known_bad_locations=[],
    )

    owned_fenced = await score_listing(
        config,
        _minimal_listing(
            {
                "fenced": True,
                "ownership": "Osobní vlastnictví",
            }
        ),
        is_poa=True,
        poa_price_threshold=1,
    )
    bare_plot = await score_listing(
        config,
        _minimal_listing(),
        is_poa=True,
        poa_price_threshold=1,
    )

    assert owned_fenced["score"] > bare_plot["score"]
    assert "Fenced plot (+30)" in owned_fenced["highlights"]
    assert "Osobní vlastnictví (+30)" in owned_fenced["highlights"]


@pytest.mark.asyncio
async def test_cottage_does_not_get_penalized_for_missing_elevator() -> None:
    config = SearchConfig(
        criteria=SearchCriteria(
            property_type="cottage",
            require_elevator=True,
        ),
        known_bad_locations=[],
    )

    result = await score_listing(
        config,
        _minimal_listing(),
        is_poa=True,
        poa_price_threshold=1,
    )

    assert result["score"] == 400
    assert not any("elevator" in flag.lower() for flag in result["red_flags"])


@pytest.mark.asyncio
async def test_garden_does_not_trigger_area_inflation_logic() -> None:
    config = SearchConfig(
        criteria=SearchCriteria(
            property_type="garden",
            max_price_per_m2=2_000,
        ),
        known_bad_locations=[],
    )
    listing = _minimal_listing()
    listing.update(
        {
            "description": "Zahrada 850 m² se skladem 12 m² a pergolou.",
            "area_m2": 850,
            "price_per_m2": 1_500,
        }
    )

    result = await score_listing(
        config,
        listing,
        is_poa=True,
        poa_price_threshold=1,
    )

    combined_notes = result["red_flags"] + result["reasons"]
    assert not any("inflated" in note.lower() for note in combined_notes)
    assert not any("usable" in note.lower() for note in combined_notes)


@pytest.mark.asyncio
async def test_runner_query_filters_cottage_property_category() -> None:
    await close_db()
    await init_db()
    unique_city = "Outdoor Query Test City"
    source = "pytest-hunt-cottage-query"
    cottage_external_id = "pytest-cottage-category"
    apartment_external_id = "pytest-apartment-category"

    async with get_session() as session:
        await session.execute(delete(Listing).where(Listing.source == source))
        session.add(
            Listing(
                source=source,
                external_id=cottage_external_id,
                url="https://example.test/cottage",
                title="Prodej chaty 45 m²",
                price_czk=1_500_000,
                listing_type=ListingType.SALE,
                city=unique_city,
                district="Test District",
                property_category=PropertyCategory.COTTAGE,
                area_m2=Decimal("45"),
                features={},
                raw_labels=[],
                image_urls=[],
                image_count=5,
                has_floor_plan=False,
                has_video=False,
                has_3d_tour=False,
                status=ListingStatus.ACTIVE,
            )
        )
        session.add(
            Listing(
                source=source,
                external_id=apartment_external_id,
                url="https://example.test/apartment",
                title="Prodej bytu 2+kk 45 m²",
                price_czk=4_000_000,
                listing_type=ListingType.SALE,
                city=unique_city,
                district="Test District",
                property_category=PropertyCategory.APARTMENT,
                apartment_type="2+kk",
                area_m2=Decimal("45"),
                features={},
                raw_labels=[],
                image_urls=[],
                image_count=5,
                has_floor_plan=False,
                has_video=False,
                has_3d_tour=False,
                status=ListingStatus.ACTIVE,
            )
        )

    try:
        config = SearchConfig(
            criteria=SearchCriteria(
                city=unique_city,
                property_type="cottage",
            ),
            runner=RunnerConfig(
                max_listings_to_process=10,
                skip_already_scored=False,
                use_llm=False,
            ),
        )

        results = await AutonomousRunner(config)._get_matching_listings()

        external_ids = {listing["external_id"] for listing in results}
        assert cottage_external_id in external_ids
        assert apartment_external_id not in external_ids
    finally:
        async with get_session() as session:
            await session.execute(delete(Listing).where(Listing.source == source))
        await close_db()


@pytest.mark.asyncio
async def test_score_listing_adds_new_building_bonus() -> None:
    config = SearchConfig(
        scoring=ScoringWeights(bonus_new_building=123),
    )

    result = await score_listing(
        config,
        _minimal_listing({"new_building": True}),
        is_poa=True,
        poa_price_threshold=1,
    )

    assert result["score"] == 523
    assert "Novostavba (+123)" in result["highlights"]


@pytest.mark.asyncio
async def test_score_listing_adds_reconstruction_bonus_and_new_building_takes_precedence() -> None:
    config = SearchConfig(
        scoring=ScoringWeights(bonus_new_building=120, bonus_reconstruction=70),
    )

    reconstructed = await score_listing(
        config,
        _minimal_listing({"reconstructed": True}),
        is_poa=True,
        poa_price_threshold=1,
    )
    both = await score_listing(
        config,
        _minimal_listing({"new_building": True, "reconstructed": True}),
        is_poa=True,
        poa_price_threshold=1,
    )

    assert reconstructed["score"] == 470
    assert "Po rekonstrukci (+70)" in reconstructed["highlights"]
    assert both["score"] == 520
    assert "Novostavba (+120)" in both["highlights"]
    assert "Po rekonstrukci (+70)" not in both["highlights"]


@pytest.mark.asyncio
async def test_score_listing_adds_very_good_condition_bonus() -> None:
    config = SearchConfig(
        scoring=ScoringWeights(bonus_very_good_condition=80),
    )

    result = await score_listing(
        config,
        _minimal_listing({"building_condition": "Velmi dobrý"}),
        is_poa=True,
        poa_price_threshold=1,
    )

    assert result["score"] == 480
    assert "Velmi dobrý stav (+80)" in result["highlights"]


@pytest.mark.asyncio
async def test_score_listing_penalizes_panel_building_when_reject_configured() -> None:
    """reject_panel_building no longer hard-rejects to -1 — it applies a -150
    penalty so the listing stays visible but ranked low."""
    config = SearchConfig(
        criteria=SearchCriteria(reject_panel_building=True),
    )

    result = await score_listing(
        config,
        _minimal_listing({"panel": True}),
        is_poa=True,
        poa_price_threshold=1,
    )

    # Minimal listing baseline 400 + REJECT_PENALTY -150 + default penalty_panel -100 = 150
    assert result["score"] == 150
    assert any("Panel building (paneláky reject)" in flag for flag in result["red_flags"])
    assert any("Panel building (-100)" in flag for flag in result["red_flags"])


@pytest.mark.asyncio
async def test_score_listing_soft_penalizes_missing_required_elevator() -> None:
    config = SearchConfig(
        criteria=SearchCriteria(require_elevator=True),
    )

    result = await score_listing(
        config,
        _minimal_listing({"elevator": False}),
        is_poa=True,
        poa_price_threshold=1,
    )

    assert result["score"] == 300
    assert "Missing elevator (required)" in result["red_flags"][0]


@pytest.mark.asyncio
async def test_score_listing_penalizes_panel_building_when_not_rejected() -> None:
    config = SearchConfig(
        criteria=SearchCriteria(reject_panel_building=False),
        scoring=ScoringWeights(penalty_panel=-75),
    )

    result = await score_listing(
        config,
        _minimal_listing({"panel": True}),
        is_poa=True,
        poa_price_threshold=1,
    )

    assert result["score"] == 325
    assert "Panel building (-75)" in result["red_flags"]


def test_runner_config_enrich_top_n_default() -> None:
    config = RunnerConfig()

    assert config.enrich_top_n == 5


@pytest.mark.asyncio
async def test_runner_uses_enrich_top_n_for_first_description_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    config = SearchConfig(
        output={"limit": 20},
        runner={
            "fetch_descriptions": True,
            "enrich_top_n": 2,
            "use_llm": False,
        },
    )
    runner = AutonomousRunner(config)
    listings = [
        {
            "id": f"listing-{i}",
            "external_id": str(i),
            "title": f"Listing {i}",
            "price_czk": 5_000_000,
            "score": 600 - i,
            "image_urls": [],
        }
        for i in range(4)
    ]
    fetched_ids: list[str] = []

    async def fake_get_matching_listings() -> list[dict[str, Any]]:
        return listings

    async def fake_process_listing(
        listing: dict[str, Any], *, fetch_description: bool
    ) -> dict[str, Any]:
        assert fetch_description is False
        return dict(listing)

    async def fake_fetch_description(
        listing_id: str,
        _external_id: str,
        **_kwargs: Any,
    ) -> tuple[str, str | None]:
        fetched_ids.append(listing_id)
        return "description", None

    async def fake_score_listing(
        listing: dict[str, Any], _is_poa: bool
    ) -> dict[str, Any]:
        return {"score": listing["score"], "red_flags": [], "highlights": []}

    async def noop_async(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(runner, "_get_matching_listings", fake_get_matching_listings)
    monkeypatch.setattr(runner, "_process_listing", fake_process_listing)
    monkeypatch.setattr(runner, "_fetch_description", fake_fetch_description)
    monkeypatch.setattr(runner, "_score_listing", fake_score_listing)
    monkeypatch.setattr(runner, "_save_score", noop_async)
    monkeypatch.setattr(runner, "_save_description", noop_async)
    monkeypatch.setattr(runner, "_prepare_output", lambda _processed: [])
    monkeypatch.setattr(runner, "_display_results", lambda _results: None)
    monkeypatch.setattr(runner, "_display_market_insights", noop_async)
    monkeypatch.setattr(runner, "_display_stats", lambda: None)
    monkeypatch.setattr("sussed.hunt.runner.asyncio.sleep", noop_async)

    await runner.run()

    assert fetched_ids == ["listing-0", "listing-1"]
