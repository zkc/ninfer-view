"""Unit tests for the stderr console parser (no ninfer involved).

Run:  python3 tests/test_console_parse.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ninfer_view.console_parse import parse_line

PREFIX = "[2026-08-24 21:00:00.000] [info] ninfer-serve: "


def cxx_progress(phase, pct, done, total, secs):
    # Mirrors LoadProgressRenderer::format_line (Log mode):
    # setw(12) left "load", setw(26) left phase, setw(8) right pct,
    # setw(14) right done, " / ", setw(14) right total, setw(12) right "N.NNN s"
    secs_s = f"{secs:.3f} s"
    return PREFIX + ("load".ljust(12) + phase.ljust(26) + pct.rjust(8)
                     + done.rjust(14) + " / " + total.rjust(14)
                     + secs_s.rjust(12))


CASES = [
    ("lifecycle/loading",
     PREFIX + "loading model...",
     lambda e: e["kind"] == "lifecycle" and e["phase"] == "loading"),
    ("progress w/ pct",
     cxx_progress("load_weights", "42.13%", "5.20 GiB", "12.40 GiB", 45.321),
     lambda e: e["kind"] == "progress" and e["progress"]["percent"] == 42.13
               and e["progress"]["phase"] == "load_weights"
               and e["progress"]["done"] == "5.20 GiB"
               and e["progress"]["total"] == "12.40 GiB"
               and e["progress"]["elapsed_s"] == 45.321),
    ("progress 100%",
     cxx_progress("load_weights", "100.00%", "12.40 GiB", "12.40 GiB", 90.0),
     lambda e: e["kind"] == "progress" and e["progress"]["percent"] == 100.0),
    ("progress n/a",
     cxx_progress("warmup", "n/a", "1.00 GiB", "1.00 GiB", 3.0),
     lambda e: e["kind"] == "progress" and e["progress"]["percent"] is None),
    ("model loaded",
     PREFIX + "model loaded in 123.456 s",
     lambda e: e["kind"] == "lifecycle" and e["phase"] == "loaded"
               and e["load_seconds"] == 123.456),
    ("kv line",
     PREFIX + "KV capacity auto resolved=245000 tokens pages=3828/4096"
               " runtime=1.00 GiB free-after-weights=3.00 GiB"
               " free-after-startup=2.00 GiB headroom=1.00 GiB slack=0.50 GiB"
               " graphs=1.20 GiB/2.00 GiB",
     lambda e: e["kind"] == "lifecycle" and e["phase"] == "kv"),
    ("warming",
     PREFIX + "warming up...",
     lambda e: e["kind"] == "lifecycle" and e["phase"] == "warming"),
    ("listening",
     PREFIX + "listening on http://127.0.0.1:8081"
               " (model id: qwen3.8-27b, auth: disabled)",
     lambda e: e["kind"] == "lifecycle" and e["phase"] == "listening"
               and e["model_id"] == "qwen3.8-27b"
               and e["endpoint"] == "http://127.0.0.1:8081"),
    ("req start",
     PREFIX + "[req 1] openai_chat stream msgs=3 max_tokens=8192"
               " (server default) tools=0 tool_choice=auto tool_history=no"
               " thinking=on preserve_thinking=off preserve_change=no"
               " sampler=[t=1.0 p=0.95 k=20 minp=0.0 pres=0.0 freq=0.0]"
               " \u2192 submitted",
     lambda e: e["kind"] == "activity"),
    ("req done",
     PREFIX + "[req 1] done finish=stop_token prompt=1520 gen=861 cache=1498"
               " reuse=append_frontier ttft=312ms prefill=9820.35tok/s"
               " decode=41.73tok/s wall=20.65s"
               " speculative=mtp 2.71tok/round (90.2%)",
     lambda e: e["kind"] == "activity"),
    ("req rejected",
     PREFIX + "[req 2] rejected phase=prepare protocol=openai_chat stream"
               " msgs=1 media=0 tools=0 status=400"
               " code=context_length_exceeded message=prompt too long",
     lambda e: e["kind"] == "activity"),
    ("throughput",
     PREFIX + "throughput interval=5.000s prefill=120.0tok/s decode=43.1tok/s"
               " running=2 prefilling=0 decode_ready=2 waiting=0"
               " avg_decode_batch=1.98",
     lambda e: e["kind"] == "activity"),
    ("error line",
     "[2026-08-24 21:02:40.000] [error] ninfer-serve: failed to bind"
     " 127.0.0.1:8081",
     lambda e: e["level"] == "error" and e["kind"] == "console"),
    ("unparsed",
     "garbage without prefix",
     lambda e: e["kind"] == "console" and e["ts"] is None),
]


def main():
    fails = 0
    for name, line, check in CASES:
        e = parse_line(line)
        if check(e):
            print(f"ok   {name}")
        else:
            fails += 1
            print(f"FAIL {name}: {e}")
    print("FAILS:", fails)
    raise SystemExit(1 if fails else 0)


if __name__ == "__main__":
    main()