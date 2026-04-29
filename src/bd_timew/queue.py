"""Scoped bead-execution queue subcommands.

Maintains an ordered list of bead IDs per scope in ``.beads/queue.yaml``.
Scope resolution: ``--scope`` flag → ``$BD_TIMEW_SCOPE`` env var → ``'default'``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml

from bd_timew.billing import get_issue
from bd_timew.util import QUEUE_FILE, find_beads_dir, root_log


def _fetch_title(bead_id: str) -> str:
    """Return the bead title, or '' on any error (non-fatal)."""
    try:
        return get_issue(bead_id).get("title", "")
    except SystemExit:
        return ""


def resolve_scope(scope_arg: str | None) -> str:
    """Resolve scope: ``--scope`` flag → ``$BD_TIMEW_SCOPE`` → ``'default'``.

    Passes ``'all'`` through unchanged (only meaningful for ``clear``).
    """
    if scope_arg is not None:
        return scope_arg
    env_scope = os.environ.get("BD_TIMEW_SCOPE", "").strip()
    return env_scope if env_scope else "default"


def load_all_queues(beads_dir: Path) -> dict[str, list[str]]:
    """Read the queue file; return a dict of scope → list of bead IDs."""
    path = beads_dir / QUEUE_FILE
    if not path.exists():
        return {}
    with path.open() as f:
        data = yaml.safe_load(f) or {}
    return {k: list(v or []) for k, v in data.items() if isinstance(v, list)}


def save_all_queues(beads_dir: Path, data: dict[str, list[str]]) -> None:
    """Persist non-empty scopes to the queue file; delete the file if all empty."""
    path = beads_dir / QUEUE_FILE
    cleaned = {k: v for k, v in data.items() if v}
    if cleaned:
        with path.open("w") as f:
            yaml.dump(cleaned, f, default_flow_style=False, allow_unicode=True)
    elif path.exists():
        path.unlink()


def cmd_queue(
    action: str,
    *,
    ids: list[str] | None = None,
    scope_arg: str | None = None,
    project_dir: Path | None = None,
    titles: bool = False,
) -> None:
    """Dispatch one of: list, push, unshift, pop, peek, remove, clear."""
    beads_dir = find_beads_dir(project_dir)
    explicit_scope = scope_arg is not None or bool(os.environ.get("BD_TIMEW_SCOPE", "").strip())
    scope = resolve_scope(scope_arg)
    all_queues = load_all_queues(beads_dir)

    def _fmt(bead_id: str) -> str:
        if not titles:
            return bead_id
        title = _fetch_title(bead_id)
        return f"{bead_id}  {title}" if title else bead_id

    if action == "list":
        if not explicit_scope:
            if not all_queues:
                root_log.info("All queues are empty")
                return
            for scope_name, queue in all_queues.items():
                if queue:
                    print(f"[{scope_name}]")
                    for i, bead_id in enumerate(queue, 1):
                        print(f"  {i:2d}. {_fmt(bead_id)}")
        else:
            queue = all_queues.get(scope, [])
            if not queue:
                root_log.info("Queue '%s' is empty", scope)
                return
            for i, bead_id in enumerate(queue, 1):
                print(f"{i:2d}. {_fmt(bead_id)}")

    elif action == "push":
        if not ids:
            sys.exit("bd-timew push: requires at least one <id>")
        queue = all_queues.get(scope, [])
        added = [i for i in ids if i not in queue]
        already = [i for i in ids if i in queue]
        queue.extend(added)
        all_queues[scope] = queue
        save_all_queues(beads_dir, all_queues)
        if added:
            root_log.info("[%s] Pushed: %s", scope, ", ".join(added))
        if already:
            root_log.warning("[%s] Already in queue (skipped): %s", scope, ", ".join(already))

    elif action == "unshift":
        if not ids or len(ids) != 1:
            sys.exit("bd-timew unshift: requires exactly one <id>")
        bead_id = ids[0]
        queue = all_queues.get(scope, [])
        if bead_id in queue:
            queue.remove(bead_id)
        queue.insert(0, bead_id)
        all_queues[scope] = queue
        save_all_queues(beads_dir, all_queues)
        root_log.info("[%s] Prepended: %s", scope, bead_id)

    elif action == "pop":
        queue = all_queues.get(scope, [])
        if not queue:
            root_log.info("Queue '%s' is empty", scope)
            return
        head = queue.pop(0)
        all_queues[scope] = queue
        save_all_queues(beads_dir, all_queues)
        print(head)
        if titles:
            title = _fetch_title(head)
            if title:
                root_log.info("%s  %s", head, title)

    elif action == "peek":
        queue = all_queues.get(scope, [])
        if not queue:
            root_log.info("Queue '%s' is empty", scope)
            return
        print(queue[0])
        if titles:
            title = _fetch_title(queue[0])
            if title:
                root_log.info("%s  %s", queue[0], title)

    elif action == "remove":
        if not ids:
            sys.exit("bd-timew remove: requires at least one <id>")
        queue = all_queues.get(scope, [])
        removed = []
        for bead_id in ids:
            if bead_id in queue:
                queue.remove(bead_id)
                removed.append(bead_id)
            else:
                root_log.warning("[%s] %s not in queue", scope, bead_id)
        if removed:
            all_queues[scope] = queue
            save_all_queues(beads_dir, all_queues)
            root_log.info("[%s] Removed: %s", scope, ", ".join(removed))

    elif action == "clear":
        if scope == "all":
            save_all_queues(beads_dir, {})
            root_log.info("All queues cleared")
        else:
            all_queues.pop(scope, None)
            save_all_queues(beads_dir, all_queues)
            root_log.info("Queue '%s' cleared", scope)
