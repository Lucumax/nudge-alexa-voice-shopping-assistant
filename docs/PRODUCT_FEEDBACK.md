# Product feedback

Feedback for the Amazon team on the tools, specifications and platform used to build Nudge for the Amazon Build, Ship, Shape hackathon. Each entry answers the five questions from the hackathon rules: what the tool was used for, what worked, what needs work, the onboarding experience, and whether the project would build with it again. Onboarding sections state only what documentation and access existed, so each statement can be checked against the public pages and this repository.

## 1. Model Context Protocol, specification revision 2025-11-25, Streamable HTTP

**Used for.** A standard-library MCP server (`app/mcp.py`) and a browser MCP client (`app/static/app.js`). Implemented: `initialize`, `notifications/initialized`, `ping`, `tools/list`, `tools/call`, session ids, protocol-version headers, JSON-RPC error codes and `DELETE` to end a session. Sixteen typed tools carry the shopping workflow. Replies are plain JSON; there is no SSE stream.

**What worked.**
- Typed tools with JSON Schema inputs mapped directly onto the human-in-the-loop steps. Splitting `propose_purchase` from `approve_purchase` made the rule "a purchase that needs approval is not made before a yes" simple to enforce and to test. (Purchases made inside approved Within limits terms do not wait for an individual yes.)
- Separating the transport envelope (HTTP status, headers, session) from tool results (`isError`) gave a clear place for each kind of failure.
- Results can carry card and carousel payloads as `structuredContent` with the same data in a text block. The bundled page renders from its REST state snapshot rather than from these payloads, so the payload format is covered by the MCP tests but not drawn by the page.

**What needs work.**
- There is no protocol-level way for a tool to require a human confirmation that the calling host cannot supply itself. Nudge solves this at the application layer: a host token can only propose, and approval needs a different token held by the page. Elicitation was not implemented or tested here, and it would not by itself show who answered.
- Some transport details are left to implementers. Nudge made these choices and documents them in the README: HTTP 400 for envelope errors versus 200 for method-level JSON-RPC errors, the numeric codes for a missing header or an unknown session, and the response when `MCP-Protocol-Version` is absent on a server that supports a single revision.
- No official conformance suite was run. Nudge's checks are its own (`tests/test_mcp.py`, `tests/test_hardening.py`). An official test kit for Streamable HTTP servers would let implementers check their work against the same expectations.

**Onboarding experience.** The specification for revision 2025-11-25 is published online and was the reference for the implementation. No SDK was used, which was a choice to keep the application dependency-free. No official conformance suite was run; verification was by the repository's own tests.

**Would build with it again.** Yes. MCP gave Nudge an open, typed way to expose a shopping workflow to an assistant host, and the same tool layer serves both the web client and any other host.

## 2. Alexa+ simulated-experience path (no Alexa+ developer tools used)

**Used for.** Nothing from Alexa+ itself is called. The simulated-experience path was chosen, and the project simulates a voice conversation in a web page with captions, cards, optional browser speech and a simulated phone panel for the step-up code. The hackathon FAQ confirms that a browser MCP client is an acceptable surface for this path.

**What worked.** The path needs no Alexa+ account, device, SDK or AWS service, so the whole approval and purchase flow could be built and tested end to end. Designing around a read-back, a second factor outside the conversation for large orders, and card-based results gave a clear structure for a purchase flow.

**What needs work.**
- A public simulator that renders MCP tool results as cards and carousels would let builders test a flow on the intended surface. Nudge built its own page and phone panel for this.
- Documentation of how tool results map to cards and carousels on the intended surface.

**Onboarding experience.** The hackathon FAQ states that the Alexa+ developer tools (MCP toolkit, category SDK, command line and web simulator) are in preview and available to select partners only, so no tool onboarding applied to this entry. See friction log entry 1.

**Would build with it again.** Yes. The first change with access to real Alexa+ tooling would be to replace the simulated voice and phone panel with the real ones.

## 3. Devpost submission requirements

