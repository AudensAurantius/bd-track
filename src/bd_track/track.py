"""Command implementations — start/stop/switch/status/active/report/resolve.

Re-wired off Timewarrior onto the append-only JSONL event log (bd-timew-9hn).
There is no ambient "active interval" anymore: every command re-derives its
view by reading + folding the per-session logs (``aggregate``). This is what
makes concurrent sessions safe — a session can only ever close an interval ULID
it can see, and ``start``/``stop`` touch only the caller's own session.

Single-active-per-session: ``start`` first stops the calling session's own open
interval(s), preserving the timew muscle-memory ("starting a new thing ends the
old one") without ever reaching across sessions — the property timew lacked.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from bd_track.aggregate import (
    POLICIES,
    Interval,
    load_intervals,
    open_intervals,
    report,
)
from bd_track.billing import get_issue, load_sidecar, resolve_tuple
from bd_track.events import log_dir, resolve_provenance, start_interval, stop_interval
from bd_track.session import resolve_session_id
from bd_track.util import find_beads_dir, record_activity, root_log

# Billing-tuple fields surfaced in start/status/active output. The *shape* is
# sidecar-defined (bd-timew-qny); these are the BOCO-NetSuite defaults the
# display layer knows how to label. Stored flat as "client:foo" tags.
_TUPLE_KEYS = ("client", "case", "svc")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_tags(tuple_: dict) -> list[str]:
    """Flat billing tags from a resolved tuple (``key:value`` + billable flag)."""
    tags = [f"{key}:{val}" for key in _TUPLE_KEYS if (val := tuple_.get(key))]
    if not tuple_.get("svc") or tuple_.get("svc") == "none":
        tags.append("billable:false")
    return tags


def _tuple_from_tags(tags: list[str]) -> dict[str, str | None]:
    """Reconstruct the display tuple from flat ``key:value`` tags."""
    out: dict[str, str | None] = {k: None for k in _TUPLE_KEYS}
    for tag in tags:
        for key in _TUPLE_KEYS:
            if tag.startswith(f"{key}:"):
                out[key] = tag[len(f"{key}:"):]
    return out


def _format_duration(delta: dt.timedelta) -> str:
    total = int(delta.total_seconds())
    hours, rem = divmod(total, 3600)
    minutes, _ = divmod(rem, 60)
    return f"{hours}h {minutes:02d}m" if hours else f"{minutes}m"


def _elapsed(start_iso: str) -> str:
    start = dt.datetime.fromisoformat(start_iso)
    now = dt.datetime.now().astimezone() if start.tzinfo else dt.datetime.now()
    return _format_duration(now - start)


def _project_root(project_dir: Path | None = None) -> Path:
    """Project root (parent of the active ``.beads/`` dir)."""
    return find_beads_dir(project_dir).parent


def _session_open(
    session_id: str, project_root: Path, *, bead: str | None = None,
) -> list[Interval]:
    """Open intervals belonging to ``session_id`` (optionally for one bead)."""
    opens = [
        iv for iv in open_intervals(load_intervals(log_dir(project_root)))
        if iv.session == session_id
    ]
    if bead:
        opens = [iv for iv in opens if iv.bead == bead]
    return opens


# ---------------------------------------------------------------------------
# start / stop / switch
# ---------------------------------------------------------------------------

def cmd_start(issue_id: str, *, dry_run: bool = False, session_id: str | None = None) -> None:
    """Resolve the billing tuple, claim the bead, append a ``start`` event."""
    beads_dir = find_beads_dir()
    project_root = beads_dir.parent
    sidecar = load_sidecar(beads_dir)
    issue = get_issue(issue_id)
    labels: list[str] = issue.get("labels") or []
    tuple_ = resolve_tuple(labels, sidecar)

    root_log.info("Issue:  %s  %s", issue.get("id", "?"), issue.get("title", ""))
    root_log.info("Labels: %s", ", ".join(labels) or "(none)")
    for key in _TUPLE_KEYS:
        root_log.info("%-7s %s", key.capitalize() + ":", tuple_[key] or "(none)")

    if dry_run:
        return

    sid = resolve_session_id(project_root, explicit=session_id)

    # Single-active-per-session: end this session's own open interval(s) first.
    for iv in _session_open(sid, project_root):
        stop_interval(iv.interval, session_id=sid, project_dir=project_root)
        root_log.info("Stopped prior interval %s (%s)", iv.bead or "?", _elapsed(iv.start))

    tags = _build_tags(tuple_)
    provenance = resolve_provenance()
    interval = start_interval(
        issue_id, tags, session_id=sid, project_dir=project_root, **provenance,
    )
    root_log.info("Started interval %s  [session %s]", interval[:8], sid)

    if issue.get("status") != "in_progress":
        from bd_track.util import run
        run(["bd", "update", issue_id, "--claim"], check=False)

    record_activity(str(project_root.resolve()))


def cmd_stop(issue_id: str | None = None, *, clean: bool = True,
             session_id: str | None = None) -> None:
    """Append a ``stop`` event for this session's open interval(s).

    With ``issue_id`` given, stops only the interval(s) tagged with that bead.
    Unlike the old timew backend, a no-argument stop can *never* reach another
    session's interval — it only closes ULIDs in the caller's own session log.

    When ``clean`` is True (default), sweeps closed/deferred beads from queue
    scopes afterward (``--no-clean`` to skip).
    """
    project_root = _project_root()
    sid = resolve_session_id(project_root, explicit=session_id)
    record_activity(str(project_root.resolve()))

    opens = _session_open(sid, project_root, bead=issue_id)
    if not opens:
        scope = f" for {issue_id}" if issue_id else ""
        root_log.info("no active interval%s in this session (%s)", scope, sid)
    for iv in opens:
        stop_interval(iv.interval, session_id=sid, project_dir=project_root)
        root_log.info("Stopped %s  %s  (%s)", iv.bead or "(no bead)",
                      iv.interval[:8], _elapsed(iv.start))

    if clean:
        from bd_track.queue import cmd_clean
        try:
            cmd_clean(quiet=True)
        except SystemExit:
            pass


def cmd_switch(issue_id: str, *, from_issue_id: str | None = None,
               session_id: str | None = None) -> None:
    """Stop the current interval and start one on ``issue_id`` (composition)."""
    cmd_stop(from_issue_id, clean=False, session_id=session_id)
    cmd_start(issue_id, session_id=session_id)


# ---------------------------------------------------------------------------
# status / active
# ---------------------------------------------------------------------------

def cmd_status(*, session_id: str | None = None) -> None:
    """Show this session's open interval(s): bead, tuple, elapsed."""
    project_root = _project_root()
    sid = resolve_session_id(project_root, explicit=session_id)
    all_open = open_intervals(load_intervals(log_dir(project_root)))
    mine = [iv for iv in all_open if iv.session == sid]

    if not mine:
        root_log.info("no active interval in this session (%s)", sid)
        others = len({iv.session for iv in all_open})
        if others:
            root_log.info("(%d interval(s) active in other sessions — see `bd-track active`)",
                          len(all_open))
        return

    for iv in mine:
        title = ""
        status = ""
        if iv.bead:
            try:
                issue = get_issue(iv.bead)
                title = issue.get("title", "")
                status = issue.get("status", "")
            except SystemExit:
                pass
        root_log.info("Tracking: %s  %s", iv.bead or "(no bead)", title)
        if status:
            root_log.info("Status:   %s", status)
        root_log.info("Elapsed:  %s", _elapsed(iv.start))
        tuple_ = _tuple_from_tags(iv.tags)
        for key in _TUPLE_KEYS:
            root_log.info("%-9s %s", key.capitalize() + ":", tuple_[key] or "(none)")

    other = [iv for iv in all_open if iv.session != sid]
    if other:
        root_log.info("(%d interval(s) active in other sessions — see `bd-track active`)",
                      len(other))


