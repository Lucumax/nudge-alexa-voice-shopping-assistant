# Nudge

**A voice shopping helper that asks before it spends your money, and earns more freedom only when you give it.**

Nudge is a simulated Alexa+ shopping assistant. It suggests everyday items and shows why. It reads each order back before you approve it. After you approve three orders in a category at Ask first, it can offer to buy small items there on its own, inside limits you set and can take back at any time. Every purchase step leaves a receipt you can check.

> **Simulation only.** Sample products, sample prices, a mock payment step and a simulated phone. No real card, store or money is involved. Nudge makes no external store or payment calls; the page talks only to the local server that serves it.

## Why it matters

Repeat errands such as coffee, pasta and paper towels are a good job for a voice assistant, but only if you stay in charge of the money. A common design offers a single switch: the assistant either asks every time or is trusted with a card. Nudge explores the space in between:

- a per-category dial with three levels: Suggest, Ask first and Within limits;
- a plain-words read-back before every purchase that needs your yes;
- a 4-digit code on a simulated phone for orders of $100 or more;
- undo for 24 hours, and a step back to Ask first when you report a problem with, or undo, an order Nudge bought on its own;
- signed, hash-chained receipts: each receipt carries a fingerprint (hash) of the one before it, so Verify receipts detects a later edit to any of them.

Under the hood, the page talks to Nudge through MCP (Model Context Protocol, an open standard that lets an AI assistant call outside tools). A connected assistant can propose a purchase through the same tools, but only the person on the page can say yes.

## Try it in three commands

You need Python 3.11 or later. Running the app needs no installed packages. From the repository folder:

```sh
python -m app                                   # 1. start Nudge, then open http://127.0.0.1:8765/
python -m pip install -r requirements-dev.txt   # 2. install pytest (needed only for the tests)
python -m pytest -q                             # 3. run the tests: 297 pass
```

