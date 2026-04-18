from __future__ import annotations

from typing import Dict

from core.knowledge_db import knowledge_db
from core.memory.store import memory_store
from runtimes.memory.knowledge_service import knowledge_service
from runtimes.memory.profile_service import profile_service


class MemoryHealthService:
    """提供轻量健康检查，便于脚本回归与后续监控。"""

    def check(self) -> Dict[str, object]:
        preferences = profile_service.list_preferences()
        knowledge_count = knowledge_service.get_knowledge_count()
        graph_stats = knowledge_service.get_graph_stats()
        recent_logs = memory_store.get_recent_logs(days=1)

        with knowledge_db._conn() as conn:
            conn.execute("SELECT 1").fetchone()

        return {
            "ok": True,
            "preferenceScopes": list(preferences.keys()),
            "preferenceCount": sum(len(values) for values in preferences.values()),
            "knowledgeCount": knowledge_count,
            "graphEntities": graph_stats.get("entities", 0),
            "graphRelations": graph_stats.get("relations", 0),
            "recentLogsAvailable": bool(recent_logs.strip()),
            "memoryMap": memory_store.get_memory_map_health(),
        }


memory_health_service = MemoryHealthService()
