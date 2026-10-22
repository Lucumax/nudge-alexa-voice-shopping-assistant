"""Core hardening v2: tri-state lookup, recovery, integrity gate, cross-process lock, expiry."""
import json, logging, os, subprocess, sys, textwrap
from datetime import datetime, timedelta, timezone

import pytest

from approval_receipt import (Engine, Ledger, MockAdapter, SimulatedCrash, IdempotencyConflict,
                              LookupResult, LedgerIntegrityError, ApprovalError)
from conftest import TEST_KEY, T0, make_intent, make_mandate, Clock

ROOT = os.path.join(os.path.dirname(__file__), "..")
LATE = make_mandate(expires_at=datetime(2030, 1, 1, tzinfo=timezone.utc))


# ---- (1) tri-state lookup ------------------------------------------------
def t20_lookup_exception_never_permits_redispatch(env):
    env.adapter.timeout_before_accept = True
    env.engine.submit(make_intent(1))

    def boom(key):
        raise ConnectionError("down")
    env.adapter.lookup = boom
    for _ in range(3):
        assert env.engine.retry("I-1")["outcome"] == "UNKNOWN"
    assert env.adapter.calls == 1


def t20_legacy_none_lookup_is_indeterminate(env):
    env.adapter.timeout_before_accept = True
    env.engine.submit(make_intent(1))
    env.adapter.lookup = lambda key: None  # legacy adapter: None is not proof of absence
    assert env.engine.retry("I-1")["outcome"] == "UNKNOWN" and env.adapter.calls == 1


def t20_indeterminate_result_never_permits_redispatch(env):
    env.adapter.timeout_before_accept = True
    env.engine.submit(make_intent(1))
    env.adapter.lookup = lambda key: LookupResult.indeterminate()
    assert env.engine.retry("I-1")["outcome"] == "UNKNOWN" and env.adapter.calls == 1


def t20_legacy_string_lookup_means_found(env):
    env.adapter.timeout_after_accept = True
    env.engine.submit(make_intent(1))
    env.adapter.lookup = lambda key: env.adapter.accepted.get(key)
    assert env.engine.retry("I-1")["outcome"] == "SUCCEEDED" and env.adapter.calls == 1


def t20_definitive_absent_redispatches_once_same_key(env):
    env.adapter.timeout_before_accept = True
    env.engine.submit(make_intent(1))
    rec = env.engine.retry("I-1")
    assert rec["outcome"] == "SUCCEEDED" and env.adapter.calls == 2
    assert list(env.adapter.accepted) == ["key-1"]


def t20_generic_dispatch_exception_is_unknown(env):
    def boom(*a):
        raise ValueError("malformed json after provider accepted")
    env.adapter.pay = boom
    assert env.engine.submit(make_intent(1)).receipt["outcome"] == "UNKNOWN"


def t20_adapter_marked_definitive_failure_is_failed(env):
    class Nope(Exception):
        definitive_failure = True

    def boom(*a):
        raise Nope()
    env.adapter.pay = boom
    assert env.engine.submit(make_intent(1)).receipt["outcome"] == "FAILED"


def t20_unknown_keeps_reserving_the_daily_limit(env):
    def boom(*a):
        raise ValueError("x")
    env.adapter.pay = boom
    env.engine.submit(make_intent(1, 50_000))
    env.engine.submit(make_intent(2, 50_000))
    assert env.engine.submit(make_intent(3, 1_000)).decision.reason_code == "DAILY_LIMIT_EXCEEDED"


# ---- (2) crash recovery ----------------------------------------------------
class CrashOnAppend:
    """Makes the append of a given outcome raise SimulatedCrash (the process dies)."""
    def __init__(self, ledger, outcome):
        self.orig, self.outcome = ledger.append, outcome
        ledger.append = self

    def __call__(self, body):
        if body.get("outcome") == self.outcome:
            raise SimulatedCrash()
        return self.orig(body)


def t21_recover_crash_before_submitting_executes_exactly_once(env):
    CrashOnAppend(env.ledger, "SUBMITTING")
    with pytest.raises(SimulatedCrash):
        env.engine.submit(make_intent(1))
    assert env.ledger.latest_for_intent("I-1")["outcome"] == "AUTHORIZED" and env.adapter.calls == 0
    eng = env.rebuild()
    assert [r["outcome"] for r in eng.recover()] == ["SUCCEEDED"] and env.adapter.calls == 1
    eng.recover()
    eng.recover()
    assert env.adapter.calls == 1 and list(env.adapter.accepted) == ["key-1"]
    assert env.ledger.verify().ok


def t21_recover_crash_after_submitting_never_blind_redispatches(env):
    def die(*a):
        raise SimulatedCrash()
    env.adapter.pay = die
    with pytest.raises(SimulatedCrash):
        env.engine.submit(make_intent(1))
    del env.adapter.pay
    eng = env.rebuild()
    out = eng.recover()  # reconcile only: provider definitively has nothing, so UNKNOWN
    assert out[0]["outcome"] == "UNKNOWN" and env.adapter.calls == 0
    assert eng.retry("I-1")["outcome"] == "SUCCEEDED" and env.adapter.calls == 1


