"""``bd-track migrate rename`` — one-way bd-timew → bd-track on-disk migration.

Companion to the read-fallback shims in :mod:`bd_track.util` (``env_compat`` /
``path_compat``), which let an upgraded bd-track keep working on a project still
laid out for bd-timew. This command performs the *cleanup* those shims defer:
rename the on-disk artifacts to the new name so the fallbacks stop firing.

Scope (see ``bd-timew-jy9``):
  * Global dirs:  ~/.config|cache|state/bd-timew, ~/.local/share/bd-timew
  * Per project:  .beads/bd-timew.yaml sidecar, <beads>/bd-timew session logs,
                  and ``BD_TIMEW_*`` → ``BD_TRACK_*`` env-var rewrites in
                  .envrc / .env / .envrc.local / mise.toml / .mise.toml
  * ``--all-repos`` sweeps every repo registered in repos.yaml, not just cwd.

Safety: dry-run by default (``--apply`` to write); per-file ``.bak`` backups;
chezmoi-managed home/dotfile targets are skipped with a warning (editing a
chezmoi target desyncs its source — use ``chezmoi apply`` for those). The
``migrate`` namespace is shared with ``bd-timew-73v`` (timew-data import).
"""

from __future__ import annotations

import dataclasses
import shutil
from pathlib import Path

from bd_track.util import find_beads_dir, root_log, run

# Env files scanned for BD_TIMEW_* → BD_TRACK_* rewrites, relative to a repo root.
_ENV_FILES = (".envrc", ".env", ".envrc.local", "mise.toml", ".mise.toml")
_ENV_OLD = "BD_TIMEW_"
_ENV_NEW = "BD_TRACK_"

# Old/new component name under each home base dir and the beads dir.
_OLD = "bd-timew"
_NEW = "bd-track"


@dataclasses.dataclass
class Action:
    """One planned migration step. ``do`` is False for skip/no-op entries."""

    kind: str  # "dir" | "file" | "env"
    label: str  # human-readable source description
    do: bool  # True = will act under --apply; False = informational skip
    detail: str  # right-hand description ("→ <dst>", "skipped: ...", etc.)
    src: Path | None = None
    dst: Path | None = None
    count: int = 0  # env: number of BD_TIMEW_* occurrences rewritten


# ---------------------------------------------------------------------------
# chezmoi guard
# ---------------------------------------------------------------------------

def _chezmoi_managed_set() -> set[Path] | None:
    """Resolve absolute paths chezmoi manages, or None if chezmoi is unavailable.

    None (vs empty set) signals "could not consult chezmoi" so the caller can
    note it rather than silently treating everything as unmanaged.
    """
    if shutil.which("chezmoi") is None:
        return None
    res = run(["chezmoi", "managed", "--path-style", "absolute"],
              check=False, capture=True)
    if res.returncode != 0:
        return None
    return {Path(line.strip()) for line in res.stdout.splitlines() if line.strip()}


def _is_managed(path: Path, managed: set[Path] | None) -> bool:
    """True if ``path`` (a file) or any descendant (a dir) is chezmoi-managed."""
    if not managed:
        return False
    rp = path.resolve()
    if rp in managed:
        return True
    # Directory target: managed if chezmoi owns anything beneath it.
    return any(rp == m or rp in m.parents for m in managed)


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------

def _plan_move(src: Path, dst: Path, kind: str, *,
               managed: set[Path] | None, check_chezmoi: bool) -> Action:
    """Plan a rename of ``src`` → ``dst`` with idempotency + chezmoi guards."""
    label = str(src)
    if not src.exists():
        return Action(kind, label, do=False, detail="skipped: source absent",
                      src=src, dst=dst)
    if dst.exists():
        return Action(kind, label, do=False,
                      detail=f"skipped: target {dst} already exists (manual merge)",
                      src=src, dst=dst)
    if check_chezmoi and _is_managed(src, managed):
        return Action(kind, label, do=False,
                      detail="skipped: chezmoi-managed (run `chezmoi apply` instead)",
                      src=src, dst=dst)
    return Action(kind, label, do=True, detail=f"→ {dst}", src=src, dst=dst)


def _plan_env(path: Path, *, managed: set[Path] | None) -> Action | None:
    """Plan a BD_TIMEW_* → BD_TRACK_* rewrite, or None if nothing to do."""
    if not path.exists():
        return None
    text = path.read_text()
    count = text.count(_ENV_OLD)
    if count == 0:
        return None
    label = str(path)
    if _is_managed(path, managed):
        return Action("env", label, do=False,
                      detail="skipped: chezmoi-managed (run `chezmoi apply` instead)",
                      src=path, count=count)
    return Action("env", label, do=True,
                  detail=f"rewrite {count} {_ENV_OLD}* → {_ENV_NEW}*",
                  src=path, count=count)


