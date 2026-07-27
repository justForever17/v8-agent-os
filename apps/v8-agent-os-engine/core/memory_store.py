"""
分层记忆存储引擎 — MEMORY.md scope-based 偏好 + 知识分区 + 时序日志

核心设计:
- MEMORY.md: scope 分区偏好（人类可读可编辑）
- knowledge/areas/{scope}/items.json: 分区知识库
- daily/{YYYY}/{MM}/{YYYY-MM-DD}.md: 时序日志
- .index/: 向量索引（ChromaDB）

参考: adaptive-agent-mcp 的 MemoryParser + KnowledgeRouter
"""

import re
import json
import uuid
import logging
import time
import hashlib
from pathlib import Path
from datetime import date, datetime, timedelta, timezone
from typing import Dict, Any, Optional, List, Tuple

from core.memory_canonicalization import canonicalize_preference_key
from core.supervisor_identity import non_identity_preferences, render_supervisor_identity_context
from core.v8_agent_os_paths import V8_AGENT_OS_HOME

logger = logging.getLogger("v8_agent_os.memory")
_VECTOR_SYNC_WARNING_INTERVAL_SECONDS = 300.0
_vector_sync_warning_last_at: dict[str, float] = {}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _classify_vector_sync_error(exc: Exception) -> str:
    text = f"{exc.__class__.__name__}: {exc}".lower()
    transient_markers = (
        "remotedisconnected",
        "connectionreseterror",
        "connection aborted",
        "connection reset",
        "read timed out",
        "timeout",
        "temporarily unavailable",
    )
    if any(marker in text for marker in transient_markers):
        return "transient_connection"
    return "vector_sync_error"

# === 配置 ===
CONFIG_DIR = V8_AGENT_OS_HOME
MEMORY_ROOT = CONFIG_DIR / "memory"

# === MEMORY.md 模板 ===
MEMORY_TEMPLATE_V2 = """---
type: user_preferences
version: "2.0"
last_updated: "{date}"
---

[global]
# 全局画像 — 长期身份、表达与背景
assistant_name: Please help me come up with a name.
user_call_name: master
relationship_tone: Warm and friendly
"""

# === 正则 ===
SCOPE_PATTERN = re.compile(r'^\[([^\]]+)\]$', re.MULTILINE)
KV_PATTERN = re.compile(r'^([a-zA-Z_][a-zA-Z0-9_]*)\s*:\s*(.+)$', re.MULTILINE)
_SPECIFIC_SCOPE_PREFIXES = ("project:", "channel:", "workspace:", "external_api_thread:")
_DAY_FILENAME_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}\.md$")
_DAILY_ENTRY_MAX_CHARS_DEFAULT = 6000
_DAILY_ENTRY_SUMMARY_EXCERPT_CHARS_DEFAULT = 900
_SUMMARY_CHILD_EXCERPT_CHARS_DEFAULT = 1200
_PERIODIC_SUMMARY_GLOBAL_POLICY = "global_only_v1"
_PERIODIC_SUMMARY_LEGACY_POLICY = "legacy_unverified"

GLOBAL_PROFILE_DEFAULTS: Dict[str, str] = {
    "preferred_language": "",
    "user_call_name": "master",
    "assistant_name": "Please help me come up with a name.",
    "system_identity_reference": "",
    "assistant_persona": "",
    "relationship_tone": "Warm and friendly",
    "emotional_boundary": "",
    "response_language_style": "",
    "format_preference": "",
    "primary_system_context": "",
    "technical_stack_bias": "",
    "long_term_goals": "",
}
GLOBAL_PROFILE_KEYS = tuple(GLOBAL_PROFILE_DEFAULTS.keys())
GLOBAL_PROFILE_ALIASES: Dict[str, str] = {
    "language": "preferred_language",
    "language_preference": "preferred_language",
    "preferred_response_language": "preferred_language",
    "response_language": "preferred_language",
    "reply_language": "preferred_language",
    "system_name": "system_identity_reference",
    "expression_style": "response_language_style",
    "tone_preference": "relationship_tone",
    "response_tone_preference": "relationship_tone",
}
REMOVED_GLOBAL_KEYS = {
    "voice_interaction_protocol",
    "planning_preference",
    "implementation_preference",
    "verification_preference",
    "autonomy_level",
    "approval_preference",
    "safety_sensitivity",
}
IGNORED_MEMORY_SECTIONS = {"execution hints", "execution_hints"}
VOICE_INTERACTION_EXECUTION_HINT = (
    "Voice interaction protocol: when an upbeat voice reply is appropriate and the active channel "
    "supports V8OS voice delivery, wrap pure spoken text in <voice>...</voice>; keep commands, code, "
    "paths, tool output, and diagnostics outside voice tags."
)


