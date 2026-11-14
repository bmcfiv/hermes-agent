"""1Password Login items (and, opt-in, Credit Card items) as a vault backend (``op`` CLI).

Unlock: ``op signin --raw`` with the master password on stdin (desktop-app
integration or account-level auth) mints an ``OP_SESSION_<account>`` token.
A configured service-account token skips the prompt entirely (headless).
List: ``op item list --categories Login --format json`` → title, urls,
username, vault. Resolve: ``op item get <id> --vault <vault-id> ...`` (service
accounts reject an item read without a vault query).

With ``vault.onepassword.include_cards: true`` the same listing also asks for
Credit Card items; each becomes a ``payment`` handle bound to the websites saved
on the card, and its fields are read only at fill time (after the tool's origin
guard and the user's payment confirmation).
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

from agent.secret_sources.base import run_cli
from agent.secret_sources.onepassword import _OP_ENV_ALLOWLIST, _scrub, find_op
from agent.vault_backends.base import LoginBackend, UnlockRequired, run_with_stdin_secret
from agent.vault_backends import unlock as _unlock
from agent.vault_store import VaultItemMeta, normalize_origin

logger = logging.getLogger(__name__)

_TIMEOUT = 30.0
_CARD_CATEGORY = "CREDIT_CARD"  # `op item list/get` JSON category of a Credit Card item
# Credit Card field ids (`op item get --format json`) → the payment payload agent/vault_store.py defines.
_CARD_FIELDS = {"ccnum": "card_number", "cvv": "cvc", "cardholder": "cardholder_name"}


class OnePasswordLoginBackend(LoginBackend):
    name = "onepassword"
    display_name = "1Password"
    prefix = "op:"
    needs_unlock = True

    def __init__(self, cfg: Optional[dict] = None):
        self.cfg = cfg or {}
        from agent.secret_scope import get_secret
        env_name = str(self.cfg.get("service_account_token_env") or "OP_SERVICE_ACCOUNT_TOKEN")
        self._service_token = get_secret(env_name, "") or ""
        self._item_vaults: dict[str, str] = {}  # item id -> vault id from the latest ``item list``
        self._item_kinds: dict[str, str] = {}   # item id -> "login" | "payment" from the same listing
        self._cards = self.cfg.get("include_cards") is True  # opt-in: stock lists Login items only

    # ── auth ────────────────────────────────────────────────────────────────

    def _op(self) -> Path:
        op = find_op(str(self.cfg.get("binary_path") or ""))
        if op is None:
            raise RuntimeError("1Password CLI (op) not found — install it or set vault.onepassword.binary_path")
        return op

    def _env(self, session_token: Optional[str]) -> dict[str, str]:
        from agent.secret_scope import get_secret
        env = {k: os.environ[k] for k in _OP_ENV_ALLOWLIST if k in os.environ and not k.startswith("OP_CONNECT_")}
        # Connect credentials outrank OP_SERVICE_ACCOUNT_TOKEN inside op, so they must come from the
        # profile's own secret scope like the service token does — never from the launch environment.
        for k in ("OP_CONNECT_HOST", "OP_CONNECT_TOKEN"):
            if v := get_secret(k, ""):
                env[k] = v
        env["NO_COLOR"] = "1"
        account = str(self.cfg.get("account") or "")
        if account:
            env["OP_ACCOUNT"] = account
        if self._service_token:
            env["OP_SERVICE_ACCOUNT_TOKEN"] = self._service_token
        elif session_token:
            # op signin --raw prints the bare token; the env var name carries the account shorthand,
            # which op also accepts as plain OP_SESSION for the default account.
            env[f"OP_SESSION_{account}" if account else "OP_SESSION"] = session_token
        return env

    def is_unlocked(self) -> bool:
        return bool(self._service_token) or _unlock.is_unlocked(self.name)

    def unlock(self, master_password: str) -> None:
        """Mint a session token from the master password (consumed on stdin, never argv)."""
        generation = _unlock.begin_unlock(self.name)
        cmd = [str(self._op()), "signin", "--raw"]
        if account := str(self.cfg.get("account") or ""):
            cmd += ["--account", account]
        proc = run_with_stdin_secret(cmd, env=self._env(None), secret=master_password, timeout=_TIMEOUT, label="op")
        token = (proc.stdout or "").strip()
        if proc.returncode != 0 or not token:
            raise RuntimeError(f"1Password unlock failed: {_scrub(proc.stderr or '')[:200] or 'no session token'}")
        if not _unlock.store_session_token(self.name, token, generation):
            raise RuntimeError("1Password was locked while unlocking; try again")

    def _run(self, *args: str) -> str:
        token = None if self._service_token else _unlock.get_session_token(self.name)
        if not self._service_token and not token:
            raise UnlockRequired(self)
        proc = run_cli([str(self._op()), *args], env=self._env(token), timeout=_TIMEOUT, label="op",
                       timeout_message="op timed out", stdin=subprocess.DEVNULL)
        if proc.returncode != 0:
            err = _scrub(proc.stderr or "")
            # A service account never holds a session, so an auth failure there is not a lock to undo:
            # surface op's own error instead of an unlock prompt that reports "already unlocked".
            if not self._service_token and any(w in err.lower() for w in ("session", "sign in", "not signed in")):
                _unlock.lock(self.name)
                raise UnlockRequired(self)
            raise RuntimeError(f"op failed: {err[:200]}")
        return proc.stdout or ""

    # ── backend contract ───────────────────────────────────────────────────
    def list_items(self) -> list[VaultItemMeta]:
        if not self.is_unlocked():
            return []
        categories = "Login,Credit Card" if self._cards else "Login"
        raw = json.loads(self._run("item", "list", "--categories", categories, "--format", "json") or "[]")
        out: list[VaultItemMeta] = []
        self._item_vaults, self._item_kinds = {}, {}
        for item in raw if isinstance(raw, list) else []:
            vault = item.get("vault") or {}
            is_card = self._cards and item.get("category") == _CARD_CATEGORY
            if item.get("id"):
                self._item_vaults[str(item["id"])] = str(vault.get("id") or "") if isinstance(vault, dict) else ""
                self._item_kinds[str(item["id"])] = "payment" if is_card else "login"
            urls = [str(u["href"]) for u in item.get("urls") or [] if isinstance(u, dict) and u.get("href")]
            origins = _all_origins(urls)
            if is_card:
                out.append(_card_meta(self.prefix, item, origins))
                continue
            if not origins:
                continue
            username = str(item.get("additional_information") or "").strip() or None
            out.append(VaultItemMeta(
                id=f"{self.prefix}{item.get('id')}", kind="login", label=str(item.get("title") or origins[0]),
                origin=origins[0], created_at=str(item.get("created_at") or ""),
                identifier_type="username" if username else None, identifier=username,
                allowed_origins=_web_origins(origins)))
        return out

    def get_meta(self, handle: str) -> Optional[VaultItemMeta]:
        return next((m for m in self.list_items() if m.id == handle), None)

    def _item_selector(self, handle: str, kind: str = "login") -> list[str]:
        """``op item get`` target for a listed item of ``kind``. A service account must name the item's
        vault (``--vault``) and ``item list`` reports it, so the vault comes from the same listing that
        proved the handle is that kind of item; a handle nothing listed as ``kind`` is refused before
        any secret read (a card handle never yields a password, a login handle never yields card fields)."""
        item_id = handle[len(self.prefix):]
        if self._item_kinds.get(item_id) != kind:
            self.list_items()
        if self._item_kinds.get(item_id) != kind:
            what = "card" if kind == "payment" else "login"
            raise RuntimeError(f"1Password {what} not found; list {what}s again")
        vault_id = self._item_vaults[item_id]
        return [item_id, "--vault", vault_id] if vault_id else [item_id]

    def resolve_password(self, handle: str) -> str:
        return self._run("item", "get", *self._item_selector(handle),
                         "--fields", "label=password", "--reveal").rstrip("\r\n")

    def resolve_otp(self, handle: str) -> Optional[str]:
        # `--otp` mints the current TOTP from the item's one-time-password field; items without one error out.
        try:
            code = self._run("item", "get", *self._item_selector(handle), "--otp").strip()
        except Exception:
            return None
        return code if code.isdigit() else None

    def resolve_secret(self, handle: str) -> dict[str, str]:
        """Card payload for a listed Credit Card handle, in the field names ``PAYMENT_FIELDS`` uses.
        Raises without any value in the message; anything that is not a listed card is refused."""
        selector = self._item_selector(handle, kind="payment")
        try:
            item = json.loads(self._run("item", "get", *selector, "--format", "json", "--reveal"))
        except ValueError:
            raise RuntimeError("1Password returned an unreadable card item") from None
        if not isinstance(item, dict) or item.get("category") != _CARD_CATEGORY:
            raise RuntimeError("1Password item is not a Credit Card")
        return _card_payload(item)


def _card_meta(prefix: str, item: dict, origins: list[str]) -> VaultItemMeta:
    """Metadata for a Credit Card item: title plus the last four digits 1Password shows in its list
    subtitle, never more. A card with no saved website lists with no origin, so the tool reports it
    unavailable and refuses to fill it (the origin guard is the same as for every payment item)."""
    title = str(item.get("title") or "Credit card")
    last4 = re.fullmatch(r"\D*(\d{4})\D*", str(item.get("additional_information") or ""))
    return VaultItemMeta(
        id=f"{prefix}{item.get('id')}", kind="payment", label=f"{title} ending {last4.group(1)}" if last4 else title,
        origin=origins[0] if origins else None, created_at=str(item.get("created_at") or ""),
        allowed_origins=_web_origins(origins) if origins else ())


def _card_payload(item: dict) -> dict[str, str]:
    """Map a Credit Card item's fields (``ccnum``, ``cvv``, ``expiry`` as ``YYYYMM``, ``cardholder``)
    onto the payment payload. Missing required fields are named, never their values."""
    fields = {str(f.get("id") or ""): str(f.get("value") or "").strip()
              for f in item.get("fields") or [] if isinstance(f, dict)}
    out = {name: fields[fid] for fid, name in _CARD_FIELDS.items() if fields.get(fid)}
    if "card_number" in out:
        out["card_number"] = re.sub(r"[\s-]", "", out["card_number"])
    expiry = fields.get("expiry", "")
    if m := re.fullmatch(r"(\d{4})(\d{2})", expiry):  # op's MONTH_YEAR value
        out["exp_year"], out["exp_month"] = m.group(1), m.group(2)
    elif m := re.fullmatch(r"(\d{1,2})\s*/\s*(\d{2}|\d{4})", expiry):  # as typed: MM/YY or MM/YYYY
        out["exp_month"], out["exp_year"] = m.group(1).zfill(2), m.group(2) if len(m.group(2)) == 4 else "20" + m.group(2)
    missing = [label for key, label in (("card_number", "number"), ("exp_month", "expiry date"),
                                        ("cvc", "verification number")) if key not in out]
    if missing:
        raise RuntimeError(f"1Password card is missing its {', '.join(missing)}")
    return out


def _web_origins(origins: list[str]) -> tuple:
    """Fill targets are browser pages, so app URIs (``androidapp://`` etc.) never
    widen the fill set; an item whose only URI is an app URI keeps its single
    (unfillable-from-a-page) origin exactly as before."""
    web = tuple(o for o in origins if o.startswith(("http://", "https://")))
    return web or (origins[0],)


def _all_origins(urls: list[str]) -> list[str]:
    """Every normalized origin saved on the item, deduped, order preserved.

    A 1Password Login item can carry several websites; each of them is a place the
    user told 1Password the credential belongs, so all of them are valid fill targets.
    """
    out: list[str] = []
    for u in urls:
        try:
            origin = normalize_origin(u)
        except Exception:
            continue
        if origin not in out:
            out.append(origin)
    return out
