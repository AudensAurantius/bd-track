"""Foundation helpers used across modules.

Includes the subprocess wrapper, beads workspace discovery, the Rich-styled
argparse formatter, logging setup, interactive prompt helpers, activity
tracking for idle-stop, and shared path constants.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import logging.handlers
import subprocess
import sys
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler
from rich.prompt import Confirm, Prompt
from rich_argparse import RawDescriptionRichHelpFormatter

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPOS_CONFIG = Path.home() / ".config" / "bd-track" / "repos.yaml"
CLEANUP_STATE = Path.home() / ".cache" / "bd-track" / "cleanup-state.json"
CLEANUP_LOG = Path.home() / ".cache" / "bd-track" / "cleanup.log"
ACTIVITY_STATE = Path.home() / ".cache" / "bd-track" / "activity-state.json"
QUEUE_FILE = "queue.yaml"

SYSTEMD_CLEANUP_NAME = "bd-track-cleanup"
SYSTEMD_IDLE_STOP_NAME = "bd-track-idle-stop"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

root_log = logging.getLogger("bd-track")
cleanup_log = logging.getLogger("bd-track.cleanup")


class HelpFormatter(
    argparse.ArgumentDefaultsHelpFormatter,
    RawDescriptionRichHelpFormatter,
):
    """Preserve description/epilog whitespace, show defaults, and style with Rich."""


def setup_logger(loglevel: str, *, enable_file_handler: bool = False) -> None:
    """Configure root and cleanup loggers; enable rotating file handler if requested."""
    level = getattr(logging, loglevel.upper(), logging.INFO)
    root_log.setLevel(level)
    root_log.addHandler(
        RichHandler(
            level=level,
            console=Console(stderr=True),
            show_path=False,
            rich_tracebacks=True,
        )
    )
    if enable_file_handler:
        CLEANUP_LOG.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            CLEANUP_LOG, maxBytes=1_000_000, backupCount=3
        )
        fh.setLevel(level)
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        cleanup_log.addHandler(fh)


# ---------------------------------------------------------------------------
# Subprocess wrapper
# ---------------------------------------------------------------------------

def run(
    cmd: list[str], *, check: bool = True, capture: bool = False,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess:
    """Thin wrapper over ``subprocess.run`` with text mode and consistent kwargs."""
    return subprocess.run(
        cmd, check=check, text=True, capture_output=capture, cwd=cwd,
    )


# ---------------------------------------------------------------------------
# Workspace discovery
# ---------------------------------------------------------------------------

def find_beads_dir(project_dir: Path | None = None) -> Path:
    """Return the active workspace's .beads/ directory.

    If `project_dir` is given, validates `.beads/` exists under it.
    Otherwise calls `bd where` to discover the active workspace.
    Exits with a message if no workspace is found.
    """
    if project_dir is not None:
        candidate = project_dir / ".beads"
        if not candidate.is_dir():
            sys.exit(f"bd-track: no .beads/ directory found under {project_dir}")
        return candidate
    result = run(["bd", "where"], check=False, capture=True)
    if result.returncode != 0:
        sys.exit(
            "bd-track: no active Beads workspace. cd into a project with "
            ".beads/, or pass --project-dir."
        )
    return Path(result.stdout.strip().split("\n", 1)[0].strip())


# ---------------------------------------------------------------------------
# Interactive prompts
# ---------------------------------------------------------------------------

def is_interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def prompt(message: str, default: str, *, yes: bool) -> str:
    if yes or not is_interactive():
        return default
    return Prompt.ask(message, default=default)


def confirm(message: str, default: bool, *, yes: bool) -> bool:
    if yes or not is_interactive():
        return default
    return Confirm.ask(message, default=default)


# ---------------------------------------------------------------------------
# Activity tracking (for idle-stop)
# ---------------------------------------------------------------------------

def load_activity_state() -> dict:
    if not ACTIVITY_STATE.exists():
        return {}
    with ACTIVITY_STATE.open() as f:
        return json.load(f)


def save_activity_state(state: dict) -> None:
    ACTIVITY_STATE.parent.mkdir(parents=True, exist_ok=True)
    with ACTIVITY_STATE.open("w") as f:
        json.dump(state, f, indent=2, default=str)


def record_activity(project_path: str) -> None:
    """Record the current time as the last-activity timestamp for a project."""
    state = load_activity_state()
    state[project_path] = dt.datetime.now(dt.timezone.utc).isoformat()
    save_activity_state(state)
