"""Data models - Pydantic schemas for listings and API responses 📦"""

from sussed.models.sreality import (
    SrealityEstate,
    SrealityResponse,
    SrealityV1Detail,
    SrealityV1DetailResponse,
    SrealityV1Estate,
    SrealityV1SearchResponse,
    get_apartment_type,
)

__all__ = [
    "SrealityEstate",
    "SrealityResponse",
    "SrealityV1Detail",
    "SrealityV1DetailResponse",
    "SrealityV1Estate",
    "SrealityV1SearchResponse",
    "get_apartment_type",
]
