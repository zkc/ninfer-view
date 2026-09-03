"use strict";
/* M2: metrics charts (no build step, plain canvas) — rolling-window line
 * charts over the shared `tput` series, with idle-advance anchoring from
 * util.js. */

function tsShort(ms) {
  const d = new Date(ms), p = (n) => String(n).padStart(2, "0");
  return p(d.getHours()) + ":" + p(d.getMinutes()) + ":" + p(d.getSeconds());
}

// Second-aligned x tick times across [xMin, xMax] — stable labels while the
// rolling window scrolls (no wobbling mid-second ticks).
function tickTimes(xMin, xMax) {
  const span = Math.max(1, xMax - xMin);
  const steps = [2000, 5000, 10000, 15000, 30000, 60000, 120000, 300000, 600000];
  const step = steps.find((s) => span / s <= 7) || 600000;
  const out = [];
  for (let t = Math.ceil(xMin / step) * step; t <= xMax; t += step) out.push(t);
  return out;
}

function sizeCanvas(cv) {
  const dpr = window.devicePixelRatio || 1;
  const w = cv.clientWidth || cv.parentElement.clientWidth || 400;
  const h = cv.clientHeight || 200;
  cv.width = Math.max(1, Math.round(w * dpr));
  cv.height = Math.max(1, Math.round(h * dpr));
  const ctx = cv.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { ctx, w, h };
}

function drawChart(cv, o) {
  const { ctx, w, h } = sizeCanvas(cv);
  ctx.clearRect(0, 0, w, h);
  const padL = 46, padR = o.yRight ? 46 : 10, padT = 12, padB = 20;
  const pw = w - padL - padR, ph = h - padT - padB;
  if (pw < 20 || ph < 20) return;
  const { xMin, xMax } = o;
  if (xMin == null || xMax == null || xMax <= xMin) {
    ctx.fillStyle = "#484f58"; ctx.font = "12px monospace"; ctx.textAlign = "left";
    ctx.fillText("no data yet", padL, h / 2);
    return;
  }
  // Y axes: the left axis always exists; `o.yRight` adds a second,
  // independently scaled axis (series opt in with `axis: "right"`), so e.g.
  // a prefill spike cannot squash the decode line — each series gets the
  // full vertical range on its own scale.
  const mkAxis = (side) => {
    let m = 0;
    // Scale to the points inside the rolling window only, so samples that
    // have scrolled out of view can't pin the axis scale.
    for (const s of o.series) if ((s.axis || "left") === side)
      for (const p of s.pts)
        if (p[0] >= xMin && p[0] <= xMax && p[1] != null && p[1] > m) m = p[1];
    const spec = side === "right" ? o.yRight : (o.yLeft || {});
    const min = spec.min != null ? spec.min
              : (side === "left" && o.yMin != null ? o.yMin : 0);
    const max = spec.max != null ? spec.max : (m <= 0 ? 1 : m * 1.12);
    return { min, max,
             fmt: spec.fmt || o.yFmt || ((v) => String(Math.round(v))),
             color: spec.color || "#8b949e" };
  };
  const AX = { left: mkAxis("left"), right: o.yRight ? mkAxis("right") : null };
  const X = (x) => padL + (x - xMin) / (xMax - xMin) * pw;
  const Y = (y, side) => {
    const a = side === "right" ? AX.right : AX.left;
    return padT + ph - (y - a.min) / (a.max - a.min) * ph;
  };
  // The axis is a rolling real-time window; if every point has scrolled out
  // (long idle), keep the time grid moving and say so instead of drawing an
  // empty box.
  let visible = 0;
  for (const s of o.series) for (const p of s.pts)
    if (p[0] >= xMin && p[0] <= xMax && p[1] != null) visible++;
  if (!visible) {
    ctx.strokeStyle = "#21262d"; ctx.lineWidth = 1; ctx.font = "10px monospace";
    for (let i = 0; i <= 4; i++) {
      const yy = padT + ph - (ph * i) / 4;
      ctx.beginPath(); ctx.moveTo(padL, yy); ctx.lineTo(w - padR, yy); ctx.stroke();
    }
    ctx.fillStyle = "#484f58"; ctx.font = "11px monospace"; ctx.textAlign = "center";
    ctx.fillText("no recent samples", padL + pw / 2, h / 2);
    return;
  }
  // grid + y labels (each axis labels its own scale; color matches series)
  ctx.strokeStyle = "#21262d"; ctx.lineWidth = 1; ctx.font = "10px monospace";
  ctx.textAlign = "right";
  for (let i = 0; i <= 4; i++) {
    const yy = padT + ph - (ph * i) / 4;
    ctx.beginPath(); ctx.moveTo(padL, yy); ctx.lineTo(w - padR, yy); ctx.stroke();
    const lv = AX.left.min + (AX.left.max - AX.left.min) * i / 4;
    ctx.fillStyle = AX.left.color; ctx.textAlign = "right";
    ctx.fillText(AX.left.fmt(lv), padL - 6, yy + 3);
    if (AX.right) {
      const rv = AX.right.min + (AX.right.max - AX.right.min) * i / 4;
      ctx.fillStyle = AX.right.color; ctx.textAlign = "left";
      ctx.fillText(AX.right.fmt(rv), w - padR + 6, yy + 3);
    }
  }
  // x labels (second-aligned ticks when provided — the rolling window)
  ctx.textAlign = "center";
  if (o.xticks && o.xticks.length) {
    for (const xv of o.xticks) {
      if (xv < xMin || xv > xMax) continue;
      ctx.fillStyle = "#8b949e";
      ctx.fillText(o.xFmt ? o.xFmt(xv) : "", X(xv), h - 6);
    }
  } else {
    for (let i = 0; i <= 4; i++) {
      const xv = xMin + (xMax - xMin) * i / 4;
      ctx.fillStyle = "#8b949e";
      ctx.fillText(o.xFmt ? o.xFmt(xv) : "", X(xv), h - 6);
    }
  }
  // series
  for (const s of o.series) {
    if (!s.pts.length) continue;
    ctx.strokeStyle = s.color; ctx.lineWidth = 1.6;
    ctx.setLineDash(s.dashed ? [4, 3] : []);
    ctx.beginPath();
    let started = false, lastT = null;
    for (const p of s.pts) {
      if (p[1] == null || p[0] < xMin) { started = false; lastT = null; continue; }
      // Break the line across time gaps longer than o.breakGapMs: after an
      // idle stretch the server resumes at a new timestamp, and drawing a
      // connector across the gap would show a long diagonal artefact.
      if (lastT != null && o.breakGapMs && p[0] - lastT > o.breakGapMs) started = false;
      const xx = X(p[0]), yy = Y(p[1], s.axis || "left");
      if (!started) { ctx.moveTo(xx, yy); started = true; } else ctx.lineTo(xx, yy);
      lastT = p[0];
    }
    ctx.stroke(); ctx.setLineDash([]);
  }
  // legend
  ctx.font = "10px monospace"; ctx.textAlign = "left";
  let lx = padL + 6, ly = padT + 9;
  for (const s of o.series) {
    ctx.fillStyle = s.color; ctx.fillRect(lx, ly - 7, 11, 3);
    ctx.fillStyle = "#8b949e"; ctx.fillText(s.name, lx + 15, ly);
    lx += 15 + ctx.measureText(s.name).width + 14;
  }
}

