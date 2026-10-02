"""State-machine tests: /health-driven startup + supervised lifecycle.

The `running` transition follows the /health poller (liveness ground
truth), so these tests prove:
  * an "up" promotes a supervised instance out of its startup states and
    sets running_at; a "down" never does; non-startup states are
    unaffected (attach mode keeps its own state);
  * the full supervised lifecycle with the fake binary: spawn ->
    starting (health poller already running) -> running via the
    pretty-log listening line / health up -> clean SIGINT stop.

Run:  python3 tests/test_state.py
"""

import os
import socket
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
os.environ["NINFER_VIEW_HOME"] = tempfile.mkdtemp(prefix="nv-state-test-")
sys.path.insert(0, str(REPO))

from ninfer_view.state import Service, START_STATES  # noqa: E402


class _Profiles:
    """Minimal profile store for unit tests (no disk)."""

    def __init__(self, **kw):
        self._p = dict(kw)

    def get(self, profile_id="default"):
        return dict(self._p) if profile_id in self._p else None


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_health_up_promotes_startup_states():
    svc = Service(_Profiles())
    for st in START_STATES:
        svc = Service(_Profiles())
        svc._set_state(st)
        svc._on_health(False)
        assert svc.state == st and svc.health == "down", (st, svc.state)
        svc._on_health(True)
        assert svc.state == "running", (st, svc.state)
        assert svc.running_at is not None
    print("ok   test_health_up_promotes_startup_states")


def test_health_only_promotes_once():
    svc = Service(_Profiles())
    svc._set_state("starting")
    svc._on_health(True)
    assert svc.state == "running"
    t0 = svc.running_at
    time.sleep(0.01)
    svc._on_health(True)  # still up, already running: no re-promote
    assert svc.state == "running" and svc.running_at == t0
    print("ok   test_health_only_promotes_once")


def test_health_does_not_touch_other_states():
    svc = Service(_Profiles())
    svc._set_state("attached")
    svc._on_health(True)
    assert svc.state == "attached" and svc.health == "up"
    # stopped: ignored entirely
    svc2 = Service(_Profiles())
    svc2._on_health(True)
    assert svc2.state == "stopped" and svc2.health is None
    print("ok   test_health_does_not_touch_other_states")


def test_listening_line_still_promotes_and_poller_guard():
    svc = Service(_Profiles())
    svc._set_state("warming")
    svc.on_console_event({"kind": "lifecycle", "phase": "listening",
                          "model_id": "m", "endpoint": "http://127.0.0.1:1",
                          "auth": "disabled"})
    try:
        assert svc.state == "running" and svc.model_id == "m"
        assert svc.health_poller is not None  # defensive start
    finally:
        svc._stop_health_poller()
    print("ok   test_listening_line_still_promotes_and_poller_guard")


def test_server_start_jsonl_backfills_model_id():
    svc = Service(_Profiles())
    svc._set_state("loading")
    svc._on_jsonl_event({"event": "server_start",
                         "server": {"public_model_id": "qwen3.8-27b"}})
    assert svc.model_id == "qwen3.8-27b"
    svc._on_jsonl_event({"event": "server_start",
                         "server": {"public_model_id": "other"}})
    assert svc.model_id == "qwen3.8-27b"  # first non-empty wins
    print("ok   test_server_start_jsonl_backfills_model_id")


def test_supervised_lifecycle_reaches_running():
    """Spawn the fake binary through the real Service and watch it reach
    `running` via the latest pretty log + /health, then stop it cleanly."""
    import ninfer_view.profiles as profiles_mod
    from ninfer_view.state import Service as _Svc  # re-check import identity

    profiles = profiles_mod.Profiles()
    port = free_port()
    profiles.save({
        "id": "fake", "name": "fake",
        # binary=python3, artifact=<fake script>: build_argv yields
        # "python3 <script> --host ... --port ... --request-log-jsonl ...",
        # which needs the fixture to be executable.
        "binary": sys.executable,
        "artifact": str(REPO / "tests" / "fake_ninfer_serve.py"),
        "host": "127.0.0.1", "port": port, "extra_flags": [],
    })
    svc = _Svc(profiles)
    ok, err = svc.start("fake")
    assert ok, f"start failed: {err}"
    try:
        # The health poller must be running while the model loads.
        assert svc.health_poller is not None and svc.health_poller.alive()
        assert svc.endpoint == f"http://127.0.0.1:{port}"
        assert svc.state in ("starting", "loading")

        deadline = time.time() + 30
        while time.time() < deadline and svc.state != "running":
            time.sleep(0.2)
        assert svc.state == "running", \
            f"state stuck at {svc.state!r} (health={svc.health})"
        assert svc.health == "up"
        assert svc.running_at is not None
        assert svc.model_id == "fake-model-1"
        # The pretty log also drove the progress bar.
        assert svc.progress is not None and \
            svc.progress["percent"] == 100.0, svc.progress

        ok, msg = svc.stop()
        assert ok, f"stop failed: {msg}"
        deadline = time.time() + 20
        while time.time() < deadline and svc.state != "stopped":
            time.sleep(0.2)
        assert svc.state == "stopped", svc.state
        assert svc.health_poller is None
    finally:
        if svc.supervisor is not None and svc.supervisor.alive():
            svc.supervisor.proc.kill()
        svc._stop_health_poller()
    print("ok   test_supervised_lifecycle_reaches_running")


if __name__ == "__main__":
    test_health_up_promotes_startup_states()
    test_health_only_promotes_once()
    test_health_does_not_touch_other_states()
    test_listening_line_still_promotes_and_poller_guard()
    test_server_start_jsonl_backfills_model_id()
    test_supervised_lifecycle_reaches_running()
    print("ALL STATE TESTS PASS")
