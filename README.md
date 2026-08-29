# ninfer-view

Configure, launch, and watch a `ninfer-serve` instance from one local app:
a dashboard (browser) plus — in a later milestone — a GNOME top-bar tray icon.

See `PROPOSAL.md` for the full design and milestone plan.

## Status: M1 + M2 complete (JSONL log stream, attach, /health, request table, metrics, launch config form)

M0 delivered the vertical slice: profile → spawn → live view → clean stop.
M1 makes the **log stream fed only by the child's
`--request-log-jsonl` file** (schema v10 — the authoritative, unrounded
source per `docs/serving.md`), adds **attach mode** for externally launched
instances, and adds a **`/health` poller** as liveness ground truth.
M2 adds the **request table** and **throughput/scheduler charts** — both
aggregate the *same* records the log pane consumes (backfill + SSE), so the
server stays stateless and there is no second data path.

Dashboard has four tabs: **Log** (formatted JSONL), **Requests** (table),
**Metrics** (two live charts), **Config** (launch command form).

- `GET /` — dashboard (status header, load progress bar, tabbed content,
  health chip, attach form)
- `POST /api/start` / `POST /api/stop` — launch / SIGINT-stop the child
  (serialized by a dedicated action lock)
- `POST /api/attach {host, port, jsonl_path}` / `POST /api/detach` — observe
  an instance started outside ninfer-view: live-only JSONL tail
  (`seek_end`, history is never replayed) + `/health` polling. While
  attached, Start/Attach are disabled and the Stop button becomes **Detach**.
- `GET /api/state` — snapshot (incl. `health: up|down`, `attached`,
  `jsonl_path`); `GET /api/logs` — JSONL backfill
- `GET /api/stream` — SSE: `state` plus the six schema-v10 events
  (`server_start`, `request_start`, `request_rejected`, `request_done`,
  `request_error`, `throughput`); state events publish only on change
- **Requests tab**: pairs `request_start` → `done/rejected/error` by
  `request_id`. Columns: status, protocol, started, prompt/gen tokens,
  finish reason, ttft, prefill/decode tok/s (unrounded, from
  `timings_seconds`), wall, prefix-cache hits, speculative acceptance.
  Click a row to expand the raw start+done records; filter box + a
  done/active/rejected/error summary.
