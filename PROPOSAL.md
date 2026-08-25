# ninfer-view — Proposal & Implementation Plan

A small local app that (1) configures and launches a `ninfer-serve` instance, and (2) shows a
live, pretty view of what it is doing — active requests, throughput, speculative-decoding stats,
and the raw console log. Optionally it attaches to an already-running instance and shows the same
view, and a top-bar (GNOME) tray icon exposes status + start/stop.

---

## 1. What we can build this on (facts from the repo)

### 1.1 Launching a server

- Binary: `/home/kyle/ninfer/build/apps/ninfer-serve` (built, ~150 MB).
- Invocation: `ninfer-serve <artifact.ninfer> [flags...]`. Full option contract documented in
  `docs/serving.md` (§Server options); `--help` matches it.
- Key flags for the config UI (subset, grouped):
  - **Core**: artifact path, `--host`, `--port`, `--model-id`, `--api-key`
  - **Sizing**: `--max-context`, `--kv-capacity N|auto`, `--kv-dtype bf16|int8`, `--max-concurrency 1..8`
  - **Speculative**: `--spec mtp|dflash`, `--draft-tokens`, `--lm-head-draft`
  - **Behavior**: `--vision`, `--no-thinking`, `--preserve-thinking`, `--no-cuda-graph`,
    `--no-prefix-reuse`, `--log-stats-interval-ms`, `--device`, sampling overrides
- Your current known-good launch (`ninfer serve.txt` in the repo root) becomes the default preset:
  `qwen3_8_27b_nvfp4.ninfer`, port 8081, `--kv-dtype int8`, `--spec mtp --draft-tokens 3
  --lm-head-draft`, `--max-context 262144`, `--max-concurrency 2`.

### 1.2 Three data sources for the live view

All three exist **without any changes to ninfer**:

1. **stderr console** — every line is exactly:
   ```
   [YYYY-MM-DD HH:MM:SS.mmm] [info|warning|error] ninfer-serve: <message>
   ```
   Lifecycle lines (from `apps/serve/main.cpp`):
   ```
   ... loading model...
   ... load  <phase>  <pct>  <done> / <total bytes>  <elapsed>   (once per ~10 s when piped)
   ... model loaded in N s
   ... KV capacity auto resolved=... tokens pages=... runtime=... free-after-weights=... graphs=...
   ... warming up...
   ... listening on http://127.0.0.1:8081 (model id: qwen3.8-27b, auth: disabled)
   ```
   Activity lines (from `request_log.cpp`):
   ```
   [req 42] openai_chat stream msgs=3 max_tokens=8192 (server default) ... → submitted
   [req 42] done finish=stop prompt=1520 gen=861 cache=1498 reuse=append_frontier ttft=312ms \
            prefill=9820.4tok/s decode=41.7tok/s wall=20.65s speculative=mtp draft=3 acc=2.71
   [req 43] rejected phase=prepare ... status=400 code=context_length_exceeded ...
   [req 44] error <message>
   throughput interval=5.000s prefill=120.0tok/s decode=43.1tok/s running=2 prefilling=0 \
            decode_ready=2 waiting=0 avg_decode_batch=1.98     (every 5 s while active)
   ```
   Note: with stderr piped (our case), load progress uses *Log mode*: plain newline-terminated
   lines every ~10 s — no `\r` in-place updates to parse.

2. **`--request-log-jsonl FILE`** — machine-readable schema-v10 JSONL, flushed after every event,
   opened in **append mode** (we give it a fresh file per run). Events:
   | Event | What the UI gets |
   |---|---|
   | `server_start` | artifact/weights identity, resolved Engine, sampler defaults, KV sizing ledger, CUDA Graph bytes, redacted argv — a "server card" |
   | `request_start` | protocol, sampler/seed, thinking modes, budget, media/tool shape, prep timings |
   | `request_rejected` | exact HTTP status/type/code/parameter/message |
   | `request_done` | finish reason, prompt/completion/cache tokens, `prefix_reuse_path`, unrounded `timings_seconds {prepare,ttft,vision,prefill,decode,total}`, full speculative counters (`rounds`, `drafted_tokens`, `accepted_tokens`, `accepted_per_position`, `fallback_steps`) |
   | `request_error` | resolved config + error message |
   | `throughput` | raw token/round deltas + scheduler snapshot (`running`, `prefilling`, `decode_ready`, `waiting`) + derived rates |

   Every line carries `timestamp_unix_ms` and `server_instance_id`; request IDs are monotonic per
   instance. This is the authoritative source for the activity table and charts.

