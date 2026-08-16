from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path


os.environ["LANGGRAPH_STRICT_MSGPACK"] = "true"
os.environ.setdefault("V8_CHECKPOINT_AES_KEY", "11" * 32)
_REAL_V8_AGENT_OS_HOME = (Path.home() / ".v8-agent-os").resolve(strict=False)
_PYTEST_V8_AGENT_OS_HOME = Path(tempfile.mkdtemp(prefix="v8-agent-os-pytest-")).resolve(strict=False)
if _PYTEST_V8_AGENT_OS_HOME == _REAL_V8_AGENT_OS_HOME:
    raise RuntimeError("pytest must never use the real V8 Agent OS home")
os.environ["V8_AGENT_OS_HOME"] = str(_PYTEST_V8_AGENT_OS_HOME)


ENGINE_ROOT = Path(__file__).resolve().parents[1]
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001
    shutil.rmtree(_PYTEST_V8_AGENT_OS_HOME, ignore_errors=True)
