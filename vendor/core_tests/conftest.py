import os, sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from approval_receipt import (Mandate, Intent, ApproverSession, Engine, Ledger, MockAdapter)  # noqa: E402

TEST_KEY = "test-key-for-pytest-only"
T0 = datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc)
SESSION = ApproverSession("approver-1", "sess-1")


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now


def make_mandate(**kw):
    base = dict(id="M-12", agent_id="agent-1", per_payment_limit=50_000, daily_limit=100_000,
                payee_allowlist=("acme",), purpose_codes=("office",),
                expires_at=datetime(2027, 1, 1, tzinfo=timezone.utc), currency="USD", version=1)
    return Mandate(**{**base, **kw})


def make_intent(n=1, amount=42_000, payee="acme", reason="routine", key=None, purpose="office"):
    return Intent(f"I-{n}", "agent-1", payee, amount, "USD", purpose, reason,
                  key or f"key-{n}", T0)


@dataclass
class Env:
    engine: Engine
    adapter: MockAdapter
    ledger: Ledger
    clock: Clock
    path: str

    def rebuild(self, mandate=None):
        """New Engine on the same file and adapter, as after a process restart."""
        self.engine = Engine([mandate or make_mandate()], Ledger(self.path, TEST_KEY),
                             self.adapter, clock=self.clock)
        return self.engine


@pytest.fixture
def env(tmp_path):
    path = str(tmp_path / "receipts.jsonl")
    adapter, clock = MockAdapter(), Clock()
    ledger = Ledger(path, TEST_KEY)
    return Env(Engine([make_mandate()], ledger, adapter, clock=clock), adapter, ledger, clock, path)


def approve_pending(env, intent_id, session=SESSION, note="ok"):
    p = env.engine.ledger.latest_for_intent(intent_id)
    return env.engine.approve(intent_id, session, p["approval_nonce"], p["intent_digest"], note)
