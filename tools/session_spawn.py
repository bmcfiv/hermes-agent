#!/usr/bin/env python3
"""start_session: the agent opens a NEW, separate, resumable chat session.

Opt-in (``session_spawn.enabled``, default off; ``check_fn`` withdraws the tool from the schema).
Unlike ``delegate_task`` (in-process, no session row, result returned) the child is a real
``hermes chat --query-file`` process with its own transcript and title, listed and resumable like
any chat; when it needs the user it ends its first turn with the question and waits there.

Guards: a spawned session may not spawn (``max_depth``), an hourly cap per parent session
(``max_per_hour``), no spawning from cron runs unless ``allow_cron``, and an existing title is never
resumed. The child env carries no ``HERMES_SESSION_*`` identity of the parent.
"""

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from gateway.session_context import get_session_env
from hermes_cli.sqlite_safe_read import connect_tracked
from hermes_constants import get_hermes_home
from tools.registry import registry, tool_error

DEFAULTS = {"enabled": False, "max_depth": 1, "max_per_hour": 6, "allow_cron": False}
DEPTH_ENV = "HERMES_SPAWN_DEPTH"
_READY_TIMEOUT = 25.0
# Header zone of a continuation prompt: "Model: X" / "Title: Y" in its first lines.
_MODEL_RE = re.compile(r"^[ \t>*_`-]*Model:[ \t*_`]*(?P<v>[^\n*_`]+?)[ \t*_`]*$", re.IGNORECASE)
_TITLE_RE = re.compile(r"^[ \t>*_`-]*Title:[ \t*_]*(?P<v>[^\s*_][^\n]*?)[ \t*_`]*$", re.IGNORECASE)
_HEADER_LINES = 6
_SESSION_RE = re.compile(r"^Session:\s*(\S+)", re.MULTILINE)
_SCRUBBED_ENV = ("HERMES_UI_SESSION_ID", "HERMES_SUPERVISED_CHILD", "HERMES_CRON_SESSION", "HERMES_EXEC_ASK", "HERMES_QUIET")


def _config() -> dict[str, Any]:
    try:
        from hermes_cli.config import load_config_readonly
        return load_config_readonly() or {}
    except (ImportError, OSError, ValueError):
        return {}


def _settings() -> dict[str, Any]:
    section = _config().get("session_spawn")
    return {**DEFAULTS, **(section if isinstance(section, dict) else {})}


def is_enabled() -> bool:
    return bool(_settings()["enabled"])


def parse_header(prompt: str) -> tuple[str | None, str | None]:
    """(model, title) from the first few non-empty lines of a continuation prompt."""
    model = title = None
    for line in [ln for ln in prompt.splitlines() if ln.strip()][:_HEADER_LINES]:
        if model is None and (m := _MODEL_RE.match(line)):
            model = m.group("v").strip()
        elif title is None and (m := _TITLE_RE.match(line)):
            title = m.group("v").strip()
    return model, title


def resolve_model(token: str | None, config: dict[str, Any]) -> tuple[str | None, str]:
    """(``-m`` argument or None for the default, note). Unknown names fall back to the default."""
    raw = (token or "").strip()
    if not raw:
        return None, ""
    if "/" in raw:
        return raw, ""
    model_cfg = config.get("model") if isinstance(config.get("model"), dict) else {}
    aliases = {str(k).lower(): str(v) for k, v in (model_cfg.get("aliases") or {}).items()}
    if raw.lower() in aliases:
        return aliases[raw.lower()], ""
    default = str(model_cfg.get("default") or "")
    if not default or raw.lower() in default.lower():
        return None, ""
    return None, f"model {raw!r} is not an alias or the default family; the default model was used"


def title_exists(title: str) -> str | None:
    db = get_hermes_home() / "state.db"
    try:
        con = connect_tracked(f"file:{db}?mode=ro", tracking_path=db, uri=True, timeout=5)
        try:
            row = con.execute("SELECT id FROM sessions WHERE title = ? LIMIT 1", (title,)).fetchone()
        finally:
            con.close()
        return row[0] if row else None
    except sqlite3.Error:
        return None


def _ledger() -> Path:
    return get_hermes_home() / "session_spawn" / "ledger.jsonl"


def recent_spawns(parent: str, now: float | None = None) -> int:
    cutoff = (now or time.time()) - 3600
    n = 0
    try:
        for line in _ledger().read_text(encoding="utf-8-sig").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("ts", 0) >= cutoff and row.get("parent", "") == parent:
                n += 1
    except OSError:
        pass
    return n


def child_env(depth: int) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("HERMES_SESSION_") and k not in _SCRUBBED_ENV}
    env[DEPTH_ENV] = str(depth + 1)
    return env


