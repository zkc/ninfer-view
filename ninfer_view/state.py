"""Event bus + service state machine for ninfer-view.

The Service is the single object the HTTP layer talks to. It owns the
supervisor (child process), the JSONL tailer, the state machine, and an
EventBus that fans out events to SSE subscribers and keeps a ring buffer of
JSONL log events for backfill.

Log stream: the dashboard's log stream is fed *only* by the child's
``--request-log-jsonl`` file (schema v10). stderr is still parsed, but solely
to drive the state machine and load-progress bar; stderr lines never enter
the SSE log stream or the log backfill buffer.

State flow (supervised, spawned by us):
    stopped -> starting -> loading -> warming -> running
              (spawn)    (model     (warmup   (listening
                          load)      line)     line)
    (stderr is the legacy free-form format or the current structured
    "startup phase=... status=..." format; both map to the same lifecycle
    events. A structured "status=failed" line records the failure reason,
    which the crashed state reports.)
    any -> stopping (SIGINT sent) -> stopped (exit 0) | crashed (exit != 0)

Attach mode (external instance we do not own):
    stopped/crashed -> attached -> (detach) -> stopped
An attached instance is observed through its JSONL file (live-only,
seek_end) plus a /health poller. No child, no stderr, no progress bar.
"""

from __future__ import annotations

import itertools
import os
import queue
import threading
import time

# schema-v10 event types; these are the kinds that populate the log stream.
LOG_KINDS = ("server_start", "request_start", "request_rejected",
             "request_done", "request_error", "throughput")


class EventBus:
    """Fan-out to SSE subscribers + ring buffer of JSONL log events."""

    def __init__(self, log_limit: int = 4000):
        self._lock = threading.Lock()
        self._subs: list[queue.Queue] = []
        self._logs: list[dict] = []
        self._log_limit = log_limit
        self._seq = itertools.count(1)

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=500)
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def publish(self, event: dict) -> None:
        event = dict(event)
        event["seq"] = next(self._seq)
        if event.get("kind") in LOG_KINDS:
            with self._lock:
                self._logs.append(event)
                if len(self._logs) > self._log_limit:
                    del self._logs[: len(self._logs) - self._log_limit]
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(event)
            except queue.Full:
                pass  # slow client; drop rather than block producers

    def recent_logs(self, limit: int = 500) -> list[dict]:
        with self._lock:
            return list(self._logs[-limit:])


