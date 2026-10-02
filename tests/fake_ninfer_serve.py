#!/usr/bin/env python3
"""Fake ninfer-serve for exercising the dashboard without loading a model.

Two output streams, both matching the latest binary:

1. The current *pretty* operational stderr (product logging):
   ``YYYY-MM-DD HH:MM:SS.mmm  LEVEL  message`` with "| "-separated
   clauses — "loading weights | ...", "weights ready | ...",
   "engine ready | ...", "warmup complete | ...",
   "listening on http://... | model ... | auth ...", then
   "req#N ..." and "throughput | ..." activity lines. (Upstream this
   format is "intentionally not parsed"; the dashboard's ground truth is
   /health, which this fake serves too.)
2. A /health endpoint with the real lifecycle: the port is bound at spawn
   but nothing is accepted until the ready sequence completes, so
   /health only answers 200 {"status":"ok"} once "server ready" has been
   logged — exactly like ninfer-serve's bind → load → warmup → attach →
   listen ordering.
3. Realistic schema-v10 records into the file named by
   ``--request-log-jsonl PATH`` (server_start at attach — model loaded,
   before the listening line — then request_start / request_done /
   request_rejected / throughput), the same shape ninfer-serve's
   JsonlRequestLog emits; the dashboard's log stream is fed from this
   file only.

A real SIGINT/SIGTERM makes it log a stop line and exit 0, matching the
supervised clean-stop path.

Usage (point a profile's "binary" at this file):
    python3 tests/fake_ninfer_serve.py [artifact] [--host H] [--port P]
        [--request-log-jsonl PATH]
"""

from __future__ import annotations

import json
import os
import signal
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL_ID = "fake-model-1"
TOTAL_BYTES = 13209497446  # matches the JSONL artifact payload below


def ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def log(msg: str, level: str = "INFO ") -> None:
    # Service presentation: timestamp + 2 spaces + 5-char level + 1 space.
    sys.stderr.write(f"{ts()}  {level} {msg}\n")
    sys.stderr.flush()


# -- argv --------------------------------------------------------------------

def parse_args(argv: list[str]) -> dict:
    args = {"host": "127.0.0.1", "port": 8099, "jsonl": None}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--host" and i + 1 < len(argv):
            args["host"] = argv[i + 1]; i += 2
        elif a == "--port" and i + 1 < len(argv):
            args["port"] = int(argv[i + 1]); i += 2
        elif a == "--request-log-jsonl" and i + 1 < len(argv):
            args["jsonl"] = argv[i + 1]; i += 2
        else:
            i += 1  # artifact path and unknown flags are ignored
    return args


# -- JSONL writer (schema v10) ----------------------------------------------

class Jsonl:
    def __init__(self, path: str | None):
        self.path = path
        self.instance_id = f"serve-{os.getpid()}-1700000000000000"

    def emit(self, event: str, **extra) -> None:
        if not self.path:
            return
        rec = {
            "artifact_type": "ninfer_serve_request_log",
            "schema_version": 10,
            "event": event,
            "timestamp_unix_ms": int(time.time() * 1000),
            "server_instance_id": self.instance_id,
        }
        rec.update(extra)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
            f.flush()


# -- /health endpoint ---------------------------------------------------------

