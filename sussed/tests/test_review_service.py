from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy.dialects import postgresql

from sussed.db.models import (
    Listing,
    ListingReview,
    ListingStatus,
    ListingType,
    PropertyCategory,
    VibeCheck,
)
from sussed.db.operations import get_listings
from sussed.review.models import ReviewResultInput, ReviewVibe
from sussed.review.service import (
    candidate_priority,
    download_listing_images,
    get_reviewed_picks,
    list_cached_image_paths,
    map_review_vibe,
    prepare_review_payload_from_listing,
    save_listing_review,
)


def utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def make_listing(**overrides: object) -> Listing:
    now = utcnow_naive()
    data = {
        "id": uuid4(),
        "source": "sreality",
        "external_id": "123",
        "url": "https://example.com/123",
        "title": "Prodej bytu 2+kk 52 m2",
        "description": "Nice flat with balcony and parking available.",
        "price_czk": 5_800_000,
        "price_per_m2": 111_538,
        "listing_type": ListingType.SALE,
        "city": "Brno",
        "district": "Královo Pole",
        "property_category": PropertyCategory.APARTMENT,
        "apartment_type": "2+kk",
        "area_m2": Decimal("52"),
        "features": {"balcony": True, "parking": True},
        "raw_labels": ["Balkon", "Parkování"],
        "image_urls": ["https://example.com/1.jpg"],
        "image_count": 1,
        "status": ListingStatus.ACTIVE,
        "first_seen_at": now - timedelta(days=3),
        "last_seen_at": now,
        "created_at": now,
        "updated_at": now,
    }
    data.update(overrides)
    return Listing(**data)


class EmptyScalars:
    def all(self) -> list[Listing]:
        return []


class EmptyResult:
    def scalars(self) -> EmptyScalars:
        return EmptyScalars()


class RecordingSession:
    statement: Any | None = None

    async def execute(self, statement: Any) -> EmptyResult:
        self.statement = statement
        return EmptyResult()


class ReviewPersistenceSession:
    def __init__(self, flushed_review_id: UUID) -> None:
        self.flushed_review_id = flushed_review_id
        self.flush_count = 0
        self.added: list[object] = []
        self.review: ListingReview | None = None

    def add(self, obj: object) -> None:
        if isinstance(obj, ListingReview):
            self.review = obj
            obj.id = None  # type: ignore[assignment]
        self.added.append(obj)

    async def flush(self) -> None:
        self.flush_count += 1
        assert self.review is not None
        self.review.id = self.flushed_review_id


def test_candidate_priority_prefers_never_reviewed() -> None:
    never_reviewed = make_listing(ai_reviewed_at=None)
    reviewed = make_listing(ai_reviewed_at=utcnow_naive(), ai_score=700)

    assert candidate_priority(never_reviewed) < candidate_priority(reviewed)


def test_candidate_priority_prioritizes_price_changed_since_review() -> None:
    now = utcnow_naive()
    stale_due_to_price = make_listing(
        ai_reviewed_at=now - timedelta(days=1),
        last_price_change_at=now,
        ai_score=700,
    )
    reviewed = make_listing(
        ai_reviewed_at=now,
        last_price_change_at=now - timedelta(days=2),
        ai_score=700,
    )

    assert candidate_priority(stale_due_to_price) < candidate_priority(reviewed)


def test_prepare_review_payload_includes_image_urls_paths_and_stable_hash() -> None:
    listing = make_listing()
    image_paths = ["review-images/123/image-1.jpg"]
    detail_items = [{"name": "Aktualizace", "value": "26.05.2026"}]
    price_history: list[dict[str, object]] = []

    payload = prepare_review_payload_from_listing(
        listing=listing,
        image_paths=image_paths,
        detail_items=detail_items,
        price_history=price_history,
    )
    repeated_payload = prepare_review_payload_from_listing(
        listing=listing,
        image_paths=image_paths,
        detail_items=detail_items,
        price_history=price_history,
    )

    assert payload.listing_id == listing.id
    assert payload.image_urls == ["https://example.com/1.jpg"]
    assert payload.image_paths == ["review-images/123/image-1.jpg"]
    assert payload.input_hash == repeated_payload.input_hash
    assert len(payload.input_hash) == 64


