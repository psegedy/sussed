"""
sreality.cz scraper 🕷️

Async scraper for sreality's public v1 JSON API. No auth required:
`https://www.sreality.cz/api/v1/estates/search` and `/api/v1/estates/{hash_id}`
both return JSON to unauthenticated requests. v1 search filters use canonical
snake_case query params; camelCase React Query keys are client-side internals
and are ignored by the API. The older `/api/cs/v2/estates` endpoint was
decommissioned upstream around 2026-05; don't add a v2 fallback.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, ClassVar
from urllib.parse import urlparse

import httpx
from loguru import logger

from sussed.config import get_settings
from sussed.models.sreality import (
    SREALITY_COTTAGE_SUBCATEGORY_CODES,
    SREALITY_GARDEN_SUBCATEGORY_CODES,
    SrealityV1Detail,
    SrealityV1DetailResponse,
    SrealityV1Estate,
    SrealityV1NamedValue,
    SrealityV1SearchResponse,
    get_apartment_type,
)

if TYPE_CHECKING:
    from sussed.db.models import Listing

SearchParam = tuple[str, str | int]
CategorySubFilter = tuple[int, ...]


class SrealityScraper:
    """
    Scraper for sreality.cz using their v1 JSON API.

    Usage:
        scraper = SrealityScraper()
        async for listing in scraper.scrape_city("brno"):
            logger.info(listing)
    """

    BASE_URL = "https://www.sreality.cz/api/v1/estates/search"
    DETAIL_BASE_URL = "https://www.sreality.cz/api/v1/estates"
    MAX_LIMIT: ClassVar[int] = 100

    # Region IDs (locality_region_id) - whole kraj
    REGION_IDS: ClassVar[dict[str, int]] = {
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
    DISTRICT_IDS: ClassVar[dict[str, int]] = {
        "brno-mesto": 72,
        "brno-venkov": 73,
    }

    # City to locality mapping: (region_key, district_key | None)
    CITY_TO_LOCALITY: ClassVar[dict[str, tuple[str, str | None]]] = {
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

    # v1 supports the same advert_age_to day-window semantics as old v2.
    ADVERT_AGE_OPTIONS: ClassVar[dict[str, int]] = {
        "day": 2,
        "week": 8,
        "month": 31,
    }
    PROPERTY_FILTERS: ClassVar[dict[str, tuple[int, CategorySubFilter | None]]] = {
        "apartment": (1, None),
        "house": (2, None),
        "cottage": (2, SREALITY_COTTAGE_SUBCATEGORY_CODES),
        "garden": (3, SREALITY_GARDEN_SUBCATEGORY_CODES),
    }

    def __init__(self) -> None:
        settings = get_settings()
        self.rate_limit = settings.scrape_rate_limit
        self.user_agent = settings.user_agent
        self._last_request_time: float = 0

    async def _rate_limit_wait(self) -> None:
        """Wait to respect rate limiting."""
        if self.rate_limit <= 0:
            return

        now = asyncio.get_running_loop().time()
        elapsed = now - self._last_request_time
        wait_time = (1.0 / self.rate_limit) - elapsed

        if wait_time > 0:
            await asyncio.sleep(wait_time)

        self._last_request_time = asyncio.get_running_loop().time()

    def _get_headers(self) -> dict[str, str]:
        """Get headers for API requests."""
        return {
            "User-Agent": self.user_agent,
            "Accept": "application/json",
            "Accept-Language": "cs,en;q=0.9",
            "Referer": "https://www.sreality.cz/",
        }

    def _get_locality_params(self, city: str) -> list[SearchParam]:
        """Get v1 locality API params for a city."""
        city_lower = city.lower().strip()

        mapping = self.CITY_TO_LOCALITY.get(city_lower)
        if mapping:
            region_key, district_key = mapping
            region_id = self.REGION_IDS.get(region_key)
            params: list[SearchParam] = []
            if region_id:
                params.append(("locality_region_id", region_id))
            if district_key and district_key in self.DISTRICT_IDS:
                district_id = self.DISTRICT_IDS[district_key]
                logger.debug(f"Using district filter: {district_key} (ID {district_id})")
                params.append(("locality_district_id", district_id))
            if params:
                return params

        if city_lower in self.REGION_IDS:
            return [("locality_region_id", self.REGION_IDS[city_lower])]

        logger.warning(f"Unknown city/region: {city}")
        return []

    def _get_property_filter(self, property_type: str) -> tuple[int, CategorySubFilter | None]:
        """Return v1 main category and optional subcategory filter for a property type."""
        property_type_normalized = property_type.lower().strip()
        try:
            return self.PROPERTY_FILTERS[property_type_normalized]
        except KeyError as err:
            valid_types = ", ".join(sorted(self.PROPERTY_FILTERS))
            raise ValueError(f"Unsupported property type: {property_type}. Use one of: {valid_types}") from err

    def _build_search_params(
        self,
        *,
        offset: int,
        limit: int,
        category_main: int,
        category_type: int,
        category_sub: CategorySubFilter | None,
        locality_params: list[SearchParam] | None,
        advert_age_to: int | None,
    ) -> list[SearchParam]:
        """Build canonical v1 search params using snake_case names."""
        bounded_limit = min(max(limit, 1), self.MAX_LIMIT)
        params: list[SearchParam] = [
            ("category_main_cb", category_main),
            ("category_type_cb", category_type),
            ("locality_country_id", 112),
        ]
        if category_sub:
            # v1 honors comma-separated subcategory codes; repeated params keep only the first value.
            params.append(("category_sub_cb", ",".join(str(code) for code in category_sub)))
        if locality_params:
            params.extend(locality_params)

        params.extend(
            [
                ("limit", bounded_limit),
                ("offset", max(offset, 0)),
            ]
        )
        if advert_age_to is not None:
            params.append(("advert_age_to", advert_age_to))
        params.append(("lang", "cs"))
        return params

    async def fetch_page(
        self,
        client: httpx.AsyncClient,
        offset: int = 0,
        limit: int = MAX_LIMIT,
        category_main: int = 1,
        category_type: int = 1,
        category_sub: CategorySubFilter | None = None,
        locality_params: list[SearchParam] | None = None,
        advert_age_to: int | None = None,
    ) -> SrealityV1SearchResponse | None:
        """Fetch a single offset-based page of listings, optionally filtered by advert age."""
        await self._rate_limit_wait()

        params = self._build_search_params(
            offset=offset,
            limit=limit,
            category_main=category_main,
            category_type=category_type,
            category_sub=category_sub,
            locality_params=locality_params,
            advert_age_to=advert_age_to,
        )

        try:
            logger.debug(f"Fetching offset {offset} with params: {params}")
            response = await client.get(
                self.BASE_URL,
                params=params,
                headers=self._get_headers(),
                timeout=30.0,
            )
            response.raise_for_status()
            return SrealityV1SearchResponse.model_validate(response.json())

        except httpx.HTTPStatusError as err:
            logger.error(f"HTTP error fetching offset {offset}: {err.response.status_code}")
            return None
        except httpx.RequestError as err:
            logger.error(f"Request error fetching offset {offset}: {err}")
            return None
        except Exception as err:
            logger.error(f"Error parsing offset {offset}: {err}")
            return None

    async def scrape(
        self,
        city: str | None = None,
        listing_type: str = "sale",
        property_type: str = "apartment",
        max_pages: int | None = None,
        max_age: str | int | None = None,
    ):
        """Scrape listings from sreality."""
        category_type = 1 if listing_type == "sale" else 2
        property_type = property_type.lower().strip()
        category_main, category_sub = self._get_property_filter(property_type)
        locality_params = self._get_locality_params(city) if city else None

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
            first_page = await self.fetch_page(
                client,
                offset=0,
                limit=self.MAX_LIMIT,
                category_main=category_main,
                category_type=category_type,
                category_sub=category_sub,
                locality_params=locality_params,
                advert_age_to=advert_age_to,
            )

            if not first_page:
                logger.error("Failed to fetch first page")
                return

            effective_limit = first_page.pagination.limit
            if effective_limit <= 0:
                logger.error("Search response did not include a valid pagination limit")
                return

            total_pages = first_page.pagination.total_pages
            pages_to_scrape = min(total_pages, max_pages) if max_pages else total_pages

            logger.info(
                f"Found {first_page.pagination.total} listings across "
                f"{total_pages} pages. Scraping {pages_to_scrape} pages."
            )

            seen_ids: set[int] = set()
            fetched_count = 0

            for estate in first_page.results:
                fetched_count += 1
                if estate.hash_id not in seen_ids:
                    seen_ids.add(estate.hash_id)
                    yield estate

            for page_number in range(2, pages_to_scrape + 1):
                offset = (page_number - 1) * effective_limit
                response = await self.fetch_page(
                    client,
                    offset=offset,
                    limit=effective_limit,
                    category_main=category_main,
                    category_type=category_type,
                    category_sub=category_sub,
                    locality_params=locality_params,
                    advert_age_to=advert_age_to,
                )

                if not response:
                    logger.warning(f"Failed to fetch offset {offset}, skipping")
                    continue

                for estate in response.results:
                    fetched_count += 1
                    if estate.hash_id not in seen_ids:
                        seen_ids.add(estate.hash_id)
                        yield estate

                if page_number % 10 == 0:
                    logger.info(f"Progress: {page_number}/{pages_to_scrape} pages scraped")

            dupes_skipped = fetched_count - len(seen_ids)
            if fetched_count > 0 and dupes_skipped > 0 and dupes_skipped / fetched_count > 0.05:
                logger.debug(f"Deduplicated {dupes_skipped} duplicate listings across offsets")

    async def fetch_listing_details(
        self,
        client: httpx.AsyncClient,
        hash_id: int,
        raise_on_gone: bool = False,
    ) -> SrealityV1Detail | None:
        """
        Fetch full details for a single listing.

        Args:
            client: Async HTTP client to use for the request.
            hash_id: The sreality listing hash ID.
            raise_on_gone: If True, raise on 404 in addition to the always-raised 410.

        Raises:
            httpx.HTTPStatusError: If the API returns 410 Gone, or 404 when raise_on_gone=True.
        """
        await self._rate_limit_wait()

        url = f"{self.DETAIL_BASE_URL}/{hash_id}"

        try:
            response = await client.get(
                url,
                headers=self._get_headers(),
                timeout=30.0,
            )
            response.raise_for_status()
            return SrealityV1DetailResponse.model_validate(response.json()).result
        except httpx.HTTPStatusError as err:
            status = err.response.status_code
            if status == 410 or (raise_on_gone and status == 404):
                logger.info(f"Listing {hash_id} is gone ({status}) - sold or removed")
                raise
            logger.error(f"HTTP error fetching listing {hash_id}: {status}")
            return None
        except Exception as err:
            logger.error(f"Error fetching listing {hash_id}: {err}")
            return None


def normalize_sreality_image_url(url: str) -> str | None:
    """Return a fully-qualified, directly downloadable sreality CDN image URL."""
    normalized = f"https:{url}" if url.startswith("//") else url
    parsed = urlparse(normalized)
    hostname = parsed.hostname or ""
    if parsed.scheme not in {"http", "https"} or not re.fullmatch(
        r"d\d+-[a-z]\.sdn\.cz", hostname
    ):
        logger.warning(f"Skipping unexpected sreality image host: {hostname or '<missing>'}")
        return None
    if "?" not in normalized:
        return f"{normalized}?fl=res,1200,1200,1|shr,,20|jpg,80"
    return normalized


def _named_int(named_value: SrealityV1NamedValue | None) -> int | None:
    """Safely extract an integer from a named value."""
    return named_value.int_value if named_value else None


def _build_listing_url(estate: SrealityV1Estate | SrealityV1Detail) -> str:
    """Build a human-clickable sreality detail URL from v1 listing data."""
    category_type = _named_int(estate.category_type_cb)
    category_main = _named_int(estate.category_main_cb)
    category_sub = _named_int(estate.category_sub_cb)

    type_seo = "prodej" if category_type == 1 else "pronajem"
    cat_seo = _category_seo_slug(category_main, category_sub)
    apt = _subtype_seo_slug(category_main, category_sub)
    slug_parts = [
        estate.locality.city_seo_name,
        estate.locality.citypart_seo_name,
        estate.locality.street_seo_name,
    ]
    locality_slug = "-".join(part for part in slug_parts if part) or "cz"
    return f"https://www.sreality.cz/detail/{type_seo}/{cat_seo}/{apt}/{locality_slug}/{estate.hash_id}"


def _category_seo_slug(category_main: int | None, category_sub: int | None) -> str:
    """Return the top-level URL slug for a v1 property category.

    Sreality URLs are ``/detail/<type>/<category>/<subtype>/...``. The category
    slug is the BROAD bucket (byt/dum/pozemek/komercni). The narrow type
    (chata, chalupa, zahrada, rodinny-dum, etc.) belongs in the SUBTYPE slot,
    handled by ``_subtype_seo_slug``. So a cottage URL is ``dum/chata/...``,
    not ``chata/chata/...``, and a garden URL is ``pozemek/zahrada/...``,
    not ``zahrada/zahrada/...``.
    """
    del category_sub  # subtype handled separately
    if category_main == 1:
        return "byt"
    if category_main == 2:
        return "dum"
    if category_main == 3:
        return "pozemek"
    if category_main == 4:
        return "komercni"
    return "nemovitost"


def _subtype_seo_slug(category_main: int | None, category_sub: int | None) -> str:
    """Return the detail URL subtype slug."""
    if category_main == 1:
        return get_apartment_type(category_sub) or "x"
    subtype_map = {
        33: "chata",
        34: "garaz",
        35: "pamatka-jine",
        37: "rodinny-dum",
        39: "vila",
        40: "na-klic",
        43: "chalupa",
        44: "zemedelska-usedlost",
        18: "komercni",
        19: "bydleni",
        20: "pole",
        21: "les",
        22: "louka",
        23: "zahrada",
        24: "ostatni",
    }
    return subtype_map.get(category_sub, "x")


def parse_v1_source_date(detail: SrealityV1Detail) -> datetime | None:
    """Parse v1 source publish/update date, preferring ``since`` over ``edited``."""
    date_value = detail.since or detail.edited
    if not date_value:
        return None
    try:
        return datetime.strptime(date_value, "%Y-%m-%d")
    except ValueError:
        logger.warning(f"Unexpected sreality source date format: {date_value}")
        return None


def _named_value_is_positive(named_value: SrealityV1NamedValue | None) -> bool:
    """Return True when a named value represents a positive/yes value."""
    value = _named_int(named_value)
    return bool(value and value > 0)


def _named_value_names(named_values: list[SrealityV1NamedValue] | None) -> list[str]:
    """Extract non-empty names from v1 list-valued detail fields."""
    if not named_values:
        return []
    return [value.name for value in named_values if value.name]


def _premise_name(premise: dict[str, Any] | None) -> str | None:
    """Extract a human-readable agency/person name from a v1 premise dict."""
    if not premise:
        return None
    name = premise.get("name")
    if isinstance(name, str) and name:
        return name
    company = premise.get("company")
    if isinstance(company, str) and company:
        return company
    if isinstance(company, dict):
        for key in ("name", "company_name", "companyName"):
            value = company.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def set_features_from_v1_detail(listing: Listing, detail: SrealityV1Detail) -> None:
    """Populate listing fields that only exist on the v1 detail endpoint."""
    building_type = detail.building_type.name if detail.building_type else None
    building_condition = detail.building_condition.name if detail.building_condition else None

    features: dict[str, Any] = {
        "garage": bool(detail.garage),
        "parking_lots": bool(detail.parking_lots),
        "balcony": bool(detail.balcony),
        "loggia": bool(detail.loggia),
        "terrace": bool(detail.terrace),
        "cellar": bool(detail.cellar),
        "garret": bool(detail.garret),
        "low_energy": bool(detail.low_energy),
        "panorama": bool(detail.panorama),
        "basin": bool(detail.basin),
    }
    features["parking"] = features["garage"] or features["parking_lots"]
    features["elevator"] = _named_value_is_positive(detail.elevator)
    features["furnished"] = detail.furnished.value if detail.furnished else None
    features["ownership"] = detail.ownership.name if detail.ownership else None
    electricity_sources = _named_value_names(detail.electricity_set)
    water_sources = _named_value_names(detail.water_set)
    sewage_sources = _named_value_names(detail.waste_set)
    features["electricity"] = bool(electricity_sources)
    features["electricity_sources"] = electricity_sources
    features["water"] = bool(water_sources)
    features["water_sources"] = water_sources
    features["sewage"] = bool(sewage_sources)
    features["sewage_sources"] = sewage_sources
    features["building_condition"] = building_condition
    features["building_type"] = building_type
    features["brick"] = building_type == "Cihlová"
    features["panel"] = building_type == "Panelová"
    features["reconstructed"] = building_condition in {"Po rekonstrukci", "V rekonstrukci"}
    features["new_building"] = building_condition == "Novostavba"

    listing.features = features

    if detail.floor_number is not None:
        listing.floor = detail.floor_number
    if detail.floors is not None:
        listing.total_floors = detail.floors
    if detail.usable_area is not None and (listing.area_m2 is None or listing.area_m2 == 0):
        listing.area_m2 = Decimal(str(detail.usable_area))

    agency_name = _premise_name(detail.premise)
    if agency_name:
        listing.agency_name = agency_name


def extract_area_from_title(title: str) -> Decimal | None:
    """
    Extract area in m² from listing title.

    Examples:
        "Prodej bytu 3+kk 77 m²" -> 77
        "Prodej bytu 3+1 107 m²" -> 107  (NOT 1107!)
        "Prodej rodinného domu 380 m², pozemek 978 m²" -> 380
        "Prodej bytu 2+kk 1 234 m²" -> 1234 (with space in number)
    """
    cleaned = re.sub(r"\d\+(?:kk|\d)", "", title)

    match = re.search(r"(\d+(?:[\s\xa0]\d{3})?)\s*m[²2]", cleaned)
    if match:
        num_str = match.group(1).replace(" ", "").replace("\xa0", "")
        try:
            area = Decimal(num_str)
            if area <= 5000:
                return area
        except Exception:
            pass

    return None


def parse_city_from_locality(locality: str) -> tuple[str | None, str | None]:
    """Parse city and district from the legacy v2 locality string."""
    if not locality:
        return None, None

    parts = locality.split(",")

    for part in parts:
        if " - " in part:
            city_district = part.strip().split(" - ")
            if len(city_district) >= 2:
                return city_district[0].strip(), city_district[1].strip()

    if len(parts) >= 2:
        return parts[-1].strip(), parts[0].strip()

    return parts[0].strip(), None
