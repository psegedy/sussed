"""Heuristic scoring for apartment hunts."""

from __future__ import annotations

import re
from datetime import datetime
from typing import TYPE_CHECKING, Any

SKETCHY_KEYWORDS: dict[str, int] = {
    "investic": -40,
    "potenciál": -30,
    "příležitost": -30,
    "k rekonstrukci": -50,
    "před rekonstrukc": -40,
    "nutná rekonstrukce": -60,
    "vhodné k": -20,
    "ideální pro": -10,
    "rušná": -30,
    "frekventovan": -25,
    "suterén": -40,
    "bez výtahu": -20,
}

GOOD_KEYWORDS: dict[str, int] = {
    "po rekonstrukci": 40,
    "kompletní rekonstrukce": 50,
    "nový": 15,
    "moderní": 15,
    "zateplení": 25,
    "plastová okna": 20,
    "klimatizac": 30,
    "podlahové topení": 25,
    "krbov": 15,
    "lodžie": 15,
    "balkon": 15,
    "terasa": 20,
    "zahrad": 20,
    "garáž": 25,
    "parkovací stání": 20,
    "sklep": 10,
    "komora": 10,
    "tichá": 20,
    "klidná": 20,
    "výhled": 15,
    "slunný": 15,
    "světlý": 10,
    "cihlový": 15,
    "nízké poplatky": 20,
    "nízké náklady": 20,
}

ENERGY_RATINGS: dict[str, int] = {
    "energetická třída a": 40,
    "energetická třída b": 30,
    "energetická třída c": 15,
    "penb a": 40,
    "penb b": 30,
    "penb c": 15,
    "penb g": -30,
    "penb f": -20,
}

if TYPE_CHECKING:
    from sussed.hunt.config import SearchConfig


