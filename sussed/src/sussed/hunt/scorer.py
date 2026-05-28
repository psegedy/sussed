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

OUTDOOR_PROPERTY_TYPES = {"cottage", "garden"}
REQUIRED_OUTDOOR_FEATURE_PENALTY = -150


def _property_type(criteria: Any) -> str:
    """Return normalized property type from hunt criteria."""
    return str(getattr(criteria, "property_type", "apartment") or "apartment").lower()


def _is_outdoor_property(criteria: Any) -> bool:
    """Return True for cottage/garden hunts that should skip apartment-only scoring."""
    return _property_type(criteria) in OUTDOOR_PROPERTY_TYPES


def _truthy_feature(features: dict[str, Any], *keys: str) -> bool:
    """Interpret bool/string feature values from JSONB as a usable signal."""
    falsey_strings = {"", "0", "false", "none", "no", "ne", "není", "bez"}
    for key in keys:
        value = features.get(key)
        if isinstance(value, str):
            if value.strip().lower() not in falsey_strings:
                return True
        elif value:
            return True
    return False


def _text_has_any(text: str, phrases: tuple[str, ...]) -> bool:
    """Return True when any phrase appears in normalized description/label text."""
    return any(phrase in text for phrase in phrases)


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
    property_type = _property_type(criteria)
    is_outdoor_property = _is_outdoor_property(criteria)
    is_cottage = property_type == "cottage"
    is_garden = property_type == "garden"

    score = 400  # Start near "average" with room for differentiation
    reasons = []
    red_flags = []
    highlights = []
    features_dict = listing.get("features") or {}
    description = listing.get("description") or ""
    desc_lower = description.lower()

    # === SIGNIFICANT PENALTIES (no auto-reject — listings stay visible) ===
    # Previously these returned score=-1 (hard reject). Now they apply a
    # large penalty so the listing is heavily demoted but still visible in
    # results, so the user can see what was filtered and why.
    REJECT_PENALTY = -150

    exclude_kws = criteria.get_exclude_keywords()
    if exclude_kws and desc_lower:
        for keyword in exclude_kws:
            if keyword in desc_lower:
                score += REJECT_PENALTY
                red_flags.append(f"🚫 Excluded keyword: '{keyword}' ({REJECT_PENALTY})")
                reasons.append(f"Excluded keyword '{keyword}' triggers {REJECT_PENALTY} penalty")
                break  # one penalty per listing even if multiple keywords match

    if criteria.reject_panel_building and features_dict.get("panel"):
        score += REJECT_PENALTY
        red_flags.append(f"🚫 Panel building (paneláky reject) ({REJECT_PENALTY})")
        reasons.append(f"Panel building triggers {REJECT_PENALTY} penalty (reject_panel_building=true)")

    # Track which feature signals were already counted so we don't
    # double-score them via description keyword matches later.
    counted_feature_keys: set[str] = set()
    # === PRICE ANALYSIS ===
    # For POA listings with a known prior price, use that for market comparison
    # so they don't get unfairly demoted into invisibility. The drop itself is
    # a strong signal — surface it.
    original_price = listing.get("original_price")
    area_m2_val = listing.get("area_m2")

    if is_poa and original_price and area_m2_val and area_m2_val > 0:
        effective_price_per_m2 = int(original_price / float(area_m2_val))
        # Flag the drop and don't penalize the missing current price.
        reasons.append(
            f"Price dropped to POA — using prior price {original_price:,} Kč for scoring"
        )
        red_flags.append("⚠ Switched to POA (seller may be in negotiation)")
        do_price_analysis = True
    elif is_poa:
        effective_price_per_m2 = None
        reasons.append("Price on Request - evaluated on features only")
        do_price_analysis = False
    else:
        effective_price_per_m2 = listing.get("price_per_m2")
        do_price_analysis = True

    if do_price_analysis and effective_price_per_m2:
        market_avg = await get_market_average(
            listing.get("city"), listing.get("apartment_type"), poa_price_threshold
        )

        if market_avg:
            price_ratio = effective_price_per_m2 / market_avg

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
        if criteria.max_price_per_m2:
            if effective_price_per_m2 > criteria.max_price_per_m2:
                score -= 100
                red_flags.append(
                    f"Over max price/m² ({effective_price_per_m2:,} > {criteria.max_price_per_m2:,})"
                )
            else:
                score += 25
                highlights.append("Within price/m² target")

    # === AREA ANALYSIS ===
    if listing.get("area_m2"):
        area = listing["area_m2"]

        if criteria.min_area_m2 and area >= criteria.min_area_m2:
            score += 15
            highlights.append(f"Meets min area ({area} m²)")

        # Bonus for spacious
        if area > 70:
            score += 15
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
    signal_text = f"{desc_lower} {' '.join(labels_flat)}"

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
            score += 75
            highlights.append("Has parking ✓")
            counted_feature_keys.add("parking")
        else:
            score += scoring.penalty_no_parking
            red_flags.append("Missing parking")
    elif has_parking:
        score += 25
        highlights.append("Parking available")
        counted_feature_keys.add("parking")

    if criteria.require_balcony:
        if has_balcony:
            score += 35
            highlights.append("Has balcony/loggia ✓")
            counted_feature_keys.add("balcony")
        else:
            score += scoring.penalty_no_balcony
            red_flags.append(f"Missing balcony (required) ({scoring.penalty_no_balcony})")
    elif has_balcony:
        score += 10
        highlights.append("Has outdoor space")
        counted_feature_keys.add("balcony")

    if not is_outdoor_property and criteria.require_elevator and not has_elevator:
        score += scoring.penalty_no_elevator
        red_flags.append(f"Missing elevator (required) ({scoring.penalty_no_elevator})")

    if not is_outdoor_property and has_elevator:
        score += 15
        highlights.append("Elevator available")

    if has_cellar:
        score += 10
        highlights.append("Has cellar/storage")
        counted_feature_keys.add("cellar")

    has_electricity = _truthy_feature(features_dict, "electricity", "electrical_power") or (
        not _text_has_any(signal_text, ("bez elektř", "bez elektr", "elektřina není"))
        and _text_has_any(signal_text, ("elektř", "elektr", "220v", "230v"))
    )
    has_water = _truthy_feature(features_dict, "water", "water_source") or (
        not _text_has_any(signal_text, ("bez vody", "voda není"))
        and _text_has_any(signal_text, ("vodovod", "voda", "studna", "vrt"))
    )
    has_sewage = _truthy_feature(features_dict, "sewage", "sewer", "sewerage") or _text_has_any(
        signal_text,
        ("kanalizace", "septik", "čistička", "čov", "jímka"),
    )
    has_fenced = _truthy_feature(features_dict, "fenced", "fence") or _text_has_any(
        signal_text,
        ("oplocen", "oplocení", "plotem", "plot"),
    )
    ownership_text = str(
        features_dict.get("ownership") or features_dict.get("ownership_type") or ""
    ).lower()
    has_personal_ownership = "osob" in ownership_text or _text_has_any(
        signal_text,
        ("osobní vlastnictví", "osobním vlastnictví"),
    )
    leased_land = _text_has_any(
        signal_text,
        ("pronájem pozemku", "pozemek v pronájmu", "pronajatý pozemek", "nájem pozemku"),
    )

    if is_cottage:
        if has_electricity:
            score += 30
            highlights.append("Electricity available (+30)")
        elif criteria.require_electricity:
            score += REQUIRED_OUTDOOR_FEATURE_PENALTY
            red_flags.append(
                f"Missing electricity (required) ({REQUIRED_OUTDOOR_FEATURE_PENALTY})"
            )

        if has_water:
            score += 30
            highlights.append("Water available (+30)")
        elif criteria.require_water:
            score += REQUIRED_OUTDOOR_FEATURE_PENALTY
            red_flags.append(f"Missing water (required) ({REQUIRED_OUTDOOR_FEATURE_PENALTY})")

        if has_sewage:
            score += 20
            highlights.append("Sewage/wastewater solution (+20)")

        if criteria.require_fenced and not has_fenced:
            score += REQUIRED_OUTDOOR_FEATURE_PENALTY
            red_flags.append(f"Missing fencing (required) ({REQUIRED_OUTDOOR_FEATURE_PENALTY})")

    if is_garden:
        if has_fenced:
            score += 30
            highlights.append("Fenced plot (+30)")
        elif criteria.require_fenced:
            score += REQUIRED_OUTDOOR_FEATURE_PENALTY
            red_flags.append(f"Missing fencing (required) ({REQUIRED_OUTDOOR_FEATURE_PENALTY})")

        if has_water:
            score += 40
            highlights.append("Water on plot (+40)")
        elif criteria.require_water:
            score += REQUIRED_OUTDOOR_FEATURE_PENALTY
            red_flags.append(f"Missing water (required) ({REQUIRED_OUTDOOR_FEATURE_PENALTY})")

        if has_electricity:
            score += 40
            highlights.append("Electricity on plot (+40)")
        elif criteria.require_electricity:
            score += REQUIRED_OUTDOOR_FEATURE_PENALTY
            red_flags.append(
                f"Missing electricity (required) ({REQUIRED_OUTDOOR_FEATURE_PENALTY})"
            )

        if has_personal_ownership:
            score += 30
            highlights.append("Osobní vlastnictví (+30)")

        if leased_land:
            score += -150
            red_flags.append("Leased land / pronájem pozemku (-150)")

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
        score += 10
        highlights.append(f"Many photos ({image_count})")
    elif image_count >= 8:
        score += 5
    elif image_count < criteria.min_photos:
        score -= 30
        red_flags.append(f"Too few photos ({image_count})")

    if listing.get("has_floor_plan"):
        score += 15
        highlights.append("Floor plan available")
    elif criteria.require_floor_plan:
        score -= 50
        red_flags.append("Missing floor plan")

    if listing.get("has_video"):
        score += 10
        highlights.append("Video tour")

    if listing.get("has_3d_tour"):
        score += 15
        highlights.append("3D tour available")

    # === FLOOR ===
    floor = listing.get("floor")
    total_floors = listing.get("total_floors")

    if not is_outdoor_property and floor is not None:
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
        if is_outdoor_property:
            sketchy_keywords.pop("bez výtahu", None)

        # Merge user-defined penalty keywords from config (lowercased for matching)
        if scoring.penalize_description_keywords:
            sketchy_keywords.update(
                {k.lower(): v for k, v in scoring.penalize_description_keywords.items()}
            )

        for word, penalty in sketchy_keywords.items():
            if word in desc_lower:
                score += penalty  # penalty is negative
                red_flags.append(f"'{word}' ({penalty})")

        # Positive keywords - things that add value.
        # De-dup against feature flags so we don't double-count a signal
        # that was already scored from features_dict (e.g. parking/balcony).
        good_keywords = dict(GOOD_KEYWORDS)

        # Map description keywords to feature-key categories they overlap with.
        # If the feature was already counted above, skip the description bonus.
        keyword_feature_overlap = {
            "garáž": "parking",
            "parkovací stání": "parking",
            "balkon": "balcony",
            "lodžie": "balcony",
            "terasa": "balcony",
            "sklep": "cellar",
            "komora": "cellar",
        }

        # Merge user-defined bonus keywords from config (lowercased for matching)
        if scoring.bonus_description_keywords:
            good_keywords.update(
                {k.lower(): v for k, v in scoring.bonus_description_keywords.items()}
            )

        # Cap total positive description contribution so a developer's
        # marketing-stuffed text can't blow past every feature signal.
        DESCRIPTION_BONUS_CAP = 75
        desc_bonus_total = 0
        found_good: set[str] = set()
        for word, bonus in good_keywords.items():
            if word in desc_lower and word not in found_good:
                overlap = keyword_feature_overlap.get(word)
                if overlap and overlap in counted_feature_keys:
                    continue  # already counted via features, skip
                allowed = max(0, DESCRIPTION_BONUS_CAP - desc_bonus_total)
                if allowed <= 0:
                    break
                applied = min(bonus, allowed)
                score += applied
                desc_bonus_total += applied
                highlights.append(f"'{word}' (+{applied})")
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

    # Soft cap: above the "great" threshold, apply diminishing returns so
    # only truly exceptional listings approach 1000. Without this, any
    # well-described novostavba with parking maxes out and we lose
    # differentiation at the top.
    SOFT_CAP_FLOOR = 700
    SOFT_CAP_CEIL = 1000
    if score > SOFT_CAP_FLOOR:
        excess = score - SOFT_CAP_FLOOR
        room = SOFT_CAP_CEIL - SOFT_CAP_FLOOR  # 300
        # Asymptotic compression — score approaches but never reaches 1000.
        score = SOFT_CAP_FLOOR + int(room * (1 - 1 / (1 + excess / room)))

    if score >= 900:
        reasons.append("HIGH SCORE - potential gem! 💎")

    # Clamp score to valid range
    if score < 0:
        score = 0
    elif score > SOFT_CAP_CEIL:
        score = SOFT_CAP_CEIL

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
