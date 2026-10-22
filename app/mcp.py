"""A small Model Context Protocol server over Streamable HTTP, standard library only.

Scope: protocol revision 2025-11-25 ONLY. Older revisions are not supported and not advertised.
  * One endpoint, POST /mcp, JSON-RPC 2.0, one message per request (batching was removed from the
    protocol before this revision, so an array is rejected).
  * initialize (clientInfo and capabilities required, version negotiation, session id in the
    Mcp-Session-Id header), notifications/initialized, ping, tools/list, tools/call.
  * After initialize, EVERY POST (request, notification or client response) and DELETE must carry the
    session id and an MCP-Protocol-Version equal to the negotiated version.
  * Replies are plain application/json. No SSE stream is opened: GET /mcp returns 405, which the
    specification allows. DELETE /mcp ends a session.
  * Tool input problems are returned as tool results with isError true (revision 2025-11-25 asks for
    this so a model can correct itself). Protocol problems are JSON-RPC errors.
  * Auth is two per-run bearer tokens: the page's (a person) and a host token (an agent). OAuth is NOT
    implemented. See README for how this maps to who may approve what.
Not implemented: resources, prompts, sampling, elicitation, pagination, resumability, server notifications.
"""
from __future__ import annotations

import json
import logging
import re
import secrets
import threading

from . import catalog as C
from . import texts as T
from .service import AGENT, HUMAN, NudgeService

LATEST = "2025-11-25"
SUPPORTED = (LATEST,)
SERVER_INFO = {"name": "nudge-shopping", "title": "Nudge shopping tools", "version": "0.2.0"}
INSTRUCTIONS = ("Simulated shopping assistant over a mock payment adapter. propose_purchase prepares an order and "
                "returns a read-back and a proposal_id; nothing is bought until a person answers that exact "
                "proposal_id in the Nudge page. A host cannot approve a purchase or raise its own authority. "
                "Amounts at or above the step-up limit need a code sent to the simulated phone, never "
                "returned by any tool.")

PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS, INTERNAL = -32700, -32600, -32601, -32602, -32603
# Implementation-defined codes in the JSON-RPC server range, as several SDKs use them. Not from the spec.
BAD_TRANSPORT, SESSION_NOT_FOUND = -32000, -32001
MAX_SESSIONS = 100


class RpcError(Exception):
    def __init__(self, code: int, message: str, http: int = 200):
        super().__init__(message)
        self.code, self.message, self.http = code, message, http


class ToolInputError(Exception):
    """Arguments that do not match the tool's input schema. Becomes a tool result with isError true."""


def _obj(props: dict, required=()) -> dict:
    return {"type": "object", "properties": props, "required": list(required), "additionalProperties": False}


