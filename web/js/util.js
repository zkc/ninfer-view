"use strict";
/* Shared utilities, formatters, and the client-side model state.
 *
 * The dashboard is a plain static site: no build step, no ES modules. Each
 * file under web/js/ loads as a classic <script> tag (order is set by
 * index.html: util -> log -> requests -> charts -> config -> main), and
 * top-level declarations are shared across files through the global scope.
 * Keep each top-level name declared in exactly ONE file, and keep cross-file
 * call targets as function declarations (hoisted / global-object bindings).
 *
 * The log stream is fed only by the child's --request-log-jsonl file
 * (schema v10). stderr drives the state machine / progress bar only.
 * Hover a line for the full raw record.
 */
const $ = (id) => document.getElementById(id);
const logEl = $("log"), badge = $("badge"), stateText = $("stateText");
const metaEl = $("meta"), uptimeEl = $("uptime"), progEl = $("progress");
const startBtn = $("startBtn"), stopBtn = $("stopBtn"), attachBtn = $("attachBtn");
const entries = [];                       // {ts, kind, label, text, rec}
const MAX = 4000;
let pinned = true;
let lastState = { state: "stopped" };

// ---- M2 model: request table + throughput series ------------------------
// Both aggregate the SAME records the log pane consumes (backfill + SSE), so
// the server stays stateless. Idempotent: re-ingesting a record (e.g. after an
// SSE reconnect re-backfill) cannot double-count.
const reqs = new Map();   // request_id -> {id,status,start,startMs,done?,doneMs?}
// Newest scheduler "waiting" count (requests queued for admission) and the
// sample time it came from (throughput events). The engine admits queued
// requests FIFO from the queue head and request ids are monotonic per
// instance, so the waiting requests are exactly the NEWEST of the
// started-but-not-finished ones; renderTable() badges those rows "waiting".
// Count-based inference: as fresh as the newest sample, and a stale
// attribution self-corrects on the next one.
let schedWait = 0, schedWaitTs = 0;
const tput = new Map();   // timestamp_ms -> point (dedupes re-ingested samples)
const TP_MAX = 4000;
let sampleIntervalMs = 5000;  // the server's --log-stats-interval-ms (default 5000);
                              // updated from server_start.engine.log_stats_interval_ms.
                              // Sets the charts' window width and the idle-advance
                              // step (the x-axis moves one of these per interval of
                              // real time, matching the log input rate).
let reqDirty = false, chartDirty = false, rafPending = false;

// xMax reference for the charts' rolling window. Anchored to the latest
// throughput sample; while the server is idle it keeps advancing forward at
// the LOG RATE — one interval of axis per interval of real time, quantized to
// whole intervals — so the chart scrolls at the same pace it had while
// following the input (neither faster nor frozen). The advance is computed
// from real time at render (chartRefNow), so a throttled timer only delays
// the repaint, never the position.
let chartAnchorWall = 0;    // Date.now() (dashboard clock) when anchor was set
let chartAnchorRef = null;  // xMax value (sample-time space) at chartAnchorWall

function chartRefNow() {
  if (chartAnchorRef == null) return null;
  const advance = Math.floor((Date.now() - chartAnchorWall) / sampleIntervalMs)
                  * sampleIntervalMs;
  return chartAnchorRef + advance;
}
function noteChartSample(ts) {
  // Follow the input: anchor the window to the newest sample (forward-only,
  // so an out-of-order/old sample can't drag the view backwards).
  if (chartAnchorRef == null || ts > chartAnchorRef) {
    chartAnchorRef = ts;
    chartAnchorWall = Date.now();
  }
}

function markReq()   { reqDirty = true;   scheduleRender(); }
function markChart() { chartDirty = true; scheduleRender(); }
function scheduleRender() {
  if (rafPending) return;
  rafPending = true;
  requestAnimationFrame(() => {
    rafPending = false;
    if (reqDirty)   { reqDirty = false;   renderTable(); }
    if (chartDirty) { chartDirty = false; renderCharts(); }
  });
}

function ingest(rec) {
  const ev = rec.event, ts = rec.timestamp_unix_ms || Date.now();
  if (ev === "server_start") {
    // Remember the stats cadence so the charts' rolling window and the
    // idle-gap threshold match the server's actual sampling interval.
    const iv = rec.engine && rec.engine.log_stats_interval_ms;
    if (iv && iv > 0) { sampleIntervalMs = Number(iv); markChart(); }
    return;
  }
  if (ev === "request_start") {
    const id = rec.request && rec.request.request_id;
    if (id != null && !reqs.has(id))
      reqs.set(id, { id, status: "active", start: rec, startMs: ts });
    markReq();
  } else if (ev === "request_done" || ev === "request_rejected"
             || ev === "request_error") {
    const id = rec.request && rec.request.request_id;
    if (id == null) return;
    const r = reqs.get(id) || { id, status: "active", start: rec, startMs: ts };
    r.status = ev === "request_done" ? "done"
             : ev === "request_rejected" ? "rejected" : "error";
    r.done = rec; r.doneMs = ts;
    reqs.set(id, r);
    markReq();
  } else if (ev === "throughput") {
    const tk = rec.tokens || {}, iv = rec.interval_seconds || 0,
          tp = rec.throughput_tokens_per_second || {},
          s = rec.scheduler || {}, b = rec.decode_batch || {};
    // rate = tokens / interval (authoritative; equals the server's own field)
    tput.set(ts, {
      t: ts,
      pfill: iv > 0 && tk.computed_prefill != null
             ? tk.computed_prefill / iv : (tp.prefill != null ? tp.prefill : null),
      dec:   iv > 0 && tk.committed_decode != null
             ? tk.committed_decode / iv : (tp.decode != null ? tp.decode : null),
      running: s.running, prefilling: s.prefilling,
      decode_ready: s.decode_ready, waiting: s.waiting,
      batch: b.average_size,
    });
    if (tput.size > TP_MAX) {
      let oldest = Infinity;
      for (const k of tput.keys()) if (k < oldest) oldest = k;
      tput.delete(oldest);
    }
    if (s.waiting != null && ts > schedWaitTs) {
      schedWaitTs = ts;
      if (s.waiting !== schedWait) { schedWait = s.waiting; markReq(); }
    }
    noteChartSample(ts);
    markChart();
  }
}

// ---- formatters (schema v10) ------------------------------------------

const f0 = (x) => x == null ? "\u2014" : String(Math.round(Number(x)));
const f1 = (x) => x == null ? "\u2014" : Number(x).toFixed(1);
const f2 = (x) => x == null ? "\u2014" : Number(x).toFixed(2);
const rate = (tok, s) => (tok > 0 && s > 0) ? (tok / s).toFixed(1) + "tok/s" : "n/a";
const kb = (x) => x == null ? "\u2014"
  : (x >= 1e6 ? (x / 1e6).toFixed(2) + "M" : Math.round(x / 1e3) + "k");

function tsOf(ms) {
  const d = new Date(ms);
  const p = (n, w = 2) => String(n).padStart(w, "0");
  return p(d.getHours()) + ":" + p(d.getMinutes()) + ":" + p(d.getSeconds())
       + "." + p(d.getMilliseconds(), 3);
}

function fmtUp(s) {
  if (s == null) return "";
  s = Math.max(0, Math.round(s));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  return (h ? h + "h " : "") + m + "m " + sec + "s up";
}
