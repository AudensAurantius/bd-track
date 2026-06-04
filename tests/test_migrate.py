"""Tests for `bd-track migrate rename` (bd-timew-jy9).

The one-way bd-timew → bd-track on-disk migration: global dirs, the per-project
.beads sidecar + session logs, and BD_TIMEW_* env-var rewrites. Dry-run by
default, chezmoi-managed targets skipped, idempotent on re-run.
"""

from __future__ import annotations

from pathlib import Path

from bd_track import migrate
from bd_track.migrate import (
    _is_managed,
    _plan_env,
    _plan_move,
    cmd_migrate_rename,
)

# ---------------------------------------------------------------------------
# chezmoi guard
# ---------------------------------------------------------------------------

def test_is_managed_none_or_empty():
    assert _is_managed(Path("/x"), None) is False
    assert _is_managed(Path("/x"), set()) is False


def test_is_managed_exact_file(tmp_path):
    f = tmp_path / ".envrc"
    f.touch()
    assert _is_managed(f, {f.resolve()}) is True


def test_is_managed_dir_with_managed_descendant(tmp_path):
    d = tmp_path / "cfg"
    (d / "sub").mkdir(parents=True)
    inner = d / "sub" / "file"
    inner.touch()
    assert _is_managed(d, {inner.resolve()}) is True
    assert _is_managed(tmp_path / "other", {inner.resolve()}) is False


# ---------------------------------------------------------------------------
# _plan_move
# ---------------------------------------------------------------------------

def test_plan_move_source_absent(tmp_path):
    a = _plan_move(tmp_path / "old", tmp_path / "new", "dir",
                   managed=None, check_chezmoi=True)
    assert a.do is False and "absent" in a.detail


def test_plan_move_target_exists(tmp_path):
    (tmp_path / "old").mkdir()
    (tmp_path / "new").mkdir()
    a = _plan_move(tmp_path / "old", tmp_path / "new", "dir",
                   managed=None, check_chezmoi=True)
    assert a.do is False and "already exists" in a.detail


def test_plan_move_chezmoi_managed_skipped(tmp_path):
    old = tmp_path / "old"
    old.mkdir()
    a = _plan_move(old, tmp_path / "new", "dir",
                   managed={old.resolve()}, check_chezmoi=True)
    assert a.do is False and "chezmoi-managed" in a.detail


def test_plan_move_chezmoi_check_disabled(tmp_path):
    """Project-internal .beads artifacts skip the chezmoi check entirely."""
    old = tmp_path / "old"
    old.mkdir()
    a = _plan_move(old, tmp_path / "new", "dir",
                   managed={old.resolve()}, check_chezmoi=False)
    assert a.do is True


def test_plan_move_normal(tmp_path):
    old = tmp_path / "old"
    old.mkdir()
    a = _plan_move(old, tmp_path / "new", "dir",
                   managed=set(), check_chezmoi=True)
    assert a.do is True and a.dst == tmp_path / "new"


# ---------------------------------------------------------------------------
# _plan_env
# ---------------------------------------------------------------------------

def test_plan_env_no_file(tmp_path):
    assert _plan_env(tmp_path / ".envrc", managed=None) is None


def test_plan_env_no_tokens(tmp_path):
    f = tmp_path / ".envrc"
    f.write_text("export FOO=bar\n")
    assert _plan_env(f, managed=None) is None


def test_plan_env_counts_tokens(tmp_path):
    f = tmp_path / ".envrc"
    f.write_text("export BD_TIMEW_SCOPE=x\nexport BD_TIMEW_ACTOR=y\n")
    a = _plan_env(f, managed=set())
    assert a.do is True and a.count == 2


def test_plan_env_managed_skipped(tmp_path):
    f = tmp_path / ".envrc"
    f.write_text("export BD_TIMEW_SCOPE=x\n")
    a = _plan_env(f, managed={f.resolve()})
    assert a.do is False and "chezmoi-managed" in a.detail


# ---------------------------------------------------------------------------
# cmd_migrate_rename — end to end
# ---------------------------------------------------------------------------

