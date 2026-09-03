"use strict";
/* Config tab: launch command form.
 * Edits a launch profile: binary / artifact / host / port plus the
 * ninfer-serve flags from the "Server options" table in docs/serving.md.
 * ninfer-view always injects --host/--port and a fresh per-run
 * --request-log-jsonl; everything else is the profile's extra_flags.
 * The form round-trips extra_flags faithfully: flags the form does not
 * know (yet) are preserved verbatim in the "extra flags" box, so saving
 * never loses anything. */

const CFG_SECTIONS = [
  { title: "Target", desc: "what runs where", fields: [
    { id: "binary",   kind: "text", target: "binary",   label: "binary",
      hint: "path to the ninfer-serve binary" },
    { id: "artifact", kind: "text", target: "artifact", label: "artifact",
      hint: "path to the .ninfer artifact" },
    { id: "host",     kind: "text", target: "host",     label: "host",
      hint: "listen address \u00b7 default 127.0.0.1" },
    { id: "port",     kind: "int",  target: "port",     label: "port",
      hint: "listen port \u00b7 default 8080" },
    { id: "model_id", flag: "--model-id", kind: "text", label: "model id",
      hint: "public alias \u00b7 default: artifact identity.model_id" },
    { id: "api_key",  flag: "--api-key",  kind: "text", label: "api key",
      hint: "required bearer / x-api-key \u00b7 empty: no auth" },
  ]},
  { title: "Sizing", desc: "context \u00b7 KV pool \u00b7 admission", fields: [
    { id: "max_context", flag: "--max-context", kind: "int", label: "max context",
      hint: "tokens per sequence \u00b7 default 8192", min: 1 },
    { id: "kv_capacity", flag: "--kv-capacity", kind: "kv", label: "kv capacity",
      hint: "shared pool \u00b7 default: follows max context" },
    { id: "kv_capacity_value", kind: "int", label: "kv tokens",
      hint: "used when kv capacity = explicit", min: 1 },
    { id: "kv_dtype", flag: "--kv-dtype", kind: "enum",
      options: ["bf16", "int8"], default: "bf16", label: "kv dtype",
      hint: "default bf16" },
    { id: "max_concurrency", flag: "--max-concurrency", kind: "int",
      label: "max concurrency", hint: "active requests \u00b7 1..8 \u00b7 default 1",
      min: 1, max: 8 },
    { id: "max_pending_requests", flag: "--max-pending-requests", kind: "int",
      label: "max pending", hint: "queued behind active \u00b7 default 16", min: 0 },
    { id: "pending_timeout_ms", flag: "--pending-timeout-ms", kind: "int",
      label: "pending timeout", hint: "prep + admission wait \u00b7 default 30000 ms",
      min: 0 },
    { id: "prefill_chunk", flag: "--prefill-chunk", kind: "int",
      label: "prefill chunk", hint: "tokens \u00b7 default 1024", min: 1 },
    { id: "default_max_tokens", flag: "--default-max-tokens", kind: "int",
      label: "default max tokens",
      hint: "output limit when the request omits it \u00b7 default 8192", min: 1 },
  ]},
  { title: "Speculative", desc: "omitting --spec loads neither backend", fields: [
    { id: "spec", flag: "--spec", kind: "enum",
      options: ["off", "mtp", "dflash"], default: "off", label: "backend",
      hint: "mtp or dflash" },
    { id: "draft_tokens", flag: "--draft-tokens", kind: "int", label: "draft tokens",
      hint: "MTP 1..5 \u00b7 DFlash 1..15", min: 1, max: 15, spec: true },
    { id: "lm_head_draft", flag: "--lm-head-draft", kind: "bool",
      label: "lm-head draft", hint: "optimized proposal head", spec: true },
  ]},
  { title: "Behavior", fields: [
    { id: "vision", flag: "--vision", kind: "bool", label: "vision",
      hint: "media input + Vision GPU allocations" },
    { id: "no_thinking", flag: "--no-thinking", kind: "bool", label: "no thinking",
      hint: "default: thinking on" },
    { id: "preserve_thinking", flag: "--preserve-thinking", kind: "bool",
      label: "preserve thinking", hint: "keep closed-turn reasoning in prompts" },
    { id: "no_cuda_graph", flag: "--no-cuda-graph", kind: "bool",
      label: "no CUDA graph", hint: "default: graphs on" },
    { id: "no_prefix_reuse", flag: "--no-prefix-reuse", kind: "bool",
      label: "no prefix reuse", hint: "default: prefix caching on" },
    { id: "cors", flag: "--cors", kind: "bool", label: "CORS",
      hint: "permissive browser CORS headers" },
    { id: "device", flag: "--device", kind: "int", label: "CUDA device",
      hint: "index \u00b7 default 0", min: 0 },
    { id: "log_stats_interval_ms", flag: "--log-stats-interval-ms", kind: "int",
      label: "stats interval", hint: "throughput report \u00b7 0 disables \u00b7 default 5000 ms",
      min: 0 },
  ]},
  { title: "Limits", desc: "bodies \u00b7 media \u00b7 stored responses", fields: [
    { id: "max_request_mib", flag: "--max-request-mib", kind: "int",
      label: "max request", hint: "MiB before JSON parse \u00b7 default 384", min: 1 },
    { id: "media_cache_mib", flag: "--media-cache-mib", kind: "int",
      label: "media cache", hint: "MiB LRU-retained \u00b7 0 disables \u00b7 default 1024",
      min: 0 },
    { id: "media_live_mib", flag: "--media-live-mib", kind: "int",
      label: "media live", hint: "MiB all live payloads \u00b7 default 2048", min: 0 },
    { id: "media_preprocess_threads", flag: "--media-preprocess-threads",
      kind: "int", label: "media threads", hint: "0 = auto (\u226416) \u00b7 default 0", min: 0 },
    { id: "response_store_max_records", flag: "--response-store-max-records",
      kind: "int", label: "responses: records", hint: "local Response LRU \u00b7 default 1024",
      min: 1 },
    { id: "response_store_max_mib", flag: "--response-store-max-mib", kind: "int",
      label: "responses: MiB", hint: "Response budget \u00b7 default 256", min: 1 },
  ]},
  { title: "Sampling overrides",
    desc: "process-level; request fields win, --greedy wins last", fields: [
    { id: "temperature", flag: "--temperature", kind: "num", label: "temperature",
      hint: "unset: model preset" },
    { id: "top_p", flag: "--top-p", kind: "num", label: "top-p", hint: "0..1",
      min: 0, max: 1 },
    { id: "top_k", flag: "--top-k", kind: "int", label: "top-k",
      hint: "unset: model preset", min: 0 },
    { id: "min_p", flag: "--min-p", kind: "num", label: "min-p", hint: "0..1",
      min: 0, max: 1 },
    { id: "presence_penalty", flag: "--presence-penalty", kind: "num",
      label: "presence penalty", hint: "unset: model preset" },
    { id: "frequency_penalty", flag: "--frequency-penalty", kind: "num",
      label: "frequency penalty", hint: "unset: model preset" },
    { id: "seed", flag: "--seed", kind: "int", label: "seed",
      hint: "fixed seed when the request omits one", min: 0 },
    { id: "greedy", flag: "--greedy", kind: "bool", label: "greedy",
      hint: "force exact argmax (temperature 0)" },
  ]},
];

