"use strict";
// Front end for the Nudge simulation. No build step, no libraries, no network except this server.
// Interface wording comes from /api/strings (copy/strings.json) or from text the server returns.

const TOKEN = document.querySelector('meta[name="nudge-token"]').content;
const TIERS = ["suggest", "ask", "limits"];
const CATS = ["groceries", "household", "gifts"];
const FAULTS = ["none", "timeout_after_accept", "timeout_before_accept", "reject_next"];
const STATUS_ICON = {SUCCEEDED: "✓", PENDING_APPROVAL: "…", UNKNOWN: "?", FAILED: "✕", BLOCKED: "✕",
  DENIED: "✕", EXPIRED: "\u2013", CANCELLED: "↺", AUTHORIZED: "…", SUBMITTING: "…"};

let STR = {};
let state = null;
let lastStage = null;
let spoken = 0;          // id of the last transcript message already handled for read-aloud
let wired = false;
const openDetails = new Set();
let openDismiss = null;
let openWhy = new Set();
const leaveOut = new Set();

const S = (key, p) => {
  let s = STR[key] || key;
  if (p) for (const k in p) s = s.split("{" + k + "}").join(p[k]);
  return s;
};

function el(tag, attrs, ...kids) {
  const n = document.createElement(tag);
  for (const k in (attrs || {})) {
    const v = attrs[k];
    if (v === false || v == null) continue;
    if (k === "class") n.className = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (k === "text") n.textContent = v;
    else n.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat()) if (c != null) n.append(c.nodeType ? c : document.createTextNode(c));
  return n;
}


// ---------- MCP client: this page talks to the local server at /mcp with JSON-RPC over HTTP.
// Revision 2025-11-25 only. After initialize every request carries the session id and the version header.
// Recovery: a 404 (session gone, for example after a server restart) re-initializes once and retries;
// a failed start is never cached, so the next action tries again.
const MCP_VERSION = "2025-11-25";
const MCP = {sid: null, id: 0, ready: null, log: []};
async function mcpPost(msg, opts) {
  const h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", "Authorization": "Bearer " + TOKEN};
  if (MCP.sid) { h["Mcp-Session-Id"] = MCP.sid; h["MCP-Protocol-Version"] = MCP_VERSION; }
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), 10000);
  let r, text;
  try {
    r = await fetch("/mcp", {method: "POST", headers: h, body: JSON.stringify(msg), signal: ctl.signal});
    text = await r.text();
  } finally { clearTimeout(timer); }
  if (msg.method === "initialize" && r.headers.get("Mcp-Session-Id")) MCP.sid = r.headers.get("Mcp-Session-Id");
  if (r.status === 404 && MCP.sid && !(opts && opts.noRecover)) {
    MCP.sid = null; MCP.ready = null;
    await mcpStart();
    return mcpPost(msg, {noRecover: true});
  }
  if (r.status === 202) return null;
  let j = null;
  try { j = JSON.parse(text); } catch (e) { /* not JSON: handled below */ }
  if (!j || typeof j !== "object") throw new Error("HTTP " + r.status);
  return j;
}
async function mcpRpc(method, params, opts) {
  const j = await mcpPost({jsonrpc: "2.0", id: ++MCP.id, method, params}, opts);
  MCP.log.unshift(method + (params && params.name ? " " + params.name : "") + (j && j.error ? " (error " + j.error.code + ")" : ""));
  MCP.log.length = Math.min(MCP.log.length, 12);
  renderMcpLog();
  return j;
}
function mcpStart() {
  if (!MCP.ready) {
    MCP.sid = null;
    MCP.ready = (async () => {
      const init = await mcpRpc("initialize", {protocolVersion: MCP_VERSION, capabilities: {}, clientInfo: {name: "nudge-web", version: "0.2.0"}}, {noRecover: true});
      if (!init.result || init.result.protocolVersion !== MCP_VERSION) throw new Error("initialize refused");
      await mcpPost({jsonrpc: "2.0", method: "notifications/initialized"}, {noRecover: true});
      MCP.log.unshift("notifications/initialized"); renderMcpLog();
      const list = await mcpRpc("tools/list", {}, {noRecover: true});
      if (!list.result) throw new Error("tools/list failed");
    })().catch(e => { MCP.ready = null; MCP.sid = null; throw e; });
  }
  return MCP.ready;
}
function renderMcpLog() {
  const ul = document.getElementById("mcp-log");
  if (ul) ul.replaceChildren(...MCP.log.map(t => el("li", {text: t})));
}
async function fetchState() {
  const r = await fetch("/api/state", {headers: {"X-Nudge-Token": TOKEN}});
  if (!r.ok) throw new Error("state " + r.status);
  return r.json();
}
const FAILED_TOOL = () => ({isError: true, content: [{type: "text", text: S("ui.mcp.retry")}], structuredContent: {ok: false}});
async function tool(name, args) {
  let j;
  try {
    await mcpStart();
    j = await mcpRpc("tools/call", {name, arguments: args || {}});
  } catch (e) { console.error(e); toast(S("ui.mcp.retry")); return FAILED_TOOL(); }
  try { state = await fetchState(); } catch (e) { console.error(e); toast(S("ui.mcp.retry")); }
  safeRender();
  if (j.error) { toast(j.error.message); return {isError: true, content: [{type: "text", text: j.error.message}], structuredContent: {}}; }
  const res = j.result;
  const text = res.content && res.content[0] ? res.content[0].text : "";
  if (res.isError) toast(text);
  else if (["set_autonomy", "undo_purchase", "report_problem", "reconcile_order", "dismiss_suggestion"].includes(name)) toast(text);
  return res;
}

