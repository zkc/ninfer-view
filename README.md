# ninfer-view

Configure, launch, and watch a `ninfer-serve` instance from one local app:
a dashboard (browser) plus — in a later milestone — a GNOME top-bar tray icon.

See `PROPOSAL.md` for the full design and milestone plan.

## Status: M0 (spike) complete

M0 delivers the vertical slice: profile → spawn → live log view → clean stop.

- `GET /` — dashboard (status header, load progress bar, live console)
- `POST /api/start` / `POST /api/stop` — launch / SIGINT-stop the child
- `GET /api/state`, `GET /api/logs` — snapshot + console backfill
- `GET /api/stream` — SSE push of state changes and every console event
- stderr parser understands the lifecycle lines, load-progress lines, and tags
  request/throughput activity lines (verified against the exact formats in
  `apps/serve/main.cpp`, `src/serve/request_log.cpp`, `src/product/load_progress/`)
- fresh per-run dir under `~/.local/state/ninfer-view/runs/<ts>/` with
  `argv.txt`, `stderr.log`, and the injected `--request-log-jsonl` file

Not yet in (M1+): JSONL request table & charts, attach mode for external
instances, `/health` poller, tray icon, config drawer in the UI.

## Run

```bash
cd /home/kyle/code/ninfer-view
python3 -m ninfer_view            # dashboard on http://127.0.0.1:18080
python3 -m ninfer_view --port 19000   # different port
```

No dependencies — Python 3.12+ stdlib only.

The default profile launches your known-good instance
(`~/.config/ninfer-view/profile.json` is consulted first if present — a JSON
object merged over the built-in defaults):

```
/home/kyle/ninfer/build/apps/ninfer-serve
    /home/kyle/ninfer/models/qwen3_8_27b_nvfp4.ninfer
    --host 127.0.0.1 --port 8081
    --max-context 262144 --max-concurrency 2 --kv-dtype int8
    --spec mtp --draft-tokens 3 --lm-head-draft
```

Custom launch flags go in the profile as `extra_flags`; the dashboard always
injects `--host`, `--port`, and `--request-log-jsonl` itself.

Environment override: `NINFER_VIEW_HOME=/some/dir` redirects both the config
dir (`$NINFER_VIEW_HOME/profiles.json`) and the run dirs
(`$NINFER_VIEW_HOME/runs/`).

## Testing M0 (manual checklist)

1. **Start the dashboard** (in a terminal):
   ```bash
   cd /home/kyle/code/ninfer-view && python3 -m ninfer_view
   ```
   Open http://127.0.0.1:18080 — header shows `STOPPED`, Start enabled.

2. **Launch**: click **Start**.
   - Header should move `STARTING → LOADING`, the progress bar should fill
     from the model-load lines (phase / % / GiB / elapsed), then `WARMING`,
     then `RUNNING` with the model id, endpoint, and live uptime.
   - The console pane streams every stderr line live.
   - Verify `curl http://127.0.0.1:8081/health` → `{"status":"ok"}`.
   - The run dir appeared under `~/.local/state/ninfer-view/runs/` with
     `requests.jsonl` + `stderr.log`.

3. **Generate traffic** (optional): `curl` a `/v1/chat/completions` request
   against 8081 — the `[req N] … → submitted` and `done …` lines should show
   up live in the console (blue-tinted activity lines), as should the
   `throughput …` lines every 5 s.

4. **Stop**: click **Stop** (or from the tray in M3). Header goes
   `STOPPING → STOPPED (exit 0)`; the child exited cleanly via SIGINT.

5. **Crash UX**: launch a profile whose binary fails (e.g. stop any existing
   8081 instance first, then change the port in the profile to a taken port) —
   header shows `CRASHED` with the exit code; the last stderr lines (including
   `failed to bind …`) remain visible; **Start** is enabled again.

### Test without loading the real model (~30 s instead of minutes)

A fake binary that mimics the exact stderr lifecycle lives in the repo at
`tests/fake_ninfer_serve.py`. In a browser console on the dashboard:

```js
fetch("/api/profiles", {method: "POST", headers: {"Content-Type": "application/json"},
  body: JSON.stringify({profile: {id: "fake", name: "fake",
    binary: "/home/kyle/code/ninfer-view/tests/fake_ninfer_serve.py",
    artifact: "/dev/null", host: "127.0.0.1", port: 8099}})})
```

Then start with that profile (the UI currently starts `default`; for M0 use
`curl -X POST http://127.0.0.1:18080/api/start -d '{"profile_id":"fake"}'`).
You get the full lifecycle — progress bar, warming, running, activity lines —
in about 8 seconds, and a clean SIGINT stop.

## Layout

```
ninfer_view/
├── __main__.py       CLI entry: python3 -m ninfer_view
├── console_parse.py  stderr line → typed event (lifecycle/progress/activity)
├── state.py          EventBus (SSE fan-out + ring buffer) + state machine
├── supervisor.py     spawn child, tee stderr, SIGINT stop, exit watch
├── profiles.py       ~/.config/ninfer-view/profiles.json (+ NINFER_VIEW_HOME)
└── httpd.py          ThreadingHTTPServer: REST + SSE + static dashboard
web/
└── index.html        single-file dashboard (no build step)
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
- The JSONL written by the child is the authoritative source for the M2
  request table/charts; M0 consumes it only by giving it a fresh file.