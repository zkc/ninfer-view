"use strict";
/* M2: request table — rows, scheduler "waiting" attribution, filtering,
 * and the summary line. Reads the shared `reqs` model from util.js. */

// ---- M2: request table ---------------------------------------------------

function specCell(sp) {
  if (!sp || !sp.backend || sp.backend === "none" || !(sp.rounds > 0)) return "\u2014";
  const per = 1 + (sp.accepted_tokens || 0) / sp.rounds;
  return per.toFixed(2) + (sp.drafted_tokens > 0
    ? " (" + (100 * sp.accepted_tokens / sp.drafted_tokens).toFixed(1) + "%)" : "");
}

// ids of the active rows the scheduler's waiting count currently covers —
// the newest ones (FIFO admission + monotonic request ids). Clamped to the
// active set, so a sample that lags reality can never over-mark.
function waitingSet() {
  const act = [...reqs.values()].filter((r) => r.status === "active");
  if (schedWait <= 0) return new Set();
  act.sort((a, b) => {
    const an = Number(a.id), bn = Number(b.id);
    return (Number.isFinite(an) && Number.isFinite(bn))
      ? an - bn : (a.startMs || 0) - (b.startMs || 0);
  });
  return new Set(act.slice(-schedWait).map((r) => r.id));
}

function reqRow(r, status) {
  status = status || r.status;
  const d = r.done, res = (d && d.result) || {}, t = (d && d.timings_seconds) || {},
        sp = (d && d.speculative) || {}, q = (r.start && r.start.request) || {};
  const gen = res.completion_tokens || 0;
  const decodeTok = gen > 0 ? gen - 1 : 0;
  const frag = document.createDocumentFragment();
  const tr = document.createElement("tr"); tr.className = "main";
  const cell = (txt, cls) => {
    const td = document.createElement("td");
    td.textContent = txt; if (cls) td.className = cls; tr.appendChild(td); return td;
  };
  cell("#" + r.id);
  cell(status, "st " + status);
  cell(q.protocol || "?");
  cell(r.startMs ? tsOf(r.startMs) : "\u2014");
  cell(res.prompt_tokens != null ? String(res.prompt_tokens) : "\u2014", "num");
  cell(status === "active" || status === "waiting" ? "\u2014" : String(gen), "num");
  let finish = "\u2014";
  if (r.status === "done") finish = res.finish_reason || "\u2014";
  else if (r.status === "rejected") { const e = (r.done && r.done.error) || {}; finish = e.code || e.type || "rejected"; }
  else if (r.status === "error")    { const e = (r.done && r.done.error) || {}; finish = e.code || e.type || "error"; }
  cell(finish);
  cell(r.status === "done" ? ((t.ttft || 0) * 1000).toFixed(0) + "ms" : "\u2014", "num");
  cell(r.status === "done" ? rate(res.computed_prefill_tokens, t.prefill) : "\u2014", "num");
  cell(r.status === "done" ? rate(decodeTok, t.decode) : "\u2014", "num");
  cell(r.status === "done" ? f2(t.total) + "s" : "\u2014", "num");
  cell(r.status === "done" ? String(res.prefix_cache_hit_tokens || 0) : "\u2014", "num");
  cell(r.status === "done" ? specCell(sp) : "\u2014", "num");
  // expandable raw-record row
  const det = document.createElement("tr"); det.className = "detail";
  det.style.display = "none";
  const dtd = document.createElement("td"); dtd.colSpan = 13;
  dtd.textContent =
    "start: " + JSON.stringify(r.start || null) + "\ndone: " + JSON.stringify(d || null);
  det.appendChild(dtd);
  tr.addEventListener("click", () => {
    det.style.display = det.style.display === "none" ? "" : "none";
  });
  frag.append(tr, det);
  return frag;
}

function renderTable() {
  const body = $("reqBody");
  const f = ($("reqFilter").value || "").trim().toLowerCase();
  const wait = waitingSet();
  const st = (r) => (wait.has(r.id) ? "waiting" : r.status);
  const all = [...reqs.values()].sort((a, b) => (b.startMs || 0) - (a.startMs || 0));
  const MAXSHOW = 500;
  let rows = all.slice(0, MAXSHOW);
  if (f) rows = rows.filter((r) => {
    const q = (r.start && r.start.request) || {}, res = (r.done && r.done.result) || {};
    const hay = (r.id + " " + (q.protocol || "") + " " + (res.finish_reason || "") + " " + st(r))
      .toLowerCase();
    return hay.includes(f);
  });
  let nDone = 0, nAct = 0, nWait = 0, nRej = 0, nErr = 0;
  for (const r of all) {
    if (r.status === "done") nDone++;
    else if (r.status === "active") { if (wait.has(r.id)) nWait++; else nAct++; }
    else if (r.status === "rejected") nRej++;
    else nErr++;
  }
  body.textContent = "";
  const frag = document.createDocumentFragment();
  if (!rows.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td"); td.colSpan = 13; td.className = "empty";
    td.textContent = reqs.size === 0
      ? "\u2014 no requests yet \u2014" : "\u2014 no requests match the filter \u2014";
    tr.appendChild(td); frag.appendChild(tr);
  } else {
    for (const r of rows) frag.appendChild(reqRow(r, st(r)));
  }
  body.appendChild(frag);
  $("reqPill").textContent = String(all.length);
  $("reqSummary").textContent =
    "done " + nDone + " \u00b7 active " + nAct + " \u00b7 waiting " + nWait +
    " \u00b7 rejected " + nRej +
    " \u00b7 error " + nErr + (all.length > MAXSHOW ? " \u00b7 latest " + MAXSHOW + " of " + all.length : "");
}
