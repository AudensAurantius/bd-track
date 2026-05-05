"""Timewarrior bridge — start/stop/switch/status/resolve subcommands."""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

from bd_timew.billing import get_issue, load_sidecar, resolve_tuple
from bd_timew.util import find_beads_dir, record_activity, root_log, run


def cmd_start(issue_id: str, *, dry_run: bool = False) -> None:
    """Resolve billing tuple, claim the bead, start a tagged timew interval."""
    beads_dir = find_beads_dir()
    sidecar = load_sidecar(beads_dir)
    issue = get_issue(issue_id)
    labels: list[str] = issue.get("labels") or []
    tuple_ = resolve_tuple(labels, sidecar)

    root_log.info("Issue:  %s  %s", issue.get("id", "?"), issue.get("title", ""))
    root_log.info("Labels: %s", ", ".join(labels) or "(none)")
    root_log.info("Client: %s", tuple_["client"] or "(none)")
    root_log.info("Case:   %s", tuple_["case"] or "(none)")
    root_log.info("Svc:    %s", tuple_["svc"] or "(none)")

    if dry_run:
        return

    tags = [issue_id]
    for key in ("client", "case", "svc"):
        val = tuple_[key]
        if val:
            tags.append(f"{key}:{val}")
    if not tuple_["svc"] or tuple_["svc"] == "none":
        tags.append("billable:false")

    run(["timew", "start", *tags])
    title = issue.get("title", "")
    if title:
        run(["timew", "annotate", f"{issue_id}: {title}"])
    if issue.get("status") != "in_progress":
        run(["bd", "update", issue_id, "--claim"], check=False)

    record_activity(str(beads_dir.parent.resolve()))


def cmd_stop(issue_id: str | None = None, *, clean: bool = True) -> None:
    """Stop the active timew interval (tagged or untagged).

    When ``clean`` is True (default), runs a queue sweep afterward to drop any
    closed/deferred beads from any queue scope. Pass ``clean=False`` (CLI:
    ``--no-clean``) to skip — useful when stopping a bead that is intentionally
    closed but should remain queued for a follow-up.
    """
    # Record activity before stopping so the timestamp reflects last use.
    result = run(["bd", "where"], check=False, capture=True)
    if result.returncode == 0:
        project_path = result.stdout.strip().split("\n", 1)[0].strip()
        beads_dir = Path(project_path)
        record_activity(str(beads_dir.parent.resolve()))
    if issue_id:
        run(["timew", "stop", issue_id], check=False)
    else:
        run(["timew", "stop"], check=False)

    if clean:
        # Late import keeps cmd_status / cmd_resolve startup snappy.
        from bd_timew.queue import cmd_clean
        try:
            cmd_clean(quiet=True)
        except SystemExit:
            # No active beads workspace — silently skip the sweep.
            pass


def cmd_switch(issue_id: str, *, from_issue_id: str | None = None) -> None:
    """Stop one bead and start another (non-transactional).

    The intervening clean step is skipped: ``from_issue_id`` typically isn't
    closed yet, and we're about to re-prime the workspace with ``cmd_start``.
    """
    cmd_stop(from_issue_id, clean=False)
    cmd_start(issue_id)


def _timew_get(key: str) -> str | None:
    result = run(["timew", "get", key], check=False, capture=True)
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _format_elapsed(start_iso: str) -> str:
    compact = re.fullmatch(r"(\d{8})T(\d{6})Z", start_iso)
    if compact:
        date_part, time_part = compact.groups()
        start = dt.datetime.strptime(
            f"{date_part}T{time_part}Z", "%Y%m%dT%H%M%SZ"
        ).replace(tzinfo=dt.timezone.utc)
        now = dt.datetime.now(dt.timezone.utc)
    else:
        start = dt.datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
        now = (
            dt.datetime.now(dt.timezone.utc)
            if start.tzinfo
            else dt.datetime.now()
        )
    delta = now - start
    total_seconds = int(delta.total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    return f"{hours}h {minutes:02d}m" if hours else f"{minutes}m"


def cmd_status() -> None:
    """Show the active timew interval's bead, tuple, and elapsed time."""
    start_iso = _timew_get("dom.active.start")
    if not start_iso:
        root_log.info("no active timew interval")
        return

    tag_count = int(_timew_get("dom.active.tag.count") or "0")
    tags = [
        t for i in range(1, tag_count + 1)
        if (t := _timew_get(f"dom.active.tag.{i}"))
    ]

    bead_id = next((t for t in tags if ":" not in t), None)
    elapsed = _format_elapsed(start_iso)

    if bead_id:
        issue = None
        try:
            issue = get_issue(bead_id)
        except SystemExit:
            pass
        title = (issue or {}).get("title", "")
        status = (issue or {}).get("status", "")
        root_log.info("Tracking: %s  %s", bead_id, title)
        if status:
            root_log.info("Status:   %s", status)
    else:
        root_log.info("Tracking: (no bead tag found on active interval)")

    root_log.info("Elapsed:  %s", elapsed)

    tuple_display: dict[str, str | None] = {"client": None, "case": None, "svc": None}
    for tag in tags:
        for key in tuple_display:
            if tag.startswith(f"{key}:"):
                tuple_display[key] = tag[len(f"{key}:"):]
    root_log.info("Client:   %s", tuple_display["client"] or "(none)")
    root_log.info("Case:     %s", tuple_display["case"] or "(none)")
    root_log.info("Svc:      %s", tuple_display["svc"] or "(none)")
