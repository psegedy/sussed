"""Pure listing similarity matcher for duplicate/relisting detection."""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from math import asin, cos, radians, sin, sqrt
from typing import TYPE_CHECKING, ClassVar
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

if TYPE_CHECKING:
    from collections.abc import Iterable


@dataclass(frozen=True)
class DedupListing:
    """ORM-decoupled listing shape used by the pure dedup matcher."""

    external_id: str
    source: str
    listing_type: str
    property_category: str
    apartment_type: str | None
    city: str | None
    latitude: float | None
    longitude: float | None
    area_m2: float | None
    floor: int | None
    title: str
    description: str | None
    image_urls: tuple[str, ...]
    new_building: bool
    agency_name: str | None
    price_czk: int
    status: str


class DedupConfig(BaseModel):
    """Tunable weights and thresholds for pairwise duplicate scoring."""

    model_config = ConfigDict(frozen=True)

    gps_full_m: float = Field(default=30.0, allow_inf_nan=False, gt=0)
    gps_veto_m: float = Field(default=150.0, allow_inf_nan=False, gt=0)
    area_exact_m2: float = Field(default=1.0, allow_inf_nan=False, gt=0)
    area_veto_pct: float = Field(default=0.06, allow_inf_nan=False, gt=0, le=1)
    min_desc_len: int = Field(default=40, ge=0)
    desc_full_ratio: float = Field(default=0.90, allow_inf_nan=False, ge=0, le=1)
    desc_min_ratio: float = Field(default=0.50, allow_inf_nan=False, ge=0, le=1)
    overwhelming_desc_ratio: float = Field(default=0.95, allow_inf_nan=False, ge=0, le=1)
    weight_gps: float = Field(default=0.25, allow_inf_nan=False, ge=0, le=1)
    weight_area: float = Field(default=0.20, allow_inf_nan=False, ge=0, le=1)
    weight_floor: float = Field(default=0.15, allow_inf_nan=False, ge=0, le=1)
    weight_desc: float = Field(default=0.25, allow_inf_nan=False, ge=0, le=1)
    weight_title: float = Field(default=0.05, allow_inf_nan=False, ge=0, le=1)
    weight_photos: float = Field(default=0.10, allow_inf_nan=False, ge=0, le=1)
    weight_corroborating: float = Field(default=0.05, allow_inf_nan=False, ge=0, le=1)
    threshold_duplicate: float = Field(default=0.85, allow_inf_nan=False, ge=0, le=1)
    threshold_suspected: float = Field(default=0.60, allow_inf_nan=False, ge=0, le=1)

    @model_validator(mode="after")
    def _check_ordering_invariants(self) -> DedupConfig:
        """Enforce invariants between related fields to prevent misconfiguration."""
        if self.gps_full_m > self.gps_veto_m:
            raise ValueError(
                f"gps_full_m ({self.gps_full_m}) must be <= gps_veto_m ({self.gps_veto_m})"
            )
        if self.desc_min_ratio > self.desc_full_ratio:
            raise ValueError(
                f"desc_min_ratio ({self.desc_min_ratio}) must be <= desc_full_ratio ({self.desc_full_ratio})"
            )
        if self.threshold_suspected > self.threshold_duplicate:
            raise ValueError(
                f"threshold_suspected ({self.threshold_suspected}) must be <= threshold_duplicate ({self.threshold_duplicate})"
            )
        return self


class DuplicateMatch(BaseModel):
    """Result of comparing two listings for duplicate/relisting similarity."""

    status: str | None = Field(default=None)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reasons: list[str] = Field(default_factory=list)

    @property
    def is_duplicate(self) -> bool:
        """Return whether the pair cleared at least suspected duplicate status."""
        return self.status is not None


