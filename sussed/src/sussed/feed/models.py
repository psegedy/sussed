"""Pydantic models describing the JSON payload embedded in the static feed.

These models are output-only: they are serialized to JSON with
``model_dump(mode="json")`` and injected into the generated HTML. The client-side
JavaScript reads them back and renders each :class:`FeedPost` as one Instagram-style
post. The payload is normalized (a ``posts`` map keyed by id plus ordered id lists
per tab) so a listing appearing in both tabs is only serialized once.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class FeedPost(BaseModel):
    """One listing rendered as a single feed post.

    Fields mirror the display needs of the frontend: identity/link, location,
    price with a change block, honest listing dates, media for the carousel, and
    the AI/heuristic review block (score, summary, pros/cons). ``score`` is the
    *effective* score — the full AI review score when the listing has been
    reviewed, otherwise the cheap hunt quick-score — with ``is_reviewed``
    distinguishing the two so the UI never overstates confidence.
    """

    model_config = ConfigDict(extra="forbid")

    # Identity & link
    id: str
    external_id: str
    source: str
    url: str
    title: str

    # Classification
    listing_type: str | None = None
    property_category: str | None = None
    apartment_type: str | None = None
    area_m2: float | None = None
    floor: int | None = None
    total_floors: int | None = None

    # Location
    city: str
    district: str | None = None
    address: str | None = None

    # Price & change block
    price_czk: int
    price_per_m2: int | None = None
    is_poa: bool = False
    initial_price: int | None = Field(
        default=None, description="First price ever recorded (None if no history)."
    )
    original_price: int | None = Field(
        default=None, description="Last non-POA price before going POA; set only when now POA."
    )
    last_change_amount: int | None = Field(
        default=None, description="Absolute CZK amount of the most recent price change."
    )
    last_change_percent: float | None = Field(
        default=None, description="Signed percentage of the most recent price change."
    )
    change_direction: str | None = Field(
        default=None, description="'increase' or 'decrease' for the most recent change."
    )
    dropped_to_poa: bool = Field(
        default=False, description="Current price is POA and a prior real price existed."
    )
    price_change_count: int = 0

    # Honest dates — sreality exposes no original publish date, so we never
    # fabricate one. ``first_seen_at`` is when we scraped it; ``source_updated_at``
    # is sreality's "Aktualizace" (last modified) when known.
    first_seen_at: datetime | None = None
    source_updated_at: datetime | None = None
    last_price_change_at: datetime | None = None
    ai_reviewed_at: datetime | None = None

    # Media
    image_urls: list[str] = Field(default_factory=list)
    image_count: int = 0
    has_floor_plan: bool = False
    has_video: bool = False
    has_3d_tour: bool = False

    # Review block
    score: int | None = Field(
        default=None, description="Effective score: AI review score if reviewed, else hunt score."
    )
    is_reviewed: bool = Field(
        default=False, description="True when a full AI review exists (ai_reviewed_at set)."
    )
    vibe: str | None = None
    summary: str | None = None
    recommendation: str | None = None
    confidence: float | None = None
    pros: list[str] = Field(default_factory=list)
    cons_red: list[str] = Field(default_factory=list)
    cons_yellow: list[str] = Field(default_factory=list)
    hidden_costs: dict[str, Any] = Field(default_factory=dict)
    parking_price: int | None = None
    parking_included: bool | None = None
    usable_area_m2: float | None = None

    # Agency
    agency_name: str | None = None


class FeedData(BaseModel):
    """Normalized feed payload embedded in the page.

    ``posts`` maps each post id to its :class:`FeedPost`; ``ai_picks`` and
    ``fresh`` are ordered lists of ids into that map. A listing that qualifies for
    both tabs is stored once in ``posts`` and referenced from both lists.
    """

    model_config = ConfigDict(extra="forbid")

    posts: dict[str, FeedPost] = Field(default_factory=dict)
    ai_picks: list[str] = Field(default_factory=list)
    fresh: list[str] = Field(default_factory=list)


class FeedContext(BaseModel):
    """Metadata about a generated feed (title, timing, applied filters, counts)."""

    model_config = ConfigDict(extra="forbid")

    title: str
    generated_at: datetime
    fresh_days: int
    ai_picks_count: int = 0
    fresh_count: int = 0
    filters: dict[str, Any] = Field(default_factory=dict)
