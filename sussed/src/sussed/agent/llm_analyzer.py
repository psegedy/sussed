"""
LLM-Powered Description Analyzer 🧠

THIS is where the actual AI happens! No more keyword dictionary bullshit.
We use agno + Claude/OpenAI to actually understand Czech real estate descriptions.

The LLM can:
- Understand context and nuance in Czech descriptions
- Detect subtle red flags humans would catch
- Extract hidden costs (parking, fees, etc.)
- Identify gems that keyword matching would miss
"""

import asyncio
from dataclasses import dataclass
from typing import Any

from loguru import logger
from pydantic import BaseModel, Field

from sussed.config import get_settings


class ListingAnalysis(BaseModel):
    """Structured output from LLM analysis."""
    
    # Overall assessment
    score_adjustment: int = Field(
        description="Points to add/subtract from base score (-200 to +200)",
        ge=-200,
        le=200,
    )
    confidence: float = Field(
        description="How confident the LLM is in this analysis (0.0-1.0)",
        ge=0.0,
        le=1.0,
    )
    
    # Key findings
    red_flags: list[str] = Field(
        default_factory=list,
        description="Serious concerns found in the description",
    )
    yellow_flags: list[str] = Field(
        default_factory=list,
        description="Minor concerns or things to verify",
    )
    highlights: list[str] = Field(
        default_factory=list,
        description="Positive aspects found in the description",
    )
    
    # Extracted info
    hidden_costs: dict[str, int | None] = Field(
        default_factory=dict,
        description="Hidden costs extracted (parking, fees, etc.) in CZK",
    )
    true_usable_area_m2: float | None = Field(
        default=None,
        description="Estimated true usable living area if different from advertised",
    )
    renovation_needed: bool = Field(
        default=False,
        description="Whether the listing needs renovation",
    )
    
    # Summary
    one_liner: str = Field(
        description="One sentence summary for quick scanning",
    )
    recommendation: str = Field(
        description="BUY, CONSIDER, SKIP, or AVOID",
    )


# System prompt for the real estate analyzer agent
ANALYZER_SYSTEM_PROMPT = '''You are an expert Czech real estate analyst. Your job is to analyze apartment listing descriptions and extract key information.

## Your Analysis Should Cover:

### Red Flags to Watch For:
- "investiční příležitost" / "vhodné k investici" = usually overpriced or problematic
- "k rekonstrukci" / "před rekonstrukcí" = needs work (big money!)
- "potenciál" / "možnosti" = euphemism for "needs everything"
- "suterén" / "suterénní" = basement apartment (lower value, dampness issues)
- "rušná ulice" / "frekventovaná" = noisy location
- Vague descriptions with lots of marketing speak but no specifics
- Missing key info (no floor mentioned, no building age, etc.)
- "pouze hotovost" = cash only (sus AF)
- Price seems way too good = possible scam or hidden issues

### Good Signs:
- "po rekonstrukci" / "kompletně zrekonstruováno" = recently renovated
- Specific details about materials, year of renovation
- Clear energy rating (PENB A/B/C is good)
- "cihlový dům" = brick building (better than panel)
- "nízké náklady" / "nízké poplatky" with actual numbers
- Detailed description of layout and features
- "tichá lokalita" / "klidné prostředí" = quiet area

### Hidden Costs to Extract:
- Parking (often 300k-800k CZK extra in Brno!)
- Cellar/storage fees
- Monthly service charges (fond oprav)
- Energy costs if mentioned
- Any "příplatek" or additional fees

### Area Calculations:
- Czech listings often include balcony, cellar, terrace in m²
- True "usable living area" = total minus these
- Note if the listing inflates area with non-living space

## Output Format:
Provide your analysis as structured JSON matching the ListingAnalysis schema.
Be concise but thorough. Focus on actionable insights.

## Language Note:
The descriptions are in Czech. Analyze them in their original language to catch nuances.
Your output should be in English for consistency.
'''