CAT = {"type": "string", "enum": list(C.CATEGORIES)}
TOOLS: dict[str, dict] = {
    "search_products": dict(
        description="Search the sample catalog. Returns product cards for a carousel: price, rating, delivery date, return window.",
        schema=_obj({"query": {"type": "string", "maxLength": 80}, "category": CAT}), read_only=True),
    "suggest_items": dict(
        description="List the idea cards Nudge has for categories the user turned on. Each card carries its reason and a why-am-I-seeing-this text.",
        schema=_obj({}), read_only=True),
    "dismiss_suggestion": dict(
        description="Dismiss an idea card and say why. Nudge adjusts what it shows next.",
        schema=_obj({"suggestion_id": {"type": "string"}, "reason": {"type": "string", "enum": ["not_interested", "have_it", "too_soon", "price"]}},
                    ["suggestion_id", "reason"])),
    "propose_purchase": dict(
        description="Prepare an order and open the read-back. Nothing is bought. Returns the spoken read-back (item, store, amount, delivery, card last four), a proposal_id, and whether a phone code will be needed. Refused while an earlier order for the same product is unsettled.",
        schema=_obj({"product_id": {"type": "string"}, "quantity": {"type": "integer", "minimum": 1, "maximum": 5},
                     "suggestion_id": {"type": "string"}}, ["product_id"])),
    "start_digest": dict(
        description="Open one read-back for all small pending orders (each under the step-up amount). Returns a proposal_id.",
        schema=_obj({"leave_out": {"type": "array", "items": {"type": "string"}, "maxItems": 50}})),
    "approve_purchase": dict(
        description="Answer the open read-back named by proposal_id. confirm=true is a person's yes and is refused for a host token; only the Nudge page can say yes. If a phone code is required the first yes returns requires_step_up; call again with step_up_code. confirm=false declines and is allowed for anyone.",
        schema=_obj({"proposal_id": {"type": "string"}, "confirm": {"type": "boolean"},
                     "step_up_code": {"type": "string", "pattern": "^[0-9]{4}$"}}, ["proposal_id", "confirm"])),
    "deny_purchase": dict(
        description="Decline a pending order. Nothing is bought.", schema=_obj({"order_id": {"type": "string"}}, ["order_id"])),
    "undo_purchase": dict(
        description="Cancel and refund an order inside its 24 hour undo window, counted from the original completion (simulated).",
        schema=_obj({"order_id": {"type": "string"}}, ["order_id"])),
    "report_problem": dict(
        description="Report a problem with a delivered order. If the category was Within limits it resets to Ask first.",
        schema=_obj({"order_id": {"type": "string"}, "reason": {"type": "string", "enum": ["wrong_item", "late", "not_as_described", "didnt_want"]}},
                    ["order_id", "reason"])),
    "list_recent_decisions": dict(
        description="Recent orders with status, plain-English receipt story, and what Nudge did not do.",
        schema=_obj({"limit": {"type": "integer", "minimum": 1, "maximum": 50}}), read_only=True),
    "get_mandate": dict(
        description="The rule for each category as one sentence, plus tier and limits.",
        schema=_obj({"category": CAT}), read_only=True),
    "set_autonomy": dict(
        description="Change a category's tier (suggest, ask, limits) or its per item and per day limits. Lowering applies at once. Raising does NOT apply: it returns needs_confirmation and a proposal_id, and a person confirms it in the Nudge page. Within limits also needs three approved orders in that category. The daily limit must not be below the per item limit. Per item limits are capped below the step-up amount.",
        schema=_obj({"category": CAT, "tier": {"type": "string", "enum": ["suggest", "ask", "limits"]},
                     "item_limit_dollars": {"type": "number", "exclusiveMinimum": 0, "maximum": 100000},
                     "day_limit_dollars": {"type": "number", "exclusiveMinimum": 0, "maximum": 100000}}, ["category"])),
    "pause_autonomy": dict(
        description="Pause everything Nudge does by itself, or one category. Resuming (paused=false) is refused for a host token; a person resumes in the page.",
        schema=_obj({"paused": {"type": "boolean"}, "category": CAT}, ["paused"])),
    "run_daily_check": dict(
        description="Run the assistant's daily check. Behavior depends on each category's tier.", schema=_obj({})),
    "reconcile_order": dict(
        description="For an order whose outcome is unknown, ask the store using the same request key. Never charges twice. Pass retry=true to re-send only if the store definitively has no record.",
        schema=_obj({"order_id": {"type": "string"}, "retry": {"type": "boolean"}}, ["order_id"])),
    "verify_receipts": dict(
        description="Recompute the whole receipt chain and name the first changed record, if any.", schema=_obj({}), read_only=True),
}


def validate(value, schema, path="arguments"):
    """Minimal JSON Schema check for the keywords used above. Raises ToolInputError."""
    t = schema.get("type")
    bad = lambda why: ToolInputError(f"{path}: {why}")
    if t == "object":
        if not isinstance(value, dict):
            raise bad("must be an object")
        for r in schema.get("required", []):
            if r not in value:
                raise bad(f"missing required '{r}'")
        props = schema.get("properties", {})
        for k, v in value.items():
            if k not in props:
                if schema.get("additionalProperties") is False:
                    raise bad(f"unknown property '{k}'")
                continue
            validate(v, props[k], f"{path}.{k}")
    elif t == "string":
        if not isinstance(value, str):
            raise bad("must be a string")
        if len(value) > schema.get("maxLength", 300):
            raise bad("too long")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            raise bad("does not match pattern")
    elif t == "integer":
        if type(value) is not int:
            raise bad("must be an integer")
    elif t == "number":
        if type(value) not in (int, float) or value != value or value in (float("inf"), float("-inf")):
            raise bad("must be a finite number")
    elif t == "boolean":
        if type(value) is not bool:
            raise bad("must be a boolean")
    elif t == "array":
        if not isinstance(value, list) or len(value) > schema.get("maxItems", 100):
            raise bad("must be a short array")
        for i, v in enumerate(value):
            validate(v, schema["items"], f"{path}[{i}]")
    if "enum" in schema and value not in schema["enum"]:
        raise bad(f"must be one of {schema['enum']}")
    if "minimum" in schema and value < schema["minimum"]:
        raise bad("too small")
    if "maximum" in schema and value > schema["maximum"]:
        raise bad("too large")
    if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
        raise bad("too small")


def tool_list() -> list[dict]:
    out = []
    for name, d in TOOLS.items():
        out.append({"name": name, "description": d["description"], "inputSchema": d["schema"],
                    "annotations": {"readOnlyHint": bool(d.get("read_only")), "openWorldHint": False}})
    return out


