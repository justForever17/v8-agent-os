from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any


ACP_PROTOCOL_VERSION = 1


def source_checkout_command() -> str:
    script = Path(__file__).resolve().parents[1] / "scripts" / "v8os_acp_agent.py"
    return f'"{sys.executable}" "{script}"'


def build_launch_manifest() -> dict[str, Any]:
    admin_url = os.environ.get("V8OS_ADMIN_URL") or "http://127.0.0.1:9528"
    return {
        "command": "v8os acp",
        "sourceCheckoutCommand": source_checkout_command(),
        "transport": "stdio",
        "adminUrl": admin_url,
        "requiredEnv": [],
        "optionalEnv": ["V8OS_ADMIN_URL"],
        "authentication": "Local CLI session issued in memory by the loopback Admin; no credential required in client configuration.",
        "cwdHint": str(Path.cwd()),
        "failureTips": [
            "若命令不存在，请先使用 sourceCheckoutCommand，或确认桌面/CLI 安装包已加入 PATH。",
            "若返回 401/403，请确认本机 Admin 已初始化 Owner，并重新启动 ACP 会话。",
            "若连接失败，请确认 Admin 9528 与 Engine 9530 正在运行。",
        ],
    }
