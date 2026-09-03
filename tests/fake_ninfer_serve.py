#!/usr/bin/env python3
"""Fake ninfer-serve for exercising the dashboard without loading a model.

Two output streams, both matching the real binary:

1. The current structured stderr startup log
   (``[ninfer-serve] startup phase=... status=begin|complete ...``,
   ``engine status=ready``, ``server status=ready ...`` and
   ``request id=N status=...`` / ``throughput ...`` activity lines) over
   ~8 seconds.
2. Realistic schema-v10 records into the file named by
   ``--request-log-jsonl PATH`` (server_start after load, then
   request_start / request_done / request_rejected / throughput), the same
   shape ninfer-serve's JsonlRequestLog emits — the dashboard's log stream
   is fed from this file only.

A real SIGINT/SIGTERM makes it log a stop line and exit 0, matching the
supervised clean-stop path.

Usage (point a profile's "binary" at this file):
    python3 tests/fake_ninfer_serve.py [artifact] [--host H] [--port P]
        [--request-log-jsonl PATH]
"""

import json
import os
import signal
import sys
import time
from datetime import datetime

MODEL_ID = "fake-model-1"


def ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def log(msg: str, level: str = "info") -> None:
    sys.stderr.write(f"[{ts()}] [{level}] [ninfer-serve] {msg}\n")
    sys.stderr.flush()


def on_signal(signum, frame):
    log("stopping via SIGINT")
    sys.exit(0)


# -- argv ------------------------------------------------------------------

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


def main() -> None:
    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    a = parse_args(sys.argv[1:])
    jsonl = Jsonl(a["jsonl"])

    total_bytes = 13209497446  # matches the JSONL artifact payload below
    log("startup phase=engine-startup status=begin")
    log("startup phase=cuda-initialize status=begin")
    time.sleep(1)
    log("startup phase=cuda-initialize status=complete duration_ms=229.309")
    log("startup phase=artifact-inspect status=begin")
    log("startup phase=artifact-inspect status=complete duration_ms=3.164")
    log("startup phase=target-plan status=begin")
    log("startup phase=target-plan status=complete duration_ms=26.542")
    log(f"startup phase=weights-materialize status=begin"
        f" total_bytes={total_bytes}")
    # The current binary emits no intermediate progress lines while
    # materializing; the phase jumps 0% -> 100% at completion.
    time.sleep(4)
    log(f"startup phase=weights-materialize status=complete"
        f" completed_bytes={total_bytes} total_bytes={total_bytes}"
        " duration_ms=4000.000")
    log("startup phase=target-finalize status=begin")
    log("startup phase=target-finalize status=complete duration_ms=2.635")
    log("startup phase=frontend-initialize status=begin")
    log("startup phase=frontend-initialize status=complete duration_ms=327.474")
    log("startup phase=program-initialize status=begin")
    log("startup phase=host-state-pin status=begin total_bytes=1073741824")
    time.sleep(1)
    log("startup phase=host-state-pin status=complete"
        " completed_bytes=1073741824 total_bytes=1073741824"
        " duration_ms=1000.000")
    log("startup phase=host-kv-pin status=begin total_bytes=2147483648")
    log("startup phase=host-kv-pin status=complete completed_bytes=2147483648"
        " total_bytes=2147483648 duration_ms=150.000")
    log("startup phase=cuda-graph-prepare status=begin")
    log("startup phase=cuda-graph-prepare status=complete duration_ms=532.571")
    log("startup phase=program-initialize status=complete"
        " duration_ms=6000.000")
    log("startup phase=engine-finalize status=begin")
    log("startup phase=engine-finalize status=complete duration_ms=0.282")
    log("startup phase=engine-startup status=complete duration_ms=10500.000")
    log(f"engine status=ready target=\"fake\" model_id=\"{MODEL_ID}\""
        " weights_id=\"fake-weights-1\" target_load_ms=10500.000"
        " materialization_pipeline_ms=4000.000"
        f" artifact_bytes_read={total_bytes} host_to_device_bytes={total_bytes}"
        " peak_staging_bytes=1073741824 tensors=290 resources=4")
    log("engine capacity kv_capacity_mode=auto kv_capacity_tokens=245000"
        " kv_page_groups=3828 kv_max_page_groups=4096"
        " runtime_reservation_bytes=1073741824"
        " available_after_weights_bytes=3221225472"
        " available_after_startup_bytes=2147483648")
    # server_start is written at attach: model loaded, warmup not yet done.
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
            "weights_id": "fake-weights-1", "bytes_read": 13209497446,
            "host_to_device_bytes": 13209497446, "peak_staging_bytes": 1073741824,
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
                        "used_bytes": 13209497446,
                        "peak_used_bytes": 13209497446},
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
            "gpu_uuid": "GPU-12345678-1234-1234-1234-123456789abc",
            "total_device_memory_bytes": 85899345920,
            "compute_capability_major": 9, "compute_capability_minor": 0,
            "cuda_compile_version": "12.8", "cuda_runtime_version": "12.8",
            "cuda_driver_version": "570.124.06",
        },
        argv=[sys.argv[0], "--host", a["host"], "--port", str(a["port"]),
              "--request-log-jsonl", a["jsonl"] or ""],
    )
    log("startup phase=serve-warmup status=begin")
    time.sleep(1)
    log("startup phase=serve-warmup status=complete duration_ms=1000.000")
    log(f"server status=ready host=\"{a['host']}\" port={a['port']}"
        f" model_id=\"{MODEL_ID}\" auth_enabled=false")

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
    log("request id=1 status=submitted protocol=\"openai_chat\" stream=false"
        " messages=2 media_items=0 requested_output_tokens=128 tools=0"
        " thinking=true reasoning_effort=medium preserve_thinking=false")
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
    log("request id=1 status=done finish_reason=stop_token prompt_tokens=42"
        " completion_tokens=128 prefix_cache_hit_tokens=40"
        " prefix_reuse_path=append_frontier ttft_ms=95.000 duration_ms=2400.000"
        " prefill_tokens_per_second=8100.000"
        " decode_tokens_per_second=55.000")
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
    log("request id=2 status=rejected phase=prepare protocol=openai_responses"
        " stream=true messages=1 media_items=0 requested_output_tokens=8192"
        " tools=0 status=400 code=context_length_exceeded"
        " message=expanded prompt (40980 tokens) exceeds the configured context"
        " ceiling (262144)")

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
        log(
            f"throughput interval_ms={interval * 1000:.3f}"
            f" computed_prefill_tokens={prefill} committed_decode_tokens={decode}"
            f" prefill_tokens_per_second={prefill / interval:.3f}"
            f" decode_tokens_per_second={decode / interval:.3f} running={running}"
            f" prefilling=0 decode_ready={running} waiting=0"
            f" average_decode_batch={(row_rounds / rounds) if rounds else 0:.3f}"
        )

    throughput_event(210, 275, 1, 55, 55)
    while True:
        time.sleep(5)
        throughput_event(0, 275, 1, 55, 82)


if __name__ == "__main__":
    main()