"""A tool_call naming a tool by its wire spelling gets the exact working call back.

On the Anthropic OAuth wire every tool is named ``mcp__<name>`` and the system prompt (including
the deferred catalog) says ``chat_history_lookup`` where the registry says ``session_search``. A
model that copies those spellings into ``tool_call`` got "not a known tool name ... Use
tool_search" (129 refusals in 31 days on one Mac), and tool_search cannot fix a spelling it
never saw. The error now names the registered tool and the call that works, with the arguments
the model sent. Dispatch is unchanged: the same names are still refused.
"""

import json

import pytest

import tools.session_search_tool  # registers session_search
import tools.vision_tools  # registers vision_analyze
from tools import tool_search
from tools.tool_search import resolve_underlying_call
from tools.tool_search_validation import _registered_spelling, not_deferrable_error


@pytest.fixture()
def defer_session_search(monkeypatch):
    cfg = tool_search.load_config_readonly()
    pinned = type(cfg)(**{**cfg.__dict__, "defer_tools": frozenset({"session_search", "browser_vault"})})
    monkeypatch.setattr(tool_search, "load_config_readonly", lambda: pinned)


def _working_call(message):
    return message.split("Working call: ", 1)[1]


def test_prose_alias_of_a_deferred_tool_names_the_bridge_call(defer_session_search):
    args = {"query": "spark retirement", "limit": 3}
    name, _, err = resolve_underlying_call({"calls": [{"name": "chat_history_lookup", "arguments": args}]})
    assert name is None  # still refused: the fix is the message, not the dispatch
    assert "'session_search'" in err
    call = _working_call(err)
    assert call.startswith('tool_call(calls=[{"name": "session_search", "arguments": ')
    inner = json.loads(call[len('tool_call(calls=['):-len('])')])
    assert inner == {"name": "session_search", "arguments": args}
    # ...and that call resolves.
    assert resolve_underlying_call({"calls": [inner]}) == ("session_search", args, None)


def test_mcp_prefixed_deferred_tool_names_the_bridge_call(defer_session_search):
    _, _, err = resolve_underlying_call({"calls": [{"name": "mcp__session_search", "arguments": {"query": "x"}}]})
    assert 'tool_call(calls=[{"name": "session_search", "arguments": {"query": "x"}}])' in err


def test_mcp_prefixed_direct_tool_says_call_it_directly(defer_session_search):
    args = {"image_url": "/abs/shot.png", "question": "what is on screen?"}
    _, _, err = resolve_underlying_call({"calls": [{"name": "mcp__vision_analyze", "arguments": args}]})
    assert "directly-listed tool (registered as 'vision_analyze')" in err
    assert err.endswith("mcp__vision_analyze(" + json.dumps(args) + ")")
    assert "tool_search" not in err  # the old advice sent the model to a search that cannot help


def test_a_spelling_the_session_lacks_reports_unavailable(defer_session_search):
    msg = not_deferrable_error("chat_history_lookup", session_tool_names={"terminal"}, arguments={})
    assert "not available in this execution context" in msg


def test_truly_unknown_names_keep_todays_text():
    assert _registered_spelling("no_such_tool_xyz") is None
    assert _registered_spelling("mcp__no_such_tool_xyz") is None
    msg = not_deferrable_error("no_such_tool_xyz")
    assert msg.startswith("'no_such_tool_xyz' is not a known tool name.")


def test_registered_names_are_never_respelled():
    assert _registered_spelling("session_search") is None
    assert _registered_spelling("vision_analyze") is None


def test_long_arguments_are_not_echoed(defer_session_search):
    _, _, err = resolve_underlying_call({"calls": [{"name": "chat_history_lookup", "arguments": {"query": "q" * 900}}]})
    assert "{...same arguments...}" in err and "q" * 50 not in err


def test_tool_describe_names_the_registered_tool(defer_session_search):
    out = json.loads(tool_search.dispatch_tool_describe({"names": ["chat_history_lookup"]}, current_tool_defs=[]))
    blob = json.dumps(out)
    assert "session_search" in blob and "chat_history_lookup" not in out.get("not_found", [])
