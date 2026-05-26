"""
sreality.cz scraper 🕷️

Async scraper using their beautiful JSON API.
No BeautifulSoup needed - we're not animals.
"""

import asyncio
import re
from datetime import datetime
from decimal import Decimal
from typing import Any

import httpx
from loguru import logger

from sussed.config import get_settings
from sussed.models.sreality import (
    APARTMENT_TYPE_MAP,
    SrealityEstate,
    SrealityResponse,
    get_apartment_type,
)


class SrealityScraper:
    """
    Scraper for sreality.cz using their JSON API.
    
    Usage:
        scraper = SrealityScraper()
        async for listing in scraper.scrape_city("brno"):
            print(listing)
    """
    
    BASE_URL = "https://www.sreality.cz/api/cs/v2/estates"
    
    # Region IDs (locality_region_id) - whole kraj
    REGION_IDS = {
        "praha": 10,
        "jihocesky": 11,
        "jihomoravsky": 14,
        "karlovarsky": 12,
        "kralovehradecky": 13,
        "liberecky": 15,
        "moravskoslezsky": 16,
        "olomoucky": 17,
        "pardubicky": 18,
        "plzensky": 19,
        "stredocesky": 20,
        "ustecky": 21,
        "vysocina": 22,
        "zlinsky": 23,
    }
    
    # District IDs (locality_district_id) - okres level, more precise
    DISTRICT_IDS = {
        "brno-mesto": 72,
        "brno-venkov": 73,
    }
    
    # City to locality mapping: (region_key, district_key | None)
    # If district_key is set, we use locality_district_id (more precise)
    # If None, we fall back to locality_region_id (whole kraj)
    CITY_TO_LOCALITY = {
        "brno": ("jihomoravsky", "brno-mesto"),
        "brno-venkov": ("jihomoravsky", "brno-venkov"),
        "praha": ("praha", None),
        "prague": ("praha", None),
        "ostrava": ("moravskoslezsky", None),
        "plzen": ("plzensky", None),
        "liberec": ("liberecky", None),
        "olomouc": ("olomoucky", None),
        "ceske budejovice": ("jihocesky", None),
        "hradec kralove": ("kralovehradecky", None),
        "pardubice": ("pardubicky", None),
        "zlin": ("zlinsky", None),
        "karlovy vary": ("karlovarsky", None),
    }
    
    def __init__(self):
        settings = get_settings()
        self.rate_limit = settings.scrape_rate_limit
        self.user_agent = settings.user_agent
        self._last_request_time: float = 0
    
    async def _rate_limit_wait(self) -> None:
        """Wait to respect rate limiting."""
        if self.rate_limit <= 0:
            return
        
        now = asyncio.get_event_loop().time()
        elapsed = now - self._last_request_time
        wait_time = (1.0 / self.rate_limit) - elapsed
        
        if wait_time > 0:
            await asyncio.sleep(wait_time)
        
        self._last_request_time = asyncio.get_event_loop().time()
    
    def _get_headers(self) -> dict[str, str]:
        """Get headers for API requests."""
        return {
            "User-Agent": self.user_agent,
            "Accept": "application/json",
            "Accept-Language": "cs,en;q=0.9",
            "Referer": "https://www.sreality.cz/",
        }
    
    def _get_locality_params(self, city: str) -> dict[str, int]:
        """Get locality API params for a city.
        
        Returns dict with either locality_district_id (precise)
        or locality_region_id (whole kraj), or empty dict if unknown.
        """
        city_lower = city.lower().strip()
        
        # Check city mapping first (most common path)
        mapping = self.CITY_TO_LOCALITY.get(city_lower)
        if mapping:
            region_key, district_key = mapping
            # Use district ID if available (more precise!)
            if district_key and district_key in self.DISTRICT_IDS:
                district_id = self.DISTRICT_IDS[district_key]
                logger.debug(f"Using district filter: {district_key} (ID {district_id})")
                return {"locality_district_id": district_id}
            # Fall back to region
            region_id = self.REGION_IDS.get(region_key)
            if region_id:
                return {"locality_region_id": region_id}
        
        # Direct region lookup (e.g., "jihomoravsky")
        if city_lower in self.REGION_IDS:
            return {"locality_region_id": self.REGION_IDS[city_lower]}
        
        logger.warning(f"Unknown city/region: {city}")
        return {}
    
    # Advert age filter values (in days)
    ADVERT_AGE_OPTIONS = {
        "day": 2,      # Last 24h (use 2 to be safe)
        "week": 8,     # Last week
        "month": 31,   # Last month  
    }
    
    async def fetch_page(
        self,
        client: httpx.AsyncClient,
        page: int = 1,
        per_page: int = 60,
        category_main: int = 1,  # 1=apartments
        category_type: int = 1,  # 1=sale, 2=rent
        locality_params: dict[str, int] | None = None,
        advert_age_to: int | None = None,
    ) -> SrealityResponse | None:
        """
        Fetch a single page of listings.
        
        Args:
            client: httpx AsyncClient
            page: Page number (1-indexed)
            per_page: Items per page (max 60)
            category_main: 1=apartment, 2=house, 3=land, 4=commercial
            category_type: 1=sale, 2=rent
            locality_params: Locality filter params (locality_district_id or locality_region_id)
            advert_age_to: Max age of listings in days (filters to recent listings)
        
        Returns:
            Parsed SrealityResponse or None on error
        """
        await self._rate_limit_wait()
        
        params: dict[str, Any] = {
            "page": page,
            "per_page": min(per_page, 60),  # API max is 60
            "category_main_cb": category_main,
            "category_type_cb": category_type,
        }
        
        if locality_params:
            params.update(locality_params)
        
        if advert_age_to:
            params["advert_age_to"] = advert_age_to
        
        try:
            logger.debug(f"Fetching page {page} with params: {params}")
            response = await client.get(
                self.BASE_URL,
                params=params,
                headers=self._get_headers(),
                timeout=30.0,
            )
            response.raise_for_status()
            
            data = response.json()
            return SrealityResponse.model_validate(data)
            
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error fetching page {page}: {e.response.status_code}")
            return None
        except httpx.RequestError as e:
            logger.error(f"Request error fetching page {page}: {e}")
            return None
        except Exception as e:
            logger.error(f"Error parsing page {page}: {e}")
            return None
    
    async def scrape(
        self,
        city: str | None = None,
        listing_type: str = "sale",  # "sale" or "rent"
        property_type: str = "apartment",  # "apartment" or "house"
        max_pages: int | None = None,
        max_age: str | int | None = None,  # "day", "week", "month" or days as int
    ):
        """
        Scrape listings from sreality.
        
        Args:
            city: City name (e.g., "brno", "praha")
            listing_type: "sale" or "rent"
            property_type: "apartment" or "house"
            max_pages: Maximum pages to scrape (None = all)
            max_age: Filter by listing age - "day", "week", "month", raw int days, or None for all
        
        Yields:
            SrealityEstate objects
        """
        # Map parameters to API codes
        category_type = 1 if listing_type == "sale" else 2
        category_main = 1 if property_type == "apartment" else 2
        locality_params = self._get_locality_params(city) if city else None
        
        # Resolve advert_age_to: named preset or raw days
        if isinstance(max_age, int):
            advert_age_to = max_age
        elif isinstance(max_age, str) and max_age.isdigit():
            advert_age_to = int(max_age)
        else:
            advert_age_to = self.ADVERT_AGE_OPTIONS.get(max_age) if max_age else None
        
        logger.info(
            f"Starting scrape: city={city}, type={listing_type}, "
            f"property={property_type}, locality={locality_params}, max_age={max_age}"
        )
        
        async with httpx.AsyncClient() as client:
            # Fetch first page to get total count
            first_page = await self.fetch_page(
                client,
                page=1,
                category_main=category_main,
                category_type=category_type,
                locality_params=locality_params,
                advert_age_to=advert_age_to,
            )
            
            if not first_page:
                logger.error("Failed to fetch first page")
                return
            
            total_pages = first_page.total_pages
            if max_pages:
                total_pages = min(total_pages, max_pages)
            
            logger.info(
                f"Found {first_page.result_size} listings across {first_page.total_pages} pages. "
                f"Scraping {total_pages} pages."
            )
            
            # Deduplicate: sreality API can return the same listing on
            # different pages with inconsistent prices (pagination shifts
            # as new listings appear). Only yield each hash_id once.
            seen_ids: set[int] = set()
            
            # Yield estates from first page
            for estate in first_page.estates:
                if estate.hash_id not in seen_ids:
                    seen_ids.add(estate.hash_id)
                    yield estate
            
            # Fetch remaining pages
            for page in range(2, total_pages + 1):
                response = await self.fetch_page(
                    client,
                    page=page,
                    category_main=category_main,
                    category_type=category_type,
                    locality_params=locality_params,
                    advert_age_to=advert_age_to,
                )
                
                if not response:
                    logger.warning(f"Failed to fetch page {page}, skipping")
                    continue
                
                for estate in response.estates:
                    if estate.hash_id not in seen_ids:
                        seen_ids.add(estate.hash_id)
                        yield estate
                
                if page % 10 == 0:
                    logger.info(f"Progress: {page}/{total_pages} pages scraped")
            
            dupes_skipped = (first_page.result_size or 0) - len(seen_ids)
            if dupes_skipped > 0:
                logger.info(f"Deduplicated {dupes_skipped} duplicate listings across pages")
    
    async def fetch_listing_details(
        self,
        client: httpx.AsyncClient,
        hash_id: int,
    ) -> dict | None:
        """
        Fetch full details for a single listing.
        
        The list endpoint doesn't include description, so we need
        to fetch individual listings for full data.
        
        Raises:
            httpx.HTTPStatusError: If the API returns 410 Gone (listing sold/removed).
                Caller should handle this to mark the listing appropriately.
        """
        await self._rate_limit_wait()
        
        url = f"{self.BASE_URL}/{hash_id}"
        
        try:
            response = await client.get(
                url,
                headers=self._get_headers(),
                timeout=30.0,
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 410:
                logger.info(f"Listing {hash_id} is gone (410) - sold or removed")
                raise  # Let caller handle sold/removed listings
            logger.error(f"HTTP error fetching listing {hash_id}: {e.response.status_code}")
            return None
        except Exception as e:
            logger.error(f"Error fetching listing {hash_id}: {e}")
            return None


def extract_area_from_title(title: str) -> Decimal | None:
    """
    Extract area in m² from listing title.
    
    Examples:
        "Prodej bytu 3+kk 77 m²" -> 77
        "Prodej bytu 3+1 107 m²" -> 107  (NOT 1107!)
        "Prodej rodinného domu 380 m², pozemek 978 m²" -> 380
        "Prodej bytu 2+kk 1 234 m²" -> 1234 (with space in number)
    
    The tricky part: "3+1 107 m²" - the "1" from apartment type must not
    be concatenated with "107" to make "1107".
    """
    # Strategy: First find the apartment type pattern and skip past it
    # Apartment types: 1+kk, 2+1, 3+kk, 4+1, garsoniéra, etc.
    
    # Remove apartment type pattern to avoid confusion
    # This handles "3+1 107 m²" -> " 107 m²"
    cleaned = re.sub(r"\d\+(?:kk|\d)", "", title)
    
    # Now try to extract area from cleaned string
    # Match numbers with optional space separator (for "1 234 m²")
    match = re.search(r"(\d+(?:[\s\xa0]\d{3})?)\s*m[²2]", cleaned)
    if match:
        num_str = match.group(1).replace(" ", "").replace("\xa0", "")
        try:
            area = Decimal(num_str)
            # Sanity check - apartments rarely > 500m², houses rarely > 2000m²
            if area <= 5000:
                return area
        except Exception:
            pass
    
    return None


def parse_city_from_locality(locality: str) -> tuple[str | None, str | None]:
    """
    Parse city and district from locality string.
    
    Examples:
        "Marie Podvalové, Praha - Čakovice" -> ("Praha", "Čakovice")
        "Brno - Žabovřesky" -> ("Brno", "Žabovřesky")
        "Severovýchod, Zábřeh" -> ("Zábřeh", "Severovýchod")
    """
    if not locality:
        return None, None
    
    # Common patterns:
    # "Street, City - District"
    # "City - District"
    # "District, City"
    
    parts = locality.split(",")
    
    # Look for "City - District" pattern
    for part in parts:
        if " - " in part:
            city_district = part.strip().split(" - ")
            if len(city_district) >= 2:
                return city_district[0].strip(), city_district[1].strip()
    
    # If multiple parts, last part is usually the city
    if len(parts) >= 2:
        return parts[-1].strip(), parts[0].strip()
    
    # Single part - assume it's the city
    return parts[0].strip(), None
