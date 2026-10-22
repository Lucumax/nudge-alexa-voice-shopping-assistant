"""Regressions for the last release pass: explicit stop beside negation, storage failure handling, ids across
demo resets, restore consistency and catalog copy."""
import json
import os
import threading

import pytest

from conftest import HUMAN, buy_by_voice, say
from app import catalog as C
from app import texts as T
from app.server import serve
from app.service import NudgeService, StateError
from app.voice import parse
from test_hardening import http_call
from test_mcp import Client


# ---------- 1. an explicit stop is not defeated by a negation elsewhere in the sentence
@pytest.mark.parametrize("text,kind", [
    ("Stop, don't buy it", "stop"),
    ("stop and do not buy", "stop"),
    ("please stop", "stop"),
    ("stop, do not resume groceries", "stop"),
    ("do not buy it, stop", "stop"),
    ("stop but do not pause", "stop"),
    ("do not pause", "negated"),
    ("do not pause groceries", "negated"),
    ("don't stop", "negated"),
    ("never stop buying", "negated"),
    ("do not resume groceries", "negated"),
    ("don't undo my last order", "negated"),
    ("do not set groceries to within limits", "negated"),
    ("don't buy it", "negated"),
])
def test_stop_precedes_negation(text, kind):
    assert parse(text).kind == kind


def test_stop_closes_the_open_read_back_despite_negation(svc):
    svc.shop("pasta")
    assert svc.session
    say(svc, "Stop, don't buy it")
    assert svc.session is None
    assert not [o for o in svc.orders.values() if o.approved and o.product_id == "pasta"]


def test_negated_pause_does_not_pause(svc):
    say(svc, "do not pause groceries")
    assert svc.cats["groceries"]["paused"] is False and svc.paused_all is False


def test_stop_and_do_not_buy_keeps_other_rules(svc):
    svc.shop("pasta")
    say(svc, "stop and do not buy")
    assert svc.session is None and svc.paused_all is False


# ---------- 2. storage failures
def test_storage_wording_does_not_promise_nothing_changed():
    msg = T.t("err.STORAGE_ERROR")
    assert "Nothing was bought" not in msg and "may or may not" in msg and "Orders and receipts" in msg


def _web(tmp_path):
    s = NudgeService(str(tmp_path / "r.jsonl"), key="k", profile="demo")
    web = serve("127.0.0.1", 0, s)
    threading.Thread(target=web.serve_forever, daemon=True).start()
    return s, web


def _post(web, path, body):
    st, data, _ = http_call(web, "POST", path, body, {"X-Nudge-Token": web.nudge_token})
    return st, json.loads(data)


def _boom(*a, **k):
    raise OSError("disk full at /private/place")


def test_authenticated_state_read_failure_is_a_typed_error(tmp_path, monkeypatch):
    s, web = _web(tmp_path)
    try:
        monkeypatch.setattr(s, "state", _boom)
        st, data, _ = http_call(web, "GET", "/api/state", None, {"X-Nudge-Token": web.nudge_token})
        out = json.loads(data)
        assert st == 500 and out["code"] == "STORAGE_ERROR" and "private" not in data.decode()
    finally:
        web.shutdown()
        web.server_close()


def test_failed_save_after_a_completed_payment_blocks_further_actions(tmp_path, monkeypatch):
    s, web = _web(tmp_path)
    try:
        assert _post(web, "/api/shop", {"product": "pasta"})[0] == 200
        pid = s.session["proposal_id"]
        paid_before = len(s.adapter.accepted)
        monkeypatch.setattr(s, "_save", _boom)
        st, out = _post(web, "/api/answer", {"proposal": pid, "confirm": True})
        assert st == 500 and out["code"] == "STORAGE_ERROR"
        assert len(s.adapter.accepted) == paid_before + 1        # the payment did complete before the failed save
        assert "Nothing was bought" not in out["message"]
        assert s.needs_restart
        orders_before = dict(s.orders)
        st, out = _post(web, "/api/shop", {"product": "coffee"})
        assert st == 503 and out["code"] == "RESTART_REQUIRED"
        st, out = _post(web, "/api/profile", {"name": "fresh"})
        assert st == 503 and out["code"] == "RESTART_REQUIRED"
        assert s.orders == orders_before
        c = Client(web)
        c.start()
        r = c.tool("pause_autonomy", paused=True)
        assert r["isError"] and r["structuredContent"]["code"] == "RESTART_REQUIRED"
        assert s.paused_all is False
    finally:
        web.shutdown()
        web.server_close()


def test_failed_pause_save_blocks_further_actions(tmp_path, monkeypatch):
    s, web = _web(tmp_path)
    try:
        monkeypatch.setattr(s, "_save", _boom)
        st, out = _post(web, "/api/pause", {"on": True})
        assert st == 500 and out["code"] == "STORAGE_ERROR"
        st, out = _post(web, "/api/pause", {"on": False})
        assert st == 503 and out["code"] == "RESTART_REQUIRED"
    finally:
        web.shutdown()
        web.server_close()


def test_restart_after_failed_payment_save_refuses_a_state_behind_the_receipts(tmp_path, monkeypatch):
    path = str(tmp_path / "r.jsonl")
    s = NudgeService(path, key="k", profile="demo")
    s.shop("pasta")
    monkeypatch.setattr(s, "_save", _boom)
    with pytest.raises(OSError):
        say(s, "yes")
    with pytest.raises(StateError):
        NudgeService(path, key="k")           # the receipts moved on; the old state is refused, nothing changes


