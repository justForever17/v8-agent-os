from __future__ import annotations

import sys
from pathlib import Path


ENGINE_ROOT = Path(__file__).resolve().parents[1]
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

from acp_bridge.stdio_server import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
