"""Graduation after three approvals, reset after a bad outcome, undo, and the daily digest."""
from datetime import timedelta

from conftest import buy_by_voice, last_assistant, say, code, tier, limit, HUMAN
from app import catalog as C


def graduate_groceries(s):
    buy_by_voice(s, "pasta")
    s.accept_offer("groceries", HUMAN, s.offers["groceries"]["proposal_id"])
    assert s.cats["groceries"]["tier"] == "limits"


# ---------- graduation
def test_offer_appears_only_after_three_approvals(fresh):
    s = fresh
    s.set_enabled("groceries", True, HUMAN)
    tier(s, "groceries", "ask")
    buy_by_voice(s, "oat-milk")
    buy_by_voice(s, "olive-oil")
    assert "groceries" not in s.offers and s.cats["groceries"]["streak"] == 2
    buy_by_voice(s, "pasta")
    off = s.offers["groceries"]
    assert off["to"] == "limits"
    assert "approved three" in last_assistant(s)
    assert s.cats["groceries"]["tier"] == "ask"          # an offer never changes the tier by itself


def test_graduation_builds_a_real_core_mandate_with_known_stores(svc):
    buy_by_voice(svc, "pasta")
    svc.accept_offer("groceries", HUMAN, svc.offers["groceries"]["proposal_id"])
    m = svc.engine.mandates["nudge-groceries-auto"]
    assert 0 < m.per_payment_limit < C.STEP_UP_MINOR and m.daily_limit >= m.per_payment_limit
    assert set(m.payee_allowlist) == {"amazon-fresh", "whole-foods"}
    assert "up to $" in svc.mandate_sentence("groceries") and "Amazon Fresh" in svc.mandate_sentence("groceries")


def test_declining_the_offer_resets_the_count(svc):
    buy_by_voice(svc, "pasta")
    svc.decline_offer("groceries", HUMAN)
    assert svc.cats["groceries"]["tier"] == "ask" and svc.cats["groceries"]["streak"] == 0
    assert "groceries" not in svc.offers


def test_declined_orders_do_not_count_toward_graduation(fresh):
    s = fresh
    s.set_enabled("groceries", True, HUMAN)
    tier(s, "groceries", "ask")
    for pid in ("oat-milk", "olive-oil", "pasta"):
        s.shop(pid, "shop")
        say(s, "no")
    assert s.cats["groceries"]["streak"] == 0 and not s.offers


def test_yes_by_voice_accepts_the_offer(svc):
    buy_by_voice(svc, "pasta")
    say(svc, "yes")
    assert svc.cats["groceries"]["tier"] == "limits"


def test_suggest_tier_offers_ask_first_not_limits(fresh):
    s = fresh
    s.set_enabled("household", True, HUMAN)
    for pid in ("paper-towels", "detergent", "storage-set"):
        buy_by_voice(s, pid)
    assert s.offers["household"]["to"] == "ask"


def test_within_limits_needs_history_first(fresh):
    r = tier(fresh, "gifts", "limits")
    assert not r["ok"] and r["code"] == "NEED_HISTORY"


# ---------- reset after a bad outcome
def test_problem_report_resets_to_ask_first_and_drops_the_mandate(svc):
    graduate_groceries(svc)
    svc.run_assistant()
    auto = next(o for o in svc.orders.values() if o.auto)
    r = svc.report_problem(auto.id, "wrong_item")
    assert r["ok"] and svc.cats["groceries"]["tier"] == "ask" and svc.cats["groceries"]["streak"] == 0
    assert "nudge-groceries-auto" not in svc.engine.mandates
    assert any(f["kind"] == "reset" for f in svc.feed)
    assert svc._latest(auto.id)["problem"] == "wrong_item"      # recorded on the receipt chain
    assert svc.verify()["ok"]
    calls, known = svc.adapter.calls, set(svc.orders)
    svc.run_assistant()
    assert all(not o.auto for o in svc.orders.values() if o.id not in known)
    assert svc.adapter.calls == calls                            # nothing bought alone after the reset


def test_undoing_an_automatic_order_also_resets(svc):
    graduate_groceries(svc)
    svc.run_assistant()
    auto = next(o for o in svc.orders.values() if o.auto)
    assert svc.undo(auto.id)["ok"]
    assert svc.cats["groceries"]["tier"] == "ask"


