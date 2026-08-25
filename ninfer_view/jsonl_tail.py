"""Append-follower for the per-run requests.jsonl (schema v10).

ninfer-serve opens the file (append mode) at startup and flushes after every
event, so a poll-and-read follower sees events with sub-second latency. A
fresh per-run file means we read from the beginning; ``seek_end`` is provided
for the later attach mode, where the user points us at a file that already
contains history.

Each complete, parseable record is handed to ``on_event`` as a dict. Lines
that are not valid JSON or not ``ninfer_serve_request_log`` records are
counted in ``malformed`` and skipped. A raising consumer can never kill the
tailer thread.
"""

from __future__ import annotations

import json
import os
import threading
import time

ARTIFACT_TYPE = "ninfer_serve_request_log"


class JsonlTailer:
    def __init__(self, path: str, on_event, poll_s: float = 0.15,
                 seek_end: bool = False):
        self.path = path
        self.on_event = on_event
        self.poll_s = poll_s
        self.seek_end = seek_end
        self.malformed = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="jsonl-tailer")
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # -- internals -----------------------------------------------------------

    def _open(self):
        # The server creates the file right after construction, but we can
        # lose the race at startup; wait politely until stop() is called.
        while not self._stop.is_set():
            try:
                f = open(self.path, "rb")
                break
            except FileNotFoundError:
                time.sleep(min(0.25, self.poll_s))
        else:
            return None
        if self.seek_end:
            f.seek(0, os.SEEK_END)
        return f

    def _run(self) -> None:
        f = self._open()
        if f is None:
            return
        buf = b""
        try:
            while not self._stop.is_set():
                chunk = f.read()
                if chunk:
                    buf += chunk
                    # Emit complete lines only; keep a trailing partial line.
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        if line.strip():
                            self._emit(line)
                else:
                    self._stop.wait(self.poll_s)
        finally:
            try:
                f.close()
            except OSError:
                pass

    def _emit(self, line: bytes) -> None:
        try:
            rec = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            self.malformed += 1
            return
        if not isinstance(rec, dict) or \
                rec.get("artifact_type") != ARTIFACT_TYPE:
            self.malformed += 1
            return
        try:
            self.on_event(rec)
        except Exception:
            pass  # consumer errors must not kill the tailer