async function api(action, body) {
  let j;
  try {
    const r = await fetch("/api/" + action, {method: "POST", headers: {"Content-Type": "application/json", "X-Nudge-Token": TOKEN},
      body: JSON.stringify(body || {})});
    const text = await r.text();
    try { j = JSON.parse(text); } catch (e) { throw new Error("HTTP " + r.status); }
  } catch (e) { console.error(e); toast(S("ui.mcp.retry")); return {ok: false}; }
  if (j.state) { state = j.state; safeRender(); }
  const res = j.result || j;
  if (res && res.message && !res.ok) toast(res.message);
  else if (res && res.message && ["tier", "limits", "autonomy_confirm"].includes(action)) toast(res.message);
  return res;
}

let toastTimer = null;
function toast(msg, action) {
  const t = document.getElementById("toast");
  document.getElementById("toast-text").textContent = msg;
  const b = document.getElementById("toast-act");
  b.hidden = !action;
  b.onclick = action ? () => { t.classList.remove("show"); action.run(); } : null;
  if (action) b.textContent = action.label;
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 6000);   // every toast dismisses itself
}

// A drawing error is not a connection error: log it, say so, and keep whatever was already on screen.
function safeRender() {
  try { render(); return true; }
  catch (e) { console.error(e); toast(S("ui.error.render")); return false; }
}

function applyStaticText() {
  document.querySelectorAll("[data-s]").forEach(n => { n.textContent = S(n.dataset.s); });
  document.querySelectorAll("[data-s-aria]").forEach(n => n.setAttribute("aria-label", S(n.dataset.sAria)));
}

// ---------- shared pieces
function statusTag(status, label) {
  return el("span", {class: "status s-" + status}, el("span", {class: "icon", "aria-hidden": "true"}, STATUS_ICON[status] || "•"), " ", label);
}

function priceNode(text) {
  const m = /^(\D*)(\d[\d,]*)\.(\d\d)$/.exec(String(text));
  if (!m) return el("p", {class: "price", text});
  return el("p", {class: "price"}, el("span", {class: "sup"}, m[1]), m[2], el("span", {class: "sup"}, m[3]));
}

function ratingNode(text) {
  const m = /(\d(?:\.\d)?)/.exec(String(text));
  const half = m ? Math.max(0, Math.min(10, Math.round(Number(m[1]) * 2))) : 0;
  return el("p", {class: "rating"}, m ? el("span", {class: "stars r" + half, "aria-hidden": "true", text: "★★★★★"}) : null, el("span", {text}));
}

