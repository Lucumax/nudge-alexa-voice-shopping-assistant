"""Adversarial regression tests: authorization, approval binding, limits, double-charge protection, voice parsing,
persistence, local security and protocol shape. They try to break each rule rather than follow the happy path."""
import dataclasses
import http.client
import json
import os
import threading
from datetime import timedelta

import pytest

from conftest import HUMAN, AGENT, buy_by_voice, code, last_assistant, limit, say, tier
from app import catalog as C
from app.mcp import McpServer
from app.server import serve
from app.service import NudgeService, StateError
from app.voice import code_only, parse


def graduate(s, c="groceries", pid="pasta"):
    buy_by_voice(s, pid)
    assert c in s.offers
    assert s.accept_offer(c, HUMAN, s.offers[c]["proposal_id"])["ok"]


# ======================= the caller cannot raise its own autonomy
def test_an_agent_can_propose_a_raise_but_never_apply_it(svc):
    graduate_ready = svc.cats["groceries"]
    graduate_ready["streak"] = 3
    r = svc.set_tier("groceries", "limits", AGENT)
    assert r["ok"] and r["needs_confirmation"] and svc.cats["groceries"]["tier"] == "ask"
    assert svc.confirm_autonomy(r["proposal_id"], AGENT)["code"] == "NOT_AUTHORIZED"
    assert svc.accept_offer("groceries", AGENT)["code"] == "NOT_AUTHORIZED"
    assert svc.cats["groceries"]["tier"] == "ask" and "nudge-groceries-auto" not in svc.engine.mandates


def test_raising_needs_three_approvals_even_for_a_person(fresh):
    fresh.set_enabled("groceries", True, HUMAN)
    tier(fresh, "groceries", "ask")
    buy_by_voice(fresh, "oat-milk")                       # one historical purchase
    r = fresh.set_tier("groceries", "limits", HUMAN)
    assert r["ok"] is False and r["code"] == "NEED_APPROVALS"
    assert fresh.cats["groceries"]["tier"] == "ask"


def test_a_confirmation_is_single_use_and_applies_the_terms_that_were_shown(svc):
    svc.cats["groceries"]["streak"] = 3
    r = svc.set_tier("groceries", "limits", HUMAN)
    pid = r["proposal_id"]
    shown = dict(svc.props[pid])
    assert svc.confirm_autonomy(pid, HUMAN)["ok"]
    m = svc.engine.mandates["nudge-groceries-auto"]
    assert (m.per_payment_limit, m.daily_limit) == (shown["item_minor"], shown["day_minor"])
    assert svc.confirm_autonomy(pid, HUMAN)["code"] == "NO_PROPOSAL"      # spent


def test_a_stale_or_expired_proposal_is_refused(svc):
    svc.cats["groceries"]["streak"] = 3
    pid = svc.set_tier("groceries", "limits", HUMAN)["proposal_id"]
    svc.now += timedelta(hours=1)
    assert svc.confirm_autonomy(pid, HUMAN)["code"] == "PROPOSAL_EXPIRED"
    pid = svc.set_tier("groceries", "limits", HUMAN)["proposal_id"]
    svc.cats["groceries"]["streak"] = 0                                    # the earned approvals are gone
    assert svc.confirm_autonomy(pid, HUMAN)["code"] == "PROPOSAL_STALE"
    assert svc.cats["groceries"]["tier"] == "ask"


def test_resuming_after_a_pause_is_not_available_to_an_agent(svc):
    graduate(svc)
    svc.pause("groceries", True, AGENT)                    # anyone may stop
    assert svc.pause("groceries", False, AGENT)["code"] == "NOT_AUTHORIZED"
    assert svc.cats["groceries"]["paused"] is True
    assert svc.pause("groceries", False, HUMAN)["ok"]


def test_agent_voice_and_enable_are_refused(svc):
    assert svc.handle_voice("resume groceries", None, AGENT)["code"] == "NOT_AUTHORIZED"
    assert svc.set_enabled("gifts", False, AGENT)["code"] == "NOT_AUTHORIZED"


