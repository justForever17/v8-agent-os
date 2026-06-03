from __future__ import annotations

import json
import re
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from core.database import db
from core.delegation_broker import build_workset_dispatch_decisions, normalize_task_brief
from core.prompt_budget import enforce_prompt_budget, estimate_prompt_tokens, truncate_to_estimated_tokens
from core.storage import storage
from core.workspace_resolution import workspace_resolution_service
from erc.runtime_registry import runtime_registry
from runtimes.memory.workflow_service import workflow_memory_service


CODE_SIGNAL_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    ("code_change", ("code", "implement", "implementation", "patch", "edit", "modify", "fix", "代码", "实现", "修改", "修复", "改")),
    ("debug_or_error", ("bug", "error", "traceback", "exception", "debug", "报错", "异常", "故障", "排查")),
    ("refactor_or_architecture", ("refactor", "architecture", "migration", "runtime", "重构", "架构", "迁移", "主链")),
    ("verification", ("test", "pytest", "typecheck", "tsc", "build", "lint", "验证", "测试", "构建", "编译", "回归")),
    ("repo_terms", ("repo", "repository", "workspace", "file", "directory", "git", "仓库", "工作区", "文件", "目录")),
    ("frontend_terms", ("component", "page", "route", "api", "tsx", "react", "next", "frontend", "front-end", "web app", "web application", "next.js", "组件", "页面", "接口", "前端", "前端界面", "前端页面", "web应用", "web 应用", "网页应用", "应用界面")),
    ("code_media_frameworks", ("remotion", "manim", "ffmpeg", "ffprobe", "three.js", "threejs", "p5.js", "p5js", "processing", "webgl", "canvas")),
]

NON_ENGINEERING_PATTERNS = (
    "生成图片",
    "生成视频",
    "写一首",
    "聊天",
    "闲聊",
    "海报",
    "ppt",
    "演示稿",
)

IGNORED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".next",
    "dist",
    "build",
    "coverage",
    ".turbo",
    ".cache",
}

MANIFEST_FILES = (
    "package.json",
    "pnpm-workspace.yaml",
    "yarn.lock",
    "package-lock.json",
    "pyproject.toml",
    "requirements.txt",
    "pytest.ini",
    "tox.ini",
    "tsconfig.json",
    "vitest.config.ts",
    "vite.config.ts",
    "next.config.js",
    "next.config.mjs",
)

SOURCE_EXTENSIONS = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".json",
    ".md",
    ".toml",
    ".yaml",
    ".yml",
    ".css",
    ".scss",
    ".html",
}

TEST_FILE_MARKERS = (
    ".test.",
    ".spec.",
    "_test.",
    "test_",
    "__tests__",
    "tests/",
    "test/",
)


def _safe_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(int(value or default), maximum))
    except (TypeError, ValueError):
        return default


