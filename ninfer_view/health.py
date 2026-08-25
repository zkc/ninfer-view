"""Liveness poller for a ninfer-serve /health endpoint.

ninfer-serve answers GET /health with {"status":"ok"} and no auth. The poller
runs one daemon thread at a fixed interval and reports each check's result to
a callback; connection errors (port bound but not listening yet, refused,
timeout) simply read as "down". A raising callback cannot kill the poller.
"""

from __future__ import annotations

import threading
import urllib.request


class HealthPoller:
    def __init__(self, url: str, on_result, interval_s: float = 2.0,
                 timeout_s: float = 1.5):
        self.url = url
        self.on_result = on_result
        self.interval_s = interval_s
        self.timeout_s = timeout_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self.alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="health-poller")
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # -- checks -------------------------------------------------------------

    def check_now(self) -> bool:
        """Synchronous single check; usable before start() or from tests."""
        try:
            with urllib.request.urlopen(self.url,
                                        timeout=self.timeout_s) as resp:
                return 200 <= resp.status < 300
        except Exception:
            return False

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.on_result(self.check_now())
            except Exception:
                pass  # consumer errors must not kill the poller
            self._stop.wait(self.interval_s)