function cfgShlexSplit(s) {
  const out = []; let cur = "", q = null;
  for (const ch of s) {
    if (q) { if (ch === q) q = null; else cur += ch; }
    else if (ch === '"' || ch === "'") q = ch;
    else if (ch === " " || ch === "\t") { if (cur) { out.push(cur); cur = ""; } }
    else cur += ch;
  }
  if (cur) out.push(cur);
  return out;
}

function cfgQuote(s) {
  return /[\s"'\\]/.test(s) ? "'" + s.replace(/'/g, "'\\''") + "'" : s;
}

function cfgDefaultValues() {
  const v = {
    binary: "/home/kyle/ninfer/build/apps/ninfer-serve",
    artifact: "",
    host: "127.0.0.1",
    port: "8080",
    raw: "",
    kv_capacity: "follow",      // follow | auto | num
    kv_capacity_value: "",
  };
  for (const sec of CFG_SECTIONS) for (const f of sec.fields) {
    if (f.id === "kv_capacity" || f.id === "kv_capacity_value") continue;
    v[f.id] = (f.kind === "bool") ? false
      : (f.kind === "enum" ? (f.default || "") : "");
  }
  return v;
}

// values -> extra_flags (documented order; raw flags appended last)
function cfgValuesToFlags(v) {
  const out = [];
  const specOn = ((v.spec || "off") !== "off");
  for (const sec of CFG_SECTIONS) for (const f of sec.fields) {
    if (!f.flag || (f.spec && !specOn)) continue;
    const val = v[f.id];
    if (f.kind === "bool") { if (val) out.push(f.flag); }
    else if (f.kind === "kv") {
      if (val === "auto") out.push(f.flag, "auto");
      else if (val === "num" && v.kv_capacity_value !== "" &&
               v.kv_capacity_value != null)
        out.push(f.flag, String(v.kv_capacity_value));
    }
    else if (f.kind === "enum") {
      // equal to the documented default -> omit
      if (val && val !== (f.default || "")) out.push(f.flag, String(val));
    }
    else if (val !== "" && val != null) out.push(f.flag, String(val));
  }
  if (v.raw && v.raw.trim()) out.push.apply(out, cfgShlexSplit(v.raw.trim()));
  return out;
}