function productCard(p, actions, extra) {
  return el("article", {class: "card product"},
    el("img", {src: p.image, alt: p.name, width: "300", height: "300", loading: "lazy"}),
    el("div", {class: "body"},
      el("h3", {text: p.name}),
      el("p", {class: "muted", text: p.merchant}),
      priceNode(p.price),
      ratingNode(p.rating),
      el("p", {class: "delivery"}, p.delivery, p.prime ? el("span", {class: "prime", text: S("ui.prime")}) : null),
      el("p", {class: "returns", text: p.returns}),
      p.step_up ? el("p", {class: "note", text: S("ui.card.stepup", {limit: state.step_up_text})}) : null,
      extra, actions));
}

// ---------- assistant panel
function renderAssistant() {
  const tr = document.getElementById("transcript");
  const atBottom = tr.scrollHeight - tr.scrollTop - tr.clientHeight < 24;
  const keepTop = tr.scrollTop;
  tr.replaceChildren(...state.transcript.map(m => el("p", {class: "line " + m.who},
    el("strong", {text: S("ui.who." + m.who) + ": "}), m.text)));
  tr.scrollTop = atBottom ? tr.scrollHeight : keepTop;
  const sess = state.session;
  const stage = sess ? sess.stage : null;
  document.getElementById("stage").textContent = stage ? S("ui.stage." + stage) : "";
  document.getElementById("quick").hidden = !(sess && stage === "confirm") && !state.awaiting;
  const cf = document.getElementById("code-form");
  cf.hidden = stage !== "stepup";
  if (stage !== lastStage) {
    if (stage === "stepup") document.getElementById("code-input").focus();
    else if (stage === "confirm") document.querySelector('#quick [data-say="yes"]').focus();
    lastStage = stage;
  }
  const ph = document.getElementById("phone-list");
  ph.replaceChildren(...(state.phone.length ? state.phone.map(m => el("li", {class: "sms", text: m.text}))
    : [el("li", {class: "muted", text: S("ui.phone_empty")})]));
  // read aloud: only assistant lines, never the phone panel
  const tts = document.getElementById("tts");
  if (tts && tts.checked && "speechSynthesis" in window) {
    const fresh = state.transcript.filter(m => m.id > spoken && m.who === "assistant");
    fresh.forEach(m => window.speechSynthesis.speak(new SpeechSynthesisUtterance(m.text)));
  }
  const lastId = state.transcript.length ? state.transcript[state.transcript.length - 1].id : 0;
  spoken = lastId;   // message ids, not array length: the server sends a rolling window
}

function currentProposal() {
  return state && state.session ? state.session.proposal_id : (state ? state.awaiting_id : null);
}

async function say(text) {
  if (!text.trim()) return;
  const t = text.trim().toLowerCase().replace(/[^a-z ]/g, "");
  const sess = state && state.session;
  if (sess && sess.stage === "confirm" && ["yes", "yes please", "yeah", "yep", "sure", "okay", "ok", "confirm"].includes(t)) {
    await tool("approve_purchase", {proposal_id: sess.proposal_id, confirm: true}); return;
  }
  if (sess && sess.stage === "confirm" && ["no", "no thanks", "nope", "cancel"].includes(t)) {
    await tool("approve_purchase", {proposal_id: sess.proposal_id, confirm: false}); return;
  }
  await api("voice", {text, proposal: currentProposal()});
}

