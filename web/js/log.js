"use strict";
/* Log pane: record -> text formatters (schema v10) and the live log view
 * (scroll pinning, filtering, incremental append). */

function fmtServerStart(r) {
  const s = r.server || {}, e = r.engine || {}, a = r.artifact || {},
        env = r.environment || {};
  const p = [
    "model=" + (s.public_model_id || "\u2014"),
    "http://" + s.host + ":" + s.port,
    "ctx=" + f0(e.max_context),
    "kv=" + e.kv_capacity_mode + " " + kb(e.kv_capacity) + " tok",
    "conc=" + e.max_concurrency,
    "kv_cache=" + (e.kv_cache || "\u2014"),
  ];
  if (e.speculative_backend && e.speculative_backend !== "none")
    p.push("spec=" + e.speculative_backend + " draft=" + e.speculative_draft_window);
  if (env.gpu_name) p.push(env.gpu_name);
  if (a.load_seconds != null) p.push("load=" + f1(a.load_seconds) + "s");
  p.push("instance=" + r.server_instance_id);
  return p.join(" \u00b7 ");
}

function fmtRequestStart(r) {
  const q = r.request || {}, pr = r.preparation_seconds || {};
  const p = [
    (q.protocol || "?") + " " + (q.stream ? "stream" : "non-stream"),
    "thinking=" + (q.enable_thinking ? "on" : "off"),
    "budget=" + q.requested_output_tokens +
      "(" + (q.requested_output_tokens_source === "client" ? "client" : "default") + ")",
  ];
  if (q.message_count) p.push("msgs=" + q.message_count);
  if (q.media_item_count) p.push("media=" + q.media_item_count);
  if (q.tool_count) p.push("tools=" + q.tool_count);
  if (pr.total > 0.001) p.push("prepare=" + (pr.total * 1000).toFixed(1) + "ms");
  return p.join(" \u00b7 ");
}

function fmtRequestDone(r) {
  const q = r.request || {}, res = r.result || {}, t = r.timings_seconds || {},
        sp = r.speculative || {};
  const gen = res.completion_tokens || 0;
  const decodeTok = gen > 0 ? gen - 1 : 0;   // prefill emits the first token
  const p = [
    "done " + (res.finish_reason || "\u2014"),
    "prompt=" + (res.prompt_tokens != null ? res.prompt_tokens : "\u2014"),
    "gen=" + gen,
    "cache=" + (res.prefix_cache_hit_tokens || 0),
    "reuse=" + (res.prefix_reuse_path || "\u2014"),
    "ttft=" + ((t.ttft || 0) * 1000).toFixed(0) + "ms",
    "prefill=" + rate(res.computed_prefill_tokens, t.prefill),
    "decode=" + rate(decodeTok, t.decode),
    "wall=" + f2(t.total) + "s",
  ];
  if (res.tool_call_count) p.push("tool_calls=" + res.tool_call_count);
  if (sp.backend && sp.backend !== "none" && sp.rounds > 0) {
    const per = 1 + sp.accepted_tokens / sp.rounds;
    const s = "spec=" + sp.backend + " " + per.toFixed(2) + "tok/round";
    p.push(sp.drafted_tokens > 0
      ? s + " (" + (100 * sp.accepted_tokens / sp.drafted_tokens).toFixed(1) + "%)"
      : s);
  }
  return p.join(" \u00b7 ");
}

function fmtRequestRejected(r) {
  const e = r.error || {};
  return ("REJECTED " + (e.status != null ? e.status : "?") + " " +
          (e.code || e.type || "") + " " + (e.message || "")).replace(/\s+/g, " ").trim();
}

function fmtRequestError(r) {
  return "ERROR " + (r.error && r.error.message ? r.error.message : "unknown");
}

function fmtThroughput(r) {
  const t = r.throughput_tokens_per_second || {}, s = r.scheduler || {},
        b = r.decode_batch || {};
  return "throughput " + f1(r.interval_seconds) + "s \u00b7 "
    + "prefill=" + f1(t.prefill) + "tok/s decode=" + f1(t.decode) + "tok/s \u00b7 "
    + "running=" + (s.running != null ? s.running : 0)
    + " prefilling=" + (s.prefilling != null ? s.prefilling : 0)
    + " decode_ready=" + (s.decode_ready != null ? s.decode_ready : 0)
    + " waiting=" + (s.waiting != null ? s.waiting : 0) + " \u00b7 "
    + "batch=" + (b.average_size != null ? f2(b.average_size) : "n/a");
}

const KIND_LABEL = {
  server_start: "server", request_start: "start", request_rejected: "reject",
  request_done: "done", request_error: "error", throughput: "thru",
};
const KIND_CLASS = {
  server_start: "k-server", request_start: "k-start",
  request_rejected: "k-reject", request_done: "k-done",
  request_error: "k-error", throughput: "k-thru",
};
const FORMATTERS = {
  server_start: fmtServerStart, request_start: fmtRequestStart,
  request_rejected: fmtRequestRejected, request_done: fmtRequestDone,
  request_error: fmtRequestError, throughput: fmtThroughput,
};

function makeEntry(rec) {
  const kind = rec.event || "unknown";
  const fmt = FORMATTERS[kind];
  let text = fmt ? fmt(rec) : JSON.stringify(rec);
  const id = rec.request && rec.request.request_id;
  if (id != null && kind !== "server_start" && kind !== "throughput")
    text = "[req " + id + "] " + text;
  return { ts: rec.timestamp_unix_ms || Date.now(), kind,
           label: KIND_LABEL[kind] || kind, text, rec };
}

// ---- log pane rendering -------------------------------------------------

logEl.addEventListener("scroll", () => {
  pinned = logEl.scrollHeight - logEl.scrollTop - logEl.clientHeight < 40;
});

function lineNode(e) {
  const div = document.createElement("div");
  div.className = "line " + (KIND_CLASS[e.kind] || "");
  const ts = document.createElement("span"); ts.className = "ts";
  ts.textContent = tsOf(e.ts);
  const label = document.createElement("span"); label.className = "label";
  label.textContent = e.label;
  const msg = document.createElement("span"); msg.className = "msg";
  msg.textContent = e.text;
  div.append(ts, label, msg);
  div.title = "raw record:\n" + JSON.stringify(e.rec, null, 2);
  return div;
}

function applyFilter() {
  const f = $("filter").value.trim().toLowerCase();
  logEl.textContent = "";
  const frag = document.createDocumentFragment();
  let n = 0;
  for (const e of entries) {
    if (f && !e.text.toLowerCase().includes(f)) continue;
    frag.appendChild(lineNode(e)); n++;
  }
  if (n === 0) {
    const d = document.createElement("div");
    d.className = "empty";
    d.textContent = entries.length === 0
      ? "\u2014 no events yet; the log stream is the instance's requests.jsonl \u2014"
      : "\u2014 no events match the filter \u2014";
    frag.appendChild(d);
  }
  logEl.appendChild(frag);
  $("count").textContent = n + (n === 1 ? " event" : " events");
  if (pinned) logEl.scrollTop = logEl.scrollHeight;
}

function addEntry(e) {
  entries.push(e);
  if (entries.length > MAX) entries.splice(0, entries.length - MAX);
  const f = $("filter").value.trim().toLowerCase();
  if (!f || e.text.toLowerCase().includes(f)) {
    logEl.appendChild(lineNode(e));
    if (pinned) logEl.scrollTop = logEl.scrollHeight;
  }
  $("count").textContent = entries.length + " events";
}
