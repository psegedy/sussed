"""
Database models for sussed 🗄️

SQLModel models for PostgreSQL - the foundation of our data layer.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from sqlalchemy import Column, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, Relationship, SQLModel


class VibeCheck(str, Enum):
    """The sacred vibe classifications."""
    PEAK = "peak"
    VALID = "valid"
    MID = "mid"
    SUS = "sus"
    UNKNOWN = "unknown"


class ListingType(str, Enum):
    """Sale or rent?"""
    SALE = "sale"
    RENT = "rent"


class PropertyCategory(str, Enum):
    """Type of property."""
    APARTMENT = "apartment"
    HOUSE = "house"
    LAND = "land"
    COMMERCIAL = "commercial"
    OTHER = "other"


class ListingStatus(str, Enum):
    """Is this listing still active?"""
    ACTIVE = "active"
    SOLD = "sold"
    REMOVED = "removed"
    EXPIRED = "expired"


class Listing(SQLModel, table=True):
    """The main listing model."""
    __tablename__ = "listings"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    source: str = Field(index=True)
    external_id: str = Field(index=True)
    url: str = Field(sa_column=Column(Text))
    title: str = Field(sa_column=Column(Text))
    description: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    price_czk: int = Field(index=True)
    price_per_m2: int | None = Field(default=None)
    listing_type: ListingType = Field(index=True)
    city: str = Field(index=True)
    district: str | None = Field(default=None, index=True)
    address: str | None = Field(default=None)
    latitude: Decimal | None = Field(default=None, max_digits=10, decimal_places=7)
    longitude: Decimal | None = Field(default=None, max_digits=10, decimal_places=7)
    property_category: PropertyCategory = Field(index=True)
    apartment_type: str | None = Field(default=None, index=True)
    area_m2: Decimal | None = Field(default=None)
    floor: int | None = Field(default=None)
    total_floors: int | None = Field(default=None)
    features: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    raw_labels: list[str] | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    image_urls: list[str] | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    image_count: int = Field(default=0)
    has_floor_plan: bool = Field(default=False)
    has_video: bool = Field(default=False)
    has_3d_tour: bool = Field(default=False)
    agency_name: str | None = Field(default=None)
    agency_id: str | None = Field(default=None)
    vibe_check: VibeCheck = Field(default=VibeCheck.UNKNOWN, index=True)
    ai_analysis: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    status: ListingStatus = Field(default=ListingStatus.ACTIVE, index=True)
    first_seen_at: datetime = Field(default_factory=datetime.utcnow)
    last_seen_at: datetime = Field(default_factory=datetime.utcnow)
    last_price_change_at: datetime | None = Field(default=None)
    # "Aktualizace" from sreality detail endpoint - this is the last MODIFIED date,
    # NOT the original publish date. Sreality doesn't expose "Vloženo" via API.
    # We store the oldest "Aktualizace" we've seen as a rough lower bound.
    updated_at_source: datetime | None = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    price_history: list["PriceHistory"] = Relationship(back_populates="listing")


class PriceHistory(SQLModel, table=True):
    """Track price changes over time."""
    __tablename__ = "price_history"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    listing_id: uuid.UUID = Field(foreign_key="listings.id", index=True)
    price_czk: int
    price_per_m2: int | None = Field(default=None)
    change_type: str
    change_amount: int | None = Field(default=None)
    change_percent: Decimal | None = Field(default=None)
    recorded_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    listing: Listing = Relationship(back_populates="price_history")


class ScrapeRun(SQLModel, table=True):
    """Track scraping runs for monitoring."""
    __tablename__ = "scrape_runs"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    source: str
    city: str | None = Field(default=None)
    started_at: datetime = Field(default_factory=datetime.utcnow)
    finished_at: datetime | None = Field(default=None)
    duration_seconds: int | None = Field(default=None)
    listings_found: int = Field(default=0)
    listings_new: int = Field(default=0)
    listings_updated: int = Field(default=0)
    listings_removed: int = Field(default=0)
    price_changes_detected: int = Field(default=0)
    errors_count: int = Field(default=0)
    error_details: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    status: str = Field(default="running")


class SearchFilter(SQLModel, table=True):
    """Saved search filters / user preferences."""
    __tablename__ = "search_filters"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    name: str
    cities: list[str] | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    listing_type: ListingType | None = Field(default=None)
    property_categories: list[str] | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    apartment_types: list[str] | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    price_min: int | None = Field(default=None)
    price_max: int | None = Field(default=None)
    area_min: Decimal | None = Field(default=None)
    area_max: Decimal | None = Field(default=None)
    must_have_garage: bool | None = Field(default=None)
    must_have_balcony: bool | None = Field(default=None)
    must_have_elevator: bool | None = Field(default=None)
    must_have_cellar: bool | None = Field(default=None)
    min_vibe_check: VibeCheck | None = Field(default=None)
    notify_new_listings: bool = Field(default=True)
    notify_price_drops: bool = Field(default=True)
    notify_immediately: bool = Field(default=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