// extra_flags -> values (unknown flags preserved verbatim in v.raw)
function cfgFlagsToValues(extra) {
  const v = cfgDefaultValues();
  const byFlag = new Map();
  for (const sec of CFG_SECTIONS) for (const f of sec.fields)
    if (f.flag) byFlag.set(f.flag, f);
  const raw = [];
  let i = 0;
  while (i < extra.length) {
    const tok = String(extra[i]);
    if (tok === "--request-log-jsonl") {
      // always injected per run; drop a hand-written one (and its value)
      if (i + 1 < extra.length && !String(extra[i + 1]).startsWith("--")) i++;
      i++; continue;
    }
    if (tok === "--host" || tok === "--port") {
      // injected from the form's target row; fold into it
      if (i + 1 < extra.length) {
        const val = String(extra[i]);
        v[val === "--host" ? "host" : "port"] = String(extra[i + 1]);
        i += 2;
      } else i++;
      continue;
    }
    const f = tok.startsWith("--") ? byFlag.get(tok) : null;
    if (!f) {
      raw.push(tok);
      if (i + 1 < extra.length && !String(extra[i + 1]).startsWith("--"))
        raw.push(String(extra[++i]));
      i++; continue;
    }
    i++;
    if (f.kind === "bool") { v[f.id] = true; continue; }
    if (f.kind === "kv") {
      if (i < extra.length) {
        const val = String(extra[i++]);
        v[f.id] = (val === "auto") ? "auto" : "num";
        if (val !== "auto") v.kv_capacity_value = val;
      }
      continue;
    }
    if (i < extra.length) { v[f.id] = String(extra[i]); i++; }
  }
  v.raw = raw.join(" ");
  return v;
}

// The exact command the supervisor will spawn.
function cfgCommand(v) {
  const a = [v.binary || "?", v.artifact || "?",
             "--host", v.host || "127.0.0.1", "--port", v.port || "8080",
             "--request-log-jsonl", "<run>/requests.jsonl"];
  a.push.apply(a, cfgValuesToFlags(v));
  return a.map(cfgQuote).join(" ");
}

