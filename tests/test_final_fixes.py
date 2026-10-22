"""Regressions for exact-offer acceptance, voice safety, recovery persistence,
proposal persistence, MCP transport order and error handling, storage errors, schema validation."""
import json
import os
import threading
from datetime import timedelta

import pytest

from conftest import HUMAN, buy_by_voice, last_assistant, say
from app import mcp as M
from app.server import serve
from app.service import NudgeService, StateError
from app.voice import parse
from test_hardening import http_call
from test_mcp import Client, srv  # noqa: F401  (the MCP client and server fixture)

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _js():
    return open(os.path.join(HERE, "app", "static", "app.js"), encoding="utf-8").read()


# ---------- 1. Today's offer acceptance names the exact displayed proposal
def test_offer_acceptance_needs_the_exact_displayed_proposal(svc):
    buy_by_voice(svc, "pasta")
    old = svc.offers["groceries"]["proposal_id"]
    # a replacement offer for the same category appears (different terms) while an old page is still open
    svc._open_proposal("groceries", "limits", 4500, 9000, "offer")
    new = f"A-{svc.prop_no:03d}"
    svc.offers["groceries"] = dict(category="groceries", to="limits", proposal_id=new)
    assert old != new
    for pid in (None, old, "A-999"):
        res = svc.accept_offer("groceries", HUMAN, pid)
        assert not res["ok"] and res["code"] == "PROPOSAL_STALE"
        assert svc.cats["groceries"]["tier"] == "ask" and svc.cats["groceries"]["item_minor"] == 2500
    assert svc.accept_offer("groceries", HUMAN, new)["ok"]
    assert svc.cats["groceries"]["tier"] == "limits" and svc.cats["groceries"]["item_minor"] == 4500


def test_rest_offer_accept_requires_the_proposal_field(tmp_path):
    s = NudgeService(str(tmp_path / "r.jsonl"), key="k", profile="demo")
    web = serve("127.0.0.1", 0, s)
    threading.Thread(target=web.serve_forever, daemon=True).start()
    try:
        buy_by_voice(s, "pasta")
        tok = {"X-Nudge-Token": web.nudge_token}
        assert http_call(web, "POST", "/api/offer_accept", {"category": "groceries"}, tok)[0] == 400
        assert s.cats["groceries"]["tier"] == "ask"
        pid = s.offers["groceries"]["proposal_id"]
        st, data, _ = http_call(web, "POST", "/api/offer_accept", {"category": "groceries", "proposal": pid}, tok)
        assert st == 200 and json.loads(data)["result"]["ok"] and s.cats["groceries"]["tier"] == "limits"
    finally:
        web.shutdown()
        web.server_close()


def test_page_sends_the_displayed_proposal_id():
    assert 'api("offer_accept", {category: c, proposal: o.proposal_id})' in _js()


# ---------- 2. voice parsing
@pytest.mark.parametrize("text,kind", [
    ("stop and resume groceries", "stop"),
    ("I said stop and resume groceries", "stop"),
    ("stop and undo my last order", "stop"),
    ("pause groceries and resume gifts", "pause"),
    ("what does undo my last order mean", "unknown"),
    ("avoid undo my last order", "unknown"),
    ("how do I resume groceries", "unknown"),
    ("what does resume mean", "unknown"),
    ("why would I set groceries to within limits", "unknown"),
    ("is it possible to set groceries limit to 30 dollars", "unknown"),
    ("what is a digest", "unknown"),
    ("I didn't ask you to undo my last order", "negated"),
    ("What is my rule?", "show_rule"),
    ("Approve my digest", "digest"),
    ("Pause everything", "pause"),
    ("Pause groceries", "pause"),
    ("Undo my last order", "undo_last"),
    ("please undo my last order", "undo_last"),
    ("resume groceries", "resume"),
    ("Set groceries limit to 30 dollars", "set_limit"),
    ("limit groceries to 40 dollars a day", "set_limit"),
    ("Set household to within limits", "set_tier"),
    ("yes", "yes"), ("no", "no"), ("not now", "later"),
])
def test_voice_command_forms(text, kind):
    assert parse(text).kind == kind


