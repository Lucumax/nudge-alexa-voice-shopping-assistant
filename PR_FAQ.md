# Nudge: working-backwards press release, customer FAQ, product FAQ

This is a product thinking exercise for a simulated prototype, written in a working-backwards style. It names no real customers, includes no statistics, and makes no claims about availability, compliance or production readiness. The behavior it describes is the behavior of the simulation in this repository; see the README for the details and the limits.

## Press release

**Nudge helps you shop by voice without handing over the wallet**

Headline summary: Nudge is a voice shopping assistant that suggests what you may need, says why, and buys on its own only inside rules you can read in one sentence. It is designed as a skill for Alexa+ and shown here as a simulation with Amazon Fresh, Whole Foods Market and Amazon private-label items. Products, prices and delivery dates are sample data; nothing is sold or priced by Amazon.

People who run a household know the pattern: the paper towels run low, the coffee is cheaper this week, a birthday is coming. A helper that handles those errands is useful. A helper that quietly spends your money is not. Nudge is built around that difference.

In a fresh start Nudge has nothing enabled and buys nothing. You turn on ideas for a category, and each idea arrives with its reason, such as "You bought this on September 26 and usually reorder about every 28 days." There are no countdown timers and no stock pressure. Dismiss an idea and tell Nudge why, and it adjusts by fixed rules: the idea is hidden for a while, and repeated dismissals mean fewer ideas. The demo profile, Maya, starts with her categories already on, groceries at Ask first and two earlier grocery orders approved.

When you want something, one tap opens a read-back: the item, the store, the price, the delivery date and the card ending. You say yes. Orders of one hundred dollars or more also need a code, shown on the simulated phone and entered in the page, so a spoken yes alone never moves a large amount.

Trust is earned. After you approve three orders in a category, Nudge offers the next step up: from Suggest, asking first; from Ask first, handling small orders on its own. The offer to act on its own shows the exact terms before you confirm it: an amount per item, an amount per day, the stores you already approved orders from, and an end date (90 days by default). Nudge tells you what it bought and what it chose not to buy, and why. If you report a problem with, or undo, an order it bought on its own, that category goes back to asking first. You can pause everything with one tap or one sentence, and undo an order for 24 hours.

Every purchase writes receipts in plain words, backed by a signed chain you can verify. If a store does not answer, Nudge checks before trying again, so in this simulation a slow reply never means a second charge.

How it works: you set a category to Suggest, Ask first, or Within limits. Suggest shows ideas. Ask first prepares orders and waits for your yes. Within limits buys small things inside your rules. You can say "pause groceries" and it pauses at once. You can say "set groceries limit to 30 dollars"; lowering a limit applies at once, and raising authority shows the exact terms and waits for your confirmation in the page.

Getting started: this is a simulation with sample products and a mock payment step. There is nothing to buy or install beyond running the demo.

## Customer FAQ

**Will it spend my money without asking?** Not unless you move a category to Within limits, and then only inside the item and daily limits, from stores you already approved orders from, until the end date, and never at or above the code amount.

**How do I stop it?** Pause everything with one tap or say "pause". Lower a limit by voice and it applies at once.

**Why am I seeing this suggestion?** Every card shows its reason and a longer explanation. In this simulation, cards come from eight fixed sample signals based on past orders, reminders and prices of things bought before.

**Can it use things I have not told it, like life events?** No. It does not infer sensitive events.

**What if I change my mind after ordering?** Undo works for 24 hours from completion. Product pages show return information; a return workflow is not implemented.

**What if the order status is unknown?** Nudge says so, checks with the store using the same request, and in this mock workflow does not send a second charge.

**How do I know the record is real?** Verify recomputes the receipt chain and names the first record that changed. It detects edits. It does not prove a recorded fact was true.

**Is this a real product?** No. It is a simulation with sample data.

## Product FAQ

**Who is it for?** People who want help with repeat household purchases but do not want to lose control. This is a hypothesis: no customer research has been done and the repository holds no first-hand evidence.

**Why tiers per category and not one switch?** Comfort differs by category. Groceries under thirty dollars is not gifts over one hundred.

**Why does autonomy have a cap below the code amount?** So nothing at or above the code amount can ever skip the second check.

**What stops a model or a prompt from buying?** The decision comes from deterministic rules in the core, never from free text. The assistant host can propose a purchase but cannot say yes: approval needs a different token that only the page holds. The core's approval covers store, amount, category and mandate, and Nudge adds a binding for product, quantity, delivery and price.

**What is deliberately not built?** Real checkout, real voice hardware, login, hosting, recurring billing, weekly budget windows, a return workflow, and anything that infers sensitive events.

**What are the known weaknesses?** The yes is not identity proof. The demo approver is a fixed session. One process only. The payment is a mock. The suggestion signals and the language layer are tested only against sample data. See the README.

**What could be measured with real users?** Hypotheses, not results: how often people accept an offer to move to Within limits, how often they reset, how often a dismissal is followed by fewer dismissals, and whether people can state their rule after a session. No such measurement has been made.

**Which risks need an accountable team before any real launch?** Payments, identity and step-up, privacy of purchase history, accessibility review by real users, and legal review. None of this is claimed done.
