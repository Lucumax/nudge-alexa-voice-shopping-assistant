"""Regression tests: voice negation, stale offers, restore consistency, quantity, indeterminate lookups, feed accuracy and provenance."""
import json

import pytest

from approval_receipt import LookupResult
from app.service import HUMAN, NudgeService, StateError
from app.voice import parse
from conftest import buy_by_voice, say, tier
from test_mcp import Client, srv  # noqa: F401  (the MCP client and server fixture)


# ---- A: negated or reported sentences never become an action
@pytest.mark.parametrize("text", [
    "I didn't ask you to undo my last order", "I didnt ask you to undo my last order",
    "that isn't what I meant, undo is not wanted", "I asked you to undo nothing", "if I say undo, what happens",
    "he said take it back", "don't undo it", "I never said resume groceries"])
def test_negated_or_reported_sentences_do_nothing(text):
    assert parse(text).kind == "negated"


def test_a_negated_undo_leaves_the_order_completed(svc):
    oid = buy_by_voice(svc, "coffee")
    assert svc._status(oid) == "SUCCEEDED"
    say(svc, "I didn't ask you to undo my last order")
    assert svc._status(oid) == "SUCCEEDED" and svc.adapter.net_charges() == 3
    say(svc, "undo my last order")                      # the plain command still works
    assert svc._status(oid) == "CANCELLED"


def test_stop_and_pause_still_work_in_reported_speech():
    assert parse("I said stop").kind == "stop"
    assert parse("no stop").kind == "stop"
    assert parse("pause groceries").kind == "pause"


# ---- B: a stale autonomy proposal can never override a newer, lower limit
def test_old_proposal_cannot_raise_a_limit_that_was_lowered_later(svc):
    tier(svc, "groceries", "limits")
    pid = svc.set_limits("groceries", item_minor=3000, actor=HUMAN)["proposal_id"]
    assert svc.set_limits("groceries", item_minor=500, actor=HUMAN)["ok"]       # lowered at once
    assert pid not in svc.props                                                # superseded
    r = svc.confirm_autonomy(pid, HUMAN)
    assert not r["ok"] and svc.cats["groceries"]["item_minor"] == 500


def test_proposal_is_stale_when_the_stored_limit_changes_by_any_path(svc):
    tier(svc, "groceries", "limits")
    pid = svc.set_limits("groceries", item_minor=3000, actor=HUMAN)["proposal_id"]
    svc.cats["groceries"]["day_minor"] = 4000                                   # any other change of the base
    r = svc.confirm_autonomy(pid, HUMAN)
    assert r["code"] == "PROPOSAL_STALE" and svc.cats["groceries"]["item_minor"] != 3000


def test_a_reset_to_ask_drops_open_proposals(svc):
    tier(svc, "groceries", "limits")
    pid = svc.set_limits("groceries", item_minor=3000, actor=HUMAN)["proposal_id"]
    svc._reset_to_ask("groceries", "problem")
    assert pid not in svc.props and svc.confirm_autonomy(pid, HUMAN)["code"] == "NO_PROPOSAL"


# ---- C: restore refuses a state that disagrees with the receipts, and changes nothing
def _snapshot(path):
    return {p: open(p, "rb").read() for p in (path, path + ".state.json")}


def test_restore_refuses_an_old_state_file_with_newer_receipts(tmp_path):
    path = str(tmp_path / "receipts.jsonl")
    a = NudgeService(path, key="k", profile="demo")
    old_state = open(path + ".state.json", "rb").read()
    buy_by_voice(a, "coffee")                                  # receipts move on; then an old state comes back
    open(path + ".state.json", "wb").write(old_state)
    before = _snapshot(path)
    with pytest.raises(StateError, match="does not match the verified receipts"):
        NudgeService(path, key="k")
    assert _snapshot(path) == before


def test_restore_refuses_a_completed_receipt_the_store_state_lacks(tmp_path):
    path = str(tmp_path / "receipts.jsonl")
    a = NudgeService(path, key="k", profile="demo")
    buy_by_voice(a, "coffee")
    d = json.load(open(path + ".state.json", encoding="utf-8"))
    d["adapter"]["accepted"] = {}                              # the store side of the state lost the payment
    json.dump(d, open(path + ".state.json", "w", encoding="utf-8"))
    before = _snapshot(path)
    with pytest.raises(StateError):
        NudgeService(path, key="k")
    assert _snapshot(path) == before


def test_restore_after_an_ordinary_session_still_works(tmp_path):
    path = str(tmp_path / "receipts.jsonl")
    a = NudgeService(path, key="k", profile="demo")
    oid = buy_by_voice(a, "coffee")
    say(a, "undo my last order")
    a.run_assistant()
    a.advance(30)
    b = NudgeService(path, key="k")
    assert b.restored and b._status(oid) == "CANCELLED"


# ---- D: quantity is read back, is part of the terms, and reviewing a waiting order does not change it
def _last_say(s):
    return [m["text"] for m in s.transcript if m["who"] == "assistant"][-1]