3. **`GET /health`** — `{"status":"ok"}`, unauthenticated. Cheap liveness ground truth.

### 1.3 Shutdown

`ninfer-serve` handles SIGINT/SIGTERM → `server.stop()` → clean exit (plus a final tail
throughput report). So "Stop" = send SIGINT to the child, wait, mark stopped. No hacky kills.

### 1.4 Environment (verified)

- Ubuntu 26.04 LTS, GNOME Shell 50.1 with the Ubuntu extensions pack — the **AppIndicator /
  KStatusNotifierItem** top-bar area is active, and `libayatana-appindicator3-1` is installed.
  → a real system tray icon in the top bar is available with no extra OS configuration.

---

## 2. Architecture

One local daemon process (`ninfer-view`) does everything; the dashboard is a browser page it
serves, and the tray icon is a thin client of the same process.

```
┌────────────────────────────┐      spawn / SIGINT      ┌──────────────────────────┐
│       ninfer-view          │ ───────────────────────▶  │   ninfer-serve (child)   │
│                            │ ◀───────────────────────  │  stderr ──┐             │
│  supervisor  (child proc)  │                           │  /health ─┼─┐          │
│  console parser (stderr)   │ ◀──────────────────────────┘          │ │          │
│  jsonl tailer (FILE)       │ ◀── tail requests.jsonl ──────────────┘ │          │
│  health poller (2 s)       │ ◀──────────────────────────────────────┘          │
│  state machine             │                                                    │
│  event bus ── REST + SSE ──┼──────────────────────────────┐                     │
└────────────────────────────┘                              ▼                     │
        │ poll /api/state (2 s)                      browser: dashboard (127.0.0.1:18080)
        ▼                                                                   │
 tray icon (top bar) ──── menu → REST actions (start/stop/attach/open) ◀───┘
```

### Components

| Component | Responsibility |
|---|---|
| **supervisor** | Builds argv from a saved profile (always injects `--request-log-jsonl <runs>/<ts>/requests.jsonl` and ensures the run dir exists), spawns the child with stderr piped, watches exit, sends SIGINT on Stop. One instance at a time (v1). |
| **console parser** | Splits the stderr stream into lines, strips the `[ts] [level] ninfer-serve:` prefix into typed events; recognizes lifecycle lines (loading / progress / loaded / KV / warming / listening) and activity lines (`[req N] ...`, `throughput ...`). |
| **jsonl tailer** | Opens the run's JSONL (seek-to-end on attach), follows appends, parses schema-v10 events. New file per supervised run; for attach mode the user points at an existing file. |
| **health poller** | Every 2 s: `GET {host}:{port}/health` → liveness + endpoint for the header card. |
| **state machine** | `stopped → starting → loading → warming → running → stopped/crashed`. Driven by lifecycle lines, exit code, and /health. `crashed` keeps the last stderr tail visible for debugging. |
| **web UI** | Static single page (no build step) + REST + **SSE** (`/api/stream`). SSE (one-way server→client) is all the UI needs; no WebSocket handshake complexity. |
| **tray** | AppIndicator via `pystray`: icon + tooltip updated from `/api/state`, menu with Start / Stop / Open dashboard / Attach… / Quit. Runs in the same process (`--tray`) or standalone (`tray` subcommand). |

### Attach mode (existing instances)

`POST /api/attach {health_url, jsonl_path}` → poll that health endpoint and tail that JSONL;
the same dashboard renders from the same event types. stderr isn't available for a process we
didn't spawn (unless the user tells us where it went), so attach mode's "console" panel is
populated from the JSONL events instead — that's still the full story (requests, throughput,
rejections).

---

## 3. Dashboard UI

