from __future__ import annotations

from typing import Any

import pytest

import sussed.scrapers.sreality as sreality_module
from sussed.models.sreality import SrealityV1SearchResponse
from sussed.scrapers.sreality import SrealityScraper


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeClient:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.requests: list[dict[str, Any]] = []

    async def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.requests.append({"url": url, **kwargs})
        return _FakeResponse(self.payload)


def _estate_payload(
    hash_id: int = 123456789,
    *,
    category_main: int = 1,
    region_id: int = 14,
    district_id: int = 72,
) -> dict[str, Any]:
    return {
        "hash_id": hash_id,
        "advert_name": "Prodej bytu 2+kk 50 m²",
        "category_main_cb": {"name": "Byty", "value": category_main},
        "category_sub_cb": {"name": "2+kk", "value": 4},
        "category_type_cb": {"name": "Prodej", "value": 1},
        "locality": {
            "city": "Brno",
            "region_id": region_id,
            "district_id": district_id,
        },
    }


def _search_payload(
    *,
    category_main: int = 1,
    region_id: int = 14,
    district_id: int = 72,
    limit: int = 60,
    offset: int = 0,
    total: int = 1175,
    hash_id: int = 123456789,
) -> dict[str, Any]:
    return {
        "status_code": 200,
        "pagination": {"limit": limit, "offset": offset, "total": total},
        "results": [
            _estate_payload(
                hash_id,
                category_main=category_main,
                region_id=region_id,
                district_id=district_id,
            )
        ],
    }


@pytest.mark.asyncio
async def test_fetch_page_uses_limit_and_offset_v1_params_for_brno_search() -> None:
    scraper = SrealityScraper()
    scraper.rate_limit = 0
    client = _FakeClient(_search_payload())

    await scraper.fetch_page(
        client,
        offset=100,
        limit=60,
        category_main=1,
        category_type=1,
        locality_params=scraper._get_locality_params("brno"),
        advert_age_to=8,
    )

    params = dict(client.requests[0]["params"])
    assert params == {
        "category_main_cb": 1,
        "category_type_cb": 1,
        "locality_country_id": 112,
        "locality_region_id": 14,
        "locality_district_id": 72,
        "limit": 60,
        "offset": 100,
        "advert_age_to": 8,
        "lang": "cs",
    }
    assert "page" not in params
    assert "per_page" not in params
    assert all("[]" not in key for key in params)
    assert "imageSort" not in params
    assert "sort" not in params


@pytest.mark.asyncio
async def test_fetch_page_does_not_retry_or_warn_about_legacy_filter_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scraper = SrealityScraper()
    scraper.rate_limit = 0
    client = _FakeClient(_search_payload(category_main=4, region_id=1, district_id=7))
    warnings: list[str] = []

    def fake_warning(message: object, *_args: object, **_kwargs: object) -> None:
        warnings.append(str(message))

    monkeypatch.setattr(sreality_module.logger, "warning", fake_warning)

    await scraper.fetch_page(
        client,
        locality_params=scraper._get_locality_params("brno"),
    )

    assert len(client.requests) == 1
    assert not any("camelCase" in warning for warning in warnings)
    assert not any("legacy snake_case" in warning for warning in warnings)


@pytest.mark.asyncio
async def test_scrape_uses_effective_limit_offsets_without_distinct_dedup_noise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scraper = SrealityScraper()
    scraper.rate_limit = 0
    responses = {
        0: SrealityV1SearchResponse.model_validate(
            _search_payload(limit=1, offset=0, total=2, hash_id=111)
        ),
        1: SrealityV1SearchResponse.model_validate(
            _search_payload(limit=1, offset=1, total=2, hash_id=222)
        ),
    }
    requested_offsets: list[int] = []
    debug_messages: list[str] = []

    async def fake_fetch_page(
        _client: Any,
        *,
        offset: int = 0,
        **_kwargs: Any,
    ) -> SrealityV1SearchResponse | None:
        requested_offsets.append(offset)
        return responses[offset]

    def fake_debug(message: object, *_args: object, **_kwargs: object) -> None:
        debug_messages.append(str(message))

    monkeypatch.setattr(scraper, "fetch_page", fake_fetch_page)
    monkeypatch.setattr(sreality_module.logger, "debug", fake_debug)

    estates = [estate async for estate in scraper.scrape(city="brno", max_pages=2)]

    assert [estate.hash_id for estate in estates] == [111, 222]
    assert requested_offsets == [0, 1]
    assert not any("Deduplicated" in message for message in debug_messages)
