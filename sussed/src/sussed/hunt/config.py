"""
Search configuration for autonomous hunt mode 🎯

Define what you're looking for and let the hunt runner score it.
"""

from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator


class OutputMode(str, Enum):
    """What kind of results do you want?"""

    BEST = "best"  # Top N highest scored
    TRASH = "trash"  # Bottom N (scams, overpriced shit)
    GEMS = "gems"  # Only the 9999 absolute gems
    SUS = "sus"  # Only sus/scam listings (-1)
    ALL = "all"  # Everything scored, sorted by score


class SearchCriteria(BaseModel):
    """
    What are you looking for?

    All fields are optional - only specify what matters to you.
    """

    # Location
    city: str = Field(default="Brno", description="City to search in")
    districts: list[str] | None = Field(default=None, description="Specific districts to include")
    exclude_districts: list[str] | None = Field(default=None, description="Districts to avoid")

    # Property type
    apartment_types: list[str] | None = Field(
        default=None,
        description="Types like ['2+kk', '2+1', '3+kk'] - None = any",
        examples=[["2+kk", "2+1"], ["3+kk", "3+1", "4+kk"]],
    )
    property_type: str = Field(default="apartment", description="apartment or house")
    listing_type: str = Field(default="sale", description="sale or rent")

    # Price
    min_price: int | None = Field(default=None, description="Minimum price in CZK")
    max_price: int | None = Field(default=None, description="Maximum price in CZK")
    max_price_per_m2: int | None = Field(default=None, description="Max price per m² (key metric!)")

    # Size
    min_area_m2: float | None = Field(default=None, description="Minimum usable area")
    max_area_m2: float | None = Field(default=None, description="Maximum area (for budget)")

    # Floor
    min_floor: int | None = Field(default=None, description="Minimum floor (0 = ground floor)")
    max_floor: int | None = Field(
        default=None, description="Max floor (avoid top floor = leaky roof)"
    )
    avoid_ground_floor: bool = Field(default=False, description="Skip ground floor listings")
    avoid_top_floor: bool = Field(default=False, description="Skip top floor listings")

    # Must-have features
    require_parking: bool = Field(default=False, description="Must have parking/garage")
    require_balcony: bool = Field(default=False, description="Must have balcony/loggia/terrace")
    require_cellar: bool = Field(default=False, description="Must have cellar/storage")
    require_elevator: bool = Field(default=False, description="Building must have elevator")

    # Red flags to auto-reject
    reject_panel_building: bool = Field(default=False, description="No commie blocks (panelák)")
    reject_ground_floor: bool = Field(
        default=False, description="No ground floor (louder, less safe)"
    )
    min_photos: int = Field(default=3, description="Min photos (few photos = hiding something)")
    require_floor_plan: bool = Field(default=False, description="Must have floor plan")

    # Description keyword filtering (hard reject)
    exclude_description_keywords: str | None = Field(
        default=None,
        description="Comma-separated keywords to auto-reject listings (case-insensitive). "
        "If a description contains ANY of these, listing is killed. "
        "Great for filtering out auctions, foreclosures, etc.",
        examples=["dražba, exekuce, aukce, spoluvlastnick"],
    )

    @field_validator("exclude_description_keywords", mode="before")
    @classmethod
    def parse_exclude_keywords(cls, v: Any) -> str | None:
        """Accept list[str] from code and coerce to comma string."""
        if isinstance(v, list):
            return ", ".join(v)
        return v

    def get_exclude_keywords(self) -> list[str]:
        """Split comma string into lowercase keyword list."""
        if not self.exclude_description_keywords:
            return []
        return [
            k.strip().lower() for k in self.exclude_description_keywords.split(",") if k.strip()
        ]

    # Age filtering
    max_listing_age: str | int | None = Field(
        default=None,
        description="Only listings from: day, week, month, or number of days (e.g. 14)",
    )