function cfgValidate(v) {
  const errs = [], warns = [];
  if (!v.binary || !v.binary.trim()) errs.push("binary is required");
  if (!v.artifact || !v.artifact.trim()) errs.push("artifact is required");
  const port = Number(v.port);
  if (!Number.isInteger(port) || port < 1 || port > 65535)
    errs.push("port: must be an integer 1..65535");
  for (const sec of CFG_SECTIONS) for (const f of sec.fields) {
    if (f.kind === "bool" || f.kind === "kv" || f.kind === "enum" ||
        f.target) continue;
    if (f.id === "kv_capacity_value") {
      if (v.kv_capacity === "num" &&
          !(Number.isInteger(+v.kv_capacity_value) && +v.kv_capacity_value >= 1))
        errs.push("kv capacity: needs a token count when explicit (or pick follow/auto)");
      continue;
    }
    const val = v[f.id];
    if (val === "" || val == null) continue;
    const n = Number(val);
    if (!Number.isFinite(n)) { errs.push(f.label + ": not a number"); continue; }
    if (f.kind === "int" && !Number.isInteger(n))
      errs.push(f.label + ": must be an integer");
    if (f.min != null && n < f.min) errs.push(f.label + ": minimum is " + f.min);
    if (f.max != null && n > f.max) errs.push(f.label + ": maximum is " + f.max);
  }
  const spec = v.spec || "off";
  const dt = (v.draft_tokens === "" || v.draft_tokens == null) ? null
    : Number(v.draft_tokens);
  if (spec === "mtp" && dt != null && (dt < 1 || dt > 5))
    errs.push("draft tokens: MTP allows 1..5");
  if (spec === "dflash" && dt != null && (dt < 1 || dt > 15))
    errs.push("draft tokens: DFlash allows 1..15");
  if (spec !== "off" && dt == null)
    warns.push("--spec " + spec + " without --draft-tokens: no draft window set");
  if (spec === "dflash" && v.vision)
    warns.push("DFlash is 35B-A3B text-only and cannot be combined with --vision");
  if (v.greedy && v.temperature !== "")
    warns.push("--greedy forces temperature 0; the temperature override is ignored");
  return { errs, warns };
}

const cfg = { profiles: {}, id: "default", dirty: false, values: null, els: {} };
const cfgProfileSel = $("cfgProfile"), cfgDirtyEl = $("cfgDirty"),
      cfgStart = $("cfgStart");

function cfgBuildForm() {
  const root = $("cfgForm");
  root.textContent = "";
  cfg.els = {};
  const mkLabel = (t) => {
    const l = document.createElement("label"); l.textContent = t; return l;
  };
  const mkHint = (t) => {
    const h = document.createElement("div");
    h.className = "cfghint"; h.textContent = t; return h;
  };
  const secBox = (title, desc) => {
    const box = document.createElement("div"); box.className = "cfgsec";
    const head = document.createElement("div"); head.className = "cfgsect";
    const t = document.createElement("span"); t.textContent = title;
    head.appendChild(t);
    if (desc) {
      const d = document.createElement("span"); d.className = "desc";
      d.textContent = desc; head.appendChild(d);
    }
    box.appendChild(head);
    const grid = document.createElement("div"); grid.className = "cfggrid";
    box.appendChild(grid);
    root.appendChild(box);
    return grid;
  };
  const wire = (el, id) => {
    el.dataset.fid = id;
    el.spellcheck = false;
    el.addEventListener("input", onCfgInput);
    el.addEventListener("change", onCfgInput);
    cfg.els[id] = el;
  };
  for (const sec of CFG_SECTIONS) {
    const grid = secBox(sec.title, sec.desc);
    for (const f of sec.fields) {
      const cell = document.createElement("div");
      const lab = mkLabel(f.label);
      let input;
      if (f.kind === "bool") {
        cell.className = "cfgf cfgbool";
        input = document.createElement("input");
        input.type = "checkbox";
        const row = document.createElement("div"); row.className = "row";
        row.appendChild(input); row.appendChild(lab);
        cell.appendChild(row);
      } else {
        cell.className = "cfgf";
        cell.appendChild(lab);
        if (f.kind === "kv") {
          input = document.createElement("select"); input.className = "cfginput";
          for (const [val, label] of [["follow", "follow max context"],
                                      ["auto", "auto"],
                                      ["num", "explicit tokens"]]) {
            const o = document.createElement("option");
            o.value = val; o.textContent = label; input.appendChild(o);
          }
          cell.appendChild(input);
          const num = document.createElement("input");
          num.className = "cfginput"; num.type = "number"; num.step = "1";
          num.placeholder = "tokens, e.g. 32768";
          cell.appendChild(num);
          wire(num, "kv_capacity_value");
        } else if (f.kind === "enum") {
          input = document.createElement("select"); input.className = "cfginput";
          for (const o of f.options) {
            const op = document.createElement("option");
            op.value = o; op.textContent = o; input.appendChild(op);
          }
          cell.appendChild(input);
        } else {
          input = document.createElement("input");
          input.className = "cfginput";
          input.type = (f.kind === "text") ? "text" : "number";
          if (f.kind === "int") input.step = "1";
          if (f.kind === "num") input.step = "any";
          if (f.min != null) input.min = f.min;
          if (f.max != null) input.max = f.max;
          cell.appendChild(input);
        }
      }
      wire(input, f.id);
      cell.appendChild(mkHint(f.hint || ""));
      grid.appendChild(cell);
    }
  }
  const grid = secBox("Advanced", "appended last, verbatim \u2014 for flags not in the form");
  const cell = document.createElement("div"); cell.className = "cfgf cfgwide";
  cell.appendChild(mkLabel("extra flags"));
  const raw = document.createElement("input");
  raw.className = "cfginput"; raw.type = "text";
  raw.placeholder = "--flag value --flag2 ...";
  cell.appendChild(raw);
  cell.appendChild(mkHint("space-separated, quoted values ok \u00b7 round-trips untouched"));
  grid.appendChild(cell);
  wire(raw, "raw");
}