On macOS and Linux, use `python3`. The first start loads the demo profile "Maya". To start over, run `python -m app --reset-demo`. More options and a suggested walkthrough are in [Run and test](#run-and-test).

## About this entry

Nudge was built for the Amazon Build, Ship, Shape hackathon (Alexa+ track, simulated-experience path). It is independent work and is not affiliated with or endorsed by Amazon. Amazon store names and Amazon private-label brand names appear in the demo because this entry is for the Amazon hackathon. The products, prices, ratings and delivery dates are sample data: nothing is sold or priced by Amazon, and no real Amazon service is called.

License: MIT (see [LICENSE](LICENSE)).

Track: Alexa+, simulated-experience path.

Mini challenges: none entered.

## Contents

- [Why it matters](#why-it-matters)
- [Try it in three commands](#try-it-in-three-commands)
- [How it works](#how-it-works)
- [Architecture](#architecture)
- [MCP server](#mcp-server)
- [Run and test](#run-and-test)
- [Repository layout](#repository-layout)
- [What is simulated](#what-is-simulated)
- [Security model as enforced](#security-model-as-enforced)
- [Limitations](#limitations)
- [Built during the hackathon period](#built-during-the-hackathon-period)
- [License](#license)

## How it works

### Three autonomy tiers, per category

| Tier | Behavior |
|---|---|
| Suggest | Shows ideas with a reason. The daily check never prepares an order for a Suggest category. An explicit Review and buy still prepares one, and it waits for an explicit yes. |
| Ask first | Prepares an order and waits for an explicit yes. |
| Within limits | Buys small items on its own, inside a per-item limit, a per-day limit, a store list and an end date. The user chooses the two limits; the stores are the ones already used in that category's approved orders, and the end date defaults to 90 days. Reports what it bought and what it chose not to buy, and why. |

Autonomy is earned, not defaulted. Three approved orders in a category make the next step available: from Suggest the offer is Ask first, and from Ask first the offer is Within limits. The Within limits offer shows the exact terms (per-item limit, per-day limit, stores, end date) and is confirmed by a person on a separate step that names the exact proposal shown, so a replacement offer cannot be accepted from a stale page. The proposal is single use, expires after 30 minutes on the demo clock, and is refused if the tier or either limit changed since it was shown. The daily limit is exactly what the user states and may not be below the per-item limit; an inconsistent pair is rejected rather than adjusted. Reporting a problem on, or undoing, an order the assistant bought alone resets a Within limits category to Ask first. Lowering or pausing autonomy applies immediately to automatic purchases.

### Starting points

- Fresh start (Demo controls, "fresh"): every category is disabled at Suggest, there are no saved orders or activity, and nothing has been bought.
- Demo profile "Maya" (the default when the app starts for the first time): all categories are enabled, groceries is at Ask first and the other categories are at Suggest, and two grocery orders were approved in the previous calendar week (October 14 and 15; the demo clock starts on October 21), so one more approval triggers the Within limits offer.

### Read-back and what a yes binds to

Before any purchase that needs approval (Suggest and Ask first, and any order of $100 or more) the assistant reads back the item, the quantity when it is more than one, the store, the amount, the delivery date and the card last four. Nothing is bought until the person says or taps yes. Purchases the assistant makes inside approved Within limits terms proceed without an individual yes; they are reported afterwards with the reason.

Each read-back carries a proposal id. A yes, a no and a step-up code must name the proposal currently shown, so a yes for an older read-back approves nothing. Proposal ids keep counting up across demo resets and restarts, so an id shown before a reset or a restart cannot match a new read-back or autonomy offer. The proposal also carries a hash of the full shopping terms: product id, quantity, delivery promise, card last four, unit price and total. A 16-character prefix of that hash is placed in the order's idempotency key, which is part of the signed receipt; the full terms are kept in the saved state. At yes time the stored terms are re-checked against the catalog price and the receipt. If the price or terms changed, the yes is refused and nothing is bought.

The core's own approval digest covers store, amount, category and mandate. It does not cover product, quantity or delivery, which is why the application adds this binding.

### Step-up code for $100 or more

Orders of $100 or more need a 4-digit code. The code is generated after the first yes and appears on the simulated phone panel, never in the conversation. A code counts only when it is the whole message. Autonomy is capped below the step-up amount, so no automatic purchase can skip the second check. A wrong, locked or expired code is an error, never a success.

### Undo for 24 hours

An order can be undone within 24 hours of its completion. The window is computed from the original completion time; later events such as a problem report do not extend it.

### Reconcile on unknown outcomes

If the payment step does not answer, the order is recorded as unknown, the assistant says so ("Not sure yet"), and buying the same item again is refused until the order is settled with Check with the store. That check re-uses the original idempotency key, so in this mock payment workflow one order produces at most one charge. No claim is made about any real payment provider.

### Signed, hash-chained receipts

Every purchase-state transition (approval, completion, unknown outcome, reconciliation, undo and problem report) writes a receipt. Receipts are chained by hash and signed with HMAC. Dismissals, pauses and rule changes are recorded in the activity feed and the saved state, not as signed receipts. Verify receipts recomputes the chain against a trusted checkpoint and names the first record that changed. The demo's tamper control edits a copy and shows the first broken record. Verification detects edits; it does not prove that a recorded fact was true.

### Other behavior

- Every idea card shows its reason and a longer "Why am I seeing this?". Dismissing an idea asks why and applies a fixed rule: the idea is hidden for a period, and after repeated dismissals fewer ideas are shown. This is deterministic, not a learned model.
- No urgency language, no countdown timers, no inferred life events.
- A daily check runs the Within limits categories and reports what it did not buy and why.
- Voice commands such as "What is my rule?", "Pause groceries", "Set groceries limit to 30 dollars", "Approve my digest" and "Undo my last order" are handled by a rule-based parser. Negated or reported requests ("do not resume groceries", "I didn't ask you to undo") do nothing, and so do questions and loose mentions ("what does undo mean"): undo, resume and settings changes need an explicit command form. An explicit stop or pause outranks any other word in the same sentence, including during an approval, so "Stop, don't buy it" closes the open read-back; a stop that is itself negated ("do not pause", "don't stop") does nothing. Limits per week or month are refused; only per-item and per-day limits exist.

## Architecture

```
browser page (static HTML/CSS/JS)
   |  JSON-RPC 2.0 over HTTP (Streamable HTTP, protocol 2025-11-25)
   v
/mcp  -- app/mcp.py: MCP server, typed tools, sessions
   |                                          ^
   |  same service methods                    |
   v                                          |
app/service.py  <---- REST: GET /api/state, POST /api/* (page token)
   tiers, cards, proposals, sessions, undo, digest
   |
   v
vendor/approval_receipt   policy check, engine, mock payment adapter,
                          signed hash-chained ledger, checkpoint
```

- `app/mcp.py` is the MCP server. The web page is a genuine MCP client: it sends `initialize`, `notifications/initialized`, `tools/list` and `tools/call`. A disclosure under the assistant panel lists the calls it makes. Buttons for proposing, approving, declining, undoing, reconciling, reporting a problem, pausing, changing autonomy, the daily check and verification call MCP tools. While a read-back awaits confirmation, the common typed or spoken yes forms (yes, yes please, yeah, yep, sure, okay, confirm) and no forms (no, no thanks, nope, cancel) also go directly through the `approve_purchase` tool. Other typed or spoken text, the code boxes and offers go through `/api/voice` to the rule-based language layer in `app/voice.py` or through REST routes, as the code does, and so do offer acceptance, autonomy confirmation and the demo controls. The page reads its state from `GET /api/state`; it does not render the cards in MCP results directly.
- `app/service.py` holds the product logic. Suggest and Ask first use a mandate with a per-payment limit of 0, so every order escalates to a human yes. Within limits uses a second mandate with real limits, and the application routes to it only when the core's pure `check()` returns ALLOW.
- REST endpoints and MCP tools call the same service methods, so there is one authorization path.

## MCP server

`app/mcp.py` is a stdlib-only MCP server over Streamable HTTP for protocol revision 2025-11-25, served at `/mcp`.

### Typed tools

`search_products`, `suggest_items`, `dismiss_suggestion`, `propose_purchase`, `start_digest`, `approve_purchase`, `deny_purchase`, `undo_purchase`, `report_problem`, `list_recent_decisions`, `get_mandate`, `set_autonomy`, `pause_autonomy`, `run_daily_check`, `reconcile_order`, `verify_receipts`.

Each tool has a JSON Schema input. Results carry card and carousel payloads as `structuredContent`, and the same data is serialized as a second text block for clients that ignore `structuredContent`. The bundled web client does not draw its cards from these payloads; it renders from the REST state snapshot. `SKILL.md` describes the workflow for an agent host.

### Protocol behavior

- Only revision 2025-11-25 is supported. An `initialize` asking for another revision receives 2025-11-25 in reply.
- `initialize` requires `protocolVersion`, `capabilities` and `clientInfo` with name and version. Request ids must be strings or integers. `params` must be an object. JSON-RPC batches are refused.
- After `initialize`, every well-formed POST (request, notification or client response) and every DELETE needs `Mcp-Session-Id` and an `MCP-Protocol-Version` equal to the negotiated one. A missing or wrong value gets HTTP 400; an unknown or ended session gets 404. A session belongs to the token that created it. Messages that are not valid JSON-RPC envelopes are rejected before these checks.
- Tool argument problems return a tool result with `isError: true`. Unknown tool names and a malformed `tools/call` are JSON-RPC errors (-32602). `approve_purchase` can return `ok: true` for a step that is not a completed purchase: with `stage: "stepup"` when a code is now required, and when the answer is a decline. Only a result with `stage: "done"` and no failed outcome means the purchase completed. Wrong, locked and expired codes are errors, and an unknown outcome is `ok: false` with code `OUTCOME_UNKNOWN` and `isError: false`.
- JSON-RPC error codes: -32700 parse, -32600 invalid request, -32601 unknown method, -32602 invalid params. For a missing header or version mismatch the server uses -32000, and for an unknown session -32001. These two are implementation choices, not values from the specification.
- `Accept` must list `application/json`; `Content-Type` must be `application/json`. Replies are plain JSON; there is no SSE stream, so `GET /mcp` returns 405. `DELETE /mcp` ends a session. The bundled client is written for this JSON-only server; a general Streamable HTTP client must also be prepared for SSE replies.
- Not implemented: resources, prompts, sampling, elicitation, pagination, resumability, server notifications and OAuth 2.1.

### Who may do what

Two per-run bearer tokens decide who is calling, and the server alone derives the role from the token:

- The page's token identifies a person. It is embedded in the page the server serves.
- A host token, printed at startup and not in the page, identifies an agent. It may use the MCP endpoint only.

A host can search, suggest, propose a purchase, decline, undo, report a problem, pause, lower autonomy, run the daily check and verify receipts. A host cannot say yes to a purchase, enter a step-up code, confirm a raise in autonomy, accept an offer or resume after a pause. Those need the page's token. No request field can change the role.

## Run and test

Requirements: Python 3.11 or later. The application uses the standard library only. Tests need `pytest`. Node.js is optional; without it one front-end test is skipped.

### Windows (PowerShell)

```powershell
python -m app                  # then open http://127.0.0.1:8765/
python -m app --reset-demo     # demo only: delete saved state and receipts, start over
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

### macOS and Linux

```sh
python3 -m app                 # then open http://127.0.0.1:8765/
python3 -m app --reset-demo    # demo only: delete saved state and receipts, start over
python3 -m pip install -r requirements-dev.txt
python3 -m pytest -q
```

Options: `--port` (default 8765), `--host` (loopback addresses only), `--data-dir` (default `.nudge-data`).

State (receipts, saved state and a checkpoint in its own subfolder) is kept in `.nudge-data/`. Starting again restores that state after verifying the receipts and never deletes anything. If the receipts fail verification, if one file exists without the other, or if the saved state disagrees with the verified receipts, startup stops with a message and changes nothing. The comparison covers the chain position, the list of purchases, the matching payments and refunds, each order's amount, store and category against its receipt, and each order's quantity, unit price, total and delivery against its saved terms snapshot. Starting over is the explicit `--reset-demo` command or the page's Demo controls.

### Tests

`python -m pytest -q` reports 297 passed. It covers the application tests, the adversarial regression tests in `tests/test_hardening.py`, the MCP checks in `tests/test_mcp.py`, the regression files `tests/test_review_fixes.py`, `tests/test_final_fixes.py` and `tests/test_demo_path.py`, and one top-level test that runs the vendored core's own suite (`vendor/core_tests`, 67 tests) in a subprocess; those 67 are not counted individually in that total. The explainer has its own 12 tests, which are not part of that run (`python -m pytest -q explainer/test_explainer.py`).

### A suggested walkthrough (demo profile "Maya", the default)

1. On the Today tab, dismiss an idea and say why.
2. Tap Review and buy. Read the read-back and say or type yes.
3. Buy the large item (over $100) and enter the code shown on the simulated phone.
4. Approve a third grocery order, confirm the Within limits offer, then run the daily check.
5. Report a problem on an order bought automatically and see the category reset to Ask first.
6. In Demo controls make the next payment time out, see "Not sure yet", then use Check with the store.
7. Undo an order, then open Orders and receipts and run Verify receipts. Use the tamper control to see the first broken record named.

## Repository layout

```
app/                    Nudge application (stdlib only)
  __main__.py           command line entry point
  server.py             HTTP server, Host/Origin checks, REST endpoints
  mcp.py                MCP server (Streamable HTTP, 2025-11-25)
  service.py            tiers, proposals, step-up, undo, digest, persistence
  voice.py              rule-based language layer
  catalog.py            sample products
  texts.py, svg.py      string loading, generated imagery
  static/               web client (index.html, app.js, style.css)
                        img/products/ holds the 12 sample product photos
copy/strings.json       user-facing interface strings used by the app
tests/                  application, MCP and regression tests
  js/                   node harness for the MCP client recovery test
vendor/
  approval_receipt/     approval-receipt core, version 2.0.0
  core_tests/           the core's own tests
  VENDOR_MANIFEST.json  SHA-256 manifest of the vendored files
  CORE_CHANGES.md       the core's change notes
explainer/              self-contained animated explainer (open index.html)
SKILL.md                workflow notes for an agent host
PR_FAQ.md               working-backwards press release and FAQs
docs/PRODUCT_FEEDBACK.md  feedback on the tools and specifications used
docs/FRICTION_LOG.md    friction encountered while building
requirements-dev.txt    test dependency (pytest)
LICENSE                 MIT
```

## What is simulated

- Store, catalog and prices: sample data in `app/catalog.py`. Store names (Amazon Fresh, Whole Foods Market, Amazon) and private-label product names are used for this Amazon hackathon; the Prime badge and the FREE delivery dates are sample data too.
- Payment: a mock adapter. No card is charged, and no payment or store service is called.
- Phone: a panel in the page that shows the step-up code.
- Identity: a fixed demo profile ("Maya") and a fixed approver session. There is no login.
- Voice: typed input, plus optional browser speech that is shown only if the browser supports it and is off until pressed. The browser's speech service may send audio to its provider.
- Alexa+ itself: no Alexa+ tool, SDK or device is used. The page simulates a voice conversation with captions and cards.
- The explainer: its illustrative catalog (items, prices and stores) differs from the sample catalog in the app.

## Security model as enforced

- The server binds to loopback addresses only; any other `--host` is refused.
- The `Host` header must exactly match `127.0.0.1`, `localhost` or `[::1]` with the server's port. A present `Origin` must exactly match the corresponding `http://` origin. The page and the MCP endpoint refuse cross-site fetch metadata.
- The page's state, which includes simulated phone messages, requires the page token.
- Page, static, REST and MCP responses, including the MCP 401 reply, carry a Content-Security-Policy that includes `frame-ancestors 'none'`.
- Roles come from the token only (see [Who may do what](#who-may-do-what)).
- A yes binds to a proposal id, which is never reused after a reset, and a hash of the shopping terms. A step-up code binds to the open proposal, is limited in attempts and expires.
- The ledger verifies its chain and signatures on every read and before every append, and fails closed if a checkpoint is missing, rolled back or inconsistent.
- The application runs the core in its demo key mode unless `APPROVAL_RECEIPT_HMAC_KEY` is set. Demo mode is stamped on every receipt. Receipts signed with the public demo key prove nothing to a third party.

This is a local demonstration. Anyone who can load the page from the same machine can read its token.

## Limitations

- Simulated store and payment. There is no real purchase, store, card, phone or voice device.
- Demo identity. A spoken or typed yes is not proof of identity. The demo has no login, a fixed approver session and one human session. The person role means "holds the page's token", and a yes is bound to the open proposal, not to a particular browser tab.
- Per-run tokens. Both bearer tokens are generated for each run. There is no OAuth or account system.
- Rule-based language layer. `app/voice.py` is a small parser. Unusual phrasing gets a help message, and it has been exercised against sample phrases only. "Buy the paper towels" as a spoken command is not supported; purchases start from the cards and buttons.
- Receipt chain scope. The chain detects changes against a trusted checkpoint. It does not prove the recorded facts were true. The checkpoint is in its own folder but on the same disk as the receipts, so an attacker who can replace both is not detected.
- Persistence is local files for one process. A crash between a receipt write and a state write can leave the saved state one step behind the receipts. At startup the saved state is compared with the verified receipts (same point in the chain, same purchases, matching payments and refunds, and each order's amount, store, category, quantity and terms snapshot). If they disagree, Nudge stops with a clear message and changes nothing; it does not attempt automatic recovery. When they agree, unknown orders are reconciled and the recovered state is saved. If a save fails while an action is under way, the action may or may not have been recorded: Nudge answers with a storage error that says so, refuses every further action until it is restarted, and the restart checks the saved state against the receipts. It is not a database and is not multi-user.
- Limits exist per item and per day only. There is no weekly or monthly window.
- MCP features not implemented: resources, prompts, sampling, elicitation, pagination, resumability, server notifications, SSE streams and OAuth 2.1. Only revision 2025-11-25 is supported.
- Conformance. The MCP server was checked with this repository's own tests (`tests/test_mcp.py`, `tests/test_hardening.py`), not with an official conformance suite, and it is a limited implementation rather than a general-purpose one. Some transport details, noted above, are implementation choices.
- Front end. The page's HTTP and MCP calls are covered by tests, and its MCP client recovery (re-initialize after a 404, no cached failure, non-JSON errors) is tested under Node without a browser. Layout, focus behavior, speech and accessibility have not been audited with a screen reader or across browsers.
- Catalog and suggestions. The catalog is twelve sample products and the suggestions come from eight fixed signals. Product names and descriptions are in `app/catalog.py`; the interface strings are in `copy/strings.json`.
- Historical wording. Order descriptions in the receipts view are rendered from the current sample catalog and settings, so they would change if those changed. The signed receipts themselves do not.
- Nudge makes no compliance or certification claim and is not production ready. No user study has been run, so there is no usage evidence.

## Built during the hackathon period

Written for this hackathon, in October 2026: everything in `app/`, `copy/`, `tests/`, `explainer/`, `SKILL.md`, `PR_FAQ.md` and the documents in this repository, including the MCP server and client, the tier and proposal logic, the terms binding, the step-up flow, the language layer, and the web client.

Vendored from an earlier project: `vendor/approval_receipt/` and `vendor/core_tests/` are an earlier approval-receipt core by the same author, version 2.0.0, copied unmodified. They predate this project. Their integrity is checked two ways: `vendor/VENDOR_MANIFEST.json` lists a SHA-256 hash per package file, and a test in `tests/test_server_and_standards.py` runs the core's own manifest check, so a local edit is caught even in a standalone clone. The manifest confirms that the files match the included hashes; it does not by itself establish where they came from or when they were written. The core's tests run in place from `vendor/core_tests`. To check by hand, from inside `vendor/`:

```sh
python -m approval_receipt manifest --check --root .
```

or from the repository root:

```sh
PYTHONPATH=vendor python -m approval_receipt manifest --check --root vendor
```

In PowerShell, from the repository root:

```powershell
$env:PYTHONPATH = "vendor"
python -m approval_receipt manifest --check --root vendor
```
 What the core provides: the policy check, the engine with approval and reconcile, a mock payment adapter, and the signed hash-chained ledger with checkpoint. What this project adds on top: product tiers mapped onto core mandates, the proposal id and terms hash, the step-up flow, undo, the daily check, earned autonomy and reset, the MCP server and client, the rule-based language layer, and the web interface. Where the core lacks a feature (cancel or refund events, a weekly window, per-category mandates), the application works around it at the application level.

## License

MIT. See [LICENSE](LICENSE).

## Notices

Amazon, Alexa+ and related names, marks and page designs are the property of Amazon.com, Inc. or its affiliates, shown here only to illustrate an entry in the Build, Ship, Shape Amazon Developer Hackathon. Nudge is an independent simulation, not affiliated with or endorsed by Amazon. All rights remain with their owners.
