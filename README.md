# ninfer-view

Configure, launch, and watch a `ninfer-serve` instance from one local dashboard: spawn the
server or attach to one already running, and view its live logs, requests, and metrics.

Python 3.12+ stdlib only — no dependencies.

See `PROPOSAL.md` for the full design, milestone plan, status, and implementation notes.

## Features

- **Launch & control** — start/stop the server from a saved profile, with a live
  load-progress bar and status header.
- **Attach mode** — observe an instance started outside ninfer-view (live JSONL tail +
  `/health` polling).
- **Dashboard tabs**:
  - **Log** — the child's `--request-log-jsonl` stream (schema v10), one readable line per
    record; hover a line for the full raw record.
  - **Requests** — a per-request table (status, protocol, tokens, ttft, prefill/decode tok/s,
    speculative acceptance, ...); click a row to expand its raw start+done JSON.
  - **Metrics** — live throughput and scheduler charts, computed in the browser from the same
    records the log pane renders.
  - **Config** — a full launch-command form (every flag from `docs/serving.md`, grouped, with
    documented defaults), plus a live argv preview of the exact command that will run.
- **Profiles** — saved launch configs in `~/.config/ninfer-view/profiles.json`; switching
  artifact/port is instant.

## Run

```bash
python3 -m ninfer_view                 # dashboard on http://127.0.0.1:18080
python3 -m ninfer_view --port 19000    # different port
python3 -m ninfer_view --load          # also load the model (default profile) at startup
```

The dashboard always injects `--host`, `--port`, and `--request-log-jsonl` itself. The
default profile uses launch settings shown here:

```
.../ninfer-serve
    .../ninfer/models/qwen3_8_27b_nvfp4.ninfer
    --host 127.0.0.1 --port 8081
    --max-context 262144 --max-concurrency 2 --kv-dtype int8
    --spec mtp --draft-tokens 3 --lm-head-draft
```

Custom launch flags are stored on a profile as `extra_flags`; the **Config** tab edits them
through the form and previews the exact command before Start. Each run gets a fresh dir under
`~/.local/state/ninfer-view/runs/<ts>/` containing `argv.txt`, `stderr.log`, and the injected
`requests.jsonl`.

Set `NINFER_VIEW_HOME=/some/dir` to redirect both the config dir
(`$NINFER_VIEW_HOME/profiles.json`) and the run dirs (`$NINFER_VIEW_HOME/runs/`).
Set `NINFER_SERVE_BINARY=/abs/path/to/ninfer-serve` to give the built-in default
profile a binary, and `NINFER_SERVE_ARTIFACT=/abs/path/to/model.ninfer` to give it an
artifact; without either, the default profile seeds with that field empty, so fill it
in from the Config tab (the form pre-fills the binary field from this variable).

## API (127.0.0.1 only)

```
GET  /                  dashboard (status header, progress bar, tabbed content, health chip)
GET  /api/state         snapshot: state, health up|down, attached, jsonl_path, ...
GET  /api/logs          JSONL backfill
GET  /api/stream        SSE: state + the six schema-v10 events
POST /api/start         launch the selected profile
POST /api/stop          SIGINT-stop the child
POST /api/attach        {host, port, jsonl_path} — observe an external instance
POST /api/detach        release the attach slot
GET  /api/profiles      list saved launch configs
POST /api/profiles      save a launch config
```

`/api/stream` emits the schema-v10 events `server_start`, `request_start`, `request_rejected`,
`request_done`, `request_error`, and `throughput`; state events publish only on change. While
attached, Start/Attach are disabled and Stop becomes **Detach**.

## Testing

Automated tests (no ninfer needed):

```bash
python3 tests/test_console_parse.py
python3 tests/test_jsonl_tail.py
python3 tests/test_health.py
node tests/test_dashboard.js
```

A fake binary (`tests/fake_ninfer_serve.py`) mimics the real stderr lifecycle **and** emits
realistic schema-v10 JSONL, so you can exercise the full lifecycle — progress bar,
`server_start`, request lines, `THRU` every 5 s, clean SIGINT stop — in about 8 seconds instead
of waiting on a real model load. Create a profile for it and start it from the **Config** tab:

```js
fetch("/api/profiles", {method: "POST", headers: {"Content-Type": "application/json"},
  body: JSON.stringify({profile: {id: "fake", name: "fake",
    binary: "<repo>/tests/fake_ninfer_serve.py",
    artifact: "/dev/null", host: "127.0.0.1", port: 8099}})})
```

`tests/fake_external_instance.py` is a ready-made external instance (health endpoint + JSONL
writer) for the attach-mode test.

## Layout

```
ninfer_view/
├── __main__.py       CLI entry: python3 -m ninfer_view
├── console_parse.py  stderr line → typed event (drives the state machine only)
├── jsonl_tail.py     append-follower for requests.jsonl (schema v10)
├── health.py         /health poller (liveness ground truth)
├── state.py          EventBus (SSE fan-out + JSONL ring buffer) + state machine
├── supervisor.py     spawn child, tee stderr, SIGINT stop, exit watch
├── profiles.py       ~/.config/ninfer-view/profiles.json
│                     (+ NINFER_VIEW_HOME, NINFER_SERVE_BINARY, NINFER_SERVE_ARTIFACT)
└── httpd.py          ThreadingHTTPServer: REST + SSE + static dashboard
web/
├── index.html        dashboard markup: status header, attach form, progress bar, tab panes;
│                     loads the js/ scripts in document order
├── style.css         dashboard styling
└── js/               classic scripts, no build step, no ES modules — shared global scope,
                      loaded util -> log -> requests -> charts -> config -> main
    ├── util.js       shared utilities, formatters, client-side model state
    ├── log.js        log pane: record formatters (schema v10), live log view
    ├── requests.js   request table: rows, "waiting" attribution, filtering, summary line
    ├── charts.js     metrics: rolling-window canvas line charts over the throughput series
    ├── config.js     launch command form: profile fields + extra_flags, argv preview
    └── main.js       state header, SSE stream, header controls, tab switching, boot
tests/
├── fake_ninfer_serve.py      fake binary: real stderr + realistic JSONL
├── fake_external_instance.py fake external instance (health + JSONL writer)
├── test_console_parse.py     stderr parser tests
├── test_jsonl_tail.py        tailer tests (live append, partial lines, seek_end, crashes)
├── test_health.py            health poller tests
└── test_dashboard.js         dashboard script in a stub DOM (table + charts + config form)
```
