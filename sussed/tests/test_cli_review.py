from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from typer.testing import CliRunner

from sussed.cli import _validate_partial_uuid_prefix, app

if TYPE_CHECKING:
    from pathlib import Path


def test_validate_partial_uuid_prefix_accepts_hex_prefix() -> None:
    assert _validate_partial_uuid_prefix("abcdef12") == "abcdef12"


@pytest.mark.parametrize("value", ["abc%' OR 1=1 --", "xyz", "", "123/456"])
def test_validate_partial_uuid_prefix_rejects_unsafe_input(value: str) -> None:
    with pytest.raises(ValueError):
        _validate_partial_uuid_prefix(value)


def test_review_json_file_contract(tmp_path: Path) -> None:
    path = tmp_path / "review.json"
    path.write_text(
        """
{
  "score": 700,
  "vibe": "valid",
  "confidence": 0.8,
  "recommendation": "CONSIDER",
  "score_reason": "Good enough to inspect.",
  "summary": "Solid listing.",
  "reviewer_name": "sussed-ai-review",
  "input_hash": "hash"
}
""".strip(),
        encoding="utf-8",
    )

    from sussed.review.models import ReviewResultInput

    review = ReviewResultInput.model_validate_json(path.read_text(encoding="utf-8"))
    assert review.score == 700


def test_review_help_lists_review_commands() -> None:
    result = CliRunner().invoke(app, ["review", "--help"])

    assert result.exit_code == 0
    for command in ("candidates", "prepare", "prepare-batch", "save", "status", "picks"):
        assert command in result.output


def test_review_prepare_help_defaults_cache_to_sussed_image_cache() -> None:
    result = CliRunner().invoke(app, ["review", "prepare", "--help"])

    assert result.exit_code == 0
    assert ".sussed/image-cache" in result.output


def test_enrich_help_lists_image_options() -> None:
    result = CliRunner().invoke(app, ["enrich", "--help"])

    assert result.exit_code == 0
    assert "--cache-dir" in result.output
    assert "--image-limit" in result.output