class Service:
    def __init__(self, profiles):
        self.profiles = profiles
        self.bus = EventBus()
        self.lock = threading.Lock()
        # Held across the entire start/stop *action* (check + spawn / check +
        # signal + wait). ThreadingHTTPServer runs each request in its own
        # thread, so without this, two fast POST /api/start (the UI and the
        # tray in M3 can do exactly this) both pass the state check and spawn
        # two children — the loser fails to bind and crashes.
        self.action_lock = threading.Lock()
        self.state = "stopped"
        self.model_id: str | None = None
        self.endpoint: str | None = None
        self.progress: dict | None = None
        self.started_at: float | None = None   # spawn time (epoch)
        self.running_at: float | None = None   # listening time (epoch)
        self.exit_code: int | None = None
        self.error: str | None = None
        self.run_dir: str | None = None
        self.pid: int | None = None
        self.supervisor = None
        self.jsonl_tailer = None
        self.health_poller = None
        self.health: str | None = None          # "up" | "down" | None (unknown)
        self.attached = False                    # observing an external instance
        self.jsonl_path: str | None = None       # JSONL being tailed (any mode)

    # -- actions ---------------------------------------------------------

    def start(self, profile_id: str = "default") -> tuple[bool, str | None]:
        profile = self.profiles.get(profile_id)
        if profile is None:
            return False, f"unknown profile {profile_id!r}"

        # Serialize the whole action; the state check is re-done under the
        # action lock so a concurrent start that lost the race sees the new
        # state and is rejected instead of spawning a second child.
        with self.action_lock:
            with self.lock:
                if self.state != "stopped" and self.state != "crashed":
                    return False, f"already {self.state}"

            from .supervisor import Supervisor  # local import avoids a cycle

            self.model_id = None
            self.endpoint = None
            self.progress = None
            self.running_at = None
            self.exit_code = None
            self.error = None
            self.pid = None
            self.health = None
            self.attached = False
            self.supervisor = Supervisor(self)
            self.jsonl_tailer = None
            ok, msg = self.supervisor.start(profile)
            if not ok:
                self.supervisor = None
                return False, msg
            self.started_at = time.time()
            self.run_dir = str(self.supervisor.run_dir)
            self.pid = self.supervisor.pid
            self.jsonl_path = str(self.supervisor.jsonl_path)
            from .jsonl_tail import JsonlTailer
            self.jsonl_tailer = JsonlTailer(self.supervisor.jsonl_path,
                                            self._on_jsonl_event)
            self.jsonl_tailer.start()
            self._set_state("starting")
            return True, None

    def stop(self, timeout: float = 15.0) -> tuple[bool, str | None]:
        # Serialized against start(): a stop in flight blocks a start, which
        # then re-checks the state (still non-stopped until the child exits)
        # and is rejected. Holding the lock during the (bounded) wait is fine
        # for a localhost tool.
        with self.action_lock:
            sup = self.supervisor
            if sup is None or not sup.alive():
                return False, "not running"
            return sup.stop(timeout=timeout)

    # -- attach mode (external instance) ---------------------------------

    def attach(self, host: str, port: int,
               jsonl_path: str) -> tuple[bool, str | None]:
        """Observe an instance started outside ninfer-view.

        Tails its requests.jsonl from the end (live-only) and polls its
        /health endpoint. Requires a free slot: no supervised child and no
        other attached instance.
        """
        jsonl_path = os.path.expanduser(str(jsonl_path))
        if not jsonl_path:
            return False, "jsonl_path is required"
        try:
            port = int(port)
        except (TypeError, ValueError):
            return False, "invalid port"
        if not os.path.isfile(jsonl_path):
            return False, f"jsonl file not found: {jsonl_path}"
        with self.action_lock:
            with self.lock:
                if self.state not in ("stopped", "crashed"):
                    return False, f"already {self.state}"
            self._stop_health_poller()
            self.model_id = None
            self.progress = None
            self.exit_code = None
            self.error = None
            self.pid = None
            self.run_dir = None
            self.running_at = None
            self.started_at = time.time()
            self.health = "down"  # updated by the first poll within ~2 s
            self.attached = True
            self.endpoint = f"http://{host}:{port}"
            self.jsonl_path = jsonl_path
            from .health import HealthPoller
            from .jsonl_tail import JsonlTailer
            self.jsonl_tailer = JsonlTailer(jsonl_path, self._on_jsonl_event,
                                            seek_end=True)
            self.jsonl_tailer.start()
            self.health_poller = HealthPoller(
                f"{self.endpoint}/health", self._on_health)
            self.health_poller.start()
            self._set_state("attached")
            return True, None

    def detach(self) -> tuple[bool, str | None]:
        with self.action_lock:
            if not self.attached:
                return False, "not attached"
            self._stop_health_poller()
            if self.jsonl_tailer is not None:
                self.jsonl_tailer.stop()
                self.jsonl_tailer = None
            self.attached = False
            self.endpoint = None
            self.jsonl_path = None
            self.health = None
            self.model_id = None
            self._set_state("stopped")
            return True, None

    def _stop_health_poller(self) -> None:
        if self.health_poller is not None:
            self.health_poller.stop()
            self.health_poller = None

    # -- callbacks from the supervisor -----------------------------------

    def on_console_event(self, ev: dict) -> None:
        """stderr-driven state/progress only — never enters the log stream."""
        kind = ev["kind"]
        if kind == "progress":
            self.progress = ev["progress"]
            if self.state == "starting":
                self._set_state("loading")
            else:
                # No state change, but push a snapshot so the progress bar
                # advances between the 10-second progress lines.
                self.bus.publish({"kind": "state", **self.snapshot()})
        elif kind == "lifecycle":
            phase = ev.get("phase")
            if phase == "loading" and self.state == "starting":
                self._set_state("loading")
            elif phase == "warming" and self.state in ("starting", "loading"):
                self._set_state("warming")
            elif phase == "listening":
                self.model_id = ev.get("model_id")
                self.endpoint = ev.get("endpoint")
                self.running_at = time.time()
                # Ground-truth liveness from here on: /health answers only
                # when the server actually accepts requests.
                from .health import HealthPoller
                if self.health_poller is None:
                    self.health_poller = HealthPoller(
                        f"{self.endpoint}/health", self._on_health)
                    self.health_poller.start()
                self._set_state("running")
            elif phase == "failed":
                # Current-format `status=failed` line: keep the reason so the
                # crashed state can show it. The child exits non-zero
                # shortly after; on_child_exit preserves this detail.
                with self.lock:
                    changed = (self.state not in ("stopped", "crashed"))
                    if changed:
                        self.error = ev.get("detail") or "startup failed"
                if changed:
                    self.bus.publish({"kind": "state", **self.snapshot()})

    def _on_health(self, ok: bool) -> None:
        """Health poller callback (any mode); publishes only on change."""
        value = "up" if ok else "down"
        with self.lock:
            if self.state == "stopped":
                return
            if self.health == value:
                return
            self.health = value
        self.bus.publish({"kind": "state", **self.snapshot()})

    def _on_jsonl_event(self, rec: dict) -> None:
        """One parsed schema-v10 record from the tailer -> log stream."""
        self.bus.publish({"kind": rec.get("event", "unknown"), "record": rec})

    def on_child_exit(self, code: int, stopping: bool) -> None:
        if self.jsonl_tailer is not None:
            self.jsonl_tailer.stop()
            self.jsonl_tailer = None
        self._stop_health_poller()
        self.pid = None
        if stopping or code == 0:
            self._set_state("stopped", exit_code=code)
        else:
            # Prefer the structured failure reason captured from the
            # child's own stderr ("status=failed" line); fall back to the
            # bare exit code.
            with self.lock:
                if not self.error:
                    self.error = f"process exited with code {code}"
            self._set_state("crashed", exit_code=code)

    # -- internals ---------------------------------------------------------

    def _set_state(self, state: str, **kw) -> None:
        with self.lock:
            self.state = state
            for k, v in kw.items():
                setattr(self, k, v)
        self.bus.publish({"kind": "state", **self.snapshot()})

    def snapshot(self) -> dict:
        with self.lock:
            now = time.time()
            return {
                "state": self.state,
                "model_id": self.model_id,
                "endpoint": self.endpoint,
                "progress": self.progress,
                "started_at": self.started_at,
                "running_at": self.running_at,
                "uptime_s": (round(now - self.running_at, 1)
                             if self.state == "running" and self.running_at else None),
                "exit_code": self.exit_code,
                "error": self.error,
                "run_dir": self.run_dir,
                "pid": self.pid,
                "health": self.health,
                "attached": self.attached,
                "jsonl_path": self.jsonl_path,
            }