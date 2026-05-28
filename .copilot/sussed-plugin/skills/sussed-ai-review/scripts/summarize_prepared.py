#!/usr/bin/env python3
"""Print compact summaries of prepared sussed review payloads."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

DESCRIPTION_LIMIT = 1500


def _format_int(value: Any) -> str:
    """Format integer-like values with commas, or return None for missing values."""
    if value is None:
        return "None"
    if isinstance(value, bool):
        return str(value)
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return str(value)


def _format_price(value: Any) -> str:
    """Format a CZK price for display."""
    if value is None:
        return "None"
    return f"{_format_int(value)} Kč"


def _format_area(value: Any) -> str:
    """Format square meters without noisy trailing decimals."""
    if value is None:
        return "None"
    if isinstance(value, int | float) and not isinstance(value, bool):
        if float(value).is_integer():
            return f"{int(value)} m²"
        return f"{float(value):.1f} m²"
    return f"{value} m²"


def _format_floor(floor: Any, total_floors: Any) -> str:
    """Format floor information in a compact form."""
    if floor is None and total_floors is None:
        return "floor unknown"
    if floor is None:
        return f"floor ?/{total_floors}"
    if total_floors is None:
        return f"floor {floor}"
    return f"floor {floor}/{total_floors}"


def _feature_value(features: dict[str, Any], key: str, fallback: Any = None) -> Any:
    """Read a feature value and preserve explicit false/null values."""
    return features[key] if key in features else fallback


def _format_features(features: dict[str, Any]) -> str:
    """Format the apartment fields reviewers most often need first."""
    parking = _feature_value(features, "parking")
    if parking is None:
        parking = bool(features.get("garage") or features.get("parking_lots"))

    pairs = [
        ("building_type", features.get("building_type")),
        ("brick", features.get("brick")),
        ("condition", features.get("building_condition") or features.get("condition")),
        ("new_building", features.get("new_building")),
        ("reconstructed", features.get("reconstructed")),
        ("elevator", features.get("elevator")),
        ("parking", parking),
        ("balcony", features.get("balcony")),
        ("loggia", features.get("loggia")),
        ("terrace", features.get("terrace")),
        ("cellar", features.get("cellar")),
    ]
    return " ".join(f"{key}={value}" for key, value in pairs)


def _format_list(values: Any) -> str:
    """Render a list-of-strings feature compactly."""
    if isinstance(values, list) and values:
        return ",".join(str(v) for v in values)
    return "None"


def _format_garden_features(features: dict[str, Any]) -> str:
    """Format the garden/cottage land fields reviewers most often need first."""
    pairs = [
        ("ownership", features.get("ownership")),
        ("water", _format_list(features.get("water_sources"))),
        ("electricity", _format_list(features.get("electricity_sources"))),
        ("sewage", _format_list(features.get("sewage_sources"))),
        ("condition", features.get("building_condition") or features.get("condition")),
    ]
    return " ".join(f"{key}={value}" for key, value in pairs)


def _features_for_category(category: Any, features: dict[str, Any]) -> str:
    """Pick the property-appropriate feature renderer."""
    if isinstance(category, str) and category.lower() in {"garden", "cottage"}:
        return _format_garden_features(features)
    return _format_features(features)


def _preview_description(description: Any) -> str:
    """Return a readable description preview."""
    if not description:
        return "(no description)"
    text = str(description).replace("\r\n", "\n").strip()
    if len(text) <= DESCRIPTION_LIMIT:
        return text
    return f"{text[:DESCRIPTION_LIMIT].rstrip()}…"


def load_json(path: Path) -> dict[str, Any]:
    """Load a prepared JSON payload from disk."""
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError("prepared payload must be a JSON object")
    return data


def summarize_payload(data: dict[str, Any], source_path: Path | None = None) -> str:
    """Build a compact human-readable summary for one prepared payload."""
    listing_id = str(data.get("listing_id") or "")
    prefix = listing_id[:8] or (source_path.stem.split("-prepared", 1)[0] if source_path else "unknown")
    title = data.get("title") or "(untitled)"
    url = data.get("url") or "None"
    price = _format_price(data.get("price_czk"))
    price_per_m2 = _format_int(data.get("price_per_m2"))
    area = _format_area(data.get("area_m2"))
    apartment_type = data.get("apartment_type") or data.get("listing_type") or "type unknown"
    floor = _format_floor(data.get("floor"), data.get("total_floors"))
    features = data.get("features") if isinstance(data.get("features"), dict) else {}
    image_paths = data.get("image_paths") if isinstance(data.get("image_paths"), list) else []
    image_count = data.get("image_count")

    advertised = f" ({_format_int(image_count)} advertised)" if image_count is not None else ""

    return "\n".join(
        [
            f"=== {prefix} | {title} ===",
            f"URL: {url}",
            f"Price: {price} ({price_per_m2} /m²) | {area} | {apartment_type} | {floor}",
            (
                "Drop signals: "
                f"initial={_format_int(data.get('initial_price'))} "
                f"original={_format_int(data.get('original_price'))} "
                f"to_poa={data.get('price_dropped_to_poa')}"
            ),
            f"Features: {_features_for_category(data.get('property_category'), features)}",
            f"Photos: {len(image_paths)} cached{advertised}",
            f"input_hash: {data.get('input_hash') or 'None'}",
            "--- Description ---",
            _preview_description(data.get("description")),
        ]
    )


def summarize_file(path: Path) -> str:
    """Load and summarize one prepared JSON file."""
    return summarize_payload(load_json(path), path)


def iter_prepared_files(directory: Path) -> list[Path]:
    """Return sorted prepared payloads in a directory."""
    return sorted(path for path in directory.glob("*-prepared.json") if path.is_file())


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Summarize one prepared sussed review JSON, or every *-prepared.json in a directory.",
    )
    parser.add_argument("path", nargs="?", help="Path to one prepared JSON file.")
    parser.add_argument("--all", dest="all_dir", help="Summarize every *-prepared.json in this directory.")
    args = parser.parse_args(argv)
    if bool(args.path) == bool(args.all_dir):
        parser.error("provide exactly one of <path-to-prepared.json> or --all <dir>")
    return args


def main(argv: list[str] | None = None) -> int:
    """Run the CLI."""
    args = parse_args(sys.argv[1:] if argv is None else argv)

    try:
        if args.all_dir:
            directory = Path(args.all_dir).expanduser()
            files = iter_prepared_files(directory)
            if not files:
                print(f"No *-prepared.json files found in {directory}", file=sys.stderr)
                return 1
            for index, path in enumerate(files):
                if index:
                    print()
                print(summarize_file(path))
            return 0

        print(summarize_file(Path(args.path).expanduser()))
        return 0
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
