"""Install and manage the daily scheduled sussed service.

Renders a shell orchestration script plus OS-native service files (systemd
user timer on Linux, launchd agent on macOS), installs them, and manages
their lifecycle. All user-level — no root required.
"""

from __future__ import annotations

import contextlib
import platform
import shlex
import shutil
import stat
import subprocess
from pathlib import Path

from loguru import logger
from rich.console import Console

from sussed.service_templates import (
    COPILOT_PROMPT,
    LAUNCHD_PLIST_TEMPLATE,
    SHELL_SCRIPT_TEMPLATE,
    SYSTEMD_SERVICE_TEMPLATE,
    SYSTEMD_TIMER_TEMPLATE,
)

console = Console()

SUSSED_DIR = Path.home() / ".sussed"
SCRIPT_PATH = SUSSED_DIR / "sussed-daily.sh"
LAST_RUN_PATH = SUSSED_DIR / "last-run.txt"
RESULTS_DIR = SUSSED_DIR / "results"

# systemd paths for user-level units
SYSTEMD_USER_DIR = Path.home() / ".config" / "systemd" / "user"
SYSTEMD_SERVICE_PATH = SYSTEMD_USER_DIR / "sussed-daily.service"
SYSTEMD_TIMER_PATH = SYSTEMD_USER_DIR / "sussed-daily.timer"

# launchd
LAUNCHD_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
LAUNCHD_PLIST_PATH = LAUNCHD_AGENTS_DIR / "com.sussed.daily.plist"
LAUNCHD_LABEL = "com.sussed.daily"

_SYSTEM_PATH_DIRS = (
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/usr/bin",
    "/bin",
    "/usr/sbin",
    "/sbin",
)


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #
def _detect_os() -> str:
    """Return ``'darwin'`` or ``'linux'``; raise on anything else."""
    system = platform.system().lower()
    if system not in ("darwin", "linux"):
        raise RuntimeError(f"Unsupported OS: {system}. Only macOS and Linux are supported.")
    return system


def _resolve_binary(name: str) -> str:
    """Absolute path to ``name`` on ``PATH``; raise if missing."""
    path = shutil.which(name)
    if not path:
        raise FileNotFoundError(f"'{name}' not found in PATH. Install it first.")
    return path


def _parse_time(time_str: str) -> tuple[int, int]:
    """Parse ``'HH:MM'`` (24h) into ``(hour, minute)``.

    Raises ``ValueError`` on malformed input or out-of-range values.
    """
    parts = time_str.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"Invalid time format {time_str!r}, expected HH:MM")
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError as exc:
        raise ValueError(f"Invalid time {time_str!r}, expected numeric HH:MM") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"Invalid time {time_str!r}: hour must be 0-23, minute 0-59")
    return hour, minute


def _build_path_dirs(*binaries: str) -> str:
    """Build a colon-joined PATH from binary dirs + system defaults, deduped."""
    dirs: list[str] = []
    for binary in binaries:
        parent = str(Path(binary).parent)
        if parent not in dirs:
            dirs.append(parent)
    for sysdir in _SYSTEM_PATH_DIRS:
        if sysdir not in dirs:
            dirs.append(sysdir)
    return ":".join(dirs)


def _run_checked(cmd: list[str]) -> None:
    """Run ``cmd``; raise ``RuntimeError`` with captured stderr on failure.

    Converts ``subprocess`` failures into a clean, user-facing error instead
    of an uncaught ``CalledProcessError`` traceback.
    """
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"Command failed ({' '.join(cmd)}): {detail}")


# --------------------------------------------------------------------------- #
# Template rendering                                                           #
# --------------------------------------------------------------------------- #
def render_shell_script(
    *,
    config_path: str,
    project_dir: str,
    uv_path: str,
    copilot_path: str,
    sched_hhmm: str,
    path_dirs: str,
) -> str:
    """Render the orchestration shell script with shell-safe substitutions."""
    script = SHELL_SCRIPT_TEMPLATE
    script = script.replace("__PATH_DIRS__", shlex.quote(path_dirs))
    script = script.replace("__CONFIG_PATH__", shlex.quote(config_path))
    script = script.replace("__PROJECT_DIR__", shlex.quote(project_dir))
    script = script.replace("__UV_BIN__", shlex.quote(uv_path))
    script = script.replace("__COPILOT_BIN__", shlex.quote(copilot_path))
    script = script.replace("__SCHED_HHMM__", sched_hhmm)
    # Prompt goes inside a quoted heredoc, so it is inserted verbatim (no quoting).
    script = script.replace("__COPILOT_PROMPT__", COPILOT_PROMPT)
    return script