def _make_project(root: Path, *, sidecar=True, sessions=True, envrc=True) -> None:
    beads = root / ".beads"
    beads.mkdir(parents=True)
    if sidecar:
        (beads / "bd-timew.yaml").write_text("billing: {}\n")
    if sessions:
        (beads / "bd-timew" / "sessions").mkdir(parents=True)
        (beads / "bd-timew" / "sessions" / "s.jsonl").write_text("{}\n")
    if envrc:
        (root / ".envrc").write_text("export BD_TIMEW_SCOPE=demo\n")


def _isolate(monkeypatch, tmp_path) -> Path:
    """Point HOME at a tmp dir and stub chezmoi to 'manages nothing'."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(migrate, "_chezmoi_managed_set", lambda: set())
    return home


def test_dry_run_writes_nothing(tmp_path, monkeypatch, capsys):
    _isolate(monkeypatch, tmp_path)
    proj = tmp_path / "proj"
    _make_project(proj)

    cmd_migrate_rename(project_dir=proj, apply=False)

    # Nothing renamed.
    assert (proj / ".beads" / "bd-timew.yaml").exists()
    assert (proj / ".beads" / "bd-track.yaml").exists() is False
    assert "DRY RUN" in capsys.readouterr().out


def test_apply_migrates_project(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    proj = tmp_path / "proj"
    _make_project(proj)

    cmd_migrate_rename(project_dir=proj, apply=True)

    beads = proj / ".beads"
    assert (beads / "bd-track.yaml").exists()
    assert (beads / "bd-timew.yaml").exists() is False
    assert (beads / "bd-track" / "sessions" / "s.jsonl").exists()
    assert (beads / "bd-timew").exists() is False
    assert (proj / ".envrc").read_text() == "export BD_TRACK_SCOPE=demo\n"
    assert (proj / ".envrc.bak").read_text() == "export BD_TIMEW_SCOPE=demo\n"


def test_apply_migrates_global_dirs(tmp_path, monkeypatch):
    home = _isolate(monkeypatch, tmp_path)
    (home / ".config" / "bd-timew").mkdir(parents=True)
    (home / ".config" / "bd-timew" / "repos.yaml").write_text("repos: []\n")
    proj = tmp_path / "proj"
    _make_project(proj, sidecar=False, sessions=False, envrc=False)

    cmd_migrate_rename(project_dir=proj, apply=True)

    assert (home / ".config" / "bd-track" / "repos.yaml").exists()
    assert (home / ".config" / "bd-timew").exists() is False


def test_apply_idempotent(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    proj = tmp_path / "proj"
    _make_project(proj)

    cmd_migrate_rename(project_dir=proj, apply=True)
    # Second run: everything already renamed → no-op, no exception.
    cmd_migrate_rename(project_dir=proj, apply=True)

    assert (proj / ".beads" / "bd-track.yaml").exists()
    assert (proj / ".envrc").read_text() == "export BD_TRACK_SCOPE=demo\n"


def test_no_backup_flag(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    proj = tmp_path / "proj"
    _make_project(proj, sidecar=False, sessions=False)

    cmd_migrate_rename(project_dir=proj, apply=True, backup=False)

    assert (proj / ".envrc").read_text() == "export BD_TRACK_SCOPE=demo\n"
    assert (proj / ".envrc.bak").exists() is False


def test_chezmoi_managed_env_skipped(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    proj = tmp_path / "proj"
    _make_project(proj, sidecar=False, sessions=False)
    # Mark the .envrc as chezmoi-managed.
    monkeypatch.setattr(migrate, "_chezmoi_managed_set",
                        lambda: {(proj / ".envrc").resolve()})

    cmd_migrate_rename(project_dir=proj, apply=True)

    # Untouched — still the old token.
    assert (proj / ".envrc").read_text() == "export BD_TIMEW_SCOPE=demo\n"
    assert (proj / ".envrc.bak").exists() is False


def test_all_repos_sweeps_registry(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    r1, r2 = tmp_path / "r1", tmp_path / "r2"
    _make_project(r1, sessions=False, envrc=False)
    _make_project(r2, sessions=False, envrc=False)
    monkeypatch.setattr(migrate, "_registered_repo_roots", lambda: [r1, r2])

    cmd_migrate_rename(all_repos=True, apply=True)

    assert (r1 / ".beads" / "bd-track.yaml").exists()
    assert (r2 / ".beads" / "bd-track.yaml").exists()
