"""Parse ninfer-serve stderr console output into structured events.

Every console line is exactly:

    [YYYY-MM-DD HH:MM:SS.mmm] [info|warning|error] ninfer-serve: <message>

Lifecycle messages (apps/serve/main.cpp) are classified; load-progress lines
(LoadProgressRenderer Log mode, emitted ~every 10 s when stderr is piped) are
parsed into structured data; activity lines (src/serve/request_log.cpp) are
tagged so the UI can style them. Everything else passes through as a plain
console event.
"""

from __future__ import annotations

import re

LINE_RE = re.compile(
    r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})\] "
    r"\[(info|warning|error)\] ninfer-serve: ?(.*)$"
)

# "load<phase 26w><pct 8w><done 14w> / <total 14w><secs 12w>"
# pct is "NN.NN%" (or "n/a" for unknown totals); done/total are "<num> <unit>"
# (e.g. "5.20 GiB"); seconds are "NNN.NNN s".
LOAD_RE = re.compile(
    r"^load\s+(\S+)\s+(n/a|\d{1,3}\.\d{2}%)\s+(\S+\s\S+)\s*/\s*(\S+\s\S+)\s+([\d.]+)\s+s\s*$"
)
LISTENING_RE = re.compile(
    r"^listening on (http://\S+) \(model id: ([^,]+), auth: (\S+)\)"
)
MODEL_LOADED_RE = re.compile(r"^model loaded in ([\d.]+) s$")
KV_RE = re.compile(r"^KV capacity (.*)$")
LOADING_MSG = "loading model..."
WARMING_MSG = "warming up..."


def parse_line(raw: str) -> dict:
    """Parse one stderr line into an event dict.

    Always returns a dict with keys: kind, ts, level, message, raw.
    kind is one of: progress | lifecycle | activity | console.
    """
    line = raw.rstrip("\r\n")
    m = LINE_RE.match(line)
    if not m:
        return {"kind": "console", "ts": None, "level": "info",
                "message": line, "raw": line}

    ts, level, msg = m.group(1), m.group(2), m.group(3)
    ev: dict = {"kind": "console", "ts": ts, "level": level,
                "message": msg, "raw": line}

    lm = LOAD_RE.match(msg)
    if lm:
        phase, pct, done, total, secs = lm.groups()
        ev["kind"] = "progress"
        ev["progress"] = {
            "phase": phase,
            "percent": None if pct == "n/a" else float(pct[:-1]),
            "done": done,
            "total": total,
            "elapsed_s": float(secs),
        }
        return ev

    if msg == LOADING_MSG:
        ev.update(kind="lifecycle", phase="loading")
        return ev
    if msg == WARMING_MSG:
        ev.update(kind="lifecycle", phase="warming")
        return ev

    mld = MODEL_LOADED_RE.match(msg)
    if mld:
        ev.update(kind="lifecycle", phase="loaded", load_seconds=float(mld.group(1)))
        return ev

    if KV_RE.match(msg):
        ev.update(kind="lifecycle", phase="kv")
        return ev

    ls = LISTENING_RE.match(msg)
    if ls:
        ev.update(kind="lifecycle", phase="listening",
                  endpoint=ls.group(1), model_id=ls.group(2).strip(),
                  auth=ls.group(3))
        return ev

    if msg.startswith("[req ") or msg.startswith("throughput "):
        ev["kind"] = "activity"
        return ev

    return ev