def render_systemd_service() -> str:
    """Render the systemd user service unit (no [Install]; timer owns enable)."""
    return SYSTEMD_SERVICE_TEMPLATE


def render_systemd_timer(hour: int, minute: int) -> str:
    """Render the systemd user timer unit for ``hour:minute`` daily."""
    return SYSTEMD_TIMER_TEMPLATE.replace("__HHMM_HOUR__", f"{hour:02d}").replace(
        "__HHMM_MINUTE__", f"{minute:02d}"
    )


def render_launchd_plist(home: str, hour: int, minute: int) -> str:
    """Render the launchd agent plist for ``hour:minute`` daily."""
    plist = LAUNCHD_PLIST_TEMPLATE
    plist = plist.replace("__HOME__", home)
    plist = plist.replace("__HHMM_HOUR__", str(hour))
    plist = plist.replace("__HHMM_MINUTE__", str(minute))
    return plist


# --------------------------------------------------------------------------- #
# Install / uninstall / status                                                 #
# --------------------------------------------------------------------------- #
def install_service(time_str: str, config_path: str) -> None:
    """Generate the shell script + OS service files, then enable the service."""
    os_type = _detect_os()
    hour, minute = _parse_time(time_str)

    home = str(Path.home())
    uv_path = _resolve_binary("uv")
    copilot_path = _resolve_binary("copilot")
    project_dir = str(Path.cwd())
    config_resolved = str(Path(config_path).resolve())

    # Validate we're in a real sussed project (uv run needs pyproject.toml here).
    if not (Path(project_dir) / "pyproject.toml").exists():
        raise FileNotFoundError(
            f"No pyproject.toml in {project_dir}. "
            "Run this command from the sussed project directory."
        )
    if not Path(config_resolved).exists():
        raise FileNotFoundError(f"Config file not found: {config_resolved}")

    SUSSED_DIR.mkdir(parents=True, exist_ok=True)
    SUSSED_DIR.chmod(0o700)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    script = render_shell_script(
        config_path=config_resolved,
        project_dir=project_dir,
        uv_path=uv_path,
        copilot_path=copilot_path,
        sched_hhmm=f"{hour:02d}{minute:02d}",
        path_dirs=_build_path_dirs(uv_path, copilot_path),
    )
    SCRIPT_PATH.write_text(script)
    SCRIPT_PATH.chmod(SCRIPT_PATH.stat().st_mode | stat.S_IEXEC)
    SCRIPT_PATH.chmod(0o700)
    logger.info(f"Generated script: {SCRIPT_PATH}")

    try:
        if os_type == "linux":
            _install_systemd(hour, minute)
        else:
            _install_launchd(home, hour, minute)
    except Exception:
        # Roll back generated files so a failed install doesn't leave a
        # half-configured service behind.
        logger.error("Install failed; rolling back generated files.")
        with contextlib.suppress(Exception):
            _teardown(os_type)
        raise

    console.print("\n[green]✅ sussed daily service installed![/green]")
    console.print(f"   Schedule: [cyan]{hour:02d}:{minute:02d}[/cyan] daily")
    console.print(f"   Config:   [cyan]{config_resolved}[/cyan]")
    console.print(f"   Script:   [cyan]{SCRIPT_PATH}[/cyan]")
    console.print(f"   Logs:     [cyan]{SUSSED_DIR / 'service.log'}[/cyan]")
    console.print(f"   Reports:  [cyan]{RESULTS_DIR}[/cyan]")
    if os_type == "darwin":
        console.print("\n[dim]Also runs at login if today's scheduled run was missed.[/dim]")
    else:
        console.print("\n[dim]Catches up on missed runs after boot (Persistent=true).[/dim]")


def _install_systemd(hour: int, minute: int) -> None:
    SYSTEMD_USER_DIR.mkdir(parents=True, exist_ok=True)
    SYSTEMD_SERVICE_PATH.write_text(render_systemd_service())
    SYSTEMD_TIMER_PATH.write_text(render_systemd_timer(hour, minute))
    logger.info(f"Generated: {SYSTEMD_SERVICE_PATH}, {SYSTEMD_TIMER_PATH}")

    _run_checked(["systemctl", "--user", "daemon-reload"])
    _run_checked(["systemctl", "--user", "enable", "--now", "sussed-daily.timer"])
    logger.info("systemd timer enabled and started")


