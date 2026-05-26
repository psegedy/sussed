"""
Pydantic models for the sreality.cz v1 API responses ��

These models parse the raw JSON from sreality's current API into typed
Python objects while ignoring unknown fields because that API is a moving
fucking target.
"""

from __future__ import annotations

from decimal import Decimal  # noqa: TC003
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SrealityV1BaseModel(BaseModel):
    """Base model that tolerates future sreality API additions."""

    model_config = ConfigDict(extra="ignore")


class SrealityV1NamedValue(SrealityV1BaseModel):
    """Common ``{"name": ..., "value": ...}`` shape used by v1."""

    name: str | None = None
    value: int | float | str | bool | None = None

    @property
    def int_value(self) -> int | None:
        """Return value as an int when possible."""
        if self.value is None:
            return None
        if isinstance(self.value, bool):
            return int(self.value)
        if isinstance(self.value, int | float):
            return int(self.value)
        if isinstance(self.value, str) and self.value.isdigit():
            return int(self.value)
        return None


class SrealityV1Locality(SrealityV1BaseModel):
    """Nested locality object returned by v1 search and detail."""

    city: str | None = None
    city_seo_name: str | None = None
    citypart: str | None = None
    citypart_seo_name: str | None = None
    country: str | None = None
    country_id: int | None = None
    country_seo_name: str | None = None
    district: str | None = None
    district_id: int | None = None
    district_seo_name: str | None = None
    entity_type: str | None = None
    geohash: str | None = None
    gps_lat: float | None = None
    gps_lon: float | None = None
    housenumber: str | None = None
    inaccuracy_type: str | None = None
    municipality: str | None = None
    municipality_id: int | None = None
    municipality_seo_name: str | None = None
    quarter: str | None = None
    quarter_id: int | None = None
    quarter_seo_name: str | None = None
    region: str | None = None
    region_id: int | None = None
    region_seo_name: str | None = None
    street: str | None = None
    street_id: int | None = None
    street_seo_name: str | None = None
    streetnumber: str | None = None
    ward: str | None = None
    ward_id: int | None = None
    ward_seo_name: str | None = None
    zip: str | int | None = None


class SrealityV1AdvertImageSummary(SrealityV1BaseModel):
    """Image metadata shape used in search results."""

    advert_image_sdn_url: str | None = None
    restb_room_type: int | None = None


class SrealityV1DetailImage(SrealityV1BaseModel):
    """Image metadata shape used in detail responses."""

    url: str | None = None
    alt: str | None = None
    height: int | None = None
    id: int | str | None = None
    kind: int | None = None
    order: int | None = None
    width: int | None = None


class SrealityV1Estate(SrealityV1BaseModel):
    """A single estate from ``/api/v1/estates/search``."""

    hash_id: int
    advert_name: str
    locality: SrealityV1Locality = Field(default_factory=SrealityV1Locality)

    price: float | None = None
    price_czk: float | None = None
    price_czk_m2: int | None = None
    price_unit_cb: SrealityV1NamedValue | None = None
    price_currency_cb: SrealityV1NamedValue | None = None
    price_summary: float | None = None
    price_summary_czk: float | None = None
    price_summary_czk_m2: float | None = None
    price_summary_unit_cb: SrealityV1NamedValue | None = None

    category_main_cb: SrealityV1NamedValue = Field(default_factory=SrealityV1NamedValue)
    category_sub_cb: SrealityV1NamedValue = Field(default_factory=SrealityV1NamedValue)
    category_type_cb: SrealityV1NamedValue = Field(default_factory=SrealityV1NamedValue)

    advert_images: list[str] = Field(default_factory=list)
    advert_images_all: list[SrealityV1AdvertImageSummary] = Field(default_factory=list)
    has_video: bool = False
    has_matterport_url: bool = False

    premise: dict[str, Any] | None = None
    premise_id: int | None = None
    premise_logo: str | None = None
    user_id: int | None = None

    @property
    def name(self) -> str:
        """Backward-compatible v2 title alias."""
        return self.advert_name

    @property
    def type(self) -> int | None:
        """Backward-compatible v2 listing type alias."""
        return self.category_type_cb.int_value

    @property
    def category(self) -> int | None:
        """Backward-compatible v2 property category alias."""
        return self.category_main_cb.int_value

    @property
    def labels(self) -> list[str]:
        """v1 search no longer exposes feature labels."""
        return []

    @property
    def features(self) -> list[str]:
        """v1 search no longer exposes feature labels."""
        return []

    @property
    def price_per_m2(self) -> int | None:
        """Return v1 price-per-square-meter value."""
        return self.price_czk_m2

    @property
    def image_urls(self) -> list[str]:
        """Return raw image URLs from search results."""
        return self.advert_images

    @property
    def advert_images_count(self) -> int:
        """Backward-compatible image-count alias."""
        return len(self.advert_images_all or self.advert_images)

    @property
    def agency_name(self) -> None:
        """Search results only expose premise IDs; detail has the name."""
        return None

    @property
    def agency_id(self) -> str | None:
        """Return premise ID as a string for DB storage."""
        return str(self.premise_id) if self.premise_id else None


