/* DOM-stub smoke test for the dashboard scripts.
 *
 * Loads web/index.html, collects its <script src="js/..."> files in document
 * order, and runs them (concatenated, as one global-scope script — the way a
 * browser's classic <script> tags share scope) in a minimal DOM (no browser,
 * no server), feeds it schema-v10 records via the backfill path and simulated
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

const webRoot = path.join(__dirname, "..", "web");
const html = fs.readFileSync(path.join(webRoot, "index.html"), "utf8");
const srcs = [...html.matchAll(/<script[^>]*\bsrc="([^"]+)"/g)].map((m) => m[1]);
if (!srcs.length) throw new Error("no <script src> tags in index.html");
const code = srcs.map((src) => {
  const p = path.resolve(webRoot, src);
  if (p !== webRoot && !p.startsWith(webRoot + path.sep))
    throw new Error("script src escapes web/: " + src);
  return fs.readFileSync(p, "utf8");
}).join("\n");

// ---- minimal DOM ----------------------------------------------------------

const ctxStub = {
  setTransform() {}, clearRect() {}, beginPath() {}, moveTo() {}, lineTo() {},
  stroke() {}, fillText() {}, fillRect() {}, setLineDash() {},
  measureText: (t) => ({ width: String(t).length * 6 }),
  strokeStyle: "", fillStyle: "", lineWidth: 1, font: "", textAlign: "",
  _calls: { fillText: 0, stroke: 0 },
  _fills: [],
  _moveTo: [],
};
ctxStub.moveTo = function (x, y) { ctxStub._moveTo.push({ x, y }); };
ctxStub.fillText = function (text, x, y) {
  ctxStub._calls.fillText++;
  ctxStub._fills.push({ text: String(text), x, y, align: ctxStub.textAlign });
};
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
      "--device-state-slots", "2",
      "--host-state-slots", "16",
      "--host-kv-mib", "24576",
      "--spec", "mtp", "--draft-tokens", "3",
      "--kv-dtype", "int8", "--lm-head-draft",
      "--default-thinking-budget", "512",
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

const intervals = [];              // recorded setInterval registrations
let fakeNow = Date.now();          // controllable dashboard clock
class FakeDate extends Date {      // the script only uses Date.now() and new Date(ms)
  static now() { return fakeNow; }
}
const sandbox = {
  document, window: { addEventListener: () => {}, devicePixelRatio: 1 },
  requestAnimationFrame: (fn) => fn(),
  setInterval: (fn, ms) => { intervals.push({ fn, ms }); return intervals.length; },
  Date: FakeDate,
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

  // dual y-axis on the throughput chart: color-coded right-axis labels for
  // the decode scale (absent when both series share a single left axis)
  ctxStub._fills.length = 0;
  sandbox.renderCharts();
  const rightLabels = ctxStub._fills.filter((f) => f.align === "left" && f.x > 500);
  console.assert(rightLabels.length >= 5,
    "right y-axis labels missing (single shared axis?): " + rightLabels.length);
  console.log("right-axis labels: " + rightLabels.map((f) => f.text).join(","));

  // idle-gap handling: a 40 s gap between samples (>> breakGapMs) must break
  // the line — each series restarts its segment at the later point instead of
  // drawing a long diagonal connector. Geometry: w=600, padL=46, yRight →
  // padR=46, pw=508; grid moveTos sit at x=46 (excluded by x>50).
  const T = 1700000000000;
  ctxStub._moveTo.length = 0;
  sandbox.drawChart(getEl("chartTput"), {
    xMin: T, xMax: T + 120000,
    yLeft: { min: 0 }, yRight: { min: 0 },
    breakGapMs: 10000,
    series: [
      { name: "a", axis: "left",  pts: [[T + 1000, 5], [T + 50000, 7], [T + 90000, 9]] },
      { name: "b", axis: "right", pts: [[T + 1000, 2], [T + 50000, 3], [T + 90000, 4]] },
    ],
  });
  const segStarts = ctxStub._moveTo.filter((p) => p.x > 200);
  console.assert(segStarts.length === 4,
    "idle gap did not break the line (expected 2 restarts x 2 series): " + segStarts.length);

  // timer jitter (2 s gaps < breakGapMs) must NOT break the line
  ctxStub._moveTo.length = 0;
  sandbox.drawChart(getEl("chartTput"), {
    xMin: T, xMax: T + 120000, yLeft: { min: 0 },
    breakGapMs: 10000,
    series: [{ name: "a", pts: [[T + 50000, 5], [T + 52000, 6], [T + 54000, 9]] }],
  });
  console.assert(ctxStub._moveTo.filter((p) => p.x > 50).length === 1,
    "small gap broke the line (jitter should stay connected)");

  // small-value y axis (one in-flight request): the scheduler chart's values
  // are 0/1, so the axis must label round, strictly-increasing values — not a
  // 0–1.12 span integer-rounded into 1,1,1,0,0
  ctxStub._fills.length = 0;
  sandbox.drawChart(getEl("chartSched"), {
    xMin: T, xMax: T + 120000, yMin: 0, yFmt: (v) => String(Math.round(v)),
    series: [
      { name: "running", pts: [[T + 10000, 1]] },
      { name: "waiting", pts: [[T + 10000, 0]] },
    ],
  });
  const schedLabels = ctxStub._fills
    .filter((f) => f.align === "right" && f.x < 100)   // left-axis labels only
    .map((f) => parseFloat(f.text));
  console.assert(schedLabels.length >= 3,
    "scheduler y labels missing: " + schedLabels.length);
  console.assert(schedLabels.every((v, i) => i === 0 || v > schedLabels[i - 1]),
    "scheduler y labels not strictly increasing: " + schedLabels.join(","));
  console.assert(schedLabels[0] === 0 && schedLabels[schedLabels.length - 1] >= 1,
    "scheduler y axis should start at 0 and cover the data: " + schedLabels.join(","));
  console.log("scheduler y labels (one request): " + schedLabels.join(","));

  // rolling window: all points scrolled out → grid + hint, no empty box
  ctxStub._fills.length = 0;
  sandbox.drawChart(getEl("chartTput"), {
    xMin: 1000000000, xMax: 2000000000,
    series: [{ name: "x", pts: [[500000000, 5]] }],
  });
  console.assert(ctxStub._fills.some((f) => f.text === "no recent samples"),
    "scrolled-out window did not show the 'no recent samples' hint");

  // step-scroll: the x-axis is pinned to the latest sample (log input rate),
  // not Date.now() — a new sample 5 s later must advance xMax by exactly 5 s
  const winOpts = [];
  const realDraw = sandbox.drawChart;
  sandbox.drawChart = (cv, o) => { winOpts.push(o); };
  sandbox.renderCharts();
  sandbox.drawChart = realDraw;
  const xMaxA = winOpts.length ? winOpts[winOpts.length - 1].xMax : null;
  fire("throughput", R("throughput", {
    timestamp_unix_ms: now + 5000, interval_seconds: 5,
    tokens: { computed_prefill: 0, committed_decode: 300 },
    scheduler: { running: 1, prefilling: 0, decode_ready: 1, waiting: 0 },
    decode_batch: { rounds: 60, row_rounds: 60, average_size: 1 } }));
  await tick();
  winOpts.length = 0;
  sandbox.drawChart = (cv, o) => { winOpts.push(o); };
  sandbox.renderCharts();
  sandbox.drawChart = realDraw;
  const xMaxB = winOpts.length ? winOpts[winOpts.length - 1].xMax : null;
  console.assert(xMaxA === now, "xMax not pinned to latest sample (got " + xMaxA + ", want " + now + ")");
  console.assert(xMaxB === now + 5000, "xMax did not advance one interval per sample (got " + xMaxB + ", want " + (now + 5000) + ")");

  // idle advance: with NO new samples the window keeps moving at the log
  // rate — one interval of axis per interval of real time, quantized to whole
  // intervals (neither faster than the input, nor frozen)
  const lastXMax = () => winOpts.length ? winOpts[winOpts.length - 1].xMax : null;
  const capRender = () => {
    winOpts.length = 0;
    sandbox.drawChart = (cv, o) => { winOpts.push(o); };
    sandbox.renderCharts();
    sandbox.drawChart = realDraw;
  };
  fakeNow += 5000;                 // one stats interval of real time
  capRender();
  console.assert(lastXMax() === now + 10000,
    "idle advance did not move xMax by one interval (got " + lastXMax() + ", want " + (now + 10000) + ")");
  fakeNow += 4999;                 // sub-interval drift: no partial advance
  capRender();
  console.assert(lastXMax() === now + 10000,
    "partial (sub-interval) advance moved the window (got " + lastXMax() + ")");
  fakeNow += 10000;                // two more intervals
  capRender();
  console.assert(lastXMax() === now + 20000,
    "xMax should track real time at the log rate (got " + lastXMax() + ", want " + (now + 20000) + ")");

  // the idle-advance timer is registered at half the stats cadence (2500 ms
  // at the 5 s default) and its callback repaints the visible metrics pane
  const adv = intervals.find((iv) => iv.ms === 2500);
  console.assert(adv, "idle-advance interval (2500 ms) not registered");
  getEl("pane-metrics").style.display = "flex";
  const strokes = ctxStub._calls.stroke;
  adv.fn();
  console.assert(ctxStub._calls.stroke > strokes,
    "idle-advance timer callback did not repaint the charts");

  // summary line should reflect done/active/rejected counts
  const summary = getEl("reqSummary").textContent;
  console.assert(/done 2/.test(summary) && /rejected 1/.test(summary), "summary wrong: " + summary);

  // waiting status: active rows are attributed to the scheduler's waiting
  // count (newest first — FIFO admission, monotonic request ids), shown as a
  // badge and a separate summary count; a later sample with waiting=0
  // flips them back to active.
  fire("request_start", R("request_start", { request: req(4),
    timestamp_unix_ms: now + 5500 }));
  fire("throughput", R("throughput", { timestamp_unix_ms: now + 6000, interval_seconds: 5,
    tokens: { computed_prefill: 0, committed_decode: 300 },
    scheduler: { running: 1, prefilling: 0, decode_ready: 1, waiting: 1 },
    decode_batch: { rounds: 60, row_rounds: 60, average_size: 1 } }));
  await tick();
  let sumW = getEl("reqSummary").textContent;
  console.assert(/waiting 1/.test(sumW) && /active 0/.test(sumW),
    "waiting not attributed from scheduler count: " + sumW);
  // (the stub's appendChild pushes each row fragment into the body instead
  // of splicing its children like a real DOM would, so unwrap one level)
  const badges = getEl("reqBody").children
    .flatMap((c) => (c.children || [])
      .map((rf) => rf.children && rf.children[0])
      .filter((tr) => tr && tr.className === "main"))
    .map((tr) => tr.children[1]);
  console.assert(badges.some((td) => td.textContent === "waiting"),
    "no row shows the waiting badge");
  // clamp: waiting=3 with a single active row still marks exactly one
  fire("throughput", R("throughput", { timestamp_unix_ms: now + 11000, interval_seconds: 5,
    tokens: { computed_prefill: 0, committed_decode: 100 },
    scheduler: { running: 1, prefilling: 0, decode_ready: 1, waiting: 3 },
    decode_batch: { rounds: 20, row_rounds: 20, average_size: 1 } }));
  await tick();
  sumW = getEl("reqSummary").textContent;
  console.assert(/waiting 1/.test(sumW),
    "waiting count not clamped to the active rows: " + sumW);
  // newest sample waiting=0 → back to active
  fire("throughput", R("throughput", { timestamp_unix_ms: now + 16000, interval_seconds: 5,
    tokens: { computed_prefill: 0, committed_decode: 300 },
    scheduler: { running: 1, prefilling: 0, decode_ready: 1, waiting: 0 },
    decode_batch: { rounds: 60, row_rounds: 60, average_size: 1 } }));
  await tick();
  sumW = getEl("reqSummary").textContent;
  console.assert(/active 1/.test(sumW) && /waiting 0/.test(sumW),
    "waiting not cleared when the scheduler count drops: " + sumW);

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
  console.assert(cmdTxt.includes(
    "--device-state-slots 2 --host-state-slots 16 --host-kv-mib 24576"),
    "preview lost context-cache flags: " + cmdTxt);
  console.assert(cmdTxt.includes("--default-thinking-budget 512"),
    "preview lost default thinking budget: " + cmdTxt);
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
  const dsi = prof.extra_flags.indexOf("--device-state-slots");
  console.assert(dsi >= 0 && prof.extra_flags[dsi + 1] === "2",
    "context-cache flag lost on save");
  const tbi = prof.extra_flags.indexOf("--default-thinking-budget");
  console.assert(tbi >= 0 && prof.extra_flags[tbi + 1] === "512",
    "default thinking budget lost on save");
  console.assert(startCall &&
    JSON.parse(startCall.opts.body).profile_id === "default",
    "Start did not POST /api/start with the selected profile id");

  // --no-prefix-reuse conflicts with any explicit context-cache capacity
  // flag (serve_options rejects the combination at startup)
  const conflict = sandbox.cfgDefaultValues();
  conflict.binary = "/bin/serve"; conflict.artifact = "/m.ninfer";
  conflict.no_prefix_reuse = true; conflict.host_kv_mib = "24576";
  const cv = sandbox.cfgValidate(conflict);
  console.assert(cv.errs.some((e) => /no-prefix-reuse/.test(e)),
    "no-prefix-reuse x context-cache conflict not flagged: " + JSON.stringify(cv));
  // non-numeric text fields (model id / api key / preset path) are not
  // number-validated
  const textv = sandbox.cfgDefaultValues();
  textv.binary = "/bin/serve"; textv.artifact = "/m.ninfer";
  textv.model_id = "qwen3.8-27b"; textv.api_key = "local-secret";
  textv.context_cost_presets = "/presets/rt.json";
  const tv = sandbox.cfgValidate(textv);
  console.assert(tv.errs.length === 0,
    "text fields should not be number-validated: " + JSON.stringify(tv));

  console.log("reqPill:", reqPill.textContent,
    "| summary:", getEl("reqSummary").textContent);
  console.log("curTput:", curTput.textContent);
  console.log("ctx calls: fillText=" + ctxStub._calls.fillText + " stroke=" + ctxStub._calls.stroke);
  console.log("cmdPreview:", cmdTxt.slice(0, 130) + "\u2026");
  console.log("upserted extra_flags:", prof.extra_flags.join(" "));
  console.log("DASHBOARD SMOKE TEST PASS");
})().catch((e) => { console.error("SMOKE TEST FAIL:", e); process.exit(1); });