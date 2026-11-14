"""1Password Credit Card items as browser_vault payment handles (opt-in ``vault.onepassword.include_cards``).

Contracts:
1. Off by default: the listing asks `op` for Login items only and no handle is a card.
2. On: Credit Card items list as ``payment`` handles whose metadata carries the title and last four
   digits, never the number; the payload maps ``ccnum``/``cvv``/``expiry`` (YYYYMM)/``cardholder``
   onto the local vault's payment field names.
3. A handle the listing did not return as a card (unknown, or a Login) is refused before any secret read,
   and a card handle never yields a password.
4. Through the tool the existing origin guard and payment confirmation still run before the card is read.
"""

from __future__ import annotations

import json
import stat
from unittest.mock import patch

import pytest

from agent.vault_backends.onepassword import OnePasswordLoginBackend

pytestmark = pytest.mark.platforms("posix")  # fake op is a shebang script; the backend under test is host-agnostic

TEST_PAN = "4242424242424242"  # the public test card number; no other number appears in this file

# A stand-in `op`: `item list` honours --categories the way the real CLI does, `item get` keeps the real
# service-account rule (a vault query is required) and returns the item JSON shape of `--format json`.
_FAKE_OP = r'''#!/usr/bin/env python3
import json, os, sys
argv = sys.argv[1:]
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "op.log"), "a") as log:
    log.write(json.dumps(argv) + "\n")
PAN = "4242424242424242"
LOGIN = {"id": "login-1", "title": "Shop login", "category": "LOGIN", "vault": {"id": "vault-l"},
         "additional_information": "jane@example.com", "urls": [{"href": "https://shop.example.com/login"}]}
CARDS = [
    {"id": "card-1", "title": "Visa", "category": "CREDIT_CARD", "vault": {"id": "vault-c"},
     "additional_information": "*4242", "urls": [{"href": "https://shop.example.com/checkout"}]},
    {"id": "card-2", "title": "Spare card", "category": "CREDIT_CARD", "vault": {"id": "vault-c"},
     "additional_information": "*4242"},
    {"id": "card-3", "title": "No CVC", "category": "CREDIT_CARD", "vault": {"id": "vault-c"},
     "urls": [{"href": "https://shop.example.com"}]},
]
if argv[:2] == ["item", "list"]:
    cats = argv[argv.index("--categories") + 1].split(",")
    print(json.dumps(([LOGIN] if "Login" in cats else []) + (CARDS if "Credit Card" in cats else []))); sys.exit(0)
if argv[:2] == ["item", "get"]:
    if os.environ.get("OP_SERVICE_ACCOUNT_TOKEN") and "--vault" not in argv:
        sys.stderr.write("a vault query must be provided when this command is called by a service account\n"); sys.exit(1)
    if argv[2] == "login-1":
        if "--format" in argv:
            print(json.dumps({"id": "login-1", "category": "LOGIN", "fields": [{"id": "password", "value": "pw"}]}))
        else:
            print("dummy-password")
        sys.exit(0)
    fields = [{"id": "cardholder", "value": "Test Holder"}, {"id": "ccnum", "value": "4242 4242 4242 4242"},
              {"id": "expiry", "value": "203012"}]
    if argv[2] != "card-3":
        fields.append({"id": "cvv", "value": "123"})
    print(json.dumps({"id": argv[2], "category": "CREDIT_CARD", "fields": fields})); sys.exit(0)
sys.exit(2)
'''


