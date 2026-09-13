"""Unit tests for the stderr console parser (no ninfer involved).

Run:  python3 tests/test_console_parse.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ninfer_view.console_parse import parse_line

PREFIX = "[2026-08-24 21:00:00.000] [info] ninfer-serve: "

# Current (structured) format, as emitted by the latest ninfer-serve:
NPREFIX = "[2026-09-02 00:19:18.705] [info] [ninfer-serve] "


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

# --- current (structured) format -------------------------------------------
# These mirror real lines from ninfer-serve >= the structured-logging build,
# e.g. .../.local/state/ninfer-view/runs/20260902-001918-683821.

NEW_CASES = [
    ("new: first startup line -> loading",
     NPREFIX + "startup phase=engine-startup status=begin",
     lambda e: e["kind"] == "lifecycle" and e["phase"] == "loading"),
    ("new: weights begin -> progress 0%",
     NPREFIX + "startup phase=weights-materialize status=begin"
              " total_bytes=21183894976",
     lambda e: e["kind"] == "progress"
               and e["progress"]["phase"] == "weights-materialize"
               and e["progress"]["percent"] == 0.0
               and e["progress"]["total"] == "19.73 GiB"
               and e["progress"]["done"] == "0 B"),
    ("new: weights complete -> progress 100%",
     NPREFIX + "startup phase=weights-materialize status=complete"
              " completed_bytes=21183894976 total_bytes=21183894976"
              " duration_ms=5886.584",
     lambda e: e["kind"] == "progress"
               and e["progress"]["percent"] == 100.0
               and e["progress"]["done"] == "19.73 GiB"
               and abs(e["progress"]["elapsed_s"] - 5.886584) < 1e-9),
    ("new: serve-warmup begin -> warming",
     NPREFIX + "startup phase=serve-warmup status=begin",
     lambda e: e["kind"] == "lifecycle" and e["phase"] == "warming"),
    ("new: engine ready -> loaded",
     "[2026-09-02 00:19:29.053] [info] [ninfer-serve] engine status=ready"
     ' target="qwen3_8_27b" model_id="qwen3.8-27b" weights_id="nvfp4"'
     " target_load_ms=10116.848 materialization_pipeline_ms=5809.461"
     " artifact_bytes_read=21196796217 host_to_device_bytes=21183894976"
     " peak_staging_bytes=268435456 tensors=673 resources=6",
     lambda e: e["kind"] == "lifecycle" and e["phase"] == "loaded"
               and abs(e["load_seconds"] - 10.116848) < 1e-9),
    ("new: engine capacity -> kv",
     NPREFIX + "engine capacity kv_capacity_mode=explicit"
              " kv_capacity_tokens=262144 kv_page_groups=4096"
              " kv_max_page_groups=16384",
     lambda e: e["kind"] == "lifecycle" and e["phase"] == "kv"),
    ("new: server ready -> listening",
     "[2026-09-02 00:19:29.126] [info] [ninfer-serve] server status=ready"
     ' host="127.0.0.1" port=8081 model_id="qwen3.8-27b" auth_enabled=false',
     lambda e: e["kind"] == "lifecycle" and e["phase"] == "listening"
               and e["endpoint"] == "http://127.0.0.1:8081"
               and e["model_id"] == "qwen3.8-27b"
               and e["auth"] == "disabled"),
    ("new: startup failed (error)",
     "[2026-09-02 00:15:16.861] [error] [ninfer-serve]"
     " startup phase=target-plan status=failed duration_ms=0.561",
     lambda e: e["kind"] == "lifecycle" and e["phase"] == "failed"
               and e["level"] == "error"
               and e["detail"] == "startup phase target-plan failed"),
    ("new: server failed w/ detail (critical)",
     '[2026-09-02 00:15:16.861] [critical] [ninfer-serve] server status=failed'
     ' phase=startup detail="requested Engine runtime reservation requires'
     ' 10894288128 bytes, but only 10758513664 bytes are available for'
     ' runtime capacity"',
     lambda e: e["kind"] == "lifecycle" and e["phase"] == "failed"
               and e["level"] == "critical"
               and e["detail"].startswith("requested Engine runtime"
                                          " reservation")),
    ("new: request submitted -> activity",
     NPREFIX + 'request id=1 status=submitted protocol="openai_chat_completions"'
     " stream=true messages=2 media_items=0 requested_output_tokens=64"
     " tools=0 thinking=false reasoning_effort=none preserve_thinking=false",
     lambda e: e["kind"] == "activity"),
    ("new: request done -> activity",
     NPREFIX + "request id=1 status=done finish_reason=stop_token"
     " prompt_tokens=198 completion_tokens=10 prefix_cache_hit_tokens=0"
     " prefix_reuse_path=root ttft_ms=90.939 duration_ms=495.584"
     " prefill_tokens_per_second=2238.976 decode_tokens_per_second=128.868",
     lambda e: e["kind"] == "activity"),
    ("new: throughput -> activity",
     NPREFIX + "throughput interval_ms=5000.066 computed_prefill_tokens=8407"
     " committed_decode_tokens=144 prefill_tokens_per_second=1681.378"
     " decode_tokens_per_second=28.800 running=1 prefilling=1"
     " decode_ready=0 waiting=0",
     lambda e: e["kind"] == "activity"),
    ("new: complete w/o bytes passes through",
     NPREFIX + "startup phase=engine-startup status=complete"
              " duration_ms=10348.024",
     lambda e: e["kind"] == "console" and e["ts"] is not None),
    ("new: engine context_cache passes through",
     NPREFIX + "engine context_cache enabled=true active_lanes=4"
              " device_state_slots=2 host_state_slots=16",
     lambda e: e["kind"] == "console" and e["level"] == "info"),
]

CASES += NEW_CASES


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