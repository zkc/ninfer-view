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

// "Nice" tick step (1/2/5 * 10^k), rounded UP — for the second axis, which
// must share the first axis's interval count so both label the same lines.
function niceStepUp(raw) {
  if (!(raw > 0)) return 1;
  const pow = Math.pow(10, Math.floor(Math.log10(raw)));
  const n = raw / pow;
  const f = n <= 1 + 1e-9 ? 1 : n <= 2 + 1e-9 ? 2 : n <= 5 + 1e-9 ? 5 : 10;
  return f * pow;
}

// Extend [lo0, hi0] to round "nice" tick boundaries (1/2/5 * 10^k, d3-style)
// and return the tick values, so grid lines and labels land on round numbers
// even for small ranges (a 0–1.12 axis used to integer-round into 1,1,1,0,0).
function niceAxis(lo0, hi0, target) {
  if (!(hi0 > lo0)) hi0 = lo0 + 1;
  const step0 = (hi0 - lo0) / Math.max(1, target);
  const pow = Math.pow(10, Math.floor(Math.log10(step0)));
  const err = Number((step0 / pow).toPrecision(12));  // kill 0.2/0.1=2.000…4 noise
  // geometric-mean thresholds (d3-style): nearest of 1/2/5 * 10^k in log space
  const step = err >= 7.0711 ? 10 * pow : err >= 3.1623 ? 5 * pow
             : err >= 1.4142 ? 2 * pow : pow;
  const lo = Math.floor(lo0 / step) * step;
  const hi = Math.ceil(hi0 / step) * step;
  const n = Math.round((hi - lo) / step);
  const ticks = [];
  for (let i = 0; i <= n; i++) ticks.push(Number((lo + i * step).toPrecision(12)));
  return { lo, hi, ticks };
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
  const mkAxis = (side, nInt) => {
    let m = 0;
    // Scale to the points inside the rolling window only, so samples that
    // have scrolled out of view can't pin the axis scale.
    for (const s of o.series) if ((s.axis || "left") === side)
      for (const p of s.pts)
        if (p[0] >= xMin && p[0] <= xMax && p[1] != null && p[1] > m) m = p[1];
    const spec = side === "right" ? o.yRight : (o.yLeft || {});
    const min = spec.min != null ? spec.min
              : (side === "left" && o.yMin != null ? o.yMin : 0);
    const rawMax = spec.max != null ? spec.max : (m <= 0 ? 1 : m * 1.12);
    const lo0 = Math.min(min, rawMax), hi0 = Math.max(min, rawMax);
    let lo, hi, ticks;
    if (nInt != null) {
      // Second axis: keep the first axis's interval count so its labels land
      // on the same grid lines (dual-axis alignment).
      const st = niceStepUp((hi0 - lo0) / Math.max(1, nInt));
      lo = min; hi = min + nInt * st; ticks = [];
      for (let i = 0; i <= nInt; i++)
        ticks.push(Number((lo + i * st).toPrecision(12)));
    } else {
      const a = niceAxis(lo0, hi0, 5);
      lo = a.lo; hi = a.hi; ticks = a.ticks;
    }
    const step = ticks.length > 1 ? ticks[1] - ticks[0] : 1;
    const userFmt = spec.fmt || o.yFmt;
    // A rounding formatter can only keep grid labels distinct when lines are
    // >= 1 apart; for finer steps fall back to decimals so adjacent labels
    // never collapse into each other.
    const dec = step >= 1 ? 0 : step >= 0.1 ? 1 : 2;
    const fmt = userFmt && dec === 0 ? userFmt : (v) => {
      let s = v.toFixed(dec);
      if (s.indexOf(".") >= 0) s = s.replace(/0+$/, "").replace(/\.$/, "");
      return s === "-0" ? "0" : s;
    };
    return { min: lo, max: hi, ticks, fmt, color: spec.color || "#8b949e" };
  };
  const AX = { left: mkAxis("left"), right: null };
  if (o.yRight) AX.right = mkAxis("right", AX.left.ticks.length - 1);
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
  // grid + y labels: grid lines follow the left axis's ticks (round values);
  // each axis labels its own scale on those lines, color matches the series
  ctx.strokeStyle = "#21262d"; ctx.lineWidth = 1; ctx.font = "10px monospace";
  for (const v of AX.left.ticks) {
    const yy = Y(v, "left");
    ctx.beginPath(); ctx.moveTo(padL, yy); ctx.lineTo(w - padR, yy); ctx.stroke();
    ctx.fillStyle = AX.left.color; ctx.textAlign = "right";
    ctx.fillText(AX.left.fmt(v), padL - 6, yy + 3);
  }
  if (AX.right) {
    for (const v of AX.right.ticks) {
      ctx.fillStyle = AX.right.color; ctx.textAlign = "left";
      ctx.fillText(AX.right.fmt(v), w - padR + 6, Y(v, "right") + 3);
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
