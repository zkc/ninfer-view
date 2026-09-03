"""Parse ninfer-serve stderr console output into structured events.

Two line formats, depending on the ninfer-serve version:

Legacy (pre-structured-logging builds):

    [YYYY-MM-DD HH:MM:SS.mmm] [info|warning|error] ninfer-serve: <message>

    with lifecycle messages ("loading model...", "warming up...",
    "model loaded in N s", "listening on ..."), LoadProgressRenderer
    load-progress lines, and "[req N] ..." activity lines.

Current (structured startup logs):

    [YYYY-MM-DD HH:MM:SS.mmm] [info|warning|error|critical] [ninfer-serve] <message>

    with key=value messages:
        startup phase=<p> status=begin|complete|failed [total_bytes=...]
        startup phase=<p> status=complete completed_bytes=... total_bytes=...
            duration_ms=...
        engine status=ready ... target_load_ms=...
        engine capacity ...
        server status=ready host="..." port=... model_id="..."
            auth_enabled=...
        server status=failed ... detail="..."
        request id=N status=submitted|done|...   (activity)
        throughput ...                            (activity)

Both are classified into the same event kinds (progress | lifecycle |
activity | console) so the state machine and the dashboard are
format-agnostic. In the current format there are no intermediate
progress lines: byte-count phases emit 0% on ``status=begin`` and 100%
on ``status=complete``.
"""

from __future__ import annotations

import re

LINE_RE = re.compile(
    r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})\] "
    r"\[(info|warning|error|critical)\] "
    r"(?:\[ninfer-serve\] ?|ninfer-serve: ?)(.*)$"
)

# --- current (structured) format -------------------------------------------

# key=value pairs; values may be double-quoted (detail="... with spaces ...").
PAIR_RE = re.compile(r'(\w+)=("[^"]*"|\S+)')


def _kv(msg: str) -> dict:
    out: dict = {}
    for m in PAIR_RE.finditer(msg):
        v = m.group(2)
        if v.startswith('"') and v.endswith('"') and len(v) > 1:
            v = v[1:-1]
        out[m.group(1)] = v
    return out


def human_bytes(n: float) -> str:
    """1234567 -> '1.18 MiB' (matches the legacy progress-line units)."""
    n = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024.0 or unit == "TiB":
            return f"{n:.0f} B" if unit == "B" else f"{n:.2f} {unit}"
        n /= 1024.0
    raise AssertionError("unreachable")


def _progress(phase: str, percent: float, done_b: int, total_b: int,
              elapsed_s: float) -> dict:
    return {
        "phase": phase,
        "percent": percent,
        "done": human_bytes(done_b),
        "total": human_bytes(total_b),
        "elapsed_s": elapsed_s,
    }


def _parse_current(msg: str, ev: dict) -> dict | None:
    """Classify a current-format message; None if it is a plain line."""
    if msg.startswith("startup "):
        kv = _kv(msg)
        phase, status = kv.get("phase"), kv.get("status")
        if status == "begin":
            if phase == "serve-warmup":
                ev.update(kind="lifecycle", phase="warming")
                return ev
            if phase and "total_bytes" in kv:
                ev["kind"] = "progress"
                ev["progress"] = _progress(
                    phase, 0.0, 0, int(kv["total_bytes"]), 0.0)
                return ev
            if phase:
                ev.update(kind="lifecycle", phase="loading")
            return ev
        if (status == "complete" and "completed_bytes" in kv
                and "total_bytes" in kv):
            secs = (float(kv["duration_ms"]) / 1000.0
                    if kv.get("duration_ms") else 0.0)
            ev["kind"] = "progress"
            ev["progress"] = _progress(
                phase or "unknown", 100.0, int(kv["completed_bytes"]),
                int(kv["total_bytes"]), secs)
            return ev
        if status == "failed":
            ev.update(kind="lifecycle", phase="failed",
                      detail=kv.get("detail")
                      or f"startup phase {phase} failed")
            return ev
        return ev

    if msg.startswith("engine status=ready"):
        kv = _kv(msg)
        ev.update(kind="lifecycle", phase="loaded",
                  load_seconds=(float(kv["target_load_ms"]) / 1000.0
                                if kv.get("target_load_ms") else None))
        return ev

    if msg.startswith("server status=ready"):
        kv = _kv(msg)
        host, port = kv.get("host"), kv.get("port")
        ev.update(kind="lifecycle", phase="listening",
                  endpoint=(f"http://{host}:{port}"
                            if host and port else None),
                  model_id=kv.get("model_id"),
                  auth=("enabled" if kv.get("auth_enabled") == "true"
                        else "disabled"))
        return ev

    if msg.startswith("engine capacity"):
        ev.update(kind="lifecycle", phase="kv")
        return ev

    if msg.startswith("server status=failed"):
        kv = _kv(msg)
        ev.update(kind="lifecycle", phase="failed",
                  detail=kv.get("detail") or "server failed to start")
        return ev

    if msg.startswith("request id=") or msg.startswith("throughput "):
        ev["kind"] = "activity"
        return ev

    return None  # engine context_cache/context_cost etc. stay plain console


# --- legacy format -----------------------------------------------------------

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

    cur = _parse_current(msg, ev)
    if cur is not None:
        return cur

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