class ScoringWeights(BaseModel):
    """
    Customize what matters most in scoring.

    Higher weight = more important in final score.
    Default weights are balanced for typical buyer.
    """

    price_vs_market: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        description="How much does price vs market average matter",
    )
    location_quality: float = Field(
        default=0.25,
        ge=0.0,
        le=1.0,
        description="How much does location/district matter",
    )
    features_match: float = Field(
        default=0.2,
        ge=0.0,
        le=1.0,
        description="How much does having required features matter",
    )
    listing_quality: float = Field(
        default=0.15,
        ge=0.0,
        le=1.0,
        description="Photos, floor plan, description quality",
    )
    age_condition: float = Field(
        default=0.1,
        ge=0.0,
        le=1.0,
        description="Building age and condition",
    )

    # Special scoring modifiers
    bonus_new_building: int = Field(
        default=100,
        description="Bonus points for new construction",
    )
    bonus_reconstruction: int = Field(
        default=50,
        description="Bonus for recently reconstructed",
    )
    bonus_very_good_condition: int = Field(
        default=40,
        description="Bonus when building_condition is 'Velmi dobrý' or similar high-quality state",
    )
    penalty_no_parking: int = Field(
        default=-50,
        description="Penalty when parking is required but missing",
    )
    penalty_panel: int = Field(
        default=-100,
        description="Penalty for panel buildings (if user dislikes them)",
    )

    # Custom description keyword scoring
    penalize_description_keywords: dict[str, int] | None = Field(
        default=None,
        description="Additional keywords to penalize in descriptions with custom penalty values (negative ints). "
        "Merged with built-in sketchy keywords.",
        examples=[{"dražba": -80, "exekuce": -100}],
    )
    bonus_description_keywords: dict[str, int] | None = Field(
        default=None,
        description="Additional keywords to reward in descriptions with custom bonus values (positive ints). "
        "Merged with built-in good keywords.",
        examples=[{"výhled na park": 30, "garáž v ceně": 40}],
    )


class OutputConfig(BaseModel):
    """How do you want the results?"""

    mode: OutputMode = Field(
        default=OutputMode.BEST,
        description="What to show: best, trash, gems, sus, or all",
    )
    limit: int = Field(
        default=10,
        ge=1,
        le=100,
        description="How many results to show",
    )
    include_analysis: bool = Field(
        default=True,
        description="Include AI analysis in output",
    )
    include_description: bool = Field(
        default=False,
        description="Include full description (verbose)",
    )
    format: str = Field(
        default="table",
        description="Output format: table, json, or markdown",
    )
    save_to_file: str | None = Field(
        default=None,
        description="Save output to this file path",
    )


class AgentConfig(BaseModel):
    """
    Agent behavior configuration.

    Controls how autonomous the agent is.
    """

    # Processing limits
    max_listings_to_process: int = Field(
        default=50,
        ge=1,
        description="Max listings to analyze in one run",
    )
    fetch_descriptions: bool = Field(
        default=True,
        description="Fetch missing descriptions from API (slower but better analysis)",
    )
    enrich_top_n: int = Field(
        default=5,
        ge=1,
        le=2000,
        description="How many top-scored candidates to fetch full descriptions for (controls API load).",
    )
    skip_already_scored: bool = Field(
        default=True,
        description="Skip listings that already have a score",
    )

    # LLM settings - THIS IS WHERE THE REAL AI HAPPENS! 🧠
    use_llm: bool = Field(
        default=True,
        description="Use LLM for description analysis (requires API key)",
    )
    llm_provider: str = Field(
        default="anthropic",
        description="LLM provider: 'anthropic' (Claude) or 'openai' (GPT)",
    )
    llm_model: str | None = Field(
        default=None,
        description="Specific model ID (None = use default: claude-haiku-4-5 or gpt-4o-mini)",
    )
    llm_analyze_top_n: int = Field(
        default=5,
        ge=1,
        le=50,
        description="How many top listings to analyze with LLM (API costs money!)",
    )

    # Price handling
    treat_1kc_as_poa: bool = Field(
        default=True,
        description="Treat 1 Kč price as 'Price On Request' - don't penalize, analyze description instead",
    )
    poa_evaluation_mode: str = Field(
        default="description_only",
        description="How to evaluate POA listings: description_only, skip, or estimate",
    )

    # Scrape settings (if agent should scrape first)
    auto_scrape: bool = Field(
        default=False,
        description="Automatically scrape before processing",
    )
    scrape_max_pages: int = Field(
        default=5,
        description="Max pages to scrape if auto_scrape is enabled",
    )


