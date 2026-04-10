from __future__ import annotations

from typing import Dict, List, Optional

from core.memory.store import memory_store


class RecallService:
    """封装统一混合检索能力。"""

    def unified_recall(
        self,
        *,
        query: str,
        limit: int = 5,
        scope: Optional[str] = None,
        scopes: Optional[List[str]] = None,
    ) -> List[Dict]:
        return memory_store.unified_recall(
            query=query,
            limit=limit,
            scope=scope,
            scopes=scopes,
        )

    def preview_unified_recall(
        self,
        *,
        query: str,
        limit: int = 5,
        scope: Optional[str] = None,
        scopes: Optional[List[str]] = None,
    ) -> Dict:
        return memory_store.preview_unified_recall(
            query=query,
            limit=limit,
            scope=scope,
            scopes=scopes,
        )


recall_service = RecallService()
