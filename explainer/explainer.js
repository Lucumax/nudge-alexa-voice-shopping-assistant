/* Nudge explainer. Self contained: no network, no libraries. */

// ---------------------------------------------------------------------------
// Every on-screen string lives in this one object.
// Rules: no third party names, no invented numbers, no dash punctuation.
// Facts used come from this repository (PR_FAQ.md, README.md): the $100 phone code
// threshold, the 24 hour undo window, three approvals before an offer, the
// $30 grocery example limit. Items, prices, dates and card digits are sample data,
// and the illustrative catalog here differs from the one in the app.
// ---------------------------------------------------------------------------
const TEXT = {
  badge: "Simulation",
  brand: "Nudge",

  // captions, one per scene
  cap1: "Today, an assistant can buy for you. But where are the limits, the undo and the record?",
  cap2: "Nudge starts by suggesting, never buying. Every idea shows its reason.",
  cap3: "You set the dial for each category. Trust is earned one approval at a time.",
  cap4: "Before any purchase, it reads back the details. Larger amounts need a code on your phone.",
  cap5: "A real MCP client, typed tools, a policy gate, your approval, then a signed receipt.",
  cap6: "If the store goes quiet, Nudge checks first, never charges twice, and you can undo.",
  cap7: "See the reason. Set the limit. Undo a purchase. Verify the record.",
  cap8: "Nudge replaces the search, the scroll and the comparing with one suggestion, one read back and one yes.",
  cap9: "",

  // scene 1
  s1_title: "Today",
  shopAria: "A generic shopping page wireframe with six product tiles",
  s1_bubble: "I can buy for you.",
  s1_buy: "Buy for me",
  s1_gap1: "Limits you can see?",
  s1_gap2: "A way to undo?",
  s1_gap3: "A record you can check?",

  // scene 8 (plays second)
  s8_title: "Shopping today vs with Nudge",
  s8_alt: "Illustration of a manual shopping journey in four steps, search, results, product and checkout, above the same journey with Nudge in four steps, suggestion with reason, read back and yes, code for orders of $100 or more, and signed receipt",
  s8_illus: "Illustration",
  s8_c1: "Today: you type a search and scroll through the results.",
  s8_c2: "Then you open the product, review the order and place it yourself.",
  s8_c3: "With Nudge: it starts with a suggestion and the reason why.",
  s8_c4: "It reads the order back and waits for your yes. Orders of $100 or more also need a code.",
  s8_c5: "Then a signed receipt, with Verify and a 24-hour Undo.",

  // scene 2
  s2_title: "The Nudge idea",
  s2_card: "We thought you'd like these",
  s2_i1: "Paper towels", s2_p1: "Sample price",
  s2_i2: "Coffee beans", s2_p2: "Sample price",
  s2_i3: "Dish soap", s2_p3: "Sample price",
  s2_reasonTag: "Reason",
  s2_reason: "You bought paper towels on September 26 and usually reorder about every 28 days.",
  s2_why: "Why am I seeing this?",
  s2_dismiss: "Dismiss",
  s2_pop: "Ideas use past orders, reminders you set, and prices of things you bought before. Nothing else.",
  s2_learned: "Dismissed: too soon. Nudge will wait longer.",

  // scene 3
  s3_title: "You set the dial, per category",
  s3_cat1: "Groceries", s3_cat2: "Household", s3_cat3: "Gifts",
  s3_suggest: "Suggest", s3_ask: "Ask first", s3_within: "Within limits",
  s3_approvals: "Approvals",
  s3_offer: "Offer after three approvals: handle small ones within limits?",
  s3_rule: "Up to $30 per item, from stores you chose. Bigger orders always ask.",

  // scene 4
  s4_title: "Say yes, with the facts in front of you",
  s4_say: "“Buy the paper towels.”",
  s4_read: "Read back before you say yes",
  s4_k1: "Item", s4_v1: "Paper towels",
  s4_k2: "Amount", s4_v2: "$12.99 (sample)",
  s4_k3: "Delivery", s4_v3: "Thursday (sample)",
  s4_k4: "Store", s4_v4: "Sample Shop",
  s4_k5: "Card", s4_v5: "Ending 4242 (sample)",
  s4_yes: "Yes, buy it",
  s4_phone: "Orders of $100 or more",
  s4_phoneNote: "The code goes to your phone, never into the conversation.",

  // scene 5
  s5_title: "Under the hood",
  s5_aria: "Diagram: web client calls typed tools over Streamable HTTP, then a policy gate with allow, ask or deny, then human approval, then a signed receipt chain with Verify",
  s5_n1: "Web client", s5_n1s: "A real MCP client", s5_n1s2: "on the page",
  s5_w1a: "Typed tools", s5_w1b: "Streamable HTTP",
  s5_n2: "Policy gate", s5_n2s: "Rules decide",
  s5_allow: "Allow", s5_ask: "Ask", s5_deny: "Deny",
  s5_w2: "If ask",
  s5_n3: "Human approval", s5_n3s: "Your yes", s5_n3s2: "A separate step",
  s5_w3: "Then",
  s5_n4: "Signed receipt", s5_n4s: "Each one links", s5_n4s2: "to the last",
  s5_r1: "1", s5_r2: "2", s5_r3: "3",
  s5_verify: "Verify",

  // scene 6
  s6_title: "When things go wrong",
  s6_a: "Payment sent", s6_as: "Request ID saved",
  s6_b: "Unknown, checking", s6_bs: "No answer from the store",
  s6_c: "Reconcile", s6_cs: "Ask again with the same request",
  s6_d: "Ordered", s6_ds: "One charge",
  s6_second: "Second charge", s6_never: "Never",
  s6_undo: "Undo window: 24 hours",
  s6_undoBtn: "Undo", s6_undone: "Undone",

  // scene 7
  s7_title: "Three promises",
  s7_p1: "See the reason", s7_p1s: "Every idea says why.",
  s7_p2: "Set the limit", s7_p2s: "One sentence you can read and change.",
  s7_p3: "Undo a purchase. Verify the record.", s7_p3s: "Receipts you can check.",
  s7_open: "Open source, MIT license. Code linked in this submission.",
  s7_disc: "Simulation with sample data and a mock payment step. Independent work.",

  // scene 9: the end card (silent, 4 seconds). This exact notice is the only place third party names appear in the text.
  s9_title: "Notice",
  s9_notice: "Amazon, Alexa+ and related names, marks and page designs are the property of Amazon.com, Inc. or its affiliates, shown here only to illustrate an entry in the Build, Ship, Shape Amazon Developer Hackathon. Nudge is an independent simulation, not affiliated with or endorsed by Amazon. All rights remain with their owners.",

  // controls (not recorded when hidden)
  ui_pause: "Pause", ui_resume: "Play",
  ui_prev: "Back", ui_next: "Next", ui_restart: "Restart",
  ui_caps: "Captions", ui_prog: "Progress", ui_full: "Full screen",
  ui_hint: "Space pause, arrows step, R restart, C captions, P progress, H hide controls, F full screen, 1 to 9 jump"
};