function cfgApplySpec() {
  const off = ((cfg.values && cfg.values.spec) || "off") === "off";
  const dt = cfg.els.draft_tokens, lm = cfg.els.lm_head_draft;
  if (dt) dt.disabled = off;
  if (lm) lm.disabled = off;
}

function cfgApplyKv() {
  const el = cfg.els.kv_capacity_value;
  if (el) el.disabled = (cfg.values.kv_capacity || "follow") !== "num";
}

function cfgRefresh() {
  if (!cfg.values) return;
  cfgApplySpec(); cfgApplyKv();
  $("cmdPreview").textContent = cfgCommand(cfg.values);
  const { errs, warns } = cfgValidate(cfg.values);
  const w = $("cfgWarns");
  w.textContent = "";
  const add = (cls, txt) => {
    const d = document.createElement("div");
    d.className = cls; d.textContent = txt; w.appendChild(d);
  };
  for (const e of errs) add("cfgerr", "\u2716 " + e);
  for (const e of warns) add("cfgwarn", "\u26a0 " + e);
  cfgDirtyEl.textContent = cfg.dirty ? "unsaved changes" : "";
}

function cfgSetValues() {
  const v = cfg.values;
  for (const fid in cfg.els) {
    const el = cfg.els[fid];
    const val = v[fid];
    if (el.type === "checkbox") el.checked = !!val;
    else el.value = (val == null) ? "" : String(val);
  }
}

function onCfgInput(ev) {
  const el = ev.target;
  const fid = el.dataset && el.dataset.fid;
  if (!fid || !cfg.values) return;
  cfg.values[fid] = (el.type === "checkbox") ? el.checked : el.value;
  cfg.dirty = true;
  cfgRefresh();
}

// ---- profile load / save ---------------------------------------------------

function cfgProfileOrder() {
  const ids = Object.keys(cfg.profiles).sort();
  if (ids.indexOf(cfg.id) === -1) ids.push(cfg.id); // unsaved new profile
  const i = ids.indexOf("default");
  if (i > 0) { ids.splice(i, 1); ids.unshift("default"); }
  return ids;
}

function cfgRebuildDropdown() {
  const sel = cfgProfileSel;
  sel.textContent = "";
  for (const id of cfgProfileOrder()) {
    const p = cfg.profiles[id] || {};
    const o = document.createElement("option");
    o.value = id;
    o.textContent = (p.name && p.name !== id) ? p.name + " \u00b7 " + id : id;
    sel.appendChild(o);
  }
  sel.value = cfg.id;
}

function cfgLoadProfile(id) {
  const p = cfg.profiles[id] || {};
  const v = cfgFlagsToValues(Array.isArray(p.extra_flags) ? p.extra_flags : []);
  if (p.binary) v.binary = String(p.binary);
  v.artifact = p.artifact ? String(p.artifact) : "";
  if (p.host != null && String(p.host) !== "") v.host = String(p.host);
  if (p.port != null) v.port = String(p.port);
  cfg.values = v;
  cfgSetValues();
  cfg.dirty = false;
  cfgRefresh();
}

