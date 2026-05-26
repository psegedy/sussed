from __future__ import annotations

import json
from pathlib import Path

from sussed.db.models import Listing, ListingType, PropertyCategory
from sussed.models.sreality import SrealityV1DetailResponse, SrealityV1SearchResponse
from sussed.scrapers.sreality import (
    _build_listing_url,
    normalize_sreality_image_url,
    set_features_from_v1_detail,
)

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_v1_search_response_parses_real_sample() -> None:
    response = SrealityV1SearchResponse.model_validate(
        load_fixture("sreality_v1_search_sample.json")
    )

    assert response.status_code == 200
    assert len(response.results) == 22
    assert response.pagination.total > 0
    assert response.pagination.limit == 22
    assert response.pagination.total_pages == (
        response.pagination.total + response.pagination.limit - 1
    ) // response.pagination.limit


def test_v1_pagination_total_pages_uses_effective_server_limit() -> None:
    response = SrealityV1SearchResponse.model_validate(
        {
            "status_code": 200,
            "pagination": {"limit": 100, "offset": 0, "total": 422},
            "results": [],
        }
    )

    assert response.pagination.total_pages == 5


def test_v1_detail_response_parses_real_sample_features_and_dates() -> None:
    response = SrealityV1DetailResponse.model_validate(
        load_fixture("sreality_v1_detail_sample.json")
    )
    detail = response.result

    assert detail.hash_id == 4162929484
    assert detail.garage is True
    assert detail.cellar is True
    assert detail.terrace is True
    assert detail.since == "2026-03-28"


def test_v1_detail_feature_population_preserves_condition_and_type_names() -> None:
    response = SrealityV1DetailResponse.model_validate(
        load_fixture("sreality_v1_detail_sample.json")
    )
    listing = Listing(
        source="sreality",
        external_id="4162929484",
        url="https://example.com/listing",
        title="Test listing",
        price_czk=1,
        listing_type=ListingType.SALE,
        city="Brno",
        property_category=PropertyCategory.APARTMENT,
    )

    set_features_from_v1_detail(listing, response.result)

    assert listing.features is not None
    assert listing.features["building_condition"] == "Velmi dobrý"
    assert listing.features["building_type"] == "Cihlová"
    assert listing.features["brick"] is True


def test_v1_url_builder_uses_sreality_detail_slug() -> None:
    response = SrealityV1DetailResponse.model_validate(
        load_fixture("sreality_v1_detail_sample.json")
    )

    assert _build_listing_url(response.result).endswith(
        "/detail/prodej/byt/4+kk/brno-zebetin-prirodni/4162929484"
    )


def test_v1_image_url_normalizer_adds_https_and_download_transform() -> None:
    image_url = "//d18-a.sdn.cz/d_18/c_img_qA_A/example/9115.jpeg"

    normalized = normalize_sreality_image_url(image_url)

    assert normalized == (
        "https://d18-a.sdn.cz/d_18/c_img_qA_A/example/9115.jpeg"
        "?fl=res,1200,1200,1|shr,,20|jpg,80"
    )


def test_v1_image_url_normalizer_rejects_non_sreality_cdn_hosts() -> None:
    image_url = "//evil.example/sdn.cz/exploit.jpeg"

    assert normalize_sreality_image_url(image_url) is None


def test_v1_image_url_normalizer_rejects_unexpected_sdn_subdomains() -> None:
    image_url = "//evil.com.sdn.cz/exploit.jpeg"

    assert normalize_sreality_image_url(image_url) is None