class SrealityV1Pagination(SrealityV1BaseModel):
    """Pagination block from search response."""

    limit: int = 0
    offset: int = 0
    total: int = 0

    @property
    def total_pages(self) -> int:
        """Calculate total pages from the effective server limit."""
        if self.limit <= 0:
            return 0
        return (self.total + self.limit - 1) // self.limit

    @property
    def current_offset(self) -> int:
        """Return the current zero-based result offset."""
        return self.offset

    @property
    def next_offset(self) -> int | None:
        """Return the next result offset, or None when there is no next page."""
        next_offset = self.offset + self.limit
        if self.limit <= 0 or next_offset >= self.total:
            return None
        return next_offset


class SrealityV1SearchResponse(SrealityV1BaseModel):
    """Root response from ``/api/v1/estates/search``."""

    status_code: int
    status_message: str = ""
    pagination: SrealityV1Pagination = Field(default_factory=SrealityV1Pagination)
    results: list[SrealityV1Estate] = Field(default_factory=list)
    search_title: str | None = None
    meta_title: str | None = None
    meta_description: str | None = None

    @property
    def estates(self) -> list[SrealityV1Estate]:
        """Backward-compatible alias for v2 response consumers."""
        return self.results

    @property
    def result_size(self) -> int:
        """Backward-compatible alias for total result count."""
        return self.pagination.total

    @property
    def per_page(self) -> int:
        """Backward-compatible alias for page size."""
        return self.pagination.limit

    @property
    def total_pages(self) -> int:
        """Return total search pages."""
        return self.pagination.total_pages


class SrealityV1Detail(SrealityV1Estate):
    """The ``result`` object from ``/api/v1/estates/{hash_id}``."""

    advert_code: str | None = None
    advert_description: str | None = None
    since: str | None = None
    edited: str | None = None

    balcony: bool | None = None
    loggia: bool | None = None
    terrace: bool | None = None
    cellar: bool | None = None
    garage: bool | None = None
    parking_lots: bool | None = None
    low_energy: bool | None = None
    panorama: bool | None = None
    basin: bool | None = None
    garret: bool | None = None

    usable_area: Decimal | None = None
    floor_area: Decimal | None = None
    garden_area: Decimal | None = None
    terrace_area: Decimal | None = None
    balcony_area: Decimal | None = None
    loggia_area: Decimal | None = None
    cellar_area: Decimal | None = None
    garage_count: int | None = None

    floor_number: int | None = None
    floors: int | None = None
    underground_floors: int | None = None
    acceptance_year: int | None = None
    reconstruction_year: int | None = None

    building_type: SrealityV1NamedValue | None = None
    building_condition: SrealityV1NamedValue | None = None
    ownership: SrealityV1NamedValue | None = None
    furnished: SrealityV1NamedValue | None = None
    elevator: SrealityV1NamedValue | None = None
    flat_class: SrealityV1NamedValue | None = None
    object_location: SrealityV1NamedValue | None = None
    easy_access: SrealityV1NamedValue | None = None
    energy_efficiency_rating_cb: SrealityV1NamedValue | None = None
    energy_performance_certificate: SrealityV1NamedValue | None = None
    protection: SrealityV1NamedValue | None = None
    state_cb: SrealityV1NamedValue | None = None
    surroundings_type: SrealityV1NamedValue | None = None

    price_note: str | None = None
    matterport_url: str | None = None
    advert_images: list[SrealityV1DetailImage] = Field(default_factory=list)  # type: ignore[assignment]
    premise: dict[str, Any] | None = None

    @property
    def image_urls(self) -> list[str]:
        """Return raw image URLs from detail images."""
        return [image.url for image in self.advert_images if image.url]


class SrealityV1DetailResponse(SrealityV1BaseModel):
    """Root detail response wrapping a single v1 estate result."""

    status_code: int
    status_message: str = ""
    result: SrealityV1Detail


# Mapping of apartment type codes to human-readable strings
APARTMENT_TYPE_MAP = {
    2: "1+kk",
    3: "1+1",
    4: "2+kk",
    5: "2+1",
    6: "3+kk",
    7: "3+1",
    8: "4+kk",
    9: "4+1",
    10: "5+kk",
    11: "5+1",
    12: "6+",
    16: "atypický",
    37: "rodinný dům",
    39: "vila",
    43: "chalupa",
    44: "chata",
    47: "zemědělská usedlost",
}


def get_apartment_type(category_sub_cb: int | None) -> str | None:
    """Convert category code to apartment type string."""
    if category_sub_cb is None:
        return None
    return APARTMENT_TYPE_MAP.get(category_sub_cb)


# Backward-compatible aliases for existing imports during the migration.
SrealityEstate = SrealityV1Estate
SrealityResponse = SrealityV1SearchResponse