function renderCharts() {
  const pts = [...tput.values()].sort((a, b) => a.t - b.t);
  if (!pts.length) {
    $("curTput").textContent = "no throughput data yet";
    drawChart($("chartTput"), { series: [], xMin: null, xMax: null });
    drawChart($("chartSched"), { series: [], xMin: null, xMax: null });
    return;
  }
  const last = pts[pts.length - 1];
  $("curTput").textContent =
    "live \u00b7 prefill " + f1(last.pfill) + " tok/s \u00b7 decode " + f1(last.dec) +
    " tok/s \u00b7 running " + (last.running != null ? last.running : 0) +
    " \u00b7 waiting " + (last.waiting != null ? last.waiting : 0) +
    " \u00b7 batch " + f2(last.batch);
  // Rolling window whose right edge (xMax) is the anchored reference: pinned
  // to the latest sample while data flows, and advancing one interval per
  // interval of real time while idle — the same pace as following the input.
  // drawChart's gap-break (breakGapMs) keeps a resample after an idle stretch
  // from drawing a long diagonal; the idle stretch shows as blank axis.
  const winMs = Math.max(30000, 24 * sampleIntervalMs);  // ~2 min at the 5 s default
  const xMax = chartRefNow();
  const xMin = xMax - winMs;
  const xticks = tickTimes(xMin, xMax);
  const breakGapMs = 2 * sampleIntervalMs;  // tolerate timer jitter, break real idle gaps
  const vis = pts.filter((p) => p.t >= xMin);  // skip what has scrolled out
  drawChart($("chartTput"), {
    xMin, xMax, xticks, yFmt: (v) => String(Math.round(v)), xFmt: tsShort,
    breakGapMs,
    yLeft:  { min: 0, color: "#d29922" },
    yRight: { min: 0, color: "#3fb950" },
    series: [
      { name: "prefill", color: "#d29922", axis: "left",
        pts: vis.map((p) => [p.t, p.pfill]) },
      { name: "decode", color: "#3fb950", axis: "right",
        pts: vis.map((p) => [p.t, p.dec]) },
    ],
  });
  drawChart($("chartSched"), {
    xMin, xMax, xticks, yMin: 0, yFmt: (v) => String(Math.round(v)), xFmt: tsShort,
    breakGapMs,
    series: [
      { name: "running",      color: "#3fb950", pts: vis.map((p) => [p.t, p.running]) },
      { name: "prefilling",   color: "#d29922", pts: vis.map((p) => [p.t, p.prefilling]) },
      { name: "decode_ready", color: "#58a6ff", pts: vis.map((p) => [p.t, p.decode_ready]) },
      { name: "waiting",      color: "#f85149", pts: vis.map((p) => [p.t, p.waiting]) },
      { name: "avg batch",    color: "#d2a8ff", pts: vis.map((p) => [p.t, p.batch]), dashed: true },
    ],
  });
}
