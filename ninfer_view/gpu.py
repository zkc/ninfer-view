"""nvidia-smi VRAM sampler for the dashboard (Linux/Ubuntu, NVIDIA).

While an instance is active the dashboard polls ``nvidia-smi`` (shipped with
the NVIDIA driver) every few seconds so the status header can show how much
VRAM is used and how much is left on the GPU. Stdlib only, like the rest of
the project: if nvidia-smi is missing (or the hardware isn't NVIDIA)
``sample_gpus()`` returns ``None`` and the UI simply hides the chip.

The binary path can be overridden with ``NINFER_VIEW_NVIDIA_SMI`` (tests
point it at a fake executable).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading

# One CSV row per GPU. The name is parsed from the MIDDLE of the line so a
# comma inside a GPU name can't shift the numeric columns.
QUERY = ("--query-gpu=index,name,memory.total,memory.used,memory.free,"
         "utilization.gpu --format=csv,noheader,nounits")
_TIMEOUT_S = 3.0


def nvidia_smi_binary() -> str | None:
    """Path to the nvidia-smi binary (env override wins), or None."""
    override = os.environ.get("NINFER_VIEW_NVIDIA_SMI")
    if override:
        return override
    return shutil.which("nvidia-smi")


def sample_gpus() -> list[dict] | None:
    """One nvidia-smi snapshot.

    Returns ``[{"index", "name", "total_mib", "used_mib", "free_mib",
    "util_pct"}, ...]`` (one entry per GPU) or ``None`` when nvidia-smi is
    unavailable, fails, or emits nothing parseable.
    """
    binary = nvidia_smi_binary()
    if not binary:
        return None
    try:
        proc = subprocess.run([binary, QUERY], capture_output=True,
                              text=True, timeout=_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    gpus = []
    for line in proc.stdout.splitlines():
        f = line.split(",")
        if len(f) < 6:
            continue
        try:
            idx = int(f[0].strip())
            total = int(f[-4].strip()); used = int(f[-3].strip())
            free = int(f[-2].strip()); util = int(f[-1].strip())
        except ValueError:
            continue
        # Name keeps its interior whitespace; only the outer edges are trimmed.
        gpus.append({"index": idx, "name": ",".join(f[1:-4]).strip(),
                     "total_mib": total, "used_mib": used,
                     "free_mib": free, "util_pct": util})
    return gpus or None


class GpuPoller:
    """Daemon-thread VRAM poller; same shape as HealthPoller.

    Samples every ``interval_s`` (default 5 s) and reports each sample to
    ``on_sample`` — a list of per-GPU dicts, or ``None`` when the sampler
    found no supported GPU. A raising callback cannot kill the poller.
    """

    def __init__(self, on_sample, interval_s: float = 5.0):
        self.on_sample = on_sample
        self.interval_s = interval_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self.alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="gpu-poller")
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # -- sampling -----------------------------------------------------------

    def check_now(self):
        """Synchronous single sample; usable before start() or from tests."""
        return sample_gpus()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.on_sample(self.check_now())
            except Exception:
                pass  # consumer errors must not kill the poller
            self._stop.wait(self.interval_s)
