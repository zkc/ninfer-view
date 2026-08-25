"""Unit tests for the /health poller.

Run:  python3 tests/test_health.py
"""

import json
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ninfer_view.health import HealthPoller


class HealthHandler(BaseHTTPRequestHandler):
    up = True

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path != "/health":
            self.send_response(404)
            self.end_headers()
            return
        if HealthHandler.up:
            body = json.dumps({"status": "ok"}).encode()
            self.send_response(200)
        else:
            body = json.dumps({"status": "degraded"}).encode()
            self.send_response(503)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_check_now():
    port = free_port()
    srv = ThreadingHTTPServer(("127.0.0.1", port), HealthHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        p = HealthPoller(f"http://127.0.0.1:{port}/health", lambda ok: None,
                         interval_s=0.1, timeout_s=1.0)
        assert p.check_now() is True
        HealthHandler.up = False
        assert p.check_now() is False  # 503 is not "ok"
        HealthHandler.up = True
        # closed port -> down, no exception
        p2 = HealthPoller("http://127.0.0.1:1/health", lambda ok: None,
                          timeout_s=0.5)
        assert p2.check_now() is False
    finally:
        srv.shutdown()
    print("ok   test_check_now")


def test_poller_callbacks():
    port = free_port()
    srv = ThreadingHTTPServer(("127.0.0.1", port), HealthHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    got = []
    try:
        p = HealthPoller(f"http://127.0.0.1:{port}/health", got.append,
                         interval_s=0.05, timeout_s=1.0)
        p.start()
        time.sleep(0.5)
        HealthHandler.up = False
        time.sleep(0.5)
        p.stop()
    finally:
        srv.shutdown()
    assert got and got[0] is True and False in got, got
    assert not p.alive()
    print("ok   test_poller_callbacks (sequence: %s)" % got[:8])


def test_callback_errors_cannot_kill_poller():
    port = free_port()  # nothing listening: every check returns False
    p = HealthPoller(f"http://127.0.0.1:{port}/health",
                     lambda ok: 1 / 0, interval_s=0.05, timeout_s=0.3)
    p.start()
    time.sleep(0.4)
    assert p.alive(), "poller must survive raising callbacks"
    p.stop()
    print("ok   test_callback_errors_cannot_kill_poller")


if __name__ == "__main__":
    test_check_now()
    test_poller_callbacks()
    test_callback_errors_cannot_kill_poller()
    print("ALL HEALTH TESTS PASS")