def _valid_id(v) -> bool:
    return type(v) is str or type(v) is int          # bool, float, null, object and array are not ids


class McpServer:
    def __init__(self, svc: NudgeService) -> None:
        self.svc = svc
        self.sessions: dict[str, dict] = {}
        self._slock = threading.Lock()            # guards session creation, eviction, lookup and deletion

    # ---- HTTP-independent entry: returns (http_status, extra_headers, response_object_or_None)
    def handle(self, msg, session_id: str | None, proto_header: str | None, principal: str = AGENT):
        if isinstance(msg, list):
            return 400, {}, self._err(None, INVALID_REQUEST, "batching is not supported")
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            return 400, {}, self._err(None, INVALID_REQUEST, "not a JSON-RPC 2.0 message")
        has_id, mid = "id" in msg, msg.get("id")
        if has_id and not _valid_id(mid):
            return 400, {}, self._err(None, INVALID_REQUEST, "id must be a string or an integer")
        rid = mid if has_id else None
        if "method" not in msg:                  # a response from the client
            err = msg.get("error")
            if (not has_id or ("result" in msg) == ("error" in msg)
                    or ("error" in msg and not (isinstance(err, dict) and type(err.get("code")) is int
                                                and isinstance(err.get("message"), str)))):
                return 400, {}, self._err(rid, INVALID_REQUEST, "not a valid request, notification or response")
            _, fail = self._transport(session_id, proto_header, principal, rid)
            return fail or (202, {}, None)
        method = msg["method"]
        if not isinstance(method, str) or not method:
            return 400, {}, self._err(rid, INVALID_REQUEST, "method must be a non-empty string")
        params = msg.get("params")
        bad_params = "params" in msg and not isinstance(params, dict)
        params = params if isinstance(params, dict) else {}
        if method == "initialize":
            if bad_params:
                return self._bad_params(has_id, mid)
            if not has_id:
                return 400, {}, self._err(None, INVALID_REQUEST, "initialize is a request and needs an id")
            return self._initialize(mid, params, principal)
        sess, fail = self._transport(session_id, proto_header, principal, rid)    # before any params check
        if fail:
            return fail
        if bad_params:
            return self._bad_params(has_id, mid)
        if not has_id:                           # notification
            if method == "notifications/initialized":
                with self._slock:
                    sess["initialized"] = True
            return 202, {}, None
        try:
            if method == "ping":
                return 200, {}, self._ok(mid, {})
            if method.startswith("notifications/"):
                raise RpcError(METHOD_NOT_FOUND, f"{method} is a notification, not a request")
            if method not in ("tools/list", "tools/call"):
                raise RpcError(METHOD_NOT_FOUND, f"method not found: {method}")
            if not sess["initialized"]:
                raise RpcError(INVALID_REQUEST, "send notifications/initialized first")
            if method == "tools/list":
                if params:
                    raise RpcError(INVALID_PARAMS, "tools/list takes no parameters here (no pagination)")
                return 200, {}, self._ok(mid, {"tools": tool_list()})
            return 200, {}, self._ok(mid, self._call(params, principal))
        except RpcError as e:
            return 200, {}, self._err(mid, e.code, e.message)

    def _bad_params(self, has_id, mid):
        if has_id:
            return 200, {}, self._err(mid, INVALID_PARAMS, "params must be an object")
        return 400, {}, self._err(None, INVALID_PARAMS, "params must be an object")

    def _transport(self, session_id, proto_header, principal, rid):
        """Session and protocol-version checks that apply to every message after initialize."""
        if not session_id:
            return None, (400, {}, self._err(rid, BAD_TRANSPORT, "Mcp-Session-Id header required"))
        with self._slock:
            sess = self.sessions.get(session_id)
        if sess is None or sess["principal"] != principal:
            return None, (404, {}, self._err(rid, SESSION_NOT_FOUND, "unknown or ended session"))
        if proto_header is None:
            return None, (400, {}, self._err(rid, BAD_TRANSPORT, "MCP-Protocol-Version header required"))
        if proto_header != sess["version"]:
            return None, (400, {}, self._err(rid, BAD_TRANSPORT,
                                             f"MCP-Protocol-Version must be {sess['version']}, got {proto_header[:40]}"))
        return sess, None

    def check_delete(self, session_id, proto_header, principal):
        """DELETE: same session and version rules. Returns an http status."""
        sess, fail = self._transport(session_id, proto_header, principal, None)
        if fail:
            return fail[0]
        with self._slock:
            self.sessions.pop(session_id, None)
        return 204

    def end_session(self, session_id: str | None) -> bool:      # kept for tests and tooling
        with self._slock:
            return self.sessions.pop(session_id or "", None) is not None

    def _initialize(self, mid, params, principal):
        pv, caps, info = params.get("protocolVersion"), params.get("capabilities"), params.get("clientInfo")
        if (not isinstance(pv, str) or not isinstance(caps, dict) or not isinstance(info, dict)
                or not isinstance(info.get("name"), str) or not info["name"]
                or not isinstance(info.get("version"), str) or not info["version"]):
            return 200, {}, self._err(mid, INVALID_PARAMS,
                                      "initialize needs protocolVersion, capabilities and clientInfo with name and version")
        # If the client asks for a revision this server does not support, answer with the one it does.
        sid = secrets.token_hex(16)
        with self._slock:
            while len(self.sessions) >= MAX_SESSIONS:
                self.sessions.pop(next(iter(self.sessions)))
            self.sessions[sid] = {"initialized": False, "version": LATEST, "principal": principal}
        return 200, {"Mcp-Session-Id": sid}, self._ok(mid, {
            "protocolVersion": LATEST, "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO, "instructions": INSTRUCTIONS})

    @staticmethod
    def _ok(mid, result):
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    @staticmethod
    def _err(mid, code, message):
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}

    # ---- tools/call
    def _call(self, params, who):
        if not isinstance(params.get("name"), str):
            raise RpcError(INVALID_PARAMS, "tools/call needs a name")
        name = params["name"]
        if name not in TOOLS:
            raise RpcError(INVALID_PARAMS, f"unknown tool: {name}")
        args = params.get("arguments", {})
        if not isinstance(args, dict):
            raise RpcError(INVALID_PARAMS, "arguments must be an object")
        try:
            validate(args, TOOLS[name]["schema"])
        except ToolInputError as e:      # input problems are tool execution errors, so a model can correct them
            return {"content": [{"type": "text", "text": f"Invalid arguments. {e}"}],
                    "structuredContent": {"ok": False, "code": "INVALID_ARGUMENTS"}, "isError": True}
        with self.svc.lock:
            if self.svc.needs_restart:     # an earlier save failed: refuse until the service is restarted
                text, data = T.t("err.RESTART_REQUIRED"), {"ok": False, "code": "RESTART_REQUIRED"}
                return {"content": [{"type": "text", "text": text}, {"type": "text", "text": json.dumps(data)}],
                        "structuredContent": data, "isError": True}
            try:
                text, data, is_error = getattr(self, "t_" + name)(who, **args)
            except (OSError, RuntimeError):   # storage or ledger trouble: typed error, details only in the log
                self.svc.storage_failed()
                logging.getLogger("nudge").exception("storage error in tool %s", name)
                text, data, is_error = T.t("err.STORAGE_ERROR"), {"ok": False, "code": "STORAGE_ERROR"}, True
            except Exception:  # a bug must not leak internals; report as a tool error
                logging.getLogger("nudge").exception("tool %s failed", name)
                text, data, is_error = T.t("err.BAD_REQUEST"), {"ok": False, "code": "BAD_REQUEST"}, True
        return {"content": [{"type": "text", "text": text}, {"type": "text", "text": json.dumps(data)}],
                "structuredContent": data, "isError": is_error}

    @staticmethod
    def _res(r: dict, **data):
        """Turn a service result dict into (text, structured, isError)."""
        ok = bool(r.get("ok"))
        return r.get("message") or "", {"ok": ok, **({"code": r["code"]} if "code" in r else {}), **data}, not ok

    def _order(self, oid):
        o = self.svc.orders.get(oid)
        return self.svc.order_view(o) if o else None

    # ---- tools (first argument is the principal the server derived from the token)
    def t_search_products(self, who, query="", category=None):
        q = query.lower().strip()
        items = [self.svc.product_view(p) for p in C.PRODUCTS
                 if (not category or p.category == category) and (not q or q in p.name.lower() or q in p.blurb.lower())]
        return f"{len(items)}", {"ui": "carousel", "cards": items}, False

    def t_suggest_items(self, who):
        cards = self.svc.suggestions()
        return f"{len(cards)}", {"ui": "cards", "cards": cards}, False

    def t_dismiss_suggestion(self, who, suggestion_id, reason):
        return self._res(self.svc.dismiss(suggestion_id, reason))

    def _readback_result(self, r):
        s = self.svc.session
        if not r["ok"]:
            return r["message"], {"ok": False, "code": r.get("code"), "order_id": None, "proposal_id": None}, True
        text = [m["text"] for m in self.svc.transcript if m["who"] == "assistant"][-1]
        data = {"ok": True, "proposal_id": s["proposal_id"], "order_ids": s["ids"], "order_id": s["ids"][0],
                "requires_step_up": s["step_up"], "stage": s["stage"],
                "orders": [self._order(i) for i in s["ids"]]}
        data["order"] = data["orders"][0]
        return text, data, False

    def t_propose_purchase(self, who, product_id, quantity=None, suggestion_id=None):
        # A purchase that starts from an idea card keeps that provenance; quantity omitted means "as prepared".
        from_card = any(sg["id"] == suggestion_id and sg["product"] == product_id for sg in C.SIGNALS)
        return self._readback_result(self.svc.shop(product_id, "suggestion" if from_card else "shop", quantity))

    def t_start_digest(self, who, leave_out=()):
        return self._readback_result(self.svc.start_digest(list(leave_out)))

    def t_approve_purchase(self, who, proposal_id, confirm, step_up_code=None):
        svc = self.svc
        r = svc.answer(proposal_id, confirm, step_up_code, actor=who)
        s = svc.session
        text = r.get("message") or [m["text"] for m in svc.transcript if m["who"] == "assistant"][-1]
        data = {"ok": bool(r.get("ok")), "stage": r.get("stage") or (s["stage"] if s else None),
                "requires_step_up": bool(s and s["stage"] == "stepup")}
        if "code" in r:
            data["code"] = r["code"]
        if "outcomes" in r:
            data["outcomes"] = r["outcomes"]
            views = [self._order(i) for i in r["order_ids"]]
            data["orders"] = views
            data["receipts"] = [v["receipts"][-1]["id"] for v in views]
        # A purchase that is not confirmed done is never ok:true. UNKNOWN is a state, not a tool failure.
        is_error = (not r.get("ok")) and r.get("code") != "OUTCOME_UNKNOWN"
        return text, data, is_error

    def t_deny_purchase(self, who, order_id):
        return self._res(self.svc.decline_order(order_id))

    def t_undo_purchase(self, who, order_id):
        r = self.svc.undo(order_id)
        return self._res(r, order=self._order(order_id))

    def t_report_problem(self, who, order_id, reason):
        return self._res(self.svc.report_problem(order_id, reason))

    def t_list_recent_decisions(self, who, limit=10):
        st = self.svc.state()
        return str(len(st["orders"][:limit])), {"orders": st["orders"][:limit],
                                                 "did_not": [f for f in st["feed"] if f["kind"] == "did_not"][:limit]}, False

    def t_get_mandate(self, who, category=None):
        st = self.svc.state()["categories"]
        pick = {c: v for c, v in st.items() if not category or c == category}
        return " ".join(v["sentence"] for v in pick.values()), {"categories": pick}, False

    def t_set_autonomy(self, who, category, tier=None, item_limit_dollars=None, day_limit_dollars=None):
        svc = self.svc
        if tier is None and item_limit_dollars is None and day_limit_dollars is None:
            return T.t("cmd.ok_nothing"), {"ok": True, "needs_confirmation": False,
                                           "mandate": svc.mandate_sentence(category)}, False
        item = None if item_limit_dollars is None else int(round(item_limit_dollars * 100))
        day = None if day_limit_dollars is None else int(round(day_limit_dollars * 100))
        r = svc.propose_autonomy(category, tier, item, day, actor=who)
        data = {"ok": bool(r.get("ok")), "needs_confirmation": bool(r.get("needs_confirmation")),
                "mandate": svc.mandate_sentence(category)}
        if "code" in r:
            data["code"] = r["code"]
        if r.get("needs_confirmation"):
            data["proposal_id"] = r["proposal_id"]
            data["waiting_for"] = "a person to confirm in the Nudge page"
        return r.get("message") or "", data, not r.get("ok")

    def t_pause_autonomy(self, who, paused, category=None):
        return self._res(self.svc.pause(category, paused, actor=who), paused=paused)

    def t_run_daily_check(self, who):
        r = self.svc.run_assistant()
        st = self.svc.state()
        return str(r.get("count", 0)), {"ok": True, "prepared_or_bought": r.get("count", 0),
                                         "feed": st["feed"][:6]}, False

    def t_reconcile_order(self, who, order_id, retry=False):
        r = self.svc.retry(order_id) if retry else self.svc.reconcile(order_id)
        return self._res(r, order=self._order(order_id))

    def t_verify_receipts(self, who):
        v = self.svc.verify()
        return v["message"], {"ok": v["ok"], "records": v["records"], "first_broken": v["broken"], "reason": v["code"]}, False
