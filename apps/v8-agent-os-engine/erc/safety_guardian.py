from __future__ import annotations

import os
import base64
import binascii
import hashlib
import json
import re
import shlex
import time
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Literal, Optional
from urllib.parse import parse_qsl, urlparse
from uuid import uuid4

import psutil

from core.storage import storage
from core.database import db
from core.safety_active_defense import DEFAULT_ACTIVE_DEFENSE_CONFIG, normalize_active_defense_config, safety_active_defense_monitor
from core.v8_agent_os_paths import WORKSPACE_HOME, protected_runtime_paths
from erc.event_bus import event_bus
from erc.models import RuntimeSource


MACHINE_POSTURE_DEDICATED = "dedicated_runtime_host"
MACHINE_POSTURE_DEVELOPER = "developer_mixed_host"
VALID_MACHINE_POSTURES = {MACHINE_POSTURE_DEDICATED, MACHINE_POSTURE_DEVELOPER}
_ENGINE_ROOT = Path(__file__).resolve().parents[1]
_TRUSTED_NETWORK_CATALOG_PATH = Path(__file__).resolve().parent / "assets" / "trusted_network_catalog.json"
_MODEL_PROVIDER_CATALOG_PATH = _ENGINE_ROOT / "core" / "model_catalog" / "provider_catalog.json"
_MEDIA_PROVIDER_MATRIX_PATH = _ENGINE_ROOT / "runtimes" / "creative_media" / "assets" / "media_provider_format_matrix.json"

DEFAULT_SAFETY_GUARDIAN_CONFIG: Dict[str, Any] = {
    "enabled": True,
    "machinePosture": MACHINE_POSTURE_DEDICATED,
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
                "kill",
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
        "trustedHosts": [],
        "reviewMethods": ["POST", "PUT", "PATCH", "DELETE"],
        "unknownCredentialHostVerdict": "review",
    },
    "automationRules": {
        "blockedActionTypes": [],
        "reviewActionTypes": ["command"],
        "reviewTargetPatterns": [],
        "blockedTargetPatterns": [],
    },
    "runtimeRules": {
        "chat": {
            "auditTriggerSources": [],
            "reviewTriggerSources": [],
            "blockedTriggerSources": [],
            "auditScopePatterns": [],
            "reviewScopePatterns": [],
            "blockedScopePatterns": [],
        },
        "automation": {
            "auditTriggerSources": [],
            "reviewTriggerSources": [],
            "blockedTriggerSources": [],
            "auditScopePatterns": [],
            "reviewScopePatterns": [],
            "blockedScopePatterns": [],
        },
        "plugin_host": {
            "auditTriggerSources": [],
            "reviewTriggerSources": [],
            "blockedTriggerSources": [],
            "auditScopePatterns": [],
            "reviewScopePatterns": [],
            "blockedScopePatterns": [],
        },
        "computer_use": {
            "auditTriggerSources": [],
            "reviewTriggerSources": [],
            "blockedTriggerSources": [],
            "auditScopePatterns": [],
            "reviewScopePatterns": [],
            "blockedScopePatterns": [],
        },
        "rpa": {
            "auditTriggerSources": [],
            "reviewTriggerSources": [],
            "blockedTriggerSources": [],
            "auditScopePatterns": [],
            "reviewScopePatterns": [],
            "blockedScopePatterns": [],
        },
    },
    "skillRules": {
        "declarationVerdict": "audit",
        "localSecretReadVerdict": "review",
        "browserProfileAccessVerdict": {
            MACHINE_POSTURE_DEDICATED: "review",
            MACHINE_POSTURE_DEVELOPER: "block",
        },
        "downloadExecuteVerdict": "block",
        "persistenceVerdict": "block",
        "destructiveVerdict": "block",
        "binaryPayloadVerdict": "review",
        "llmReviewEnabledFor": ["review"],
    },
    "networkMutationRules": {
        "defaultExternalMutationVerdict": {
            MACHINE_POSTURE_DEDICATED: "audit",
            MACHINE_POSTURE_DEVELOPER: "review",
        },
        "sensitivePayloadVerdict": "review",
        "credentialExfiltrationVerdict": "block",
    },
    "computerUseRules": {
        "defaultMutationVerdict": {
            MACHINE_POSTURE_DEDICATED: "audit",
            MACHINE_POSTURE_DEVELOPER: "review",
        },
        "destructiveKeywordVerdict": "block",
        "hotkeyLifecycleVerdict": "review",
    },
    "systemIntegrityRules": {
        "packageInstallVerdict": {
            MACHINE_POSTURE_DEDICATED: "audit",
            MACHINE_POSTURE_DEVELOPER: "review",
        },
        "destructiveCommandVerdict": "block",
    },
    "v8IntegrityRules": {
        "protectedConfigWriteVerdict": "review",
        "protectedRuntimeProcessVerdict": "block",
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
    "windowsProfileProtection": {
        "enabled": True,
        "sensitiveReadVerdict": "review",
        "highRiskMutationVerdict": "block",
        "mediumRiskMutationVerdict": "review",
    },
    "crossPlatformSystemProtection": {
        "enabled": True,
        "sensitiveReadVerdict": "review",
        "highRiskMutationVerdict": "block",
        "mediumRiskMutationVerdict": "review",
        "privilegeElevationVerdict": "review",
        "persistenceVerdict": "review",
        "firewallVerdict": "review",
    },
    "externalToolLocalSystemProtection": {
        "enabled": True,
    },
    "activeDefense": DEFAULT_ACTIVE_DEFENSE_CONFIG,
}

_SKILL_SCAN_TEXT_SUFFIXES = {
    ".py",
    ".sh",
    ".ps1",
    ".bat",
    ".cmd",
    ".command",
    ".js",
    ".ts",
    ".mjs",
    ".cjs",
}
_SKILL_SCAN_BINARY_SUFFIXES = {
    ".exe",
    ".dll",
    ".so",
    ".dylib",
    ".bin",
    ".jar",
    ".com",
    ".msi",
    ".app",
}
_SKILL_SCAN_IGNORED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    "node_modules",
    ".next",
    "dist",
    "build",
    "venv",
    ".venv",
}
_SKILL_SCAN_MAX_FILES = 256
_SKILL_SCAN_MAX_BYTES = 200_000
_SKILL_SCAN_MAX_FLAGGED_FILES = 16
_SKILL_SCAN_REASON_LIMIT = 8
_SKILL_SCAN_RULES: tuple[dict[str, Any], ...] = (
    {
        "id": "download_then_execute",
        "label": "下载后立即执行",
        "severity": "high",
        "score": 42,
        "reason": "发现下载后立即执行的链式特征。",
        "all_groups": (
            (
                "curl ",
                "wget ",
                "invoke-webrequest",
                "invoke-restmethod",
                "downloadstring",
                "urlretrieve(",
                "urllib.request.urlretrieve",
                "requests.get(",
                "httpx.get(",
            ),
            (
                "| bash",
                "| sh",
                "invoke-expression",
                " iex",
                "subprocess.",
                "os.system(",
                "start-process",
                "cmd.exe /c",
                "powershell.exe -command",
                "bash -c",
                "sh -c",
            ),
        ),
    },
    {
        "id": "encoded_payload",
        "label": "编码载荷/反射执行",
        "severity": "high",
        "score": 36,
        "reason": "发现编码载荷、反射执行或混淆执行特征。",
        "any_tokens": (
            "frombase64string",
            "base64 -d",
            "powershell -enc",
            "encodedcommand",
            "marshal.loads",
            "exec(base64",
            "eval(base64",
            "reflection.assembly",
            "add-type",
            "rundll32",
            "mshta ",
        ),
    },
    {
        "id": "credential_exfiltration",
        "label": "敏感信息读取后外传",
        "severity": "high",
        "score": 38,
        "reason": "发现凭证/环境变量读取后外传特征。",
        "all_groups": (
            (
                ".ssh",
                ".aws",
                ".kube",
                "id_rsa",
                "known_hosts",
                "login data",
                "cookies.sqlite",
                "web data",
                "places.sqlite",
                "user data",
                "chrome/user data",
                "edge/user data",
                "firefox/profiles",
                "default/profile",
                "os.environ",
                "$env:",
                "access_key",
                "secret_key",
                "api_key",
                "token",
            ),
            (
                "requests.post(",
                "httpx.post(",
                "urllib.request",
                "invoke-webrequest",
                "curl -d",
                "curl --data",
                "fetch(",
                "axios.post(",
                "webhook",
            ),
        ),
    },
    {
        "id": "persistence",
        "label": "持久化植入",
        "severity": "medium",
        "score": 24,
        "reason": "发现计划任务、开机自启或持久化植入特征。",
        "any_tokens": (
            "schtasks",
            "crontab",
            "launchctl",
            "launchagents",
            "launchdaemons",
            "reg add",
            "currentversion\\run",
            "systemctl enable",
            "login item",
        ),
    },
    {
        "id": "destructive_fs",
        "label": "大范围破坏性文件系统操作",
        "severity": "high",
        "score": 40,
        "reason": "发现可能清空、格式化或批量破坏文件系统的特征。",
        "any_tokens": (
            "rm -rf",
            "remove-item -recurse -force",
            "del /f /s /q",
            "rd /s /q",
            "format c:",
            "format /fs",
            "mkfs",
            "diskpart",
            "cipher /w",
            "shutil.rmtree(",
        ),
    },
    {
        "id": "hidden_shell_exec",
        "label": "隐蔽 shell/PowerShell 执行",
        "severity": "medium",
        "score": 14,
        "reason": "发现隐蔽 shell 或 PowerShell 执行特征。",
        "any_tokens": (
            "subprocess.popen(",
            "subprocess.run(",
            "os.system(",
            "start-process",
            "cmd.exe /c",
            "powershell.exe -command",
            "bash -c",
            "sh -c",
        ),
    },
    {
        "id": "secret_declaration",
        "label": "声明式密钥/环境变量依赖",
        "severity": "low",
        "score": 4,
        "reason": "发现 skill 需要 API Key、Token 或环境变量配置。",
        "any_tokens": (
            "api_key",
            "access_key",
            "secret_key",
            "authorization",
            "bearer ",
            "token",
            "os.getenv(",
            "process.env.",
            "$env:",
            ".env",
        ),
    },
    {
        "id": "local_secret_read",
        "label": "本地敏感材料读取",
        "severity": "medium",
        "score": 16,
        "reason": "发现对本地凭证、环境变量或敏感配置的主动读取。",
        "all_groups": (
            (
                ".ssh",
                ".aws",
                ".kube",
                "id_rsa",
                "known_hosts",
                "os.environ",
                "$env:",
                ".env",
                "api_key",
                "authorization",
            ),
            (
                "open(",
                "read_text(",
                "read_bytes(",
                "get-content",
                "cat ",
                "copy-item",
                "json.load(",
                "yaml.safe_load(",
            ),
        ),
    },
    {
        "id": "browser_profile_access",
        "label": "浏览器资料访问",
        "severity": "medium",
        "score": 24,
        "reason": "发现读取浏览器 Cookie、登录资料或 Profile 数据的特征。",
        "any_tokens": (
            "login data",
            "cookies.sqlite",
            "cookies",
            "web data",
            "browser history",
            "chrome history",
            "firefox history",
            "edge history",
            "places.sqlite",
            "user data",
            "chrome/user data",
            "edge/user data",
            "firefox/profiles",
            "default/profile",
        ),
    },
)

_DOWNLOAD_EXECUTABLE_SUFFIXES = {
    ".bat",
    ".cmd",
    ".command",
    ".com",
    ".exe",
    ".jar",
    ".js",
    ".msi",
    ".ps1",
    ".py",
    ".sh",
}
_RECENT_DOWNLOAD_TTL_SECONDS = 10 * 60


@dataclass(slots=True)
class _RecentDownload:
    path: Path
    host: str | None
    recorded_at: float
    runtime_kind: str | None = None


@dataclass(slots=True)
class _CommandAnalysis:
    original: str
    decoded_commands: list[str] = field(default_factory=list)
    ambiguous_encoded_execution: bool = False
    encoded_indicators: list[str] = field(default_factory=list)
    download_execute_detected: bool = False
    download_execute_reason: str = ""
    download_output_paths: list[Path] = field(default_factory=list)
    download_hosts: list[str] = field(default_factory=list)

    @property
    def policy_commands(self) -> list[str]:
        commands = [self.original]
        for command in self.decoded_commands:
            if command and command not in commands:
                commands.append(command)
        return commands

    def to_payload(self) -> dict[str, Any]:
        return {
            "decodedCommands": self.decoded_commands[:4],
            "ambiguousEncodedExecution": self.ambiguous_encoded_execution,
            "encodedIndicators": self.encoded_indicators[:8],
            "downloadExecuteDetected": self.download_execute_detected,
            "downloadExecuteReason": self.download_execute_reason,
            "downloadOutputPaths": [str(path) for path in self.download_output_paths[:8]],
            "downloadHosts": self.download_hosts[:8],
        }


@dataclass(slots=True)
class SafetyDecision:
    verdict: Literal["allow", "audit", "review", "block"] = "allow"
    reason: str = ""
    risk_code: str = "safe"
    details: Dict[str, Any] = field(default_factory=dict)
    allow_override: bool = True
    governance_target: str = "general"
    posture: str = MACHINE_POSTURE_DEDICATED

    def is_allow(self) -> bool:
        return self.verdict in {"allow", "audit"}

    def is_audit(self) -> bool:
        return self.verdict == "audit"

    def is_review(self) -> bool:
        return self.verdict == "review"

    def is_block(self) -> bool:
        return self.verdict == "block"

    def to_payload(self) -> Dict[str, Any]:
        event_summary = self.details.get("eventSummary") if isinstance(self.details, dict) else None
        return {
            "verdict": self.verdict,
            "reason": self.reason,
            "risk_code": self.risk_code,
            "riskCode": self.risk_code,
            "allow_override": self.allow_override,
            "governanceTarget": self.governance_target,
            "posture": self.posture,
            "eventSummary": event_summary if isinstance(event_summary, dict) else {},
            "details": self.details,
        }

    def to_interrupt_request(self, *, question: str, tool_call_id: str = "") -> Dict[str, Any]:
        runtime_context = self.details.get("runtime_context") if isinstance(self.details, dict) else {}
        runtime_kind = runtime_context.get("runtime_kind") if isinstance(runtime_context, dict) else None
        return {
            "question": question,
            "prompt": question,
            "toolCallId": tool_call_id,
            "approvalKind": "safety_review" if self.is_review() else "safety_blocked",
            "riskCode": self.risk_code,
            "reviewMode": "human_approval" if self.is_review() else "blocked",
            "runtimeKind": runtime_kind or "unknown",
            "targetSurface": "governance_hud",
            "safety": self.to_payload(),
            "eventSummary": self.to_payload().get("eventSummary") or {},
        }


