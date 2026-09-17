"""Tests for the nvidia-smi VRAM sampler (gpu.py).

No GPU required: a fake nvidia-smi executable (a small script) is written to
a temp dir and selected via NINFER_VIEW_NVIDIA_SMI, so the real binary and
real hardware are never needed.

Run:  python3 tests/test_gpu.py
"""

import os
import stat
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ninfer_view.gpu import GpuPoller, nvidia_smi_binary, sample_gpus


def write_fake_nvidia_smi(d: Path, stdout: str = "", code: int = 0) -> str:
    """Write an executable fake nvidia-smi printing `stdout`, exiting `code`.

    The fake validates its argv like the real binary: it expects exactly
    ``--query-gpu=<fields>`` and ``--format=csv,noheader,nounits`` as SEPARATE
    arguments and fails with exit code 2 otherwise — the same failure mode
    the real nvidia-smi produces when the two are accidentally merged into
    one argv entry.
    """
    p = d / "nvidia-smi"
    p.write_text("#!/usr/bin/env python3\n"
                 "import sys\n"
                 "a = sys.argv[1:]\n"
                 "if not (len(a) == 2 and a[0].startswith('--query-gpu=')\n"
                 "        and '--format' not in a[0]\n"
                 "        and a[1] == '--format=csv,noheader,nounits'):\n"
                 "    sys.stdout.write('invalid arguments: %r\\n' % (a,))\n"
                 "    sys.exit(2)\n"
                 f"sys.stdout.write({stdout!r})\n"
                 f"sys.exit({code})\n")
    p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return str(p)


def with_fake(fake: str):
    """Context manager: point NINFER_VIEW_NVIDIA_SMI at `fake`."""
    import contextlib
    saved = os.environ.get("NINFER_VIEW_NVIDIA_SMI")
    os.environ["NINFER_VIEW_NVIDIA_SMI"] = fake

    @contextlib.contextmanager
    def _ctx():
        try:
            yield
        finally:
            if saved is None:
                os.environ.pop("NINFER_VIEW_NVIDIA_SMI", None)
            else:
                os.environ["NINFER_VIEW_NVIDIA_SMI"] = saved
    return _ctx()


def test_sample_single_gpu():
    with tempfile.TemporaryDirectory() as td:
        fake = write_fake_nvidia_smi(Path(td),
                                     "0, NVIDIA H100 80GB HBM3, 81559, 20480, 61079, 87\n")
        with with_fake(fake):
            g = sample_gpus()
            assert g == [{"index": 0, "name": "NVIDIA H100 80GB HBM3",
                          "total_mib": 81559, "used_mib": 20480,
                          "free_mib": 61079, "util_pct": 87}], g
    print("ok   test_sample_single_gpu")


def test_sample_multi_gpu_and_comma_in_name():
    # a comma inside the name must not shift the numeric columns
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        fake = write_fake_nvidia_smi(d,
                                     "0, Foo, Bar Corp, 24564, 10000, 14564, 12\n"
                                     "1, Baz, 19660, 500, 19160, 0\n")
        with with_fake(fake):
            g = sample_gpus()
            assert g is not None and len(g) == 2, g
            assert g[0]["name"] == "Foo, Bar Corp", g[0]
            assert g[0]["used_mib"] == 10000 and g[0]["free_mib"] == 14564
            assert g[1]["index"] == 1 and g[1]["util_pct"] == 0
    print("ok   test_sample_multi_gpu_and_comma_in_name")


def test_sample_none_when_binary_missing_or_fails():
    # missing binary -> FileNotFoundError -> None
    with with_fake("/nonexistent/nvidia-smi"):
        assert sample_gpus() is None
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        fake = write_fake_nvidia_smi(d, "boom\n", code=1)
        with with_fake(fake):
            assert sample_gpus() is None          # non-zero exit
        fake = write_fake_nvidia_smi(d, "not a csv line\n")
        with with_fake(fake):
            assert sample_gpus() is None          # unparseable output
    print("ok   test_sample_none_when_binary_missing_or_fails")


