"""Pydantic models for AI listing review payloads."""

import re
from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import UUID  # noqa: TC003

from pydantic import BaseModel, ConfigDict, Field, field_validator

URL_PATTERN = re.compile(r"https?://\S+")


class ReviewVibe(str, Enum):
    """Human-readable Pydantic input labels mapped to DB `VibeCheck` during persistence."""

    PEAK = "peak"
    VALID = "valid"
    MID = "mid"
    SUS = "sus"


class PreparedListingReview(BaseModel):
    """Listing payload prepared for a coding-agent skill to review."""

    model_config = ConfigDict(extra="forbid")

    listing_id: UUID
    external_id: str
    source: str
    title: str
    url: str
    price_czk: int
    price_per_m2: int | None = None
    initial_price: int | None = Field(
        default=None,
        description="First price ever recorded for this listing (None if no history).",
    )
    original_price: int | None = Field(
        default=None,
        description="Last non-POA price before listing went POA. Set only when current price is POA.",
    )
    price_dropped_to_poa: bool = Field(
        default=False,
        description="True if listing currently is POA (≤10 CZK) and had a prior real price.",
    )
    property_category: str | None = Field(
        default=None,
        description=(
            "Property kind from the DB enum: 'apartment', 'house', 'cottage', "
            "'garden', etc. Use this to dispatch to the right AI review skill "
            "(sussed-ai-review / sussed-cottage-review / sussed-garden-review)."
        ),
    )
    listing_type: str | None = None
    city: str
    district: str | None = None
    address: str | None = None
    apartment_type: str | None = None
    area_m2: float | None = None
    floor: int | None = None
    total_floors: int | None = None
    description: str | None = None
    detail_items: list[dict[str, Any]] = Field(default_factory=list)
    features: dict[str, Any] = Field(default_factory=dict)
    raw_labels: list[str] = Field(default_factory=list)
    image_urls: list[str] = Field(default_factory=list)
    image_paths: list[str] = Field(default_factory=list)
    image_count: int = 0
    has_floor_plan: bool = False
    has_video: bool = False
    has_3d_tour: bool = False
    price_history: list[dict[str, Any]] = Field(default_factory=list)
    current_ai_score: int | None = None
    current_ai_reviewed_at: datetime | None = None
    heuristic_notes: list[str] = Field(default_factory=list)
    input_hash: str


class ReviewResultInput(BaseModel):
    """Structured AI review result accepted by `sussed review save`."""

    model_config = ConfigDict(extra="forbid")

    score: int = Field(description="0-1000, 9999 for unicorn, -1 for scam/sus")
    vibe: ReviewVibe
    confidence: float = Field(ge=0.0, le=1.0)
    recommendation: str = Field(min_length=1, max_length=40)
    score_reason: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    red_flags: list[str] = Field(default_factory=list)
    yellow_flags: list[str] = Field(default_factory=list)
    highlights: list[str] = Field(default_factory=list)
    hidden_costs: dict[str, int | None] = Field(default_factory=dict)
    parking_price: int | None = Field(default=None, ge=0)
    parking_included: bool | None = None
    usable_area_m2: float | None = Field(default=None, gt=0)
    photo_observations: list[str] = Field(default_factory=list)
    reviewer_name: str = Field(min_length=1)
    reviewer_model: str | None = None
    reviewer_session: str | None = None
    input_hash: str = Field(min_length=1)
    reviewed_at: datetime = Field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None))
    raw_review: dict[str, Any] | None = None

    @field_validator("score")
    @classmethod
    def validate_score(cls, value: int) -> int:
        """Allow normal scores and the two project-specific special values."""
        if value in (-1, 9999):
            return value
        if 0 <= value <= 1000:
            return value
        raise ValueError("score must be -1, 0-1000, or 9999")

    @field_validator("score_reason")
    @classmethod
    def validate_score_reason_has_url(cls, value: str) -> str:
        """Require the listing URL inside ``score_reason``.

        Every review skill mandates the listing URL inside ``score_reason``
        (typically at the end in square brackets) so downstream readers can
        always jump back to the source. Reject payloads that lack it instead
        of silently storing rootless reviews.
        """
        if not URL_PATTERN.search(value):
            raise ValueError(
                "score_reason must include the listing URL (e.g. end with "
                "'[https://www.sreality.cz/detail/...]')"
            )
        return value
