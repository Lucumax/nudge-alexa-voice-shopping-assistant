---
name: nudge-shopping
description: Help a person shop by voice with a human in the loop. Use when asked to suggest, prepare, approve, undo or review purchases through the Nudge tools.
---

# Nudge shopping workflow (for an agent host)

Use the Nudge MCP server at /mcp (Streamable HTTP, JSON replies). It is a simulation over a mock payment adapter. Authenticate with the host token printed when the app starts.

1. Call get_mandate and suggest_items. Show each idea with its reason. Never invent a reason.
2. To buy, call propose_purchase. Read the returned text aloud as is: item, quantity when more than one, store, amount, delivery, card last four. Nothing is bought yet.
3. Wait for the human. The human says yes in the Nudge page, naming the proposal_id you were given. With a host token approve_purchase with confirm true is refused, and a yes for any other proposal_id approves nothing (ids keep counting up and are not reused after a demo reset). confirm false (decline) is always allowed.
4. If requires_step_up is true, tell the human that a code will appear on the simulated phone panel in the page once they say yes. You will never see the code, and the human enters it in the page.
5. Offer undo_purchase for 24 hours from completion. If an order outcome is unknown, call reconcile_order. A new proposal for the same product is refused until it is settled.
6. Autonomy: lowering or pausing is immediate. set_autonomy that would raise it only returns a proposal_id and needs_confirmation; tell the human to confirm it in the page. Within limits needs three approved orders first. Resuming after a pause is the human's. Autonomy never exceeds the per item cap below the step-up amount, and the daily limit may not be below the per item limit.
7. Say what you did not buy and why (list_recent_decisions). No urgency, no countdowns, no inferred life events.
8. Offer verify_receipts when asked whether the record is intact.
