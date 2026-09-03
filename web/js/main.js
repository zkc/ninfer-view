"use strict";
/* State header, live SSE stream, header controls, tab switching, and boot.
 * Loads last: it wires handlers that reach into the other files. */

// ---- state header ---------------------------------------------------------

function applyState(s) {
  lastState = s || lastState;
  const st = lastState.state || "stopped";
  badge.className = "badge " + st;
  stateText.textContent = st.toUpperCase();
  const canStart = ["stopped", "crashed"].includes(st);
  startBtn.disabled = !canStart;
  cfgStart.disabled = !canStart;
  attachBtn.disabled = !canStart;
  stopBtn.disabled = !["starting", "loading", "warming", "running",
                       "attached"].includes(st);
  stopBtn.textContent = st === "attached" ? "Detach" : "Stop";

  const hEl = $("health");
  hEl.textContent = lastState.health ? "health: " + lastState.health : "";
  hEl.className = lastState.health || "";

  const bits = [];
  if (lastState.model_id) bits.push("<b>" + lastState.model_id + "</b>");
  if (lastState.endpoint) bits.push(lastState.endpoint);
  if (lastState.pid != null) bits.push("pid " + lastState.pid);
  if (lastState.exit_code != null) bits.push("exit " + lastState.exit_code);
  if (lastState.error) bits.push("\u26a0 " + lastState.error);
  metaEl.innerHTML = bits.length ? bits.join(" \u00b7 ") : "no instance";
  $("runDir").textContent = lastState.run_dir ||
    (lastState.attached ? "jsonl: " + (lastState.jsonl_path || "") : "");

  if ((st === "loading" || st === "starting") && lastState.progress) {
    const p = lastState.progress;
    progEl.style.display = "block";
    $("progPhase").textContent = "loading \u00b7 " + (p.phase || "");
    $("progPct").textContent = p.percent != null ? p.percent.toFixed(1) + "%" : "";
    $("progBytes").textContent = p.total ? p.done + " / " + p.total : (p.done || "");
    $("progElapsed").textContent =
      p.elapsed_s != null ? p.elapsed_s.toFixed(0) + " s elapsed" : "";
    $("progFill").style.width = (p.percent == null ? 4 : p.percent) + "%";
  } else if (st === "warming") {
    progEl.style.display = "block";
    $("progPhase").textContent = "warming up\u2026";
    $("progPct").textContent = ""; $("progBytes").textContent = "";
    $("progElapsed").textContent = "";
    $("progFill").style.width = "100%";
  } else {
    progEl.style.display = "none";
  }
}

function tick() {
  const now = Date.now() / 1000;
  if (lastState.state === "running" && lastState.running_at) {
    uptimeEl.textContent = fmtUp(now - lastState.running_at);
  } else if (lastState.started_at &&
             ["starting", "loading", "warming", "attached"].includes(lastState.state)) {
    uptimeEl.textContent = (lastState.state === "attached" ? "observing " : "started ")
      + Math.round(now - lastState.started_at) + "s ago";
  } else {
    uptimeEl.textContent = "";
  }
}

// ---- live stream -----------------------------------------------------------

const LOG_KINDS = ["server_start", "request_start", "request_rejected",
                   "request_done", "request_error", "throughput"];

function connect() {
  const es = new EventSource("/api/stream");
  es.addEventListener("state", (ev) => { applyState(JSON.parse(ev.data)); tick(); });
  for (const kind of LOG_KINDS) {
    es.addEventListener(kind, (ev) => {
      const d = JSON.parse(ev.data);
      addEntry(makeEntry(d.record));
      ingest(d.record);
    });
  }
  es.addEventListener("error", () => {
    // EventSource reconnects on its own; re-backfill history on failure
    // (ingest is idempotent, so re-feeding the model is safe).
    fetch("/api/logs?limit=2000").then((r) => r.json()).then((d) => {
      entries.length = 0;
      backfill(d);
    }).catch(() => {});
  });
}

// Start launches the profile selected in the Config tab; if that form has
// unsaved edits, they are persisted first, so the launch always matches the
// command preview. doStart() is defined in the Config tab section above.
startBtn.onclick = doStart;
stopBtn.onclick = async () => {
  stopBtn.disabled = true;
  const detach = lastState.state === "attached";
  const r = await fetch(detach ? "/api/detach" : "/api/stop", { method: "POST" });
  const d = await r.json();
  if (!d.ok) alert((detach ? "detach: " : "stop: ") + (d.error || r.status));
};

const attachForm = $("attachForm");
attachBtn.onclick = () => {
  attachForm.style.display = attachForm.style.display === "flex" ? "none" : "flex";
  if (attachForm.style.display === "flex") $("attJsonl").focus();
};
$("attCancel").onclick = () => { attachForm.style.display = "none"; };
$("attGo").onclick = async () => {
  const host = $("attHost").value.trim() || "127.0.0.1";
  const port = parseInt($("attPort").value, 10) || 8081;
  const jsonl_path = $("attJsonl").value.trim();
  if (!jsonl_path) { $("attJsonl").focus(); return; }
  attachBtn.disabled = true;
  const r = await fetch("/api/attach", { method: "POST",
    body: JSON.stringify({ host, port, jsonl_path }) });
  const d = await r.json();
  if (!d.ok) { alert("attach failed: " + (d.error || r.status)); return; }
  attachForm.style.display = "none";
};
$("filter").addEventListener("input", applyFilter);
$("reqFilter").addEventListener("input", renderTable);

// Tab switching (charts re-render on show so the canvas gets real dimensions).
document.querySelectorAll(".tab").forEach((t) => {
  t.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
    t.classList.add("active");
    const name = t.dataset.tab;
    ["log", "requests", "metrics", "config"].forEach((p) => {
      $("pane-" + p).style.display = (p === name) ? "flex" : "none";
    });
    if (name === "metrics") renderCharts();
    if (name === "requests") renderTable();
    if (name === "log" && pinned) logEl.scrollTop = logEl.scrollHeight;
  });
});
window.addEventListener("resize", () => {
  if ($("pane-metrics").style.display !== "none") renderCharts();
});

// Backfill + initial state, then go live.
function backfill(d) {
  for (const e of d.logs) { entries.push(makeEntry(e.record)); ingest(e.record); }
  if (entries.length > MAX) entries.splice(0, entries.length - MAX);
  applyFilter();
  renderTable();
  renderCharts();
}
fetch("/api/state").then((r) => r.json()).then(applyState).catch(() => {});
fetch("/api/logs?limit=2000").then((r) => r.json()).then(backfill).catch(() => {});
setInterval(tick, 1000);
// Idle-advance timer: the charts' window position is computed from real time
// in chartRefNow() (one interval of axis per interval of real time), so this
// timer only triggers repaints so the axis keeps moving while the server is
// idle. It runs at half the stats cadence so the tick lands within ~2.5 s of
// each interval boundary; the actual position is always correct at render.
setInterval(() => {
  if (chartAnchorRef != null && tput.size &&
      $("pane-metrics").style.display !== "none") renderCharts();
}, Math.max(250, sampleIntervalMs / 2));
connect();