def _plan_global(managed: set[Path] | None) -> list[Action]:
    home = Path.home()
    bases = [
        home / ".config",
        home / ".cache",
        home / ".local" / "state",
        home / ".local" / "share",
    ]
    return [
        _plan_move(base / _OLD, base / _NEW, "dir",
                   managed=managed, check_chezmoi=True)
        for base in bases
    ]


def _plan_project(root: Path, managed: set[Path] | None) -> list[Action]:
    """Plan per-project steps for the repo rooted at ``root``."""
    actions: list[Action] = []
    beads = root / ".beads"

    # Sidecar + session-log dir live inside .beads/ — project-internal data,
    # never chezmoi-managed, so no chezmoi check (per design decision).
    actions.append(_plan_move(beads / f"{_OLD}.yaml", beads / f"{_NEW}.yaml",
                              "file", managed=managed, check_chezmoi=False))
    actions.append(_plan_move(beads / _OLD, beads / _NEW,
                              "dir", managed=managed, check_chezmoi=False))

    # Env files are dotfiles a chezmoi setup may own — guarded.
    for name in _ENV_FILES:
        action = _plan_env(root / name, managed=managed)
        if action is not None:
            actions.append(action)
    return actions


def _registered_repo_roots() -> list[Path]:
    """Repo roots from repos.yaml (read via the compat path), sorted + deduped."""
    from bd_track.project import load_repos_config

    config = load_repos_config()
    roots: list[Path] = []
    for entry in config.get("repos", []):
        path = entry.get("path")
        if path:
            roots.append(Path(path).expanduser())
    return sorted(set(roots), key=str)


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

def _execute(action: Action, *, backup: bool) -> None:
    if action.kind in ("dir", "file"):
        assert action.src is not None and action.dst is not None
        shutil.move(str(action.src), str(action.dst))
        root_log.info("moved %s → %s", action.src, action.dst)
        return
    if action.kind == "env":
        assert action.src is not None
        path = action.src
        if backup:
            bak = path.parent / (path.name + ".bak")
            shutil.copy2(path, bak)
            root_log.info("backed up %s → %s", path, bak)
        path.write_text(path.read_text().replace(_ENV_OLD, _ENV_NEW))
        root_log.info("rewrote %d %s* → %s* in %s",
                      action.count, _ENV_OLD, _ENV_NEW, path)


def _render_group(title: str, actions: list[Action]) -> None:
    print(f"\n{title}")
    if not actions:
        print("  (nothing to migrate)")
        return
    for a in actions:
        mark = "move " if a.do and a.kind != "env" else \
               "edit " if a.do else "skip "
        print(f"  {mark} {a.label}  {a.detail}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def cmd_migrate_rename(
    *,
    project_dir: Path | None = None,
    all_repos: bool = False,
    apply: bool = False,
    backup: bool = True,
) -> None:
    """Rename bd-timew on-disk artifacts to bd-track. Dry-run unless ``apply``."""
    managed = _chezmoi_managed_set()

    # Resolve target repo roots BEFORE any global rename moves repos.yaml.
    if all_repos:
        roots = _registered_repo_roots()
    else:
        try:
            beads = find_beads_dir(project_dir)
            roots = [beads.parent]
        except SystemExit:
            roots = []

    global_actions = _plan_global(managed)
    project_actions = {root: _plan_project(root, managed) for root in roots}

    mode = "APPLYING" if apply else "DRY RUN (pass --apply to migrate)"
    print(f"bd-track migrate rename — {mode}")
    if managed is None:
        print("  note: chezmoi unavailable; home/dotfile targets are NOT guarded")

    _render_group("Global directories:", global_actions)
    for root, actions in project_actions.items():
        _render_group(f"Project {root}:", actions)

    todo = [a for a in global_actions if a.do]
    todo += [a for actions in project_actions.values() for a in actions if a.do]

    if not apply:
        print(f"\n{len(todo)} change(s) planned. Re-run with --apply to perform them.")
        return

    if not todo:
        print("\nNothing to migrate.")
        return

    for action in todo:
        _execute(action, backup=backup)
    print(f"\nMigrated {len(todo)} item(s).")