def t21_recover_crash_after_provider_acceptance_no_second_pay(env):
    env.adapter.crash_after_accept = True
    with pytest.raises(SimulatedCrash):
        env.engine.submit(make_intent(1))
    out = env.rebuild().recover()
    assert out[0]["outcome"] == "SUCCEEDED" and env.adapter.calls == 1


def t21_recover_rechecks_policy(env):
    CrashOnAppend(env.ledger, "SUBMITTING")
    with pytest.raises(SimulatedCrash):
        env.engine.submit(make_intent(1))
    env.clock.now = datetime(2027, 2, 1, tzinfo=timezone.utc)  # mandate expired
    out = env.rebuild().recover()
    assert out[0]["outcome"] == "BLOCKED" and env.adapter.calls == 0


def t21_recover_refuses_on_tampered_ledger(env):
    CrashOnAppend(env.ledger, "SUBMITTING")
    with pytest.raises(SimulatedCrash):
        env.engine.submit(make_intent(1))
    _tamper(env.path, 1, "outcome", "FAILED")
    with pytest.raises(ApprovalError):
        env.rebuild().recover()
    assert env.adapter.calls == 0


# ---- (5) expiry ------------------------------------------------------------
def t25_authorization_carries_execution_validity(env):
    rec = env.engine.submit(make_intent(1)).receipt
    assert env.ledger.receipts()[0]["execute_by"] is not None
    assert rec["execute_by"] == env.ledger.receipts()[0]["execute_by"]


def t25_stale_authorized_is_expired_not_dispatched(env):
    CrashOnAppend(env.ledger, "SUBMITTING")
    with pytest.raises(SimulatedCrash):
        env.engine.submit(make_intent(1))
    env.clock.now = T0 + timedelta(days=30)
    out = env.rebuild(LATE).recover()
    assert out[0]["outcome"] == "EXPIRED" and env.adapter.calls == 0


def t25_retry_after_validity_does_not_redispatch(env):
    env.adapter.timeout_before_accept = True
    env.engine.submit(make_intent(1))
    env.clock.now = T0 + timedelta(days=30)
    assert env.rebuild(LATE).retry("I-1")["outcome"] == "EXPIRED" and env.adapter.calls == 1


# ---- (3) integrity gate ----------------------------------------------------
def _tamper(path, line_no, field, value):
    lines = open(path).read().splitlines()
    rec = json.loads(lines[line_no])
    rec[field] = value
    lines[line_no] = json.dumps(rec)
    open(path, "w").write("\n".join(lines) + "\n")


def t30_read_verifies_signatures(env):
    env.engine.submit(make_intent(1))
    _tamper(env.path, 1, "outcome", "FAILED")
    with pytest.raises(LedgerIntegrityError) as e:
        env.ledger.records()
    assert e.value.code == "LEDGER_INTEGRITY_FAILED"
    with pytest.raises(LedgerIntegrityError):
        env.ledger.latest_for_intent("I-1")


def t30_append_refuses_on_broken_chain(env):
    env.engine.submit(make_intent(1))
    _tamper(env.path, 1, "outcome", "FAILED")
    with pytest.raises(LedgerIntegrityError):
        env.ledger.append({"event": "x"})


def t30_engine_fails_closed_before_any_side_effect(env):
    env.engine.submit(make_intent(1))
    _tamper(env.path, 2, "payment_ref", "evil")
    with pytest.raises(ApprovalError) as e:
        env.engine.submit(make_intent(2))
    assert e.value.code == "LEDGER_INTEGRITY_FAILED" and env.adapter.calls == 1


def t30_truncation_caught_by_trusted_checkpoint_before_authorizing(env):
    env.engine.submit(make_intent(1))
    env.engine.submit(make_intent(2))
    lines = open(env.path).read().splitlines()
    open(env.path, "w").write("\n".join(lines[:-3]) + "\n")  # drop intent 2 receipts
    with pytest.raises(ApprovalError) as e:
        env.rebuild().submit(make_intent(3))
    assert e.value.code == "LEDGER_INTEGRITY_FAILED" and env.adapter.calls == 2


def t30_no_key_refuses_to_start(monkeypatch, tmp_path):
    monkeypatch.delenv("APPROVAL_RECEIPT_HMAC_KEY", raising=False)
    with pytest.raises(RuntimeError):
        Ledger(str(tmp_path / "l.jsonl"))


def t30_demo_key_is_explicit_loud_and_marked(monkeypatch, tmp_path, caplog):
    monkeypatch.delenv("APPROVAL_RECEIPT_HMAC_KEY", raising=False)
    with caplog.at_level(logging.WARNING):
        led = Ledger(str(tmp_path / "l.jsonl"), allow_demo_key=True)
    assert "DEMO" in caplog.text
    assert all(r["key_mode"] == "DEMO" for r in led.records())