@pytest.fixture
def web(tmp_path):
    svc = NudgeService(str(tmp_path / "r.jsonl"), key="k", profile="demo")
    httpd = serve("127.0.0.1", 0, svc)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd
    httpd.shutdown()
    httpd.server_close()


def http_call(httpd, method, path, body=None, headers=None, host=None):
    port = httpd.server_address[1]
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    h = {"Host": host or f"127.0.0.1:{port}", **(headers or {})}
    if body is not None:
        h["Content-Type"] = "application/json"
    c.request(method, path, json.dumps(body) if body is not None else None, h)
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, data, dict(r.getheaders())


def mcp_session(httpd, token):
    port = httpd.server_address[1]
    sid = {}

    def rpc(method, params=None, note=False):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        h = {"Host": f"127.0.0.1:{port}", "Accept": "application/json", "Content-Type": "application/json",
             "Authorization": "Bearer " + token}
        if sid:
            h["Mcp-Session-Id"], h["MCP-Protocol-Version"] = sid["id"], "2025-11-25"
        msg = {"jsonrpc": "2.0", "method": method} if note else {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
        c.request("POST", "/mcp", json.dumps(msg), h)
        r = c.getresponse()
        raw = r.read()
        if r.getheader("Mcp-Session-Id"):
            sid["id"] = r.getheader("Mcp-Session-Id")
        c.close()
        return json.loads(raw) if raw else None

    rpc("initialize", {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}})
    rpc("notifications/initialized", note=True)
    return lambda name, **a: rpc("tools/call", {"name": name, "arguments": a})["result"]


def test_host_token_cannot_say_yes_or_confirm_over_mcp_or_rest(web):
    svc = web.nudge_service
    tool = mcp_session(web, web.nudge_agent_token)
    pid = tool("propose_purchase", product_id="pasta")["structuredContent"]["proposal_id"]
    calls = svc.adapter.calls
    r = tool("approve_purchase", proposal_id=pid, confirm=True)
    assert r["isError"] and r["structuredContent"]["code"] == "NOT_AUTHORIZED" and svc.adapter.calls == calls
    svc.cats["groceries"]["streak"] = 3
    r = tool("set_autonomy", category="groceries", tier="limits")
    assert r["structuredContent"]["needs_confirmation"] and svc.cats["groceries"]["tier"] == "ask"
    assert tool("pause_autonomy", paused=True)["isError"] is False
    assert tool("pause_autonomy", paused=False)["structuredContent"]["code"] == "NOT_AUTHORIZED"
    # the host token does not open the page API either
    tok = {"X-Nudge-Token": web.nudge_agent_token}
    assert http_call(web, "POST", "/api/autonomy_confirm", {"proposal": r["structuredContent"]["proposal_id"]}, tok)[0] == 403
    assert http_call(web, "POST", "/api/offer_accept", {"category": "groceries"}, tok)[0] == 403
    assert http_call(web, "GET", "/api/state", headers=tok)[0] == 403
    assert svc.cats["groceries"]["tier"] == "ask"
    # the person, through the same service, can answer the very same proposal
    human = mcp_session(web, web.nudge_token)
    assert human("approve_purchase", proposal_id=pid, confirm=True)["isError"] is False


# ======================= a yes approves only the exact proposal and terms
def test_a_yes_for_an_older_readback_approves_nothing(svc):
    svc.shop("pasta", "shop")
    old = svc.session["proposal_id"]
    svc.shop("coffee", "shop")                              # another tab opens a different read-back
    new = svc.session["proposal_id"]
    assert old != new
    calls = svc.adapter.calls
    r = svc.answer(old, True, None, HUMAN)
    assert r["code"] == "PROPOSAL_MISMATCH" and svc.adapter.calls == calls
    assert svc.handle_voice("yes", None, HUMAN)["code"] == "PROPOSAL_MISMATCH"      # an untargeted yes
    assert svc.handle_voice("yes", old, HUMAN)["code"] == "PROPOSAL_MISMATCH"
    assert svc.adapter.calls == calls and svc.session["proposal_id"] == new
    assert svc.answer(new, True, None, HUMAN)["ok"] and svc.adapter.calls == calls + 1


