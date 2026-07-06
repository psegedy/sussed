from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from sussed.dedup.matcher import (
    DedupListing,
    haversine_m,
    image_overlap,
    image_path_token,
    normalize_text,
    score_pair,
    text_similarity,
)

if TYPE_CHECKING:
    from collections.abc import Callable


@pytest.fixture
def listing_factory() -> Callable[..., DedupListing]:
    """Build dedup listings with boring defaults and explicit differences."""

    def build(**overrides: object) -> DedupListing:
        data: dict[str, object] = {
            "external_id": "old-123",
            "source": "sreality",
            "listing_type": "sale",
            "property_category": "apartment",
            "apartment_type": "2+kk",
            "city": "Brno",
            "latitude": 49.1951,
            "longitude": 16.6068,
            "area_m2": 64.0,
            "floor": 6,
            "title": "Prodej bytu 2+kk 64 m² Brno střed",
            "description": (
                "Krásný světlý byt 2+kk po rekonstrukci s balkonem, sklepem a výbornou "
                "dostupností v centru Brna."
            ),
            "image_urls": (
                "https://d18-a.sdn.cz/d_18/c_img_x/ABC/fc37.jpeg?fl=res",
                "https://d18-a.sdn.cz/d_18/c_img_y/DEF/living.jpeg?fl=res",
            ),
            "new_building": False,
            "agency_name": "Peak Reality",
            "price_czk": 6_400_000,
            "status": "removed",
        }
        data.update(overrides)
        return DedupListing(**data)  # type: ignore[arg-type]

    return build


def test_haversine_returns_zero_for_identical_points() -> None:
    assert haversine_m(49.1951, 16.6068, 49.1951, 16.6068) == pytest.approx(0.0)


def test_haversine_calculates_known_brno_distance() -> None:
    distance = haversine_m(49.1951, 16.6068, 49.2051, 16.6068)

    assert distance == pytest.approx(1_112.0, abs=25.0)


def test_normalize_text_casefolds_punctuation_and_whitespace() -> None:
    assert normalize_text("  ŽLUTÝ, byt!!!\n2+KK\tBrno  ") == "žlutý byt 2 kk brno"


def test_text_similarity_handles_identical_missing_short_and_dissimilar_text() -> None:
    assert text_similarity("Krásný byt v Brně", "Krásný byt v Brně", min_len=5) == 1.0
    assert text_similarity(None, "Krásný byt v Brně", min_len=5) == 0.0
    assert text_similarity("byt", "byt", min_len=5) == 0.0
    assert text_similarity("slunný byt s balkonem", "garážové stání v Praze", min_len=5) < 0.4


def test_image_path_token_strips_query_scheme_and_host() -> None:
    url = "//d18-a.sdn.cz/d_18/c_img_x/ABC/fc37.jpeg?fl=res,400,300|watermark"

    assert image_path_token(url) == "/d_18/c_img_x/ABC/fc37.jpeg"


def test_image_overlap_reports_jaccard_and_shared_count() -> None:
    overlap, shared = image_overlap(
        [
            "https://d18-a.sdn.cz/d_18/c_img_x/ABC/fc37.jpeg?fl=small",
            "https://d18-a.sdn.cz/d_18/c_img_x/ABC/kitchen.jpeg",
        ],
        [
            "https://d18-b.sdn.cz/d_18/c_img_x/ABC/fc37.jpeg?fl=large",
            "https://d18-b.sdn.cz/d_18/c_img_x/ABC/bath.jpeg",
        ],
    )

    assert shared == 1
    assert overlap == pytest.approx(1 / 3)
    assert image_overlap(["https://example.com/a.jpg"], ["https://example.com/b.jpg"]) == (0.0, 0)


def test_true_relisting_scores_as_duplicate(listing_factory: Callable[..., DedupListing]) -> None:
    old = listing_factory()
    new = listing_factory(
        external_id="new-456",
        latitude=49.19518,
        longitude=16.60686,
        title="Prodej bytu 2+kk 64 m2 Brno střed",
        description=old.description,
        image_urls=(
            "https://d18-b.sdn.cz/d_18/c_img_x/ABC/fc37.jpeg?fl=res",
            "https://d18-b.sdn.cz/d_18/c_img_y/DEF/living.jpeg?fl=res",
        ),
        price_czk=5_950_000,
        status="active",
    )

    result = score_pair(old, new)

    assert result.status == "duplicate"
    assert result.is_duplicate is True
    assert result.confidence >= 0.85
    assert any("GPS" in reason for reason in result.reasons)
    assert any("price" in reason for reason in result.reasons)


