"""Autonomy tiers, the read-back, the voice yes, and the phone step-up."""
import re

from conftest import buy_by_voice, last_assistant, say, code, tier, limit, HUMAN
from app import catalog as C


def graduate_groceries(s):
    """Demo profile already holds 2 approvals. One more earns the offer; accept it."""
    buy_by_voice(s, "pasta")
    assert "groceries" in s.offers
    assert s.accept_offer("groceries", HUMAN, s.offers["groceries"]["proposal_id"])["ok"]
    assert s.cats["groceries"]["tier"] == "limits"


# ---------- tiers
def test_ask_first_never_pays_before_a_yes(svc):
    svc.shop("coffee", "suggestion")
    assert svc.adapter.calls == svc.base_calls                      # prepared, not bought
    assert svc.orders["O-003"] and svc._status("O-003") == "PENDING_APPROVAL"
    say(svc, "yes")
    assert svc.adapter.calls == svc.base_calls + 1 and svc._status("O-003") == "SUCCEEDED"


def test_suggest_tier_assistant_makes_no_orders(svc):
    assert svc.cats["household"]["tier"] == "suggest"
    before = len(svc.orders)
    svc.run_assistant()
    household = [o for o in svc.orders.values() if o.category == "household"]
    assert household == [] and svc.adapter.calls == svc.base_calls


def test_ask_tier_assistant_prepares_but_does_not_buy(svc):
    tier(svc, "household", "ask")
    calls = svc.adapter.calls
    svc.run_assistant()
    prepared = [o for o in svc.orders.values() if o.category == "household"]
    assert prepared and all(svc._status(o.id) == "PENDING_APPROVAL" for o in prepared)
    assert svc.adapter.calls == calls


def test_within_limits_buys_alone_inside_limits_and_receipt_says_so(svc):
    graduate_groceries(svc)
    calls = svc.adapter.calls
    svc.run_assistant()                                  # coffee price drop is a groceries card
    autos = [o for o in svc.orders.values() if o.auto]
    assert autos and svc.adapter.calls == calls + len(autos)
    rec = svc._latest(autos[0].id)
    assert rec["decision"]["reason_code"] == "WITHIN_MANDATE" and rec["outcome"] == "SUCCEEDED"
    assert rec["approver"] is None                       # nobody approved: the mandate did


def test_above_item_limit_asks_and_explains_what_it_did_not_do(svc):
    graduate_groceries(svc)
    limit(svc, "groceries", 1000)                     # $10.00 each; coffee is $11.50
    svc.run_assistant()
    coffee = [o for o in svc.orders.values() if o.product_id == "coffee"][0]
    assert not coffee.auto and svc._status(coffee.id) == "PENDING_APPROVAL"
    assert any(f["kind"] == "did_not" and "Happy Belly Ground Coffee" in f["text"] for f in svc.feed)


def test_store_outside_the_mandate_is_not_bought_alone(svc):
    graduate_groceries(svc)
    svc.cats["groceries"]["merchants"] = ["amazon-fresh"]
    svc._install_auto("groceries")
    forbidden = [p for p in C.PRODUCTS if p.category == "groceries" and p.merchant == "whole-foods"]
    assert forbidden, "fixture: the catalog must have a grocery item from a store outside the mandate"
    o = svc._propose(forbidden[0].id, "assistant")      # explicitly the forbidden store, no reliance on signals
    assert not o.auto and svc._status(o.id) == "PENDING_APPROVAL"
    assert any(f["kind"] == "did_not" and "not one of the stores" in f["text"] for f in svc.feed)


def test_daily_limit_caps_what_it_does_alone(svc):
    graduate_groceries(svc)
    limit(svc, "groceries", 1500)
    limit(svc, "groceries", 1500, daily=True)
    svc.run_assistant()
    auto_total = sum(o.amount_minor for o in svc.orders.values() if o.auto and o.category == "groceries")
    assert auto_total <= 1500


def test_autonomy_can_never_reach_the_step_up_amount(svc):
    graduate_groceries(svc)
    limit(svc, "groceries", 20_000, daily=True)
    r = svc.set_limits("groceries", item_minor=25_000, actor=HUMAN)      # $250 asked for
    assert "$99.99" in r["message"]
    svc.confirm_autonomy(r["proposal_id"], HUMAN)
    assert svc.cats["groceries"]["item_minor"] == C.AUTONOMY_CAP_MINOR
    assert svc.engine.mandates["nudge-groceries-auto"].per_payment_limit < C.STEP_UP_MINOR
    # an expensive grocery-like item can never be bought alone: use household filter via tier change
    svc.cats["household"].update(tier="limits", merchants=["amazon-household"], item_minor=C.AUTONOMY_CAP_MINOR, day_minor=50_000)
    svc._install_auto("household")
    o = svc._propose("air-filter", "assistant")
    assert not o.auto and svc._status(o.id) == "PENDING_APPROVAL"


