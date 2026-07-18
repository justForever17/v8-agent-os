from __future__ import annotations

import os
import sys
from pathlib import Path


os.environ["LANGGRAPH_STRICT_MSGPACK"] = "true"
os.environ.setdefault("V8_CHECKPOINT_AES_KEY", "11" * 32)


ENGINE_ROOT = Path(__file__).resolve().parents[1]
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))
