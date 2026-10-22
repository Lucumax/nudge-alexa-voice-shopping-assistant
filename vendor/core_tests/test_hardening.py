"""Hardening tests: durable execution states, idempotency, money, daily limit, concurrency."""
import threading
from datetime import timedelta

import pytest

from approval_receipt import (IdempotencyConflict, Intent, Engine, Ledger, SimulatedCrash)
from conftest import TEST_KEY, T0, make_intent, make_mandate, approve_pending


def t08_retry_with_same_key_never_pays_twice(env):
    first = env.engine.submit(make_intent(1, 42_000, key="K"))
    again = env.engine.submit(make_intent(9, 42_000, key="K"))  # retry: new id, same payload
    assert again.replayed and again.receipt["id"] == first.receipt["id"]
    assert env.adapter.calls == 1


def t08_same_key_different_payload_rejected(env):
    env.engine.submit(make_intent(1, 42_000, key="K"))
    with pytest.raises(IdempotencyConflict):
        env.engine.submit(make_intent(2, 43_000, key="K"))
    with pytest.raises(IdempotencyConflict):
        env.engine.submit(make_intent(3, 42_000, payee="other", key="K"))
    assert env.adapter.calls == 1


def t09_timeout_goes_unknown_then_reconcile_without_second_payment(env):
    env.adapter.timeout_after_accept = True
    res = env.engine.submit(make_intent(1, 42_000))
    assert res.receipt["outcome"] == "UNKNOWN"
    assert env.engine.submit(make_intent(1, 42_000)).receipt["outcome"] == "UNKNOWN"
    assert env.adapter.calls == 1  # resubmitting does not dispatch
    rec = env.engine.retry("I-1")  # reconcile finds the provider's record first
    assert rec["outcome"] == "SUCCEEDED" and rec["payment_ref"]
    assert env.adapter.calls == 1 and env.adapter.lookups == 1


def t09_definite_failure_is_failed_not_unknown(env):
    env.adapter.reject_next = True
    assert env.engine.submit(make_intent(1, 42_000)).receipt["outcome"] == "FAILED"


def t09_retry_when_provider_has_no_record_reuses_the_same_key(env):
    env.adapter.timeout_before_accept = True
    assert env.engine.submit(make_intent(1, 42_000)).receipt["outcome"] == "UNKNOWN"
    rec = env.engine.retry("I-1")  # lookup finds nothing, so re-dispatch with the same key
    assert rec["outcome"] == "SUCCEEDED" and env.adapter.calls == 2
    assert list(env.adapter.accepted) == ["key-1"]
    assert env.engine.retry("I-1")["outcome"] == "SUCCEEDED" and env.adapter.calls == 2


def t09_crash_after_provider_accepts_before_receipt_is_persisted(env):
    env.adapter.crash_after_accept = True
    with pytest.raises(SimulatedCrash):
        env.engine.submit(make_intent(1, 42_000))
    assert env.ledger.latest_for_intent("I-1")["outcome"] == "SUBMITTING"  # no result receipt
    assert env.adapter.calls == 1 and len(env.adapter.accepted) == 1
    eng = env.rebuild()  # process restart
    assert eng.submit(make_intent(1, 42_000)).replayed and env.adapter.calls == 1
    rec = eng.reconcile("I-1")
    assert rec["outcome"] == "SUCCEEDED" and rec["payment_ref"] == next(iter(env.adapter.accepted.values()))
    assert env.adapter.calls == 1 and env.ledger.verify().ok


def t09_authorized_is_persisted_before_dispatch(env):
    seen = []
    orig = env.adapter.pay

    def spy(*a):
        seen.append(env.ledger.latest_for_intent("I-1")["outcome"])
        return orig(*a)
    env.adapter.pay = spy
    env.engine.submit(make_intent(1, 42_000))
    assert seen == ["SUBMITTING"]
    assert [r["outcome"] for r in env.ledger.receipts()][:2] == ["AUTHORIZED", "SUBMITTING"]


def t10_money_must_be_integer_minor_units():
    with pytest.raises(TypeError):
        Intent("I", "a", "p", 420.0, "USD", "office", "", "k", T0)


def t10_currency_mismatch_denied(env):
    i = make_intent(1, 100)
    res = env.engine.submit(Intent(i.id, i.agent_id, i.payee, 100, "EUR", i.purpose_code, "", "k", T0))
    assert res.decision.reason_code == "CURRENCY_MISMATCH" and env.adapter.calls == 0


def t10_threshold_splitting_hits_the_daily_limit(env):
    out = [env.engine.submit(make_intent(n, 40_000)).decision for n in (1, 2, 3)]
    assert [d.decision for d in out] == ["ALLOW", "ALLOW", "ESCALATE"]
    assert out[2].reason_code == "DAILY_LIMIT_EXCEEDED" and env.adapter.calls == 2


def t10_daily_window_is_utc_date(env):
    env.engine.submit(make_intent(1, 50_000))
    env.engine.submit(make_intent(2, 50_000))
    assert env.engine.submit(make_intent(3, 1_000)).decision.decision == "ESCALATE"
    env.clock.now = T0.replace(hour=0, minute=0) + timedelta(days=1)  # next UTC day
    assert env.engine.submit(make_intent(4, 1_000)).decision.decision == "ALLOW"


def t10_concurrent_intents_cannot_both_pass(env):
    results = []

    def go(n):
        results.append(env.engine.submit(make_intent(n, 40_000)).decision.decision)
    threads = [threading.Thread(target=go, args=(n,)) for n in range(1, 9)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results.count("ALLOW") == 2 and env.adapter.calls == 2
    assert env.ledger.verify().ok


def t10_approved_payment_counts_toward_daily_limit(env):
    env.engine.submit(make_intent(1, 230_000))
    approve_pending(env, "I-1")
    res = env.engine.submit(make_intent(2, 40_000))  # 230k already reserved today
    assert res.decision.reason_code == "DAILY_LIMIT_EXCEEDED"