def test_review_picks_json_outputs_ranked_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json
    from contextlib import asynccontextmanager
    from datetime import datetime
    from decimal import Decimal

    from sussed.db.models import Listing, ListingStatus, ListingType, PropertyCategory, VibeCheck

    calls: list[dict[str, object]] = []
    listing = Listing(
        id=uuid4(),
        source="sreality",
        external_id="777",
        url="https://example.com/777",
        title="Prodej bytu 2+kk",
        price_czk=6_100_000,
        price_per_m2=117_308,
        listing_type=ListingType.SALE,
        city="Brno",
        district="Královo Pole",
        property_category=PropertyCategory.APARTMENT,
        apartment_type="2+kk",
        area_m2=Decimal("52"),
        floor=3,
        ai_score=812,
        ai_vibe=VibeCheck.PEAK,
        ai_summary="Absolute heater.",
        ai_analysis={"parking_price": 350_000, "parking_included": False},
        first_seen_at=datetime(2025, 1, 2, 3, 4, 5),
        status=ListingStatus.ACTIVE,
    )

    class FakeSession:
        pass

    @asynccontextmanager
    async def fake_get_session():
        yield FakeSession()

    async def fake_get_reviewed_picks(session: object, **kwargs: object) -> list[Listing]:
        calls.append({"session": session, **kwargs})
        return [listing]

    monkeypatch.setattr("sussed.db.connection.get_session", fake_get_session)
    monkeypatch.setattr("sussed.review.service.get_reviewed_picks", fake_get_reviewed_picks)

    result = CliRunner().invoke(
        app,
        [
            "review",
            "picks",
            "--all",
            "--district",
            "Pole",
            "--min-score",
            "700",
            "--limit",
            "5",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls == [
        {
            "session": calls[0]["session"],
            "include_unreviewed": True,
            "district": "Pole",
            "min_score": 700,
            "max_age_days": None,
            "limit": 5,
        }
    ]
    payload = json.loads(result.output)
    assert payload == [
        {
            "id": str(listing.id),
            "external_id": "777",
            "title": "Prodej bytu 2+kk",
            "district": "Královo Pole",
            "price_czk": 6100000,
            "price_per_m2": 117308,
            "area_m2": 52.0,
            "apartment_type": "2+kk",
            "floor": 3,
            "ai_score": 812,
            "ai_vibe": "peak",
            "ai_summary": "Absolute heater.",
            "parking_price": 350000,
            "parking_included": False,
            "usable_area_m2": None,
            "updated_at_source": None,
            "first_seen_at": "2025-01-02T03:04:05",
            "url": "https://example.com/777",
        }
    ]


def test_review_picks_table_outputs_parking_and_added_columns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from contextlib import asynccontextmanager
    from datetime import datetime
    from decimal import Decimal

    from rich.console import Console

    from sussed.db.models import Listing, ListingStatus, ListingType, PropertyCategory, VibeCheck

    listing = Listing(
        id=uuid4(),
        source="sreality",
        external_id="778",
        url="https://example.com/778",
        title="Prodej bytu 3+kk",
        price_czk=7_200_000,
        price_per_m2=120_000,
        listing_type=ListingType.SALE,
        city="Brno",
        district="Veveří",
        property_category=PropertyCategory.APARTMENT,
        area_m2=Decimal("60"),
        ai_score=900,
        ai_vibe=VibeCheck.PEAK,
        ai_summary="Great place.",
        ai_analysis={"parking_price": 0, "parking_included": True},
        first_seen_at=datetime(2025, 1, 2, 3, 4, 5),
        status=ListingStatus.ACTIVE,
    )

    class FakeSession:
        pass

    @asynccontextmanager
    async def fake_get_session():
        yield FakeSession()

    async def fake_get_reviewed_picks(_session: object, **_kwargs: object) -> list[Listing]:
        return [listing]

    monkeypatch.setattr("sussed.db.connection.get_session", fake_get_session)
    monkeypatch.setattr("sussed.review.service.get_reviewed_picks", fake_get_reviewed_picks)
    monkeypatch.setattr("sussed.cli.console", Console(width=200, color_system=None))

    result = CliRunner().invoke(app, ["review", "picks", "--limit", "1"])

    assert result.exit_code == 0, result.output
    assert "Parking" in result.output
    assert "Source Date" in result.output
    assert "✓ incl" in result.output
    assert "2025-01-02" in result.output


def test_review_prepare_reads_from_cache_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`review prepare` must read existing cached images and NOT download anything."""
    import json
    from contextlib import asynccontextmanager

    from sussed.db.models import Listing, ListingStatus, ListingType, PropertyCategory

    listing_id = uuid4()
    cache_root = tmp_path / "image-cache"
    listing_cache = cache_root / str(listing_id)
    listing_cache.mkdir(parents=True)
    (listing_cache / "image-1.jpg").write_bytes(b"img1")
    (listing_cache / "image-2.jpg").write_bytes(b"img2")

    listing = Listing(
        id=listing_id,
        source="sreality",
        external_id="999",
        url="https://example.com/999",
        title="Prodej bytu 2+kk",
        price_czk=5_000_000,
        price_per_m2=100_000,
        listing_type=ListingType.SALE,
        city="Brno",
        property_category=PropertyCategory.APARTMENT,
        image_urls=["https://example.com/never-downloaded.jpg"],
        image_count=2,
        status=ListingStatus.ACTIVE,
    )

    class FakeScalars:
        def all(self) -> list[Listing]:
            return [listing]

    class FakeResult:
        def scalars(self) -> FakeScalars:
            return FakeScalars()

    class FakeSession:
        async def execute(self, _statement: object) -> FakeResult:
            return FakeResult()

    @asynccontextmanager
    async def fake_get_session():
        yield FakeSession()

    monkeypatch.setattr("sussed.db.connection.get_session", fake_get_session)

    async def fake_price_history(_session: object, _listing_id: object) -> list:
        return []

    monkeypatch.setattr(
        "sussed.review.service.get_price_history_payload",
        fake_price_history,
    )

    def boom(*_args: object, **_kwargs: object):
        raise AssertionError("review prepare must not download images")

    monkeypatch.setattr("sussed.review.service.download_listing_images", boom)

    output_file = tmp_path / "prepared.json"
    result = CliRunner().invoke(
        app,
        [
            "review",
            "prepare",
            listing_id.hex[:8],
            "--cache-dir",
            str(cache_root),
            "--output",
            str(output_file),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(output_file.read_text(encoding="utf-8"))
    assert payload["image_paths"] == [
        str(listing_cache / "image-1.jpg"),
        str(listing_cache / "image-2.jpg"),
    ]


def test_review_prepare_returns_empty_image_paths_when_cache_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import json
    from contextlib import asynccontextmanager

    from sussed.db.models import Listing, ListingStatus, ListingType, PropertyCategory

    listing_id = uuid4()
    cache_root = tmp_path / "image-cache"  # nothing in it

    listing = Listing(
        id=listing_id,
        source="sreality",
        external_id="888",
        url="https://example.com/888",
        title="Prodej bytu",
        price_czk=4_000_000,
        listing_type=ListingType.SALE,
        city="Brno",
        property_category=PropertyCategory.APARTMENT,
        image_urls=["https://example.com/photo.jpg"],
        image_count=1,
        status=ListingStatus.ACTIVE,
    )

    class FakeScalars:
        def all(self) -> list[Listing]:
            return [listing]

    class FakeResult:
        def scalars(self) -> FakeScalars:
            return FakeScalars()

    class FakeSession:
        async def execute(self, _statement: object) -> FakeResult:
            return FakeResult()

    @asynccontextmanager
    async def fake_get_session():
        yield FakeSession()

    monkeypatch.setattr("sussed.db.connection.get_session", fake_get_session)

    async def fake_price_history(_session: object, _listing_id: object) -> list:
        return []

    monkeypatch.setattr(
        "sussed.review.service.get_price_history_payload",
        fake_price_history,
    )

    output_file = tmp_path / "prepared.json"
    result = CliRunner().invoke(
        app,
        [
            "review",
            "prepare",
            listing_id.hex[:8],
            "--cache-dir",
            str(cache_root),
            "--output",
            str(output_file),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(output_file.read_text(encoding="utf-8"))
    assert payload["image_paths"] == []