Single page, dark theme, no framework, no build step. Roughly:

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│ ● RUNNING   qwen3.8-27b   http://127.0.0.1:8081   up 00:41:03   [Stop] [Attach] │
├─────────────────────────────────────────────────────────────────────────────────┤
│ ┌─ Throughput ────────────────────────────┐ ┌─ Scheduler ────────────────────┐  │
│ │  decode tok/s ▂▃▅▇▇▆▇   (line chart)    │ │ running 2  waiting 0           │  │
│ │  prefill tok/s ▇ (bursty)               │ │ prefilling 0  decode_ready 2   │  │
│ │  last: 43.1 tok/s · acc 2.71/draft 3    │ │ avg_decode_batch 1.98          │  │
│ └─────────────────────────────────────────┘ └────────────────────────────────┘  │
├─────────────────────────────────────────────────────────────────────────────────┤
│ Requests                                                                        │
│ #   proto      state   finish  prompt  gen  cache  ttft   prefill  decode  wall │
│ 47  chat       ● live  —       —       —    —     —      —        —       12.3s │
│ 46  responses  done    stop     1520   861  1498  312ms  9.8k/s   41.7/s  20.6s │
│ 45  messages   done    tool_calls 890  204  0    95ms   12.1k/s  38.0/s   5.4s  │
│ 44  chat       ✗ error 400 context_length_exceeded (21k > 16384)                │
├─────────────────────────────────────────────────────────────────────────────────┤
│ Server (server_start): artifact qwen3_8_27b_nvfp4 · KV auto 245k tok · graphs   │
│   1.2 GiB/2.0 GiB · sampler 1.0/0.95/20 · argv: ninfer-serve … --spec mtp …     │
├─────────────────────────────────────────────────────────────────────────────────┤
│ Console  [filter:____] [level: all ▾] [auto-scroll ✓] [download]                │
│ 23:14:01.220 [info] listening on http://127.0.0.1:8081 (model id: qwen3.8-27b)  │
│ 23:14:09.881 [info] [req 46] done finish=stop … decode=41.7tok/s …              │
└─────────────────────────────────────────────────────────────────────────────────┘
   Config panel (drawer): artifact, host/port, max-context, kv-capacity(auto/N),
   max-concurrency, kv-dtype, spec/draft-tokens/lm-head, vision, api-key, … +
   "advanced: raw flags" box. Saved profiles in ~/.config/ninfer-view/profiles.json