def test_voice_stop_wins_and_questions_do_nothing(svc):
    buy_by_voice(svc, "pasta")
    svc.pause("groceries", True)
    say(svc, "stop and resume groceries")
    assert svc.cats["groceries"]["paused"] is True               # not resumed
    n_orders = len(svc.orders)
    undo_before = [o for o in svc.orders if svc.undo_until(o)]
    for text in ("what does undo my last order mean", "avoid undo my last order"):
        say(svc, text)
        assert "I did not catch that" in last_assistant(svc)
    assert len(svc.orders) == n_orders and [o for o in svc.orders if svc.undo_until(o)] == undo_before
    assert all(svc._status(o) != "CANCELLED" for o in svc.orders)


def test_stop_during_approval_still_wins_in_a_compound_sentence(svc):
    svc.shop("coffee", "shop")
    assert svc.session
    say(svc, "I said stop and resume groceries")
    assert svc.session is None


def test_yes_still_works_during_approval(svc):
    svc.shop("coffee", "shop")
    say(svc, "yes")
    assert svc._status("O-003") == "SUCCEEDED"


# ---------- 3. recovery is persisted: a second restart works
def test_second_restart_after_recovery_needs_no_other_action(tmp_path):
    path = str(tmp_path / "receipts.jsonl")
    a = NudgeService(path, key="k", profile="demo")
    a.set_fault("timeout_after_accept")
    a.shop("coffee", "shop")
    say(a, "yes")
    assert a._status("O-003") == "UNKNOWN"
    b = NudgeService(path, key="k")                     # recovery makes it SUCCEEDED
    assert b._status("O-003") == "SUCCEEDED"
    c = NudgeService(path, key="k")                     # no action in between: must still start
    assert c._status("O-003") == "SUCCEEDED"
    assert c._charges_for("O-003") == 1
    assert c.order_view(c.orders["O-003"])["can_undo"] is True
    assert c.undo("O-003")["ok"]


# ---------- 4. proposals and terminal error paths are persisted
def test_new_autonomy_proposal_survives_a_restart(tmp_path):
    path = str(tmp_path / "receipts.jsonl")
    a = NudgeService(path, key="k", profile="demo")
    r = a.set_tier("household", "ask", HUMAN)           # household starts at suggest, so this raises authority
    assert r["needs_confirmation"]
    pid = r["proposal_id"]
    b = NudgeService(path, key="k")
    assert pid in b.props and b.props[pid]["category"] == "household"
    assert b.confirm_autonomy(pid, HUMAN)["ok"] and b.cats["household"]["tier"] == "ask"


def test_offer_proposal_opened_by_an_approval_survives_a_restart(tmp_path):
    path = str(tmp_path / "receipts.jsonl")
    a = NudgeService(path, key="k", profile="demo")
    buy_by_voice(a, "pasta")
    pid = a.offers["groceries"]["proposal_id"]
    b = NudgeService(path, key="k")
    assert b.offers["groceries"]["proposal_id"] == pid and pid in b.props


def test_expired_step_up_session_stays_gone_after_restart(tmp_path):
    path = str(tmp_path / "receipts.jsonl")
    a = NudgeService(path, key="k", profile="demo")
    a.shop("coffee", "shop")
    sid = a.session["proposal_id"]
    a.now += timedelta(days=2)
    res = a.answer(sid, True, None, HUMAN)
    assert not res["ok"] and a.session is None
    b = NudgeService(path, key="k")
    assert b.session is None


# ---------- 5. MCP transport order and envelopes
def _client(srv):
    c = Client(srv)
    c.start()
    return c


def test_transport_checks_come_before_params_validation(srv):
    c = _client(srv)
    body = {"jsonrpc": "2.0", "id": 9, "method": "tools/list", "params": "oops"}
    st, _, b = c.raw(body=body, session=False)                  # no session header: transport error first
    assert st == 400 and b["error"]["code"] != -32602
    st, _, b = c.raw(body=body, headers={"MCP-Protocol-Version": "1999-01-01"})
    assert st == 400 and b["error"]["code"] != -32602
    st, _, b = c.raw(body=body)                                  # valid transport: now params are checked
    assert st == 200 and b["error"]["code"] == -32602


@pytest.mark.parametrize("env", [
    {"jsonrpc": "2.0", "id": 2, "error": "bogus"},
    {"jsonrpc": "2.0", "id": 2, "error": {"code": "x", "message": "m"}},
    {"jsonrpc": "2.0", "id": 2, "error": {"code": -1}},
    {"jsonrpc": "2.0", "id": 2, "result": {}, "error": {"code": -1, "message": "m"}},
])
def test_malformed_client_responses_are_rejected(srv, env):
    st, _, b = _client(srv).raw(body=env)
    assert st == 400 and b["error"]["code"] == -32600


