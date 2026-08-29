"""CLI entry point.

    python3 -m ninfer_view [--host 127.0.0.1] [--port 18080] [--load]
"""

from __future__ import annotations

import argparse
import sys

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
    ap.add_argument("--load", action="store_true",
                    help="launch the default profile (load the model) at "
                         "startup")
    args = ap.parse_args()

    service = Service(Profiles())
    if args.load:
        ok, err = service.start("default")
        if ok:
            print("loading model (default profile)")
        else:
            print(f"--load: failed to start model: {err}", file=sys.stderr)
    serve(service, args.host, args.port)


if __name__ == "__main__":
    main()