```

Details that make it "nice":

- **Live request rows**: a `request_start` opens a row with a pulsing "live" dot and running
  elapsed time; `request_done`/`error`/`rejected` fills the row (colored finish reason,
  speculative acceptance, reuse path) or turns it red.
- **Charts from raw deltas**: the `throughput` JSONL event keeps unrounded token/round deltas —
  compute tok/s from those (per the docs, stderr strings are rounded and not the aggregation
  source).
- **Startup progress**: while `loading`, show the parsed progress line (phase, %, GiB done/total,
  elapsed) as a progress bar in the header — model loads are the longest phase and currently a
  black box.
- **Server card** from `server_start` (identity, KV ledger, graph bytes, sampler defaults).
- **Console** with level coloring, filter, auto-scroll, and download.
- Backfill: `GET /api/requests` and `GET /api/logs` let a reopened dashboard page repopulate from
  the tail buffer instead of starting blank.

## 4. Top-bar (menu bar) item

Recommendation: **AppIndicator tray icon** (native Ubuntu top bar; the extension + libayatana
are already present).

- **Icon states**: gray = stopped · amber = loading/warming · green = running · red = crashed.
- **Tooltip** (updated ~every 5 s): `qwen3.8-27b · running · 43 tok/s` — a glanceable "what's
  happening" from the panel itself.
- **Menu**: Open dashboard · Start (default profile) · Stop · Attach… (small dialog) · Quit.
- Launch options: `ninfer-view serve --tray` (one process), a `~/.config/autostart/` entry, or a
  `systemd --user` unit (cleaner for the supervisor; recommended).

Alternatives considered:

| Option | Verdict |
|---|---|
| **GNOME Shell extension** (rich popover in the panel itself) | Most "native" but heaviest: JS re-implementation of the dashboard, no fast dev loop (shell reload), process supervision from the shell, extension review/distribution friction. **Stretch goal for v2**, not the core. |
| **Electron / Tauri app** | Overkill for a single-user localhost tool; tray is still an AppIndicator under the hood. |
| **Web app only** | Works, but the "where is it / what's it doing" question is better answered by the panel icon. |

---

## 5. Stack recommendation

**Python 3.12+, core on the standard library** — one process, ~1.5–2k lines of backend, no
build step. ✅ **Decided: Python stdlib core, with the AppIndicator tray in v1** (see §4).

- `http.server.ThreadingHTTPServer` for REST + SSE + static files (localhost scale; one thread
  per SSE client is fine).
- `subprocess` + threads for the child, stderr reader, and JSONL tailer.
- `pystray` (single pip dep, ctypes → `libayatana-appindicator3`) for the tray.
- Frontend: one `index.html` + `app.js` + `style.css`; a small hand-rolled canvas chart
  (~150 LOC) or vendored uPlot — no bundler.

Why: zero-dependency core (resilient on Ubuntu 26's Python 3.13/3.14), SSE avoids WebSocket
handshaking, and the tray path is one well-trodden binding. Known small risk: pystray's lookup
name vs. Ubuntu's `libayatana-appindicator3.so` — fallbacks are a symlink/`LD_LIBRARY_PATH` or a
~50-line direct ctypes call; if the tray proves flaky, it drops out cleanly (core app is unaffected).

Alternative: **Node 26** (you have it): `node:http` + `ws`/SSE + `child_process` — equally viable;
pick whichever you'd rather maintain. The architecture doesn't change.

## 6. API sketch (127.0.0.1 only)

```
GET  /                    dashboard
GET  /api/state           {state, model_id, endpoint, uptime, pid, exit_code, last_throughput}
GET  /api/profiles        list saved launch configs
POST /api/profiles        save a profile
POST /api/serve/start     {profile_id}      → spawn (state: starting)
POST /api/serve/stop      → SIGINT, wait for clean exit
POST /api/attach          {health_url, jsonl_path}
POST /api/detach
GET  /api/stream          SSE: state | console | request | throughput | server_start | tick
GET  /api/requests?limit= backfill of request view-models
GET  /api/logs?limit=&level=  backfill of console lines
```

## 7. Implementation plan

```
ninfer-view/
├── ninfer_view/
│   ├── __main__.py      # CLI: serve [--tray] | tray | attach
│   ├── supervisor.py    # argv builder, spawn, SIGINT stop, exit watch
│   ├── console_parse.py # stderr line/progress/activity parser
│   ├── jsonl_tail.py    # append-follow tailer (seek-to-end on attach)
│   ├── events.py        # schema-v10 → view models
│   ├── state.py         # state machine
│   ├── profiles.py      # ~/.config/ninfer-view/profiles.json
│   ├── httpd.py         # routes, static, SSE
│   └── tray.py          # pystray AppIndicator
├── web/                 # index.html, app.js, style.css (chart)
├── PROPOSAL.md  README.md
└── ninfer-view.service  # optional systemd --user unit
```

| Milestone | Contents | Rough effort |
|---|---|---|
| **M0 — spike** | Minimal stdlib server: spawn `ninfer-serve` (your 8081 preset, fresh JSONL), parse lifecycle lines, push everything via SSE to a bare HTML page with the log console. Proves: startup→listen, progress parsing, SSE in browser, clean SIGINT stop. | 0.5 d |
| **M1 — backend core** | Full parser set (activity + throughput + JSONL v10), state machine, health poller, profiles, REST + SSE API, attach mode. | 1–2 d |
| **M2 — dashboard** | Status header w/ progress bar, throughput + scheduler panels (charts), live requests table, server card, console panel (filter/level/autoscroll/download), config drawer + profiles. | 2–3 d |
| **M3 — tray** | AppIndicator icon states, live tooltip, menu actions; autostart/systemd unit. | 0.5–1 d |
| **M4 — polish** | Crash UX (red state + stderr tail), backfill on reconnect, edge cases (port busy, artifact missing, JSONL open failure), README. | 1 d |

**Total: ~5–7 focused days.**

### Test plan

1. Launch from the UI with the `ninfer serve.txt` preset → watch loading progress → warmup →
   `running`; header matches `/health`.
2. Fire a few requests (curl `/v1/chat/completions`, one streaming, one over-long prompt to force
   `context_length_exceeded`, one thinking request) → verify request rows, rejection row,
   throughput chart, and that `request_done` timings match the JSONL exactly.
3. Stop from tray and from UI → clean exit, state `stopped`; restart → fresh run dir/JSONL.
4. Start `ninfer-serve` manually in a terminal → attach → verify identical view from JSONL alone.
5. Kill -9 the child → `crashed` state with the last stderr lines visible.

### Risks / notes

- JSONL is append-only and shared across campaigns — we always use a fresh per-run file (we
  control it), so no cross-run contamination.
- Request IDs are per-instance monotonic — UI shows `server_instance_id` in the server card.
- The dashboard binds 127.0.0.1 only; no auth (local tool). If `--api-key` is set on the served
  instance, the dashboard never needs it (health + logs are unauthenticated).
- Model load can take minutes for a 27B artifact — that's the longest "dead" phase; the progress
  bar + tray amber state are specifically for it.
- v1 = one supervised instance per daemon. Profiles make switching artifacts/ports instant;
  multiple concurrent instances is a natural v2 extension (supervisor becomes a small dict).