async def score_listing(
    config: SearchConfig,
    listing: dict[str, Any],
    is_poa: bool,
    poa_price_threshold: int,
) -> dict[str, Any]:
    """
    Score a listing based on config criteria.

    For POA (1 Kč) listings, we skip price calculations entirely!
    """
    criteria = config.criteria
    scoring = config.scoring

    score = 500  # Start at average
    reasons = []
    red_flags = []
    highlights = []
    features_dict = listing.get("features") or {}
    description = listing.get("description") or ""
    desc_lower = description.lower()

    # === HARD REJECTS ===
    exclude_kws = criteria.get_exclude_keywords()
    if exclude_kws and desc_lower:
        for keyword in exclude_kws:
            if keyword in desc_lower:
                return {
                    "score": -1,
                    "reasons": [f"Auto-rejected: description contains '{keyword}'"],
                    "highlights": [],
                    "red_flags": [f"🚫 Excluded keyword: '{keyword}'"],
                }

    if criteria.reject_panel_building and features_dict.get("panel"):
        return {
            "score": -1,
            "reasons": ["Auto-rejected: panel building"],
            "highlights": [],
            "red_flags": ["🚫 Panel building (paneláky reject)"],
        }

    # === DESCRIPTION BONUS ===
    # Having a description at all is valuable for analysis
    has_description = bool(description)
    if has_description:
        score += 20  # Bonus for having analyzable description
        highlights.append("Has description")

    # === PRICE ANALYSIS (skip for POA) ===
    if is_poa:
        reasons.append("Price on Request - evaluated on features only")
        # Don't penalize, don't reward - neutral on price
    else:
        # Price vs market average
        if listing.get("price_per_m2"):
            price_per_m2 = listing["price_per_m2"]

            # Get market stats for comparison
            market_avg = await get_market_average(
                listing.get("city"), listing.get("apartment_type"), poa_price_threshold
            )

            if market_avg:
                price_ratio = price_per_m2 / market_avg

                if price_ratio < 0.8:
                    score += 150
                    highlights.append(f"Below market avg ({price_ratio:.0%} of avg)")
                elif price_ratio < 0.95:
                    score += 75
                    highlights.append(f"Good price ({price_ratio:.0%} of avg)")
                elif price_ratio > 1.2:
                    score -= 100
                    red_flags.append(f"Above market ({price_ratio:.0%} of avg)")
                elif price_ratio > 1.1:
                    score -= 50
                    reasons.append(f"Slightly overpriced ({price_ratio:.0%} of avg)")

        # Max price per m² check
        if criteria.max_price_per_m2 and listing.get("price_per_m2"):
            if listing["price_per_m2"] > criteria.max_price_per_m2:
                score -= 100
                red_flags.append(
                    f"Over max price/m² ({listing['price_per_m2']:,} > {criteria.max_price_per_m2:,})"
                )
            else:
                score += 25
                highlights.append("Within price/m² target")

    # === AREA ANALYSIS ===
    if listing.get("area_m2"):
        area = listing["area_m2"]

        if criteria.min_area_m2 and area >= criteria.min_area_m2:
            score += 25
            highlights.append(f"Meets min area ({area} m²)")

        # Bonus for spacious
        if area > 70:
            score += 25
            highlights.append("Spacious")

    # === LOCATION ===
    district = (listing.get("district") or "").lower()
    address = (listing.get("address") or "").lower()
    location_text = f"{district} {address}"  # Check both!

    if config.preferred_districts:
        for i, pref in enumerate(config.preferred_districts):
            if pref.lower() in location_text:
                bonus = max(25 - (i * 5), 5)  # First choice = 25pts, decreasing
                score += bonus
                highlights.append(f"Preferred district: {pref}")
                break

    if config.avoid_districts:
        for avoid in config.avoid_districts:
            if avoid.lower() in location_text:
                score -= 200
                red_flags.append(f"Avoided district: {avoid}")
                break  # Only penalize once even if multiple avoid_districts match

    # Check known bad locations (streets like Cejl, Bratislavská, etc.)
    if config.known_bad_locations:
        for bad_loc in config.known_bad_locations:
            if bad_loc.lower() in location_text:
                score -= 200  # Penalty for known problem areas
                red_flags.append(f"⚠️ Bad location: {bad_loc}")
                break  # Only penalize once

    # === FEATURES ===
    labels = listing.get("raw_labels", []) or []
    labels_flat = [str(label).lower() for label in labels]

    # Check for required features
    has_parking = bool(
        features_dict.get("parking")
        or features_dict.get("garage")
        or features_dict.get("parking_lots")
        or any("park" in label or "garáž" in label for label in labels_flat)
    )
    has_balcony = bool(
        features_dict.get("balcony")
        or features_dict.get("loggia")
        or features_dict.get("terrace")
        or any("balk" in label or "lodž" in label or "tera" in label for label in labels_flat)
    )
    has_cellar = bool(features_dict.get("cellar") or any("sklep" in label for label in labels_flat))
    has_elevator = bool(
        features_dict.get("elevator") or any("výtah" in label for label in labels_flat)
    )

    if criteria.require_parking:
        if has_parking:
            score += 100
            highlights.append("Has parking ✓")
        else:
            score += scoring.penalty_no_parking
            red_flags.append("Missing parking")
    elif has_parking:
        score += 50
        highlights.append("Parking available")

    if criteria.require_balcony:
        if has_balcony:
            score += 50
            highlights.append("Has balcony/loggia ✓")
        else:
            score -= 50
            red_flags.append("Missing balcony")
    elif has_balcony:
        score += 20
        highlights.append("Has outdoor space")

    if criteria.require_elevator and not has_elevator:
        score += -50
        red_flags.append("Missing elevator (required)")

    if has_elevator:
        score += 20
        highlights.append("Elevator available")

    if has_cellar:
        score += 10
        highlights.append("Has cellar/storage")

    if features_dict.get("new_building"):
        score += scoring.bonus_new_building
        highlights.append(f"Novostavba (+{scoring.bonus_new_building})")
    elif features_dict.get("reconstructed"):
        score += scoring.bonus_reconstruction
        highlights.append(f"Po rekonstrukci (+{scoring.bonus_reconstruction})")

    cond_name = (features_dict.get("building_condition") or "").lower()
    if "velmi dobr" in cond_name:
        score += scoring.bonus_very_good_condition
        highlights.append(f"Velmi dobrý stav (+{scoring.bonus_very_good_condition})")
    elif "novostavba" in cond_name and not features_dict.get("new_building"):
        score += scoring.bonus_new_building
        highlights.append(f"Novostavba (raw) (+{scoring.bonus_new_building})")

    if scoring.penalty_panel and features_dict.get("panel"):
        score += scoring.penalty_panel
        red_flags.append(f"Panel building ({scoring.penalty_panel})")

    # === LISTING QUALITY ===
    image_count = listing.get("image_count", 0)

    if image_count >= 15:
        score += 25
        highlights.append(f"Many photos ({image_count})")
    elif image_count >= 8:
        score += 10
    elif image_count < criteria.min_photos:
        score -= 30
        red_flags.append(f"Too few photos ({image_count})")

    if listing.get("has_floor_plan"):
        score += 30
        highlights.append("Floor plan available")
    elif criteria.require_floor_plan:
        score -= 50
        red_flags.append("Missing floor plan")

    if listing.get("has_video"):
        score += 15
        highlights.append("Video tour")

    if listing.get("has_3d_tour"):
        score += 20
        highlights.append("3D tour available")

    # === FLOOR ===
    floor = listing.get("floor")
    total_floors = listing.get("total_floors")

    if floor is not None:
        if floor == 0 and (criteria.avoid_ground_floor or criteria.reject_ground_floor):
            score -= 75
            red_flags.append("Ground floor")

        if total_floors and floor == total_floors and criteria.avoid_top_floor:
            score -= 50
            red_flags.append("Top floor (potential roof issues)")

        if 1 <= floor <= 3:
            score += 10
            highlights.append(f"Good floor ({floor})")

    # === DESCRIPTION ANALYSIS ===
    if description:
        # Red flag keywords - things that suggest problems or overselling
        sketchy_keywords = dict(SKETCHY_KEYWORDS)

        # Merge user-defined penalty keywords from config (lowercased for matching)
        if scoring.penalize_description_keywords:
            sketchy_keywords.update(
                {k.lower(): v for k, v in scoring.penalize_description_keywords.items()}
            )

        for word, penalty in sketchy_keywords.items():
            if word in desc_lower:
                score += penalty  # penalty is negative
                red_flags.append(f"'{word}' ({penalty})")

        # Positive keywords - things that add value
        good_keywords = dict(GOOD_KEYWORDS)

        # Merge user-defined bonus keywords from config (lowercased for matching)
        if scoring.bonus_description_keywords:
            good_keywords.update(
                {k.lower(): v for k, v in scoring.bonus_description_keywords.items()}
            )

        found_good = set()  # Avoid duplicates
        for word, bonus in good_keywords.items():
            if word in desc_lower and word not in found_good:
                score += bonus
                highlights.append(f"'{word}' (+{bonus})")
                found_good.add(word)

        # Energy rating detection
        for rating, points in ENERGY_RATINGS.items():
            if rating in desc_lower:
                if points > 0:
                    highlights.append(f"Energy rating: {rating.upper()} (+{points})")
                else:
                    red_flags.append(f"Poor energy rating: {rating.upper()} ({points})")
                score += points
                break  # Only count one rating

        # Parking price detection (important for true cost calculation)

        parking_patterns = [
            r"parkov\w*\s*(\d+)\s*(?:tis|000)",  # "parking 500 tis" or "parking 500000"
            r"garáž\w*\s*(\d+)\s*(?:tis|000)",
            r"stání\s*(\d+)\s*(?:tis|000)",
        ]

        for pattern in parking_patterns:
            match = re.search(pattern, desc_lower)
            if match:
                parking_price = int(match.group(1))
                if parking_price < 100:  # Probably in thousands
                    parking_price *= 1000
                red_flags.append(f"Separate parking: ~{parking_price:,} Kč")
                # Don't penalize, just note it for awareness
                break

    # === FINAL ADJUSTMENTS ===

    # Clamp score to valid range
    if score >= 900:
        # Potential gem - but let's not auto-assign 9999
        reasons.append("HIGH SCORE - potential gem! 💎")

    if score < 0:
        score = 0
    elif score > 1000:
        score = 1000

    # Determine category
    if len(red_flags) >= 3:
        reasons.append("Multiple red flags present")

    if len(highlights) >= 5:
        reasons.append("Many positive features")

    return {
        "score": score,
        "reasons": reasons,
        "red_flags": red_flags,
        "highlights": highlights,
        "is_poa": is_poa,
        "scored_at": datetime.utcnow().isoformat(),
    }


async def get_market_average(
    city: str | None, apartment_type: str | None, poa_price_threshold: int
) -> int | None:
    """Get market average price per m² for comparison."""
    from sqlalchemy import func
    from sqlmodel import and_, select

    from sussed.db.connection import get_session
    from sussed.db.models import Listing, ListingStatus

    async with get_session() as session:
        conditions = [
            Listing.status == ListingStatus.ACTIVE,
            Listing.price_czk > poa_price_threshold,  # Exclude POA
            Listing.price_per_m2.isnot(None),
        ]

        if city:
            conditions.append(Listing.city.ilike(f"%{city}%"))

        if apartment_type:
            conditions.append(Listing.apartment_type == apartment_type)

        stmt = select(func.avg(Listing.price_per_m2)).where(and_(*conditions))
        result = await session.execute(stmt)
        avg = result.scalar()

        return int(avg) if avg else None