// Scene ids must exist in index.html. Durations in seconds (total about 93, scene 8 plays second).
const SCENES = [
  { id: "scene-1", dur: 9,  cap: "cap1" },
  { id: "scene-8", dur: 18, cap: "cap8" },
  { id: "scene-2", dur: 10, cap: "cap2" },
  { id: "scene-3", dur: 11, cap: "cap3" },
  { id: "scene-4", dur: 11, cap: "cap4" },
  { id: "scene-5", dur: 12, cap: "cap5" },
  { id: "scene-6", dur: 10, cap: "cap6" },
  { id: "scene-7", dur: 8,  cap: "cap7" },
  { id: "scene-9", dur: 4,  cap: "cap9" }
];

(function () {
  "use strict";
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var stage = $("#stage"), viewport = $("#viewport");
  var motionQuery = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : null;
  var reduced = !!(motionQuery && motionQuery.matches);

  // fill text
  document.querySelectorAll("[data-t]").forEach(function (el) {
    var k = el.getAttribute("data-t");
    if (k in TEXT) { el.textContent = TEXT[k]; }
  });
  document.querySelectorAll("[data-ta]").forEach(function (el) {
    var k = el.getAttribute("data-ta");
    if (k in TEXT) { el.setAttribute("aria-label", TEXT[k]); }
  });
  var tpl = $("#tpl-shop");
  document.querySelectorAll("[data-shop]").forEach(function (h) {
    h.appendChild(tpl.content.cloneNode(true));
    var svg = h.querySelector("svg");
    if (svg) { svg.setAttribute("aria-label", TEXT.shopAria); }
  });

  // waveform bars
  var wave = $("#wave");
  for (var i = 0; i < 28; i++) {
    var b = document.createElement("span");
    b.style.setProperty("--i", i);
    wave.appendChild(b);
  }

  // progress segments
  var total = SCENES.reduce(function (a, s) { return a + s.dur; }, 0);
  var progress = $("#progress");
  var fills = SCENES.map(function (s) {
    var seg = document.createElement("div");
    seg.className = "seg";
    seg.style.flexGrow = s.dur;
    var f = document.createElement("div");
    f.className = "fill";
    seg.appendChild(f);
    progress.appendChild(seg);
    return f;
  });

  var idx = -1, t = 0, playing = !reduced, last = 0, finished = false;
  var els = SCENES.map(function (s) { return document.getElementById(s.id); });

  function show(n, fromStart) {
    n = Math.max(0, Math.min(SCENES.length - 1, n));
    els.forEach(function (e, k) {
      if (k !== n) { e.classList.remove("active"); }
    });
    var e = els[n];
    e.classList.remove("active");
    void e.offsetWidth; // restart CSS animations
    e.classList.add("active");
    idx = n; t = 0; finished = false;
    $("#captionText").textContent = TEXT[SCENES[n].cap];
    stage.setAttribute("data-scene", n + 1);
    fills.forEach(function (f, k) { f.style.width = k < n ? "100%" : "0%"; });
    if (fromStart && !reduced) { playing = true; syncPause(); }
  }

  function syncPause() {
    stage.classList.toggle("paused", !playing);
    $("#bPause").textContent = playing ? TEXT.ui_pause : TEXT.ui_resume;
  }

  function frame(now) {
    var dt = last ? Math.min(100, now - last) / 1000 : 0;
    last = now;
    if (playing && !reduced && !finished) {
      t += dt;
      var d = SCENES[idx].dur;
      fills[idx].style.width = Math.min(100, (t / d) * 100) + "%";
      if (t >= d) {
        if (idx < SCENES.length - 1) { show(idx + 1); }
        else { finished = true; playing = false; syncPause(); }
      }
    }
    requestAnimationFrame(frame);
  }

  function step(dir) {
    var n = idx + dir;
    if (n < 0 || n >= SCENES.length) { return; }
    show(n);
    if (reduced) { fills[n].style.width = "100%"; }
  }
  function toggle() {
    if (reduced) { return; }
    if (finished) { show(0, true); return; }
    playing = !playing; syncPause();
  }
  function restart() { show(0, true); }
  function toggleCaps() {
    var off = stage.classList.toggle("nocaps");
    $("#bCaps").setAttribute("aria-pressed", off ? "false" : "true");
  }
  function toggleProg() {
    var off = stage.classList.toggle("noprog");
    $("#bProg").setAttribute("aria-pressed", off ? "false" : "true");
  }
  function toggleControls() { document.body.classList.toggle("nocontrols"); }
  function fullscreen() {
    if (document.fullscreenElement) { document.exitFullscreen(); }
    else if (viewport.requestFullscreen) { viewport.requestFullscreen(); }
  }

  $("#bPause").addEventListener("click", toggle);
  $("#bPrev").addEventListener("click", function () { step(-1); });
  $("#bNext").addEventListener("click", function () { step(1); });
  $("#bRestart").addEventListener("click", restart);
  $("#bCaps").addEventListener("click", toggleCaps);
  $("#bProg").addEventListener("click", toggleProg);
  $("#bFull").addEventListener("click", fullscreen);

  document.addEventListener("keydown", function (e) {
    if (e.ctrlKey || e.metaKey || e.altKey) { return; }
    var tag = e.target && e.target.tagName;
    var k = e.key;
    if (k === " " || k === "Spacebar") {
      if (tag === "BUTTON") { return; } // let a focused button activate itself
      e.preventDefault(); toggle();
    } else if (k === "ArrowRight") { e.preventDefault(); step(1); }
    else if (k === "ArrowLeft") { e.preventDefault(); step(-1); }
    else if (k === "r" || k === "R") { restart(); }
    else if (k === "c" || k === "C") { toggleCaps(); }
    else if (k === "p" || k === "P") { toggleProg(); }
    else if (k === "h" || k === "H") { toggleControls(); }
    else if (k === "f" || k === "F") { fullscreen(); }
    else if (k >= "1" && k <= "9") { show(parseInt(k, 10) - 1); }
  });

  function fit() {
    var s = Math.min(window.innerWidth / 1920, window.innerHeight / 1080);
    stage.style.setProperty("--s", s);
  }
  window.addEventListener("resize", fit);
  document.addEventListener("fullscreenchange", function () {
    $("#bFull").textContent = TEXT.ui_full;
    fit();
  });
  fit();

  // controls fade when the mouse is idle
  var idle;
  function wake() {
    document.body.classList.remove("idle");
    clearTimeout(idle);
    idle = setTimeout(function () { document.body.classList.add("idle"); }, 2500);
  }
  document.addEventListener("mousemove", wake);
  document.addEventListener("focusin", wake);
  wake();

  function applyMotion() {
    reduced = !!(motionQuery && motionQuery.matches);
    stage.classList.toggle("reduced", reduced);
    if (reduced) { playing = false; }
    syncPause();
  }
  if (motionQuery) {
    if (motionQuery.addEventListener) { motionQuery.addEventListener("change", applyMotion); }
  }
  applyMotion();
  var hm = /^#s([1-9])$/.exec(location.hash || "");
  show(hm ? parseInt(hm[1], 10) - 1 : 0);
  if (reduced) { fills[0].style.width = "100%"; }
  stage.focus({ preventScroll: true });
  requestAnimationFrame(frame);
})();
