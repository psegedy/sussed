#!/usr/bin/env python3
"""Build and validate sussed AI review JSON payloads."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

VALID_VIBES = {"peak", "valid", "mid", "sus"}
ALLOWED_FIELDS = {
    "score",
    "vibe",
    "confidence",
    "recommendation",
    "score_reason",
    "summary",
    "red_flags",
    "yellow_flags",
    "highlights",
    "hidden_costs",
    "parking_price",
    "parking_included",
    "usable_area_m2",
    "photo_observations",
    "reviewer_name",
    "reviewer_model",
    "reviewer_session",
    "input_hash",
    "reviewed_at",
    "raw_review",
}
REQUIRED_FIELDS = {
    "score",
    "vibe",
    "confidence",
    "recommendation",
    "score_reason",
    "summary",
    "reviewer_name",
    "input_hash",
}
DEFAULT_REVIEWER_NAME = "sussed-ai-review"
DEFAULT_REVIEWER_MODEL = "copilot-cli"
URL_PATTERN = re.compile(r"https?://\S+")


def _has_valid_score(value: Any) -> bool:
    """Return whether a value matches the ReviewResultInput score rule."""
    if isinstance(value, bool) or not isinstance(value, int):
        return False
    return value in (-1, 9999) or 0 <= value <= 1000


def _is_number(value: Any) -> bool:
    """Return whether a value is a real JSON number, excluding booleans."""
    return isinstance(value, int | float) and not isinstance(value, bool)


def _is_int_or_none(value: Any) -> bool:
    """Return whether a value is an integer or null, excluding booleans."""
    return value is None or (isinstance(value, int) and not isinstance(value, bool))


def _is_non_empty_string(value: Any) -> bool:
    """Return whether a value is a non-empty string after trimming."""
    return isinstance(value, str) and bool(value.strip())


def _ensure_list(review: dict[str, Any], field: str, errors: list[str]) -> None:
    """Validate that a field is a list when present."""
    if field in review and not isinstance(review[field], list):
        errors.append(f"{field} must be a list")


def build_review(
    score: int,
    vibe: str,
    confidence: float,
    recommendation: str,
    score_reason: str,
    summary: str,
    input_hash: str,
    url: str,
    **optional_kwargs: Any,
) -> dict[str, Any]:
    """Return a complete review dict with schema-friendly defaults.

    If ``score_reason`` does not contain ``url``, the URL is appended so the
    review satisfies the skill's source-link rule.
    """
    unknown_keys = set(optional_kwargs) - ALLOWED_FIELDS
    if unknown_keys:
        names = ", ".join(sorted(unknown_keys))
        raise ValueError(f"unknown review field(s): {names}")

    if url and url not in score_reason:
        score_reason = f"{score_reason.rstrip()} URL: {url}"

    review: dict[str, Any] = {
        "score": score,
        "vibe": vibe,
        "confidence": confidence,
        "recommendation": recommendation,
        "score_reason": score_reason,
        "summary": summary,
        "red_flags": [],
        "yellow_flags": [],
        "highlights": [],
        "hidden_costs": {},
        "parking_price": None,
        "parking_included": None,
        "usable_area_m2": None,
        "photo_observations": [],
        "raw_review": None,
        "reviewer_name": DEFAULT_REVIEWER_NAME,
        "reviewer_model": DEFAULT_REVIEWER_MODEL,
        "reviewer_session": None,
        "input_hash": input_hash,
    }
    review.update(optional_kwargs)
    return review


def validate_review(review: dict[str, Any]) -> list[str]:
    """Return validation errors for a review dict; an empty list means valid."""
    errors: list[str] = []

    if not isinstance(review, dict):
        return ["review must be a JSON object"]

    missing = sorted(field for field in REQUIRED_FIELDS if field not in review)
    errors.extend(f"missing required field: {field}" for field in missing)

    unknown = sorted(set(review) - ALLOWED_FIELDS)
    errors.extend(f"unknown field: {field}" for field in unknown)

    if "score" in review and not _has_valid_score(review["score"]):
        errors.append("score must be -1, 0-1000, or 9999")

    if "vibe" in review and review["vibe"] not in VALID_VIBES:
        errors.append("vibe must be one of: peak, valid, mid, sus")

    if "confidence" in review:
        confidence = review["confidence"]
        if not _is_number(confidence) or not 0 <= float(confidence) <= 1:
            errors.append("confidence must be a number between 0 and 1")

    if "recommendation" in review:
        recommendation = review["recommendation"]
        if not _is_non_empty_string(recommendation):
            errors.append("recommendation must be a non-empty string")
        elif len(recommendation) > 40:
            errors.append("recommendation must be at most 40 characters")

    if "score_reason" in review:
        score_reason = review["score_reason"]
        if not _is_non_empty_string(score_reason):
            errors.append("score_reason must be a non-empty string")
        elif not URL_PATTERN.search(score_reason):
            errors.append("score_reason must include the listing URL")

    if "summary" in review and not _is_non_empty_string(review["summary"]):
        errors.append("summary must be a non-empty string")

    if "reviewer_name" in review and not _is_non_empty_string(review["reviewer_name"]):
        errors.append("reviewer_name must be a non-empty string")

    if "input_hash" in review and not _is_non_empty_string(review["input_hash"]):
        errors.append("input_hash must be a non-empty string")

    for field in ("red_flags", "yellow_flags", "highlights", "photo_observations"):
        _ensure_list(review, field, errors)

    if "hidden_costs" in review:
        hidden_costs = review["hidden_costs"]
        if not isinstance(hidden_costs, dict):
            errors.append("hidden_costs must be an object")
        else:
            for key, value in hidden_costs.items():
                if not isinstance(key, str):
                    errors.append("hidden_costs keys must be strings")
                if not _is_int_or_none(value):
                    errors.append(f"hidden_costs.{key} must be an int or null")

    if "parking_price" in review:
        parking_price = review["parking_price"]
        if not _is_int_or_none(parking_price) or (parking_price is not None and parking_price < 0):
            errors.append("parking_price must be a non-negative int or null")

    if "parking_included" in review and review["parking_included"] is not None:
        if not isinstance(review["parking_included"], bool):
            errors.append("parking_included must be a boolean or null")

    if "usable_area_m2" in review:
        usable_area_m2 = review["usable_area_m2"]
        if usable_area_m2 is not None and (not _is_number(usable_area_m2) or float(usable_area_m2) <= 0):
            errors.append("usable_area_m2 must be a positive number or null")

    if "raw_review" in review and review["raw_review"] is not None and not isinstance(review["raw_review"], dict):
        errors.append("raw_review must be an object or null")

    if "reviewer_model" in review and review["reviewer_model"] is not None:
        if not isinstance(review["reviewer_model"], str):
            errors.append("reviewer_model must be a string or null")

    if "reviewer_session" in review and review["reviewer_session"] is not None:
        if not isinstance(review["reviewer_session"], str):
            errors.append("reviewer_session must be a string or null")

    return errors


def dump_review(review: dict[str, Any], output_path: str) -> None:
    """Write a review JSON file with stable pretty formatting."""
    path = Path(output_path).expanduser()
    if path.parent != Path("."):
        path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(review, file, ensure_ascii=False, indent=2)
        file.write("\n")


def _load_review(path: Path) -> dict[str, Any]:
    """Load a JSON review object from disk."""
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError("review must be a JSON object")
    return data


def _load_prepared(path: Path) -> dict[str, Any]:
    """Load a prepared review payload from disk."""
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError("prepared payload must be a JSON object")
    required = ("url", "input_hash")
    for field in required:
        if not data.get(field):
            raise ValueError(f"prepared payload missing '{field}'")
    return data


def _skeleton_command(prepared_path: str, out_path: str | None, reviewer: str) -> int:
    """Emit a valid review-JSON stub seeded from a prepared payload.

    The skeleton fills the mechanical fields (URL, input_hash, usable_area_m2,
    reviewer_name) so the reviewer only needs to set score, vibe, confidence,
    recommendation, summary, score_reason content, and flags/highlights.
    """
    try:
        prepared = _load_prepared(Path(prepared_path).expanduser())
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"❌ cannot read prepared payload: {exc}", file=sys.stderr)
        return 1

    url = prepared["url"]
    area = prepared.get("area_m2")
    usable_area = float(area) if isinstance(area, int | float) and area else None

    review = build_review(
        score=0,
        vibe="mid",
        confidence=0.5,
        recommendation="TODO",
        score_reason=f"TODO: justify the score with concrete evidence. [{url}]",
        summary="TODO: one- or two-sentence human summary.",
        input_hash=prepared["input_hash"],
        url=url,
        red_flags=[],
        yellow_flags=[],
        highlights=[],
        hidden_costs={},
        usable_area_m2=usable_area,
        photo_observations=[],
        reviewer_name=reviewer,
    )

    target = Path(out_path).expanduser() if out_path else Path(prepared_path).with_name(
        Path(prepared_path).stem.replace("-prepared", "-review") + ".json"
    )
    dump_review(review, str(target))
    print(f"✓ wrote skeleton: {target}")
    return 0


def _validate_command(path: str) -> int:
    """Validate a review JSON file and return a process exit code."""
    try:
        review = _load_review(Path(path).expanduser())
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"❌ invalid: {path}", file=sys.stderr)
        print(f"  - {exc}", file=sys.stderr)
        return 1

    errors = validate_review(review)
    if errors:
        print(f"❌ invalid: {path}")
        for error in errors:
            print(f"  - {error}")
        return 1

    print(f"✓ valid: {path}")
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Build and validate sussed review JSON payloads.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate", help="Validate a review JSON file.")
    validate_parser.add_argument("path", help="Path to a review JSON file.")

    skeleton_parser = subparsers.add_parser(
        "skeleton",
        help="Emit a valid review JSON stub seeded from a prepared payload.",
    )
    skeleton_parser.add_argument("prepared", help="Path to a *-prepared.json payload.")
    skeleton_parser.add_argument("--out", help="Output review JSON path. Defaults to <prefix>-review.json next to the prepared file.")
    skeleton_parser.add_argument(
        "--reviewer-name",
        default=DEFAULT_REVIEWER_NAME,
        help=f"Override reviewer_name (default: {DEFAULT_REVIEWER_NAME}).",
    )

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the CLI."""
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.command == "validate":
        return _validate_command(args.path)
    if args.command == "skeleton":
        return _skeleton_command(args.prepared, args.out, args.reviewer_name)
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
