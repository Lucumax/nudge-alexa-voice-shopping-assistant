"""HTTP surface, copy standards, no third-party marks, and the untouched vendored core."""
import hashlib
import http.client
import json
import os
import re
import subprocess
import sys
import threading

import pytest

from app import texts as T
from app.server import serve
from app.service import NudgeService

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


@pytest.fixture
def server(tmp_path):
    svc = NudgeService(str(tmp_path / "r.jsonl"), key="k", profile="demo")
    httpd = serve("127.0.0.1", 0, svc)
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    yield httpd
    httpd.shutdown()
    httpd.server_close()


def call(httpd, method, path, body=None, headers=None, host=None):
    port = httpd.server_address[1]
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    h = {"Host": host or f"127.0.0.1:{port}", **(headers or {})}
    if body is not None:
        h["Content-Type"] = "application/json"
    c.request(method, path, json.dumps(body) if body is not None else None, h)
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, data, dict(r.getheaders())


# ---------- server
def test_page_state_strings_and_svg_are_served(server):
    st, html, hdr = call(server, "GET", "/")
    assert st == 200 and b"__TOKEN__" not in html and server.nudge_token.encode() in html
    assert "script-src 'self'" in hdr["Content-Security-Policy"]
    assert call(server, "GET", "/api/state")[0] == 403                     # state holds phone messages: token needed
    assert call(server, "GET", "/api/state", headers={"X-Nudge-Token": server.nudge_token})[0] == 200
    assert json.loads(call(server, "GET", "/api/strings")[1])["ui.app_name"] == "Nudge"
    st, svg, hdr = call(server, "GET", "/svg/pasta.svg")
    assert st == 200 and hdr["Content-Type"] == "image/svg+xml" and svg.startswith(b"<svg")
    assert call(server, "GET", "/svg/../../etc/passwd")[0] == 404
    st, jpg, hdr = call(server, "GET", "/img/products/pasta.jpg")
    assert st == 200 and hdr["Content-Type"] == "image/jpeg" and jpg[:2] == bytes([255, 216])
    assert call(server, "GET", "/img/products/../../app.js")[0] == 404
    assert call(server, "GET", "/img/products/nope.jpg")[0] == 404


def test_state_changing_calls_need_the_token(server):
    assert call(server, "POST", "/api/voice", {"text": "yes"})[0] == 403
    assert call(server, "POST", "/api/voice", {"text": "yes"}, {"X-Nudge-Token": "wrong"})[0] == 403
    ok = call(server, "POST", "/api/voice", {"text": "what is my rule"}, {"X-Nudge-Token": server.nudge_token})
    assert ok[0] == 200


def test_foreign_host_or_origin_is_refused(server):
    tok = {"X-Nudge-Token": server.nudge_token}
    assert call(server, "GET", "/api/state", headers=tok, host="evil.example")[0] == 403
    assert call(server, "POST", "/api/voice", {"text": "x"}, {**tok, "Origin": "http://evil.example"})[0] == 403


def test_bad_input_is_rejected_not_executed(server):
    tok = {"X-Nudge-Token": server.nudge_token}
    assert call(server, "POST", "/api/nope", {}, tok)[0] == 400
    assert call(server, "POST", "/api/shop", {"product": "x" * 999}, tok)[0] == 400
    assert call(server, "POST", "/api/limit", {"category": "groceries", "cents": "30"}, tok)[0] == 400
    assert call(server, "POST", "/api/tier", {"category": "cars", "tier": "limits"}, tok)[0] == 400
    assert server.nudge_service.adapter.calls == 2        # only the two seeded orders ever reached the mock


def test_full_flow_over_http_including_code_and_receipt(server):
    tok = {"X-Nudge-Token": server.nudge_token}
    st, body, _ = call(server, "POST", "/api/shop", {"product": "air-filter", "source": "shop"}, tok)
    assert json.loads(body)["result"]["ok"]
    pid = json.loads(call(server, "GET", "/api/state", headers=tok)[1])["session"]["proposal_id"]
    st, body, _ = call(server, "POST", "/api/voice", {"text": "yes", "proposal": pid}, tok)
    state = json.loads(body)["state"]
    assert state["session"]["stage"] == "stepup" and state["phone"]
    code = re.search(r"code is (\d{4})", state["phone"][0]["text"]).group(1)
    st, body, _ = call(server, "POST", "/api/code", {"code": code, "proposal": pid}, tok)
    assert json.loads(body)["state"]["orders"][0]["status"] == "SUCCEEDED"
    v = json.loads(call(server, "POST", "/api/verify", {}, tok)[1])["result"]
    assert v["ok"]


# ---------- strings
def test_every_string_has_text_and_the_app_loads_them():
    raw = json.loads(read("copy", "strings.json"))
    assert raw["strings"] and all(v["text"].strip() for v in raw["strings"].values())
    assert "DR" + "AFT" not in json.dumps(raw)


def _keys_used():
    keys = set()
    for fn in ("service.py", "server.py", "voice.py"):
        keys |= set(re.findall(r'T\.t\("([A-Za-z_.]+)"', read("app", fn)))
    keys |= set(re.findall(r'data-s(?:-aria)?="([^"]+)"', read("app", "static", "index.html")))
    js = read("app", "static", "app.js")
    keys |= set(re.findall(r'\bS\("([A-Za-z_.]+)"', js))
    return keys


def test_every_key_the_code_uses_exists():
    strings = T.load()
    missing = [k for k in _keys_used() if k not in strings and not any(s.startswith(k) for s in strings)]
    assert missing == []