def test_real_save_failure_sets_needs_restart(tmp_path, monkeypatch):
    s = NudgeService(str(tmp_path / "r.jsonl"), key="k", profile="demo")

    def denied(*a):
        raise OSError("denied")
    monkeypatch.setattr(os, "replace", denied)
    with pytest.raises(OSError):
        s._save()
    assert s.needs_restart


# ---------- 3. ids are never reused across demo resets
@pytest.mark.parametrize("profile", ["fresh", "demo"])
def test_read_back_ids_survive_a_profile_reset(tmp_path, profile):
    s = NudgeService(str(tmp_path / "r.jsonl"), key="k", profile="demo")
    s.shop("pasta")
    old = s.session["proposal_id"]
    s.reset(profile)
    s.shop("pasta")
    assert s.session["proposal_id"] != old
    assert not s.answer(old, True, actor=HUMAN)["ok"]
    assert not any(o.approved and o.product_id == "pasta" for o in s.orders.values())


@pytest.mark.parametrize("profile", ["fresh", "demo"])
def test_autonomy_proposal_ids_survive_a_profile_reset(tmp_path, profile):
    s = NudgeService(str(tmp_path / "r.jsonl"), key="k", profile="demo")
    s.cats["groceries"]["streak"] = 3
    old = s.set_tier("groceries", "limits", HUMAN)["proposal_id"]
    s.reset(profile)
    if profile == "fresh":                  # a fresh start has no history, so earn it with three approvals
        s.cats["groceries"].update(enabled=True, tier="ask")
        for pid in ("pasta", "coffee", "oat-milk"):
            buy_by_voice(s, pid)
    s.cats["groceries"]["streak"] = 3
    new = s.set_tier("groceries", "limits", HUMAN)["proposal_id"]
    assert new != old
    assert not s.confirm_autonomy(old, HUMAN)["ok"]
    assert s.cats["groceries"]["tier"] != "limits"


def test_ids_survive_reset_over_http(tmp_path):
    s, web = _web(tmp_path)
    try:
        _post(web, "/api/shop", {"product": "pasta"})
        old = s.session["proposal_id"]
        for name in ("fresh", "demo"):
            assert _post(web, "/api/profile", {"name": name})[0] == 200
            _post(web, "/api/shop", {"product": "pasta"})
            assert s.session["proposal_id"] != old
            st, out = _post(web, "/api/answer", {"proposal": old, "confirm": True})
            assert not out["result"]["ok"]
            old = s.session["proposal_id"]
    finally:
        web.shutdown()
        web.server_close()


# ---------- 4. restore compares amounts, quantities and terms
def _tamper_state(path, fn):
    sp = path + ".state.json"
    d = json.load(open(sp, encoding="utf-8"))
    fn(d)
    json.dump(d, open(sp, "w", encoding="utf-8"))
    return open(path, "rb").read()


def test_restore_rejects_a_saved_amount_that_differs_from_the_receipt(tmp_path):
    path = str(tmp_path / "r.jsonl")
    s = NudgeService(path, key="k", profile="demo")
    s.shop("pasta")
    say(s, "yes")
    assert s.orders[max(s.orders)].amount_minor == 920

    def fn(d):
        d["orders"][max(d["orders"])]["amount_minor"] = 1
    before = _tamper_state(path, fn)
    with pytest.raises(StateError):
        NudgeService(path, key="k")
    assert open(path, "rb").read() == before


def test_restore_rejects_a_displayed_quantity_that_differs_from_the_snapshot(tmp_path):
    path = str(tmp_path / "r.jsonl")
    s = NudgeService(path, key="k", profile="demo")
    s.shop("pasta")

    def fn(d):
        d["orders"][max(d["orders"])]["qty"] = 5
    before = _tamper_state(path, fn)
    with pytest.raises(StateError):
        NudgeService(path, key="k")
    assert open(path, "rb").read() == before


def test_terms_ok_rejects_a_quantity_that_differs_from_the_snapshot(svc):
    svc.shop("pasta")
    s = svc.session
    o = svc.orders[s["ids"][0]]
    assert svc._terms_ok(s)
    o.qty = 5
    assert not svc._terms_ok(s)


def test_an_untouched_state_still_restores(tmp_path):
    path = str(tmp_path / "r.jsonl")
    s = NudgeService(path, key="k", profile="demo")
    s.shop("pasta")
    t = NudgeService(path, key="k")
    assert set(t.orders) == set(s.orders)


# ---------- 5. catalog copy and illustration label
def test_catalog_descriptions_match_the_photos():
    assert "glass bottle" in C.BY_ID["olive-oil"].blurb and "tin" not in C.BY_ID["olive-oil"].blurb
    assert C.BY_ID["candle-set"].blurb.startswith("Four")


def test_illustration_label_is_not_a_placeholder():
    from app.svg import product_svg
    out = product_svg("pasta")
    assert "Product illustration" in out and "placeholder" not in out.lower()


# ---------- 6. the MCP 401 carries the same content security policy
def test_mcp_unauthorized_reply_carries_the_csp(tmp_path):
    s, web = _web(tmp_path)
    try:
        st, _, h = http_call(web, "POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "ping"},
                             {"Accept": "application/json"})
        assert st == 401 and "frame-ancestors 'none'" in h.get("Content-Security-Policy", "")
    finally:
        web.shutdown()
        web.server_close()