@pytest.fixture
def fake_op(tmp_path):
    exe = tmp_path / "op"
    exe.write_text(_FAKE_OP, encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    token = lambda name, default="": "dummy-sa-token" if name == "OP_SERVICE_ACCOUNT_TOKEN" else default
    log = tmp_path / "op.log"

    def calls():
        return [json.loads(a) for a in log.read_text(encoding="utf-8-sig").splitlines()] if log.exists() else []

    with patch("agent.secret_scope.get_secret", side_effect=token):
        # a fresh backend per call, exactly as backend_for_handle builds one: nothing listed yet
        yield (lambda **cfg: OnePasswordLoginBackend({"binary_path": str(exe), **cfg})), calls


def _secret_reads(calls):
    return [c for c in calls() if c[:2] == ["item", "get"]]


def test_cards_are_not_listed_unless_the_setting_is_on(fake_op):
    new_backend, calls = fake_op
    metas = new_backend().list_items()
    assert [(m.id, m.kind) for m in metas] == [("op:login-1", "login")]
    assert calls() == [["item", "list", "--categories", "Login", "--format", "json"]]  # stock argv unchanged
    with pytest.raises(RuntimeError, match="card not found"):
        new_backend().resolve_secret("op:card-1")
    assert _secret_reads(calls) == []


def test_setting_on_lists_credit_cards_as_payment_handles_without_the_number(fake_op):
    new_backend, calls = fake_op
    metas = {m.id: m for m in new_backend(include_cards=True).list_items()}
    assert calls()[0][:4] == ["item", "list", "--categories", "Login,Credit Card"]
    assert metas["op:login-1"].kind == "login"
    card = metas["op:card-1"]
    assert card.kind == "payment" and card.label == "Visa ending 4242"
    assert card.origin == "https://shop.example.com" and list(card.allowed_origins) == ["https://shop.example.com"]
    assert card.identifier is None
    # a card with no saved website is listed, but with no origin: the tool marks it unavailable
    assert metas["op:card-2"].origin is None and metas["op:card-2"].allowed_origins == ()
    assert TEST_PAN not in json.dumps([m.to_dict() for m in metas.values()])
    assert _secret_reads(calls) == []  # listing reads no secret


def test_resolve_secret_maps_card_fields_and_names_the_vault(fake_op):
    new_backend, calls = fake_op
    secret = new_backend(include_cards=True).resolve_secret("op:card-1")
    assert secret == {"card_number": TEST_PAN, "cvc": "123", "cardholder_name": "Test Holder",
                      "exp_month": "12", "exp_year": "2030"}
    assert _secret_reads(calls) == [["item", "get", "card-1", "--vault", "vault-c", "--format", "json", "--reveal"]]


def test_card_expiry_as_typed_and_missing_fields_never_echo_values(fake_op):
    from agent.vault_backends.onepassword import _card_payload

    item = {"category": "CREDIT_CARD", "fields": [{"id": "ccnum", "value": TEST_PAN}, {"id": "cvv", "value": "123"},
                                                  {"id": "expiry", "value": "7/29"}]}
    assert _card_payload(item) == {"card_number": TEST_PAN, "cvc": "123", "exp_month": "07", "exp_year": "2029"}
    new_backend, _calls = fake_op
    with pytest.raises(RuntimeError) as exc:
        new_backend(include_cards=True).resolve_secret("op:card-3")
    assert "verification number" in str(exc.value) and TEST_PAN not in str(exc.value) and "4242" not in str(exc.value)


def test_unknown_or_login_handles_are_refused_before_any_secret_read(fake_op):
    new_backend, calls = fake_op
    for handle in ("op:ghost", "op:--help", "op:login-1"):
        with pytest.raises(RuntimeError, match="card not found"):
            new_backend(include_cards=True).resolve_secret(handle)
    with pytest.raises(RuntimeError, match="login not found"):  # and a card never yields a password
        new_backend(include_cards=True).resolve_password("op:card-1")
    assert new_backend(include_cards=True).resolve_otp("op:card-1") is None
    assert _secret_reads(calls) == []


def _page(origin):
    controls = [{"autocomplete": "cc-number", "index": 0, "type": "text"},
                {"autocomplete": "cc-exp", "index": 1, "type": "text"},
                {"autocomplete": "cc-csc", "index": 2, "type": "text"}]

    def fake_eval(task_id, expression):
        if "location.href" in expression:
            return {"success": True, "result": f"{origin}/checkout"}
        return {"success": True, "result": json.dumps(controls)}
    return fake_eval


def test_tool_fill_keeps_origin_guard_and_confirmation_before_the_card_is_read(fake_op):
    from agent import redact
    from tools import browser_vault_tool

    new_backend, calls = fake_op
    backend = new_backend(include_cards=True)
    writes, asked = [], []

    def consent(*args, **kwargs):
        asked.append(kwargs.get("surface"))
        return answer

    def fill(origin):
        with patch("agent.vault_backends.base.enabled_backends", return_value=[backend]), \
             patch.object(browser_vault_tool, "_focus_bound_origin", return_value=None), \
             patch.object(browser_vault_tool, "_eval_js", side_effect=_page(origin)), \
             patch.object(browser_vault_tool, "_eval_js_secret",
                          side_effect=lambda t, e: writes.append(e) or {"success": True, "result": '{"filled": 3}'}), \
             patch("tools.approval_prompt.request_elicitation_consent", side_effect=consent):
            return browser_vault_tool.browser_vault_fill("op:card-1")

    try:
        answer = "accept"
        wrong = json.loads(fill("https://evil.example.net"))
        assert wrong["error_type"] == "origin_mismatch" and asked == [] and _secret_reads(calls) == []

        answer = "decline"
        assert json.loads(fill("https://shop.example.com"))["error_type"] == "payment_declined"
        assert asked == ["vault-payment"] and _secret_reads(calls) == [] and writes == []

        answer = "accept"
        raw = fill("https://shop.example.com")
        out = json.loads(raw)
        assert out["success"] is True and out["backend"] == "onepassword" and out["kind"] == "payment"
        assert out["fields"] == ["cc-csc", "cc-exp", "cc-number"]
        assert TEST_PAN not in raw and "123" not in raw
        assert len(writes) == 1 and TEST_PAN in writes[0] and "12/30" in writes[0]
        assert TEST_PAN not in redact.redact_sensitive_text(f"page echoes {TEST_PAN}")
    finally:
        redact.clear_vault_redaction_values()


def test_tool_list_reports_an_unbound_card_as_unavailable(fake_op):
    from tools import browser_vault_tool

    new_backend, _calls = fake_op
    backend = new_backend(include_cards=True)
    with patch("agent.vault_backends.enabled_backends", return_value=[backend]):
        raw = browser_vault_tool.browser_vault_list()
    items = {i["handle"]: i for i in json.loads(raw)["items"]}
    assert items["op:card-1"]["kind"] == "payment" and items["op:card-1"]["available"] is True
    assert items["op:card-2"]["available"] is False
    assert TEST_PAN not in raw
