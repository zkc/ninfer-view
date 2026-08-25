#!/usr/bin/env python3
"""Fake ninfer-serve for exercising the dashboard without loading a model.

It emits the exact stderr lifecycle the real binary produces (same prefix
format, progress-line shape, listening line, request/throughput activity
lines) over ~8 seconds, then idles. A real SIGINT/SIGTERM makes it log a stop
line and exit 0, matching the supervised clean-stop path.

Usage (point a profile's "binary" at this file):
    python3 tests/fake_ninfer_serve.py          # any args are ignored
"""

import signal
import sys
import time
from datetime import datetime


def ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def log(msg: str) -> None:
    sys.stderr.write(f"[{ts()}] [info] ninfer-serve: {msg}\n")
    sys.stderr.flush()


def on_signal(signum, frame):
    log("stopping via SIGINT")
    sys.exit(0)


def main() -> None:
    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    log("loading model...")
    for p in (10.00, 35.50, 60.25, 90.10, 100.00):
        # Mirrors LoadProgressRenderer::format_line (Log mode).
        log(
            f"load        load_weights                  {p:.2f}%      6.20 GiB /"
            f"      12.40 GiB        2.300 s"
        )
        time.sleep(1)
    log("model loaded in 10.500 s")
    log(
        "KV capacity auto resolved=245000 tokens pages=3828/4096"
        " runtime=1.00 GiB free-after-weights=3.00 GiB free-after-startup=2.00 GiB"
        " headroom=1.00 GiB slack=0.50 GiB graphs=1.20 GiB/2.00 GiB"
    )
    log("warming up...")
    time.sleep(1)
    log("listening on http://127.0.0.1:8099 (model id: fake-model-1, auth: disabled)")
    log(
        "[req 1] openai_chat non-stream msgs=2 max_tokens=128 (client) tools=0"
        " tool_choice=auto tool_history=no thinking=on preserve_thinking=off"
        " preserve_change=no sampler=[t=1.0 p=0.95 k=20 minp=0.0 pres=0.0 freq=0.0]"
        " \u2192 submitted"
    )
    log(
        "[req 1] done finish=stop prompt=42 gen=128 cache=40"
        " reuse=append_frontier ttft=95ms prefill=8100.00tok/s decode=55.00tok/s"
        " wall=2.40s speculative=mtp draft=3 acc=2.50"
    )
    log(
        "throughput interval=5.000s prefill=42.0tok/s decode=55.0tok/s running=1"
        " prefilling=0 decode_ready=1 waiting=0 avg_decode_batch=1.00"
    )
    while True:
        time.sleep(5)
        log(
            "throughput interval=5.000s prefill=0.0tok/s decode=55.0tok/s running=1"
            " prefilling=0 decode_ready=1 waiting=0 avg_decode_batch=1.00"
        )


if __name__ == "__main__":
    main()