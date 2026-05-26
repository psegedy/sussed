from datetime import datetime
from uuid import uuid4

import pytest
from annotated_types import Ge, Le
from pydantic import ValidationError
from sqlalchemy import CheckConstraint

from sussed.db.models import ListingReview
from sussed.review.models import ReviewResultInput, ReviewVibe


def valid_review_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "score": 650,
        "vibe": ReviewVibe.VALID,
        "confidence": 0.75,
        "recommendation": "CONSIDER",
        "score_reason": "A solid listing with a reasonable price for the district.",
        "summary": "Worth a closer look.",
        "reviewer_name": "sussed-ai-review",
        "input_hash": "abc123",
    }
    payload.update(overrides)
    return payload


def test_review_result_accepts_full_structured_payload() -> None:
    payload = ReviewResultInput(
        score=842,
        vibe=ReviewVibe.PEAK,
        confidence=0.88,
        recommendation="CONSIDER",
        score_reason="Below-market 2+kk with good light, but parking costs extra.",
        summary="Strong listing with a parking-price caveat.",
        red_flags=["Parking not included"],
        yellow_flags=["Verify HOA fees"],
        highlights=["Good floor plan", "Bright interior"],
        hidden_costs={"parking": 450_000},
        parking_price=450_000,
        parking_included=False,
        usable_area_m2=52.4,
        photo_observations=[
            "Photos match the described reconstruction.",
            "Kitchen looks smaller than description implies.",
        ],
        reviewer_name="sussed-ai-review",
        reviewer_model="copilot-cli",
        input_hash="abc123",
        reviewed_at=datetime(2026, 5, 26, 12, 0, 0),
    )

    assert payload.score == 842
    assert payload.vibe == ReviewVibe.PEAK
    assert payload.hidden_costs["parking"] == 450_000


@pytest.mark.parametrize("score", [-2, 1001, 9998])
def test_review_result_rejects_invalid_scores(score: int) -> None:
    with pytest.raises(ValidationError):
        ReviewResultInput(
            score=score,
            vibe=ReviewVibe.MID,
            confidence=0.5,
            recommendation="SKIP",
            score_reason="Invalid score should fail.",
            summary="Invalid.",
            reviewer_name="sussed-ai-review",
            input_hash="abc123",
        )


def test_review_result_accepts_special_scores() -> None:
    sus = ReviewResultInput(
        score=-1,
        vibe=ReviewVibe.SUS,
        confidence=0.9,
        recommendation="AVOID",
        score_reason="Likely scam.",
        summary="Avoid.",
        reviewer_name="sussed-ai-review",
        input_hash="abc123",
    )
    gem = ReviewResultInput(
        score=9999,
        vibe=ReviewVibe.PEAK,
        confidence=0.95,
        recommendation="BUY",
        score_reason="Exceptional deal.",
        summary="Unicorn.",
        reviewer_name="sussed-ai-review",
        input_hash="def456",
    )

    assert sus.score == -1
    assert gem.score == 9999


@pytest.mark.parametrize("confidence", [-0.1, 1.5])
def test_review_result_rejects_invalid_confidence(confidence: float) -> None:
    with pytest.raises(ValidationError):
        ReviewResultInput(**valid_review_payload(confidence=confidence))


@pytest.mark.parametrize("field_name", ["score_reason", "summary", "reviewer_name"])
def test_review_result_rejects_empty_required_strings(field_name: str) -> None:
    with pytest.raises(ValidationError):
        ReviewResultInput(**valid_review_payload(**{field_name: ""}))


def test_review_result_rejects_too_long_recommendation() -> None:
    with pytest.raises(ValidationError):
        ReviewResultInput(**valid_review_payload(recommendation="X" * 41))


@pytest.mark.parametrize("usable_area_m2", [0, -10])
def test_review_result_rejects_invalid_usable_area(usable_area_m2: int) -> None:
    with pytest.raises(ValidationError):
        ReviewResultInput(**valid_review_payload(usable_area_m2=usable_area_m2))


def test_listing_review_confidence_declares_review_input_bounds() -> None:
    metadata = ListingReview.model_fields["confidence"].metadata

    assert any(isinstance(item, Ge) and item.ge == 0 for item in metadata)
    assert any(isinstance(item, Le) and item.le == 1 for item in metadata)


def listing_review_check_constraints() -> dict[str, CheckConstraint]:
    return {
        constraint.name: constraint
        for constraint in ListingReview.__table__.constraints
        if isinstance(constraint, CheckConstraint) and constraint.name is not None
    }


@pytest.mark.parametrize(
    ("constraint_name", "expected_sql"),
    [
        (
            "ck_listing_reviews_score_valid",
            "score = -1 OR score BETWEEN 0 AND 1000 OR score = 9999",
        ),
        (
            "ck_listing_reviews_confidence_range",
            "confidence IS NULL OR confidence BETWEEN 0 AND 1",
        ),
        (
            "ck_listing_reviews_parking_price_non_negative",
            "parking_price IS NULL OR parking_price >= 0",
        ),
        (
            "ck_listing_reviews_usable_area_positive",
            "usable_area_m2 IS NULL OR usable_area_m2 > 0",
        ),
    ],
)
def test_listing_review_table_declares_named_check_constraints(
    constraint_name: str, expected_sql: str
) -> None:
    constraints = listing_review_check_constraints()

    assert constraint_name in constraints
    assert " ".join(str(constraints[constraint_name].sqltext).split()) == expected_sql


@pytest.mark.parametrize("column_name", ["score_reason", "summary"])
def test_listing_review_required_text_columns_are_non_nullable(column_name: str) -> None:
    assert not ListingReview.__table__.columns[column_name].nullable


def test_prepared_listing_payload_has_stable_ids() -> None:
    from sussed.review.models import PreparedListingReview

    listing_id = uuid4()
    payload = PreparedListingReview(
        listing_id=listing_id,
        external_id="123456",
        source="sreality",
        title="Prodej bytu 2+kk 52 m2",
        url="https://www.sreality.cz/detail/prodej/byt/2+kk/brno/123456",
        price_czk=5_800_000,
        city="Brno",
        image_paths=[],
        input_hash="hash",
    )

    assert payload.listing_id == listing_id
    assert payload.external_id == "123456"
