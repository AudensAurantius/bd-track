"""Tests for queue.py — scoped queue operations."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from bd_timew.queue import (
    cmd_queue,
    load_all_queues,
    resolve_scope,
    save_all_queues,
)


@pytest.fixture
def beads_dir(tmp_path: Path) -> Path:
    """A temporary .beads/-like directory for queue file ops."""
    d = tmp_path / ".beads"
    d.mkdir()
    return d


# ---------------------------------------------------------------------------
# resolve_scope
# ---------------------------------------------------------------------------

def test_resolve_scope_explicit_wins():
    with patch.dict(os.environ, {"BD_TIMEW_SCOPE": "from_env"}, clear=False):
        assert resolve_scope("explicit") == "explicit"


def test_resolve_scope_env_var_used_when_no_flag():
    with patch.dict(os.environ, {"BD_TIMEW_SCOPE": "from_env"}, clear=False):
        assert resolve_scope(None) == "from_env"


def test_resolve_scope_default_when_neither_set(monkeypatch):
    monkeypatch.delenv("BD_TIMEW_SCOPE", raising=False)
    assert resolve_scope(None) == "default"


def test_resolve_scope_empty_env_var_treated_as_unset(monkeypatch):
    monkeypatch.setenv("BD_TIMEW_SCOPE", "   ")
    assert resolve_scope(None) == "default"


def test_resolve_scope_passes_all_through():
    """clear --scope all uses 'all' as a special value; resolve_scope must not normalize it."""
    assert resolve_scope("all") == "all"


# ---------------------------------------------------------------------------
# load_all_queues / save_all_queues round-trip
# ---------------------------------------------------------------------------

def test_load_all_queues_missing_file_returns_empty(beads_dir):
    assert load_all_queues(beads_dir) == {}


def test_save_then_load_round_trip(beads_dir):
    data = {"default": ["A", "B"], "tooling": ["C"]}
    save_all_queues(beads_dir, data)
    assert load_all_queues(beads_dir) == data


def test_save_empty_deletes_file(beads_dir):
    save_all_queues(beads_dir, {"default": ["A"]})
    save_all_queues(beads_dir, {"default": []})
    assert not (beads_dir / "queue.yaml").exists()


def test_save_drops_empty_scopes(beads_dir):
    save_all_queues(beads_dir, {"default": ["A"], "empty": []})
    loaded = load_all_queues(beads_dir)
    assert loaded == {"default": ["A"]}


# ---------------------------------------------------------------------------
# cmd_queue actions
# ---------------------------------------------------------------------------

def test_push_appends_and_dedups(beads_dir, capsys, monkeypatch):
    monkeypatch.delenv("BD_TIMEW_SCOPE", raising=False)
    cmd_queue("push", ids=["A", "B"], project_dir=beads_dir.parent)
    cmd_queue("push", ids=["B", "C"], project_dir=beads_dir.parent)
    assert load_all_queues(beads_dir) == {"default": ["A", "B", "C"]}


def test_unshift_prepends_and_moves_existing(beads_dir, monkeypatch):
    monkeypatch.delenv("BD_TIMEW_SCOPE", raising=False)
    cmd_queue("push", ids=["A", "B", "C"], project_dir=beads_dir.parent)
    cmd_queue("unshift", ids=["B"], project_dir=beads_dir.parent)
    assert load_all_queues(beads_dir) == {"default": ["B", "A", "C"]}


def test_pop_removes_and_prints_head(beads_dir, capsys, monkeypatch):
    monkeypatch.delenv("BD_TIMEW_SCOPE", raising=False)
    cmd_queue("push", ids=["A", "B"], project_dir=beads_dir.parent)
    capsys.readouterr()  # discard push log
    cmd_queue("pop", project_dir=beads_dir.parent)
    captured = capsys.readouterr()
    assert captured.out.strip() == "A"
    assert load_all_queues(beads_dir) == {"default": ["B"]}


def test_peek_does_not_remove(beads_dir, capsys, monkeypatch):
    monkeypatch.delenv("BD_TIMEW_SCOPE", raising=False)
    cmd_queue("push", ids=["A", "B"], project_dir=beads_dir.parent)
    capsys.readouterr()
    cmd_queue("peek", project_dir=beads_dir.parent)
    captured = capsys.readouterr()
    assert captured.out.strip() == "A"
    assert load_all_queues(beads_dir) == {"default": ["A", "B"]}


def test_remove_deletes_specific_ids(beads_dir, monkeypatch):
    monkeypatch.delenv("BD_TIMEW_SCOPE", raising=False)
    cmd_queue("push", ids=["A", "B", "C"], project_dir=beads_dir.parent)
    cmd_queue("remove", ids=["B"], project_dir=beads_dir.parent)
    assert load_all_queues(beads_dir) == {"default": ["A", "C"]}


def test_clear_scope_removes_only_named(beads_dir, monkeypatch):
    monkeypatch.delenv("BD_TIMEW_SCOPE", raising=False)
    cmd_queue("push", ids=["A"], scope_arg="default", project_dir=beads_dir.parent)
    cmd_queue("push", ids=["X"], scope_arg="tooling", project_dir=beads_dir.parent)
    cmd_queue("clear", scope_arg="tooling", project_dir=beads_dir.parent)
    assert load_all_queues(beads_dir) == {"default": ["A"]}


def test_clear_scope_all_empties_everything(beads_dir, monkeypatch):
    monkeypatch.delenv("BD_TIMEW_SCOPE", raising=False)
    cmd_queue("push", ids=["A"], scope_arg="default", project_dir=beads_dir.parent)
    cmd_queue("push", ids=["X"], scope_arg="tooling", project_dir=beads_dir.parent)
    cmd_queue("clear", scope_arg="all", project_dir=beads_dir.parent)
    assert load_all_queues(beads_dir) == {}


def test_pop_empty_queue_is_noop(beads_dir, capsys, monkeypatch):
    monkeypatch.delenv("BD_TIMEW_SCOPE", raising=False)
    cmd_queue("pop", project_dir=beads_dir.parent)
    captured = capsys.readouterr()
    assert captured.out == ""  # nothing on stdout


def test_push_separates_scopes(beads_dir, monkeypatch):
    monkeypatch.delenv("BD_TIMEW_SCOPE", raising=False)
    cmd_queue("push", ids=["A"], scope_arg="default", project_dir=beads_dir.parent)
    cmd_queue("push", ids=["B"], scope_arg="tooling", project_dir=beads_dir.parent)
    assert load_all_queues(beads_dir) == {"default": ["A"], "tooling": ["B"]}


def test_env_var_sets_scope(beads_dir, monkeypatch):
    monkeypatch.setenv("BD_TIMEW_SCOPE", "tooling")
    cmd_queue("push", ids=["A"], project_dir=beads_dir.parent)
    assert load_all_queues(beads_dir) == {"tooling": ["A"]}