def build_command(query_file: Path, title: str | None, model: str | None, cwd: str | None,
                  skills: list[str] | None) -> list[str]:
    exe = shutil.which("hermes")
    cmd = [exe] if exe else [sys.executable, "-m", "hermes_cli.main"]
    cmd += ["chat", "--query-file", str(query_file), "--format", "stream-json", "--source", "cli"]
    if title:
        cmd += ["-c", title, "--create-if-missing"]
    if model:
        cmd += ["-m", model]
    if cwd:
        cmd += ["--in", cwd]
    for skill in skills or []:
        cmd += ["-s", str(skill)]
    return cmd


def _read_session_id(log: Path, proc: subprocess.Popen, timeout: float) -> str | None:
    deadline = time.time() + timeout
    while True:
        try:
            if m := _SESSION_RE.search(log.read_text(encoding="utf-8-sig", errors="replace")):
                return m.group(1)
        except OSError:
            pass
        if proc.poll() is not None or time.time() >= deadline:
            return None
        time.sleep(0.4)


def start_session(args: dict, **_kw: Any) -> str:
    prompt = str(args.get("prompt") or "").strip()
    if not prompt:
        return tool_error("prompt is required")
    cfg = _settings()
    depth = int(os.environ.get(DEPTH_ENV, "0") or 0)
    if depth >= int(cfg["max_depth"]):
        return tool_error(f"a spawned session may not start another session (depth {depth}, "
                          f"max_depth {cfg['max_depth']}); finish here or ask the user")
    if get_session_env("HERMES_CRON_SESSION", "").strip().lower() in ("1", "true") and not cfg["allow_cron"]:
        return tool_error("unattended (cron) runs may not start sessions; nobody is there to answer them")
    parent = get_session_env("HERMES_SESSION_ID", "")
    if recent_spawns(parent) >= int(cfg["max_per_hour"]):
        return tool_error(f"spawn cap reached ({cfg['max_per_hour']} per hour per session); reuse an open session")

    head_model, head_title = parse_header(prompt)
    title = str(args.get("title") or "").strip() or head_title
    if title and (existing := title_exists(title)):
        return json.dumps({"error": f"a session titled {title!r} already exists ({existing}); "
                                    "use another title or omit it", "existing_session_id": existing})
    model, note = resolve_model(str(args.get("model") or "").strip() or head_model, _config())
    cwd = str(args.get("cwd") or "").strip() or os.environ.get("TERMINAL_CWD") or None
    if cwd:
        cwd = os.path.expanduser(cwd)
        if not os.path.isdir(cwd):
            return tool_error(f"cwd is not a directory: {cwd}")
    skills = args.get("skills") if isinstance(args.get("skills"), list) else None

    work = get_hermes_home() / "session_spawn"
    for sub in ("prompts", "logs"):
        (work / sub).mkdir(parents=True, exist_ok=True)
    tag = f"{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    qfile, log = work / "prompts" / f"{tag}.md", work / "logs" / f"{tag}.log"
    qfile.write_text(prompt, encoding="utf-8")
    with open(log, "wb") as out:
        proc = subprocess.Popen(build_command(qfile, title, model, cwd, skills), stdin=subprocess.DEVNULL,
                                stdout=out, stderr=subprocess.STDOUT, env=child_env(depth), cwd=cwd,
                                start_new_session=True)  # detached-spawn: ok — the new session must outlive this turn and process; it waits for the user
    sid = _read_session_id(log, proc, _READY_TIMEOUT)
    with open(_ledger(), "a", encoding="utf-8") as fh:  # windows-footgun: ok -- append write
        fh.write(json.dumps({"ts": time.time(), "parent": parent, "child": sid, "title": title, "pid": proc.pid}) + "\n")
    result: dict[str, Any] = {
        "started": True, "session_id": sid, "title": title, "pid": proc.pid, "log": str(log),
        "note": "The new session runs its first turn now; if it needs the user it asks in its reply and "
                "waits in the session list. Its result is not returned here."}
    if note:
        result["model_note"] = note
    if sid is None:
        result["session_id_pending"] = True
    return json.dumps(result, ensure_ascii=False)


registry.register(
    name="start_session",
    toolset="delegation",
    schema={
        "name": "start_session",
        "description": (
            "Open a NEW separate chat session and start its first turn with `prompt`. It has its own "
            "transcript and title, appears in the session list, and if it needs the user it asks in its "
            "reply and waits there. Use for a continuation prompt (fresh context) or sub-work needing its own "
            "line of questioning; not for quick parallel work (delegate_task). The prompt must be "
            "self-contained: the new session sees nothing of this one. A leading `Model: X` / `Title: Y` "
            "line in prompt is honoured."),
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Full, self-contained first message for the new session."},
                "title": {"type": "string", "description": "Session title. Must not already exist. Omit to auto-title."},
                "model": {"type": "string", "description": "Model alias or provider/model. Omit for the default."},
                "cwd": {"type": "string", "description": "Working directory. Defaults to this session's."},
                "skills": {"type": "array", "items": {"type": "string"}, "description": "Skills to preload."},
            },
            "required": ["prompt"],
        },
    },
    handler=start_session,
    check_fn=is_enabled,
    emoji="🪄",
)
