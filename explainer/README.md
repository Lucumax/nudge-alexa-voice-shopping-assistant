# Nudge explainer (about 93 seconds)

A self contained animation that introduces the simulated Nudge experience. It is labeled "Simulation" on screen. Scenes 1 and 3 use an invented storefront wireframe; scene 2 uses the labeled illustration. The items, prices and stores in the explainer are illustrative and differ from the sample catalog in the app (`app/catalog.py`). All on screen text lives in the `TEXT` object at the top of `explainer.js`.

## Open and play

1. Open `index.html` in any current browser. No server and no network are needed.
2. Press F or click Full screen. Press H to hide the control bar. Press P to hide the progress bar.
3. The stage is a 16:9 frame that scales to the window. The explainer autoplays and stops on the last scene.
4. Keys: Space pauses, Left and Right arrows step, 1 to 9 jump to a scene, R restarts, C toggles captions.

Reduced motion: if the system asks for reduced motion, the explainer does not autoplay. Each scene shows its final state and you step with the arrow keys.

Open `index.html#s6` to start on scene 6 (works for s1 to s9).

## Scenes and timing

Scenes are numbered by play order (keys 1 to 9, `#s1` to `#s9`). In the files, the scene with id `scene-8` plays second; the other ids match play order.

| # | Scene | Seconds | Cumulative | What it shows |
|---|---|---|---|---|
| 1 | Today | 9 | 0:09 | Generic page, an assistant that can buy, three open questions: limits, undo, record |
| 2 | Shopping today vs with Nudge | 18 | 0:27 | Realistic illustration (`assets/journey_illustration.png`, tagged Illustration). Slow pan and zoom across the manual lane (search, results, product, checkout), then the Nudge lane (suggestion with reason, read back and yes, code for orders of $100 or more, signed receipt with Verify and 24 hour Undo). Five timed callouts say what Nudge replaces |
| 3 | The Nudge idea | 10 | 0:37 | "We thought you'd like these", visible reason, why am I seeing this, dismiss that teaches |
| 4 | The autonomy dial | 11 | 0:48 | Suggest, Ask first, Within limits per category; three approvals earn an offer |
| 5 | Voice approval | 11 | 0:59 | Waveform, read back of item, amount, delivery, store, card; phone code for $100 or more |
| 6 | Under the hood | 12 | 1:11 | Web client (real MCP client), typed tools over Streamable HTTP, policy gate, approval, signed hash chained receipt, Verify |
| 7 | When things go wrong | 10 | 1:21 | Unknown, checking; reconcile; never a second charge; 24 hour undo |
| 8 | Close | 8 | 1:29 | Three promises (see the reason, set the limit, undo a purchase and verify the record), one line: "Open source, MIT license. Code linked in this submission." |
| 9 | End card | 4 | 1:33 | Silent. A small ownership and non-affiliation notice for the third party marks in the illustration (exact text is `s9_notice` in `explainer.js`; the tests allow that string and nothing else) |

Total about 93 seconds. Scene 2 timeline (seconds): 0 to 1 overview, 1 to 4 zoom to search and results, 5.5 to 8.5 pan to product and checkout, 10.5 to 12 down to suggestion and read back, 13.5 to 15 across to the code step, 15 to 18 receipt with Verify and Undo. With reduced motion the scene shows its final frame (receipt and Undo) with one callout.

The illustration contains third party marks. It is tagged Illustration, the Simulation badge stays on screen, and the final end card carries the ownership and non-affiliation notice. The tag and notice do not grant any rights in those marks. The explainer's own on screen text, apart from that one notice, names no third party.

## Facts used

- Only facts from this repository appear: the $100 code threshold, the 24 hour undo window, an offer after three approvals, a $30 grocery example and the reason text pattern from `PR_FAQ.md`. No statistics, customers or company claims. Items, prices, dates and card digits are sample data and are labeled as such.

## Test

From the `explainer/` folder:

    node --check explainer.js

From the repository root:

    python -m pytest -q explainer/test_explainer.py
