from __future__ import annotations

import os
import json
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import psutil

from core.storage import storage
from core.v8_agent_os_paths import protected_runtime_paths
from erc.event_bus import event_bus
from erc.models import RuntimeSource


DEFAULT_SAFETY_GUARDIAN_CONFIG: Dict[str, Any] = {
    "enabled": True,
    "commandRules": [
        {
            "id": "command_block",
            "label": "系统级阻断命令",
            "verdict": "block",
            "description": "命中后直接阻断，不允许 override。",
            "patterns": [
                "shutdown",
                "reboot",
                "poweroff",
                "diskpart",
                "mkfs",
                "format ",
                "rm -rf /",
                "remove-item",
            ],
        },
        {
            "id": "command_review",
            "label": "高风险复核命令",
            "verdict": "review",
            "description": "命中后进入 pending approval。",
            "patterns": [
                "taskkill",
                "pkill",
                "kill ",
                "git push",
                "curl -x post",
                "invoke-webrequest",
                "pip install",
                "npm install",
                "pnpm add",
                "yarn add",
            ],
        },
    ],
    "fileRules": {
        "protectedPaths": [*protected_runtime_paths(include_home=True), str(Path.home() / ".ssh")],
        "blockedPathPatterns": [".ssh", ".aws", ".kube"],
        "reviewPathPatterns": [".v8chat", "projects.json", "hooks_config.json", "cron_config.json"],
        "protectedFileExtensions": [".db", ".sqlite", ".sqlite3"],
    },
    "processRules": {
        "protectedPatterns": [
            "v8chat",
            "uvicorn main:app",
            "next dev",
            "next start",
        ],
        "reviewPatterns": [
            "python",
            "node",
            "uvicorn",
        ],
    },
    "networkRules": {
        "localHosts": ["127.0.0.1", "localhost", "::1"],
        "blockedHosts": [],
        "reviewHosts": [],
        "reviewMethods": ["POST", "PUT", "PATCH", "DELETE"],
    },
    "automationRules": {
        "blockedActionTypes": [],
        "reviewActionTypes": ["command"],
        "reviewTargetPatterns": [],
        "blockedTargetPatterns": [],
    },
    "runtimeRules": {
        "chat": {
            "reviewTriggerSources": [],
            "blockedTriggerSources": [],
            "reviewScopePatterns": [],
            "blockedScopePatterns": [],
        },
        "automation": {
            "reviewTriggerSources": [],
            "blockedTriggerSources": [],
            "reviewScopePatterns": [],
            "blockedScopePatterns": [],
        },
        "plugin_host": {
            "reviewTriggerSources": [],
            "blockedTriggerSources": [],
            "reviewScopePatterns": [],
            "blockedScopePatterns": [],
        },
        "computer_use": {
            "reviewTriggerSources": [],
            "blockedTriggerSources": [],
            "reviewScopePatterns": [],
            "blockedScopePatterns": [],
        },
    },
    "channelGroupGuard": {
        "enabled": False,
        "allowlistOnly": False,
        "requireMention": False,
        "auditOnly": False,
        "allowlistGroups": [],
    },
    "postActionRules": {
        "enabledFamilies": [
            "command",
            "file_write",
            "http_request",
            "process",
            "cron_mutation",
            "hook_mutation",
            "background_command",
            "automation_action",
            "computer_use_action",
        ],
        "highlightFamilies": [
            "process",
            "cron_mutation",
            "hook_mutation",
            "http_request",
            "computer_use_action",
        ],
        "mutatingHttpMethods": ["POST", "PUT", "PATCH", "DELETE"],
    },
}


@dataclass(slots=True)
class SafetyDecision:
    verdict: str = "allow"
    reason: str = ""
    risk_code: str = "safe"
    details: Dict[str, Any] = field(default_factory=dict)
    allow_override: bool = True

    def is_allow(self) -> bool:
        return self.verdict == "allow"

    def is_review(self) -> bool:
        return self.verdict == "review"

    def is_block(self) -> bool:
        return self.verdict == "block"

    def to_payload(self) -> Dict[str, Any]:
        return {
            "verdict": self.verdict,
            "reason": self.reason,
            "risk_code": self.risk_code,
            "allow_override": self.allow_override,
            "details": self.details,
        }

    def to_interrupt_request(self, *, question: str, tool_call_id: str = "") -> Dict[str, Any]:
        return {
            "question": question,
            "prompt": question,
            "toolCallId": tool_call_id,
            "approvalKind": "safety_review" if self.is_review() else "safety_blocked",
            "safety": self.to_payload(),
        }


