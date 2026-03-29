from __future__ import annotations

from pathlib import Path

"""Plugin host shared constants and path helpers.

语义拆分后，manifest/setup/capability/health/inbound/outbound 的具体逻辑
都已迁入对应模块；这里只保留不会引起职责混淆的公共常量。
"""

PLUGIN_LIFECYCLE_STATES = (
    "discovered",
    "installed",
    "onboarded",
    "active",
    "degraded",
    "incompatible",
    "disabled",
)

INSTALL_JOB_STATUSES = (
    "queued",
    "running",
    "needs_user_action",
    "completed",
    "failed",
)


def install_path_for(plugin_root: Path, plugin_id: str) -> Path:
    return plugin_root / "extensions" / plugin_id
