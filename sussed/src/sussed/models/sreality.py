"""
Pydantic models for sreality.cz API responses 📦

These models parse the raw JSON from sreality's API into typed Python objects.
"""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class SrealityGPS(BaseModel):
    """GPS coordinates from sreality."""
    lat: Decimal
    lon: Decimal


class SrealityPriceAlt(BaseModel):
    """Alternative price display (e.g., price per m²)."""
    value_raw: int
    unit: str


class SrealityPrice(BaseModel):
    """Price information from sreality."""
    value_raw: int
    unit: str = ""  # Empty for sale, "za měsíc" for rent
    name: str = ""
    alt: SrealityPriceAlt | None = None


class SrealityCompany(BaseModel):
    """Real estate agency info."""
    id: int
    name: str
    url: str | None = None
    logo_small: str | None = None


class SrealityEmbedded(BaseModel):
    """Embedded objects in listing."""
    company: SrealityCompany | None = None


class SrealitySeo(BaseModel):
    """SEO/category data - contains the real category codes."""
    category_main_cb: int  # 1=apartment, 2=house, 3=land, 4=commercial
    category_sub_cb: int | None = None  # Apartment type: 2=1+kk, 3=1+1, etc.
    category_type_cb: int  # 1=sale, 2=rent
    locality: str | None = None


class SrealityImageLink(BaseModel):
    """Image link from sreality."""
    href: str


class SrealityLinks(BaseModel):
    """Links section of listing."""
    self: dict | None = None
    images: list[SrealityImageLink] = Field(default_factory=list)


class SrealityEstate(BaseModel):
    """
    A single estate/listing from sreality API.
    
    This is the main model for parsing listing data.
    """
    model_config = ConfigDict(extra="ignore")  # Ignore unknown fields
    
    # Core identification
    hash_id: int  # Unique ID - use as external_id
    name: str  # Listing title
    
    # Pricing
    price: int  # Raw price
    price_czk: SrealityPrice
    
    # Type info
    type: int  # 1=sale, 2=rent
    category: int  # 1=apartment, 2=house, etc.
    seo: SrealitySeo
    
    # Location
    locality: str  # Human-readable location
    gps: SrealityGPS | None = None
    
    # Labels/features
    labels: list[str] = Field(default_factory=list)  # Human-readable: ["Novostavba", "Balkon"]
    labels_all: list[list[str]] = Field(default_factory=list, alias="labelsAll")
    # labelsAll[0] = property features: ["garage", "balcony", "elevator"]
    # labelsAll[1] = nearby amenities: ["metro", "shop", "school"]
    
    # Media
    advert_images_count: int = 0
    has_floor_plan: int | bool = 0
    has_video: bool = False
    has_matterport_url: bool = False  # 3D tour
    has_panorama: int | bool = 0
    
    # Agency
    embedded_data: SrealityEmbedded | None = Field(default=None, alias="_embedded")
    
    # Links
    links_data: SrealityLinks | None = Field(default=None, alias="_links")
    
    # Other
    new: bool = False
    rus: bool = False  # Russian listing?
    is_auction: bool = False
    attractive_offer: int = 0
    exclusively_at_rk: int = 0  # Exclusive to agency
    region_tip: int = 0
    paid_logo: int = 0
    
    @property
    def features(self) -> list[str]:
        """Get property features from labelsAll[0]."""
        if self.labels_all and len(self.labels_all) > 0:
            return self.labels_all[0]
        return []
    
    @property
    def amenities(self) -> list[str]:
        """Get nearby amenities from labelsAll[1]."""
        if self.labels_all and len(self.labels_all) > 1:
            return self.labels_all[1]
        return []
    
    @property
    def price_per_m2(self) -> int | None:
        """Get price per m² if available."""
        if self.price_czk.alt:
            return self.price_czk.alt.value_raw
        return None
    
    @property
    def image_urls(self) -> list[str]:
        """Extract image URLs from links."""
        if self.links_data and self.links_data.images:
            return [img.href for img in self.links_data.images]
        return []
    
    @property
    def agency_name(self) -> str | None:
        """Get agency name if available."""
        if self.embedded_data and self.embedded_data.company:
            return self.embedded_data.company.name
        return None
    
    @property 
    def agency_id(self) -> str | None:
        """Get agency ID if available."""
        if self.embedded_data and self.embedded_data.company:
            return str(self.embedded_data.company.id)
        return None


class SrealityNotPreciseCount(BaseModel):
    """Count of listings without precise location."""
    result_size: int


class SrealityIsSaved(BaseModel):
    """Saved search info."""
    saved: bool = False


class SrealityEmbeddedResponse(BaseModel):
    """Embedded data in API response."""
    estates: list[SrealityEstate] = Field(default_factory=list)
    is_saved: SrealityIsSaved | None = None
    not_precise_location_count: SrealityNotPreciseCount | None = None


class SrealityResponse(BaseModel):
    """
    Root response from sreality estates API.
    
    GET https://www.sreality.cz/api/cs/v2/estates
    """
    model_config = ConfigDict(extra="ignore")
    
    result_size: int  # Total number of results
    per_page: int = 20
    page: int = 1
    
    embedded_data: SrealityEmbeddedResponse = Field(alias="_embedded")
    
    # Metadata
    title: str = ""
    locality: str = ""
    logged_in: bool = False
    
    @property
    def estates(self) -> list[SrealityEstate]:
        """Get list of estates from embedded data."""
        return self.embedded_data.estates
    
    @property
    def total_pages(self) -> int:
        """Calculate total number of pages."""
        if self.per_page == 0:
            return 0
        return (self.result_size + self.per_page - 1) // self.per_page


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
    37: "rodinný dům",  # House
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