**Used for.** Planning and assembling the submission: a repository with source and run instructions, an open-source license, per-tool product feedback, an optional friction log and optional feature requests, track and mini-challenge identification, and a disclosure of pre-existing work.

**What worked.** The requirement to disclose pre-existing work and explain what changed in the hackathon period led to a clear separation in this repository between the vendored approval-receipt core and the new code (see the README section "Built during the hackathon period"). The optional friction log and feature request entries have defined fields, which made them straightforward to structure.

**What needs work.**
- Programmatic requests to the rules and judging pages returned HTTP 403 during planning (see friction log entry 2). A plain-text or machine-readable version of the criteria would help builders who use tooling to track requirements.

**Onboarding experience.** The requirements are published on the hackathon's rules and FAQ pages. The rules list the per-tool feedback questions, and the FAQ describes tool availability.

**Would build with it again.** Yes, with the clarifications above.

## 4. Approval-receipt core, version 2.0.0 (an earlier library by the same author, vendored)

**Used for.** Policy checks, the engine with approval and reconcile, a mock payment adapter, and the signed hash-chained ledger with verification. It is used unmodified and checked against `vendor/VENDOR_MANIFEST.json`. Its own tests (`vendor/core_tests`, 67 tests) pass in place.

**What worked.** Deterministic decisions, a single-use approval nonce bound to the terms, reconcile that re-uses the same idempotency key, and tamper detection that names the first broken record.

**What needs work.** The application worked around these gaps: no cancel or refund event (undo appends a CANCELLED receipt directly), step-up and channel evidence stored as text in the approver note, no weekly budget window, one mandate per agent id, reserved spend not exposed publicly, and an approval digest that does not cover product, quantity or delivery (the application adds a terms hash). The manifest command's `--root` value depends on the working directory, which is easy to get wrong (friction log entry 8).

**Onboarding experience.** This is not an Amazon tool. The documentation available to an integrator is `vendor/CORE_CHANGES.md` (changes, migration notes and the manifest procedure), docstrings in `vendor/approval_receipt/`, a command-line demo (`python -m approval_receipt`) and the core's own tests under `vendor/core_tests`.

**Would build with it again.** Yes, after the gaps listed above are closed in the core itself.

## 5. Other tooling: Python standard library, pytest, Node.js, browser speech APIs

**Used for.** `http.server` and `json` for the server, `pytest` for the 297-result suite (one of its tests runs the 67 vendored core tests in a subprocess; the explainer's 12 tests are separate), Node.js for one front-end recovery test, and optional `SpeechSynthesis` and `SpeechRecognition` behind feature checks.

**What worked.** The application needs no installed packages, and the test suite runs without a browser.

**What needs work.** Browser speech support differs between browsers, and the recognition service may send audio to its provider. The speech features are not exercised by this repository's tests.

**Onboarding experience.** All four are general-purpose tools with public documentation, independent of the hackathon platform; none required an account.

**Would build with it again.** Yes.

## 6. AI development tools: Claude Code, Codex and Kokoro

**Used for.** Claude Code and Codex were used for development and review of the code, tests and documents in this repository. Kokoro was used to produce the narration audio.

**What worked.** Each could be run locally against the repository, and the test suites could be re-run after every change.

**What needs work.** Generated code and text needed checking against the running application and the tests, so every behavior this repository describes has a test or a command that shows it.

**Onboarding experience.** General-purpose tools with public documentation, independent of the hackathon platform.

**Would build with it again.** Yes.

## 7. AWS Builder mini challenge

Not used. No AWS service is called, and no claim is made. Mini challenges: none entered.

## Feature requests

| Request | Why it matters | Priority |
|---|---|---|
| A public web simulator that renders MCP tool results as cards and carousels | Builders on the simulated path could test on the intended surface instead of building their own | Critical |
| A tool-level human-confirmation primitive that a host cannot forge, with a way to show that a specific person answered | Every shopping or payment flow needs to separate "the assistant proposed" from "a person approved" | Critical |
| A conformance test kit for Streamable HTTP servers | Removes guesswork about transport details such as error codes and header handling | Important |
