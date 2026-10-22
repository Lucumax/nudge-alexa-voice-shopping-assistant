"""Conformance-style tests for the MCP server (Streamable HTTP, JSON-RPC 2.0, revision 2025-11-25 only).
Written from the author's reading of the specification, not from an official conformance suite."""
import http.client
import json
import re
import threading

import pytest

from app.server import serve
from app.service import NudgeService
from app.mcp import LATEST, TOOLS, SUPPORTED


@pytest.fixture
def srv(tmp_path):
    svc = NudgeService(str(tmp_path / "r.jsonl"), key="k", profile="demo")
    httpd = serve("127.0.0.1", 0, svc)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd
    httpd.shutdown()
    httpd.server_close()


class Client:
    """role: 'human' (the page's token), 'agent' (a host's token), or None (no token)."""

    def __init__(self, httpd, role="human"):
        self.httpd, self.sid, self.n = httpd, None, 0
        self.token = {"human": httpd.nudge_token, "agent": httpd.nudge_agent_token}.get(role)
        self.version = LATEST

    def raw(self, method="POST", body=None, headers=None, path="/mcp", session=True):
        port = self.httpd.server_address[1]
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        h = {"Host": f"127.0.0.1:{port}", "Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
        if self.token:
            h["Authorization"] = "Bearer " + self.token
        if self.sid and session:
            h["Mcp-Session-Id"] = self.sid
            h["MCP-Protocol-Version"] = self.version
        h.update(headers or {})
        data = body if isinstance(body, (bytes, str)) or body is None else json.dumps(body)
        c.request(method, path, data, h)
        r = c.getresponse()
        out = r.read()
        hdrs = {k.lower(): v for k, v in r.getheaders()}
        c.close()
        return r.status, hdrs, (json.loads(out) if out else None)

    def rpc(self, method, params=None, **kw):
        self.n += 1
        return self.raw(body={"jsonrpc": "2.0", "id": self.n, "method": method, "params": params or {}}, **kw)

    def start(self, version=LATEST, info=None):
        st, h, b = self.rpc("initialize", {"protocolVersion": version, "capabilities": {},
                                           "clientInfo": info or {"name": "t", "version": "1"}})
        self.sid = h.get("mcp-session-id")
        self.raw(body={"jsonrpc": "2.0", "method": "notifications/initialized"})
        return st, h, b

    def tool(self, name, **args):
        st, h, b = self.rpc("tools/call", {"name": name, "arguments": args})
        assert st == 200, b
        return b["result"] if "result" in b else b


@pytest.fixture
def mc(srv):
    c = Client(srv)
    c.start()
    return c


@pytest.fixture
def agent(srv):
    c = Client(srv, "agent")
    c.start()
    return c


# ---------- handshake and sessions
def test_initialize_negotiates_version_and_issues_a_session(srv):
    c = Client(srv)
    st, h, b = c.start()
    assert st == 200 and b["result"]["protocolVersion"] == LATEST
    assert re.fullmatch(r"[0-9a-f]{32}", h["mcp-session-id"])
    assert b["result"]["capabilities"] == {"tools": {"listChanged": False}}
    assert b["result"]["serverInfo"]["name"] and "instructions" in b["result"]


def test_only_one_revision_is_supported_and_advertised(srv):
    assert SUPPORTED == ("2025-11-25",)
    for old in ("2025-06-18", "2025-03-26", "1999-01-01"):
        st, h, b = Client(srv).start(old)
        assert b["result"]["protocolVersion"] == LATEST                 # the server answers with its own version


def test_initialize_requires_client_info_and_capabilities(srv):
    c = Client(srv)
    for params in ({"protocolVersion": LATEST, "capabilities": {}},
                   {"protocolVersion": LATEST, "capabilities": {}, "clientInfo": {}},
                   {"protocolVersion": LATEST, "capabilities": {}, "clientInfo": {"name": "x"}},
                   {"protocolVersion": LATEST, "clientInfo": {"name": "x", "version": "1"}},
                   {"capabilities": {}, "clientInfo": {"name": "x", "version": "1"}},
                   {"protocolVersion": 5, "capabilities": {}, "clientInfo": {"name": "x", "version": "1"}}):
        st, h, b = c.rpc("initialize", params)
        assert b["error"]["code"] == -32602 and "mcp-session-id" not in h, params
    assert c.rpc("initialize", "nope")[2]["error"]["code"] == -32602


def test_initialize_must_be_a_request_with_an_id(srv):
    st, _, b = Client(srv).raw(body={"jsonrpc": "2.0", "method": "initialize", "params": {}})
    assert st == 400 and b["error"]["code"] == -32600


def test_requests_need_a_session_and_unknown_sessions_get_404(srv):
    c = Client(srv)
    assert c.rpc("tools/list")[0] == 400
    c.sid, c.version = "nope", LATEST
    assert c.rpc("tools/list")[0] == 404


def test_every_message_after_initialize_needs_session_and_version(mc):
    note = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    response = {"jsonrpc": "2.0", "id": 1, "result": {}}
    for body in (note, response, {"jsonrpc": "2.0", "id": 9, "method": "ping"}):
        assert mc.raw(body=body, session=False)[0] == 400                          # no session header
        assert mc.raw(body=body, headers={"MCP-Protocol-Version": ""})[0] == 400    # no version header
        assert mc.raw(body=body, headers={"MCP-Protocol-Version": "2025-06-18"})[0] == 400
        assert mc.raw(body=body, headers={"Mcp-Session-Id": "bogus"})[0] == 404
    assert mc.raw(body=note)[0] == 202 and mc.raw(body=response)[0] == 202


def test_a_session_belongs_to_the_token_that_made_it(srv, mc):
    other = Client(srv, "agent")
    other.sid = mc.sid                                   # a host replaying the person's session id
    assert other.rpc("tools/list")[0] == 404


def test_tools_wait_for_the_initialized_notification(srv):
    c = Client(srv)
    st, h, b = c.rpc("initialize", {"protocolVersion": LATEST, "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}})
    c.sid = h["mcp-session-id"]
    st, _, b = c.rpc("tools/list")
    assert b["error"]["code"] == -32600
    assert c.rpc("ping")[2]["result"] == {}                       # ping is always allowed
    c.raw(body={"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert "result" in c.rpc("tools/list")[2]


def test_notification_gets_202_and_no_body(mc):
    st, _, b = mc.raw(body={"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert st == 202 and b is None


def test_delete_ends_the_session_and_follows_the_same_rules(mc):
    assert mc.raw("DELETE", session=False)[0] == 400
    assert mc.raw("DELETE", headers={"MCP-Protocol-Version": "2025-06-18"})[0] == 400
    assert mc.raw("DELETE")[0] == 204
    assert mc.rpc("tools/list")[0] == 404
    assert mc.raw("DELETE")[0] == 404


def test_get_is_405_because_there_is_no_sse_stream(mc):
    st, h, _ = mc.raw("GET")
    assert st == 405 and "POST" in h["allow"]


# ---------- JSON-RPC envelope
def test_malformed_json_is_a_parse_error(mc):
    st, _, b = mc.raw(body="{not json")
    assert st == 400 and b["error"]["code"] == -32700


def test_batches_and_non_rpc_bodies_are_invalid_requests(mc):
    assert mc.raw(body=[{"jsonrpc": "2.0", "id": 1, "method": "ping"}])[2]["error"]["code"] == -32600
    assert mc.raw(body={"id": 1, "method": "ping"})[2]["error"]["code"] == -32600
    assert mc.raw(body={"jsonrpc": "1.0", "id": 1, "method": "ping"})[2]["error"]["code"] == -32600


def test_request_ids_must_be_strings_or_integers(mc):
    for bad in (True, False, None, 1.5, {"a": 1}, [1]):
        st, _, b = mc.raw(body={"jsonrpc": "2.0", "id": bad, "method": "ping"})
        assert st == 400 and b["error"]["code"] == -32600 and b["id"] is None, bad
    assert mc.raw(body={"jsonrpc": "2.0", "id": "abc", "method": "ping"})[2]["id"] == "abc"
    assert mc.raw(body={"jsonrpc": "2.0", "id": 0, "method": "ping"})[2]["id"] == 0


def test_method_and_params_shapes_are_validated(mc):
    assert mc.raw(body={"jsonrpc": "2.0", "id": 1, "method": 5})[2]["error"]["code"] == -32600
    assert mc.raw(body={"jsonrpc": "2.0", "id": 1, "method": ""})[2]["error"]["code"] == -32600
    st, _, b = mc.raw(body={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": [1]})
    assert b["error"]["code"] == -32602
    assert mc.raw(body={"jsonrpc": "2.0", "id": 1, "result": {}, "error": {"code": 1, "message": "x"}})[0] == 400
    assert mc.raw(body={"jsonrpc": "2.0", "result": {}})[0] == 400                   # a response needs an id


def test_unknown_method_is_32601(mc):
    assert mc.rpc("resources/list")[2]["error"]["code"] == -32601


def test_bearer_token_origin_host_and_accept_are_enforced(srv):
    assert Client(srv, None).rpc("initialize", {"protocolVersion": LATEST})[0] == 401
    c = Client(srv)
    ok = {"protocolVersion": LATEST, "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}
    assert c.rpc("initialize", ok, headers={"Origin": "http://evil.example"})[0] == 403
    assert c.rpc("initialize", ok, headers={"Host": "evil.example"})[0] == 403
    assert c.rpc("initialize", ok, headers={"Accept": "text/plain"})[0] == 406
    assert c.rpc("initialize", ok, headers={"Content-Type": "text/plain"})[0] == 415


# ---------- tools
def test_tools_list_is_typed_and_complete(mc):
    tools = mc.rpc("tools/list")[2]["result"]["tools"]
    names = {t["name"] for t in tools}
    assert {"search_products", "suggest_items", "propose_purchase", "approve_purchase", "deny_purchase", "undo_purchase",
            "list_recent_decisions", "get_mandate", "set_autonomy", "verify_receipts"} <= names == set(TOOLS)
    for t in tools:
        assert t["description"] and t["inputSchema"]["type"] == "object" and t["inputSchema"]["additionalProperties"] is False
    assert "user_confirmed" not in json.dumps(tools)


def test_invalid_arguments_are_tool_errors_and_unknown_tools_are_protocol_errors(mc):
    cases = (("propose_purchase", {}), ("propose_purchase", {"product_id": 5}),
             ("propose_purchase", {"product_id": "pasta", "extra": 1}), ("set_autonomy", {"category": "cars"}),
             ("approve_purchase", {"proposal_id": "P-1", "confirm": "yes"}),
             ("propose_purchase", {"product_id": "pasta", "quantity": 99}),
             ("set_autonomy", {"category": "groceries", "user_confirmed": True}))
    for name, args in cases:
        st, _, b = mc.rpc("tools/call", {"name": name, "arguments": args})
        assert st == 200 and "error" not in b and b["result"]["isError"] is True, (name, args)
        assert b["result"]["structuredContent"]["code"] == "INVALID_ARGUMENTS"
    assert mc.rpc("tools/call", {"name": "buy_everything", "arguments": {}})[2]["error"]["code"] == -32602
    assert mc.rpc("tools/call", {"arguments": {}})[2]["error"]["code"] == -32602
    assert mc.rpc("tools/call", {"name": "get_mandate", "arguments": [1]})[2]["error"]["code"] == -32602


def test_business_failures_are_tool_errors_not_protocol_errors(mc):
    r = mc.tool("deny_purchase", order_id="O-999")
    assert r["isError"] is True and r["structuredContent"]["ok"] is False


def test_structured_results_also_carry_serialized_json_text(mc):
    r = mc.tool("get_mandate")
    assert json.loads(r["content"][1]["text"]) == r["structuredContent"]


def test_search_returns_carousel_cards_with_price_delivery_returns(mc):
    r = mc.tool("search_products", category="gifts")
    cards = r["structuredContent"]["cards"]
    assert r["structuredContent"]["ui"] == "carousel" and len(cards) == 4
    for c in cards:
        assert c["price"] and c["delivery"].startswith("FREE delivery") and c["returns"] and c["image"].endswith(".jpg")


def test_suggest_items_cards_carry_reasons(mc):
    cards = mc.tool("suggest_items")["structuredContent"]["cards"]
    assert cards and all(c["reason"] and c["why"] for c in cards)


def test_propose_does_not_buy_and_approve_needs_the_proposal_id(srv, mc):
    svc = srv.nudge_service
    base = svc.adapter.calls
    assert mc.tool("approve_purchase", proposal_id="P-999", confirm=True)["isError"] is True      # no read-back open
    r = mc.tool("propose_purchase", product_id="pasta")
    sc = r["structuredContent"]
    assert svc.adapter.calls == base and sc["requires_step_up"] is False and sc["proposal_id"]
    for needle in ("Whole-Grain Pasta", "Amazon Fresh", "$9.20", "4242"):
        assert needle in r["content"][0]["text"]
    done = mc.tool("approve_purchase", proposal_id=sc["proposal_id"], confirm=True)
    assert done["isError"] is False and svc.adapter.calls == base + 1
    assert done["structuredContent"]["orders"][0]["status"] == "SUCCEEDED" and done["structuredContent"]["receipts"]


def test_step_up_over_mcp_never_returns_the_code(srv, mc):
    r = mc.tool("propose_purchase", product_id="air-filter")
    pid = r["structuredContent"]["proposal_id"]
    assert r["structuredContent"]["requires_step_up"] is True
    first = mc.tool("approve_purchase", proposal_id=pid, confirm=True)
    assert first["structuredContent"]["requires_step_up"] is True and srv.nudge_service.adapter.calls == 2
    code = srv.nudge_service.session["code"]
    assert code not in json.dumps(first) and code not in json.dumps(r)
    assert any(code in m["text"] for m in srv.nudge_service.phone)             # only the simulated phone has it
    wrong = "0000" if code != "0000" else "1111"
    bad = mc.tool("approve_purchase", proposal_id=pid, confirm=True, step_up_code=wrong)
    assert bad["isError"] is True and bad["structuredContent"]["ok"] is False and bad["structuredContent"]["code"] == "STEPUP_WRONG"
    ok = mc.tool("approve_purchase", proposal_id=pid, confirm=True, step_up_code=code)
    assert ok["isError"] is False and srv.nudge_service.adapter.calls == 3


def test_code_before_yes_is_refused(mc):
    pid = mc.tool("propose_purchase", product_id="air-filter")["structuredContent"]["proposal_id"]
    assert mc.tool("approve_purchase", proposal_id=pid, confirm=True, step_up_code="1234")["isError"] is True


def test_confirm_false_declines(srv, mc):
    pid = mc.tool("propose_purchase", product_id="pasta")["structuredContent"]["proposal_id"]
    r = mc.tool("approve_purchase", proposal_id=pid, confirm=False)
    assert r["isError"] is False and srv.nudge_service._status("O-003") == "DENIED"


def test_undo_over_mcp_and_receipts_verify(srv, mc):
    pid = mc.tool("propose_purchase", product_id="pasta")["structuredContent"]["proposal_id"]
    mc.tool("approve_purchase", proposal_id=pid, confirm=True)
    assert mc.tool("undo_purchase", order_id="O-003")["structuredContent"]["order"]["status"] == "CANCELLED"
    v = mc.tool("verify_receipts")["structuredContent"]
    assert v["ok"] and v["records"] > 10


def test_set_autonomy_lowering_is_immediate_and_raising_is_only_a_proposal(srv, mc):
    svc = srv.nudge_service
    r = mc.tool("set_autonomy", category="household", tier="ask")
    sc = r["structuredContent"]
    assert svc.cats["household"]["tier"] == "suggest" and sc["needs_confirmation"] is True and sc["proposal_id"]
    mc.tool("set_autonomy", category="groceries", tier="suggest")           # lowering applies at once
    assert svc.cats["groceries"]["tier"] == "suggest"


def test_mandate_and_pause_and_decisions_and_daily_check(mc):
    m = mc.tool("get_mandate", category="groceries")["structuredContent"]["categories"]["groceries"]
    assert m["tier"] == "ask" and "asks for your yes" in m["sentence"]
    assert mc.tool("pause_autonomy", paused=True)["isError"] is False
    assert mc.tool("pause_autonomy", paused=False, category="gifts")["isError"] is False
    chk = mc.tool("run_daily_check")["structuredContent"]
    assert chk["ok"] and "feed" in chk
    dec = mc.tool("list_recent_decisions", limit=5)["structuredContent"]
    assert isinstance(dec["orders"], list)


def test_unknown_outcome_over_mcp_is_never_ok_true_and_reconciles_without_double_charge(srv, mc):
    svc = srv.nudge_service
    svc.set_fault("timeout_after_accept")
    pid = mc.tool("propose_purchase", product_id="pasta")["structuredContent"]["proposal_id"]
    done = mc.tool("approve_purchase", proposal_id=pid, confirm=True)
    assert svc._status("O-003") == "UNKNOWN"
    assert done["structuredContent"]["ok"] is False and done["structuredContent"]["code"] == "OUTCOME_UNKNOWN"
    r = mc.tool("reconcile_order", order_id="O-003")
    assert r["structuredContent"]["order"]["status"] == "SUCCEEDED" and len(svc.adapter.accepted) == 3


def test_graduation_is_still_earned_through_mcp_only(srv, mc):
    svc = srv.nudge_service
    pid = mc.tool("propose_purchase", product_id="pasta")["structuredContent"]["proposal_id"]
    mc.tool("approve_purchase", proposal_id=pid, confirm=True)
    assert "groceries" in svc.offers                               # third approval, offer made, nothing granted
    assert svc.cats["groceries"]["tier"] == "ask"