def test_readback_says_the_quantity(svc):
    svc.shop("coffee", "shop", 5)
    text = _last_say(svc)
    assert "5 of Happy Belly Ground Coffee" in text and "$57.50" in text
    o = svc.orders[max(svc.orders)]
    assert o.qty == 5 and o.terms["quantity"] == 5


def test_quantity_is_part_of_the_terms_hash(svc):
    from app.service import _terms, _terms_hash
    t1 = _terms("coffee", 1, 1150, svc.now.date())
    t5 = _terms("coffee", 5, 1150, svc.now.date())
    assert _terms_hash("O-1", t1) != _terms_hash("O-1", t5)


def test_reviewing_a_pending_order_keeps_its_quantity_and_id(svc):
    svc.shop("coffee", "shop", 5)
    oid = max(svc.orders)
    svc.session = None                                         # the read-back is closed; the order still waits
    assert svc._status(oid) == "PENDING_APPROVAL"
    n = len(svc.orders)
    svc.shop("coffee")                                         # what the Review button sends: no quantity
    assert len(svc.orders) == n and svc.orders[oid].qty == 5 and svc._status(oid) == "PENDING_APPROVAL"
    assert svc.session["ids"] == [oid]
    assert "5 of Happy Belly Ground Coffee" in _last_say(svc)


def test_order_view_carries_the_quantity(svc):
    svc.shop("coffee", "shop", 3)
    assert svc.order_view(svc.orders[max(svc.orders)])["qty"] == 3


# ---- E: an indeterminate lookup never says "you were not charged"
def test_indeterminate_reconcile_says_the_outcome_is_unknown(svc):
    svc.set_fault("timeout_after_accept")
    svc.shop("coffee", "shop")
    say(svc, "yes")
    oid = max(svc.orders)
    assert svc._status(oid) == "UNKNOWN"
    svc.adapter.lookup = lambda key: LookupResult.indeterminate()
    r = svc.reconcile(oid)
    assert svc._status(oid) == "UNKNOWN"
    assert "unknown" in r["message"] and "not charged" not in r["message"] and "retried" in r["message"]


def test_a_definitive_absence_still_says_not_charged(svc):
    svc.set_fault("timeout_before_accept")
    svc.shop("coffee", "shop")
    say(svc, "yes")
    oid = max(svc.orders)
    assert "not charged" in svc.reconcile(oid)["message"]


# ---- F: the feed announces a purchase only when it is verified
def test_feed_does_not_say_bought_for_failed_or_unknown_manual_payments(svc):
    svc.set_fault("reject_next")
    svc.shop("coffee", "shop")
    say(svc, "yes")
    svc.set_fault("timeout_after_accept")
    svc.shop("pasta", "shop")
    say(svc, "yes")
    assert not any(f["text"].startswith("Bought") for f in svc.feed)
    assert [f["kind"] for f in svc.feed[:2]] == ["unknown", "failed"]
    assert svc.sugg["s-pasta"]["status"] == "prepared" and svc.sugg["s-coffee"]["status"] == "new"


def test_feed_says_bought_for_a_verified_manual_payment(svc):
    buy_by_voice(svc, "coffee")
    assert any(f["kind"] == "bought" and f["text"].startswith("Bought Happy Belly Ground Coffee") for f in svc.feed)
    assert svc.sugg["s-coffee"]["status"] == "ordered"


def test_automatic_attempt_that_fails_is_not_announced_as_bought(svc):
    tier(svc, "groceries", "limits")
    svc.set_fault("reject_next")
    svc.run_assistant()
    failed = [o for o in svc.orders.values() if o.auto and svc._status(o.id) == "FAILED"]
    done = [o for o in svc.orders.values() if o.auto and svc._status(o.id) == "SUCCEEDED"]
    assert len(failed) == 1
    assert sum(f["kind"] == "auto" for f in svc.feed) == len(done)        # one "Bought" per verified purchase
    assert any(f["kind"] == "failed" for f in svc.feed)
    assert sum(s.get("status") == "ordered" for s in svc.sugg.values()) == len(done)


def test_automatic_purchase_that_succeeds_is_announced(svc):
    tier(svc, "groceries", "limits")
    svc.run_assistant()
    assert any(f["kind"] == "auto" for f in svc.feed)
    assert any(s.get("status") == "ordered" for s in svc.sugg.values())


# ---- G: a purchase started from an idea card keeps its provenance
def test_card_purchase_through_mcp_is_recorded_as_a_suggestion(srv):  # noqa: F811
    c = Client(srv)
    c.start()
    svc = srv.nudge_service
    c.tool("propose_purchase", product_id="pasta", suggestion_id="s-pasta")
    assert svc.orders[max(svc.orders)].source == "suggestion"
    c.tool("propose_purchase", product_id="paper-towels")
    assert svc.orders[max(svc.orders)].source == "shop"
    c.tool("propose_purchase", product_id="coffee", suggestion_id="s-pasta")   # card for a different product
    assert svc.orders[max(svc.orders)].source == "shop"
