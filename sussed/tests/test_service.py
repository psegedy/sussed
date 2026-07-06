"""Tests for the scheduled-service helpers and template rendering."""

from __future__ import annotations

import shlex

import pytest

from sussed import service
from sussed.service_templates import COPILOT_PROMPT


class TestParseTime:
    def test_parses_valid_time(self) -> None:
        assert service._parse_time("10:30") == (10, 30)

    def test_parses_midnight(self) -> None:
        assert service._parse_time("00:00") == (0, 0)

    def test_parses_end_of_day(self) -> None:
        assert service._parse_time("23:59") == (23, 59)

    def test_strips_whitespace(self) -> None:
        assert service._parse_time("  07:05  ") == (7, 5)

    @pytest.mark.parametrize(
        "bad",
        ["25:00", "10:60", "abc", "1030", "10:30:00", "", "-1:00", "10:-5"],
    )
    def test_rejects_invalid(self, bad: str) -> None:
        with pytest.raises(ValueError):
            service._parse_time(bad)


class TestDetectOS:
    def test_darwin(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(service.platform, "system", lambda: "Darwin")
        assert service._detect_os() == "darwin"

    def test_linux(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(service.platform, "system", lambda: "Linux")
        assert service._detect_os() == "linux"

    def test_unsupported_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(service.platform, "system", lambda: "Windows")
        with pytest.raises(RuntimeError):
            service._detect_os()


class TestBuildPathDirs:
    def test_includes_binary_dirs_and_system_defaults(self) -> None:
        result = service._build_path_dirs("/opt/homebrew/bin/uv", "/usr/local/bin/copilot")
        parts = result.split(":")
        assert "/opt/homebrew/bin" in parts
        assert "/usr/local/bin" in parts
        assert "/usr/bin" in parts
        assert "/bin" in parts

    def test_no_duplicate_dirs(self) -> None:
        # Both binaries live in /usr/local/bin -> should appear once
        result = service._build_path_dirs("/usr/local/bin/uv", "/usr/local/bin/copilot")
        parts = result.split(":")
        assert parts.count("/usr/local/bin") == 1


class TestRenderShellScript:
    def _render(self, **overrides: str) -> str:
        kwargs = {
            "config_path": "/home/u/search_config.yaml",
            "project_dir": "/home/u/sussed",
            "uv_path": "/usr/local/bin/uv",
            "copilot_path": "/usr/local/bin/copilot",
            "sched_hhmm": "1000",
            "path_dirs": "/usr/local/bin:/usr/bin:/bin",
        }
        kwargs.update(overrides)
        return service.render_shell_script(**kwargs)

    def test_starts_with_shebang(self) -> None:
        assert self._render().startswith("#!/bin/bash")

    def test_no_unreplaced_tokens(self) -> None:
        assert "__" not in self._render()

    def test_embeds_prompt(self) -> None:
        script = self._render()
        first_line = COPILOT_PROMPT.splitlines()[0]
        assert first_line in script

    def test_contains_lock(self) -> None:
        assert 'mkdir "$LOCK_DIR"' in self._render()

    def test_contains_schedule_guard(self) -> None:
        script = self._render(sched_hhmm="1000")
        assert "10#$NOW_HHMM" in script
        assert "SCHED_HHMM=1000" in script

    def test_shell_quotes_path_with_space(self) -> None:
        script = self._render(config_path="/home/u/my configs/search.yaml")
        assert shlex.quote("/home/u/my configs/search.yaml") in script

    def test_shell_quotes_path_with_single_quote(self) -> None:
        tricky = "/home/u/o'brien/search.yaml"
        script = self._render(config_path=tricky)
        assert shlex.quote(tricky) in script

    def test_scoped_copilot_permissions(self) -> None:
        script = self._render()
        # Inspect the actual copilot invocation line, not the security-note comment.
        invocation = next(line for line in script.splitlines() if '"$COPILOT_BIN" -p' in line)
        assert "--allow-all-tools" in invocation
        assert "--allow-all-paths" in invocation
        assert "--no-ask-user" in invocation
        assert "--allow-all-urls" not in invocation

    def test_notification_uses_tilde_abbreviated_path(self) -> None:
        # The success notification must show the short ~-path, not the full one.
        script = self._render()
        assert 'REPORT_SHORT="${REPORT/#$HOME/~}"' in script
        assert "see $REPORT_SHORT" in script


class TestRenderSystemd:
    def test_service_is_oneshot(self) -> None:
        out = service.render_systemd_service()
        assert "Type=oneshot" in out
        assert "ExecStart=/bin/bash %h/.sussed/sussed-daily.sh" in out

    def test_service_has_no_install_section(self) -> None:
        # Only the timer should be enabled; the service must not auto-start.
        assert "[Install]" not in service.render_systemd_service()

    def test_timer_has_calendar_and_persistent(self) -> None:
        out = service.render_systemd_timer(10, 30)
        assert "OnCalendar=*-*-* 10:30:00" in out
        assert "Persistent=true" in out
        assert "[Install]" in out

    def test_timer_zero_pads(self) -> None:
        out = service.render_systemd_timer(7, 5)
        assert "OnCalendar=*-*-* 07:05:00" in out


class TestRenderLaunchd:
    def test_contains_expected_keys(self) -> None:
        out = service.render_launchd_plist("/Users/u", 10, 30)
        assert "com.sussed.daily" in out
        assert "<integer>10</integer>" in out
        assert "<integer>30</integer>" in out
        assert "RunAtLoad" in out

    def test_uses_absolute_home_no_tilde(self) -> None:
        out = service.render_launchd_plist("/Users/u", 10, 30)
        assert "/Users/u/.sussed/sussed-daily.sh" in out
        assert "~" not in out

    def test_launchd_log_separate_from_service_log(self) -> None:
        out = service.render_launchd_plist("/Users/u", 10, 30)
        assert "/Users/u/.sussed/launchd.log" in out

    def test_no_unreplaced_tokens(self) -> None:
        assert "__" not in service.render_launchd_plist("/Users/u", 10, 30)


class TestCopilotPrompt:
    def test_reviews_only_prepared_listings(self) -> None:
        # Guard against re-reviewing stale *-prepared.json from earlier runs.
        prompt = COPILOT_PROMPT
        assert "ONLY the listings prepared in step 1" in prompt
        assert "older *-prepared.json" in prompt

    def test_no_blind_glob_instruction(self) -> None:
        # The old prompt globbed every prepared file; that must be gone.
        assert "For each prepared JSON in .sussed/image-cache/*-prepared.json" not in COPILOT_PROMPT


class TestGemCountRendering:
    def test_counts_json_array_length_not_score_key(self) -> None:
        # review picks emits "ai_score", not "score" -> grepping "score" is wrong.
        # We count the JSON array length instead.
        script = service.render_shell_script(
            config_path="/c.yaml",
            project_dir="/p",
            uv_path="/usr/local/bin/uv",
            copilot_path="/usr/local/bin/copilot",
            sched_hhmm="1000",
            path_dirs="/usr/local/bin:/usr/bin:/bin",
        )
        gem_line = next(line for line in script.splitlines() if line.startswith("GEM_COUNT="))
        assert "json.load(sys.stdin)" in gem_line
        assert "len(d)" in gem_line
        assert "grep -o '\"score\"'" not in script


class TestRunChecked:
    def test_raises_on_nonzero_with_stderr(self) -> None:
        with pytest.raises(RuntimeError, match="Command failed"):
            service._run_checked(["sh", "-c", "echo boom >&2; exit 3"])

    def test_ok_on_success(self) -> None:
        # Should not raise.
        service._run_checked(["sh", "-c", "exit 0"])