EARTH_RADIUS_M = 6_371_000.0
INACTIVE_STATUSES: frozenset[str] = frozenset({"sold", "removed", "expired"})


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate great-circle distance between two WGS84 coordinates in metres."""
    lat1_rad = radians(lat1)
    lat2_rad = radians(lat2)
    delta_lat = radians(lat2 - lat1)
    delta_lon = radians(lon2 - lon1)

    a = sin(delta_lat / 2) ** 2 + cos(lat1_rad) * cos(lat2_rad) * sin(delta_lon / 2) ** 2
    return 2 * EARTH_RADIUS_M * asin(sqrt(a))


def normalize_text(s: str) -> str:
    """Normalize text for fuzzy comparisons while preserving Czech unicode."""
    normalized_chars = [char.casefold() if char.isalnum() else " " for char in s]
    return " ".join("".join(normalized_chars).split())


def text_similarity(a: str | None, b: str | None, min_len: int) -> float:
    """Return SequenceMatcher ratio after normalization or 0 when text is insufficient."""
    if a is None or b is None:
        return 0.0

    normalized_a = normalize_text(a)
    normalized_b = normalize_text(b)
    if len(normalized_a) < min_len or len(normalized_b) < min_len:
        return 0.0

    return SequenceMatcher(None, normalized_a, normalized_b).ratio()


def image_path_token(url: str) -> str:
    """Return the stable uploaded-image path without scheme, host, or query string."""
    return urlsplit(url).path


def image_overlap(a: Iterable[str], b: Iterable[str]) -> tuple[float, int]:
    """Return Jaccard overlap and shared count for stable image path tokens."""
    tokens_a = {token for url in a if (token := image_path_token(url))}
    tokens_b = {token for url in b if (token := image_path_token(url))}
    shared_count = len(tokens_a & tokens_b)
    union_count = len(tokens_a | tokens_b)
    if union_count == 0:
        return 0.0, 0
    return shared_count / union_count, shared_count


def score_pair(
    a: DedupListing,
    b: DedupListing,
    config: DedupConfig | None = None,
) -> DuplicateMatch:
    """Score whether two listings represent the same physical property."""
    dedup_config = config or DedupConfig()

    veto = _veto_reason(a, b, dedup_config)
    if veto is not None:
        return DuplicateMatch(status=None, confidence=0.0, reasons=[veto])

    context = _SignalContext(a=a, b=b, config=dedup_config)
    confidence = min(1.0, context.score())
    status = _status_for_confidence(confidence, dedup_config)
    reasons = context.reasons

    if status == "duplicate" and _should_apply_new_building_cap(a, b, dedup_config, context):
        status = "suspected"
        reasons.append("new building cap")

    return DuplicateMatch(status=status, confidence=confidence, reasons=reasons)


def _veto_reason(a: DedupListing, b: DedupListing, config: DedupConfig) -> str | None:
    """Return a hard-veto reason when listings cannot be relistings."""
    if a.listing_type != b.listing_type:
        return "different listing type"
    if a.property_category != b.property_category:
        return "different property category"
    if (
        a.apartment_type is not None
        and b.apartment_type is not None
        and a.apartment_type != b.apartment_type
    ):
        return "different apartment type"

    gps_distance = _gps_distance(a, b)
    if gps_distance is not None and gps_distance > config.gps_veto_m:
        return f"GPS ~{gps_distance:.0f}m apart"

    if _is_apartment(a, b) and a.area_m2 is not None and b.area_m2 is not None:
        area_diff_pct = _relative_area_diff(a.area_m2, b.area_m2)
        if area_diff_pct > config.area_veto_pct:
            return f"apartment area differs by {area_diff_pct:.1%}"

    if _is_apartment(a, b) and a.floor is not None and b.floor is not None and a.floor != b.floor:
        if a.new_building or b.new_building:
            return "different floors in new building"

        desc_ratio = text_similarity(a.description, b.description, config.min_desc_len)
        _, shared_photos = image_overlap(a.image_urls, b.image_urls)
        if not (desc_ratio >= config.overwhelming_desc_ratio and shared_photos >= 1):
            return "different apartment floors"

    return None


def _status_for_confidence(confidence: float, config: DedupConfig) -> str | None:
    if confidence >= config.threshold_duplicate:
        return "duplicate"
    if confidence >= config.threshold_suspected:
        return "suspected"
    return None


def _gps_distance(a: DedupListing, b: DedupListing) -> float | None:
    if a.latitude is None or a.longitude is None or b.latitude is None or b.longitude is None:
        return None
    return haversine_m(a.latitude, a.longitude, b.latitude, b.longitude)


def _is_apartment(a: DedupListing, b: DedupListing) -> bool:
    return a.property_category == "apartment" and b.property_category == "apartment"


def _relative_area_diff(area_a: float, area_b: float) -> float:
    denominator = min(abs(area_a), abs(area_b))
    if denominator == 0:
        return 0.0 if area_a == area_b else 1.0
    return abs(area_a - area_b) / denominator


def _linear_between(value: float, zero_at: float, full_at: float) -> float:
    if value >= full_at:
        return 1.0
    if value <= zero_at:
        return 0.0
    return (value - zero_at) / (full_at - zero_at)


def _format_ratio_pct(ratio: float) -> str:
    return f"{ratio:.0%}"


@dataclass
class _SignalContext:
    """Accumulator for weighted scoring signals and human-readable reasons."""

    a: DedupListing
    b: DedupListing
    config: DedupConfig
    reasons: list[str] = field(default_factory=list)

    _STATUS_PRIORITY: ClassVar[dict[str, int]] = {
        "active": 0,
        "sold": 1,
        "removed": 2,
        "expired": 3,
    }

    @property
    def desc_similarity(self) -> float:
        """Description similarity ratio used by scoring and novostavba cap."""
        return text_similarity(self.a.description, self.b.description, self.config.min_desc_len)

    @property
    def photo_overlap(self) -> tuple[float, int]:
        """Image Jaccard overlap and shared count used by scoring and cap logic."""
        return image_overlap(self.a.image_urls, self.b.image_urls)

    def score(self) -> float:
        """Calculate the weighted confidence score and collect reasons."""
        return (
            self._gps_score()
            + self._area_score()
            + self._floor_score()
            + self._description_score()
            + self._title_score()
            + self._photo_score()
            + self._corroborating_score()
        )

    def _gps_score(self) -> float:
        distance = _gps_distance(self.a, self.b)
        if distance is None:
            return 0.0

        if distance <= self.config.gps_full_m:
            score = 1.0
        else:
            span = self.config.gps_veto_m - self.config.gps_full_m
            score = max(0.0, 1.0 - ((distance - self.config.gps_full_m) / span))
        if score > 0:
            self.reasons.append(f"GPS {distance:.0f}m apart")
        return self.config.weight_gps * score

    def _area_score(self) -> float:
        if self.a.area_m2 is None or self.b.area_m2 is None:
            return 0.0

        diff = abs(self.a.area_m2 - self.b.area_m2)
        veto_diff_m2 = self.config.area_veto_pct * min(abs(self.a.area_m2), abs(self.b.area_m2))
        if diff <= self.config.area_exact_m2:
            score = 1.0
        elif veto_diff_m2 <= self.config.area_exact_m2:
            score = 0.0
        else:
            score = max(
                0.0,
                1.0
                - ((diff - self.config.area_exact_m2) / (veto_diff_m2 - self.config.area_exact_m2)),
            )

        if score == 1.0:
            if diff == 0:
                self.reasons.append(f"identical {self.a.area_m2:g} m²")
            else:
                self.reasons.append(f"area within {diff:g} m²")
        elif score > 0:
            self.reasons.append(f"area differs by {diff:g} m²")
        return self.config.weight_area * score

    def _floor_score(self) -> float:
        if self.a.floor is None or self.b.floor is None:
            return 0.0
        if self.a.floor == self.b.floor:
            self.reasons.append(f"same floor {self.a.floor}")
            return self.config.weight_floor
        lower_floor, higher_floor = sorted((self.a.floor, self.b.floor))
        self.reasons.append(f"floor mismatch {lower_floor} vs {higher_floor}")
        return 0.0

    def _description_score(self) -> float:
        ratio = self.desc_similarity
        score = _linear_between(ratio, self.config.desc_min_ratio, self.config.desc_full_ratio)
        if score > 0:
            self.reasons.append(f"description {_format_ratio_pct(ratio)} match")
        return self.config.weight_desc * score

    def _title_score(self) -> float:
        ratio = text_similarity(self.a.title, self.b.title, min_len=0)
        score = _linear_between(ratio, self.config.desc_min_ratio, self.config.desc_full_ratio)
        if score > 0:
            self.reasons.append(f"title {_format_ratio_pct(ratio)} match")
        return self.config.weight_title * score

    def _photo_score(self) -> float:
        jaccard, shared_count = self.photo_overlap
        if shared_count >= 1:
            score = min(1.0, 0.6 + (0.4 * jaccard))
            self.reasons.append(f"{shared_count} shared photos")
        else:
            score = jaccard
        return self.config.weight_photos * score

    def _corroborating_score(self) -> float:
        signals = 0
        if self._has_active_inactive_pair():
            signals += 1
            self.reasons.append(f"old listing {self._inactive_status()}")
        if self.a.agency_name and self.a.agency_name == self.b.agency_name:
            signals += 1
            self.reasons.append("same agency")
        if self.a.price_czk != self.b.price_czk:
            signals += 1
            self.reasons.append(self._price_change_reason())
        return self.config.weight_corroborating * min(1.0, signals / 3)

    def _has_active_inactive_pair(self) -> bool:
        statuses = {self.a.status, self.b.status}
        return "active" in statuses and bool(statuses & INACTIVE_STATUSES)

    def _inactive_status(self) -> str:
        inactive = sorted(
            {self.a.status, self.b.status} & INACTIVE_STATUSES,
            key=lambda status: self._STATUS_PRIORITY.get(status, 99),
        )
        return inactive[0]

    def _price_change_reason(self) -> str:
        high = max(self.a.price_czk, self.b.price_czk)
        low = min(self.a.price_czk, self.b.price_czk)
        if high <= 0:
            return "price changed"
        return f"price -{((high - low) / high):.0%}"


def _should_apply_new_building_cap(
    a: DedupListing,
    b: DedupListing,
    config: DedupConfig,
    context: _SignalContext,
) -> bool:
    if not (a.new_building or b.new_building):
        return False

    floor_strong = a.floor is not None and b.floor is not None and a.floor == b.floor
    area_strong = (
        a.area_m2 is not None
        and b.area_m2 is not None
        and abs(a.area_m2 - b.area_m2) <= config.area_exact_m2
    )
    _, shared_photos = context.photo_overlap
    desc_strong = context.desc_similarity >= config.desc_full_ratio
    return not (floor_strong and area_strong and shared_photos >= 1 and desc_strong)
