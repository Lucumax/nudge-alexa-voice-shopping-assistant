"""Nudge service: all product logic, over the approval-receipt core.

Every purchase goes through the core: policy check, engine, mock adapter, signed receipt chain.
The app adds the shopping layer: autonomy tiers, suggestions, the voice approval conversation,
step-up, graduation, undo, digest. Nothing here talks to a network. The payment is a mock.

Tier mapping onto core mandates (one core "agent" per category and mode):
  Suggest / Ask first : mandate nudge-<cat>-ask, per payment limit 0, so every order escalates
                        to a human yes.
  Within limits       : mandate nudge-<cat>-auto, per item and per day limits, merchants chosen.
                        The app routes an order to the auto mandate only when the core's own pure
                        check() says ALLOW; otherwise it goes to the ask mandate.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import shutil
import tempfile
import threading
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta

from . import catalog as C
from . import texts as T
from .voice import Command, code_only, parse
from approval_receipt import (ALLOW, ApprovalError, ApproverSession, Engine, Intent, Ledger, Mandate,
                              MockAdapter, check)
from approval_receipt.engine import RESERVING, PENDING, SUCCEEDED, UNKNOWN, FAILED, INDETERMINATE

DEMO_KEY = "demo-only-key-not-a-secret"  # public, fixed; set APPROVAL_RECEIPT_HMAC_KEY to override
APPROVER_ID = "maya-demo"
FAR = datetime(2030, 1, 1, tzinfo=C.DEMO_TZ)
RANK = {"suggest": 0, "ask": 1, "limits": 2}
FAULTS = {"none", "timeout_after_accept", "timeout_before_accept", "reject_next"}
STEP_UP_TTL = timedelta(minutes=10)
MAX_CODE_TRIES = 3
RESTOCK_LEAD_DAYS = 3
REMINDER_LEAD_DAYS = 14
DISMISS_RUN_FOR_FEWER = 3
GRADUATION_APPROVALS = 3          # approvals in Ask first before Within limits can be proposed
PROPOSAL_TTL = timedelta(minutes=30)   # read-backs and autonomy proposals, on the demo clock
DAY_CAP_MINOR = 100_000           # $1,000 per day at most
HUMAN, AGENT = "human", "agent"   # principals; the server decides which one a request is
UNRESOLVED = ("UNKNOWN", "SUBMITTING", "AUTHORIZED")
STATE_VERSION = 2


class StateError(RuntimeError):
    """Saved state cannot be trusted or restored. Nothing is deleted; reset is an explicit command."""


class DemoAdapter(MockAdapter):
    """The core's mock adapter plus a simulated cancel, for the 24 hour undo window."""

    def __init__(self) -> None:
        super().__init__()
        self.cancelled: dict[str, str] = {}

    def cancel(self, idempotency_key: str) -> str | None:
        ref = self.accepted.get(idempotency_key)
        if ref is None:
            return None
        return self.cancelled.setdefault(idempotency_key, "refund-" + ref[5:])

    def net_charges(self) -> int:
        return len(self.accepted) - len(self.cancelled)


@dataclass
class Order:
    id: str
    product_id: str
    category: str
    qty: int
    amount_minor: int
    source: str            # suggestion | shop | assistant
    agent_id: str
    placed_at: datetime
    delivery: date
    reason: str
    tier_at_order: str
    approved: bool = False
    channel: str = ""
    step_up: bool = False
    problem: str = ""
    auto: bool = False     # bought by the assistant inside a mandate
    terms: dict = field(default_factory=dict)       # immutable checkout snapshot, see _terms()
    terms_hash: str = ""

    @property
    def merchant(self) -> str:
        return C.BY_ID[self.product_id].merchant


def _cat_state() -> dict:
    return dict(enabled=False, tier="suggest", streak=0, item_minor=2500, day_minor=5000,
                merchants=[], paused=False, max_cards=2, dismiss_run=0, enabled_at=None,
                auto_version=0, auto_expires=None)


def _terms(pid: str, qty: int, price_minor: int, delivery: date) -> dict:
    """Everything a yes binds to: product, quantity, delivery promise, card ending, unit price, total."""
    return dict(product_id=pid, quantity=qty, delivery=delivery.isoformat(), card_last4=C.CARD_LAST4,
                unit_minor=price_minor, amount_minor=price_minor * qty)


