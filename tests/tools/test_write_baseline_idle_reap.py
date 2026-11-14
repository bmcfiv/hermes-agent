"""The terminal idle reaper must not erase what the file tools know about a task's own writes.

``_cleanup_inactive_envs`` tears down a task's environment after ``lifetime_seconds`` (300 s) of
no terminal activity, and invalidates the task's file_ops cache so it cannot reference a dead
sandbox. That invalidation also dropped the task's write_file baselines, so a task that wrote a
script, waited five minutes (a long think, a user reply, another tool) and rewrote it was
refused: "exists but this task has not seen its full current content". A baseline is re-checked
against the file's stat and sha256 before it is honoured, so keeping it through an idle reap
cannot let a stale copy overwrite a changed file. An explicit task teardown still forgets all.
"""

from __future__ import annotations

import json
import time

import pytest

from tools import terminal_tool as tt
from tools.file_tools import clear_file_ops_cache
from tools.registry import registry
from tools.terminal_tool_lifecycle import _cleanup_inactive_envs


def _call(name, path, task_id, **arguments):
    result = registry.dispatch(name, {"path": str(path), **arguments}, task_id=task_id)
    assert isinstance(result, str)
    return json.loads(result)


class _IdleEnv:
    def __init__(self):
        self.cleaned = False

    def cleanup(self):
        self.cleaned = True


@pytest.fixture()
def reaper(monkeypatch):
    monkeypatch.setattr(tt, "_active_environments", {})
    monkeypatch.setattr(tt, "_last_activity", {})
    monkeypatch.setattr(tt, "_creation_locks", {})
    monkeypatch.setattr(tt, "_start_cleanup_thread", lambda: None)

    def reap(task_id):
        env = _IdleEnv()
        tt._active_environments[task_id] = env
        tt._last_activity[task_id] = time.time() - 600
        _cleanup_inactive_envs(lifetime_seconds=300)
        assert env.cleaned, "the idle env was not reaped; the test did not exercise the reaper"
        assert task_id not in tt._active_environments

    return reap


def test_own_write_survives_an_idle_reap(tmp_path, reaper):
    task = "idle-reap-own-write"
    path = tmp_path / "job.py"
    try:
        assert "error" not in _call("write_file", path, task, content="print('v1')\n")
        reaper(task)
        written = _call("write_file", path, task, content="print('v2')\n")
        assert "error" not in written, written
        assert path.read_text(encoding="utf-8") == "print('v2')\n"
    finally:
        clear_file_ops_cache(task)


def test_full_read_survives_an_idle_reap(tmp_path, reaper):
    task = "idle-reap-full-read"
    path = tmp_path / "notes.txt"
    path.write_text("a\nb\n", encoding="utf-8")
    try:
        assert "error" not in _call("read_file", path, task)
        reaper(task)
        assert "error" not in _call("write_file", path, task, content="c\n")
    finally:
        clear_file_ops_cache(task)


def test_a_file_changed_during_the_idle_gap_is_still_refused(tmp_path, reaper):
    task = "idle-reap-changed"
    path = tmp_path / "job.py"
    try:
        assert "error" not in _call("write_file", path, task, content="print('v1')\n")
        reaper(task)
        path.write_text("print('someone else')\n", encoding="utf-8")
        refused = _call("write_file", path, task, content="print('v2')\n")
        assert refused.get("error") and "Refusing to overwrite" in refused["error"], refused
        assert path.read_text(encoding="utf-8") == "print('someone else')\n"
    finally:
        clear_file_ops_cache(task)


def test_an_explicit_task_teardown_still_forgets_the_baseline(tmp_path):
    task = "explicit-teardown"
    path = tmp_path / "job.py"
    assert "error" not in _call("write_file", path, task, content="print('v1')\n")
    clear_file_ops_cache(task)
    refused = _call("write_file", path, task, content="print('v2')\n")
    try:
        assert refused.get("error") and "Refusing to overwrite" in refused["error"], refused
    finally:
        clear_file_ops_cache(task)