def test_problem_on_a_tier_that_already_asks_does_not_change_it(svc):
    oid = buy_by_voice(svc, "oat-milk")
    svc.cats["groceries"]["tier"] = "ask"
    svc.report_problem(oid, "late")
    assert svc.cats["groceries"]["tier"] == "ask"


# ---------- undo
def test_undo_inside_24_hours_cancels_and_refunds(svc):
    oid = buy_by_voice(svc, "pasta")
    assert svc.state()["orders"][0]["can_undo"]
    svc.advance(23)
    r = svc.undo(oid)
    assert r["ok"] and svc._status(oid) == "CANCELLED"
    assert svc.adapter.net_charges() == svc.base_calls         # the two seeded orders only
    assert svc._latest(oid)["refund_ref"].startswith("refund-")
    assert svc.verify()["ok"]


def test_undo_window_closes_after_24_hours(svc):
    oid = buy_by_voice(svc, "pasta")
    svc.advance(25)
    r = svc.undo(oid)
    assert not r["ok"] and r["code"] == "UNDO_WINDOW_CLOSED" and ("refund" in r["message"].lower() or "returns" in r["message"].lower())
    assert svc._status(oid) == "SUCCEEDED"


def test_undo_twice_is_refused(svc):
    oid = buy_by_voice(svc, "pasta")
    svc.undo(oid)
    assert not svc.undo(oid)["ok"]


def test_undo_by_voice(svc):
    buy_by_voice(svc, "pasta")
    say(svc, "undo my last order")
    assert svc._status(max(svc.orders)) == "CANCELLED"


def test_undone_spend_frees_the_daily_limit(svc):
    graduate_groceries(svc)
    limit(svc, "groceries", 1500)
    limit(svc, "groceries", 1500, daily=True)
    first = svc._propose("coffee", "assistant")
    assert first.auto and svc._reserved_auto("groceries") == first.amount_minor
    svc.undo(first.id)
    assert svc._reserved_auto("groceries") == 0


# ---------- digest
def prepared_household(s):
    tier(s, "household", "ask")
    s.run_assistant()
    return [o for o in s.digest_items() if o.category == "household"]


def test_digest_approves_several_small_items_with_one_yes(svc):
    prepared_household(svc)
    items = svc.digest_items()                               # household and grocery items together
    assert len(items) >= 2
    svc.start_digest()
    assert "Approving" in last_assistant(svc) and "Total" in last_assistant(svc)
    calls = svc.adapter.calls
    say(svc, "yes")
    if svc.session:                                          # total at or above $100 asks for a code
        code(svc, svc.session["code"])
    done = [o for o in items if svc._status(o.id) == "SUCCEEDED"]
    assert len(done) == len(items) and svc.adapter.calls == calls + len(items)
    for o in done:                                           # each item has its own receipt and channel
        assert "channel=digest" in svc._latest(o.id)["approver"]["note"]
    assert svc.verify()["ok"]


def test_digest_leave_out_keeps_that_item_pending(svc):
    items = prepared_household(svc)
    skip = items[0]
    svc.start_digest(exclude=[skip.id])
    say(svc, "yes")
    if svc.session:
        code(svc, svc.session["code"])
    assert svc._status(skip.id) == "PENDING_APPROVAL"


def test_digest_total_at_or_above_100_needs_a_code(svc):
    s = svc
    tier(s, "household", "ask")
    for pid in ("storage-set", "detergent", "paper-towels", "storage-set"):
        s._propose(pid, "assistant")
    total = sum(o.amount_minor for o in s.digest_items())
    assert total >= C.STEP_UP_MINOR
    s.start_digest()
    say(s, "yes")
    assert s.session and s.session["stage"] == "stepup" and s.adapter.calls == s.base_calls
    assert "total" in last_assistant(s).lower()


def test_digest_never_contains_items_that_need_their_own_code(svc):
    tier(svc, "household", "ask")
    svc._propose("air-filter", "assistant")
    assert all(o.amount_minor < C.STEP_UP_MINOR for o in svc.digest_items())
    assert any(o.amount_minor >= C.STEP_UP_MINOR for o in svc.pending())


def test_pending_requests_expire_after_24_hours_and_buy_nothing(svc):
    items = prepared_household(svc)
    svc.advance(25)
    assert all(svc._status(o.id) == "EXPIRED" for o in items)
    assert svc.adapter.calls == svc.base_calls
