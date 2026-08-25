#!/usr/bin/env python3
"""Fake *external* ninfer-serve for testing attach mode.

Serves GET /health ({"status":"ok"}) on --port and appends realistic
schema-v10 records to --request-log-jsonl every few seconds. It is meant to
be launched OUTSIDE ninfer-view (no stderr lifecycle, no SIGINT contract —
kill it however you like) and then attached via POST /api/attach.

Usage:
    python3 tests/fake_external_instance.py --port 8098 \
        --request-log-jsonl /tmp/ext.jsonl
"""

import argparse
import json
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

INSTANCE_ID = "serve-external-42"


class Health(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = json.dumps({"status": "ok"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def emit(path, event, **extra):
    rec = {
        "artifact_type": "ninfer_serve_request_log",
        "schema_version": 10,
        "event": event,
        "timestamp_unix_ms": int(time.time() * 1000),
        "server_instance_id": INSTANCE_ID,
    }
    rec.update(extra)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
        f.flush()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--request-log-jsonl", required=True)
    args = ap.parse_args()

    signal.signal(signal.SIGINT, lambda *a: sys.exit(0))
    signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))

    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Health)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    # Pretend we've been serving for a while: pre-existing history that an
    # attaching tailer (seek_end) must NOT replay.
    emit(args.request_log_jsonl, "server_start",
         server={"host": "127.0.0.1", "port": args.port,
                 "public_model_id": "external-model"},
         engine={"device": 0, "max_context": 32768,
                 "kv_capacity_mode": "auto", "kv_capacity": 40000,
                 "max_concurrency": 4, "kv_cache": "int8-group64",
                 "speculative_backend": "none",
                 "speculative_draft_window": 0})
    for i in (1, 2):
        emit(args.request_log_jsonl, "request_start",
             request={"request_id": i, "protocol": "openai_chat",
                      "stream": False, "enable_thinking": False})
        emit(args.request_log_jsonl, "request_done",
             request={"request_id": i, "protocol": "openai_chat"},
             result={"finish_reason": "stop_token",
                     "prompt_tokens": 100 + i,
                     "completion_tokens": 50,
                     "computed_prefill_tokens": 100,
                     "prefix_cache_hit_tokens": 0,
                     "prefix_reuse_path": "none"},
             timings_seconds={"prepare": 0.001, "ttft": 0.05,
                              "vision": 0.0, "prefill": 0.01,
                              "decode": 1.2, "total": 1.25})

    rid = 3
    while True:
        time.sleep(3)
        emit(args.request_log_jsonl, "request_start",
             request={"request_id": rid, "protocol": "openai_chat",
                      "stream": False, "enable_thinking": False})
        emit(args.request_log_jsonl, "request_done",
             request={"request_id": rid, "protocol": "openai_chat"},
             result={"finish_reason": "stop_token",
                     "prompt_tokens": 64, "completion_tokens": 32,
                     "computed_prefill_tokens": 64,
                     "prefix_cache_hit_tokens": 64,
                     "prefix_reuse_path": "exact"},
             timings_seconds={"prepare": 0.001, "ttft": 0.03,
                              "vision": 0.0, "prefill": 0.008,
                              "decode": 0.6, "total": 0.63})
        emit(args.request_log_jsonl, "throughput",
             interval_seconds=5.0,
             tokens={"computed_prefill": 64, "committed_decode": 32},
             throughput_tokens_per_second={"prefill": 12.8, "decode": 6.4},
             scheduler={"running": 1, "prefilling": 0, "decode_ready": 1,
                        "waiting": 0},
             decode_batch={"rounds": 32, "row_rounds": 32,
                           "average_size": 1.0})
        rid += 1


if __name__ == "__main__":
    main()