def t30_checkpoint_path_configurable(tmp_path):
    cp = tmp_path / "elsewhere" / "cp.json"
    led = Ledger(str(tmp_path / "l.jsonl"), TEST_KEY, checkpoint_path=str(cp))
    led.append({"event": "x"})
    assert cp.exists() and not (tmp_path / "l.jsonl.checkpoint.json").exists()
    assert json.load(open(cp))["seq"] == 1


def t30_checkpoint_rollback_detected(env):
    env.engine.submit(make_intent(1))
    old = open(env.ledger.checkpoint_path).read()
    env.engine.submit(make_intent(2))
    env.engine.submit(make_intent(3))
    open(env.ledger.checkpoint_path, "w").write(old)  # attacker restores an older signed checkpoint
    assert env.ledger.verify_trusted().reason_code == "CHECKPOINT_ROLLBACK"
    with pytest.raises(ApprovalError) as e:
        env.engine.submit(make_intent(4))
    assert e.value.code == "LEDGER_INTEGRITY_FAILED"


def t30_saving_an_older_checkpoint_is_refused(env):
    env.engine.submit(make_intent(1))
    old = env.ledger.checkpoint()
    env.engine.submit(make_intent(2))
    with pytest.raises(LedgerIntegrityError):
        env.ledger.save_checkpoint(old)


def t30_missing_checkpoint_on_existing_ledger_fails_closed(env):
    env.engine.submit(make_intent(1))
    os.remove(env.ledger.checkpoint_path)
    with pytest.raises(ApprovalError) as e:
        env.rebuild().submit(make_intent(2))
    assert e.value.code == "LEDGER_INTEGRITY_FAILED"


# ---- (4) cross-process -----------------------------------------------------
WORKER = textwrap.dedent("""
    import sys
    sys.path.insert(0, {root!r})
    sys.path.insert(0, {tests!r})
    from conftest import make_mandate, make_intent, Clock, TEST_KEY
    from approval_receipt import Engine, Ledger, MockAdapter
    p, path = sys.argv[1], sys.argv[2]
    eng = Engine([make_mandate()], Ledger(path, TEST_KEY), MockAdapter(), clock=Clock())
    for n in range(8):
        i = make_intent(n, 40_000, key="P%s-%d" % (p, n))
        object.__setattr__(i, "id", "P%s-%d" % (p, n))
        eng.submit(i)
""")


def t40_two_processes_cannot_double_spend_or_corrupt_chain(tmp_path):
    path = str(tmp_path / "shared.jsonl")
    Ledger(path, TEST_KEY)
    script = tmp_path / "w.py"
    script.write_text(WORKER.format(root=os.path.abspath(ROOT),
                                    tests=os.path.abspath(os.path.dirname(__file__))))
    procs = [subprocess.Popen([sys.executable, str(script), str(p), path],
                              stderr=subprocess.PIPE) for p in (1, 2)]
    errs = [p.communicate(timeout=120)[1].decode() for p in procs]
    assert [p.returncode for p in procs] == [0, 0], errs
    led = Ledger(path, TEST_KEY)
    assert led.verify_trusted().ok
    assert len([r for r in led.receipts() if r["outcome"] == "AUTHORIZED"]) == 2  # 100k / 40k
    ids = [r["id"] for r in led.records()]
    assert len(ids) == len(set(ids))


def t40_two_engines_one_process_share_the_limit(tmp_path):
    path = str(tmp_path / "s.jsonl")
    e1 = Engine([make_mandate()], Ledger(path, TEST_KEY), MockAdapter(), clock=Clock())
    e2 = Engine([make_mandate()], Ledger(path, TEST_KEY), MockAdapter(), clock=Clock())
    d = [e.submit(make_intent(n, 40_000)).decision.decision for n, e in ((1, e1), (2, e2), (3, e1))]
    assert d == ["ALLOW", "ALLOW", "ESCALATE"]


# ---- (6) other core bugs ---------------------------------------------------
def t60_intent_id_reused_with_a_different_key_is_rejected(env):
    env.engine.submit(make_intent(1, key="A"))
    with pytest.raises(IdempotencyConflict):
        env.engine.submit(make_intent(1, 100, key="B"))
    assert env.adapter.calls == 1


def t60_naive_mandate_expiry_rejected():
    with pytest.raises(ValueError):
        make_mandate(expires_at=datetime(2027, 1, 1))


def t60_duplicate_mandates_for_one_agent_rejected(tmp_path):
    with pytest.raises(ValueError):
        Engine([make_mandate(), make_mandate(version=2)], Ledger(str(tmp_path / "l"), TEST_KEY),
               MockAdapter())


def t60_version_and_manifest_match():
    import approval_receipt
    from approval_receipt.manifest import verify_manifest
    assert approval_receipt.__version__ == open(os.path.join(ROOT, "VERSION")).read().strip()
    ok, problems = verify_manifest(ROOT)
    assert ok, problems
