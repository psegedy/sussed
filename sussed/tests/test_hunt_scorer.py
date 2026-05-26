from __future__ import annotations

from typing import Any

import pytest

from sussed.hunt.config import AgentConfig, ScoringWeights, SearchConfig, SearchCriteria
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

    assert result["score"] == 623
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

    assert reconstructed["score"] == 570
    assert "Po rekonstrukci (+70)" in reconstructed["highlights"]
    assert both["score"] == 620
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

    assert result["score"] == 580
    assert "Velmi dobrý stav (+80)" in result["highlights"]


@pytest.mark.asyncio
async def test_score_listing_rejects_panel_building_when_configured() -> None:
    config = SearchConfig(
        criteria=SearchCriteria(reject_panel_building=True),
    )

    result = await score_listing(
        config,
        _minimal_listing({"panel": True}),
        is_poa=True,
        poa_price_threshold=1,
    )

    assert result["score"] == -1
    assert result["reasons"] == ["Auto-rejected: panel building"]
    assert result["red_flags"] == ["🚫 Panel building (paneláky reject)"]


@pytest.mark.asyncio
async def test_score_listing_soft_penalizes_missing_required_elevator() -> None:
    config = SearchConfig(
        criteria=SearchCriteria(require_elevator=True),
    )

    result = await score_listing(
        config,
        _minimal_listing(),
        is_poa=True,
        poa_price_threshold=1,
    )

    assert result["score"] == 450
    assert "Missing elevator (required)" in result["red_flags"]


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

    assert result["score"] == 425
    assert "Panel building (-75)" in result["red_flags"]


def test_agent_config_enrich_top_n_default() -> None:
    config = AgentConfig()

    assert config.enrich_top_n == 5


@pytest.mark.asyncio
async def test_runner_uses_enrich_top_n_for_first_description_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    config = SearchConfig(
        output={"limit": 20},
        agent={
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
