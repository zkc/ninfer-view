"""Parse ninfer-serve stderr console output into structured events.

Three line formats, depending on the ninfer-serve version:

Legacy (pre-structured-logging builds):

    [YYYY-MM-DD HH:MM:SS.mmm] [info|warning|error] ninfer-serve: <message>

    with lifecycle messages ("loading model...", "warming up...",
    "model loaded in N s", "listening on ..."), LoadProgressRenderer
    load-progress lines, and "[req N] ..." activity lines.

Structured startup logs (spdlog build):

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

Pretty startup logs (latest build, product logging):

    YYYY-MM-DD HH:MM:SS.mmm  LEVEL  <message>

    LEVEL is a fixed 5-char token (TRACE|DEBUG|ERROR|FATAL|INFO␣|WARN␣);
    the message is a human presentation of "| "-separated clauses, e.g.
    "loading weights | 19.73 GiB", "weights ready | 19.73 GiB | 9.5s",
    "engine ready | qwen3.8-27b | total 10.5s | ...",
    "listening on http://127.0.0.1:8081 | model ... | auth disabled",
    "throughput | 5.0s | ...", "req#13 done | ...". Upstream the pretty
    view is "intentionally not parsed"; we classify only the stable
    clauses that drive the state machine and progress bar, and treat
    the /health endpoint (not stderr) as the ground truth for readiness.

All three are classified into the same event kinds (progress | lifecycle
| activity | console) so the state machine and the dashboard are
format-agnostic. In the structured format there are no intermediate
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


# --- pretty format (latest product-logging build) ---------------------------
#
#   2026-09-02 23:12:56.607  INFO  weights ready | 19.73 GiB | 9.5s
#
# timestamp + two spaces + 5-char level token (INFO/WARN carry a trailing
# pad space) + one space + the message. The phase names below are the
# PhasePresentation active/complete strings from startup_log.cpp.

PRETTY_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})  "
    r"(TRACE|DEBUG|ERROR|FATAL|INFO |WARN ) (.*)$"
)
PRETTY_LEVELS = {
    "TRACE": "debug", "DEBUG": "debug", "INFO ": "info",
    "WARN ": "warning", "ERROR": "error", "FATAL": "critical",
}

# Byte phases whose begin/complete lines carry a pretty byte total.
PRETTY_PROGRESS_START = ("loading weights", "pinning host state",
                         "pinning host KV")
PRETTY_PROGRESS_DONE = ("weights ready", "host state pinned", "host KV pinned")

# Rate-limited persistent progress record (redirected stderr):
# "  loading weights 43.2% | 8.52 GiB/19.73 GiB | 1.31 GiB/s | ETA 9.1s"
WEIGHTS_PROGRESS_RE = re.compile(
    r"^\s*loading weights (\d+\.\d+)% \| (\S+ \S+)/(\S+ \S+)")
ENGINE_READY_RE = re.compile(r"^engine ready \| ([^|]+) \| total ([^|]+)")
LISTENING_PRETTY_RE = re.compile(
    r"^listening on (http://\S+) \| model ([^|]+?) \| auth (\S+)$")


def _pretty_duration_s(text: str) -> float | None:
    """Parse a pretty duration: "532 us" | "150 ms" | "9.5s" |
    "5m 21.0s" | "1h 2m" (format_pretty_duration)."""
    t = text.strip()
    m = re.fullmatch(r"(\d+) us", t)
    if m:
        return int(m.group(1)) / 1e6
    m = re.fullmatch(r"([\d.]+) ms", t)
    if m:
        return float(m.group(1)) / 1e3
    m = re.fullmatch(r"([\d.]+)s", t)
    if m:
        return float(m.group(1))
    m = re.fullmatch(r"(\d+)m ([\d.]+)s", t)
    if m:
        return int(m.group(1)) * 60 + float(m.group(2))
    m = re.fullmatch(r"(\d+)h (\d+)m", t)
    if m:
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60
    return None


def _progress_str(phase: str, percent: float, done: str, total: str,
                  elapsed_s: float) -> dict:
    """Progress dict from already-human-readable pretty byte strings."""
    return {"phase": phase, "percent": percent, "done": done,
            "total": total, "elapsed_s": elapsed_s}


def _parse_pretty(msg: str, ev: dict) -> dict | None:
    """Classify a pretty-format message; None if it is a plain line."""
    s = msg.strip()
    if s == "starting engine":
        ev.update(kind="lifecycle", phase="loading")
        return ev

    wm = WEIGHTS_PROGRESS_RE.match(msg)
    if wm:
        ev["kind"] = "progress"
        ev["progress"] = _progress_str("loading weights", float(wm.group(1)),
                                       wm.group(2), wm.group(3), None)
        return ev

    for name in PRETTY_PROGRESS_START:
        if s.startswith(name + " | "):
            ev["kind"] = "progress"
            ev["progress"] = _progress_str(name, 0.0, "0 B",
                                           s.split(" | ", 1)[1], 0.0)
            return ev
    for name in PRETTY_PROGRESS_DONE:
        if s.startswith(name + " | "):
            parts = s.split(" | ")
            secs = _pretty_duration_s(parts[2]) if len(parts) > 2 else None
            ev["kind"] = "progress"
            ev["progress"] = _progress_str(name, 100.0, parts[1], parts[1],
                                           secs if secs is not None else 0.0)
            return ev

    em = ENGINE_READY_RE.match(s)
    if em:
        ev.update(kind="lifecycle", phase="loaded",
                  model_id=em.group(1).strip(),
                  load_seconds=_pretty_duration_s(em.group(2)))
        return ev

    if s.startswith("capacity | "):
        ev.update(kind="lifecycle", phase="kv")
        return ev

    if s == "warming up" or s.startswith("warmup complete"):
        ev.update(kind="lifecycle", phase="warming")
        return ev

    lm = LISTENING_PRETTY_RE.match(s)
    if lm:
        ev.update(kind="lifecycle", phase="listening", endpoint=lm.group(1),
                  model_id=lm.group(2).strip(), auth=lm.group(3))
        return ev

    if s.startswith("throughput | ") or s.startswith("req#"):
        ev["kind"] = "activity"
        return ev

    if s.startswith("startup failed") or s.startswith("warmup failed") \
            or s.startswith("server failed during") \
            or s.startswith("cannot bind") \
            or s.startswith("server listen failed"):
        ev.update(kind="lifecycle", phase="failed", detail=s)
        return ev

    return None  # context cache / media / memory ledger etc. stay plain console


def parse_line(raw: str) -> dict:
    """Parse one stderr line into an event dict.

    Always returns a dict with keys: kind, ts, level, message, raw.
    kind is one of: progress | lifecycle | activity | console.
    """
    line = raw.rstrip("\r\n")
    m = LINE_RE.match(line)
    if not m:
        pm = PRETTY_RE.match(line)
        if pm:
            ev = {"kind": "console", "ts": pm.group(1),
                  "level": PRETTY_LEVELS[pm.group(2)],
                  "message": pm.group(3), "raw": line}
            cur = _parse_pretty(pm.group(3), ev)
            return cur if cur is not None else ev
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