def _install_launchd(home: str, hour: int, minute: int) -> None:
    LAUNCHD_AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    if LAUNCHD_PLIST_PATH.exists():
        subprocess.run(["launchctl", "unload", str(LAUNCHD_PLIST_PATH)], capture_output=True)
    LAUNCHD_PLIST_PATH.write_text(render_launchd_plist(home, hour, minute))
    logger.info(f"Generated: {LAUNCHD_PLIST_PATH}")
    _run_checked(["launchctl", "load", "-w", str(LAUNCHD_PLIST_PATH)])
    logger.info("launchd agent loaded")


def _teardown(os_type: str) -> None:
    """Stop the service (best-effort) and remove all generated files."""
    if os_type == "linux":
        _uninstall_systemd()
    else:
        _uninstall_launchd()
    if SCRIPT_PATH.exists():
        SCRIPT_PATH.unlink()
        logger.info(f"Removed: {SCRIPT_PATH}")


def uninstall_service() -> None:
    """Stop, disable, and remove service files. Keeps logs and data."""
    _teardown(_detect_os())
    console.print("[green]✅ sussed daily service uninstalled.[/green]")
    console.print("[dim]Logs and data in ~/.sussed/ were kept.[/dim]")


def _uninstall_systemd() -> None:
    subprocess.run(
        ["systemctl", "--user", "disable", "--now", "sussed-daily.timer"],
        capture_output=True,
    )
    for path in (SYSTEMD_SERVICE_PATH, SYSTEMD_TIMER_PATH):
        if path.exists():
            path.unlink()
            logger.info(f"Removed: {path}")
    subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)


def _uninstall_launchd() -> None:
    if LAUNCHD_PLIST_PATH.exists():
        subprocess.run(["launchctl", "unload", str(LAUNCHD_PLIST_PATH)], capture_output=True)
        LAUNCHD_PLIST_PATH.unlink()
        logger.info(f"Removed: {LAUNCHD_PLIST_PATH}")


def show_service_status() -> None:
    """Print the current service status with Rich formatting."""
    os_type = _detect_os()

    console.print("\n[bold]sussed daily service status[/bold]\n")

    if os_type == "linux":
        installed = SYSTEMD_TIMER_PATH.exists() and SYSTEMD_SERVICE_PATH.exists()
    else:
        installed = LAUNCHD_PLIST_PATH.exists()

    if not installed:
        console.print("[yellow]Not installed.[/yellow]")
        console.print("Run [cyan]sussed service install[/cyan] to set up.")
        return

    console.print(f"[green]✅ Installed[/green] ({os_type})")

    if LAST_RUN_PATH.exists():
        console.print(f"   Last run:  [cyan]{LAST_RUN_PATH.read_text().strip()}[/cyan]")
    else:
        console.print("   Last run:  [dim]never[/dim]")
    if SCRIPT_PATH.exists():
        console.print(f"   Script:    [cyan]{SCRIPT_PATH}[/cyan]")

    if os_type == "linux":
        result = subprocess.run(
            ["systemctl", "--user", "list-timers", "sussed-daily.timer", "--no-pager"],
            capture_output=True,
            text=True,
        )
        console.print("\n[bold]Timer:[/bold]")
        console.print(result.stdout.strip() or "[dim]No timer info[/dim]")
    else:
        result = subprocess.run(
            ["launchctl", "list"],
            capture_output=True,
            text=True,
        )
        lines = [ln for ln in result.stdout.splitlines() if LAUNCHD_LABEL in ln]
        console.print("\n[bold]launchd:[/bold]")
        if lines:
            for line in lines:
                console.print(f"   {line}")
        else:
            console.print("[yellow]plist present but job not loaded[/yellow]")
            console.print(f"Try: [cyan]launchctl load -w {LAUNCHD_PLIST_PATH}[/cyan]")

    if RESULTS_DIR.exists():
        reports = sorted(RESULTS_DIR.glob("*-daily-report.md"), reverse=True)[:3]
        if reports:
            console.print("\n[bold]Recent reports:[/bold]")
            for report in reports:
                console.print(f"   📄 {report}")
