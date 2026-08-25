"""CLI entry point.

    python3 -m ninfer_view [--host 127.0.0.1] [--port 18080]
"""

from __future__ import annotations

import argparse

from .httpd import serve
from .profiles import Profiles
from .state import Service


def main() -> None:
    ap = argparse.ArgumentParser(
        prog="ninfer-view",
        description="Configure, launch, and watch a ninfer-serve instance.")
    ap.add_argument("--host", default="127.0.0.1",
                    help="dashboard bind address (default 127.0.0.1)")
    ap.add_argument("--port", type=int, default=18080,
                    help="dashboard port (default 18080)")
    args = ap.parse_args()

    service = Service(Profiles())
    serve(service, args.host, args.port)


if __name__ == "__main__":
    main()