def _terms_hash(oid: str, terms: dict) -> str:
    return hashlib.sha256(json.dumps([oid, terms], sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _enc(o):
    if isinstance(o, datetime):
        return {"$dt": o.isoformat()}
    if isinstance(o, date):
        return {"$d": o.isoformat()}
    raise TypeError(type(o))


def _dec(d: dict):
    if "$dt" in d:
        return datetime.fromisoformat(d["$dt"])
    if "$d" in d:
        return date.fromisoformat(d["$d"])
    return d


class NudgeService:
    """Authorization model. Every method that can raise autonomy, approve a payment or resume a pause
    takes an `actor`. The HTTP layer decides the actor from which token the request carried (the page's
    token is HUMAN, a host's token is AGENT); no argument a caller can send changes it. REST handlers and
    MCP tools call the same methods, so there is one authorization path."""

    def __init__(self, ledger_path: str | None = None, key: str | None = None,
                 profile: str = "demo") -> None:
        """Starting never deletes anything. If a ledger and a saved state exist they are restored (after
        verifying the ledger against its checkpoint). If only one of them exists, StateError is raised.
        reset() is the explicit, demo-only way to start over."""
        self.lock = threading.RLock()
        self.needs_restart = False   # set when a save failed after state may already have changed
        self._explicit_key = key  # None means: use APPROVAL_RECEIPT_HMAC_KEY if set, else the stamped demo key
        self._tmp = None
        if ledger_path is None:
            self._tmp = tempfile.mkdtemp(prefix="nudge-")
            ledger_path = os.path.join(self._tmp, "receipts.jsonl")
        self.ledger_path = ledger_path
        self.state_path = ledger_path + ".state.json"
        self.restored = False
        has_ledger, has_state = os.path.exists(ledger_path), os.path.exists(self.state_path)
        if has_ledger and has_state:
            self._restore()
            self.restored = True
        elif has_ledger or has_state:
            raise StateError("A receipt file or a saved state exists without the other. Nothing was deleted. "
                             "Restore both, or start over on purpose with the demo reset command.")
        else:
            self.reset(profile)

    # ---- setup ---------------------------------------------------------
    def reset(self, profile: str = "demo") -> None:
        """DEMO ONLY. Deletes the receipt file, checkpoint and saved state, then starts a fresh profile.
        Never called at startup. Not exposed as an MCP tool."""
        with self.lock:
            self._seeding = True
            for p in (self.ledger_path, self.ledger_path + ".checkpoint.json", self.state_path,
                      os.path.join(os.path.dirname(os.path.abspath(self.ledger_path)), "checkpoint",
                                   os.path.basename(self.ledger_path) + ".checkpoint.json")):
                if os.path.exists(p):
                    os.remove(p)
            self.now = C.DEMO_NOW
            self.adapter = DemoAdapter()
            self.ledger = self._open_ledger()
            self.ledger.save_checkpoint()
            self._build_engine()
            self.cats = {c: _cat_state() for c in C.CATEGORIES}
            self.offers: dict[str, dict] = {}
            self.props: dict[str, dict] = {}   # autonomy proposals waiting for a human
            # Counters are never rewound, so an id shown before a demo reset cannot match a new one after it.
            self.prop_no = getattr(self, "prop_no", 0)
            self.orders: dict[str, Order] = {}
            self.seq = 0
            self.sugg: dict[str, dict] = {}   # signal id -> {status, until}
            self.feed: list[dict] = []
            self.transcript: list[dict] = []
            self.phone: list[dict] = []
            self.session: dict | None = None
            self.confirm: dict | None = None
            self.paused_all = False
            self.checkpoint: dict | None = None
            self.profile = profile
            self.session_no = getattr(self, "session_no", 0)
            self.fault = "none"
            self.code_no = 0
            self.opened_at = self.now
            if profile == "demo":
                self._seed_demo()
            self._seeding = False
            self._cp()
            self._say("assistant", T.t("hello.demo" if profile == "demo" else "hello.fresh"))
            self._save()

    def _open_ledger(self) -> Ledger:
        """The checkpoint lives in its own folder beside the data, not next to the receipts. On one disk
        that is still weak protection; the README says so."""
        base = os.path.dirname(os.path.abspath(self.ledger_path))
        cp = os.path.join(base, "checkpoint", os.path.basename(self.ledger_path) + ".checkpoint.json")
        self.checkpoint_path = cp
        return Ledger(self.ledger_path, self._explicit_key, checkpoint_path=cp, allow_demo_key=self._explicit_key is None
                      and not os.environ.get("APPROVAL_RECEIPT_HMAC_KEY"))

    def _build_engine(self) -> None:
        asks = [self._ask_mandate(c) for c in C.CATEGORIES]
        self.engine = Engine(asks, self.ledger, self.adapter, clock=lambda: self.now, day_tz=C.DEMO_TZ)

    # ---- persistence -------------------------------------------------------
    def _save(self) -> None:
        a = self.adapter
        snap = dict(version=STATE_VERSION, now=self.now, cats=self.cats, offers=self.offers, props=self.props,
                    prop_no=self.prop_no, orders={k: asdict(o) for k, o in self.orders.items()}, seq=self.seq,
                    sugg=self.sugg, feed=self.feed, transcript=self.transcript, phone=self.phone,
                    session=self.session, confirm=self.confirm, paused_all=self.paused_all,
                    profile=self.profile, session_no=self.session_no, fault=self.fault, code_no=self.code_no,
                    opened_at=self.opened_at,
                    adapter=dict(calls=a.calls, lookups=a.lookups, accepted=a.accepted, cancelled=a.cancelled,
                                 flags={f: getattr(a, f) for f in FAULTS - {"none"}}),
                    checkpoint=self.checkpoint)
        tmp = self.state_path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(snap, f, default=_enc)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.state_path)
        except OSError:
            self.storage_failed()
            raise

    def storage_failed(self) -> None:
        """A save or receipt write failed after the in-memory state may already have changed. From here the
        service refuses further actions until it is restarted, so it never carries on from a state that
        is not on disk. The restart reads the saved state and checks it against the verified receipts."""
        self.needs_restart = True

    def _restore(self) -> None:
        try:
            with open(self.state_path, encoding="utf-8") as f:
                d = json.load(f, object_hook=_dec)
            if not isinstance(d, dict) or d.get("version") != STATE_VERSION:
                raise StateError("Saved state has an unknown version.")
        except (ValueError, OSError) as e:
            raise StateError("Saved state could not be read: " + str(e)) from e
        if not isinstance(d, dict):
            raise StateError("Saved state could not be read: it is not a state file.")
        try:
            self._restore_from(d)
        except (KeyError, TypeError, AttributeError) as e:
            raise StateError("Saved state is incomplete or damaged (" + type(e).__name__ + " " + str(e)[:80]
                             + "). Nothing was changed or deleted.") from e

    def _restore_from(self, d: dict) -> None:
        self.adapter = DemoAdapter()
        ad = d["adapter"]
        self.adapter.calls, self.adapter.lookups = ad["calls"], ad["lookups"]
        self.adapter.accepted, self.adapter.cancelled = ad["accepted"], ad["cancelled"]
        self.fault = d["fault"]
        for f, on in ad["flags"].items():       # one-shot switches come back exactly as they were
            setattr(self.adapter, f, on)
        try:
            self.ledger = self._open_ledger()
        except RuntimeError as e:
            raise StateError(f"The receipt file failed verification at startup ({e}). "
                             "Nothing was changed or deleted.") from e
        self.checkpoint = d["checkpoint"]
        res = self.ledger.verify_trusted()
        if not res.ok:
            raise StateError(f"The receipt file failed verification at startup ({res.reason_code} at "
                             f"{res.first_broken_id}). Nothing was changed or deleted.")
        self.now, self.cats, self.offers, self.props = d["now"], d["cats"], d["offers"], d["props"]
        self.prop_no, self.seq, self.sugg = d["prop_no"], d["seq"], d["sugg"]
        self.orders = {k: Order(**v) for k, v in d["orders"].items()}
        self._check_state_matches_receipts()      # before anything is written: refuse, change nothing
        self.feed, self.transcript, self.phone = d["feed"], d["transcript"], d["phone"]
        self.session, self.confirm, self.paused_all = d["session"], d["confirm"], d["paused_all"]
        self.profile, self.session_no, self.code_no, self.opened_at = (d["profile"], d["session_no"],
                                                                      d["code_no"], d["opened_at"])
        self._seeding = False
        self._build_engine()
        for c in C.CATEGORIES:
            if self.cats[c]["tier"] == "limits":
                self._install_auto(c, bump=False)
        recovered = self.engine.recover()   # finish or reconcile anything a crash left half done
        for rec in recovered:
            o = self.orders.get(rec["intent"]["id"])
            if o is not None:
                self._log("reconcile", T.t("log.recovered", item=self._item_text(o), status=T.t("status." + rec["outcome"])))
        # A crash between a receipt write and a state write must never reuse an order number.
        nums = [int(m.group(1)) for r in self.ledger.receipts()
                if (m := re.fullmatch(r"O-(\d+)", r["intent"]["id"]))]
        self.seq = max([self.seq] + nums)
        if recovered:       # recovery appended receipts: save the state and checkpoint that match them now
            self._cp()

    def _order_fields_ok(self, o: Order) -> bool:
        """The saved order and its bound checkout snapshot must say the same thing: product, quantity,
        unit price, total, delivery and the hash that covers them."""
        t = o.terms
        try:
            return (isinstance(o.qty, int) and o.qty >= 1 and t["product_id"] == o.product_id
                    and t["quantity"] == o.qty and t["amount_minor"] == o.amount_minor
                    and t["unit_minor"] * o.qty == o.amount_minor
                    and t["delivery"] == o.delivery.isoformat()
                    and o.terms_hash == _terms_hash(o.id, t))
        except (KeyError, TypeError, AttributeError):
            return False

    def _check_state_matches_receipts(self) -> None:
        """The verified receipts are the truth. If the saved state does not agree with them (a crash between a
        receipt write and a state write, or an older state file put back), stop and say so; do not guess."""
        recs = self.ledger.receipts()
        latest: dict[str, dict] = {}
        for r in recs:
            latest[r["intent"]["id"]] = r
        problems = []
        if recs and (self.checkpoint or {}).get("seq") != recs[-1]["seq"]:
            problems.append("the saved state is not at the same point as the receipts")
        if set(latest) != set(self.orders):
            problems.append("the orders and the receipts do not list the same purchases")
        for iid, r in latest.items():
            key = r["intent"]["idempotency_key"]
            o = self.orders.get(iid)
            if o is not None:
                it = r["intent"]
                if not self._order_fields_ok(o):
                    problems.append(f"{iid} has a quantity, amount or terms snapshot that disagree with each other")
                elif (it["amount_minor"] != o.amount_minor or it["payee"] != o.merchant
                        or it["purpose_code"] != o.category or key != self._idem(o)):
                    problems.append(f"{iid} has an amount, store or category that differs from its receipt")
            if r["outcome"] == SUCCEEDED and key not in self.adapter.accepted:
                problems.append(f"{iid} has a completed receipt but the store has no matching payment")
            if r["outcome"] == "CANCELLED" and key not in self.adapter.cancelled:
                problems.append(f"{iid} has a cancelled receipt but the store has no matching refund")
        if problems:
            raise StateError("The saved state does not match the verified receipts (" + "; ".join(problems[:3])
                             + "). Nothing was changed or deleted. Restore the matching state file, or start over "
                             "on purpose with the demo reset command.")

    def _ask_mandate(self, c: str) -> Mandate:
        return Mandate(f"M-{c}-ask", f"nudge-{c}-ask", 0, 10**9, C.merchants_for(c), (c,), FAR, "USD", 1)

    def _seed_demo(self) -> None:
        """Maya's earlier week, produced through the real engine so the receipts are real.
        Two approved grocery orders on Oct 14 and 15, so one more approval offers graduation."""
        self.now = C.DEMO_NOW - timedelta(days=7)
        for c in C.CATEGORIES:
            self.cats[c].update(enabled=True, enabled_at=self.now.date())
        self.cats["groceries"]["tier"] = "ask"
        for n, pid in enumerate(("olive-oil", "oat-milk")):
            self.now = C.DEMO_NOW - timedelta(days=7 - n)
            o = self._propose(pid, "shop")
            self._approve_ids([o.id], "voice", False, quiet=True)
        self.now = C.DEMO_NOW
        self.sugg.clear()   # seeded orders were history; today's ideas start fresh
        self.feed.clear()
        self.transcript.clear()

    # ---- small helpers ---------------------------------------------------
    def _cp(self) -> None:
        self.checkpoint = self.ledger.checkpoint()   # the ledger saves its own trusted checkpoint after each append
        if hasattr(self, "state_path") and not getattr(self, "_seeding", False):
            self._save()

    def _log(self, kind: str, text: str) -> None:
        self.feed.insert(0, dict(kind=kind, text=text, at=self.now.isoformat(),
                                 when=T.t("when.fmt", date=T.fdate(self.now.date()),
                                          time=f"{self.now:%H:%M}")))

    def _say(self, who: str, text: str) -> None:
        last = self.transcript[-1].get("id", len(self.transcript)) if self.transcript else 0
        self.transcript.append(dict(who=who, text=text, id=last + 1))   # ids let the page tell new from old

    def _ok(self, **kw) -> dict:
        self._cp()
        return {"ok": True, **kw}

    def _err(self, code: str, **kw) -> dict:
        self._cp()          # error paths may have dropped a proposal or ended a session; keep that on disk
        return {"ok": False, "code": code, "message": T.t("err." + code, **kw)}

    def cat_label(self, c: str) -> str:
        return T.t("cat." + c)

    def _latest(self, oid: str) -> dict:
        return self.ledger.latest_for_intent(oid)

    def _status(self, oid: str) -> str:
        return self._latest(oid)["outcome"]

    def _auto_agent(self, c: str) -> str:
        return f"nudge-{c}-auto"

    def _item_text(self, o: Order) -> str:
        return C.BY_ID[o.product_id].name

    def _item_qty_text(self, o: Order) -> str:
        """The item as the read-back says it: the quantity is spoken whenever it is more than one."""
        return T.t("readback.qty", qty=o.qty, item=self._item_text(o)) if o.qty > 1 else self._item_text(o)

    def _paused(self, c: str) -> bool:
        return self.paused_all or self.cats[c]["paused"]

    # ---- mandates and tiers ------------------------------------------------
    def _need_human(self, actor: str):
        """The single authorization check. Returns an error result, or None when the actor is a person."""
        return None if actor == HUMAN else self._err("NOT_AUTHORIZED")

    def _install_auto(self, c: str, bump: bool = True) -> None:
        cs = self.cats[c]
        if bump:
            cs["auto_version"] += 1
        if cs["auto_expires"] is None:
            cs["auto_expires"] = self.now + timedelta(days=90)
        exp = cs["auto_expires"] if not self._paused(c) else self.now - timedelta(seconds=1)
        item = min(cs["item_minor"], C.AUTONOMY_CAP_MINOR, cs["day_minor"])
        # The daily limit is enforced exactly as stated: it is never raised to fit the item limit.
        self.engine.mandates[self._auto_agent(c)] = Mandate(
            f"M-{c}-auto", self._auto_agent(c), item, cs["day_minor"],
            tuple(cs["merchants"]), (c,), exp, "USD", cs["auto_version"])

    def _drop_auto(self, c: str) -> None:
        self.engine.mandates.pop(self._auto_agent(c), None)
        self.cats[c]["auto_expires"] = None

    def known_merchants(self, c: str) -> list[str]:
        seen = []
        for o in self.orders.values():
            if o.category == c and o.approved and o.merchant not in seen:
                seen.append(o.merchant)
        return seen

    def _names(self, merchants) -> str:
        return " and ".join(C.merchant_name(m) for m in merchants)

    def mandate_sentence(self, c: str) -> str:
        cs, name = self.cats[c], self.cat_label(c)
        if self._paused(c):
            return T.t("mandate.paused", cat=name)
        if cs["tier"] == "suggest":
            return T.t("mandate.suggest", cat=name)
        if cs["tier"] == "ask":
            return T.t("mandate.ask", cat=name)
        return T.t("mandate.limits", cat=name, item=T.money(min(cs["item_minor"], C.AUTONOMY_CAP_MINOR)),
                   day=T.money(cs["day_minor"]), merchants=self._names(cs["merchants"]),
                   until=T.fdate_long(cs["auto_expires"].date()), cap=T.money(C.STEP_UP_MINOR))

    def set_enabled(self, c: str, on: bool, actor: str = AGENT) -> dict:
        with self.lock:
            if (bad := self._need_human(actor)):
                return bad
            cs = self.cats[c]
            cs["enabled"] = bool(on)
            cs["enabled_at"] = self.now.date() if on else None
            self._log("rules", T.t("log.enabled" if on else "log.disabled", cat=self.cat_label(c)))
            return self._ok()

    # -- autonomy: lowering applies at once; raising is a proposal that a person must confirm
    def _open_proposal(self, c: str, tier: str, item: int, day: int, origin: str, note: str = "",
                       voice: bool = False) -> dict:
        cs = self.cats[c]
        for pid in [k for k, v in self.props.items() if v["category"] == c]:
            self.props.pop(pid)                       # one open proposal per category
        self.prop_no += 1
        pid = f"A-{self.prop_no:03d}"
        merchants = list(cs["merchants"] or self.known_merchants(c)) if tier == "limits" else []
        if tier == "limits":
            if cs["tier"] == "limits":
                text = T.t("proposal.limit_change", cat=self.cat_label(c), item=T.money(item), day=T.money(day))
            else:
                until = (cs["auto_expires"] or self.now + timedelta(days=90)).date()
                text = T.t("proposal.limits", cat=self.cat_label(c), item=T.money(item), day=T.money(day),
                           merchants=self._names(merchants), until=T.fdate_long(until))
        else:
            text = T.t("proposal." + tier, cat=self.cat_label(c))
        self.props[pid] = dict(id=pid, category=c, tier=tier, item_minor=item, day_minor=day,
                               merchants=merchants, from_tier=cs["tier"], from_item=cs["item_minor"],
                               from_day=cs["day_minor"], origin=origin, text=text,
                               created=self.now, expires=self.now + PROPOSAL_TTL)
        if voice:
            self.confirm = dict(type="autonomy", proposal_id=pid)
            self._say("assistant", text + " " + T.t("proposal.say_yes") + (" " + note if note else ""))
        self._cp()          # a proposal or confirmation the person can see must survive a restart
        return dict(ok=True, needs_confirmation=True, proposal_id=pid, message=(note + " " if note else "") + text)

    def propose_autonomy(self, c: str, tier: str | None = None, item_minor: int | None = None,
                         day_minor: int | None = None, actor: str = AGENT, voice: bool = False,
                         origin: str = "request") -> dict:
        """Ask for a change. A change that lowers or keeps authority applies now. A change that raises it
        only opens a proposal; confirm_autonomy (human only) applies it."""
        with self.lock:
            if c not in self.cats or (tier is not None and tier not in RANK):
                return self._err("BAD_REQUEST")
            for v in (item_minor, day_minor):
                if v is not None and (type(v) is not int or v <= 0):
                    return self._err("BAD_REQUEST")
            cs = self.cats[c]
            target = tier or cs["tier"]
            item = cs["item_minor"] if item_minor is None else item_minor
            day = cs["day_minor"] if day_minor is None else day_minor
            note = ""
            if item > C.AUTONOMY_CAP_MINOR:
                item, note = C.AUTONOMY_CAP_MINOR, T.t("limit.clamped", cap=T.money(C.AUTONOMY_CAP_MINOR),
                                                       step=T.money(C.STEP_UP_MINOR))
            if day > DAY_CAP_MINOR:
                return self._err("DAY_TOO_HIGH", cap=T.money(DAY_CAP_MINOR))
            if day < item:
                return self._err("DAY_BELOW_ITEM", item=T.money(item), day=T.money(day))
            same_limits = (item, day) == (cs["item_minor"], cs["day_minor"])
            if target == cs["tier"] and same_limits:
                return self._ok(message=T.t("rules.same", cat=self.cat_label(c)))
            raising = (RANK[target] > RANK[cs["tier"]]
                       or (target == "limits" == cs["tier"] and (item > cs["item_minor"] or day > cs["day_minor"])))
            if target == "limits" and cs["tier"] != "limits":
                if cs["streak"] < GRADUATION_APPROVALS:
                    return self._err("NEED_APPROVALS", need=GRADUATION_APPROVALS, have=cs["streak"],
                                     cat=self.cat_label(c))
                if not self.known_merchants(c):
                    return self._err("NEED_HISTORY", cat=self.cat_label(c))
            if raising:
                return self._open_proposal(c, target, item, day, origin, note, voice)
            old = cs["tier"]
            cs["item_minor"], cs["day_minor"] = item, day
            self._drop_open_proposals(c)              # a newer change supersedes any older proposal
            if target != old:
                cs["tier"], cs["streak"] = target, 0
                self.offers.pop(c, None)
                self._drop_auto(c)
                self._log("rules", T.t("log.tier", cat=self.cat_label(c), old=T.t("tier." + old),
                                       new=T.t("tier." + target)))
            if cs["tier"] == "limits":
                self._install_auto(c)
            if not same_limits:
                self._log("rules", T.t("log.limits_set", cat=self.cat_label(c), item=T.money(item), day=T.money(day)))
            base = (self.mandate_sentence(c) if cs["tier"] == "limits" else
                    T.t("limit.stored", cat=self.cat_label(c), item=T.money(item), day=T.money(day)))
            return self._ok(message=(note + " " if note else "") + base)

    def set_tier(self, c: str, tier: str, actor: str = AGENT, voice: bool = False) -> dict:
        return self.propose_autonomy(c, tier=tier, actor=actor, voice=voice)

    def set_limits(self, c: str, item_minor: int | None = None, day_minor: int | None = None,
                   actor: str = AGENT, voice: bool = False) -> dict:
        return self.propose_autonomy(c, item_minor=item_minor, day_minor=day_minor, actor=actor, voice=voice)

    def confirm_autonomy(self, proposal_id: str, actor: str = AGENT) -> dict:
        """The only way autonomy goes up. Human only, single use, and it applies exactly the terms that
        were shown when the proposal was opened. The three-approval rule is re-checked here."""
        with self.lock:
            if (bad := self._need_human(actor)):
                return bad
            pr = self.props.get(proposal_id)
            if pr is None:
                return self._err("NO_PROPOSAL")
            c, cs = pr["category"], self.cats[pr["category"]]
            if self.now >= pr["expires"]:
                self._drop_proposal(pr)
                return self._err("PROPOSAL_EXPIRED")
            # The proposal was shown against one exact base (tier and both limits). Anything that changed
            # since, including a lowered limit, makes it stale; it never restores authority a person took away.
            if (cs["tier"], cs["item_minor"], cs["day_minor"]) != (
                    pr["from_tier"], pr.get("from_item", cs["item_minor"]), pr.get("from_day", cs["day_minor"])):
                self._drop_proposal(pr)
                return self._err("PROPOSAL_STALE")
            if pr["tier"] == "limits" and pr["from_tier"] != "limits":
                if cs["streak"] < GRADUATION_APPROVALS or any(m not in self.known_merchants(c) for m in pr["merchants"]):
                    self._drop_proposal(pr)
                    return self._err("PROPOSAL_STALE")
            self._drop_proposal(pr)
            old = cs["tier"]
            cs["item_minor"], cs["day_minor"] = pr["item_minor"], pr["day_minor"]
            if pr["tier"] != old:
                cs["tier"], cs["streak"] = pr["tier"], 0
                cs["merchants"] = list(pr["merchants"]) if pr["tier"] == "limits" else cs["merchants"]
                self._drop_auto(c)
                self._log("rules", T.t("log.tier", cat=self.cat_label(c), old=T.t("tier." + old),
                                       new=T.t("tier." + pr["tier"])))
            else:
                self._log("rules", T.t("log.limits_set", cat=self.cat_label(c), item=T.money(pr["item_minor"]),
                                       day=T.money(pr["day_minor"])))
            if cs["tier"] == "limits":
                self._install_auto(c)
            self.offers.pop(c, None)
            if self.confirm and self.confirm.get("proposal_id") == proposal_id:
                self.confirm = None
            msg = self.mandate_sentence(c)
            self._say("assistant", T.t("offer.accepted", cat=self.cat_label(c)))
            return self._ok(message=msg)

    def cancel_autonomy(self, proposal_id: str, actor: str = AGENT) -> dict:
        with self.lock:
            pr = self.props.get(proposal_id)
            if pr is None:
                return self._err("NO_PROPOSAL")
            self._drop_proposal(pr)
            return self._ok(message=T.t("cmd.ok_nothing"))

    def _drop_open_proposals(self, c: str) -> None:
        for pr in [v for v in self.props.values() if v["category"] == c]:
            self._drop_proposal(pr)

    def _drop_proposal(self, pr: dict) -> None:
        self.props.pop(pr["id"], None)
        if self.confirm and self.confirm.get("proposal_id") == pr["id"]:
            self.confirm = None
        off = self.offers.get(pr["category"])
        if off and off.get("proposal_id") == pr["id"]:
            self.offers.pop(pr["category"], None)

    def pause(self, c: str | None = None, on: bool = True, actor: str = AGENT) -> dict:
        """Pausing is always allowed. Resuming gives authority back, so only a person can do it."""
        with self.lock:
            if not on and (bad := self._need_human(actor)):
                return bad
            if c is None:
                self.paused_all = on
            else:
                self.cats[c]["paused"] = on
            for cat in C.CATEGORIES:
                if self.cats[cat]["tier"] == "limits":
                    self._install_auto(cat, bump=False)
            self._log("rules", T.t("log.pause" if on else "log.resume",
                                   scope=T.t("scope.all") if c is None else self.cat_label(c)))
            return self._ok()

    # ---- offers (earned autonomy) --------------------------------------------
    def _rule_end(self, c: str) -> str:
        """The date a new within-limits rule would end (same date the full proposal shows)."""
        return T.fdate_long((self.cats[c]["auto_expires"] or self.now + timedelta(days=90)).date())

    def _maybe_offer(self, c: str) -> None:
        cs = self.cats[c]
        if cs["streak"] < GRADUATION_APPROVALS or cs["tier"] == "limits" or c in self.offers:
            return
        if cs["tier"] == "suggest":
            pr = self._open_proposal(c, "ask", cs["item_minor"], cs["day_minor"], "offer")
            text = T.t("offer.ask", cat=self.cat_label(c))
            to = "ask"
        else:
            used = [o for o in self.orders.values() if o.category == c and o.approved
                    and o.amount_minor < C.STEP_UP_MINOR]
            big = max((o.amount_minor for o in used), default=2500)
            item = min(-(-big // 500) * 500, 5000)
            day = min(item * 2, 20000)
            pr = self._open_proposal(c, "limits", item, day, "offer")
            text = T.t("offer.limits", cat=self.cat_label(c), item=T.money(item), day=T.money(day),
                       merchants=self._names(self.props[pr["proposal_id"]]["merchants"]),
                       until=self._rule_end(c))
            to = "limits"
        self.offers[c] = dict(category=c, to=to, proposal_id=pr["proposal_id"])
        self.confirm = dict(type="offer", category=c, proposal_id=pr["proposal_id"])
        self._log("offer", text)
        self._say("assistant", text)

    def accept_offer(self, c: str, actor: str = AGENT, proposal_id: str | None = None) -> dict:
        """Accept the offer the person is looking at. The exact displayed proposal id is required, so a
        stale page can never confirm a different (replacement) offer."""
        with self.lock:
            if (bad := self._need_human(actor)):
                return bad
            off = self.offers.get(c)
            if not off:
                return self._err("NO_OFFER")
            if proposal_id != off["proposal_id"]:
                return self._err("PROPOSAL_STALE")
            return self.confirm_autonomy(off["proposal_id"], actor)

    def decline_offer(self, c: str, actor: str = AGENT) -> dict:
        with self.lock:
            off = self.offers.pop(c, None)
            if not off:
                return self._err("NO_OFFER")
            self.props.pop(off.get("proposal_id"), None)
            self.cats[c]["streak"] = 0
            if self.confirm and self.confirm.get("type") == "offer":
                self.confirm = None
            self._say("assistant", T.t("offer.declined", cat=self.cat_label(c)))
            self._log("offer", T.t("log.offer_declined", cat=self.cat_label(c)))
            return self._ok()

    # ---- suggestions ----------------------------------------------------------
    def _signal_card(self, sg: dict):
        p = C.BY_ID[sg["product"]]
        cs = self.cats[p.category]
        st = self.sugg.get(sg["id"], {})
        if not cs["enabled"] or st.get("status") in ("dismissed", "ordered"):
            return None
        until = st.get("until")
        today = self.now.date()
        if until and today < until:
            return None
        if sg["kind"] == "restock":
            days = (today - sg["last_bought"]).days
            if days < sg["cycle_days"] - RESTOCK_LEAD_DAYS:
                return None
            reason = T.t("reason.restock", product=p.name, last=T.fdate_long(sg["last_bought"]),
                         cycle=sg["cycle_days"], days=days)
        elif sg["kind"] == "price_drop":
            if p.price_minor >= sg["old_minor"]:
                return None
            reason = T.t("reason.price_drop", product=p.name, last=T.fdate_long(sg["last_bought"]),
                         old=T.money(sg["old_minor"]), new=T.money(p.price_minor))
        else:
            left = (sg["event_date"] - today).days
            if left > REMINDER_LEAD_DAYS or left < 0:
                return None
            reason = T.t("reason.reminder", note=T.fdate_long(sg["note_date"]),
                         event=T.fdate_long(sg["event_date"]))
        why = T.t("why.card", cat=self.cat_label(p.category), since=T.fdate_long(cs["enabled_at"]))
        return dict(id=sg["id"], product=self.product_view(p), reason=reason, why=why, kind=sg["kind"],
                    category=p.category, status=st.get("status", "new"))

    def suggestions(self) -> list[dict]:
        out, per = [], {}
        for sg in C.SIGNALS:
            card = self._signal_card(sg)
            if card is None:
                continue
            c = card["category"]
            per[c] = per.get(c, 0) + 1
            if per[c] <= self.cats[c]["max_cards"]:
                out.append(card)
        return out

    def dismiss(self, sid: str, why: str) -> dict:
        with self.lock:
            sg = next((s for s in C.SIGNALS if s["id"] == sid), None)
            if sg is None or why not in ("not_interested", "have_it", "too_soon", "price"):
                return self._err("BAD_REQUEST")
            p = C.BY_ID[sg["product"]]
            cs = self.cats[p.category]
            if why == "have_it":
                self.sugg[sid] = dict(status="new", until=self.now.date() + timedelta(days=30))
            elif why == "too_soon":
                self.sugg[sid] = dict(status="new", until=self.now.date() + timedelta(days=7))
            else:
                self.sugg[sid] = dict(status="dismissed")
            cs["dismiss_run"] += 1
            msg = T.t("learn." + why, product=p.name)
            if cs["dismiss_run"] >= DISMISS_RUN_FOR_FEWER and cs["max_cards"] > 1:
                cs["max_cards"] = 1
                msg += " " + T.t("learn.fewer", cat=self.cat_label(p.category))
            self._log("learn", msg)
            return self._ok(message=msg)

    # ---- products ---------------------------------------------------------------
    def product_view(self, p: C.Product) -> dict:
        dd = C.delivery_date(p, self.now)
        return dict(id=p.id, name=p.name, category=p.category, merchant=C.merchant_name(p.merchant),
                    price=T.money(p.price_minor), price_minor=p.price_minor,
                    rating=T.t("rating.fmt", rating=f"{p.rating:.1f}"),
                    delivery=T.t("delivery.fmt", date=T.fdate(dd)), delivery_iso=dd.isoformat(),
                    returns=self.returns_text(p), blurb=p.blurb, image=f"/img/products/{p.id}.jpg",
                    step_up=p.price_minor >= C.STEP_UP_MINOR, prime=C.prime_eligible(p))

    def returns_text(self, p: C.Product) -> str:
        return T.t("returns.days", days=p.return_days) if p.return_days else T.t("returns.perishable")

    # ---- orders -----------------------------------------------------------------
    def _idem(self, o: Order) -> str:
        """The idempotency key carries a hash of the full shopping terms, so the signed receipt chain
        records which terms were approved (the core's own digest covers payee, amount, category, mandate)."""
        return f"idem-{o.id}-{o.terms_hash[:16]}"

    def _propose(self, pid: str, source: str, qty: int = 1) -> Order:
        """Create an intent and submit it to the core. Returns the order, whatever the outcome."""
        p = C.BY_ID[pid]
        c, amount = p.category, p.price_minor * qty
        self.seq += 1
        oid = f"O-{self.seq:03d}"
        delivery = C.delivery_date(p, self.now)
        terms = _terms(pid, qty, p.price_minor, delivery)
        thash = _terms_hash(oid, terms)
        reason = T.t("intent.reason." + source)
        agent = f"nudge-{c}-ask"
        o = Order(oid, pid, c, qty, amount, source, agent, self.now, delivery, reason, self.cats[c]["tier"],
                  terms=terms, terms_hash=thash)
        if source == "assistant" and self.cats[c]["tier"] == "limits" and not self._paused(c):
            probe = self._intent(o, self._auto_agent(c), p, amount, reason)
            mandate = self.engine.mandates.get(self._auto_agent(c))
            d = check(mandate, probe, self._reserved_auto(c), self.now)
            if d.decision == ALLOW:
                agent = self._auto_agent(c)
            else:
                self._log("did_not", T.t("did_not." + d.reason_code, item=p.name,
                                         item_lim=T.money(self.cats[c]["item_minor"]),
                                         day_lim=T.money(self.cats[c]["day_minor"]),
                                         merchant=C.merchant_name(p.merchant)))
        o.agent_id = agent
        intent = self._intent(o, agent, p, amount, reason)
        res = self.engine.submit(intent)
        o.auto = res.decision.decision == ALLOW
        self.orders[oid] = o
        if o.auto:
            if res.receipt["outcome"] == SUCCEEDED:
                self._log("auto", T.t("log.auto_bought", item=p.name, merchant=C.merchant_name(p.merchant),
                                      amount=T.money(amount)))
            else:
                self._outcome_note(o, res.receipt["outcome"])
        return o

    @staticmethod
    def _sugg_status(outcome: str) -> str:
        """A suggestion counts as ordered only once the purchase is verified. An unsettled one stays
        'prepared' (the unresolved-order guard blocks a retry); a failed one can be suggested again."""
        return "ordered" if outcome == SUCCEEDED else ("prepared" if outcome == UNKNOWN else "new")

    def _intent(self, o: Order, agent, p, amount, reason) -> Intent:
        return Intent(o.id, agent, p.merchant, amount, "USD", p.category, reason, self._idem(o), self.now)

    def _reserved_auto(self, c: str) -> int:
        agent, today = self._auto_agent(c), self.now.astimezone(C.DEMO_TZ).date()
        latest = {}
        for r in self.ledger.receipts():
            latest[r["intent"]["id"]] = r
        total = 0
        for r in latest.values():
            if r["intent"]["agent_id"] == agent and r["outcome"] in RESERVING:
                created = datetime.fromisoformat(r["created_at"]).astimezone(C.DEMO_TZ).date()
                if created == today:
                    total += r["intent"]["amount_minor"]
        return total

    def _outcome_note(self, o: Order, outcome: str) -> None:
        if outcome == UNKNOWN:
            self._log("unknown", T.t("log.unknown", item=self._item_text(o)))
        elif outcome == FAILED:
            self._log("failed", T.t("log.failed", item=self._item_text(o)))

    def unresolved_for(self, pid: str):
        """An earlier order for this product whose outcome is not settled (the store may have charged)."""
        for o in self.orders.values():
            if o.product_id == pid and self._status(o.id) in UNRESOLVED:
                return o
        return None

    def shop(self, pid: str, source: str = "shop", qty: int | None = None) -> dict:
        """One tap on a product or a card: prepare the order and open the read-back. With no quantity given,
        an order that is already waiting is read back exactly as it is; only an explicit, different quantity
        replaces it (and the old one is declined in the signed record)."""
        with self.lock:
            if (pid not in C.BY_ID or source not in ("shop", "suggestion")
                    or (qty is not None and (type(qty) is not int or not (1 <= qty <= 5)))):
                return self._err("BAD_REQUEST")
            prior = self.unresolved_for(pid)
            if prior is not None:   # a retry here would be a second charge; direct the person to reconcile
                return self._err("UNRESOLVED_ORDER", item=self._item_text(prior), order=prior.id,
                                 status=T.t("status." + self._status(prior.id)))
            existing = next((o for o in self.orders.values() if o.product_id == pid
                             and self._status(o.id) == PENDING), None)
            if existing and ((qty is not None and existing.qty != qty)
                             or existing.terms.get("unit_minor") != C.BY_ID[pid].price_minor):
                self.decline_order(existing.id)
                qty = qty if qty is not None else existing.qty
                existing = None
            o = existing or self._propose(pid, source, qty or 1)
            for sg in C.SIGNALS:
                if sg["product"] == pid:
                    self.sugg[sg["id"]] = dict(status="prepared")
            return self.start_approval([o.id], "voice")

    def run_assistant(self) -> dict:
        """The assistant's scheduled check (simulated by a button). Behavior depends on the tier."""
        with self.lock:
            made = 0
            for c in C.CATEGORIES:
                cs = self.cats[c]
                if not cs["enabled"]:
                    continue
                for sg in C.SIGNALS:
                    card = self._signal_card(sg)
                    if card is None or card["category"] != c or self.sugg.get(sg["id"], {}).get("status") == "prepared":
                        continue
                    if cs["tier"] == "suggest":
                        continue
                    if self._paused(c):
                        self._log("did_not", T.t("did_not.PAUSED", item=C.BY_ID[sg["product"]].name,
                                                 cat=self.cat_label(c)))
                        continue
                    prior = self.unresolved_for(sg["product"])
                    if prior is not None:
                        self._log("did_not", T.t("did_not.UNRESOLVED", item=C.BY_ID[sg["product"]].name))
                        continue
                    o = self._propose(sg["product"], "assistant")
                    self.sugg[sg["id"]] = dict(status=self._sugg_status(self._status(o.id)) if o.auto else "prepared")
                    if not o.auto:
                        self._log("prepared", T.t("log.prepared", item=self._item_text(o),
                                                  amount=T.money(o.amount_minor)))
                    made += 1
            if made == 0:
                self._log("info", T.t("log.nothing_due"))
            return self._ok(count=made)

    # ---- approval of a purchase: one proposal id, bound to the full terms ---------------
    def pending(self) -> list[Order]:
        return [o for o in self.orders.values() if self._status(o.id) == PENDING]

    def digest_items(self) -> list[Order]:
        return [o for o in self.pending() if o.amount_minor < C.STEP_UP_MINOR]

    def _readback(self, items: list[Order]) -> str:
        if len(items) == 1:
            o = items[0]
            return T.t("readback.one", item=self._item_qty_text(o), merchant=C.merchant_name(o.merchant),
                       amount=T.money(o.amount_minor), date=T.fdate(o.delivery), last4=C.CARD_LAST4)
        total = sum(o.amount_minor for o in items)
        lines = " ".join(T.t("readback.line", item=self._item_qty_text(o), merchant=C.merchant_name(o.merchant),
                              amount=T.money(o.amount_minor), date=T.fdate(o.delivery)) for o in items)
        return T.t("readback.many", count=len(items), lines=lines, total=T.money(total), last4=C.CARD_LAST4)

    def start_approval(self, ids: list[str], kind: str = "voice") -> dict:
        with self.lock:
            items = []
            for i in ids:
                o = self.orders.get(i)
                if o is None or self._status(i) != PENDING:
                    return self._err("NOT_PENDING")
                items.append(o)
            if not items:
                return self._err("NOTHING_TO_APPROVE")
            self.session_no += 1
            digs = {o.id: self._latest(o.id)["intent_digest"] for o in items}
            total = sum(o.amount_minor for o in items)
            step_up = any(o.amount_minor >= C.STEP_UP_MINOR for o in items) or total >= C.STEP_UP_MINOR
            self.session = dict(proposal_id=f"P-{self.session_no:03d}", ids=[o.id for o in items], kind=kind,
                                stage="confirm", tries=0, code=None, code_at=None, digests=digs,
                                terms={o.id: o.terms_hash for o in items}, total=total, no=self.session_no,
                                step_up=step_up, expires=self.now + PROPOSAL_TTL)
            self.confirm = None
            self._say("assistant", self._readback(items) + " " + T.t("readback.ask"))
            return self._ok(stage="confirm", proposal_id=self.session["proposal_id"], requires_step_up=step_up,
                            order_ids=[o.id for o in items])

    def _cancel_session(self, text_key: str) -> None:
        self.session = None
        self._say("assistant", T.t(text_key))

    def _terms_ok(self, s: dict) -> bool:
        """The yes applies only to what was read back: same terms hash, same price in the catalog, same
        amount and idempotency key in the signed receipt."""
        for i in s["ids"]:
            o = self.orders.get(i)
            if o is None or o.terms_hash != s["terms"].get(i) or o.terms_hash != _terms_hash(o.id, o.terms):
                return False
            if not self._order_fields_ok(o):      # displayed quantity, amount and delivery are the snapshot's
                return False
            if C.BY_ID[o.product_id].price_minor != o.terms["unit_minor"] or o.amount_minor != o.terms["amount_minor"]:
                return False
            rec = self._latest(i)
            if rec["intent"]["amount_minor"] != o.amount_minor or rec["intent"]["idempotency_key"] != self._idem(o):
                return False
        return True

    def _session_for(self, proposal_id: str | None):
        """(session, error). The proposal id must be the one currently shown; a yes for any other
        read-back, or no id at all, approves nothing."""
        s = self.session
        if s is None:
            return None, self._err("NO_READBACK")
        if not proposal_id or proposal_id != s["proposal_id"]:
            return None, self._err("PROPOSAL_MISMATCH")
        if self.now >= s["expires"]:
            self.session = None
            self._say("assistant", T.t("err.READBACK_EXPIRED"))
            return None, self._err("READBACK_EXPIRED")
        return s, None

    def answer(self, proposal_id: str | None, confirm: bool, code: str | None = None, actor: str = AGENT) -> dict:
        """The one path for a yes, a no and the step-up code (REST, MCP and voice all end here)."""
        with self.lock:
            if confirm and (bad := self._need_human(actor)):
                return bad
            s, err = self._session_for(proposal_id)
            if err:
                return err
            if not confirm:
                for i in s["ids"]:
                    if self._status(i) == PENDING:
                        self.decline_order(i)
                self.session = None
                self._say("assistant", T.t("session.declined"))
                return self._ok(stage="declined")
            if not self._terms_ok(s):
                self.session = None
                self._say("assistant", T.t("err.TERMS_CHANGED"))
                return self._err("TERMS_CHANGED")
            if s["stage"] == "confirm":
                if code is not None:
                    return self._err("CODE_TOO_EARLY")
                if s["step_up"]:
                    self._send_code()
                    return self._ok(stage="stepup", requires_step_up=True, proposal_id=s["proposal_id"])
                return self._finalize(s)
            if code is None:
                return self._err("CODE_REQUIRED")
            return self._check_code(s, code)

    def _send_code(self) -> None:
        s = self.session
        self.code_no += 1
        raw = self.ledger.mac(f"stepup:{s['no']}:{self.code_no}:{','.join(s['ids'])}")
        s["code"] = f"{int(raw[:8], 16) % 10000:04d}"
        s["code_at"] = self.now
        s["stage"] = "stepup"
        self.phone.insert(0, dict(text=T.t("phone.code", code=s["code"], amount=T.money(s["total"])),
                                  at=self.now.isoformat()))
        why = T.t("stepup.why_total" if s["total"] >= C.STEP_UP_MINOR and len(s["ids"]) > 1 else "stepup.why",
                  limit=T.money(C.STEP_UP_MINOR))
        self._say("assistant", why + " " + T.t("stepup.ask"))

    def submit_code(self, code: str, proposal_id: str | None = None, actor: str = AGENT) -> dict:
        with self.lock:
            if (bad := self._need_human(actor)):
                return bad
            s, err = self._session_for(proposal_id)
            if err:
                return err
            if s["stage"] != "stepup":
                return self._err("NO_CODE_PENDING")
            if not self._terms_ok(s):
                self.session = None
                self._say("assistant", T.t("err.TERMS_CHANGED"))
                return self._err("TERMS_CHANGED")
            return self._check_code(s, code)

    def _check_code(self, s: dict, code: str) -> dict:
        if self.now - s["code_at"] > STEP_UP_TTL:
            self.session = None
            self._say("assistant", T.t("stepup.expired"))
            return self._err("STEPUP_EXPIRED")
        if not isinstance(code, str) or not hmac.compare_digest(code, s["code"]):
            s["tries"] += 1
            if s["tries"] >= MAX_CODE_TRIES:
                self.session = None
                self._say("assistant", T.t("stepup.locked"))
                self._log("info", T.t("log.stepup_locked"))
                return self._err("STEPUP_LOCKED")
            self._say("assistant", T.t("stepup.wrong", left=MAX_CODE_TRIES - s["tries"]))
            self._cp()
            return self._err("STEPUP_WRONG", left=MAX_CODE_TRIES - s["tries"])
        return self._finalize(s, step_up=True)

    def _finalize(self, s: dict, step_up: bool = False) -> dict:
        channel = "digest" if s["kind"] == "digest" else "voice"
        self.session = None
        results = self._approve_ids(s["ids"], channel, step_up, digests=s["digests"])
        done = [o for o, r in results if r == SUCCEEDED]
        unk = [o for o, r in results if r == UNKNOWN]
        bad = [o for o, r in results if r not in (SUCCEEDED, UNKNOWN)]
        if len(s["ids"]) == 1 and done:
            self._say("assistant", T.t("done.one", item=self._item_text(done[0]),
                                       date=T.fdate(done[0].delivery),
                                       hours=C.UNDO_WINDOW_HOURS))
        elif done:
            self._say("assistant", T.t("done.many", count=len(done), hours=C.UNDO_WINDOW_HOURS))
        if unk:
            self._say("assistant", T.t("done.unknown", item=self._item_text(unk[0])))
        if bad:
            self._say("assistant", T.t("done.failed", count=len(bad)))
        for c in {o.category for o, _ in results}:
            self._maybe_offer(c)
        outcomes = {o.id: r for o, r in results}
        extra = dict(stage="done", outcomes=outcomes, order_ids=list(outcomes))
        if not unk and not bad:
            return self._ok(**extra)
        self._cp()
        code = "OUTCOME_FAILED" if bad else "OUTCOME_UNKNOWN"
        msg = T.t("done.failed", count=len(bad)) if bad else T.t("done.unknown", item=self._item_text(unk[0]))
        return {"ok": False, "code": code, "message": msg, **extra}

    def _approve_ids(self, ids, channel, step_up, digests=None, quiet=False):
        self.session_no += 1
        sess = ApproverSession(APPROVER_ID, f"demo-session-{self.session_no}")
        out = []
        for i in ids:
            o = self.orders[i]
            p = self._latest(i)
            note = f"channel={channel};step_up={'yes' if step_up else 'no'}"
            try:
                rec = self.engine.approve(i, sess, p["approval_nonce"],
                                          (digests or {}).get(i, p["intent_digest"]), note)
            except ApprovalError as e:
                self._log("failed", T.t("log.approval_error", item=self._item_text(o), code=e.code))
                out.append((o, e.code))
                continue
            o.approved, o.channel, o.step_up = True, channel, step_up
            cs = self.cats[o.category]
            if cs["tier"] != "limits" and rec["outcome"] == SUCCEEDED:
                cs["streak"] += 1
            cs["dismiss_run"] = 0
            for sg in C.SIGNALS:
                if sg["product"] == o.product_id:
                    self.sugg[sg["id"]] = dict(status=self._sugg_status(rec["outcome"]))
            if rec["outcome"] == SUCCEEDED:     # say "bought" only once the store has said so
                self._log("bought", T.t("log.bought", item=self._item_text(o), merchant=C.merchant_name(o.merchant),
                                        amount=T.money(o.amount_minor), channel=T.t("channel." + channel)))
            else:
                self._outcome_note(o, rec["outcome"])
            out.append((o, rec["outcome"]))
        return out

    def start_digest(self, exclude: list[str] | None = None) -> dict:
        with self.lock:
            items = [o for o in self.digest_items() if o.id not in (exclude or [])]
            if not items:
                return self._err("NOTHING_TO_APPROVE")
            return self.start_approval([o.id for o in items], "digest")

    def decline_order(self, oid: str) -> dict:
        with self.lock:
            o = self.orders.get(oid)
            if o is None or self._status(oid) != PENDING:
                return self._err("NOT_PENDING")
            self.session_no += 1
            self.engine.deny(oid, ApproverSession(APPROVER_ID, f"demo-session-{self.session_no}"),
                             "channel=tap")
            for sg in C.SIGNALS:
                if sg["product"] == o.product_id:
                    self.sugg[sg["id"]] = dict(status="new")
            if self.session and oid in self.session["ids"]:
                self.session = None
            self._log("declined", T.t("log.declined", item=self._item_text(o)))
            return self._ok()

    # ---- undo, problems, failure path ----------------------------------------------
    def undo_until(self, oid: str):
        rec = self._latest(oid)
        if rec["outcome"] != SUCCEEDED:
            return None
        # The FIRST SUCCEEDED receipt is the completion. Later SUCCEEDED records (problem reports) never
        # move it, so nothing a person or a host does can restart the window.
        done = next(r for r in self.ledger.receipts()
                    if r["intent"]["id"] == oid and r["outcome"] == SUCCEEDED)
        return datetime.fromisoformat(done["created_at"]) + timedelta(hours=C.UNDO_WINDOW_HOURS)

    def undo(self, oid: str) -> dict:
        with self.lock:
            o = self.orders.get(oid)
            if o is None:
                return self._err("NOT_FOUND")
            until = self.undo_until(oid)
            if until is None:
                return self._err("UNDO_NOT_AVAILABLE")
            if self.now >= until:
                return self._err("UNDO_WINDOW_CLOSED", returns=self.returns_text(C.BY_ID[o.product_id]))
            ref = self.adapter.cancel(self._idem(o))
            if ref is None:
                return self._err("UNDO_NOT_AVAILABLE")
            self._append_event(self._latest(oid), "undo", "CANCELLED", refund_ref=ref)
            self._log("undo", T.t("log.undo", item=self._item_text(o), amount=T.money(o.amount_minor)))
            extra = ""
            if o.auto and self.cats[o.category]["tier"] == "limits":
                self._reset_to_ask(o.category, "undo")
                extra = " " + T.t("reset.note", cat=self.cat_label(o.category))
            msg = T.t("undo.done", item=self._item_text(o), amount=T.money(o.amount_minor)) + extra
            self._say("assistant", msg)
            return self._ok(message=msg)

    def _append_event(self, prev: dict, event: str, outcome: str, **extra) -> dict:
        body = {k: v for k, v in prev.items() if k not in ("seq", "id", "key_id", "prev_hash", "hash", "signature")}
        body["approval_nonce"] = None  # single use, never carried forward
        body.update(event=event, outcome=outcome, created_at=self.now.isoformat(), **extra)
        return self.ledger.append(body)

    def report_problem(self, oid: str, why: str) -> dict:
        with self.lock:
            o = self.orders.get(oid)
            if o is None or why not in ("wrong_item", "late", "not_as_described", "didnt_want"):
                return self._err("BAD_REQUEST")
            if self._status(oid) != SUCCEEDED or o.problem:
                return self._err("NO_PROBLEM_REPORT")
            o.problem = why
            self._append_event(self._latest(oid), "problem_reported", SUCCEEDED, problem=why)
            self._log("problem", T.t("log.problem", item=self._item_text(o)))
            msg = T.t("problem.thanks", item=self._item_text(o))
            if self.cats[o.category]["tier"] == "limits":
                self._reset_to_ask(o.category, "problem")
                msg += " " + T.t("reset.note", cat=self.cat_label(o.category))
            else:
                msg += " " + T.t("problem.already_asks", cat=self.cat_label(o.category))
            self._say("assistant", msg)
            return self._ok(message=msg)

    def _reset_to_ask(self, c: str, why: str) -> None:
        cs = self.cats[c]
        cs["tier"], cs["streak"] = "ask", 0
        self._drop_open_proposals(c)
        self._drop_auto(c)
        self.offers.pop(c, None)
        self._log("reset", T.t("log.reset", cat=self.cat_label(c), why=T.t("reset.why." + why)))

    def set_fault(self, name: str) -> dict:
        with self.lock:
            if name not in FAULTS:
                return self._err("BAD_REQUEST")
            for f in FAULTS - {"none"}:
                setattr(self.adapter, f, False)
            if name != "none":
                setattr(self.adapter, name, True)
            self.fault = name
            return self._ok()

    def reconcile(self, oid: str) -> dict:
        with self.lock:
            if oid not in self.orders or self._status(oid) not in (UNKNOWN, "SUBMITTING"):
                return self._err("NOT_UNKNOWN")
            rec, status = self.engine._reconcile(oid)
            o = self.orders[oid]
            if rec["outcome"] == SUCCEEDED:
                msg = T.t("reconcile.found", item=self._item_text(o))
            elif status == INDETERMINATE:
                msg = T.t("reconcile.unsure", item=self._item_text(o))   # the store did not answer clearly
            else:
                msg = T.t("reconcile.none", item=self._item_text(o))
            self._log("reconcile", msg)
            self._say("assistant", msg)
            return self._ok(message=msg)

    def retry(self, oid: str) -> dict:
        with self.lock:
            if oid not in self.orders or self._status(oid) != UNKNOWN:
                return self._err("NOT_UNKNOWN")
            rec = self.engine.retry(oid)
            o = self.orders[oid]
            msg = T.t("retry.done" if rec["outcome"] == SUCCEEDED else "retry.still", item=self._item_text(o))
            self._log("reconcile", msg)
            self._say("assistant", msg)
            return self._ok(message=msg)

    # ---- time ---------------------------------------------------------------------------
    def advance(self, hours: int) -> dict:
        with self.lock:
            if not (1 <= int(hours) <= 24 * 14):
                return self._err("BAD_REQUEST")
            self.now += timedelta(hours=int(hours))
            for p in self.engine.expire_due():
                self._log("expired", T.t("log.expired", item=self._item_text(self.orders[p["intent"]["id"]])))
                for sg in C.SIGNALS:
                    if sg["product"] == self.orders[p["intent"]["id"]].product_id:
                        self.sugg[sg["id"]] = dict(status="new")
            if self.session and (any(self._status(i) != PENDING for i in self.session["ids"])
                                 or self.now >= self.session["expires"]):
                self.session = None
            for pr in [x for x in self.props.values() if self.now >= x["expires"]]:
                self._drop_proposal(pr)
            for c in C.CATEGORIES:
                if self.cats[c]["tier"] == "limits":
                    self._install_auto(c)
            self._log("info", T.t("log.advanced", hours=int(hours), now=T.fdate(self.now.date())))
            return self._ok()

    # ---- receipts and verify -----------------------------------------------------------------
    def verify(self) -> dict:
        with self.lock:
            r = self.ledger.verify_trusted()
            return dict(ok=r.ok, records=r.records, broken=r.first_broken_id, code=r.reason_code,
                        message=T.t("verify.ok", n=r.records) if r.ok else
                        T.t("verify.broken", id=r.first_broken_id, code=r.reason_code))

    def tamper_demo(self) -> dict:
        """Edit one amount on a COPY of the ledger and verify the copy. The real ledger is untouched."""
        with self.lock:
            lines = open(self.ledger_path, encoding="utf-8").read().splitlines()
            target = next((i for i, ln in enumerate(lines) if '"outcome":"SUCCEEDED"' in ln), None)
            if target is None:
                return self._err("NOTHING_TO_TAMPER")
            rec = json.loads(lines[target])
            rec["intent"]["amount_minor"] = 1
            lines[target] = json.dumps(rec, sort_keys=True, separators=(",", ":"))
            tmp = tempfile.mkdtemp(prefix="nudge-tamper-")
            try:
                path = os.path.join(tmp, "copy.jsonl")
                with open(path, "w", encoding="utf-8") as f:
                    f.write("\n".join(lines) + "\n")
                r = Ledger(path, self._explicit_key, allow_demo_key=self.ledger.demo_key).verify(self.checkpoint)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
            return dict(ok=True, verified_ok=r.ok, broken=r.first_broken_id, code=r.reason_code,
                        message=T.t("tamper.result", id=r.first_broken_id, code=r.reason_code))

    # ---- voice entry point -------------------------------------------------------------------------
    def handle_voice(self, text: str, proposal_id: str | None = None, actor: str = AGENT) -> dict:
        """Typed or spoken text. Only a person's channel may use it (the REST layer passes HUMAN).
        A yes, no or code must name the proposal it answers; stop and pause never need one."""
        with self.lock:
            if (bad := self._need_human(actor)):
                return bad
            text = (text or "")[:300]
            s = self.session
            cmd = parse(text)
            code = code_only(text)
            hide = bool(s and s["stage"] == "stepup" and code)
            if cmd.kind != "empty":
                self._say("you", T.t("transcript.code_hidden") if hide else text)
            if cmd.kind in ("pause", "stop") and (s or self.confirm):
                # stopping always wins, in every stage of an approval
                self.session, self.confirm = None, None
                self._say("assistant", T.t("session.stopped"))
                if cmd.kind == "pause":
                    self.pause(cmd.params.get("category"), True, actor)
                return self._ok(stage="stopped")
            if cmd.kind == "negated":
                self._say("assistant", T.t("cmd.negated"))
                return self._ok()
            if s and s["stage"] == "stepup" and code:
                return self._voice_reply(self.submit_code(code, proposal_id, actor))
            if s:
                return self._voice_in_session(cmd, proposal_id, actor)
            return self._voice_command(cmd, proposal_id, actor)

    def _voice_reply(self, res: dict) -> dict:
        """Say an error aloud when the underlying call refused (success paths already spoke)."""
        if not res.get("ok") and res.get("message") and res.get("code") not in ("STEPUP_WRONG", "STEPUP_LOCKED",
                                                                                "STEPUP_EXPIRED", "TERMS_CHANGED",
                                                                                "OUTCOME_UNKNOWN", "OUTCOME_FAILED"):
            self._say("assistant", res["message"])
        return res

    def _voice_in_session(self, cmd: Command, proposal_id, actor) -> dict:
        s = self.session
        if cmd.kind == "yes" and s["stage"] == "confirm":
            return self._voice_reply(self.answer(proposal_id, True, None, actor))
        if cmd.kind == "no":
            return self._voice_reply(self.answer(proposal_id, False, None, actor))
        if cmd.kind == "later":
            if proposal_id != s["proposal_id"]:
                return self._voice_reply(self._err("PROPOSAL_MISMATCH"))
            self._cancel_session("session.later")
        elif s["stage"] == "stepup":
            self._say("assistant", T.t("stepup.ask"))
        else:
            self._say("assistant", T.t("session.reask"))
        return self._ok()

    def _voice_command(self, cmd: Command, proposal_id, actor) -> dict:
        k, p = cmd.kind, cmd.params
        if k == "yes":
            return self._confirm_pending(proposal_id, actor)
        if k in ("no", "later", "stop"):
            if self.confirm and self.confirm["type"] == "offer":
                return self.decline_offer(self.confirm["category"], actor)
            if self.confirm and self.confirm["type"] == "autonomy":
                self.cancel_autonomy(self.confirm["proposal_id"], actor)
            self.confirm = None
            self._say("assistant", T.t("cmd.ok_nothing"))
        elif k == "pause":
            self.pause(p.get("category"), True, actor)
            self._say("assistant", T.t("cmd.paused", scope=T.t("scope.all") if not p.get("category")
                                       else self.cat_label(p["category"])))
        elif k == "resume":
            self.pause(p.get("category"), False, actor)
            self._say("assistant", T.t("cmd.resumed", scope=T.t("scope.all") if not p.get("category")
                                       else self.cat_label(p["category"])))
        elif k == "set_tier":
            res = self.set_tier(p["category"], p["tier"], actor, voice=True)
            if not res.get("needs_confirmation"):
                self._say("assistant", res.get("message") or "")
        elif k == "set_limit":
            if p.get("minor") is None:
                self._say("assistant", T.t("cmd.limit_missing"))
            else:
                kw = dict(day_minor=p["minor"]) if p["daily"] else dict(item_minor=p["minor"])
                res = self.set_limits(p["category"], actor=actor, voice=True, **kw)
                if not res.get("needs_confirmation"):
                    self._say("assistant", res.get("message") or "")
        elif k == "unsupported_period":
            self._say("assistant", T.t("cmd.unsupported_period"))
        elif k == "show_rule":
            cats = [p["category"]] if p.get("category") else [c for c in C.CATEGORIES if self.cats[c]["enabled"]]
            for c in cats or [None]:
                self._say("assistant", self.mandate_sentence(c) if c else T.t("cmd.no_rules"))
        elif k == "undo_last":
            last = [o for o in self.orders.values() if self.undo_until(o.id)]
            res = self.undo(last[-1].id) if last else self._err("UNDO_NOT_AVAILABLE")
            if not res["ok"]:
                self._say("assistant", res["message"])
        elif k == "digest":
            res = self.start_digest()
            if not res["ok"]:
                self._say("assistant", res["message"])
        elif k == "pending":
            pend = self.pending()
            self._say("assistant", T.t("cmd.pending", count=len(pend)) if pend else T.t("cmd.nothing_waiting"))
        elif k == "status":
            recent = [f["text"] for f in self.feed[:3]]
            self._say("assistant", " ".join(recent) if recent else T.t("cmd.nothing_yet"))
        else:
            self._say("assistant", T.t("cmd.unknown"))
        return self._ok()

    def _confirm_pending(self, proposal_id, actor) -> dict:
        c = self.confirm
        if not c:
            self._say("assistant", T.t("cmd.nothing_to_confirm"))
            return self._ok()
        if not proposal_id or proposal_id != c.get("proposal_id"):
            self._say("assistant", T.t("err.PROPOSAL_MISMATCH"))
            return self._err("PROPOSAL_MISMATCH")
        res = self.confirm_autonomy(c["proposal_id"], actor)
        if not res.get("ok"):
            self._say("assistant", res["message"])
        return res

    # ---- views ----------------------------------------------------------------------------------------
    def order_view(self, o: Order) -> dict:
        recs = [r for r in self.ledger.receipts() if r["intent"]["id"] == o.id]
        last = recs[-1]
        p = C.BY_ID[o.product_id]
        until = self.undo_until(o.id)
        oc = last["outcome"]
        return dict(
            id=o.id, product=self.product_view(p), qty=o.qty, amount=T.money(o.amount_minor),
            amount_minor=o.amount_minor, status=oc, status_label=T.t("status." + oc),
            status_help=T.t("statushelp." + oc), delivery=T.t("delivery.fmt", date=T.fdate(o.delivery)),
            card=T.t("card.last4", last4=C.CARD_LAST4), auto=o.auto,
            source=T.t("source." + ("auto" if o.auto else o.source)), step_up=o.step_up,
            can_undo=bool(until and self.now < until),
            undo_text=(T.t("undo.until", when=f"{T.fdate(until.date())} {until:%H:%M}") if until and self.now < until
                       else (T.t("undo.closed", returns=self.returns_text(p)) if until else "")),
            can_reconcile=oc in (UNKNOWN, "SUBMITTING"), can_retry=oc == UNKNOWN,
            can_report=oc == SUCCEEDED and not o.problem, problem=o.problem,
            charges=self._charges_for(o.id),
            story=self.story(last, o),
            receipts=[dict(id=r["id"], outcome=r["outcome"], label=T.t("status." + r["outcome"]),
                           event=r["event"], at=r["created_at"]) for r in recs],
            flags=last["flags"], payment_ref=last["payment_ref"], rule=last["decision"]["rule_id"],
            reason_code=last["decision"]["reason_code"])

    def _charges_for(self, oid: str) -> int:
        """Charges the mock store holds for this one order (net of refunds), not the running total."""
        pre = f"idem-{oid}-"
        return sum(1 for k in self.adapter.accepted if k.startswith(pre) and k not in self.adapter.cancelled)

    def story(self, rec: dict, o: Order) -> str:
        d = rec["decision"]
        when = datetime.fromisoformat(rec["intent"]["created_at"]).astimezone(C.DEMO_TZ)
        s = T.t("story.intro", when=T.fdate(when.date()), item=self._item_text(o),
                merchant=C.merchant_name(o.merchant), amount=T.money(o.amount_minor),
                cat=self.cat_label(o.category))
        s += " " + T.t("story.rule", rule=d["rule_id"], what=T.t("reason." + d["reason_code"], item_lim=T.money(self.cats[o.category]["item_minor"])))
        a = rec.get("approver")
        if a and a.get("action") == "approve":
            note = dict(kv.split("=", 1) for kv in (a.get("note") or "").split(";") if "=" in kv)
            ch = note.get("channel") if note.get("channel") in ("voice", "digest") else "voice"
            s += " " + T.t("story.approved", channel=T.t("channel." + ch),
                           step=T.t("story.step_up") if note.get("step_up") == "yes" else "")
        elif a and a.get("action") == "deny":
            s += " " + T.t("story.denied")
        s += " " + T.t("story.result", result=T.t("statushelp." + rec["outcome"]))
        return s.replace("  ", " ")

    def state(self) -> dict:
        with self.lock:
            cats = {}
            for c in C.CATEGORIES:
                cs = self.cats[c]
                cats[c] = dict(label=self.cat_label(c), enabled=cs["enabled"], tier=cs["tier"],
                               paused=self._paused(c), sentence=self.mandate_sentence(c), streak=cs["streak"],
                               item_minor=cs["item_minor"], day_minor=cs["day_minor"],
                               item=T.money(cs["item_minor"]), day=T.money(cs["day_minor"]),
                               offer=self._offer_view(c), max_cards=cs["max_cards"],
                               streak_needed=GRADUATION_APPROVALS)
            orders = [self.order_view(o) for o in reversed(list(self.orders.values()))]
            dig = self.digest_items()
            s = self.session
            return dict(
                now=self.now.isoformat(), now_text=T.t("when.fmt", date=T.fdate(self.now.date()),
                                                       time=f"{self.now:%H:%M}"),
                profile=self.profile, paused_all=self.paused_all, categories=cats,
                suggestions=self.suggestions(), orders=orders,
                products=[self.product_view(p) for p in C.PRODUCTS],
                digest=dict(items=[self.order_view(o) for o in dig], total=T.money(sum(o.amount_minor for o in dig)),
                            step_up=sum(o.amount_minor for o in dig) >= C.STEP_UP_MINOR),
                needs_yes=[self.order_view(o) for o in self.pending() if o.amount_minor >= C.STEP_UP_MINOR],
                feed=self.feed[:40], transcript=[dict(m, id=m.get('id', i + 1)) for i, m in enumerate(self.transcript)][-40:], phone=self.phone[:5],
                session=None if not s else dict(stage=s["stage"], ids=s["ids"], kind=s["kind"],
                                                proposal_id=s["proposal_id"], tries_left=MAX_CODE_TRIES - s["tries"]),
                awaiting=None if not self.confirm else self.confirm["type"],
                awaiting_id=None if not self.confirm else self.confirm.get("proposal_id"),
                autonomy_proposals=[dict(id=v["id"], category=v["category"], text=v["text"], origin=v["origin"])
                                    for v in self.props.values()],
                step_up_text=T.money(C.STEP_UP_MINOR), cap_text=T.money(C.AUTONOMY_CAP_MINOR),
                payments_made=len(self.adapter.accepted), net_charges=self.adapter.net_charges(),
                fault=self.fault, checkpoint_seq=self.checkpoint["seq"], restored=self.restored)

    def _offer_view(self, c: str):
        o = self.offers.get(c)
        if not o:
            return None
        cs = self.cats[c]
        pr = self.props.get(o["proposal_id"])
        if pr is None:
            return None
        text = (T.t("offer.ask", cat=self.cat_label(c)) if o["to"] == "ask" else
                T.t("offer.limits", cat=self.cat_label(c), item=T.money(pr["item_minor"]),
                    day=T.money(pr["day_minor"]), merchants=self._names(pr["merchants"]),
                    until=self._rule_end(c)))
        return dict(to=o["to"], text=text, proposal_id=o["proposal_id"])

    def close(self) -> None:
        if self._tmp:
            shutil.rmtree(self._tmp, ignore_errors=True)