class SearchConfig(BaseModel):
    """
    The full search configuration - your dream home spec 🏠

    Save this as YAML and run: sussed hunt --config my_search.yaml
    """

    name: str = Field(
        default="My Dream Home Search",
        description="Give your search a name",
    )
    description: str | None = Field(
        default=None,
        description="Optional notes about this search",
    )

    criteria: SearchCriteria = Field(default_factory=SearchCriteria)
    scoring: ScoringWeights = Field(default_factory=ScoringWeights)
    output: OutputConfig = Field(default_factory=OutputConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)

    # Custom notes for the AI agent
    notes_for_agent: str | None = Field(
        default=None,
        description="Free-form notes/preferences for the AI to consider",
        examples=[
            "I work from home so need a quiet area. "
            "Prefer south-facing windows. "
            "Don't mind older buildings if recently renovated."
        ],
    )

    # Districts ranking (for location scoring)
    preferred_districts: list[str] | None = Field(
        default=None,
        description="Ranked list of preferred districts (first = best)",
    )
    avoid_districts: list[str] | None = Field(
        default=None,
        description="Districts to avoid or heavily penalize",
    )

    # Known problematic areas in Brno (streets/districts with issues)
    # These are checked against both district AND address fields
    known_bad_locations: list[str] = Field(
        default=[
            "Cejl",  # Sketchy area, high crime
            "Zábrdovice",  # Problematic neighborhood
            "Bratislavská",  # Noisy main road, sketchy
            "Francouzská",  # Near Cejl, similar issues
            "Vlhká",  # Name says it all ("Damp street")
            "Koliště",  # Busy ring road
        ],
        description="Known problematic streets/districts to penalize",
    )

    @classmethod
    def from_yaml(cls, path: str | Path) -> SearchConfig:
        """Load config from YAML file."""
        with Path(path).open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return cls.model_validate(data)

    def to_yaml(self, path: str | Path | None = None) -> str:
        """Export config to YAML string, optionally save to file."""
        # Convert to dict, excluding None values for cleaner YAML
        data = self.model_dump(exclude_none=True, mode="json")
        yaml_str = yaml.dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False)

        if path:
            with Path(path).open("w", encoding="utf-8") as f:
                f.write(yaml_str)

        return yaml_str

    @classmethod
    def example(cls) -> SearchConfig:
        """Create an example config with reasonable defaults for Brno."""
        return cls(
            name="Brno 2+kk/2+1 Hunt",
            description="Looking for a nice 2-room apartment in Brno under 6M",
            criteria=SearchCriteria(
                city="Brno",
                apartment_types=["2+kk", "2+1"],
                max_price=6_000_000,
                min_area_m2=45,
                max_price_per_m2=130_000,
                require_balcony=True,
                avoid_ground_floor=True,
                min_photos=5,
                exclude_description_keywords="dražba, exekuce, aukce",
            ),
            scoring=ScoringWeights(
                price_vs_market=0.35,
                location_quality=0.25,
            ),
            output=OutputConfig(
                mode=OutputMode.BEST,
                limit=10,
                include_analysis=True,
            ),
            agent=AgentConfig(
                max_listings_to_process=30,
                fetch_descriptions=True,
                treat_1kc_as_poa=True,
            ),
            notes_for_agent=(
                "I prefer quiet neighborhoods, ideally close to a park or green space. "
                "I work from home so natural light is important. "
                "Don't mind an older building if it's been recently renovated."
            ),
            preferred_districts=[
                "Královo Pole",
                "Žabovřesky",
                "Kohoutovice",
                "Líšeň",
                "Bystrc",
            ],
            avoid_districts=[
                "Cejl",  # Loud and sketchy
            ],
        )


def generate_example_config(path: str | Path = "search_config.yaml") -> str:
    """Generate an example config file with comments."""
    example = SearchConfig.example()
    yaml_content = example.to_yaml()

    # Add header comment
    header = """# sussed Search Configuration 🏠
#
# This file defines what you're looking for in an apartment.
# Customize it and run: sussed hunt --config search_config.yaml
#
# Pro tips:
# - 1 Kč price = "Price on request" - we handle this specially!
# - max_price_per_m2 is often better than max_price for finding deals
# - The agent will explain its scoring in the results
#

"""
    full_content = header + yaml_content

    with Path(path).open("w", encoding="utf-8") as f:
        f.write(full_content)

    return full_content
