"""Tests for the per-listing image cache helper used by ``sussed enrich``.

The enrich command's outer ``except httpx.HTTPStatusError`` block is for the
*details* fetch (it special-cases 410 -> SOLD). Image-download failures must
NOT bubble up into that block — otherwise an image HTTP error would either
get mis-labeled as the listing being gone, or fall through to the generic
``except Exception`` handler and bypass the friendly "📷 partial" UX.

We test the inner helper directly so the regression is caught even if the
surrounding CLI control flow is later restructured.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import httpx
import pytest

from sussed.cli import _cache_listing_images_for_enrich

if TYPE_CHECKING:
    from pathlib import Path


class _StubListing:
    """Minimal duck-type stand-in for the SQLModel ``Listing`` row."""

    def __init__(self, image_urls: list[str] | None) -> None:
        self.id = uuid4()
        self.image_urls = image_urls


@pytest.mark.asyncio
async def test_cache_listing_images_for_enrich_returns_zero_when_disabled(
    tmp_path: Path,
) -> None:
    listing = _StubListing(image_urls=["https://example.com/photo.jpg"])

    result = await _cache_listing_images_for_enrich(
        listing=listing,  # type: ignore[arg-type]
        cache_root=tmp_path,
        image_limit=0,
    )

    assert result == {"saved": 0, "status": "skipped"}


@pytest.mark.asyncio
async def test_cache_listing_images_for_enrich_returns_zero_when_no_urls(
    tmp_path: Path,
) -> None:
    listing = _StubListing(image_urls=None)

    result = await _cache_listing_images_for_enrich(
        listing=listing,  # type: ignore[arg-type]
        cache_root=tmp_path,
        image_limit=5,
    )

    assert result == {"saved": 0, "status": "skipped"}


@pytest.mark.asyncio
async def test_cache_listing_images_for_enrich_returns_saved_count_on_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    listing = _StubListing(image_urls=["https://example.com/a.jpg", "https://example.com/b.jpg"])

    async def fake_download(
        image_urls: list[str], destination_dir: object, limit: int
    ) -> list[str]:
        assert image_urls == listing.image_urls
        assert limit == 5
        return [str(destination_dir) + "/image-1.jpg", str(destination_dir) + "/image-2.jpg"]

    monkeypatch.setattr("sussed.cli.download_listing_images", fake_download)

    result = await _cache_listing_images_for_enrich(
        listing=listing,  # type: ignore[arg-type]
        cache_root=tmp_path,
        image_limit=5,
    )

    assert result == {"saved": 2, "status": "ok"}


@pytest.mark.asyncio
async def test_cache_listing_images_for_enrich_handles_http_status_error_as_partial(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An HTTPStatusError from one image must NOT crash the enrich loop.

    This is the bug fix: the previous control flow let HTTPStatusError escape
    into the outer details-fetch handler, hiding the friendly partial UX.
    """
    listing = _StubListing(image_urls=["https://example.com/photo.jpg"])

    async def fake_download(*_args: object, **_kwargs: object) -> list[str]:
        request = httpx.Request("GET", "https://example.com/photo.jpg")
        response = httpx.Response(503, request=request)
        raise httpx.HTTPStatusError("boom", request=request, response=response)

    monkeypatch.setattr("sussed.cli.download_listing_images", fake_download)

    result = await _cache_listing_images_for_enrich(
        listing=listing,  # type: ignore[arg-type]
        cache_root=tmp_path,
        image_limit=3,
    )

    assert result == {"saved": 0, "status": "partial"}


@pytest.mark.asyncio
async def test_cache_listing_images_for_enrich_handles_generic_http_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    listing = _StubListing(image_urls=["https://example.com/photo.jpg"])

    async def fake_download(*_args: object, **_kwargs: object) -> list[str]:
        raise httpx.ConnectError("network down")

    monkeypatch.setattr("sussed.cli.download_listing_images", fake_download)

    result = await _cache_listing_images_for_enrich(
        listing=listing,  # type: ignore[arg-type]
        cache_root=tmp_path,
        image_limit=3,
    )

    assert result == {"saved": 0, "status": "partial"}


@pytest.mark.asyncio
async def test_cache_listing_images_for_enrich_handles_disk_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    listing = _StubListing(image_urls=["https://example.com/photo.jpg"])

    async def fake_download(*_args: object, **_kwargs: object) -> list[str]:
        raise OSError("disk full")

    monkeypatch.setattr("sussed.cli.download_listing_images", fake_download)

    result = await _cache_listing_images_for_enrich(
        listing=listing,  # type: ignore[arg-type]
        cache_root=tmp_path,
        image_limit=3,
    )

    assert result == {"saved": 0, "status": "partial"}


@pytest.mark.asyncio
async def test_cache_listing_images_for_enrich_does_not_swallow_unexpected_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """We must catch broadly enough (HTTPError + OSError) but NOT bare ``Exception``.

    Truly unexpected errors should bubble to the outer handler so the user
    actually finds out something is wrong.
    """
    listing = _StubListing(image_urls=["https://example.com/photo.jpg"])

    async def fake_download(*_args: object, **_kwargs: object) -> list[str]:
        raise RuntimeError("programmer error")

    monkeypatch.setattr("sussed.cli.download_listing_images", fake_download)

    with pytest.raises(RuntimeError, match="programmer error"):
        await _cache_listing_images_for_enrich(
            listing=listing,  # type: ignore[arg-type]
            cache_root=tmp_path,
            image_limit=3,
        )