- **Metrics tab**: two plain-canvas charts (no build step, no library),
  redrawn live — *Throughput* (prefill + decode tok/s, computed as
  `tokens / interval_seconds`; each series on its **own y-axis** — prefill
  left, decode right, color-coded — so a prefill spike can't squash the
  decode line) and *Scheduler* (running / prefilling / decode_ready /
  waiting + avg decode batch), plus a current-values readout.
  The x-axis is a **rolling window** (~2 min = 24 intervals, paces from
  the server's `--log-stats-interval-ms`, read from `server_start`) whose
  right edge is anchored to the latest sample and advances at the **log
  input rate**: while data flows it follows the newest entry, and while the
  server is idle it keeps stepping forward — one interval of axis per
  interval of real time, quantized to whole intervals — so the chart scrolls
  at exactly the pace it had while following the input (never faster, never
  frozen). A line is never drawn across an idle gap (a resample starts a
  fresh segment instead of a long diagonal); the idle stretch shows up as a
  blank span of axis. Samples older than the window scroll out.
- **Config tab**: form for the full launch command, built from the
  "Server options" table in `docs/serving.md` — target (binary, artifact,
  host, port, model-id, api-key), sizing (max context, kv capacity
  follow/auto/explicit, kv dtype, concurrency 1..8, pending/timeout,
  prefill chunk, default max tokens), speculative (spec off/mtp/dflash +
  draft-tokens + lm-head draft), behavior (vision, thinking, cuda graph,
  prefix reuse, CORS, device, stats interval), limits (request body, media
  cache/live/threads, response store), and sampling overrides
  (temperature / top-p / top-k / min-p / penalties / seed / greedy).
  Fields show the documented defaults; empty = server default. A live
  *launch command* preview shows the exact argv (including the per-run
  `--request-log-jsonl` ninfer-view injects), with range/consistency
  validation (e.g. concurrency 1..8, draft-tokens 1..5 MTP / 1..15 DFlash,
  dflash×vision warning). Profiles are chosen/created in the tab
  (`GET`/`POST /api/profiles`); flags the form doesn't know are preserved
  verbatim in an *extra flags* box, so round-trips never lose anything.
  **Start** (header or Config tab) persists unsaved form edits first, then
  launches the selected profile, so the launch always matches the preview.
- **JSONL tailer** (`jsonl_tail.py`): append-follower on `requests.jsonl`
  (poll-and-read; the server flushes per event). Malformed / non-ninfer lines
  are skipped and counted; consumer exceptions can't kill it.
- **Health poller** (`health.py`): 2 s interval `GET /health`; supervised
  children are polled from the `listening` line on, attached instances from
  attach time. A raising callback cannot kill the poller.
- The log pane renders one readable line per record, colored by type
  (rejected/error red, request-start blue, throughput dim, server_start
  purple); **hover a line for the full raw record**.
- stderr is still parsed, but *only* to drive the state machine and the
  load-progress bar; stderr lines never enter the log stream or backfill.
- fresh per-run dir under `~/.local/state/ninfer-view/runs/<ts>/` with
  `argv.txt`, `stderr.log`, and the injected `requests.jsonl`

Not yet in (M3+): tray icon.

## Run

```bash
cd /home/kyle/code/ninfer-view
python3 -m ninfer_view            # dashboard on http://127.0.0.1:18080
python3 -m ninfer_view --port 19000   # different port
python3 -m ninfer_view --load         # also load the model (default profile) at startup
```

No dependencies — Python 3.12+ stdlib only.

The default profile launches your known-good instance. Saved profiles live in
`~/.config/ninfer-view/profiles.json` (a `{"profiles": {id: {...}}}` map);
the built-in default is always present:

```
/home/kyle/ninfer/build/apps/ninfer-serve
    /home/kyle/ninfer/models/qwen3_8_27b_nvfp4.ninfer
    --host 127.0.0.1 --port 8081
    --max-context 262144 --max-concurrency 2 --kv-dtype int8
    --spec mtp --draft-tokens 3 --lm-head-draft
```

Custom launch flags go in the profile as `extra_flags` — the **Config** tab
edits them through a form (every flag from the `docs/serving.md` server
options table, grouped, with defaults and ranges shown) and previews the
exact command before Start. The dashboard always injects `--host`, `--port`,
and `--request-log-jsonl` itself.

Environment override: `NINFER_VIEW_HOME=/some/dir` redirects both the config
dir (`$NINFER_VIEW_HOME/profiles.json`) and the run dirs
(`$NINFER_VIEW_HOME/runs/`).

## Testing (manual checklist)

Automated tests (no ninfer needed): `python3 tests/test_console_parse.py`,
`python3 tests/test_jsonl_tail.py`, `python3 tests/test_health.py`, and
`node tests/test_dashboard.js` (runs the dashboard script in a stub DOM and
asserts the request table + charts build from schema-v10 records).

1. **Start the dashboard** (in a terminal):
   ```bash
   cd /home/kyle/code/ninfer-view && python3 -m ninfer_view
   ```
   Open http://127.0.0.1:18080 — header shows `STOPPED`, Start enabled.

2. **Launch**: click **Start**.
   - Header should move `STARTING → LOADING`, the progress bar should fill
     from the model-load lines (phase / % / GiB / elapsed), then `WARMING`,
     then `RUNNING` with the model id, endpoint, and live uptime.
   - The log pane stays quiet until the model is loaded: `server_start`
     (purple) appears once — that's the child's JSONL, not its stderr.
   - Verify `curl http://127.0.0.1:8081/health` → `{"status":"ok"}`.
   - The run dir appeared under `~/.local/state/ninfer-view/runs/` with
     `requests.jsonl` + `stderr.log`.

3. **Generate traffic** (optional): `curl` a `/v1/chat/completions` request
   against 8081 — the log pane shows a blue `START` line when it's admitted
   and a `DONE` line with unrounded `ttft`/`prefill`/`decode`/`wall` and the
   speculative acceptance rate when it completes, plus dim `THRU` lines every
   5 s. A prompt that exceeds `--max-context` produces a red `REJECT` line
   with the HTTP code and message. Hover any line for the raw JSON record.

4. **Requests & Metrics tabs** (M2): with traffic running, the **Requests**
   tab lists each request as a row — status badge (active/done/rejected/
   error), protocol, prompt/gen tokens, finish reason, ttft, prefill &
   decode tok/s, wall, prefix-cache hits, speculative acceptance. Click a
   row to expand its raw start+done JSON. The **Metrics** tab shows two live
   charts: *Throughput* (prefill + decode tok/s on separate left/right
   y-axes) and *Scheduler* (running / prefilling / decode_ready / waiting +
   avg decode batch), plus a current-values readout. Both update in real
   time from the same JSONL.

5. **Stop**: click **Stop** (or from the tray in M3). Header goes to
   `STOPPED (exit 0)`; the child exited cleanly via SIGINT and the JSONL
   tailer thread shuts down with it.

6. **Crash UX**: launch a profile whose binary fails (e.g. a profile whose
   `binary` is a script that exits non-zero) — header shows `CRASHED` with
   the exit code; **Start** is enabled again. (stderr is still captured to
   `<run dir>/stderr.log` for diagnosis.)

7. **Attach mode**: launch an instance *outside* ninfer-view (with
   `--request-log-jsonl /tmp/your.jsonl`), then click **Attach** in the
   dashboard and point it at host/port + that file. Header shows `ATTACHED`
   with the endpoint and a live `health: up` chip; the log pane shows *new*
   events only (the file's history is never replayed). Kill the external
   process — the chip flips to `health: down`. Click **Detach** to release
   the slot. `tests/fake_external_instance.py` is a ready-made external
   instance for this test (health endpoint + JSONL writer, ~30 s).

### Test without loading the real model (~30 s instead of minutes)

A fake binary that mimics the real stderr lifecycle **and emits realistic
schema-v10 JSONL** (server_start, request_start, request_done,
request_rejected, throughput — it reads `--request-log-jsonl` from its own
argv) lives in the repo at `tests/fake_ninfer_serve.py`. In a browser console
on the dashboard:

```js
fetch("/api/profiles", {method: "POST", headers: {"Content-Type": "application/json"},
  body: JSON.stringify({profile: {id: "fake", name: "fake",
    binary: "/home/kyle/code/ninfer-view/tests/fake_ninfer_serve.py",
    artifact: "/dev/null", host: "127.0.0.1", port: 8099}})})
```

Then start it from the dashboard: open the **Config** tab, pick the `fake`
profile from the dropdown, and click **Start** (or
`curl -X POST http://127.0.0.1:18080/api/start -d '{"profile_id":"fake"}'`).
You get the full lifecycle — progress bar, the purple `server_start` line,
blue/red/dim request lines, `THRU` every 5 s — in about 8 seconds, and a
clean SIGINT stop.

## Layout

```
ninfer_view/
├── __main__.py       CLI entry: python3 -m ninfer_view
├── console_parse.py  stderr line → typed event (drives state machine only)
├── jsonl_tail.py     append-follower for requests.jsonl (schema v10)
├── health.py         /health poller (liveness ground truth)
├── state.py          EventBus (SSE fan-out + JSONL ring buffer) + state machine
│                     (supervised + attached)
├── supervisor.py     spawn child, tee stderr, SIGINT stop, exit watch
├── profiles.py       ~/.config/ninfer-view/profiles.json (+ NINFER_VIEW_HOME)
└── httpd.py          ThreadingHTTPServer: REST + SSE + static dashboard
web/
└── index.html        single-file dashboard (no build step): JSONL formatters,
                      attach form, health chip, request table + canvas charts,
                      launch config form (flags per docs/serving.md)
tests/
├── fake_ninfer_serve.py      fake binary: real stderr + realistic JSONL
├── fake_external_instance.py fake external instance (health + JSONL writer)
├── test_console_parse.py     stderr parser tests
├── test_jsonl_tail.py        tailer tests (live append, partial lines,
│                             seek_end, crashes)
├── test_health.py            health poller tests
└── test_dashboard.js         dashboard script in a stub DOM (table + charts
                              + config-form round-trip and Start flow)
```

## Notes

- Dashboard binds 127.0.0.1 only; no auth (local tool). The served instance's
  `--api-key` (if any) is never needed by the dashboard: `/health` and the
  logs are unauthenticated.
- **Stopping during model load**: `ninfer-serve` installs its SIGINT handler
  only after load + warmup (see `apps/serve/main.cpp`), so a Stop in the
  `LOADING` phase terminates the child by the default signal action
  (reported exit code −2, not 0). The supervisor still records this as a
  clean stop; by the `RUNNING` phase SIGINT always yields a graceful exit 0.
- **Start/stop are serialized** by a dedicated action lock
  (`Service.action_lock`) held across the whole action — two fast
  `POST /api/start` calls (UI + tray) cannot both pass the state check and
  spawn two children.
- One supervised instance per daemon (v1). Profiles make switching
  artifact/port instant; multi-instance is a v2 extension.
- **Attach mode observes, it never controls.** The attached instance is not
  spawned by us, so Stop becomes Detach (no SIGINT); health `down` only means
  `/health` stopped answering — the process may still be alive mid-restart.
- **The log stream is JSONL-only.** The JSONL is the authoritative source
  (unrounded timings, full counters); the rounded stderr summaries are kept
  only for the state machine, the progress bar, and the `stderr.log` artifact
  in each run dir.
- **M2 (table + charts) is computed in the browser** from the very same
  records the log pane renders — there is no second server-side aggregate and
  no extra endpoint. Throughput rates are `tokens / interval_seconds` (the
  per-interval counters, not cumulative). Because it runs client-side, the
  table/charts only cover records the browser has seen (the 4000-event
  backfill ring + the live stream); refreshing the page re-backfills from
  `/api/logs`.
- `server_start` arrives only after the model is loaded (the child writes it
  at Engine attach), so the log pane is intentionally quiet while loading.