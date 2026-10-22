"""Suggestion cards (reason, why, dismiss that learns, never default on, never urgent) and voice parity."""
import re
from datetime import date

from conftest import last_assistant, say, code, tier, limit, HUMAN
from app import catalog as C
from app import texts as T
from app.voice import parse


# ---------- nudge rules
def test_nothing_is_opted_in_by_default(fresh):
    assert fresh.suggestions() == []
    assert all(not c["enabled"] for c in fresh.cats.values())
    fresh.run_assistant()
    assert fresh.orders == {}


def test_every_card_has_a_visible_reason_and_a_why(svc):
    cards = svc.suggestions()
    assert cards
    for c in cards:
        assert len(c["reason"]) > 20 and "you" in c["reason"].lower()
        assert "turned on ideas" in c["why"] and "no timers" in c["why"].lower()


def test_turning_a_category_off_removes_its_cards(svc):
    svc.set_enabled("household", False, HUMAN)
    assert all(c["category"] != "household" for c in svc.suggestions())


def test_cards_are_driven_by_the_demo_clock_not_by_urgency(svc):
    ids = {c["id"] for c in svc.suggestions()}
    assert "s-paper" in ids
    svc.pause(None, True, HUMAN)
    assert {c["id"] for c in svc.suggestions()} == ids              # pausing never invents or hides ideas


def test_copy_has_no_manufactured_urgency():
    banned = ["hurry", "last chance", "act now", "limited time", "selling fast", "running out", "only a few",
              "don't miss", "ends soon", "left in stock", "expires", "deal ends", "flash", "countdown timer"]
    for key, entry in T.load().items():
        text = entry["text"].lower()
        for b in banned:
            assert b not in text, (key, b)


def test_a_new_card_never_uses_a_sensitive_inference(svc):
    """Reasons come from past orders, reminders the user typed, and prices. Nothing about health or life events."""
    sensitive = ["pregnan", "health", "diagnos", "divorce", "medical", "illness", "grief"]
    for c in svc.suggestions():
        assert not any(w in (c["reason"] + c["why"]).lower() for w in sensitive)
    assert {s["kind"] for s in C.SIGNALS} <= {"restock", "price_drop", "reminder"}


# ---------- dismissal learns
def test_dismiss_not_interested_stops_that_idea_and_says_so(svc):
    r = svc.dismiss("s-paper", "not_interested")
    assert "will not suggest" in r["message"]
    assert "s-paper" not in {c["id"] for c in svc.suggestions()}
    svc.advance(24 * 14)
    assert "s-paper" not in {c["id"] for c in svc.suggestions()}


def test_dismiss_too_soon_snoozes_for_a_week_then_returns(svc):
    svc.dismiss("s-paper", "too_soon")
    assert "s-paper" not in {c["id"] for c in svc.suggestions()}
    svc.advance(24 * 8)
    assert "s-paper" in {c["id"] for c in svc.suggestions()}


def test_dismiss_already_have_it_waits_a_month(svc):
    svc.dismiss("s-paper", "have_it")
    svc.advance(24 * 8)
    assert "s-paper" not in {c["id"] for c in svc.suggestions()}


def test_three_dismissals_in_a_row_reduce_how_many_cards_show(svc):
    assert svc.cats["household"]["max_cards"] == 2
    for sid in ("s-paper", "s-detergent", "s-filter"):
        svc.dismiss(sid, "not_interested")
    assert svc.cats["household"]["max_cards"] == 1
    assert "only one idea at a time" in svc.feed[0]["text"]


def test_bad_dismiss_reason_is_rejected(svc):
    assert not svc.dismiss("s-paper", "because")["ok"]


# ---------- catalog and delivery
def test_delivery_dates_come_from_the_fixed_demo_date():
    assert C.DEMO_NOW.date() == date(2026, 10, 21)
    coffee = C.BY_ID["coffee"]                                      # 1 business day
    assert C.delivery_date(coffee, C.DEMO_NOW) == date(2026, 10, 22)
    late = C.DEMO_NOW.replace(hour=15)                              # after the cutoff ships tomorrow
    assert C.delivery_date(coffee, late) == date(2026, 10, 23)
    friday = C.DEMO_NOW.replace(day=23)
    assert C.delivery_date(C.BY_ID["candle-set"], friday) == date(2026, 10, 28)   # skips the weekend