// ---------- today
function renderToday() {
  const offers = document.getElementById("offers");
  offers.replaceChildren(...CATS.filter(c => state.categories[c].offer).map(c => {
    const o = state.categories[c].offer;
    return el("div", {class: "card offer", role: "group", "aria-label": S("ui.offer.title")},
      el("h3", {text: S("ui.offer.title")}), el("p", {text: o.text}),
      el("div", {class: "row"},
        el("button", {class: "btn primary", type: "button", onclick: () => api("offer_accept", {category: c, proposal: o.proposal_id}), text: S("ui.offer.accept")}),
        el("button", {class: "btn", type: "button", onclick: () => api("offer_decline", {category: c}), text: S("ui.offer.decline")})));
  }));

  const ideas = document.getElementById("ideas");
  const anyOn = CATS.some(c => state.categories[c].enabled);
  if (!state.suggestions.length) {
    ideas.replaceChildren(el("p", {class: "muted", text: S(anyOn ? "ui.today.ideas_empty" : "ui.today.ideas_off")}));
  } else {
    ideas.replaceChildren(...state.suggestions.map(sg => {
      const whyOpen = openWhy.has(sg.id);
      const dismissOpen = openDismiss === sg.id;
      const reasonBox = el("p", {class: "reason"}, el("strong", {text: S("ui.card.reason") + ": "}), sg.reason);
      const why = el("div", {class: "why", id: "why-" + sg.id, hidden: !whyOpen, text: sg.why});
      const dismissBox = el("div", {class: "dismiss", role: "group", "aria-label": S("ui.dismiss.title"), hidden: !dismissOpen},
        el("p", {text: S("ui.dismiss.title")}),
        ...["not_interested", "have_it", "too_soon", "price"].map(w =>
          el("button", {class: "btn", type: "button", onclick: () => { openDismiss = null; tool("dismiss_suggestion", {suggestion_id: sg.id, reason: w}); },
            text: S("ui.dismiss." + w)})),
        el("button", {class: "btn quiet", type: "button", onclick: () => { openDismiss = null; render(); }, text: S("ui.dismiss.cancel")}));
      const acts = el("div", {class: "row"},
        el("button", {class: "btn primary", type: "button", onclick: () => tool("propose_purchase", {product_id: sg.product.id, suggestion_id: sg.id}),
          text: S("ui.card.buy")}),
        el("button", {class: "btn", type: "button", "aria-expanded": String(whyOpen), "aria-controls": "why-" + sg.id,
          onclick: () => { openWhy.has(sg.id) ? openWhy.delete(sg.id) : openWhy.add(sg.id); render(); }, text: S("ui.card.why")}),
        el("button", {class: "btn", type: "button", "aria-expanded": String(dismissOpen),
          onclick: () => { openDismiss = dismissOpen ? null : sg.id; render(); }, text: S("ui.card.dismiss")}));
      const extra = el("div", {}, reasonBox,
        sg.status === "prepared" ? el("p", {class: "tag", text: S("ui.card.ready")}) : null, why, dismissBox);
      return productCard(sg.product, acts, extra);
    }));
  }

  const dg = document.getElementById("digest");
  if (!state.digest.items.length) {
    dg.replaceChildren(el("p", {class: "muted", text: S("ui.digest.empty")}));
  } else {
    const ids = new Set(state.digest.items.map(i => i.id));
    [...leaveOut].forEach(i => { if (!ids.has(i)) leaveOut.delete(i); });
    const kept = state.digest.items.filter(i => !leaveOut.has(i.id));
    const total = kept.reduce((a, i) => a + i.amount_minor, 0);
    dg.replaceChildren(
      el("p", {class: "muted", text: S("ui.digest.hint")}),
      el("ul", {class: "list"}, ...state.digest.items.map(i => el("li", {},
        el("label", {class: "check"},
          el("input", {type: "checkbox", checked: !leaveOut.has(i.id), onchange: ev => {
            ev.target.checked ? leaveOut.delete(i.id) : leaveOut.add(i.id); render(); }}),
          " ", el("span", {text: i.product.name + ", " + S("ui.orders.qty", {n: i.qty}) + ", " + i.product.merchant + ", " + i.amount + ", " + i.delivery}))))),
      total >= 10000 ? el("p", {class: "note", text: S("ui.digest.stepup")}) : null,
      el("button", {class: "btn primary", type: "button", disabled: kept.length === 0,
        onclick: () => tool("start_digest", {leave_out: [...leaveOut]}),
        text: S("ui.digest.approve", {count: kept.length, total: "$" + (total / 100).toFixed(2)})}));
  }

  const needs = document.getElementById("needs");
  document.getElementById("needs-h").hidden = !state.needs_yes.length;
  needs.replaceChildren(...state.needs_yes.map(o => el("div", {class: "card"},
    el("p", {text: o.product.name + ", " + S("ui.orders.qty", {n: o.qty}) + ", " + o.product.merchant + ", " + o.amount + ", " + o.delivery}),
    el("div", {class: "row"},
      el("button", {class: "btn primary", type: "button", onclick: () => tool("propose_purchase", {product_id: o.product.id}), text: S("ui.orders.approve")}),
      el("button", {class: "btn", type: "button", onclick: () => tool("deny_purchase", {order_id: o.id}), text: S("ui.orders.decline")})))));

  const feed = document.getElementById("feed");
  feed.replaceChildren(...(state.feed.length ? state.feed.map(f => el("li", {class: "f-" + f.kind},
    el("span", {class: "when", text: f.when}), " ", f.text)) : [el("li", {class: "muted", text: S("ui.feed.empty")})]));
}