def test_merged_query_arg_rejected():
    """Regression: the query and format must be SEPARATE argv entries.

    subprocess.run does no shell word-splitting, so joining them with a
    space passes ONE argument; nvidia-smi then treats everything after
    ``--query-gpu=`` (including ``--format=csv``) as a field list and
    exits 2 with 'Field "utilization.gpu --format=csv" is not a valid
    field to query'. The fake reproduces exactly this failure mode.
    """
    import subprocess
    with tempfile.TemporaryDirectory() as td:
        fake = write_fake_nvidia_smi(Path(td), "0, G, 100, 50, 50, 1\n")
        proc = subprocess.run(
            [fake, "--query-gpu=index --format=csv,noheader,nounits"],
            capture_output=True, text=True)
        assert proc.returncode == 2, proc
        assert "invalid arguments" in proc.stdout, proc.stdout
    print("ok   test_merged_query_arg_rejected")


def test_binary_resolution():
    with tempfile.TemporaryDirectory() as td:
        fake = write_fake_nvidia_smi(Path(td))
        with with_fake(fake):
            assert nvidia_smi_binary() == fake    # override wins
    # no override: PATH lookup (None on machines without the driver)
    saved = os.environ.get("PATH")
    os.environ["PATH"] = "/nonexistent"
    try:
        assert nvidia_smi_binary() is None
    finally:
        if saved is None:
            os.environ.pop("PATH", None)
        else:
            os.environ["PATH"] = saved
    print("ok   test_binary_resolution")


def test_poller_lifecycle():
    with tempfile.TemporaryDirectory() as td:
        fake = write_fake_nvidia_smi(Path(td), "0, Test GPU, 8192, 4096, 4096, 50\n")
        with with_fake(fake):
            got = []
            p = GpuPoller(got.append, interval_s=0.05)
            assert p.check_now()[0]["used_mib"] == 4096
            p.start()
            assert p.alive()
            time.sleep(0.3)
            p.stop()
        assert not p.alive()
        assert len(got) >= 2 and all(s and s[0]["name"] == "Test GPU"
                                     for s in got), got[:3]
    print("ok   test_poller_lifecycle (%d samples)" % len(got))


def test_poller_survives_callback_errors():
    p = GpuPoller(lambda s: 1 / 0, interval_s=0.05)
    p.start()
    time.sleep(0.3)
    assert p.alive(), "poller must survive a raising callback"
    p.stop()
    print("ok   test_poller_survives_callback_errors")


def test_service_wiring():
    """Service owns the poller lifecycle; sample lands in snapshot + bus."""
    from ninfer_view.state import Service
    svc = Service(profiles=None)
    assert "gpu" in svc.snapshot() and svc.snapshot()["gpu"] is None
    with tempfile.TemporaryDirectory() as td:
        fake = write_fake_nvidia_smi(Path(td), "0, Test GPU, 8192, 3072, 5120, 9\n")
        with with_fake(fake):
            svc._start_gpu_poller()
            assert svc.gpu_poller is not None and svc.gpu_poller.alive()
            deadline = time.time() + 3.0
            while svc.gpu is None and time.time() < deadline:
                time.sleep(0.05)
            assert svc.gpu and svc.gpu[0]["used_mib"] == 3072, svc.gpu
            assert svc.snapshot()["gpu"] == svc.gpu
            # bus fan-out: a fresh sample publishes a gpu event
            q = svc.bus.subscribe()
            svc._on_gpu([{"index": 0}])
            ev = q.get(timeout=1.0)
            assert ev["kind"] == "gpu" and ev["gpus"] == [{"index": 0}], ev
            svc.bus.unsubscribe(q)
            svc._stop_gpu_poller()
    assert svc.gpu_poller is None
    print("ok   test_service_wiring")


if __name__ == "__main__":
    test_sample_single_gpu()
    test_sample_multi_gpu_and_comma_in_name()
    test_sample_none_when_binary_missing_or_fails()
    test_merged_query_arg_rejected()
    test_binary_resolution()
    test_poller_lifecycle()
    test_poller_survives_callback_errors()
    test_service_wiring()
    print("ALL GPU TESTS PASS")
