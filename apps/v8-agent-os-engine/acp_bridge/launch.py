from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any


ACP_PROTOCOL_VERSION = 1


def source_checkout_command() -> str:
    return f"{sys.executable} apps/v8-agent-os-engine/scripts/v8os_acp_agent.py"


def build_launch_manifest() -> dict[str, Any]:
    admin_url = os.environ.get("V8OS_ADMIN_URL") or "http://127.0.0.1:9528"
    engine_url = os.environ.get("V8OS_ENGINE_URL") or "http://127.0.0.1:9530"
    token_source = "V8OS_CLIENT_TOKEN" if os.environ.get("V8OS_CLIENT_TOKEN") else (
        "V8OS_ADMIN_TOKEN" if os.environ.get("V8OS_ADMIN_TOKEN") else None
    )
    return {
        "command": "v8os acp",
        "sourceCheckoutCommand": source_checkout_command(),
        "transport": "stdio",
        "adminUrl": admin_url,
        "engineUrl": engine_url,
        "requiredEnv": ["V8OS_ADMIN_URL", "V8OS_CLIENT_TOKEN"],
        "fallbackEnv": ["V8OS_ENGINE_URL", "V8OS_ADMIN_TOKEN"],
        "tokenSource": token_source,
        "cwdHint": str(Path.cwd()),
        "failureTips": [
            "若命令不存在，请先使用 sourceCheckoutCommand，或确认桌面/CLI 安装包已加入 PATH。",
            "若返回 401/403，请从 Admin 的连接卡复制 token 到 V8OS_CLIENT_TOKEN。",
            "若连接失败，请确认 Admin 9528 与 Engine 9530 正在运行。",
        ],
    }