// ---------- shop
function renderShop() {
  const root = document.getElementById("shelves");
  root.replaceChildren(...CATS.map(c => {
    const items = state.products.filter(p => p.category === c);
    const label = S("ui.shop.carousel", {cat: state.categories[c].label});
    const track = el("div", {class: "track", tabindex: "0", role: "group", "aria-label": label},
      ...items.map(p => productCard(p, el("div", {class: "row"},
        el("button", {class: "btn primary", type: "button", onclick: () => tool("propose_purchase", {product_id: p.id}), text: S("ui.card.buy")})))));
    const by = dir => () => track.scrollBy({left: dir * 260, behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth"});
    track.addEventListener("keydown", ev => {
      if (ev.key === "ArrowRight") { ev.preventDefault(); by(1)(); }
      if (ev.key === "ArrowLeft") { ev.preventDefault(); by(-1)(); }
    });
    return el("section", {class: "shelf", "aria-roledescription": "carousel", "aria-label": label},
      el("div", {class: "shelf-head"}, el("h3", {text: label}),
        el("div", {class: "row"},
          el("button", {class: "btn", type: "button", onclick: by(-1), "aria-label": S("ui.carousel.prev"), text: "‹"}),
          el("button", {class: "btn", type: "button", onclick: by(1), "aria-label": S("ui.carousel.next"), text: "›"}))),
      track, el("p", {class: "muted small", text: S("ui.card.sample")}));
  }));
}

// ---------- rules
function renderRules() {
  const root = document.getElementById("rules");
  if (root.contains(document.activeElement) && document.activeElement.tagName === "INPUT" &&
      document.activeElement.type === "number") return; // do not steal focus from a field being edited
  root.replaceChildren(...CATS.map(c => {
    const cs = state.categories[c];
    const radios = TIERS.map(t => el("label", {class: "radio" + (cs.tier === t ? " on" : "")},
      el("input", {type: "radio", name: "tier-" + c, value: t, checked: cs.tier === t, onchange: () => tool("set_autonomy", {category: c, tier: t})}),
      " ", el("strong", {text: S("tier." + t)}), el("span", {class: "desc", text: S("ui.tier." + t + ".desc")})));
    const item = el("input", {type: "number", min: "1", max: "99.99", step: "0.01", id: "item-" + c, value: (cs.item_minor / 100).toFixed(2)});
    const day = el("input", {type: "number", min: "1", max: "1000", step: "0.01", id: "day-" + c, value: (cs.day_minor / 100).toFixed(2)});
    const save = el("button", {class: "btn", type: "button", onclick: async () => {
      const iv = parseFloat(item.value), dv = parseFloat(day.value);
      if (!(iv > 0) || !(dv > 0)) { toast(S("err.BAD_REQUEST")); return; }
      await tool("set_autonomy", {category: c, item_limit_dollars: iv, day_limit_dollars: dv});
    }, text: S("ui.rules.save")});
    const props = state.autonomy_proposals.filter(p => p.category === c).map(p =>
      el("div", {class: "card offer", role: "group", "aria-label": S("ui.autonomy.title")},
        el("h4", {text: S("ui.autonomy.title")}), el("p", {text: p.text}),
        el("div", {class: "row"},
          el("button", {class: "btn primary", type: "button", onclick: () => api("autonomy_confirm", {proposal: p.id}), text: S("ui.autonomy.confirm")}),
          el("button", {class: "btn", type: "button", onclick: () => api("autonomy_cancel", {proposal: p.id}), text: S("ui.autonomy.cancel")}))));
    return el("fieldset", {class: "card rule"},
      el("legend", {text: S("ui.rules.dial", {cat: cs.label})}), ...props,
      el("label", {class: "check"}, el("input", {type: "checkbox", checked: cs.enabled, onchange: ev => api("enable", {category: c, on: ev.target.checked})}),
        " ", el("span", {text: S("ui.rules.on", {cat: cs.label})})),
      el("div", {class: "radios", role: "radiogroup", "aria-label": S("ui.rules.dial", {cat: cs.label})}, radios),
      el("p", {class: "muted", text: S("ui.rules.progress", {n: Math.min(cs.streak, 3)})}),
      el("div", {class: "sentence", "aria-live": "polite"}, el("strong", {text: S("ui.rules.sentence") + ": "}), cs.sentence),
      el("div", {class: "limits"},
        el("label", {for: "item-" + c, text: S("ui.rules.item")}), item,
        el("label", {for: "day-" + c, text: S("ui.rules.day")}), day, save),
      el("p", {class: "muted small", text: S("ui.rules.limits_note", {cap: state.cap_text})}),
      el("button", {class: "btn", type: "button", onclick: () => tool("pause_autonomy", Object.assign({paused: !cs.paused}, {category: c})),
        text: S(cs.paused ? "ui.rules.resume_cat" : "ui.rules.pause_cat", {cat: cs.label})}));
  }), el("p", {class: "muted", text: S("ui.rules.voice_hint")}));
}

// ---------- orders
function dkeep(key) {   // a disclosure that stays open across redraws
  return {open: openDetails.has(key), ontoggle: ev => { ev.target.open ? openDetails.add(key) : openDetails.delete(key); }};
}
function renderOrders() {
  const root = document.getElementById("orders");
  if (!state.orders.length) { root.replaceChildren(el("p", {class: "muted", text: S("ui.orders.empty")})); return; }
  root.replaceChildren(...state.orders.map(o => {
    const acts = el("div", {class: "row"});
    if (o.status === "PENDING_APPROVAL") {
      acts.append(el("button", {class: "btn primary", type: "button", onclick: () => tool("propose_purchase", {product_id: o.product.id}), text: S("ui.orders.approve")}),
        el("button", {class: "btn", type: "button", onclick: () => tool("deny_purchase", {order_id: o.id}), text: S("ui.orders.decline")}));
    }
    if (o.can_undo) acts.append(el("button", {class: "btn", type: "button", onclick: () => tool("undo_purchase", {order_id: o.id}), text: S("ui.orders.undo")}));
    if (o.can_reconcile) acts.append(el("button", {class: "btn primary", type: "button", onclick: () => tool("reconcile_order", {order_id: o.id}), text: S("ui.orders.reconcile")}));
    if (o.can_retry) acts.append(el("button", {class: "btn", type: "button", onclick: () => tool("reconcile_order", {order_id: o.id, retry: true}), text: S("ui.orders.retry")}));
    let problem = null;
    if (o.can_report) {
      problem = el("details", dkeep("problem-" + o.id), el("summary", {text: S("ui.orders.report")}),
        el("p", {text: S("ui.problem.title")}),
        el("div", {class: "row"}, ...["wrong_item", "late", "not_as_described", "didnt_want"].map(w =>
          el("button", {class: "btn", type: "button", onclick: () => tool("report_problem", {order_id: o.id, reason: w}), text: S("ui.problem." + w)}))));
    }
    return el("article", {class: "card order"},
      el("h3", {text: o.product.name}),
      el("p", {}, statusTag(o.status, o.status_label), " ", o.status_help),
      el("p", {text: S("ui.orders.qty", {n: o.qty}) + " · " + o.amount + " · " + o.delivery + " · " + o.card}),
      el("p", {class: "muted", text: o.source}),
      el("p", {class: "charges", text: S("ui.orders.charges", {n: o.charges})}),
      o.undo_text ? el("p", {class: "note", text: o.undo_text}) : null,
      el("p", {class: "muted", text: S("ui.orders.returns") + ": " + o.product.returns}),
      acts, problem,
      el("h4", {text: S("ui.orders.story")}), el("p", {class: "story", text: o.story}),
      el("details", dkeep("raw-" + o.id), el("summary", {text: S("ui.orders.raw")}),
        el("dl", {},
          el("dt", {text: S("ui.orders.rule")}), el("dd", {text: o.rule + " " + o.reason_code}),
          el("dt", {text: S("ui.orders.payref")}), el("dd", {text: o.payment_ref || "-"}),
          el("dt", {text: S("ui.orders.flags")}), el("dd", {text: o.flags.join(", ") || "-"}),
          el("dt", {text: S("ui.orders.receipts")}),
          el("dd", {}, el("ol", {}, ...o.receipts.map(r => el("li", {text: r.id + " " + r.label + " (" + r.event + ")"})))))));
  }));
}

// ---------- demo controls
function renderDemo() {
  const f = document.getElementById("faults");
  if (!f.dataset.built) {
    f.dataset.built = "1";
    f.replaceChildren(...FAULTS.map(n => el("label", {class: "radio"},
      el("input", {type: "radio", name: "fault", value: n, onchange: () => api("fault", {name: n})}),
      " ", el("span", {text: S("ui.fault." + n)}))));
  }
  f.querySelectorAll("input").forEach(i => { i.checked = i.value === state.fault; });
  document.getElementById("payments").textContent = S("ui.demo.payments", {made: state.payments_made, net: state.net_charges});
}

function focusSnapshot() {
  const a = document.activeElement;
  if (!a || a === document.body) return null;
  const root = a.closest("#assistant, #main");
  if (!root) return null;
  const all = [...root.querySelectorAll("button, input, summary, [tabindex]")];
  return {id: a.id || null, root: root.id, idx: all.indexOf(a)};
}
function focusRestore(f) {
  if (!f || (document.activeElement && document.activeElement !== document.body)) return;
  const byId = f.id && document.getElementById(f.id);
  const root = document.getElementById(f.root);
  const all = root ? [...root.querySelectorAll("button, input, summary, [tabindex]")] : [];
  const target = byId || all[f.idx];
  if (target && !target.hidden && typeof target.focus === "function") target.focus({preventScroll: true});
}

function render() {
  if (!state) return;
  const snap = focusSnapshot();
  document.getElementById("now").textContent = state.now_text;
  const pa = document.getElementById("pause-all");
  pa.textContent = S(state.paused_all ? "ui.resume_all" : "ui.pause_all");
  pa.setAttribute("aria-pressed", String(state.paused_all));
  renderAssistant(); renderToday(); renderShop(); renderRules(); renderOrders(); renderDemo();
  focusRestore(snap);
}

// ---------- wiring
function showTab(name) {
  document.querySelectorAll('[role="tab"]').forEach(t => {
    const on = t.dataset.tab === name;
    t.setAttribute("aria-selected", String(on)); t.tabIndex = on ? 0 : -1;
    document.getElementById("p-" + t.dataset.tab).hidden = !on;
  });
}

function wire() {
  document.querySelectorAll('[role="tab"]').forEach(t => {
    t.addEventListener("click", () => showTab(t.dataset.tab));
    t.addEventListener("keydown", ev => {
      const tabs = [...document.querySelectorAll('[role="tab"]')];
      const i = tabs.indexOf(t);
      let j = null;
      if (ev.key === "ArrowRight") j = (i + 1) % tabs.length;
      if (ev.key === "ArrowLeft") j = (i - 1 + tabs.length) % tabs.length;
      if (j !== null) { ev.preventDefault(); tabs[j].focus(); showTab(tabs[j].dataset.tab); }
    });
  });
  document.getElementById("say-form").addEventListener("submit", ev => {
    ev.preventDefault();
    const i = document.getElementById("say-input"); const v = i.value; i.value = ""; say(v);
  });
  document.getElementById("code-form").addEventListener("submit", ev => {
    ev.preventDefault();
    const i = document.getElementById("code-input"); const v = i.value; i.value = "";
    if (!state || !state.session) { toast(S("ui.code.no_session")); return; }
    tool("approve_purchase", {proposal_id: state.session.proposal_id, confirm: true, step_up_code: v});
  });
  document.querySelectorAll("[data-say]").forEach(b => b.addEventListener("click", () => say(b.dataset.say)));
  document.getElementById("pause-all").addEventListener("click", () => tool("pause_autonomy", {paused: !state.paused_all}));
  document.getElementById("run").addEventListener("click", () => tool("run_daily_check"));
  document.getElementById("verify").addEventListener("click", async () => {
    const r = await tool("verify_receipts"); document.getElementById("verify-result").textContent = r.content && r.content[0] ? r.content[0].text : "";
  });
  document.getElementById("tamper").addEventListener("click", async () => {
    const r = await api("tamper"); document.getElementById("tamper-result").textContent = r.message || "";
  });
  document.querySelectorAll("[data-profile]").forEach(b => b.addEventListener("click", async () => {
    spoken = 0; lastStage = null; openWhy.clear(); openDetails.clear(); leaveOut.clear();
    document.getElementById("verify-result").textContent = ""; document.getElementById("tamper-result").textContent = "";
    await api("profile", {name: b.dataset.profile});
  }));
  document.querySelectorAll("[data-hours]").forEach(b => b.addEventListener("click", () => api("advance", {hours: parseInt(b.dataset.hours, 10)})));

  const chips = ["chip.rule", "chip.digest", "chip.pause", "chip.undo", "chip.limit", "chip.fewer"];
  document.getElementById("chips").replaceChildren(...chips.map(k => el("li", {},
    el("button", {class: "chip", type: "button", onclick: () => say(S("ui." + k)), text: S("ui." + k)}))));

  // Optional browser speech. Both are behind feature checks and off until the user asks.
  if ("speechSynthesis" in window) document.getElementById("tts-wrap").hidden = false;
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const note = document.getElementById("speech-note");
  if (SR) {
    const mic = document.getElementById("mic"); mic.hidden = false;
    note.textContent = S("ui.mic_note");
    mic.addEventListener("click", () => {
      const r = new SR(); r.lang = "en-US"; r.interimResults = false;
      mic.textContent = S("ui.mic_listening");
      r.onresult = ev => say(ev.results[0][0].transcript);
      r.onend = r.onerror = () => { mic.textContent = S("ui.mic"); };
      r.start();
    });
  } else {
    note.textContent = S("ui.mic_unavailable");
  }
  if (!("speechSynthesis" in window)) note.textContent += " " + S("ui.tts_unavailable");
}

function bootFailed(e, key) {
  console.error(e);
  const msg = STR[key] || (key === "ui.error.render" ? "The page could not draw your data. Details are in the browser console." :
    "Nudge could not start. Retry in a moment.");
  const retry = {label: STR["ui.retry"] || "Retry", run: boot};
  document.getElementById("boot-error-text").textContent = msg;
  const b = document.getElementById("boot-retry");
  b.textContent = retry.label; b.onclick = boot;
  document.getElementById("boot-error").hidden = false;
  toast(msg, retry);
}

async function boot() {
  try {
    if (!Object.keys(STR).length) {
      const r = await fetch("/api/strings");
      if (!r.ok) throw new Error("strings " + r.status);
      STR = await r.json();
    }
    applyStaticText();
    if (!wired) { wired = true; wire(); }
    mcpStart().catch(e => console.error(e));
    state = await fetchState();
  } catch (e) { bootFailed(e, "ui.error.start"); return; }
  try { render(); } catch (e) { bootFailed(e, "ui.error.render"); return; }
  document.getElementById("boot-error").hidden = true;
}
boot();
