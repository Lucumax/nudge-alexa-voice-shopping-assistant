"""Tiny local web server (standard library), loopback only.

Trust model: two per-run tokens. The page's token (X-Nudge-Token, and Bearer on /mcp) identifies a PERSON.
The host token (Bearer on /mcp only, printed at startup) identifies an AGENT. The server derives the actor
from the token and passes it to the service; no request field can change it. Host and Origin are checked
against an exact allow-list that includes the port. Nothing sensitive is served without a token."""
from __future__ import annotations

import ipaddress
import json
import logging
import os
import secrets
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import catalog as C
from . import texts as T
from .mcp import McpServer, PARSE_ERROR, INVALID_REQUEST
from .service import AGENT, HUMAN, NudgeService
from .svg import product_svg

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".js": "text/javascript; charset=utf-8"}
MAX_BODY = 20_000
CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
       "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


def _int(v, lo, hi):
    if type(v) is not int or not (lo <= v <= hi):
        raise ValueError("range")
    return v


def _str(v):
    if not isinstance(v, str) or len(v) > 300:
        raise ValueError("str")
    return v


def _cat(v):
    if v not in C.CATEGORIES:
        raise ValueError("cat")
    return v


def dispatch(svc: NudgeService, action: str, b: dict) -> dict:
    """Map a page API action to a service call. Only the page's token reaches this function, so the actor
    is a person. Raises ValueError on bad input, KeyError on unknown action."""
    who = HUMAN
    if action == "voice":
        pid = b.get("proposal")
        return svc.handle_voice(_str(b.get("text", "")), None if pid is None else _str(pid), who)
    if action == "shop":
        return svc.shop(_str(b.get("product")), _str(b.get("source", "shop")), _int(b["qty"], 1, 5) if "qty" in b else None)
    if action == "approve_order":
        return svc.start_approval([_str(b.get("order"))])
    if action == "answer":
        code = b.get("code")
        if type(b.get("confirm")) is not bool:
            raise ValueError("confirm")
        return svc.answer(_str(b.get("proposal")), b["confirm"], None if code is None else _str(code), who)
    if action == "code":
        return svc.submit_code(_str(b.get("code")), _str(b.get("proposal")), who)
    if action == "digest":
        ex = b.get("exclude", [])
        if not isinstance(ex, list) or len(ex) > 50:
            raise ValueError("exclude")
        return svc.start_digest([_str(x) for x in ex])
    if action == "decline":
        return svc.decline_order(_str(b.get("order")))
    if action == "undo":
        return svc.undo(_str(b.get("order")))
    if action == "report":
        return svc.report_problem(_str(b.get("order")), _str(b.get("why")))
    if action == "reconcile":
        return svc.reconcile(_str(b.get("order")))
    if action == "retry":
        return svc.retry(_str(b.get("order")))
    if action == "dismiss":
        return svc.dismiss(_str(b.get("id")), _str(b.get("why")))
    if action == "tier":
        return svc.set_tier(_cat(b.get("category")), _str(b.get("tier")), who)
    if action == "limit":
        c = _int(b.get("cents"), 1, 100_000_00)
        kw = {"day_minor": c} if b.get("daily") else {"item_minor": c}
        return svc.set_limits(_cat(b.get("category")), actor=who, **kw)
    if action == "limits":
        return svc.set_limits(_cat(b.get("category")), _int(b.get("item_cents"), 1, 100_000_00),
                              _int(b.get("day_cents"), 1, 100_000_00), who)
    if action == "autonomy_confirm":
        return svc.confirm_autonomy(_str(b.get("proposal")), who)
    if action == "autonomy_cancel":
        return svc.cancel_autonomy(_str(b.get("proposal")), who)
    if action == "enable":
        return svc.set_enabled(_cat(b.get("category")), bool(b.get("on")), who)
    if action == "pause":
        c = b.get("category")
        return svc.pause(_cat(c) if c else None, bool(b.get("on", True)), who)
    if action == "offer_accept":
        return svc.accept_offer(_cat(b.get("category")), who, _str(b.get("proposal")))
    if action == "offer_decline":
        return svc.decline_offer(_cat(b.get("category")), who)
    if action == "run_assistant":
        return svc.run_assistant()
    if action == "advance":
        return svc.advance(_int(b.get("hours"), 1, 24 * 14))
    if action == "fault":
        return svc.set_fault(_str(b.get("name")))
    if action == "profile":          # DEMO ONLY: deletes the receipts and saved state, then starts over
        name = _str(b.get("name"))
        if name not in ("demo", "fresh"):
            raise ValueError("profile")
        svc.reset(name)
        return {"ok": True}
    if action == "verify":
        return svc.verify()
    if action == "tamper":
        return svc.tamper_demo()
    raise KeyError(action)