def test_a_proposal_can_be_consumed_only_once(svc):
    svc.shop("pasta", "shop")
    pid = svc.session["proposal_id"]
    assert svc.answer(pid, True, None, HUMAN)["ok"]
    calls = svc.adapter.calls
    assert svc.answer(pid, True, None, HUMAN)["ok"] is False and svc.adapter.calls == calls


def test_changed_catalog_terms_invalidate_the_yes(svc, monkeypatch):
    svc.shop("pasta", "shop")
    pid = svc.session["proposal_id"]
    p = C.BY_ID["pasta"]
    monkeypatch.setitem(C.BY_ID, "pasta", dataclasses.replace(p, price_minor=p.price_minor + 500))
    calls = svc.adapter.calls
    r = svc.answer(pid, True, None, HUMAN)
    assert r["ok"] is False and r["code"] == "TERMS_CHANGED" and svc.adapter.calls == calls
    assert svc.session is None


def test_the_terms_hash_is_in_the_signed_receipt_and_covers_quantity_delivery_card(svc):
    svc.shop("pasta", "shop", 2)
    o = svc.orders["O-003"]
    assert o.terms == dict(product_id="pasta", quantity=2, delivery=o.delivery.isoformat(), card_last4="4242",
                           unit_minor=920, amount_minor=1840)
    key = svc._latest(o.id)["intent"]["idempotency_key"]
    assert o.terms_hash[:16] in key
    other = svc._propose("pasta", "shop", 3)
    assert other.terms_hash != o.terms_hash and other.terms_hash[:16] not in key


def test_a_readback_expires(svc):
    svc.shop("pasta", "shop")
    pid = svc.session["proposal_id"]
    svc.now += timedelta(hours=1)
    calls = svc.adapter.calls
    assert svc.answer(pid, True, None, HUMAN)["code"] == "READBACK_EXPIRED" and svc.adapter.calls == calls


# ======================= the stated daily limit is the enforced limit
def test_a_daily_limit_below_the_item_limit_is_rejected_not_silently_raised(svc):
    graduate(svc)
    m = svc.engine.mandates["nudge-groceries-auto"]
    before = (m.per_payment_limit, m.daily_limit)
    r = svc.set_limits("groceries", day_minor=1000, actor=HUMAN)       # stated $10 a day, item limit is higher
    assert r["ok"] is False and r["code"] == "DAY_BELOW_ITEM"
    m = svc.engine.mandates["nudge-groceries-auto"]
    assert (m.per_payment_limit, m.daily_limit) == before


def test_the_mandate_daily_limit_equals_the_stated_amount_and_coffee_waits(svc):
    graduate(svc)
    limit(svc, "groceries", 500)
    limit(svc, "groceries", 1000, daily=True)
    m = svc.engine.mandates["nudge-groceries-auto"]
    assert (m.per_payment_limit, m.daily_limit) == (500, 1000)
    svc.run_assistant()
    coffee = [o for o in svc.orders.values() if o.product_id == "coffee"]
    assert coffee and not coffee[0].auto                    # $11.50 is over both limits


def test_the_saved_daily_amount_is_the_sum_the_assistant_may_spend(svc):
    graduate(svc)
    limit(svc, "groceries", 1500, daily=True)
    spent = lambda: sum(o.amount_minor for o in svc.orders.values() if o.auto and o.category == "groceries")
    svc.run_assistant()
    assert spent() <= 1500
    o = svc._propose("coffee", "assistant")                 # a second purchase the same day
    assert spent() <= 1500 and not (o.auto and spent() > 1500)


# ======================= no double charge through a new proposal
def test_a_new_proposal_for_an_unknown_order_is_blocked(svc):
    svc.set_fault("timeout_after_accept")
    svc.shop("pasta", "shop")
    say(svc, "yes")
    assert svc._status("O-003") == "UNKNOWN"
    accepted = dict(svc.adapter.accepted)
    r = svc.shop("pasta", "shop")
    assert r["ok"] is False and r["code"] == "UNRESOLVED_ORDER" and "Check with the store" in r["message"]
    assert len(svc.orders) == 3 and svc.session is None and svc.adapter.accepted == accepted
    svc.run_assistant()
    assert len([o for o in svc.orders.values() if o.product_id == "pasta"]) == 1     # the daily check also waits
    svc.reconcile("O-003")
    assert svc._status("O-003") == "SUCCEEDED" and len(svc.adapter.accepted) == 3
    assert svc.shop("pasta", "shop")["ok"]                                          # settled: a new purchase is a new purchase


