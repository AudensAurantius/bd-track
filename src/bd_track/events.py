"""JSONL event schema (v1) + appender for the timetracking backend (bd-timew-ahp).

Append-only event log: one JSON object per line, written with a single
``O_APPEND`` ``os.write()``. Per-session files under the active beads dir
(bd-timew-tq9) keep concurrent sessions from contending on the same file.

Schema v1 fields:
  common:  v=1, eid (event ULID — global order), event, interval (interval ULID),
           session_id, ts (ISO-8601 with tz offset)
  start:   + bead, tags (flat list of strings), group_id|null, actor|null, role|null
  stop:    (common only)
  cancel:  (common only)
  correction: + any subset of {start, stop, tags, group_id, actor, role};
              the aggregator folds corrections by eid, per-field latest-wins.

Provenance originates on ``start`` and is mutable only via ``correction`` events
— never by editing a past line. ``tags`` is a flat list (org-agnostic); the
billing tuple's *shape* is the sidecar config's concern (see bd-timew-qny).

Two ULIDs per event: ``interval`` groups an interval's start/stop/cancel/
correction; ``eid`` is the event's own id, giving a global total order so
"latest correction wins" is unambiguous without relying on file offsets.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from collections.abc import Iterable
from pathlib import Path

from ulid import ULID

from bd_track.session import project_id
from bd_track.util import find_beads_dir, root_log

SCHEMA_VERSION = 1

# POSIX guarantees atomic writes <= PIPE_BUF only for pipes; on Linux LOCAL
# filesystems an O_APPEND write() is serialized by the inode lock and does not
# interleave regardless of size. Our events are ~400 bytes; we guard against a
# pathological oversize rather than splitting lines (see bd memory
# jsonl-maildir-fallback-unbounded-records for the fallback if that ever changes).
PIPE_BUF = 4096

# Authoritative log lives alongside the active beads dir so it rides beads sync;
# this XDG_DATA_HOME path is the server-mode / no-local-.beads fallback (NOT
# ~/.cache — billing data is not disposable; and distinct from session.py's
# ~/.local/state pointer, which is machine-local operational state).
LOG_FALLBACK_DIR = Path.home() / ".local" / "share" / "bd-track"

VALID_EVENTS = ("start", "stop", "cancel", "correction")

# Sentinel: distinguishes "field not being corrected" from "correct to null"
# (group_id/actor/role can legitimately be cleared to null by a correction).
_UNSET = object()


def _now_iso() -> str:
    """Local-time ISO-8601 with tz offset, second precision (eid carries finer order)."""
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _safe_filename(name: str) -> str:
    """Sanitise a session id for use as a filename (ids may be injected verbatim)."""
    return "".join(c if (c.isalnum() or c in "._-") else "_" for c in name) or "_"


def log_dir(project_dir: Path | None = None) -> Path:
    """Resolve the per-session log directory (bd-timew-tq9).

    ``<beads_dir>/bd-track/sessions/`` when a local beads dir exists (rides beads
    sync); otherwise ``~/.local/share/bd-track/<project-id>/sessions/``.
    """
    beads_dir: Path | None = None
    try:
        beads_dir = find_beads_dir(project_dir)
    except SystemExit:
        beads_dir = None
    if beads_dir is not None and Path(beads_dir).is_dir():
        return Path(beads_dir) / "bd-track" / "sessions"
    root = Path(project_dir) if project_dir else Path.cwd()
    return LOG_FALLBACK_DIR / project_id(root) / "sessions"


def _session_log_path(session_id: str, project_dir: Path | None = None) -> Path:
    return log_dir(project_dir) / f"{_safe_filename(session_id)}.jsonl"


def append_event(event: dict, *, session_id: str, project_dir: Path | None = None) -> None:
    """Append one event as a JSONL line via a single O_APPEND write()."""
    data = (json.dumps(event, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")
    if len(data) >= PIPE_BUF:
        root_log.warning(
            "bd-track event line is %d bytes (>= PIPE_BUF %d); append atomicity is "
            "not guaranteed under concurrent same-file writers", len(data), PIPE_BUF,
        )
    path = _session_log_path(session_id, project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)


def _base_event(event_type: str, interval: str, session_id: str) -> dict:
    return {
        "v": SCHEMA_VERSION,
        "eid": str(ULID()),
        "event": event_type,
        "interval": interval,
        "session_id": session_id,
        "ts": _now_iso(),
    }


def start_interval(
    bead: str, tags: Iterable[str], *, session_id: str,
    group_id: str | None = None, actor: str | None = None, role: str | None = None,
    project_dir: Path | None = None,
) -> str:
    """Append a ``start`` event and return the new interval id (ULID)."""
    interval = str(ULID())
    event = _base_event("start", interval, session_id)
    event.update({
        "bead": bead,
        "tags": list(tags),
        "group_id": group_id,
        "actor": actor,
        "role": role,
    })
    append_event(event, session_id=session_id, project_dir=project_dir)
    return interval


def stop_interval(interval: str, *, session_id: str, project_dir: Path | None = None) -> None:
    """Append a ``stop`` event for ``interval`` (closes it on aggregation)."""
    append_event(_base_event("stop", interval, session_id),
                 session_id=session_id, project_dir=project_dir)


def cancel_interval(interval: str, *, session_id: str, project_dir: Path | None = None) -> None:
    """Append a ``cancel`` event for ``interval`` (drops it on aggregation)."""
    append_event(_base_event("cancel", interval, session_id),
                 session_id=session_id, project_dir=project_dir)


def correct_interval(
    interval: str, *, session_id: str,
    start: str | None = None, stop: str | None = None,
    tags: Iterable[str] | None = None,
    group_id: object = _UNSET, actor: object = _UNSET, role: object = _UNSET,
    project_dir: Path | None = None,
) -> None:
    """Append a ``correction`` event carrying any subset of mutable fields.

    Time/tags fields are corrected when given (non-None). Provenance fields
    (group_id/actor/role) use a sentinel so they can be corrected *to* null.
    The aggregator folds corrections by eid, per-field latest-wins. Raises if
    nothing would change.
    """
    event = _base_event("correction", interval, session_id)
    if start is not None:
        event["start"] = start
    if stop is not None:
        event["stop"] = stop
    if tags is not None:
        event["tags"] = list(tags)
    if group_id is not _UNSET:
        event["group_id"] = group_id
    if actor is not _UNSET:
        event["actor"] = actor
    if role is not _UNSET:
        event["role"] = role
    if not any(k in event for k in ("start", "stop", "tags", "group_id", "actor", "role")):
        raise ValueError("correction must change at least one field")
    append_event(event, session_id=session_id, project_dir=project_dir)


def _infer_actor() -> str:
    """Pre-merge actor inference: a Claude-driven call vs a human at a shell."""
    return "claude" if os.environ.get("CLAUDE_CODE_SESSION_ID") else "human"


def resolve_provenance(
    *, group_id: str | None = None, actor: str | None = None, role: str | None = None,
) -> dict:
    """Source provenance: explicit arg → env → (actor only) inference; else null."""
    return {
        "group_id": group_id or os.environ.get("BD_TRACK_GROUP_ID") or None,
        "actor": actor or os.environ.get("BD_TRACK_ACTOR") or _infer_actor(),
        "role": role or os.environ.get("BD_TRACK_ROLE") or None,
    }