def test_map_review_vibe_maps_to_db_enum() -> None:
    assert map_review_vibe(ReviewVibe.PEAK) == VibeCheck.PEAK
    assert map_review_vibe(ReviewVibe.VALID) == VibeCheck.VALID
    assert map_review_vibe(ReviewVibe.MID) == VibeCheck.MID
    assert map_review_vibe(ReviewVibe.SUS) == VibeCheck.SUS


def test_review_payload_roundtrip_for_save_contract() -> None:
    result = ReviewResultInput(
        score=725,
        vibe=ReviewVibe.VALID,
        confidence=0.8,
        recommendation="CONSIDER",
        score_reason="Good but not a unicorn.",
        summary="Solid option.",
        reviewer_name="sussed-ai-review",
        input_hash="inputhash",
    )

    dumped = result.model_dump(mode="json")

    assert dumped["score"] == 725
    assert ReviewResultInput.model_validate(dumped) == result


@pytest.mark.asyncio
async def test_save_listing_review_inserts_review_and_updates_listing_denormalized_fields() -> None:
    listing = make_listing(
        ai_score=None,
        ai_vibe=None,
        ai_summary=None,
        ai_reviewed_at=None,
        ai_review_id=None,
        vibe_check=VibeCheck.UNKNOWN,
        ai_analysis={"stale": True},
    )
    original_updated_at = listing.updated_at
    reviewed_at = datetime(2026, 5, 26, 12, 0, 0)
    review_input = ReviewResultInput(
        score=842,
        vibe=ReviewVibe.PEAK,
        confidence=0.88,
        recommendation="CONSIDER",
        score_reason="Below-market 2+kk with a separate parking cost.",
        summary="Strong listing with a parking-price caveat.",
        red_flags=["Parking not included"],
        yellow_flags=["Verify HOA fees"],
        highlights=["Good floor plan", "Bright interior"],
        hidden_costs={"parking": 450_000},
        parking_price=450_000,
        parking_included=False,
        usable_area_m2=52.4,
        photo_observations=["Kitchen looks smaller than description implies."],
        reviewer_name="sussed-ai-review",
        reviewer_model="copilot-cli",
        reviewer_session="session-123",
        input_hash="inputhash",
        reviewed_at=reviewed_at,
        raw_review={"raw": "payload"},
    )
    flushed_review_id = uuid4()
    session = ReviewPersistenceSession(flushed_review_id)

    review = await save_listing_review(session, listing, review_input)  # type: ignore[arg-type]

    assert session.flush_count == 1
    assert session.added[0] is review
    assert session.added[-1] is listing
    assert review.id == flushed_review_id
    assert review.listing_id == listing.id
    assert review.reviewer_type == "skill"
    assert review.reviewer_name == "sussed-ai-review"
    assert review.reviewer_model == "copilot-cli"
    assert review.reviewer_session == "session-123"
    assert review.score == 842
    assert review.vibe == VibeCheck.PEAK
    assert review.confidence == Decimal("0.88")
    assert review.recommendation == "CONSIDER"
    assert review.score_reason == "Below-market 2+kk with a separate parking cost."
    assert review.summary == "Strong listing with a parking-price caveat."
    assert review.red_flags == ["Parking not included"]
    assert review.yellow_flags == ["Verify HOA fees"]
    assert review.highlights == ["Good floor plan", "Bright interior"]
    assert review.hidden_costs == {"parking": 450_000}
    assert review.parking_price == 450_000
    assert review.parking_included is False
    assert review.usable_area_m2 == Decimal("52.4")
    assert review.photo_observations == ["Kitchen looks smaller than description implies."]
    assert review.input_hash == "inputhash"
    assert review.raw_review == {"raw": "payload"}
    assert review.reviewed_at == reviewed_at

    assert listing.ai_score == 842
    assert listing.ai_vibe == VibeCheck.PEAK
    assert listing.ai_summary == "Strong listing with a parking-price caveat."
    assert listing.ai_reviewed_at == reviewed_at
    assert listing.ai_review_id == flushed_review_id
    assert listing.vibe_check == VibeCheck.PEAK
    assert listing.updated_at >= original_updated_at

    assert listing.ai_analysis == {
        "score": 842,
        "vibe": "peak",
        "confidence": 0.88,
        "recommendation": "CONSIDER",
        "score_reason": "Below-market 2+kk with a separate parking cost.",
        "summary": "Strong listing with a parking-price caveat.",
        "red_flags": ["Parking not included"],
        "yellow_flags": ["Verify HOA fees"],
        "highlights": ["Good floor plan", "Bright interior"],
        "hidden_costs": {"parking": 450_000},
        "parking_price": 450_000,
        "parking_included": False,
        "usable_area_m2": 52.4,
        "photo_observations": ["Kitchen looks smaller than description implies."],
        "reviewed_at": reviewed_at.isoformat(),
        "review_id": str(flushed_review_id),
        "reviewer_name": "sussed-ai-review",
        "reviewer_model": "copilot-cli",
    }


