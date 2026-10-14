"""auto-continuation plugin — write the "continue in a fresh chat" prompt before a session gets unwieldy.

Three hooks, no tools, no core changes:

* ``pre_llm_call`` remembers the conversation so far (the reply hook only receives the session id);
* ``post_api_request`` remembers the last prompt size and the model's context window;
* ``transform_llm_output`` checks the limits (context %, session cost, message count). The first time one
  trips for a session it asks the model (``ctx.llm``) to write a self-contained continuation prompt,
  saves it under ``<HERMES_HOME>/continuations/<session_id>.md`` and appends it to that reply in a code
  block. One prompt per session; a failed attempt is retried at most twice.
"""

from __future__ import annotations

import logging
import os
import shlex
import subprocess
import threading
from contextlib import closing
from pathlib import Path
from typing import Any, Optional

from hermes_constants import get_hermes_home

from . import continuation as cont

logger = logging.getLogger(__name__)

_MAX_TRACKED = 200
_MAX_ATTEMPTS = 2
_lock = threading.Lock()
_history: dict[str, tuple[list, str]] = {}      # session_id -> (messages before the reply, user message)
_usage: dict[str, tuple[int, int]] = {}         # session_id -> (last prompt tokens, context window)
_attempts: dict[str, int] = {}                  # session_id -> failed generations (done = _MAX_ATTEMPTS)


def _remember(store: dict, key: str, value: Any) -> None:
    with _lock:
        store.pop(key, None)
        store[key] = value
        while len(store) > _MAX_TRACKED:
            store.pop(next(iter(store)))


def _session_row(session_id: str) -> dict:
    try:
        from hermes_state import SessionDB
        with closing(SessionDB(read_only=True)) as db:
            return db.get_session(session_id) or {}
    except Exception:
        logger.debug("auto-continuation: session row unreadable", exc_info=True)
        return {}


def _extra_header(command: str, session_id: str) -> str:
    if not command.strip():
        return ""
    try:
        done = subprocess.run(
            shlex.split(command), capture_output=True, text=True, timeout=10, check=False,
            env={**os.environ, "HERMES_SESSION_ID": session_id},
        )
        return done.stdout.strip() if done.returncode == 0 else ""
    except Exception:
        logger.debug("auto-continuation: header_command failed", exc_info=True)
        return ""


def _out_dir(s: cont.Settings) -> Path:
    return Path(s.output_dir).expanduser() if s.output_dir.strip() else get_hermes_home() / "continuations"


def _already_done(ctx, session_id: str) -> bool:
    if _attempts.get(session_id, 0) >= _MAX_ATTEMPTS:
        return True
    try:
        return bool(ctx.state.get(f"done:{session_id}"))
    except (OSError, RuntimeError, ValueError):
        return False


def register(ctx) -> None:
    def settings() -> cont.Settings:
        return cont.Settings.load(ctx.get_config)

    def on_pre_llm_call(session_id: str = "", user_message: Any = "", conversation_history: Any = None,
                        **_: Any) -> None:
        if session_id and isinstance(conversation_history, list):
            _remember(_history, session_id, (conversation_history, cont.text_of(user_message)))

    def on_post_api_request(session_id: str = "", usage: Any = None, context_length: Any = None,
                            **_: Any) -> None:
        try:
            tokens = int((usage or {}).get("prompt_tokens") or 0)
            window = int(context_length or 0)
        except (TypeError, ValueError, AttributeError):
            return
        if session_id and tokens:
            _remember(_usage, session_id, (tokens, window))

    def on_transform_llm_output(response_text: Any = None, session_id: str = "", model: str = "",
                                platform: str = "", **_: Any) -> Optional[str]:
        try:
            if not isinstance(response_text, str) or not response_text.strip() or not session_id:
                return None
            if platform == "cron" or _already_done(ctx, session_id):
                return None
            s = settings()
            history, user_message = _history.get(session_id, ([], ""))
            tokens, window = _usage.get(session_id, (0, 0))
            cost = None
            if s.cost_usd > 0:
                row = _session_row(session_id)
                cost = row.get("actual_cost_usd") or row.get("estimated_cost_usd")
            reason = cont.trigger_reason(
                s, prompt_tokens=tokens, context_length=window,
                cost_usd=float(cost) if cost else None, message_count=len(history) + 2,
            )
            if not reason:
                return None
            _remember(_attempts, session_id, _attempts.get(session_id, 0) + 1)
            digest = cont.transcript_digest(history, user_message, response_text)
            result = ctx.llm.complete(
                messages=cont.writer_messages(digest, cont.known_paths(history), s.extra_instructions),
                max_tokens=2000, temperature=0.2, purpose="auto-continuation.prompt",
            )
            body = (result.text or "").strip()
            if not body:
                return None
            title = (_session_row(session_id).get("title") or "") if "{title}" in s.header else ""
            header = s.header.format_map(
                {"session_id": session_id, "model": model or "", "title": title}
            ) if s.header else ""
            prompt = cont.assemble(header, _extra_header(s.header_command, session_id), body)
            saved = ""
            try:
                out = _out_dir(s)
                out.mkdir(parents=True, exist_ok=True)
                (out / f"{session_id}.md").write_text(prompt + "\n", encoding="utf-8")
                saved = str(out / f"{session_id}.md")
            except OSError:
                logger.debug("auto-continuation: could not save prompt", exc_info=True)
            try:
                ctx.state.set(f"done:{session_id}", True)
            except (OSError, RuntimeError, ValueError):
                _remember(_attempts, session_id, _MAX_ATTEMPTS)
            return response_text + cont.reply_suffix(reason, prompt, saved)
        except Exception:
            logger.warning("auto-continuation: skipped", exc_info=True)
            return None

    ctx.register_hook("pre_llm_call", on_pre_llm_call)
    ctx.register_hook("post_api_request", on_post_api_request)
    ctx.register_hook("transform_llm_output", on_transform_llm_output)
