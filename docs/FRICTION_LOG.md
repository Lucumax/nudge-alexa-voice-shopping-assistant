# Friction log

Friction encountered while building Nudge for the Amazon Build, Ship, Shape hackathon, between 2026-10-20 and 2026-10-22. Each entry gives the date, the task attempted, steps taken, expected versus actual result, severity, workaround and suggestion. Where an entry rests on an observation that is not reproduced in this repository, it says so.

Severity scale: High (blocks a path or changes the plan), Medium (costs significant time or adds risk), Low (minor).

## 1. Alexa+ developer tools were not available to participants

- **Date.** 2026-10-20 (checked again 2026-10-22).
- **Task attempted.** Build the entry on the Alexa+ developer tools (MCP toolkit, category SDK, command line, web simulator).
- **Steps taken.** Read the hackathon FAQ for access details.
- **Expected.** Access to the tools through the hackathon.
- **Actual.** The FAQ states that these tools are in preview and available to select partners only.
- **Severity.** High. It determines the track.
- **Workaround.** Use the simulated-experience path: a web page that simulates the voice conversation, plus a simulated phone.
- **Suggestion.** State tool availability on the hackathon overview page as well as in the FAQ.

## 2. Rules and judging pages returned errors to programmatic requests

- **Date.** 2026-10-20.
- **Task attempted.** Extract the judging criteria and submission requirements into a checklist.
- **Steps taken.** Requested the Devpost rules and judging pages programmatically.
- **Expected.** Page text.
- **Actual.** HTTP 403 responses, recorded in project notes from that date. The Join step also sits behind a bot check. A later attempt on 2026-10-22 retrieved the rules and FAQ, so the behavior may be intermittent. This entry rests on those notes and is not reproduced in the repository.
- **Severity.** Low to Medium. A person may have to read these pages in a browser.
- **Workaround.** Read the pages in a browser.
- **Suggestion.** Publish criteria as plain text or in a machine-readable page.

## 3. No official conformance suite was run for the MCP Streamable HTTP server

- **Date.** 2026-10-20 to 2026-10-21.
- **Task attempted.** Check a hand-written Streamable HTTP server against the specification.
- **Steps taken.** Implemented the server for revision 2025-11-25 and wrote local protocol tests (`tests/test_mcp.py`, `tests/test_hardening.py`). A hardening pass tightened envelope validation: request ids must be strings or integers, `initialize` requires client information, session and version checks apply to client responses as well as requests, and the web client re-initializes after a 404 instead of caching a failure. Each change has a regression test.
- **Expected.** A reference test suite to run.
- **Actual.** Only self-written checks were used, so some transport choices (error codes for a missing header or unknown session, status for method-level errors, behavior when the version header is absent) are implementation decisions.
- **Severity.** Medium.
- **Workaround.** Document each choice in the README and test it locally.
- **Suggestion.** Publish a small official conformance test or reference server.

## 4. No simulator was used for MCP-driven shopping flows

- **Date.** 2026-10-20.
- **Task attempted.** Show tool results as cards and a carousel, and a phone-based step-up code.
- **Steps taken.** Built a custom web page with captions, cards and a phone panel.
- **Expected.** A public simulator that renders tool results.
- **Actual.** No public simulator was used on this path, so a front end had to be built.
- **Severity.** Medium. Time went to the interface rather than to the agent workflow.
- **Workaround.** Custom page and simulated phone.
- **Suggestion.** Offer a public web simulator that renders tool results as cards and carousels.

## 5. A host cannot prove a human answered a confirmation

- **Date.** 2026-10-20 to 2026-10-21.
- **Task attempted.** Let an agent host raise its own autonomy only after a person confirms.
- **Steps taken.** The first version accepted a `user_confirmed` flag on the tool call, which a host could set itself. It was replaced with a two-step flow decided by which token calls: a host token can only propose, and a person confirms the proposal id in the page.
- **Expected.** A protocol feature to require a human confirmation a host cannot supply.
- **Actual.** The protocol offers none for this case, so the boundary is application-specific. Elicitation was not implemented here.
- **Severity.** Medium.
- **Workaround.** Two per-run tokens with roles derived on the server (see README, "Who may do what").
- **Suggestion.** Guidance on identifying the human behind a confirmation.

## 6. Core library gaps found during integration

- **Date.** 2026-10-20.
- **Task attempted.** Add undo, two autonomy mandates per category, and a weekly limit on top of the vendored approval-receipt core.
- **Steps taken.** Integrated core version 2.0.0 unmodified.
- **Expected.** First-class cancel or refund, a weekly window and per-category mandates.
- **Actual.** The core has no cancel or refund event, no weekly window, and one mandate per agent id.
- **Severity.** Low to Medium. This is the author's earlier library, not an Amazon tool.
- **Workaround.** Undo appends a CANCELLED receipt at the application level, each category uses two agent ids (ask and auto) routed by the core's pure `check()`, and limits are per item and per day only.
- **Suggestion.** Not applicable to the hackathon platform.

## 7. Browser speech support varies

- **Date.** 2026-10-21.
- **Task attempted.** Offer optional speech input and read-aloud.
- **Steps taken.** Wrapped both behind feature checks, off until pressed.
- **Expected.** Consistent support.
- **Actual.** Browser support for these APIs differs, and recognition may send audio to a provider, so the feature is optional and typed input is the primary path. The repository's tests do not exercise real browser speech.
- **Severity.** Low.
- **Workaround.** Feature checks and a typed fallback.
- **Suggestion.** None.

## 8. The manifest check depends on the working directory

- **Date.** 2026-10-22.
- **Task attempted.** Document a hand-run integrity check of the vendored core.
- **Steps taken.** Ran `python -m approval_receipt manifest --check --root vendor` from inside `vendor/`.
- **Expected.** The documented command works from either location.
- **Actual.** Inside `vendor/`, `--root vendor` points at a folder that does not exist and the check fails. The command works with `--root .` from inside `vendor/`, or with `PYTHONPATH=vendor` and `--root vendor` from the repository root.
- **Severity.** Low.
- **Workaround.** The README states both forms.
- **Suggestion.** Have the manifest command default to the folder that contains the package.