@dataclass
class LLMAnalyzer:
    """
    LLM-powered listing analyzer using agno framework.
    
    Supports both Claude (Anthropic) and GPT (OpenAI) models.
    Falls back gracefully if no API key is configured.
    """
    
    model_provider: str = "anthropic"  # or "openai"
    model_id: str | None = None
    
    def __post_init__(self):
        """Initialize the agno agent."""
        self._agent = None
        self._available = False
        self._init_error: str | None = None
        
        try:
            self._setup_agent()
        except Exception as e:
            self._init_error = str(e)
            logger.warning(f"LLM analyzer not available: {e}")
    
    def _setup_agent(self):
        """Set up the agno agent with appropriate model."""
        settings = get_settings()
        
        # Determine which model to use
        if self.model_provider == "anthropic":
            if not settings.anthropic_api_key:
                raise ValueError("ANTHROPIC_API_KEY not set. Set it in .env or environment.")
            
            from agno.agent import Agent
            from agno.models.anthropic import Claude
            
            model_id = self.model_id or "claude-haiku-4-5-20251001"  # Cheap and fast!
            
            self._agent = Agent(
                model=Claude(
                    id=model_id,
                    api_key=settings.anthropic_api_key,
                ),
                instructions=ANALYZER_SYSTEM_PROMPT,
                markdown=False,  # We want structured output
                response_model=ListingAnalysis,  # Structured output!
            )
            
        elif self.model_provider == "openai":
            if not settings.openai_api_key:
                raise ValueError("OPENAI_API_KEY not set. Set it in .env or environment.")
            
            from agno.agent import Agent
            from agno.models.openai import OpenAIChat
            
            model_id = self.model_id or "gpt-4o-mini"  # Cheap and fast!
            
            self._agent = Agent(
                model=OpenAIChat(
                    id=model_id,
                    api_key=settings.openai_api_key,
                ),
                instructions=ANALYZER_SYSTEM_PROMPT,
                markdown=False,
                response_model=ListingAnalysis,
            )
        else:
            raise ValueError(f"Unknown model provider: {self.model_provider}")
        
        self._available = True
        logger.info(f"LLM analyzer initialized with {self.model_provider}/{model_id}")
    
    @property
    def is_available(self) -> bool:
        """Check if LLM analysis is available."""
        return self._available
    
    @property
    def initialization_error(self) -> str | None:
        """Get initialization error if any."""
        return self._init_error
    
    async def analyze_listing(
        self,
        title: str,
        description: str,
        price_czk: int,
        area_m2: float | None,
        apartment_type: str | None,
        district: str | None,
        features: dict[str, Any] | None = None,
    ) -> ListingAnalysis | None:
        """
        Analyze a listing description using LLM.
        
        Args:
            title: Listing title
            description: Full listing description (Czech)
            price_czk: Listed price in CZK
            area_m2: Advertised area
            apartment_type: Type like "2+kk", "3+1"
            district: Location/district
            features: Additional features dict
            
        Returns:
            ListingAnalysis with structured insights, or None if unavailable
        """
        if not self._available or not self._agent:
            logger.warning("LLM analyzer not available, skipping analysis")
            return None
        
        # Build the prompt with listing context
        prompt = self._build_analysis_prompt(
            title=title,
            description=description,
            price_czk=price_czk,
            area_m2=area_m2,
            apartment_type=apartment_type,
            district=district,
            features=features,
        )
        
        try:
            # Run the agent asynchronously
            response = await self._agent.arun(prompt)
            
            # The response should be a ListingAnalysis thanks to response_model
            if hasattr(response, 'content') and isinstance(response.content, ListingAnalysis):
                return response.content
            
            # Fallback: try to parse from response
            logger.warning(f"Unexpected response type: {type(response)}")
            return None
            
        except Exception as e:
            logger.error(f"LLM analysis failed: {e}")
            return None
    
    def _build_analysis_prompt(
        self,
        title: str,
        description: str,
        price_czk: int,
        area_m2: float | None,
        apartment_type: str | None,
        district: str | None,
        features: dict[str, Any] | None,
    ) -> str:
        """Build the analysis prompt with all listing context."""
        
        # Format price nicely
        if price_czk <= 10:
            price_str = "Price on Request (POA)"
        else:
            price_str = f"{price_czk:,} Kč"
            if area_m2 and area_m2 > 0:
                price_per_m2 = price_czk / area_m2
                price_str += f" ({price_per_m2:,.0f} Kč/m²)"
        
        # Build context section
        context_parts = [
            f"**Title:** {title}",
            f"**Price:** {price_str}",
        ]
        
        if area_m2:
            context_parts.append(f"**Advertised Area:** {area_m2} m²")
        
        if apartment_type:
            context_parts.append(f"**Type:** {apartment_type}")
        
        if district:
            context_parts.append(f"**Location:** {district}")
        
        if features:
            # Format features nicely
            feature_str = ", ".join(f"{k}: {v}" for k, v in features.items() if v)
            if feature_str:
                context_parts.append(f"**Features:** {feature_str}")
        
        context = "\n".join(context_parts)
        
        return f"""Analyze this Czech real estate listing:

## Listing Context:
{context}

## Description (Czech):
{description}

---

Provide your structured analysis. Focus on:
1. Any red flags or concerns
2. Hidden costs (especially parking!)
3. True usable area vs advertised
4. Overall recommendation
"""
    
    def analyze_listing_sync(
        self,
        title: str,
        description: str,
        price_czk: int,
        area_m2: float | None = None,
        apartment_type: str | None = None,
        district: str | None = None,
        features: dict[str, Any] | None = None,
    ) -> ListingAnalysis | None:
        """Synchronous wrapper for analyze_listing."""
        return asyncio.run(
            self.analyze_listing(
                title=title,
                description=description,
                price_czk=price_czk,
                area_m2=area_m2,
                apartment_type=apartment_type,
                district=district,
                features=features,
            )
        )


# Singleton instance for reuse
_analyzer: LLMAnalyzer | None = None


def get_llm_analyzer(
    model_provider: str = "anthropic",
    model_id: str | None = None,
    force_new: bool = False,
) -> LLMAnalyzer:
    """
    Get or create LLM analyzer instance.
    
    Args:
        model_provider: "anthropic" or "openai"
        model_id: Specific model ID (optional)
        force_new: Force creating new instance
        
    Returns:
        LLMAnalyzer instance (may not be available if no API key)
    """
    global _analyzer
    
    if _analyzer is None or force_new:
        _analyzer = LLMAnalyzer(
            model_provider=model_provider,
            model_id=model_id,
        )
    
    return _analyzer


async def analyze_description(
    title: str,
    description: str,
    price_czk: int,
    area_m2: float | None = None,
    apartment_type: str | None = None,
    district: str | None = None,
    features: dict[str, Any] | None = None,
    model_provider: str = "anthropic",
) -> ListingAnalysis | None:
    """
    Convenience function to analyze a listing description.
    
    This is the main entry point for LLM analysis.
    Returns None if LLM is not available (no API key).
    """
    analyzer = get_llm_analyzer(model_provider=model_provider)
    
    if not analyzer.is_available:
        return None
    
    return await analyzer.analyze_listing(
        title=title,
        description=description,
        price_czk=price_czk,
        area_m2=area_m2,
        apartment_type=apartment_type,
        district=district,
        features=features,
    )