class SafetyGuardian:
    def _flatten_text_values(self, value: Any) -> str:
        parts: list[str] = []

        def _walk(node: Any) -> None:
            if node is None:
                return
            if isinstance(node, str):
                text = node.strip()
                if text:
                    parts.append(text)
                return
            if isinstance(node, (int, float)) and not isinstance(node, bool):
                parts.append(str(node))
                return
            if isinstance(node, dict):
                for item in node.values():
                    _walk(item)
                return
            if isinstance(node, (list, tuple, set)):
                for item in node:
                    _walk(item)

        _walk(value)
        return " ".join(parts).lower()

    def normalize_config(self, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        raw = deepcopy(config if config is not None else storage.get_safety_guardian_config() or {})
        merged = deepcopy(DEFAULT_SAFETY_GUARDIAN_CONFIG)

        merged["enabled"] = bool(raw.get("enabled", merged["enabled"]))

        legacy_blocked_commands = [str(item).strip() for item in raw.get("blockedCommandPatterns", []) if str(item).strip()]
        legacy_review_commands = [str(item).strip() for item in raw.get("reviewCommandPatterns", []) if str(item).strip()]
        legacy_protected_processes = [str(item).strip() for item in raw.get("protectedProcessPatterns", []) if str(item).strip()]
        legacy_local_hosts = [str(item).strip() for item in raw.get("localHosts", []) if str(item).strip()]
        legacy_protected_paths = [str(item).strip() for item in raw.get("protectedPaths", []) if str(item).strip()]

        command_rules = raw.get("commandRules")
        if isinstance(command_rules, list) and command_rules:
            merged["commandRules"] = [
                {
                    "id": str(rule.get("id") or f"command_rule_{index}"),
                    "label": str(rule.get("label") or f"命令规则 {index + 1}"),
                    "verdict": "block" if str(rule.get("verdict")).lower() == "block" else "review",
                    "description": str(rule.get("description") or ""),
                    "patterns": [str(item).strip() for item in rule.get("patterns", []) if str(item).strip()],
                }
                for index, rule in enumerate(command_rules)
                if isinstance(rule, dict)
            ]
        else:
            merged["commandRules"] = [
                {
                    **merged["commandRules"][0],
                    "patterns": legacy_blocked_commands or merged["commandRules"][0]["patterns"],
                },
                {
                    **merged["commandRules"][1],
                    "patterns": legacy_review_commands or merged["commandRules"][1]["patterns"],
                },
            ]

        file_rules = dict(raw.get("fileRules") or {})
        merged["fileRules"] = {
            "protectedPaths": legacy_protected_paths or [str(item).strip() for item in file_rules.get("protectedPaths", []) if str(item).strip()] or merged["fileRules"]["protectedPaths"],
            "blockedPathPatterns": [str(item).strip() for item in file_rules.get("blockedPathPatterns", []) if str(item).strip()] or merged["fileRules"]["blockedPathPatterns"],
            "reviewPathPatterns": [str(item).strip() for item in file_rules.get("reviewPathPatterns", []) if str(item).strip()] or merged["fileRules"]["reviewPathPatterns"],
            "protectedFileExtensions": [str(item).strip().lower() for item in file_rules.get("protectedFileExtensions", []) if str(item).strip()] or merged["fileRules"]["protectedFileExtensions"],
        }

        process_rules = dict(raw.get("processRules") or {})
        merged["processRules"] = {
            "protectedPatterns": legacy_protected_processes or [str(item).strip() for item in process_rules.get("protectedPatterns", []) if str(item).strip()] or merged["processRules"]["protectedPatterns"],
            "reviewPatterns": [str(item).strip() for item in process_rules.get("reviewPatterns", []) if str(item).strip()] or merged["processRules"]["reviewPatterns"],
        }

        network_rules = dict(raw.get("networkRules") or {})
        merged["networkRules"] = {
            "localHosts": legacy_local_hosts or [str(item).strip().lower() for item in network_rules.get("localHosts", []) if str(item).strip()] or merged["networkRules"]["localHosts"],
            "blockedHosts": [str(item).strip().lower() for item in network_rules.get("blockedHosts", []) if str(item).strip()],
            "reviewHosts": [str(item).strip().lower() for item in network_rules.get("reviewHosts", []) if str(item).strip()],
            "reviewMethods": [str(item).strip().upper() for item in network_rules.get("reviewMethods", []) if str(item).strip()] or merged["networkRules"]["reviewMethods"],
        }

        automation_rules = dict(raw.get("automationRules") or {})
        merged["automationRules"] = {
            "blockedActionTypes": [str(item).strip().lower() for item in automation_rules.get("blockedActionTypes", []) if str(item).strip()],
            "reviewActionTypes": [str(item).strip().lower() for item in automation_rules.get("reviewActionTypes", []) if str(item).strip()] or merged["automationRules"]["reviewActionTypes"],
            "reviewTargetPatterns": [str(item).strip().lower() for item in automation_rules.get("reviewTargetPatterns", []) if str(item).strip()],
            "blockedTargetPatterns": [str(item).strip().lower() for item in automation_rules.get("blockedTargetPatterns", []) if str(item).strip()],
        }

        runtime_rules = dict(raw.get("runtimeRules") or {})
        merged["runtimeRules"] = {}
        for runtime_kind, default_rules in DEFAULT_SAFETY_GUARDIAN_CONFIG["runtimeRules"].items():
            configured_rules = dict(runtime_rules.get(runtime_kind) or {})
            merged["runtimeRules"][runtime_kind] = {
                "reviewTriggerSources": [str(item).strip().lower() for item in configured_rules.get("reviewTriggerSources", []) if str(item).strip()],
                "blockedTriggerSources": [str(item).strip().lower() for item in configured_rules.get("blockedTriggerSources", []) if str(item).strip()],
                "reviewScopePatterns": [str(item).strip().lower() for item in configured_rules.get("reviewScopePatterns", []) if str(item).strip()],
                "blockedScopePatterns": [str(item).strip().lower() for item in configured_rules.get("blockedScopePatterns", []) if str(item).strip()],
            }
            for key, fallback_value in default_rules.items():
                if not merged["runtimeRules"][runtime_kind][key]:
                    merged["runtimeRules"][runtime_kind][key] = list(fallback_value)

        channel_group_guard = dict(raw.get("channelGroupGuard") or {})
        merged["channelGroupGuard"] = {
            "enabled": bool(channel_group_guard.get("enabled", DEFAULT_SAFETY_GUARDIAN_CONFIG["channelGroupGuard"]["enabled"])),
            "allowlistOnly": bool(channel_group_guard.get("allowlistOnly", DEFAULT_SAFETY_GUARDIAN_CONFIG["channelGroupGuard"]["allowlistOnly"])),
            "requireMention": bool(channel_group_guard.get("requireMention", DEFAULT_SAFETY_GUARDIAN_CONFIG["channelGroupGuard"]["requireMention"])),
            "auditOnly": bool(channel_group_guard.get("auditOnly", DEFAULT_SAFETY_GUARDIAN_CONFIG["channelGroupGuard"]["auditOnly"])),
            "allowlistGroups": [str(item).strip() for item in channel_group_guard.get("allowlistGroups", []) if str(item).strip()],
        }

        post_action_rules = dict(raw.get("postActionRules") or {})
        merged["postActionRules"] = {
            "enabledFamilies": [str(item).strip().lower() for item in post_action_rules.get("enabledFamilies", []) if str(item).strip()]
            or list(DEFAULT_SAFETY_GUARDIAN_CONFIG["postActionRules"]["enabledFamilies"]),
            "highlightFamilies": [str(item).strip().lower() for item in post_action_rules.get("highlightFamilies", []) if str(item).strip()]
            or list(DEFAULT_SAFETY_GUARDIAN_CONFIG["postActionRules"]["highlightFamilies"]),
            "mutatingHttpMethods": [str(item).strip().upper() for item in post_action_rules.get("mutatingHttpMethods", []) if str(item).strip()]
            or list(DEFAULT_SAFETY_GUARDIAN_CONFIG["postActionRules"]["mutatingHttpMethods"]),
        }

        merged["protectedPaths"] = list(merged["fileRules"]["protectedPaths"])
        merged["blockedCommandPatterns"] = self._command_patterns(merged["commandRules"], verdict="block")
        merged["reviewCommandPatterns"] = self._command_patterns(merged["commandRules"], verdict="review")
        merged["protectedProcessPatterns"] = list(merged["processRules"]["protectedPatterns"])
        merged["localHosts"] = list(merged["networkRules"]["localHosts"])
        return merged

    def export_config(self) -> Dict[str, Any]:
        return self.normalize_config()

    def save_config(self, data: Dict[str, Any]) -> Dict[str, Any]:
        normalized = self.normalize_config(data)
        storage.save_safety_guardian_config(normalized)
        return normalized

    def _config(self) -> Dict[str, Any]:
        return self.export_config()

    def preflight_runtime(
        self,
        *,
        runtime_kind: str,
        trigger_source: str,
        session_id: str | None = None,
        run_id: str | None = None,
        resolved_scope: str | None = None,
        user_id: str | None = None,
    ) -> SafetyDecision:
        config = self._config()
        runtime_rule = dict((config.get("runtimeRules") or {}).get(runtime_kind, {}) or {})
        normalized_trigger = (trigger_source or "").strip().lower()
        normalized_scope = (resolved_scope or "").strip().lower()

        if self._matches_patterns(normalized_trigger, runtime_rule.get("blockedTriggerSources", [])):
            return SafetyDecision(
                verdict="block",
                reason=f"{runtime_kind} 运行时命中了阻断触发源规则：{trigger_source}",
                risk_code="runtime_blocked_trigger",
                details={
                    "runtime_kind": runtime_kind,
                    "trigger_source": trigger_source,
                    "session_id": session_id,
                    "run_id": run_id,
                    "resolved_scope": resolved_scope,
                    "user_id": user_id,
                },
                allow_override=False,
            )

        if normalized_scope and self._matches_patterns(normalized_scope, runtime_rule.get("blockedScopePatterns", [])):
            return SafetyDecision(
                verdict="block",
                reason=f"{runtime_kind} 运行时命中了阻断 scope 规则：{resolved_scope}",
                risk_code="runtime_blocked_scope",
                details={
                    "runtime_kind": runtime_kind,
                    "trigger_source": trigger_source,
                    "session_id": session_id,
                    "run_id": run_id,
                    "resolved_scope": resolved_scope,
                    "user_id": user_id,
                },
                allow_override=False,
            )

        if self._matches_patterns(normalized_trigger, runtime_rule.get("reviewTriggerSources", [])):
            return SafetyDecision(
                verdict="review",
                reason=f"{runtime_kind} 运行时命中了复核触发源规则：{trigger_source}",
                risk_code="runtime_review_trigger",
                details={
                    "runtime_kind": runtime_kind,
                    "trigger_source": trigger_source,
                    "session_id": session_id,
                    "run_id": run_id,
                    "resolved_scope": resolved_scope,
                    "user_id": user_id,
                },
            )

        if normalized_scope and self._matches_patterns(normalized_scope, runtime_rule.get("reviewScopePatterns", [])):
            return SafetyDecision(
                verdict="review",
                reason=f"{runtime_kind} 运行时命中了复核 scope 规则：{resolved_scope}",
                risk_code="runtime_review_scope",
                details={
                    "runtime_kind": runtime_kind,
                    "trigger_source": trigger_source,
                    "session_id": session_id,
                    "run_id": run_id,
                    "resolved_scope": resolved_scope,
                    "user_id": user_id,
                },
            )

        return SafetyDecision(
            verdict="allow",
            reason="runtime_preflight_passed",
            risk_code="runtime_preflight",
            details={
                "runtime_kind": runtime_kind,
                "trigger_source": trigger_source,
                "session_id": session_id,
                "run_id": run_id,
                "resolved_scope": resolved_scope,
                "user_id": user_id,
            },
        )

    def build_runtime_preflight_request(
        self,
        *,
        runtime_kind: str,
        trigger_source: str,
        decision: SafetyDecision,
        subject: str | None = None,
    ) -> Dict[str, Any]:
        question = (
            f"Safety Guardian 检测到 {runtime_kind} 运行存在风险，是否继续？\n\n"
            f"来源：{trigger_source or 'unknown'}"
        )
        if subject:
            question += f"\n对象：{subject}"
        if decision.reason:
            question += f"\n原因：{decision.reason}"
        return {
            "question": question,
            "prompt": question,
            "approvalKind": "safety_review" if decision.is_review() else "safety_blocked",
            "runtime": {
                "runtime_kind": runtime_kind,
                "trigger_source": trigger_source,
                "subject": subject,
            },
            "safety": decision.to_payload(),
        }

    def assess_system_command(self, command: str, *, runtime_context: Optional[Dict[str, Any]] = None) -> SafetyDecision:
        config = self._config()
        if not config["enabled"]:
            return SafetyDecision()

        normalized = (command or "").strip().lower()
        if not normalized:
            return SafetyDecision()

        for rule in config["commandRules"]:
            for pattern in rule["patterns"]:
                if pattern and pattern.lower() in normalized:
                    verdict = "block" if rule["verdict"] == "block" else "review"
                    return SafetyDecision(
                        verdict=verdict,
                        reason=f"命令命中了{rule['label']}：{pattern}",
                        risk_code="blocked_command_pattern" if verdict == "block" else "review_command_pattern",
                        details={"command": command, "pattern": pattern, "rule": rule, "runtime_context": runtime_context or {}},
                        allow_override=verdict != "block",
                    )

        if self._touches_protected_path_in_command(command):
            return SafetyDecision(
                verdict="block",
                reason="命令试图删除、覆盖或移动 v8chat 核心目录/受保护路径。",
                risk_code="protected_path_command",
                details={"command": command, "runtime_context": runtime_context or {}},
                allow_override=False,
            )

        if self._targets_protected_process(command):
            return SafetyDecision(
                verdict="block",
                reason="命令疑似试图结束 v8chat 主程序或关键守护进程。",
                risk_code="protected_process_command",
                details={"command": command, "runtime_context": runtime_context or {}},
                allow_override=False,
            )

        return SafetyDecision(
            verdict="allow",
            reason="command_allowed",
            risk_code="command_allowed",
            details={"command": command, "runtime_context": runtime_context or {}},
        )

    def assess_background_command(self, command: str, *, runtime_context: Optional[Dict[str, Any]] = None) -> SafetyDecision:
        return self.assess_system_command(command, runtime_context=runtime_context)

    def assess_file_write(self, path: str, *, append: bool, runtime_context: Optional[Dict[str, Any]] = None) -> SafetyDecision:
        config = self._config()
        if not config["enabled"]:
            return SafetyDecision()

        normalized = self._normalize_path(path)
        if not normalized:
            return SafetyDecision()

        if self._matches_path_patterns(normalized, config["fileRules"]["blockedPathPatterns"]):
            return SafetyDecision(
                verdict="block",
                reason="目标路径命中了 Safety Guardian 的阻断文件规则。",
                risk_code="blocked_file_pattern",
                details={"path": str(normalized), "append": append, "runtime_context": runtime_context or {}},
                allow_override=False,
            )

        if normalized.suffix.lower() in set(config["fileRules"]["protectedFileExtensions"]) and self._is_under_protected_path(normalized):
            return SafetyDecision(
                verdict="block",
                reason="禁止直接写入受保护目录下的核心状态/数据库文件。",
                risk_code="protected_database_write",
                details={"path": str(normalized), "append": append, "runtime_context": runtime_context or {}},
                allow_override=False,
            )

        if self._is_under_protected_path(normalized) or self._matches_path_patterns(normalized, config["fileRules"]["reviewPathPatterns"]):
            return SafetyDecision(
                verdict="review",
                reason="当前写入目标位于 v8chat 核心配置/状态目录，需要人工确认。",
                risk_code="protected_config_write",
                details={"path": str(normalized), "append": append, "runtime_context": runtime_context or {}},
            )

        if self._is_sensitive_system_path(normalized):
            return SafetyDecision(
                verdict="block",
                reason="当前写入目标位于系统敏感路径，已被 Safety Guardian 阻止。",
                risk_code="sensitive_system_write",
                details={"path": str(normalized), "append": append, "runtime_context": runtime_context or {}},
                allow_override=False,
            )

        return SafetyDecision(
            verdict="allow",
            reason="file_write_allowed",
            risk_code="file_write_allowed",
            details={"path": str(normalized), "append": append, "runtime_context": runtime_context or {}},
        )

    def assess_process_action(self, pid: int, action: str, *, runtime_context: Optional[Dict[str, Any]] = None) -> SafetyDecision:
        try:
            process = psutil.Process(pid)
            description = " ".join(process.cmdline() or []) or process.name()
        except Exception:
            description = f"pid:{pid}"

        if self._matches_process_patterns(description, self._config()["processRules"]["protectedPatterns"]):
            return SafetyDecision(
                verdict="block",
                reason="目标进程疑似属于 v8chat 主程序或关键服务，禁止直接终止。",
                risk_code="protected_process_action",
                details={"pid": pid, "action": action, "process": description, "runtime_context": runtime_context or {}},
                allow_override=False,
            )

        if self._matches_process_patterns(description, self._config()["processRules"]["reviewPatterns"]):
            return SafetyDecision(
                verdict="review",
                reason="目标进程命中了复核规则，需要人工确认。",
                risk_code="review_process_action",
                details={"pid": pid, "action": action, "process": description, "runtime_context": runtime_context or {}},
            )

        return SafetyDecision(
            verdict="review",
            reason="终止本地进程属于高风险行为，需要人工确认。",
            risk_code="process_action_review",
            details={"pid": pid, "action": action, "process": description, "runtime_context": runtime_context or {}},
        )

    def assess_http_request(
        self,
        method: str,
        url: str,
        *,
        body: str | None = None,
        runtime_context: Optional[Dict[str, Any]] = None,
    ) -> SafetyDecision:
        config = self._config()
        method_upper = (method or "GET").upper()
        parsed = urlparse(url or "")
        host = (parsed.hostname or "").lower()

        if not parsed.scheme or not host:
            return SafetyDecision(
                verdict="review",
                reason="网络请求目标不完整，无法确认安全性，需要人工确认。",
                risk_code="ambiguous_http_target",
                details={"method": method_upper, "url": url, "runtime_context": runtime_context or {}},
            )

        if host in config["networkRules"]["localHosts"]:
            return SafetyDecision(
                verdict="allow",
                reason="loopback_http_allowed",
                risk_code="loopback_http_allowed",
                details={"method": method_upper, "url": url, "runtime_context": runtime_context or {}},
            )

        if self._matches_host(host, config["networkRules"]["blockedHosts"]):
            return SafetyDecision(
                verdict="block",
                reason="目标域名命中了 Safety Guardian 的阻断网络规则。",
                risk_code="blocked_host",
                details={"method": method_upper, "url": url, "runtime_context": runtime_context or {}},
                allow_override=False,
            )

        if self._matches_host(host, config["networkRules"]["reviewHosts"]):
            return SafetyDecision(
                verdict="review",
                reason="目标域名命中了 Safety Guardian 的复核网络规则。",
                risk_code="review_host",
                details={"method": method_upper, "url": url, "runtime_context": runtime_context or {}},
            )

        if method_upper in set(config["networkRules"]["reviewMethods"]):
            return SafetyDecision(
                verdict="review",
                reason="对外部域名发起变更型网络请求，需要人工确认。",
                risk_code="external_mutating_http",
                details={"method": method_upper, "url": url, "body_preview": (body or "")[:300], "runtime_context": runtime_context or {}},
            )

        return SafetyDecision(
            verdict="allow",
            reason="http_allowed",
            risk_code="http_allowed",
            details={"method": method_upper, "url": url, "runtime_context": runtime_context or {}},
        )

    def assess_cron_mutation(self, action: str, *, runtime_context: Optional[Dict[str, Any]] = None) -> SafetyDecision:
        if (action or "").lower() in {"add", "remove"}:
            return SafetyDecision(
                verdict="review",
                reason="修改系统定时任务会影响长期自动化行为，需要人工确认。",
                risk_code="cron_mutation_review",
                details={"action": action, "runtime_context": runtime_context or {}},
            )
        return SafetyDecision(verdict="allow", reason="cron_query_allowed", risk_code="cron_query_allowed", details={"action": action})

    def assess_hook_mutation(self, action: str, *, runtime_context: Optional[Dict[str, Any]] = None) -> SafetyDecision:
        if (action or "").lower() == "add":
            return SafetyDecision(
                verdict="review",
                reason="新增系统 Hook 会改变引擎生命周期行为，需要人工确认。",
                risk_code="hook_mutation_review",
                details={"action": action, "runtime_context": runtime_context or {}},
            )
        return SafetyDecision(verdict="allow", reason="hook_query_allowed", risk_code="hook_query_allowed", details={"action": action})

    def assess_automation_action(
        self,
        *,
        action_type: str,
        target: str,
        payload: Dict[str, Any] | None = None,
        trigger_source: str | None = None,
    ) -> SafetyDecision:
        config = self._config()
        runtime_context = {
            "runtime_kind": "automation",
            "trigger_source": trigger_source,
            "payload": payload or {},
        }
        action_type_lower = (action_type or "").lower()
        target_lower = (target or "").lower()

        if action_type_lower in set(config["automationRules"]["blockedActionTypes"]):
            return SafetyDecision(
                verdict="block",
                reason=f"自动化动作类型 {action_type} 位于阻断清单中。",
                risk_code="blocked_automation_action_type",
                details={"action_type": action_type, "target": target, "runtime_context": runtime_context},
                allow_override=False,
            )

        if self._matches_patterns(target_lower, config["automationRules"]["blockedTargetPatterns"]):
            return SafetyDecision(
                verdict="block",
                reason="自动化目标命中了阻断规则。",
                risk_code="blocked_automation_target",
                details={"action_type": action_type, "target": target, "runtime_context": runtime_context},
                allow_override=False,
            )

        if self._matches_patterns(target_lower, config["automationRules"]["reviewTargetPatterns"]):
            return SafetyDecision(
                verdict="review",
                reason="自动化目标命中了复核规则。",
                risk_code="review_automation_target",
                details={"action_type": action_type, "target": target, "runtime_context": runtime_context},
            )

        if action_type_lower in set(config["automationRules"]["reviewActionTypes"]):
            if action_type_lower == "command":
                return self.assess_system_command(target, runtime_context=runtime_context)
            return SafetyDecision(
                verdict="review",
                reason=f"自动化动作类型 {action_type} 命中了复核策略。",
                risk_code="review_automation_action_type",
                details={"action_type": action_type, "target": target, "runtime_context": runtime_context},
            )

        if action_type_lower == "command":
            return self.assess_system_command(target, runtime_context=runtime_context)

        return SafetyDecision(
            verdict="allow",
            reason="automation_target_allowed",
            risk_code="automation_target_allowed",
            details={"action_type": action_type, "target": target, "runtime_context": runtime_context},
        )

    def assess_computer_use_action(
        self,
        *,
        action_type: str,
        target: Dict[str, Any] | None = None,
        runtime_context: Optional[Dict[str, Any]] = None,
    ) -> SafetyDecision:
        config = self._config()
        if not config["enabled"]:
            return SafetyDecision()

        runtime_context = dict(runtime_context or {})
        action_type_lower = (action_type or "").strip().lower()
        target_values = self._flatten_text_values(target or {})

        blocked_keywords = ["付款", "支付", "转账", "删除账号", "恢复出厂", "格式化磁盘"]
        review_keywords = [
            "删除",
            "提交",
            "发送",
            "发布",
            "确认",
            "安装",
            "卸载",
            "覆盖",
            "apply",
            "submit",
            "send",
            "delete",
            "publish",
            "confirm",
            "install",
            "uninstall",
        ]

        if any(keyword.lower() in target_values for keyword in blocked_keywords):
            return SafetyDecision(
                verdict="block",
                reason="computer use 目标疑似涉及高危系统/资金操作，已被阻止。",
                risk_code="computer_use_blocked_action",
                details={"action_type": action_type, "target": target or {}, "runtime_context": runtime_context},
                allow_override=False,
            )

        if action_type_lower == "hotkey":
            sequence = str((target or {}).get("sequence") or target_values).lower()
            if any(token in sequence for token in ["%{f4}", "^+{esc}", "^{esc}", "#{l}", "#{r}"]):
                return SafetyDecision(
                    verdict="review",
                    reason="该热键可能影响系统或窗口生命周期，需要人工确认。",
                    risk_code="computer_use_hotkey_review",
                    details={"action_type": action_type, "target": target or {}, "runtime_context": runtime_context},
                )

        if action_type_lower in {"click", "double_click", "type_text", "hotkey"} and any(
            keyword.lower() in target_values for keyword in review_keywords
        ):
            return SafetyDecision(
                verdict="review",
                reason="computer use 动作命中了高风险文案关键词，需要人工确认。",
                risk_code="computer_use_review_action",
                details={"action_type": action_type, "target": target or {}, "runtime_context": runtime_context},
            )

        return SafetyDecision(
            verdict="allow",
            reason="computer_use_action_allowed",
            risk_code="computer_use_action_allowed",
            details={"action_type": action_type, "target": target or {}, "runtime_context": runtime_context},
        )

    def observe_post_action(
        self,
        *,
        action_family: str,
        summary: str,
        details: Dict[str, Any] | None = None,
        runtime_context: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        config = self._config()
        family = (action_family or "").strip().lower()
        if not config["enabled"] or not family:
            return None

        post_action_rules = dict(config.get("postActionRules") or {})
        enabled_families = set(post_action_rules.get("enabledFamilies") or [])
        if family not in enabled_families:
            return None

        runtime_context = dict(runtime_context or {})
        session_id = runtime_context.get("session_id")
        run_id = runtime_context.get("run_id")
        if not session_id or not run_id:
            return None

        highlight_families = set(post_action_rules.get("highlightFamilies") or [])
        is_highlight = family in highlight_families
        if family == "http_request":
            mutating_methods = {str(item).upper() for item in post_action_rules.get("mutatingHttpMethods") or []}
            observed_method = str((details or {}).get("method") or "").upper()
            if observed_method and observed_method not in mutating_methods:
                is_highlight = False

        topic = "safety.post_action.alerted" if is_highlight else "safety.post_action.observed"
        emitter = event_bus.create_emitter(
            session_id=session_id,
            conversation_id=runtime_context.get("conversation_id") or session_id,
            run_id=run_id,
            source=RuntimeSource(
                plane="engine",
                component="safety_guardian",
                node="post_action_guard",
                agent_id=runtime_context.get("agent_id"),
            ),
        )
        return emitter.emit(
            topic,
            {
                "action_family": family,
                "summary": summary,
                "details": details or {},
                "runtime_context": runtime_context,
            },
        )

    def _normalize_path(self, path: str | None) -> Optional[Path]:
        if not path:
            return None
        try:
            return Path(path).expanduser().resolve(strict=False)
        except Exception:
            return None

    def _protected_paths(self) -> list[Path]:
        return [path for item in self._config()["fileRules"]["protectedPaths"] if (path := self._normalize_path(item)) is not None]

    def _is_under_protected_path(self, path: Path) -> bool:
        normalized = self._normalize_path(str(path))
        if normalized is None:
            return False
        for protected in self._protected_paths():
            try:
                normalized.relative_to(protected)
                return True
            except ValueError:
                continue
        return False

    def _is_sensitive_system_path(self, path: Path) -> bool:
        normalized = self._normalize_path(str(path))
        if normalized is None:
            return False
        sensitive_roots = [
            Path.home() / ".ssh",
            Path.home() / ".aws",
            Path.home() / ".kube",
            Path(os.environ.get("WINDIR", "C:/Windows")),
            Path("/etc"),
        ]
        for root in sensitive_roots:
            try:
                normalized.relative_to(root.expanduser().resolve(strict=False))
                return True
            except Exception:
                continue
        return False

    def _touches_protected_path_in_command(self, command: str) -> bool:
        lower = (command or "").lower()
        destructive_hint = any(token in lower for token in ["rm ", "del ", "remove-item", "rmdir", "move ", "mv ", "rename ", "ren "])
        if not destructive_hint:
            return False
        for protected in self._protected_paths():
            if str(protected).lower() in lower:
                return True
        return False

    def _matches_process_patterns(self, value: str, patterns: list[str]) -> bool:
        lower = (value or "").lower()
        return any(pattern.lower() in lower for pattern in patterns if pattern)

    def _targets_protected_process(self, command: str) -> bool:
        return self._matches_process_patterns(command, self._config()["processRules"]["protectedPatterns"])

    def _matches_path_patterns(self, path: Path, patterns: list[str]) -> bool:
        normalized = str(path).lower()
        return any(pattern.lower() in normalized for pattern in patterns if pattern)

    def _matches_host(self, host: str, patterns: list[str]) -> bool:
        lower = (host or "").lower()
        for pattern in patterns:
            normalized = (pattern or "").lower().strip()
            if not normalized:
                continue
            if normalized == lower or lower.endswith(f".{normalized}"):
                return True
        return False

    def _matches_patterns(self, value: str, patterns: list[str]) -> bool:
        lower = (value or "").lower()
        return any(pattern.lower() in lower for pattern in patterns if pattern)

    def _command_patterns(self, command_rules: list[Dict[str, Any]], *, verdict: str) -> list[str]:
        patterns: list[str] = []
        for rule in command_rules:
            if rule.get("verdict") != verdict:
                continue
            patterns.extend([str(item).strip() for item in rule.get("patterns", []) if str(item).strip()])
        return patterns


safety_guardian = SafetyGuardian()