def test_sale_and_rent_are_vetoed_even_when_otherwise_identical(
    listing_factory: Callable[..., DedupListing],
) -> None:
    sale = listing_factory()
    rent = listing_factory(external_id="rent-456", listing_type="rent", status="active")

    result = score_pair(sale, rent)

    assert result.status is None
    assert result.confidence == 0.0
    assert result.reasons == ["different listing type"]


def test_new_building_different_floor_is_vetoed(
    listing_factory: Callable[..., DedupListing],
) -> None:
    floor_two = listing_factory(new_building=True, floor=2)
    floor_five = listing_factory(external_id="new-456", new_building=True, floor=5)

    result = score_pair(floor_two, floor_five)

    assert result.status is None
    assert result.confidence == 0.0
    assert result.reasons == ["different floors in new building"]


def test_new_building_same_unit_without_strong_unit_evidence_is_capped_to_suspected(
    listing_factory: Callable[..., DedupListing],
) -> None:
    old = listing_factory(
        new_building=True,
        description=(
            "Novostavba 2+kk s balkonem, sklepem a moderním standardem v projektu u parku v Brně."
        ),
    )
    new = listing_factory(
        external_id="new-456",
        new_building=True,
        status="active",
        price_czk=6_200_000,
        description=(
            "Novostavba 2+kk s lodžií, sklepem a kvalitním standardem v projektu u tramvaje v Brně."
        ),
        image_urls=(
            "https://d18-b.sdn.cz/d_18/c_img_x/ABC/fc37.jpeg?fl=res",
            "https://d18-b.sdn.cz/d_18/c_img_y/DEF/living.jpeg?fl=res",
        ),
    )

    result = score_pair(old, new)

    assert result.status == "suspected"
    assert result.confidence >= 0.85
    assert any("new building cap" in reason for reason in result.reasons)


def test_apartments_with_large_area_difference_are_vetoed(
    listing_factory: Callable[..., DedupListing],
) -> None:
    sixty_four = listing_factory(area_m2=64.0)
    seventy_eight = listing_factory(external_id="new-456", area_m2=78.0, status="active")

    result = score_pair(sixty_four, seventy_eight)

    assert result.status is None
    assert result.confidence == 0.0
    assert result.reasons == ["apartment area differs by 21.9%"]


def test_far_apart_gps_is_vetoed(listing_factory: Callable[..., DedupListing]) -> None:
    center = listing_factory()
    far = listing_factory(external_id="new-456", latitude=49.1978, longitude=16.6068)

    result = score_pair(center, far)

    assert result.status is None
    assert result.confidence == 0.0
    assert result.reasons == ["GPS ~300m apart"]


def test_floor_mismatch_with_overwhelming_evidence_is_not_vetoed(
    listing_factory: Callable[..., DedupListing],
) -> None:
    old = listing_factory(floor=4)
    new = listing_factory(external_id="new-456", floor=5, status="active", price_czk=6_100_000)

    result = score_pair(old, new)

    assert result.status is not None
    assert any("floor mismatch" in reason for reason in result.reasons)


def test_house_area_difference_is_scoring_signal_not_veto(
    listing_factory: Callable[..., DedupListing],
) -> None:
    house_a = listing_factory(
        property_category="house",
        apartment_type=None,
        area_m2=120.0,
        floor=None,
        title="Prodej domu 120 m² Brno",
        description="Rodinný dům se zahradou v klidné části Brna.",
        image_urls=(),
    )
    house_b = listing_factory(
        external_id="new-456",
        property_category="house",
        apartment_type=None,
        area_m2=180.0,
        floor=None,
        title="Prodej domu 180 m² Brno",
        description="Rodinný dům se zahradou v klidné části Brna.",
        image_urls=(),
        status="active",
    )

    result = score_pair(house_a, house_b)

    assert result.status != "duplicate"
    assert result.confidence > 0.0
    assert not any("veto" in reason.lower() for reason in result.reasons)
    assert not any("area differs" in reason for reason in result.reasons)


def test_missing_floor_and_description_do_not_crash_or_overclaim(
    listing_factory: Callable[..., DedupListing],
) -> None:
    enriched = listing_factory()
    fresh = listing_factory(
        external_id="new-456",
        floor=None,
        description=None,
        image_urls=(),
        status="active",
        price_czk=6_250_000,
    )

    result = score_pair(enriched, fresh)

    assert result.status in {None, "suspected"}
    assert result.confidence < 0.85


def test_score_pair_confidence_is_symmetric(listing_factory: Callable[..., DedupListing]) -> None:
    old = listing_factory()
    new = listing_factory(external_id="new-456", status="active", price_czk=6_100_000)

    assert score_pair(old, new) == score_pair(new, old)