async function cfgLoadProfiles() {
  try {
    const r = await fetch("/api/profiles");
    const d = await r.json();
    if (d && d.profiles) cfg.profiles = d.profiles;
  } catch (e) { /* keep whatever we have */ }
  if (!cfg.profiles[cfg.id]) cfg.id = cfgProfileOrder()[0] || "default";
  cfgRebuildDropdown();
  cfgLoadProfile(cfg.id);
}

function cfgFormToProfile() {
  const v = cfg.values, ex = cfg.profiles[cfg.id];
  return {
    id: cfg.id,
    name: (ex && ex.name) ? ex.name : cfg.id,
    binary: String(v.binary || "").trim(),
    artifact: String(v.artifact || "").trim(),
    host: String(v.host || "127.0.0.1").trim(),
    port: Number(v.port) || 8080,
    extra_flags: cfgValuesToFlags(v),
  };
}

async function cfgSaveProfile() {
  const { errs } = cfgValidate(cfg.values);
  if (errs.length) { alert("fix the config first:\n" + errs.join("\n")); return false; }
  const prof = cfgFormToProfile();
  try {
    const r = await fetch("/api/profiles", { method: "POST",
      body: JSON.stringify({ profile: prof }) });
    const d = await r.json();
    if (!d.ok) { alert("save failed: " + (d.error || r.status)); return false; }
  } catch (e) {
    alert("save failed: " + e); return false;
  }
  cfg.profiles[prof.id] = prof;
  cfg.dirty = false;
  cfgRebuildDropdown();
  cfgRefresh();
  return true;
}

function syncStartBtns() {
  const can = ["stopped", "crashed"].includes((lastState || {}).state || "stopped");
  startBtn.disabled = !can;
  cfgStart.disabled = !can;
}

// Shared by the header and the Config tab: validate, persist unsaved edits,
// then launch the selected profile.
async function doStart() {
  if (cfg.values) {
    const { errs } = cfgValidate(cfg.values);
    if (errs.length) { alert("config errors:\n" + errs.join("\n")); return; }
  }
  const pid = cfg.id;
  startBtn.disabled = true; cfgStart.disabled = true;
  if (cfg.dirty) {
    const ok = await cfgSaveProfile();
    if (!ok) { syncStartBtns(); return; }
  }
  try {
    const r = await fetch("/api/start", { method: "POST",
      body: JSON.stringify({ profile_id: pid }) });
    const d = await r.json();
    if (!d.ok) alert("start failed: " + (d.error || r.status));
  } catch (e) {
    alert("start failed: " + e);
  }
  syncStartBtns();
}

cfgStart.onclick = doStart;
$("cfgSave").onclick = () => { cfgSaveProfile(); };
$("cfgReset").onclick = () => {
  if (cfg.dirty && !confirm("discard unsaved changes?")) return;
  cfgLoadProfile(cfg.id);
};
$("cfgNew").onclick = () => {
  if (cfg.dirty && !confirm("discard unsaved changes to " + cfg.id + "?")) return;
  const raw = prompt("new profile id:");
  if (raw == null) return;
  const id = String(raw).trim().toLowerCase().replace(/[^a-z0-9._-]/g, "-");
  if (!id) return;
  if (cfg.profiles[id] && !confirm("profile " + id + " already exists \u2014 overwrite?")) return;
  cfg.id = id;
  // template: start from the values currently in the form
  cfg.values = cfg.values ? JSON.parse(JSON.stringify(cfg.values))
                          : cfgDefaultValues();
  cfg.dirty = true;
  cfgSetValues();
  cfgRebuildDropdown();
  cfgRefresh();
};
cfgProfileSel.addEventListener("change", () => {
  const id = cfgProfileSel.value;
  if (!id || id === cfg.id) return;
  if (cfg.dirty && !confirm("discard unsaved changes?")) {
    cfgProfileSel.value = cfg.id; return;
  }
  cfg.id = id;
  cfgLoadProfile(id);
});
cfgBuildForm();
cfgLoadProfiles();