def test_pause_stops_buying_by_itself_but_not_the_users_own_yes(svc):
    graduate_groceries(svc)
    svc.pause(None, True, HUMAN)
    calls = svc.adapter.calls
    svc.run_assistant()
    assert svc.adapter.calls == calls
    assert any(f["kind"] == "did_not" for f in svc.feed)
    svc.pause(None, False, HUMAN)
    svc.pause("groceries", True, HUMAN)
    assert svc.engine.mandates["nudge-groceries-auto"].expires_at < svc.now   # core would DENY it too
    buy_by_voice(svc, "oat-milk")                         # the user's own tap and yes still works
    assert svc._status(max(svc.orders)) == "SUCCEEDED"


# ---------- read-back and voice yes
def test_readback_has_item_merchant_amount_delivery_and_card_last_four(svc):
    svc.shop("pasta", "shop")
    text = last_assistant(svc)
    for needle in ("Whole-Grain Pasta", "Amazon Fresh", "$9.20", "Thu, Oct 22", "4242"):
        assert needle in text
    assert "yes" in text.lower()


def test_no_declines_and_nothing_is_bought(svc):
    svc.shop("pasta", "shop")
    say(svc, "no")
    assert svc.adapter.calls == svc.base_calls and svc._status("O-003") == "DENIED"


def test_not_now_leaves_it_pending(svc):
    svc.shop("pasta", "shop")
    say(svc, "not now")
    assert svc.adapter.calls == svc.base_calls and svc._status("O-003") == "PENDING_APPROVAL" and svc.session is None


def test_unclear_answer_does_not_buy(svc):
    svc.shop("pasta", "shop")
    say(svc, "what is the weather")
    assert svc.adapter.calls == svc.base_calls and svc.session is not None
    assert "yes or a no" in last_assistant(svc)


def test_yes_without_a_readback_buys_nothing(svc):
    say(svc, "yes")
    assert svc.adapter.calls == svc.base_calls and "nothing to confirm" in last_assistant(svc).lower()


def test_approval_is_recorded_with_approver_channel_and_note(svc):
    oid = buy_by_voice(svc, "pasta")
    rec = svc._latest(oid)
    assert rec["approver"]["id"] == "maya-demo" and "channel=voice" in rec["approver"]["note"]


# ---------- step-up
def test_step_up_sends_code_to_phone_and_never_speaks_it(svc):
    svc.shop("air-filter", "shop")                       # $129.00
    say(svc, "yes")
    assert svc.session["stage"] == "stepup" and svc.adapter.calls == svc.base_calls
    code = svc.session["code"]
    assert re.fullmatch(r"\d{4}", code)
    assert any(code in m["text"] for m in svc.phone)
    assert all(code not in m["text"] for m in svc.transcript)
    say(svc, f"my code is {code}")               # even typed into the voice box
    assert all(code not in m["text"] for m in svc.transcript)
    assert svc._status("O-003") == "SUCCEEDED"
    assert "step_up=yes" in svc._latest("O-003")["approver"]["note"]


def test_wrong_code_three_times_locks_and_buys_nothing(svc):
    svc.shop("air-filter", "shop")
    say(svc, "yes")
    bad = "0000" if svc.session["code"] != "0000" else "1111"
    for _ in range(3):
        code(svc, bad)
    assert svc.session is None and svc.adapter.calls == svc.base_calls
    assert svc._status("O-003") == "PENDING_APPROVAL"


def test_code_cannot_be_skipped_by_saying_yes_again(svc):
    svc.shop("air-filter", "shop")
    say(svc, "yes")
    say(svc, "yes")
    assert svc.adapter.calls == svc.base_calls and svc.session["stage"] == "stepup"


def test_code_expires(svc):
    svc.shop("air-filter", "shop")
    say(svc, "yes")
    good = svc.session["code"]
    svc.now += __import__("datetime").timedelta(minutes=11)
    r = code(svc, good)
    assert r["ok"] is False and r["code"] == "STEPUP_EXPIRED"
    assert svc.adapter.calls == svc.base_calls and svc.session is None


def test_just_under_the_limit_needs_no_code(svc):
    svc.shop("saute-pan", "shop")                        # $112.00 gift: needs code
    assert svc.session["step_up"]
    say(svc, "no")
    svc.shop("storage-set", "shop")                      # $34.00
    assert not svc.session["step_up"]
    say(svc, "yes")
    assert svc._status(max(svc.orders)) == "SUCCEEDED"


def test_changed_rules_cannot_be_approved_with_a_stale_digest(svc):
    """The core binds approval to terms: a stale digest is refused and nothing is bought."""
    svc.shop("pasta", "shop")
    svc.session["digests"]["O-003"] = "0" * 64
    say(svc, "yes")
    assert svc.adapter.calls == svc.base_calls and svc._status("O-003") == "PENDING_APPROVAL"
    assert any("TERMS_CHANGED" in f["text"] for f in svc.feed)