def test_blocked_also_for_an_order_that_is_still_submitting_or_authorized(svc):
    svc.set_fault("timeout_before_accept")
    svc.shop("pasta", "shop")
    say(svc, "yes")
    assert svc._status("O-003") == "UNKNOWN"                # not accepted by the store, but not proven either
    assert svc.shop("pasta", "shop")["code"] == "UNRESOLVED_ORDER"


# ======================= a failed step-up is an error, never ok
def test_wrong_locked_and_expired_codes_are_errors(svc):
    svc.shop("air-filter", "shop")
    say(svc, "yes")
    good = svc.session["code"]
    bad = "0000" if good != "0000" else "1111"
    r = code(svc, bad)
    assert r["ok"] is False and r["code"] == "STEPUP_WRONG" and svc.session is not None
    code(svc, bad)
    r = code(svc, bad)
    assert r["ok"] is False and r["code"] == "STEPUP_LOCKED" and svc.session is None
    assert svc.adapter.calls == svc.base_calls
    svc.shop("air-filter", "shop")
    say(svc, "yes")
    good = svc.session["code"]
    svc.now += timedelta(minutes=11)
    r = code(svc, good)
    assert r["ok"] is False and r["code"] == "STEPUP_EXPIRED" and svc.adapter.calls == 2


def test_over_mcp_a_failed_step_up_has_is_error_and_no_receipt(web):
    svc = web.nudge_service
    tool = mcp_session(web, web.nudge_token)
    pid = tool("propose_purchase", product_id="air-filter")["structuredContent"]["proposal_id"]
    tool("approve_purchase", proposal_id=pid, confirm=True)
    good = svc.session["code"]
    bad = "0000" if good != "0000" else "1111"
    for _ in range(3):
        r = tool("approve_purchase", proposal_id=pid, confirm=True, step_up_code=bad)
        assert r["isError"] is True and r["structuredContent"]["ok"] is False and "receipts" not in r["structuredContent"]
    assert r["structuredContent"]["code"] == "STEPUP_LOCKED" and svc.adapter.calls == 2


def test_a_failed_payment_and_an_unknown_payment_are_not_ok(svc):
    svc.set_fault("reject_next")
    svc.shop("pasta", "shop")
    r = svc.answer(svc.session["proposal_id"], True, None, HUMAN)
    assert r["ok"] is False and r["code"] == "OUTCOME_FAILED"
    svc.set_fault("timeout_after_accept")
    svc.shop("coffee", "shop")
    r = svc.answer(svc.session["proposal_id"], True, None, HUMAN)
    assert r["ok"] is False and r["code"] == "OUTCOME_UNKNOWN"


# ======================= voice parsing
@pytest.mark.parametrize("text", ["do not resume groceries", "don't resume groceries", "please do not resume everything",
                                  "do not undo my last order", "don't undo it", "never undo my last order",
                                  "do not pause groceries", "I do not want to approve my digest",
                                  "don't set groceries to within limits", "do not set groceries limit to 30 dollars"])
def test_a_negated_request_never_becomes_the_action(svc, text):
    assert parse(text).kind == "negated"
    buy_by_voice(svc, "pasta")
    svc.pause("groceries", True, HUMAN)
    undo_ready = [o for o in svc.orders.values() if svc.undo_until(o.id)]
    before = (svc.cats["groceries"]["paused"], svc.cats["groceries"]["tier"], svc.cats["groceries"]["item_minor"],
              len([o for o in svc.orders.values() if svc._status(o.id) == "CANCELLED"]), len(undo_ready))
    say(svc, text)
    after = (svc.cats["groceries"]["paused"], svc.cats["groceries"]["tier"], svc.cats["groceries"]["item_minor"],
             len([o for o in svc.orders.values() if svc._status(o.id) == "CANCELLED"]),
             len([o for o in svc.orders.values() if svc.undo_until(o.id)]))
    assert before == after


