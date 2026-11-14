"""start_session: opt-in tool, guards, command shape."""
import json
from unittest import mock

import pytest

from tools import session_spawn as ss
from tools.registry import registry


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, "get_hermes_home", lambda: tmp_path)
    monkeypatch.delenv(ss.DEPTH_ENV, raising=False)
    return tmp_path


def _cfg(monkeypatch, **section):
    monkeypatch.setattr(ss, "_config", lambda: {"session_spawn": section, "model": {"default": "claude-sonnet-5-5"}})


class _Proc:
    pid = 4242

    def poll(self):
        return None


def test_registered_and_off_by_default(monkeypatch):
    entry = registry.get_entry("start_session")
    assert entry is not None and entry.toolset == "delegation"
    _cfg(monkeypatch)
    assert entry.check_fn() is False
    _cfg(monkeypatch, enabled=True)
    assert entry.check_fn() is True


def test_header_and_model_resolution():
    assert ss.parse_header("Model: Opus\nTitle: Plan v2\n\nbody") == ("Opus", "Plan v2")
    assert ss.parse_header("\n".join(["a"] * 7 + ["Title: late"])) == (None, None)
    cfg = {"model": {"default": "claude-sonnet-5-5", "aliases": {"opus": "anthropic/claude-opus-5-5"}}}
    assert ss.resolve_model("Opus", cfg) == ("anthropic/claude-opus-5-5", "")
    assert ss.resolve_model("Sonnet", cfg) == (None, "")
    assert ss.resolve_model("a/b", cfg) == ("a/b", "")
    assert "default model" in ss.resolve_model("Other", cfg)[1]


def test_build_command_flags(tmp_path):
    cmd = ss.build_command(tmp_path / "q.md", "T", "p/m", "/w", ["s1"])
    assert cmd[cmd.index("chat"):][:6] == ["chat", "--query-file", str(tmp_path / "q.md"), "--format", "stream-json", "--source"]
    assert cmd[-9:] == ["-c", "T", "--create-if-missing", "-m", "p/m", "--in", "/w", "-s", "s1"]


def test_depth_guard_and_child_env(home, monkeypatch):
    _cfg(monkeypatch, enabled=True)
    monkeypatch.setenv(ss.DEPTH_ENV, "1")
    assert "may not start another session" in json.loads(ss.start_session({"prompt": "x"}))["error"]
    monkeypatch.setenv("HERMES_SESSION_ID", "parent")
    monkeypatch.setenv("HERMES_SESSION_KEY", "k")
    env = ss.child_env(0)
    assert env[ss.DEPTH_ENV] == "1" and "HERMES_SESSION_ID" not in env and "HERMES_SESSION_KEY" not in env


def test_cron_and_cap_guards(home, monkeypatch):
    _cfg(monkeypatch, enabled=True, max_per_hour=1)
    monkeypatch.setattr(ss, "get_session_env", lambda name, default="": {"HERMES_CRON_SESSION": "1"}.get(name, default))
    assert "cron" in json.loads(ss.start_session({"prompt": "x"}))["error"]
    monkeypatch.setattr(ss, "get_session_env", lambda name, default="": {"HERMES_SESSION_ID": "p"}.get(name, default))
    (home / "session_spawn").mkdir()
    (home / "session_spawn" / "ledger.jsonl").write_text(json.dumps({"ts": __import__("time").time(), "parent": "p"}) + "\n")
    assert "cap reached" in json.loads(ss.start_session({"prompt": "x"}))["error"]


def test_existing_title_refused(home, monkeypatch):
    _cfg(monkeypatch, enabled=True)
    monkeypatch.setattr(ss, "title_exists", lambda t: "sid-1")
    out = json.loads(ss.start_session({"prompt": "x", "title": "Dup"}))
    assert out["existing_session_id"] == "sid-1"


def test_spawn_returns_session_id(home, monkeypatch):
    _cfg(monkeypatch, enabled=True)
    monkeypatch.setattr(ss, "title_exists", lambda t: None)
    with mock.patch.object(ss.subprocess, "Popen", return_value=_Proc()) as popen, \
            mock.patch.object(ss, "_read_session_id", return_value="sess-9"):
        out = json.loads(ss.start_session({"prompt": "Title: Hello\n\ndo it"}))
    assert out["session_id"] == "sess-9" and out["title"] == "Hello" and out["started"] is True
    assert popen.call_args.kwargs["env"][ss.DEPTH_ENV] == "1"
    assert (home / "session_spawn" / "ledger.jsonl").exists()