def test_dynamic_key_families_are_complete():
    s = T.load()
    outcomes = ["PENDING_APPROVAL", "AUTHORIZED", "SUBMITTING", "SUCCEEDED", "FAILED", "UNKNOWN", "BLOCKED", "DENIED", "EXPIRED", "CANCELLED"]
    codes = ["WITHIN_MANDATE", "AMOUNT_OVER_PER_PAYMENT_LIMIT", "DAILY_LIMIT_EXCEEDED", "PAYEE_NOT_ALLOWLISTED",
             "PURPOSE_NOT_PERMITTED", "MANDATE_EXPIRED", "MANDATE_NOT_FOUND", "AMOUNT_NOT_POSITIVE", "CURRENCY_MISMATCH"]
    for o in outcomes:
        assert f"status.{o}" in s and f"statushelp.{o}" in s
    for c in codes:
        assert f"reason.{c}" in s
        assert c == "WITHIN_MANDATE" or f"did_not.{c}" in s
    for k in ("cat.groceries", "tier.ask", "channel.voice", "channel.digest", "source.auto", "reset.why.undo", "reset.why.problem"):
        assert k in s


def test_no_visible_words_are_hard_coded_in_the_front_end():
    html = read("app", "static", "index.html")
    body = re.sub(r"<(script|style|title)[\s\S]*?</\1>", "", html)
    text = re.sub(r"<[^>]+>", " ", body)
    words = re.findall(r"[A-Za-z]{3,}", text)
    assert words == [], words      # every visible word comes from strings via data-s


def test_copy_follows_the_style_rules():
    for key, entry in T.load().items():
        t = entry["text"]
        assert "\u2014" not in t and "\u2013" not in t and " - " not in t, key      # no dashes as punctuation
        assert not re.search(r"\b\d+\+? years?\b", t.lower()) or key.startswith("returns"), key
        assert "!" not in t or key.startswith("ui."), key


def test_no_years_of_experience_or_invented_claims_in_the_repo():
    pat = re.compile(r"\b\d+\+?\s+years?\s+of\s+experience\b|\byears of experience\b", re.I)
    for dirpath, _, files in os.walk(ROOT):
        if any(x in dirpath for x in (".git", "vendor", "__pycache__", ".pytest_cache", "tests")):
            continue
        for f in files:
            if f.endswith((".md", ".json", ".py", ".js", ".html", ".css")):
                assert not pat.search(read(os.path.relpath(os.path.join(dirpath, f), ROOT))), f


# ---------- third-party marks and network
MARKS = ["paypal", "visa", "mastercard", "google", "apple",
         "youtube", "facebook", "walmart", "ebay", "etsy", "airwallex", "bedrock", "aws"]


def test_no_third_party_names_in_the_ui_or_the_strings():
    blob = (read("app", "static", "index.html") + read("app", "static", "app.js") + read("app", "static", "style.css")
            + read("app", "catalog.py") + read("app", "svg.py") + read("copy", "strings.json")).lower()
    for m in MARKS:
        assert not re.search(r"\b" + re.escape(m) + r"\b", blob), m


def test_front_end_and_server_make_no_external_requests():
    files = [read("app", "static", f) for f in ("index.html", "app.js", "style.css")] + [read("app", "server.py"), read("app", "service.py")]
    for src in files:
        for url in re.findall(r"https?://[^\s\"')]+", src):
            assert url.startswith(("http://www.w3.org", "http://127.0.0.1", "http://localhost")), url
    for fn in ("service.py", "server.py", "catalog.py", "voice.py", "texts.py", "svg.py"):
        src = read("app", fn)
        assert not re.search(r"^\s*(import|from)\s+(requests|urllib|http\.client|boto3)", src, re.M), fn
        assert "create_connection" not in src and "urlopen" not in src, fn   # the server only listens (socket for IPv6)


def test_fonts_are_system_fonts_only():
    css = read("app", "static", "style.css")
    assert "@font-face" not in css and "fonts.googleapis" not in css


# ---------- the vendored core
def test_vendored_core_matches_its_committed_manifest():
    """VENDOR_MANIFEST.json came from the core repo with the copy; it is committed, so a local edit to the
    vendored files is caught even in a standalone clone."""
    vendor = os.path.join(ROOT, "vendor")
    assert os.path.exists(os.path.join(vendor, "VENDOR_MANIFEST.json"))
    r = subprocess.run([sys.executable, "-m", "approval_receipt", "manifest", "--check", "--root", "."],
                       cwd=vendor, capture_output=True, text=True, timeout=60,
                       env={**os.environ, "PYTHONPATH": vendor})
    assert r.returncode == 0 and '"manifest_ok": true' in r.stdout, r.stdout + r.stderr


def test_a_modified_vendored_file_would_be_caught(tmp_path):
    import shutil
    dst = tmp_path / "v"
    shutil.copytree(os.path.join(ROOT, "vendor"), dst, ignore=shutil.ignore_patterns("__pycache__", "core_tests"))
    pol = dst / "approval_receipt" / "policy.py"
    pol.write_text(pol.read_text(encoding="utf-8") + "\n# tampered\n", encoding="utf-8")
    r = subprocess.run([sys.executable, "-m", "approval_receipt", "manifest", "--check", "--root", "."],
                       cwd=dst, capture_output=True, text=True, timeout=60, env={**os.environ, "PYTHONPATH": str(dst)})
    assert r.returncode != 0 and "MODIFIED" in r.stdout.upper()


def test_vendored_core_tests_pass_in_place():
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-c",
                        os.path.join("vendor", "core_tests", "pytest.ini"), os.path.join("vendor", "core_tests")],
                       cwd=ROOT, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout[-800:] + r.stderr[-400:]
    assert "passed" in r.stdout