def test_pause_or_stop_during_approval_stops_it(svc):
    for text in ("pause groceries", "please stop", "stop", "stop everything", "hold on"):
        svc.shop("air-filter", "shop")
        say(svc, "yes")                                    # now waiting for the code
        assert svc.session["stage"] == "stepup"
        calls = svc.adapter.calls
        say(svc, text, proposal=None)                       # stopping never needs a proposal id
        assert svc.session is None and svc.adapter.calls == calls, text
        assert "Stopped" in last_assistant(svc)
        svc.pause(None, False, HUMAN)
        for o in list(svc.pending()):
            svc.decline_order(o.id)


def test_pause_during_approval_also_pauses(svc):
    svc.shop("pasta", "shop")
    say(svc, "pause groceries")
    assert svc.session is None and svc.cats["groceries"]["paused"]


def test_four_digits_inside_other_words_are_not_a_code(svc):
    for text in ("pay 1234 dollars", "set groceries limit to $1234", "order 12345", "my code is 1234 and buy more",
                 "call 5551234", "1234 5678"):
        assert code_only(text) is None, text
    for text in ("1234", "my code is 1234", "its 1234.", "1 2 3 4", "code 0042"):
        assert code_only(text) is not None, text
    svc.shop("air-filter", "shop")
    say(svc, "yes")
    good = svc.session["code"]
    calls = svc.adapter.calls
    say(svc, f"send {good} dollars to someone")
    assert svc.session is not None and svc.adapter.calls == calls and svc.session["tries"] == 0
    say(svc, good)
    assert svc.adapter.calls == calls + 1


def test_unsupported_budget_periods_are_refused(svc):
    graduate(svc)
    for text in ("set groceries limit to 30 dollars a week", "groceries limit 100 per month", "set groceries limit to 20 weekly"):
        before = (svc.cats["groceries"]["item_minor"], svc.cats["groceries"]["day_minor"])
        say(svc, text)
        assert parse(text).kind == "unsupported_period" and "per item or per day" in last_assistant(svc)
        assert (svc.cats["groceries"]["item_minor"], svc.cats["groceries"]["day_minor"]) == before and not svc.props


def test_an_ambiguous_amount_is_not_guessed(svc):
    say(svc, "set groceries limit to 30 or 40 dollars")
    assert "Tell me the amount" in last_assistant(svc)


# ======================= undo window
def test_a_problem_report_does_not_extend_the_undo_window(svc):
    buy_by_voice(svc, "pasta")
    oid = "O-003"
    done_at = svc.now
    svc.now += timedelta(hours=23)
    assert svc.report_problem(oid, "late")["ok"]
    svc.now += timedelta(hours=2)                           # 25h after completion, 2h after the report
    assert svc.undo_until(oid) == done_at + timedelta(hours=C.UNDO_WINDOW_HOURS)
    r = svc.undo(oid)
    assert r["ok"] is False and r["code"] == "UNDO_WINDOW_CLOSED" and svc._status(oid) == "SUCCEEDED"
    assert not svc.adapter.cancelled


def test_undo_inside_the_window_still_works_after_a_report(svc):
    buy_by_voice(svc, "pasta")
    svc.now += timedelta(hours=1)
    svc.report_problem("O-003", "late")
    assert svc.undo("O-003")["ok"]


# ======================= persistence
def test_restart_restores_state_and_never_deletes(tmp_path):
    path = str(tmp_path / "receipts.jsonl")
    a = NudgeService(path, key="k", profile="demo")
    graduate(a)
    a.set_fault("timeout_before_accept")
    a.shop("coffee", "shop")
    say(a, "yes")
    ids = list(a.orders)
    cats, accepted = a.cats["groceries"]["tier"], dict(a.adapter.accepted)
    size = os.path.getsize(path)
    b = NudgeService(path, key="k")                          # startup: restore, not reset
    assert b.restored and list(b.orders) == ids and b.cats["groceries"]["tier"] == cats == "limits"
    assert b.adapter.accepted == accepted and os.path.getsize(path) >= size
    assert b._status("O-004") == "UNKNOWN" and b.adapter.timeout_before_accept is False   # one-shot stays spent
    assert b.engine.mandates["nudge-groceries-auto"].daily_limit == b.cats["groceries"]["day_minor"]
    assert b.verify()["ok"]
    b.shop("coffee", "shop")                                # still blocked after a restart
    assert b.unresolved_for("coffee") is not None
    nxt = b._propose("oat-milk", "shop")
    assert int(nxt.id[2:]) > max(int(i[2:]) for i in ids)


