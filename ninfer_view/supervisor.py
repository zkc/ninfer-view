"""Child-process supervision for ninfer-serve.

Spawns the serve binary with a fresh per-run request JSONL, tees stderr to a
file and into the parser, and provides a clean SIGINT stop (ninfer-serve
handles SIGINT/SIGTERM by stopping the server and exiting 0).
"""

from __future__ import annotations

import os
import shlex
import signal
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from .console_parse import parse_line


def runs_root() -> Path:
    # Override the state root with NINFER_VIEW_HOME (runs/ underneath).
    root = Path(os.environ.get("NINFER_VIEW_HOME",
                               str(Path.home() / ".local" / "state" / "ninfer-view"))) / "runs"
    root.mkdir(parents=True, exist_ok=True)
    return root


class Supervisor:
    def __init__(self, service):
        self.service = service
        self.proc: subprocess.Popen | None = None
        self.run_dir: Path | None = None
        self._stopping = False
        self._stderr_file = None

    # -- lifecycle ---------------------------------------------------------

    def build_argv(self, profile: dict) -> list[str]:
        argv = [
            profile["binary"],
            profile["artifact"],
            "--host", str(profile.get("host", "127.0.0.1")),
            "--port", str(profile.get("port", 8080)),
            "--request-log-jsonl", str(self.run_dir / "requests.jsonl"),
        ]
        argv += list(profile.get("extra_flags", []))
        return argv

    def start(self, profile: dict) -> tuple[bool, str | None]:
        if self.alive():
            return False, "already running"
        self.run_dir = runs_root() / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        self.run_dir.mkdir(parents=True, exist_ok=True)
        argv = self.build_argv(profile)
        (self.run_dir / "argv.txt").write_text(
            " ".join(shlex.quote(a) for a in argv) + "\n")
        try:
            self._stderr_file = open(self.run_dir / "stderr.log",
                                     "a", encoding="utf-8", errors="replace")
            self.proc = subprocess.Popen(
                argv,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
        except OSError as e:
            self.proc = None
            return False, f"spawn failed: {e}"

        self._stopping = False
        threading.Thread(target=self._read_stderr, daemon=True).start()
        threading.Thread(target=self._watch, daemon=True).start()
        return True, str(self.run_dir)

    def stop(self, timeout: float = 15.0) -> tuple[bool, str | None]:
        proc = self.proc
        if proc is None or proc.poll() is not None:
            return False, "not running"
        self._stopping = True
        try:
            proc.send_signal(signal.SIGINT)
        except ProcessLookupError:
            return True, "already gone"
        try:
            proc.wait(timeout=timeout)
            return True, f"exit {proc.returncode}"
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                return False, "failed to kill"
            return True, "SIGINT ignored; sent SIGKILL"

    # -- helpers -----------------------------------------------------------

    @property
    def pid(self) -> int | None:
        return self.proc.pid if self.proc is not None else None

    @property
    def jsonl_path(self) -> str | None:
        """Per-run --request-log-jsonl file (created by the child)."""
        return str(self.run_dir / "requests.jsonl") if self.run_dir else None

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def _read_stderr(self) -> None:
        assert self.proc is not None
        for line in self.proc.stderr:
            if self._stderr_file is not None:
                self._stderr_file.write(line)
                self._stderr_file.flush()
            self.service.on_console_event(parse_line(line))
        if self._stderr_file is not None:
            self._stderr_file.close()

    def _watch(self) -> None:
        assert self.proc is not None
        code = self.proc.wait()
        self.service.on_child_exit(code, self._stopping)