def is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def make_handler(svc: NudgeService, cfg: dict, mcp: McpServer):
    """cfg is filled by serve() after the port is known: tokens, allowed_hosts, allowed_origins."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "Nudge"

        def log_message(self, fmt, *args):  # keep the console quiet
            pass

        # ---- checks
        def _host_ok(self) -> bool:
            return (self.headers.get("Host") or "").lower() in cfg["allowed_hosts"]

        def _origin_ok(self) -> bool:
            """No Origin (a non-browser client) passes; any Origin must be an exact allowed one."""
            origin = self.headers.get("Origin")
            return origin is None or origin in cfg["allowed_origins"]

        def _site_ok(self) -> bool:
            return self.headers.get("Sec-Fetch-Site") in (None, "same-origin", "none")

        def _gate(self) -> bool:
            if self._host_ok() and self._origin_ok() and self._site_ok():
                return True
            self._forbidden()
            return False

        def _ui_ok(self) -> bool:
            return secrets.compare_digest(self.headers.get("X-Nudge-Token") or "", cfg["token"])

        def _principal(self):
            """Who a bearer token is: HUMAN (the page), AGENT (a host), or None."""
            auth = self.headers.get("Authorization") or ""
            if not auth.startswith("Bearer "):
                return None
            tok = auth[7:]
            if secrets.compare_digest(tok, cfg["token"]):
                return HUMAN
            if secrets.compare_digest(tok, cfg["agent_token"]):
                return AGENT
            return None

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.send_header("Content-Security-Policy", CSP)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj) -> None:
            self._send(code, json.dumps(obj).encode(), "application/json; charset=utf-8")

        def _forbidden(self):
            self._json(403, {"ok": False, "message": T.t("err.FORBIDDEN")})

        def _mcp_gate(self):
            """Host, Origin and bearer checks for /mcp. Returns the principal, or None after replying."""
            if not self._gate():
                return None
            who = self._principal()
            if who is None:
                body = json.dumps({"error": "bearer token required"}).encode()
                self.send_response(401)
                self.send_header("WWW-Authenticate", "Bearer")
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Security-Policy", CSP)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return None
            return who

        def _mcp_post(self, raw: bytes, too_big: bool):
            who = self._mcp_gate()
            if who is None:
                return
            if "application/json" not in (self.headers.get("Accept") or ""):
                return self._json(406, {"error": "Accept must include application/json"})
            if "application/json" not in (self.headers.get("Content-Type") or ""):
                return self._json(415, {"error": "Content-Type must be application/json"})
            if too_big:
                return self._json(413, {"jsonrpc": "2.0", "id": None,
                                        "error": {"code": INVALID_REQUEST, "message": "request too large"}})
            try:
                msg = json.loads(raw)
            except ValueError:
                return self._json(400, {"jsonrpc": "2.0", "id": None, "error": {"code": PARSE_ERROR, "message": "parse error"}})
            status, headers, resp = mcp.handle(msg, self.headers.get("Mcp-Session-Id"),
                                               self.headers.get("MCP-Protocol-Version"), who)
            body = b"" if resp is None else json.dumps(resp).encode()
            self.send_response(status)
            for k, v in headers.items():
                self.send_header(k, v)
            if body:
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", CSP)
            self.end_headers()
            self.wfile.write(body)

        def do_DELETE(self):
            if self.path != "/mcp":
                return self._json(404, {"ok": False})
            who = self._mcp_gate()
            if who is None:
                return
            status = mcp.check_delete(self.headers.get("Mcp-Session-Id"), self.headers.get("MCP-Protocol-Version"), who)
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):
            if self.path == "/mcp":
                if self._mcp_gate() is None:
                    return
                self.send_response(405)  # no server-initiated SSE stream; allowed by the spec
                self.send_header("Allow", "POST, DELETE")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if not self._gate():
                return
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                with open(os.path.join(STATIC, "index.html"), encoding="utf-8") as f:
                    html = f.read()
                html = html.replace("__TOKEN__", cfg["token"]).replace("__TITLE__", T.plain("ui.app_name"))
                return self._send(200, html.encode(), TYPES[".html"])
            if path in ("/style.css", "/app.js"):
                with open(os.path.join(STATIC, path[1:]), "rb") as f:
                    return self._send(200, f.read(), TYPES[os.path.splitext(path)[1]])
            if path.startswith("/img/products/") and path.endswith(".jpg") and path[14:-4] in C.BY_ID:
                with open(os.path.join(STATIC, "img", "products", path[14:]), "rb") as f:
                    return self._send(200, f.read(), "image/jpeg")
            if path.startswith("/svg/") and path.endswith(".svg") and path[5:-4] in C.BY_ID:
                return self._send(200, product_svg(path[5:-4]).encode(), "image/svg+xml")
            if path == "/api/strings":
                return self._json(200, T.for_client())
            if path == "/api/state":
                if not self._ui_ok():       # the state holds the simulated phone messages, so it needs the page token
                    return self._forbidden()
                try:
                    return self._json(200, svc.state())
                except (OSError, RuntimeError):
                    logging.getLogger("nudge").exception("storage error reading state")
                    return self._json(500, {"ok": False, "code": "STORAGE_ERROR", "message": T.t("err.STORAGE_ERROR")})
            return self._json(404, {"ok": False, "message": T.t("err.NOT_FOUND_ROUTE")})

        def do_POST(self):
            try:  # drain the body first so a refusal is never a connection reset
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = 0
            too_big = n > MAX_BODY
            raw = self.rfile.read(n) if 0 < n <= MAX_BODY else b""
            if self.path == "/mcp":
                return self._mcp_post(raw, too_big)
            if not self._gate():
                return
            if not self._ui_ok():
                return self._forbidden()
            if not self.path.startswith("/api/"):
                return self._json(404, {"ok": False, "message": T.t("err.NOT_FOUND_ROUTE")})
            try:
                if too_big:
                    raise ValueError("size")
                body = json.loads(raw or b"{}")
                if not isinstance(body, dict):
                    raise ValueError("shape")
            except ValueError:
                return self._json(400, {"ok": False, "message": T.t("err.BAD_JSON")})
            if svc.needs_restart:      # an earlier save failed: no further action until the service is restarted
                return self._json(503, {"ok": False, "code": "RESTART_REQUIRED", "message": T.t("err.RESTART_REQUIRED")})
            try:
                result = dispatch(svc, self.path[5:], body)
            except (ValueError, TypeError, KeyError):
                return self._json(400, {"ok": False, "message": T.t("err.BAD_REQUEST")})
            except (OSError, RuntimeError):      # disk, ledger or checkpoint trouble: safe typed error, details in the log
                svc.storage_failed()
                logging.getLogger("nudge").exception("storage error handling %s", self.path[:60])
                return self._json(500, {"ok": False, "code": "STORAGE_ERROR", "message": T.t("err.STORAGE_ERROR")})
            try:
                state = svc.state()
            except (OSError, RuntimeError):
                svc.storage_failed()
                logging.getLogger("nudge").exception("storage error reading state")
                return self._json(500, {"ok": False, "code": "STORAGE_ERROR", "message": T.t("err.STORAGE_ERROR")})
            self._json(200, {"result": result, "state": state})

    return Handler


class _Server(ThreadingHTTPServer):
    daemon_threads = True


class _Server6(_Server):
    address_family = socket.AF_INET6


def serve(host: str = "127.0.0.1", port: int = 8765, svc: NudgeService | None = None):
    """Bind to a loopback address only. A non-loopback host raises ValueError: this demo has no real login."""
    if not is_loopback(host):
        raise ValueError(f"refusing to bind to {host!r}: Nudge only listens on loopback addresses")
    svc = svc or NudgeService()
    cfg: dict = {"token": secrets.token_hex(16), "agent_token": secrets.token_hex(16)}
    cls = _Server6 if ":" in host else _Server
    httpd = cls((host, port), make_handler(svc, cfg, McpServer(svc)))
    p = httpd.server_address[1]
    cfg["allowed_hosts"] = {f"127.0.0.1:{p}", f"localhost:{p}", f"[::1]:{p}"}
    cfg["allowed_origins"] = {"http://" + h for h in cfg["allowed_hosts"]}
    httpd.nudge_token, httpd.nudge_agent_token, httpd.nudge_service = cfg["token"], cfg["agent_token"], svc
    return httpd