@pytest.mark.asyncio
async def test_get_reviewed_picks_returns_active_reviewed_listings_by_default() -> None:
    session = RecordingSession()

    result = await get_reviewed_picks(session)  # type: ignore[arg-type]

    assert result == []
    assert session.statement is not None
    compiled = str(
        session.statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    normalized_sql = " ".join(compiled.split())

    assert "listings.status = 'ACTIVE'" in normalized_sql
    assert "listings.ai_reviewed_at IS NOT NULL" in normalized_sql
    assert "ORDER BY listings.ai_score DESC NULLS LAST, listings.price_per_m2 ASC" in normalized_sql
    assert "LIMIT 20" in normalized_sql


@pytest.mark.asyncio
async def test_get_reviewed_picks_applies_optional_filters() -> None:
    session = RecordingSession()

    await get_reviewed_picks(
        session,
        include_unreviewed=True,
        district="Pole",
        min_score=700,
        limit=5,
    )  # type: ignore[arg-type]

    assert session.statement is not None
    compiled = str(
        session.statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    normalized_sql = " ".join(compiled.split())

    assert "listings.status = 'ACTIVE'" in normalized_sql
    assert "listings.ai_reviewed_at IS NOT NULL" not in normalized_sql
    assert "listings.district ILIKE '%%Pole%%'" in normalized_sql
    assert "listings.ai_score >= 700" in normalized_sql
    assert "LIMIT 5" in normalized_sql


@pytest.mark.asyncio
@pytest.mark.parametrize(("has_garage", "expected_sql_value"), [(True, "true"), (False, "false")])
async def test_get_listings_filters_by_structured_garage_feature(
    has_garage: bool, expected_sql_value: str
) -> None:
    session = RecordingSession()

    await get_listings(session, has_garage=has_garage)  # type: ignore[arg-type]

    assert session.statement is not None
    compiled = str(
        session.statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    normalized_sql = " ".join(compiled.split())

    assert (
        f"CAST((listings.features ->> 'garage') AS BOOLEAN) = {expected_sql_value}"
        in normalized_sql
    )


def test_list_cached_image_paths_returns_empty_for_missing_dir(tmp_path: Path) -> None:
    listing_id = uuid4()

    result = list_cached_image_paths(tmp_path / "image-cache", listing_id, limit=5)

    assert result == []


def test_list_cached_image_paths_returns_empty_when_no_files(tmp_path: Path) -> None:
    listing_id = uuid4()
    cache_root = tmp_path / "image-cache"
    (cache_root / str(listing_id)).mkdir(parents=True)

    result = list_cached_image_paths(cache_root, listing_id, limit=5)

    assert result == []


def test_list_cached_image_paths_returns_sorted_paths_and_applies_limit(
    tmp_path: Path,
) -> None:
    listing_id = uuid4()
    cache_dir = tmp_path / "image-cache" / str(listing_id)
    cache_dir.mkdir(parents=True)

    # Create out of natural order to prove we sort
    for name in ("image-10.jpg", "image-1.jpg", "image-2.jpg", "image-3.jpg"):
        (cache_dir / name).write_bytes(b"data")

    result = list_cached_image_paths(tmp_path / "image-cache", listing_id, limit=3)

    assert result == [
        str(cache_dir / "image-1.jpg"),
        str(cache_dir / "image-2.jpg"),
        str(cache_dir / "image-3.jpg"),
    ]


def test_list_cached_image_paths_ignores_zero_byte_files(tmp_path: Path) -> None:
    listing_id = uuid4()
    cache_dir = tmp_path / "image-cache" / str(listing_id)
    cache_dir.mkdir(parents=True)
    (cache_dir / "image-1.jpg").write_bytes(b"")  # empty/corrupt
    (cache_dir / "image-2.jpg").write_bytes(b"ok")

    result = list_cached_image_paths(tmp_path / "image-cache", listing_id, limit=5)

    assert result == [str(cache_dir / "image-2.jpg")]


def test_list_cached_image_paths_accepts_string_listing_id(tmp_path: Path) -> None:
    listing_id = uuid4()
    cache_dir = tmp_path / "image-cache" / str(listing_id)
    cache_dir.mkdir(parents=True)
    (cache_dir / "image-1.jpg").write_bytes(b"ok")

    result = list_cached_image_paths(tmp_path / "image-cache", str(listing_id), limit=5)

    assert result == [str(cache_dir / "image-1.jpg")]


# -------------------------
# download_listing_images atomic-write tests
# -------------------------


class _FakeResponse:
    def __init__(self, content: bytes, status_code: int = 200) -> None:
        self.content = content
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import httpx

            request = httpx.Request("GET", "https://example.com/photo.jpg")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=request, response=response
            )


class _FakeAsyncClient:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = list(responses)
        self.requested_urls: list[str] = []

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def get(self, url: str) -> _FakeResponse:
        self.requested_urls.append(url)
        if not self._responses:
            raise AssertionError("No more fake responses queued")
        return self._responses.pop(0)


def _patch_async_client(monkeypatch: pytest.MonkeyPatch, fake_client: _FakeAsyncClient) -> None:
    def factory(*_args: object, **_kwargs: object) -> _FakeAsyncClient:
        return fake_client

    import sussed.review.service as service_module

    monkeypatch.setattr(service_module.httpx, "AsyncClient", factory)


@pytest.mark.asyncio
async def test_download_listing_images_writes_via_temp_then_renames(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cache write must be atomic: write to a .tmp file, then rename.

    This prevents a concurrent reader (e.g. a parallel enrich/hunt run)
    from ever observing a half-written image at the final cached path.
    """
    fake_client = _FakeAsyncClient([_FakeResponse(b"image-bytes")])
    _patch_async_client(monkeypatch, fake_client)

    destination = tmp_path / "listing-xyz"

    # Track ordering of fs writes vs renames so we can assert atomicity.
    original_write_bytes = Path.write_bytes
    original_replace = Path.replace
    events: list[tuple[str, str]] = []

    def tracking_write_bytes(self: Path, data: bytes) -> int:
        events.append(("write", self.name))
        return original_write_bytes(self, data)

    def tracking_replace(self: Path, target: Path) -> Path:
        events.append(("replace", f"{self.name}->{Path(target).name}"))
        return original_replace(self, target)

    monkeypatch.setattr(Path, "write_bytes", tracking_write_bytes)
    monkeypatch.setattr(Path, "replace", tracking_replace)

    paths = await download_listing_images(["https://example.com/photo.jpg"], destination, limit=1)

    assert len(paths) == 1
    final_path = Path(paths[0])
    assert final_path.exists()
    assert final_path.read_bytes() == b"image-bytes"

    # The .tmp file must NOT exist after a successful rename.
    leftover_tmps = list(destination.glob("*.tmp"))
    assert leftover_tmps == [], f"unexpected leftover tmp files: {leftover_tmps}"

    # We must have written to a .tmp path first, then renamed to the final path.
    write_events = [name for kind, name in events if kind == "write"]
    replace_events = [name for kind, name in events if kind == "replace"]
    assert write_events, "expected at least one write event"
    assert all(name.endswith(".tmp") for name in write_events), (
        f"image bytes should be written to a .tmp path first, got: {write_events}"
    )
    assert replace_events, "expected the tmp file to be replaced/renamed into place"
    # Order: tmp write must happen BEFORE the rename.
    first_write_idx = next(i for i, (k, _) in enumerate(events) if k == "write")
    first_replace_idx = next(i for i, (k, _) in enumerate(events) if k == "replace")
    assert first_write_idx < first_replace_idx


@pytest.mark.asyncio
async def test_download_listing_images_leaves_no_tmp_on_http_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the HTTP fetch raises, no stale .tmp file may be left behind."""
    import httpx

    fake_client = _FakeAsyncClient([_FakeResponse(b"", status_code=500)])
    _patch_async_client(monkeypatch, fake_client)

    destination = tmp_path / "listing-err"

    with pytest.raises(httpx.HTTPStatusError):
        await download_listing_images(["https://example.com/broken.jpg"], destination, limit=1)

    assert list(destination.glob("*.tmp")) == []
    # The final cache path must also not exist (no partial write observable).
    assert list(destination.glob("image-*.jpg")) == []


@pytest.mark.asyncio
async def test_download_listing_images_cleans_up_tmp_when_write_bytes_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If write_bytes raises mid-write, the partial .tmp file must be removed."""
    fake_client = _FakeAsyncClient([_FakeResponse(b"data")])
    _patch_async_client(monkeypatch, fake_client)

    destination = tmp_path / "listing-disk-fail"
    destination.mkdir(parents=True, exist_ok=True)

    original_write_bytes = Path.write_bytes

    def exploding_write_bytes(self: Path, data: bytes) -> int:
        # Simulate a disk error AFTER creating a stub .tmp file on disk.
        if self.name.endswith(".tmp"):
            # Touch the tmp file so we can prove cleanup happens even if
            # bytes were partially flushed before the failure.
            self.touch()
            raise OSError("simulated disk failure")
        return original_write_bytes(self, data)

    monkeypatch.setattr(Path, "write_bytes", exploding_write_bytes)

    with pytest.raises(OSError, match="simulated disk failure"):
        await download_listing_images(["https://example.com/photo.jpg"], destination, limit=1)

    assert list(destination.glob("*.tmp")) == []


@pytest.mark.asyncio
async def test_download_listing_images_skips_existing_non_empty_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Existing valid cache entries must short-circuit (no HTTP, no rename)."""
    fake_client = _FakeAsyncClient([])  # zero responses queued -> assert no fetch
    _patch_async_client(monkeypatch, fake_client)

    destination = tmp_path / "listing-cached"
    destination.mkdir(parents=True, exist_ok=True)
    cached = destination / "image-1.jpg"
    cached.write_bytes(b"already-here")

    paths = await download_listing_images(["https://example.com/photo.jpg"], destination, limit=1)

    assert paths == [str(cached)]
    assert fake_client.requested_urls == []
    assert list(destination.glob("*.tmp")) == []