def test_startup_with_a_ledger_but_no_state_refuses_and_keeps_the_files(tmp_path):
    path = str(tmp_path / "receipts.jsonl")
    NudgeService(path, key="k", profile="demo")
    os.remove(path + ".state.json")
    size = os.path.getsize(path)
    with pytest.raises(StateError):
        NudgeService(path, key="k")
    assert os.path.exists(path) and os.path.getsize(path) == size


def test_startup_refuses_a_tampered_ledger(tmp_path):
    path = str(tmp_path / "receipts.jsonl")
    NudgeService(path, key="k", profile="demo")
    lines = open(path, encoding="utf-8").read().splitlines()
    lines[2] = lines[2].replace('"amount_minor":1380', '"amount_minor":1')
    open(path, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    with pytest.raises(StateError):
        NudgeService(path, key="k")


def test_reset_is_explicit_and_the_only_thing_that_deletes(tmp_path):
    path = str(tmp_path / "receipts.jsonl")
    a = NudgeService(path, key="k", profile="demo")
    buy_by_voice(a, "pasta")
    n = len(a.orders)
    assert len(NudgeService(path, key="k").orders) == n                     # opening again changes nothing
    a.reset("fresh")
    assert len(a.orders) == 0 and len(NudgeService(path, key="k").orders) == 0


def test_the_checkpoint_lives_apart_from_the_receipts(tmp_path):
    path = str(tmp_path / "receipts.jsonl")
    s = NudgeService(path, key="k", profile="demo")
    assert os.path.dirname(s.checkpoint_path) != os.path.dirname(path) and os.path.exists(s.checkpoint_path)


def test_a_demo_key_is_stamped_on_the_receipts(tmp_path, monkeypatch):
    monkeypatch.delenv("APPROVAL_RECEIPT_HMAC_KEY", raising=False)
    s = NudgeService(str(tmp_path / "r.jsonl"), profile="demo")        # no key anywhere: loudly marked demo mode
    assert s.ledger.demo_key and all(r.get("key_mode") == "DEMO" for r in s.ledger.receipts())


# ======================= local security
def test_binding_to_a_non_loopback_address_is_refused():
    for host in ("0.0.0.0", "192.168.1.20", "example.com", ""):
        with pytest.raises(ValueError):
            serve(host, 0)


def test_origin_and_host_are_exact(web):
    port = web.server_address[1]
    tok = {"X-Nudge-Token": web.nudge_token}
    bad_origins = ["http://127.0.0.1", "http://127.0.0.1:1", f"http://127.0.0.1:{port}.evil.example",
                   f"https://127.0.0.1:{port}", f"http://127.0.0.1:{port}/", "null", "file://", "http://evil.example",
                   f"http://localhost.evil.example:{port}", f"ftp://127.0.0.1:{port}", f"http://127.0.0.1:{port}x"]
    for o in bad_origins:
        assert http_call(web, "POST", "/api/voice", {"text": "hi"}, {**tok, "Origin": o})[0] == 403, o
        assert http_call(web, "GET", "/api/state", headers={**tok, "Origin": o})[0] == 403, o
    good = {**tok, "Origin": f"http://127.0.0.1:{port}"}
    assert http_call(web, "GET", "/api/state", headers=good)[0] == 200
    assert http_call(web, "GET", "/api/state", headers={**tok, "Origin": f"http://localhost:{port}"})[0] == 200
    for host in ("127.0.0.1", "localhost", f"127.0.0.1:{port + 1}", f"127.0.0.1.evil.example:{port}", "evil.example",
                 f"[::2]:{port}"):
        assert http_call(web, "GET", "/api/strings", host=host)[0] == 403, host
    assert http_call(web, "GET", "/api/strings", host=f"LOCALHOST:{port}")[0] == 200
    assert http_call(web, "GET", "/api/strings", headers={"Sec-Fetch-Site": "cross-site"})[0] == 403


def test_phone_codes_are_not_in_unauthenticated_state(web):
    svc = web.nudge_service
    svc.shop("air-filter", "shop")
    say(svc, "yes")
    good = svc.session["code"]
    st, body, _ = http_call(web, "GET", "/api/state")
    assert st == 403 and good.encode() not in body
    st, body, _ = http_call(web, "GET", "/api/state", headers={"X-Nudge-Token": "x" * 32})
    assert st == 403 and good.encode() not in body
    for path in ("/", "/app.js", "/api/strings", "/style.css"):
        assert good.encode() not in http_call(web, "GET", path)[1]


def test_security_headers_forbid_framing(web):
    _, _, h = http_call(web, "GET", "/")
    assert "frame-ancestors 'none'" in h["Content-Security-Policy"] and h["X-Frame-Options"] == "DENY"


def test_agent_token_is_not_in_the_page(web):
    assert web.nudge_agent_token.encode() not in http_call(web, "GET", "/")[1]
    assert web.nudge_agent_token != web.nudge_token


# ======================= protocol shape (more cases live in test_mcp.py)
def test_the_server_object_rejects_responses_without_a_session():
    m = McpServer.__new__(McpServer)
    m.sessions = {}
    st, _, b = m.handle({"jsonrpc": "2.0", "id": 1, "result": {}}, None, None, HUMAN)
    assert st == 400


def test_a_new_initialize_works_after_the_session_is_gone(web):
    """Server side of client recovery: after a session 404 a fresh initialize succeeds."""
    port = web.server_address[1]

    def post(body, sid=None):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        h = {"Host": f"127.0.0.1:{port}", "Accept": "application/json", "Content-Type": "application/json",
             "Authorization": "Bearer " + web.nudge_token}
        if sid:
            h["Mcp-Session-Id"], h["MCP-Protocol-Version"] = sid, "2025-11-25"
        c.request("POST", "/mcp", json.dumps(body), h)
        r = c.getresponse()
        r.read()
        out = (r.status, r.getheader("Mcp-Session-Id"))
        c.close()
        return out

    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}}
    st, sid = post(init)
    web.RequestHandlerClass  # noqa: B018 (server object exists)
    assert post({"jsonrpc": "2.0", "id": 2, "method": "ping"}, "stale")[0] == 404
    st, sid2 = post(init)
    assert st == 200 and sid2 and sid2 != sid


