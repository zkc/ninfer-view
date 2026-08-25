/* DOM-stub smoke test for the dashboard script.
 *
 * Loads web/index.html, runs its <script> in a minimal DOM (no browser, no
 * server), feeds it schema-v10 records via the backfill path and simulated
 * SSE events, then asserts the request table + throughput series actually
 * built and the chart code path executed without throwing. Catches runtime
 * errors in the M2 aggregation/rendering code, not just syntax.
 *
 * Also drives the Config tab: loads a saved profile into the generated
 * form, checks the launch-command preview (incl. unknown-flag passthrough),
 * simulates an edit, and verifies Start persists the dirty profile before
 * POSTing /api/start.
 *
 * Run:  node tests/test_dashboard.js
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const html = fs.readFileSync(path.join(__dirname, "..", "web", "index.html"), "utf8");
const m = html.match(/<script>([\s\S]*?)<\/script>/);
if (!m) throw new Error("no <script> in index.html");
const code = m[1];

// ---- minimal DOM ----------------------------------------------------------

const ctxStub = {
  setTransform() {}, clearRect() {}, beginPath() {}, moveTo() {}, lineTo() {},
  stroke() {}, fillText() {}, fillRect() {}, setLineDash() {},
  measureText: (t) => ({ width: String(t).length * 6 }),
  strokeStyle: "", fillStyle: "", lineWidth: 1, font: "", textAlign: "",
  _calls: { fillText: 0, stroke: 0 },
};
ctxStub.fillText = function () { ctxStub._calls.fillText++; };
ctxStub.stroke = function () { ctxStub._calls.stroke++; };

const elById = new Map();
const tabs = ["log", "requests", "metrics"].map((name) => {
  const el = makeEl("button");
  el.dataset.tab = name;
  return el;
});

function makeEl(tag) {
  const el = {
    tag, children: [], style: {}, dataset: {}, value: "", textContent: "",
    innerHTML: "", className: "", title: "", colSpan: 0, disabled: false,
    scrollHeight: 0, scrollTop: 0, clientHeight: 0, clientWidth: 600,
    _cls: new Set(), _l: {},
    classList: {
      add: (c) => el._cls.add(c),
      remove: (c) => el._cls.delete(c),
      contains: (c) => el._cls.has(c),
    },
    append: (...nodes) => { nodes.forEach((n) => el.children.push(n)); },
    appendChild: (n) => { el.children.push(n); return n; },
    addEventListener: (t, fn) => { el._l[t] = fn; },
    removeEventListener: () => {},
    getContext: () => ctxStub,
  };
  return el;
}

function getEl(id) {
  if (!elById.has(id)) { const e = makeEl("div"); e.id = id; elById.set(id, e); }
  return elById.get(id);
}

const document = {
  getElementById: getEl,
  createElement: (t) => makeEl(t),
  createDocumentFragment: () => {
    const f = { children: [],
      append: (...n) => n.forEach((x) => f.children.push(x)),
      appendChild: (n) => f.children.push(n) };
    return f;
  },
  querySelectorAll: (sel) => (sel === ".tab" ? tabs : []),
};

const esInstances = [];
class EventSource {
  constructor(url) { this.url = url; this._l = {}; esInstances.push(this); }
  addEventListener(t, fn) { this._l[t] = fn; }
  close() {}
}

// ---- records (schema v10) -------------------------------------------------

const now = Date.now();
const R = (event, extra) => Object.assign({
  artifact_type: "ninfer_serve_request_log", schema_version: 10, event,
  timestamp_unix_ms: now, server_instance_id: "serve-test-1",
}, extra || {});

const req = (id) => ({ request_id: id, protocol: "openai_chat", stream: false,
  enable_thinking: false, message_count: 2, requested_output_tokens: 128 });

const backfillLogs = [
  { record: R("server_start", { server: { host: "127.0.0.1", port: 8081, public_model_id: "m" }, engine: { max_context: 8192, kv_capacity: 40000, max_concurrency: 2 } }) },
  { record: R("request_start", { request: req(1) }) },
  { record: R("request_done", { request: req(1),
      result: { finish_reason: "stop_token", prompt_tokens: 42, completion_tokens: 128,
                computed_prefill_tokens: 2, prefix_cache_hit_tokens: 40 },
      timings_seconds: { ttft: 0.095, prefill: 0.00025, decode: 2.29, total: 2.4 },
      speculative: { backend: "mtp", rounds: 29, drafted_tokens: 87, accepted_tokens: 72 } }) },
  { record: R("request_rejected", { request: req(2),
      error: { status: 400, code: "context_length_exceeded", message: "too long" } }) },
  { record: R("throughput", { interval_seconds: 5,
      tokens: { computed_prefill: 210, committed_decode: 275 },
      scheduler: { running: 1, prefilling: 0, decode_ready: 1, waiting: 0 },
      decode_batch: { rounds: 55, row_rounds: 55, average_size: 1 } }) },
].map((e, i) => Object.assign({ kind: e.record.event, seq: i + 1 }, e));

const profilesData = { profiles: {
  default: {
    id: "default", name: "test preset",
    binary: "/bin/ninfer-serve", artifact: "/models/m.ninfer",
    host: "127.0.0.1", port: 9999,
    extra_flags: [
      "--max-concurrency", "2",
      "--spec", "mtp", "--draft-tokens", "3",
      "--kv-dtype", "int8", "--lm-head-draft",
      "--unknown-flag", "keepme",
    ],
  },
}};
const fetchCalls = [];
const fetchImpl = (url, opts) => {
  fetchCalls.push({ url: String(url), opts: opts || null });
  if (opts && opts.method === "POST")
    return Promise.resolve({ json: () => Promise.resolve({ ok: true }) });
  return Promise.resolve({
    json: () => Promise.resolve(
      String(url).includes("/api/state")
        ? { state: "stopped" }
        : String(url).includes("/api/profiles")
        ? profilesData
        : { logs: backfillLogs }),
  });
};

const sandbox = {
  document, window: { addEventListener: () => {}, devicePixelRatio: 1 },
  requestAnimationFrame: (fn) => fn(),
  setInterval: () => 0,
  fetch: fetchImpl,
  EventSource,
  alert: (msg) => { console.error("alert():", msg); },
  confirm: () => true,
  prompt: () => null,
  console,
  // standard builtins are provided by the vm context
};
vm.createContext(sandbox);

let threw = null;
try {
  vm.runInContext(code, sandbox, { filename: "dashboard.js" });
} catch (e) { threw = e; }
if (threw) { console.error("script threw on load:", threw); process.exit(1); }

const tick = () => new Promise((r) => setImmediate(r));
(async () => {
  await tick(); await tick();

  // --- assertions after backfill -----------------------------------------
  const reqBody = getEl("reqBody");
  const reqPill = getEl("reqPill");
  const curTput = getEl("curTput");
  if (threw) throw new Error("pending: " + threw);

  console.assert(reqPill.textContent === "2", "reqPill should be 2 (start/done id1 + reject id2), got " + reqPill.textContent);
  console.assert(reqBody.children.length >= 4, "reqBody should have >=4 rows (2 main + 2 detail), got " + reqBody.children.length);

  // simulate live SSE: a new request done + a new throughput sample
  const es = esInstances[0];
  if (!es) throw new Error("EventSource not constructed");
  const fire = (kind, record) => {
    if (es._l[kind]) es._l[kind]({ data: JSON.stringify({ kind, record, seq: 99 }) });
  };
  fire("request_start", R("request_start", { request: req(3) }));
  fire("request_done", R("request_done", { request: req(3),
    result: { finish_reason: "stop_token", prompt_tokens: 10, completion_tokens: 50,
              computed_prefill_tokens: 10 },
    timings_seconds: { ttft: 0.05, prefill: 0.001, decode: 1.0, total: 1.05 } }));
  fire("throughput", R("throughput", { interval_seconds: 5,
    tokens: { computed_prefill: 0, committed_decode: 250 },
    scheduler: { running: 1, prefilling: 0, decode_ready: 1, waiting: 0 },
    decode_batch: { rounds: 50, row_rounds: 50, average_size: 1 } }));
  await tick(); await tick();

  console.assert(reqPill.textContent === "3", "reqPill should be 3 after live req, got " + reqPill.textContent);
  console.assert(curTput.textContent.includes("tok/s"), "curTput readout missing, got: " + curTput.textContent);
  console.assert(ctxStub._calls.fillText > 0 && ctxStub._calls.stroke > 0,
    "chart code path did not run (fillText=%d stroke=%d)", ctxStub._calls.fillText, ctxStub._calls.stroke);

  // summary line should reflect done/active/rejected counts
  const summary = getEl("reqSummary").textContent;
  console.assert(/done 2/.test(summary) && /rejected 1/.test(summary), "summary wrong: " + summary);

  // --- config tab -----------------------------------------------------------
  const findFid = (root, fid) => {
    const stack = [root];
    while (stack.length) {
      const el = stack.pop();
      if (el.dataset && el.dataset.fid === fid) return el;
      for (const c of el.children || []) stack.push(c);
    }
    return null;
  };
  const cmdTxt = getEl("cmdPreview").textContent;
  console.assert(cmdTxt.includes("/bin/ninfer-serve /models/m.ninfer"),
    "preview missing target: " + cmdTxt);
  console.assert(cmdTxt.includes("--host 127.0.0.1 --port 9999"),
    "preview missing host/port: " + cmdTxt);
  console.assert(cmdTxt.includes("--request-log-jsonl <run>/requests.jsonl"),
    "preview missing injected jsonl: " + cmdTxt);
  console.assert(cmdTxt.includes("--kv-dtype int8 --max-concurrency 2"),
    "preview lost sizing flags: " + cmdTxt);
  console.assert(cmdTxt.includes("--spec mtp --draft-tokens 3 --lm-head-draft"),
    "preview lost spec flags: " + cmdTxt);
  console.assert(cmdTxt.includes("--unknown-flag keepme"),
    "preview lost unknown flag (raw passthrough): " + cmdTxt);

  // simulate an edit: temperature 0.7 -> dirty -> reflected in the preview
  const tempEl = findFid(getEl("cfgForm"), "temperature");
  console.assert(tempEl, "temperature input missing from the generated form");
  tempEl.value = "0.7";
  tempEl._l.input({ target: tempEl });
  await tick();
  console.assert(getEl("cmdPreview").textContent.includes("--temperature 0.7"),
    "preview not live after edit");
  console.assert(getEl("cfgDirty").textContent === "unsaved changes",
    "dirty indicator wrong: " + getEl("cfgDirty").textContent);

  // Start must persist the dirty profile first, then launch it
  await sandbox.doStart();
  const saveCall = fetchCalls.find((c) => c.url === "/api/profiles" &&
    c.opts && c.opts.method === "POST");
  const startCall = fetchCalls.find((c) => c.url === "/api/start");
  console.assert(saveCall, "Start did not upsert the dirty profile first");
  const prof = JSON.parse(saveCall.opts.body).profile;
  console.assert(prof.id === "default" && prof.port === 9999 &&
    prof.binary === "/bin/ninfer-serve", "upserted profile mangled: " + saveCall.opts.body);
  const ti = prof.extra_flags.indexOf("--temperature");
  console.assert(ti >= 0 && prof.extra_flags[ti + 1] === "0.7",
    "upserted extra_flags lost the edit");
  const ui = prof.extra_flags.indexOf("--unknown-flag");
  console.assert(ui >= 0 && prof.extra_flags[ui + 1] === "keepme",
    "raw flag lost on save");
  console.assert(startCall &&
    JSON.parse(startCall.opts.body).profile_id === "default",
    "Start did not POST /api/start with the selected profile id");

  console.log("reqPill:", reqPill.textContent, "| summary:", summary);
  console.log("curTput:", curTput.textContent);
  console.log("ctx calls: fillText=" + ctxStub._calls.fillText + " stroke=" + ctxStub._calls.stroke);
  console.log("cmdPreview:", cmdTxt.slice(0, 130) + "\u2026");
  console.log("upserted extra_flags:", prof.extra_flags.join(" "));
  console.log("DASHBOARD SMOKE TEST PASS");
})().catch((e) => { console.error("SMOKE TEST FAIL:", e); process.exit(1); });