def test_every_product_has_price_rating_delivery_return_and_svg(svc):
    from app.svg import product_svg
    for p in svc.state()["products"]:
        assert p["price"].startswith("$") and "out of 5" in p["rating"] and p["delivery"].startswith("FREE delivery")
        assert p["returns"]
        svg = product_svg(p["id"])
        assert svg.startswith("<svg") and "<text" not in svg and "http" not in svg.replace("http://www.w3.org/2000/svg", "")


# ---------- voice parity
def test_parser_understands_the_core_commands():
    assert parse("Yes!").kind == "yes" and parse("No thanks").kind == "no"
    assert parse("set groceries limit to $30").params == {"category": "groceries", "minor": 3000, "daily": False}
    assert parse("limit groceries to 40 dollars a day").params["daily"] is True
    assert parse("set household to within limits").params == {"category": "household", "tier": "limits"}
    assert parse("pause gifts").params["category"] == "gifts" and parse("resume everything").kind == "resume"
    assert parse("approve my digest").kind == "digest" and parse("undo my last order").kind == "undo_last"
    assert parse("what is my rule").kind == "show_rule"


def test_lowering_a_limit_by_voice_is_immediate(svc):
    svc.cats["groceries"]["tier"] = "limits"
    svc.cats["groceries"]["merchants"] = ["amazon-fresh"]
    svc._install_auto("groceries")
    say(svc, "set groceries limit to $8")
    assert svc.cats["groceries"]["item_minor"] == 800 and svc.confirm is None


def test_raising_a_limit_by_voice_needs_a_yes(svc):
    svc.cats["groceries"]["tier"] = "limits"
    svc.cats["groceries"]["merchants"] = ["amazon-fresh"]
    svc.cats["groceries"]["item_minor"] = 1000
    svc._install_auto("groceries")
    say(svc, "set groceries limit to $30")
    assert svc.cats["groceries"]["item_minor"] == 1000 and svc.confirm["type"] == "autonomy"
    assert "Say yes" in last_assistant(svc)
    say(svc, "yes")
    assert svc.cats["groceries"]["item_minor"] == 3000
    assert svc.engine.mandates["nudge-groceries-auto"].per_payment_limit == 3000


def test_voice_limit_above_the_cap_is_clamped_and_explained(svc):
    svc.cats["groceries"]["tier"] = "limits"
    svc.cats["groceries"]["merchants"] = ["amazon-fresh"]
    svc.cats["groceries"]["day_minor"] = 20_000
    svc._install_auto("groceries")
    say(svc, "set groceries limit to $500")
    say(svc, "yes")
    assert svc.cats["groceries"]["item_minor"] == C.AUTONOMY_CAP_MINOR
    assert "phone" in " ".join(m["text"] for m in svc.transcript if m["who"] == "assistant")


def test_raising_the_tier_by_voice_needs_a_yes_and_lowering_does_not(svc):
    say(svc, "set household to ask first")
    assert svc.cats["household"]["tier"] == "suggest"              # not yet
    say(svc, "yes")
    assert svc.cats["household"]["tier"] == "ask"
    say(svc, "set household to suggest")
    assert svc.cats["household"]["tier"] == "suggest"              # lowering is immediate


def test_no_cancels_a_pending_voice_confirmation(svc):
    say(svc, "set household to ask first")
    say(svc, "no")
    say(svc, "yes")
    assert svc.cats["household"]["tier"] == "suggest"


def test_pause_and_resume_by_voice_and_the_rule_sentence(svc):
    say(svc, "pause groceries")
    assert svc.cats["groceries"]["paused"] and "paused" in svc.mandate_sentence("groceries").lower()
    say(svc, "resume groceries")
    assert not svc.cats["groceries"]["paused"]
    say(svc, "what is my rule for groceries")
    assert "asks for your yes" in last_assistant(svc)


def test_within_limits_by_voice_needs_history_and_confirmation(svc):
    say(svc, "set household to within limits")
    assert "needs 3 approved" in last_assistant(svc)               # the three-approval rule, server side
    svc.cats["groceries"]["streak"] = 3
    say(svc, "set groceries to within limits")
    assert "Say yes" in last_assistant(svc) and svc.cats["groceries"]["tier"] == "ask"


def test_unknown_input_gets_examples_not_an_action(svc):
    calls = svc.adapter.calls
    say(svc, "buy everything")
    assert "I did not catch that" in last_assistant(svc) and svc.adapter.calls == calls


def test_amount_like_digits_in_the_transcript_are_not_codes(svc):
    say(svc, "set groceries limit to $1234")
    assert svc.cats["groceries"]["item_minor"] != 123400
    assert re.search(r"\$99\.99", " ".join(m["text"] for m in svc.transcript))
