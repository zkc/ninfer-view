"""Unit tests for the JSONL tailer (no ninfer involved).

Run:  python3 tests/test_jsonl_tail.py
"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ninfer_view.jsonl_tail import JsonlTailer, ARTIFACT_TYPE


def base(event, **extra):
    rec = {
        "artifact_type": ARTIFACT_TYPE,
        "schema_version": 10,
        "event": event,
        "timestamp_unix_ms": int(time.time() * 1000),
        "server_instance_id": "serve-test-1",
    }
    rec.update(extra)
    return rec


def test_live_append():
    got = []
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "requests.jsonl")
        t = JsonlTailer(p, got.append, poll_s=0.02)
        t.start()
        time.sleep(0.3)  # let the tailer open the (absent) file, then create it
        with open(p, "a") as f:
            f.write(json.dumps(base("server_start", server={"port": 8081})) + "\n")
            f.write(json.dumps(base("request_start", request={"request_id": 1})) + "\n")
            time.sleep(0.1)
            # second write: also exercises the partial-line buffer, because we
            # write one record without its newline first
            line = json.dumps(base("request_done", result={"gen": 5}))
            f.write(line[:20])
            f.flush()
            time.sleep(0.1)
            f.write(line[20:] + "\n")
            f.write(json.dumps(base("throughput", tokens={})) + "\n")
            f.write("not json at all\n")                    # malformed
            f.write(json.dumps({"event": "other"}) + "\n")  # wrong artifact type
        time.sleep(0.4)
        t.stop()
    assert [r["event"] for r in got] == [
        "server_start", "request_start", "request_done", "throughput"], got
    assert got[1]["request"]["request_id"] == 1
    assert got[2]["result"]["gen"] == 5
    assert t.malformed == 2, t.malformed
    print("ok   test_live_append")


def test_wait_for_file_then_stop():
    got = []
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "never.jsonl")
        t = JsonlTailer(p, got.append, poll_s=0.05)
        t.start()
        time.sleep(0.2)
        t.stop(timeout=1.0)  # must not hang waiting for the file
        assert got == [] and not t.alive()
    print("ok   test_wait_for_file_then_stop")


def test_consumer_errors_dont_kill_tailer():
    def boom(_rec):
        raise RuntimeError("consumer bug")

    got = 0
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "requests.jsonl")
        t = JsonlTailer(p, boom, poll_s=0.02)
        t.start()
        time.sleep(0.2)
        with open(p, "a") as f:
            f.write(json.dumps(base("throughput")) + "\n")
            f.write(json.dumps(base("throughput")) + "\n")
        time.sleep(0.3)
        assert t.alive(), "tailer must survive consumer exceptions"
        t.stop()
    print("ok   test_consumer_errors_dont_kill_tailer")


def test_existing_file_reads_history():
    got = []
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "requests.jsonl")
        with open(p, "w") as f:  # pre-existing history (fresh per run, so we
            f.write(json.dumps(base("server_start")) + "\n")  # read from start)
            f.write(json.dumps(base("request_done")) + "\n")
        t = JsonlTailer(p, got.append, poll_s=0.02)
        t.start()
        time.sleep(0.4)
        t.stop()
    assert [r["event"] for r in got] == ["server_start", "request_done"], got
    print("ok   test_existing_file_reads_history")


if __name__ == "__main__":
    test_live_append()
    test_wait_for_file_then_stop()
    test_consumer_errors_dont_kill_tailer()
    test_existing_file_reads_history()
    print("ALL TAILER TESTS PASS")