class SafetyGuardian:
    def __init__(self) -> None:
        self._recent_downloads: list[_RecentDownload] = []

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

    def _normalize_verdict(self, value: Any, *, fallback: str, allowed: set[str]) -> str:
        normalized = str(value or "").strip().lower()
        if normalized in allowed:
            return normalized
        return str(fallback).strip().lower()

    def _normalize_posture_map(self, value: Any, *, fallback: Dict[str, str], allowed: set[str]) -> Dict[str, str]:
        raw = dict(value or {}) if isinstance(value, dict) else {}
        return {
            MACHINE_POSTURE_DEDICATED: self._normalize_verdict(
                raw.get(MACHINE_POSTURE_DEDICATED),
                fallback=fallback[MACHINE_POSTURE_DEDICATED],
                allowed=allowed,
            ),
            MACHINE_POSTURE_DEVELOPER: self._normalize_verdict(
                raw.get(MACHINE_POSTURE_DEVELOPER),
                fallback=fallback[MACHINE_POSTURE_DEVELOPER],
                allowed=allowed,
            ),
        }

    def _current_posture(self, config: Dict[str, Any] | None = None) -> str:
        active = str((config or self._config()).get("machinePosture") or MACHINE_POSTURE_DEDICATED).strip().lower()
        if active not in VALID_MACHINE_POSTURES:
            return MACHINE_POSTURE_DEDICATED
        return active

    def _posture_verdict(self, mapping: Dict[str, str], *, posture: str, fallback: str) -> str:
        return self._normalize_verdict(mapping.get(posture), fallback=fallback, allowed={"allow", "audit", "review", "block"})

    def _decision(
        self,
        *,
        verdict: str,
        reason: str,
        risk_code: str,
        governance_target: str,
        posture: str,
        details: Dict[str, Any] | None = None,
        allow_override: bool | None = None,
    ) -> SafetyDecision:
        normalized_verdict = self._normalize_verdict(verdict, fallback="allow", allowed={"allow", "audit", "review", "block"})
        next_details = dict(details or {})
        next_details.setdefault(
            "eventSummary",
            self._build_event_summary(
                verdict=normalized_verdict,
                reason=reason,
                risk_code=risk_code,
                governance_target=governance_target,
                details=next_details,
            ),
        )
        return SafetyDecision(
            verdict=normalized_verdict,  # type: ignore[arg-type]
            reason=reason,
            risk_code=risk_code,
            details=next_details,
            allow_override=(normalized_verdict != "block") if allow_override is None else allow_override,
            governance_target=governance_target,
            posture=posture,
        )

    def _build_event_summary(
        self,
        *,
        verdict: str,
        reason: str,
        risk_code: str,
        governance_target: str,
        details: Dict[str, Any],
    ) -> Dict[str, Any]:
        runtime_context = details.get("runtime_context") if isinstance(details.get("runtime_context"), dict) else {}
        trusted_network = details.get("trustedNetwork") if isinstance(details.get("trustedNetwork"), dict) else {}
        provider_id = str(trusted_network.get("providerId") or trusted_network.get("id") or details.get("providerId") or "").strip()
        target = (
            details.get("url")
            or details.get("path")
            or details.get("command")
            or details.get("target")
            or details.get("tool_name")
            or details.get("pid")
            or ""
        )
        method = str(details.get("method") or "").upper()
        host = ""
        if details.get("url"):
            host = (urlparse(str(details.get("url") or "")).hostname or "").lower()
        os_surface = "windows" if str(risk_code).startswith("windows_") else (
            "linux_macos" if str(risk_code).startswith(("linux_", "macos_", "cross_platform_", "privilege_", "firewall_")) else ""
        )
        operation = str(details.get("operation") or method or "").strip()
        if not operation:
            if details.get("command"):
                operation = "command"
            elif details.get("path"):
                operation = "file_write"
            elif details.get("url"):
                operation = "http_request"
            else:
                operation = str(governance_target or "safety")
        summary = {
            "actionFamily": str(governance_target or "safety"),
            "operation": operation,
            "target": self._redact_sensitive_text(str(target or ""))[:500],
            "method": method or None,
            "host": host or None,
            "providerId": provider_id or None,
            "credentialClass": details.get("credentialClass") or details.get("credential_class") or None,
            "osSurface": os_surface or None,
            "riskCode": risk_code,
            "verdict": verdict,
            "matchedRule": details.get("matchedRule") or details.get("registrySignal") or details.get("pattern") or None,
            "reason": self._redact_sensitive_text(str(reason or ""))[:500],
            "nextAction": "block" if verdict == "block" else ("approval_required" if verdict == "review" else "continue"),
            "redactedPreview": self._redact_sensitive_text(
                str(details.get("body_preview") or details.get("matched_command") or details.get("command") or "")
            )[:500],
            "omittedFields": [],
        }
        if runtime_context:
            summary["runtimeKind"] = runtime_context.get("runtime_kind") or runtime_context.get("runtimeKind") or None
            summary["runId"] = runtime_context.get("run_id") or runtime_context.get("runId") or None
        return {key: value for key, value in summary.items() if value not in (None, "", [])}

    def log_decision_event(
        self,
        *,
        action: str,
        decision: SafetyDecision,
        subject: str | None = None,
        metadata: Dict[str, Any] | None = None,
    ) -> None:
        if decision.verdict == "allow":
            return
        try:
            from core.audit_logger import audit_logger

            status = {
                "audit": "INFO",
                "review": "WARNING",
                "block": "ERROR",
            }.get(decision.verdict, "INFO")
            payload = {
                "subject": str(subject or "").strip() or None,
                "verdict": decision.verdict,
                "reason": decision.reason,
                "riskCode": decision.risk_code,
                "governanceTarget": decision.governance_target,
                "posture": decision.posture,
                "details": decision.details,
                "metadata": dict(metadata or {}),
            }
            audit_logger.log(
                source_type="SAFETY",
                action=action,
                status=status,
                details=json.dumps(payload, ensure_ascii=False),
            )
        except Exception:
            return

    def build_allowlist_candidate(self, decision: SafetyDecision) -> Dict[str, Any]:
        details = decision.details if isinstance(decision.details, dict) else {}
        runtime_context = details.get("runtime_context") if isinstance(details.get("runtime_context"), dict) else {}
        target_value = (
            details.get("path")
            or details.get("command")
            or details.get("url")
            or details.get("target")
            or details.get("pid")
            or ""
        )
        action = self._allowlist_action_for_decision(decision)
        normalized_target, target_label = self._normalize_allowlist_target(target_value, action=action)
        path_plane = self._classify_allowlist_path_plane(target_value, runtime_context=runtime_context, action=action)
        runtime_source = self._allowlist_runtime_source(runtime_context)
        content_hash = self._allowlist_content_hash(target_value, action=action)
        raw_key = json.dumps(
            {
                "target": normalized_target,
                "pathPlane": path_plane,
                "runtimeSource": runtime_source,
                "action": action,
                "riskCode": decision.risk_code,
                "contentHash": content_hash or "",
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return {
            "normalizedTargetHash": hashlib.sha256(raw_key.encode("utf-8")).hexdigest(),
            "normalizedTargetLabel": target_label,
            "pathPlane": path_plane,
            "runtimeSource": runtime_source,
            "action": action,
            "riskCode": decision.risk_code,
            "governanceTarget": decision.governance_target,
            "contentHash": content_hash,
        }

    def is_allowlisted(self, decision: SafetyDecision) -> Dict[str, Any] | None:
        candidate = self.build_allowlist_candidate(decision)
        entry = db.find_safety_allowlist_entry(
            normalized_target_hash=str(candidate.get("normalizedTargetHash") or ""),
            path_plane=str(candidate.get("pathPlane") or "unknown"),
            runtime_source=str(candidate.get("runtimeSource") or "unknown"),
            action=str(candidate.get("action") or "unknown"),
            risk_code=str(candidate.get("riskCode") or decision.risk_code),
            enabled_only=True,
        )
        return entry

    def record_allowlist_candidate(
        self,
        candidate: Dict[str, Any],
        *,
        approval_id: str | None = None,
        approval_kind: str | None = None,
        source: str = "admin_approval",
        metadata: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        return db.upsert_safety_allowlist_entry(
            entry_id=f"safetyallow_{uuid4().hex[:16]}",
            normalized_target_hash=str(candidate.get("normalizedTargetHash") or ""),
            normalized_target_label=str(candidate.get("normalizedTargetLabel") or "")[:300] or None,
            path_plane=str(candidate.get("pathPlane") or "unknown"),
            runtime_source=str(candidate.get("runtimeSource") or "unknown"),
            action=str(candidate.get("action") or "unknown"),
            risk_code=str(candidate.get("riskCode") or "unknown"),
            governance_target=str(candidate.get("governanceTarget") or "") or None,
            approval_id=approval_id,
            approval_kind=approval_kind,
            source=source,
            enabled=True,
            metadata={"contentHash": candidate.get("contentHash"), **(metadata or {})},
        )

    def record_allowlist_from_approval(self, approval: Dict[str, Any], response: Dict[str, Any] | None = None) -> Dict[str, Any] | None:
        response = dict(response or {})
        if not bool(
            response.get("persistSafetyAllowlist")
            or response.get("rememberSafetyAllowlist")
            or response.get("persist_safety_allowlist")
        ):
            return None
        request = approval.get("request") if isinstance(approval.get("request"), dict) else {}
        candidate = request.get("allowlistCandidate") if isinstance(request.get("allowlistCandidate"), dict) else {}
        if not candidate:
            return None
        return self.record_allowlist_candidate(
            candidate,
            approval_id=str(approval.get("id") or approval.get("approval_id") or ""),
            approval_kind=str(approval.get("approval_kind") or request.get("approvalKind") or ""),
            source="admin_approval",
            metadata={
                "runId": approval.get("run_id"),
                "sessionId": approval.get("session_id"),
                "answer": response.get("answer"),
            },
        )

    def list_safety_allowlist_entries(self, *, status: str | None = None, limit: int = 100) -> list[Dict[str, Any]]:
        return db.list_safety_allowlist_entries(status=status, limit=limit)

    def revoke_safety_allowlist_entry(self, entry_id: str) -> Dict[str, Any] | None:
        return db.revoke_safety_allowlist_entry(entry_id)

    def build_dashboard_payload(self, *, limit: int = 80) -> Dict[str, Any]:
        safety_approvals = []
        for approval in db.list_pending_approvals(status="pending"):
            request = approval.get("request") if isinstance(approval.get("request"), dict) else {}
            approval_kind = str(approval.get("approval_kind") or request.get("approvalKind") or "").strip().lower()
            if approval_kind not in {"safety_review", "safety_blocked"}:
                continue
            safety_approvals.append(self._project_safety_approval(approval))

        skill_reviews = self.list_skill_safety_reviews(limit=limit)
        allowlist = self.list_safety_allowlist_entries(limit=limit)
        audit_rows = db.get_audit_logs(limit=max(limit, 100), source_type="SAFETY")
        decisions = [self._project_safety_audit_row(row) for row in audit_rows]
        decisions = [item for item in decisions if item][:limit]
        verdict_counts: dict[str, int] = {}
        risk_counts: dict[str, int] = {}
        for item in decisions:
            verdict = str(item.get("verdict") or "unknown")
            verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1
            risk_code = str(item.get("riskCode") or "unknown")
            risk_counts[risk_code] = risk_counts.get(risk_code, 0) + 1
        active_defense = dict(safety_active_defense_monitor.dashboard(sample=True))
        active_defense_summary = dict(active_defense.get("summary") or {})
        active_defense_summary.update(
            {
                "recentSafetyReviewEvents": verdict_counts.get("review", 0),
                "recentSafetyBlockEvents": verdict_counts.get("block", 0),
                "recentHighRiskEvents": sum(
                    count
                    for risk_code, count in risk_counts.items()
                    if str(risk_code).startswith(("protected_", "destructive_", "encoded_", "download_execute"))
                ),
            }
        )
        active_defense["summary"] = active_defense_summary
        return {
            "pendingSafetyApprovals": safety_approvals[:limit],
            "skillSafetyReviews": skill_reviews[:limit],
            "allowlistEntries": allowlist[:limit],
            "recentDecisions": decisions,
            "activeDefense": active_defense,
            "summary": {
                "pendingSafetyApprovals": len(safety_approvals),
                "skillReviews": len(skill_reviews),
                "activeAllowlist": sum(1 for item in allowlist if item.get("enabled")),
                "recentDecisions": len(decisions),
                "verdictCounts": verdict_counts,
                "riskCounts": risk_counts,
                "activeDefenseIncidents": int((active_defense.get("summary") or {}).get("activeIncidents") or 0),
            },
        }

    def explain_system_command(self, command: str, *, runtime_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        runtime_context = dict(runtime_context or {})
        analysis = self._analyze_system_command(command, runtime_context=runtime_context)
        snapshot = list(self._recent_downloads)
        try:
            decision = self.assess_system_command(command, runtime_context=runtime_context)
        finally:
            self._recent_downloads = snapshot
        return {
            "command": self._redact_sensitive_text(str(command or "")),
            "normalizedCommand": self._redact_sensitive_text(re.sub(r"\s+", " ", str(command or "")).strip()),
            "analysis": self._redact_sensitive_payload(analysis.to_payload()),
            "expectedVerdict": decision.verdict,
            "riskCode": decision.risk_code,
            "governanceTarget": decision.governance_target,
            "reason": decision.reason,
            "allowlistCandidate": self.build_allowlist_candidate(decision) if not decision.is_allow() else None,
            "dryRun": True,
        }

    def _allowlist_action_for_decision(self, decision: SafetyDecision) -> str:
        details = decision.details if isinstance(decision.details, dict) else {}
        if details.get("command"):
            return "command"
        if details.get("url"):
            return "network_mutate" if str(details.get("method") or "").upper() in {"POST", "PUT", "PATCH", "DELETE"} else "network_read"
        if details.get("path"):
            risk = str(decision.risk_code or "").lower()
            if "write" in risk:
                return "write"
            if "delete" in risk or "destructive" in risk:
                return "delete"
            if "execute" in risk:
                return "execute"
            return "path"
        if details.get("pid"):
            return "process"
        return str(decision.governance_target or "general")

    def _allowlist_runtime_source(self, runtime_context: Dict[str, Any] | None) -> str:
        if not isinstance(runtime_context, dict):
            return "unknown"
        for key in ("runtime_kind", "runtimeKind", "runtime", "source_runtime", "sourceRuntime"):
            value = str(runtime_context.get(key) or "").strip().lower()
            if value:
                return value
        return "unknown"

    def _normalize_allowlist_target(self, value: Any, *, action: str) -> tuple[str, str]:
        raw = str(value or "").strip()
        if not raw:
            return "unknown", "unknown"
        if action == "command":
            normalized = re.sub(r"\s+", " ", raw).strip().lower()
            return normalized, self._redact_sensitive_text(normalized[:300])
        if action.startswith("network"):
            parsed = urlparse(raw)
            if parsed.scheme and parsed.netloc:
                normalized = f"{parsed.scheme.lower()}://{(parsed.hostname or parsed.netloc).lower()}{parsed.path or ''}"
                return normalized, normalized
        normalized_path = self._normalize_path(raw)
        if normalized_path is not None and (normalized_path.is_absolute() or "\\" in raw or "/" in raw):
            path_label = str(normalized_path)
            return path_label.lower(), path_label
        normalized = re.sub(r"\s+", " ", raw).strip().lower()
        return normalized, self._redact_sensitive_text(normalized[:300])

    def _classify_allowlist_path_plane(self, value: Any, *, runtime_context: Dict[str, Any] | None, action: str) -> str:
        raw = str(value or "").strip()
        if action.startswith("network"):
            return "network"
        runtime_context = dict(runtime_context or {})
        candidates: list[Path] = []
        for key in ("workspace_path", "workspacePath", "project_workspace", "projectWorkspace"):
            path = self._normalize_path(runtime_context.get(key))
            if path is not None:
                candidates.append(path)
        raw_looks_like_path = action != "command" and bool(re.match(r"^[a-zA-Z]:[\\/]", raw) or raw.startswith(("\\\\", "/", "~\\", "~/")) or "\\" in raw or "/" in raw)
        normalized = self._normalize_path(raw) if raw_looks_like_path else None
        if normalized is None:
            for candidate in self._extract_explicit_paths_from_command(raw):
                normalized = candidate
                break
        if normalized is None:
            return "command"
        for workspace in [*candidates, WORKSPACE_HOME]:
            try:
                normalized.relative_to(workspace.expanduser().resolve(strict=False))
                return "workspace"
            except Exception:
                continue
        lowered = str(normalized).lower()
        if "\\openclaw" in lowered or "/openclaw" in lowered:
            return "openclaw_workspace"
        home = Path.home().expanduser().resolve(strict=False)
        try:
            normalized.relative_to((home / ".agents").resolve(strict=False))
            return "agents_home"
        except Exception:
            pass
        temp_roots = [
            self._normalize_path(os.environ.get("TEMP")),
            self._normalize_path(os.environ.get("TMP")),
            Path("/tmp"),
        ]
        for temp_root in [item for item in temp_roots if item is not None]:
            try:
                normalized.relative_to(temp_root.expanduser().resolve(strict=False))
                return "temp"
            except Exception:
                continue
        code_root = Path(__file__).resolve().parents[3]
        try:
            normalized.relative_to(code_root)
            return "v8_codebase"
        except Exception:
            pass
        if self._is_sensitive_system_path(normalized):
            return "core_os"
        try:
            normalized.relative_to(home)
            return "user_home"
        except Exception:
            return "external_unknown"

    def _allowlist_content_hash(self, value: Any, *, action: str) -> str | None:
        if action.startswith("network"):
            return None
        paths: list[Path] = []
        raw = str(value or "").strip()
        if action == "command":
            paths.extend(self._extract_explicit_paths_from_command(raw))
        else:
            normalized = self._normalize_path(raw)
            if normalized is not None:
                paths.append(normalized)
        for path in paths:
            try:
                if path.exists() and path.is_file():
                    return self._hash_file(path)
            except Exception:
                continue
        return None

    def _project_safety_approval(self, approval: Dict[str, Any]) -> Dict[str, Any]:
        request = approval.get("request") if isinstance(approval.get("request"), dict) else {}
        safety = request.get("safety") if isinstance(request.get("safety"), dict) else {}
        event_summary = safety.get("eventSummary") if isinstance(safety.get("eventSummary"), dict) else {}
        if not event_summary:
            details = safety.get("details") if isinstance(safety.get("details"), dict) else {}
            event_summary = details.get("eventSummary") if isinstance(details.get("eventSummary"), dict) else {}
        allowlist_candidate = request.get("allowlistCandidate") if isinstance(request.get("allowlistCandidate"), dict) else None
        return {
            "id": approval.get("id"),
            "session_id": approval.get("session_id"),
            "run_id": approval.get("run_id"),
            "approval_kind": approval.get("approval_kind"),
            "status": approval.get("status"),
            "created_at": approval.get("created_at"),
            "question": self._redact_sensitive_text(str(request.get("question") or request.get("prompt") or "")),
            "riskCode": safety.get("risk_code") or safety.get("riskCode") or request.get("riskCode"),
            "verdict": safety.get("verdict"),
            "reason": self._redact_sensitive_text(str(safety.get("reason") or "")),
            "eventSummary": event_summary,
            "allowlistCandidate": allowlist_candidate,
        }

    def _project_safety_audit_row(self, row: Dict[str, Any]) -> Dict[str, Any] | None:
        raw_details = row.get("details")
        if not raw_details:
            return None
        try:
            details = json.loads(raw_details)
        except (TypeError, json.JSONDecodeError):
            return None
        safety_details = details.get("details") if isinstance(details.get("details"), dict) else {}
        runtime_context = safety_details.get("runtime_context") if isinstance(safety_details.get("runtime_context"), dict) else {}
        analysis = safety_details.get("analysis") if isinstance(safety_details.get("analysis"), dict) else {}
        event_summary = details.get("eventSummary") if isinstance(details.get("eventSummary"), dict) else {}
        if not event_summary:
            event_summary = safety_details.get("eventSummary") if isinstance(safety_details.get("eventSummary"), dict) else {}
        return {
            "id": row.get("id"),
            "timestamp": row.get("timestamp"),
            "action": row.get("action"),
            "status": row.get("status"),
            "verdict": details.get("verdict"),
            "riskCode": details.get("riskCode"),
            "governanceTarget": details.get("governanceTarget"),
            "runtimeSource": self._allowlist_runtime_source(runtime_context),
            "subject": self._redact_sensitive_text(str(details.get("subject") or "")),
            "reason": self._redact_sensitive_text(str(details.get("reason") or "")),
            "decodedPreview": self._redact_sensitive_payload(analysis.get("decodedCommands") if isinstance(analysis, dict) else []),
            "encodedIndicators": analysis.get("encodedIndicators") if isinstance(analysis, dict) else [],
            "downloadHosts": analysis.get("downloadHosts") if isinstance(analysis, dict) else [],
            "eventSummary": event_summary,
        }

    def _redact_sensitive_payload(self, value: Any) -> Any:
        if isinstance(value, str):
            return self._redact_sensitive_text(value)
        if isinstance(value, list):
            return [self._redact_sensitive_payload(item) for item in value[:20]]
        if isinstance(value, dict):
            redacted: dict[str, Any] = {}
            for key, item in value.items():
                lowered = str(key).lower()
                if any(token in lowered for token in ("key", "token", "secret", "password", "cookie")):
                    redacted[key] = "[REDACTED]"
                else:
                    redacted[key] = self._redact_sensitive_payload(item)
            return redacted
        return value

    def _redact_sensitive_text(self, value: str) -> str:
        text = str(value or "")
        text = re.sub(r"(?i)(api[_-]?key|token|secret|password|cookie)(\s*[:=]\s*)([^\s;&|]+)", r"\1\2[REDACTED]", text)
        text = re.sub(r"(?i)(bearer\s+)[a-z0-9._\-+/=]{12,}", r"\1[REDACTED]", text)
        text = re.sub(r"(?i)(sk-[a-z0-9]{8})[a-z0-9_\-]{12,}", r"\1[REDACTED]", text)
        return text

    def normalize_config(self, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        raw = deepcopy(config if config is not None else storage.get_safety_guardian_config() or {})
        merged = deepcopy(DEFAULT_SAFETY_GUARDIAN_CONFIG)

        merged["enabled"] = bool(raw.get("enabled", merged["enabled"]))
        machine_posture = str(raw.get("machinePosture") or merged["machinePosture"]).strip().lower()
        if machine_posture not in VALID_MACHINE_POSTURES:
            machine_posture = MACHINE_POSTURE_DEDICATED
        merged["machinePosture"] = machine_posture

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
            "trustedHosts": [str(item).strip().lower() for item in network_rules.get("trustedHosts", []) if str(item).strip()],
            "reviewMethods": [str(item).strip().upper() for item in network_rules.get("reviewMethods", []) if str(item).strip()] or merged["networkRules"]["reviewMethods"],
            "unknownCredentialHostVerdict": self._normalize_verdict(
                network_rules.get("unknownCredentialHostVerdict"),
                fallback=DEFAULT_SAFETY_GUARDIAN_CONFIG["networkRules"]["unknownCredentialHostVerdict"],
                allowed={"audit", "review", "block"},
            ),
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
                "auditTriggerSources": [str(item).strip().lower() for item in configured_rules.get("auditTriggerSources", []) if str(item).strip()],
                "reviewTriggerSources": [str(item).strip().lower() for item in configured_rules.get("reviewTriggerSources", []) if str(item).strip()],
                "blockedTriggerSources": [str(item).strip().lower() for item in configured_rules.get("blockedTriggerSources", []) if str(item).strip()],
                "auditScopePatterns": [str(item).strip().lower() for item in configured_rules.get("auditScopePatterns", []) if str(item).strip()],
                "reviewScopePatterns": [str(item).strip().lower() for item in configured_rules.get("reviewScopePatterns", []) if str(item).strip()],
                "blockedScopePatterns": [str(item).strip().lower() for item in configured_rules.get("blockedScopePatterns", []) if str(item).strip()],
            }
            for key, fallback_value in default_rules.items():
                if not merged["runtimeRules"][runtime_kind][key]:
                    merged["runtimeRules"][runtime_kind][key] = list(fallback_value)

        skill_rules = dict(raw.get("skillRules") or {})
        merged["skillRules"] = {
            "declarationVerdict": self._normalize_verdict(
                skill_rules.get("declarationVerdict"),
                fallback=DEFAULT_SAFETY_GUARDIAN_CONFIG["skillRules"]["declarationVerdict"],
                allowed={"allow", "audit", "review"},
            ),
            "localSecretReadVerdict": self._normalize_verdict(
                skill_rules.get("localSecretReadVerdict"),
                fallback=DEFAULT_SAFETY_GUARDIAN_CONFIG["skillRules"]["localSecretReadVerdict"],
                allowed={"audit", "review", "block"},
            ),
            "browserProfileAccessVerdict": self._normalize_posture_map(
                skill_rules.get("browserProfileAccessVerdict"),
                fallback=DEFAULT_SAFETY_GUARDIAN_CONFIG["skillRules"]["browserProfileAccessVerdict"],
                allowed={"review", "block"},
            ),
            "downloadExecuteVerdict": self._normalize_verdict(
                skill_rules.get("downloadExecuteVerdict"),
                fallback=DEFAULT_SAFETY_GUARDIAN_CONFIG["skillRules"]["downloadExecuteVerdict"],
                allowed={"review", "block"},
            ),
            "persistenceVerdict": self._normalize_verdict(
                skill_rules.get("persistenceVerdict"),
                fallback=DEFAULT_SAFETY_GUARDIAN_CONFIG["skillRules"]["persistenceVerdict"],
                allowed={"review", "block"},
            ),
            "destructiveVerdict": self._normalize_verdict(
                skill_rules.get("destructiveVerdict"),
                fallback=DEFAULT_SAFETY_GUARDIAN_CONFIG["skillRules"]["destructiveVerdict"],
                allowed={"review", "block"},
            ),
            "binaryPayloadVerdict": self._normalize_verdict(
                skill_rules.get("binaryPayloadVerdict"),
                fallback=DEFAULT_SAFETY_GUARDIAN_CONFIG["skillRules"]["binaryPayloadVerdict"],
                allowed={"audit", "review", "block"},
            ),
            "llmReviewEnabledFor": [
                item
                for item in (
                    self._normalize_verdict(candidate, fallback="", allowed={"review", "block"})
                    for candidate in list(skill_rules.get("llmReviewEnabledFor") or [])
                )
                if item
            ] or list(DEFAULT_SAFETY_GUARDIAN_CONFIG["skillRules"]["llmReviewEnabledFor"]),
        }

        network_mutation_rules = dict(raw.get("networkMutationRules") or {})
        merged["networkMutationRules"] = {
            "defaultExternalMutationVerdict": self._normalize_posture_map(
                network_mutation_rules.get("defaultExternalMutationVerdict"),
                fallback=DEFAULT_SAFETY_GUARDIAN_CONFIG["networkMutationRules"]["defaultExternalMutationVerdict"],
                allowed={"audit", "review"},
            ),
            "sensitivePayloadVerdict": self._normalize_verdict(
                network_mutation_rules.get("sensitivePayloadVerdict"),
                fallback=DEFAULT_SAFETY_GUARDIAN_CONFIG["networkMutationRules"]["sensitivePayloadVerdict"],
                allowed={"audit", "review", "block"},
            ),
            "credentialExfiltrationVerdict": self._normalize_verdict(
                network_mutation_rules.get("credentialExfiltrationVerdict"),
                fallback=DEFAULT_SAFETY_GUARDIAN_CONFIG["networkMutationRules"]["credentialExfiltrationVerdict"],
                allowed={"review", "block"},
            ),
        }

        computer_use_rules = dict(raw.get("computerUseRules") or {})
        merged["computerUseRules"] = {
            "defaultMutationVerdict": self._normalize_posture_map(
                computer_use_rules.get("defaultMutationVerdict"),
                fallback=DEFAULT_SAFETY_GUARDIAN_CONFIG["computerUseRules"]["defaultMutationVerdict"],
                allowed={"audit", "review"},
            ),
            "destructiveKeywordVerdict": self._normalize_verdict(
                computer_use_rules.get("destructiveKeywordVerdict"),
                fallback=DEFAULT_SAFETY_GUARDIAN_CONFIG["computerUseRules"]["destructiveKeywordVerdict"],
                allowed={"review", "block"},
            ),
            "hotkeyLifecycleVerdict": self._normalize_verdict(
                computer_use_rules.get("hotkeyLifecycleVerdict"),
                fallback=DEFAULT_SAFETY_GUARDIAN_CONFIG["computerUseRules"]["hotkeyLifecycleVerdict"],
                allowed={"audit", "review", "block"},
            ),
        }

        system_integrity_rules = dict(raw.get("systemIntegrityRules") or {})
        merged["systemIntegrityRules"] = {
            "packageInstallVerdict": self._normalize_posture_map(
                system_integrity_rules.get("packageInstallVerdict"),
                fallback=DEFAULT_SAFETY_GUARDIAN_CONFIG["systemIntegrityRules"]["packageInstallVerdict"],
                allowed={"audit", "review"},
            ),
            "destructiveCommandVerdict": self._normalize_verdict(
                system_integrity_rules.get("destructiveCommandVerdict"),
                fallback=DEFAULT_SAFETY_GUARDIAN_CONFIG["systemIntegrityRules"]["destructiveCommandVerdict"],
                allowed={"review", "block"},
            ),
        }

        v8_integrity_rules = dict(raw.get("v8IntegrityRules") or {})
        merged["v8IntegrityRules"] = {
            "protectedConfigWriteVerdict": self._normalize_verdict(
                v8_integrity_rules.get("protectedConfigWriteVerdict"),
                fallback=DEFAULT_SAFETY_GUARDIAN_CONFIG["v8IntegrityRules"]["protectedConfigWriteVerdict"],
                allowed={"audit", "review", "block"},
            ),
            "protectedRuntimeProcessVerdict": self._normalize_verdict(
                v8_integrity_rules.get("protectedRuntimeProcessVerdict"),
                fallback=DEFAULT_SAFETY_GUARDIAN_CONFIG["v8IntegrityRules"]["protectedRuntimeProcessVerdict"],
                allowed={"review", "block"},
            ),
        }

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

        windows_profile_protection = dict(raw.get("windowsProfileProtection") or {})
        merged["windowsProfileProtection"] = {
            "enabled": bool(
                windows_profile_protection.get(
                    "enabled",
                    DEFAULT_SAFETY_GUARDIAN_CONFIG["windowsProfileProtection"]["enabled"],
                )
            ),
            "sensitiveReadVerdict": self._normalize_verdict(
                windows_profile_protection.get("sensitiveReadVerdict"),
                fallback=DEFAULT_SAFETY_GUARDIAN_CONFIG["windowsProfileProtection"]["sensitiveReadVerdict"],
                allowed={"audit", "review", "block"},
            ),
            "highRiskMutationVerdict": self._normalize_verdict(
                windows_profile_protection.get("highRiskMutationVerdict"),
                fallback=DEFAULT_SAFETY_GUARDIAN_CONFIG["windowsProfileProtection"]["highRiskMutationVerdict"],
                allowed={"review", "block"},
            ),
            "mediumRiskMutationVerdict": self._normalize_verdict(
                windows_profile_protection.get("mediumRiskMutationVerdict"),
                fallback=DEFAULT_SAFETY_GUARDIAN_CONFIG["windowsProfileProtection"]["mediumRiskMutationVerdict"],
                allowed={"audit", "review", "block"},
            ),
        }

        cross_platform_system_protection = dict(raw.get("crossPlatformSystemProtection") or {})
        merged["crossPlatformSystemProtection"] = {
            "enabled": bool(
                cross_platform_system_protection.get(
                    "enabled",
                    DEFAULT_SAFETY_GUARDIAN_CONFIG["crossPlatformSystemProtection"]["enabled"],
                )
            ),
            "sensitiveReadVerdict": self._normalize_verdict(
                cross_platform_system_protection.get("sensitiveReadVerdict"),
                fallback=DEFAULT_SAFETY_GUARDIAN_CONFIG["crossPlatformSystemProtection"]["sensitiveReadVerdict"],
                allowed={"audit", "review", "block"},
            ),
            "highRiskMutationVerdict": self._normalize_verdict(
                cross_platform_system_protection.get("highRiskMutationVerdict"),
                fallback=DEFAULT_SAFETY_GUARDIAN_CONFIG["crossPlatformSystemProtection"]["highRiskMutationVerdict"],
                allowed={"review", "block"},
            ),
            "mediumRiskMutationVerdict": self._normalize_verdict(
                cross_platform_system_protection.get("mediumRiskMutationVerdict"),
                fallback=DEFAULT_SAFETY_GUARDIAN_CONFIG["crossPlatformSystemProtection"]["mediumRiskMutationVerdict"],
                allowed={"audit", "review", "block"},
            ),
            "privilegeElevationVerdict": self._normalize_verdict(
                cross_platform_system_protection.get("privilegeElevationVerdict"),
                fallback=DEFAULT_SAFETY_GUARDIAN_CONFIG["crossPlatformSystemProtection"]["privilegeElevationVerdict"],
                allowed={"audit", "review", "block"},
            ),
            "persistenceVerdict": self._normalize_verdict(
                cross_platform_system_protection.get("persistenceVerdict"),
                fallback=DEFAULT_SAFETY_GUARDIAN_CONFIG["crossPlatformSystemProtection"]["persistenceVerdict"],
                allowed={"audit", "review", "block"},
            ),
            "firewallVerdict": self._normalize_verdict(
                cross_platform_system_protection.get("firewallVerdict"),
                fallback=DEFAULT_SAFETY_GUARDIAN_CONFIG["crossPlatformSystemProtection"]["firewallVerdict"],
                allowed={"audit", "review", "block"},
            ),
        }

        external_tool_local_system_protection = dict(raw.get("externalToolLocalSystemProtection") or {})
        merged["externalToolLocalSystemProtection"] = {
            "enabled": bool(
                external_tool_local_system_protection.get(
                    "enabled",
                    DEFAULT_SAFETY_GUARDIAN_CONFIG["externalToolLocalSystemProtection"]["enabled"],
                )
            ),
        }

        merged["activeDefense"] = normalize_active_defense_config(
            raw.get("activeDefense") if isinstance(raw.get("activeDefense"), dict) else {}
        )

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
        posture = self._current_posture(config)
        runtime_rule = dict((config.get("runtimeRules") or {}).get(runtime_kind, {}) or {})
        normalized_trigger = (trigger_source or "").strip().lower()
        normalized_scope = (resolved_scope or "").strip().lower()

        if self._matches_patterns(normalized_trigger, runtime_rule.get("blockedTriggerSources", [])):
            return self._decision(
                verdict="block",
                reason=f"{runtime_kind} 运行时命中了阻断触发源规则：{trigger_source}",
                risk_code="runtime_blocked_trigger",
                governance_target="operator_posture",
                posture=posture,
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
            return self._decision(
                verdict="block",
                reason=f"{runtime_kind} 运行时命中了阻断 scope 规则：{resolved_scope}",
                risk_code="runtime_blocked_scope",
                governance_target="operator_posture",
                posture=posture,
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

        if self._matches_patterns(normalized_trigger, runtime_rule.get("auditTriggerSources", [])):
            return self._decision(
                verdict="audit",
                reason=f"{runtime_kind} 运行时命中了审计触发源规则：{trigger_source}",
                risk_code="runtime_audit_trigger",
                governance_target="operator_posture",
                posture=posture,
                details={
                    "runtime_kind": runtime_kind,
                    "trigger_source": trigger_source,
                    "session_id": session_id,
                    "run_id": run_id,
                    "resolved_scope": resolved_scope,
                    "user_id": user_id,
                },
            )

        if self._matches_patterns(normalized_trigger, runtime_rule.get("reviewTriggerSources", [])):
            return self._decision(
                verdict="review",
                reason=f"{runtime_kind} 运行时命中了复核触发源规则：{trigger_source}",
                risk_code="runtime_review_trigger",
                governance_target="operator_posture",
                posture=posture,
                details={
                    "runtime_kind": runtime_kind,
                    "trigger_source": trigger_source,
                    "session_id": session_id,
                    "run_id": run_id,
                    "resolved_scope": resolved_scope,
                    "user_id": user_id,
                },
            )

        if normalized_scope and self._matches_patterns(normalized_scope, runtime_rule.get("auditScopePatterns", [])):
            return self._decision(
                verdict="audit",
                reason=f"{runtime_kind} 运行时命中了审计 scope 规则：{resolved_scope}",
                risk_code="runtime_audit_scope",
                governance_target="operator_posture",
                posture=posture,
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
            return self._decision(
                verdict="review",
                reason=f"{runtime_kind} 运行时命中了复核 scope 规则：{resolved_scope}",
                risk_code="runtime_review_scope",
                governance_target="operator_posture",
                posture=posture,
                details={
                    "runtime_kind": runtime_kind,
                    "trigger_source": trigger_source,
                    "session_id": session_id,
                    "run_id": run_id,
                    "resolved_scope": resolved_scope,
                    "user_id": user_id,
                },
            )

        return self._decision(
            verdict="allow",
            reason="runtime_preflight_passed",
            risk_code="runtime_preflight",
            governance_target="operator_posture",
            posture=posture,
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
            "interactionKind": "approval",
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
        posture = self._current_posture(config)
        runtime_context = runtime_context or {}
        if not config["enabled"]:
            return SafetyDecision()

        normalized = (command or "").strip().lower()
        if not normalized:
            return SafetyDecision()

        analysis = self._analyze_system_command(command, runtime_context=runtime_context)
        analysis_payload = analysis.to_payload()
        policy_commands = analysis.policy_commands
        self._remember_recent_downloads(analysis, runtime_context)

        if any(self._is_workspace_read_only_enumeration_command(candidate, runtime_context) for candidate in policy_commands):
            return self._decision(
                verdict="allow",
                reason="workspace_read_allowed",
                risk_code="workspace_read_allowed",
                governance_target="system_integrity",
                posture=posture,
                details={"command": command, "runtime_context": runtime_context, "analysis": analysis_payload},
            )

        windows_profile_decision = None
        for candidate in policy_commands:
            windows_profile_decision = self._assess_windows_profile_command(
                candidate,
                config=config,
                posture=posture,
                runtime_context=runtime_context,
                analysis_payload=analysis_payload,
            )
            if windows_profile_decision is not None:
                break
        if windows_profile_decision is not None:
            return windows_profile_decision

        cross_platform_decision = None
        for candidate in policy_commands:
            cross_platform_decision = self._assess_cross_platform_system_command(
                candidate,
                config=config,
                posture=posture,
                runtime_context=runtime_context,
                analysis_payload=analysis_payload,
            )
            if cross_platform_decision is not None:
                break
        if cross_platform_decision is not None:
            return cross_platform_decision

        sensitive_read_command = next(
            (
                candidate
                for candidate in policy_commands
                if self._looks_like_read_only_enumeration_command(candidate)
                and self._targets_sensitive_system_path_in_command(candidate)
            ),
            None,
        )
        if sensitive_read_command:
            return self._decision(
                verdict="review",
                reason="当前命令会枚举或访问系统敏感路径，需要人工确认。",
                risk_code="sensitive_system_read_command",
                governance_target="system_integrity",
                posture=posture,
                details={"command": command, "matched_command": sensitive_read_command, "runtime_context": runtime_context, "analysis": analysis_payload},
            )

        protected_path_command = next((candidate for candidate in policy_commands if self._touches_protected_path_in_command(candidate)), None)
        if protected_path_command:
            return self._decision(
                verdict=config["systemIntegrityRules"]["destructiveCommandVerdict"],
                reason="命令试图删除、覆盖或移动 v8chat 核心目录/受保护路径。",
                risk_code="protected_path_command",
                governance_target="v8_integrity",
                posture=posture,
                details={"command": command, "matched_command": protected_path_command, "runtime_context": runtime_context, "analysis": analysis_payload},
                allow_override=False,
            )

        protected_process_command = next((candidate for candidate in policy_commands if self._targets_protected_process(candidate)), None)
        if protected_process_command:
            return self._decision(
                verdict=config["v8IntegrityRules"]["protectedRuntimeProcessVerdict"],
                reason="命令疑似试图结束 v8chat 主程序或关键守护进程。",
                risk_code="protected_process_command",
                governance_target="v8_integrity",
                posture=posture,
                details={"command": command, "matched_command": protected_process_command, "runtime_context": runtime_context, "analysis": analysis_payload},
                allow_override=False,
            )

        skill_root_command = next((candidate for candidate in policy_commands if self._command_touches_skill_root(candidate, runtime_context)), None)
        if skill_root_command:
            mutation_level = self._skill_root_command_mutation_level(skill_root_command)
            if mutation_level == "destructive":
                return self._decision(
                    verdict="block",
                    reason="命令疑似会清空或删除 Skill 根目录。此类高危批量写删已被阻断。",
                    risk_code="protected_skill_root_destructive_command",
                    governance_target="extensions_integrity",
                    posture=posture,
                    details={"command": command, "matched_command": skill_root_command, "runtime_context": runtime_context, "analysis": analysis_payload},
                    allow_override=False,
                )
            if mutation_level == "mutation":
                return self._decision(
                    verdict="review",
                    reason="命令疑似会写入或覆盖 Skill 根目录内容，需要人工确认。",
                    risk_code="protected_skill_root_mutation_command",
                    governance_target="extensions_integrity",
                    posture=posture,
                    details={"command": command, "matched_command": skill_root_command, "runtime_context": runtime_context, "analysis": analysis_payload},
                )

        skills_overwrite_command = next((candidate for candidate in policy_commands if self._is_skills_overwrite_install_command(candidate)), None)
        if skills_overwrite_command:
            return self._decision(
                verdict="review",
                reason="命令会覆盖已有 Skill。Skill 删除只能通过 Admin Extensions 的手动删除按钮完成；覆盖更新需要人工确认。",
                risk_code="skill_install_overwrite_command",
                governance_target="extensions_integrity",
                posture=posture,
                details={"command": command, "matched_command": skills_overwrite_command, "runtime_context": runtime_context, "analysis": analysis_payload},
            )

        process_control_command = next((candidate for candidate in policy_commands if self._is_process_control_command(candidate)), None)
        if process_control_command:
            return self._decision(
                verdict="review",
                reason="当前命令涉及进程控制，需要人工确认。",
                risk_code="process_control_command",
                governance_target="system_integrity",
                posture=posture,
                details={"command": command, "matched_command": process_control_command, "runtime_context": runtime_context, "analysis": analysis_payload},
            )

        if analysis.download_execute_detected:
            return self._decision(
                verdict="review",
                reason="检测到下载后执行链路，需要人工确认。",
                risk_code="download_execute_command",
                governance_target="system_integrity",
                posture=posture,
                details={"command": command, "runtime_context": runtime_context, "analysis": analysis_payload},
            )

        recent_download = self._matches_recent_download_execution(command, runtime_context)
        if recent_download is not None:
            return self._decision(
                verdict="review",
                reason="当前命令正在执行近期下载的脚本或可执行文件，需要人工确认。",
                risk_code="recent_download_execution",
                governance_target="system_integrity",
                posture=posture,
                details={
                    "command": command,
                    "runtime_context": runtime_context,
                    "recentDownload": {
                        "path": str(recent_download.path),
                        "host": recent_download.host,
                        "recordedAt": recent_download.recorded_at,
                        "runtimeKind": recent_download.runtime_kind,
                    },
                    "analysis": analysis_payload,
                },
            )

        if analysis.ambiguous_encoded_execution:
            return self._decision(
                verdict="review",
                reason="检测到疑似编码/混淆执行，但无法安全还原完整意图，需要人工确认。",
                risk_code="encoded_command_review",
                governance_target="system_integrity",
                posture=posture,
                details={"command": command, "runtime_context": runtime_context, "analysis": analysis_payload},
            )

        for rule in config["commandRules"]:
            for pattern in rule["patterns"]:
                matched_command = next((candidate for candidate in policy_commands if self._matches_command_pattern(candidate, pattern)), None)
                if matched_command:
                    verdict = "block" if rule["verdict"] == "block" else "review"
                    governance_target = "system_integrity" if verdict == "block" else "external_mutation"
                    if verdict == "review" and any(self._is_package_install_command(candidate) for candidate in policy_commands):
                        verdict = self._posture_verdict(
                            config["systemIntegrityRules"]["packageInstallVerdict"],
                            posture=posture,
                            fallback="review",
                        )
                    return self._decision(
                        verdict=verdict,
                        reason=f"命令命中了{rule['label']}：{pattern}",
                        risk_code="blocked_command_pattern" if verdict == "block" else "review_command_pattern",
                        governance_target=governance_target,
                        posture=posture,
                        details={"command": command, "matched_command": matched_command, "pattern": pattern, "rule": rule, "runtime_context": runtime_context, "analysis": analysis_payload},
                        allow_override=verdict != "block",
                    )

        if any(self._is_package_install_command(candidate) for candidate in policy_commands):
            verdict = self._posture_verdict(
                config["systemIntegrityRules"]["packageInstallVerdict"],
                posture=posture,
                fallback="audit",
            )
            return self._decision(
                verdict=verdict,
                reason="当前命令会安装或变更本机依赖环境，已按机器姿态记录治理结果。",
                risk_code="package_install_command",
                governance_target="system_integrity",
                posture=posture,
                details={"command": command, "runtime_context": runtime_context, "analysis": analysis_payload},
            )

        return self._decision(
            verdict="allow",
            reason="command_allowed",
            risk_code="command_allowed",
            governance_target="system_integrity",
            posture=posture,
            details={"command": command, "runtime_context": runtime_context, "analysis": analysis_payload},
        )

    def assess_background_command(self, command: str, *, runtime_context: Optional[Dict[str, Any]] = None) -> SafetyDecision:
        return self.assess_system_command(command, runtime_context=runtime_context)

    def assess_file_write(
        self,
        path: str,
        *,
        append: bool,
        runtime_context: Optional[Dict[str, Any]] = None,
        content_preview: str | None = None,
    ) -> SafetyDecision:
        config = self._config()
        posture = self._current_posture(config)
        if not config["enabled"]:
            return SafetyDecision()

        normalized = self._normalize_path(path)
        if not normalized:
            return SafetyDecision()

        windows_profile_file_decision = self._assess_windows_profile_file_write(
            normalized,
            append=append,
            config=config,
            posture=posture,
            runtime_context=runtime_context or {},
            content_preview=content_preview,
        )
        if windows_profile_file_decision is not None:
            return windows_profile_file_decision

        cross_platform_file_decision = self._assess_cross_platform_file_write(
            normalized,
            append=append,
            config=config,
            posture=posture,
            runtime_context=runtime_context or {},
            content_preview=content_preview,
        )
        if cross_platform_file_decision is not None:
            return cross_platform_file_decision

        if self._matches_path_patterns(normalized, config["fileRules"]["blockedPathPatterns"]):
            return self._decision(
                verdict="block",
                reason="目标路径命中了 Safety Guardian 的阻断文件规则。",
                risk_code="blocked_file_pattern",
                governance_target="system_integrity",
                posture=posture,
                details={"path": str(normalized), "append": append, "runtime_context": runtime_context or {}},
                allow_override=False,
            )

        if self._is_under_skill_root(normalized, runtime_context):
            return self._decision(
                verdict="review",
                reason="当前写入目标位于 Skill 根目录。Skill 扫描器只读，任何写入、清空或删除 Skill 文件都需要人工确认。",
                risk_code="protected_skill_root_write",
                governance_target="extensions_integrity",
                posture=posture,
                details={"path": str(normalized), "append": append, "runtime_context": runtime_context or {}},
            )

        if self._is_user_workspace_write_path(normalized, runtime_context):
            return self._decision(
                verdict="allow",
                reason="workspace_file_write_allowed",
                risk_code="workspace_file_write_allowed",
                governance_target="workspace_artifact",
                posture=posture,
                details={"path": str(normalized), "append": append, "runtime_context": runtime_context or {}},
            )

        if normalized.suffix.lower() in set(config["fileRules"]["protectedFileExtensions"]) and self._is_under_protected_path(normalized):
            return self._decision(
                verdict="block",
                reason="禁止直接写入受保护目录下的核心状态/数据库文件。",
                risk_code="protected_database_write",
                governance_target="v8_integrity",
                posture=posture,
                details={"path": str(normalized), "append": append, "runtime_context": runtime_context or {}},
                allow_override=False,
            )

        if self._is_under_protected_path(normalized) or self._matches_path_patterns(normalized, config["fileRules"]["reviewPathPatterns"]):
            return self._decision(
                verdict=config["v8IntegrityRules"]["protectedConfigWriteVerdict"],
                reason="当前写入目标位于 v8chat 核心配置/状态目录，需要人工确认。",
                risk_code="protected_config_write",
                governance_target="v8_integrity",
                posture=posture,
                details={"path": str(normalized), "append": append, "runtime_context": runtime_context or {}},
            )

        if self._is_sensitive_system_path(normalized):
            return self._decision(
                verdict="block",
                reason="当前写入目标位于系统敏感路径，已被 Safety Guardian 阻止。",
                risk_code="sensitive_system_write",
                governance_target="system_integrity",
                posture=posture,
                details={"path": str(normalized), "append": append, "runtime_context": runtime_context or {}},
                allow_override=False,
            )

        return self._decision(
            verdict="allow",
            reason="file_write_allowed",
            risk_code="file_write_allowed",
            governance_target="system_integrity",
            posture=posture,
            details={"path": str(normalized), "append": append, "runtime_context": runtime_context or {}},
        )

    def assess_process_action(self, pid: int, action: str, *, runtime_context: Optional[Dict[str, Any]] = None) -> SafetyDecision:
        config = self._config()
        posture = self._current_posture(config)
        try:
            process = psutil.Process(pid)
            description = " ".join(process.cmdline() or []) or process.name()
        except Exception:
            description = f"pid:{pid}"

        if self._matches_process_patterns(description, config["processRules"]["protectedPatterns"]):
            return self._decision(
                verdict="block",
                reason="目标进程疑似属于 v8chat 主程序或关键服务，禁止直接终止。",
                risk_code="protected_process_action",
                governance_target="v8_integrity",
                posture=posture,
                details={"pid": pid, "action": action, "process": description, "runtime_context": runtime_context or {}},
                allow_override=False,
            )

        if self._matches_process_patterns(description, config["processRules"]["reviewPatterns"]):
            return self._decision(
                verdict="review",
                reason="目标进程命中了复核规则，需要人工确认。",
                risk_code="review_process_action",
                governance_target="system_integrity",
                posture=posture,
                details={"pid": pid, "action": action, "process": description, "runtime_context": runtime_context or {}},
            )

        return self._decision(
            verdict="review",
            reason="终止本地进程属于高风险行为，需要人工确认。",
            risk_code="process_action_review",
            governance_target="system_integrity",
            posture=posture,
            details={"pid": pid, "action": action, "process": description, "runtime_context": runtime_context or {}},
        )

    def assess_external_tool_call(
        self,
        *,
        tool_name: str,
        params: Optional[Dict[str, Any]] = None,
        tool_kind: str | None = None,
        side_effect: str | None = None,
        runtime_context: Optional[Dict[str, Any]] = None,
    ) -> SafetyDecision:
        config = self._config()
        posture = self._current_posture(config)
        runtime_context = runtime_context or {}
        params = dict(params or {})
        if not config["enabled"] or not bool(config.get("externalToolLocalSystemProtection", {}).get("enabled", True)):
            return SafetyDecision()

        name = str(tool_name or "").strip()
        normalized_name = name.lower()
        normalized_kind = str(tool_kind or "").strip().lower()
        normalized_side_effect = str(side_effect or "").strip().lower()
        command_text = self._external_tool_command_text(params)
        path_values = self._external_tool_path_values(params)
        is_shell_tool = normalized_name in {"bash", "shell", "powershell", "cmd", "terminal"} or normalized_kind in {"shell", "command", "bash"}
        is_write_tool = (
            any(marker in normalized_name for marker in ["write", "edit", "multiedit", "delete", "move", "rename", "patch"])
            or normalized_kind in {"write", "edit", "delete", "move"}
            or any(marker in normalized_side_effect for marker in ["write", "delete", "mutat", "filesystem"])
        )
        is_read_tool = any(marker in normalized_name for marker in ["read", "grep", "glob", "ls", "list", "search"]) or normalized_kind in {"read", "search", "list"}

        if command_text:
            command_decision = self._assess_windows_profile_command(
                command_text,
                config=config,
                posture=posture,
                runtime_context=runtime_context,
                analysis_payload=None,
            )
            if command_decision is not None and not command_decision.is_allow():
                return self._external_tool_local_system_decision(
                    source_decision=command_decision,
                    tool_name=name,
                    params=params,
                    posture=posture,
                    runtime_context=runtime_context,
                )
            command_decision = self._assess_cross_platform_system_command(
                command_text,
                config=config,
                posture=posture,
                runtime_context=runtime_context,
                analysis_payload=None,
            )
            if command_decision is not None and not command_decision.is_allow():
                return self._external_tool_local_system_decision(
                    source_decision=command_decision,
                    tool_name=name,
                    params=params,
                    posture=posture,
                    runtime_context=runtime_context,
                )
            if is_shell_tool and "__pregel_scratchpad" in command_text.lower():
                return self._decision(
                    verdict="block",
                    reason="外部工具命令包含兼容桥接内部 scratchpad 标识，已 hard stop。",
                    risk_code="external_tool_local_system_hard_stop",
                    governance_target="system_integrity",
                    posture=posture,
                    details={"tool_name": name, "runtime_context": runtime_context, "sourceRiskCode": "compat_bridge_hard_stop"},
                    allow_override=False,
                )

        normalized_paths = [path for item in path_values if (path := self._normalize_path(item)) is not None]
        if normalized_paths:
            if is_write_tool or normalized_side_effect in {"write", "mutation", "mutating", "delete", "move"}:
                for path in normalized_paths:
                    file_decision = self._assess_windows_profile_file_write(
                        path,
                        append=False,
                        config=config,
                        posture=posture,
                        runtime_context=runtime_context,
                        content_preview=self._external_tool_content_preview(params),
                    )
                    if file_decision is not None and not file_decision.is_allow():
                        return self._external_tool_local_system_decision(
                            source_decision=file_decision,
                            tool_name=name,
                            params=params,
                            posture=posture,
                            runtime_context=runtime_context,
                        )
                    file_decision = self._assess_cross_platform_file_write(
                        path,
                        append=False,
                        config=config,
                        posture=posture,
                        runtime_context=runtime_context,
                        content_preview=self._external_tool_content_preview(params),
                    )
                    if file_decision is not None and not file_decision.is_allow():
                        return self._external_tool_local_system_decision(
                            source_decision=file_decision,
                            tool_name=name,
                            params=params,
                            posture=posture,
                            runtime_context=runtime_context,
                        )
                    if self._is_windows_profile_sensitive_path(path, include_roots=True):
                        return self._decision(
                            verdict=self._windows_profile_verdict(config, "highRiskMutationVerdict", "block"),
                            reason="外部工具会写入、编辑、移动或删除 Windows profile 关键路径，已阻断。",
                            risk_code="external_tool_local_system_hard_stop",
                            governance_target="system_integrity",
                            posture=posture,
                            details={
                                "tool_name": name,
                                "path": str(path),
                                "runtime_context": runtime_context,
                                "sourceRiskCode": "windows_profile_hive_mutation",
                            },
                            allow_override=False,
                        )
            if is_read_tool:
                for path in normalized_paths:
                    if self._is_windows_profile_sensitive_path(path, include_roots=True):
                        source_decision = self._windows_profile_decision(
                            verdict=self._windows_profile_verdict(config, "sensitiveReadVerdict", "review"),
                            reason="外部工具会读取 Windows profile 敏感对象，需要先经 V8 Safety 审批。",
                            risk_code="windows_profile_sensitive_read",
                            posture=posture,
                            path=path,
                            runtime_context=runtime_context,
                        )
                        return self._external_tool_local_system_decision(
                            source_decision=source_decision,
                            tool_name=name,
                            params=params,
                            posture=posture,
                            runtime_context=runtime_context,
                        )
                    if self._is_cross_platform_sensitive_path(path):
                        source_decision = self._cross_platform_decision(
                            verdict=self._cross_platform_verdict(config, "sensitiveReadVerdict", "review"),
                            reason="外部工具会读取 Linux/macOS 敏感系统、认证或 secret 对象，需要先经 V8 Safety 审批。",
                            risk_code=self._cross_platform_risk_code(path, mutation=False),
                            posture=posture,
                            path=path,
                            runtime_context=runtime_context,
                        )
                        return self._external_tool_local_system_decision(
                            source_decision=source_decision,
                            tool_name=name,
                            params=params,
                            posture=posture,
                            runtime_context=runtime_context,
                        )

        return self._decision(
            verdict="allow",
            reason="external_tool_local_system_allowed",
            risk_code="external_tool_local_system_allowed",
            governance_target="system_integrity",
            posture=posture,
            details={
                "tool_name": name,
                "tool_kind": normalized_kind,
                "side_effect": normalized_side_effect,
                "runtime_context": runtime_context,
            },
        )

    def assess_http_request(
        self,
        method: str,
        url: str,
        *,
        headers: Dict[str, Any] | None = None,
        body: str | None = None,
        runtime_context: Optional[Dict[str, Any]] = None,
    ) -> SafetyDecision:
        config = self._config()
        posture = self._current_posture(config)
        method_upper = (method or "GET").upper()
        parsed = urlparse(url or "")
        host = (parsed.hostname or "").lower()
        body_preview = (body or "")[:300]
        headers_preview = self._redact_sensitive_payload(dict(headers or {}))

        if not parsed.scheme or not host:
            return self._decision(
                verdict="review",
                reason="网络请求目标不完整，无法确认安全性，需要人工确认。",
                risk_code="ambiguous_http_target",
                governance_target="external_mutation",
                posture=posture,
                details={"method": method_upper, "url": url, "body_preview": body_preview, "headers_preview": headers_preview, "runtime_context": runtime_context or {}},
            )

        if host in config["networkRules"]["localHosts"]:
            return self._decision(
                verdict="allow",
                reason="loopback_http_allowed",
                risk_code="loopback_http_allowed",
                governance_target="external_mutation",
                posture=posture,
                details={"method": method_upper, "url": url, "body_preview": body_preview, "headers_preview": headers_preview, "runtime_context": runtime_context or {}},
            )

        if self._matches_host(host, config["networkRules"]["blockedHosts"]):
            return self._decision(
                verdict="block",
                reason="目标域名命中了 Safety Guardian 的阻断网络规则。",
                risk_code="blocked_host",
                governance_target="external_mutation",
                posture=posture,
                details={"method": method_upper, "url": url, "body_preview": body_preview, "headers_preview": headers_preview, "runtime_context": runtime_context or {}},
                allow_override=False,
            )

        if self._matches_host(host, config["networkRules"]["reviewHosts"]):
            return self._decision(
                verdict="review",
                reason="目标域名命中了 Safety Guardian 的复核网络规则。",
                risk_code="review_host",
                governance_target="external_mutation",
                posture=posture,
                details={"method": method_upper, "url": url, "body_preview": body_preview, "headers_preview": headers_preview, "runtime_context": runtime_context or {}},
            )

        trusted_network = self._match_trusted_network_target(url, config=config)
        credential_class = self._http_request_credential_class(url=url, headers=headers, body=body, runtime_context=runtime_context)
        base_details = {
            "method": method_upper,
            "url": url,
            "body_preview": body_preview,
            "headers_preview": headers_preview,
            "runtime_context": runtime_context or {},
            "trustedNetwork": trusted_network,
            "credentialClass": credential_class,
        }

        if credential_class and trusted_network.get("matched"):
            if method_upper in set(config["networkRules"]["reviewMethods"]) and bool(trusted_network.get("mutationRequiresReview")):
                return self._decision(
                    verdict="review",
                    reason="请求携带凭据且目标为可信金融/支付类官方 API；由于可能产生真实资金或账户副作用，需要人工确认。",
                    risk_code="trusted_financial_mutation_http",
                    governance_target="external_mutation",
                    posture=posture,
                    details=base_details,
                )
            if method_upper in set(config["networkRules"]["reviewMethods"]):
                verdict = self._posture_verdict(
                    config["networkMutationRules"]["defaultExternalMutationVerdict"],
                    posture=posture,
                    fallback="audit",
                )
                return self._decision(
                    verdict=verdict,
                    reason="请求携带凭据且目标匹配可信官方 API，按机器姿态放行/审计。",
                    risk_code="trusted_provider_api_http",
                    governance_target="external_mutation",
                    posture=posture,
                    details=base_details,
                )
            return self._decision(
                verdict="allow",
                reason="请求携带凭据且目标匹配可信官方 API。",
                risk_code="trusted_provider_api_http",
                governance_target="external_mutation",
                posture=posture,
                details=base_details,
            )

        if credential_class and not trusted_network.get("matched"):
            verdict = self._normalize_verdict(
                config["networkRules"].get("unknownCredentialHostVerdict"),
                fallback="review",
                allowed={"audit", "review", "block"},
            )
            return self._decision(
                verdict=verdict,
                reason="请求携带 API key/token/cookie 等凭据，但目标域名不在 Safety 可信官方 API 目录中，需要确认最终请求目标。",
                risk_code="unknown_credential_host_http",
                governance_target="private_data_exfiltration",
                posture=posture,
                details=base_details,
                allow_override=verdict != "block",
            )

        if self._http_request_looks_like_credential_exfiltration(url=url, headers=headers, body=body, runtime_context=runtime_context):
            return self._decision(
                verdict=config["networkMutationRules"]["credentialExfiltrationVerdict"],
                reason="检测到疑似浏览器数据、凭证或本地敏感材料外传请求，已提升到高风险治理。",
                risk_code="credential_exfiltration_http",
                governance_target="private_data_exfiltration",
                posture=posture,
                details=base_details,
                allow_override=False,
            )

        if method_upper in set(config["networkRules"]["reviewMethods"]):
            if self._http_request_looks_sensitive(url=url, headers=headers, body=body, runtime_context=runtime_context):
                verdict = config["networkMutationRules"]["sensitivePayloadVerdict"]
                risk_code = "sensitive_external_mutation_http"
                reason = "对外部域名发起的变更型网络请求携带了敏感 payload，需要人工确认。"
                governance_target = "private_data_exfiltration"
            else:
                verdict = self._posture_verdict(
                    config["networkMutationRules"]["defaultExternalMutationVerdict"],
                    posture=posture,
                    fallback="audit",
                )
                risk_code = "external_mutating_http"
                reason = "对外部域名发起了变更型网络请求，已按机器姿态记录治理结果。"
                governance_target = "external_mutation"
            return self._decision(
                verdict=verdict,
                reason=reason,
                risk_code=risk_code,
                governance_target=governance_target,
                posture=posture,
                details=base_details,
            )

        return self._decision(
            verdict="allow",
            reason="http_allowed",
            risk_code="http_allowed",
            governance_target="external_mutation",
            posture=posture,
            details=base_details,
        )

    def assess_cron_mutation(self, action: str, *, runtime_context: Optional[Dict[str, Any]] = None) -> SafetyDecision:
        posture = self._current_posture()
        if (action or "").lower() in {"add", "remove"}:
            return self._decision(
                verdict="review",
                reason="修改系统定时任务会影响长期自动化行为，需要人工确认。",
                risk_code="cron_mutation_review",
                governance_target="external_mutation",
                posture=posture,
                details={"action": action, "runtime_context": runtime_context or {}},
            )
        return self._decision(
            verdict="allow",
            reason="cron_query_allowed",
            risk_code="cron_query_allowed",
            governance_target="external_mutation",
            posture=posture,
            details={"action": action, "runtime_context": runtime_context or {}},
        )

    def assess_hook_mutation(self, action: str, *, runtime_context: Optional[Dict[str, Any]] = None) -> SafetyDecision:
        posture = self._current_posture()
        if (action or "").lower() == "add":
            return self._decision(
                verdict="review",
                reason="新增系统 Hook 会改变引擎生命周期行为，需要人工确认。",
                risk_code="hook_mutation_review",
                governance_target="v8_integrity",
                posture=posture,
                details={"action": action, "runtime_context": runtime_context or {}},
            )
        return self._decision(
            verdict="allow",
            reason="hook_query_allowed",
            risk_code="hook_query_allowed",
            governance_target="v8_integrity",
            posture=posture,
            details={"action": action, "runtime_context": runtime_context or {}},
        )

    def assess_automation_action(
        self,
        *,
        action_type: str,
        target: str,
        payload: Dict[str, Any] | None = None,
        trigger_source: str | None = None,
    ) -> SafetyDecision:
        config = self._config()
        posture = self._current_posture(config)
        runtime_context = {
            "runtime_kind": "automation",
            "trigger_source": trigger_source,
            "payload": payload or {},
        }
        action_type_lower = (action_type or "").lower()
        target_lower = (target or "").lower()

        if action_type_lower in set(config["automationRules"]["blockedActionTypes"]):
            return self._decision(
                verdict="block",
                reason=f"自动化动作类型 {action_type} 位于阻断清单中。",
                risk_code="blocked_automation_action_type",
                governance_target="external_mutation",
                posture=posture,
                details={"action_type": action_type, "target": target, "runtime_context": runtime_context},
                allow_override=False,
            )

        if self._matches_patterns(target_lower, config["automationRules"]["blockedTargetPatterns"]):
            return self._decision(
                verdict="block",
                reason="自动化目标命中了阻断规则。",
                risk_code="blocked_automation_target",
                governance_target="external_mutation",
                posture=posture,
                details={"action_type": action_type, "target": target, "runtime_context": runtime_context},
                allow_override=False,
            )

        if self._matches_patterns(target_lower, config["automationRules"]["reviewTargetPatterns"]):
            return self._decision(
                verdict="review",
                reason="自动化目标命中了复核规则。",
                risk_code="review_automation_target",
                governance_target="external_mutation",
                posture=posture,
                details={"action_type": action_type, "target": target, "runtime_context": runtime_context},
            )

        if action_type_lower in set(config["automationRules"]["reviewActionTypes"]):
            if action_type_lower == "command":
                return self.assess_system_command(target, runtime_context=runtime_context)
            return self._decision(
                verdict="review",
                reason=f"自动化动作类型 {action_type} 命中了复核策略。",
                risk_code="review_automation_action_type",
                governance_target="external_mutation",
                posture=posture,
                details={"action_type": action_type, "target": target, "runtime_context": runtime_context},
            )

        if action_type_lower == "command":
            return self.assess_system_command(target, runtime_context=runtime_context)

        return self._decision(
            verdict="allow",
            reason="automation_target_allowed",
            risk_code="automation_target_allowed",
            governance_target="external_mutation",
            posture=posture,
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
        posture = self._current_posture(config)
        if not config["enabled"]:
            return SafetyDecision()

        runtime_context = dict(runtime_context or {})
        action_type_lower = (action_type or "").strip().lower()
        target_values = self._flatten_text_values(target or {})

        blocked_keywords = ["付款", "支付", "转账", "删除账号", "恢复出厂", "格式化磁盘"]

        if any(keyword.lower() in target_values for keyword in blocked_keywords):
            return self._decision(
                verdict=config["computerUseRules"]["destructiveKeywordVerdict"],
                reason="computer use 目标疑似涉及高危系统/资金操作，已被阻止。",
                risk_code="computer_use_blocked_action",
                governance_target="system_integrity",
                posture=posture,
                details={"action_type": action_type, "target": target or {}, "runtime_context": runtime_context},
                allow_override=False,
            )

        if action_type_lower == "hotkey":
            sequence = str((target or {}).get("sequence") or target_values).lower()
            if any(token in sequence for token in ["%{f4}", "^+{esc}", "^{esc}", "#{l}", "#{r}"]):
                return self._decision(
                    verdict=config["computerUseRules"]["hotkeyLifecycleVerdict"],
                    reason="该热键可能影响系统或窗口生命周期，需要人工确认。",
                    risk_code="computer_use_hotkey_review",
                    governance_target="system_integrity",
                    posture=posture,
                    details={"action_type": action_type, "target": target or {}, "runtime_context": runtime_context},
                )

        if action_type_lower in {"click", "double_click", "type_text", "hotkey", "scroll", "hover"}:
            verdict = self._posture_verdict(
                config["computerUseRules"]["defaultMutationVerdict"],
                posture=posture,
                fallback="audit",
            )
            return self._decision(
                verdict=verdict,
                reason="computer use 动作已按当前机器姿态记录治理结果。",
                risk_code="computer_use_mutation",
                governance_target="external_mutation",
                posture=posture,
                details={"action_type": action_type, "target": target or {}, "runtime_context": runtime_context},
            )

        return self._decision(
            verdict="allow",
            reason="computer_use_action_allowed",
            risk_code="computer_use_action_allowed",
            governance_target="external_mutation",
            posture=posture,
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

    def build_skill_safety_identity(
        self,
        *,
        skill_id: str | None = None,
        skill_name: str | None = None,
        skill_root: str,
        instruction_path: str | None = None,
    ) -> Dict[str, Any]:
        root = self._normalize_path(skill_root)
        instruction = self._normalize_path(instruction_path)
        normalized_root = str(root or skill_root or "").strip()
        normalized_instruction = str(instruction or instruction_path or "").strip()
        identity_key = str(skill_id or normalized_root or skill_name or "").strip().lower()
        manifest_hash = self._hash_file(instruction) if instruction and instruction.exists() else None
        content_hash = self._hash_skill_content(root, instruction)
        return {
            "skillId": str(skill_id or "").strip(),
            "skillName": str(skill_name or "").strip(),
            "skillPath": normalized_root,
            "instructionPath": normalized_instruction,
            "identityKey": identity_key,
            "contentHash": content_hash,
            "manifestHash": manifest_hash,
        }

    def get_skill_safety_review(
        self,
        *,
        skill_id: str | None = None,
        skill_name: str | None = None,
        skill_root: str,
        instruction_path: str | None = None,
    ) -> Dict[str, Any] | None:
        identity = self.build_skill_safety_identity(
            skill_id=skill_id,
            skill_name=skill_name,
            skill_root=skill_root,
            instruction_path=instruction_path,
        )
        review = db.get_skill_safety_review(
            identity_key=identity["identityKey"],
            content_hash=identity["contentHash"],
        )
        if not review:
            return None
        review["identity"] = identity
        return review

    def record_skill_safety_review(
        self,
        *,
        skill_id: str | None = None,
        skill_name: str | None = None,
        skill_root: str,
        instruction_path: str | None = None,
        scan_payload: Dict[str, Any],
        llm_review: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        identity = self.build_skill_safety_identity(
            skill_id=skill_id,
            skill_name=skill_name,
            skill_root=skill_root,
            instruction_path=instruction_path,
        )
        static_verdict = str(scan_payload.get("staticVerdict") or scan_payload.get("verdict") or "review").strip().lower()
        effective_verdict = self._skill_effective_verdict(static_verdict=static_verdict, llm_review=llm_review, user_override=None)
        disabled = effective_verdict == "block"
        review = db.upsert_skill_safety_review(
            review_id=f"skillreview_{uuid4().hex[:16]}",
            skill_id=identity["skillId"],
            skill_name=identity["skillName"],
            skill_path=identity["skillPath"],
            instruction_path=identity["instructionPath"],
            identity_key=identity["identityKey"],
            content_hash=identity["contentHash"],
            manifest_hash=identity["manifestHash"],
            static_verdict=static_verdict,
            effective_verdict=effective_verdict,
            disabled=disabled,
            scan_payload=scan_payload,
            llm_review=llm_review,
            reasons=list(scan_payload.get("reasons") or []),
            flagged_files=list(scan_payload.get("flaggedFiles") or []),
            finding_categories=list(scan_payload.get("findingCategories") or []),
        )
        review["identity"] = identity
        return review

    def list_skill_safety_reviews(self, *, status: str | None = None, limit: int = 100) -> list[Dict[str, Any]]:
        return db.list_skill_safety_reviews(status=status, limit=limit)

    def mark_skill_safety_reviews_inactive(
        self,
        *,
        skill_root: str,
        instruction_path: str | None = None,
    ) -> int:
        return db.mark_skill_safety_reviews_inactive(
            skill_path=str(skill_root or ""),
            instruction_path=str(instruction_path or "") or None,
        )

    def approve_skill_safety_review(self, review_id: str) -> Dict[str, Any] | None:
        return db.update_skill_safety_review_override(
            review_id=review_id,
            user_override="approved",
            disabled=False,
            effective_verdict="audit",
        )

    def disable_skill_safety_review(self, review_id: str) -> Dict[str, Any] | None:
        return db.update_skill_safety_review_override(
            review_id=review_id,
            user_override="disabled",
            disabled=True,
            effective_verdict="block",
        )

    def revoke_skill_safety_review(self, review_id: str) -> Dict[str, Any] | None:
        current = db.get_skill_safety_review_by_id(review_id)
        if not current:
            return None
        effective = self._skill_effective_verdict(
            static_verdict=str(current.get("static_verdict") or "review"),
            llm_review=current.get("llmReview") if isinstance(current.get("llmReview"), dict) else None,
            user_override=None,
        )
        return db.update_skill_safety_review_override(
            review_id=review_id,
            user_override=None,
            disabled=effective == "block",
            effective_verdict=effective,
        )

    def rescan_skill_safety_review(self, review_id: str) -> Dict[str, Any] | None:
        current = db.get_skill_safety_review_by_id(review_id)
        if not current:
            return None
        skill_root = str(current.get("skill_path") or "")
        instruction_path = str(current.get("instruction_path") or "") or None
        skill_name = str(current.get("skill_name") or "")
        skill_id = str(current.get("skill_id") or "")
        scan_payload = self.assess_skill_directory(
            skill_name=skill_name or skill_id or skill_root,
            skill_root=skill_root,
            instruction_path=instruction_path,
        )
        static_verdict = str(scan_payload.get("verdict") or "").strip().lower()
        llm_review = None
        if static_verdict == "review" and bool(scan_payload.get("llmReviewRecommended")):
            llm_review = self.review_skill_scan_with_llm(
                skill_name=skill_name or skill_id or skill_root,
                skill_root=skill_root,
                scan_payload=scan_payload,
            )
        scan_payload = self.apply_skill_llm_review(scan_payload, llm_review)
        return self.record_skill_safety_review(
            skill_id=skill_id,
            skill_name=skill_name,
            skill_root=skill_root,
            instruction_path=instruction_path,
            scan_payload=scan_payload,
            llm_review=llm_review,
        )

    def apply_skill_llm_review(self, scan_payload: Dict[str, Any], review_payload: Dict[str, Any] | None) -> Dict[str, Any]:
        if not review_payload:
            scan_payload.setdefault("reviewMode", "rules_only")
            return scan_payload
        scan_payload["llmReview"] = review_payload
        scan_payload["reviewMode"] = "llm_assisted"
        static_verdict = str(scan_payload.get("staticVerdict") or scan_payload.get("verdict") or "").strip().lower()
        if review_payload.get("status") == "completed":
            review_summary = str(review_payload.get("summary") or "").strip()
            if review_payload.get("decision") == "allow":
                scan_payload["staticVerdict"] = static_verdict
                scan_payload["verdict"] = "audit"
                reasons = list(scan_payload.get("reasons") or [])
                reasons.append(f"安全复审模型认为该 skill 可放行：{review_summary or '证据不足以支持阻断。'}")
                scan_payload["reasons"] = reasons[:10]
            elif review_payload.get("decision") == "block":
                scan_payload["staticVerdict"] = static_verdict
                scan_payload["verdict"] = "block"
                reasons = list(scan_payload.get("reasons") or [])
                reasons.append(f"安全复审模型维持阻断：{review_summary or '疑点仍然足够高风险。'}")
                scan_payload["reasons"] = reasons[:10]
        else:
            scan_payload["reviewMode"] = "rules_only_fallback"
        return scan_payload

    def skill_review_to_scan_payload(self, review: Dict[str, Any]) -> Dict[str, Any]:
        scan_payload = dict(review.get("scanPayload") or {})
        static_verdict = str(review.get("static_verdict") or scan_payload.get("staticVerdict") or scan_payload.get("verdict") or "review").strip().lower()
        effective_verdict = str(review.get("effective_verdict") or static_verdict or "review").strip().lower()
        if bool(review.get("disabled")):
            effective_verdict = "block"
        scan_payload.setdefault("auditId", review.get("id") or "")
        scan_payload["staticVerdict"] = static_verdict
        scan_payload["verdict"] = effective_verdict
        scan_payload["effectiveVerdict"] = effective_verdict
        scan_payload["ledgerId"] = review.get("id")
        scan_payload["ledgerStatus"] = self._skill_review_status(review)
        scan_payload["fromLedger"] = True
        scan_payload["userOverride"] = review.get("user_override")
        scan_payload["disabled"] = bool(review.get("disabled"))
        scan_payload["contentHash"] = review.get("content_hash")
        if review.get("llmReview"):
            scan_payload["llmReview"] = review.get("llmReview")
        return scan_payload

    def assess_skill_directory(
        self,
        *,
        skill_name: str,
        skill_root: str,
        instruction_path: str | None = None,
    ) -> Dict[str, Any]:
        audit_id = f"skillscan_{uuid4().hex[:12]}"
        root = self._normalize_path(skill_root)
        config = self._config()
        posture = self._current_posture(config)
        excluded = {
            path
            for path in [self._normalize_path(instruction_path)]
            if path is not None
        }
        if root is None or not root.exists() or not root.is_dir():
            return {
                "auditId": audit_id,
                "verdict": "review",
                "governanceTarget": "skill_supply_chain",
                "posture": posture,
                "confidence": 0.74,
                "reasons": ["Skill 根目录不存在或不可访问，无法完成安全初筛。"],
                "flaggedFiles": [],
                "skillTrustScore": 35,
                "scannedFiles": 0,
                "candidateFiles": 0,
                "findingCategories": [],
                "llmReviewRecommended": False,
                "skillName": skill_name,
                "skillPath": skill_root,
            }

        candidates = self._collect_skill_scan_candidates(root, excluded_paths=excluded)
        flagged_files: list[dict[str, Any]] = []
        reason_hits: dict[str, int] = {}
        total_score = 0
        highest_severity_rank = 1
        finding_categories: set[str] = set()

        for candidate in candidates:
            assessment = self._assess_skill_candidate(root, candidate)
            if not assessment:
                continue
            flagged_files.append(assessment)
            total_score = min(100, total_score + int(assessment.get("score") or 0))
            highest_severity_rank = max(highest_severity_rank, self._severity_rank(str(assessment.get("severity") or "low")))
            for finding in list(assessment.get("findings") or []):
                finding_id = str(finding.get("id") or "").strip()
                if finding_id:
                    finding_categories.add(finding_id)
                label = str(finding.get("label") or "").strip()
                if label:
                    reason_hits[label] = reason_hits.get(label, 0) + 1

        reasons: list[str] = []
        for label, count in sorted(reason_hits.items(), key=lambda item: (-item[1], item[0])):
            suffix = f"（{count} 个文件）" if count > 1 else ""
            reasons.append(f"发现 {label}{suffix}。")
            if len(reasons) >= _SKILL_SCAN_REASON_LIMIT:
                break

        if not reasons:
            if candidates:
                reasons.append(f"已扫描 {len(candidates)} 个候选文件，未发现高风险静态特征。")
            else:
                reasons.append("未发现需要扫描的可执行或高风险候选文件。")

        verdict, governance_target, llm_review_recommended = self._skill_scan_governance(
            finding_categories=finding_categories,
            config=config,
            posture=posture,
        )
        confidence = self._skill_scan_confidence(total_score, len(flagged_files), highest_severity_rank)
        trust_score = max(0, 100 - total_score)

        return {
            "auditId": audit_id,
            "verdict": verdict,
            "governanceTarget": governance_target,
            "posture": posture,
            "confidence": confidence,
            "reasons": reasons,
            "flaggedFiles": flagged_files[:_SKILL_SCAN_MAX_FLAGGED_FILES],
            "skillTrustScore": trust_score,
            "scannedFiles": len(candidates),
            "candidateFiles": len(candidates),
            "findingCategories": sorted(finding_categories),
            "llmReviewRecommended": llm_review_recommended,
            "skillName": skill_name,
            "skillPath": str(root),
        }

    def review_skill_scan_with_llm(
        self,
        *,
        skill_name: str,
        skill_root: str,
        scan_payload: Dict[str, Any],
    ) -> Dict[str, Any] | None:
        config = self._config()
        static_verdict = str(scan_payload.get("verdict") or "").strip().lower()
        if static_verdict != "review":
            return None
        if not bool(scan_payload.get("llmReviewRecommended")):
            return None
        if static_verdict not in set(config.get("skillRules", {}).get("llmReviewEnabledFor") or []):
            return None

        try:
            from core.models.control_plane import model_control_plane
            from core.llm_factory import llm_factory
            from langchain_core.messages import HumanMessage, SystemMessage
        except Exception as exc:
            return {
                "status": "error",
                "decision": "unavailable",
                "reason": f"无法加载安全复审依赖：{exc}",
            }

        model_id = str(model_control_plane.get_role_model_id("safety_review") or "").strip()
        if not model_id:
            return None

        try:
            review_llm = llm_factory.create_for_role("safety_review", temperature=0.0, streaming=False)
        except Exception as exc:
            return {
                "status": "error",
                "decision": "unavailable",
                "modelId": model_id,
                "reason": f"安全复审模型初始化失败：{exc}",
            }

        flagged_files = list(scan_payload.get("flaggedFiles") or [])[:8]
        reasons = list(scan_payload.get("reasons") or [])[:6]
        prompt_payload = {
            "skillName": skill_name,
            "skillRoot": skill_root,
            "staticVerdict": static_verdict,
            "governanceTarget": scan_payload.get("governanceTarget"),
            "confidence": scan_payload.get("confidence"),
            "skillTrustScore": scan_payload.get("skillTrustScore"),
            "reasons": reasons,
            "flaggedFiles": flagged_files,
            "findingCategories": list(scan_payload.get("findingCategories") or []),
        }
        system_prompt = (
            "你是 V8 Agent OS 的 Safety Review 模型。"
            "你只根据提供的 skill 静态扫描结果做二次复审，不要臆造额外文件内容。"
            "如果证据显示该 skill 很可能包含恶意执行、敏感信息外传、持久化植入或破坏性系统操作，请输出 block。"
            "如果证据不足以认定为恶意、且更像合法自动化/开发辅助逻辑，则输出 allow。"
            "只返回 JSON，不要带 Markdown 代码块。"
            'JSON schema: {"decision":"allow|block","confidence":0.0,"summary":"...","notes":["..."]}'
        )

        try:
            response = review_llm.invoke(
                [
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=json.dumps(prompt_payload, ensure_ascii=False, indent=2)),
                ],
                config={"callbacks": []},
            )
            raw_text = self._extract_llm_text(response)
            parsed = self._parse_skill_review_json(raw_text)
            decision = str(parsed.get("decision") or "").strip().lower()
            if decision not in {"allow", "block"}:
                raise ValueError("decision must be allow or block")
            confidence = parsed.get("confidence")
            try:
                normalized_confidence = round(min(0.99, max(0.0, float(confidence))), 2)
            except (TypeError, ValueError):
                normalized_confidence = 0.5
            notes = parsed.get("notes")
            return {
                "status": "completed",
                "decision": decision,
                "confidence": normalized_confidence,
                "summary": str(parsed.get("summary") or "").strip() or "安全复审未提供摘要。",
                "notes": [str(item).strip() for item in list(notes or []) if str(item).strip()][:4],
                "modelId": model_id,
            }
        except Exception as exc:
            return {
                "status": "error",
                "decision": "unavailable",
                "modelId": model_id,
                "reason": f"安全复审模型返回不可解析结果：{exc}",
            }

    def _extract_llm_text(self, response: Any) -> str:
        content = getattr(response, "content", response)
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                    continue
                if isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str) and text.strip():
                        parts.append(text.strip())
            return "\n".join(parts).strip()
        return str(content or "").strip()

    def _parse_skill_review_json(self, raw_text: str) -> Dict[str, Any]:
        text = str(raw_text or "").strip()
        if not text:
            raise ValueError("empty review response")
        fenced = re.search(r"\{.*\}", text, re.DOTALL)
        candidate = fenced.group(0) if fenced else text
        parsed = json.loads(candidate)
        if not isinstance(parsed, dict):
            raise ValueError("review response must be a JSON object")
        return parsed

    def _hash_file(self, path: Path | None) -> str | None:
        if path is None:
            return None
        try:
            digest = hashlib.sha256()
            with path.open("rb") as file:
                while chunk := file.read(1024 * 1024):
                    digest.update(chunk)
            return digest.hexdigest()
        except OSError:
            return None

    def _hash_skill_content(self, root: Path | None, instruction_path: Path | None) -> str:
        digest = hashlib.sha256()
        if root is None or not root.exists() or not root.is_dir():
            digest.update(f"missing:{root}".encode("utf-8", errors="ignore"))
            return digest.hexdigest()
        included: list[Path] = []
        if instruction_path is not None and instruction_path.exists():
            included.append(instruction_path)
        excluded_paths = {instruction_path} if instruction_path is not None else set()
        included.extend(self._collect_skill_scan_candidates(root, excluded_paths={item for item in excluded_paths if item is not None}))
        for subdir_name in ("references", "scripts", "assets", "templates", "examples"):
            subdir = root / subdir_name
            if not subdir.exists() or not subdir.is_dir():
                continue
            included.extend(path for path in subdir.rglob("*") if path.is_file())
        seen: set[str] = set()
        for path in sorted(included, key=lambda item: str(item).lower()):
            normalized = self._normalize_path(str(path))
            if normalized is None:
                continue
            key = str(normalized).lower()
            if key in seen:
                continue
            seen.add(key)
            try:
                relative = normalized.relative_to(root).as_posix()
            except ValueError:
                relative = normalized.name
            digest.update(relative.encode("utf-8", errors="ignore"))
            digest.update(b"\0")
            digest.update(str(self._hash_file(normalized) or "unreadable").encode("ascii", errors="ignore"))
            digest.update(b"\0")
        return digest.hexdigest()

    def _skill_effective_verdict(
        self,
        *,
        static_verdict: str,
        llm_review: Dict[str, Any] | None,
        user_override: str | None,
    ) -> str:
        override = str(user_override or "").strip().lower()
        if override == "approved":
            return "audit"
        if override == "disabled":
            return "block"
        if isinstance(llm_review, dict) and llm_review.get("status") == "completed":
            decision = str(llm_review.get("decision") or "").strip().lower()
            if decision == "allow":
                return "audit"
            if decision == "block":
                return "block"
        normalized = str(static_verdict or "review").strip().lower()
        return normalized if normalized in {"allow", "audit", "review", "block"} else "review"

    def _skill_review_status(self, review: Dict[str, Any]) -> str:
        if bool(review.get("disabled")):
            return "disabled"
        override = str(review.get("user_override") or "").strip().lower()
        if override == "approved":
            return "approved"
        verdict = str(review.get("effective_verdict") or "").strip().lower()
        if verdict == "block":
            return "blocked"
        if verdict == "review":
            return "review"
        if verdict in {"allow", "audit"}:
            return verdict
        return "unknown"

    def _is_package_install_command(self, command: str) -> bool:
        normalized = (command or "").strip().lower()
        if not normalized:
            return False
        install_patterns = (
            r"\bpip(?:3)?\s+install\b",
            r"\bpython\s+-m\s+pip\s+install\b",
            r"\bnpm\s+install\b",
            r"\bpnpm\s+add\b",
            r"\byarn\s+add\b",
            r"\buv\s+pip\s+install\b",
            r"\bapt(?:-get)?\s+install\b",
            r"\bbrew\s+install\b",
            r"\bchoco\s+install\b",
            r"\bwinget\s+install\b",
        )
        return any(re.search(pattern, normalized) for pattern in install_patterns)

    def _http_request_looks_sensitive(
        self,
        *,
        url: str,
        headers: Dict[str, Any] | None = None,
        body: str | None,
        runtime_context: Optional[Dict[str, Any]] = None,
    ) -> bool:
        combined = self._flatten_text_values(
            {
                "url": url,
                "headers": headers or {},
                "body": body or "",
                "runtime_context": runtime_context or {},
            }
        )
        sensitive_tokens = (
            "authorization",
            "bearer ",
            "api_key",
            "token",
            "secret",
            "password",
            "cookie",
            "session",
            "credential",
            ".ssh",
            ".aws",
            ".kube",
            "id_rsa",
            "login data",
            "cookies.sqlite",
            "user data",
        )
        return any(token in combined for token in sensitive_tokens)

    def _http_request_looks_like_credential_exfiltration(
        self,
        *,
        url: str,
        headers: Dict[str, Any] | None = None,
        body: str | None,
        runtime_context: Optional[Dict[str, Any]] = None,
    ) -> bool:
        combined = self._flatten_text_values(
            {
                "url": url,
                "headers": headers or {},
                "body": body or "",
                "runtime_context": runtime_context or {},
            }
        )
        local_sensitive_tokens = (
            "cookies.sqlite",
            "login data",
            "user data",
            ".ssh",
            ".aws",
            ".kube",
            "id_rsa",
            "authorization",
            "bearer ",
            "api_key",
            "secret_key",
            "token",
            "cookie",
            "session",
        )
        exfil_tokens = (
            "multipart/form-data",
            "webhook",
            "upload",
            "exfil",
            "sendfile",
            "attachment",
            "file=",
            "content-disposition",
        )
        return any(token in combined for token in local_sensitive_tokens) and any(token in combined for token in exfil_tokens)

    def _http_request_credential_class(
        self,
        *,
        url: str,
        headers: Dict[str, Any] | None = None,
        body: str | None = None,
        runtime_context: Optional[Dict[str, Any]] = None,
    ) -> str:
        headers = dict(headers or {})
        header_text = self._flatten_text_values(headers)
        query_keys = {key.lower() for key, value in parse_qsl(urlparse(url or "").query, keep_blank_values=True) if str(value or "").strip()}
        combined = self._flatten_text_values(
            {
                "url": url,
                "headers": headers,
                "body": body or "",
                "runtime_context": runtime_context or {},
            }
        )
        if "authorization" in header_text and "bearer" in header_text:
            return "bearer_api_key"
        if "x-api-key" in header_text or "api-key" in header_text:
            return "api_key"
        if "cookie" in header_text:
            return "cookie"
        if query_keys.intersection({"api_key", "apikey", "appid", "key", "access_token", "token"}):
            return "api_key"
        if re.search(r"(?i)(api[_-]?key|access[_-]?token|refresh[_-]?token|secret[_-]?key)\s*[:=]", combined):
            return "api_key"
        if re.search(r"(?i)\bbearer\s+[a-z0-9._\-+/=]{12,}", combined):
            return "bearer_api_key"
        if re.search(r"(?i)\bsk-[a-z0-9][a-z0-9_\-]{12,}", combined):
            return "api_key"
        if re.search(r"(?i)\b(?:xox[baprs]-|gh[pousr]_|ya29\.|eyj[a-z0-9_-]{12,})", combined):
            return "token"
        return ""

    def _read_json_file(self, path: Path) -> Dict[str, Any]:
        try:
            raw = path.read_text(encoding="utf-8")
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    def _trusted_network_catalog_entries(self, config: Dict[str, Any]) -> list[Dict[str, Any]]:
        entries: list[Dict[str, Any]] = []
        seen: set[str] = set()

        def _add_entry(entry: Dict[str, Any], *, source: str = "") -> None:
            hosts = [str(item).strip().lower() for item in entry.get("officialHosts", []) if str(item).strip()]
            base_urls = [str(item).strip() for item in entry.get("allowedBaseUrls", []) if str(item).strip()]
            if not hosts and base_urls:
                for base_url in base_urls:
                    parsed = urlparse(base_url)
                    if parsed.hostname:
                        hosts.append(parsed.hostname.lower())
            if not hosts:
                return
            normalized = dict(entry)
            normalized["officialHosts"] = sorted(set(hosts))
            normalized["allowedBaseUrls"] = base_urls
            normalized.setdefault("category", "provider")
            normalized.setdefault("id", hosts[0])
            normalized.setdefault("displayName", normalized["id"])
            normalized.setdefault("confidence", "configured")
            if source:
                normalized.setdefault("source", source)
            key = json.dumps(
                {
                    "id": normalized.get("id"),
                    "hosts": normalized["officialHosts"],
                    "baseUrls": normalized["allowedBaseUrls"],
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            if key in seen:
                return
            seen.add(key)
            entries.append(normalized)

        for entry in list(self._read_json_file(_TRUSTED_NETWORK_CATALOG_PATH).get("entries") or []):
            if isinstance(entry, dict):
                _add_entry(entry, source="builtin")

        for provider in list(self._read_json_file(_MODEL_PROVIDER_CATALOG_PATH).get("providers") or []):
            if not isinstance(provider, dict):
                continue
            base_url = str(provider.get("baseUrl") or provider.get("base_url") or "").strip()
            if not base_url:
                continue
            _add_entry(
                {
                    "id": provider.get("id") or provider.get("name") or base_url,
                    "category": "ai",
                    "displayName": provider.get("name") or provider.get("id") or base_url,
                    "allowedBaseUrls": [base_url],
                    "credentialTypes": ["api_key", "bearer_api_key"],
                    "allowedMethods": ["GET", "POST"],
                    "sourceUrl": provider.get("sourceUrl"),
                    "confidence": provider.get("confidence") or "provider_catalog",
                },
                source="model_provider_catalog",
            )

        media_matrix = self._read_json_file(_MEDIA_PROVIDER_MATRIX_PATH)
        for modality_items in media_matrix.values():
            if not isinstance(modality_items, dict):
                continue
            for provider_id, provider in modality_items.items():
                if not isinstance(provider, dict):
                    continue
                base_url = str(provider.get("baseUrlDefault") or provider.get("baseUrl") or "").strip()
                if not base_url or base_url in {"internal", "local"}:
                    continue
                if not base_url.startswith(("http://", "https://")):
                    continue
                _add_entry(
                    {
                        "id": str(provider_id or base_url),
                        "category": "ai",
                        "displayName": provider.get("displayName") or provider.get("name") or provider_id,
                        "allowedBaseUrls": [base_url],
                        "credentialTypes": ["api_key", "bearer_api_key"],
                        "allowedMethods": ["GET", "POST"],
                        "confidence": provider.get("confidence") or "media_provider_matrix",
                    },
                    source="media_provider_matrix",
                )

        try:
            models_config = storage.get_models_config() or {}
        except Exception:
            models_config = {}
        providers = models_config.get("providers") if isinstance(models_config.get("providers"), dict) else {}
        for provider_id, provider in providers.items():
            if not isinstance(provider, dict):
                continue
            base_url = str(provider.get("baseUrl") or provider.get("base_url") or "").strip()
            if not base_url:
                continue
            _add_entry(
                {
                    "id": f"user_configured:{provider_id}",
                    "category": "user_configured",
                    "displayName": provider.get("name") or provider_id,
                    "allowedBaseUrls": [base_url],
                    "credentialTypes": ["api_key", "bearer_api_key", "oauth"],
                    "allowedMethods": ["GET", "POST", "PUT", "PATCH", "DELETE"],
                    "confidence": "user_configured",
                },
                source="models_config",
            )

        for host in list((config.get("networkRules") or {}).get("trustedHosts") or []):
            normalized_host = str(host or "").strip().lower()
            if normalized_host:
                _add_entry(
                    {
                        "id": f"trusted_host:{normalized_host}",
                        "category": "user_configured",
                        "displayName": normalized_host,
                        "officialHosts": [normalized_host],
                        "allowedBaseUrls": [],
                        "credentialTypes": ["api_key", "bearer_api_key", "oauth"],
                        "allowedMethods": ["GET", "POST", "PUT", "PATCH", "DELETE"],
                        "confidence": "user_configured",
                    },
                    source="safety_config",
                )

        return entries

    def _trusted_base_url_matches(self, request_url: str, base_url: str) -> bool:
        if not base_url:
            return False
        if "*" in base_url:
            pattern = "^" + re.escape(base_url).replace(r"\*", r"[^/]+") + r"(?:/.*)?$"
            return re.match(pattern, request_url, re.IGNORECASE) is not None
        return request_url.lower().startswith(base_url.rstrip("/").lower())

    def _match_trusted_network_target(self, url: str, *, config: Dict[str, Any]) -> Dict[str, Any]:
        parsed = urlparse(url or "")
        host = (parsed.hostname or "").lower()
        if not host:
            return {"matched": False, "reason": "missing_host"}
        normalized_url = str(url or "").strip()
        for entry in self._trusted_network_catalog_entries(config):
            hosts = [str(item).strip().lower() for item in entry.get("officialHosts", []) if str(item).strip()]
            allow_subdomains = bool(entry.get("allowSubdomains"))
            host_matches = any(host == item or (allow_subdomains and host.endswith(f".{item}")) for item in hosts)
            base_urls = [str(item).strip() for item in entry.get("allowedBaseUrls", []) if str(item).strip()]
            base_matches = any(self._trusted_base_url_matches(normalized_url, item) for item in base_urls)
            if host_matches or base_matches:
                return {
                    "matched": True,
                    "providerId": entry.get("id"),
                    "displayName": entry.get("displayName"),
                    "category": entry.get("category"),
                    "confidence": entry.get("confidence"),
                    "sourceUrl": entry.get("sourceUrl"),
                    "mutationRequiresReview": bool(entry.get("mutationRequiresReview")) or str(entry.get("category") or "").lower() in {"finance", "payment"},
                    "matchedHost": host,
                }
        return {"matched": False, "matchedHost": host, "reason": "not_in_trusted_catalog"}

    def _skill_scan_governance(
        self,
        *,
        finding_categories: set[str],
        config: Dict[str, Any],
        posture: str,
    ) -> tuple[str, str, bool]:
        if not finding_categories:
            return "allow", "skill_supply_chain", False

        skill_rules = dict(config.get("skillRules") or {})
        hard_block_categories = {"download_then_execute", "credential_exfiltration", "persistence", "destructive_fs"}
        if finding_categories & hard_block_categories:
            governance_target = "private_data_exfiltration" if "credential_exfiltration" in finding_categories else "skill_supply_chain"
            if "destructive_fs" in finding_categories:
                governance_target = "system_integrity"
            return "block", governance_target, False

        if "browser_profile_access" in finding_categories:
            verdict = self._posture_verdict(
                skill_rules.get("browserProfileAccessVerdict") or {},
                posture=posture,
                fallback="review",
            )
            return verdict, "private_data_exfiltration", False

        if "local_secret_read" in finding_categories:
            verdict = self._normalize_verdict(
                skill_rules.get("localSecretReadVerdict"),
                fallback="review",
                allowed={"audit", "review", "block"},
            )
            return verdict, "private_data_exfiltration", False

        ambiguous_categories = {"binary_executable", "encoded_payload", "hidden_shell_exec"}
        if finding_categories & ambiguous_categories:
            verdict = self._normalize_verdict(
                skill_rules.get("binaryPayloadVerdict"),
                fallback="review",
                allowed={"audit", "review", "block"},
            )
            return verdict, "skill_supply_chain", verdict == "review"

        if finding_categories == {"secret_declaration"}:
            verdict = self._normalize_verdict(
                skill_rules.get("declarationVerdict"),
                fallback="audit",
                allowed={"allow", "audit", "review"},
            )
            return verdict, "skill_supply_chain", False

        if "secret_declaration" in finding_categories and finding_categories.issubset({"secret_declaration"} | ambiguous_categories):
            verdict = self._normalize_verdict(
                skill_rules.get("binaryPayloadVerdict"),
                fallback="review",
                allowed={"audit", "review", "block"},
            )
            return verdict, "skill_supply_chain", verdict == "review"

        return "review", "skill_supply_chain", False

    def _collect_skill_scan_candidates(self, root: Path, *, excluded_paths: set[Path]) -> list[Path]:
        candidates: list[Path] = []
        seen: set[str] = set()

        for current_root, dir_names, file_names in os.walk(root, topdown=True):
            dir_names[:] = [name for name in dir_names if name not in _SKILL_SCAN_IGNORED_DIRS]
            current_root_path = Path(current_root)
            for file_name in file_names:
                if len(candidates) >= _SKILL_SCAN_MAX_FILES:
                    return candidates
                normalized = self._normalize_path(str(current_root_path / file_name))
                if normalized is None or normalized in excluded_paths:
                    continue
                if not self._should_scan_skill_path(root, normalized):
                    continue
                key = str(normalized)
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(normalized)

        return candidates

    def _should_scan_skill_path(self, root: Path, path: Path) -> bool:
        suffix = path.suffix.lower()
        if suffix in _SKILL_SCAN_TEXT_SUFFIXES or suffix in _SKILL_SCAN_BINARY_SUFFIXES:
            return True
        try:
            relative = path.relative_to(root).as_posix().lower()
        except ValueError:
            return False
        if relative.startswith("scripts/"):
            return True
        try:
            return os.access(path, os.X_OK)
        except Exception:
            return False

    def _assess_skill_candidate(self, root: Path, path: Path) -> Dict[str, Any] | None:
        try:
            raw = path.read_bytes()[:_SKILL_SCAN_MAX_BYTES]
        except Exception:
            return None
        is_binary = b"\x00" in raw
        try:
            relative_path = path.relative_to(root).as_posix()
        except ValueError:
            relative_path = path.name
        lowered_path = relative_path.lower()
        text = raw.decode("utf-8", errors="ignore").lower()
        composite = f"{lowered_path}\n{text}"

        findings: list[dict[str, Any]] = []
        for rule in _SKILL_SCAN_RULES:
            if self._matches_skill_scan_rule(composite, rule):
                findings.append(
                    {
                        "id": str(rule["id"]),
                        "label": str(rule["label"]),
                        "severity": str(rule["severity"]),
                        "score": int(rule["score"]),
                        "reason": str(rule["reason"]),
                    }
                )

        if is_binary and path.suffix.lower() in _SKILL_SCAN_BINARY_SUFFIXES:
            findings.append(
                {
                    "id": "binary_executable",
                    "label": "可执行二进制载荷",
                    "severity": "medium",
                    "score": 18,
                    "reason": "发现可执行二进制文件，需人工确认其来源与用途。",
                }
            )

        if not findings:
            return None

        score = min(100, sum(int(item["score"]) for item in findings))
        severity = max((str(item["severity"]) for item in findings), key=self._severity_rank)
        return {
            "path": relative_path,
            "severity": severity,
            "score": score,
            "isBinary": is_binary,
            "findings": findings,
        }

    def _matches_skill_scan_rule(self, text: str, rule: Dict[str, Any]) -> bool:
        any_tokens = tuple(str(item).lower() for item in rule.get("any_tokens", ()) if str(item).strip())
        if any_tokens and not any(token in text for token in any_tokens):
            return False
        for group in tuple(rule.get("all_groups", ()) or ()):
            normalized = tuple(str(item).lower() for item in group if str(item).strip())
            if normalized and not any(token in text for token in normalized):
                return False
        return bool(any_tokens or rule.get("all_groups"))

    def _severity_rank(self, severity: str) -> int:
        return {
            "low": 1,
            "medium": 2,
            "high": 3,
            "critical": 4,
        }.get(str(severity or "").strip().lower(), 1)

    def _skill_scan_verdict(self, total_score: int, highest_severity_rank: int) -> str:
        if total_score >= 80 or highest_severity_rank >= 4:
            return "critical"
        if total_score >= 45 or highest_severity_rank >= 3:
            return "high"
        if total_score >= 20 or highest_severity_rank >= 2:
            return "medium"
        return "low"

    def _skill_scan_confidence(self, total_score: int, flagged_count: int, highest_severity_rank: int) -> float:
        confidence = 0.42
        confidence += min(0.28, flagged_count * 0.05)
        confidence += min(0.2, total_score / 250.0)
        if highest_severity_rank >= 3:
            confidence += 0.08
        return round(min(0.98, max(0.35, confidence)), 2)

    def _expand_path_text(self, path: str | None) -> str:
        text = str(path or "").strip().strip("\"'`")
        if not text:
            return ""
        text = re.sub(
            r"(?i)\$env:([a-z_][a-z0-9_]*)",
            lambda match: os.environ.get(match.group(1), match.group(0)),
            text,
        )
        text = re.sub(
            r"(?i)\$\{env:([a-z_][a-z0-9_]*)\}",
            lambda match: os.environ.get(match.group(1), match.group(0)),
            text,
        )
        text = os.path.expandvars(text)
        return text

    def _normalize_path(self, path: str | None) -> Optional[Path]:
        if not path:
            return None
        try:
            return Path(self._expand_path_text(path)).expanduser().resolve(strict=False)
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

    def _skill_root_paths(self, runtime_context: Optional[Dict[str, Any]] = None) -> list[Path]:
        roots: list[Path] = [
            Path.home() / ".agents" / "skills",
            WORKSPACE_HOME / ".agents" / "skills",
        ]
        context = runtime_context or {}
        workspace_path = str(context.get("workspace_path") or context.get("workspacePath") or "").strip()
        if workspace_path:
            roots.append(Path(workspace_path).expanduser() / ".agents" / "skills")
        normalized: list[Path] = []
        seen: set[str] = set()
        for root in roots:
            candidate = self._normalize_path(str(root))
            if candidate is None:
                continue
            key = str(candidate).lower()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(candidate)
        return normalized

    def _is_under_skill_root(self, path: Path, runtime_context: Optional[Dict[str, Any]] = None) -> bool:
        normalized = self._normalize_path(str(path))
        if normalized is None:
            return False
        for root in self._skill_root_paths(runtime_context):
            try:
                normalized.relative_to(root)
                return True
            except ValueError:
                continue
        return False

    def _command_touches_skill_root(self, command: str, runtime_context: Optional[Dict[str, Any]] = None) -> bool:
        normalized_command = str(command or "").lower().replace("\\", "/")
        if ".agents/skills" in normalized_command:
            return True
        for root in self._skill_root_paths(runtime_context):
            root_text = str(root).lower().replace("\\", "/")
            if root_text and root_text in normalized_command:
                return True
        return False

    def _skill_root_command_mutation_level(self, command: str) -> str:
        lowered = f" {str(command or '').lower()} "
        destructive_markers = (
            " remove-item ",
            " rm -rf ",
            " rmdir ",
            " rd ",
            " del ",
            " erase ",
            " clear-content ",
            " shutil.rmtree",
        )
        mutation_markers = (
            " set-content ",
            " out-file ",
            " new-item ",
            " copy-item ",
            " move-item ",
            " write-output ",
            " add-content ",
            " >",
            ">>",
        )
        if any(marker in lowered for marker in destructive_markers):
            return "destructive"
        if any(marker in lowered for marker in mutation_markers):
            return "mutation"
        return ""

    def _is_skills_overwrite_install_command(self, command: str) -> bool:
        lowered = f" {str(command or '').lower()} "
        return " npx " in lowered and " skills " in lowered and " add " in lowered and " --overwrite" in lowered

    def _is_sensitive_system_path(self, path: Path) -> bool:
        normalized = self._normalize_path(str(path))
        if normalized is None:
            return False
        if self._windows_profile_protection_enabled(self._config()) and self._is_windows_profile_sensitive_path(normalized, include_roots=True):
            return True
        if self._cross_platform_protection_enabled(self._config()) and self._is_cross_platform_sensitive_path(normalized):
            return True
        sensitive_roots = [
            Path.home() / ".ssh",
            Path.home() / ".aws",
            Path.home() / ".kube",
            Path(os.environ.get("WINDIR", "C:/Windows")),
            Path(os.environ.get("ProgramFiles", "C:/Program Files")),
            Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")),
            Path("/etc"),
        ]
        for root in sensitive_roots:
            try:
                normalized.relative_to(root.expanduser().resolve(strict=False))
                return True
            except Exception:
                continue
        return False

    def _cross_platform_config(self, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return dict((config or self._config()).get("crossPlatformSystemProtection") or {})

    def _cross_platform_protection_enabled(self, config: Optional[Dict[str, Any]] = None) -> bool:
        return bool(self._cross_platform_config(config).get("enabled", True))

    def _cross_platform_verdict(self, config: Dict[str, Any], key: str, fallback: str) -> str:
        return self._normalize_verdict(
            self._cross_platform_config(config).get(key),
            fallback=fallback,
            allowed={"allow", "audit", "review", "block"},
        )

    def _path_text_for_policy(self, path: Path | str) -> str:
        text = str(path or "").strip().lower().replace("\\", "/")
        text = re.sub(r"/+", "/", text)
        return text

    def _is_cross_platform_hard_auth_path(self, path: Path) -> bool:
        text = self._path_text_for_policy(path)
        hard_needles = (
            "/etc/passwd",
            "/etc/shadow",
            "/etc/group",
            "/etc/gshadow",
            "/etc/sudoers",
            "/etc/sudoers.d/",
            "/etc/pam.d/",
            "/etc/ssh/sshd_config",
            "/root/",
            "/var/db/dslocal/nodes/default/users",
        )
        return any(needle in text for needle in hard_needles)

    def _is_cross_platform_persistence_path(self, path: Path) -> bool:
        text = self._path_text_for_policy(path)
        persistence_needles = (
            "/etc/systemd/system/",
            "/lib/systemd/system/",
            "/usr/lib/systemd/system/",
            "/library/launchdaemons/",
            "/library/launchagents/",
            "/users/",
            "/.config/autostart/",
            "/etc/cron",
            "/var/spool/cron",
        )
        if any(needle in text for needle in persistence_needles if needle != "/users/"):
            return True
        return "/users/" in text and "/library/launchagents/" in text

    def _is_cross_platform_secret_path(self, path: Path) -> bool:
        text = self._path_text_for_policy(path)
        secret_needles = (
            "/.ssh/",
            "/.aws/credentials",
            "/.kube/config",
            "/library/keychains/",
            "/.gnupg/",
            "/.docker/config.json",
            "/.config/gcloud/",
            "/login.keychain",
            "/keychains/",
        )
        return any(needle in text for needle in secret_needles)

    def _is_cross_platform_sensitive_path(self, path: Path) -> bool:
        return (
            self._is_cross_platform_hard_auth_path(path)
            or self._is_cross_platform_persistence_path(path)
            or self._is_cross_platform_secret_path(path)
        )

    def _cross_platform_risk_code(self, path: Path, *, mutation: bool) -> str:
        text = self._path_text_for_policy(path)
        if "/var/db/dslocal/" in text or "/library/keychains/" in text or "/keychains/" in text:
            return "macos_account_store_mutation" if mutation else "macos_keychain_sensitive_read"
        if self._is_cross_platform_hard_auth_path(path):
            return "linux_auth_store_mutation" if mutation else "linux_sensitive_read"
        if self._is_cross_platform_persistence_path(path):
            return "cross_platform_persistence_mutation" if mutation else "cross_platform_sensitive_read"
        return "cross_platform_sensitive_system_mutation" if mutation else "cross_platform_sensitive_read"

    def _cross_platform_decision(
        self,
        *,
        verdict: str,
        reason: str,
        risk_code: str,
        posture: str,
        command: str | None = None,
        path: Path | None = None,
        runtime_context: Optional[Dict[str, Any]] = None,
        analysis_payload: Optional[Dict[str, Any]] = None,
        extra_details: Optional[Dict[str, Any]] = None,
    ) -> SafetyDecision:
        details: Dict[str, Any] = {"runtime_context": runtime_context or {}}
        if command is not None:
            details["command"] = command
        if path is not None:
            details["path"] = str(path)
        if analysis_payload is not None:
            details["analysis"] = analysis_payload
        if extra_details:
            details.update(extra_details)
        return self._decision(
            verdict=verdict,
            reason=reason,
            risk_code=risk_code,
            governance_target="system_integrity",
            posture=posture,
            details=details,
            allow_override=verdict != "block",
        )

    def _posix_command_has_mutating_verb(self, command: str) -> bool:
        lower = f" {str(command or '').lower()} "
        if re.search(r"(?<![a-z0-9_-])(rm|mv|cp|rsync|chmod|chown|chgrp|chattr|setfacl|ln|mount|umount)(?![a-z0-9_-])", lower):
            return True
        return any(token in lower for token in [" > ", ">>", " tee ", " install ", " ditto "])

    def _posix_command_is_sensitive_read(self, command: str) -> bool:
        lower = str(command or "").lower()
        if any(token in lower for token in ["&&", "||", ";", ">", ">>", "| tee", " rm ", " mv ", " cp ", "rsync", "chmod", "chown", "chattr", "setfacl"]):
            return False
        return re.search(r"(?<![a-z0-9_-])(cat|less|more|head|tail|grep|rg|find|ls|stat|file|get-content|dir)(?![a-z0-9_-])", lower) is not None

    def _firewall_command_is_mutating(self, command: str) -> bool:
        lower = str(command or "").strip().lower()
        if not re.search(r"(?<![a-z0-9_-])(ufw|iptables|ip6tables|nft|firewall-cmd|pfctl|socketfilterfw|networksetup|netsh)(?![a-z0-9_-])", lower):
            return False
        read_only_tokens = (" status", " list", " show", " -l", " --list", " print", " get")
        if any(token in lower for token in read_only_tokens) and not any(token in lower for token in (" allow", " deny", " delete", " add", " enable", " disable", " flush", " -f ", " --reload", " set")):
            return False
        return True

    def _persistence_command_is_mutating(self, command: str) -> str:
        lower = str(command or "").strip().lower()
        if re.search(r"\bsystemctl\s+(enable|disable|start|stop|restart|reload|mask|unmask|daemon-reload|edit)\b", lower):
            return "systemd"
        if re.search(r"\blaunchctl\s+(load|unload|bootstrap|bootout|enable|disable|kickstart|submit|remove)\b", lower):
            return "launchd"
        if re.search(r"\bcrontab\s+(-e|-r|[^-]\S*)", lower) and " -l" not in lower:
            return "cron"
        if re.search(r"\b(defaults|security|dscl|sysadminctl)\b", lower):
            return "macos_admin"
        return ""

    def _strip_leading_sudo(self, command: str) -> str:
        tokens = self._command_tokens(command)
        if not tokens:
            return command
        try:
            index = next(i for i, token in enumerate(tokens) if token.strip("\"'").lower() in {"sudo", "doas"})
        except StopIteration:
            return command
        rest = tokens[index + 1:]
        while rest and rest[0].startswith("-"):
            rest = rest[1:]
        return " ".join(rest) if rest else command

    def _assess_cross_platform_system_command(
        self,
        command: str,
        *,
        config: Dict[str, Any],
        posture: str,
        runtime_context: Optional[Dict[str, Any]],
        analysis_payload: Optional[Dict[str, Any]],
    ) -> Optional[SafetyDecision]:
        if not self._cross_platform_protection_enabled(config):
            return None
        raw = str(command or "").strip()
        if not raw:
            return None
        lower = raw.lower()
        paths = self._extract_explicit_paths_from_command(raw)
        high_verdict = self._cross_platform_verdict(config, "highRiskMutationVerdict", "block")
        read_verdict = self._cross_platform_verdict(config, "sensitiveReadVerdict", "review")
        privilege_verdict = self._cross_platform_verdict(config, "privilegeElevationVerdict", "review")
        persistence_verdict = self._cross_platform_verdict(config, "persistenceVerdict", "review")
        firewall_verdict = self._cross_platform_verdict(config, "firewallVerdict", "review")

        mutating = self._posix_command_has_mutating_verb(raw)
        sensitive_paths = [path for path in paths if self._is_cross_platform_sensitive_path(path)]
        hard_paths = [path for path in paths if self._is_cross_platform_hard_auth_path(path)]
        persistence_paths = [path for path in paths if self._is_cross_platform_persistence_path(path)]

        if (mutating or re.search(r"\b(sudo|doas)\b", lower)) and hard_paths:
            path = hard_paths[0]
            return self._cross_platform_decision(
                verdict=high_verdict,
                reason="命令会修改 Linux/macOS 账号、认证或 sudo/PAM/SSH 核心系统对象，可能导致无法登录或权限失控。",
                risk_code=self._cross_platform_risk_code(path, mutation=True),
                posture=posture,
                command=raw,
                path=path,
                runtime_context=runtime_context,
                analysis_payload=analysis_payload,
            )

        if mutating and persistence_paths:
            path = persistence_paths[0]
            return self._cross_platform_decision(
                verdict=persistence_verdict,
                reason="命令会修改 systemd/launchd/cron/autostart 等持久化启动面，需要人工确认。",
                risk_code=self._cross_platform_risk_code(path, mutation=True),
                posture=posture,
                command=raw,
                path=path,
                runtime_context=runtime_context,
                analysis_payload=analysis_payload,
            )

        if self._posix_command_is_sensitive_read(raw) and sensitive_paths:
            path = sensitive_paths[0]
            return self._cross_platform_decision(
                verdict=read_verdict,
                reason="命令会读取 Linux/macOS 敏感系统、认证或 secret 对象，需要人工确认用途。",
                risk_code=self._cross_platform_risk_code(path, mutation=False),
                posture=posture,
                command=raw,
                path=path,
                runtime_context=runtime_context,
                analysis_payload=analysis_payload,
            )

        persistence_signal = self._persistence_command_is_mutating(raw)
        if persistence_signal:
            return self._cross_platform_decision(
                verdict=persistence_verdict,
                reason="命令会修改启动项、系统服务、cron 或 macOS 管理面，需要人工确认。",
                risk_code="cross_platform_persistence_mutation",
                posture=posture,
                command=raw,
                runtime_context=runtime_context,
                analysis_payload=analysis_payload,
                extra_details={"matchedRule": persistence_signal},
            )

        if self._firewall_command_is_mutating(raw):
            return self._cross_platform_decision(
                verdict=firewall_verdict,
                reason="命令会修改本机防火墙或网络安全策略，需要人工确认。",
                risk_code="cross_platform_firewall_mutation",
                posture=posture,
                command=raw,
                runtime_context=runtime_context,
                analysis_payload=analysis_payload,
            )

        if re.search(r"(?<![a-z0-9_-])(sudo|doas)(?![a-z0-9_-])", lower):
            stripped = self._strip_leading_sudo(raw)
            if stripped and stripped != raw:
                nested = self._assess_cross_platform_system_command(
                    stripped,
                    config=config,
                    posture=posture,
                    runtime_context=runtime_context,
                    analysis_payload=analysis_payload,
                )
                if nested is not None and nested.is_block():
                    return nested
            return self._cross_platform_decision(
                verdict=privilege_verdict,
                reason="命令请求 sudo/doas 提权。提权本身不阻断，但需要远程 Safety 审批后才能继续。",
                risk_code="privilege_elevation_review",
                posture=posture,
                command=raw,
                runtime_context=runtime_context,
                analysis_payload=analysis_payload,
                extra_details={"requiresSensitiveInput": True, "secretType": "sudo_password"},
            )

        return None

    def _assess_cross_platform_file_write(
        self,
        path: Path,
        *,
        append: bool,
        config: Dict[str, Any],
        posture: str,
        runtime_context: Optional[Dict[str, Any]],
        content_preview: str | None,
    ) -> Optional[SafetyDecision]:
        if not self._cross_platform_protection_enabled(config):
            return None
        high_verdict = self._cross_platform_verdict(config, "highRiskMutationVerdict", "block")
        persistence_verdict = self._cross_platform_verdict(config, "persistenceVerdict", "review")
        if self._is_cross_platform_hard_auth_path(path) or self._is_cross_platform_secret_path(path):
            return self._cross_platform_decision(
                verdict=high_verdict,
                reason="禁止直接写入 Linux/macOS 账号、认证、sudo/PAM/SSH 或 secret store 关键对象。",
                risk_code=self._cross_platform_risk_code(path, mutation=True),
                posture=posture,
                path=path,
                runtime_context=runtime_context,
                extra_details={"append": append},
            )
        if self._is_cross_platform_persistence_path(path):
            return self._cross_platform_decision(
                verdict=persistence_verdict,
                reason="写入 systemd/launchd/cron/autostart 等启动持久化面需要人工确认。",
                risk_code=self._cross_platform_risk_code(path, mutation=True),
                posture=posture,
                path=path,
                runtime_context=runtime_context,
                extra_details={"append": append},
            )
        if path.suffix.lower() in {".sh", ".command", ".zsh", ".bash", ".plist", ".service"} and content_preview:
            script_decision = self._assess_cross_platform_system_command(
                content_preview,
                config=config,
                posture=posture,
                runtime_context=runtime_context,
                analysis_payload=None,
            )
            if script_decision is not None and not script_decision.is_allow():
                return self._cross_platform_decision(
                    verdict=script_decision.verdict,
                    reason="写入的脚本或启动配置内容包含 Linux/macOS 系统安全高风险操作。",
                    risk_code=script_decision.risk_code,
                    posture=posture,
                    path=path,
                    runtime_context=runtime_context,
                    extra_details={"append": append, "scriptSafety": script_decision.to_payload()},
                )
        return None

    def _windows_profile_config(self, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return dict((config or self._config()).get("windowsProfileProtection") or {})

    def _windows_profile_protection_enabled(self, config: Optional[Dict[str, Any]] = None) -> bool:
        return bool(self._windows_profile_config(config).get("enabled", True))

    def _windows_profile_verdict(self, config: Dict[str, Any], key: str, fallback: str) -> str:
        return self._normalize_verdict(
            self._windows_profile_config(config).get(key),
            fallback=fallback,
            allowed={"allow", "audit", "review", "block"},
        )

    def _windows_users_root(self) -> Path:
        home = Path.home()
        if home.parent:
            return home.parent
        return Path(os.environ.get("SystemDrive", "C:") + "\\Users")

    def _is_case_relative_to(self, path: Path, root: Path) -> bool:
        normalized_path = self._normalize_path(str(path))
        normalized_root = self._normalize_path(str(root))
        if normalized_path is None or normalized_root is None:
            return False
        try:
            normalized_path.relative_to(normalized_root)
            return True
        except ValueError:
            return str(normalized_path).lower().startswith(str(normalized_root).rstrip("\\/").lower() + os.sep.lower())

    def _is_windows_profile_hive_path(self, path: Path) -> bool:
        name = path.name.lower()
        if name in {"ntuser.dat", "usrclass.dat"}:
            return True
        return bool(re.match(r"^(?:ntuser|usrclass)\.dat(?:\.log\d*|\.tmp|\.blf|\.regtrans-ms)?$", name))

    def _is_windows_default_or_temp_profile_path(self, path: Path) -> bool:
        users_root = self._normalize_path(str(self._windows_users_root()))
        normalized = self._normalize_path(str(path))
        if users_root is None or normalized is None:
            return False
        try:
            relative = normalized.relative_to(users_root)
        except ValueError:
            return False
        first = relative.parts[0].lower() if relative.parts else ""
        return first == "default" or first == "default user" or first.startswith("temp")

    def _is_windows_startup_path(self, path: Path) -> bool:
        text = str(path).lower().replace("/", "\\")
        return "\\microsoft\\windows\\start menu\\programs\\startup\\" in text

    def _is_windows_user_shell_script_path(self, path: Path) -> bool:
        return path.suffix.lower() in {".ps1", ".bat", ".cmd", ".vbs", ".js", ".jse", ".wsf", ".wsh", ".lnk", ".exe"}

    def _is_windows_current_profile_root(self, path: Path) -> bool:
        normalized = self._normalize_path(str(path))
        home = self._normalize_path(str(Path.home()))
        return normalized is not None and home is not None and str(normalized).rstrip("\\/").lower() == str(home).rstrip("\\/").lower()

    def _is_windows_profile_root_path(self, path: Path) -> bool:
        if self._is_windows_current_profile_root(path):
            return True
        users_root = self._normalize_path(str(self._windows_users_root()))
        normalized = self._normalize_path(str(path))
        if users_root is None or normalized is None:
            return False
        try:
            relative = normalized.relative_to(users_root)
        except ValueError:
            return False
        if len(relative.parts) != 1:
            return False
        name = relative.parts[0].lower()
        return name == "default" or name == "default user" or name.startswith("temp")

    def _is_windows_profile_sensitive_path(self, path: Path, *, include_roots: bool = False) -> bool:
        normalized = self._normalize_path(str(path))
        if normalized is None:
            return False
        if self._is_windows_profile_hive_path(normalized):
            return True
        if self._is_windows_default_or_temp_profile_path(normalized):
            return True
        if include_roots and self._is_windows_profile_root_path(normalized):
            return True
        if self._is_windows_startup_path(normalized):
            return True
        return False

    def _windows_profile_registry_signal(self, value: str) -> str:
        normalized = str(value or "").lower().replace("/", "\\")
        normalized = re.sub(r"\s+", " ", normalized)
        registry_needles = [
            "\\profilelist",
            "profileimagepath",
            "user shell folders",
            "\\shell folders",
            "currentversion\\run",
            "currentversion\\runonce",
        ]
        if any(needle in normalized for needle in registry_needles):
            if "profilelist" in normalized or "profileimagepath" in normalized:
                return "profile_mapping"
            if "shell folders" in normalized:
                return "shell_folders"
            if "currentversion\\run" in normalized or "currentversion\\runonce" in normalized:
                return "startup_registry"
        return ""

    def _reg_command_verb(self, command: str) -> str:
        tokens = self._command_tokens(command)
        for index, token in enumerate(tokens):
            clean = token.strip("\"'").lower()
            if clean in {"reg", "reg.exe"} and index + 1 < len(tokens):
                return tokens[index + 1].strip("\"'").lower()
        return ""

    def _command_has_any_verb(self, command: str, verbs: set[str]) -> bool:
        tokens = [token.strip("\"'").lower() for token in self._command_tokens(command)]
        joined = " ".join(tokens)
        if any(token in verbs for token in tokens):
            return True
        return any(re.search(rf"(?<![a-z0-9_-]){re.escape(verb)}(?![a-z0-9_-])", joined) for verb in verbs)

    def _path_list_has_windows_profile_sensitive_target(self, paths: list[Path], *, include_roots: bool = False) -> bool:
        return any(self._is_windows_profile_sensitive_path(path, include_roots=include_roots) for path in paths)

    def _windows_profile_decision(
        self,
        *,
        verdict: str,
        reason: str,
        risk_code: str,
        posture: str,
        command: str | None = None,
        path: Path | None = None,
        runtime_context: Optional[Dict[str, Any]] = None,
        analysis_payload: Optional[Dict[str, Any]] = None,
        extra_details: Optional[Dict[str, Any]] = None,
    ) -> SafetyDecision:
        details: Dict[str, Any] = {"runtime_context": runtime_context or {}}
        if command is not None:
            details["command"] = command
        if path is not None:
            details["path"] = str(path)
        if analysis_payload is not None:
            details["analysis"] = analysis_payload
        if extra_details:
            details.update(extra_details)
        return self._decision(
            verdict=verdict,
            reason=reason,
            risk_code=risk_code,
            governance_target="system_integrity",
            posture=posture,
            details=details,
            allow_override=verdict != "block",
        )

    def _assess_windows_profile_command(
        self,
        command: str,
        *,
        config: Dict[str, Any],
        posture: str,
        runtime_context: Optional[Dict[str, Any]],
        analysis_payload: Optional[Dict[str, Any]],
    ) -> Optional[SafetyDecision]:
        if not self._windows_profile_protection_enabled(config):
            return None
        raw = str(command or "").strip()
        if not raw:
            return None
        lower = raw.lower()
        paths = self._extract_explicit_paths_from_command(raw)
        has_sensitive_path = self._path_list_has_windows_profile_sensitive_target(paths, include_roots=False)
        has_profile_root_path = self._path_list_has_windows_profile_sensitive_target(paths, include_roots=True)
        registry_signal = self._windows_profile_registry_signal(raw)
        high_verdict = self._windows_profile_verdict(config, "highRiskMutationVerdict", "block")
        medium_verdict = self._windows_profile_verdict(config, "mediumRiskMutationVerdict", "review")
        read_verdict = self._windows_profile_verdict(config, "sensitiveReadVerdict", "review")

        reg_verb = self._reg_command_verb(raw)
        if reg_verb:
            if registry_signal and reg_verb in {"add", "delete", "import", "load", "unload", "restore"}:
                return self._windows_profile_decision(
                    verdict=high_verdict,
                    reason="命令会修改 Windows profile 注册表映射或用户 Shell Folder，可能导致无法登录或用户目录重映射。",
                    risk_code="windows_profile_registry_mutation",
                    posture=posture,
                    command=raw,
                    runtime_context=runtime_context,
                    analysis_payload=analysis_payload,
                    extra_details={"registrySignal": registry_signal, "verb": reg_verb},
                )
            if registry_signal and reg_verb in {"query", "export", "save"}:
                return self._windows_profile_decision(
                    verdict=read_verdict,
                    reason="命令会读取 Windows profile 注册表映射，需要人工确认用途。",
                    risk_code="windows_profile_sensitive_read",
                    posture=posture,
                    command=raw,
                    runtime_context=runtime_context,
                    analysis_payload=analysis_payload,
                    extra_details={"registrySignal": registry_signal, "verb": reg_verb},
                )
            if reg_verb in {"import", "load", "restore"} and any(path.suffix.lower() == ".reg" for path in paths):
                return self._windows_profile_decision(
                    verdict=medium_verdict,
                    reason="命令会导入或加载注册表文件，需要确认不会修改 Windows profile 映射。",
                    risk_code="windows_profile_registry_mutation",
                    posture=posture,
                    command=raw,
                    runtime_context=runtime_context,
                    analysis_payload=analysis_payload,
                    extra_details={"verb": reg_verb},
                )

        if registry_signal and re.search(r"(?<![a-z0-9_-])(set-itemproperty|new-itemproperty|remove-itemproperty|remove-item|new-item)(?![a-z0-9_-])", lower):
            return self._windows_profile_decision(
                verdict=high_verdict,
                reason="PowerShell 注册表命令会修改 Windows profile 映射或用户 Shell Folder。",
                risk_code="windows_profile_registry_mutation",
                posture=posture,
                command=raw,
                runtime_context=runtime_context,
                analysis_payload=analysis_payload,
                extra_details={"registrySignal": registry_signal},
            )

        acl_commands = {"icacls", "takeown", "set-acl"}
        if self._command_has_any_verb(raw, acl_commands) and has_profile_root_path:
            return self._windows_profile_decision(
                verdict=high_verdict,
                reason="命令会修改 Windows profile hive/Default/TEMP/profile root 的 ACL 或所有权。",
                risk_code="windows_profile_acl_mutation",
                posture=posture,
                command=raw,
                runtime_context=runtime_context,
                analysis_payload=analysis_payload,
            )

        attrib_mutation = re.search(r"(?<![a-z0-9_-])attrib(?:\.exe)?(?![a-z0-9_-]).*(?:\+|-)[rashsi]", lower) is not None
        if attrib_mutation and has_profile_root_path:
            return self._windows_profile_decision(
                verdict=high_verdict,
                reason="命令会修改 Windows profile 关键文件属性。",
                risk_code="windows_profile_acl_mutation",
                posture=posture,
                command=raw,
                runtime_context=runtime_context,
                analysis_payload=analysis_payload,
            )

        reparse_signal = (
            self._command_has_any_verb(raw, {"mklink", "subst"})
            or "fsutil reparsepoint" in lower
            or "fsutil hardlink" in lower
            or "new-item" in lower and any(marker in lower for marker in ["symboliclink", "junction", "hardlink"])
        )
        if reparse_signal and has_profile_root_path:
            return self._windows_profile_decision(
                verdict=high_verdict,
                reason="命令会在 Windows profile 关键路径创建或删除 reparse point / junction / subst 映射。",
                risk_code="windows_profile_reparse_mutation",
                posture=posture,
                command=raw,
                runtime_context=runtime_context,
                analysis_payload=analysis_payload,
            )

        destructive_commands = {
            "del",
            "erase",
            "rd",
            "rmdir",
            "remove-item",
            "rm",
            "move",
            "mv",
            "ren",
            "rename",
            "rename-item",
            "move-item",
            "clear-content",
        }
        destructive_flags = any(flag in lower for flag in [" -recurse", " /s", " -force", " /q", " -r "])
        if self._command_has_any_verb(raw, destructive_commands) and (has_sensitive_path or (has_profile_root_path and destructive_flags)):
            return self._windows_profile_decision(
                verdict=high_verdict,
                reason="命令会删除、移动、重命名或清空 Windows profile hive/Default/TEMP/profile root。",
                risk_code="windows_profile_hive_mutation",
                posture=posture,
                command=raw,
                runtime_context=runtime_context,
                analysis_payload=analysis_payload,
            )

        copy_commands = {"copy", "copy-item", "cp", "xcopy", "robocopy"}
        destructive_copy = any(flag in lower for flag in ["/mir", "/purge", " /move", " /mov"])
        if self._command_has_any_verb(raw, copy_commands) and has_profile_root_path:
            if destructive_copy:
                return self._windows_profile_decision(
                    verdict=high_verdict,
                    reason="命令会用 destructive copy/mirror 影响 Windows profile 关键路径。",
                    risk_code="windows_profile_destructive_copy",
                    posture=posture,
                    command=raw,
                    runtime_context=runtime_context,
                    analysis_payload=analysis_payload,
                    extra_details={"destructiveCopy": True},
                )
            if has_sensitive_path:
                return self._windows_profile_decision(
                    verdict=read_verdict,
                    reason="命令会复制或备份 Windows profile hive/Default/TEMP/profile 关键对象，需要确认用途。",
                    risk_code="windows_profile_sensitive_read",
                    posture=posture,
                    command=raw,
                    runtime_context=runtime_context,
                    analysis_payload=analysis_payload,
                )

        if self._looks_like_read_only_enumeration_command(raw) and (has_sensitive_path or registry_signal):
            return self._windows_profile_decision(
                verdict=read_verdict,
                reason="命令会枚举 Windows profile hive/映射/Default/TEMP 敏感对象，需要人工确认。",
                risk_code="windows_profile_sensitive_read",
                posture=posture,
                command=raw,
                runtime_context=runtime_context,
                analysis_payload=analysis_payload,
                extra_details={"registrySignal": registry_signal} if registry_signal else None,
            )

        return None

    def _assess_windows_profile_file_write(
        self,
        path: Path,
        *,
        append: bool,
        config: Dict[str, Any],
        posture: str,
        runtime_context: Optional[Dict[str, Any]],
        content_preview: str | None,
    ) -> Optional[SafetyDecision]:
        if not self._windows_profile_protection_enabled(config):
            return None
        high_verdict = self._windows_profile_verdict(config, "highRiskMutationVerdict", "block")
        medium_verdict = self._windows_profile_verdict(config, "mediumRiskMutationVerdict", "review")
        text = str(content_preview or "")

        if self._is_windows_profile_hive_path(path) or self._is_windows_default_or_temp_profile_path(path):
            return self._windows_profile_decision(
                verdict=high_verdict,
                reason="禁止直接写入 Windows profile hive、Default profile 或 TEMP profile。",
                risk_code="windows_profile_hive_mutation",
                posture=posture,
                path=path,
                runtime_context=runtime_context,
                extra_details={"append": append},
            )

        if self._is_windows_startup_path(path) and self._is_windows_user_shell_script_path(path):
            return self._windows_profile_decision(
                verdict=high_verdict,
                reason="禁止直接写入用户 Startup 自启动脚本、快捷方式或可执行文件。",
                risk_code="windows_profile_registry_mutation",
                posture=posture,
                path=path,
                runtime_context=runtime_context,
                extra_details={"append": append},
            )

        registry_signal = self._windows_profile_registry_signal(text)
        if path.suffix.lower() == ".reg" and registry_signal:
            return self._windows_profile_decision(
                verdict=high_verdict,
                reason="禁止写入会修改 Windows profile 映射、Shell Folder 或启动项的 .reg 内容。",
                risk_code="windows_profile_registry_mutation",
                posture=posture,
                path=path,
                runtime_context=runtime_context,
                extra_details={"append": append, "registrySignal": registry_signal},
            )
        if path.suffix.lower() == ".reg":
            return self._windows_profile_decision(
                verdict=medium_verdict,
                reason="写入注册表导入文件需要确认不会影响 Windows profile 映射。",
                risk_code="windows_profile_sensitive_read",
                posture=posture,
                path=path,
                runtime_context=runtime_context,
                extra_details={"append": append},
            )

        if path.suffix.lower() in {".ps1", ".bat", ".cmd"} and text:
            script_decision = self._assess_windows_profile_command(
                text,
                config=config,
                posture=posture,
                runtime_context=runtime_context,
                analysis_payload=None,
            )
            if script_decision is not None and not script_decision.is_allow():
                return self._windows_profile_decision(
                    verdict=script_decision.verdict,
                    reason="写入的脚本内容包含 Windows profile/ACL/registry 映射高风险操作。",
                    risk_code=script_decision.risk_code,
                    posture=posture,
                    path=path,
                    runtime_context=runtime_context,
                    extra_details={"append": append, "scriptSafety": script_decision.to_payload()},
                )

        return None

    def _external_tool_command_text(self, params: Dict[str, Any]) -> str:
        for key in ("command", "cmd", "script", "shell", "input"):
            value = params.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return ""

    def _external_tool_content_preview(self, params: Dict[str, Any]) -> str:
        for key in ("content", "new_string", "newString", "text", "body"):
            value = params.get(key)
            if isinstance(value, str) and value.strip():
                return value[:12000]
        return ""

    def _external_tool_path_values(self, params: Dict[str, Any]) -> list[str]:
        paths: list[str] = []
        path_keys = {
            "path",
            "file_path",
            "filepath",
            "filePath",
            "target",
            "source",
            "destination",
            "dest",
            "old_path",
            "new_path",
            "oldPath",
            "newPath",
        }

        def _walk(value: Any, key_hint: str = "") -> None:
            if value is None:
                return
            if isinstance(value, str):
                if key_hint in path_keys or self._looks_like_path_value(value):
                    paths.append(value)
                return
            if isinstance(value, dict):
                for key, item in value.items():
                    _walk(item, str(key))
                return
            if isinstance(value, (list, tuple, set)):
                for item in value:
                    _walk(item, key_hint)

        _walk(params)
        deduped: list[str] = []
        seen: set[str] = set()
        for path in paths:
            key = path.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(path)
        return deduped[:24]

    def _looks_like_path_value(self, value: str) -> bool:
        text = str(value or "").strip()
        if not text or len(text) > 500:
            return False
        expanded = self._expand_path_text(text)
        return bool(
            re.match(r"^[a-zA-Z]:[\\/]", expanded)
            or expanded.startswith(("\\\\", "/", "~\\", "~/"))
            or re.search(r"(?i)(%userprofile%|\$env:userprofile|\$\{env:userprofile\}|%localappdata%|\$env:localappdata|~[\\/])", text)
        )

    def _external_tool_local_system_decision(
        self,
        *,
        source_decision: SafetyDecision,
        tool_name: str,
        params: Dict[str, Any],
        posture: str,
        runtime_context: Optional[Dict[str, Any]],
    ) -> SafetyDecision:
        if source_decision.is_block():
            verdict = "block"
            risk_code = "external_tool_local_system_hard_stop"
            reason = "外部工具命中本地系统破坏面，V8 已 hard stop，不向外发出 tool_use。"
            allow_override = False
        else:
            verdict = "review"
            risk_code = "external_tool_local_system_review"
            reason = "外部工具命中本地系统敏感面，需要 V8 Safety 审批后才可发出。"
            allow_override = True
        return self._decision(
            verdict=verdict,
            reason=reason,
            risk_code=risk_code,
            governance_target="system_integrity",
            posture=posture,
            details={
                "tool_name": tool_name,
                "runtime_context": runtime_context or {},
                "sourceSafety": source_decision.to_payload(),
                "paramsPreview": self._redact_sensitive_payload(params),
            },
            allow_override=allow_override,
        )

    def _analyze_system_command(self, command: str, *, runtime_context: Optional[Dict[str, Any]]) -> _CommandAnalysis:
        analysis = _CommandAnalysis(original=str(command or ""))
        decoded_commands = self._decode_command_payloads(command)
        analysis.decoded_commands = decoded_commands
        lower = str(command or "").lower()
        if self._has_encoded_or_reflective_indicator(lower):
            analysis.encoded_indicators = self._encoded_indicators(lower)
            if not decoded_commands and self._has_execution_context(lower):
                analysis.ambiguous_encoded_execution = True
        analysis.download_output_paths = self._extract_download_output_paths(command)
        analysis.download_hosts = self._extract_download_hosts(command)
        download_reason = self._download_execute_reason(command)
        if download_reason:
            analysis.download_execute_detected = True
            analysis.download_execute_reason = download_reason
        return analysis

    def _decode_command_payloads(self, command: str) -> list[str]:
        decoded: list[str] = []
        for candidate in self._extract_powershell_encoded_candidates(command):
            decoded.extend(self._decode_base64_text(candidate, prefer_utf16=True))
        lower = str(command or "").lower()
        if self._has_execution_context(lower) and self._has_encoded_or_reflective_indicator(lower):
            for candidate in self._extract_generic_base64_candidates(command):
                decoded.extend(self._decode_base64_text(candidate, prefer_utf16=False))

        cleaned: list[str] = []
        seen: set[str] = set()
        for item in decoded:
            text = self._clean_decoded_command(item)
            if not text:
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(text)
        return cleaned[:6]

    def _extract_powershell_encoded_candidates(self, command: str) -> list[str]:
        try:
            tokens = [token for token in shlex.split(str(command or "").strip(), posix=False) if token]
        except Exception:
            tokens = [token for token in re.split(r"\s+", str(command or "").strip()) if token]
        candidates: list[str] = []
        for index, token in enumerate(tokens):
            normalized = token.strip("\"'").lower()
            if normalized in {"-enc", "-encodedcommand", "/enc", "/encodedcommand"} and index + 1 < len(tokens):
                candidates.append(tokens[index + 1].strip("\"'"))
                continue
            match = re.match(r"(?i)^-(?:enc|encodedcommand):?(.+)$", token.strip("\"'"))
            if match:
                candidates.append(match.group(1).strip("\"'"))
        return candidates

    def _extract_generic_base64_candidates(self, command: str) -> list[str]:
        raw = str(command or "")
        candidates = re.findall(r"(?<![a-zA-Z0-9+/=_-])([a-zA-Z0-9+/]{24,}={0,2})(?![a-zA-Z0-9+/=_-])", raw)
        return [candidate for candidate in candidates if len(candidate.strip("=")) >= 24][:8]

    def _decode_base64_text(self, candidate: str, *, prefer_utf16: bool) -> list[str]:
        clean = re.sub(r"\s+", "", str(candidate or "").strip().strip("\"'"))
        if not clean or len(clean) < 8:
            return []
        padding = "=" * ((4 - len(clean) % 4) % 4)
        try:
            raw = base64.b64decode(clean + padding, validate=True)
        except (binascii.Error, ValueError):
            return []
        encodings = ["utf-16le", "utf-8", "utf-16"] if prefer_utf16 else ["utf-8", "utf-16le", "utf-16"]
        decoded: list[str] = []
        for encoding in encodings:
            try:
                text = raw.decode(encoding)
            except UnicodeDecodeError:
                continue
            if self._looks_like_decoded_text(text):
                decoded.append(text)
        return decoded

    def _clean_decoded_command(self, value: str) -> str:
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]+", " ", str(value or ""))
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _looks_like_decoded_text(self, value: str) -> bool:
        text = self._clean_decoded_command(value)
        if len(text) < 2:
            return False
        printable = sum(1 for char in text if char.isprintable() or char.isspace())
        return printable / max(1, len(text)) > 0.85

    def _has_encoded_or_reflective_indicator(self, lower_command: str) -> bool:
        return any(
            token in lower_command
            for token in [
                "-enc",
                "-encodedcommand",
                "frombase64string",
                "base64 -d",
                "base64 --decode",
                "eval(",
                "exec(",
                "iex",
                "invoke-expression",
                "downloadstring",
                "reflection.assembly",
                "add-type",
                "mshta",
                "rundll32",
                "regsvr32",
            ]
        )

    def _encoded_indicators(self, lower_command: str) -> list[str]:
        indicators = []
        for token in [
            "-enc",
            "-encodedcommand",
            "frombase64string",
            "base64 -d",
            "base64 --decode",
            "eval(",
            "exec(",
            "iex",
            "invoke-expression",
            "downloadstring",
            "reflection.assembly",
            "add-type",
            "mshta",
            "rundll32",
            "regsvr32",
        ]:
            if token in lower_command:
                indicators.append(token)
        return indicators

    def _has_execution_context(self, lower_command: str) -> bool:
        return any(
            token in lower_command
            for token in [
                "powershell",
                "pwsh",
                "cmd /c",
                "bash -c",
                "sh -c",
                "python -c",
                "node -e",
                "start-process",
                "invoke-expression",
                " iex",
                "eval(",
                "exec(",
                "| bash",
                "| sh",
                "chmod +x",
                "mshta",
                "rundll32",
                "regsvr32",
            ]
        )

    def _extract_download_hosts(self, command: str) -> list[str]:
        hosts: list[str] = []
        for match in re.finditer(r"https?://[^\s\"'`<>]+", str(command or ""), flags=re.IGNORECASE):
            parsed = urlparse(match.group(0))
            if parsed.hostname and parsed.hostname not in hosts:
                hosts.append(parsed.hostname.lower())
        return hosts

    def _extract_download_output_paths(self, command: str) -> list[Path]:
        raw = str(command or "")
        patterns = [
            r"(?i)\b(?:curl|curl\.exe)\b.*?(?:-o|--output)\s+([^\s;&|]+)",
            r"(?i)\b(?:wget|wget\.exe)\b.*?(?:-o|-O|--output-document=?)\s*([^\s;&|]+)",
            r"(?i)\b(?:invoke-webrequest|invoke-restmethod|iwr|irm)\b.*?-outfile\s+([^\s;&|]+)",
            r"(?i)\bcertutil\b.*?-urlcache.*?-f\s+https?://[^\s\"']+\s+([^\s;&|]+)",
            r"(?i)\bbitsadmin\b.*?/transfer.*?https?://[^\s\"']+\s+([^\s;&|]+)",
        ]
        paths: list[Path] = []
        seen: set[str] = set()
        for pattern in patterns:
            for match in re.finditer(pattern, raw):
                candidate = match.group(1).strip().strip("\"'")
                normalized = self._normalize_path(candidate)
                if normalized is None:
                    continue
                key = str(normalized).lower()
                if key in seen:
                    continue
                seen.add(key)
                paths.append(normalized)
        return paths

    def _download_execute_reason(self, command: str) -> str:
        lower = str(command or "").lower()
        if re.search(r"\b(?:curl|wget|irm|iwr|invoke-webrequest|invoke-restmethod)\b.*\|\s*(?:bash|sh|powershell|pwsh|cmd|python|node)\b", lower):
            return "download_pipe_to_interpreter"
        if "downloadstring" in lower and (" iex" in lower or "invoke-expression" in lower):
            return "downloadstring_invoke_expression"
        if re.search(r"\b(?:invoke-webrequest|invoke-restmethod|iwr|irm|curl|wget|certutil|bitsadmin)\b", lower):
            if re.search(r"(?:;|&&|\|).*\b(?:start-process|powershell(?:\.exe)?\s+-file|pwsh(?:\.exe)?\s+-file|cmd\s+/c|bash\s+|sh\s+|python\s+|node\s+)", lower):
                return "download_then_execute_in_command"
            if "chmod +x" in lower and re.search(r"(?:;|&&).*(?:\./|/tmp/|temp|\.sh|\.py|\.js)", lower):
                return "download_chmod_execute"
        if re.search(r"\b(?:urlretrieve|requests\.get|httpx\.get)\b", lower) and re.search(r"\b(?:subprocess\.|os\.system|start-process|exec\()", lower):
            return "programmatic_download_then_execute"
        return ""

    def _remember_recent_downloads(self, analysis: _CommandAnalysis, runtime_context: Optional[Dict[str, Any]]) -> None:
        now = time.monotonic()
        self._recent_downloads = [
            item
            for item in self._recent_downloads
            if now - item.recorded_at <= _RECENT_DOWNLOAD_TTL_SECONDS
        ]
        if not analysis.download_output_paths:
            return
        runtime_kind = None
        if isinstance(runtime_context, dict):
            runtime_kind = str(runtime_context.get("runtime_kind") or runtime_context.get("runtimeKind") or "") or None
        host = analysis.download_hosts[0] if analysis.download_hosts else None
        existing = {str(item.path).lower() for item in self._recent_downloads}
        for path in analysis.download_output_paths:
            key = str(path).lower()
            if key in existing:
                continue
            self._recent_downloads.append(_RecentDownload(path=path, host=host, recorded_at=now, runtime_kind=runtime_kind))

    def _matches_recent_download_execution(self, command: str, runtime_context: Optional[Dict[str, Any]]) -> _RecentDownload | None:
        now = time.monotonic()
        self._recent_downloads = [
            item
            for item in self._recent_downloads
            if now - item.recorded_at <= _RECENT_DOWNLOAD_TTL_SECONDS
        ]
        if not self._recent_downloads:
            return None
        lower = str(command or "").lower()
        target_paths = self._extract_explicit_paths_from_command(command)
        for token in self._command_tokens(command):
            normalized = self._normalize_path(token.strip("\"'"))
            if normalized is not None:
                target_paths.append(normalized)
        for download in self._recent_downloads:
            if download.path.suffix.lower() not in _DOWNLOAD_EXECUTABLE_SUFFIXES:
                continue
            for target in target_paths:
                if str(target).lower() == str(download.path).lower():
                    if self._has_execution_context(lower) or self._looks_like_direct_executable_command(command, target):
                        return download
        return None

    def _looks_like_direct_executable_command(self, command: str, target: Path) -> bool:
        tokens = self._command_tokens(command)
        if not tokens:
            return False
        first = self._normalize_path(tokens[0].strip("\"'"))
        return first is not None and str(first).lower() == str(target).lower()

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
        if not self._is_process_control_command(command):
            return False
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

    def _command_tokens(self, command: str) -> list[str]:
        normalized = (command or "").strip().lower()
        if not normalized:
            return []
        try:
            return [token.lower() for token in shlex.split(normalized, posix=False) if token]
        except Exception:
            return [token for token in re.split(r"\s+", normalized) if token]

    def _extract_explicit_paths_from_command(self, command: str) -> list[Path]:
        raw = str(command or "").strip()
        if not raw:
            return []
        seen: set[str] = set()
        extracted: list[Path] = []
        quoted_matches = re.findall(r'["\']([^"\']+)["\']', raw)
        tokens = quoted_matches + self._command_tokens(raw)
        for token in tokens:
            candidate = str(token or "").strip().strip("\"'")
            if not candidate:
                continue
            expanded = self._expand_path_text(candidate)
            if candidate.startswith("-") and not re.match(r"^[a-zA-Z]:[\\/]", expanded):
                continue
            if (
                candidate.startswith("/")
                and not re.match(r"^[a-zA-Z]:[\\/]", expanded)
                and not re.match(r"^/(?:etc|var|root|users|library|system|boot|lib|usr|home|tmp)(?:/|$)", expanded, re.IGNORECASE)
            ):
                continue
            if not (
                re.match(r"^[a-zA-Z]:[\\/]", expanded)
                or expanded.startswith(("\\\\", "/", "~\\", "~/"))
                or re.search(r"(?i)(%userprofile%|\$env:userprofile|\$\{env:userprofile\}|%localappdata%|\$env:localappdata|~[\\/])", candidate)
            ):
                continue
            normalized = self._normalize_path(expanded)
            if normalized is None:
                continue
            key = str(normalized).lower()
            if key in seen:
                continue
            seen.add(key)
            extracted.append(normalized)
        return extracted

    def _workspace_root_from_context(self, runtime_context: Optional[Dict[str, Any]]) -> Optional[Path]:
        if not isinstance(runtime_context, dict):
            return None
        raw = runtime_context.get("workspace_path") or runtime_context.get("workspacePath")
        if not raw:
            return None
        return self._normalize_path(str(raw))

    def _workspace_roots_from_context(self, runtime_context: Optional[Dict[str, Any]]) -> list[Path]:
        roots: list[Path] = []
        context_root = self._workspace_root_from_context(runtime_context)
        if context_root is not None:
            roots.append(context_root)
        default_root = self._normalize_path(str(WORKSPACE_HOME))
        if default_root is not None and all(str(default_root).lower() != str(item).lower() for item in roots):
            roots.append(default_root)
        return roots

    def _is_path_within_root(self, path: Path, root: Path) -> bool:
        normalized_path = self._normalize_path(str(path))
        normalized_root = self._normalize_path(str(root))
        if normalized_path is None or normalized_root is None:
            return False
        try:
            normalized_path.relative_to(normalized_root)
            return True
        except ValueError:
            return False

    def _is_user_workspace_write_path(self, path: Path, runtime_context: Optional[Dict[str, Any]]) -> bool:
        return any(self._is_path_within_root(path, root) for root in self._workspace_roots_from_context(runtime_context))

    def _is_process_control_command(self, command: str) -> bool:
        lower = str(command or "").strip().lower()
        if not lower:
            return False
        return bool(re.search(
            r"(?<![a-z0-9_])(taskkill|stop-process|kill|pkill|wmic(?:\s+process)?|sc\s+stop|net\s+stop)(?![a-z0-9_])",
            lower,
        )) and (
            "terminate" in lower
            or "taskkill" in lower
            or "stop-process" in lower
            or re.search(r"(?<![a-z0-9_])(kill|pkill)(?![a-z0-9_])", lower) is not None
            or re.search(r"\b(?:sc|net)\s+stop\b", lower) is not None
            or "wmic" in lower
        )

    def _looks_like_read_only_enumeration_command(self, command: str) -> bool:
        lower = str(command or "").strip().lower()
        if not lower:
            return False
        if any(token in lower for token in ["&&", "||", ";", ">", ">>", "|", "remove-item", "del ", "erase ", "rmdir", "rd ", "move ", "mv ", "rename ", "ren ", "copy ", "xcopy", "robocopy", "set-content", "add-content", "out-file", "new-item", "start-process", "taskkill", "stop-process", "kill", "pkill"]):
            return False
        return re.search(r"(?<![a-z0-9_])(dir|get-childitem|where|findstr|rg)(?![a-z0-9_-])", lower) is not None

    def _is_workspace_read_only_enumeration_command(self, command: str, runtime_context: Optional[Dict[str, Any]]) -> bool:
        if not self._looks_like_read_only_enumeration_command(command):
            return False
        workspace_root = self._workspace_root_from_context(runtime_context)
        if workspace_root is None:
            return False
        target_paths = self._extract_explicit_paths_from_command(command)
        if not target_paths:
            return False
        return all(self._is_path_within_root(path, workspace_root) for path in target_paths)

    def _targets_sensitive_system_path_in_command(self, command: str) -> bool:
        return any(self._is_sensitive_system_path(path) for path in self._extract_explicit_paths_from_command(command))

    def _matches_command_pattern(self, command: str, pattern: str) -> bool:
        normalized_pattern = str(pattern or "").strip().lower()
        normalized_command = str(command or "").strip().lower()
        if not normalized_pattern or not normalized_command:
            return False

        pattern_tokens = self._command_tokens(normalized_pattern)
        command_tokens = self._command_tokens(normalized_command)
        if pattern_tokens and command_tokens:
            window = len(pattern_tokens)
            for index in range(0, len(command_tokens) - window + 1):
                if command_tokens[index:index + window] == pattern_tokens:
                    return True

        if " " in normalized_pattern:
            return normalized_pattern in normalized_command

        boundary_pattern = rf"(?<![a-z0-9_./-]){re.escape(normalized_pattern)}(?![a-z0-9_./-])"
        return re.search(boundary_pattern, normalized_command) is not None

    def _command_patterns(self, command_rules: list[Dict[str, Any]], *, verdict: str) -> list[str]:
        patterns: list[str] = []
        for rule in command_rules:
            if rule.get("verdict") != verdict:
                continue
            patterns.extend([str(item).strip() for item in rule.get("patterns", []) if str(item).strip()])
        return patterns


safety_guardian = SafetyGuardian()
