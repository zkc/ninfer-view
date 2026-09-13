"""Launch profiles stored at <home>/profiles.json.

Default home: ~/.config/ninfer-view. Override with NINFER_VIEW_HOME
(e.g. NINFER_VIEW_HOME=/tmp/nv → /tmp/nv/profiles.json).

The built-in default profile takes its binary and artifact from
NINFER_SERVE_BINARY (absolute path to the ninfer-serve binary) and
NINFER_SERVE_ARTIFACT (absolute path to the .ninfer artifact). Unset →
the default profile seeds with no binary/artifact; set them in the
Config tab (or via the env vars) before launching.

A profile is a dict:
    {
      "id": "default",
      "name": "human label",
      "binary": "/abs/path/to/ninfer-serve",
      "artifact": "/abs/path/to/model.ninfer",
      "host": "127.0.0.1",
      "port": 8081,
      "extra_flags": ["--max-context", "16384", ...],
    }
The dashboard always injects --host/--port and --request-log-jsonl itself.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("NINFER_VIEW_HOME",
                                 str(Path.home() / ".config" / "ninfer-view")))
PROFILES_PATH = CONFIG_DIR / "profiles.json"

DEFAULT_BINARY = os.environ.get("NINFER_SERVE_BINARY", "")

DEFAULT_ARTIFACT = os.environ.get("NINFER_SERVE_ARTIFACT", "")

DEFAULT_PROFILE: dict = {
    "id": "default",
    "name": "qwen3.8-27b (port 8081)",
    "binary": DEFAULT_BINARY,
    "artifact": DEFAULT_ARTIFACT,
    "host": "127.0.0.1",
    "port": 8081,
    "extra_flags": [
        "--max-context", "262144",
        "--max-concurrency", "2",
        "--kv-dtype", "int8",
        "--spec", "mtp",
        "--draft-tokens", "3",
        "--lm-head-draft",
    ],
}


class Profiles:
    def __init__(self) -> None:
        self._profiles: dict[str, dict] = {}
        self.load()

    def load(self) -> None:
        data: dict = {}
        if PROFILES_PATH.exists():
            try:
                data = json.loads(PROFILES_PATH.read_text())
            except (OSError, json.JSONDecodeError):
                data = {}
        self._profiles = data.get("profiles", {}) or {}
        if DEFAULT_PROFILE["id"] not in self._profiles:
            self._profiles[DEFAULT_PROFILE["id"]] = dict(DEFAULT_PROFILE)

    def all(self) -> dict[str, dict]:
        return {k: dict(v) for k, v in self._profiles.items()}

    def default_binary(self) -> str:
        """Binary the default profile seeds from (NINFER_SERVE_BINARY)."""
        return str(DEFAULT_PROFILE.get("binary") or "")

    def get(self, profile_id: str = "default") -> dict | None:
        p = self._profiles.get(profile_id)
        return dict(p) if p is not None else None

    def save(self, profile: dict) -> None:
        if not profile.get("id"):
            raise ValueError("profile needs an 'id'")
        self._profiles[profile["id"]] = dict(profile)
        self._persist()

    def remove(self, profile_id: str) -> bool:
        if profile_id in self._profiles:
            del self._profiles[profile_id]
            self._persist()
            return True
        return False

    def _persist(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        PROFILES_PATH.write_text(
            json.dumps({"profiles": self._profiles}, indent=2) + "\n")