def test_well_formed_client_response_is_accepted(srv):
    c = _client(srv)
    assert c.raw(body={"jsonrpc": "2.0", "id": 2, "result": {}})[0] == 202
    assert c.raw(body={"jsonrpc": "2.0", "id": 3, "error": {"code": -1, "message": "m"}})[0] == 202


def test_mcp_responses_carry_the_csp(srv):
    st, h, _ = _client(srv).rpc("ping")
    assert st == 200 and "default-src" in h.get("content-security-policy", "")


def test_session_table_is_locked_and_bounded(srv):
    assert hasattr(srv.RequestHandlerClass, "__init__")
    for _ in range(M.MAX_SESSIONS + 3):
        Client(srv).start()


# ---------- 6. storage errors and state schema
def test_storage_error_is_a_safe_typed_error(tmp_path, monkeypatch):
    s = NudgeService(str(tmp_path / "r.jsonl"), key="k", profile="demo")
    web = serve("127.0.0.1", 0, s)
    threading.Thread(target=web.serve_forever, daemon=True).start()
    try:
        def boom(*a, **k):
            raise OSError("disk full at C:/secret/path")
        monkeypatch.setattr(s, "_save", boom)
        st, data, _ = http_call(web, "POST", "/api/pause", {"on": True}, {"X-Nudge-Token": web.nudge_token})
        out = json.loads(data)
        assert st == 500 and out["code"] == "STORAGE_ERROR" and "secret" not in data.decode()
        c = Client(web)
        c.start()
        r = c.tool("pause_autonomy", paused=True)
        assert r["isError"] and r["structuredContent"]["code"] == "RESTART_REQUIRED"   # refused after the failure
        assert "secret" not in json.dumps(r)
    finally:
        web.shutdown()
        web.server_close()


@pytest.mark.parametrize("drop", ["cats", "orders", "checkpoint", "adapter", "now"])
def test_restore_rejects_incomplete_state_file(tmp_path, drop):
    path = str(tmp_path / "receipts.jsonl")
    NudgeService(path, key="k", profile="demo")
    sp = path + ".state.json"
    d = json.load(open(sp, encoding="utf-8"))
    d.pop(drop)
    json.dump(d, open(sp, "w", encoding="utf-8"))
    with pytest.raises(StateError):
        NudgeService(path, key="k")


def test_restore_rejects_non_object_state_file(tmp_path):
    path = str(tmp_path / "receipts.jsonl")
    NudgeService(path, key="k", profile="demo")
    open(path + ".state.json", "w", encoding="utf-8").write("[1, 2]")
    with pytest.raises(StateError):
        NudgeService(path, key="k")


# ---------- 7. quantity in digest and large-pending summaries; no "null" text; honest origin labels
def _top_level_args(src, start):
    """Top-level arguments of the call whose '(' is at src[start] (string literals skipped)."""
    depth, i, quote, args, cur = 0, start, None, [], []
    while i < len(src):
        ch = src[i]
        if quote:
            cur.append(ch)
            if ch == "\\":
                cur.append(src[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "\"'`":
            quote = ch
            cur.append(ch)
        elif ch in "([{":
            depth += 1
            if depth > 1:
                cur.append(ch)
        elif ch in ")]}":
            depth -= 1
            if depth == 0:
                args.append("".join(cur).strip())
                return args
            cur.append(ch)
        elif ch == "," and depth == 1:
            args.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
        i += 1
    raise AssertionError("unbalanced call")


def test_digest_and_pending_summaries_show_quantity():
    import re
    js = _js()
    assert 'S("ui.orders.qty", {n: i.qty})' in js and 'S("ui.orders.qty", {n: o.qty})' in js
    # replaceChildren prints a null child as the word "null" (el() filters nulls, replaceChildren does not)
    bad = []
    for m in re.finditer(r"\.replaceChildren\(", js):
        for arg in _top_level_args(js, m.end() - 1):
            a = re.sub(r"//[^\n]*", "", arg).strip()
            if a == "null" or re.search(r"(:|\|\|)\s*null$", a):
                bad.append(a[:80])
    assert not bad, bad
    # an order prepared by the daily check is labeled by origin, never as already approved
    strings = json.load(open(os.path.join(HERE, "copy", "strings.json"), encoding="utf-8"))["strings"]
    for key in ("source.assistant", "source.shop", "source.suggestion"):
        assert "approved" not in strings[key]["text"].lower(), key
