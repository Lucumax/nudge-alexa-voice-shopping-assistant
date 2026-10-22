import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.service import AGENT, HUMAN, NudgeService  # noqa: E402,F401


@pytest.fixture
def svc(tmp_path):
    s = NudgeService(str(tmp_path / "receipts.jsonl"), key="pytest-key", profile="demo")
    s.base_calls = s.adapter.calls  # the demo profile seeds two real orders
    yield s


@pytest.fixture
def fresh(tmp_path):
    s = NudgeService(str(tmp_path / "receipts.jsonl"), key="pytest-key", profile="fresh")
    s.base_calls = 0
    yield s


def last_assistant(s):
    return [m["text"] for m in s.transcript if m["who"] == "assistant"][-1]


def current_proposal(s):
    if s.session:
        return s.session["proposal_id"]
    return s.confirm.get("proposal_id") if s.confirm else None


def say(s, text, proposal=...):
    """What the page does: type text as the person, naming the proposal currently shown."""
    return s.handle_voice(text, current_proposal(s) if proposal is ... else proposal, HUMAN)


def code(s, value, proposal=...):
    return s.submit_code(value, current_proposal(s) if proposal is ... else proposal, HUMAN)


def tier(s, c, t, earned=True):
    """Propose a tier change and confirm it as the person. earned=True fills in the three approvals a
    test is not about, so the rule under test can be exercised directly."""
    if earned and t == "limits":
        s.cats[c]["streak"] = max(s.cats[c]["streak"], 3)
    r = s.set_tier(c, t, HUMAN)
    if r.get("needs_confirmation"):
        r = s.confirm_autonomy(r["proposal_id"], HUMAN)
    return r


def limit(s, c, minor, daily=False):
    kw = {"day_minor": minor} if daily else {"item_minor": minor}
    r = s.set_limits(c, actor=HUMAN, **kw)
    if r.get("needs_confirmation"):
        r = s.confirm_autonomy(r["proposal_id"], HUMAN)
    return r


def buy_by_voice(s, pid, source="shop"):
    """Tap a product, say yes, and (if a code is asked for) enter it from the simulated phone."""
    s.shop(pid, source)
    say(s, "yes")
    if s.session and s.session["stage"] == "stepup":
        code(s, s.session["code"])
    oid = max(s.orders)
    return oid