def test_restart_reconciles_an_order_the_store_accepted_but_never_answered(tmp_path):
    path = str(tmp_path / "receipts.jsonl")
    a = NudgeService(path, key="k", profile="demo")
    a.set_fault("timeout_after_accept")
    a.shop("coffee", "shop")
    say(a, "yes")
    assert a._status("O-003") == "UNKNOWN"
    charges = len(a.adapter.accepted)
    b = NudgeService(path, key="k")
    assert b._status("O-003") == "SUCCEEDED" and len(b.adapter.accepted) == charges   # settled, never re-sent
    assert any("After a restart" in f["text"] for f in b.feed)


# ======================= client recovery (the MCP client code, run under node without a browser)
def test_page_mcp_client_recovers(tmp_path):
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed; client recovery is then covered only by the README note")
    here = os.path.dirname(os.path.abspath(__file__))
    r = subprocess.run([node, os.path.join(here, "js", "mcp_client_harness.js")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    res = {x["name"]: x for x in json.loads(r.stdout)}
    a = res["failed_init_not_cached"]
    assert a["firstIsError"] and a["secondOk"] and a["inits"] == 2 and a["toasted"]
    b = res["reinitialize_after_404"]
    assert b["ok"] and b["inits"] == 2 and b["calls"] == 2
    c = res["network_failure_is_visible"]
    assert c["isError"] and c["toasted"]
