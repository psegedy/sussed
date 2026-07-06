"""Tests for the `sussed refresh` CLI command."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from typer.testing import CliRunner

from sussed.cli import app

runner = CliRunner()


def test_refresh_command_invokes_run_refresh() -> None:
    fake = AsyncMock(
        return_value={
            "checked": 3,
            "removed": 1,
            "price_changes": 1,
            "updated": 2,
            "errors": 0,
        }
    )
    with patch("sussed.scrapers.refresh.run_refresh", fake):
        result = runner.invoke(
            app, ["refresh", "--source", "sreality", "--limit", "5", "--dry-run"]
        )
    assert result.exit_code == 0, result.output
    assert "Checked" in result.output or "checked" in result.output
    assert fake.await_count == 1
    assert fake.call_args.kwargs["dry_run"] is True
    assert fake.call_args.kwargs["limit"] == 5