def cmd_active(*, session_id: str | None = None) -> None:
    """Show ALL open intervals across ALL sessions (the multi-active view)."""
    project_root = _project_root()
    sid = resolve_session_id(project_root, explicit=session_id)
    opens = sorted(open_intervals(load_intervals(log_dir(project_root))),
                   key=lambda iv: iv.start or "")
    if not opens:
        root_log.info("no active intervals in any session")
        return
    root_log.info("%d active interval(s):", len(opens))
    for iv in opens:
        marker = "*" if iv.session == sid else " "
        tuple_ = _tuple_from_tags(iv.tags)
        tup = " ".join(f"{k}:{v}" for k in _TUPLE_KEYS if (v := tuple_[k])) or "(no tuple)"
        root_log.info("%s %-20s %-14s %-7s  %s", marker, iv.session or "?",
                      iv.bead or "(no bead)", _elapsed(iv.start), tup)


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

def _in_range(iv: Interval, since: dt.date | None, until: dt.date | None) -> bool:
    if iv.start is None:
        return False
    d = dt.datetime.fromisoformat(iv.start).date()
    if since and d < since:
        return False
    if until and d > until:
        return False
    return True


def cmd_report(*, group_by: str = "bead", policy_name: str = "billing",
               since: str | None = None, until: str | None = None,
               session_id: str | None = None) -> None:
    """Aggregate closed intervals; group by a dimension under a named policy."""
    project_root = _project_root()
    policy = POLICIES[policy_name]
    since_d = dt.date.fromisoformat(since) if since else None
    until_d = dt.date.fromisoformat(until) if until else None
    intervals = [iv for iv in load_intervals(log_dir(project_root))
                 if _in_range(iv, since_d, until_d)]

    rows = report(intervals, group_by=group_by, policy=policy)
    if not rows:
        root_log.info("no closed intervals in range")
    else:
        root_log.info("Report by %s  [policy: %s]", group_by, policy_name)
        total = dt.timedelta(0)
        for r in rows:
            root_log.info("  %-24s %8s  (%d interval(s))",
                          str(r.group) if r.group is not None else "(none)",
                          _format_duration(r.duration), r.intervals)
            total += r.duration
        root_log.info("  %-24s %8s", "TOTAL", _format_duration(total))

    stale = [iv for iv in intervals if iv.status == "open"]
    if stale:
        root_log.info("(%d open/stale interval(s) excluded from totals — see `bd-track active`)",
                      len(stale))
