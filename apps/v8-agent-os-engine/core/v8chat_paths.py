from __future__ import annotations

"""Legacy shim for the historical v8chat path module.

运行时 canonical 实现已经迁移到 `core.v8_agent_os_paths`。
本模块仅保留给仓库内仍未切完的旧 import 使用，避免一次性改名造成无意义破坏。
"""

from core.v8_agent_os_paths import *  # noqa: F401,F403


V8CHAT_HOME = V8_AGENT_OS_HOME
V8CHAT_CORE_PATH = V8_AGENT_OS_CORE_PATH
V8CHAT_TMP_PATH = V8_AGENT_OS_TMP_PATH
V8CHAT_TEST_TMP_PATH = V8_AGENT_OS_TEST_TMP_PATH


def ensure_v8chat_tmp_path(*, scope: str = "runtime"):
    return ensure_v8_agent_os_tmp_path(scope=scope)
