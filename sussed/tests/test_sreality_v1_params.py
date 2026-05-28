from __future__ import annotations

from typing import Any

import pytest

import sussed.db.operations as operations
import sussed.scrapers.sreality as sreality_module
from sussed.db.models import PropertyCategory
from sussed.models.sreality import SrealityV1Estate, SrealityV1SearchResponse
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
    category_sub: int = 4,
    category_sub_name: str = "2+kk",
    region_id: int = 14,
    district_id: int = 72,
) -> dict[str, Any]:
    return {
        "hash_id": hash_id,
        "advert_name": "Prodej bytu 2+kk 50 m²",
        "category_main_cb": {"name": "Byty", "value": category_main},
        "category_sub_cb": {"name": category_sub_name, "value": category_sub},
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
    category_sub: int = 4,
    category_sub_name: str = "2+kk",
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
                category_sub=category_sub,
                category_sub_name=category_sub_name,
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
async def test_fetch_page_includes_comma_separated_category_subcodes() -> None:
    scraper = SrealityScraper()
    scraper.rate_limit = 0
    client = _FakeClient(_search_payload(category_main=2, category_sub=33, category_sub_name="Chata"))

    await scraper.fetch_page(
        client,
        category_main=2,
        category_type=1,
        category_sub=(33, 43),
        locality_params=scraper._get_locality_params("brno"),
    )

    params = dict(client.requests[0]["params"])
    assert params["category_main_cb"] == 2
    assert params["category_sub_cb"] == "33,43"


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


@pytest.mark.asyncio
async def test_scrape_cottage_uses_house_main_and_cottage_subcodes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scraper = SrealityScraper()
    scraper.rate_limit = 0
    response = SrealityV1SearchResponse.model_validate(
        _search_payload(
            category_main=2,
            category_sub=33,
            category_sub_name="Chata",
            limit=1,
            total=1,
        )
    )
    calls: list[dict[str, Any]] = []

    async def fake_fetch_page(_client: Any, **kwargs: Any) -> SrealityV1SearchResponse | None:
        calls.append(kwargs)
        return response

    monkeypatch.setattr(scraper, "fetch_page", fake_fetch_page)

    estates = [
        estate
        async for estate in scraper.scrape(city="brno", property_type="cottage", max_pages=1)
    ]

    assert [estate.category_sub_cb.int_value for estate in estates] == [33]
    assert calls[0]["category_main"] == 2
    assert calls[0]["category_sub"] == (33, 43)


@pytest.mark.asyncio
async def test_scrape_garden_uses_land_main_and_garden_subcode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scraper = SrealityScraper()
    scraper.rate_limit = 0
    response = SrealityV1SearchResponse.model_validate(
        _search_payload(
            category_main=3,
            category_sub=23,
            category_sub_name="Zahrady",
            limit=1,
            total=1,
        )
    )
    calls: list[dict[str, Any]] = []

    async def fake_fetch_page(_client: Any, **kwargs: Any) -> SrealityV1SearchResponse | None:
        calls.append(kwargs)
        return response

    monkeypatch.setattr(scraper, "fetch_page", fake_fetch_page)

    estates = [estate async for estate in scraper.scrape(city="brno", property_type="garden", max_pages=1)]

    assert [estate.category_sub_cb.int_value for estate in estates] == [23]
    assert calls[0]["category_main"] == 3
    assert calls[0]["category_sub"] == (23,)


class _FakeSession:
    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, obj: object) -> None:
        self.added.append(obj)


@pytest.mark.asyncio
async def test_upsert_listing_maps_chata_subcode_to_cottage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get_listing_by_external_id(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(operations, "get_listing_by_external_id", fake_get_listing_by_external_id)
    estate = SrealityV1Estate.model_validate(
        _estate_payload(category_main=2, category_sub=33, category_sub_name="Chata")
    )

    listing, is_new, price_changed = await operations.upsert_listing_from_sreality(
        _FakeSession(),
        estate,
        city_override="Brno",
    )

    assert is_new is True
    assert price_changed is False
    assert listing.property_category == PropertyCategory.COTTAGE
    assert listing.apartment_type is None


@pytest.mark.asyncio
async def test_upsert_listing_maps_zahrada_subcode_to_garden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get_listing_by_external_id(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(operations, "get_listing_by_external_id", fake_get_listing_by_external_id)
    estate = SrealityV1Estate.model_validate(
        _estate_payload(category_main=3, category_sub=23, category_sub_name="Zahrady")
    )

    listing, is_new, price_changed = await operations.upsert_listing_from_sreality(
        _FakeSession(),
        estate,
        city_override="Brno",
    )

    assert is_new is True
    assert price_changed is False
    assert listing.property_category == PropertyCategory.GARDEN
    assert listing.apartment_type is None