class Health(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path != "/health":
            self.send_response(404)
            self.end_headers()
            return
        body = b'{"status":"ok"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


_httpd: ThreadingHTTPServer | None = None   # bound at spawn (like bind())
_listen_thread: threading.Thread | None = None  # started after ready


def on_signal(signum, frame):
    log("server stopped")
    if _listen_thread is not None:
        _httpd.shutdown()
        _httpd.server_close()
    sys.exit(0)


def main() -> None:
    global _httpd, _listen_thread
    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    a = parse_args(sys.argv[1:])
    jsonl = Jsonl(a["jsonl"])

    # Bind the port immediately (the real binary binds before loading);
    # nothing is accepted until listen() after the ready sequence, so
    # /health stays unreachable/down while the "model" loads.
    _httpd = ThreadingHTTPServer((a["host"], a["port"]), Health)

    # -- engine startup (pretty, INFO level = what a default run shows) ----
    log("starting engine")
    log(f"loading weights | 19.73 GiB")
    # Rate-limited persistent progress records (redirected stderr): the
    # real binary emits at most one per 10 s; this fake paces one per
    # second so the dashboard's progress bar is visible in the test run.
    for pct in (12.5, 37.5, 62.5, 87.5):
        time.sleep(1)
        done = (TOTAL_BYTES * pct / 100) / 1024 ** 3
        eta = (100 - pct) / 12.5
        log(f"  loading weights {pct:.1f}% | {done:.2f} GiB/19.73 GiB"
            f" | 4.38 GiB/s | ETA {eta:.1f}s")
    time.sleep(0.5)
    log(f"weights ready | 19.73 GiB | 4.5s | 4.38 GiB/s")
    log("pinning host state | 1.00 GiB")
    time.sleep(1)
    log("host state pinned | 1.00 GiB | 1.0s")
    log("pinning host KV | 2.00 GiB")
    time.sleep(0.2)
    log("host KV pinned | 2.00 GiB | 150 ms")
    log("CUDA graphs ready | 532 ms")
    log(f"engine ready | {MODEL_ID} | total 6.0s | weights 19.73 GiB"
        " | CUDA sync off")
    log("capacity | KV 245,000 tokens, int8, auto | pages 3,828/4,096"
        " | runtime 1.00 GiB | free 2.00 GiB")
    log("context cache | 2 active + 2 cached device states"
        " | host 16 states, 24.00 GiB KV | private 32 | shared 8"
        " | anchors 4")
    time.sleep(1)
    log("warmup complete | 1.0s")

    # -- attach: server_start is written here, before the listening line ---
    jsonl.emit(
        "server_start",
        server={
            "host": a["host"], "port": a["port"],
            "public_model_id": MODEL_ID,
            "api_key_configured": False, "cors_enabled": False,
            "max_request_bytes": 402653184, "media_cache_bytes": 1073741824,
            "media_live_bytes": 2147483648, "media_preprocess_threads": 0,
            "request_log_jsonl": a["jsonl"], "default_output_tokens": 8192,
            "default_thinking": True, "default_preserve_thinking": False,
        },
        artifact={
            "path": "/dev/null", "size_bytes": None, "target": "fake",
            "weights_id": "fake-weights-1", "bytes_read": TOTAL_BYTES,
            "host_to_device_bytes": TOTAL_BYTES, "peak_staging_bytes": 1073741824,
            "tensor_count": 290, "resource_count": 4,
            "load_seconds": 10.5, "upload_seconds": 10.2,
        },
        engine={
            "device": 0, "max_context": 262144, "kv_capacity_mode": "auto",
            "kv_capacity": 245000, "kv_capacity_page_groups": 3828,
            "kv_capacity_max_page_groups": 4096, "max_concurrency": 2,
            "max_pending_requests": 16, "pending_timeout_ms": 30000,
            "prefill_chunk": 1024, "log_stats_interval_ms": 5000,
            "kv_cache": "int8-group64", "vision": False, "cuda_graph": True,
            "prefix_reuse": True, "speculative_backend": "mtp",
            "speculative_draft_window": 3, "proposal_head": "optimized",
        },
        sampling_defaults={
            "thinking": {"temperature": 1.0, "top_p": 0.95, "top_k": 20,
                         "min_p": 0.0, "presence_penalty": 0.0,
                         "frequency_penalty": 0.0},
            "non_thinking": {"temperature": 0.7, "top_p": 0.80, "top_k": 20,
                             "min_p": 0.0, "presence_penalty": 1.5,
                             "frequency_penalty": 0.0},
            "server_overrides": {"temperature": None, "top_p": None,
                                 "top_k": None, "min_p": None,
                                 "presence_penalty": None,
                                 "frequency_penalty": None, "seed": None},
            "omitted_seed": "random", "greedy": False,
        },
        memory={
            "weights": {"capacity_bytes": 14495514624,
                        "used_bytes": TOTAL_BYTES,
                        "peak_used_bytes": TOTAL_BYTES},
            "sequence": {"capacity_bytes": 1073741824, "used_bytes": 524288000,
                         "peak_used_bytes": 524288000},
            "workspace": {"capacity_bytes": 1073741824, "used_bytes": 104857600,
                          "peak_used_bytes": 209715200},
            "request_transient": {"capacity_bytes": 536870912,
                                  "used_bytes": 0, "peak_used_bytes": 0},
            "minimum_runtime_reservation_bytes": 1073741824,
            "kv_capacity_increment_bytes": 262144,
            "runtime_reservation_bytes": 1073741824,
            "available_after_weights_bytes": 3221225472,
            "available_after_startup_bytes": 2147483648,
            "kv_capacity_headroom_bytes": 1073741824,
            "planned_slack_bytes": 536870912,
            "cuda_graph_allowance_bytes": 2147483648,
            "cuda_graph_observed_bytes": 1288490188,
            "kv_payload_bytes": 490000,
        },
        environment={
            "device": 0, "gpu_name": "NVIDIA H100 80GB HBM3",
            "gpu_uuid": "GPU-12345678-1234-2234-1234-123456789abc",
            "total_device_memory_bytes": 85899345920,
            "compute_capability_major": 9, "compute_capability_minor": 0,
            "cuda_compile_version": "12.8", "cuda_runtime_version": "12.8",
            "cuda_driver_version": "570.124.06",
        },
        argv=[sys.argv[0], "--host", a["host"], "--port", str(a["port"]),
              "--request-log-jsonl", a["jsonl"] or ""],
    )
    log(f"listening on http://{a['host']}:{a['port']} | model {MODEL_ID}"
        " | auth disabled")

    # -- listen: only now does /health answer (and accept requests) --------
    _listen_thread = threading.Thread(target=_httpd.serve_forever,
                                      daemon=True, name="health-listen")
    _listen_thread.start()

    # One accepted request (with thinking) and one rejection to exercise the
    # red rows in the dashboard.
    jsonl.emit(
        "request_start",
        request={
            "request_id": 1, "protocol": "openai_chat", "model": MODEL_ID,
            "stream": False, "message_count": 2, "media_item_count": 0,
            "requested_output_tokens": 128,
            "requested_output_tokens_source": "client",
            "tool_count": 0, "tool_choice": "auto", "has_tool_history": False,
            "enable_thinking": True, "preserve_thinking": False,
            "preserve_thinking_semantic_change": False,
            "sampling": {"temperature": 1.0, "top_p": 0.95, "top_k": 20,
                         "min_p": 0.0, "presence_penalty": 0.0,
                         "frequency_penalty": 0.0, "seed": 1234567890},
        },
        preparation_seconds={
            "total": 0.012, "acquisition": 0.0, "media_preprocess": 0.0,
            "media_preprocess_work": 0.0, "tokenize": 0.011,
            "media_items": 0, "media_bytes": 0, "raw_patches": 0,
            "vision_tokens": 0, "patch_bytes": 0, "cache_hits": 0,
            "cache_misses": 0, "singleflight_waits": 0,
            "built_patch_bytes": 0, "reused_patch_bytes": 0,
        },
    )
    log("req#1 started | openai-chat non-stream | 2 messages | max output 128"
        " | thinking medium")
    time.sleep(1)
    jsonl.emit(
        "request_done",
        request={
            "request_id": 1, "protocol": "openai_chat", "model": MODEL_ID,
            "stream": False, "message_count": 2, "media_item_count": 0,
            "requested_output_tokens": 128,
            "requested_output_tokens_source": "client",
            "tool_count": 0, "tool_choice": "auto", "has_tool_history": False,
            "enable_thinking": True, "preserve_thinking": False,
            "preserve_thinking_semantic_change": False,
            "sampling": {"temperature": 1.0, "top_p": 0.95, "top_k": 20,
                         "min_p": 0.0, "presence_penalty": 0.0,
                         "frequency_penalty": 0.0, "seed": 1234567890},
        },
        result={
            "finish_reason": "stop_token", "prompt_tokens": 42,
            "completion_tokens": 128, "computed_prefill_tokens": 2,
            "prefix_cache_hit_tokens": 40,
            "prefix_reuse_path": "append_frontier", "tool_call_count": 0,
        },
        timings_seconds={
            "prepare": 0.012, "ttft": 0.095, "vision": 0.0,
            "prefill": 0.00025, "decode": 2.29, "total": 2.4,
        },
        speculative={
            "backend": "mtp", "draft_window": 3, "rounds": 29,
            "drafted_tokens": 87, "accepted_tokens": 72,
            "fallback_steps": 0, "accepted_per_position": 2.5,
        },
    )
    log("req#1 done | openai-chat | stop token | prompt 42 | output 128"
        " | cache 40 (95.2%, turn closure) | TTFT 95ms | total 2.4s"
        " | prefill 8k tok/s | decode 55.0 tok/s"
        " | mtp accepted 72/87 (82.8%)")
    jsonl.emit(
        "request_rejected",
        phase="prepare",
        request={
            "request_id": 2, "protocol": "openai_responses",
            "model": MODEL_ID, "stream": True, "message_count": 1,
            "media_item_count": 0, "requested_output_tokens": 8192,
            "requested_output_tokens_source": "server_default",
            "tool_count": 0, "tool_choice": "auto", "has_tool_history": False,
        },
        error={
            "status": 400, "type": "invalid_request_error",
            "code": "context_length_exceeded", "param": None,
            "message": "expanded prompt (40980 tokens) exceeds the configured"
                       " context ceiling (262144)",
        },
    )
    log("req#2 rejected during prepare | openai-responses stream | HTTP 400"
        " | context length exceeded | messages 1")

    def throughput_event(prefill: int, decode: int, running: int,
                         rounds: int, row_rounds: int) -> None:
        interval = 5.0
        jsonl.emit(
            "throughput",
            interval_seconds=interval,
            tokens={"computed_prefill": prefill, "committed_decode": decode},
            throughput_tokens_per_second={
                "prefill": prefill / interval, "decode": decode / interval,
            },
            scheduler={"running": running, "prefilling": 0,
                       "decode_ready": running, "waiting": 0},
            decode_batch={"rounds": rounds, "row_rounds": row_rounds,
                          "average_size": (row_rounds / rounds) if rounds else None},
        )
        parts = ["throughput | 5.0s"]
        if prefill != 0:
            parts.append(f"prefill {prefill / interval:.1f} tok/s"
                         f" ({prefill} tok)")
        parts.append(f"decode {decode / interval:.1f} tok/s ({decode} tok)")
        parts.append(f"running {running} (decode-ready {running})")
        parts.append(f"batch {(row_rounds / rounds) if rounds else 0:.2f}")
        parts.append("host 30.0% (1.5s)")
        log(" | ".join(parts))

    throughput_event(210, 275, 1, 55, 55)
    while True:
        time.sleep(5)
        throughput_event(0, 275, 1, 55, 82)


if __name__ == "__main__":
    main()
