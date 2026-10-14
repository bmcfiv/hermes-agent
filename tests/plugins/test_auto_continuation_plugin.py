"""Tests for the bundled auto-continuation plugin (plugins/auto-continuation/)."""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PLUGIN_DIR = Path(__file__).resolve().parents[2] / "plugins" / "auto-continuation"


def _load(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    for name in [n for n in sys.modules if n.startswith("auto_continuation_t")]:
        del sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        "auto_continuation_t", PLUGIN_DIR / "__init__.py", submodule_search_locations=[str(PLUGIN_DIR)]
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["auto_continuation_t"] = mod
    spec.loader.exec_module(mod)
    return mod, sys.modules["auto_continuation_t.continuation"]


class FakeCtx:
    def __init__(self, config=None, text="Goal: ship it\nNext: 1. run tests", fail=False):
        self.config = config or {}
        self.hooks = {}
        self.store = {}
        self.calls = []
        self._text, self._fail = text, fail
        self.state = SimpleNamespace(get=self.store.get, set=self.store.__setitem__)
        self.llm = SimpleNamespace(complete=self._complete)

    def _complete(self, messages, **kw):
        self.calls.append(messages)
        if self._fail:
            raise RuntimeError("boom")
        return SimpleNamespace(text=self._text)

    def get_config(self, key, default=None):
        return self.config.get(key, default)

    def register_hook(self, name, fn):
        self.hooks[name] = fn


def _drive(ctx, mod, sid="s1", tokens=0, window=0, history=None):
    mod.register(ctx)
    ctx.hooks["pre_llm_call"](
        session_id=sid, user_message="do the thing",
        conversation_history=history if history is not None else [{"role": "user", "content": "start"}],
    )
    ctx.hooks["post_api_request"](session_id=sid, usage={"prompt_tokens": tokens}, context_length=window)
    return lambda text="answer", platform="desktop": ctx.hooks["transform_llm_output"](
        response_text=text, session_id=sid, model="m-1", platform=platform
    )


def test_trigger_reason_each_limit(monkeypatch, tmp_path):
    c = _load(monkeypatch, tmp_path)[1]
    s = c.Settings(context_percent=40, cost_usd=5, messages=200)
    assert c.trigger_reason(s, prompt_tokens=39_000, context_length=100_000) is None
    assert "40% full" in c.trigger_reason(s, prompt_tokens=40_000, context_length=100_000)
    assert "$5.00" in c.trigger_reason(s, cost_usd=5.0)
    assert "200 messages" in c.trigger_reason(s, message_count=200)
    off = c.Settings(context_percent=0, cost_usd=0, messages=0)
    assert c.trigger_reason(off, prompt_tokens=99, context_length=100, cost_usd=99, message_count=999) is None


def test_digest_keeps_opening_and_newest_within_budget(monkeypatch, tmp_path):
    c = _load(monkeypatch, tmp_path)[1]
    history = [{"role": "user", "content": "OPENING GOAL"}] + [
        {"role": "assistant", "content": f"step {i} " + "x" * 400} for i in range(100)
    ]
    out = c.transcript_digest(history, "latest ask", "latest answer", budget=3000)
    assert "OPENING GOAL" in out and "latest ask" in out and "latest answer" in out
    assert "earlier messages omitted" in out and len(out) < 3600
    assert "step 0 " in out and "step 5 " not in out and "step 99 " in out


def test_known_paths_from_tool_calls_and_fence_growth(monkeypatch, tmp_path):
    c = _load(monkeypatch, tmp_path)[1]
    hist = [{"role": "assistant", "tool_calls": [
        {"function": {"name": "write_file", "arguments": '{"path": "/Users/x/proj/a.py"}'}}]}]
    assert c.known_paths(hist) == ["/Users/x/proj/a.py"]
    assert c.reply_suffix("r", "has ``` inside").count("````") == 2


def test_fires_once_with_header_and_saves_file(monkeypatch, tmp_path):
    mod, _ = _load(monkeypatch, tmp_path)
    ctx = FakeCtx({"header": "Model: {model}\nContinues: {session_id}"})
    reply = _drive(ctx, mod, tokens=50_000, window=100_000)
    out = reply()
    assert out.startswith("answer") and "Long session:" in out and "50% full" in out
    assert "Model: m-1\nContinues: s1\n\nGoal: ship it" in out
    assert (tmp_path / "continuations" / "s1.md").read_text().startswith("Model: m-1")
    assert reply() is None                       # once per session
    assert len(ctx.calls) == 1
    assert "do the thing" in ctx.calls[0][1]["content"]


def test_below_limits_and_cron_do_nothing(monkeypatch, tmp_path):
    mod, _ = _load(monkeypatch, tmp_path)
    ctx = FakeCtx({"cost_usd": 0})
    reply = _drive(ctx, mod, tokens=10_000, window=100_000)
    assert reply() is None and ctx.calls == []
    ctx2 = FakeCtx()
    reply2 = _drive(ctx2, mod, sid="s2", tokens=90_000, window=100_000)
    assert reply2(platform="cron") is None and ctx2.calls == []


def test_llm_failure_retries_twice_then_stops(monkeypatch, tmp_path):
    mod, _ = _load(monkeypatch, tmp_path)
    ctx = FakeCtx(fail=True)
    reply = _drive(ctx, mod, tokens=90_000, window=100_000)
    assert [reply(), reply(), reply()] == [None, None, None]
    assert len(ctx.calls) == 2


def test_message_count_trigger(monkeypatch, tmp_path):
    mod, _ = _load(monkeypatch, tmp_path)
    ctx = FakeCtx({"messages": 5, "cost_usd": 0, "context_percent": 0})
    hist = [{"role": "user", "content": str(i)} for i in range(6)]
    assert "messages" in _drive(ctx, mod, history=hist)()


def test_header_command_output_is_added(monkeypatch, tmp_path):
    mod, _ = _load(monkeypatch, tmp_path)
    ctx = FakeCtx({"header_command": f"{sys.executable} -c \"print('Title: X')\""})
    out = _drive(ctx, mod, tokens=90_000, window=100_000)()
    assert "Continues: s1\nTitle: X\n\nGoal" in out


def test_manifest_declares_registered_hooks():
    import yaml
    manifest = yaml.safe_load((PLUGIN_DIR / "plugin.yaml").read_text())
    assert set(manifest["provides_hooks"]) == {"pre_llm_call", "post_api_request", "transform_llm_output"}
