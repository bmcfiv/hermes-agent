"""Pure logic for the auto-continuation plugin: trigger checks, transcript digest, prompt assembly."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

DIGEST_CHARS = 60_000          # transcript budget handed to the writer model
PER_MESSAGE_CHARS = 1_500      # one message is cut to this before it enters the digest
HEAD_MESSAGES = 2              # the opening request is always kept
_PATH_RE = re.compile(r"(?:~|/)[\w.\-/@+ ]*\w\.\w{1,8}\b")

SYSTEM_PROMPT = """You write a continuation prompt: one self-contained message a person pastes into a \
brand-new chat so the work resumes with no memory of this one.

Rules:
- The new chat has NOTHING: name every file (absolute path), account, ID, URL and command that matters. \
Never say "as discussed" or "the file from earlier".
- Use exactly these sections, in this order: Goal, Done so far, Running now (omit if nothing is running), \
Next, Done means.
- Done so far: facts and results actually established in the transcript, each with its file or ID. \
Never invent a number, path or outcome that is not in the transcript.
- Running now: background processes or scheduled jobs started in this session (id, what it does, its log, \
how to tell it finished).
- Next: numbered, concrete steps the new chat takes first.
- Done means: the observable end state that finishes the goal.
- Plain text, no preamble, no closing remarks, under 600 words."""


@dataclass
class Settings:
    context_percent: float = 40.0
    cost_usd: float = 5.0
    messages: int = 200
    header: str = "Continues: {session_id}"
    header_command: str = ""
    extra_instructions: str = ""
    output_dir: str = ""

    @classmethod
    def load(cls, get) -> Settings:
        """Build from a ``ctx.get_config``-style getter; bad values fall back to the default."""
        base = cls()
        out = {}
        for name, default in base.__dict__.items():
            raw = get(name, default)
            try:
                out[name] = type(default)(raw) if raw is not None else default
            except (TypeError, ValueError):
                out[name] = default
        return cls(**out)


def trigger_reason(
    s: Settings, *, prompt_tokens: int = 0, context_length: int = 0, cost_usd: Optional[float] = None,
    message_count: int = 0,
) -> Optional[str]:
    """First tripped limit as a short human sentence, else ``None``."""
    if s.context_percent > 0 and context_length > 0 and prompt_tokens > 0:
        pct = 100.0 * prompt_tokens / context_length
        if pct >= s.context_percent:
            return f"context is {pct:.0f}% full ({prompt_tokens:,} of {context_length:,} tokens)"
    if s.cost_usd > 0 and cost_usd is not None and cost_usd >= s.cost_usd:
        return f"this session has cost about ${cost_usd:.2f}"
    if s.messages > 0 and message_count >= s.messages:
        return f"this session has {message_count} messages"
    return None


def text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            p.get("text", "") for p in content if isinstance(p, dict) and isinstance(p.get("text"), str)
        )
    return ""


def _clip(text: str, limit: int = PER_MESSAGE_CHARS) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    keep = limit // 2
    return f"{text[:keep]} …[{len(text) - limit} chars cut]… {text[-keep:]}"


def _tool_line(call: Any) -> str:
    fn = call.get("function", {}) if isinstance(call, dict) else {}
    return f"{fn.get('name', 'tool')}({_clip(str(fn.get('arguments', '')), 300)})"


def transcript_digest(history: list[dict], user_message: str = "", response: str = "",
                      budget: int = DIGEST_CHARS) -> str:
    """Compact transcript: the opening request, then as many of the newest messages as fit the budget.

    Tool results are reduced to a 300-char stub; assistant tool calls to ``name(args…)``.
    """
    rows: list[str] = []
    for msg in history or []:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role == "system":
            continue
        if role == "tool":
            rows.append(f"[tool result] {_clip(text_of(msg.get('content')), 300)}")
            continue
        body = _clip(text_of(msg.get("content")))
        calls = [_tool_line(c) for c in msg.get("tool_calls") or []]
        if calls:
            body = (body + "\n" if body else "") + "[tool calls] " + "; ".join(calls)
        if body:
            rows.append(f"[{role}] {body}")
    if user_message and (not rows or not rows[-1].endswith(_clip(user_message))):
        rows.append(f"[user] {_clip(user_message)}")
    if response:
        rows.append(f"[assistant] {_clip(response)}")
    head, tail = rows[:HEAD_MESSAGES], rows[HEAD_MESSAGES:]
    used = sum(len(r) + 1 for r in head)
    kept: list[str] = []
    for row in reversed(tail):
        if used + len(row) + 1 > budget:
            break
        kept.append(row)
        used += len(row) + 1
    kept.reverse()
    cut = len(tail) - len(kept)
    gap = [f"[… {cut} earlier messages omitted …]"] if cut else []
    return "\n".join(head + gap + kept)


def known_paths(history: list[dict], limit: int = 40) -> list[str]:
    """Absolute/~ file paths mentioned in tool calls, newest first, so the writer never has to guess one."""
    seen: dict[str, None] = {}
    for msg in reversed(history or []):
        if not isinstance(msg, dict):
            continue
        for call in msg.get("tool_calls") or []:
            args = str((call.get("function") or {}).get("arguments", "")) if isinstance(call, dict) else ""
            for m in _PATH_RE.findall(args):
                seen.setdefault(m.strip(), None)
        if len(seen) >= limit:
            break
    return list(seen)[:limit]


def writer_messages(digest: str, paths: list[str], extra: str = "") -> list[dict]:
    system = SYSTEM_PROMPT + (f"\n\nAdditional guidance:\n{extra.strip()}" if extra.strip() else "")
    user = "Transcript of the session so far:\n\n" + digest
    if paths:
        user += "\n\nFiles touched (from tool calls):\n" + "\n".join(f"- {p}" for p in paths)
    user += "\n\nWrite the continuation prompt now."
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def assemble(header: str, extra_header: str, body: str) -> str:
    top = "\n".join(p.strip() for p in (header, extra_header) if p and p.strip())
    return (top + "\n\n" if top else "") + body.strip()


def reply_suffix(reason: str, prompt: str, saved_to: str = "") -> str:
    saved = f" (also saved to `{saved_to}`)" if saved_to else ""
    fence = "```"
    while fence in prompt:
        fence += "`"
    return (
        f"\n\n---\n**Long session:** {reason}. Paste the block below into a new chat to continue{saved}.\n\n"
        f"{fence}\n{prompt}\n{fence}"
    )