class MemoryStore:
    """
    分层记忆存储引擎。
    
    Layer 1: 用户画像 (MEMORY.md, scope-based KV)
    Layer 2: 知识库 (knowledge/areas/{scope}/items.json)
    Layer 3: 时序日志 (daily/YYYY/MM/YYYY-MM-DD.md)
    """
    
    def __init__(self):
        self.memory_path = MEMORY_ROOT / "MEMORY.md"
        self._preferences_cache: Optional[Dict[str, Dict[str, str]]] = None
        self._cache_mtime: float = 0.0
        self._last_session_context_diagnostics: Dict[str, Any] = {}
        self._last_vector_sync_status: Dict[str, Any] = {
            "state": "unknown",
            "lastErrorKind": "",
            "lastError": "",
            "pendingRetry": False,
            "nextRetryAt": "",
        }
        self._ensure_structure()

    def _record_vector_sync_ok(self, fact_id: str, operation: str) -> None:
        self._last_vector_sync_status = {
            "state": "ok",
            "factId": fact_id,
            "operation": operation,
            "lastErrorKind": "",
            "lastError": "",
            "pendingRetry": False,
            "nextRetryAt": "",
            "updatedAt": _utc_now_iso(),
        }

    def _record_vector_sync_failure(self, exc: Exception, *, fact_id: str, operation: str) -> None:
        error_kind = _classify_vector_sync_error(exc)
        next_retry = datetime.now(timezone.utc) + timedelta(minutes=5)
        self._last_vector_sync_status = {
            "state": "queued_retry" if error_kind == "transient_connection" else "degraded",
            "factId": fact_id,
            "operation": operation,
            "lastErrorKind": error_kind,
            "lastError": str(exc),
            "pendingRetry": error_kind == "transient_connection",
            "nextRetryAt": next_retry.isoformat().replace("+00:00", "Z") if error_kind == "transient_connection" else "",
            "updatedAt": _utc_now_iso(),
        }
        now = time.monotonic()
        warning_key = f"{operation}:{error_kind}"
        last_at = _vector_sync_warning_last_at.get(warning_key, 0.0)
        if now - last_at < _VECTOR_SYNC_WARNING_INTERVAL_SECONDS:
            logger.debug("[MemoryStore] Vector Store sync still degraded (%s): %s", error_kind, exc)
            return
        _vector_sync_warning_last_at[warning_key] = now
        logger.warning(
            "[MemoryStore] Vector Store sync degraded (%s, non-fatal; canonical memory saved): %s",
            error_kind,
            exc,
        )

    def _sync_vector_store_document(self, fact_id: str, fact: str, metadata: dict[str, Any], *, operation: str) -> None:
        try:
            from core.vector_store import get_vector_store

            vs = get_vector_store()
            vs.add_documents([{"id": fact_id, "text": fact, "metadata": metadata}])
            self._record_vector_sync_ok(fact_id, operation)
        except Exception as exc:
            self._record_vector_sync_failure(exc, fact_id=fact_id, operation=operation)

    def get_vector_sync_status(self) -> Dict[str, Any]:
        return dict(self._last_vector_sync_status)
    
    # ==========================================
    # 目录结构初始化
    # ==========================================
    
    def _ensure_structure(self):
        """确保存储目录结构存在"""
        MEMORY_ROOT.mkdir(parents=True, exist_ok=True)
        
        # 知识分区目录
        areas_dir = MEMORY_ROOT / "knowledge" / "areas"
        for area in ["general", "projects", "channels", "workspaces", "external_api_threads"]:
            (areas_dir / area).mkdir(parents=True, exist_ok=True)
        
        # 日志目录
        (MEMORY_ROOT / "daily").mkdir(exist_ok=True)
        
        # 索引目录
        (MEMORY_ROOT / ".index").mkdir(exist_ok=True)
        
        # 图谱目录
        (MEMORY_ROOT / ".graph").mkdir(exist_ok=True)
        (MEMORY_ROOT / "quarantine").mkdir(exist_ok=True)
        
        # 创建默认 MEMORY.md
        if not self.memory_path.exists():
            content = MEMORY_TEMPLATE_V2.format(date=datetime.now().strftime("%Y-%m-%d"))
            self.memory_path.write_text(content, encoding="utf-8")
            logger.info(f"[MemoryStore] Created default MEMORY.md at {self.memory_path}")

    def _is_valid_scope(self, scope: str) -> bool:
        normalized = (scope or "").strip()
        if not normalized:
            return False
        if normalized == "global":
            return True
        return normalized.startswith(_SPECIFIC_SCOPE_PREFIXES)

    def _validate_scope(self, scope: str) -> str:
        normalized = (scope or "").strip()
        if not self._is_valid_scope(normalized):
            raise ValueError(f"Unsupported memory scope: {scope}")
        return normalized

    def _normalize_memory_key(self, key: str) -> str:
        return re.sub(r"[^a-z0-9_]+", "_", str(key or "").strip().lower()).strip("_")

    def _canonicalize_global_profile_key(self, key: str) -> Tuple[str, str]:
        normalized = self._normalize_memory_key(key)
        if not normalized:
            return "preference", "unknown"
        if normalized in GLOBAL_PROFILE_KEYS:
            return normalized, "fixed"
        if normalized in GLOBAL_PROFILE_ALIASES:
            return GLOBAL_PROFILE_ALIASES[normalized], "alias"
        if normalized in REMOVED_GLOBAL_KEYS:
            return normalized, "removed"
        return normalized, "custom"

    def get_global_profile_schema(self) -> Dict[str, Any]:
        return {
            "fixedKeys": list(GLOBAL_PROFILE_KEYS),
            "defaults": dict(GLOBAL_PROFILE_DEFAULTS),
            "aliases": dict(GLOBAL_PROFILE_ALIASES),
            "removedKeys": sorted(REMOVED_GLOBAL_KEYS),
            "executionHints": {
                "voice_interaction_protocol": VOICE_INTERACTION_EXECUTION_HINT,
            },
        }

    def _trim_text_to_budget(self, text: str, remaining_tokens: int) -> str:
        if not text or remaining_tokens <= 0:
            return ""
        max_chars = max(0, remaining_tokens * 4)
        if len(text) <= max_chars:
            return text
        if max_chars <= 3:
            return text[:max_chars]
        return text[: max_chars - 3].rstrip() + "..."

    def _backup_memory_file(self, reason: str) -> Optional[Path]:
        if not self.memory_path.exists():
            return None
        try:
            backup_dir = MEMORY_ROOT / "backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            safe_reason = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(reason or "backup")).strip("_") or "backup"
            backup_path = backup_dir / f"MEMORY.{stamp}.{safe_reason}.md"
            backup_path.write_text(self.memory_path.read_text(encoding="utf-8"), encoding="utf-8")
            return backup_path
        except Exception as exc:
            logger.warning(f"[MemoryStore] Failed to create MEMORY.md backup before repair: {exc}")
            return None
    
    # ==========================================
    # Layer 1: 偏好管理 (MEMORY.md)
    # ==========================================
    
    def _load_raw_preferences(self) -> Dict[str, Dict[str, str]]:
        """解析 MEMORY.md，返回 {scope: {key: value}} 字典"""
        if not self.memory_path.exists():
            return {"global": {}}
        
        # 缓存检查：仅当文件变更时重新解析
        try:
            mtime = self.memory_path.stat().st_mtime
            if self._preferences_cache is not None and mtime == self._cache_mtime:
                return self._preferences_cache
        except Exception:
            pass
        
        content = self.memory_path.read_text(encoding="utf-8")
        
        # 剥离 YAML frontmatter
        fm_match = re.match(r'^---\s*\n(.*?)\n---\s*\n', content, re.DOTALL)
        if fm_match:
            content = content[fm_match.end():]
        
        # 按 scope 分区解析
        data: Dict[str, Dict[str, str]] = {"global": {}}
        current_scope: Optional[str] = "global"
        invalid_global_lines: List[str] = []
        
        for line in content.split('\n'):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            
            # 检测 [scope] 头
            scope_match = SCOPE_PATTERN.match(line)
            if scope_match:
                current_scope = scope_match.group(1)
                if current_scope.strip().lower() in IGNORED_MEMORY_SECTIONS:
                    current_scope = None
                    continue
                if not self._is_valid_scope(current_scope):
                    logger.warning(f"[MemoryStore] Ignoring invalid scope section in MEMORY.md: {current_scope}")
                    current_scope = None
                    continue
                if current_scope not in data:
                    data[current_scope] = {}
                continue
            
            # 解析 key: value
            kv_match = KV_PATTERN.match(line)
            if kv_match and current_scope:
                key = kv_match.group(1)
                value = kv_match.group(2).strip()
                data[current_scope][key] = value
            elif current_scope == "global":
                invalid_global_lines.append(line)

        data, changed = self._normalize_global_profile_data(data)
        if invalid_global_lines:
            self._append_global_preference_quarantine_records(
                [
                    {
                        "key": "invalid_global_line",
                        "value": "\n".join(invalid_global_lines[:20]),
                        "reason": "global_profile_parse_repair",
                        "metadata": {
                            "source": "memory_global_repair",
                            "lineCount": len(invalid_global_lines),
                        },
                    }
                ]
            )
            changed = True
        if changed:
            self._backup_memory_file("global_profile_repair")
            self._save_preferences(data)
            return data
        
        # 更新缓存
        self._preferences_cache = data
        try:
            self._cache_mtime = self.memory_path.stat().st_mtime
        except Exception:
            self._cache_mtime = 0.0
        
        return data

    def _normalize_global_profile_data(self, data: Dict[str, Dict[str, str]]) -> Tuple[Dict[str, Dict[str, str]], bool]:
        global_values = dict(data.get("global") or {})
        normalized_global: Dict[str, str] = {}
        changed = False
        migrated_records: List[Dict[str, Any]] = []

        legacy_identity_parts: Dict[str, str] = {}

        for raw_key, raw_value in global_values.items():
            key = self._normalize_memory_key(raw_key)
            value = str(raw_value or "").strip()
            canonical_key, key_kind = self._canonicalize_global_profile_key(key)
            if raw_key != canonical_key:
                changed = True
            if key in {"system_name", "system_slug", "system_author"}:
                legacy_identity_parts[key] = value
                if key != "system_name":
                    changed = True
                    continue
            if key_kind in {"fixed", "alias"}:
                if value:
                    normalized_global[canonical_key] = value
                continue
            if key_kind == "removed":
                changed = True
                if value:
                    migrated_records.append(
                        {
                            "key": key,
                            "value": value,
                            "reason": "global_profile_field_removed",
                            "metadata": {
                                "source": "memory_global_migration",
                                "target": "execution_hints" if key == "voice_interaction_protocol" else "quarantine",
                            },
                        }
                    )
                continue
            if key:
                normalized_global[key] = value

        if "system_identity_reference" not in normalized_global:
            identity_name = legacy_identity_parts.get("system_name")
            if identity_name:
                detail_parts = []
                if legacy_identity_parts.get("system_slug"):
                    detail_parts.append(f"slug: {legacy_identity_parts['system_slug']}")
                if legacy_identity_parts.get("system_author"):
                    detail_parts.append(f"author: {legacy_identity_parts['system_author']}")
                normalized_global["system_identity_reference"] = (
                    f"{identity_name} ({'; '.join(detail_parts)})" if detail_parts else identity_name
                )
                changed = True

        for key, value in GLOBAL_PROFILE_DEFAULTS.items():
            if value and not str(normalized_global.get(key) or "").strip():
                normalized_global[key] = value
                changed = True

        if normalized_global != global_values:
            changed = True
        data = dict(data)
        data["global"] = normalized_global
        if migrated_records:
            self._append_global_preference_quarantine_records(migrated_records)
        return data, changed
    
    def load_preferences(self, scope: str = "global", scope_chain: Optional[List[str]] = None) -> Dict[str, str]:
        """
        加载合并后的偏好（考虑 scope 优先级回退）。
        
        优先级链: current_scope > global
        """
        all_data = self._load_raw_preferences()
        
        scopes_order = self._normalize_scope_chain(scope=scope, scope_chain=scope_chain)
        
        # 从低到高合并
        merged = {}
        for s in scopes_order:
            if s in all_data:
                for key, value in all_data[s].items():
                    if s == "global":
                        canonical_key, _ = self._canonicalize_global_profile_key(key)
                        merged[canonical_key] = value
                    else:
                        merged[canonicalize_preference_key(key)] = value
        
        return merged
    
    def get_all_scopes(self) -> List[str]:
        """获取所有已定义的 scope"""
        return list(self._load_raw_preferences().keys())

    def get_scope_preferences_raw(self, scope: str = "global") -> Dict[str, str]:
        normalized_scope = self._validate_scope(scope)
        return dict(self._load_raw_preferences().get(normalized_scope) or {})
    
    def update_preference(self, key: str, value: str, scope: str = "global", source: str = "human_admin"):
        """
        写入偏好到 MEMORY.md（覆盖同 scope 同 key）。
        """
        normalized_scope = self._validate_scope(scope)
        data = self._load_raw_preferences()
        normalized_source = str(source or "human_admin").strip() or "human_admin"
        if normalized_scope == "global":
            canonical_key, key_kind = self._canonicalize_global_profile_key(key)
            if key_kind == "removed":
                self._append_global_preference_quarantine_records(
                    [
                        {
                            "key": canonical_key,
                            "value": value,
                            "reason": "global_profile_field_removed",
                            "metadata": {"source": normalized_source},
                        }
                    ]
                )
                logger.info(f"[MemoryStore] Quarantined removed global preference key={canonical_key}")
                return
            if normalized_source == "memory_agent" and key_kind == "custom":
                self._append_global_preference_quarantine_records(
                    [
                        {
                            "key": canonical_key,
                            "value": value,
                            "reason": "unmapped_global_preference",
                            "metadata": {"source": normalized_source, "rawKey": key},
                        }
                    ]
                )
                logger.info(f"[MemoryStore] Quarantined unmapped memory-agent global preference key={canonical_key}")
                return
        else:
            canonical_key = canonicalize_preference_key(key)
        
        if normalized_scope not in data:
            data[normalized_scope] = {}
        data[normalized_scope][canonical_key] = value
        
        self._save_preferences(data)
        logger.info(f"[MemoryStore] Updated preference [{normalized_scope}] {canonical_key} = {value}")

    def delete_preference(self, key: str, scope: str = "global") -> bool:
        """
        从 MEMORY.md 删除某个 scope 下的单条偏好。
        """
        normalized_scope = self._validate_scope(scope)
        data = self._load_raw_preferences()
        if normalized_scope == "global":
            canonical_key, _ = self._canonicalize_global_profile_key(key)
        else:
            canonical_key = canonicalize_preference_key(key)
        if normalized_scope not in data or canonical_key not in data[normalized_scope]:
            return False

        del data[normalized_scope][canonical_key]
        self._save_preferences(data)
        logger.info(f"[MemoryStore] Deleted preference [{normalized_scope}] {canonical_key}")
        return True

    def _global_preference_quarantine_path(self) -> Path:
        return MEMORY_ROOT / "quarantine" / "global_preferences.json"

    def load_global_preference_quarantine(self) -> List[Dict[str, Any]]:
        path = self._global_preference_quarantine_path()
        if not path.exists():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []
        items = payload if isinstance(payload, list) else []
        normalized: List[Dict[str, Any]] = []
        changed = False
        for item in items:
            if not isinstance(item, dict):
                continue
            record = dict(item)
            if not str(record.get("id") or "").strip():
                key_part = canonicalize_preference_key(str(record.get("key") or "preference"))
                stamp = str(record.get("quarantinedAt") or _utc_now_iso()).replace(":", "-")
                record["id"] = f"prefq:{key_part}:{stamp}"
                changed = True
            normalized.append(record)
        if changed:
            self._save_global_preference_quarantine_items(normalized)
        return normalized

    def _save_global_preference_quarantine_items(self, items: List[Dict[str, Any]]) -> None:
        path = self._global_preference_quarantine_path()
        path.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")

    def _append_global_preference_quarantine_records(self, records: List[Dict[str, Any]]) -> None:
        if not records:
            return
        items = self.load_global_preference_quarantine()
        existing = {
            (str(item.get("key") or ""), str(item.get("value") or ""), str(item.get("reason") or ""))
            for item in items
            if isinstance(item, dict)
        }
        for record in records:
            key = canonicalize_preference_key(str(record.get("key") or "preference"))
            value = str(record.get("value") or "").strip()
            reason = str(record.get("reason") or "global_profile_migration").strip()
            if (key, value, reason) in existing:
                continue
            items.append(
                {
                    "id": f"prefq:{key}:{uuid.uuid4().hex[:10]}",
                    "key": key,
                    "value": value,
                    "reason": reason,
                    "metadata": dict(record.get("metadata") or {}),
                    "quarantinedAt": _utc_now_iso(),
                }
            )
        self._save_global_preference_quarantine_items(items)

    def quarantine_global_preference(
        self,
        *,
        key: str,
        value: str,
        reason: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        canonical_key = canonicalize_preference_key(key)
        record = {
            "id": f"prefq:{canonical_key}:{uuid.uuid4().hex[:10]}",
            "key": canonical_key,
            "value": str(value or "").strip(),
            "reason": str(reason or "").strip() or "unspecified",
            "metadata": dict(metadata or {}),
            "quarantinedAt": _utc_now_iso(),
        }
        items = self.load_global_preference_quarantine()
        items.append(record)
        self._save_global_preference_quarantine_items(items)
        self.delete_preference(canonical_key, scope="global")
        return record

    def restore_global_preference_quarantine(self, record_id: str) -> Optional[Dict[str, Any]]:
        normalized_id = str(record_id or "").strip()
        if not normalized_id:
            return None
        items = self.load_global_preference_quarantine()
        restored: Optional[Dict[str, Any]] = None
        remaining: List[Dict[str, Any]] = []
        for item in items:
            if str(item.get("id") or "").strip() == normalized_id and restored is None:
                restored = dict(item)
                continue
            remaining.append(item)
        if not restored:
            return None
        self._save_global_preference_quarantine_items(remaining)
        self.update_preference(
            key=str(restored.get("key") or "").strip(),
            value=str(restored.get("value") or "").strip(),
            scope="global",
        )
        return restored

    def delete_global_preference_quarantine(self, record_id: str) -> bool:
        normalized_id = str(record_id or "").strip()
        if not normalized_id:
            return False
        items = self.load_global_preference_quarantine()
        remaining = [item for item in items if str(item.get("id") or "").strip() != normalized_id]
        if len(remaining) == len(items):
            return False
        self._save_global_preference_quarantine_items(remaining)
        return True
    
    def _save_preferences(self, data: Dict[str, Dict[str, str]]):
        """将偏好数据序列化写回 MEMORY.md"""
        lines = []
        
        # Frontmatter
        lines.append("---")
        lines.append("type: user_preferences")
        lines.append('version: "2.0"')
        lines.append(f'last_updated: "{datetime.now().strftime("%Y-%m-%d")}"')
        lines.append("---")
        lines.append("")
        
        # Scope 注释
        scope_comments = {
            "global": "# 全局画像 — 长期身份、表达与背景",
            "workspace:main": "# 默认工作区专属偏好",
        }
        
        # global 优先，其余排序
        valid_scopes = [s for s in data if self._is_valid_scope(s)]
        scope_order = ["global"] + sorted([s for s in valid_scopes if s != "global"])
        
        for scope in scope_order:
            if scope not in data:
                continue
            
            lines.append(f"[{scope}]")
            if scope in scope_comments:
                lines.append(scope_comments[scope])
            elif scope.startswith("project:"):
                project_name = scope.split(":", 1)[1]
                lines.append(f"# 项目 {project_name} 专属偏好")
            elif scope.startswith("channel:"):
                channel_name = scope.split(":", 1)[1]
                lines.append(f"# 渠道 {channel_name} 专属偏好")
            elif scope.startswith("workspace:"):
                workspace_name = scope.split(":", 1)[1]
                lines.append(f"# 工作区 {workspace_name} 专属偏好")
            elif scope.startswith("external_api_thread:"):
                thread_name = scope.split(":", 1)[1]
                lines.append(f"# 外部 API 线程 {thread_name} 专属偏好")
            
            entries = list(data[scope].items())
            if scope == "global":
                fixed = [(key, data[scope][key]) for key in GLOBAL_PROFILE_KEYS if key in data[scope]]
                custom = sorted(
                    [(key, value) for key, value in data[scope].items() if key not in GLOBAL_PROFILE_KEYS],
                    key=lambda item: item[0],
                )
                entries = fixed + custom

            for key, value in entries:
                if not key.startswith("_"):
                    lines.append(f"{key}: {value}")
            lines.append("")
        
        content = "\n".join(lines)
        self.memory_path.write_text(content, encoding="utf-8")
        
        # 刷新缓存
        self._preferences_cache = data
        try:
            self._cache_mtime = self.memory_path.stat().st_mtime
        except Exception:
            pass
    
    def format_preferences_for_injection(self, scope: str = "global", scope_chain: Optional[List[str]] = None) -> str:
        """格式化偏好用于 System Prompt 注入"""
        prefs = self.load_preferences(scope, scope_chain=scope_chain)
        if not prefs:
            return ""
        
        lines = [f"- {key}: {value}" for key, value in prefs.items()]
        return "\n".join(lines)
    
    # ==========================================
    # Layer 2: 知识分区 (knowledge/areas/)
    # ==========================================
    
    def _get_knowledge_path(self, scope: str, category: str = "general") -> Path:
        """根据 scope 路由到分区文件"""
        scope = self._validate_scope(scope)
        areas_dir = MEMORY_ROOT / "knowledge" / "areas"
        
        if scope.startswith("project:"):
            project_name = self._safe_scope_path_token(scope.split(":", 1)[1])
            path = areas_dir / "projects" / project_name / "items.json"
        elif scope.startswith("channel:"):
            channel_name = self._safe_scope_path_token(scope.split(":", 1)[1])
            path = areas_dir / "channels" / channel_name / "items.json"
        elif scope.startswith("workspace:"):
            workspace_name = self._safe_scope_path_token(scope.split(":", 1)[1])
            path = areas_dir / "workspaces" / workspace_name / "items.json"
        elif scope.startswith("external_api_thread:"):
            thread_name = self._safe_scope_path_token(scope.split(":", 1)[1])
            path = areas_dir / "external_api_threads" / thread_name / "items.json"
        else:
            path = areas_dir / "general" / "items.json"
        
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _safe_scope_path_token(self, value: str) -> str:
        token = str(value or "").strip().replace(":", "__").replace("/", "_").replace("\\", "_")
        return token or "default"

    def _scope_uses_repo_signature(self, scope: str) -> bool:
        normalized = str(scope or "").strip()
        return normalized.startswith("project:") or normalized.startswith("workspace:")

    @staticmethod
    def _empty_soft_signature(*, resolution: str = "unresolved") -> Dict[str, Any]:
        return {
            "signaturePolicy": "soft_v1",
            "workspaceRoot": "",
            "agentsHash": "",
            "repoSignature": "",
            "resolution": resolution,
        }

    def _current_soft_signature(self) -> Dict[str, Any]:
        try:
            from core.storage import storage
            from erc.runtime_context import get_runtime_context
            from runtimes.memory.signature import build_soft_repo_signature

            context = get_runtime_context()
            workspace_path = (
                str(
                    context.get("original_workspace_path")
                    or context.get("originalWorkspacePath")
                    or context.get("workspace_path")
                    or ""
                ).strip()
                or str((storage.get_workspace_config() or {}).get("agent_workspace_path") or "").strip()
            )
            signature = build_soft_repo_signature(workspace_path)
            signature["resolution"] = "runtime_context" if workspace_path and context else "main_workspace"
            return signature
        except Exception:
            return self._empty_soft_signature()

    def _soft_signature_for_scope(
        self,
        scope: str,
        *,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Resolve a repository signature from the workspace that owns ``scope``.

        A scope that cannot be mapped back to a workspace is deliberately left
        unresolved. Comparing it with the configured main workspace was the
        cross-workspace invalidation bug that could stale unrelated memories.
        """
        normalized_scope = str(scope or "").strip()
        if not self._scope_uses_repo_signature(normalized_scope):
            return self._empty_soft_signature(resolution="not_repo_scoped")

        workspace_path = str((metadata or {}).get("workspaceRoot") or "").strip()
        resolution = "fact_metadata" if workspace_path else ""
        try:
            from core.storage import storage
            from erc.runtime_context import get_runtime_context
            from runtimes.memory.project_registry import project_registry_service
            from runtimes.memory.signature import build_soft_repo_signature

            context = get_runtime_context()
            context_path = str(
                context.get("original_workspace_path")
                or context.get("originalWorkspacePath")
                or context.get("workspace_path")
                or ""
            ).strip()
            context_project_id = str(context.get("project_id") or "").strip()
            context_workspace_id = str(context.get("workspace_id") or "").strip()
            context_scope = str(context.get("resolved_scope") or "").strip()

            if not workspace_path and normalized_scope == "workspace:main":
                # Historical ``workspace:main`` rows do not prove which
                # physical directory was "main" when they were written.
                return self._empty_soft_signature(resolution="unresolved_legacy_main")

            if not workspace_path:
                try:
                    from runtimes.memory.workspace_scope import resolve_workspace_scope_identity

                    identity = resolve_workspace_scope_identity(scope_alias=normalized_scope)
                except Exception:
                    identity = None
                identity_path = str((identity or {}).get("workspacePath") or "").strip()
                if identity_path:
                    workspace_path = identity_path
                    resolution = "workspace_scope_catalog"

            if not workspace_path and normalized_scope.startswith("project:"):
                project_id = normalized_scope.split(":", 1)[1]
                if context_path and context_project_id == project_id:
                    workspace_path = context_path
                    resolution = "runtime_project"
                else:
                    project = project_registry_service.get_project(project_id)
                    if project and str(project.workspace_path or "").strip():
                        workspace_path = str(project.workspace_path).strip()
                        resolution = "project_registry"
            elif not workspace_path and normalized_scope.startswith("workspace:"):
                workspace_id = normalized_scope.split(":", 1)[1]
                runtime_matches = (
                    context_scope == normalized_scope
                    or (context_workspace_id and context_workspace_id == workspace_id)
                )
                if context_path and runtime_matches:
                    workspace_path = context_path
                    resolution = "runtime_workspace"
                elif not normalized_scope.startswith("workspace:external:"):
                    project = project_registry_service.find_project_for_workspace(workspace_id=workspace_id)
                    if project and str(project.workspace_path or "").strip():
                        workspace_path = str(project.workspace_path).strip()
                        resolution = "workspace_registry"

            if not workspace_path:
                return self._empty_soft_signature(resolution="unresolved_scope")
            signature = build_soft_repo_signature(workspace_path)
            signature["resolution"] = resolution or "resolved_scope"
            return signature
        except Exception as exc:
            logger.warning("[MemoryStore] Could not resolve signature for %s: %s", normalized_scope, exc)
            return self._empty_soft_signature(resolution="resolution_error")

    def _mark_stale_for_signature_mismatch(self, scopes: List[str]) -> Dict[str, Any]:
        from core.knowledge_db import knowledge_db

        scoped = list(dict.fromkeys(scope for scope in scopes if self._scope_uses_repo_signature(scope)))
        marked = 0
        resolved_scopes: List[Dict[str, Any]] = []
        skipped_scopes: List[Dict[str, str]] = []
        for scope in scoped:
            signature = self._soft_signature_for_scope(scope)
            if not (signature.get("agentsHash") or signature.get("repoSignature")):
                skipped_scopes.append({"scope": scope, "reason": str(signature.get("resolution") or "unresolved_scope")})
                continue
            marked += knowledge_db.mark_stale_for_signature_mismatch(
                scopes=[scope],
                agents_hash=str(signature.get("agentsHash") or ""),
                repo_signature=str(signature.get("repoSignature") or ""),
            )
            resolved_scopes.append({
                "scope": scope,
                "workspaceRoot": str(signature.get("workspaceRoot") or ""),
                "resolution": str(signature.get("resolution") or "resolved_scope"),
            })
        return {
            "staleMarked": marked,
            "signaturePolicy": "soft_v1",
            "resolvedScopes": resolved_scopes,
            "skippedScopes": skipped_scopes,
        }

    def refresh_stale_revalidation(self, scopes: Optional[List[str]] = None) -> Dict[str, Any]:
        if scopes is not None:
            return self._mark_stale_for_signature_mismatch(scopes)
        try:
            from core.knowledge_db import knowledge_db

            with knowledge_db._conn() as conn:
                rows = conn.execute(
                    """
                    SELECT DISTINCT scope FROM knowledge
                    WHERE status = 'active'
                      AND (scope LIKE 'project:%' OR scope LIKE 'workspace:%')
                    """
                ).fetchall()
            return self._mark_stale_for_signature_mismatch([str(row["scope"] or "") for row in rows])
        except Exception as exc:
            logger.warning(f"[MemoryStore] Could not refresh stale revalidation state: {exc}")
            return {"staleMarked": 0, "signaturePolicy": "soft_v1"}

    def _is_injectable_knowledge(self, fact_id: str) -> bool:
        from core.knowledge_db import knowledge_db

        try:
            with knowledge_db._conn() as conn:
                row = conn.execute(
                    "SELECT status, lifecycle_state FROM knowledge WHERE id = ?",
                    (fact_id,),
                ).fetchone()
            if not row:
                return False
            status = str(row["status"] or "active").strip().lower()
            lifecycle_state = str(row["lifecycle_state"] or "active").strip().lower()
            return status == "active" and lifecycle_state not in {"stale", "tombstoned", "superseded", "quarantined"}
        except Exception as exc:
            logger.warning(f"[MemoryStore] Could not verify knowledge injection state for {fact_id}: {exc}")
            return False

    def revalidate_knowledge(self, fact_id: str, maintainer_source: str = "human_admin") -> bool:
        from core.knowledge_db import knowledge_db
        from core.knowledge_projection import knowledge_projection_service

        try:
            with knowledge_db._conn() as conn:
                row = conn.execute("SELECT scope, metadata_json FROM knowledge WHERE id = ?", (fact_id,)).fetchone()
                if not row:
                    return False
                scope = str(row["scope"] or "global")
                try:
                    metadata = json.loads(row["metadata_json"] or "{}")
                except Exception:
                    metadata = {}
        except Exception as exc:
            logger.warning(f"[MemoryStore] Could not fetch scope for knowledge revalidation {fact_id}: {exc}")
            return False

        signature = self._soft_signature_for_scope(scope, metadata=metadata) if self._scope_uses_repo_signature(scope) else {}
        ok = knowledge_db.revalidate_knowledge(
            fact_id,
            agents_hash=str(signature.get("agentsHash") or ""),
            repo_signature=str(signature.get("repoSignature") or ""),
            signature_policy=str(signature.get("signaturePolicy") or "soft_v1"),
            maintainer_source=maintainer_source,
        )
        if not ok:
            return False
        knowledge_projection_service.process_outbox(limit=10)
        return True
    
    def add_knowledge(
        self,
        fact: str,
        category: str,
        scope: str = "global",
        source_session: Optional[str] = None,
        tags: Optional[List[str]] = None,
        maintainer_source: str = "memory_runtime",
        confidence: float = 1.0,
    ) -> str:
        """Deprecated compatibility entry that writes canonical SQLite only."""
        from core.knowledge_db import knowledge_db
        from core.knowledge_projection import knowledge_projection_service

        normalized_scope = self._validate_scope(scope)
        fact_id = f"fact-{uuid.uuid4().hex[:8]}"
        signature = self._soft_signature_for_scope(normalized_scope) if self._scope_uses_repo_signature(normalized_scope) else {}
        result = knowledge_db.write_knowledge(
            fact=fact,
            category=category,
            scope=normalized_scope,
            relation="new",
            source_session=source_session,
            maintainer_source=maintainer_source,
            confidence=confidence,
            agents_hash=str(signature.get("agentsHash") or ""),
            repo_signature=str(signature.get("repoSignature") or ""),
            signature_policy=str(signature.get("signaturePolicy") or "soft_v1"),
            metadata={
                "tags": [str(tag).strip() for tag in list(tags or []) if str(tag).strip()],
                "workspaceRoot": signature.get("workspaceRoot") if signature else "",
                "compatibilityEntry": "MemoryStore.add_knowledge",
            },
            fact_id=fact_id,
        )
        knowledge_projection_service.process_outbox(limit=10)
        canonical_id = str(result["factId"])
        logger.info("[MemoryStore] Canonical knowledge write %s [%s]", canonical_id, normalized_scope)
        return canonical_id
    
    def update_knowledge(self, fact_id: str, new_fact: str, category: str = None, scope: str = None,
                         maintainer_source: str | None = None, confidence: float | None = None) -> bool:
        """Deprecated compatibility entry mapped to a replacement revision."""
        from core.knowledge_db import knowledge_db
        from core.knowledge_projection import knowledge_projection_service

        with knowledge_db._conn() as conn:
            current = conn.execute("SELECT * FROM knowledge WHERE id = ?", (fact_id,)).fetchone()
        if not current:
            return False
        normalized_scope = self._validate_scope(scope or str(current["scope"] or "global"))
        signature = self._soft_signature_for_scope(normalized_scope) if self._scope_uses_repo_signature(normalized_scope) else {}
        result = knowledge_db.write_knowledge(
            fact=new_fact,
            category=category or str(current["category"] or "general"),
            scope=normalized_scope,
            relation="replace",
            target_fact_id=fact_id,
            maintainer_source=maintainer_source or str(current["maintainer_source"] or "memory_runtime"),
            confidence=confidence if confidence is not None else float(current["confidence"] or 1.0),
            importance=int(current["importance"] or 50),
            durability=str(current["durability"] or "operational"),
            agents_hash=str(signature.get("agentsHash") or ""),
            repo_signature=str(signature.get("repoSignature") or ""),
            signature_policy=str(signature.get("signaturePolicy") or "soft_v1"),
            metadata={
                "deprecatedOverwriteId": fact_id,
                "compatibilityEntry": "MemoryStore.update_knowledge",
                "workspaceRoot": signature.get("workspaceRoot") if signature else "",
            },
        )
        knowledge_projection_service.process_outbox(limit=10)
        return bool(result.get("factId"))
        
    def delete_knowledge(self, fact_id: str) -> bool:
        """Tombstone canonical knowledge; projections are removed by the outbox."""
        from core.knowledge_db import knowledge_db
        from core.knowledge_projection import knowledge_projection_service

        deleted = knowledge_db.delete_knowledge(fact_id)
        if deleted:
            knowledge_projection_service.process_outbox(limit=10)
            logger.info("[MemoryStore] Tombstoned canonical knowledge %s", fact_id)
        return deleted
    
    def query_knowledge(self, query: Optional[str] = None,
                        scope: Optional[str] = None,
                        scopes: Optional[List[str]] = None,
                        category: Optional[str] = None,
                        limit: int = 20) -> List[Dict]:
        """
        查询知识。优先使用 FTS5 全文检索，无查询词时直接从数据库获取最新列表。
        """
        from core.knowledge_db import knowledge_db

        scope_candidates = self._normalize_scope_chain(scope=scope or "global", scope_chain=scopes)
        include_exact_scopes = [item for item in scope_candidates if item != "global"]
        # Query exact workspace aliases first, then the shared global scope.
        # Passing ``scope=None`` to KnowledgeDB means every scope, which is not
        # a safe fallback for a workspace-bound request.
        query_scopes = [*reversed(include_exact_scopes), "global"]
        results = []
        seen_ids = set()
        for candidate_scope in query_scopes:
            try:
                batch = (
                    knowledge_db.fts_search(query, scope=candidate_scope, limit=limit)
                    if query
                    else knowledge_db.get_all_knowledge(scope=candidate_scope, limit=limit)
                )
            except Exception as e:
                logger.warning(f"[MemoryStore] Scoped knowledge query failed for {candidate_scope}: {e}")
                batch = []
            for item in batch:
                item_id = item.get("id")
                if item_id and item_id in seen_ids:
                    continue
                if item_id:
                    seen_ids.add(item_id)
                results.append(item)
            if len(results) >= limit:
                break
                
        if category:
            results = [r for r in results if r.get("category") == category]

        results = [r for r in results if self._is_valid_scope(str(r.get("scope") or "global"))]
        results = [
            r for r in results
            if str(r.get("lifecycle_state") or "active").strip().lower() not in {"stale", "tombstoned", "superseded"}
        ]
        return results[:limit]

    def _load_recall_runtime_config(
        self,
        *,
        limit: int,
    ) -> Dict[str, Any]:
        from core.storage import MEMORY_RETRIEVAL_THRESHOLD_RECOMMENDED, storage

        memory_config = storage.get_memory_config() or {}
        recall_strategy = str(memory_config.get("recall_strategy") or "balanced").strip().lower()
        if recall_strategy not in {"balanced", "semantic", "keyword"}:
            recall_strategy = "balanced"

        try:
            configured_top_k = int(memory_config.get("recall_top_k") or limit or 5)
        except (TypeError, ValueError):
            configured_top_k = limit or 5
        effective_limit = max(1, configured_top_k if limit == 5 else int(limit or configured_top_k))

        try:
            retrieval_threshold = float(memory_config.get("retrieval_threshold"))
        except (TypeError, ValueError, KeyError):
            retrieval_threshold = MEMORY_RETRIEVAL_THRESHOLD_RECOMMENDED
        retrieval_threshold = max(0.0, min(retrieval_threshold, 1.0))

        return {
            "memory_config": memory_config,
            "recall_strategy": recall_strategy,
            "effective_limit": effective_limit,
            "retrieval_threshold": retrieval_threshold,
            "use_vector": recall_strategy in {"balanced", "semantic"},
            "use_fts": bool(memory_config.get("fts_enabled", True)) and recall_strategy in {"balanced", "keyword"},
            "use_graph": bool(memory_config.get("graph_enabled", True)),
        }

    def _normalize_recall_score(self, value: Any) -> float:
        try:
            score = float(value)
        except (TypeError, ValueError):
            score = 0.0
        return max(0.0, min(score, 1.0))

    def _normalize_fts_relevance(self, raw_rank: Any, *, position: int, total: int) -> float:
        try:
            rank_value = abs(float(raw_rank))
        except (TypeError, ValueError):
            rank_value = 9999.0
        rank_score = 1.0 / (1.0 + rank_value)
        positional_score = max(0.0, 1.0 - (position / max(total, 1)))
        return max(0.0, min((rank_score * 0.7) + (positional_score * 0.3), 1.0))

    def _merge_recall_candidate(self, pool: Dict[str, Dict[str, Any]], candidate: Dict[str, Any]) -> None:
        candidate_id = str(candidate.get("id") or "").strip()
        if not candidate_id:
            return
        normalized_candidate = {
            "id": candidate_id,
            "fact": str(candidate.get("fact") or "").strip(),
            "category": str(candidate.get("category") or "general").strip() or "general",
            "scope": str(candidate.get("scope") or "global").strip() or "global",
            "source": str(candidate.get("source") or "unknown").strip() or "unknown",
            "raw_relevance_score": self._normalize_recall_score(candidate.get("raw_relevance_score")),
            "final_relevance_score": self._normalize_recall_score(candidate.get("final_relevance_score")),
            "accepted": bool(candidate.get("accepted", False)),
            "reject_reason": str(candidate.get("reject_reason") or "").strip(),
        }
        existing = pool.get(candidate_id)
        if existing is None:
            pool[candidate_id] = normalized_candidate
            return
        merged_sources = {
            item.strip()
            for item in f"{existing.get('source', '')}+{normalized_candidate['source']}".split("+")
            if item.strip()
        }
        existing["source"] = "+".join(sorted(merged_sources))
        existing["raw_relevance_score"] = max(
            self._normalize_recall_score(existing.get("raw_relevance_score")),
            normalized_candidate["raw_relevance_score"],
        )
        if not str(existing.get("fact") or "").strip() and normalized_candidate["fact"]:
            existing["fact"] = normalized_candidate["fact"]
        if not str(existing.get("category") or "").strip():
            existing["category"] = normalized_candidate["category"]
        if not str(existing.get("scope") or "").strip():
            existing["scope"] = normalized_candidate["scope"]

    def _extract_graph_seed_entities(self, query: str, seed_items: List[Dict[str, Any]]) -> List[str]:
        stop_words = {
            "为什么", "怎么", "如何", "什么", "这个", "那个", "这些", "那些", "需要", "以及",
            "记忆", "内容", "问题", "系统", "当前", "最近", "事情", "说明", "结果", "因为",
            "that", "this", "with", "from", "will", "would", "should", "about", "have", "into",
            "memory", "context", "issue", "problem", "result", "query", "history",
        }
        fragments = [str(query or "").strip()]
        fragments.extend(str(item.get("fact") or "").strip()[:240] for item in seed_items[:3])
        entities: List[str] = []
        for fragment in fragments:
            if not fragment:
                continue
            for token in re.findall(r"\b[a-zA-Z][a-zA-Z0-9._-]{2,}\b", fragment):
                normalized = token.lower().strip()
                if normalized in stop_words or normalized in entities:
                    continue
                entities.append(normalized)
            for token in re.findall(r"[\u4e00-\u9fa5]{2,8}", fragment):
                normalized = token.strip()
                if normalized in stop_words or normalized in entities:
                    continue
                entities.append(normalized)
        return entities[:6]

    def _execute_unified_recall(
        self,
        *,
        query: str,
        limit: int = 5,
        scope: Optional[str] = None,
        scopes: Optional[List[str]] = None,
        recall_strategy_override: Optional[str] = None,
        allow_rerank: Optional[bool] = None,
    ) -> Dict[str, Any]:
        from core.knowledge_db import knowledge_db

        config = self._load_recall_runtime_config(limit=limit)
        if recall_strategy_override:
            override = str(recall_strategy_override or "").strip().lower()
            if override in {"balanced", "semantic", "keyword"}:
                config["recall_strategy"] = override
                config["use_vector"] = override in {"balanced", "semantic"}
                config["use_fts"] = bool(config.get("memory_config", {}).get("fts_enabled", True)) and override in {"balanced", "keyword"}
        effective_limit = int(config["effective_limit"])
        retrieval_threshold = float(config["retrieval_threshold"])
        minimum_quality_floor = 0.05
        effective_acceptance_threshold = max(retrieval_threshold, minimum_quality_floor)
        recall_strategy = str(config["recall_strategy"])
        use_vector = bool(config["use_vector"])
        use_fts = bool(config["use_fts"])
        use_graph = bool(config["use_graph"])

        scope_chain = self._normalize_scope_chain(scope=scope or "global", scope_chain=scopes)
        if "global" not in scope_chain:
            scope_chain.append("global")
        allowed_scopes = set(scope_chain)

        seed_candidates: Dict[str, Dict[str, Any]] = {}
        diagnostics: Dict[str, Any] = {
            "query": query,
            "scope": scope,
            "scopes": list(scopes or []),
            "allowed_scopes": list(allowed_scopes),
            "recall_strategy": recall_strategy,
            "threshold_snapshot": retrieval_threshold,
            "effective_acceptance_threshold": effective_acceptance_threshold,
            "seed_candidate_count": 0,
            "graph_candidate_count": 0,
            "graph_allowed": False,
            "graph_reject_reason": "",
            "graph_entities": [],
            "rerank_error": "",
            "rerank_skipped_reason": "",
        }

        if use_vector:
            try:
                from core.vector_store import get_vector_store

                vs = get_vector_store()
                vector_results = vs.similarity_search_with_rerank(
                    query,
                    top_k=max(effective_limit * 2, 6),
                    fetch_k=max(effective_limit * 4, 20),
                )
                for result in vector_results:
                    fact_id = result["id"]
                    parent_id = result.get("metadata", {}).get("parent_id")
                    final_fact = result["text"]
                    final_id = fact_id
                    if parent_id:
                        with knowledge_db._conn() as conn:
                            parent_row = conn.execute(
                                "SELECT fact FROM knowledge WHERE id = ?",
                                (parent_id,),
                            ).fetchone()
                            if parent_row:
                                final_fact = parent_row[0]
                                final_id = parent_id
                    item_scope = str(result.get("metadata", {}).get("scope", "global") or "global")
                    if item_scope not in allowed_scopes:
                        continue
                    if not self._is_injectable_knowledge(final_id):
                        continue
                    self._merge_recall_candidate(
                        seed_candidates,
                        {
                            "id": final_id,
                            "fact": final_fact,
                            "category": result.get("metadata", {}).get("category", "general"),
                            "scope": item_scope,
                            "source": "vector",
                            "raw_relevance_score": result.get("relevance_score", 0.0),
                        },
                    )
            except Exception as exc:
                diagnostics["vector_error"] = str(exc)
                diagnostics["vector_degraded"] = True
                logger.warning(f"[MemoryStore] Vector search error in unified_recall: {exc}")

        if use_fts:
            try:
                fts_results = knowledge_db.fts_search(query, limit=max(effective_limit * 4, 8))
                total_fts = len(fts_results)
                for index, result in enumerate(fts_results):
                    fact_id = result.get("id")
                    final_fact = result.get("fact", "")
                    final_id = fact_id
                    item_scope = str(result.get("scope", "global") or "global")
                    if item_scope not in allowed_scopes:
                        continue
                    with knowledge_db._conn() as conn:
                        parent_row = conn.execute(
                            "SELECT parent_id FROM knowledge WHERE id = ?",
                            (fact_id,),
                        ).fetchone()
                        if parent_row and parent_row[0]:
                            elevated = conn.execute(
                                "SELECT fact FROM knowledge WHERE id = ?",
                                (parent_row[0],),
                            ).fetchone()
                            if elevated:
                                final_fact = elevated[0]
                                final_id = parent_row[0]
                    if not self._is_injectable_knowledge(str(final_id or "")):
                        continue
                    self._merge_recall_candidate(
                        seed_candidates,
                        {
                            "id": final_id,
                            "fact": final_fact,
                            "category": result.get("category", "general"),
                            "scope": item_scope,
                            "source": "fts5",
                            "raw_relevance_score": self._normalize_fts_relevance(
                                result.get("relevance"),
                                position=index,
                                total=total_fts,
                            ),
                        },
                    )
            except Exception as exc:
                diagnostics["fts5_error"] = str(exc)
                diagnostics["fts5_degraded"] = True
                logger.warning(f"[MemoryStore] FTS5 search error in unified_recall: {exc}")

        seed_items = sorted(
            seed_candidates.values(),
            key=lambda item: (
                self._normalize_recall_score(item.get("raw_relevance_score")),
                str(item.get("source") or ""),
            ),
            reverse=True,
        )
        diagnostics["seed_candidate_count"] = len(seed_items)

        combined_candidates: Dict[str, Dict[str, Any]] = dict(seed_candidates)
        graph_seed_floor = max(effective_acceptance_threshold, 0.20)
        graph_seed_items = [
            item for item in seed_items
            if self._normalize_recall_score(item.get("raw_relevance_score")) >= graph_seed_floor
        ]

        if use_graph and graph_seed_items:
            try:
                graph_entities = self._extract_graph_seed_entities(query, graph_seed_items)
                diagnostics["graph_entities"] = graph_entities
                extracted_relations: set[str] = set()
                base_graph_score = max(
                    self._normalize_recall_score(graph_seed_items[0].get("raw_relevance_score")),
                    graph_seed_floor,
                ) * 0.9
                for entity in graph_entities:
                    relations = knowledge_db.multi_hop_query(entity, hops=2, scopes=scope_chain)
                    for relation in relations:
                        relation_text = f"{relation['subject']} {relation['predicate']} {relation['object']}"
                        if relation_text in extracted_relations:
                            continue
                        extracted_relations.add(relation_text)
                        virtual_id = f"graph:{uuid.uuid5(uuid.NAMESPACE_OID, relation_text).hex[:12]}"
                        self._merge_recall_candidate(
                            combined_candidates,
                            {
                                "id": virtual_id,
                                "fact": f"[Graph Context] {relation_text}",
                                "category": "graph_context",
                                "scope": str(relation.get("scope") or "global"),
                                "source": "graph",
                                "raw_relevance_score": base_graph_score,
                            },
                        )
                diagnostics["graph_candidate_count"] = sum(
                    1 for item in combined_candidates.values()
                    if str(item.get("source") or "").find("graph") >= 0
                )
                diagnostics["graph_allowed"] = diagnostics["graph_candidate_count"] > 0
                if not diagnostics["graph_allowed"]:
                    diagnostics["graph_reject_reason"] = "no_graph_relations_from_seed_entities"
            except Exception as exc:
                diagnostics["graph_reject_reason"] = f"graph_pipeline_failed:{exc}"
                logger.warning(f"[MemoryStore] Graph expansion pipeline failed in unified_recall: {exc}")
        elif use_graph:
            diagnostics["graph_reject_reason"] = "no_high_quality_seed_results"
        else:
            diagnostics["graph_reject_reason"] = "graph_disabled"

        if not combined_candidates:
            return {
                "items": [],
                "accepted_items": [],
                "threshold_snapshot": retrieval_threshold,
                "diagnostics": diagnostics,
            }

        ids_order: List[str] = []
        docs_to_rerank: List[str] = []
        for fact_id, item in combined_candidates.items():
            ids_order.append(fact_id)
            docs_to_rerank.append(str(item.get("fact") or ""))

        memory_config = dict(config.get("memory_config") or {})
        if allow_rerank is None:
            rerank_enabled = bool(memory_config.get("rerank_enabled", True))
        else:
            rerank_enabled = bool(allow_rerank)
        try:
            rerank_min_candidates = max(2, min(int(memory_config.get("rerank_min_candidates") or max(effective_limit + 1, 6)), 50))
        except (TypeError, ValueError):
            rerank_min_candidates = max(effective_limit + 1, 6)
        reranked_scores: Dict[str, float] = {}
        if rerank_enabled and len(docs_to_rerank) >= rerank_min_candidates:
            try:
                from core.memory_router import MemoryRouter

                reranker = MemoryRouter().get_reranker_model()
                ranked = reranker.rerank(query, docs_to_rerank, top_k=len(docs_to_rerank))
                for row in ranked:
                    idx = int(row.get("index") or 0)
                    if idx < 0 or idx >= len(ids_order):
                        continue
                    reranked_scores[ids_order[idx]] = self._normalize_recall_score(row.get("relevance_score", 0.0))
            except Exception as exc:
                diagnostics["rerank_error"] = str(exc)
                logger.warning(f"[MemoryStore] Unified recall reranking failed, falling back to raw scores: {exc}")
        else:
            diagnostics["rerank_skipped_reason"] = "disabled" if not rerank_enabled else f"candidate_count_below_{rerank_min_candidates}"

        all_items: List[Dict[str, Any]] = []
        for fact_id, item in combined_candidates.items():
            raw_score = self._normalize_recall_score(item.get("raw_relevance_score"))
            final_score = reranked_scores.get(fact_id, raw_score)
            accepted = final_score >= effective_acceptance_threshold
            all_items.append(
                {
                    **item,
                    "raw_relevance_score": raw_score,
                    "final_relevance_score": final_score,
                    "relevance_score": final_score,
                    "accepted": accepted,
                    "reject_reason": "" if accepted else "below_threshold",
                }
            )

        all_items.sort(
            key=lambda item: (
                self._normalize_recall_score(item.get("final_relevance_score")),
                self._normalize_recall_score(item.get("raw_relevance_score")),
            ),
            reverse=True,
        )

        all_accepted_items = [item for item in all_items if item.get("accepted")]
        accepted_items = all_accepted_items[:effective_limit]
        diagnostics["accepted_count"] = len(all_accepted_items)
        diagnostics["rejected_count"] = max(0, len(all_items) - len(all_accepted_items))

        return {
            "items": all_items[: max(effective_limit * 4, 12)],
            "accepted_items": accepted_items,
            "threshold_snapshot": retrieval_threshold,
            "effective_acceptance_threshold": effective_acceptance_threshold,
            "diagnostics": diagnostics,
        }

    def preview_unified_recall(
        self,
        query: str,
        limit: int = 5,
        scope: Optional[str] = None,
        scopes: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        preview = self._execute_unified_recall(query=query, limit=limit, scope=scope, scopes=scopes)
        return {
            "query": query,
            "scope": scope,
            "scopes": scopes or [],
            "threshold_snapshot": preview.get("threshold_snapshot"),
            "effective_acceptance_threshold": preview.get("effective_acceptance_threshold"),
            "diagnostics": preview.get("diagnostics") or {},
            "items": preview.get("items") or [],
            "accepted_items": preview.get("accepted_items") or [],
        }

    def build_memory_injection_pack(
        self,
        *,
        user_query: str,
        scope: str = "global",
        scope_chain: Optional[List[str]] = None,
        session_id: Optional[str] = None,
        run_id: Optional[str] = None,
        target_role: str = "supervisor",
        latency_tier: str = "balanced",
        visual_evidence: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Build an answer-oriented, explainable memory pack for diagnostics/injection.

        第一版主要服务 Admin 诊断和报告：说明选中了哪些记忆、为什么选、
        哪些候选被拒绝，以及作用域/延迟档位。真正 prompt 注入仍由
        build_session_context 负责，避免一次性改变 Phone/Web 主体验。
        """

        tier = str(latency_tier or "balanced").strip().lower()
        if tier not in {"fast", "balanced", "accurate"}:
            tier = "balanced"
        limit_by_tier = {"fast": 3, "balanced": 5, "accurate": 8}
        limit = limit_by_tier[tier]
        normalized_chain = self._normalize_scope_chain(scope=scope, scope_chain=scope_chain)
        preview = self.preview_unified_recall(
            query=user_query,
            limit=limit,
            scope=scope,
            scopes=normalized_chain,
        )
        threshold = preview.get("effective_acceptance_threshold")

        def _compact_content(item: Dict[str, Any], *, max_chars: int = 420) -> str:
            content = str(
                item.get("fact")
                or item.get("content")
                or item.get("text")
                or item.get("summary")
                or ""
            ).strip()
            if len(content) <= max_chars:
                return content
            return content[: max_chars - 1].rstrip() + "…"

        def _score(item: Dict[str, Any]) -> float:
            try:
                return float(item.get("final_relevance_score", item.get("relevance_score", 0.0)) or 0.0)
            except (TypeError, ValueError):
                return 0.0

        selected_memory: List[Dict[str, Any]] = []
        for item in preview.get("accepted_items") or []:
            score = _score(item)
            selected_memory.append(
                {
                    "id": item.get("id") or item.get("fact_id") or item.get("memory_id"),
                    "content": _compact_content(item),
                    "category": item.get("category") or item.get("type") or "memory",
                    "scope": item.get("scope") or "global",
                    "source": item.get("source") or item.get("maintainer_source") or item.get("origin") or "memory_store",
                    "confidence": score,
                    "recency": item.get("updated_at") or item.get("created_at") or item.get("timestamp"),
                    "whySelected": (
                        f"accepted_by_unified_recall(score={score:.3f}"
                        + (f", threshold={float(threshold):.3f}" if isinstance(threshold, (int, float)) else "")
                        + ")"
                    ),
                }
            )

        accepted_ids = {entry.get("id") for entry in selected_memory if entry.get("id")}
        rejected_memory: List[Dict[str, Any]] = []
        for item in preview.get("items") or []:
            item_id = item.get("id") or item.get("fact_id") or item.get("memory_id")
            if item_id in accepted_ids or item.get("accepted"):
                continue
            rejected_memory.append(
                {
                    "id": item_id,
                    "content": _compact_content(item, max_chars=260),
                    "category": item.get("category") or item.get("type") or "memory",
                    "scope": item.get("scope") or "global",
                    "source": item.get("source") or item.get("maintainer_source") or item.get("origin") or "memory_store",
                    "confidence": _score(item),
                    "doNotInjectReason": item.get("reject_reason") or "below_threshold_or_scope",
                }
            )
            if len(rejected_memory) >= limit * 2:
                break

        diagnostics = preview.get("diagnostics") or {}
        do_not_inject_reasons: List[Dict[str, Any]] = []
        rejected_count = int(diagnostics.get("rejected_count") or 0)
        if rejected_count:
            do_not_inject_reasons.append(
                {
                    "reason": "below_threshold",
                    "count": rejected_count,
                    "detail": "Unified recall found candidates below the injection threshold.",
                }
            )
        if not selected_memory:
            do_not_inject_reasons.append(
                {
                    "reason": "no_selected_memory",
                    "count": 0,
                    "detail": "No memory candidate was confident enough for injection.",
                }
            )

        return {
            "version": "memory_injection_pack_v3",
            "mode": tier,
            "query": user_query,
            "sessionId": session_id,
            "runId": run_id,
            "targetRole": target_role,
            "scope": {
                "active": scope,
                "chain": normalized_chain,
            },
            "selectedMemory": selected_memory,
            "whySelected": [item.get("whySelected") for item in selected_memory],
            "rejectedMemory": rejected_memory,
            "doNotInjectReasons": do_not_inject_reasons,
            "visualEvidence": list(visual_evidence or []),
            "stats": {
                "selectedCount": len(selected_memory),
                "rejectedPreviewCount": len(rejected_memory),
                "candidateCount": len(preview.get("items") or []),
                "acceptedCount": len(preview.get("accepted_items") or []),
                "latencyTier": tier,
                "visualEvidenceCount": len(visual_evidence or []),
            },
            "diagnostics": diagnostics,
        }

    def unified_recall(self, query: str, limit: int = 5, scope: Optional[str] = None, scopes: Optional[List[str]] = None) -> List[Dict]:
        from core.knowledge_db import knowledge_db

        preview = self._execute_unified_recall(query=query, limit=limit, scope=scope, scopes=scopes)
        accepted = list(preview.get("accepted_items") or [])
        knowledge_db.mark_knowledge_injected(
            [str(item.get("id") or "") for item in accepted if isinstance(item, dict) and item.get("id")]
        )
        return accepted
            
    # ==========================================
    # Layer 3: 时序日志 (daily/)
    # ==========================================
    
    def _get_daily_log_path(self, date: Optional[datetime] = None) -> Path:
        """获取日志文件路径: YYYY/MM_monthname/week_WW/YYYY-MM-DD.md"""
        if date is None:
            date = datetime.now()
        
        year = date.strftime("%Y")
        month_name = date.strftime("%m_%B").lower()
        week_num = date.strftime("%V")
        filename = date.strftime("%Y-%m-%d.md")
        
        path = MEMORY_ROOT / "daily" / year / month_name / f"week_{week_num}"
        path.mkdir(parents=True, exist_ok=True)
        return path / filename
    
    def append_daily_log(self, content: str, tags: Optional[List[str]] = None):
        """追加日志条目"""
        now = datetime.now()
        log_path = self._get_daily_log_path(now)
        
        # 新文件写入 frontmatter
        if not log_path.exists() or log_path.stat().st_size == 0:
            header_tags = str(tags) if tags else "[]"
            header = f"---\ntype: daily_log\ndate: \"{now.strftime('%Y-%m-%d')}\"\ntags: {header_tags}\n---\n\n"
            log_path.write_text(header, encoding="utf-8")
        
        # 追加带时间戳的条目
        time_str = now.strftime("%H:%M")
        entry = f"\n### {time_str}\n{content}\n"
        
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(entry)
        
        logger.info(f"[MemoryStore] Appended daily log to {log_path}")

    def _split_frontmatter(self, content: str) -> tuple[Dict[str, Any], str]:
        header_match = re.match(r'^---\n(.*?)\n---\s*', content, flags=re.DOTALL)
        if not header_match:
            return {}, content
        header = header_match.group(1)
        body = content[header_match.end():]
        metadata: Dict[str, Any] = {}
        current_list_key: Optional[str] = None
        for raw_line in header.splitlines():
            line = raw_line.rstrip()
            if not line:
                continue
            list_item = re.match(r'^\s*-\s*"?(.+?)"?\s*$', line)
            if list_item and current_list_key:
                metadata.setdefault(current_list_key, [])
                metadata[current_list_key].append(list_item.group(1).strip())
                continue
            key_match = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$', line)
            if not key_match:
                continue
            key = key_match.group(1).strip()
            value = key_match.group(2).strip()
            if value == "":
                metadata[key] = []
                current_list_key = key
            else:
                metadata[key] = value.strip('"')
                current_list_key = None
        return metadata, body

    def _render_frontmatter(self, metadata: Dict[str, Any]) -> str:
        lines = ["---"]

        scalar_key_order = (
            "type",
            "tier",
            "scopePolicy",
            "date",
            "periodStart",
            "periodEnd",
            "sourceDigest",
            "sourceRangeStart",
            "sourceRangeEnd",
            "summary",
            "missingChildrenCount",
            "presentChildrenCount",
        )
        list_key_order = (
            "tags",
            "children",
            "coverage",
            "sourceRefs",
            "sourceEvidence",
            "changedSourceRefs",
            "removedSourceRefs",
            "summaries",
        )

        emitted_keys: set[str] = set()

        def _render_scalar(key: str, value: Any) -> None:
            if value is None:
                return
            if isinstance(value, bool):
                lines.append(f"{key}: {'true' if value else 'false'}")
                return
            if isinstance(value, (int, float)):
                lines.append(f"{key}: {value}")
                return
            rendered = json.dumps(str(value), ensure_ascii=False)
            lines.append(f"{key}: {rendered}")

        def _render_list(key: str, values: Any) -> None:
            normalized = [str(item).strip() for item in list(values or []) if str(item).strip()]
            lines.append(f"{key}:")
            for item in normalized:
                lines.append(f"  - {json.dumps(item, ensure_ascii=False)}")

        for key in scalar_key_order:
            if key in metadata:
                _render_scalar(key, metadata.get(key))
                emitted_keys.add(key)

        for key in list_key_order:
            if key in metadata:
                _render_list(key, metadata.get(key))
                emitted_keys.add(key)

        for key in sorted(metadata.keys()):
            if key in emitted_keys:
                continue
            value = metadata.get(key)
            if isinstance(value, list):
                _render_list(key, value)
            else:
                _render_scalar(key, value)
        lines.append("---\n")
        return "\n".join(lines)

    def _read_frontmatter(self, path: Path) -> tuple[Dict[str, Any], str]:
        if not path.exists():
            return {}, ""
        return self._split_frontmatter(path.read_text(encoding="utf-8"))

    def _summary_date_value(self, *, tier: str, dt: date | datetime) -> str:
        target = dt.date() if isinstance(dt, datetime) else dt
        if tier == "week":
            return f"{target.year}-W{int(target.strftime('%V')):02d}"
        if tier == "month":
            return target.strftime("%Y-%m")
        if tier == "year":
            return str(target.year)
        raise ValueError(f"Unknown summary tier: {tier}")

    def _has_memory_content(self, path: Path) -> bool:
        if not path.exists():
            return False
        if path.is_file():
            return bool(path.stat().st_size)
        if (path / "summary.md").exists():
            return True
        try:
            return any(item.is_file() and _DAY_FILENAME_PATTERN.match(item.name) for item in path.rglob("*.md"))
        except Exception:
            return False

    def _extract_primary_summary(self, metadata: Dict[str, Any]) -> str:
        summary = self._normalize_summary_text(metadata.get("summary"))
        if summary:
            return summary
        for item in list(metadata.get("summaries") or []):
            value = self._normalize_summary_text(item)
            if value:
                return value
        return ""

    def _normalize_summary_text(self, value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
        text = re.sub(r"`([^`]+)`", r"\1", text)
        text = text.replace("**", "").replace("__", "").replace("*", "")
        text = re.sub(r"^\s*[-#>]+\s*", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _extract_summary_candidate_from_body(self, body: str) -> str:
        normalized = str(body or "").strip()
        if not normalized:
            return ""
        without_headings = "\n".join(
            line for line in normalized.splitlines() if not re.match(r"^\s*#+\s+", line)
        ).strip()
        paragraphs = [segment.strip() for segment in re.split(r"\n\s*\n", without_headings) if segment.strip()]
        for paragraph in paragraphs:
            compact = re.sub(r"^\s*[-*]\s*", "", paragraph, flags=re.MULTILINE)
            excerpt = self._read_summary_excerpt_from_text(self._normalize_summary_text(compact), limit=180)
            if excerpt:
                return excerpt
        return self._read_summary_excerpt_from_text(self._normalize_summary_text(normalized), limit=180) or ""

    def _read_summary_coverage(self, metadata: Dict[str, Any], *, limit: int | None = None) -> List[str]:
        coverage = [str(item).strip() for item in list(metadata.get("coverage") or []) if str(item).strip()]
        if limit is None or limit <= 0:
            return coverage
        if len(coverage) <= limit:
            return coverage
        return [*coverage[:limit], f"...({len(coverage) - limit} more)"]

    def _build_periodic_summary_metadata(
        self,
        *,
        tier: str,
        dt: date | datetime,
        summary: str,
        scope_policy: str = _PERIODIC_SUMMARY_GLOBAL_POLICY,
        source_metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        target = dt.date() if isinstance(dt, datetime) else dt
        start_date, end_date = self._period_date_bounds(tier, datetime.combine(target, datetime.min.time()))
        date_value = self._summary_date_value(tier=tier, dt=target)
        tags: List[str] = []
        children: List[str] = []
        coverage: List[str] = []
        present_children_count = 0
        missing_children_count = 0

        if tier == "week":
            cursor = start_date
            while cursor <= end_date:
                log_path = self._get_daily_log_path(datetime.combine(cursor, datetime.min.time()))
                label = cursor.strftime("%Y-%m-%d")
                has_record = bool(
                    log_path.exists()
                    and self._read_scoped_daily_entries(
                        log_path=log_path,
                        allowed_scopes=["global"],
                        max_entries_per_day=1,
                    )
                )
                coverage.append(f"{label}: {'有记录' if has_record else '未产生记录'}")
                if has_record:
                    tags.append(label)
                    children.append(self._day_memory_ref(cursor))
                    present_children_count += 1
                else:
                    missing_children_count += 1
                cursor += timedelta(days=1)
        elif tier == "month":
            current = start_date
            week_status: Dict[str, bool] = {}
            while current <= end_date:
                week_label = f"{current.year}-W{int(current.strftime('%V')):02d}"
                _memory_ref, summary_path, _label = self._resolve_summary_target("week", datetime.combine(current, datetime.min.time()))
                child_metadata, _child_body = self._read_frontmatter(summary_path)
                has_record = (
                    summary_path.exists()
                    and str(child_metadata.get("scopePolicy") or "").strip()
                    == _PERIODIC_SUMMARY_GLOBAL_POLICY
                )
                week_status[week_label] = bool(week_status.get(week_label)) or has_record
                current += timedelta(days=1)
            for week_label, has_record in week_status.items():
                coverage.append(f"{week_label}: {'有周记摘要' if has_record else '缺周记摘要'}")
                if has_record:
                    tags.append(week_label)
                    children.append(self._memory_ref("week", week_label))
                    present_children_count += 1
                else:
                    missing_children_count += 1
        elif tier == "year":
            for month in range(1, 13):
                month_label = f"{target.year}-{month:02d}"
                _memory_ref, summary_path, _label = self._resolve_summary_target("month", date(target.year, month, 1))
                child_metadata, _child_body = self._read_frontmatter(summary_path)
                has_record = (
                    summary_path.exists()
                    and str(child_metadata.get("scopePolicy") or "").strip()
                    == _PERIODIC_SUMMARY_GLOBAL_POLICY
                )
                coverage.append(f"{month_label}: {'有月记摘要' if has_record else '缺月记摘要'}")
                if has_record:
                    tags.append(month_label)
                    children.append(self._month_memory_ref(target.year, month))
                    present_children_count += 1
                else:
                    missing_children_count += 1
        else:
            raise ValueError(f"Unknown summary tier: {tier}")

        metadata: Dict[str, Any] = {
            "type": "periodic_summary",
            "tier": tier,
            "scopePolicy": str(scope_policy or "").strip(),
            "date": date_value,
            "periodStart": start_date.strftime("%Y-%m-%d"),
            "periodEnd": end_date.strftime("%Y-%m-%d"),
            "summary": str(summary or "").strip(),
            "tags": tags,
            "children": children,
            "coverage": coverage,
            "missingChildrenCount": missing_children_count,
            "presentChildrenCount": present_children_count,
        }
        # Source provenance is computed by code before the Memory Agent is
        # invoked.  It is deliberately additive so old summaries remain
        # readable and can be regenerated once to obtain a digest.
        for key in (
            "sourceDigest",
            "sourceRangeStart",
            "sourceRangeEnd",
        ):
            value = (source_metadata or {}).get(key)
            if value not in (None, ""):
                metadata[key] = str(value)
        for key in ("sourceRefs", "sourceEvidence", "changedSourceRefs", "removedSourceRefs"):
            values = (source_metadata or {}).get(key)
            if isinstance(values, list):
                metadata[key] = [str(item).strip() for item in values if str(item).strip()]
        return metadata

    def _is_complete_periodic_summary_metadata(
        self,
        *,
        tier: str,
        dt: date | datetime,
        metadata: Dict[str, Any],
    ) -> bool:
        if str(metadata.get("type") or "").strip() != "periodic_summary":
            return False
        if str(metadata.get("tier") or "").strip() != tier:
            return False
        if str(metadata.get("scopePolicy") or "").strip() != _PERIODIC_SUMMARY_GLOBAL_POLICY:
            return False
        if str(metadata.get("date") or "").strip() != self._summary_date_value(tier=tier, dt=dt):
            return False
        required_scalars = (
            "periodStart",
            "periodEnd",
            "summary",
            "missingChildrenCount",
            "presentChildrenCount",
        )
        for key in required_scalars:
            if metadata.get(key) in {None, ""}:
                return False
        for key in ("tags", "children", "coverage"):
            if key not in metadata or not isinstance(metadata.get(key), list):
                return False
        return True

    def _render_periodic_summary_document(
        self,
        *,
        tier: str,
        dt: date | datetime,
        summary: str,
        body: str,
        scope_policy: str = _PERIODIC_SUMMARY_GLOBAL_POLICY,
        source_metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        metadata = self._build_periodic_summary_metadata(
            tier=tier,
            dt=dt,
            summary=summary,
            scope_policy=scope_policy,
            source_metadata=source_metadata,
        )
        rendered = self._render_frontmatter(metadata)
        normalized_body = str(body or "").strip()
        if normalized_body:
            rendered += normalized_body.rstrip() + "\n"
        return rendered

    def _memory_maintenance_summary_options(self) -> Dict[str, int]:
        try:
            from core.storage import storage

            cfg = storage.get_memory_config() or {}
        except Exception:
            cfg = {}
        maintenance = cfg.get("maintenance") if isinstance(cfg.get("maintenance"), dict) else {}
        summary = maintenance.get("summary") if isinstance(maintenance.get("summary"), dict) else {}

        def _int_option(key: str, fallback: int, minimum: int, maximum: int) -> int:
            try:
                return max(minimum, min(int(summary.get(key) or fallback), maximum))
            except (TypeError, ValueError):
                return fallback

        return {
            "maxEntryChars": _int_option("maxEntryChars", _DAILY_ENTRY_MAX_CHARS_DEFAULT, 500, 30000),
            "maxEntriesPerDay": _int_option("maxEntriesPerDay", 8, 1, 24),
            "summaryExcerptChars": _int_option("summaryExcerptChars", _DAILY_ENTRY_SUMMARY_EXCERPT_CHARS_DEFAULT, 160, 2400),
            "childSummaryExcerptChars": _int_option("childSummaryExcerptChars", _SUMMARY_CHILD_EXCERPT_CHARS_DEFAULT, 240, 4000),
        }

    def _truncate_for_daily_entry(self, content: str, *, max_chars: int) -> tuple[str, bool, int]:
        normalized = str(content or "").strip()
        original_len = len(normalized)
        if original_len <= max_chars:
            return normalized, False, original_len
        trimmed = normalized[: max(0, max_chars - 1)].rstrip() + "…"
        return trimmed, True, original_len

    def append_daily_log_with_yaml(
        self,
        content: str,
        session_summary: str,
        session_tags: List[str],
        entry_metadata: Optional[Dict[str, Any]] = None,
    ):
        """追加结构化日志并稳定维护 YAML frontmatter。"""
        now = datetime.now()
        log_path = self._get_daily_log_path(now)
        time_str = now.strftime("%H:%M")

        if log_path.exists() and log_path.stat().st_size > 0:
            existing_meta, existing_body = self._split_frontmatter(log_path.read_text(encoding="utf-8"))
        else:
            existing_meta, existing_body = {}, ""

        merged_tags = list(dict.fromkeys([*list(existing_meta.get("tags") or []), *[str(tag).strip() for tag in session_tags if str(tag).strip()]]))
        merged_summaries = list(
            dict.fromkeys(
                [*list(existing_meta.get("summaries") or []), *([session_summary.strip()] if str(session_summary or "").strip() else [])]
            )
        )
        try:
            existing_entry_count = int(existing_meta.get("entryCount") or 0)
        except (TypeError, ValueError):
            existing_entry_count = 0
        try:
            existing_truncated_count = int(existing_meta.get("truncatedEntryCount") or 0)
        except (TypeError, ValueError):
            existing_truncated_count = 0
        summary_options = self._memory_maintenance_summary_options()
        max_entry_chars = summary_options["maxEntryChars"]
        trimmed_content, content_truncated, original_content_chars = self._truncate_for_daily_entry(
            content,
            max_chars=max_entry_chars,
        )
        meta = {
            "type": "daily_log",
            "date": now.strftime("%Y-%m-%d"),
            "entryCount": existing_entry_count + 1,
            "truncatedEntryCount": existing_truncated_count + (1 if content_truncated else 0),
            "maxEntryChars": max_entry_chars,
            "tags": merged_tags,
            "summaries": merged_summaries,
        }

        entry_metadata = dict(entry_metadata or {})
        session_id = entry_metadata.get("session_id") or "unknown"
        structured_lines = [
            f"session_id: {session_id}",
            f"effective_memory_scope: {entry_metadata.get('effective_memory_scope') or 'global'}",
            f"source_runtime: {entry_metadata.get('source_runtime') or 'chat'}",
            f"provenance_class: {entry_metadata.get('provenance_class') or 'human_dialogue'}",
            f"memory_policy: {entry_metadata.get('memory_policy') or 'durable'}",
            f"extracted_long_term_items_count: {int(entry_metadata.get('extracted_long_term_items_count') or 0)}",
            f"summary: {session_summary.strip() if str(session_summary or '').strip() else 'n/a'}",
            f"content_truncated: {'true' if content_truncated else 'false'}",
            f"original_content_chars: {original_content_chars}",
            f"detail_ref: session://{session_id}" if content_truncated and session_id != "unknown" else "",
            "",
            trimmed_content,
        ]
        entry = f"\n### {time_str}\n" + "\n".join(line for line in structured_lines if line is not None).strip() + "\n"
        log_path.write_text(self._render_frontmatter(meta) + (existing_body.rstrip() + "\n" if existing_body.strip() else "") + entry, encoding="utf-8")
        logger.info(f"[MemoryStore] Appended daily log with YAML updates to {log_path}")
    
    def _read_scoped_daily_entries(
        self,
        *,
        log_path: Path,
        allowed_scopes: List[str],
        max_entries_per_day: int = 8,
        max_entry_chars: Optional[int] = None,
    ) -> List[str]:
        content = log_path.read_text(encoding="utf-8")
        body = content
        header_match = re.match(r'^---\n.*?\n---\s*', content, flags=re.DOTALL)
        if header_match:
            body = content[header_match.end():]

        sections = re.split(r'\n(?=###\s+\d{2}:\d{2}\n)', body)
        matched: List[str] = []
        for section in sections:
            snippet = section.strip()
            if not snippet.startswith("### "):
                continue
            if allowed_scopes:
                if not any(f"effective_memory_scope: {scope}" in snippet for scope in allowed_scopes):
                    continue
            if max_entry_chars and len(snippet) > max_entry_chars:
                snippet = snippet[: max(0, max_entry_chars - 1)].rstrip() + "…"
            matched.append(snippet)
        return matched[-max_entries_per_day:]

    def _summaries_from_scoped_daily_entries(
        self,
        entries: List[str],
        *,
        limit: int = 4,
        max_chars: int = _DAILY_ENTRY_SUMMARY_EXCERPT_CHARS_DEFAULT,
    ) -> List[str]:
        summaries: List[str] = []
        for entry in entries:
            match = re.search(r"(?m)^summary:\s*(.+?)\s*$", str(entry or ""))
            if not match:
                continue
            summary = self._normalize_summary_text(match.group(1))
            if not summary or summary.lower() == "n/a":
                continue
            excerpt = self._read_summary_excerpt_from_text(summary, limit=max_chars)
            if excerpt and excerpt not in summaries:
                summaries.append(excerpt)
            if len(summaries) >= max(1, limit):
                break
        return summaries

    def _memory_ref(self, kind: str, value: str) -> str:
        return f"memory://{kind}/{value}"

    def _day_memory_ref(self, dt: date | datetime) -> str:
        target = dt.date() if isinstance(dt, datetime) else dt
        return self._memory_ref("day", target.strftime("%Y-%m-%d"))

    def _week_memory_ref(self, year: int, week: int) -> str:
        return self._memory_ref("week", f"{year}-W{int(week):02d}")

    def _month_memory_ref(self, year: int, month: int) -> str:
        return self._memory_ref("month", f"{year}-{int(month):02d}")

    def _year_memory_ref(self, year: int) -> str:
        return self._memory_ref("year", str(year))

    def _parse_memory_ref(self, memory_ref: str) -> tuple[str, str]:
        raw = str(memory_ref or "").strip()
        if not raw.startswith("memory://"):
            raise ValueError(f"Invalid memory ref: {memory_ref}")
        remainder = raw[len("memory://") :]
        kind, _, value = remainder.partition("/")
        if not kind or not value:
            raise ValueError(f"Invalid memory ref: {memory_ref}")
        return kind, value

    def _resolve_day_date(self, memory_ref_or_date: str) -> date:
        raw = str(memory_ref_or_date or "").strip()
        if raw.startswith("memory://"):
            kind, value = self._parse_memory_ref(raw)
            if kind != "day":
                raise ValueError(f"memory_read_day only accepts day refs, got: {raw}")
            raw = value
        return date.fromisoformat(raw)

    def _resolve_summary_target(self, tier: str, dt: date | datetime) -> tuple[str, Path, str]:
        target = dt.date() if isinstance(dt, datetime) else dt
        year = target.year
        month_name = target.strftime("%m_%B").lower()
        week_num = int(target.strftime("%V"))
        base_dir = MEMORY_ROOT / "daily" / str(year)
        if tier == "week":
            return self._week_memory_ref(year, week_num), base_dir / month_name / f"week_{week_num:02d}" / "summary.md", f"Week {week_num:02d}"
        if tier == "month":
            return self._month_memory_ref(year, target.month), base_dir / month_name / "summary.md", target.strftime("%Y-%m")
        if tier == "year":
            return self._year_memory_ref(year), base_dir / "summary.md", str(year)
        raise ValueError(f"Unknown summary tier: {tier}")

    def _read_summary_excerpt_from_text(self, text: str, *, limit: int = 180) -> str | None:
        normalized = str(text or "").strip()
        if not normalized:
            return None
        compact = re.sub(r"\s+", " ", normalized)
        if len(compact) <= limit:
            return compact
        return compact[: limit - 3].rstrip() + "..."

    def _read_summary_excerpt(self, summary_path: Path) -> str | None:
        if not summary_path.exists():
            return None
        try:
            metadata, body = self._read_frontmatter(summary_path)
            primary_summary = self._extract_primary_summary(metadata)
            if primary_summary:
                return self._read_summary_excerpt_from_text(primary_summary)
            return self._read_summary_excerpt_from_text(body)
        except Exception:
            return None

    def _read_periodic_summary_compact(self, *, summary_path: Path, memory_ref: str, label: str, max_chars: int) -> str | None:
        if not summary_path.exists():
            return None
        try:
            metadata, body = self._read_frontmatter(summary_path)
            if str(metadata.get("scopePolicy") or "").strip() != _PERIODIC_SUMMARY_GLOBAL_POLICY:
                return None
            summary_text = self._extract_primary_summary(metadata) or self._extract_summary_candidate_from_body(body)
            if not summary_text:
                return None
            excerpt = self._read_summary_excerpt_from_text(summary_text, limit=max_chars)
            if not excerpt:
                return None
            return f"[{label} Summary] Ref: {memory_ref}\nSummary: {excerpt}"
        except Exception:
            return None

    def _iter_year_dirs(self) -> List[Path]:
        daily_root = MEMORY_ROOT / "daily"
        if not daily_root.exists():
            return []
        return sorted([item for item in daily_root.iterdir() if item.is_dir() and item.name.isdigit()], key=lambda item: item.name)

    def _iter_month_dirs(self, year_dir: Path) -> List[Path]:
        return sorted([item for item in year_dir.iterdir() if item.is_dir() and re.match(r"^\d{2}_", item.name)], key=lambda item: item.name)

    def _iter_day_log_paths(self, month_dir: Path) -> List[Path]:
        return sorted(
            [
                item
                for item in month_dir.rglob("*.md")
                if item.is_file() and item.name != "summary.md" and _DAY_FILENAME_PATTERN.match(item.name)
            ],
            key=lambda item: item.name,
        )

    def _latest_memory_day_date(self) -> date | None:
        latest: date | None = None
        for year_dir in self._iter_year_dirs():
            for month_dir in self._iter_month_dirs(year_dir):
                for day_path in self._iter_day_log_paths(month_dir):
                    try:
                        day_date = date.fromisoformat(day_path.stem)
                    except ValueError:
                        continue
                    if latest is None or day_date > latest:
                        latest = day_date
        return latest

    def _summary_state(self, *, summary_path: Path, descendant_paths: List[Path]) -> tuple[bool, str]:
        if not summary_path.exists():
            return False, "missing"
        try:
            metadata, _body = self._read_frontmatter(summary_path)
            if str(metadata.get("scopePolicy") or "").strip() != _PERIODIC_SUMMARY_GLOBAL_POLICY:
                return True, "stale"
            # Verified summaries created before source-digest tracking need a
            # one-time refresh. After that, mtime-only staleness can be closed
            # without another model call.
            if not str(metadata.get("sourceDigest") or "").strip():
                return True, "stale"
        except Exception:
            return True, "stale"
        if not descendant_paths:
            return True, "present"
        try:
            summary_mtime = summary_path.stat().st_mtime
            latest_descendant_mtime = max(path.stat().st_mtime for path in descendant_paths)
            if latest_descendant_mtime > summary_mtime:
                return True, "stale"
        except Exception:
            pass
        return True, "present"

    def _build_memory_calendar(self) -> List[Dict[str, Any]]:
        years: List[Dict[str, Any]] = []
        for year_dir in self._iter_year_dirs():
            year_value = int(year_dir.name)
            month_nodes: List[Dict[str, Any]] = []
            year_day_paths: List[Path] = []
            year_latest_day: str | None = None

            for month_dir in self._iter_month_dirs(year_dir):
                month_part = month_dir.name.split("_", 1)[0]
                month_value = int(month_part)
                day_paths = self._iter_day_log_paths(month_dir)
                month_summary_path = month_dir / "summary.md"
                has_week_summary = any(month_dir.glob("week_*/summary.md"))
                if not day_paths and not month_summary_path.exists() and not has_week_summary:
                    continue
                year_day_paths.extend(day_paths)
                latest_month_day = day_paths[-1].stem if day_paths else None
                if latest_month_day and (year_latest_day is None or latest_month_day > year_latest_day):
                    year_latest_day = latest_month_day

                week_groups: Dict[str, Dict[str, Any]] = {}
                day_nodes: List[Dict[str, Any]] = []
                for day_path in day_paths:
                    day_date = date.fromisoformat(day_path.stem)
                    frontmatter_summaries = self._read_daily_frontmatter_summaries(log_path=day_path)
                    day_node = {
                        "memoryRef": self._day_memory_ref(day_date),
                        "kind": "day",
                        "label": day_date.strftime("%Y-%m-%d"),
                        "hasSummary": bool(frontmatter_summaries),
                        "summaryState": "present" if frontmatter_summaries else "missing",
                        "dayCount": 1,
                        "latestDay": day_date.strftime("%Y-%m-%d"),
                        "summaryExcerpt": self._read_summary_excerpt_from_text(frontmatter_summaries[0]) if frontmatter_summaries else None,
                        "_path": day_path,
                    }
                    day_nodes.append(day_node)
                    week_key = f"{int(day_date.strftime('%V')):02d}"
                    bucket = week_groups.setdefault(
                        week_key,
                        {
                            "days": [],
                            "latestDay": None,
                        },
                    )
                    bucket["days"].append(day_node)
                    if bucket["latestDay"] is None or day_node["latestDay"] > bucket["latestDay"]:
                        bucket["latestDay"] = day_node["latestDay"]

                week_nodes: List[Dict[str, Any]] = []
                for week_key in sorted(week_groups.keys()):
                    bucket = week_groups[week_key]
                    descendant_paths = [item["_path"] for item in bucket["days"]]
                    week_summary_path = month_dir / f"week_{week_key}" / "summary.md"
                    has_summary, summary_state = self._summary_state(summary_path=week_summary_path, descendant_paths=descendant_paths)
                    week_nodes.append(
                        {
                            "memoryRef": self._week_memory_ref(year_value, int(week_key)),
                            "kind": "week",
                            "label": f"{year_value}-W{week_key}",
                            "hasSummary": has_summary,
                            "summaryState": summary_state,
                            "dayCount": len(bucket["days"]),
                            "latestDay": bucket["latestDay"],
                            "summaryExcerpt": self._read_summary_excerpt(week_summary_path),
                            "_summary_path": week_summary_path,
                            "_paths": descendant_paths,
                            "children": bucket["days"],
                        }
                    )

                month_has_summary, month_summary_state = self._summary_state(summary_path=month_summary_path, descendant_paths=day_paths)
                month_nodes.append(
                    {
                        "memoryRef": self._month_memory_ref(year_value, month_value),
                        "kind": "month",
                        "label": f"{year_value}-{month_value:02d}",
                        "hasSummary": month_has_summary,
                        "summaryState": month_summary_state,
                        "dayCount": len(day_paths),
                        "latestDay": latest_month_day,
                        "summaryExcerpt": self._read_summary_excerpt(month_summary_path),
                        "_summary_path": month_summary_path,
                        "_paths": list(day_paths),
                        "children": week_nodes,
                    }
                )

            year_summary_path = year_dir / "summary.md"
            if not year_day_paths and not month_nodes and not year_summary_path.exists():
                continue
            year_has_summary, year_summary_state = self._summary_state(summary_path=year_summary_path, descendant_paths=year_day_paths)
            years.append(
                {
                    "memoryRef": self._year_memory_ref(year_value),
                    "kind": "year",
                    "label": str(year_value),
                    "hasSummary": year_has_summary,
                    "summaryState": year_summary_state,
                    "dayCount": len(year_day_paths),
                    "latestDay": year_latest_day,
                    "summaryExcerpt": self._read_summary_excerpt(year_summary_path),
                    "_summary_path": year_summary_path,
                    "_paths": list(year_day_paths),
                    "children": month_nodes,
                }
            )
        return years

    def _shallow_memory_node(self, node: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "memoryRef": node.get("memoryRef"),
            "kind": node.get("kind"),
            "label": node.get("label"),
            "hasSummary": bool(node.get("hasSummary")),
            "summaryState": node.get("summaryState") or "missing",
            "dayCount": int(node.get("dayCount") or 0),
            "latestDay": node.get("latestDay"),
            "summaryExcerpt": node.get("summaryExcerpt"),
        }

    def _find_memory_node(self, memory_ref: str) -> Dict[str, Any] | None:
        def _walk(nodes: List[Dict[str, Any]]) -> Dict[str, Any] | None:
            for node in nodes:
                if node.get("memoryRef") == memory_ref:
                    return node
                child_match = _walk(node.get("children") or [])
                if child_match:
                    return child_match
            return None

        return _walk(self._build_memory_calendar())

    def build_memory_map(self, anchor_date: Optional[str] = None) -> Dict[str, Any]:
        anchor = date.fromisoformat(anchor_date) if str(anchor_date or "").strip() else datetime.now().date()
        years = self._build_memory_calendar()
        return {
            "anchorDate": anchor.strftime("%Y-%m-%d"),
            "currentRefs": {
                "year": self._year_memory_ref(anchor.year),
                "month": self._month_memory_ref(anchor.year, anchor.month),
                "week": self._week_memory_ref(anchor.year, int(anchor.strftime("%V"))),
                "day": self._day_memory_ref(anchor),
            },
            "items": [self._shallow_memory_node(node) for node in years],
        }

    def expand_memory_map(self, memory_ref: str) -> Dict[str, Any]:
        node = self._find_memory_node(str(memory_ref or "").strip())
        if not node:
            raise ValueError(f"Unknown memory ref: {memory_ref}")
        return {
            "memoryRef": node.get("memoryRef"),
            "kind": node.get("kind"),
            "label": node.get("label"),
            "children": [self._shallow_memory_node(child) for child in node.get("children") or []],
        }

    def read_memory_day(
        self,
        memory_ref_or_date: str,
        scope_chain: Optional[List[str]] = None,
    ) -> str:
        day_date = self._resolve_day_date(memory_ref_or_date)
        log_path = self._get_daily_log_path(datetime.combine(day_date, datetime.min.time()))
        memory_ref = self._day_memory_ref(day_date)
        if log_path.exists():
            if scope_chain is not None:
                allowed_scopes = [
                    item
                    for item in self._normalize_scope_chain(scope_chain=scope_chain)
                    if self._is_valid_scope(item)
                ]
                entries = self._read_scoped_daily_entries(
                    log_path=log_path,
                    allowed_scopes=allowed_scopes,
                    max_entries_per_day=100,
                )
                if not entries:
                    return (
                        f"No daily memory visible to the current workspace for "
                        f"{day_date.strftime('%Y-%m-%d')} (Ref: {memory_ref})."
                    )
                return (
                    f"[{day_date.strftime('%Y-%m-%d')}] Ref: {memory_ref}\n"
                    + "\n\n".join(entries)
                )
            return f"[{day_date.strftime('%Y-%m-%d')}] Ref: {memory_ref}\n{log_path.read_text(encoding='utf-8')}"
        return f"No daily log found for {day_date.strftime('%Y-%m-%d')} (Ref: {memory_ref})."

    def _resolve_passive_context_options(self, *, memory_config: dict[str, Any]) -> dict[str, Any]:
        profile = str(memory_config.get("passive_context_profile") or "balanced").strip().lower() or "balanced"
        if profile not in {"light", "balanced", "detailed"}:
            profile = "balanced"

        defaults_by_profile = {
            "light": {
                "summaryEnabled": True,
                "memoryMapEnabled": True,
                "recentActivityTeaserEnabled": False,
                "recentActivityTeaserLimit": 1,
                "memoryMapNodeLimit": 3,
            },
            "balanced": {
                "summaryEnabled": True,
                "memoryMapEnabled": True,
                "recentActivityTeaserEnabled": True,
                "recentActivityTeaserLimit": 2,
                "memoryMapNodeLimit": 4,
            },
            "detailed": {
                "summaryEnabled": True,
                "memoryMapEnabled": True,
                "recentActivityTeaserEnabled": True,
                "recentActivityTeaserLimit": 4,
                "memoryMapNodeLimit": 6,
            },
        }
        defaults = defaults_by_profile[profile]

        def _resolve_bool(key: str, fallback: bool) -> bool:
            raw = memory_config.get(key)
            if raw is None:
                return fallback
            return bool(raw)

        def _resolve_int(key: str, fallback: int, minimum: int, maximum: int) -> int:
            try:
                return max(minimum, min(int(memory_config.get(key) or fallback), maximum))
            except (TypeError, ValueError):
                return fallback

        return {
            "profile": profile,
            "summaryEnabled": _resolve_bool("passive_summary_enabled", defaults["summaryEnabled"]),
            "memoryMapEnabled": _resolve_bool("passive_memory_map_enabled", defaults["memoryMapEnabled"]),
            "recentActivityTeaserEnabled": _resolve_bool(
                "passive_recent_activity_teaser_enabled",
                defaults["recentActivityTeaserEnabled"],
            ),
            "recentActivityTeaserLimit": _resolve_int(
                "passive_recent_activity_teaser_limit",
                defaults["recentActivityTeaserLimit"],
                1,
                12,
            ),
            "memoryMapNodeLimit": _resolve_int(
                "passive_memory_map_node_limit",
                defaults["memoryMapNodeLimit"],
                1,
                12,
            ),
            "knowledgeGraphSummaryEnabled": _resolve_bool("passive_knowledge_graph_summary_enabled", True),
            "knowledgeGraphSummaryMaxRelations": _resolve_int(
                "passive_knowledge_graph_summary_max_relations",
                5,
                1,
                12,
            ),
            "knowledgeGraphSummaryMaxChars": _resolve_int(
                "passive_knowledge_graph_summary_max_chars",
                720,
                240,
                2000,
            ),
        }

    def _build_knowledge_graph_summary_for_injection(
        self,
        *,
        query: str,
        scope: str,
        scope_chain: List[str],
        max_relations: int,
        max_chars: int,
    ) -> tuple[str, Dict[str, Any]]:
        diagnostics: Dict[str, Any] = {
            "graphSummaryInjected": False,
            "graphSummaryRelationCount": 0,
            "graphSummarySeedEntities": [],
            "graphSummaryTrimmed": False,
            "graphSummaryRejectReason": "",
        }
        if not str(query or "").strip():
            diagnostics["graphSummaryRejectReason"] = "empty_query"
            return "", diagnostics

        try:
            preview = self._execute_unified_recall(
                query=query,
                limit=max(max_relations, 3),
                scope=scope,
                scopes=scope_chain,
                recall_strategy_override="keyword",
                allow_rerank=False,
            )
        except Exception as exc:
            diagnostics["graphSummaryRejectReason"] = f"recall_failed:{exc}"
            return "", diagnostics

        recall_diagnostics = dict(preview.get("diagnostics") or {})
        diagnostics["graphSummarySeedEntities"] = list(recall_diagnostics.get("graph_entities") or [])
        if recall_diagnostics.get("graph_reject_reason"):
            diagnostics["graphSummaryRejectReason"] = str(recall_diagnostics.get("graph_reject_reason") or "")

        graph_items = [
            item for item in list(preview.get("accepted_items") or [])
            if str(item.get("source") or "").find("graph") >= 0
            or str(item.get("category") or "") == "graph_context"
        ]
        if not graph_items:
            if not diagnostics["graphSummaryRejectReason"]:
                diagnostics["graphSummaryRejectReason"] = "no_accepted_graph_items"
            return "", diagnostics

        lines: List[str] = []
        seen: set[str] = set()
        for item in graph_items:
            fact = str(item.get("fact") or "").strip()
            fact = re.sub(r"^\[Graph Context\]\s*", "", fact).strip()
            if not fact or fact in seen:
                continue
            seen.add(fact)
            lines.append(f"- {fact}")
            if len(lines) >= max_relations:
                break
        summary = "\n".join(lines).strip()
        if not summary:
            diagnostics["graphSummaryRejectReason"] = "empty_graph_summary"
            return "", diagnostics

        if len(summary) > max_chars:
            summary = summary[: max_chars - 3].rstrip() + "..."
            diagnostics["graphSummaryTrimmed"] = True
        diagnostics["graphSummaryInjected"] = True
        diagnostics["graphSummaryRelationCount"] = len(lines)
        diagnostics["graphSummaryRejectReason"] = ""
        return summary, diagnostics

    def _build_memory_consistency_note_for_injection(
        self,
        *,
        active_preferences: Dict[str, str],
        passive_text: str,
    ) -> tuple[str, Dict[str, Any]]:
        diagnostics: Dict[str, Any] = {
            "consistencyNoteInjected": False,
            "consistencyConflicts": [],
        }
        if not active_preferences or not passive_text:
            return "", diagnostics

        value_hints: Dict[str, List[str]] = {
            "favorite_shoe_brand": ["阿迪达斯", "adidas", "耐克", "nike"],
        }
        haystack = str(passive_text or "")
        haystack_lower = haystack.lower()
        conflicts: List[Dict[str, str]] = []
        for key, current_value in active_preferences.items():
            canonical_key = canonicalize_preference_key(key)
            candidates = value_hints.get(canonical_key) or []
            if not candidates:
                continue
            current = str(current_value or "").strip()
            current_lower = current.lower()
            for candidate in candidates:
                candidate_text = str(candidate or "").strip()
                if not candidate_text:
                    continue
                if candidate_text.lower() == current_lower or candidate_text == current:
                    continue
                if candidate_text.lower() in haystack_lower:
                    conflicts.append(
                        {
                            "key": canonical_key,
                            "currentValue": current,
                            "staleValue": candidate_text,
                        }
                    )
                    break

        if not conflicts:
            return "", diagnostics

        lines = [
            "[MEMORY CONSISTENCY NOTE]",
            "Canonical active preferences are authoritative when older summaries disagree:",
        ]
        for conflict in conflicts[:4]:
            lines.append(
                f"- {conflict['key']}: use current value \"{conflict['currentValue']}\"; "
                f"older summary mentions \"{conflict['staleValue']}\"."
            )
        lines.append("[/MEMORY CONSISTENCY NOTE]")
        diagnostics["consistencyNoteInjected"] = True
        diagnostics["consistencyConflicts"] = conflicts
        return "\n".join(lines), diagnostics

    def _format_memory_map_for_injection(
        self,
        anchor_date: Optional[str] = None,
        *,
        node_limit: int = 4,
        scope_chain: Optional[List[str]] = None,
    ) -> str:
        try:
            anchor = date.fromisoformat(anchor_date) if anchor_date else datetime.now().date()
        except ValueError:
            anchor = datetime.now().date()
        current_refs = {
            "year": self._year_memory_ref(anchor.year),
            "month": self._month_memory_ref(anchor.year, anchor.month),
            "week": self._week_memory_ref(anchor.year, int(anchor.strftime("%V"))),
            "day": self._day_memory_ref(anchor),
        }
        allowed_scopes = [
            item
            for item in self._normalize_scope_chain(scope_chain=scope_chain)
            if self._is_valid_scope(item)
        ]
        current_items: list[str] = []
        for kind in ("year", "month", "week", "day"):
            memory_ref = str(current_refs.get(kind) or "").strip()
            if not memory_ref:
                continue
            summary_state = "missing"
            excerpt = ""
            label = memory_ref.rsplit("/", 1)[-1]
            if kind == "day":
                log_path = self._get_daily_log_path(datetime.combine(anchor, datetime.min.time()))
                entries = (
                    self._read_scoped_daily_entries(
                        log_path=log_path,
                        allowed_scopes=allowed_scopes,
                        max_entries_per_day=1,
                    )
                    if log_path.exists()
                    else []
                )
                if entries:
                    summary_state = "present"
                    summaries = self._summaries_from_scoped_daily_entries(entries, limit=1)
                    excerpt = summaries[0] if summaries else ""
            else:
                _resolved_ref, summary_path, resolved_label = self._resolve_summary_target(
                    kind,
                    datetime.combine(anchor, datetime.min.time()),
                )
                label = resolved_label
                metadata, body = self._read_frontmatter(summary_path)
                if str(metadata.get("scopePolicy") or "").strip() == _PERIODIC_SUMMARY_GLOBAL_POLICY:
                    summary_state = "present"
                    excerpt = self._extract_primary_summary(metadata) or self._extract_summary_candidate_from_body(body)
                elif summary_path.exists():
                    summary_state = "stale"
            line = f"- [{kind}] {label} | Ref: {memory_ref}"
            if summary_state:
                line += f" | summary={summary_state}"
            if excerpt:
                clipped = excerpt[:120] + "..." if len(excerpt) > 120 else excerpt
                line += f" | excerpt={clipped}"
            current_items.append(line)

        parts = ["Current focus refs:"]
        if current_items:
            parts.extend(current_items)
        else:
            parts.append("- No current memory refs available.")
        parts.append("")
        parts.append("Use memory_broker(mode='expand_map', memory_ref='...') or memory_map_expand(memoryRef) to drill down. Use memory_broker(mode='read_day', memory_ref_or_date='...') or memory_read_day(memory://day/YYYY-MM-DD or YYYY-MM-DD) when you need an exact daily log.")
        return "\n".join(parts).strip()

    def _build_memory_summary_for_injection(
        self,
        *,
        detailed_days: int,
        scope_chain: Optional[List[str]] = None,
    ) -> str:
        segments: List[str] = []
        hierarchical = self.get_hierarchical_summaries(scope_chain=scope_chain)
        if hierarchical:
            segments.append(hierarchical)
        prior_summary = self.get_prior_window_memory_summary(
            detailed_days=detailed_days,
            scope_chain=scope_chain,
        )
        if prior_summary:
            segments.append(prior_summary)
        return "\n\n".join(segment for segment in segments if str(segment or "").strip()).strip()

    def _extract_entry_summary_line(self, entry: str) -> str:
        text = str(entry or "").strip()
        if not text:
            return ""
        summary_match = re.search(r"^summary:\s*(.+)$", text, flags=re.MULTILINE)
        if summary_match:
            return str(summary_match.group(1) or "").strip()
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        for line in lines:
            if line.startswith("### "):
                continue
            if ":" in line:
                continue
            return line
        return lines[-1] if lines else ""

    def _build_recent_activity_teaser(
        self,
        *,
        days: int = 1,
        scope_chain: Optional[List[str]] = None,
        max_items: int = 2,
    ) -> str:
        now = datetime.now()
        allowed_scopes = [scope for scope in self._normalize_scope_chain(scope_chain=scope_chain) if self._is_valid_scope(scope)]
        options = self._memory_maintenance_summary_options()
        items: List[str] = []

        for i in range(max(1, days)):
            date_check = now - timedelta(days=i)
            log_path = self._get_daily_log_path(date_check)
            if not log_path.exists():
                continue

            matched_entries = self._read_scoped_daily_entries(
                log_path=log_path,
                allowed_scopes=allowed_scopes,
                max_entries_per_day=1,
                max_entry_chars=options["summaryExcerptChars"],
            )
            if not matched_entries:
                continue

            summary_line = self._extract_entry_summary_line(matched_entries[-1])
            teaser = summary_line
            if not teaser:
                continue
            if len(teaser) > 140:
                teaser = teaser[:140] + "..."
            items.append(f"- [{date_check.strftime('%Y-%m-%d')}] Ref: {self._day_memory_ref(date_check)} | {teaser}")
            if len(items) >= max(1, max_items):
                break

        return "\n".join(items).strip()

    def get_memory_map_health(self) -> Dict[str, Any]:
        counts = {
            "year": 0,
            "month": 0,
            "week": 0,
            "day": 0,
            "present": 0,
            "missing": 0,
            "stale": 0,
        }
        missing_refs: List[str] = []
        stale_refs: List[str] = []

        def _walk(nodes: List[Dict[str, Any]]) -> None:
            for node in nodes:
                kind = str(node.get("kind") or "")
                if kind in counts:
                    counts[kind] += 1
                state = str(node.get("summaryState") or "missing")
                if kind in {"year", "month", "week"} and state in {"present", "missing", "stale"}:
                    counts[state] += 1
                if kind in {"year", "month", "week"} and state == "missing":
                    missing_refs.append(str(node.get("memoryRef")))
                if kind in {"year", "month", "week"} and state == "stale":
                    stale_refs.append(str(node.get("memoryRef")))
                _walk(node.get("children") or [])

        roots = self._build_memory_calendar()
        _walk(roots)
        return {
            "counts": counts,
            "missingRefs": missing_refs[:24],
            "staleRefs": stale_refs[:24],
            "hasMissingSummaries": bool(missing_refs),
            "hasStaleSummaries": bool(stale_refs),
        }

    def list_summary_targets(self, *, states: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        allowed_states = {str(item).strip() for item in (states or ["missing", "stale"]) if str(item).strip()}
        targets: List[Dict[str, Any]] = []

        def _walk(nodes: List[Dict[str, Any]]) -> None:
            for node in nodes:
                kind = str(node.get("kind") or "")
                state = str(node.get("summaryState") or "")
                if kind in {"week", "month", "year"} and state in allowed_states:
                    latest_day = str(node.get("latestDay") or "").strip()
                    if latest_day:
                        targets.append(
                            {
                                "memoryRef": node.get("memoryRef"),
                                "kind": kind,
                                "summaryState": state,
                                "latestDay": latest_day,
                            }
                        )
                _walk(node.get("children") or [])

        _walk(self._build_memory_calendar())
        return targets

    def _period_date_bounds(self, tier: str, dt: datetime) -> tuple[date, date]:
        target = dt.date()
        if tier == "week":
            week_start = target - timedelta(days=target.weekday())
            week_end = week_start + timedelta(days=6)
            return week_start, week_end
        if tier == "month":
            month_start = target.replace(day=1)
            if month_start.month == 12:
                next_month = month_start.replace(year=month_start.year + 1, month=1, day=1)
            else:
                next_month = month_start.replace(month=month_start.month + 1, day=1)
            return month_start, next_month - timedelta(days=1)
        if tier == "year":
            year_start = target.replace(month=1, day=1)
            year_end = target.replace(month=12, day=31)
            return year_start, year_end
        raise ValueError(f"Unknown summary tier: {tier}")

    def _periodic_summary_source_blocks(
        self,
        *,
        tier: str,
        dt: Optional[datetime] = None,
        scope_chain: Optional[List[str]] = None,
    ) -> List[Dict[str, str]]:
        """Build deterministic source blocks without asking the model.

        A block is the smallest unit that can be compared across maintenance
        runs: one day for a week, one verified week for a month, and one
        verified month for a year.  File timestamps are intentionally not
        included in the block content or digest.
        """
        target_dt = dt or datetime.now()
        start_date, end_date = self._period_date_bounds(tier, target_dt)
        allowed_scopes = [scope for scope in self._normalize_scope_chain(scope_chain=scope_chain) if self._is_valid_scope(scope)]
        options = self._memory_maintenance_summary_options()
        blocks: List[Dict[str, str]] = []

        if tier == "week":
            cursor = start_date
            while cursor <= end_date:
                log_path = self._get_daily_log_path(datetime.combine(cursor, datetime.min.time()))
                if log_path.exists():
                    matched_entries = self._read_scoped_daily_entries(
                        log_path=log_path,
                        allowed_scopes=allowed_scopes,
                        max_entries_per_day=min(options["maxEntriesPerDay"], 2),
                        max_entry_chars=options["summaryExcerptChars"],
                    )
                    scoped_summaries = self._summaries_from_scoped_daily_entries(
                        matched_entries,
                        limit=4,
                        max_chars=options["summaryExcerptChars"],
                    )
                    day_lines = [f"[{cursor.strftime('%Y-%m-%d')}] Ref: {self._day_memory_ref(cursor)}"]
                    if scoped_summaries:
                        day_lines.append("Summaries:")
                        day_lines.extend(f"- {item}" for item in scoped_summaries)
                    if matched_entries:
                        day_lines.append("Capped entries:")
                        day_lines.extend(matched_entries)
                    if len(day_lines) > 1:
                        blocks.append(
                            {
                                "ref": self._day_memory_ref(cursor),
                                "label": cursor.strftime("%Y-%m-%d"),
                                "content": "\n".join(line for line in day_lines if str(line or "").strip()),
                            }
                        )
                cursor += timedelta(days=1)
            return blocks

        if tier == "month":
            seen_weeks: set[str] = set()
            cursor = start_date
            while cursor <= end_date:
                week_key = f"{cursor.year}-W{int(cursor.strftime('%V')):02d}"
                if week_key not in seen_weeks:
                    seen_weeks.add(week_key)
                    memory_ref, summary_path, label = self._resolve_summary_target("week", datetime.combine(cursor, datetime.min.time()))
                    compact = self._read_periodic_summary_compact(
                        summary_path=summary_path,
                        memory_ref=memory_ref,
                        label=label,
                        max_chars=options["childSummaryExcerptChars"],
                    )
                    if compact:
                        blocks.append(
                            {
                                "ref": memory_ref,
                                "label": label,
                                "content": compact,
                            }
                        )
                cursor += timedelta(days=1)
            return blocks

        if tier == "year":
            for month in range(1, 13):
                month_dt = target_dt.replace(month=month, day=1)
                memory_ref, summary_path, label = self._resolve_summary_target("month", month_dt)
                compact = self._read_periodic_summary_compact(
                    summary_path=summary_path,
                    memory_ref=memory_ref,
                    label=label,
                    max_chars=options["childSummaryExcerptChars"],
                )
                if compact:
                    blocks.append(
                        {
                            "ref": memory_ref,
                            "label": label,
                            "content": compact,
                        }
                    )
            return blocks

        return blocks

    @staticmethod
    def _periodic_source_digest(value: str) -> str:
        normalized = "\n".join(line.rstrip() for line in str(value or "").replace("\r\n", "\n").splitlines()).strip()
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def _periodic_summary_source_path(self, *, tier: str, dt: datetime) -> Path:
        _memory_ref, summary_path, _label = self._resolve_summary_target(tier, dt)
        return summary_path

    def prepare_periodic_summary_input(
        self,
        *,
        tier: str,
        dt: Optional[datetime] = None,
        scope_chain: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Compute summary deltas and provenance before model invocation."""
        target_dt = dt or datetime.now()
        start_date, end_date = self._period_date_bounds(tier, target_dt)
        blocks = self._periodic_summary_source_blocks(tier=tier, dt=target_dt, scope_chain=scope_chain)
        source_refs = [str(block.get("ref") or "").strip() for block in blocks if str(block.get("ref") or "").strip()]
        source_evidence: List[str] = []
        block_digests: Dict[str, str] = {}
        for block in blocks:
            ref = str(block.get("ref") or "").strip()
            digest = self._periodic_source_digest(str(block.get("content") or ""))
            if ref:
                block_digests[ref] = digest
                source_evidence.append(f"{ref}|{digest}")
        digest_payload = "\n".join(f"{ref}\t{block_digests.get(ref, '')}" for ref in source_refs)
        source_digest = self._periodic_source_digest(digest_payload)
        full_content = "\n\n".join(str(block.get("content") or "").strip() for block in blocks if str(block.get("content") or "").strip()).strip()

        summary_path = self._periodic_summary_source_path(tier=tier, dt=target_dt)
        existing_metadata, existing_body = self._read_frontmatter(summary_path)
        existing_verified = (
            summary_path.exists()
            and str(existing_metadata.get("scopePolicy") or "").strip() == _PERIODIC_SUMMARY_GLOBAL_POLICY
        )
        existing_source_digest = str(existing_metadata.get("sourceDigest") or "").strip()
        existing_summary = self._extract_primary_summary(existing_metadata) or self._extract_summary_candidate_from_body(existing_body)
        previous_evidence: Dict[str, str] = {}
        for raw in list(existing_metadata.get("sourceEvidence") or []):
            raw_text = str(raw).strip()
            if "|" in raw_text:
                ref, digest = raw_text.rsplit("|", 1)
                if ref.strip() and digest.strip():
                    previous_evidence[ref.strip()] = digest.strip()
                    continue
            try:
                item = json.loads(raw_text)
                ref = str(item.get("ref") or "").strip()
                digest = str(item.get("digest") or "").strip()
                if ref and digest:
                    previous_evidence[ref] = digest
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        changed_refs = [ref for ref in source_refs if previous_evidence.get(ref) != block_digests.get(ref)]
        removed_refs = [ref for ref in previous_evidence if ref not in block_digests]
        semantic_changed = (not existing_verified) or existing_source_digest != source_digest

        # On a changed source set, only send the changed blocks plus the prior
        # narrative to the Memory Agent.  The code, not the model, determines
        # the range and evidence set.
        provenance_lines = [
            f"Code-computed source range: {start_date.strftime('%Y-%m-%d')}..{end_date.strftime('%Y-%m-%d')}",
            "Evidence refs:",
            *[f"- {ref}" for ref in source_refs],
        ]
        provenance_header = "\n".join(provenance_lines).strip()
        model_content = "\n\n".join(part for part in (provenance_header, full_content) if part).strip()
        if semantic_changed and existing_verified and (changed_refs or removed_refs):
            delta_parts: List[str] = [provenance_header]
            if existing_summary:
                delta_parts.append(f"Previous verified narrative:\n{existing_summary}")
            changed_ref_set = set(changed_refs)
            selected = [block for block in blocks if str(block.get("ref") or "") in changed_ref_set]
            if selected:
                delta_parts.append(
                    "Changed source blocks (code-computed):\n"
                    + "\n\n".join(str(block.get("content") or "").strip() for block in selected)
                )
            if removed_refs:
                delta_parts.append("Removed source refs (coverage change only):\n" + "\n".join(f"- {ref}" for ref in removed_refs))
            model_content = "\n\n".join(part for part in delta_parts if part.strip()).strip()

        return {
            "content": full_content,
            "model_content": model_content,
            "blocks": blocks,
            "source_digest": source_digest,
            "source_refs": source_refs,
            "source_evidence": source_evidence,
            "changed_source_refs": changed_refs,
            "removed_source_refs": removed_refs,
            "source_range_start": start_date.strftime("%Y-%m-%d"),
            "source_range_end": end_date.strftime("%Y-%m-%d"),
            "semantic_changed": semantic_changed,
            "existing_verified": existing_verified,
            "existing_summary": existing_summary,
            "existing_metadata": existing_metadata,
            "existing_body": existing_body,
            "summary_path": str(summary_path),
        }

    def get_logs_for_period(self, *, tier: str, dt: Optional[datetime] = None, scope_chain: Optional[List[str]] = None) -> str:
        prepared = self.prepare_periodic_summary_input(tier=tier, dt=dt, scope_chain=scope_chain)
        return str(prepared.get("content") or "").strip()

    def get_recent_logs(self, days: int = 1, scope_chain: Optional[List[str]] = None) -> str:
        """获取最近 N 天与 scope 匹配的日志条目摘要。"""
        now = datetime.now()
        summaries = []
        allowed_scopes = [scope for scope in self._normalize_scope_chain(scope_chain=scope_chain) if self._is_valid_scope(scope)]
        
        for i in range(days):
            date_check = now - timedelta(days=i)
            log_path = self._get_daily_log_path(date_check)
            
            if not log_path.exists():
                continue

            options = self._memory_maintenance_summary_options()
            matched_entries = self._read_scoped_daily_entries(
                log_path=log_path,
                allowed_scopes=allowed_scopes,
                max_entries_per_day=options["maxEntriesPerDay"],
                max_entry_chars=options["summaryExcerptChars"],
            )
            if not matched_entries:
                continue

            entry = f"[{date_check.strftime('%Y-%m-%d')}] Ref: {self._day_memory_ref(date_check)}\n" + "\n\n".join(matched_entries)
            summaries.append(entry.strip())

        return "\n\n".join(summaries) if summaries else ""

    def _read_daily_frontmatter_summaries(self, *, log_path: Path) -> List[str]:
        content = log_path.read_text(encoding="utf-8")
        header_match = re.match(r'^---\n(.*?)\n---\s*', content, flags=re.DOTALL)
        if not header_match:
            return []

        header = header_match.group(1)
        lines = header.splitlines()
        summaries: List[str] = []
        in_summaries = False
        for line in lines:
            if line.startswith("summaries:"):
                in_summaries = True
                continue
            if in_summaries and re.match(r"^[A-Za-z_][A-Za-z0-9_]*\s*:", line):
                break
            if not in_summaries:
                continue
            match = re.match(r'^\s*-\s*"?(.+?)"?\s*$', line)
            if match:
                value = match.group(1).strip()
                if value:
                    summaries.append(value)
        return summaries

    def get_prior_window_memory_summary(
        self,
        *,
        detailed_days: int = 1,
        scope_chain: Optional[List[str]] = None,
    ) -> str:
        """
        读取详细日志窗口之前一天、且只属于当前 scope 链的紧凑摘要。
        """
        normalized_chain = self._normalize_scope_chain(scope_chain=scope_chain)
        allowed_scopes = [scope for scope in normalized_chain if self._is_valid_scope(scope)]
        summary_day = max(1, int(detailed_days))
        date_check = datetime.now() - timedelta(days=summary_day)
        log_path = self._get_daily_log_path(date_check)
        if not log_path.exists():
            return ""

        matched_entries = self._read_scoped_daily_entries(
            log_path=log_path,
            allowed_scopes=allowed_scopes,
            max_entries_per_day=1,
        )
        if not matched_entries:
            return ""

        lines = [f"[{date_check.strftime('%Y-%m-%d')}] Ref: {self._day_memory_ref(date_check)}"]
        summaries = self._summaries_from_scoped_daily_entries(matched_entries, limit=4)
        if summaries:
            lines.append("Summaries:")
            lines.extend(f"- {item}" for item in summaries)
        return "\n".join(lines).strip()
        
    def get_hierarchical_summaries(self, scope_chain: Optional[List[str]] = None) -> str:
        """Read verified global-only Agent journals for every workspace."""

        now = datetime.now()
        latest_day = self._latest_memory_day_date()
        latest_anchor = datetime.combine(latest_day, datetime.min.time()) if latest_day else None
        parts = []

        for tier in ("week", "month", "year"):
            memory_ref, summary_path, label = self._resolve_summary_target(tier, now)
            if not summary_path.exists() and latest_anchor is not None:
                memory_ref, summary_path, label = self._resolve_summary_target(tier, latest_anchor)
            if not summary_path.exists():
                continue
            metadata, body = self._read_frontmatter(summary_path)
            if str(metadata.get("scopePolicy") or "").strip() != _PERIODIC_SUMMARY_GLOBAL_POLICY:
                continue
            summary_text = self._extract_primary_summary(metadata) or self._extract_summary_candidate_from_body(body)
            coverage_lines = self._read_summary_coverage(metadata, limit=None)
            lines = [f"[{label} Summary] Ref: {memory_ref}"]
            if summary_text:
                lines.append(f"Summary: {summary_text}")
            if coverage_lines:
                lines.append("Coverage:")
                lines.extend(f"- {item}" for item in coverage_lines)
            elif body.strip():
                fallback_excerpt = self._read_summary_excerpt_from_text(body, limit=220)
                if fallback_excerpt:
                    lines.append(fallback_excerpt)
            parts.append("\n".join(lines).strip())

        return "\n\n".join(parts)
        
    def read_memory_summary(
        self,
        tier: str,
        date_str: str = None,
        scope_chain: Optional[List[str]] = None,
    ) -> str:
        """
        根据层级(day, week, month, year)与指定的日期字符串，查找相应的结构化记录(日志或摘要)。
        date_str 格式必须至少包含对应的粒度，例如 YYYY-MM-DD 或 YYYY-MM，未指定则用当前时间。
        """
        dt = datetime.strptime(date_str, "%Y-%m-%d") if date_str else datetime.now()
        if tier == "day":
            return self.read_memory_day(dt.strftime("%Y-%m-%d"), scope_chain=scope_chain)

        if tier in {"week", "month", "year"}:
            memory_ref, summary_path, label = self._resolve_summary_target(tier, dt)
            if summary_path.exists():
                return f"[{label} Summary] Ref: {memory_ref}\n{summary_path.read_text(encoding='utf-8')}"
            return f"No {tier} summary found for {label} (Ref: {memory_ref})."

        return f"Unknown tier: {tier}"

    def save_periodic_summary(self, tier: str, payload: Dict[str, Any], dt: datetime = None):
        """保存更高层级的聚合摘要"""
        dt = dt or datetime.now()
        year = dt.strftime("%Y")
        month_name = dt.strftime("%m_%B").lower()
        week_num = int(dt.strftime("%V"))
        
        base_dir = MEMORY_ROOT / "daily" / year
        
        if tier == "week":
            path = base_dir / month_name / f"week_{week_num:02d}" / "summary.md"
        elif tier == "month":
            path = base_dir / month_name / "summary.md"
        elif tier == "year":
            path = base_dir / "summary.md"
        else:
            raise ValueError(f"Unknown summary tier: {tier}")

        payload = dict(payload or {})
        summary = str(payload.get("summary") or "").strip()
        body = str(payload.get("body") or "").strip()
        source_metadata = payload.get("sourceMetadata") if isinstance(payload.get("sourceMetadata"), dict) else {}
        if not source_metadata:
            prepared = self.prepare_periodic_summary_input(tier=tier, dt=dt, scope_chain=["global"])
            source_metadata = {
                "sourceDigest": str(prepared.get("source_digest") or ""),
                "sourceRangeStart": str(prepared.get("source_range_start") or ""),
                "sourceRangeEnd": str(prepared.get("source_range_end") or ""),
                "sourceRefs": list(prepared.get("source_refs") or []),
                "sourceEvidence": list(prepared.get("source_evidence") or []),
                "changedSourceRefs": list(prepared.get("changed_source_refs") or []),
                "removedSourceRefs": list(prepared.get("removed_source_refs") or []),
            }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            self._render_periodic_summary_document(
                tier=tier,
                dt=dt,
                summary=summary,
                body=body,
                source_metadata=source_metadata,
            ),
            encoding="utf-8",
        )
        logger.info(f"[MemoryStore] Saved {tier} memory summary to {path}")

    def _backfill_periodic_summary_file(self, *, tier: str, dt: date | datetime, summary_path: Path) -> bool:
        metadata, body = self._read_frontmatter(summary_path)
        if self._is_complete_periodic_summary_metadata(tier=tier, dt=dt, metadata=metadata):
            return False
        current_scope_policy = str(metadata.get("scopePolicy") or "").strip()
        if current_scope_policy == _PERIODIC_SUMMARY_LEGACY_POLICY:
            return False
        summary_text = self._extract_primary_summary(metadata) or self._extract_summary_candidate_from_body(body)
        summary_path.write_text(
            self._render_periodic_summary_document(
                tier=tier,
                dt=dt,
                summary=summary_text,
                body=body,
                # Metadata backfill cannot prove that an old summary was
                # generated from global-only entries. Keep it visible to
                # maintenance as stale, but never inject it into a workspace.
                scope_policy=_PERIODIC_SUMMARY_LEGACY_POLICY,
            ),
            encoding="utf-8",
        )
        logger.info(f"[MemoryStore] Backfilled {tier} memory summary frontmatter at {summary_path}")
        return True

    def backfill_periodic_summaries(self) -> Dict[str, Any]:
        touched_refs: List[str] = []
        for year_dir in self._iter_year_dirs():
            year_value = int(year_dir.name)
            year_summary_path = year_dir / "summary.md"
            if year_summary_path.exists() and self._backfill_periodic_summary_file(
                tier="year",
                dt=date(year_value, 1, 1),
                summary_path=year_summary_path,
            ):
                touched_refs.append(self._year_memory_ref(year_value))

            for month_dir in self._iter_month_dirs(year_dir):
                month_value = int(month_dir.name.split("_", 1)[0])
                month_summary_path = month_dir / "summary.md"
                if month_summary_path.exists() and self._backfill_periodic_summary_file(
                    tier="month",
                    dt=date(year_value, month_value, 1),
                    summary_path=month_summary_path,
                ):
                    touched_refs.append(self._month_memory_ref(year_value, month_value))

                week_dirs = sorted(
                    [item for item in month_dir.iterdir() if item.is_dir() and re.match(r"^week_\d{2}$", item.name)],
                    key=lambda item: item.name,
                )
                for week_dir in week_dirs:
                    week_summary_path = week_dir / "summary.md"
                    if not week_summary_path.exists():
                        continue
                    day_paths = sorted(
                        [
                            item
                            for item in week_dir.iterdir()
                            if item.is_file() and _DAY_FILENAME_PATTERN.match(item.name)
                        ],
                        key=lambda item: item.name,
                    )
                    if day_paths:
                        anchor_date = date.fromisoformat(day_paths[-1].stem)
                    else:
                        week_value = int(week_dir.name.split("_", 1)[1])
                        try:
                            anchor_date = date.fromisocalendar(year_value, week_value, 7)
                        except ValueError:
                            anchor_date = date(year_value, month_value, 1)
                    if self._backfill_periodic_summary_file(
                        tier="week",
                        dt=anchor_date,
                        summary_path=week_summary_path,
                    ):
                        touched_refs.append(self._week_memory_ref(anchor_date.year, int(anchor_date.strftime("%V"))))
        return {
            "updatedCount": len(touched_refs),
            "touchedRefs": touched_refs,
        }

    # ==========================================
    # Session 初始化（三层注入组装）
    # ==========================================
    
    def build_session_context(
        self,
        user_query: str,
        scope: str = "global",
        scope_chain: Optional[List[str]] = None,
        session_id: Optional[str] = None,
        run_id: Optional[str] = None,
        suppress_daily_memory: bool = False,
        suppress_memory_map: bool = False,
        target_role: str = "supervisor",
    ) -> str:
        """
        构建渐进式 Session 上下文注入文本，结合历史概要、用户偏好、近期详细日志和紧凑前序摘要。
        Returns: 注入到 System Prompt 的文本
        """
        from core.storage import storage

        target_role_normalized = str(target_role or "supervisor").strip().lower()
        if target_role_normalized != "supervisor":
            suppress_daily_memory = True
            suppress_memory_map = True

        memory_config = storage.get_memory_config() or {}
        try:
            max_recent_days = max(1, min(int(memory_config.get("max_recent_days") or 1), 30))
        except (TypeError, ValueError):
            max_recent_days = 1
        try:
            max_context_tokens = max(256, min(int(memory_config.get("max_context_tokens") or 2000), 16000))
        except (TypeError, ValueError):
            max_context_tokens = 2000

        passive_context_options = self._resolve_passive_context_options(memory_config=memory_config)
        session_context_diagnostics: Dict[str, Any] = {
            "graphSummaryInjected": False,
            "graphSummaryRelationCount": 0,
            "graphSummarySeedEntities": [],
            "graphSummaryTrimmed": False,
            "consistencyNoteInjected": False,
            "consistencyConflicts": [],
        }
        parts = []
        parts.append("[SYSTEM NOTE] The following information is dynamically provided by the internal Memory & RAG agent system. It contains user preferences, memory summaries, knowledge graph summaries, procedural workflow hints, navigation refs, and compact recent activity hints. This is a compact snapshot and may be stale or incomplete; when prior facts affect a decision, call memory_broker for fresh recall, exact day logs, or graph relations.")

        # --- Layer 1: 用户画像 ---
        normalized_chain = self._normalize_scope_chain(scope=scope, scope_chain=scope_chain)
        active_preferences = self.load_preferences(scope, scope_chain=normalized_chain)
        if target_role_normalized == "supervisor":
            parts.append(render_supervisor_identity_context(active_preferences))
        profile_preferences = non_identity_preferences(active_preferences)
        prefs_text = "\n".join(f"- {key}: {value}" for key, value in profile_preferences.items())
        if prefs_text:
            parts.append(
                "[USER PROFILE]\n"
                f"Active scope: {scope}\n"
                f"Scope chain: {' -> '.join(normalized_chain)}\n"
                f"User preferences:\n{prefs_text}\n"
                "Use these preferences to personalize your responses.\n"
                "[/USER PROFILE]"
            )
            
        # --- Layer 2: 摘要主层 ---
        if passive_context_options["summaryEnabled"] and not suppress_daily_memory:
            summary_text = self._build_memory_summary_for_injection(
                detailed_days=max_recent_days,
                scope_chain=normalized_chain,
            )
        else:
            summary_text = ""
        if summary_text:
            parts.append(
                "[MEMORY SUMMARY]\n"
                f"{summary_text}\n"
                "[/MEMORY SUMMARY]"
            )

        graph_summary_text = ""
        if passive_context_options["knowledgeGraphSummaryEnabled"]:
            graph_summary_text, graph_diagnostics = self._build_knowledge_graph_summary_for_injection(
                query=user_query,
                scope=scope,
                scope_chain=normalized_chain,
                max_relations=passive_context_options["knowledgeGraphSummaryMaxRelations"],
                max_chars=passive_context_options["knowledgeGraphSummaryMaxChars"],
            )
            session_context_diagnostics.update(graph_diagnostics)
        if graph_summary_text:
            parts.append(
                "[KNOWLEDGE GRAPH SUMMARY]\n"
                f"{graph_summary_text}\n"
                "[/KNOWLEDGE GRAPH SUMMARY]"
            )

        workflow_hints_text = ""
        try:
            from runtimes.memory.workflow_service import workflow_memory_service

            workflow_hints_text = workflow_memory_service.build_hints_block(
                query=user_query,
                scope_chain=normalized_chain,
                session_id=session_id,
                run_id=run_id,
            )
        except Exception:
            workflow_hints_text = ""
        if workflow_hints_text:
            parts.append(workflow_hints_text)

        memory_map_text = self._format_memory_map_for_injection(
            node_limit=passive_context_options["memoryMapNodeLimit"],
            scope_chain=normalized_chain,
        ) if passive_context_options["memoryMapEnabled"] and not suppress_memory_map else ""
        if memory_map_text:
            parts.append(
                "[MEMORY MAP]\n"
                f"{memory_map_text}\n"
                "[/MEMORY MAP]"
            )

        # --- Layer 3: 精简近期提示 ---
        recent_teaser = self._build_recent_activity_teaser(
            days=max_recent_days,
            scope_chain=normalized_chain,
            max_items=passive_context_options["recentActivityTeaserLimit"],
        ) if passive_context_options["recentActivityTeaserEnabled"] and not suppress_daily_memory else ""
        if recent_teaser:
            parts.append(
                f"[RECENT ACTIVITY TEASER]\n"
                f"{recent_teaser}\n"
                "Use memory_broker(mode='read_day', memory_ref_or_date='memory://day/YYYY-MM-DD') or memory_read_day(memory://day/YYYY-MM-DD or YYYY-MM-DD) when you need the exact daily log.\n"
                "[/RECENT ACTIVITY TEASER]"
            )

        passive_audit_text = "\n\n".join(
            item for item in (summary_text, memory_map_text, recent_teaser) if item
        )
        consistency_note, consistency_diagnostics = self._build_memory_consistency_note_for_injection(
            active_preferences=active_preferences,
            passive_text=passive_audit_text,
        )
        session_context_diagnostics.update(consistency_diagnostics)
        if consistency_note:
            parts.append(consistency_note)

        rendered_parts: List[str] = []
        remaining_tokens = max_context_tokens
        for part in parts:
            trimmed = self._trim_text_to_budget(part, remaining_tokens)
            if not trimmed:
                continue
            rendered_parts.append(trimmed)
            remaining_tokens -= max(1, len(trimmed) // 4)
            if remaining_tokens <= 0:
                break

        self._last_session_context_diagnostics = session_context_diagnostics
        return "\n\n".join(rendered_parts)

    def _normalize_scope_chain(self, *, scope: str = "global", scope_chain: Optional[List[str]] = None) -> List[str]:
        candidate_chain = [item for item in (scope_chain or []) if item and self._is_valid_scope(item)]
        if not candidate_chain:
            candidate_chain = ["global"]
            if scope != "global" and self._is_valid_scope(scope):
                candidate_chain.append(scope)

        normalized: List[str] = []
        for item in candidate_chain:
            if item not in normalized:
                normalized.append(item)
        if "global" not in normalized:
            normalized.insert(0, "global")
        return normalized


# === 全局单例 ===
memory_store = MemoryStore()
