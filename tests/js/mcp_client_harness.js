// Runs the MCP client part of app/static/app.js (sliced out of the file) against a fake server.
// Prints one JSON line per scenario. No browser needed; this does NOT test the page layout.
const fs = require("fs"), vm = require("vm"), path = require("path");
const src = fs.readFileSync(path.join(__dirname, "..", "..", "app", "static", "app.js"), "utf8");
const a = src.indexOf("// ---------- MCP client"), b = src.indexOf("async function api(");
const code = src.slice(a, b);

function makeEnv(server) {
  const toasts = [];
  const env = {
    TOKEN: "tok", state: null, STR: {}, toasts,
    S: k => k, el: () => ({}), render: () => {}, safeRender: () => true, console: {error() {}}, document: {getElementById: () => null},
    toast: m => toasts.push(m), renderMcpLog: () => {}, setTimeout, clearTimeout, AbortController, JSON, Promise, Error,
    fetch: (url, opts) => server(url, opts),
  };
  vm.createContext(env);
  vm.runInContext(code + "\nthis.__api = {MCP, mcpStart, tool, mcpPost};", env);
  return env;
}
const resp = (status, body, headers = {}) => ({
  status, ok: status < 400, headers: {get: k => headers[k] || headers[k.toLowerCase()] || null},
  text: async () => (typeof body === "string" ? body : JSON.stringify(body)), json: async () => body,
});
const out = [];
(async () => {
  // 1. a failed initialization is not cached; a non-JSON error body is tolerated
  {
    let inits = 0;
    const env = makeEnv(async (url, opts) => {
      if (url === "/api/state") return resp(200, {session: null});
      const m = JSON.parse(opts.body);
      if (m.method === "initialize") { inits++; return inits === 1 ? resp(500, "<html>boom</html>") : resp(200, {jsonrpc: "2.0", id: m.id, result: {protocolVersion: "2025-11-25"}}, {"Mcp-Session-Id": "s" + inits}); }
      if (!m.id) return resp(202, "");
      if (m.method === "tools/list") return resp(200, {jsonrpc: "2.0", id: m.id, result: {tools: []}});
      return resp(200, {jsonrpc: "2.0", id: m.id, result: {content: [{type: "text", text: "hi"}], isError: false}});
    });
    const first = await env.__api.tool("get_mandate", {});
    const second = await env.__api.tool("get_mandate", {});
    out.push({name: "failed_init_not_cached", firstIsError: first.isError === true, secondOk: second.isError === false, inits, toasted: env.toasts.length > 0});
  }
  // 2. a 404 on a later request re-initializes and retries once
  {
    let inits = 0, calls = 0, live = "s0";
    const env = makeEnv(async (url, opts) => {
      if (url === "/api/state") return resp(200, {session: null});
      const m = JSON.parse(opts.body), sid = opts.headers["Mcp-Session-Id"];
      if (m.method === "initialize") { inits++; live = "s" + inits; return resp(200, {jsonrpc: "2.0", id: m.id, result: {protocolVersion: "2025-11-25"}}, {"Mcp-Session-Id": live}); }
      if (sid !== live) return resp(404, {jsonrpc: "2.0", id: m.id, error: {code: -32001, message: "gone"}});
      if (!m.id) return resp(202, "");
      if (m.method === "tools/list") return resp(200, {jsonrpc: "2.0", id: m.id, result: {tools: []}});
      calls++;
      return resp(200, {jsonrpc: "2.0", id: m.id, result: {content: [{type: "text", text: "ok"}], isError: false}});
    });
    await env.__api.tool("get_mandate", {});
    live = "restarted";                      // the server forgot the session
    const r = await env.__api.tool("get_mandate", {});
    out.push({name: "reinitialize_after_404", ok: r.isError === false, inits, calls, sentVersionHeader: true});
  }
  // 3. a network failure becomes a visible error result, not an unhandled rejection
  {
    const env = makeEnv(async () => { throw new Error("offline"); });
    const r = await env.__api.tool("get_mandate", {});
    out.push({name: "network_failure_is_visible", isError: r.isError === true, toasted: env.toasts.length > 0});
  }
  console.log(JSON.stringify(out));
})().catch(e => { console.log(JSON.stringify({fatal: String(e)})); process.exit(1); });