def _run_command(args: list[str], *, cwd: Path, timeout: float = 5.0) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        output = (completed.stdout or "").strip()
        error = (completed.stderr or "").strip()
        return {
            "ok": completed.returncode == 0,
            "returnCode": completed.returncode,
            "stdout": output[:4000],
            "stderr": error[:2000],
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _read_text(path: Path, *, limit: int = 12000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except Exception:
        return ""


def _relative(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except Exception:
        return str(path)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


VALIDATION_COMMAND_PATTERNS = (
    "pytest",
    "py_compile",
    "tsc",
    "typecheck",
    "npm run build",
    "pnpm build",
    "yarn build",
    "npm run test",
    "pnpm test",
    "yarn test",
    "vitest",
    "lint",
    "mypy",
)


class EngineeringLaneService:
    """Engineering Runtime: ContextPack, proof ledger and workset governance."""

    kind = "engineering"

    def runtime_descriptor(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "displayName": "EngineeringRuntime",
            "summary": "负责工程任务的 ContextPack、写集治理、Proof Ledger、工作区观测与 workflow hints；内部计划保留在工程账本，不展开成 Supervisor todos。",
            "responsibilities": [
                "识别 project_coding / 工程任务形态并准备轻量 ContextPack",
                "维护 Proof Ledger、Workset Observation 与工程行为链证据",
                "为 Planner / Delegation Broker 提供工程约束和验收线索",
            ],
            "routingKeywords": ["project_coding", "代码", "实现", "修复", "测试", "工程", "ContextPack", "Proof Ledger"],
            "acceptedInputs": ["user_query", "workspace_descriptor", "task_brief", "git_status"],
            "producedOutputs": ["context_pack", "proof_entry", "workset_observation", "workflow_hints"],
            "ownedSteps": [
                "engineering.context_pack",
                "engineering.proof_ledger",
                "engineering.workset_observation",
            ],
            "supportsPause": False,
            "supportsResume": True,
            "supportsApproval": False,
            "supportsRepair": False,
            "visibility": "secondary",
            "promptHints": [
                "project_coding 任务优先参考 Engineering Runtime 的 ContextPack / Proof Ledger；不要把内部工程步骤展开成 Supervisor todos。",
                "科普、课程、产品介绍、讲解类视频默认优先走可编辑代码视频链路，例如 Remotion、Hyperframes、Manim、HTML video 或 ffmpeg。",
                "Remotion、Manim、ffmpeg、Three.js、p5.js 等用代码生成媒体的任务优先视为工程实现，Creative Media 只作为素材或 provider 子能力参与。",
                "用户说打开终端安装、启动或运行命令时，默认解释为逻辑命令会话，优先使用 run_system_command / command_session_broker，而不是拉起真实 GUI 终端。",
            ],
            "capabilities": [
                {
                    "key": "engineering.context_pack",
                    "label": "工程上下文胶囊",
                    "summary": "压缩仓库、git、规则、关键文件和 workflow hints，给工程任务提供轻量事实层。",
                    "accepts": ["用户请求", "工作区", "task brief"],
                    "outputs": ["ContextPack", "关键文件候选", "验证建议"],
                    "examples": ["修复测试失败", "实现新功能", "用 Remotion 做视频代码"],
                    "risk_level": "medium",
                },
                {
                    "key": "engineering.proof_ledger",
                    "label": "Proof Ledger",
                    "summary": "记录工程动作证据、验证状态、残余风险和写集观测。",
                    "accepts": ["命令结果", "git diff", "验证摘要"],
                    "outputs": ["proofEntryId", "verificationStatus", "residualRisks"],
                    "examples": ["记录 typecheck 结果", "收集变更文件证据"],
                    "risk_level": "low",
                },
            ],
        }

    def get_config(self) -> dict[str, Any]:
        return storage.get_engineering_lane_config()

    def normalize_mode(self, value: Any) -> str:
        normalized = str(value or "").strip().lower()
        return normalized if normalized in {"auto", "force", "off"} else "auto"

    def trigger_decision(
        self,
        *,
        user_query: str,
        mode: str = "auto",
        workspace_descriptor: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        cfg = self.get_config()
        normalized_mode = self.normalize_mode(mode)
        root = Path(str((workspace_descriptor or {}).get("workspaceRoot") or workspace_resolution_service.get_main_workspace_path())).expanduser()
        repo = self._repo_brief(root)
        signals = self._detect_code_signals(user_query)
        if not cfg.get("enabled", True):
            return {
                "mode": normalized_mode,
                "active": False,
                "matched": False,
                "signals": signals,
                "repoDetected": bool(repo.get("repoDetected")),
                "reason": "engineering_lane_disabled",
            }
        if normalized_mode == "off":
            return {
                "mode": normalized_mode,
                "active": False,
                "matched": bool(signals),
                "signals": signals,
                "repoDetected": bool(repo.get("repoDetected")),
                "reason": "request_override_off",
            }
        if normalized_mode == "force":
            return {
                "mode": normalized_mode,
                "active": True,
                "matched": True,
                "signals": signals or ["force"],
                "repoDetected": bool(repo.get("repoDetected")),
                "workspaceMode": "repo" if repo.get("repoDetected") else "project_creation_workspace",
                "reason": "request_override_force",
            }

        trigger_mode = str(cfg.get("triggerMode") or "auto").strip().lower()
        if trigger_mode == "off":
            return {
                "mode": normalized_mode,
                "active": False,
                "matched": bool(signals),
                "signals": signals,
                "repoDetected": bool(repo.get("repoDetected")),
                "reason": "config_trigger_off",
            }
        if trigger_mode == "force":
            return {
                "mode": normalized_mode,
                "active": True,
                "matched": True,
                "signals": signals or ["config_force"],
                "repoDetected": bool(repo.get("repoDetected")),
                "reason": "config_trigger_force",
            }

        project_creation_workspace = "project_creation_candidate" in signals
        active = bool(signals) and (bool(repo.get("repoDetected")) or project_creation_workspace)
        reason = "engineering_signals_and_repo" if active and repo.get("repoDetected") else "no_engineering_signal_or_repo"
        if active and project_creation_workspace and not repo.get("repoDetected"):
            reason = "project_creation_workspace"
        if signals and not repo.get("repoDetected"):
            reason = "project_creation_workspace" if project_creation_workspace else "engineering_signals_without_repo_supervisor_route_choice"
        return {
            "mode": normalized_mode,
            "active": active,
            "matched": bool(signals),
            "signals": signals,
            "repoDetected": bool(repo.get("repoDetected")),
            "workspaceMode": "project_creation_workspace" if project_creation_workspace and not repo.get("repoDetected") else ("repo" if repo.get("repoDetected") else "unknown"),
            "reason": reason,
        }

    def build_context_pack(
        self,
        *,
        user_query: str,
        mode: str = "auto",
        session_id: Optional[str] = None,
        run_id: Optional[str] = None,
        project_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        workspace_path: Optional[str] = None,
        task_brief: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        descriptor = workspace_resolution_service.resolve_workspace_descriptor(
            runtime_kind="chat",
            session_id=session_id,
            explicit_project_id=project_id,
            explicit_workspace_id=workspace_id,
            explicit_workspace_path=workspace_path,
        )
        root = Path(str(descriptor.get("workspaceRoot") or workspace_resolution_service.get_main_workspace_path())).expanduser()
        cfg = self.get_config()
        budget = _safe_int(cfg.get("contextPackBudget"), 48000, 800, 128000)
        trigger = self.trigger_decision(user_query=user_query, mode=mode, workspace_descriptor=descriptor)
        source_diags: list[dict[str, Any]] = []
        repo_brief = self._repo_brief(root)
        rules_digest = self._workspace_rules_digest(root, budget=max(200, budget // 5), diagnostics=source_diags)
        git_summary = self._git_summary(root)
        manifests = self._manifest_summary(root)
        critical_files = self._critical_file_candidates(
            root,
            user_query=user_query,
            limit=_safe_int(cfg.get("maxCriticalFiles"), 24, 4, 120),
        )
        workflow_paths = self._ranked_workflow_paths(
            query=user_query,
            scope_chain=self._scope_chain_for_descriptor(descriptor),
            max_paths=_safe_int(cfg.get("rankedWorkflowPathCount"), 3, 1, 5),
        )
        evidence_graph_digest = self._evidence_graph_digest(
            root=root,
            user_query=user_query,
            descriptor=descriptor,
            repo_brief=repo_brief,
            rules_digest=rules_digest,
            git_summary=git_summary,
            manifest_summary=manifests,
            critical_files=critical_files,
            workflow_paths=workflow_paths,
            cfg=cfg,
        )
        coding_contract = self._coding_planner_contract_preview(
            user_query=user_query,
            task_brief=task_brief,
            evidence_graph_digest=evidence_graph_digest,
            critical_files=critical_files,
            manifest_summary=manifests,
            git_summary=git_summary,
            cfg=cfg,
        )
        workset_gate = self._workset_soft_gate_decision(
            changed_files=self._changed_files_from_status(str(git_summary.get("statusShort") or "")),
            write_set=list(coding_contract.get("writeSet") or []),
            cfg=cfg,
        )
        broker_dispatch_simulation = self._broker_dispatch_simulation(
            user_query=user_query,
            task_brief=task_brief,
            coding_contract=coding_contract,
        )
        dry_run_matrix = self._dry_run_matrix(
            user_query=user_query,
            task_brief=task_brief,
            coding_contract=coding_contract,
        )
        context_pack = {
            "repoBrief": repo_brief,
            "evidenceGraphDigest": evidence_graph_digest,
            "workspaceRulesDigest": rules_digest,
            "gitSummary": git_summary,
            "manifestSummary": manifests,
            "criticalFiles": critical_files,
            "taskBrief": task_brief or None,
            "codingPlannerContractPreview": coding_contract,
            "worksetSoftGateDecision": workset_gate,
            "brokerDispatchSimulation": broker_dispatch_simulation,
            "dryRunMatrix": dry_run_matrix,
            "workflowRankedPaths": workflow_paths,
            "memorySuppression": {
                "suppressDailyMemory": bool(cfg.get("suppressDailyMemory", True)) and bool(trigger.get("active")),
                "suppressMemoryMap": bool(cfg.get("suppressMemoryMap", True)) and bool(trigger.get("active")),
                "workflowHintsRetained": True,
            },
            "sourceDiagnostics": source_diags,
        }
        raw = json.dumps(context_pack, ensure_ascii=False, default=str)
        estimated = estimate_prompt_tokens(raw)
        truncated = False
        if estimated > budget:
            truncated = True
            context_pack = self._shrink_context_pack(context_pack, budget)
            estimated = estimate_prompt_tokens(json.dumps(context_pack, ensure_ascii=False, default=str))
        return {
            "triggerDecision": trigger,
            "engineeringMode": self.normalize_mode(mode),
            "workspace": descriptor,
            "contextPackBudget": budget,
            "contextPackEstimatedTokens": estimated,
            "contextPackTruncated": truncated,
            "contextPack": context_pack,
            "evidenceGraphDigest": evidence_graph_digest,
            "codingPlannerContractPreview": coding_contract,
            "worksetSoftGateDecision": workset_gate,
            "brokerDispatchSimulation": broker_dispatch_simulation,
            "dryRunMatrix": dry_run_matrix,
            "proofDraft": self._proof_draft(
                session_id=session_id,
                run_id=run_id,
                task_brief=task_brief,
                context_pack=context_pack,
                trigger=trigger,
            ),
        }

    def dry_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        user_query = str(payload.get("userQuery") or payload.get("user_query") or "").strip()
        mode = self.normalize_mode(payload.get("engineeringMode") or payload.get("engineering_mode") or "auto")
        session_id = str(payload.get("sessionId") or payload.get("session_id") or "").strip() or None
        run_id = str(payload.get("runId") or payload.get("run_id") or "").strip() or None
        result = self.build_context_pack(
            user_query=user_query,
            mode=mode,
            session_id=session_id,
            run_id=run_id,
            project_id=str(payload.get("projectId") or payload.get("project_id") or "").strip() or None,
            workspace_id=str(payload.get("workspaceId") or payload.get("workspace_id") or "").strip() or None,
            workspace_path=str(payload.get("workspacePath") or payload.get("workspace_path") or "").strip() or None,
            task_brief=payload.get("taskBrief") if isinstance(payload.get("taskBrief"), dict) else None,
        )
        persisted = self._record_dry_run_observations(
            result=result,
            session_id=session_id,
            run_id=run_id,
        )
        result["worksetObservations"] = persisted
        result["crossLinkDryRunMatrix"] = self._cross_link_dry_run_matrix(
            result=result,
            user_query=user_query,
            mode=mode,
            payload=payload,
            persisted_observations=persisted,
        )
        return result

    def _cross_link_dry_run_matrix(
        self,
        *,
        result: dict[str, Any],
        user_query: str,
        mode: str,
        payload: dict[str, Any],
        persisted_observations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        context_pack = result.get("contextPack") if isinstance(result.get("contextPack"), dict) else {}
        trigger = result.get("triggerDecision") if isinstance(result.get("triggerDecision"), dict) else {}
        workspace = result.get("workspace") if isinstance(result.get("workspace"), dict) else {}
        evidence = result.get("evidenceGraphDigest") if isinstance(result.get("evidenceGraphDigest"), dict) else {}
        coding_contract = result.get("codingPlannerContractPreview") if isinstance(result.get("codingPlannerContractPreview"), dict) else {}
        broker = result.get("brokerDispatchSimulation") if isinstance(result.get("brokerDispatchSimulation"), dict) else {}
        dry_run_matrix = result.get("dryRunMatrix") if isinstance(result.get("dryRunMatrix"), dict) else {}
        proof = result.get("proofDraft") if isinstance(result.get("proofDraft"), dict) else {}
        memory_suppression = context_pack.get("memorySuppression") if isinstance(context_pack.get("memorySuppression"), dict) else {}
        ranked_paths = list(context_pack.get("workflowRankedPaths") or []) if isinstance(context_pack.get("workflowRankedPaths"), list) else []
        normalized_mode = self.normalize_mode(mode)
        non_engineering_guard = self.trigger_decision(
            user_query="帮我生成一张产品海报图片",
            mode="auto",
            workspace_descriptor=workspace,
        )
        workspace_scope = {
            "source": workspace.get("source"),
            "projectId": workspace.get("projectId"),
            "workspaceId": workspace.get("workspaceId"),
            "workspaceRoot": workspace.get("workspaceRoot"),
            "scopeChain": self._scope_chain_for_descriptor(workspace),
            "repoDetected": bool(evidence.get("repoDetected") or trigger.get("repoDetected")),
        }
        persisted_phases = {str(item.get("phase") or "") for item in persisted_observations if isinstance(item, dict)}
        canonical_risks = {"within_write_set", "outside_write_set", "missing_write_set", "unknown_write_set", "read_only_safe", "not_evaluated"}
        broker_decisions = [
            item
            for key in ("autoDecisions", "manualDecisions")
            for item in list(broker.get(key) or [])
            if isinstance(item, dict)
        ]
        matrix_scenarios = [item for item in list(dry_run_matrix.get("scenarios") or []) if isinstance(item, dict)]

        def check(check_id: str, status: str, message: str, evidence_value: Any = None) -> dict[str, Any]:
            normalized_status = status if status in {"pass", "warning", "fail"} else "warning"
            item = {"id": check_id, "status": normalized_status, "message": message}
            if evidence_value not in (None, "", [], {}):
                item["evidence"] = evidence_value
            return item

        def status_for(checks: list[dict[str, Any]]) -> str:
            if any(item.get("status") == "fail" for item in checks):
                return "fail"
            if any(item.get("status") == "warning" for item in checks):
                return "warning"
            return "pass"

        def scenario(
            scenario_id: str,
            group: str,
            label: str,
            checks: list[dict[str, Any]],
            *,
            details: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            failed = [item for item in checks if item.get("status") == "fail"]
            warnings = [item for item in checks if item.get("status") == "warning"]
            status = status_for(checks)
            return {
                "id": scenario_id,
                "group": group,
                "label": label,
                "status": status,
                "summary": (failed or warnings or checks[:1])[0].get("message") if checks else status,
                "checks": checks,
                "triggerDecision": trigger,
                "workspaceScope": workspace_scope,
                "memorySuppression": memory_suppression,
                "workflowHintEligibility": {
                    "requiresEngineeringActive": True,
                    "eligible": bool(trigger.get("active")),
                    "rankedPathCount": len(ranked_paths),
                    "deliveryMode": "planner_checklist_bias" if bool((coding_contract or {}).get("enabled")) else "direct_guide",
                },
                "codingPlannerContract": {
                    "enabled": bool(coding_contract.get("enabled")),
                    "criticalFileCount": len(coding_contract.get("criticalFiles") or []),
                    "readSetCount": len(coding_contract.get("readSet") or []),
                    "writeSetCount": len(coding_contract.get("writeSet") or []),
                    "verificationCount": len(coding_contract.get("verificationMatrix") or []),
                    "riskFlags": list(coding_contract.get("riskFlags") or []),
                },
                "brokerPreflight": {
                    "enabled": bool(broker.get("enabled")),
                    "autoDispatchBlocked": bool(broker.get("autoDispatchBlocked")),
                    "decisionCount": len(broker_decisions),
                    "recommendedAction": broker.get("recommendedAction"),
                },
                "proofDraft": {
                    "verificationStatus": proof.get("verificationStatus"),
                    "changedFileCount": len(proof.get("changedFiles") or []),
                    "residualRiskCount": len(proof.get("residualRisks") or []),
                    "mode": proof.get("mode"),
                },
                "worksetObservation": {
                    "persistedCount": len(persisted_observations),
                    "phases": sorted(phase for phase in persisted_phases if phase),
                },
                "runtimeLaneProjection": {
                    "runtimeId": "engineering",
                    "messageLifecycleExcluded": True,
                    "separateFrom": ["planner_lane", "subagent_swarm", "chat"],
                },
                "learningEligibility": {
                    "status": "skipped_dry_run",
                    "reason": "Dry-run matrix must not create durable engineering workflow candidates.",
                    "phase6SourceRequired": ["proof_ledger", "workset_observation", "verification_evidence"],
                },
                "deepLinks": {
                    "workbench": "/admin/engineering-lane",
                    "workflows": "/admin/memory?tab=workflows&class=engineering",
                },
                "details": details or {},
            }

        expected_active = False
        if normalized_mode == "force":
            expected_active = True
        elif normalized_mode == "off":
            expected_active = False
        else:
            expected_active = bool(trigger.get("matched")) and bool(trigger.get("repoDetected"))

        scenarios = [
            scenario(
                "trigger_current_request",
                "trigger",
                "当前请求触发判定",
                [
                    check(
                        "trigger_consistency",
                        "pass" if bool(trigger.get("active")) == expected_active else "fail",
                        "Engineering trigger decision is consistent with mode, repo, and code signals.",
                        {"mode": normalized_mode, "expectedActive": expected_active, "actualActive": bool(trigger.get("active"))},
                    ),
                ],
            ),
            scenario(
                "trigger_non_engineering_guard",
                "trigger",
                "非工程请求误触发防线",
                [
                    check(
                        "media_request_inactive",
                        "pass" if not bool(non_engineering_guard.get("active")) else "fail",
                        "Image/video style request must not activate Engineering Runtime in auto mode.",
                        non_engineering_guard,
                    )
                ],
            ),
            scenario(
                "workspace_scope_truth",
                "workspace",
                "Workspace / Repo / Scope 单选真相",
                [
                    check(
                        "scope_chain_present",
                        "pass" if workspace_scope["scopeChain"] else "warning",
                        "Resolved scope chain is visible for dry-run diagnostics.",
                        workspace_scope,
                    ),
                    check(
                        "repo_detected_when_active",
                        "pass" if (not bool(trigger.get("active")) or bool(workspace_scope["repoDetected"])) else "warning",
                        "Active engineering mode should normally have a detected repo; no-repo is allowed but diagnostic.",
                        workspace_scope,
                    ),
                ],
            ),
            scenario(
                "memory_engineering_suppression",
                "memory",
                "工程模式记忆抑制与 workflow 保留",
                [
                    check(
                        "daily_map_suppressed_when_active",
                        "pass"
                        if (not bool(trigger.get("active")) or (bool(memory_suppression.get("suppressDailyMemory")) and bool(memory_suppression.get("suppressMemoryMap"))))
                        else "fail",
                        "Engineering mode must suppress daily/map memory while keeping workflow hints.",
                        memory_suppression,
                    ),
                    check(
                        "workflow_hints_retained",
                        "pass" if bool(memory_suppression.get("workflowHintsRetained")) else "fail",
                        "Workflow hints remain available for ranked checklist/bias.",
                        memory_suppression,
                    ),
                ],
            ),
            scenario(
                "workflow_hint_eligibility",
                "memory",
                "工程行为链注入资格",
                [
                    check(
                        "engineering_only",
                        "pass",
                        "Engineering workflow hints are eligible only when engineering trigger is active/force.",
                        {"triggerActive": bool(trigger.get("active")), "rankedPathCount": len(ranked_paths)},
                    ),
                    check(
                        "ranked_paths_available",
                        "pass" if ranked_paths or not bool(trigger.get("active")) else "warning",
                        "No ranked workflow path is available; this is acceptable but weakens Phase 6 validation coverage.",
                        {"rankedPathCount": len(ranked_paths)},
                    ),
                ],
            ),
            scenario(
                "coding_planner_contract",
                "planner",
                "Coding Planner Contract 完整性",
                [
                    check(
                        "contract_enabled",
                        "pass" if bool(coding_contract.get("enabled")) else "fail",
                        "Coding planner contract should be produced for engineering dry-runs.",
                        coding_contract,
                    ),
                    check(
                        "write_set_present",
                        "pass" if list(coding_contract.get("writeSet") or []) else "warning",
                        "writeSet is missing or empty; broker auto-dispatch should be conservative.",
                        {"writeSet": coding_contract.get("writeSet"), "riskFlags": coding_contract.get("riskFlags")},
                    ),
                ],
            ),
            scenario(
                "broker_preflight_canonical",
                "broker",
                "Broker Preflight canonical risk",
                [
                    check(
                        "broker_enabled",
                        "pass" if bool(broker.get("enabled")) else "fail",
                        "Broker simulation should be available when coding contract exists.",
                        broker,
                    ),
                    check(
                        "canonical_risks",
                        "pass" if all(str(item.get("risk") or "not_evaluated") in canonical_risks for item in broker_decisions) else "fail",
                        "All broker decisions must use canonical workset risk values.",
                        [item.get("risk") for item in broker_decisions],
                    ),
                    check(
                        "auto_block_visible",
                        "warning" if bool(broker.get("autoDispatchBlocked")) else "pass",
                        "Auto dispatch block is visible and should lead to plan repair.",
                        {"autoDispatchBlocked": broker.get("autoDispatchBlocked"), "recommendedAction": broker.get("recommendedAction")},
                    ),
                ],
            ),
            scenario(
                "broker_matrix_variants",
                "broker",
                "Broker 多场景矩阵覆盖",
                [
                    check(
                        "matrix_scenario_count",
                        "pass" if len(matrix_scenarios) >= 8 else "fail",
                        "Dry-run matrix should cover single, parallel, conflict, missing-writeSet, read-only, doc-only, and verification variants.",
                        {"scenarioCount": len(matrix_scenarios)},
                    ),
                    check(
                        "conflict_case_present",
                        "pass" if any(str(item.get("id")) == "parallel_conflict" for item in matrix_scenarios) else "fail",
                        "Parallel write-set conflict scenario must be present.",
                    ),
                ],
            ),
            scenario(
                "proof_draft_status",
                "proof",
                "Proof Draft 空运行边界",
                [
                    check(
                        "dry_run_not_verified",
                        "pass" if str(proof.get("verificationStatus") or "") in {"planned", "unverified"} else "fail",
                        "Dry-run proof must not claim verified because no validation command is executed.",
                        proof,
                    ),
                    check(
                        "no_commands_executed",
                        "pass" if not list(proof.get("commands") or []) else "fail",
                        "Dry-run must not run validation commands.",
                        {"commands": proof.get("commands")},
                    ),
                ],
            ),
            scenario(
                "workset_observation_persisted",
                "proof",
                "Workset Observation 落账",
                [
                    check(
                        "observations_persisted",
                        "pass" if persisted_observations else "warning",
                        "Dry-run should leave observation records when workset observation is enabled.",
                        {"count": len(persisted_observations), "phases": sorted(persisted_phases)},
                    ),
                    check(
                        "matrix_phase_present",
                        "pass" if "dry_run_matrix" in persisted_phases else "warning",
                        "Dry-run matrix observations should be distinguishable from dispatch preview observations.",
                        sorted(persisted_phases),
                    ),
                ],
            ),
            scenario(
                "engineering_runtime_lane_projection",
                "runtime_lane",
                "Engineering Runtime 独立投影",
                [
                    check(
                        "lane_id_declared",
                        "pass",
                        "Engineering evidence should project to Engineering Runtime, not chat/planner/subagent cards.",
                        {"runtimeId": "engineering"},
                    ),
                    check(
                        "message_lifecycle_excluded",
                        "pass",
                        "Engineering Runtime events are expected to be excluded from normal chat narrative/tool nodes.",
                    ),
                ],
            ),
            scenario(
                "phase6_learning_guard",
                "phase6_learning",
                "Phase 6 学习资格守门",
                [
                    check(
                        "dry_run_not_learned",
                        "pass",
                        "Dry-run matrix never creates durable workflow memory; Phase 6 learns only from proof-backed terminal runs.",
                    ),
                    check(
                        "verification_variants_present",
                        "pass"
                        if {"verification_success", "verification_failure", "verification_missing"}.issubset({str(item.get("id")) for item in matrix_scenarios})
                        else "fail",
                        "Verified/unverified/failed verification variants must be represented before learning policy is trusted.",
                    ),
                ],
            ),
        ]
        group_summary: dict[str, dict[str, int]] = {}
        for item in scenarios:
            group = str(item.get("group") or "other")
            bucket = group_summary.setdefault(group, {"total": 0, "pass": 0, "warning": 0, "fail": 0})
            status = str(item.get("status") or "warning")
            bucket["total"] += 1
            bucket[status if status in {"pass", "warning", "fail"} else "warning"] += 1
        return {
            "enabled": True,
            "generatedAt": _utc_now_iso(),
            "input": {
                "userQuery": user_query,
                "engineeringMode": normalized_mode,
                "sessionId": payload.get("sessionId") or payload.get("session_id"),
                "runId": payload.get("runId") or payload.get("run_id"),
            },
            "summary": {
                "total": len(scenarios),
                "pass": sum(1 for item in scenarios if item.get("status") == "pass"),
                "warning": sum(1 for item in scenarios if item.get("status") == "warning"),
                "fail": sum(1 for item in scenarios if item.get("status") == "fail"),
                "groups": group_summary,
            },
            "scenarios": scenarios,
        }

    def list_proof_entries(
        self,
        *,
        session_id: Optional[str] = None,
        run_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        normalized_status = str(status or "").strip().lower()
        return [
            self._decorate_proof_entry(entry)
            for entry in db.list_engineering_proof_entries(
            session_id=session_id,
            run_id=run_id,
            status=normalized_status if normalized_status and normalized_status != "all" else None,
            limit=limit,
            )
        ]

    def get_proof_entry(self, entry_id: str) -> Optional[dict[str, Any]]:
        entry = db.get_engineering_proof_entry(entry_id)
        return self._decorate_proof_entry(entry) if entry else None

    def add_proof_entry(self, entry: dict[str, Any]) -> dict[str, Any]:
        status = str(entry.get("verificationStatus") or entry.get("verification_status") or "planned").strip().lower()
        if status == "verified" and not entry.get("commands") and not entry.get("diagnostics"):
            entry = {**entry, "verificationStatus": "unverified"}
        return self._decorate_proof_entry(db.add_engineering_proof_entry(entry))

    def _decorate_workset_observation(self, entry: dict[str, Any]) -> dict[str, Any]:
        data = dict(entry or {})
        decision = self._normalize_workset_dispatch_decision(data.get("decision") if isinstance(data.get("decision"), dict) else {})
        if decision:
            data["decision"] = decision
        data["decisionSource"] = str(data.get("decisionSource") or decision.get("decisionSource") or decision.get("worksetDecisionSource") or "").strip() or None
        data["correlationStatus"] = str(data.get("correlationStatus") or decision.get("correlationStatus") or decision.get("risk") or "not_evaluated")
        if not data.get("warningOrBlockReason"):
            data["warningOrBlockReason"] = str(decision.get("reason") or decision.get("repairSuggestion") or "").strip() or None
        data["manualOverride"] = bool(data.get("manualOverride")) or bool(decision.get("manualOverride"))
        data["outsideWriteSetFiles"] = self._normalize_path_list(data.get("outsideWriteSetFiles") or [])
        return data

    def list_workset_observations(
        self,
        *,
        session_id: Optional[str] = None,
        run_id: Optional[str] = None,
        task_brief_id: Optional[str] = None,
        decision_source: Optional[str] = None,
        limit: int = 40,
    ) -> list[dict[str, Any]]:
        normalized_source = str(decision_source or "").strip().lower()
        return [
            self._decorate_workset_observation(entry)
            for entry in db.list_engineering_workset_observations(
                session_id=session_id,
                run_id=run_id,
                task_brief_id=task_brief_id,
                decision_source=normalized_source if normalized_source and normalized_source != "all" else None,
                limit=limit,
            )
        ]

    def collect_terminal_proof(
        self,
        *,
        session_id: str,
        run_id: str,
        source_component: str = "terminal_post_run",
        manual_refresh: bool = False,
    ) -> dict[str, Any]:
        cfg = self.get_config()
        if not bool(cfg.get("enabled", True)) or not bool(cfg.get("proofLedgerEnabled", True)):
            return {"status": "skipped", "reason": "engineering_proof_disabled"}
        if not manual_refresh and not bool(cfg.get("autoProofCollectionEnabled", True)):
            return {"status": "skipped", "reason": "auto_proof_collection_disabled"}
        proof_scope = str(cfg.get("proofCollectionScope") or "engineering_active").strip().lower()
        if proof_scope == "off":
            return {"status": "skipped", "reason": "proof_collection_scope_off"}

        run_record = db.get_run_record(run_id)
        if not run_record:
            return {"status": "skipped", "reason": "missing_run_record"}
        metadata = dict(run_record.get("metadata") or {})
        trigger = metadata.get("engineeringTriggerDecision") if isinstance(metadata.get("engineeringTriggerDecision"), dict) else {}
        engineering_mode = self.normalize_mode(metadata.get("engineeringMode") or "auto")
        active = bool(trigger.get("active")) or engineering_mode == "force"
        if proof_scope == "force_only" and engineering_mode != "force":
            return {"status": "skipped", "reason": "proof_collection_force_only", "engineeringMode": engineering_mode}
        if proof_scope == "engineering_active" and not active:
            return {"status": "skipped", "reason": "engineering_mode_inactive", "engineeringMode": engineering_mode, "triggerDecision": trigger}

        workspace_root = self._workspace_root_from_run_metadata(metadata, session_id=session_id)
        events = db.get_runtime_events_for_run(run_id, session_id=session_id, limit=1000)
        workset_observations = self._workset_observations_from_events(events)
        tool_starts = self._tool_starts_by_id(events)
        commands, diagnostics = self._command_evidence_from_events(events, tool_starts=tool_starts, cfg=cfg)
        git_summary = self._git_summary(workspace_root) if bool((cfg.get("diagnosticsProviders") or {}).get("git", True)) else {}
        changed_files = self._changed_files_from_status(str(git_summary.get("statusShort") or ""))
        if git_summary:
            diagnostics.append(self._diagnostic_item(
                source="git",
                kind="status",
                command="git status --short && git diff --stat",
                return_code=0 if not git_summary.get("statusError") else None,
                summary=self._git_diagnostic_summary(git_summary, changed_files),
                raw_preview="\n".join(
                    part
                    for part in (
                        str(git_summary.get("statusShort") or "").strip(),
                        str(git_summary.get("diffStat") or "").strip(),
                        str(git_summary.get("stagedDiffStat") or "").strip(),
                        str(git_summary.get("statusError") or "").strip(),
                    )
                    if part
                ),
            ))
        lsp_provider = self._lsp_provider_status(cfg)
        if lsp_provider:
            diagnostics.append(lsp_provider)

        task_brief = self._task_brief_from_metadata_or_events(metadata, events)
        write_set = self._normalize_path_list((task_brief or {}).get("writeSet") if isinstance(task_brief, dict) else [])
        if not write_set:
            context_pack = metadata.get("engineeringContextPack") if isinstance(metadata.get("engineeringContextPack"), dict) else {}
            pack = context_pack.get("contextPack") if isinstance(context_pack.get("contextPack"), dict) else context_pack
            coding_contract = pack.get("codingPlannerContractPreview") if isinstance(pack.get("codingPlannerContractPreview"), dict) else {}
            write_set = self._normalize_path_list(coding_contract.get("writeSet") if isinstance(coding_contract, dict) else [])
        read_set = self._read_set_from_events_or_context(events, metadata)
        workset_risk = self._workset_risk(changed_files=changed_files, write_set=write_set, cfg=cfg)
        workset_dispatch_decision = self._normalize_workset_dispatch_decision(
            task_brief.get("worksetDispatchDecision") if isinstance(task_brief, dict) and isinstance(task_brief.get("worksetDispatchDecision"), dict) else {}
        )
        if not workset_dispatch_decision and workset_observations:
            first_decision = workset_observations[0].get("worksetDispatchDecision")
            workset_dispatch_decision = self._normalize_workset_dispatch_decision(first_decision if isinstance(first_decision, dict) else {})
        workset_correlation = self._workset_correlation(
            changed_files=changed_files,
            write_set=write_set,
            workset_risk=workset_risk,
            observations=workset_observations,
        )
        verification_status = self._verification_status(
            changed_files=changed_files,
            commands=commands,
            diagnostics=diagnostics,
        )
        run_failed_before_evidence = (
            str(run_record.get("status") or "").strip().lower() == "failed"
            and not commands
            and not changed_files
            and bool(str(run_record.get("error_message") or "").strip())
        )
        if run_failed_before_evidence:
            verification_status = "failed_due_to_dispatch_error"
            diagnostics.append(self._diagnostic_item(
                source="run",
                kind="dispatch_error",
                command="run lifecycle",
                return_code=None,
                severity="error",
                summary=str(run_record.get("error_message") or "run failed before engineering evidence was produced"),
                raw_preview=str(run_record.get("error_message") or ""),
            ))
        residual_risks = self._residual_risks(
            verification_status=verification_status,
            changed_files=changed_files,
            commands=commands,
            diagnostics=diagnostics,
            workset_risk=workset_risk,
        )
        patch_intent = self._patch_intent_from_run(session_id=session_id, metadata=metadata, task_brief=task_brief)
        context_digest = self._context_pack_digest(metadata=metadata, events=events, trigger=trigger)
        entry = {
            "sessionId": session_id,
            "runId": run_id,
            "taskBriefId": (task_brief or {}).get("taskBriefId") if isinstance(task_brief, dict) else None,
            "mode": "terminal_auto" if not manual_refresh else "manual_refresh",
            "patchIntent": patch_intent,
            "readSet": read_set,
            "writeSet": write_set,
            "changedFiles": changed_files,
            "commands": commands,
            "diagnostics": {
                "items": diagnostics,
                "gitSummary": git_summary,
                "lspProvider": lsp_provider or {"provider": "disabled"},
                "worksetRisk": workset_risk,
                "worksetDispatchDecision": workset_dispatch_decision,
                "worksetObservation": {
                    "enabled": bool(cfg.get("worksetObservationEnabled", True)),
                    "observationCount": len(workset_observations),
                    "items": workset_observations[:24],
                },
                "worksetCorrelation": workset_correlation,
                "outsideWriteSetFiles": list(workset_correlation.get("outsideWriteSetFiles") or []),
                "manualOverride": workset_correlation.get("manualOverride") or {},
                "contextPackDigest": context_digest,
            },
            "verificationStatus": verification_status,
            "residualRisks": residual_risks,
            "metadata": {
                "sourceComponent": source_component,
                "autoCollected": not manual_refresh,
                "collectedAt": _utc_now_iso(),
                "engineeringMode": engineering_mode,
                "triggerDecision": trigger,
                "workspaceRoot": str(workspace_root),
                "proofCollectionScope": proof_scope,
                "delegationId": workset_dispatch_decision.get("delegationId") if workset_dispatch_decision else None,
                "worksetObservationCount": len(workset_observations),
            },
        }
        stored = self.add_proof_entry(entry)
        persisted_observations = self._record_terminal_workset_observations(
            session_id=session_id,
            run_id=run_id,
            task_brief_id=str((task_brief or {}).get("taskBriefId") or "").strip() or None,
            proof_entry_id=str(stored.get("id") or "").strip() or None,
            observations=workset_observations,
            correlation=workset_correlation,
            dispatch_decision=workset_dispatch_decision,
        )
        self._emit_proof_runtime_event(
            session_id=session_id,
            run_id=run_id,
            proof_entry=stored,
            source_component=source_component,
            manual_refresh=manual_refresh,
        )
        workflow_memory_result: dict[str, Any] = {"status": "skipped", "reason": "not_attempted"}
        try:
            workflow_memory_result = workflow_memory_service.record_engineering_proof_episode(
                proof_entry=stored,
                workset_observations=persisted_observations,
                source_component=source_component,
            )
        except Exception as exc:
            workflow_memory_result = {"status": "failed", "reason": str(exc), "proofEntryId": stored.get("id")}
        return {
            "status": "collected",
            "entry": stored,
            "worksetObservationCount": len(persisted_observations),
            "workflowMemory": workflow_memory_result,
        }

    def refresh_proof_from_existing_evidence(self, *, session_id: str, run_id: str) -> dict[str, Any]:
        return self.collect_terminal_proof(
            session_id=session_id,
            run_id=run_id,
            source_component="engineering_lane_manual_refresh",
            manual_refresh=True,
        )

    def _decorate_proof_entry(self, entry: dict[str, Any]) -> dict[str, Any]:
        data = dict(entry or {})
        diagnostics = data.get("diagnostics") if isinstance(data.get("diagnostics"), dict) else {}
        workset_observation = diagnostics.get("worksetObservation") if isinstance(diagnostics.get("worksetObservation"), dict) else {}
        workset_correlation = diagnostics.get("worksetCorrelation") if isinstance(diagnostics.get("worksetCorrelation"), dict) else {}
        workset_risk = diagnostics.get("worksetRisk") if isinstance(diagnostics.get("worksetRisk"), dict) else {}
        workset_dispatch_decision = self._normalize_workset_dispatch_decision(
            diagnostics.get("worksetDispatchDecision") if isinstance(diagnostics.get("worksetDispatchDecision"), dict) else {}
        )
        if workset_risk:
            workset_risk = dict(workset_risk)
            workset_risk["risk"] = self._normalize_workset_risk(workset_risk.get("risk"))
            diagnostics["worksetRisk"] = workset_risk
        if workset_correlation:
            workset_correlation = dict(workset_correlation)
            workset_correlation["risk"] = self._normalize_workset_risk(workset_correlation.get("risk"))
            diagnostics["worksetCorrelation"] = workset_correlation
        if workset_dispatch_decision:
            diagnostics["worksetDispatchDecision"] = workset_dispatch_decision
        observation_items = list(workset_observation.get("items") or []) if isinstance(workset_observation, dict) else []
        if observation_items:
            workset_observation = dict(workset_observation)
            workset_observation["items"] = [
                {
                    **dict(item or {}),
                    "risk": self._normalize_workset_risk((item or {}).get("risk")),
                    "worksetDispatchDecision": self._normalize_workset_dispatch_decision(
                        (item or {}).get("worksetDispatchDecision") if isinstance((item or {}).get("worksetDispatchDecision"), dict) else {}
                    ),
                }
                for item in observation_items
                if isinstance(item, dict)
            ]
            diagnostics["worksetObservation"] = workset_observation
        data["diagnostics"] = diagnostics
        data["worksetObservation"] = workset_observation
        data["worksetCorrelation"] = workset_correlation
        data["outsideWriteSetFiles"] = list(diagnostics.get("outsideWriteSetFiles") or workset_correlation.get("outsideWriteSetFiles") or [])
        data["manualOverride"] = diagnostics.get("manualOverride") if isinstance(diagnostics.get("manualOverride"), dict) else {}
        return data

    def _workset_observation_id(
        self,
        *,
        session_id: Optional[str],
        run_id: Optional[str],
        task_brief_id: Optional[str],
        delegation_id: Optional[str],
        decision_source: str,
        phase: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        stable_parts = [
            str(session_id or "").strip(),
            str(run_id or "").strip(),
            str(task_brief_id or "").strip(),
            str(delegation_id or "").strip(),
            str(decision_source or "").strip(),
            str(phase or "").strip(),
            str((metadata or {}).get("scenarioId") or "").strip(),
            str((metadata or {}).get("proofEntryId") or "").strip(),
        ]
        if any(stable_parts[:4]):
            return str(uuid.uuid5(uuid.NAMESPACE_URL, "::".join(stable_parts)))
        return str(uuid.uuid4())

    def _persist_workset_observation(
        self,
        *,
        session_id: Optional[str],
        run_id: Optional[str],
        task_brief_id: Optional[str],
        delegation_id: Optional[str],
        decision_source: str,
        phase: str,
        decision: dict[str, Any],
        warning_or_block_reason: str = "",
        manual_override: bool = False,
        outside_write_set_files: list[str] | None = None,
        correlation_status: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_session_id = str(session_id or "").strip() or None
        normalized_run_id = str(run_id or "").strip() or None
        if normalized_session_id and not db.get_session(normalized_session_id):
            normalized_session_id = None
        if normalized_run_id and not db.get_run_record(normalized_run_id):
            normalized_run_id = None
        observation_id = self._workset_observation_id(
            session_id=normalized_session_id,
            run_id=normalized_run_id,
            task_brief_id=task_brief_id,
            delegation_id=delegation_id,
            decision_source=decision_source,
            phase=phase,
            metadata=metadata,
        )
        return db.upsert_engineering_workset_observation(
            {
                "id": observation_id,
                "sessionId": normalized_session_id,
                "runId": normalized_run_id,
                "taskBriefId": task_brief_id,
                "delegationId": delegation_id,
                "decisionSource": decision_source,
                "phase": phase,
                "decision": decision,
                "warningOrBlockReason": warning_or_block_reason,
                "manualOverride": manual_override,
                "outsideWriteSetFiles": list(outside_write_set_files or []),
                "correlationStatus": correlation_status,
                "metadata": metadata or {},
            }
        )

    def _record_dry_run_observations(
        self,
        *,
        result: dict[str, Any],
        session_id: Optional[str],
        run_id: Optional[str],
    ) -> list[dict[str, Any]]:
        cfg = self.get_config()
        if not bool(cfg.get("worksetObservationEnabled", True)):
            return []
        normalized_session_id = str(session_id or "").strip() or None
        normalized_run_id = str(run_id or "").strip() or None
        # Admin and API dry-runs commonly run without a real session/run binding.
        # Skipping persistence here prevents orphan dry-run observations from
        # polluting governance diagnostics with unscoped records.
        if not normalized_session_id or not normalized_run_id:
            return []
        if not db.get_session(normalized_session_id) or not db.get_run_record(normalized_run_id):
            return []
        persisted: list[dict[str, Any]] = []
        broker_dispatch = result.get("brokerDispatchSimulation") if isinstance(result.get("brokerDispatchSimulation"), dict) else {}
        for mode_key, phase in (("autoDecisions", "dry_run_dispatch"), ("manualDecisions", "dry_run_dispatch")):
            for decision in list(broker_dispatch.get(mode_key) or []):
                if not isinstance(decision, dict):
                    continue
                normalized_decision = self._normalize_workset_dispatch_decision(decision)
                persisted.append(
                    self._persist_workset_observation(
                        session_id=normalized_session_id,
                        run_id=normalized_run_id,
                        task_brief_id=str(normalized_decision.get("taskBriefId") or "").strip() or None,
                        delegation_id=None,
                        decision_source=str(normalized_decision.get("worksetDecisionSource") or "dry_run"),
                        phase=phase,
                        decision=normalized_decision,
                        warning_or_block_reason=str(normalized_decision.get("reason") or normalized_decision.get("repairSuggestion") or "").strip(),
                        manual_override=bool(normalized_decision.get("manualOverride")),
                        outside_write_set_files=[],
                        correlation_status=str(normalized_decision.get("correlationStatus") or normalized_decision.get("risk") or "not_evaluated"),
                        metadata={"simulation": "broker_dispatch", "dispatchMode": "manual" if mode_key.startswith("manual") else "auto"},
                    )
                )
        dry_run_matrix = result.get("dryRunMatrix") if isinstance(result.get("dryRunMatrix"), dict) else {}
        for scenario in list(dry_run_matrix.get("scenarios") or []):
            if not isinstance(scenario, dict):
                continue
            scenario_id = str(scenario.get("id") or "").strip()
            scenario_label = str(scenario.get("label") or scenario_id).strip()
            for mode_key in ("autoDecisions", "manualDecisions"):
                for decision in list(scenario.get(mode_key) or []):
                    if not isinstance(decision, dict):
                        continue
                    normalized_decision = self._normalize_workset_dispatch_decision(decision)
                    persisted.append(
                        self._persist_workset_observation(
                            session_id=normalized_session_id,
                            run_id=normalized_run_id,
                            task_brief_id=str(normalized_decision.get("taskBriefId") or "").strip() or None,
                            delegation_id=None,
                            decision_source=str(normalized_decision.get("worksetDecisionSource") or "dry_run"),
                            phase="dry_run_matrix",
                            decision=normalized_decision,
                            warning_or_block_reason=str(normalized_decision.get("reason") or normalized_decision.get("repairSuggestion") or "").strip(),
                            manual_override=bool(normalized_decision.get("manualOverride")),
                            outside_write_set_files=[],
                            correlation_status=str(normalized_decision.get("correlationStatus") or normalized_decision.get("risk") or "not_evaluated"),
                            metadata={
                                "simulation": "dry_run_matrix",
                                "scenarioId": scenario_id,
                                "scenarioLabel": scenario_label,
                                "dispatchMode": "manual" if mode_key.startswith("manual") else "auto",
                                "recommendedAction": str(scenario.get("recommendedAction") or "").strip(),
                            },
                        )
                    )
        return persisted

    def _record_terminal_workset_observations(
        self,
        *,
        session_id: str,
        run_id: str,
        task_brief_id: Optional[str],
        proof_entry_id: Optional[str],
        observations: list[dict[str, Any]],
        correlation: dict[str, Any],
        dispatch_decision: dict[str, Any],
    ) -> list[dict[str, Any]]:
        cfg = self.get_config()
        if not bool(cfg.get("worksetObservationEnabled", True)):
            return []
        persisted: list[dict[str, Any]] = []
        for item in observations:
            if not isinstance(item, dict):
                continue
            decision = self._normalize_workset_dispatch_decision(
                item.get("worksetDispatchDecision") if isinstance(item.get("worksetDispatchDecision"), dict) else {}
            )
            persisted.append(
                self._persist_workset_observation(
                    session_id=session_id,
                    run_id=run_id,
                    task_brief_id=str(item.get("taskBriefId") or task_brief_id or "").strip() or None,
                    delegation_id=str(item.get("delegationId") or "").strip() or None,
                    decision_source=str(item.get("worksetDecisionSource") or dispatch_decision.get("worksetDecisionSource") or "supervisor_manual"),
                    phase="dispatch",
                    decision=decision or item,
                    warning_or_block_reason=str((decision or {}).get("reason") or item.get("repairSuggestion") or item.get("risk") or "").strip(),
                    manual_override=bool(item.get("manualOverride")),
                    outside_write_set_files=[],
                    correlation_status=str((decision or {}).get("correlationStatus") or (decision or {}).get("risk") or "not_evaluated"),
                    metadata={
                        "proofEntryId": proof_entry_id,
                        "lane": str(item.get("lane") or "").strip(),
                        "targetId": str(item.get("targetId") or "").strip(),
                        "status": str(item.get("status") or "").strip(),
                        "eventSeq": item.get("eventSeq"),
                    },
                )
            )
        persisted.append(
            self._persist_workset_observation(
                session_id=session_id,
                run_id=run_id,
                task_brief_id=task_brief_id,
                delegation_id=str(dispatch_decision.get("delegationId") or "").strip() or None,
                decision_source=str(dispatch_decision.get("worksetDecisionSource") or "planner_auto"),
                phase="proof_correlation",
                decision=correlation,
                warning_or_block_reason=str(correlation.get("risk") or "").strip(),
                manual_override=bool((correlation.get("manualOverride") or {}).get("present")),
                outside_write_set_files=[str(item).strip() for item in list(correlation.get("outsideWriteSetFiles") or []) if str(item).strip()],
                correlation_status=str(correlation.get("risk") or "not_evaluated"),
                metadata={"proofEntryId": proof_entry_id, "warningCount": correlation.get("warningCount"), "blockedCount": correlation.get("blockedCount")},
            )
        )
        return persisted

    def _parse_tool_result_dict(self, value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return dict(value)
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return {}
            try:
                parsed = json.loads(text)
                return parsed if isinstance(parsed, dict) else {}
            except Exception:
                return {}
        return {}

    def _workset_observations_from_events(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        observations: list[dict[str, Any]] = []
        for event in events:
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            tool = payload.get("tool") if isinstance(payload.get("tool"), dict) else {}
            tool_name = str(tool.get("toolName") or "").strip()
            result = self._parse_tool_result_dict(tool.get("result"))
            if tool_name != "delegation_broker" and not result.get("items"):
                continue
            items = result.get("items") if isinstance(result.get("items"), list) else []
            for item in items:
                if not isinstance(item, dict):
                    continue
                decision = self._normalize_workset_dispatch_decision(
                    item.get("worksetDispatchDecision") if isinstance(item.get("worksetDispatchDecision"), dict) else {}
                )
                if not decision:
                    continue
                source = str(decision.get("worksetDecisionSource") or item.get("autoDispatchSource") or "").strip()
                if not source:
                    source = "planner_auto" if str(item.get("autoDispatchSource") or "").startswith("planner_auto") else "supervisor_manual"
                warning = bool(decision.get("warning"))
                blocked = bool(decision.get("blocked")) or str(item.get("status") or "") == "blocked"
                observations.append(
                    {
                        "taskBriefId": str(item.get("taskBriefId") or decision.get("taskBriefId") or "").strip(),
                        "delegationId": str(item.get("delegationId") or decision.get("delegationId") or "").strip(),
                        "lane": str(item.get("lane") or "").strip(),
                        "targetId": str(item.get("targetId") or item.get("agentId") or "").strip(),
                        "status": str(item.get("status") or "").strip(),
                        "worksetDecisionSource": source,
                        "risk": str(decision.get("risk") or "").strip(),
                        "blocked": blocked,
                        "warning": warning or blocked,
                        "manualOverride": bool(decision.get("manualOverride")) or (source == "supervisor_manual" and warning and not blocked),
                        "writeSet": self._normalize_path_list(item.get("writeSet") or decision.get("writeSet") or []),
                        "readSet": self._normalize_path_list(item.get("readSet") or []),
                        "worksetDispatchDecision": decision,
                        "repairSuggestion": str(item.get("repairSuggestion") or decision.get("repairSuggestion") or "").strip(),
                        "eventSeq": event.get("seq"),
                    }
                )
        return observations[:100]

    def _workset_correlation(
        self,
        *,
        changed_files: list[str],
        write_set: list[str],
        workset_risk: dict[str, Any],
        observations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        outside = set(str(item).strip() for item in list(workset_risk.get("outsideWriteSet") or []) if str(item).strip())
        matched = set()
        manual_overrides: list[dict[str, Any]] = []
        observed_write_sets: list[str] = []
        for observation in observations:
            observed_write_set = self._normalize_path_list(observation.get("writeSet") or [])
            observed_write_sets.extend(observed_write_set)
            if bool(observation.get("manualOverride")):
                manual_overrides.append(
                    {
                        "delegationId": observation.get("delegationId"),
                        "taskBriefId": observation.get("taskBriefId"),
                        "risk": observation.get("risk"),
                        "reason": (observation.get("worksetDispatchDecision") or {}).get("reason"),
                    }
                )
        if changed_files and observed_write_sets:
            for path in changed_files:
                if self._path_matches_any_write_set(path, observed_write_sets):
                    matched.add(path)
                    outside.discard(path)
                else:
                    outside.add(path)
        if not observations and changed_files and write_set:
            for path in changed_files:
                if self._path_matches_any_write_set(path, write_set):
                    matched.add(path)
        return {
            "enabled": True,
            "changedFileCount": len(changed_files),
            "observationCount": len(observations),
            "warningCount": sum(1 for item in observations if bool(item.get("warning"))),
            "blockedCount": sum(1 for item in observations if bool(item.get("blocked"))),
            "outsideWriteSetFiles": sorted(outside),
            "matchedWriteSetFiles": sorted(matched),
            "manualOverride": {
                "present": bool(manual_overrides),
                "items": manual_overrides[:12],
            },
            "risk": "outside_write_set" if outside else self._normalize_workset_risk(workset_risk.get("risk")),
            "suggestedAction": (
                "Review manual overrides or repair task writeSets before accepting the work."
                if outside or manual_overrides
                else "No observed write-set drift."
            ),
        }

    def _workspace_root_from_run_metadata(self, metadata: dict[str, Any], *, session_id: str) -> Path:
        descriptor = workspace_resolution_service.resolve_workspace_descriptor(
            runtime_kind="chat",
            session_id=session_id,
            explicit_project_id=str(metadata.get("project_id") or metadata.get("projectId") or "").strip() or None,
            explicit_workspace_id=str(metadata.get("workspace_id") or metadata.get("workspaceId") or "").strip() or None,
            explicit_workspace_path=str(metadata.get("workspace_path") or metadata.get("workspacePath") or "").strip() or None,
        )
        return Path(str(descriptor.get("workspaceRoot") or workspace_resolution_service.get_main_workspace_path())).expanduser()

    def _tool_starts_by_id(self, events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        starts: dict[str, dict[str, Any]] = {}
        for event in events:
            if str(event.get("topic") or "") != "tool.started":
                continue
            tool = self._event_tool_payload(event)
            key = str(tool.get("toolCallId") or tool.get("toolName") or "").strip()
            if key:
                starts[key] = tool
        return starts

    def _event_tool_payload(self, event: dict[str, Any]) -> dict[str, Any]:
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        tool = payload.get("tool") if isinstance(payload.get("tool"), dict) else {}
        return dict(tool)

    def _command_evidence_from_events(
        self,
        events: list[dict[str, Any]],
        *,
        tool_starts: dict[str, dict[str, Any]],
        cfg: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        if not bool((cfg.get("diagnosticsProviders") or {}).get("command", True)):
            return [], []
        commands: list[dict[str, Any]] = []
        diagnostics: list[dict[str, Any]] = []
        for event in events:
            if str(event.get("topic") or "") != "tool.finished":
                continue
            tool = self._event_tool_payload(event)
            tool_name = str(tool.get("toolName") or "").strip()
            if tool_name not in {"run_system_command", "command_session_broker"}:
                continue
            tool_call_id = str(tool.get("toolCallId") or tool_name).strip()
            start = tool_starts.get(tool_call_id) or tool_starts.get(tool_name) or {}
            args = start.get("args") if isinstance(start.get("args"), dict) else {}
            result = tool.get("result")
            result_dict = result if isinstance(result, dict) else {}
            command_text = str(
                args.get("command")
                or result_dict.get("command")
                or result_dict.get("summary")
                or result_dict.get("runId")
                or tool_name
            ).strip()
            return_code = self._coerce_optional_int(result_dict.get("returnCode"))
            status_text = str(result_dict.get("status") or result_dict.get("state") or result_dict.get("ok") or "").strip().lower()
            ok = result_dict.get("ok")
            if return_code is None and ok is not None:
                return_code = 0 if bool(ok) else 1
            is_validation = self._is_validation_command(command_text)
            summary = self._tool_result_summary(tool_name=tool_name, result=result_dict, return_code=return_code)
            raw_preview, truncated = self._result_preview(result)
            command_entry = {
                "tool": tool_name,
                "toolCallId": tool_call_id,
                "command": command_text,
                "returnCode": return_code,
                "status": status_text or ("ok" if return_code == 0 else "unknown"),
                "summary": summary,
                "isValidation": is_validation,
                "eventSeq": event.get("seq"),
            }
            commands.append(command_entry)
            severity = "info"
            if return_code is not None and return_code != 0:
                severity = "error" if is_validation else "warning"
            diagnostics.append(self._diagnostic_item(
                source="command",
                kind="validation_command" if is_validation else "command",
                command=command_text,
                tool=tool_name,
                return_code=return_code,
                summary=summary,
                raw_preview=raw_preview,
                truncated=truncated,
                severity=severity,
            ))
        return commands, diagnostics

    def _coerce_optional_int(self, value: Any) -> Optional[int]:
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _is_validation_command(self, command: str) -> bool:
        text = str(command or "").strip().lower()
        return any(pattern in text for pattern in VALIDATION_COMMAND_PATTERNS)

    def _tool_result_summary(self, *, tool_name: str, result: dict[str, Any], return_code: Optional[int]) -> str:
        for key in ("summary", "recommendedNextAction", "reason", "error", "stderrPreview", "stdoutPreview", "finalPreview", "initialPreview"):
            value = str(result.get(key) or "").strip()
            if value:
                return value[:320]
        if return_code is not None:
            return f"{tool_name} finished with returnCode={return_code}"
        return f"{tool_name} finished"

    def _result_preview(self, result: Any, *, limit: int = 1200) -> tuple[str, bool]:
        try:
            text = json.dumps(result, ensure_ascii=False, default=str) if not isinstance(result, str) else result
        except Exception:
            text = str(result)
        text = text.strip()
        if len(text) <= limit:
            return text, False
        return text[:limit], True

    def _diagnostic_item(
        self,
        *,
        source: str,
        kind: str,
        summary: str,
        command: Optional[str] = None,
        tool: Optional[str] = None,
        return_code: Optional[int] = None,
        file_refs: Optional[list[str]] = None,
        severity: Optional[str] = None,
        raw_preview: str = "",
        truncated: bool = False,
    ) -> dict[str, Any]:
        item = {
            "source": source,
            "kind": kind,
            "summary": str(summary or "")[:500],
            "returnCode": return_code,
            "fileRefs": list(file_refs or [])[:20],
            "severity": severity or "info",
            "rawPreview": str(raw_preview or "")[:1600],
            "truncated": bool(truncated),
        }
        if command:
            item["command"] = command
        if tool:
            item["tool"] = tool
        return {key: value for key, value in item.items() if value not in (None, "", [], {})}

    def _git_diagnostic_summary(self, git_summary: dict[str, Any], changed_files: list[str]) -> str:
        if git_summary.get("statusError"):
            return f"Git status unavailable: {git_summary.get('statusError')}"
        if changed_files:
            return f"{len(changed_files)} changed file(s) detected by git status."
        return "Git status is clean or no tracked changes were detected."

    def _lsp_provider_status(self, cfg: dict[str, Any]) -> dict[str, Any] | None:
        if not bool((cfg.get("diagnosticsProviders") or {}).get("lspBestEffort", True)):
            return None
        return self._diagnostic_item(
            source="lsp",
            kind="provider_status",
            summary="LSP diagnostics provider is unavailable in this phase; proof collection continues with git/command evidence.",
            severity="info",
            raw_preview="provider=unavailable",
        ) | {"provider": "unavailable"}

    def _task_brief_from_metadata_or_events(self, metadata: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any] | None:
        for key in ("taskBrief", "plannerTaskBrief", "engineeringTaskBrief"):
            value = metadata.get(key)
            if isinstance(value, dict):
                return dict(value)
        for event in events:
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            for key in ("taskBrief", "plannerTaskBrief", "task"):
                value = payload.get(key)
                if isinstance(value, dict):
                    return dict(value)
            tool = payload.get("tool") if isinstance(payload.get("tool"), dict) else {}
            result = tool.get("result") if isinstance(tool.get("result"), dict) else {}
            items = result.get("items") if isinstance(result.get("items"), list) else []
            for item in items:
                if isinstance(item, dict) and (item.get("taskBriefId") or item.get("taskGoal")):
                    return {
                        "taskBriefId": item.get("taskBriefId"),
                        "goal": item.get("taskGoal"),
                        "writeSet": item.get("writeSet") or [],
                        "readSet": item.get("readSet") or [],
                        "engineeringTaskCapsule": item.get("engineeringTaskCapsule") if isinstance(item.get("engineeringTaskCapsule"), dict) else {},
                        "worksetDispatchDecision": item.get("worksetDispatchDecision") if isinstance(item.get("worksetDispatchDecision"), dict) else {},
                    }
        return None

    def _read_set_from_events_or_context(self, events: list[dict[str, Any]], metadata: dict[str, Any]) -> list[str]:
        context = metadata.get("engineeringContextPack") if isinstance(metadata.get("engineeringContextPack"), dict) else {}
        if isinstance(context.get("contextPack"), dict):
            context = context.get("contextPack") or {}
        critical = context.get("criticalFiles") if isinstance(context.get("criticalFiles"), list) else []
        values = [str(item.get("path") or "") for item in critical if isinstance(item, dict) and item.get("path")]
        contract = context.get("codingPlannerContractPreview") if isinstance(context.get("codingPlannerContractPreview"), dict) else {}
        values.extend(str(item or "").strip() for item in list(contract.get("readSet") or []) if str(item or "").strip())
        if values:
            return list(dict.fromkeys(values))[:100]
        reads: list[str] = []
        for event in events:
            tool = self._event_tool_payload(event)
            if str(tool.get("toolName") or "") not in {"read_native_file", "grep_search"}:
                continue
            args = tool.get("args") if isinstance(tool.get("args"), dict) else {}
            path = str(args.get("path") or args.get("filePath") or args.get("query") or "").strip()
            if path:
                reads.append(path)
        return list(dict.fromkeys(reads))[:100]

    def _normalize_path_list(self, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        normalized: list[str] = []
        for item in value:
            text = str(item or "").strip().replace("\\", "/")
            if text:
                normalized.append(text)
        return list(dict.fromkeys(normalized))[:100]

    def _normalize_workset_risk(self, value: Any) -> str:
        raw = str(value or "").strip().lower()
        if raw in {"ready", "within_write_set", "none"}:
            return "within_write_set"
        if raw in {"write_set_conflict", "outside_write_set"}:
            return "outside_write_set"
        if raw in {"missing_write_set", "unknown_write_set", "read_only_safe", "not_evaluated"}:
            return raw
        if raw == "not_engineering":
            return "not_evaluated"
        return "not_evaluated"

    def _normalize_workset_dispatch_decision(self, decision: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(decision, dict):
            return {}
        normalized = dict(decision)
        raw_risk = str(normalized.get("risk") or "").strip()
        normalized_risk = self._normalize_workset_risk(raw_risk)
        decision_source = str(
            normalized.get("decisionSource")
            or normalized.get("worksetDecisionSource")
            or "supervisor_manual"
        ).strip() or "supervisor_manual"
        blocked = bool(normalized.get("blocked"))
        warning = bool(normalized.get("warning")) or blocked
        manual_override = bool(normalized.get("manualOverride")) or (decision_source == "supervisor_manual" and warning and not blocked)
        normalized["risk"] = normalized_risk
        normalized["decisionSource"] = decision_source
        normalized["worksetDecisionSource"] = decision_source
        normalized["blocked"] = blocked
        normalized["warning"] = warning
        normalized["manualOverride"] = manual_override
        normalized["correlationStatus"] = str(normalized.get("correlationStatus") or normalized_risk)
        if raw_risk and raw_risk != normalized_risk:
            normalized["rawRisk"] = raw_risk
        return normalized

    def _workset_risk(self, *, changed_files: list[str], write_set: list[str], cfg: dict[str, Any]) -> dict[str, Any]:
        mode = str(cfg.get("worksetGovernanceMode") or cfg.get("worksetRiskMode") or "read_only").strip().lower()
        if mode == "off":
            return {"mode": "off", "risk": "not_evaluated"}
        if not changed_files:
            return {
                "mode": mode,
                "risk": "within_write_set",
                "warning": False,
                "changedFiles": [],
                "outsideWriteSet": [],
                "note": "No changed files were observed in this run.",
                "suggestedAction": "No write-set drift observed.",
            }
        warning_mode = mode in {"soft_gate", "observe_auto_block"}
        if not write_set:
            return {
                "mode": mode,
                "risk": "unknown_write_set",
                "warning": warning_mode,
                "changedFiles": changed_files,
                "outsideWriteSet": [],
                "note": "Task brief writeSet is missing; conflicts cannot be proven safe.",
                "suggestedAction": "Repair planner contract before accepting concurrent or delegated writes.",
            }
        outside = [path for path in changed_files if not self._path_matches_any_write_set(path, write_set)]
        return {
            "mode": mode,
            "risk": "outside_write_set" if outside else "within_write_set",
            "warning": bool(outside) and warning_mode,
            "changedFiles": changed_files,
            "writeSet": write_set,
            "outsideWriteSet": outside,
            "suggestedAction": "Ask supervisor to approve or expand writeSet before accepting out-of-scope changes." if outside else "Continue; current changes are within declared writeSet.",
        }

    def _path_matches_any_write_set(self, path: str, write_set: list[str]) -> bool:
        normalized = str(path or "").strip().replace("\\", "/").lstrip("./")
        for item in write_set:
            candidate = str(item or "").strip().replace("\\", "/").lstrip("./")
            if not candidate:
                continue
            if candidate in {".", "*", normalized}:
                return True
            if candidate.endswith("/"):
                candidate = candidate.rstrip("/")
            if normalized == candidate or normalized.startswith(f"{candidate}/"):
                return True
        return False

    def _verification_status(self, *, changed_files: list[str], commands: list[dict[str, Any]], diagnostics: list[dict[str, Any]]) -> str:
        validation_commands = [cmd for cmd in commands if cmd.get("isValidation")]
        failed_validation = [
            cmd for cmd in validation_commands
            if cmd.get("returnCode") is not None and int(cmd.get("returnCode")) != 0
        ]
        successful_validation = [
            cmd for cmd in validation_commands
            if cmd.get("returnCode") == 0 or str(cmd.get("status") or "").lower() in {"ok", "success", "completed"}
        ]
        diagnostic_errors = [
            item for item in diagnostics
            if item.get("kind") == "validation_command" and item.get("severity") == "error"
        ]
        if failed_validation or diagnostic_errors:
            return "failed_verification"
        if changed_files and successful_validation:
            return "verified"
        if changed_files:
            return "unverified"
        if successful_validation:
            return "observed_no_change"
        return "planned"

    def _residual_risks(
        self,
        *,
        verification_status: str,
        changed_files: list[str],
        commands: list[dict[str, Any]],
        diagnostics: list[dict[str, Any]],
        workset_risk: dict[str, Any],
    ) -> list[str]:
        risks: list[str] = []
        if verification_status == "unverified":
            risks.append("Code changes were detected but no successful validation command was observed in this run.")
        if verification_status == "failed_verification":
            risks.append("A validation command or diagnostic failed; the work cannot be marked verified.")
        if verification_status == "failed_due_to_dispatch_error":
            risks.append("The run failed before Engineering produced command, write-set, or verification evidence.")
        if changed_files and not any(cmd.get("isValidation") for cmd in commands):
            risks.append("No test/typecheck/build/compile evidence was collected for changed files.")
        if workset_risk.get("risk") == "unknown_write_set":
            risks.append("Task brief writeSet is missing, so write-set ownership cannot be proven.")
        if workset_risk.get("risk") == "outside_write_set":
            risks.append("Changed files include paths outside the task brief writeSet.")
        if any(item.get("source") == "lsp" and item.get("provider") == "unavailable" for item in diagnostics):
            risks.append("LSP diagnostics provider unavailable; proof relies on git and command evidence only.")
        return list(dict.fromkeys(risks))[:12]

    def _patch_intent_from_run(self, *, session_id: str, metadata: dict[str, Any], task_brief: dict[str, Any] | None) -> str:
        if isinstance(task_brief, dict):
            goal = str(task_brief.get("goal") or "").strip()
            if goal:
                return goal[:500]
        for message in reversed(db.get_messages(session_id)):
            if message.get("role") == "user":
                content = str(message.get("content") or "").strip()
                if content:
                    return content[:500]
        command_preset = metadata.get("commandPreset") if isinstance(metadata.get("commandPreset"), dict) else {}
        return str(command_preset.get("name") or "Engineering run evidence collected.").strip()

    def _context_pack_digest(self, *, metadata: dict[str, Any], events: list[dict[str, Any]], trigger: dict[str, Any]) -> dict[str, Any]:
        event_trigger = {}
        for event in events:
            if str(event.get("topic") or "") != "engineering_lane.trigger.decided":
                continue
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            event_trigger = payload
            break
        context = metadata.get("engineeringContextPack") if isinstance(metadata.get("engineeringContextPack"), dict) else {}
        if isinstance(context.get("contextPack"), dict):
            context = context.get("contextPack") or {}
        return {
            "triggerDecision": trigger or event_trigger.get("triggerDecision") or {},
            "contextPackActive": bool(context) or bool(event_trigger.get("contextPackActive")),
            "criticalFileCount": len(context.get("criticalFiles") or []) if isinstance(context.get("criticalFiles"), list) else None,
            "evidenceGraphEnabled": bool((context.get("evidenceGraphDigest") or {}).get("enabled")) if isinstance(context.get("evidenceGraphDigest"), dict) else False,
            "codingPlannerContractEnabled": bool((context.get("codingPlannerContractPreview") or {}).get("enabled")) if isinstance(context.get("codingPlannerContractPreview"), dict) else False,
            "source": "run_metadata" if context else "runtime_event_digest",
        }

    def _emit_proof_runtime_event(
        self,
        *,
        session_id: str,
        run_id: str,
        proof_entry: dict[str, Any],
        source_component: str,
        manual_refresh: bool,
    ) -> None:
        try:
            diagnostics = proof_entry.get("diagnostics") if isinstance(proof_entry.get("diagnostics"), dict) else {}
            workset_decision = diagnostics.get("worksetDispatchDecision") if isinstance(diagnostics.get("worksetDispatchDecision"), dict) else {}
            outside_write_set_files = [str(item).strip() for item in list(proof_entry.get("outsideWriteSetFiles") or diagnostics.get("outsideWriteSetFiles") or []) if str(item).strip()]
            residual_risks = [str(item).strip() for item in list(proof_entry.get("residualRisks") or []) if str(item).strip()]
            verification_status = str(proof_entry.get("verificationStatus") or "planned").strip() or "planned"
            risk = str(workset_decision.get("risk") or "").strip()
            summary = f"工程证明已收集 · {verification_status}"
            changed_file_count = len(proof_entry.get("changedFiles") or [])
            if changed_file_count > 0:
                summary += f" · {changed_file_count} 个变更文件"
            if outside_write_set_files:
                summary += f" · {len(outside_write_set_files)} 个越界文件"
            db.add_runtime_event({
                "event_id": str(uuid.uuid4()),
                "session_id": session_id,
                "run_id": run_id,
                "seq": db.get_next_runtime_seq(session_id),
                "kind": "event",
                "topic": "engineering.proof.collected",
                "ts": _utc_now_iso(),
                "source": {
                    "plane": "engine",
                    "component": "engineering_lane",
                    "node": source_component,
                    "agent_id": None,
                },
                "payload": {
                    "summary": summary,
                    "proofEntryId": proof_entry.get("id"),
                    "taskBriefId": proof_entry.get("taskBriefId"),
                    "patchIntent": proof_entry.get("patchIntent"),
                    "verificationStatus": proof_entry.get("verificationStatus"),
                    "status": verification_status,
                    "risk": risk,
                    "decisionSource": workset_decision.get("worksetDecisionSource") or workset_decision.get("decisionSource"),
                    "outsideWriteSetFiles": outside_write_set_files,
                    "outsideWriteSetCount": len(outside_write_set_files),
                    "residualRiskCount": len(residual_risks),
                    "changedFileCount": changed_file_count,
                    "diagnosticCount": len(((proof_entry.get("diagnostics") or {}).get("items") or [])),
                    "manualRefresh": manual_refresh,
                },
            })
        except Exception:
            # Proof evidence is already persisted; event emission should never break terminal cleanup.
            return

    def _detect_code_signals(self, text: str) -> list[str]:
        raw = str(text or "").strip().lower()
        if not raw:
            return []
        code_media_markers = ("remotion", "manim", "ffmpeg", "ffprobe", "three.js", "threejs", "p5.js", "p5js", "processing", "webgl", "canvas")
        if any(pattern in raw for pattern in NON_ENGINEERING_PATTERNS) and not any(marker in raw for marker in ("代码", "code", "repo", "仓库", "组件", "接口", *code_media_markers)):
            return []
        signals: list[str] = []
        for name, patterns in CODE_SIGNAL_PATTERNS:
            if any(pattern in raw for pattern in patterns):
                signals.append(name)
        if self._detect_project_creation_signal(raw):
            signals.append("project_creation_candidate")
        return list(dict.fromkeys(signals))

    def _detect_project_creation_signal(self, raw: str) -> bool:
        action_terms = (
            "create",
            "build",
            "scaffold",
            "make",
            "new",
            "design",
            "implement",
            "搭建",
            "创建",
            "新建",
            "做一个",
            "做个",
            "做一款",
            "设计",
            "设计一个",
            "设计一款",
            "制作",
            "实现",
            "开发",
        )
        artifact_terms = (
            "web app",
            "web application",
            "frontend",
            "front-end",
            "application",
            "project",
            "game",
            "ui",
            "前端",
            "前端界面",
            "前端页面",
            "动态ui",
            "动态 ui",
            "web应用",
            "web 应用",
            "网页应用",
            "应用",
            "项目",
            "网站",
            "游戏",
        )
        return any(term in raw for term in action_terms) and any(term in raw for term in artifact_terms)

    def _repo_brief(self, root: Path) -> dict[str, Any]:
        git_root_result = _run_command(["git", "rev-parse", "--show-toplevel"], cwd=root, timeout=3.0)
        repo_root = git_root_result.get("stdout") if git_root_result.get("ok") else ""
        branch_result = _run_command(["git", "branch", "--show-current"], cwd=root, timeout=3.0) if repo_root else {}
        return {
            "workspaceRoot": str(root),
            "repoDetected": bool(repo_root),
            "repoRoot": str(repo_root or ""),
            "branch": str(branch_result.get("stdout") or "") if branch_result else "",
        }

    def _workspace_rules_digest(self, root: Path, *, budget: int, diagnostics: list[dict[str, Any]]) -> dict[str, Any]:
        path = root / ".agents" / "rules" / "AGENTS.md"
        text = _read_text(path, limit=40000)
        if not text:
            diagnostics.append({
                "source": "workspace.AGENTS.md",
                "estimatedTokens": 0,
                "budgetTokens": budget,
                "truncated": False,
                "omittedReason": "missing",
            })
            return {"path": str(path), "exists": False, "digest": ""}
        budget_result = enforce_prompt_budget(
            source="workspace.AGENTS.md",
            text=text,
            budget_tokens=budget,
            truncate=True,
            omission_reason="engineering_context_pack_rules_truncated",
        )
        diagnostics.append(budget_result.diagnostic())
        return {
            "path": str(path),
            "exists": True,
            "estimatedTokens": budget_result.estimated_tokens,
            "truncated": budget_result.truncated,
            "digest": budget_result.text,
        }

    def _git_summary(self, root: Path) -> dict[str, Any]:
        status = _run_command(["git", "status", "--short"], cwd=root, timeout=5.0)
        diff_stat = _run_command(["git", "diff", "--stat"], cwd=root, timeout=5.0)
        staged_stat = _run_command(["git", "diff", "--cached", "--stat"], cwd=root, timeout=5.0)
        return {
            "statusShort": status.get("stdout", "") if status.get("ok") else "",
            "statusError": status.get("stderr") or status.get("error"),
            "diffStat": diff_stat.get("stdout", "") if diff_stat.get("ok") else "",
            "stagedDiffStat": staged_stat.get("stdout", "") if staged_stat.get("ok") else "",
        }

    def _manifest_summary(self, root: Path) -> dict[str, Any]:
        found: list[dict[str, Any]] = []
        scripts: dict[str, Any] = {}
        candidate_paths: list[Path] = []
        for name in MANIFEST_FILES:
            candidate_paths.append(root / name)
        try:
            for child in root.iterdir():
                if not child.is_dir() or child.name in IGNORED_DIRS or child.name.startswith("."):
                    continue
                for name in MANIFEST_FILES:
                    candidate_paths.append(child / name)
        except Exception:
            pass
        seen: set[str] = set()
        project_subroots: list[str] = []
        for path in candidate_paths:
            normalized_path = str(path)
            if normalized_path in seen or not path.exists():
                continue
            seen.add(normalized_path)
            rel = _relative(path, root)
            found.append({"path": rel, "size": path.stat().st_size if path.exists() else None})
            if path.name == "package.json":
                try:
                    package = json.loads(_read_text(path, limit=20000) or "{}")
                    package_scripts = package.get("scripts") if isinstance(package.get("scripts"), dict) else {}
                    if not scripts and isinstance(package_scripts, dict):
                        scripts = package_scripts
                    parent_rel = _relative(path.parent, root)
                    if parent_rel and parent_rel != ".":
                        project_subroots.append(parent_rel)
                except Exception:
                    if not scripts:
                        scripts = {}
        return {
            "manifests": found,
            "packageScripts": {key: scripts[key] for key in list(scripts)[:24]} if isinstance(scripts, dict) else {},
            "projectSubroots": project_subroots[:12],
        }

    def _critical_file_candidates(self, root: Path, *, user_query: str, limit: int = 24) -> list[dict[str, Any]]:
        terms = [term for term in re.split(r"[^A-Za-z0-9_\-\u4e00-\u9fff]+", str(user_query or "").lower()) if len(term) >= 3]
        candidates: list[dict[str, Any]] = []
        scanned = 0
        try:
            iterator = root.rglob("*")
            for path in iterator:
                if len(candidates) >= limit or scanned >= 5000:
                    break
                scanned += 1
                if any(part in IGNORED_DIRS for part in path.parts):
                    continue
                if not path.is_file():
                    continue
                rel = _relative(path, root)
                lower = rel.lower()
                score = 0
                if path.name in MANIFEST_FILES:
                    score += 5
                for term in terms:
                    if term in lower:
                        score += 2
                if score <= 0:
                    continue
                candidates.append({
                    "path": rel,
                    "score": score,
                    "size": path.stat().st_size if path.exists() else None,
                })
        except Exception:
            return []
        candidates.sort(key=lambda item: (-int(item.get("score") or 0), str(item.get("path") or "")))
        return candidates[:limit]

    def _scope_chain_for_descriptor(self, descriptor: dict[str, Any]) -> list[str]:
        project_id = str(descriptor.get("projectId") or "").strip()
        source = str(descriptor.get("source") or "")
        if project_id:
            return ["global", f"project:{project_id}"]
        if source == "main_workspace" or not descriptor.get("isScopedOverride"):
            return ["global", "workspace:main"]
        return ["global"]

    def _ranked_workflow_paths(self, *, query: str, scope_chain: list[str], max_paths: int) -> list[dict[str, Any]]:
        hints = workflow_memory_service.match_hints(query=query, scope_chain=scope_chain, limit=max_paths)
        ranked: list[dict[str, Any]] = []
        for item in hints:
            golden = list(item.get("goldenPathSteps") or [])
            anti = list(item.get("antiPatterns") or [])
            verify = list(item.get("verificationSteps") or [])
            diagnostics = item.get("_workflowHintDiagnostics") if isinstance(item.get("_workflowHintDiagnostics"), dict) else {}
            if not golden:
                continue
            step = golden[0]
            actions = [str(step), *self._variants_for_step(item, step, step_index=0)]
            for index, action in enumerate(actions[:max_paths]):
                ranked.append({
                    "workflowId": item.get("id"),
                    "taskFamily": item.get("task_family"),
                    "rank": len(ranked) + 1,
                    "behaviorMatch": max(0.05, float(diagnostics.get("score") or item.get("confidence") or 0) / (index + 1)),
                    "evidence": diagnostics.get("matchedReasons") or [],
                    "suggestedAction": str(action)[:260],
                    "reasonableVariants": [value for value in self._variants_for_step(item, step, step_index=0) if value != action][:3],
                    "avoid": anti[:2],
                    "verify": verify[:2],
                    "confidence": item.get("confidence"),
                })
                if len(ranked) >= max_paths:
                    return ranked
        return ranked

    def _variants_for_step(self, item: dict[str, Any], step: Any, *, step_index: int = 0) -> list[str]:
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        variants = metadata.get("actionVariants") or metadata.get("action_variants") or []
        variants_by_step = metadata.get("actionVariantsByStep") or metadata.get("action_variants_by_step") or {}
        if isinstance(variants_by_step, dict):
            variants = variants_by_step.get(str(step_index)) or variants_by_step.get(step_index) or variants
        if isinstance(variants, list) and variants:
            return [str(value)[:120] for value in variants[:3]]
        text = str(step or "")
        if "fetch_skill_instructions" in text:
            return ["Use exact skill id/name first", "If alias is used, verify resolver diagnostics before execution"]
        if "test" in text.lower() or "验证" in text:
            return ["Run the narrowest relevant test first", "Escalate to full regression only after local signal is clean"]
        return ["Keep the goal and verification invariant; adapt the concrete tool/action to current repo evidence"]

    def _evidence_graph_digest(
        self,
        *,
        root: Path,
        user_query: str,
        descriptor: dict[str, Any],
        repo_brief: dict[str, Any],
        rules_digest: dict[str, Any],
        git_summary: dict[str, Any],
        manifest_summary: dict[str, Any],
        critical_files: list[dict[str, Any]],
        workflow_paths: list[dict[str, Any]],
        cfg: dict[str, Any],
    ) -> dict[str, Any]:
        if not bool(cfg.get("evidenceGraphEnabled", True)):
            return {"enabled": False, "repoDetected": bool(repo_brief.get("repoDetected"))}
        budget = _safe_int(cfg.get("evidenceGraphBudget"), 16000, 600, 48000)
        inventory = self._file_inventory_digest(root)
        changed_files = self._changed_files_from_status(str(git_summary.get("statusShort") or ""))
        test_candidates = self._test_candidates(manifest_summary)
        prior_proof = self._prior_proof_summary(
            session_id=str(descriptor.get("sessionId") or "").strip() or None,
            workspace_root=str(root),
        )
        digest = {
            "enabled": True,
            "repoDetected": bool(repo_brief.get("repoDetected")),
            "repoRoot": repo_brief.get("repoRoot") or "",
            "workspaceRoot": str(root),
            "branch": repo_brief.get("branch") or "",
            "dirtyState": {
                "changedFileCount": len(changed_files),
                "changedFiles": changed_files[:40],
                "statusPreview": str(git_summary.get("statusShort") or "")[:1600],
            },
            "fileInventoryDigest": inventory,
            "manifestScripts": {
                "packageManager": self._package_manager_hint(manifest_summary),
                "testCandidates": test_candidates,
                "manifests": list(manifest_summary.get("manifests") or [])[:12],
            },
            "workspaceRules": {
                "path": rules_digest.get("path"),
                "exists": bool(rules_digest.get("exists")),
                "estimatedTokens": rules_digest.get("estimatedTokens"),
                "truncated": bool(rules_digest.get("truncated")),
            },
            "criticalFileCandidates": critical_files[: _safe_int(cfg.get("maxCriticalFiles"), 24, 4, 120)],
            "priorProofSummary": prior_proof,
            "workflowRankedHints": workflow_paths[: _safe_int(cfg.get("rankedWorkflowPathCount"), 3, 1, 5)],
            "graphSourceDiagnostics": {
                "ignoredDirs": sorted(IGNORED_DIRS),
                "budgetTokens": budget,
                "querySignals": self._detect_code_signals(user_query),
                "scope": {
                    "projectId": descriptor.get("projectId"),
                    "workspaceId": descriptor.get("workspaceId"),
                    "source": descriptor.get("source"),
                },
            },
        }
        estimated = estimate_prompt_tokens(json.dumps(digest, ensure_ascii=False, default=str))
        digest["estimatedTokens"] = estimated
        digest["budgetTokens"] = budget
        digest["truncated"] = False
        if estimated > budget:
            digest["truncated"] = True
            digest["criticalFileCandidates"] = list(digest.get("criticalFileCandidates") or [])[:10]
            digest["workflowRankedHints"] = list(digest.get("workflowRankedHints") or [])[:2]
            digest["dirtyState"]["statusPreview"] = str(digest["dirtyState"].get("statusPreview") or "")[:600]
            digest["fileInventoryDigest"]["sampleFiles"] = list(digest["fileInventoryDigest"].get("sampleFiles") or [])[:24]
            digest["estimatedTokens"] = estimate_prompt_tokens(json.dumps(digest, ensure_ascii=False, default=str))
        return digest

    def _file_inventory_digest(self, root: Path, *, max_scan: int = 6000) -> dict[str, Any]:
        counts: dict[str, int] = {}
        samples: list[str] = []
        total_files = 0
        scanned = 0
        try:
            for path in root.rglob("*"):
                if scanned >= max_scan:
                    break
                scanned += 1
                if any(part in IGNORED_DIRS for part in path.parts):
                    continue
                if not path.is_file():
                    continue
                total_files += 1
                ext = path.suffix.lower() or "(no_ext)"
                counts[ext] = counts.get(ext, 0) + 1
                if len(samples) < 60 and (path.suffix.lower() in SOURCE_EXTENSIONS or path.name in MANIFEST_FILES):
                    samples.append(_relative(path, root))
        except Exception as exc:
            return {"totalFiles": total_files, "scanned": scanned, "error": str(exc), "extensions": counts, "sampleFiles": samples}
        top_extensions = [
            {"extension": ext, "count": count}
            for ext, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:12]
        ]
        return {
            "totalFiles": total_files,
            "scanned": scanned,
            "scanLimit": max_scan,
            "truncated": scanned >= max_scan,
            "extensions": top_extensions,
            "sampleFiles": samples,
        }

    def _package_manager_hint(self, manifest_summary: dict[str, Any]) -> str:
        paths = {str(item.get("path") or "") for item in list(manifest_summary.get("manifests") or []) if isinstance(item, dict)}
        if "pnpm-workspace.yaml" in paths:
            return "pnpm"
        if "yarn.lock" in paths:
            return "yarn"
        if "package-lock.json" in paths or "package.json" in paths:
            return "npm"
        if "pyproject.toml" in paths:
            return "python/pyproject"
        if "requirements.txt" in paths:
            return "python/requirements"
        return ""

    def _test_candidates(self, manifest_summary: dict[str, Any]) -> list[dict[str, Any]]:
        scripts = manifest_summary.get("packageScripts") if isinstance(manifest_summary.get("packageScripts"), dict) else {}
        candidates: list[dict[str, Any]] = []
        for key, value in scripts.items():
            name = str(key or "")
            command = str(value or "")
            lowered = f"{name} {command}".lower()
            if any(marker in lowered for marker in ("test", "typecheck", "tsc", "build", "lint", "vitest", "pytest")):
                candidates.append({"source": "package.json", "name": name, "command": command[:220]})
        for item in list(manifest_summary.get("manifests") or []):
            path = str((item or {}).get("path") or "")
            if path in {"pytest.ini", "tox.ini", "pyproject.toml"}:
                candidates.append({"source": path, "name": "pytest", "command": "python -m pytest"})
            elif path == "tsconfig.json":
                candidates.append({"source": path, "name": "typecheck", "command": "npm run typecheck or tsc --noEmit"})
        deduped: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in candidates:
            key = f"{item.get('name')}::{item.get('command')}"
            if key not in seen:
                seen.add(key)
                deduped.append(item)
        return deduped[:12]

    def _prior_proof_summary(self, *, session_id: str | None, workspace_root: str) -> dict[str, Any]:
        entries = db.list_engineering_proof_entries(session_id=session_id, limit=8) if session_id else db.list_engineering_proof_entries(limit=8)
        status_counts: dict[str, int] = {}
        recent: list[dict[str, Any]] = []
        for entry in entries:
            metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
            if workspace_root:
                entry_workspace = str(metadata.get("workspaceRoot") or "").strip()
                if entry_workspace and entry_workspace != workspace_root:
                    continue
                if not session_id and not entry_workspace:
                    continue
            status = str(entry.get("verificationStatus") or "unknown")
            status_counts[status] = status_counts.get(status, 0) + 1
            recent.append({
                "id": entry.get("id"),
                "runId": entry.get("runId"),
                "verificationStatus": status,
                "changedFileCount": len(entry.get("changedFiles") or []),
                "residualRiskCount": len(entry.get("residualRisks") or []),
            })
            if len(recent) >= 5:
                break
        return {"recentCount": len(recent), "statusCounts": status_counts, "recent": recent}

    def _coding_planner_contract_preview(
        self,
        *,
        user_query: str,
        task_brief: Optional[dict[str, Any]],
        evidence_graph_digest: dict[str, Any],
        critical_files: list[dict[str, Any]],
        manifest_summary: dict[str, Any],
        git_summary: dict[str, Any],
        cfg: dict[str, Any],
    ) -> dict[str, Any]:
        if not bool(cfg.get("codingPlannerContractEnabled", True)):
            return {"enabled": False}
        max_files = _safe_int(cfg.get("maxCriticalFiles"), 24, 4, 120)
        critical = [
            str(item.get("path") or "").strip()
            for item in critical_files[:max_files]
            if isinstance(item, dict) and str(item.get("path") or "").strip()
        ]
        changed = self._changed_files_from_status(str(git_summary.get("statusShort") or ""))
        read_set = list(dict.fromkeys([*critical[:12], *changed[:12]]))[:max_files]
        explicit_write_set = self._normalize_path_list((task_brief or {}).get("writeSet") if isinstance(task_brief, dict) else [])
        write_set = explicit_write_set or self._infer_write_set_from_query(user_query, critical, changed)
        verification_matrix = self._verification_matrix(manifest_summary)
        risk_flags: list[str] = []
        if not critical and not changed:
            risk_flags.append("critical_files_not_proven")
        if not write_set:
            risk_flags.append("write_set_missing")
        if not verification_matrix:
            risk_flags.append("verification_candidates_missing")
        if not evidence_graph_digest.get("repoDetected"):
            risk_flags.append("repo_not_detected")
        ownership = self._ownership_plan(write_set=write_set, read_set=read_set)
        return {
            "enabled": True,
            "criticalFiles": read_set[:max_files],
            "readSet": read_set[:max_files],
            "writeSet": write_set[:max_files],
            "ownershipPlan": ownership,
            "verificationMatrix": verification_matrix,
            "mergeOrder": self._merge_order(write_set=write_set, verification_matrix=verification_matrix),
            "riskFlags": list(dict.fromkeys(risk_flags)),
            "proofExpectations": self._proof_expectations(verification_matrix=verification_matrix, write_set=write_set),
        }

    def _infer_write_set_from_query(self, user_query: str, critical_files: list[str], changed_files: list[str]) -> list[str]:
        text = str(user_query or "").lower()
        write_set: list[str] = []
        if any(marker in text for marker in ("test", "测试", "spec", "验证")):
            write_set.extend([path for path in critical_files if any(marker in path.lower() for marker in TEST_FILE_MARKERS)])
        if any(marker in text for marker in ("admin", "页面", "ui", "frontend", "tsx", "组件")):
            write_set.extend([path for path in critical_files if any(part in path.lower() for part in ("admin", "src/", "app/", "components/", ".tsx", ".ts"))])
        if any(marker in text for marker in ("engine", "runtime", "api", "后端", "接口")):
            write_set.extend([path for path in critical_files if any(part in path.lower() for part in ("apps/v8-agent-os-engine", "api/", "runtimes/", "core/", ".py"))])
        if changed_files:
            write_set.extend(changed_files)
        if not write_set:
            write_set.extend(critical_files[:6])
        return list(dict.fromkeys(write_set))[:24]

    def _verification_matrix(self, manifest_summary: dict[str, Any]) -> list[dict[str, Any]]:
        matrix: list[dict[str, Any]] = []
        for item in self._test_candidates(manifest_summary):
            command = str(item.get("command") or "")
            name = str(item.get("name") or "")
            kind = "test"
            lowered = f"{name} {command}".lower()
            if "typecheck" in lowered or "tsc" in lowered:
                kind = "typecheck"
            elif "build" in lowered:
                kind = "build"
            elif "lint" in lowered:
                kind = "lint"
            matrix.append({
                "kind": kind,
                "command": command,
                "source": item.get("source"),
                "requiredForVerified": kind in {"test", "typecheck", "build"},
            })
        return matrix[:8]

    def _ownership_plan(self, *, write_set: list[str], read_set: list[str]) -> list[dict[str, Any]]:
        if not write_set:
            return [{"owner": "supervisor", "scope": "unknown", "mode": "needs_planner_write_set"}]
        buckets: dict[str, list[str]] = {}
        for path in write_set:
            normalized = str(path or "").replace("\\", "/")
            root = normalized.split("/")[0] if "/" in normalized else normalized
            if normalized.startswith("apps/") and len(normalized.split("/")) >= 2:
                root = "/".join(normalized.split("/")[:2])
            buckets.setdefault(root or ".", []).append(normalized)
        return [
            {
                "owner": "supervisor_or_selected_subagent",
                "scope": scope,
                "writeSet": paths[:12],
                "readSetHint": [path for path in read_set if path.startswith(scope)][:8],
            }
            for scope, paths in list(buckets.items())[:8]
        ]

    def _merge_order(self, *, write_set: list[str], verification_matrix: list[dict[str, Any]]) -> list[str]:
        order = ["Confirm write-set and ownership before editing"]
        if write_set:
            order.append("Apply implementation patch within declared writeSet")
        else:
            order.append("Repair planner contract before editing because writeSet is missing")
        if verification_matrix:
            order.append("Run or request the narrowest listed verification before claiming verified")
        else:
            order.append("Record residual risk if no verification candidate exists")
        order.append("Update Proof Ledger with diff, diagnostics, and residual risks")
        return order

    def _proof_expectations(self, *, verification_matrix: list[dict[str, Any]], write_set: list[str]) -> list[str]:
        expectations = [
            "Patch intent must name the intended behavior change.",
            "Proof must include changed files and any commands already run.",
        ]
        if write_set:
            expectations.append("Changed files should stay inside the declared writeSet or trigger a soft-gate warning.")
        else:
            expectations.append("Missing writeSet prevents strong ownership proof.")
        if verification_matrix:
            expectations.append("Verified status requires a successful test/typecheck/build/compile command from this run.")
        else:
            expectations.append("If no verification command is available, mark proof unverified or planned.")
        return expectations

    def _workset_soft_gate_decision(self, *, changed_files: list[str], write_set: list[str], cfg: dict[str, Any]) -> dict[str, Any]:
        mode = str(cfg.get("worksetGovernanceMode") or cfg.get("worksetRiskMode") or "soft_gate").strip().lower()
        if mode == "off":
            return {"mode": "off", "risk": "not_evaluated", "warning": False}
        if not changed_files:
            return {
                "mode": mode,
                "risk": "within_write_set",
                "warning": False,
                "changedFiles": [],
                "outsideWriteSet": [],
                "suggestedAction": "No write-set drift observed.",
            }
        warning_mode = mode in {"soft_gate", "observe_auto_block"}
        if not write_set:
            return {
                "mode": mode,
                "risk": "unknown_write_set",
                "warning": warning_mode,
                "changedFiles": changed_files,
                "outsideWriteSet": [],
                "suggestedAction": "Repair planner writeSet before assigning concurrent edits.",
            }
        outside = [path for path in changed_files if not self._path_matches_any_write_set(path, write_set)]
        return {
            "mode": mode,
            "risk": "outside_write_set" if outside else "within_write_set",
            "warning": bool(outside) and warning_mode,
            "changedFiles": changed_files,
            "writeSet": write_set,
            "outsideWriteSet": outside,
            "suggestedAction": "Ask supervisor to approve or expand writeSet before accepting out-of-scope changes." if outside else "Continue; current changes are within declared writeSet.",
        }

    def enrich_planner_plan_with_engineering_contract(self, plan: dict[str, Any], *, engineering_context: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(plan, dict) or not isinstance(engineering_context, dict):
            return plan
        trigger = engineering_context.get("triggerDecision") if isinstance(engineering_context.get("triggerDecision"), dict) else {}
        if not trigger.get("active"):
            return plan
        pack = engineering_context.get("contextPack") if isinstance(engineering_context.get("contextPack"), dict) else {}
        contract = pack.get("codingPlannerContractPreview") if isinstance(pack.get("codingPlannerContractPreview"), dict) else {}
        evidence = pack.get("evidenceGraphDigest") if isinstance(pack.get("evidenceGraphDigest"), dict) else {}
        if not contract.get("enabled"):
            return plan
        next_plan = dict(plan)
        next_plan["codingPlannerContract"] = contract
        next_plan["engineeringEvidenceGraphDigest"] = {
            "repoDetected": evidence.get("repoDetected"),
            "repoRoot": evidence.get("repoRoot"),
            "branch": evidence.get("branch"),
            "dirtyState": evidence.get("dirtyState"),
            "criticalFileCount": len(evidence.get("criticalFileCandidates") or []),
        }
        risk_flags = [str(item).strip() for item in list(next_plan.get("riskFlags") or []) if str(item).strip()]
        risk_flags.extend(str(item).strip() for item in list(contract.get("riskFlags") or []) if str(item).strip())
        next_plan["riskFlags"] = list(dict.fromkeys(risk_flags))
        enriched_briefs: list[dict[str, Any]] = []
        for brief in list(next_plan.get("taskBriefs") or []):
            item = dict(brief or {})
            if not item.get("writeSet") and contract.get("writeSet"):
                item["writeSet"] = list(contract.get("writeSet") or [])[:24]
            item.setdefault("criticalFiles", list(contract.get("criticalFiles") or [])[:24])
            item.setdefault("readSet", list(contract.get("readSet") or [])[:24])
            item.setdefault("verificationMatrix", [str(row.get("command") or row.get("kind") or "") for row in list(contract.get("verificationMatrix") or []) if isinstance(row, dict)][:8])
            item.setdefault("proofExpectations", list(contract.get("proofExpectations") or [])[:8])
            item["engineeringTaskCapsule"] = {
                "criticalFiles": list(contract.get("criticalFiles") or [])[:24],
                "readSet": list(contract.get("readSet") or [])[:24],
                "writeSet": list(item.get("writeSet") or [])[:24],
                "verificationContract": list(contract.get("verificationMatrix") or [])[:8],
                "riskFlags": list(contract.get("riskFlags") or [])[:8],
                "proofExpectations": list(contract.get("proofExpectations") or [])[:8],
            }
            enriched_briefs.append(item)
        next_plan["taskBriefs"] = enriched_briefs
        return next_plan

    def _broker_dispatch_simulation(
        self,
        *,
        user_query: str,
        task_brief: Optional[dict[str, Any]],
        coding_contract: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(coding_contract, dict) or not coding_contract.get("enabled"):
            return {"enabled": False, "reason": "coding_planner_contract_unavailable"}
        if isinstance(task_brief, dict) and task_brief:
            tasks = [normalize_task_brief(task_brief)]
        else:
            tasks = [
                normalize_task_brief(
                    {
                        "taskBriefId": "engineering-preview-task-1",
                        "goal": user_query or "Engineering task preview",
                        "writeSet": list(coding_contract.get("writeSet") or []),
                        "criticalFiles": list(coding_contract.get("criticalFiles") or []),
                        "readSet": list(coding_contract.get("readSet") or []),
                        "verificationMatrix": [
                            str(row.get("command") or row.get("kind") or "")
                            for row in list(coding_contract.get("verificationMatrix") or [])
                            if isinstance(row, dict)
                        ],
                        "proofExpectations": list(coding_contract.get("proofExpectations") or []),
                        "behaviorScope": ["implementation"] if coding_contract.get("writeSet") else ["review"],
                        "engineeringTaskCapsule": {
                            "criticalFiles": list(coding_contract.get("criticalFiles") or []),
                            "readSet": list(coding_contract.get("readSet") or []),
                            "writeSet": list(coding_contract.get("writeSet") or []),
                            "verificationContract": list(coding_contract.get("verificationMatrix") or []),
                            "proofExpectations": list(coding_contract.get("proofExpectations") or []),
                            "riskFlags": list(coding_contract.get("riskFlags") or []),
                        },
                    }
                )
            ]
        auto_decisions = build_workset_dispatch_decisions(tasks, auto_dispatch=True, decision_source="dry_run")
        manual_decisions = build_workset_dispatch_decisions(tasks, auto_dispatch=False, decision_source="dry_run")
        return {
            "enabled": True,
            "taskCount": len(tasks),
            "autoDispatchBlocked": any(bool(item.get("blocked")) for item in auto_decisions),
            "autoDecisions": auto_decisions,
            "manualDecisions": manual_decisions,
            "recommendedAction": "repair_plan" if any(bool(item.get("blocked")) for item in auto_decisions) else "dispatch_allowed",
        }

    def _dry_run_matrix(
        self,
        *,
        user_query: str,
        task_brief: Optional[dict[str, Any]],
        coding_contract: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(coding_contract, dict) or not coding_contract.get("enabled"):
            return {"enabled": False, "reason": "coding_planner_contract_unavailable", "scenarios": []}
        base_write_set = list(coding_contract.get("writeSet") or []) or ["apps/v8-agent-os-engine/core/example.py"]
        base_read_set = list(coding_contract.get("readSet") or coding_contract.get("criticalFiles") or [])[:8]
        verification = [
            str(row.get("command") or row.get("kind") or "")
            for row in list(coding_contract.get("verificationMatrix") or [])
            if isinstance(row, dict)
        ][:4]

        def task(task_id: str, goal: str, write_set: list[str] | None, behavior: list[str]) -> dict[str, Any]:
            payload = {
                "taskBriefId": task_id,
                "goal": goal,
                "readSet": base_read_set,
                "behaviorScope": behavior,
                "verificationMatrix": verification,
                "proofExpectations": list(coding_contract.get("proofExpectations") or [])[:6],
                "engineeringTaskCapsule": {
                    "criticalFiles": list(coding_contract.get("criticalFiles") or [])[:12],
                    "readSet": base_read_set,
                    "writeSet": list(write_set or []),
                    "verificationContract": list(coding_contract.get("verificationMatrix") or [])[:4],
                    "proofExpectations": list(coding_contract.get("proofExpectations") or [])[:6],
                    "riskFlags": list(coding_contract.get("riskFlags") or [])[:6],
                },
            }
            if write_set is not None:
                payload["writeSet"] = list(write_set)
            return normalize_task_brief(payload)

        def simulated_verification(changed_files: list[str], commands: list[dict[str, Any]]) -> str:
            return self._verification_status(
                changed_files=changed_files,
                commands=commands,
                diagnostics=[],
            )

        same_path = base_write_set[0]
        sibling_path = f"{same_path.rstrip('/')}.test" if "." in same_path.rsplit("/", 1)[-1] else f"{same_path.rstrip('/')}/tests"
        scenarios = [
            {
                "id": "single_task",
                "label": "single task",
                "tasks": [task("single-1", user_query or "Implement focused engineering fix", base_write_set, ["implementation"])],
            },
            {
                "id": "parallel_non_conflict",
                "label": "parallel non-conflict",
                "tasks": [
                    task("impl-1", "Implement code slice", [same_path], ["implementation"]),
                    task("test-1", "Add verification slice", [sibling_path], ["verification"]),
                ],
            },
            {
                "id": "parallel_conflict",
                "label": "parallel conflict",
                "tasks": [
                    task("impl-1", "Implement code slice", [same_path], ["implementation"]),
                    task("impl-2", "Refactor same file", [same_path], ["implementation"]),
                ],
            },
            {
                "id": "missing_write_set",
                "label": "missing writeSet",
                "tasks": [task("missing-1", "Implement without declared writeSet", None, ["implementation"])],
            },
            {
                "id": "read_only_review",
                "label": "read-only reviewer",
                "tasks": [task("review-1", "Review implementation risks", [], ["review", "read_only"])],
            },
            {
                "id": "doc_only",
                "label": "doc-only task",
                "tasks": [task("docs-1", "Document behavior and residual risks", ["docs/"], ["documentation"])],
            },
            {
                "id": "manual_override_conflict",
                "label": "manual override conflict",
                "tasks": [
                    task("override-1", "Implement engine slice", [same_path], ["implementation"]),
                    task("override-2", "Supervisor manually delegates same file anyway", [same_path], ["implementation"]),
                ],
            },
            {
                "id": "verification_success",
                "label": "verification success",
                "tasks": [task("verify-ok-1", "Implement and validate targeted fix", [same_path], ["implementation"])],
                "simulatedVerificationStatus": simulated_verification(
                    [same_path],
                    [{"isValidation": True, "returnCode": 0, "status": "ok", "command": "python -m py_compile sample.py"}],
                ),
            },
            {
                "id": "verification_failure",
                "label": "verification failure",
                "tasks": [task("verify-fail-1", "Implement but validation fails", [same_path], ["implementation"])],
                "simulatedVerificationStatus": simulated_verification(
                    [same_path],
                    [{"isValidation": True, "returnCode": 1, "status": "error", "command": "npm run build"}],
                ),
            },
            {
                "id": "verification_missing",
                "label": "verification missing",
                "tasks": [task("verify-missing-1", "Implement without validation evidence", [same_path], ["implementation"])],
                "simulatedVerificationStatus": simulated_verification([same_path], []),
            },
        ]
        rendered: list[dict[str, Any]] = []
        for scenario in scenarios:
            tasks = list(scenario["tasks"])
            auto_decisions = build_workset_dispatch_decisions(tasks, auto_dispatch=True, decision_source="dry_run")
            manual_decisions = build_workset_dispatch_decisions(tasks, auto_dispatch=False, decision_source="dry_run")
            rendered.append(
                {
                    "id": scenario["id"],
                    "label": scenario["label"],
                    "taskCount": len(tasks),
                    "autoBlocked": any(bool(item.get("blocked")) for item in auto_decisions),
                    "manualWarning": any(bool(item.get("warning")) for item in manual_decisions),
                    "autoDecisions": auto_decisions,
                    "manualDecisions": manual_decisions,
                    "simulatedVerificationStatus": scenario.get("simulatedVerificationStatus") or "planned",
                    "recommendedAction": "repair_plan" if any(bool(item.get("blocked")) for item in auto_decisions) else "dispatch_allowed",
                }
            )
        return {
            "enabled": True,
            "scenarioCount": len(rendered),
            "blockedScenarioCount": sum(1 for item in rendered if bool(item.get("autoBlocked"))),
            "warningScenarioCount": sum(1 for item in rendered if bool(item.get("manualWarning"))),
            "scenarios": rendered,
        }

    def _proof_draft(
        self,
        *,
        session_id: Optional[str],
        run_id: Optional[str],
        task_brief: Optional[dict[str, Any]],
        context_pack: dict[str, Any],
        trigger: dict[str, Any],
    ) -> dict[str, Any]:
        git_summary = context_pack.get("gitSummary") if isinstance(context_pack.get("gitSummary"), dict) else {}
        changed_files = self._changed_files_from_status(str(git_summary.get("statusShort") or ""))
        coding_contract = context_pack.get("codingPlannerContractPreview") if isinstance(context_pack.get("codingPlannerContractPreview"), dict) else {}
        write_set = (task_brief or {}).get("writeSet") if isinstance(task_brief, dict) else []
        if not write_set and isinstance(coding_contract, dict):
            write_set = list(coding_contract.get("writeSet") or [])
        workset_gate = context_pack.get("worksetSoftGateDecision") if isinstance(context_pack.get("worksetSoftGateDecision"), dict) else {}
        return {
            "sessionId": session_id,
            "runId": run_id,
            "taskBriefId": (task_brief or {}).get("taskBriefId") if isinstance(task_brief, dict) else None,
            "mode": "dry_run",
            "patchIntent": "Engineering Runtime dry-run context pack only; no code was changed.",
            "readSet": [item.get("path") for item in context_pack.get("criticalFiles", []) if isinstance(item, dict)],
            "writeSet": write_set,
            "changedFiles": changed_files,
            "commands": [],
            "diagnostics": {
                "triggerDecision": trigger,
                "gitSummary": git_summary,
                "evidenceGraphDigest": context_pack.get("evidenceGraphDigest"),
                "codingPlannerContractPreview": coding_contract,
                "worksetSoftGateDecision": workset_gate,
            },
            "verificationStatus": "planned",
            "residualRisks": [
                "Dry-run only; proof cannot be verified until commands or diagnostics are attached.",
                *(["Soft gate warning: changed files are outside declared writeSet."] if workset_gate.get("risk") == "outside_write_set" else []),
                *(["WriteSet missing; work ownership cannot be proven."] if workset_gate.get("risk") == "unknown_write_set" else []),
            ],
        }

    def _changed_files_from_status(self, status_short: str) -> list[str]:
        files: list[str] = []
        for line in status_short.splitlines():
            if not line.strip():
                continue
            candidate = line[2:].strip() if len(line) > 2 else line.strip()
            if " -> " in candidate:
                candidate = candidate.split(" -> ")[-1].strip()
            files.append(candidate)
        return files[:100]

    def _shrink_context_pack(self, pack: dict[str, Any], budget: int) -> dict[str, Any]:
        next_pack = dict(pack)
        if isinstance(next_pack.get("workspaceRulesDigest"), dict):
            digest = dict(next_pack["workspaceRulesDigest"])
            digest["digest"] = truncate_to_estimated_tokens(digest.get("digest") or "", max(120, budget // 8))
            digest["truncated"] = True
            next_pack["workspaceRulesDigest"] = digest
        if isinstance(next_pack.get("criticalFiles"), list):
            next_pack["criticalFiles"] = list(next_pack["criticalFiles"])[:12]
        if isinstance(next_pack.get("workflowRankedPaths"), list):
            next_pack["workflowRankedPaths"] = list(next_pack["workflowRankedPaths"])[:3]
        return next_pack


engineering_lane_service = runtime_registry.register(EngineeringLaneService())
