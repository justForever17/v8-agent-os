from __future__ import annotations

import sys
from pathlib import Path


def _add_engine_root_to_path() -> None:
    engine_root = Path(__file__).resolve().parents[1]
    root = str(engine_root)
    if root not in sys.path:
        sys.path.insert(0, root)


def main() -> int:
    _add_engine_root_to_path()
    from acp_bridge.stdio_server import main as stdio_main

    return int(stdio_main() or 0)


if __name__ == "__main__":
    raise SystemExit(main())
