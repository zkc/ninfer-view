"""Local HTTP server: dashboard page, REST actions, and an SSE event stream.

Routes (bound to 127.0.0.1 only; local tool, no auth):
    GET  /                dashboard (web/index.html)
    GET  /style.css, /js/*.js  static dashboard assets under web/
    GET  /api/state       state snapshot
    GET  /api/logs        recent JSONL log events (backfill)
    GET  /api/profiles    saved launch profiles (+ default_binary from
                           NINFER_SERVE_BINARY, for the form's fallback)
    POST /api/profiles    upsert a profile
    POST /api/start       {profile_id}
    POST /api/stop
    GET  /api/stream      SSE: state | server_start | request_start |
                           request_rejected | request_done | request_error |
                           throughput
    POST /api/attach      {host, port, jsonl_path} — observe an external
                           instance (live-only tail + /health poller)
    POST /api/detach      stop observing
Log stream: the /api/logs and /api/stream log events come only from the
child's --request-log-jsonl file (schema v10); stderr never enters them.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


class Handler(BaseHTTPRequestHandler):
    service = None  # injected by serve()

    def log_message(self, fmt, *args):  # keep the daemon quiet
        pass

    # -- plumbing ---------------------------------------------------------

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj).encode(), "application/json")

    _STATIC_TYPES = {
        ".css": "text/css; charset=utf-8",
        ".js": "text/javascript; charset=utf-8",
        ".json": "application/json; charset=utf-8",
        ".svg": "image/svg+xml",
        ".png": "image/png",
    }

    def _static(self, rel: str) -> None:
        """Serve a dashboard asset from web/ (path-traversal-proof)."""
        base = WEB_DIR.resolve()
        target = (base / rel).resolve()
        if target != base and base not in target.parents:
            return self._json({"error": "not found"}, 404)
        if not target.is_file():
            return self._json({"error": "not found"}, 404)
        ctype = self._STATIC_TYPES.get(target.suffix, "application/octet-stream")
        self._send(200, target.read_bytes(), ctype)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            return json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return {}

    # -- routes -----------------------------------------------------------

    def do_GET(self):
        try:
            self._handle_get()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:  # keep the daemon alive; report clean JSON
            self._json({"ok": False, "error": f"{type(e).__name__}: {e}"}, 500)

    def _handle_get(self):
        path = urlparse(self.path).path
        if path == "/":
            try:
                body = (WEB_DIR / "index.html").read_bytes()
            except OSError as e:
                return self._send(500, f"missing web/index.html: {e}".encode(),
                                  "text/plain")
            self._send(200, body, "text/html; charset=utf-8")
        elif path == "/api/state":
            self._json(self.service.snapshot())
        elif path == "/api/logs":
            qs = parse_qs(urlparse(self.path).query)
            try:
                limit = max(1, min(5000, int(qs.get("limit", ["500"])[0])))
            except ValueError:
                limit = 500
            self._json({"logs": self.service.bus.recent_logs(limit)})
        elif path == "/api/profiles":
            self._json({"profiles": self.service.profiles.all(),
                        "default_binary": self.service.profiles.default_binary()})
        elif path == "/api/stream":
            self._sse()
        elif path.startswith("/api/"):
            self._json({"error": "not found"}, 404)
        else:
            self._static(path.lstrip("/"))

    def do_POST(self):
        try:
            self._handle_post()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:  # keep the daemon alive; report clean JSON
            self._json({"ok": False, "error": f"{type(e).__name__}: {e}"}, 500)

    def _handle_post(self):
        path = urlparse(self.path).path
        body = self._read_json()
        if path == "/api/start":
            profile_id = str(body.get("profile_id", "default"))
            ok, err = self.service.start(profile_id)
            self._json({"ok": ok, "error": err}, 200 if ok else 400)
        elif path == "/api/stop":
            ok, err = self.service.stop()
            self._json({"ok": ok, "error": err}, 200 if ok else 400)
        elif path == "/api/attach":
            ok, err = self.service.attach(
                str(body.get("host", "127.0.0.1")),
                body.get("port", 8081),
                str(body.get("jsonl_path", "")),
            )
            self._json({"ok": ok, "error": err}, 200 if ok else 400)
        elif path == "/api/detach":
            ok, err = self.service.detach()
            self._json({"ok": ok, "error": err}, 200 if ok else 400)
        elif path == "/api/profiles":
            profile = body.get("profile") or {}
            try:
                self.service.profiles.save(profile)
            except (ValueError, TypeError) as e:
                return self._json({"ok": False, "error": str(e)}, 400)
            self._json({"ok": True})
        else:
            self._json({"error": "not found"}, 404)

    # -- SSE ----------------------------------------------------------------

    def _sse_write(self, name: str, data: dict) -> None:
        payload = json.dumps(data, default=str).encode()
        self.wfile.write(b"event: " + name.encode() + b"\ndata: " + payload + b"\n\n")
        self.wfile.flush()

    def _sse(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        q = self.service.bus.subscribe()
        try:
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            # Replay current state so a late subscriber renders correctly.
            self._sse_write("state", self.service.snapshot())
            while True:
                try:
                    ev = q.get(timeout=15)
                except Exception:  # queue.Empty
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
                    continue
                self._sse_write(str(ev.get("kind", "event")), ev)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.service.bus.unsubscribe(q)


def serve(service, host: str, port: int) -> ThreadingHTTPServer:
    Handler.service = service
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    print(f"ninfer-view dashboard: http://{host}:{port}")
    print("Ctrl-C to quit.")
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\nninfer-view: shutting down")
    finally:
        server.server_close()
    return server