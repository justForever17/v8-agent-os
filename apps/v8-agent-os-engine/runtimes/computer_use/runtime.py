from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from PIL import Image

try:  # pragma: no cover - environment dependent
    import psutil
except Exception:  # pragma: no cover
    psutil = None

from core.artifact_store import artifact_store
from core.background_context_guard import prepare_background_model_messages
from core.background_model_output import sanitize_background_model_output
from core.database import db
from core.models.factory import llm_factory
from core.local_visual_support import is_local_provider, probe_local_multimodal_capability
from core.models.control_plane import model_control_plane
from core.multimodal_payload_adapter import utc_now_iso
from core.storage import storage
from core.runtime_signal_ingress import build_normalized_signal_payload
from core.v8_agent_os_paths import ensure_v8_agent_os_tmp_path, workspace_artifact_run_root
from core.context.workspace import workspace_resolution_service
from core.tools.vision_media_analyzer import vision_media_analyzer
from core.workspace_guard import ensure_workspace_auto_create_allowed
from runtimes.computer_use.action_policy import (
    binding_allows_profile,
    build_action_policy_metadata,
    promotion_allowed_for_invocation,
)
from runtimes.computer_use.app_adapters import ComputerUseAppAdapterRegistry
from runtimes.computer_use.app_binding_policy import AppBindingDecision, resolve_app_binding
from erc.kernel import erc_kernel
from erc.runtime_control import RuntimeControlInterruption, apply_control_signal, consume_stop_signal
from erc.runtime_context import bind_runtime_context, get_runtime_context
from erc.runtime_registry import runtime_registry
from erc.snapshot_service import snapshot_service
from erc.side_effect_idempotency import side_effect_idempotency_service
from erc.workflow_ledger import workflow_ledger_service
from erc.safety_guardian import safety_guardian
from erc.run_service import run_service
from langchain_core.messages import HumanMessage, SystemMessage
from runtimes.computer_use.app_catalog import ComputerUseAppCatalog
from runtimes.computer_use.app_profiles import ComputerUseAppProfiles
from runtimes.computer_use.browser_automation import BrowserAutomationProvider, BrowserLaneDecision
from runtimes.computer_use.budgeting import (
    build_budget_update_request,
    collect_budget_usage,
    resolve_step_budget,
)
from runtimes.computer_use.candidate_board import build_candidate_board, candidate_board_source_catalog
from runtimes.computer_use.clipboard_payload import normalize_clipboard_payload
from runtimes.computer_use.coordinate_anchor import (
    build_relative_point_candidates,
    build_spatial_anchor,
    offset_relative_point,
    resolve_absolute_click_point,
)
from runtimes.computer_use.capability_matrix import build_runtime_capability_matrix
from runtimes.computer_use.capability_truth import build_capability_truth, screen_wake_policy
from runtimes.computer_use.drivers import DesktopDriverError, create_desktop_driver
from runtimes.computer_use.environment_probes import (
    collect_environment_probe_snapshot,
    environment_probe_capabilities,
    parse_environment_probe_request,
)
from runtimes.computer_use.fallback_policy import recovery_fallback_order, normalize_visual_fallback_payload
from runtimes.computer_use.feedback_policy import build_feedback_suggestions
from runtimes.computer_use.invocation_classifier import classify_computer_use_invocation
from runtimes.computer_use.input_policy import (
    classify_target_input_kind,
    deterministic_input_normalization_required,
)

from runtimes.computer_use.live_matrix_feedback import primitive_live_feedback_for_action
from runtimes.computer_use.observation_bundle import build_observation_bundle
from runtimes.computer_use.platform_adapters import create_platform_discovery_providers
from runtimes.computer_use.playbooks import built_in_playbook_seeds, experience_asset_inventory
from runtimes.computer_use.playbook_executors import (
    PlaybookExecutionContext,
    create_default_playbook_executor_registry,
)
from runtimes.computer_use.platform_probe_runner import build_platform_probe_matrix
from runtimes.computer_use.preflight_policy import build_preflight_context
from runtimes.computer_use.post_action_visual_check import (
    normalize_expected_texts,
    summarize_semantic_post_action_verification,
    summarize_post_action_visual_check,
)
from runtimes.computer_use.task_loop import (
    github_star_click_script,
    github_star_dom_probe_script,
    prepare_task_loop,
)
from runtimes.computer_use.primitives import resolve_computer_use_primitive
from runtimes.computer_use.pure_visual_center_click import resolve_pure_visual_click_point
from runtimes.computer_use.scene_models import build_scene_assessment
from runtimes.computer_use.selector_memory import ComputerUseSelectorMemory
from runtimes.computer_use.short_sequence_verifier import build_short_sequence_verification
from runtimes.computer_use.semantic_targets import (
    generic_input_visual_hint,
    generic_result_visual_hint,
    input_selector_fallback_keys,
    is_action_target_key,
    is_input_target_key,
    is_result_target_key,
    list_selector_fallback_keys,
    should_accept_visual_point,
)
from runtimes.computer_use.target_strategy import (
    apply_target_strategy,
    infer_query_mode,
    is_result_selector_key,
    is_search_selector_key,
    normalize_target_strategy,
    result_region_from_point,
)
from runtimes.computer_use.trace_store import trace_store
from runtimes.computer_use.real_host_matrix import merge_latest_real_host_matrix
from runtimes.computer_use.recovery_policy import build_recovery_policy_metadata
from runtimes.computer_use.route_policy import build_platform_route_policy, decide_execution_route
from runtimes.computer_use.verification_contract import build_result_contract
from runtimes.computer_use.visual_locator_provider import VisualLocatorProvider, create_visual_locator_provider
from runtimes.computer_use.visual_actor_provider import VisualActorRequest, create_visual_actor_provider
from runtimes.computer_use.visual_locator_runtime import _normalize_ocr_query
from runtimes.computer_use.visual_locator_scope import (
    crop_capture_image_to_bounds,
    derive_centered_dialog_seed_bounds,
    expand_scope_bounds,
    split_locator_candidates,
)
from runtimes.computer_use.visual_dialog_observer import observe_centered_dialog_scope
from runtimes.computer_use.visual_locator_ranking import (
    infer_visual_locator_chain_role,
    merge_visual_locator_candidate_resolutions,
    rank_visual_locator_resolution,
)
from runtimes.computer_use.visual_semantic_candidates import (
    build_semantic_visual_candidates,
    semantic_candidates_to_resolution,
)
from runtimes.computer_use.visual_observation_contract import (
    build_visual_judge_suggestion,
    summarize_visual_observation,
)


_ENVIRONMENT_INTERRUPT_COOLDOWNS: Dict[str, float] = {}
from runtimes.computer_use.visual_judge import run_visual_judge
from runtimes.computer_use.window_scene import (
    build_window_binding_assessment,
    choose_best_window_candidate,
    infer_window_page_identity,
    is_shell_surface_window,
    requires_strict_window_binding,
    should_replace_window_context,
    window_satisfies_binding,
)
from runtimes.computer_use.window_hints import derive_window_title_hints
from runtimes.computer_use.types import (
    ComputerUseActionResult,
    ComputerUseObservation,
    ComputerUseTraceBudget,
    ComputerUseTracePrimitive,
    ComputerUseTraceRecovery,
    ComputerUseTraceRisk,
    ComputerUseTraceScene,
    ComputerUseTraceStep,
    ComputerUseTraceTarget,
    ComputerUseTraceTiming,
    ComputerUseTraceVariable,
    ComputerUseVerification,
)

from runtimes.computer_use.verification import normalize_verification_payload


_WORKBENCH_BROWSER_CONTROL_ERRORS = {
    "browser_user_control_active",
    "browser_reobserve_required",
}


def _raise_if_workbench_browser_control_error(exc: Exception) -> None:
    if str(getattr(exc, "code", "") or "").strip() in _WORKBENCH_BROWSER_CONTROL_ERRORS:
        raise exc


def _display_bounds_from_capture(capture_bounds: List[int] | None) -> Dict[str, Any]:
    if not isinstance(capture_bounds, list) or len(capture_bounds) != 4:
        return {}
    try:
        x0 = float(capture_bounds[0])
        y0 = float(capture_bounds[1])
        x1 = float(capture_bounds[2])
        y1 = float(capture_bounds[3])
    except Exception:
        return {}
    width = x1 - x0 if x1 > x0 else x1
    height = y1 - y0 if y1 > y0 else y1
    if width <= 0 or height <= 0:
        return {}
    return {"x": x0, "y": y0, "width": width, "height": height}


class ComputerUseRuntime:
    kind = "computer_use"

    def _offline_visual_benchmark_descriptor(self) -> Dict[str, Any]:
        return {
            "available": True,
            "mode": "offline_validation_only",
            "parserAdapters": [
                "precomputed_visual_parser",
                "rpa_desktop_visual_locator",
                "null_visual_parser",
            ],
            "script": str(Path(__file__).resolve().parents[2] / "scripts" / "offline_visual_benchmark.py"),
            "sampleManifest": str(Path(__file__).resolve().parents[2] / "scripts" / "offline_visual_benchmark.sample.json"),
            "doctorScript": str(Path(__file__).resolve().parents[2] / "scripts" / "offline_visual_parser_doctor.py"),
            "notes": [
                "当前只支持离线截图解析 benchmark，不接入主执行链。",
                "适用于验证 page identity / blocker / hit zone / affordance 设计。",
                "支持将 RPA.Desktop 作为统一视觉定位层做离线预计算 benchmark 接线。",
            ],
        }

    def _online_visual_locator_descriptor(self) -> Dict[str, Any]:
        try:
            return dict(self.visual_locator_runtime.availability_summary() or {})
        except Exception as exc:
            return {
                "providerId": "rpa_desktop_visual_locator",
                "status": "error",
                "runtimeAvailable": False,
                "mode": "online_locator_only",
                "notes": [f"读取在线视觉定位层状态失败: {exc}"],
            }

    def _visual_actor_descriptor(self) -> Dict[str, Any]:
        try:
            return dict(self.visual_actor_provider.availability_summary() or {})
        except Exception as exc:
            return {
                "providerId": "computer_use_visual_actor_provider",
                "available": False,
                "mode": "proposal_only_candidate_board_first",
                "reason": f"读取视觉动作提案层状态失败: {exc}",
            }

    def _browser_profile_persistence_payload(self, browser_lane: Dict[str, Any] | None = None) -> Dict[str, Any]:
        summary = dict(browser_lane or self.browser_automation.availability_summary() or {})
        return {
            "enabled": str(summary.get("profileMode") or "") == "dedicated_debug_profile",
            "profileMode": summary.get("profileMode"),
            "profileRoot": summary.get("profileRoot"),
            "defaultUserDataDir": summary.get("defaultUserDataDir"),
            "debugPort": summary.get("targetPort"),
            "cleanupPolicy": "close_run_owned_tabs_only_keep_profile_cookies_localStorage",
            "persistsCookiesLocalStorage": True,
            "notes": [
                "Computer Use 默认使用 V8 专用 debug profile，不复用用户默认浏览器 profile。",
                "登录态、cookies、localStorage 会保留；run cleanup 不删除该 profile。",
            ],
        }

    def _platform_probe_matrix_payload(self, *, browser_lane: Dict[str, Any] | None = None) -> Dict[str, Any]:
        matrix = build_platform_probe_matrix(
            current_platform=str(getattr(self.driver, "platform", "") or ""),
            driver_summary={
                "available": self.driver.is_available(),
                "platform": getattr(self.driver, "platform", None),
                "backend": getattr(self.driver, "backend", None),
            },
            browser_summary=dict(browser_lane or self.browser_automation.availability_summary() or {}),
        )
        return merge_latest_real_host_matrix(matrix)

    def build_candidate_board(
        self,
        *,
        goal: str,
        locator_resolution: Dict[str, Any] | None = None,
        visual_observation: Dict[str, Any] | None = None,
        observation: Dict[str, Any] | None = None,
        selector_memory_candidates: List[Dict[str, Any]] | None = None,
        browser_candidates: List[Dict[str, Any]] | None = None,
        history_candidates: List[Dict[str, Any]] | None = None,
    ) -> Dict[str, Any]:
        board = build_candidate_board(
            goal=goal,
            locator_resolution=locator_resolution,
            visual_observation=visual_observation,
            observation=observation,
            selector_memory_candidates=selector_memory_candidates,
            browser_candidates=browser_candidates,
            history_candidates=history_candidates,
        )
        return board.as_dict()

    def propose_visual_actor_action(
        self,
        *,
        goal: str,
        candidate_board: Dict[str, Any],
        screenshot_artifact_id: str | None = None,
        screenshot_path: str | None = None,
        display_bounds: Dict[str, Any] | None = None,
        previous_frame_summary: str | None = None,
    ) -> Dict[str, Any]:
        request = VisualActorRequest(
            goal=goal,
            screenshotArtifactId=screenshot_artifact_id,
            screenshotPath=screenshot_path,
            candidateBoard=dict(candidate_board or {}),
            previousFrameSummary=previous_frame_summary,
            displayBounds=dict(display_bounds or {}),
        )
        return self.visual_actor_provider.propose(request).as_dict()

    def runtime_descriptor(self) -> dict[str, Any]:
        browser_lane = self.browser_automation.availability_summary()
        return {
            "kind": self.kind,
            "displayName": "ComputerUseRuntime",
            "summary": "负责本机桌面/窗口交互、结构化观察与高风险视觉保底，不承担通用聊天推理。",
            "responsibilities": [
                "执行桌面观察、定位、点击、输入和短视距计划",
                "处理 UIA/Win32 回退与视觉保底",
                "为 RPA 提供探索、回退和局部修补来源",
                "在学习模式下执行 observe -> act -> verify -> decide 闭环",
            ],
            "routingKeywords": ["桌面操作", "窗口", "点击", "输入", "电脑使用", "本机交互"],
            "acceptedInputs": ["plan steps", "window binding", "visual guard policy"],
            "producedOutputs": ["desktop observations", "artifacts", "trace runs"],
            "ownedSteps": ["computer_use.observe", "computer_use.execute_plan", "computer_use.visual_guard"],
            "supportsPause": True,
            "supportsResume": False,
            "supportsApproval": True,
            "supportsRepair": True,
            "visibility": "specialized",
            "promptHints": [
                "用法入口：涉及本机 GUI、窗口、文件对话框、真人登录态浏览器或社交通讯应用时，通过 runtime_broker(mode='route', need={'kind':'computer_use', ...}) 创建 episode；输入 goal、app/window 线索、allowedActions、安全/登录态边界。",
                "执行流程：Computer Use 自己 observe -> plan -> act -> verify，高风险动作配合视觉保底；Supervisor 不猜坐标、不编造桌面状态、不把原始视觉网格当事实。",
                "边界：只有用户明确要求真实桌面终端、GUI 终端、桌面登录态或必须操作真实窗口时才交给 ComputerUseRuntime；可复用流程、模板、对象库和回放应转 RPA 固化。",
                "回流要求：typed handoff 必须给 observedState/actionsTaken/verification/screenshotOrTraceRef/humanAttention/limitations/detailRef；driver trace、坐标候选和 OCR raw 只进 Runtime Surface。",
                "当不存在可复用肌肉记忆时进入学习模式，而不是继续脚本式盲操。",
            ],
            "capabilities": [
                {
                    "key": "computer_use.desktop_control",
                    "label": "桌面结构化控制",
                    "summary": "执行窗口聚焦、输入、点击、滚动和高风险确认。",
                    "accepts": ["goal", "step list", "window selectors"],
                    "outputs": ["execution result", "trace", "screenshot artifact"],
                    "examples": ["打开应用并完成一组窗口操作", "作为 RPA 失败后的回退执行器"],
                    "risk_level": "high",
                },
                {
                    "key": "computer_use.offline_visual_benchmark",
                    "label": "离线视觉解析验证",
                    "summary": "对离线视觉解析器做截图 benchmark，不驱动在线动作。",
                    "accepts": ["benchmark manifest", "precomputed predictions", "screenshots"],
                    "outputs": ["benchmark report", "page identity candidates", "candidate hit zones"],
                    "examples": ["验证顶部输入区的可解析性", "验证内容接收区与主操作区的视觉语义"],
                    "risk_level": "low",
                },
                {
                    "key": "computer_use.visual_locator_runtime",
                    "label": "在线统一视觉定位层",
                    "summary": "使用 RPA.Desktop 提供跨平台视觉找位能力，仅负责找位与读位，不直接执行点击输入。",
                    "accepts": ["visual locator", "confidence", "timeout"],
                    "outputs": ["visual matches", "bbox", "center point", "ocr text"],
                    "examples": ["对自绘按钮做图像找位", "为 hover/right_click/drag 提供视觉落点"],
                    "risk_level": "medium",
                },
            ],
            "metadata": {
                "managedToolPrefixes": ["computer_use_"],
                "managedToolGroups": ["computer_use.control"],
                "offlineVisualBenchmark": self._offline_visual_benchmark_descriptor(),
                "onlineVisualLocator": self._online_visual_locator_descriptor(),
                "browserLane": self.browser_automation.availability_summary(),
                "environmentProbes": environment_probe_capabilities(),
                "screenWakePolicy": screen_wake_policy(),
                **self._resolution_policy_payload(include_current_display=False),
                "builtInPlaybookSeeds": built_in_playbook_seeds(),
                "visualActor": self._visual_actor_descriptor(),
                "candidateBoardSources": candidate_board_source_catalog(),
                "browserProfilePersistence": self._browser_profile_persistence_payload(browser_lane),
                "platformProbeMatrix": self._platform_probe_matrix_payload(browser_lane=browser_lane),
            },
        }

    def __init__(self) -> None:
        self.driver = create_desktop_driver()
        self.visual_locator_runtime: VisualLocatorProvider = create_visual_locator_provider()
        self.visual_actor_provider = create_visual_actor_provider()
        self.browser_automation = BrowserAutomationProvider()
        self.playbook_executor_registry = create_default_playbook_executor_registry()
        self.app_adapters = ComputerUseAppAdapterRegistry()
        self.app_profiles = ComputerUseAppProfiles()
        self.app_catalog = ComputerUseAppCatalog(
            app_profiles=self.app_profiles,
            app_adapters=self.app_adapters,
            platform_providers=create_platform_discovery_providers(driver=self.driver),
        )
        self.selector_memory = ComputerUseSelectorMemory()
        self.trace_store = trace_store
        self._recent_visual_locator_hits: Dict[tuple[str, str, str, str], Dict[str, Any]] = {}
        self._screen_wake_attempts: Dict[str, float] = {}
        self._resource_leases: Dict[str, Dict[str, Any]] = {}
        self._resource_lease_lock = threading.Lock()
        self._runtime_ready = False
        self._runtime_ready_lock = threading.Lock()

    def _computer_use_config(self) -> Dict[str, Any]:
        return dict(storage.get_computer_use_config() or {})

    def _browser_lane_config(self) -> Dict[str, Any]:
        config = self._computer_use_config()
        self.browser_automation.configure(config)
        return dict(config.get("browserLane") or {})

    def _observation_policy_config(self) -> Dict[str, Any]:
        return dict(self._computer_use_config().get("observationPolicy") or {})

    def _input_policy_config(self) -> Dict[str, Any]:
        return dict(self._computer_use_config().get("inputPolicy") or {})

    def _ensure_runtime_ready(self) -> None:
        if self._runtime_ready:
            return
        with self._runtime_ready_lock:
            if self._runtime_ready:
                return
            self.app_catalog.warm_start()
            self.browser_automation.configure(self._computer_use_config())
            self._runtime_ready = True

    def workbench_browser_provider(self) -> BrowserAutomationProvider:
        """Return a configured browser provider for the Web Workbench surface."""

        self._ensure_runtime_ready()
        # Configuration can change while Engine remains alive. Refresh it at the
        # public surface boundary so the first Workbench launch does not depend
        # on an earlier Computer Use availability probe.
        self.browser_automation.configure(self._computer_use_config())
        return self.browser_automation

    def _resource_lease_key(self, *, run_handle) -> str:
        return str(getattr(run_handle, "run_id", "") or "default")

    def _resource_lease_for(self, *, run_handle) -> Dict[str, Any]:
        key = self._resource_lease_key(run_handle=run_handle)
        with self._resource_lease_lock:
            lease = self._resource_leases.get(key)
            if not isinstance(lease, dict):
                lease = {
                    "runId": key,
                    "resources": [],
                    "createdAt": utc_now_iso(),
                    "lastCleanup": None,
                }
                self._resource_leases[key] = lease
            return lease

    def _record_resource_lease(
        self,
        *,
        run_handle,
        kind: str,
        resource: Dict[str, Any] | None,
        cleanup_on_complete: bool = True,
        preserve_on_human_input: bool = False,
        delayed_cleanup_seconds: int | None = None,
        reason: str | None = None,
    ) -> Dict[str, Any]:
        lease = self._resource_lease_for(run_handle=run_handle)
        entry = {
            "id": f"lease_{uuid.uuid4().hex[:10]}",
            "kind": str(kind or "resource").strip() or "resource",
            "resource": dict(resource or {}),
            "cleanupOnComplete": bool(cleanup_on_complete),
            "preserveOnHumanInput": bool(preserve_on_human_input),
            "delayedCleanupSeconds": max(int(delayed_cleanup_seconds or 0), 0),
            "reason": str(reason or "").strip() or None,
            "createdAt": utc_now_iso(),
            "cleanupStatus": "pending" if cleanup_on_complete else "not_owned_for_cleanup",
        }
        with self._resource_lease_lock:
            resources = list(lease.get("resources") or [])
            resources.append(entry)
            lease["resources"] = resources
            lease["updatedAt"] = utc_now_iso()
        try:
            run_handle.emit("computer_use.resource_lease.created", entry)
        except Exception:
            pass
        return entry

    def _resource_lease_summary(self, *, run_handle) -> Dict[str, Any]:
        lease = self._resource_lease_for(run_handle=run_handle)
        resources = [dict(item) for item in list(lease.get("resources") or []) if isinstance(item, dict)]
        cleanup_counts: Dict[str, int] = {}
        for item in resources:
            status = str(item.get("cleanupStatus") or "unknown")
            cleanup_counts[status] = cleanup_counts.get(status, 0) + 1
        return {
            "runId": lease.get("runId"),
            "resourceCount": len(resources),
            "cleanupCounts": cleanup_counts,
            "resources": resources,
            "lastCleanup": dict(lease.get("lastCleanup") or {}) or None,
        }

    def _cleanup_resource_lease(
        self,
        *,
        run_handle,
        status: str,
        reason: str | None = None,
    ) -> Dict[str, Any]:
        normalized_status = str(status or "").strip().lower()
        preserve_statuses = {
            "needs_human_login",
            "needs_human_attention",
            "waiting_input",
            "waiting_approval",
            "screen_wake_requires_human_attention",
            "credential_boundary",
            "failed",
            "review_required",
        }
        lease = self._resource_lease_for(run_handle=run_handle)
        resources = [dict(item) for item in list(lease.get("resources") or []) if isinstance(item, dict)]
        cleanup_report = {
            "status": "skipped" if normalized_status in preserve_statuses else "completed",
            "reason": str(reason or normalized_status or "").strip() or None,
            "runStatus": normalized_status,
            "closed": [],
            "skipped": [],
            "errors": [],
            "completedAt": utc_now_iso(),
        }
        if normalized_status in preserve_statuses:
            for item in resources:
                if str(item.get("cleanupStatus") or "") == "pending":
                    item["cleanupStatus"] = "skipped_preserved_for_human_attention"
                cleanup_report["skipped"].append(
                    {
                        "id": item.get("id"),
                        "kind": item.get("kind"),
                        "reason": "preserve_for_human_or_debug",
                    }
                )
            topic = "computer_use.resource_lease.cleanup_skipped"
        else:
            topic = "computer_use.resource_lease.cleanup_completed"
            for item in resources:
                if not bool(item.get("cleanupOnComplete")):
                    cleanup_report["skipped"].append(
                        {"id": item.get("id"), "kind": item.get("kind"), "reason": "cleanup_not_owned"}
                    )
                    continue
                resource = dict(item.get("resource") or {})
                kind = str(item.get("kind") or "").strip().lower()
                delay_seconds = 0
                try:
                    delay_seconds = max(int(item.get("delayedCleanupSeconds") or 0), 0)
                except Exception:
                    delay_seconds = 0
                if delay_seconds > 0:
                    item["cleanupStatus"] = "scheduled_delayed"
                    item["scheduledCleanupAt"] = (
                        datetime.fromtimestamp(time.time() + delay_seconds, timezone.utc)
                        .replace(microsecond=0)
                        .isoformat()
                        .replace("+00:00", "Z")
                    )
                    cleanup_report["skipped"].append(
                        {
                            "id": item.get("id"),
                            "kind": item.get("kind"),
                            "reason": "delayed_cleanup_scheduled",
                            "scheduledCleanupAt": item.get("scheduledCleanupAt"),
                        }
                    )
                    if not item.get("delayedCleanupScheduled"):
                        item["delayedCleanupScheduled"] = True
                        self._schedule_delayed_resource_cleanup(
                            run_id=str(run_handle.run_id),
                            lease_id=str(item.get("id") or ""),
                            kind=kind,
                            resource=resource,
                            delay_seconds=delay_seconds,
                        )
                    continue
                try:
                    if kind == "browser_tab":
                        target_id = str(resource.get("targetId") or resource.get("target_id") or "").strip()
                        if not target_id:
                            item["cleanupStatus"] = "skipped_missing_target"
                            cleanup_report["skipped"].append(
                                {"id": item.get("id"), "kind": item.get("kind"), "reason": "missing_target_id"}
                            )
                            continue
                        target_port = None
                        try:
                            target_port = int(resource.get("targetPort")) if resource.get("targetPort") not in (None, "") else None
                        except Exception:
                            target_port = None
                        close_result = self.browser_automation.close_tab(target_id=target_id, target_port=target_port)
                        if close_result.get("closed"):
                            item["cleanupStatus"] = "closed"
                            cleanup_report["closed"].append(
                                {"id": item.get("id"), "kind": item.get("kind"), "targetId": target_id}
                            )
                        else:
                            item["cleanupStatus"] = "close_failed"
                            cleanup_report["errors"].append(
                                {
                                    "id": item.get("id"),
                                    "kind": item.get("kind"),
                                    "targetId": target_id,
                                    "error": close_result.get("error") or close_result.get("reason"),
                                }
                            )
                    else:
                        item["cleanupStatus"] = "skipped_unsupported_cleanup"
                        cleanup_report["skipped"].append(
                            {"id": item.get("id"), "kind": item.get("kind"), "reason": "unsupported_cleanup"}
                        )
                except Exception as exc:
                    item["cleanupStatus"] = "cleanup_error"
                    cleanup_report["errors"].append(
                        {"id": item.get("id"), "kind": item.get("kind"), "error": str(exc)}
                    )
        with self._resource_lease_lock:
            lease["resources"] = resources
            lease["lastCleanup"] = cleanup_report
            lease["updatedAt"] = utc_now_iso()
        try:
            run_handle.emit(topic, cleanup_report)
        except Exception:
            pass
        summary = self._resource_lease_summary(run_handle=run_handle)
        summary["cleanup"] = cleanup_report
        return summary

    def _schedule_delayed_resource_cleanup(
        self,
        *,
        run_id: str,
        lease_id: str,
        kind: str,
        resource: Dict[str, Any],
        delay_seconds: int,
    ) -> None:
        if delay_seconds <= 0 or not run_id or not lease_id:
            return

        def _cleanup_later() -> None:
            threading.Event().wait(delay_seconds)
            result: Dict[str, Any] = {"closed": False, "reason": "unsupported_delayed_cleanup"}
            try:
                if kind == "browser_tab":
                    target_id = str(resource.get("targetId") or resource.get("target_id") or "").strip()
                    target_port = None
                    try:
                        target_port = int(resource.get("targetPort")) if resource.get("targetPort") not in (None, "") else None
                    except Exception:
                        target_port = None
                    if target_id:
                        result = self.browser_automation.close_tab(target_id=target_id, target_port=target_port)
                    else:
                        result = {"closed": False, "reason": "missing_target_id"}
            except Exception as exc:
                result = {"closed": False, "error": str(exc)}
            with self._resource_lease_lock:
                lease = self._resource_leases.get(run_id)
                if not lease:
                    return
                for item in list(lease.get("resources") or []):
                    if str(item.get("id") or "") != lease_id:
                        continue
                    if str(item.get("cleanupStatus") or "") != "scheduled_delayed":
                        return
                    item["cleanupStatus"] = "closed_delayed" if result.get("closed") else "delayed_close_failed"
                    item["delayedCleanupResult"] = dict(result or {})
                    item["updatedAt"] = utc_now_iso()
                    lease["updatedAt"] = utc_now_iso()
                    return

        thread = threading.Thread(target=_cleanup_later, name=f"v8-computer-use-delayed-cleanup-{lease_id}", daemon=True)
        thread.start()

    def _human_input_request_payload(
        self,
        *,
        reason: str,
        target_url: str | None = None,
        browser_target: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        normalized_reason = str(reason or "needs_human_attention").strip() or "needs_human_attention"
        question = (
            "Computer Use 需要你在 V8 专用浏览器窗口里手动完成登录或确认，然后回复“已完成”。"
        )
        return {
            "interactionKind": "ask_user",
            "reason": normalized_reason,
            "question": question,
            "prompt": question,
            "instructions": [
                "请在已打开的 V8 专用浏览器窗口中完成登录/验证。",
                "不要把密码发给 agent；除非是你明确提供的临时测试账号。",
                "完成后回复“已完成”，Computer Use 会继续观察并验证目标状态。",
            ],
            "targetUrl": str(target_url or "").strip() or None,
            "browserTarget": dict(browser_target or {}) or None,
        }

    def _resolution_policy_payload(self, *, include_current_display: bool = True) -> Dict[str, Any]:
        current_display: Dict[str, Any] = {"status": "unknown"}
        if include_current_display:
            try:
                observation = self.driver.observe_desktop(depth_limit=0, element_limit=0, use_cache=True).as_dict()
                metadata = dict(observation.get("metadata") or {})
                current_display = {
                    "status": "observed" if metadata else "unknown",
                    "displayId": metadata.get("displayId"),
                    "displayBounds": metadata.get("displayBounds"),
                    "windowBounds": metadata.get("windowBounds"),
                    "dpiScale": metadata.get("dpiScale"),
                }
            except Exception as exc:
                current_display = {"status": "unavailable", "reason": str(exc)}
        return {
            "resolutionPolicy": {
                "coordinatePriority": [
                    "browser_dom_or_accessibility",
                    "selector_memory_with_anchor",
                    "coordinate_anchor",
                    "visual_fallback",
                ],
                "rawAbsoluteCoordinates": "fallback_only",
                "mismatchBehavior": "downgrade_or_block_coordinate_anchor",
            },
            "currentDisplay": current_display,
            "coordinateAnchorPolicy": {
                "requiredContext": ["displayBounds", "windowBounds", "dpiScale", "screenRelativePoint"],
                "displayOrDpiMismatch": "do_not_reuse_anchor",
                "windowSizeMismatchThreshold": 0.10,
            },
            "resourceCleanupPolicy": {
                "closeRunOwnedBrowserTabsOnSuccess": True,
                "preserveDedicatedBrowserProfile": True,
                "preserveHumanInputWindows": True,
                "cleanupUnsupportedResources": "audit_only",
            },
        }

    def _classify_invocation(self, invocation_metadata: Optional[Dict[str, Any]] = None, *, default_trigger_source: str) -> Any:
        return classify_computer_use_invocation(invocation_metadata, default_trigger_source=default_trigger_source)

    def _resolve_app_binding(
        self,
        *,
        explicit_app_id: str | None = None,
        window_title: str | None = None,
        class_name: str | None = None,
        app_name: str | None = None,
        include_running: bool = True,
    ) -> AppBindingDecision:
        return resolve_app_binding(
            app_profiles=self.app_profiles,
            app_catalog=self.app_catalog,
            explicit_app_id=explicit_app_id,
            window_title=window_title,
            class_name=class_name,
            app_name=app_name,
            include_running=include_running,
        )

    def _binding_metadata(self, binding_decision: AppBindingDecision | None = None) -> Dict[str, Any]:
        if binding_decision is None:
            return {
                "bindingMode": "none",
                "bindingConfidence": 0.0,
                "requestedAppId": None,
                "resolvedAppId": None,
                "bindingEvidence": {},
                "profileEligible": False,
            }
        return build_action_policy_metadata(binding_decision=binding_decision, invocation=None)

    def _attach_binding_to_payload(self, payload: Dict[str, Any], binding_decision: AppBindingDecision | None) -> Dict[str, Any]:
        normalized = dict(payload or {})
        metadata = self._binding_metadata(binding_decision)
        normalized["_binding_mode"] = metadata.get("bindingMode")
        normalized["_binding_confidence"] = metadata.get("bindingConfidence")
        normalized["_binding_evidence"] = dict(metadata.get("bindingEvidence") or {})
        normalized["_requested_app_id"] = metadata.get("requestedAppId")
        normalized["_resolved_app_id"] = metadata.get("resolvedAppId")
        normalized["_profile_eligible"] = bool(metadata.get("profileEligible"))
        if metadata.get("requestedAppId") and not normalized.get("requested_app_id"):
            normalized["requested_app_id"] = metadata.get("requestedAppId")
        if metadata.get("resolvedAppId") and not normalized.get("resolved_app_id"):
            normalized["resolved_app_id"] = metadata.get("resolvedAppId")
        return normalized

    def _browser_lane_decision(
        self,
        *,
        action_type: str,
        action_payload: Dict[str, Any],
        app_id: str | None = None,
        process_name: str | None = None,
    ) -> BrowserLaneDecision:
        self.browser_automation.configure(self._computer_use_config())
        return self.browser_automation.decide_lane(
            action_type=action_type,
            action_payload=action_payload,
            app_id=app_id or action_payload.get("app_id") or action_payload.get("resolved_app_id"),
            window_title=action_payload.get("window_title"),
            class_name=action_payload.get("class_name"),
            process_name=process_name,
        )

    def _build_browser_lane_metadata(self, decision: BrowserLaneDecision) -> Dict[str, Any]:
        payload = decision.as_dict()
        payload["available"] = bool(decision.available)
        return payload

    def _app_adapter_summary(self) -> Dict[str, Any]:
        summary = self.app_adapters.capability_summary()
        summary["available"] = bool(summary.get("available"))
        summary["implemented"] = bool(summary.get("implemented"))
        return summary

    def _platform_capability_inputs(self) -> Dict[str, Dict[str, Any]]:
        payload: Dict[str, Dict[str, Any]] = {}
        current_platform = str(getattr(self.driver, "platform", "") or "")
        try:
            payload[current_platform] = dict(self.driver.capability_summary() or {})
        except Exception:
            payload[current_platform] = {}
        lazy_factories = (
            ("windows", "runtimes.computer_use.drivers.windows_uia", "WindowsUIADriver"),
            ("macos", "runtimes.computer_use.drivers.mac_ax", "MacAXUIDriver"),
            ("linux", "runtimes.computer_use.drivers.linux_atspi", "LinuxATSPIADriver"),
        )
        for name, module_name, class_name in lazy_factories:
            if name in payload:
                continue
            try:
                module = __import__(module_name, fromlist=[class_name])
                factory = getattr(module, class_name)
                payload[name] = dict(factory().capability_summary() or {})
            except Exception:
                payload[name] = {"platform": name, "backend": "unavailable"}
        return payload

    def _runtime_capability_matrix(self) -> Dict[str, Any]:
        current_platform = str(getattr(self.driver, "platform", "") or os.name)
        return build_runtime_capability_matrix(
            current_platform=current_platform,
            platform_capabilities=self._platform_capability_inputs(),
            browser_lane=self.browser_automation.lane_capabilities(),
            app_adapter=self._app_adapter_summary(),
        )

    def _capability_truth_payload(
        self,
        *,
        capability_matrix: Dict[str, Any] | None = None,
        browser_lane: Dict[str, Any] | None = None,
        app_catalog_summary: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        matrix = dict(capability_matrix or self._runtime_capability_matrix())
        resolved_app_catalog_summary = dict(app_catalog_summary or {})
        selector_stats = {}
        if app_catalog_summary is None:
            try:
                resolved_app_catalog_summary = self.app_catalog.summary(include_running=True)
            except Exception:
                resolved_app_catalog_summary = {}
        try:
            selector_stats = self.driver.selector_metrics()
        except Exception:
            selector_stats = {}
        resolved_browser_lane = dict(browser_lane or self.browser_automation.availability_summary() or {})
        truth = build_capability_truth(
            capability_matrix=matrix,
            browser_lane=resolved_browser_lane,
            app_catalog_summary=resolved_app_catalog_summary,
            app_adapter_summary=self._app_adapter_summary(),
        )
        truth["experienceAssets"] = experience_asset_inventory(
            app_profiles=self.app_profiles.list_profiles(),
            app_catalog_summary=resolved_app_catalog_summary,
            selector_stats=selector_stats,
        )
        truth["builtInPlaybookSeeds"] = built_in_playbook_seeds()
        return truth

    def _platform_route_policy_summary(self, *, capability_truth: Dict[str, Any] | None = None) -> Dict[str, Any]:
        current_platform = str(getattr(self.driver, "platform", "") or os.name)
        return build_platform_route_policy(
            platform_name=current_platform,
            capability_truth=dict(capability_truth or {}),
        )

    def _control_class_for_action(
        self,
        *,
        binding_decision: AppBindingDecision | None = None,
        catalog_entry: Dict[str, Any] | None = None,
        target: Dict[str, Any] | None = None,
        action_payload: Dict[str, Any] | None = None,
    ) -> str | None:
        for source in (
            catalog_entry or {},
            getattr(binding_decision, "catalog_entry", None) or {},
            target or {},
            action_payload or {},
        ):
            token = str(source.get("controlClass") or source.get("control_class") or "").strip()
            if token:
                return token
        return None

    def _prepare_input_preflight(
        self,
        *,
        action_payload: Dict[str, Any],
        browser_decision: BrowserLaneDecision | None = None,
    ) -> Dict[str, Any] | None:
        input_policy = self._input_policy_config()
        text = str(action_payload.get("text") or "")
        target_input_kind = classify_target_input_kind(
            action_payload=action_payload,
            text=text,
            browser_lane_active=bool(browser_decision and browser_decision.available),
            browser_family=(browser_decision.family if browser_decision else None),
        )
        if not hasattr(self.driver, "preflight_text_input_context"):
            return {
                "targetInputKind": target_input_kind,
                "normalizationApplied": False,
                "inputStrategy": None,
            }
        normalization_requested = bool(input_policy.get("normalizeDeterministicTextIme", True)) and deterministic_input_normalization_required(
            target_input_kind
        )
        try:
            preflight = self.driver.preflight_text_input_context(
                text=text,
                target_input_kind=target_input_kind,
                window_handle=action_payload.get("window_handle"),
                normalization_requested=normalization_requested,
            )
        except Exception as exc:
            preflight = {
                "targetInputKind": target_input_kind,
                "normalizationApplied": False,
                "error": str(exc),
            }
        preflight["targetInputKind"] = target_input_kind
        return preflight

    def _restore_input_preflight(self, preflight: Dict[str, Any] | None) -> Dict[str, Any] | None:
        if not preflight or not hasattr(self.driver, "restore_text_input_context"):
            return preflight
        try:
            return self.driver.restore_text_input_context(preflight)
        except Exception as exc:
            payload = dict(preflight)
            payload["restoreApplied"] = False
            payload["restoreError"] = str(exc)
            return payload

    def _attach_input_preflight_metadata(
        self,
        *,
        result: Dict[str, Any],
        preflight: Dict[str, Any] | None,
    ) -> Dict[str, Any]:
        if not preflight:
            return result
        metadata = dict(result.get("metadata") or {})
        metadata.setdefault("targetInputKind", preflight.get("targetInputKind"))
        metadata["imeStateBefore"] = preflight.get("imeStateBefore")
        metadata["imeStateAfter"] = preflight.get("imeStateAfter")
        metadata["layoutBefore"] = preflight.get("layoutBefore")
        metadata["layoutAfter"] = preflight.get("layoutAfter")
        metadata["normalizationApplied"] = bool(preflight.get("normalizationApplied"))
        metadata["restoreApplied"] = bool(preflight.get("restoreApplied"))
        if preflight.get("inputStrategy") not in (None, ""):
            metadata["inputStrategy"] = preflight.get("inputStrategy")
        result["metadata"] = metadata
        return result

    def _desktop_live_observation_context(self) -> Dict[str, Any]:
        try:
            from core.desktop_live import desktop_live_service
        except Exception as exc:
            return {
                "source": "computer_use_local_capture",
                "sessionId": None,
                "frameTimestamp": None,
                "frameArtifactId": None,
                "frameRef": None,
                "error": str(exc),
            }
        try:
            context = desktop_live_service.get_observation_context()
            if isinstance(context, dict) and context:
                return dict(context)
        except Exception as exc:
            return {
                "source": "computer_use_local_capture",
                "sessionId": None,
                "frameTimestamp": None,
                "frameArtifactId": None,
                "frameRef": None,
                "error": str(exc),
            }
        return {
            "source": "computer_use_local_capture",
            "sessionId": None,
            "frameTimestamp": None,
            "frameArtifactId": None,
            "frameRef": None,
        }

    def _merge_semantic_verification(
        self,
        *,
        action_type: str,
        verification: ComputerUseVerification,
        observation_bundle: Dict[str, Any] | None,
        action_payload: Dict[str, Any],
    ) -> ComputerUseVerification:
        details = dict(verification.details or {})
        semantic = summarize_semantic_post_action_verification(
            action_type=action_type,
            action_payload=action_payload,
            verification_details=details,
            observation_bundle=observation_bundle,
        )
        details["semanticVerificationStatus"] = semantic.get("status")
        details["semanticEvidenceType"] = semantic.get("evidenceType")
        details["semanticEvidenceSummary"] = semantic.get("evidenceSummary")
        details["frameSequenceSamplingAvailable"] = bool(semantic.get("frameSequenceSamplingAvailable"))
        details["frameSequenceSemanticVerificationAvailable"] = bool(semantic.get("frameSequenceSemanticVerificationAvailable"))
        if not semantic.get("available"):
            return ComputerUseVerification(
                passed=verification.passed,
                status=verification.status,
                reason=verification.reason,
                details=details,
                level=verification.level,
            )

        semantic_passed = bool(semantic.get("passed"))
        semantic_status = str(semantic.get("status") or "").strip() or verification.status
        semantic_reason = str(semantic.get("reason") or "").strip() or verification.reason
        semantic_level = str(semantic.get("level") or "").strip() or verification.level
        normalized_action = str(action_type or "").strip().lower()
        downgrade_verified_actions = {"open_app", "focus_window", "type_text", "scroll", "paste_files"}

        if semantic_passed:
            if verification.level in {"executed_only", "soft_verified", "review_required"}:
                return ComputerUseVerification(
                    passed=True,
                    status=semantic_status,
                    reason=semantic_reason,
                    details=details,
                    level=semantic_level,
                )
            return ComputerUseVerification(
                passed=verification.passed,
                status=verification.status,
                reason=verification.reason,
                details=details,
                level=verification.level,
            )

        if semantic_level == "failed" and verification.level != "failed" and normalized_action in downgrade_verified_actions:
            return ComputerUseVerification(
                passed=False,
                status=semantic_status,
                reason=semantic_reason,
                details=details,
                level="failed",
            )

        if semantic_level == "review_required":
            if verification.level in {"executed_only", "soft_verified"} or normalized_action in downgrade_verified_actions:
                return ComputerUseVerification(
                    passed=False,
                    status=semantic_status,
                    reason=semantic_reason,
                    details=details,
                    level="review_required",
                )

        return ComputerUseVerification(
            passed=verification.passed,
            status=verification.status,
            reason=verification.reason,
            details=details,
            level=verification.level,
        )

    def _pop_invocation_metadata(self, payload: Dict[str, Any]) -> Dict[str, Any] | None:
        invocation = payload.pop("invocation_metadata", None)
        if isinstance(invocation, dict):
            return dict(invocation)
        return None

    def _profile_lookup_app_id_for_binding(
        self,
        *,
        binding_decision: AppBindingDecision | None,
        fallback_app_id: str | None = None,
    ) -> str | None:
        if binding_decision is not None and binding_allows_profile(binding_decision):
            resolved = str(binding_decision.resolved_app_id or "").strip()
            if resolved:
                return resolved
        fallback = str(fallback_app_id or "").strip()
        return fallback or None

    def _used_profile_id_for_binding(
        self,
        *,
        binding_decision: AppBindingDecision | None,
        profile: Any = None,
        catalog_entry: Dict[str, Any] | None = None,
    ) -> str | None:
        if not binding_allows_profile(binding_decision):
            return None
        catalog_profile_id = str((catalog_entry or {}).get("profileId") or "").strip()
        if catalog_profile_id:
            return catalog_profile_id
        profile_app_id = str(getattr(profile, "app_id", "") or "").strip()
        if profile_app_id:
            return profile_app_id
        if binding_decision is not None:
            resolved = str(binding_decision.resolved_app_id or "").strip()
            if resolved:
                return resolved
        return None

    def _collect_environment_probe_snapshot(
        self,
        *,
        action_payload: Dict[str, Any] | None,
    ) -> Dict[str, Any]:
        request = parse_environment_probe_request(action_payload or {})
        if not request:
            return {}
        return collect_environment_probe_snapshot(
            driver=self.driver,
            request=request,
        )

    def resolve_session_id(self, *, session_id: str | None = None, run_id: str | None = None) -> str:
        if session_id:
            return str(session_id)
        if run_id:
            run_record = db.get_run_record(run_id)
            if run_record and run_record.get("session_id"):
                return str(run_record["session_id"])
        return f"computer_use:{uuid.uuid4().hex[:12]}"

    def _is_internal_probe_invocation(
        self,
        *,
        session_id: str | None,
        run_id: str | None,
        goal: str | None,
        trigger_source: str | None,
    ) -> bool:
        if session_id or run_id:
            return False
        normalized_trigger = str(trigger_source or "").strip()
        if normalized_trigger not in {"computer_use_api", "computer_use_compat_http"}:
            return False
        normalized_goal = str(goal or "").strip().lower()
        return normalized_goal in {"observe_desktop", "observe_scene:desktop"}

    def _internal_probe_session_id(self, goal: str | None) -> str:
        normalized_goal = str(goal or "observe_desktop").strip().lower() or "observe_desktop"
        digest = hashlib.sha1(normalized_goal.encode("utf-8")).hexdigest()[:10]
        return f"computer_use:probe:{digest}"

    def begin_run(
        self,
        *,
        session_id: str | None = None,
        run_id: str | None = None,
        user_id: str = "anonymous",
        project_id: str | None = None,
        workspace_id: str | None = None,
        workspace_path: str | None = None,
        goal: str | None = None,
        trigger_source: str = "computer_use_api",
        metadata: Optional[Dict[str, Any]] = None,
    ):
        internal_probe = self._is_internal_probe_invocation(
            session_id=session_id,
            run_id=run_id,
            goal=goal,
            trigger_source=trigger_source,
        )
        effective_session_id = (
            self._internal_probe_session_id(goal)
            if internal_probe
            else self.resolve_session_id(session_id=session_id, run_id=run_id)
        )
        existing_session = db.get_session(effective_session_id) or {}
        existing_metadata = existing_session.get("metadata")
        if isinstance(existing_metadata, str):
            try:
                existing_metadata = json.loads(existing_metadata)
            except Exception:
                existing_metadata = {}
        if not isinstance(existing_metadata, dict):
            existing_metadata = {}

        session_title = existing_session.get("title") or f"Computer Use · {goal or effective_session_id[-8:]}"
        db.create_or_update_session(
            session_id=effective_session_id,
            title=session_title,
            user_id=user_id,
            metadata={
                **existing_metadata,
                "runtime": "computer_use",
                "goal": goal,
                "project_id": project_id,
                "workspace_id": workspace_id,
                "workspace_path": workspace_path,
                "trigger_source": trigger_source,
                **(
                    {
                        "hiddenFromHistory": True,
                        "internalProbe": True,
                        "ephemeral": True,
                    }
                    if internal_probe
                    else {}
                ),
            },
        )

        return erc_kernel.submit_run(
            session_id=effective_session_id,
            conversation_id=effective_session_id,
            user_id=user_id,
            runtime_kind="computer_use",
            trigger_source=trigger_source,
            agent_id="computer_use_runtime",
            metadata={
                "runtime": "computer_use",
                "goal": goal,
                "project_id": project_id,
                "workspace_id": workspace_id,
                "workspace_path": workspace_path,
                **(metadata or {}),
            },
            run_id=run_id,
            initial_status="queued",
            component="computer_use_runtime",
            node="run_manager",
        )

    def attach_run(self, run_id: str):
        return erc_kernel.attach_run(run_id, component="computer_use_runtime", node="run_manager")

    def begin_or_attach_run(
        self,
        *,
        session_id: str | None = None,
        run_id: str | None = None,
        user_id: str = "anonymous",
        project_id: str | None = None,
        workspace_id: str | None = None,
        workspace_path: str | None = None,
        goal: str | None = None,
        trigger_source: str = "computer_use_api",
        metadata: Optional[Dict[str, Any]] = None,
    ):
        run_handle = self.attach_run(str(run_id)) if run_id else None
        if run_handle is None:
            run_handle = self.begin_run(
                session_id=session_id,
                run_id=run_id,
                user_id=user_id,
                project_id=project_id,
                workspace_id=workspace_id,
                workspace_path=workspace_path,
                goal=goal,
                trigger_source=trigger_source,
                metadata=metadata,
            )
            run_handle.emit(
                "run.created",
                {
                    "status": "queued",
                    "runtime": "computer_use",
                    "goal": goal,
                },
            )
            run_handle.transition("running", reason=trigger_source, node="computer_use_runtime")
            run_service.transition_run(run_handle.run_id, status="running")
        return run_handle

    def _run_context(self, *, run_handle, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        run_record = db.get_run_record(run_handle.run_id) or {}
        record_meta = dict(run_record.get("metadata") or {})
        return {
            "run_id": run_handle.run_id,
            "session_id": run_handle.session_id,
            "conversation_id": run_handle.session_id,
            "runtime_kind": "computer_use",
            "agent_id": "computer_use_runtime",
            "user_id": run_record.get("user_id") or "anonymous",
            "goal": record_meta.get("goal"),
            "project_id": record_meta.get("project_id"),
            "workspace_id": record_meta.get("workspace_id"),
            "workspace_path": record_meta.get("workspace_path"),
            **(metadata or {}),
        }

    def _environment_signal_fingerprint(self, *, signal_kind: str, summary: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        payload = {
            "signal_kind": signal_kind,
            "summary": summary,
            "pageIdentity": (metadata or {}).get("pageIdentity"),
            "blockerState": (metadata or {}).get("blockerState"),
            "windowHandle": (metadata or {}).get("windowHandle"),
            "bindingStatus": (metadata or {}).get("bindingStatus"),
        }
        return hashlib.md5(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()

    def _emit_environment_signal(
        self,
        *,
        run_handle,
        signal_kind: str,
        summary: str,
        blocking: bool,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        payload = build_normalized_signal_payload(
            source_kind="desktop",
            signal_kind=signal_kind,
            owner_runtime="computer_use",
            summary=summary,
            related_session_id=run_handle.session_id,
            related_run_id=run_handle.run_id,
            task_relevant=True,
            blocking=bool(blocking),
            metadata=dict(metadata or {}),
            fingerprint=self._environment_signal_fingerprint(
                signal_kind=signal_kind,
                summary=summary,
                metadata=metadata,
            ),
        )
        payload.update(
            {
                "pageIdentity": (metadata or {}).get("pageIdentity"),
                "blockerState": (metadata or {}).get("blockerState"),
                "windowHandle": (metadata or {}).get("windowHandle"),
                "bindingStatus": (metadata or {}).get("bindingStatus"),
            }
        )
        run_handle.emit("environment.signal.normalized", payload)
        return payload

    def _request_environment_interrupt(
        self,
        *,
        run_handle,
        signal_kind: str,
        summary: str,
        metadata: Optional[Dict[str, Any]] = None,
        cooldown_seconds: float = 10.0,
    ) -> None:
        payload = self._emit_environment_signal(
            run_handle=run_handle,
            signal_kind=signal_kind,
            summary=summary,
            blocking=True,
            metadata=metadata,
        )
        fingerprint = str(payload.get("fingerprint") or "").strip()
        now = time.time()
        last_emitted_at = _ENVIRONMENT_INTERRUPT_COOLDOWNS.get(fingerprint, 0.0)
        if fingerprint and now - last_emitted_at < cooldown_seconds:
            return
        if fingerprint:
            _ENVIRONMENT_INTERRUPT_COOLDOWNS[fingerprint] = now
        run_handle.emit("environment.interrupt.requested", payload)

    def _resolve_environment_interrupt_if_pending(
        self,
        *,
        run_handle,
        summary: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        pending_by_fingerprint: Dict[str, Dict[str, Any]] = {}
        ordered_fingerprints: List[str] = []
        for event in db.get_runtime_events(run_handle.session_id):
            if str(event.get("run_id") or "") != run_handle.run_id:
                continue
            topic = str(event.get("topic") or "")
            payload = dict(event.get("payload") or {})
            fingerprint = str(payload.get("fingerprint") or "").strip()
            if topic == "environment.interrupt.requested":
                key = fingerprint or f"seq:{event.get('seq')}"
                pending_by_fingerprint[key] = payload
                if key not in ordered_fingerprints:
                    ordered_fingerprints.append(key)
            elif topic == "environment.interrupt.resolved":
                key = fingerprint or ""
                if key:
                    pending_by_fingerprint.pop(key, None)
        if not pending_by_fingerprint:
            return
        pending_key = ordered_fingerprints[-1] if ordered_fingerprints else next(iter(pending_by_fingerprint.keys()))
        pending_payload = dict(pending_by_fingerprint.get(pending_key) or next(reversed(list(pending_by_fingerprint.values()))))
        resolved_metadata = dict(pending_payload.get("metadata") or {})
        resolved_metadata.update(dict(metadata or {}))
        resolved_payload = build_normalized_signal_payload(
            source_kind=str(pending_payload.get("source_kind") or "desktop"),
            signal_kind=str(pending_payload.get("signal_kind") or "signal_resolved"),
            owner_runtime="computer_use",
            summary=summary,
            related_session_id=run_handle.session_id,
            related_run_id=run_handle.run_id,
            task_relevant=True,
            blocking=False,
            metadata=resolved_metadata,
            fingerprint=str(pending_payload.get("fingerprint") or ""),
        )
        run_handle.emit("environment.interrupt.resolved", resolved_payload)

    def _high_risk_action_target_identity(
        self,
        *,
        action_type: str,
        requested_action: str,
        action_payload: Dict[str, Any],
        app_id: str | None,
    ) -> str:
        parts = [
            str(app_id or "").strip(),
            str(requested_action or action_type).strip(),
            str(action_payload.get("window_handle") or "").strip(),
            str(action_payload.get("selector_key") or "").strip(),
            str(action_payload.get("target_text") or action_payload.get("text") or "").strip(),
        ]
        return "|".join(part for part in parts if part)

    def _consume_control_signal(self, *, run_handle) -> Dict[str, Any] | None:
        signal = consume_stop_signal(run_handle.run_id)
        if signal is None:
            return None
        return apply_control_signal(
            run_handle,
            signal=signal,
            runtime_kind="computer_use",
            node="computer_use_runtime",
        )

    def _raise_if_controlled(self, *, run_handle) -> None:
        signal = self._consume_control_signal(run_handle=run_handle)
        if signal is not None:
            raise RuntimeControlInterruption(signal)

    def _build_controlled_result(
        self,
        *,
        run_handle,
        signal: Dict[str, Any],
        steps: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        snapshot = self._refresh_snapshot(run_handle=run_handle)
        return {
            "sessionId": run_handle.session_id,
            "runId": run_handle.run_id,
            "status": signal.get("status"),
            "control": signal,
            "snapshot": snapshot,
            **({"steps": list(steps or [])} if steps is not None else {}),
        }

    def _workspace_root(self, workspace_path: str | None = None) -> Path:
        runtime_context = get_runtime_context()
        resolved = workspace_resolution_service.resolve_workspace_path(
            runtime_kind="computer_use",
            session_id=str(runtime_context.get("session_id") or "") or None,
            explicit_workspace_id=str(runtime_context.get("workspace_id") or "") or None,
            explicit_project_id=str(runtime_context.get("project_id") or "") or None,
            explicit_workspace_path=workspace_path or str(runtime_context.get("workspace_path") or "") or None,
        )
        return ensure_workspace_auto_create_allowed(
            Path(resolved).expanduser(),
            source="computer_use.runtime._workspace_root",
            allow_missing=True,
        )

    def _infer_app_id(
        self,
        *,
        explicit_app_id: str | None = None,
        window_title: str | None = None,
        class_name: str | None = None,
        app_name: str | None = None,
    ) -> str | None:
        decision = self._resolve_app_binding(
            explicit_app_id=explicit_app_id,
            window_title=window_title,
            class_name=class_name,
            app_name=app_name,
            include_running=True,
        )
        return decision.resolved_app_id

    def _resolve_catalog_entry(
        self,
        *,
        app_id: str | None = None,
        app_name: str | None = None,
        window_title: str | None = None,
        class_name: str | None = None,
        include_running: bool = True,
    ) -> Dict[str, Any] | None:
        decision = self._resolve_app_binding(
            explicit_app_id=app_id,
            app_name=app_name,
            window_title=window_title,
            class_name=class_name,
            include_running=include_running,
        )
        return dict(decision.catalog_entry or {}) if decision.catalog_entry else None

    def _step_uses_observation_context(
        self,
        *,
        action: str,
        step: Dict[str, Any],
    ) -> bool:
        return action not in {"open_app", "focus_window"}

    def _action_target_text_hint(self, *, action_type: str | None, action_payload: Dict[str, Any]) -> str | None:
        normalized_action = str(action_payload.get("profile_action") or action_type or "").strip().lower()
        ordered_values: List[Any]
        if normalized_action in {"find_and_type", "type_text"}:
            if is_search_selector_key(action_payload.get("selector_key")) and action_payload.get("target_text") not in (None, ""):
                ordered_values = [
                    action_payload.get("target_text"),
                    action_payload.get("text"),
                    action_payload.get("name"),
                    action_payload.get("name_contains"),
                ]
            else:
                ordered_values = [
                    action_payload.get("text"),
                    action_payload.get("target_text"),
                    action_payload.get("name"),
                    action_payload.get("name_contains"),
                ]
        else:
            ordered_values = [
                action_payload.get("target_text"),
                action_payload.get("name"),
                action_payload.get("name_contains"),
                action_payload.get("text"),
            ]
        for value in ordered_values:
            text = str(value or "").strip()
            if text:
                return text
        return None

    def _should_capture_pre_action_observation(
        self,
        *,
        action_type: str,
        action_payload: Dict[str, Any],
    ) -> bool:
        if action_type in {"open_app", "focus_window", "wait_for_element"}:
            return True
        if action_type == "scroll":
            return True
        if action_type == "type_text" and (
            isinstance(action_payload.get("point"), list)
            or bool(self._normalize_runtime_point_candidates(action_payload.get("point_candidates"), action_payload.get("pointCandidates")))
            or isinstance(action_payload.get("spatial_anchor") or action_payload.get("spatialAnchor"), dict)
            or bool(action_payload.get("window_typing"))
        ):
            return True
        return False

    def _pre_action_scene_assessment(
        self,
        *,
        action_type: str,
        action_payload: Dict[str, Any],
        observation: Dict[str, Any] | None,
        app_id: str | None,
    ) -> Dict[str, Any] | None:
        if not isinstance(observation, dict) or not observation:
            return None
        return build_scene_assessment(
            app_id=app_id,
            action_type=action_type,
            action_payload=action_payload,
            observation=observation,
            target={
                "windowTitle": observation.get("windowTitle"),
                "windowHandle": dict(observation.get("metadata") or {}).get("windowHandle"),
                "appId": dict(observation.get("metadata") or {}).get("appId") or app_id,
            },
            before_observation=observation,
            verification=None,
            update_request=None,
            visual_guard=None,
        )

    def _should_skip_for_already_in_target_state(
        self,
        *,
        action_type: str,
        scene_assessment: Dict[str, Any] | None,
    ) -> bool:
        if not isinstance(scene_assessment, dict):
            return False
        if str(scene_assessment.get("blockerState") or "none").strip().lower() != "none":
            return False
        if str(scene_assessment.get("transitionState") or "").strip().lower() != "already_in_target_state":
            return False
        return action_type in {"open_app", "focus_window", "wait_for_element"}

    def _should_block_for_pre_action_scene(
        self,
        *,
        action_type: str,
        scene_assessment: Dict[str, Any] | None,
    ) -> bool:
        if not isinstance(scene_assessment, dict):
            return False
        blocker_state = str(scene_assessment.get("blockerState") or "none").strip().lower()
        if blocker_state not in {"dialog", "login_blocker", "permission_dialog", "confirmation_required", "major_deviation"}:
            return False
        return action_type not in {"observe", "capture_screenshot", "screenshot"}

    def _build_pre_action_scene_result(
        self,
        *,
        action_type: str,
        action_payload: Dict[str, Any],
        status: str,
        message: str,
        verification: ComputerUseVerification,
        scene_assessment: Dict[str, Any],
        observation: ComputerUseObservation | None,
        app_id: str | None,
    ) -> ComputerUseActionResult:
        observation_payload = observation.as_dict() if observation else {}
        metadata = dict(observation_payload.get("metadata") or {})
        target = {
            "title": observation_payload.get("windowTitle") or action_payload.get("window_title"),
            "windowTitle": observation_payload.get("windowTitle") or action_payload.get("window_title"),
            "handle": metadata.get("windowHandle") or action_payload.get("window_handle"),
            "windowHandle": metadata.get("windowHandle") or action_payload.get("window_handle"),
            "appId": metadata.get("appId") or app_id,
            "profileId": metadata.get("profileId") or app_id,
            "metadata": {
                "preActionScene": dict(scene_assessment),
                "alreadyInTargetState": verification.status == "already_in_target_state",
            },
        }
        metadata_payload: Dict[str, Any] = {
            "preActionScene": dict(scene_assessment),
            "scene": dict(scene_assessment),
            "actionShortCircuited": verification.status == "already_in_target_state",
        }
        if not verification.passed:
            metadata_payload["updateRequest"] = {
                "requested": True,
                "kind": "ui_update_request",
                "reason": verification.reason,
                "actionType": action_type,
                "profileAction": action_payload.get("profile_action") or action_type,
                "appId": app_id,
                "windowTitle": target.get("windowTitle"),
                "windowHandle": target.get("windowHandle"),
                "selectorKey": action_payload.get("selector_key"),
                "targetText": self._action_target_text_hint(action_type=action_type, action_payload=action_payload),
                "verification": verification.as_dict(),
                "scene": dict(scene_assessment),
            }
        return ComputerUseActionResult(
            action_id=f"{action_type}_{uuid.uuid4().hex[:8]}",
            action_type=action_type,
            status=status,
            message=message,
            target=target,
            observation=observation,
            verification=verification,
            metadata=metadata_payload,
        )

    def _profile_selector(
        self,
        *,
        app_id: str | None,
        selector_key: str | None,
    ) -> Dict[str, Any]:
        return self.app_profiles.selector_for(app_id, selector_key)

    def _toolbar_selector(
        self,
        *,
        app_id: str | None,
        action_name: str | None,
    ) -> Dict[str, Any]:
        return self.app_profiles.toolbar_selector_for(app_id, action_name)

    def _visual_expectation(
        self,
        *,
        app_id: str | None,
        action_name: str | None,
    ) -> str:
        return self.app_profiles.visual_expectation_for(app_id, action_name)

    def _prime_selector_context(
        self,
        *,
        window_handle: int | None,
        app_id: str | None,
        window_title: str | None = None,
        class_name: str | None = None,
    ) -> None:
        if window_handle is None:
            return
        profile = self.app_profiles.get(app_id)
        if profile is not None:
            for selector in profile.selectors.values():
                self.driver.record_selector_hint(
                    window_handle=int(window_handle),
                    selector=selector,
                    source=f"profile:{profile.app_id}",
                    reason="app_profile_selector",
                    weight=14,
                )
            for action_name, selector in profile.toolbar_actions.items():
                self.driver.record_selector_hint(
                    window_handle=int(window_handle),
                    selector=selector,
                    source=f"profile:{profile.app_id}",
                    reason=f"toolbar:{action_name}",
                    weight=12,
                )
        for item in self.selector_memory.get_hints(
            app_id=app_id,
            window_class=class_name,
            window_title=window_title,
        ):
            self.driver.record_selector_hint(
                window_handle=int(window_handle),
                selector=dict(item.get("selector") or {}),
                source=str(item.get("source") or "selector_memory"),
                reason=str(item.get("reason") or "selector_memory"),
                weight=int(item.get("weight") or 24),
            )

    def _window_binding_candidates(
        self,
        *,
        app_id: str | None,
        window_title: str | None = None,
        class_name: str | None = None,
        process_names: List[str] | None = None,
        fallback_titles: List[str] | None = None,
        fallback_classes: List[str] | None = None,
    ) -> Dict[str, List[str]]:
        titles: List[str] = []
        classes: List[str] = []
        processes: List[str] = [str(item).strip().lower() for item in (process_names or []) if str(item).strip()]
        seeded_process_filters = bool(processes)

        def _push(items: List[str], value: str | None, *, lower: bool = False) -> None:
            normalized = str(value or "").strip()
            if not normalized:
                return
            candidate = normalized.lower() if lower else normalized
            if candidate not in items:
                items.append(candidate)

        _push(titles, window_title)
        for item in fallback_titles or []:
            _push(titles, item)
        _push(classes, class_name)
        for item in fallback_classes or []:
            _push(classes, item)

        for hint in self.selector_memory.get_window_hints(
            app_id=app_id,
            window_title=window_title,
            window_class=class_name,
            process_names=processes,
            limit=5,
        ):
            _push(titles, hint.get("title"))
            _push(classes, hint.get("className"))
            if not seeded_process_filters:
                _push(processes, hint.get("processName"), lower=True)
        catalog_hints = self.app_catalog.binding_hints(
            app_id=app_id,
            window_title=window_title,
            class_name=class_name,
            include_running=True,
        )
        for item in list(catalog_hints.get("titles") or []):
            _push(titles, item)
        for item in list(catalog_hints.get("classes") or []):
            _push(classes, item)
        if not seeded_process_filters:
            for item in list(catalog_hints.get("processNames") or []):
                _push(processes, item, lower=True)

        return {
            "titles": titles,
            "classes": classes,
            "processNames": processes,
        }

    def _remember_window_binding(
        self,
        *,
        app_id: str | None,
        window: Dict[str, Any] | None,
        source: str,
        reason: str,
        weight: int = 32,
    ) -> None:
        if not app_id or not isinstance(window, dict):
            return
        try:
            self.selector_memory.remember_window(
                app_id=app_id,
                window_title=window.get("title"),
                window_class=window.get("className"),
                process_name=window.get("processName"),
                source=source,
                reason=reason,
                weight=weight,
            )
        except Exception:
            return

    def _collect_runtime_window_candidates(
        self,
        *,
        app_id: str | None,
        payload: Dict[str, Any],
    ) -> tuple[List[Dict[str, Any]], Dict[str, List[str]]]:
        window_title = payload.get("window_title")
        class_name = payload.get("class_name")
        process_names = list(payload.get("process_names") or [])
        if not process_names:
            process_names = self.app_profiles.process_names_for(app_id)
        profile = self.app_profiles.get(app_id)
        binding_candidates = self._window_binding_candidates(
            app_id=app_id,
            window_title=window_title,
            class_name=class_name,
            process_names=process_names,
            fallback_titles=list(payload.get("window_title_candidates") or [])
            + list(getattr(profile, "title_patterns", []) or []),
            fallback_classes=list(payload.get("class_name_candidates") or [])
            + list(getattr(profile, "class_names", []) or []),
        )
        candidates = self._collect_window_candidates(
            expected_titles=binding_candidates["titles"],
            expected_classes=binding_candidates["classes"],
            expected_process_names=binding_candidates["processNames"],
            limit=12,
        )
        foreground_window = self.driver.foreground_window()
        if foreground_window:
            candidates = [*candidates, foreground_window]
        return candidates, binding_candidates

    def _prepare_action_window_context(
        self,
        *,
        run_handle,
        action_type: str,
        action_payload: Dict[str, Any],
    ) -> tuple[Dict[str, Any], Dict[str, Any] | None]:
        if action_type == "open_app":
            return dict(action_payload), None
        app_id = self._infer_app_id_from_payloads(step=action_payload)
        if not app_id:
            return dict(action_payload), None
        prepared_payload = dict(action_payload)
        current_process_names = list(prepared_payload.get("process_names") or [])
        current_window = {
            "handle": prepared_payload.get("window_handle"),
            "title": prepared_payload.get("window_title"),
            "className": prepared_payload.get("class_name"),
            "processName": prepared_payload.get("process_name") or (current_process_names[0] if current_process_names else None),
        }
        candidates, binding_candidates = self._collect_runtime_window_candidates(
            app_id=app_id,
            payload=prepared_payload,
        )
        strict_binding_required = requires_strict_window_binding(
            expected_titles=binding_candidates["titles"],
            expected_classes=binding_candidates["classes"],
        )
        current_binding_match = window_satisfies_binding(
            current_window,
            expected_titles=binding_candidates["titles"],
            expected_classes=binding_candidates["classes"],
            expected_process_names=binding_candidates["processNames"],
            platform=self.driver.platform,
            require_title_or_class_match=True,
        )
        best_candidate = choose_best_window_candidate(
            candidates,
            expected_titles=binding_candidates["titles"],
            expected_classes=binding_candidates["classes"],
            expected_process_names=binding_candidates["processNames"],
            preferred_handle=prepared_payload.get("window_handle"),
            platform=self.driver.platform,
        )
        if best_candidate is None:
            if strict_binding_required and not current_binding_match:
                block = {
                    "actionType": action_type,
                    "appId": app_id,
                    "reason": "window_context_binding_unresolved",
                    "currentWindow": dict(current_window),
                    "expectedTitles": list(binding_candidates["titles"]),
                    "expectedClasses": list(binding_candidates["classes"]),
                    "expectedProcessNames": list(binding_candidates["processNames"]),
                }
                prepared_payload["_window_binding_block"] = block
                run_handle.emit("computer_use.action.window_context_binding_unresolved", block)
                return prepared_payload, {
                    "actionType": action_type,
                    "appId": app_id,
                    "reason": "window_context_binding_unresolved",
                    "binding": block,
                }
            return prepared_payload, None
        if not should_replace_window_context(
            current_window,
            best_candidate,
            expected_titles=binding_candidates["titles"],
            expected_classes=binding_candidates["classes"],
            expected_process_names=binding_candidates["processNames"],
            platform=self.driver.platform,
        ):
            if strict_binding_required and not current_binding_match:
                block = {
                    "actionType": action_type,
                    "appId": app_id,
                    "reason": "window_context_binding_unresolved",
                    "currentWindow": dict(current_window),
                    "candidateWindow": dict(best_candidate),
                    "expectedTitles": list(binding_candidates["titles"]),
                    "expectedClasses": list(binding_candidates["classes"]),
                    "expectedProcessNames": list(binding_candidates["processNames"]),
                }
                prepared_payload["_window_binding_block"] = block
                run_handle.emit("computer_use.action.window_context_binding_unresolved", block)
                return prepared_payload, {
                    "actionType": action_type,
                    "appId": app_id,
                    "reason": "window_context_binding_unresolved",
                    "binding": block,
                }
            return prepared_payload, None
        prepared_payload["window_handle"] = best_candidate.get("handle")
        prepared_payload["window_title"] = best_candidate.get("title") or prepared_payload.get("window_title")
        if best_candidate.get("className"):
            prepared_payload["class_name"] = best_candidate.get("className")
        if binding_candidates["processNames"]:
            prepared_payload["process_names"] = list(binding_candidates["processNames"])
        decision = {
            "actionType": action_type,
            "appId": app_id,
            "reason": "window_context_rebound",
            "replacedShellSurface": is_shell_surface_window(current_window, platform=self.driver.platform),
            "window": dict(best_candidate),
        }
        run_handle.emit("computer_use.action.window_context_rebound", decision)
        return prepared_payload, decision

    def _window_matches_profile_probe(
        self,
        *,
        app_id: str | None,
        window_title: str | None,
        window_handle: int | None,
        timeout_ms: int = 500,
        poll_ms: int = 120,
    ) -> bool:
        for selector_key in self.app_profiles.window_probe_selector_keys_for(app_id):
            selector = self._profile_selector(app_id=app_id, selector_key=selector_key)
            if not selector:
                continue
            try:
                self.driver.wait_for_element(
                    window_title=window_title,
                    window_handle=window_handle,
                    timeout_ms=timeout_ms,
                    poll_ms=poll_ms,
                    **selector,
                )
                return True
            except Exception:
                continue
        return False

    def _apply_app_startup_transition(
        self,
        *,
        run_handle,
        app_id: str | None,
        window_title: str | None,
        window_handle: int | None,
        workspace_path: str | None,
        expected_titles: List[str],
        expected_classes: List[str],
        expected_process_names: List[str],
    ) -> Dict[str, Any] | None:
        profile = self.app_profiles.get(app_id)
        selector_key = str((profile.startup_transition_selector_key if profile else "") or "").strip()
        if not selector_key:
            return None
        selector = self._profile_selector(app_id=app_id, selector_key=selector_key)
        if not selector:
            return None
        click_selector = dict(selector)
        prefer_sendinput_click = bool(click_selector.pop("prefer_sendinput_click", False))
        try:
            element = self.driver.wait_for_element(
                window_title=window_title,
                window_handle=window_handle,
                timeout_ms=1200,
                poll_ms=150,
                **click_selector,
            )
        except Exception:
            return None
        observation = None
        try:
            observation = self.driver.observe_desktop(
                window_title=window_title,
                window_handle=window_handle,
                depth_limit=2,
                element_limit=40,
                use_cache=False,
            ).as_dict()
        except Exception:
            observation = None
        self._remember_selector_hint(
            step={
                "app_id": app_id,
                "profile_action": "open_app_transition",
                "selector_key": selector_key,
                "window_title": window_title,
                "window_handle": window_handle,
            },
            target=element.as_dict() if hasattr(element, "as_dict") else selector,
            observation=observation,
            source="startup_transition_selector",
            reason=f"{selector_key}_detected",
            weight=54,
        )
        run_handle.emit(
            "computer_use.action.startup_transition_started",
            {
                "appId": app_id,
                "windowTitle": window_title,
                "windowHandle": window_handle,
                "selectorKey": selector_key,
            },
        )
        clicked = self._click_target_from_payload(
            {
                "window_title": window_title,
                "window_handle": window_handle,
                "prefer_sendinput_click": prefer_sendinput_click,
                **click_selector,
            }
        )
        deadline = time.time() + 12.0
        ready = False
        resolved_window: Dict[str, Any] | None = None
        while time.time() < deadline:
            fallback_candidate = {
                "title": window_title,
                "handle": window_handle,
            }
            candidates = self._collect_window_candidates(
                expected_titles=expected_titles,
                expected_classes=expected_classes,
                expected_process_names=expected_process_names,
                limit=12,
                extra_windows=[fallback_candidate],
            )
            for candidate in candidates:
                candidate_title = candidate.get("title") or candidate.get("windowTitle") or window_title
                candidate_handle = candidate.get("handle") or candidate.get("windowHandle") or window_handle
                ready, _visual_guard = self._confirm_window_ready(
                    run_handle=run_handle,
                    app_id=app_id,
                    window_title=candidate_title,
                    window_handle=candidate_handle,
                    workspace_path=workspace_path,
                    timeout_ms=450,
                    poll_ms=120,
                )
                if not ready:
                    continue
                resolved_window = self.driver.focus_window(
                    window_handle=candidate_handle,
                    window_title_candidates=expected_titles,
                    class_name_candidates=expected_classes,
                    process_names=expected_process_names,
                )
                ready = True
                break
            if ready:
                break
            time.sleep(0.18)
        if not ready:
            raise DesktopDriverError(
                str((profile.startup_transition_error_message if profile else "") or "启动后的主窗口仍未进入可交互状态。")
            )
        focused = dict(resolved_window or {})
        focused["windowTitle"] = focused.get("title")
        focused["windowHandle"] = focused.get("handle")
        focused["appId"] = app_id
        run_handle.emit(
            "computer_use.action.startup_transition_completed",
            {
                "appId": app_id,
                "windowTitle": focused.get("title"),
                "windowHandle": focused.get("handle"),
                "clicked": clicked,
            },
        )
        return focused

    def _ensure_app_ready_window(
        self,
        *,
        run_handle,
        app_id: str | None,
        window: Dict[str, Any],
        workspace_path: str | None,
        expected_titles: List[str],
        expected_classes: List[str],
        expected_process_names: List[str],
        wait_timeout_ms: int | None = None,
    ) -> Dict[str, Any]:
        current_window = dict(window or {})
        readiness = self._wait_for_startup_readiness(
            run_handle=run_handle,
            app_id=app_id,
            window=current_window,
            workspace_path=workspace_path,
            wait_timeout_ms=wait_timeout_ms,
        )
        current_window["startupReadiness"] = readiness
        if readiness.get("ready"):
            return current_window
        transitioned = self._apply_app_startup_transition(
            run_handle=run_handle,
            app_id=app_id,
            window_title=current_window.get("title") or current_window.get("windowTitle"),
            window_handle=current_window.get("handle") or current_window.get("windowHandle"),
            workspace_path=workspace_path,
            expected_titles=expected_titles,
            expected_classes=expected_classes,
            expected_process_names=expected_process_names,
        )
        if transitioned is not None:
            transition_readiness = self._wait_for_startup_readiness(
                run_handle=run_handle,
                app_id=app_id,
                window=transitioned,
                workspace_path=workspace_path,
                wait_timeout_ms=wait_timeout_ms,
            )
            transitioned["startupReadiness"] = transition_readiness
            if not transition_readiness.get("ready") and transition_readiness.get("status") != "not_enforced":
                raise DesktopDriverError(
                    str(transition_readiness.get("reason") or "应用启动后尚未进入可操作稳定态。")
                )
            return transitioned
        profile = self.app_profiles.get(app_id)
        if profile and (
            str(profile.startup_transition_selector_key or "").strip()
            or self.app_profiles.window_probe_selector_keys_for(app_id)
            or self._visual_expectation(app_id=app_id, action_name="open_app")
        ):
            raise DesktopDriverError(
                str(profile.startup_transition_error_message or "当前窗口尚未进入可交互状态。")
            )
        return current_window

    def _startup_readiness_policy(self, wait_timeout_ms: int | None = None) -> Dict[str, int]:
        if wait_timeout_ms not in (None, ""):
            try:
                max_wait = min(30000, max(1200, int(wait_timeout_ms)))
            except Exception:
                max_wait = 12000
        else:
            max_wait = 12000
        return {
            "maxWaitMs": max_wait,
            "pollMs": 250,
            "stableRounds": 2,
        }

    def _startup_readiness_signature(self, observation: Dict[str, Any] | None) -> str:
        if not isinstance(observation, dict):
            return "no_observation"
        metadata = dict(observation.get("metadata") or {})
        return "|".join(
            [
                str(observation.get("windowTitle") or ""),
                str(metadata.get("windowHandle") or ""),
                str(metadata.get("windowBounds") or ""),
                str(metadata.get("elementCount") or len(list(observation.get("elements") or []))),
                str(metadata.get("treeHash") or observation.get("treeHash") or ""),
                str(metadata.get("screenHash") or observation.get("screenHash") or ""),
            ]
        )

    def _wait_for_startup_readiness(
        self,
        *,
        run_handle,
        app_id: str | None,
        window: Dict[str, Any],
        workspace_path: str | None,
        wait_timeout_ms: int | None = None,
    ) -> Dict[str, Any]:
        profile = self.app_profiles.get(app_id)
        probe_keys = self.app_profiles.window_probe_selector_keys_for(app_id)
        expected_result = self._visual_expectation(app_id=app_id, action_name="open_app")
        if not probe_keys and not expected_result:
            return {
                "ready": True,
                "status": "not_enforced",
                "reason": "no_profile_or_visual_readiness_signal",
                "policy": self._startup_readiness_policy(wait_timeout_ms),
            }
        policy = self._startup_readiness_policy(wait_timeout_ms)
        window_title = window.get("title") or window.get("windowTitle")
        window_handle = window.get("handle") or window.get("windowHandle")
        started_at = time.time()
        deadline = started_at + (policy["maxWaitMs"] / 1000.0)
        stable_rounds = 0
        last_signature = ""
        last_visual_guard: Dict[str, Any] | None = None
        last_observation: Dict[str, Any] | None = None
        try:
            run_handle.emit(
                "computer_use.startup_readiness.started",
                {
                    "appId": app_id,
                    "windowTitle": window_title,
                    "windowHandle": window_handle,
                    "policy": dict(policy),
                    "probeKeys": list(probe_keys),
                    "hasVisualExpectation": bool(expected_result),
                },
            )
        except Exception:
            pass
        while time.time() < deadline:
            ready, visual_guard = self._confirm_window_ready(
                run_handle=run_handle,
                app_id=app_id,
                window_title=window_title,
                window_handle=window_handle,
                workspace_path=workspace_path,
                timeout_ms=min(650, policy["pollMs"] * 2),
                poll_ms=max(80, min(policy["pollMs"], 250)),
            )
            if isinstance(visual_guard, dict):
                last_visual_guard = dict(visual_guard)
                if str(visual_guard.get("status") or "").strip() == "screen_wake_requires_human_attention":
                    result = {
                        "ready": False,
                        "status": "screen_wake_requires_human_attention",
                        "reason": visual_guard.get("reason") or "screen_wake_requires_human_attention",
                        "policy": dict(policy),
                        "stableRoundsObserved": stable_rounds,
                        "visualGuard": last_visual_guard,
                    }
                    try:
                        run_handle.emit("computer_use.startup_readiness.timeout", result)
                    except Exception:
                        pass
                    return result
            try:
                last_observation = self.driver.observe_desktop(
                    window_title=window_title,
                    window_handle=window_handle,
                    depth_limit=2,
                    element_limit=30,
                    use_cache=False,
                ).as_dict()
            except Exception:
                last_observation = None
            signature = self._startup_readiness_signature(last_observation)
            if ready:
                stable_rounds = stable_rounds + 1 if signature == last_signature else 1
                if stable_rounds >= policy["stableRounds"]:
                    result = {
                        "ready": True,
                        "status": "ready",
                        "policy": dict(policy),
                        "stableRoundsObserved": stable_rounds,
                        "elapsedMs": int((time.time() - started_at) * 1000),
                    }
                    try:
                        run_handle.emit("computer_use.startup_readiness.completed", result)
                    except Exception:
                        pass
                    return result
            else:
                stable_rounds = 0
            last_signature = signature
            time.sleep(policy["pollMs"] / 1000.0)
        result = {
            "ready": False,
            "status": "app_not_ready",
            "reason": "应用窗口出现后未进入可操作稳定态。",
            "policy": dict(policy),
            "stableRoundsObserved": stable_rounds,
            "elapsedMs": int((time.time() - started_at) * 1000),
            "visualGuard": last_visual_guard,
        }
        try:
            run_handle.emit("computer_use.startup_readiness.timeout", result)
        except Exception:
            pass
        return result

    def _navigate_explorer_to_target_path(
        self,
        *,
        run_handle,
        window: Dict[str, Any],
        target_path: str,
        expected_title: str | None,
        expected_classes: List[str],
        expected_process_names: List[str],
        wait_timeout_ms: int,
        poll_ms: int,
    ) -> Dict[str, Any]:
        normalized_target_path = str(target_path or "").strip()
        normalized_expected_title = str(expected_title or "").strip()
        if not normalized_target_path:
            return dict(window or {})
        current_window = dict(window or {})
        target_path_probe = self._probe_explorer_current_path(
            window_title=current_window.get("title") or current_window.get("windowTitle"),
            window_handle=current_window.get("handle") or current_window.get("windowHandle"),
            target_path=normalized_target_path,
        )
        if target_path_probe.get("matched") is True:
            current_window["currentPath"] = target_path_probe.get("actualPath")
            current_window["targetPathMatched"] = True
            current_window["targetPathExpected"] = target_path_probe.get("expectedPath")
            return current_window
        current_title = str(current_window.get("title") or current_window.get("windowTitle") or "").strip()
        window_handle = current_window.get("handle") or current_window.get("windowHandle")
        if window_handle in (None, ""):
            return current_window
        run_handle.emit(
            "computer_use.open_app.target_navigation_started",
            {
                "appId": "explorer",
                "windowTitle": current_title or None,
                "windowHandle": window_handle,
                "targetPath": normalized_target_path,
                "expectedTitle": normalized_expected_title,
            },
        )
        try:
            self.driver.hotkey("^l", window_title=current_title or None, window_handle=int(window_handle))
            time.sleep(0.08)
            self.driver.type_text_in_window(
                text=normalized_target_path,
                window_title=current_title or None,
                window_handle=int(window_handle),
                clear_first=True,
                press_enter=True,
                prefer_sendinput_text=True,
            )
            if normalized_expected_title:
                redirected = self._wait_for_window_after_launch(
                    expected_title=normalized_expected_title,
                    expected_titles=[normalized_expected_title],
                    expected_class=None,
                    expected_classes=expected_classes,
                    expected_process_names=expected_process_names,
                    process_ids=None,
                    timeout_ms=max(1200, min(wait_timeout_ms, 4500)),
                    poll_ms=max(120, min(poll_ms, 240)),
                )
            else:
                time.sleep(0.35)
                redirected = dict(current_window)
            focused = self.driver.focus_window(
                window_handle=redirected.get("handle"),
                window_title_candidates=[normalized_expected_title] if normalized_expected_title else None,
                class_name_candidates=expected_classes,
                process_names=expected_process_names,
            )
            resolved = dict(focused or redirected)
            target_path_probe = self._probe_explorer_current_path(
                window_title=resolved.get("title") or resolved.get("windowTitle"),
                window_handle=resolved.get("handle") or resolved.get("windowHandle"),
                target_path=normalized_target_path,
            )
            resolved["currentPath"] = target_path_probe.get("actualPath")
            resolved["targetPathExpected"] = target_path_probe.get("expectedPath")
            resolved["targetPathMatched"] = bool(target_path_probe.get("matched"))
            if target_path_probe.get("matched") is not True:
                raise DesktopDriverError(
                    f"Explorer 未绑定到目标目录。expected={normalized_target_path} actual={target_path_probe.get('actualPath') or 'unknown'}"
                )
            run_handle.emit(
                "computer_use.open_app.target_navigation_completed",
                {
                    "appId": "explorer",
                    "windowTitle": resolved.get("title") or None,
                    "windowHandle": resolved.get("handle"),
                    "targetPath": normalized_target_path,
                    "expectedTitle": normalized_expected_title,
                    "actualPath": target_path_probe.get("actualPath"),
                },
            )
            return resolved
        except Exception as exc:
            run_handle.emit(
                "computer_use.open_app.target_navigation_failed",
                {
                    "appId": "explorer",
                    "windowTitle": current_title or None,
                    "windowHandle": window_handle,
                    "targetPath": normalized_target_path,
                    "expectedTitle": normalized_expected_title,
                    "reason": str(exc),
                },
            )
            raise

    def _normalize_path_identity(self, value: str | None) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
        try:
            normalized = str(Path(raw).expanduser().resolve(strict=False))
        except Exception:
            normalized = str(Path(raw).expanduser())
        return os.path.normcase(os.path.normpath(normalized))

    def _probe_explorer_current_path(
        self,
        *,
        window_title: str | None,
        window_handle: int | None,
        target_path: str | None,
    ) -> Dict[str, Any]:
        normalized_expected = self._normalize_path_identity(target_path)
        if not normalized_expected:
            return {
                "expectedPath": "",
                "actualPath": "",
                "matched": False,
                "reason": "missing_expected_path",
            }
        actual_path = ""
        try:
            probe = self.driver.read_selected_text_via_clipboard(
                window_title=window_title,
                window_handle=window_handle,
                select_hotkey="^l",
                settle_ms=110,
            )
            actual_path = str(probe.get("selectedText") or "").strip()
        except Exception as exc:
            return {
                "expectedPath": normalized_expected,
                "actualPath": "",
                "matched": False,
                "reason": str(exc),
            }
        normalized_actual = self._normalize_path_identity(actual_path)
        return {
            "expectedPath": normalized_expected,
            "actualPath": actual_path,
            "matched": bool(normalized_actual and normalized_actual == normalized_expected),
            "reason": "",
        }

    def _collect_window_candidates(
        self,
        *,
        expected_titles: List[str],
        expected_classes: List[str],
        expected_process_names: List[str],
        limit: int = 20,
        extra_windows: List[Dict[str, Any]] | None = None,
    ) -> List[Dict[str, Any]]:
        queries: List[Dict[str, Any]] = [
            {
                "title_filters": expected_titles,
                "class_names": expected_classes,
                "process_names": expected_process_names,
                "limit": limit,
            }
        ]
        if expected_classes or expected_process_names:
            queries.append(
                {
                    "class_names": expected_classes,
                    "process_names": expected_process_names,
                    "limit": limit,
                }
            )
        if expected_process_names:
            queries.append(
                {
                    "process_names": expected_process_names,
                    "limit": limit,
                }
            )
        if expected_classes:
            queries.append(
                {
                    "class_names": expected_classes,
                    "limit": limit,
                }
            )

        windows: List[Dict[str, Any]] = []
        seen: set[tuple[Any, str, str, str]] = set()

        def _append(items: List[Dict[str, Any]] | None) -> None:
            for item in items or []:
                if not isinstance(item, dict):
                    continue
                handle = item.get("handle") or item.get("windowHandle")
                title = str(item.get("title") or item.get("windowTitle") or "").strip().lower()
                class_name = str(item.get("className") or "").strip().lower()
                process_name = str(item.get("processName") or "").strip().lower()
                signature = (handle, title, class_name, process_name)
                if signature in seen:
                    continue
                seen.add(signature)
                windows.append(dict(item))

        for query in queries:
            try:
                _append(self.driver.list_windows(**query))
            except Exception:
                continue
        _append(extra_windows)

        title_tokens = {str(item).strip().lower() for item in expected_titles if str(item).strip()}
        class_tokens = {str(item).strip().lower() for item in expected_classes if str(item).strip()}
        process_tokens = {str(item).strip().lower() for item in expected_process_names if str(item).strip()}

        def _score(item: Dict[str, Any]) -> int:
            title = str(item.get("title") or item.get("windowTitle") or "").strip().lower()
            class_name = str(item.get("className") or "").strip().lower()
            process_name = str(item.get("processName") or "").strip().lower()
            score = int(item.get("matchScore") or 0)
            if process_tokens and process_name in process_tokens:
                score += 32
            if class_tokens and class_name in class_tokens:
                score += 18
            if title_tokens and any(token in title for token in title_tokens):
                score += 12
            if item.get("isVisible") is True:
                score += 4
            return score

        windows.sort(key=_score, reverse=True)
        return windows[: max(1, limit)]

    def _confirm_window_ready(
        self,
        *,
        run_handle,
        app_id: str | None,
        window_title: str | None,
        window_handle: int | None,
        workspace_path: str | None,
        timeout_ms: int = 500,
        poll_ms: int = 120,
    ) -> tuple[bool, Dict[str, Any] | None]:
        if self._window_matches_profile_probe(
            app_id=app_id,
            window_title=window_title,
            window_handle=window_handle,
            timeout_ms=timeout_ms,
            poll_ms=poll_ms,
        ):
            return True, None
        expected_result = self._visual_expectation(app_id=app_id, action_name="open_app")
        if not expected_result or window_handle in (None, ""):
            return False, None
        observation = None
        try:
            observation = self.driver.observe_desktop(
                window_title=window_title,
                window_handle=window_handle,
                depth_limit=2,
                element_limit=50,
                use_cache=False,
            ).as_dict()
        except Exception:
            observation = None
        if self._observation_window_matches(
            observation,
            expected_title=window_title,
            expected_handle=window_handle,
            app_id=app_id,
        ):
            run_handle.emit(
                "computer_use.action.window_ready_structured_confirmed",
                {
                    "appId": app_id,
                    "windowTitle": window_title,
                    "windowHandle": window_handle,
                    "mode": "observation_window_match",
                },
            )
            return True, {
                "status": "structured_confirmed",
                "confirmed": True,
                "mode": "observation_window_match",
                "reason": "窗口身份和观察结果已确认目标窗口。",
            }
        visual_guard = self._collect_visual_guard(
            run_handle=run_handle,
            stage="post_action",
            action="open_app",
            action_payload={
                "app_id": app_id,
                "window_title": window_title,
                "window_handle": window_handle,
                "visual_expectation": expected_result,
                "require_visual_guard": True,
            },
            workspace_path=workspace_path,
            observation=observation,
        )
        confirmed = bool(
            isinstance(visual_guard, dict)
            and str(visual_guard.get("status") or "").strip().lower() == "analyzed"
            and visual_guard.get("confirmed") is True
        )
        if not confirmed and self._is_visual_guard_desktop_capture_mismatch(visual_guard):
            wake_result = self._attempt_screen_wake_recovery(
                run_handle=run_handle,
                visual_guard=visual_guard,
                action="open_app",
                window_title=window_title,
                window_handle=int(window_handle) if window_handle not in (None, "", 0) else None,
            )
            if wake_result.get("requiresHumanAttention"):
                visual_guard = {
                    **dict(visual_guard or {}),
                    "status": "screen_wake_requires_human_attention",
                    "confirmed": False,
                    "reason": "Screen wake reached a login/credential boundary; human attention is required.",
                    "screenWakeRecovery": self._screen_wake_public_payload(wake_result),
                }
                return False, visual_guard
            if wake_result.get("attempted") and isinstance(wake_result.get("observation"), dict):
                wake_visual_guard = self._collect_visual_guard(
                    run_handle=run_handle,
                    stage="post_action",
                    action="open_app",
                    action_payload={
                        "app_id": app_id,
                        "window_title": window_title,
                        "window_handle": window_handle,
                        "visual_expectation": expected_result,
                        "require_visual_guard": True,
                    },
                    workspace_path=workspace_path,
                    observation=dict(wake_result.get("observation") or {}),
                )
                if isinstance(wake_visual_guard, dict):
                    wake_visual_guard = dict(wake_visual_guard)
                    wake_visual_guard["screenWakeRetried"] = True
                    wake_visual_guard["screenWakeRecovery"] = self._screen_wake_public_payload(wake_result)
                    wake_visual_guard["previousReason"] = visual_guard.get("reason") if isinstance(visual_guard, dict) else None
                    visual_guard = wake_visual_guard
                    confirmed = bool(
                        str(wake_visual_guard.get("status") or "").strip().lower() == "analyzed"
                        and wake_visual_guard.get("confirmed") is True
                    )
        if (
            not confirmed
            and self._is_visual_guard_desktop_capture_mismatch(visual_guard)
            and window_handle not in (None, "", 0)
        ):
            refocused_window = None
            try:
                refocused_window = self.driver.focus_window(
                    window_title=window_title,
                    window_handle=int(window_handle),
                )
            except Exception:
                refocused_window = None
            retry_title = str((refocused_window or {}).get("title") or window_title or "").strip() or window_title
            retry_handle = (refocused_window or {}).get("handle") or window_handle
            retry_visual_guard = self._collect_visual_guard(
                run_handle=run_handle,
                stage="post_action",
                action="open_app",
                action_payload={
                    "app_id": app_id,
                    "window_title": retry_title,
                    "window_handle": retry_handle,
                    "visual_expectation": expected_result,
                    "require_visual_guard": True,
                },
                workspace_path=workspace_path,
                observation=observation,
            )
            if isinstance(retry_visual_guard, dict):
                retry_visual_guard = dict(retry_visual_guard)
                retry_visual_guard["refocusRetried"] = True
                retry_visual_guard["previousReason"] = visual_guard.get("reason") if isinstance(visual_guard, dict) else None
                visual_guard = retry_visual_guard
                confirmed = bool(
                    str(retry_visual_guard.get("status") or "").strip().lower() == "analyzed"
                    and retry_visual_guard.get("confirmed") is True
                )
                run_handle.emit(
                    "computer_use.action.window_ready_visual_retry_completed",
                    {
                        "appId": app_id,
                        "windowTitle": retry_title,
                        "windowHandle": retry_handle,
                        "visualGuard": retry_visual_guard,
                    },
                )
        if confirmed:
            run_handle.emit(
                "computer_use.action.window_ready_visual_confirmed",
                {
                    "appId": app_id,
                    "windowTitle": window_title,
                    "windowHandle": window_handle,
                    "visualGuard": visual_guard,
                },
            )
        return confirmed, visual_guard

    def _wait_for_window_after_launch(
        self,
        *,
        expected_title: str | None,
        expected_titles: List[str],
        expected_class: str | None,
        expected_classes: List[str],
        expected_process_names: List[str],
        timeout_ms: int,
        poll_ms: int,
        process_ids: List[int] | None = None,
    ) -> Dict[str, Any]:
        def _find_new_candidate(candidates: List[Dict[str, Any]]) -> Dict[str, Any] | None:
            for candidate in candidates:
                handle = candidate.get("handle")
                if handle not in (None, "") and int(handle) not in baseline:
                    return candidate
            return candidates[0] if candidates else None

        baseline = {
            int(item.get("handle"))
            for item in self.driver.list_windows(
                title_filter=expected_title,
                title_filters=expected_titles,
                class_name=expected_class,
                class_names=expected_classes,
                process_names=expected_process_names,
                limit=20,
            )
            if item.get("handle") not in (None, "")
        }
        deadline = time.time() + (max(timeout_ms, 100) / 1000.0)
        last_candidates: List[Dict[str, Any]] = []
        while time.time() < deadline:
            candidates = self.driver.list_windows(
                title_filter=expected_title,
                title_filters=expected_titles,
                class_name=expected_class,
                class_names=expected_classes,
                process_ids=process_ids,
                process_names=expected_process_names,
                limit=20,
            )
            if candidates:
                last_candidates = candidates
                preferred = _find_new_candidate(candidates)
                if preferred is not None:
                    return preferred
            allow_process_only_fallback = not (expected_title or expected_titles or expected_class or expected_classes)
            if expected_process_names and allow_process_only_fallback:
                process_candidates = self.driver.list_windows(
                    process_ids=process_ids,
                    process_names=expected_process_names,
                    limit=20,
                )
                if process_candidates:
                    last_candidates = process_candidates
                    preferred = _find_new_candidate(process_candidates)
                    if preferred is not None:
                        return preferred
            time.sleep(max(poll_ms, 50) / 1000.0)
        if last_candidates:
            return last_candidates[0]
        return self.driver.wait_for_window(
            title_filter=expected_title,
            title_filters=expected_titles,
            class_name=expected_class,
            class_names=expected_classes,
            process_ids=process_ids,
            process_names=expected_process_names,
            timeout_ms=timeout_ms,
            poll_ms=poll_ms,
        )

    def _pick_running_window(
        self,
        *,
        catalog_entry: Dict[str, Any] | None,
        profile,
        expected_process_names: List[str],
        expected_title: str | None,
        expected_class: str | None,
        browser_window_preferences: Dict[str, Any] | None = None,
        title_as_hint_only: bool = False,
    ) -> Dict[str, Any] | None:
        running_windows = list((catalog_entry or {}).get("runningWindows") or [])
        if not running_windows:
            return None
        process_filters = {str(item).strip().lower() for item in expected_process_names if str(item).strip()}
        profile_titles = [str(item).strip().lower() for item in getattr(profile, "title_patterns", []) if str(item).strip()]
        profile_classes = {str(item).strip().lower() for item in getattr(profile, "class_names", []) if str(item).strip()}
        expected_title_lower = str(expected_title or "").strip().lower()
        expected_class_lower = str(expected_class or "").strip().lower()
        ranked: List[tuple[int, Dict[str, Any]]] = []
        for item in running_windows:
            if not isinstance(item, dict):
                continue
            process_name = str(item.get("processName") or "").strip().lower()
            if process_filters and process_name not in process_filters:
                continue
            title = str(item.get("title") or "").strip().lower()
            class_name = str(item.get("className") or "").strip().lower()
            if expected_title_lower and not title_as_hint_only and expected_title_lower not in title:
                continue
            if expected_class_lower and expected_class_lower != class_name:
                continue
            score = int(item.get("matchScore") or 0)
            if expected_title_lower and expected_title_lower in title:
                score += 16
            if profile_titles and any(token in title for token in profile_titles):
                score += 10
            if expected_class_lower and expected_class_lower == class_name:
                score += 14
            if profile_classes and class_name in profile_classes:
                score += 8
            if item.get("isVisible") is True:
                score += 4
            if browser_window_preferences:
                preferred_process_names = list(browser_window_preferences.get("preferredProcessNames") or [])
                for index, candidate in enumerate(preferred_process_names):
                    if process_name == str(candidate).strip().lower():
                        score += max(18, 52 - (index * 8))
                        break
                if browser_window_preferences.get("preferAttachedExistingBrowser"):
                    if preferred_process_names and process_name == str(preferred_process_names[0]).strip().lower():
                        score += 20
                    if title and "chrome" in title:
                        score += 8
            ranked.append((score, item))
        if not ranked:
            return None
        ranked.sort(key=lambda row: row[0], reverse=True)
        return ranked[0][1]

    def _probe_existing_window(
        self,
        *,
        expected_title: str | None,
        expected_titles: List[str],
        expected_class: str | None,
        expected_classes: List[str],
        expected_process_names: List[str],
        browser_window_preferences: Dict[str, Any] | None = None,
        title_as_hint_only: bool = False,
    ) -> Dict[str, Any] | None:
        query_title = None if title_as_hint_only else expected_title
        query_titles = [] if title_as_hint_only else list(expected_titles or [])
        candidate_pool: List[Dict[str, Any]] = []
        for candidate in self.driver.list_windows(
            title_filter=query_title,
            title_filters=query_titles,
            class_name=expected_class,
            class_names=expected_classes,
            process_names=expected_process_names,
            limit=20,
        ):
            candidate_pool.append(dict(candidate))
        if expected_process_names:
            for candidate in self.driver.list_windows(
                process_names=expected_process_names,
                limit=20,
            ):
                candidate_pool.append(dict(candidate))
        if candidate_pool:
            deduped_candidates: List[Dict[str, Any]] = []
            seen_keys: set[str] = set()
            for item in candidate_pool:
                handle = str(item.get("handle") or "").strip()
                title = str(item.get("title") or "").strip().lower()
                process_name = str(item.get("processName") or "").strip().lower()
                signature = handle or f"{process_name}:{title}"
                if signature in seen_keys:
                    continue
                seen_keys.add(signature)
                deduped_candidates.append(item)
            ranked = self._pick_running_window(
                catalog_entry={"runningWindows": deduped_candidates},
                profile=None,
                expected_process_names=expected_process_names,
                expected_title=expected_title,
                expected_class=expected_class,
                browser_window_preferences=browser_window_preferences,
                title_as_hint_only=title_as_hint_only,
            )
            if ranked is not None:
                return ranked
        return None

    def _running_process_ids(self, *, process_names: List[str]) -> List[int]:
        if psutil is None:
            return []
        filters = {str(item).strip().lower() for item in list(process_names or []) if str(item).strip()}
        if not filters:
            return []
        matched: List[int] = []
        try:
            for process in psutil.process_iter(["pid", "name"]):
                name = str((process.info or {}).get("name") or "").strip().lower()
                if name and name in filters:
                    pid = int((process.info or {}).get("pid") or 0)
                    if pid > 0 and pid not in matched:
                        matched.append(pid)
        except Exception:
            return matched
        return matched[:24]

    def _restore_existing_window(
        self,
        *,
        expected_titles: List[str],
        expected_classes: List[str],
        expected_process_names: List[str],
    ) -> Dict[str, Any] | None:
        process_ids = self._running_process_ids(process_names=expected_process_names)
        if not process_ids and not expected_process_names:
            return None
        restore_method = getattr(self.driver, "restore_process_window", None)
        if callable(restore_method):
            try:
                restored = restore_method(
                    title_filters=expected_titles,
                    class_names=expected_classes,
                    process_ids=process_ids,
                    process_names=expected_process_names,
                )
                if isinstance(restored, dict) and restored:
                    return restored
            except Exception:
                return None
        return None

    def _restore_existing_window_from_tray(
        self,
        *,
        catalog_entry: Dict[str, Any] | None,
        expected_titles: List[str],
        expected_classes: List[str],
        expected_process_names: List[str],
    ) -> Dict[str, Any] | None:
        process_ids = self._running_process_ids(process_names=expected_process_names)
        if not process_ids:
            return None
        restore_method = getattr(self.driver, "restore_app_from_tray", None)
        if not callable(restore_method):
            return None
        restore_labels: List[str] = []
        for raw in [
            (catalog_entry or {}).get("displayName"),
            *list((catalog_entry or {}).get("aliases") or []),
            *expected_titles,
            *[Path(item).stem for item in expected_process_names],
        ]:
            value = str(raw or "").strip()
            if value and value not in restore_labels:
                restore_labels.append(value)
        try:
            restored = restore_method(
                labels=restore_labels,
                process_ids=process_ids,
                process_names=expected_process_names,
                title_filters=expected_titles,
                class_names=expected_classes,
            )
            if isinstance(restored, dict) and restored:
                return restored
        except Exception:
            return None
        return None

    def _browser_window_preferences(
        self,
        *,
        app_id: str | None,
        app_name: str | None,
        launch_command: List[str] | str | None,
        window_title: str | None,
        class_name: str | None,
        lane_decision: BrowserLaneDecision | None,
    ) -> Dict[str, Any] | None:
        family = self.browser_automation.infer_family(
            app_id=app_id,
            app_name=app_name,
            class_name=class_name,
            launch_command=launch_command,
            window_title=window_title,
        )
        if family != "chromium":
            return None
        preferred_process_names = self.browser_automation.preferred_window_process_names(
            app_id=app_id,
            app_name=app_name,
        )
        return {
            "family": family,
            "preferredProcessNames": preferred_process_names,
            "preferAttachedExistingBrowser": bool(
                lane_decision is not None
                and lane_decision.available
                and str(lane_decision.reason or "").strip().lower() == "attached_existing_debug_browser"
            ),
        }

    def _is_visual_guard_desktop_capture_mismatch(self, visual_guard: Dict[str, Any] | None) -> bool:
        if not isinstance(visual_guard, dict):
            return False
        reason = str(visual_guard.get("reason") or "").strip().lower()
        if not reason:
            return False
        strong_tokens = ("锁屏", "lock screen", "desktop wallpaper", "桌面壁纸", "壁纸", "wallpaper")
        if any(token in reason for token in strong_tokens):
            return True
        return ("桌面" in reason or "desktop" in reason) and ("未显示" in reason or "未观察到" in reason or "not visible" in reason)

    def _screen_wake_public_payload(self, wake_result: Dict[str, Any] | None) -> Dict[str, Any]:
        payload = dict(wake_result or {})
        payload.pop("observation", None)
        return payload

    def _screen_wake_attempt_key(self, *, run_handle) -> str:
        run_id = str(getattr(run_handle, "run_id", "") or "").strip()
        if run_id:
            return run_id
        session_id = str(getattr(run_handle, "session_id", "") or "").strip()
        return session_id or "global"

    def _observation_requires_human_attention_after_wake(self, observation: Dict[str, Any] | None) -> bool:
        if not isinstance(observation, dict):
            return False
        try:
            text = json.dumps(observation, ensure_ascii=False).lower()
        except Exception:
            text = str(observation).lower()
        credential_tokens = (
            "登录",
            "登陆",
            "密码",
            "凭据",
            "解锁",
            "pin",
            "password",
            "credential",
            "sign in",
            "login",
            "unlock",
        )
        return any(token in text for token in credential_tokens)

    def _attempt_screen_wake_recovery(
        self,
        *,
        run_handle,
        visual_guard: Dict[str, Any] | None,
        action: str,
        window_title: str | None = None,
        window_handle: int | None = None,
    ) -> Dict[str, Any]:
        policy = screen_wake_policy()
        if not policy.get("enabled") or not self._is_visual_guard_desktop_capture_mismatch(visual_guard):
            return {"attempted": False, "reason": "not_applicable"}
        attempt_key = self._screen_wake_attempt_key(run_handle=run_handle)
        if attempt_key in self._screen_wake_attempts:
            return {
                "attempted": False,
                "alreadyAttempted": True,
                "reason": "max_attempts_per_run",
                "wakeKey": policy.get("wakeKey"),
            }
        self._screen_wake_attempts[attempt_key] = time.time()
        wait_seconds = float(policy.get("waitSeconds") or 2.5)
        hotkey_result: Dict[str, Any] | None = None
        hotkey_error: str | None = None
        try:
            hotkey_result = self.driver.hotkey("{SPACE}", window_title=window_title, window_handle=window_handle)
        except Exception as exc:
            hotkey_error = str(exc)
        time.sleep(max(0.1, min(wait_seconds, 5.0)))
        observation: Dict[str, Any] | None = None
        observation_error: str | None = None
        try:
            observed = self.driver.observe_desktop(
                window_title=window_title,
                window_handle=window_handle,
                depth_limit=2,
                element_limit=50,
                use_cache=False,
            )
            observation = observed.as_dict() if hasattr(observed, "as_dict") else dict(observed or {})
        except Exception as exc:
            observation_error = str(exc)
        requires_human_attention = self._observation_requires_human_attention_after_wake(observation)
        result = {
            "attempted": True,
            "wakeKey": policy.get("wakeKey"),
            "hotkeySequence": "{SPACE}",
            "waitSeconds": wait_seconds,
            "hotkeyResult": hotkey_result,
            "hotkeyError": hotkey_error,
            "observationError": observation_error,
            "requiresHumanAttention": requires_human_attention,
            "observationSummary": {
                "windowTitle": (observation or {}).get("windowTitle"),
                "elementCount": len(list((observation or {}).get("elements") or [])),
                "treeHash": (observation or {}).get("treeHash"),
                "screenHash": (observation or {}).get("screenHash"),
            },
            "previousReason": (visual_guard or {}).get("reason"),
            "observation": observation,
        }
        try:
            run_handle.emit(
                "computer_use.screen_wake_attempted",
                {
                    "action": action,
                    "screenWake": self._screen_wake_public_payload(result),
                },
            )
        except Exception:
            pass
        return result

    def _is_browser_text_visibility_false_negative(self, verification: ComputerUseVerification, reason: str) -> bool:
        if verification.status != "text_verified":
            return False
        if not reason:
            return False
        address_bar_reason_tokens = (
            "地址栏",
            "address bar",
            "输入区域",
            "input area",
            "内容区域",
            "page content",
            "文本内容",
            "text content",
        )
        if not any(token in reason for token in address_bar_reason_tokens):
            return False
        details = dict(verification.details or {})
        actual_text = str(details.get("actualText") or "").strip().lower()
        resolved_target = details.get("resolvedTarget") if isinstance(details.get("resolvedTarget"), dict) else {}
        requested_target = details.get("requestedTarget") if isinstance(details.get("requestedTarget"), dict) else {}
        class_name = str(
            resolved_target.get("className")
            or requested_target.get("className")
            or requested_target.get("class_name")
            or ""
        ).strip().lower()
        role = str(
            resolved_target.get("role")
            or requested_target.get("role")
            or requested_target.get("control_type")
            or ""
        ).strip().lower()
        browser_input_tokens = ("omniboxviewviews", "textfield")
        actual_text_tokens = ("http://", "https://", "地址和搜索栏", "address and search bar")
        return (
            class_name in browser_input_tokens
            or (role == "edit" and any(token in actual_text for token in actual_text_tokens))
        )

    def _should_soften_visual_guard_conflict(
        self,
        *,
        visual_guard: Dict[str, Any] | None,
        verification: ComputerUseVerification,
    ) -> bool:
        if not isinstance(visual_guard, dict) or not verification.passed:
            return False
        if self._is_visual_guard_desktop_capture_mismatch(visual_guard):
            return True
        reason = str(visual_guard.get("reason") or "").strip().lower()
        if not reason:
            return False
        if self._is_browser_text_visibility_false_negative(verification, reason):
            return True
        screenshot_missing_tokens = ("截图未显示", "screen does not show", "not visible in screenshot")
        if not any(token in reason for token in screenshot_missing_tokens):
            return False
        strong_structured_statuses = {"verified", "text_verified", "scroll_verified"}
        return verification.status in strong_structured_statuses

    def _is_high_risk_action(self, *, app_id: str | None, action_name: str | None) -> bool:
        return self.app_profiles.is_high_risk_action(app_id, action_name)

    def _is_transient_selector_key(self, *, app_id: str | None, selector_key: str | None) -> bool:
        return self.app_profiles.is_transient_selector(app_id, selector_key)

    def _requires_pre_action_guard(self, *, app_id: str | None, action_name: str | None) -> bool:
        if self._is_high_risk_action(app_id=app_id, action_name=action_name):
            return True
        return self.app_profiles.requires_pre_action_guard(app_id, action_name)

    def _resolve_profile_selector(
        self,
        *,
        app_id: str | None,
        selector_key: str | None,
        fallbacks: List[str],
    ) -> Dict[str, Any]:
        ordered_keys: List[str] = []
        if selector_key:
            ordered_keys.append(str(selector_key).strip())
        ordered_keys.extend(item for item in fallbacks if item not in ordered_keys)
        for key in ordered_keys:
            selector = self._profile_selector(app_id=app_id, selector_key=key)
            if selector:
                return selector
        return {}

    def _recovery_selector_keys(
        self,
        *,
        app_id: str | None,
        step: Dict[str, Any],
        action: str,
    ) -> List[str]:
        ordered: List[str] = []
        explicit_selector_key = str(step.get("selector_key") or "").strip()
        if explicit_selector_key:
            ordered.append(explicit_selector_key)
        profile_action = str(step.get("profile_action") or action).strip().lower()
        for key in self.app_profiles.action_selector_keys_for(app_id, profile_action):
            if key and key not in ordered:
                ordered.append(key)
        return ordered

    def _apply_profile_recovery_selector(
        self,
        *,
        app_id: str | None,
        step: Dict[str, Any],
        action: str,
    ) -> tuple[Dict[str, Any], str | None]:
        selector_keys = self._recovery_selector_keys(app_id=app_id, step=step, action=action)
        if not selector_keys:
            return dict(step), None

        current = dict(step)
        for selector_key in selector_keys:
            selector = self._profile_selector(app_id=app_id, selector_key=selector_key)
            if not selector:
                continue
            patched = dict(current)
            patched.pop("element_id", None)
            for field in ("name", "name_contains", "automation_id", "control_type", "class_name"):
                selector_value = selector.get(field)
                if isinstance(selector_value, str) and selector_value.strip():
                    patched[field] = selector_value.strip()
                    continue
                if (
                    str(step.get("selector_key") or "").strip()
                    and field in {"name", "automation_id", "class_name", "control_type"}
                    and field in patched
                    and field not in selector
                ):
                    patched.pop(field, None)
            if patched != current:
                return patched, selector_key
        return current, None

    def _start_step_heartbeat(
        self,
        *,
        run_handle,
        index: int,
        action: str,
        step: Dict[str, Any],
    ) -> tuple[threading.Event, threading.Thread, float]:
        stop_event = threading.Event()
        started_at = time.time()
        payload = {
            "index": index,
            "action": action,
            "goal": step.get("goal"),
            "appId": self._infer_app_id_from_payloads(step=step),
            "selectorKey": step.get("selector_key"),
        }

        def _worker() -> None:
            beat = 0
            while not stop_event.wait(2.5):
                beat += 1
                run_handle.emit(
                    "computer_use.step.heartbeat",
                    {
                        **payload,
                        "heartbeatCount": beat,
                        "elapsedSeconds": round(time.time() - started_at, 1),
                    },
                )
                run_handle.emit(
                    "run.liveness.heartbeat",
                    {
                        "heartbeat_kind": "computer_use_step",
                        "last_progress_at": utc_now_iso(),
                        "last_side_effect_at": None,
                        "idle_reason": None,
                        "watchdog_source": "computer_use_step_heartbeat",
                        "blocked_reason": None,
                        "stalled": False,
                        **payload,
                        "heartbeatCount": beat,
                        "elapsedSeconds": round(time.time() - started_at, 1),
                    },
                )

        thread = threading.Thread(
            target=_worker,
            name=f"computer_use_step_heartbeat_{index}",
            daemon=True,
        )
        thread.start()
        return stop_event, thread, started_at

    def _stop_step_heartbeat(self, stop_event: threading.Event | None, thread: threading.Thread | None) -> None:
        if stop_event is not None:
            stop_event.set()
        if thread is not None and thread.is_alive():
            thread.join(timeout=0.2)

    def _infer_app_id_from_payloads(
        self,
        *,
        step: Dict[str, Any] | None = None,
        target: Dict[str, Any] | None = None,
        observation: Dict[str, Any] | None = None,
    ) -> str | None:
        for candidate in (target or {}, step or {}, observation or {}):
            if not isinstance(candidate, dict):
                continue
            explicit = (
                candidate.get("resolved_app_id")
                or candidate.get("resolvedAppId")
                or candidate.get("app_id")
                or candidate.get("appId")
                or candidate.get("profile_id")
                or candidate.get("profileId")
            )
            if explicit:
                normalized = self._infer_app_id(explicit_app_id=str(explicit))
                if normalized:
                    return normalized
            named = candidate.get("app_name") or candidate.get("appName") or candidate.get("app")
            if named:
                normalized = self._infer_app_id(app_name=str(named))
                if normalized:
                    return normalized
        observation_meta = observation.get("metadata") if isinstance(observation, dict) else {}
        if not isinstance(observation_meta, dict):
            observation_meta = {}
        return self._infer_app_id(
            explicit_app_id=None,
            window_title=(
                (observation or {}).get("windowTitle")
                or (target or {}).get("windowTitle")
                or (target or {}).get("window_title")
                or (step or {}).get("window_title")
            ),
            class_name=(
                observation_meta.get("className")
                or (target or {}).get("className")
                or (target or {}).get("class_name")
                or (step or {}).get("class_name")
            ),
            app_name=(observation or {}).get("app"),
        )

    def _spawn_process(
        self,
        launch_command: List[str] | str,
        *,
        app_id: str | None = None,
        launch_target_path: str | None = None,
        environment: Dict[str, str] | None = None,
    ):
        if not launch_command:
            raise DesktopDriverError("缺少应用启动命令。")
        normalized_target_path = str(launch_target_path or "").strip()
        if (
            str(app_id or "").strip().lower() == "explorer"
            and normalized_target_path
            and os.name == "nt"
            and hasattr(os, "startfile")
        ):
            os.startfile(normalized_target_path)
            return None
        return subprocess.Popen(launch_command, env=environment or None)

    def _resolve_launch_command(
        self,
        *,
        app_id: str | None,
        app_name: str | None,
        window_title: str | None,
        command: str | None,
        profile: Any,
    ) -> List[str] | str:
        selection = self._resolve_launch_selection(
            app_id=app_id,
            app_name=app_name,
            window_title=window_title,
            command=command,
            profile=profile,
        )
        return selection["command"]

    def _resolve_launch_selection(
        self,
        *,
        app_id: str | None,
        app_name: str | None,
        window_title: str | None,
        command: str | None,
        profile: Any,
    ) -> Dict[str, Any]:
        if command and str(command).strip():
            return {
                "command": str(command).strip(),
                "selectionReason": "explicit_command",
                "launchCandidateSource": "explicit_command",
                "launchCandidateRole": "explicit_command",
                "launchCandidateScore": None,
            }

        launch_command = self.app_profiles.launch_command_for(app_id)
        launch_command = self.browser_automation.resolve_preferred_launch_command(
            app_id=app_id,
            app_name=app_name,
            launch_command=launch_command or None,
        ) or launch_command
        if not launch_command:
            launch_candidate = self.app_catalog.resolve_launch_candidate(
                app_id=app_id,
                app_name=app_name,
                window_title=window_title,
                class_name=None,
            )
            catalog_command = list((launch_candidate or {}).get("command") or [])
            preferred_catalog_command = self.browser_automation.resolve_preferred_launch_command(
                app_id=app_id,
                app_name=app_name,
                launch_command=catalog_command or None,
            )
            return {
                "command": preferred_catalog_command or catalog_command or [],
                "selectionReason": (launch_candidate or {}).get("selectionReason") or "catalog_fallback",
                "launchCandidateSource": (launch_candidate or {}).get("source"),
                "launchCandidateRole": (launch_candidate or {}).get("role"),
                "launchCandidateScore": (launch_candidate or {}).get("score"),
            }

        executable = str(launch_command[0] or "").strip()
        if not executable:
            return {
                "command": launch_command,
                "selectionReason": "profile_launch_empty_executable",
                "launchCandidateSource": "app_profile",
                "launchCandidateRole": "profile_launch",
                "launchCandidateScore": None,
            }

        executable_path = Path(executable)
        if executable_path.exists():
            return {
                "command": [str(executable_path), *launch_command[1:]],
                "selectionReason": "profile_launch_resolved",
                "launchCandidateSource": "app_profile",
                "launchCandidateRole": "profile_launch",
                "launchCandidateScore": None,
            }

        resolved = shutil.which(executable)
        if resolved:
            return {
                "command": [str(resolved), *launch_command[1:]],
                "selectionReason": "profile_launch_shutil_which",
                "launchCandidateSource": "app_profile",
                "launchCandidateRole": "profile_launch",
                "launchCandidateScore": None,
            }

        launch_candidate = self.app_catalog.resolve_launch_candidate(
            app_id=app_id,
            app_name=app_name,
            window_title=window_title,
            class_name=None,
        )
        catalog_command = list((launch_candidate or {}).get("command") or [])
        if catalog_command:
            preferred_catalog_command = self.browser_automation.resolve_preferred_launch_command(
                app_id=app_id,
                app_name=app_name,
                launch_command=catalog_command,
            )
            return {
                "command": preferred_catalog_command or catalog_command,
                "selectionReason": (launch_candidate or {}).get("selectionReason") or "catalog_selected",
                "launchCandidateSource": (launch_candidate or {}).get("source"),
                "launchCandidateRole": (launch_candidate or {}).get("role"),
                "launchCandidateScore": (launch_candidate or {}).get("score"),
            }

        return {
            "command": launch_command,
            "selectionReason": "profile_launch_unresolved",
            "launchCandidateSource": "app_profile",
            "launchCandidateRole": "profile_launch",
            "launchCandidateScore": None,
        }

    def _artifact_output_path(
        self,
        *,
        session_id: str,
        run_id: str,
        kind: str,
        suffix: str = ".png",
        workspace_path: str | None = None,
    ) -> tuple[Path, str | None, str | None, Path]:
        workspace_root = self._workspace_root(workspace_path)
        artifact_root = workspace_artifact_run_root(
            workspace_root,
            session_id=session_id,
            run_id=run_id,
        )
        artifact_root.mkdir(parents=True, exist_ok=True)
        full_path = artifact_root / f"{kind}_{uuid.uuid4().hex[:8]}{suffix}"
        relative_path = full_path.relative_to(workspace_root)
        preview_url = None
        try:
            from core.system_base import get_engine_origin

            preview_url = f"{get_engine_origin().rstrip('/')}/workspace/{relative_path.as_posix()}"
        except Exception:
            preview_url = None
        return full_path, relative_path.as_posix(), preview_url, workspace_root

    def _capture_runtime_screenshot_artifact(
        self,
        *,
        run_handle,
        workspace_path: str | None,
        kind: str = "observe",
        metadata: Optional[Dict[str, Any]] = None,
        **capture_kwargs,
    ) -> tuple[Dict[str, Any], Path]:
        runtime_context = get_runtime_context()
        output_path, workspace_rel, preview_url, workspace_root = self._artifact_output_path(
            session_id=run_handle.session_id,
            run_id=run_handle.run_id,
            kind=kind,
            workspace_path=workspace_path,
        )
        capture = self.driver.capture_screenshot(output_path, **capture_kwargs)
        artifact = artifact_store.record_local_file(
            file_path=output_path,
            session_id=run_handle.session_id,
            run_id=run_handle.run_id,
            workspace_path=workspace_rel,
            preview_url=preview_url,
            metadata={
                "runtime": "computer_use",
                "origin": "computer_use_screenshot",
                "capture": capture,
                "capturedAt": utc_now_iso(),
                "ephemeral": True,
                "projectId": str(runtime_context.get("project_id") or "") or None,
                "workspaceId": str(runtime_context.get("workspace_id") or "") or None,
                "workspaceRoot": str(workspace_root),
                "workspaceRelativePath": workspace_rel,
                "storageClass": "workspace",
                "surfaceVisible": True,
                "canonicalPath": workspace_rel,
                "pathPlane": "workspace_artifact",
                **(metadata or {}),
            },
            source_component="computer_use_runtime",
            node="observation_service",
        )
        return artifact, output_path

    def _record_observation_screenshot(self, *, run_handle, workspace_path: str | None, **capture_kwargs) -> Dict[str, Any]:
        artifact, _ = self._capture_runtime_screenshot_artifact(
            run_handle=run_handle,
            workspace_path=workspace_path,
            kind="observe",
            **capture_kwargs,
        )
        return artifact

    def _vision_fallback_state(self) -> Dict[str, Any]:
        return self._resolve_visual_role_state("vision")

    def _resolve_visual_role_state(
        self,
        role: str,
        *,
        fallback_role: str | None = None,
    ) -> Dict[str, Any]:
        try:
            resolved = model_control_plane.resolve_model_for_role(role)
        except Exception as exc:
            return {"available": False, "reason": str(exc)}
        raw_model_id = str(resolved.get("rawModelId") or "").strip()
        source_role = role

        if not raw_model_id and fallback_role:
            try:
                resolved = model_control_plane.resolve_model_for_role(fallback_role)
                source_role = fallback_role
            except Exception as exc:
                return {"available": False, "reason": str(exc), "sourceRole": fallback_role}

        resolved_model_id = str(resolved.get("resolvedModelId") or "").strip()
        resolved_provider = dict(resolved.get("resolvedProvider") or {})
        capability_probe = None
        available = bool(resolved_model_id)
        reason = None
        if available and is_local_provider(resolved_provider):
            capability_probe = probe_local_multimodal_capability(
                model_id=resolved_model_id,
                provider_type=str(resolved_provider.get("type") or "LOCAL"),
                base_url=str(resolved_provider.get("base_url") or ""),
                api_key=str(resolved_provider.get("api_key") or ""),
            )
            if capability_probe.get("status") == "unsupported":
                available = False
                reason = str(capability_probe.get("message") or "当前本地视觉模型不可用。")

        return {
            "available": available,
            "reason": reason,
            "sourceRole": source_role,
            "modelId": resolved_model_id or None,
            "providerId": str(resolved.get("resolvedProviderId") or "").strip() or None,
            "capabilityClass": str(
                ((resolved.get("resolvedModel") or {}).get("capabilityClass") or "vision_multimodal")
            ),
            "capabilityProbe": capability_probe,
        }

    def _computer_use_visual_judge_state(self) -> Dict[str, Any]:
        return self._resolve_visual_role_state(
            "computer_use_visual_judge",
            fallback_role="vision",
        )

    def _vision_fallback_prompt(
        self,
        *,
        action: str,
        step: Dict[str, Any],
        error: Exception,
        observation: Dict[str, Any] | None,
        coordinate_anchor: Dict[str, Any] | None = None,
    ) -> str:
        selector_parts: List[str] = []
        for key in ("name", "name_contains", "automation_id", "control_type", "class_name", "window_title", "window_handle"):
            value = step.get(key)
            if value not in (None, ""):
                selector_parts.append(f"{key}={value}")
        selector_hint = " / ".join(selector_parts) or "无明确 selector"
        observation_hint = self._observation_summary(observation or {}) if isinstance(observation, dict) else "无"
        visual_expectation = str(step.get("visual_expectation") or "").strip()
        action_intent = str(step.get("intent") or step.get("profile_action") or action).strip()
        coordinate_anchor_hint = json.dumps(coordinate_anchor or {}, ensure_ascii=False) if coordinate_anchor else "无"
        app_id = str(step.get("app_id") or "").strip().lower()
        target_text = self._action_target_text_hint(action_type=action, action_payload=step) or ""
        selector_key = str(step.get("selector_key") or "").strip()
        preferred_result_region = str(step.get("preferred_result_region") or "").strip().lower()
        preferred_result_section = str(step.get("preferred_result_section") or "").strip().lower()
        preferred_hit_zone = str(step.get("preferred_hit_zone") or "").strip().lower()
        activation_gesture = str(step.get("activation_gesture") or "").strip().lower()
        forbidden_result_tokens = [
            str(item).strip()
            for item in list(step.get("forbidden_result_tokens") or [])
            if str(item).strip()
        ]
        row_click_hint = ""
        if is_result_target_key(selector_key) or is_action_target_key(selector_key):
            row_click_hint = "6. 如果目标是候选结果行、列表项或主动作按钮，请优先返回该目标中心附近的可靠 point。\n"
            row_click_hint += generic_result_visual_hint(
                target_text=target_text,
                preferred_result_region=preferred_result_region,
                preferred_result_section=preferred_result_section,
                preferred_hit_zone=preferred_hit_zone,
                activation_gesture=activation_gesture,
                forbidden_result_tokens=forbidden_result_tokens,
            )
        text_input_hint = ""
        if is_input_target_key(selector_key):
            text_input_hint = generic_input_visual_hint(target_text=target_text)
        return (
            "你在为 Windows 结构化 Computer Use 提供失败后的视觉辅助观察。\n"
            f"失败动作：{action}\n"
            f"动作意图：{action_intent}\n"
            f"失败原因：{error}\n"
            f"原始 selector：{selector_hint}\n"
            f"坐标锚点：{coordinate_anchor_hint}\n"
            + (f"目标文本：{target_text}\n" if target_text else "")
            + (f"预期视觉结果：{visual_expectation}\n" if visual_expectation else "")
            +
            "请基于截图只输出一个 JSON 对象，不要输出其他说明。\n"
            "格式：\n"
            '{"exists": true, "reason": "简短原因", "selector": {"name": null, "automation_id": null, "control_type": null, "class_name": null}, "point": [0.52, 0.66]}\n'
            "要求：\n"
            "1. 如果截图里找不到目标控件，exists 返回 false\n"
            "2. 只有高置信度时才填写 selector 字段\n"
            "3. selector 仅允许 name / automation_id / control_type / class_name\n"
            "4. point 仅在你能给出可靠窗口相对归一化坐标时填写，范围 0 到 1\n"
            "5. reason 保持简短\n"
            + row_click_hint
            + text_input_hint
            + "\n"
            f"结构化观察摘要：\n{observation_hint}"
        )

    def _invoke_vision_fallback(self, *, file_path: Path, prompt: str) -> str:
        tool = vision_media_analyzer
        func = getattr(tool, "func", None)
        if callable(func):
            result = func(file_path=str(file_path), prompt=prompt)
        elif hasattr(tool, "invoke"):
            result = tool.invoke({"file_path": str(file_path), "prompt": prompt})
        else:
            raise DesktopDriverError("vision fallback 工具不可调用。")
        if isinstance(result, dict):
            return json.dumps(result, ensure_ascii=False)
        return str(result)

    def _invoke_visual_judge(self, *, file_path: str, prompt: str) -> str:
        visual_judge_state = self._computer_use_visual_judge_state()
        role_name = str(visual_judge_state.get("sourceRole") or "vision").strip() or "vision"
        with bind_runtime_context(vision_role_override=role_name):
            return self._invoke_vision_fallback(file_path=Path(file_path), prompt=prompt)

    def _collect_visual_fallback(
        self,
        *,
        run_handle,
        index: int,
        action: str,
        step: Dict[str, Any],
        error: Exception,
        workspace_path: str | None,
        observation: Dict[str, Any] | None = None,
    ) -> Dict[str, Any] | None:
        vision_state = self._vision_fallback_state()
        if not vision_state.get("available"):
            return None
        coordinate_anchor = self._fallback_coordinate_anchor(step=step, observation=observation)
        run_handle.emit(
            "computer_use.step.visual_fallback_started",
            {
                "index": index,
                "action": action,
                "payload": step,
                "coordinateAnchor": coordinate_anchor or None,
            },
        )
        with bind_runtime_context(**self._run_context(run_handle=run_handle)):
            artifact, screenshot_path = self._capture_runtime_screenshot_artifact(
                run_handle=run_handle,
                workspace_path=workspace_path,
                kind="vision_fallback",
                metadata={
                    "runtime": "computer_use",
                    "action": action,
                    "stepIndex": index,
                    "reason": str(error),
                    "source": "computer_use_visual_fallback",
                },
                window_title=step.get("window_title"),
                window_handle=step.get("window_handle"),
                element_id=step.get("element_id"),
            )
            prompt = self._vision_fallback_prompt(
                action=action,
                step=step,
                error=error,
                observation=observation,
                coordinate_anchor=coordinate_anchor,
            )
            try:
                analysis = self._invoke_vision_fallback(file_path=screenshot_path, prompt=prompt)
                parsed = self._parse_visual_fallback_analysis(analysis)
                payload = normalize_visual_fallback_payload(
                    attempted=True,
                    status="analyzed",
                    reason=str(error),
                    model_id=vision_state.get("modelId"),
                    provider_id=vision_state.get("providerId"),
                    artifact=artifact,
                    analysis=analysis,
                    target_exists=parsed.get("exists") if parsed else None,
                    suggested_selector=parsed.get("selector") if parsed else None,
                    suggested_point=parsed.get("point") if parsed else None,
                    coordinate_anchor=coordinate_anchor,
                )
                if parsed and parsed.get("reason"):
                    payload["suggestedReason"] = parsed.get("reason")
            except Exception as exc:
                payload = normalize_visual_fallback_payload(
                    attempted=True,
                    status="analysis_failed",
                    reason=str(error),
                    model_id=vision_state.get("modelId"),
                    provider_id=vision_state.get("providerId"),
                    artifact=artifact,
                    coordinate_anchor=coordinate_anchor,
                    error=str(exc),
                )
        run_handle.emit(
            "computer_use.step.visual_fallback_completed",
            {
                "index": index,
                "action": action,
                "visualFallback": payload,
            },
        )
        return payload

    def _should_run_visual_guard(
        self,
        *,
        action_type: str,
        action_payload: Dict[str, Any],
        target: Dict[str, Any] | None = None,
        observation: Dict[str, Any] | None = None,
    ) -> bool:
        expectation = self._visual_guard_expectation(stage="post_action", action_payload=action_payload)
        if not expectation:
            return False
        explicit = action_payload.get("require_visual_guard")
        if explicit is not None:
            return bool(explicit)
        requested_action = str(action_payload.get("profile_action") or action_type).strip().lower()
        app_id = self._infer_app_id_from_payloads(step=action_payload, target=target, observation=observation)
        if self._is_high_risk_action(app_id=app_id, action_name=requested_action):
            return True
        if self.app_profiles.requires_visual_guard(app_id, requested_action):
            return True
        return requested_action in {"open_app", "focus_window", "find_and_type", "click_toolbar_action"}

    def _should_run_pre_action_visual_guard(
        self,
        *,
        action_type: str,
        action_payload: Dict[str, Any],
        observation: Dict[str, Any] | None = None,
    ) -> bool:
        requested_action = str(action_payload.get("profile_action") or action_type).strip().lower()
        app_id = self._infer_app_id_from_payloads(step=action_payload, observation=observation)
        explicit = action_payload.get("require_pre_action_visual_guard")
        if explicit is not None:
            return bool(explicit)
        return self._requires_pre_action_guard(app_id=app_id, action_name=requested_action)

    def _visual_guard_expectation(self, *, stage: str, action_payload: Dict[str, Any]) -> str:
        stage_key = "pre_action_visual_expectation" if stage == "pre_action" else "post_action_visual_expectation"
        return str(
            action_payload.get(stage_key)
            or action_payload.get("visual_expectation")
            or ""
        ).strip()

    def _visual_guard_prompt(
        self,
        *,
        stage: str,
        action: str,
        expected_result: str,
        action_payload: Dict[str, Any],
        observation: Dict[str, Any] | None,
    ) -> str:
        observation_hint = self._observation_summary(observation or {}) if isinstance(observation, dict) else "无"
        stage_label = "动作前" if stage == "pre_action" else "动作后"
        selector_key = str(action_payload.get("selector_key") or "").strip()
        target_text = self._action_target_text_hint(action_type=action, action_payload=action_payload) or ""
        preferred_result_region = str(action_payload.get("preferred_result_region") or "").strip().lower()
        preferred_result_section = str(action_payload.get("preferred_result_section") or "").strip().lower()
        preferred_hit_zone = str(action_payload.get("preferred_hit_zone") or "").strip().lower()
        activation_gesture = str(action_payload.get("activation_gesture") or "").strip().lower()
        forbidden_result_tokens = [
            str(item).strip()
            for item in list(action_payload.get("forbidden_result_tokens") or [])
            if str(item).strip()
        ]
        exact_match_hint = ""
        if is_result_target_key(selector_key) and target_text:
            exact_match_hint = generic_result_visual_hint(
                target_text=target_text,
                preferred_result_region=preferred_result_region,
                preferred_result_section=preferred_result_section,
                preferred_hit_zone=preferred_hit_zone,
                activation_gesture=activation_gesture,
                forbidden_result_tokens=forbidden_result_tokens,
            )
        input_success_hint = ""
        if is_input_target_key(selector_key):
            input_success_hint = generic_input_visual_hint(target_text=target_text)
        target_text_line = f"目标文本：{target_text}\n" if target_text else ""
        rule_lines = [
            "1. 只有当截图与结构化观察都能支持预期结果时，confirmed 才返回 true\n",
            "2. confidence 仅允许 high / medium / low\n",
            "3. 只有在高置信度时才填写 selector\n",
            "4. selector 仅允许 name / automation_id / control_type / class_name\n",
            "5. 如果你能高置信度指出应该点击的具体行/按钮，请返回窗口相对归一化 point\n",
        ]
        if exact_match_hint:
            rule_lines.append(exact_match_hint)
        if input_success_hint:
            rule_lines.append(input_success_hint)
        rule_lines.append("reason 保持简短\n\n")
        return (
            f"你在为 Windows Computer Use 提供{stage_label}的视觉保底确认。\n"
            f"动作：{action}\n"
            f"预期结果：{expected_result}\n"
            f"{target_text_line}"
            "请只输出一个 JSON 对象，不要输出其他说明。\n"
            "格式：\n"
            '{"confirmed": true, "confidence": "high", "reason": "简短原因", '
            '"selector": {"name": null, "automation_id": null, "control_type": null, "class_name": null}, '
            '"point": [0.52, 0.66]}\n'
            "要求：\n"
            + "".join(rule_lines)
            + f"结构化观察摘要：\n{observation_hint}"
        )

    def _parse_visual_guard_analysis(self, analysis: str) -> Dict[str, Any] | None:
        text = (analysis or "").strip()
        if not text:
            return None
        for candidate in self._json_object_candidates(text):
            try:
                parsed = json.loads(candidate)
            except Exception:
                continue
            if not isinstance(parsed, dict):
                continue
            selector = self._normalize_visual_selector(
                dict(parsed.get("selector") or parsed.get("recovery") or {})
            )
            confirmed = parsed.get("confirmed")
            reason = str(parsed.get("reason") or "").strip() or None
            confidence = str(parsed.get("confidence") or "").strip().lower() or None
            point = self._normalize_visual_point(parsed.get("point") or parsed.get("suggestedPoint"))
            if selector or confirmed is not None or reason or confidence or point:
                return {
                    "confirmed": bool(confirmed) if confirmed is not None else False,
                    "selector": selector,
                    "reason": reason,
                    "confidence": confidence,
                    "point": point,
                }
        return None

    def _collect_visual_guard(
        self,
        *,
        run_handle,
        stage: str,
        action: str,
        action_payload: Dict[str, Any],
        workspace_path: str | None,
        observation: Dict[str, Any] | None = None,
    ) -> Dict[str, Any] | None:
        expected_result = self._visual_guard_expectation(stage=stage, action_payload=action_payload)
        if not expected_result:
            return None
        vision_state = self._vision_fallback_state()
        if not vision_state.get("available"):
            return {
                "attempted": False,
                "status": "unavailable",
                "reason": str(vision_state.get("reason") or "视觉模型不可用"),
            }
        with bind_runtime_context(**self._run_context(run_handle=run_handle)):
            artifact, screenshot_path = self._capture_runtime_screenshot_artifact(
                run_handle=run_handle,
                workspace_path=workspace_path,
                kind="visual_guard",
                metadata={
                    "runtime": "computer_use",
                    "action": action,
                    "expectedResult": expected_result,
                    "source": "computer_use_visual_guard",
                },
                window_title=action_payload.get("window_title"),
                window_handle=action_payload.get("window_handle"),
                element_id=action_payload.get("element_id"),
            )
            prompt = self._visual_guard_prompt(
                stage=stage,
                action=action,
                expected_result=expected_result,
                action_payload=action_payload,
                observation=observation,
            )
            try:
                analysis = self._invoke_vision_fallback(file_path=screenshot_path, prompt=prompt)
                parsed = self._parse_visual_guard_analysis(analysis)
                payload = {
                    "attempted": True,
                    "status": "analyzed",
                    "stage": stage,
                    "expectedResult": expected_result,
                    "modelId": vision_state.get("modelId"),
                    "providerId": vision_state.get("providerId"),
                    "artifact": artifact,
                    "analysis": analysis,
                    "confirmed": parsed.get("confirmed") if parsed else None,
                    "confidence": parsed.get("confidence") if parsed else None,
                    "suggestedSelector": parsed.get("selector") if parsed else None,
                    "suggestedPoint": parsed.get("point") if parsed else None,
                    "reason": parsed.get("reason") if parsed else None,
                }
            except Exception as exc:
                payload = {
                    "attempted": True,
                    "status": "analysis_failed",
                    "stage": stage,
                    "expectedResult": expected_result,
                    "modelId": vision_state.get("modelId"),
                    "providerId": vision_state.get("providerId"),
                    "artifact": artifact,
                    "error": str(exc),
                }
        run_handle.emit(
            "computer_use.action.visual_guard_completed",
            {
                "stage": stage,
                "action": action,
                "visualGuard": payload,
            },
        )
        return payload

    def _apply_visual_guard_selector_patch(
        self,
        *,
        action_payload: Dict[str, Any],
        visual_guard: Dict[str, Any] | None,
        observation: Dict[str, Any] | None,
    ) -> Dict[str, Any] | None:
        if not isinstance(visual_guard, dict):
            return None
        selector = dict(visual_guard.get("suggestedSelector") or {})
        suggested_point = self._normalize_visual_point(visual_guard.get("suggestedPoint"))
        if suggested_point and not self._accept_visual_guard_point(action_payload=action_payload, suggested_point=suggested_point):
            suggested_point = None
        if not selector:
            if not suggested_point:
                return None
        patched = dict(action_payload)
        changed = False
        for key in ("name", "automation_id", "control_type", "class_name"):
            value = selector.get(key)
            if isinstance(value, str) and value.strip() and patched.get(key) != value.strip():
                patched[key] = value.strip()
                changed = True
        if suggested_point and patched.get("point") != suggested_point:
            patched["point"] = suggested_point
            patched.pop("point_rect", None)
            patched.pop("point_bias", None)
            patched.pop("point_biases", None)
            patched["coordinate_source"] = "visual_guard_point"
            changed = True
        if not changed:
            return None
        patched.pop("element_id", None)
        latest_observation = observation or {}
        metadata = latest_observation.get("metadata") if isinstance(latest_observation, dict) else {}
        if not isinstance(metadata, dict):
            metadata = {}
        if patched.get("window_handle") is None and metadata.get("windowHandle") is not None:
            patched["window_handle"] = metadata["windowHandle"]
        if not patched.get("window_title") and latest_observation.get("windowTitle"):
            patched["window_title"] = latest_observation["windowTitle"]
        return patched

    def _accept_visual_guard_point(
        self,
        *,
        action_payload: Dict[str, Any],
        suggested_point: List[float],
    ) -> bool:
        return should_accept_visual_point(action_payload, suggested_point)

    def _json_object_candidates(self, text: str) -> List[str]:
        stripped = (text or "").strip()
        if not stripped:
            return []
        candidates: List[str] = []
        fenced = re.findall(r"```(?:json)?\s*(.*?)```", stripped, flags=re.IGNORECASE | re.DOTALL)
        candidates.extend(item.strip() for item in fenced if item.strip())
        object_start = stripped.find("{")
        object_end = stripped.rfind("}")
        if object_start >= 0 and object_end > object_start:
            candidates.append(stripped[object_start : object_end + 1].strip())
        candidates.append(stripped)
        return candidates

    def _normalize_visual_selector(self, raw_selector: Dict[str, Any]) -> Dict[str, str]:
        selector: Dict[str, str] = {}
        for source_key, target_key in (
            ("name", "name"),
            ("automation_id", "automation_id"),
            ("automationId", "automation_id"),
            ("control_type", "control_type"),
            ("controlType", "control_type"),
            ("class_name", "class_name"),
            ("className", "class_name"),
        ):
            value = raw_selector.get(source_key)
            if isinstance(value, str) and value.strip():
                selector[target_key] = value.strip()
        return selector

    def _normalize_visual_point(self, raw_point: Any) -> List[float] | None:
        if not isinstance(raw_point, (list, tuple)) or len(raw_point) != 2:
            return None
        try:
            x = float(raw_point[0])
            y = float(raw_point[1])
        except Exception:
            return None
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            return None
        return [round(x, 4), round(y, 4)]

    def _parse_visual_fallback_analysis(self, analysis: str) -> Dict[str, Any] | None:
        text = (analysis or "").strip()
        if not text:
            return None
        for candidate in self._json_object_candidates(text):
            try:
                parsed = json.loads(candidate)
            except Exception:
                continue
            if not isinstance(parsed, dict):
                continue
            selector = self._normalize_visual_selector(
                dict(parsed.get("selector") or parsed.get("recovery") or parsed)
            )
            exists = parsed.get("exists")
            reason = str(parsed.get("reason") or "").strip() or None
            point = self._normalize_visual_point(parsed.get("point") or parsed.get("suggestedPoint"))
            if selector or exists is not None or reason or point:
                return {
                    "exists": bool(exists) if exists is not None else True,
                    "selector": selector,
                    "reason": reason,
                    "point": point,
                }

        selector: Dict[str, str] = {}
        for pattern, target_key in (
            (r"automation[_ ]?id\s*[:=]\s*([^\n,]+)", "automation_id"),
            (r"control[_ ]?type\s*[:=]\s*([^\n,]+)", "control_type"),
            (r"class[_ ]?name\s*[:=]\s*([^\n,]+)", "class_name"),
            (r"name\s*[:=]\s*([^\n,]+)", "name"),
        ):
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                selector[target_key] = match.group(1).strip().strip('"')
        point_match = re.search(r"point\s*[:=]\s*\[\s*([0-9.]+)\s*,\s*([0-9.]+)\s*\]", text, flags=re.IGNORECASE)
        point = None
        if point_match:
            point = self._normalize_visual_point([point_match.group(1), point_match.group(2)])
        if selector:
            return {"exists": True, "selector": selector, "reason": None, "point": point}
        return None

    def _fallback_coordinate_anchor(
        self,
        *,
        step: Dict[str, Any],
        observation: Dict[str, Any] | None,
    ) -> Dict[str, Any]:
        if not isinstance(observation, dict):
            return {}
        elements = [item for item in list(observation.get("elements") or []) if isinstance(item, dict)]
        preferred_element_id = str(step.get("element_id") or "").strip()
        automation_id = str(step.get("automation_id") or "").strip()
        name = str(step.get("name") or "").strip()
        control_type = str(step.get("control_type") or step.get("role") or "").strip().lower()
        class_name = str(step.get("class_name") or "").strip().lower()
        for candidate in elements:
            if preferred_element_id and str(candidate.get("elementId") or "").strip() == preferred_element_id:
                return build_spatial_anchor(target=candidate, observation=observation)
            if automation_id and str(candidate.get("automationId") or "").strip() == automation_id:
                return build_spatial_anchor(target=candidate, observation=observation)
            candidate_name = str(candidate.get("name") or "").strip()
            candidate_role = str(candidate.get("role") or "").strip().lower()
            candidate_class = str(candidate.get("className") or "").strip().lower()
            if name and candidate_name != name:
                continue
            if control_type and candidate_role != control_type:
                continue
            if class_name and candidate_class != class_name:
                continue
            if name or automation_id or control_type or class_name:
                return build_spatial_anchor(target=candidate, observation=observation)
        return {}

    def _apply_visual_selector_patch(
        self,
        *,
        step: Dict[str, Any],
        visual_fallback: Dict[str, Any] | None,
        observation: Dict[str, Any] | None,
    ) -> Dict[str, Any] | None:
        if not isinstance(visual_fallback, dict):
            return None
        if visual_fallback.get("targetExists") is False:
            return None
        selector = dict(visual_fallback.get("suggestedSelector") or {})
        if not selector:
            return None

        patched = dict(step)
        changed = False
        for key in ("name", "automation_id", "control_type", "class_name"):
            value = selector.get(key)
            if isinstance(value, str) and value.strip() and patched.get(key) != value.strip():
                patched[key] = value.strip()
                changed = True
        if not changed:
            return None
        patched.pop("element_id", None)

        latest_observation = observation or {}
        metadata = latest_observation.get("metadata") or {}
        if patched.get("window_handle") is None and metadata.get("windowHandle") is not None:
            patched["window_handle"] = metadata["windowHandle"]
        if not patched.get("window_title") and latest_observation.get("windowTitle"):
            patched["window_title"] = latest_observation["windowTitle"]
        return patched

    def _apply_coordinate_click_patch(
        self,
        *,
        step: Dict[str, Any],
        visual_fallback: Dict[str, Any] | None,
        observation: Dict[str, Any] | None,
    ) -> Dict[str, Any] | None:
        if not isinstance(visual_fallback, dict):
            return None
        suggested_point = self._normalize_visual_point(visual_fallback.get("suggestedPoint"))
        coordinate_anchor = visual_fallback.get("coordinateAnchor")
        if not suggested_point and not isinstance(coordinate_anchor, dict):
            return None
        patched = dict(step)
        patched["point"] = suggested_point or patched.get("point")
        if isinstance(coordinate_anchor, dict) and coordinate_anchor:
            patched["spatial_anchor"] = dict(coordinate_anchor)
        elif observation:
            inferred_anchor = self._fallback_coordinate_anchor(step=step, observation=observation)
            if inferred_anchor:
                patched["spatial_anchor"] = inferred_anchor
        patched["coordinate_source"] = (
            "visual_suggested_point"
            if suggested_point
            else "coordinate_anchor"
        )
        patched.pop("element_id", None)
        latest_observation = observation or {}
        metadata = latest_observation.get("metadata") or {}
        if patched.get("window_handle") is None and metadata.get("windowHandle") is not None:
            patched["window_handle"] = metadata["windowHandle"]
        if not patched.get("window_title") and latest_observation.get("windowTitle"):
            patched["window_title"] = latest_observation["windowTitle"]
        return patched

    def _selector_hint_from_payload(self, payload: Dict[str, Any] | None) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        selector: Dict[str, Any] = {}
        for source_key, target_key in (
            ("name", "name"),
            ("automation_id", "automation_id"),
            ("automationId", "automation_id"),
            ("control_type", "control_type"),
            ("controlType", "control_type"),
            ("class_name", "class_name"),
            ("className", "class_name"),
        ):
            value = payload.get(source_key)
            if isinstance(value, str) and value.strip():
                selector[target_key] = value.strip()
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        handle = payload.get("handle")
        if handle in (None, ""):
            handle = metadata.get("handle")
        if handle not in (None, ""):
            selector["handle"] = handle
        return selector

    def _remember_selector_hint(
        self,
        *,
        step: Dict[str, Any] | None = None,
        target: Dict[str, Any] | None = None,
        observation: Dict[str, Any] | None = None,
        source: str,
        reason: str | None = None,
        weight: int = 24,
    ) -> None:
        selector = self._selector_hint_from_payload(target or step)
        if not selector:
            return
        window_handle = None
        candidate_sources = [target or {}, step or {}, observation or {}]
        for candidate in candidate_sources:
            if not isinstance(candidate, dict):
                continue
            for key in ("window_handle", "windowHandle"):
                value = candidate.get(key)
                if value not in (None, ""):
                    window_handle = value
                    break
            if window_handle is not None:
                break
            metadata = candidate.get("metadata") if isinstance(candidate.get("metadata"), dict) else {}
            value = metadata.get("windowHandle")
            if value not in (None, ""):
                window_handle = value
                break
        try:
            self.driver.record_selector_hint(
                window_handle=int(window_handle) if window_handle is not None else None,
                selector=selector,
                source=source,
                reason=reason,
                weight=weight,
            )
        except Exception:
            return
        app_id = self._infer_app_id_from_payloads(step=step, target=target, observation=observation)
        if not app_id:
            return
        observation_meta = observation.get("metadata") if isinstance(observation, dict) else {}
        if not isinstance(observation_meta, dict):
            observation_meta = {}
        try:
            self.selector_memory.remember(
                app_id=app_id,
                selector=selector,
                source=source,
                reason=reason,
                weight=weight,
                window_class=(
                    observation_meta.get("className")
                    or (target or {}).get("className")
                    or (target or {}).get("class_name")
                    or (step or {}).get("class_name")
                ),
                window_title=(
                    (observation or {}).get("windowTitle")
                    or (target or {}).get("windowTitle")
                    or (target or {}).get("window_title")
                    or (step or {}).get("window_title")
                ),
                action_name=(step or {}).get("profile_action") or (step or {}).get("action"),
            )
        except Exception:
            return

    def _normalize_runtime_point_rect(self, raw_rect: Any) -> List[float] | None:
        if not isinstance(raw_rect, (list, tuple)) or len(raw_rect) != 4:
            return None
        try:
            left = round(float(raw_rect[0]), 4)
            top = round(float(raw_rect[1]), 4)
            right = round(float(raw_rect[2]), 4)
            bottom = round(float(raw_rect[3]), 4)
        except Exception:
            return None
        if not (0.0 <= left <= 1.0 and 0.0 <= top <= 1.0 and 0.0 <= right <= 1.0 and 0.0 <= bottom <= 1.0):
            return None
        if right < left or bottom < top:
            return None
        return [left, top, right, bottom]

    def _normalize_runtime_point_candidates(self, *groups: Any) -> List[List[float]]:
        candidates: List[List[float]] = []
        for group in groups:
            if not isinstance(group, (list, tuple)):
                continue
            for item in group:
                normalized = self._normalize_visual_point(item)
                if normalized and normalized not in candidates:
                    candidates.append(normalized)
        return candidates

    def _interaction_patch_context(
        self,
        *,
        action_type: str,
        action_payload: Dict[str, Any],
        target: Dict[str, Any] | None = None,
        observation: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        resolved_target = dict(target or {})
        observation_payload = dict(observation or {})
        observation_meta = dict(observation_payload.get("metadata") or {}) if isinstance(observation_payload, dict) else {}
        if not isinstance(observation_meta, dict):
            observation_meta = {}
        app_id = self._infer_app_id_from_payloads(
            step=action_payload,
            target=resolved_target,
            observation=observation_payload,
        )
        return {
            "app_id": app_id,
            "action_name": str(action_payload.get("profile_action") or action_type or "").strip().lower() or None,
            "selector_key": str(action_payload.get("selector_key") or "").strip() or None,
            "target_text": str(action_payload.get("target_text") or "").strip() or None,
            "control_type": (
                str(
                    action_payload.get("control_type")
                    or action_payload.get("role")
                    or resolved_target.get("role")
                    or resolved_target.get("controlType")
                    or ""
                ).strip()
                or None
            ),
            "window_class": (
                str(
                    action_payload.get("class_name")
                    or resolved_target.get("className")
                    or resolved_target.get("class_name")
                    or observation_meta.get("className")
                    or ""
                ).strip()
                or None
            ),
            "window_title": (
                str(
                    action_payload.get("window_title")
                    or resolved_target.get("windowTitle")
                    or resolved_target.get("title")
                    or observation_payload.get("windowTitle")
                    or ""
                ).strip()
                or None
            ),
        }

    def _target_strategy_context(
        self,
        *,
        action_type: str,
        action_payload: Dict[str, Any],
        target: Dict[str, Any] | None = None,
        observation: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        context = self._interaction_patch_context(
            action_type=action_type,
            action_payload=action_payload,
            target=target,
            observation=observation,
        )
        selector_key = str(context.get("selector_key") or "").strip()
        target_text = str(context.get("target_text") or "").strip()
        if not target_text and is_search_selector_key(selector_key):
            target_text = str(action_payload.get("text") or "").strip()
        context["target_text"] = target_text or None
        return context

    def _apply_target_strategy_patch(
        self,
        *,
        action_type: str,
        action_payload: Dict[str, Any],
    ) -> tuple[Dict[str, Any], Dict[str, Any] | None]:
        memory = getattr(self, "selector_memory", None)
        if memory is None:
            return dict(action_payload), None
        context = self._target_strategy_context(action_type=action_type, action_payload=action_payload)
        app_id = context.get("app_id")
        if not app_id:
            return dict(action_payload), None
        selector_key = context.get("selector_key")
        if not selector_key:
            return dict(action_payload), None
        strategy = memory.get_target_strategy(
            app_id=app_id,
            action_name=context.get("action_name"),
            selector_key=selector_key,
            target_text=context.get("target_text"),
            window_class=context.get("window_class"),
            window_title=context.get("window_title"),
            limit=4,
        )
        strategy_payload = dict(strategy.get("strategy") or {}) if isinstance(strategy, dict) else {}
        if not strategy_payload:
            return dict(action_payload), None
        patched, applied = apply_target_strategy(
            action_payload=action_payload,
            strategy=strategy_payload,
        )
        if applied is None:
            return dict(action_payload), None
        return (
            patched,
            {
                "appId": app_id,
                "actionName": context.get("action_name"),
                "selectorKey": selector_key,
                "targetText": context.get("target_text"),
                "strategy": normalize_target_strategy(strategy_payload),
                "changes": dict(applied.get("changes") or {}),
                "matches": list(strategy.get("matches") or []) if isinstance(strategy, dict) else [],
                "weight": strategy.get("weight") if isinstance(strategy, dict) else None,
            },
        )

    def _apply_governance_feedback_patch(
        self,
        *,
        action_type: str,
        action_payload: Dict[str, Any],
    ) -> tuple[Dict[str, Any], Dict[str, Any] | None]:
        memory = getattr(self, "selector_memory", None)
        if memory is None:
            return dict(action_payload), None
        context = self._interaction_patch_context(action_type=action_type, action_payload=action_payload)
        app_id = context.get("app_id")
        if not app_id:
            return dict(action_payload), None
        selector_key = str(context.get("selector_key") or "").strip() or None
        hints = memory.get_governance_hints(app_id=app_id, selector_key=selector_key, limit=3)
        if not hints:
            return dict(action_payload), None
        patched = dict(action_payload)
        changes: Dict[str, Any] = {}
        primary_event = dict((hints[0] or {}).get("event") or {})
        stage = str(primary_event.get("stage") or "").strip().lower()
        execution_path = str(primary_event.get("executionPath") or "").strip().lower()
        if action_type in {"click", "double_click", "type_text", "hotkey", "scroll"}:
            if execution_path == "computer_use_first" or stage in {"approved_at_risk", "frozen_hold", "rejected_hold"}:
                if not bool(patched.get("abort_on_major_deviation")):
                    patched["abort_on_major_deviation"] = True
                    changes["abort_on_major_deviation"] = True
                if patched.get("require_visual_guard") is None and action_type in {"click", "double_click", "type_text"}:
                    patched["require_visual_guard"] = True
                    changes["require_visual_guard"] = True
                if patched.get("post_action_stable_rounds") in (None, ""):
                    patched["post_action_stable_rounds"] = 2
                    changes["post_action_stable_rounds"] = 2
                if patched.get("post_action_settle_timeout_ms") in (None, ""):
                    patched["post_action_settle_timeout_ms"] = 1800
                    changes["post_action_settle_timeout_ms"] = 1800
            elif stage == "approved_live":
                if patched.get("post_action_stable_rounds") in (None, ""):
                    patched["post_action_stable_rounds"] = 2
                    changes["post_action_stable_rounds"] = 2
        if not changes:
            return dict(action_payload), None
        return (
            patched,
            {
                "appId": app_id,
                "selectorKey": selector_key,
                "changes": changes,
                "matches": hints,
                "event": primary_event,
            },
        )

    def _target_strategy_region_hint(
        self,
        *,
        action_payload: Dict[str, Any],
        target: Dict[str, Any] | None = None,
    ) -> str | None:
        direct_point = action_payload.get("point")
        region = result_region_from_point(direct_point)
        if region:
            return region
        metadata = dict((target or {}).get("metadata") or {}) if isinstance(target, dict) else {}
        region = result_region_from_point(metadata.get("suggestedPoint"))
        if region:
            return region
        spatial_anchor = metadata.get("spatialAnchor") or action_payload.get("spatial_anchor") or action_payload.get("spatialAnchor")
        if isinstance(spatial_anchor, dict):
            region = result_region_from_point(spatial_anchor.get("windowRelativePoint"))
            if region:
                return region
            rect = spatial_anchor.get("windowRelativeRect")
            if isinstance(rect, (list, tuple)) and len(rect) == 4:
                try:
                    return result_region_from_point(
                        [
                            round((float(rect[0]) + float(rect[2])) / 2.0, 4),
                            round((float(rect[1]) + float(rect[3])) / 2.0, 4),
                        ]
                    )
                except Exception:
                    return None
        return None

    def _remember_target_strategy(
        self,
        *,
        action_type: str,
        action_payload: Dict[str, Any],
        result: ComputerUseActionResult,
    ) -> None:
        memory = getattr(self, "selector_memory", None)
        if memory is None:
            return
        verification = self._normalize_verification(result.verification)
        if not verification.passed or result.status == "update_requested":
            return
        target = dict(result.target or {})
        context = self._target_strategy_context(
            action_type=action_type,
            action_payload=action_payload,
            target=target,
            observation=result.observation.as_dict() if result.observation else None,
        )
        app_id = context.get("app_id")
        selector_key = str(context.get("selector_key") or "").strip()
        target_text = str(context.get("target_text") or "").strip()
        if not app_id or not selector_key or not target_text:
            return
        strategy: Dict[str, Any] = {"target_text": target_text}
        if is_search_selector_key(selector_key):
            query_text = str(action_payload.get("text") or "").strip()
            if not query_text:
                return
            strategy["query_text"] = query_text
            strategy["query_mode"] = infer_query_mode(query_text, target_text)
            strategy["required_exact_match"] = bool(target_text)
            strategy["search_selector_key"] = selector_key
        if is_result_selector_key(selector_key):
            strategy["required_exact_match"] = bool(target_text)
            strategy["result_selector_key"] = selector_key
            region = self._target_strategy_region_hint(action_payload=action_payload, target=target)
            if region:
                strategy["preferred_result_region"] = region
            preferred_result_section = str(action_payload.get("preferred_result_section") or "").strip().lower()
            if preferred_result_section:
                strategy["preferred_result_section"] = preferred_result_section
            preferred_hit_zone = str(action_payload.get("preferred_hit_zone") or "").strip().lower()
            if preferred_hit_zone:
                strategy["preferred_hit_zone"] = preferred_hit_zone
            activation_gesture = str(action_payload.get("activation_gesture") or "").strip().lower()
            if activation_gesture:
                strategy["activation_gesture"] = activation_gesture
            if action_payload.get("preferred_result_index") not in (None, ""):
                strategy["preferred_result_index"] = action_payload.get("preferred_result_index")
            forbidden_tokens = [
                str(item).strip()
                for item in list(action_payload.get("forbidden_result_tokens") or [])
                if str(item).strip()
            ]
            if forbidden_tokens:
                strategy["forbidden_result_tokens"] = forbidden_tokens
        normalized_strategy = normalize_target_strategy(strategy)
        if not normalized_strategy:
            return
        source = "learned_search_result_strategy" if is_result_selector_key(selector_key) else "learned_search_strategy"
        weight = 64 if verification.level == "verified" else 54
        memory.remember_target_strategy(
            app_id=app_id,
            strategy=normalized_strategy,
            source=source,
            reason=verification.status or action_type,
            weight=weight,
            action_name=context.get("action_name"),
            selector_key=selector_key,
            target_text=target_text,
            window_class=context.get("window_class"),
            window_title=context.get("window_title"),
        )

    def _apply_learned_interaction_patch(
        self,
        *,
        action_type: str,
        action_payload: Dict[str, Any],
    ) -> tuple[Dict[str, Any], Dict[str, Any] | None]:
        memory = getattr(self, "selector_memory", None)
        if memory is None:
            return dict(action_payload), None
        context = self._interaction_patch_context(action_type=action_type, action_payload=action_payload)
        app_id = context.get("app_id")
        if not app_id:
            return dict(action_payload), None
        interaction = memory.get_interaction_patch(
            app_id=app_id,
            action_name=context.get("action_name"),
            selector_key=context.get("selector_key"),
            target_text=context.get("target_text"),
            control_type=context.get("control_type"),
            window_class=context.get("window_class"),
            window_title=context.get("window_title"),
            limit=3,
        )
        patch = dict(interaction.get("patch") or {}) if isinstance(interaction, dict) else {}
        if not patch:
            return dict(action_payload), None
        patched = dict(action_payload)
        changed = False

        for key in ("point", "point_rect", "spatial_anchor", "coordinate_source"):
            current_value = patched.get(key)
            patch_value = patch.get(key)
            if current_value in (None, "", [], {}) and patch_value not in (None, "", [], {}):
                patched[key] = patch_value
                changed = True
        current_candidates = self._normalize_runtime_point_candidates(
            patched.get("point_candidates"),
            patched.get("pointCandidates"),
        )
        learned_candidates = self._normalize_runtime_point_candidates(
            patch.get("point_candidates"),
            patch.get("pointCandidates"),
        )
        merged_candidates = self._normalize_runtime_point_candidates(current_candidates, learned_candidates)
        if merged_candidates and merged_candidates != current_candidates:
            patched["point_candidates"] = merged_candidates
            patched.pop("pointCandidates", None)
            changed = True
        for key in ("prefer_sendinput_click", "window_typing", "clear_first"):
            if bool(patch.get(key)) and not bool(patched.get(key)):
                patched[key] = True
                changed = True
        for key in (
            "post_action_settle_timeout_ms",
            "post_action_settle_poll_ms",
            "post_action_stable_rounds",
            "abort_on_major_deviation",
        ):
            if patched.get(key) in (None, "") and patch.get(key) not in (None, ""):
                patched[key] = patch.get(key)
                changed = True
        if not changed:
            return dict(action_payload), None
        return (
            patched,
            {
                "appId": app_id,
                "actionName": context.get("action_name"),
                "selectorKey": context.get("selector_key"),
                "targetText": context.get("target_text"),
                "patch": patch,
                "matches": list(interaction.get("matches") or []) if isinstance(interaction, dict) else [],
                "weight": interaction.get("weight") if isinstance(interaction, dict) else None,
            },
        )

    def _remember_learned_interaction(
        self,
        *,
        action_type: str,
        action_payload: Dict[str, Any],
        result: ComputerUseActionResult,
    ) -> None:
        memory = getattr(self, "selector_memory", None)
        if memory is None:
            return
        verification = self._normalize_verification(result.verification)
        if not verification.passed:
            return
        if result.status == "update_requested":
            return
        target = dict(result.target or {})
        metadata = dict(target.get("metadata") or {}) if isinstance(target, dict) else {}
        patch: Dict[str, Any] = {}
        point = self._normalize_visual_point(action_payload.get("point"))
        if point:
            patch["point"] = point
        point_rect = self._normalize_runtime_point_rect(action_payload.get("point_rect"))
        if point_rect:
            patch["point_rect"] = point_rect
        point_candidates = self._normalize_runtime_point_candidates(
            action_payload.get("point_candidates"),
            action_payload.get("pointCandidates"),
            metadata.get("pointCandidates"),
        )
        if point_candidates:
            patch["point_candidates"] = point_candidates
            if "point" not in patch:
                patch["point"] = point_candidates[0]
        spatial_anchor = action_payload.get("spatial_anchor") or action_payload.get("spatialAnchor") or metadata.get("spatialAnchor")
        if isinstance(spatial_anchor, dict) and spatial_anchor:
            patch["spatial_anchor"] = dict(spatial_anchor)
        coordinate_source = str(action_payload.get("coordinate_source") or metadata.get("coordinateSource") or "").strip()
        if coordinate_source:
            patch["coordinate_source"] = coordinate_source
        if bool(action_payload.get("prefer_sendinput_click")) or bool(metadata.get("sendInputPreferred")):
            patch["prefer_sendinput_click"] = True
        if action_type == "type_text" and (
            bool(action_payload.get("window_typing"))
            or bool(metadata.get("coordinateFallback"))
            or point_candidates
        ):
            patch["window_typing"] = True
        if action_type == "type_text" and bool(action_payload.get("clear_first")):
            patch["clear_first"] = True
        for key in (
            "post_action_settle_timeout_ms",
            "post_action_settle_poll_ms",
            "post_action_stable_rounds",
            "abort_on_major_deviation",
        ):
            if action_payload.get(key) not in (None, ""):
                patch[key] = action_payload.get(key)
        if not patch:
            return
        context = self._interaction_patch_context(
            action_type=action_type,
            action_payload=action_payload,
            target=target,
            observation=result.observation.as_dict() if result.observation else None,
        )
        app_id = context.get("app_id")
        if not app_id:
            return
        source = "learned_interaction"
        if bool(metadata.get("coordinateFallback")) or patch.get("point_candidates") or patch.get("point") or patch.get("spatial_anchor"):
            source = "learned_coordinate_interaction"
        elif bool(metadata.get("sendInputPreferred")):
            source = "learned_sendinput_interaction"
        weight = 66 if verification.level == "verified" else 56
        memory.remember_interaction(
            app_id=app_id,
            patch=patch,
            source=source,
            reason=verification.status or action_type,
            weight=weight,
            action_name=context.get("action_name"),
            selector_key=context.get("selector_key"),
            target_text=context.get("target_text"),
            control_type=context.get("control_type"),
            window_class=context.get("window_class"),
            window_title=context.get("window_title"),
        )

    def _refresh_snapshot(self, *, run_handle, observation: ComputerUseObservation | None = None, action: Dict[str, Any] | None = None) -> Dict[str, Any]:
        snapshot = {
            "session_id": run_handle.session_id,
            "run_id": run_handle.run_id,
            "kind": "computer_use",
            "latest_observation": observation.as_dict() if observation else None,
            "last_action": action or None,
            "artifacts": db.list_runtime_artifacts(run_id=run_handle.run_id, limit=50),
        }
        snapshot_service.record_runtime_snapshot(
            session_id=run_handle.session_id,
            run_id=run_handle.run_id,
            snapshot_type="computer_use_projection",
            snapshot=snapshot,
        )
        run_service.update_metadata(
            run_handle.run_id,
            {
                "computer_use": {
                    "latest_observation": snapshot["latest_observation"],
                    "last_action": action or None,
                }
            },
        )
        return snapshot

    def _normalize_verification(
        self,
        verification: Dict[str, Any] | ComputerUseVerification | None,
    ) -> ComputerUseVerification:
        return normalize_verification_payload(verification)

    def _int_payload_value(self, value: Any, default: int) -> int:
        try:
            return int(value)
        except Exception:
            return int(default)

    def _apply_live_matrix_budget_feedback(
        self,
        *,
        action_type: str,
        action_payload: Dict[str, Any],
        feedback: Dict[str, Any] | None,
    ) -> Dict[str, Any]:
        if not isinstance(feedback, dict):
            return dict(action_payload)
        status = str(feedback.get("status") or "").strip().lower()
        if status not in {"degraded", "unhealthy"}:
            return dict(action_payload)
        patched = dict(action_payload)
        patched["_budget_feedback_source"] = "matrix_feedback"
        if patched.get("time_budget_ms") in (None, ""):
            patched["time_budget_ms"] = 14000 if status == "degraded" else 18000
        if patched.get("vision_budget") in (None, ""):
            patched["vision_budget"] = 2 if status == "degraded" else 3
        if patched.get("fallback_budget") in (None, ""):
            patched["fallback_budget"] = 2 if status == "degraded" else 3
        if patched.get("post_action_settle_timeout_ms") in (None, ""):
            patched["post_action_settle_timeout_ms"] = 1100 if status == "degraded" else 1600
        if patched.get("post_action_stable_rounds") in (None, "") and not bool(patched.get("prefer_fast_path")):
            patched["post_action_stable_rounds"] = 2
        strong_verification_rate = float(feedback.get("strongVerificationRate") or 0.0)
        if strong_verification_rate < 0.5:
            if patched.get("post_action_settle_timeout_ms") in (None, ""):
                patched["post_action_settle_timeout_ms"] = 1250 if status == "degraded" else 1800
            if patched.get("post_action_stable_rounds") in (None, ""):
                patched["post_action_stable_rounds"] = 2 if status == "degraded" else 3
        return patched

    def _apply_live_matrix_verification_gate(
        self,
        *,
        feedback: Dict[str, Any] | None,
        verification: ComputerUseVerification,
    ) -> ComputerUseVerification:
        if not isinstance(feedback, dict):
            return verification
        status = str(feedback.get("status") or "").strip().lower()
        if status not in {"degraded", "unhealthy"}:
            return verification
        details = dict(verification.details or {})
        details["primitiveLiveBaseline"] = dict(feedback)
        if self._is_live_validation_mode():
            details["primitiveLiveBaselineValidationMode"] = True
            details["primitiveLiveBaselineBypass"] = "validation_non_interrupt"
            return ComputerUseVerification(
                passed=verification.passed,
                status=verification.status,
                reason=verification.reason,
                details=details,
                level=verification.level,
            )
        if self._has_strong_visual_locator_verification_evidence(verification, details):
            details["primitiveLiveBaselineBypass"] = "strong_visual_locator_evidence"
            return ComputerUseVerification(
                passed=verification.passed,
                status=verification.status,
                reason=verification.reason,
                details=details,
                level=verification.level,
            )
        level = str(verification.level or "").strip().lower()
        strong_verification_rate = float(feedback.get("strongVerificationRate") or 0.0)
        if status == "unhealthy" and level != "verified":
            return ComputerUseVerification(
                passed=False,
                status="primitive_live_baseline_unhealthy",
                reason="基础原语 live 基线未通过，当前动作缺少强验证证据。",
                details=details,
                level="review_required",
            )
        if status == "degraded" and level == "soft_verified":
            return ComputerUseVerification(
                passed=False,
                status="primitive_live_baseline_degraded",
                reason="基础原语 live 基线退化，当前仅有软验证证据，已升级为人工复核。",
                details=details,
                level="review_required",
            )
        details["primitiveLiveBaselineVerificationBias"] = "strong_verification_preferred" if strong_verification_rate < 0.5 else "balanced"
        return ComputerUseVerification(
            passed=verification.passed,
            status=verification.status,
            reason=verification.reason,
            details=details,
            level=verification.level,
        )

    def _has_strong_visual_locator_verification_evidence(
        self,
        verification: ComputerUseVerification,
        details: Dict[str, Any] | None = None,
    ) -> bool:
        payload = dict(details or verification.details or {})
        visual_locator = dict(payload.get("visualLocator") or {})
        if not visual_locator:
            return False
        match_count = int(visual_locator.get("matchCount") or len(list(visual_locator.get("matches") or [])))
        if match_count <= 0:
            return False
        coordinate_source = str(payload.get("coordinateSource") or "").strip().lower()
        if not coordinate_source.startswith("visual_locator"):
            return False
        search_mode = str(visual_locator.get("searchMode") or "").strip().lower()
        semantic_region = dict(visual_locator.get("semanticRegionHint") or {})
        semantic_bounds = list(semantic_region.get("bounds") or [])
        if search_mode in {"observation_region_ocr", "captured_window_ocr"}:
            return True
        if str(visual_locator.get("readText") or "").strip():
            return True
        if len(semantic_bounds) == 4 and str(semantic_region.get("role") or "").strip().lower() == "button":
            return True
        status = str(verification.status or "").strip().lower()
        if status in {"coordinate_click_executed", "coordinate_text_executed", "coordinate_file_paste_executed"}:
            return True
        return False

    def _is_live_validation_mode(self) -> bool:
        flag = str(os.getenv("V8_AGENT_OS_COMPUTER_USE_VALIDATION_MODE") or "").strip().lower()
        return flag in {"1", "true", "yes", "primitive_live", "live_matrix"}

    def _is_fast_path_requested(self, action_payload: Dict[str, Any]) -> bool:
        explicit = action_payload.get("prefer_fast_path")
        if explicit is not None:
            return bool(explicit)
        has_attachment_payload = bool(
            action_payload.get("file_path")
            or action_payload.get("file_paths")
            or action_payload.get("attachment_paths")
        )
        if has_attachment_payload:
            return False
        requested_action = str(action_payload.get("profile_action") or action_payload.get("action") or "").strip().lower()
        if requested_action in {"send", "delete", "remove", "submit", "purchase"}:
            return False
        has_structured_target = bool(
            action_payload.get("element_id")
            or action_payload.get("automation_id")
            or action_payload.get("selector_key")
            or action_payload.get("target_text")
            or action_payload.get("window_typing")
        )
        return has_structured_target

    def _normalized_observation_text(self, value: Any) -> str:
        return re.sub(r"\s+", "", str(value or "").strip()).lower()

    def _observation_window_matches(
        self,
        observation: Dict[str, Any] | None,
        *,
        expected_title: str | None = None,
        expected_handle: Any = None,
        app_id: str | None = None,
    ) -> bool:
        if not isinstance(observation, dict):
            return False
        metadata = dict(observation.get("metadata") or {})
        expected_title_norm = self._normalized_observation_text(expected_title)
        observed_title_norm = self._normalized_observation_text(observation.get("windowTitle"))
        observed_handle = metadata.get("windowHandle") or observation.get("windowHandle")
        normalized_app_id = self._normalized_observation_text(app_id)
        observed_app_id = self._normalized_observation_text(
            metadata.get("appId") or metadata.get("profileId") or observation.get("app")
        )

        if expected_handle not in (None, "") and observed_handle not in (None, ""):
            try:
                if int(expected_handle) == int(observed_handle):
                    return True
            except Exception:
                pass
        if expected_title_norm and observed_title_norm:
            if expected_title_norm in observed_title_norm or observed_title_norm in expected_title_norm:
                return True
        if normalized_app_id and observed_app_id and normalized_app_id == observed_app_id:
            return True
        return False

    def _observation_contains_target_text(self, observation: Dict[str, Any] | None, *, target_text: str | None) -> bool:
        normalized_target = self._normalized_observation_text(target_text)
        if not normalized_target or not isinstance(observation, dict):
            return False

        def _candidate_matches(value: Any) -> bool:
            normalized_value = self._normalized_observation_text(value)
            if not normalized_value:
                return False
            return normalized_target == normalized_value or normalized_target in normalized_value

        if _candidate_matches(observation.get("windowTitle")) or _candidate_matches(observation.get("app")):
            return True

        for element in list(observation.get("elements") or [])[:60]:
            if not isinstance(element, dict):
                continue
            if (
                _candidate_matches(element.get("name"))
                or _candidate_matches(element.get("automationId"))
                or _candidate_matches(element.get("className"))
                or _candidate_matches(element.get("role"))
            ):
                return True
            metadata = dict(element.get("metadata") or {})
            for key in ("text", "value", "currentValue", "legacyValue", "windowText", "displayName", "description"):
                if _candidate_matches(metadata.get(key)):
                    return True
        return False

    def _is_strong_structured_verification(self, verification: ComputerUseVerification) -> bool:
        return bool(
            verification.passed
            and verification.level == "verified"
            and verification.status in {"verified", "text_verified", "scroll_verified"}
        )

    def _fast_path_settle_evidence(
        self,
        *,
        action_type: str,
        action_payload: Dict[str, Any],
        observation: ComputerUseObservation | None,
        window_title: str | None,
        window_handle: int | None,
        app_id: str | None,
        high_risk_action: bool,
    ) -> Dict[str, Any] | None:
        if high_risk_action or not self._is_fast_path_requested(action_payload) or observation is None:
            return None
        observation_payload = observation.as_dict()
        if self._observation_window_matches(
            observation_payload,
            expected_title=window_title,
            expected_handle=window_handle,
            app_id=app_id,
        ):
            return {
                "mode": "window_match",
                "windowTitle": observation.window_title,
                "windowHandle": (observation.metadata or {}).get("windowHandle"),
            }
        target_text = self._action_target_text_hint(action_type=action_type, action_payload=action_payload)
        if target_text and self._observation_contains_target_text(observation_payload, target_text=target_text):
            return {
                "mode": "target_text_visible",
                "targetText": target_text,
            }
        typed_text = str(action_payload.get("text") or "").strip()
        if (
            action_type in {"find_and_type", "type_text"}
            and typed_text
            and self._observation_contains_target_text(observation_payload, target_text=typed_text)
        ):
            return {
                "mode": "typed_text_visible",
                "targetText": typed_text,
            }
        return None

    def _post_action_visual_guard_skip_payload(
        self,
        *,
        action_type: str,
        action_payload: Dict[str, Any],
        verification: ComputerUseVerification,
        post_action_visual_locator: Dict[str, Any] | None,
        post_observation: Dict[str, Any] | None,
        post_window_title: str | None,
        post_window_handle: Any,
        app_id: str | None,
        high_risk_action: bool,
    ) -> Dict[str, Any] | None:
        explicit = action_payload.get("require_visual_guard")
        if explicit is True or high_risk_action or not isinstance(post_observation, dict):
            return None
        if isinstance(post_action_visual_locator, dict) and post_action_visual_locator.get("confirmed") is True:
            return {
                "mode": "post_action_visual_locator",
                "reason": str(post_action_visual_locator.get("reason") or "统一视觉定位层已确认动作后结果。"),
                "targetText": list(post_action_visual_locator.get("expectedTexts") or []),
            }
        if not self._is_fast_path_requested(action_payload) or not self._is_strong_structured_verification(verification):
            return None
        if bool(
            action_payload.get("file_path")
            or action_payload.get("file_paths")
            or action_payload.get("attachment_paths")
        ):
            return None
        requested_action = str(action_payload.get("profile_action") or action_type).strip().lower()
        target_text = self._action_target_text_hint(action_type=action_type, action_payload=action_payload)
        window_match = self._observation_window_matches(
            post_observation,
            expected_title=post_window_title,
            expected_handle=post_window_handle,
            app_id=app_id,
        )
        target_match = self._observation_contains_target_text(post_observation, target_text=target_text) if target_text else False
        typed_text = str(action_payload.get("text") or "").strip()
        typed_match = self._observation_contains_target_text(post_observation, target_text=typed_text) if typed_text else False
        if requested_action in {"open_app", "focus_window"} and window_match:
            return {"mode": "window_match", "reason": "结构化验证和窗口观察已确认目标窗口。"}
        if requested_action in {"find_and_type", "type_text"} and (target_match or typed_match):
            return {
                "mode": "text_visible",
                "reason": "结构化验证已通过，且界面中已出现目标文本，跳过额外视觉保底。",
                "targetText": target_text or typed_text,
            }
        if requested_action in {"click", "double_click", "click_toolbar_action"} and window_match and target_match:
            return {
                "mode": "window_and_target_match",
                "reason": "结构化验证已通过，且界面中已出现目标结果，跳过额外视觉保底。",
                "targetText": target_text,
            }
        return None

    def _default_post_action_settle_timeout_ms(
        self,
        *,
        action_type: str,
        action_payload: Dict[str, Any],
        high_risk_action: bool,
        visual_guard_requested: bool,
    ) -> int:
        explicit = action_payload.get("post_action_settle_timeout_ms")
        if explicit in (None, ""):
            explicit = action_payload.get("settle_timeout_ms")
        if explicit not in (None, ""):
            return max(0, self._int_payload_value(explicit, 0))
        normalized_action = str(action_type or "").strip().lower()
        fast_path = self._is_fast_path_requested(action_payload)
        if normalized_action == "open_app":
            return 900 if fast_path and not high_risk_action else 1800
        if normalized_action == "focus_window":
            return 320 if fast_path and not high_risk_action else 700
        if normalized_action in {"click", "double_click", "click_toolbar_action"}:
            if fast_path and not high_risk_action and not visual_guard_requested:
                return 520
            if fast_path and not high_risk_action:
                return 900
            return 2200 if (high_risk_action or visual_guard_requested) else 1200
        if normalized_action in {"find_and_type", "type_text"}:
            has_attachment_payload = bool(
                action_payload.get("file_path")
                or action_payload.get("file_paths")
                or action_payload.get("attachment_paths")
            )
            if has_attachment_payload:
                if fast_path and not high_risk_action:
                    return 2100 if bool(action_payload.get("window_typing")) else 1600
                return 3600 if bool(action_payload.get("window_typing")) else 2800
            if fast_path and not high_risk_action:
                if bool(action_payload.get("press_enter")) or bool(action_payload.get("window_typing")):
                    return 900
                return 320
            if bool(action_payload.get("press_enter")) or bool(action_payload.get("window_typing")):
                return 1600
            return 700
        if normalized_action in {"scroll", "scroll_list"}:
            if fast_path and not high_risk_action:
                return 260
            return 600
        return 0

    def _wait_for_post_action_stability(
        self,
        *,
        run_handle,
        action_type: str,
        action_payload: Dict[str, Any],
        window_title: str | None,
        window_handle: int | None,
        app_id: str | None,
        high_risk_action: bool,
        visual_guard_requested: bool,
    ) -> tuple[ComputerUseObservation | None, Dict[str, Any] | None]:
        timeout_ms = self._default_post_action_settle_timeout_ms(
            action_type=action_type,
            action_payload=action_payload,
            high_risk_action=high_risk_action,
            visual_guard_requested=visual_guard_requested,
        )
        if timeout_ms <= 0:
            return None, None
        poll_ms = max(80, self._int_payload_value(action_payload.get("post_action_settle_poll_ms") or action_payload.get("settle_poll_ms"), 220))
        fast_path = self._is_fast_path_requested(action_payload)
        stable_rounds_default = 1 if (fast_path and not high_risk_action) else 2
        stable_rounds = max(1, self._int_payload_value(action_payload.get("post_action_stable_rounds") or action_payload.get("stable_rounds"), stable_rounds_default))
        run_handle.emit(
            "computer_use.action.settle_wait_started",
            {
                "actionType": action_type,
                "timeoutMs": timeout_ms,
                "pollMs": poll_ms,
                "stableRounds": stable_rounds,
                "windowTitle": window_title,
                "windowHandle": window_handle,
            },
        )
        deadline = time.time() + (timeout_ms / 1000.0)
        last_signature: tuple[Any, ...] | None = None
        stable_count = 0
        last_observation: ComputerUseObservation | None = None
        started_at = time.time()
        last_error: str | None = None
        while time.time() < deadline:
            try:
                observed = self.driver.observe_desktop(
                    window_title=window_title,
                    window_handle=window_handle,
                    depth_limit=3,
                    element_limit=60,
                    use_cache=False,
                )
                if app_id:
                    observed.metadata["appId"] = app_id
                    observed.metadata["profileId"] = app_id
                signature = (
                    observed.window_title,
                    observed.tree_hash,
                    observed.screen_hash,
                    observed.focused_element_id,
                )
                stable_count = stable_count + 1 if signature == last_signature else 1
                last_signature = signature
                last_observation = observed
                fast_path_evidence = self._fast_path_settle_evidence(
                    action_type=action_type,
                    action_payload=action_payload,
                    observation=observed,
                    window_title=window_title,
                    window_handle=window_handle,
                    app_id=app_id,
                    high_risk_action=high_risk_action,
                )
                if stable_count >= stable_rounds or isinstance(fast_path_evidence, dict):
                    payload = {
                        "status": "settled",
                        "timeoutMs": timeout_ms,
                        "pollMs": poll_ms,
                        "stableRounds": stable_rounds,
                        "observedRounds": stable_count,
                        "elapsedMs": int((time.time() - started_at) * 1000),
                        "settledBy": "fast_path_signal" if isinstance(fast_path_evidence, dict) else "stable_rounds",
                        "fastPathEvidence": fast_path_evidence,
                    }
                    run_handle.emit(
                        "computer_use.action.settle_wait_completed",
                        {
                            "actionType": action_type,
                            **payload,
                        },
                    )
                    return observed, payload
            except Exception as exc:
                last_error = str(exc)
            time.sleep(poll_ms / 1000.0)
        payload = {
            "status": "timeout",
            "timeoutMs": timeout_ms,
            "pollMs": poll_ms,
            "stableRounds": stable_rounds,
            "observedRounds": stable_count,
            "elapsedMs": int((time.time() - started_at) * 1000),
            "error": last_error,
        }
        run_handle.emit(
            "computer_use.action.settle_wait_timeout",
            {
                "actionType": action_type,
                **payload,
            },
        )
        return last_observation, payload

    def _build_update_request(
        self,
        *,
        action_type: str,
        action_payload: Dict[str, Any],
        result: ComputerUseActionResult,
    ) -> Dict[str, Any] | None:
        verification = self._normalize_verification(result.verification)
        visual_guard = dict(result.metadata.get("visualGuard") or {}) if isinstance(result.metadata, dict) else {}
        selector_error = str((result.metadata or {}).get("selectorFallbackError") or "").strip() if isinstance(result.metadata, dict) else ""
        major_statuses = {
            "visual_guard_unconfirmed",
            "high_risk_visual_confirmation_required",
            "high_risk_pre_action_confirmation_required",
            "window_unresolved",
            "pre_action_blocker_detected",
        }
        has_visual_mismatch = bool(
            visual_guard
            and str(visual_guard.get("status") or "").strip().lower() == "analyzed"
            and visual_guard.get("confirmed") is False
        )
        if verification.status not in major_statuses and not has_visual_mismatch and not selector_error:
            return None
        app_id = self._infer_app_id_from_payloads(
            step=action_payload,
            target=result.target,
            observation=result.observation.as_dict() if result.observation else None,
        )
        target_text_hint = self._action_target_text_hint(action_type=action_type, action_payload=action_payload)
        expected_selector = {
            key: value
            for key, value in {
                "selectorKey": action_payload.get("selector_key"),
                "elementId": action_payload.get("element_id"),
                "name": action_payload.get("name"),
                "nameContains": action_payload.get("name_contains"),
                "targetText": target_text_hint,
                "automationId": action_payload.get("automation_id"),
                "controlType": action_payload.get("control_type"),
                "className": action_payload.get("class_name"),
            }.items()
            if value not in (None, "")
        }
        reason = (
            verification.reason
            or str(visual_guard.get("reason") or "").strip()
            or selector_error
            or "检测到页面结构变化或重大操作偏差，需要更新步骤。"
        )
        return {
            "requested": True,
            "kind": "ui_update_request",
            "reason": reason,
            "actionType": action_type,
            "profileAction": action_payload.get("profile_action") or action_type,
            "appId": app_id,
            "windowTitle": action_payload.get("window_title") or result.target.get("windowTitle") or result.target.get("title"),
            "windowHandle": action_payload.get("window_handle") or result.target.get("windowHandle") or result.target.get("handle"),
            "selectorKey": action_payload.get("selector_key"),
            "targetText": target_text_hint,
            "expectedSelector": expected_selector,
            "suggestedSelector": dict(visual_guard.get("suggestedSelector") or {}),
            "suggestedPoint": list(visual_guard.get("suggestedPoint") or []) if isinstance(visual_guard.get("suggestedPoint"), list) else None,
            "verification": verification.as_dict(),
            "visualGuard": visual_guard or None,
            "selectorFallbackError": selector_error or None,
        }

    def _recommended_next_action(
        self,
        *,
        action_type: str,
        result: ComputerUseActionResult,
        verification: ComputerUseVerification,
        update_request: Dict[str, Any] | None,
    ) -> str:
        scene = dict(result.metadata.get("scene") or {}) if isinstance(result.metadata, dict) else {}
        return build_result_contract(
            action_type=action_type,
            execution_mode=self._execution_mode(action_payload={}, scene=scene),
            result=result,
            verification=verification,
            update_request=update_request,
        )["recommendedNextAction"]

    def _blocked_reason(
        self,
        *,
        result: ComputerUseActionResult,
        verification: ComputerUseVerification,
        update_request: Dict[str, Any] | None,
    ) -> str | None:
        scene = dict(result.metadata.get("scene") or {}) if isinstance(result.metadata, dict) else {}
        return build_result_contract(
            action_type=str((result.metadata or {}).get("primitive", {}).get("action") or result.action_type or ""),
            execution_mode=self._execution_mode(action_payload={}, scene=scene),
            result=result,
            verification=verification,
            update_request=update_request,
        )["blockedReason"]

    def _execution_mode(self, *, action_payload: Dict[str, Any], scene: Dict[str, Any]) -> str:
        explicit = str(action_payload.get("execution_mode") or action_payload.get("executionMode") or "").strip().lower()
        if explicit in {"reuse_mode", "hybrid_mode", "learn_mode"}:
            return explicit
        rollout_mode = str(scene.get("templateRolloutMode") or scene.get("rolloutMode") or "").strip().lower()
        if rollout_mode in {"template_preferred", "template_preferred_with_fallback"}:
            return "reuse_mode"
        if rollout_mode in {"candidate_shadow", "computer_use_first"}:
            return "hybrid_mode"
        return "learn_mode"

    def _resolve_execution_route(
        self,
        *,
        action_type: str,
        action_payload: Dict[str, Any],
        result: ComputerUseActionResult,
        verification: ComputerUseVerification | None,
        update_request: Dict[str, Any] | None,
    ) -> Dict[str, Any]:
        metadata = dict(result.metadata or {})
        target_metadata = dict(result.target.get("metadata") or {}) if isinstance(result.target, dict) else {}
        existing_payload = dict(metadata.get("executionRoute") or {})
        route = str(
            existing_payload.get("route")
            or target_metadata.get("route")
            or metadata.get("route")
            or ""
        ).strip().lower()
        allowed_routes = {
            "native_command",
            "browser_automation",
            "structured_accessibility",
            "visual_locator",
            "coordinate_fallback",
            "human_approval",
        }
        if route not in allowed_routes:
            route = ""
        has_visual_locator = bool(
            self._has_explicit_visual_locator(action_payload)
            or metadata.get("visualLocator")
            or metadata.get("startVisualLocator")
            or metadata.get("endVisualLocator")
            or metadata.get("postActionVisualLocator")
            or target_metadata.get("visualLocator")
        )
        coordinate_fallback = bool(
            target_metadata.get("coordinateFallback")
            or metadata.get("coordinateFallback")
            or target_metadata.get("coordinateSource")
            or result.target.get("clickedPoint")
            or action_payload.get("point")
            or self._normalize_runtime_point_candidates(
                action_payload.get("point_candidates"),
                action_payload.get("pointCandidates"),
            )
        )
        verification_status = str((verification.status if verification else "") or "").strip().lower()
        human_approval_required = bool(
            verification_status in {
                "high_risk_visual_confirmation_required",
                "high_risk_pre_action_confirmation_required",
            }
            or (isinstance(update_request, dict) and update_request.get("requested") and str(update_request.get("kind") or "").strip().lower() == "human_approval")
        )
        browser_target_family = str(
            target_metadata.get("browserTargetFamily")
            or ((metadata.get("browserAutomation") or {}).get("family") if isinstance(metadata.get("browserAutomation"), dict) else "")
            or existing_payload.get("browserTargetFamily")
            or ""
        ).strip().lower() or None
        browser_lane_reason = str(
            target_metadata.get("browserLaneReason")
            or ((metadata.get("browserAutomation") or {}).get("reason") if isinstance(metadata.get("browserAutomation"), dict) else "")
            or existing_payload.get("browserLaneReason")
            or ""
        ).strip() or None
        browser_lane_provider = str(
            target_metadata.get("browserLaneProvider")
            or ((metadata.get("browserAutomation") or {}).get("provider") if isinstance(metadata.get("browserAutomation"), dict) else "")
            or existing_payload.get("browserLaneProvider")
            or ""
        ).strip() or None
        browser_lane_available = bool(
            target_metadata.get("route") == "browser_automation"
            or target_metadata.get("browserTargetId")
            or (isinstance(metadata.get("browserAutomation"), dict) and metadata.get("browserAutomation", {}).get("available"))
        )
        if not browser_target_family or not browser_lane_reason or not browser_lane_provider:
            browser_decision = self._browser_lane_decision(
                action_type=action_type,
                action_payload=action_payload,
                app_id=(
                    result.target.get("appId")
                    or result.target.get("profileId")
                    or action_payload.get("app_id")
                    or action_payload.get("resolved_app_id")
                ),
                process_name=(target_metadata.get("processName") or result.target.get("processName")),
            )
            browser_target_family = browser_target_family or browser_decision.family
            browser_lane_reason = browser_lane_reason or browser_decision.reason
            browser_lane_provider = browser_lane_provider or browser_decision.provider
            browser_lane_available = browser_lane_available or bool(browser_decision.available)
        capability_matrix = self._runtime_capability_matrix()
        capability_truth = dict(capability_matrix.get("truth") or {})
        policy_result = decide_execution_route(
            action_type=action_type,
            current_platform=str(getattr(self.driver, "platform", "") or os.name),
            capability_truth=capability_truth,
            control_class=self._control_class_for_action(target=result.target, action_payload=action_payload),
            browser_lane_available=browser_lane_available,
            browser_target_family=browser_target_family,
            browser_lane_reason=browser_lane_reason,
            has_visual_locator=has_visual_locator,
            coordinate_fallback=coordinate_fallback,
            human_approval_required=human_approval_required,
            existing_route=route,
        )
        return {
            **policy_result,
            "visualLocatorBacked": has_visual_locator,
            "coordinateFallback": coordinate_fallback,
            "humanApprovalRequired": human_approval_required,
            "windowHandle": result.target.get("windowHandle") or result.target.get("window_handle"),
            "windowTitle": result.target.get("windowTitle") or result.target.get("window_title") or result.target.get("title"),
            "primitiveId": str((metadata.get("primitive") or {}).get("id") or ""),
            "browserTargetFamily": browser_target_family,
            "browserLaneReason": browser_lane_reason,
            "browserLaneProvider": browser_lane_provider,
            "browserLaneAvailable": browser_lane_available,
            "routePolicy": self._platform_route_policy_summary(capability_truth=capability_truth),
        }

    def _learning_loop_summary(
        self,
        *,
        execution_mode: str,
        result: ComputerUseActionResult,
        verification: ComputerUseVerification,
        update_request: Dict[str, Any] | None,
    ) -> Dict[str, Any]:
        return build_result_contract(
            action_type=str((result.metadata or {}).get("primitive", {}).get("action") or ""),
            execution_mode=execution_mode,
            result=result,
            verification=verification,
            update_request=update_request,
        )["learningLoop"]

    def _build_result_evidence_summary(self, result: ComputerUseActionResult) -> Dict[str, Any]:
        scene = dict(result.metadata.get("scene") or {}) if isinstance(result.metadata, dict) else {}
        return build_result_contract(
            action_type=str((result.metadata or {}).get("primitive", {}).get("action") or result.action_type or ""),
            execution_mode=self._execution_mode(action_payload={}, scene=scene),
            result=result,
            verification=self._normalize_verification(result.verification),
            update_request=dict(result.metadata.get("updateRequest") or {}) if isinstance(result.metadata.get("updateRequest"), dict) else None,
        )["evidenceSummary"]

    def _should_abort_on_major_deviation(self, *, action: str, step: Dict[str, Any]) -> bool:
        explicit = step.get("abort_on_major_deviation")
        if explicit not in (None, ""):
            return bool(explicit)
        normalized_action = str(action or "").strip().lower()
        return normalized_action in {
            "open_app",
            "focus_window",
            "click",
            "double_click",
            "type_text",
            "find_and_type",
            "click_toolbar_action",
        }

    def _extract_update_request(self, result: Dict[str, Any] | None) -> Dict[str, Any] | None:
        metadata = (((result or {}).get("result") or {}).get("metadata") or {})
        if not isinstance(metadata, dict):
            return None
        payload = metadata.get("updateRequest")
        if not isinstance(payload, dict):
            return None
        return dict(payload)

    def _trace_variable_name(self, key: str) -> str | None:
        normalized = str(key or "").strip().lower()
        if not normalized:
            return None
        if any(token in normalized for token in ("timeout", "poll", "settle", "stable_round", "deviation")):
            return None
        alias_map = {
            "text": "input_text",
            "target_text": "target_text",
            "query": "query",
            "search": "query",
            "contact": "contact_name",
            "contact_name": "contact_name",
            "song": "song_name",
            "song_name": "song_name",
            "message": "message",
            "message_body": "message_body",
            "subject": "email_subject",
            "email_subject": "email_subject",
            "recipient": "recipient",
            "to": "recipient",
            "amount": "amount",
            "search_text": "query",
            "file_path": "file_path",
            "file_paths": "file_paths",
            "attachment_paths": "file_paths",
            "path": "file_path",
            "url": "url",
        }
        if normalized in alias_map:
            return alias_map[normalized]
        for needle, value in (
            ("contact", "contact_name"),
            ("song", "song_name"),
            ("message", "message"),
            ("subject", "email_subject"),
            ("recipient", "recipient"),
            ("amount", "amount"),
            ("query", "query"),
            ("search", "query"),
            ("path", "file_path"),
            ("attachment", "file_paths"),
            ("file_paths", "file_paths"),
            ("url", "url"),
            ("text", "input_text"),
        ):
            if needle in normalized:
                return value
        return None

    def _is_variableizable_payload_value(self, value: Any) -> bool:
        if isinstance(value, (str, int, float)) and str(value).strip():
            return True
        if isinstance(value, list) and value:
            return all(
                isinstance(item, (str, int, float)) and str(item).strip()
                for item in value
            )
        return False

    def _trace_params(
        self,
        *,
        action_payload: Dict[str, Any],
    ) -> tuple[Dict[str, Any], Dict[str, Any], List[ComputerUseTraceVariable]]:
        ignored_keys = {
            "app_id",
            "app_name",
            "profile_action",
            "profile_id",
            "visual_expectation",
            "require_visual_guard",
            "transient_selector",
            "window_title",
            "window_handle",
            "class_name",
            "process_name",
            "process_names",
            "element_id",
            "name",
            "automation_id",
            "automationId",
            "control_type",
            "controlType",
            "role",
            "className",
            "selector_key",
        }
        templated: Dict[str, Any] = {}
        raw_params: Dict[str, Any] = {}
        variables: List[ComputerUseTraceVariable] = []

        for key, value in dict(action_payload or {}).items():
            if key in ignored_keys or value is None:
                continue
            raw_params[key] = value
            variable_name = self._trace_variable_name(key)
            if variable_name and self._is_variableizable_payload_value(value):
                placeholder = f"{{{{{variable_name}}}}}"
                templated[key] = placeholder
                variables.append(
                    ComputerUseTraceVariable(
                        name=variable_name,
                        placeholder=placeholder,
                        original_key=key,
                        example_value=value,
                    )
                )
                continue
            templated[key] = value
        return templated, raw_params, variables

    def _trace_target(
        self,
        *,
        action_payload: Dict[str, Any],
        result: ComputerUseActionResult,
    ) -> ComputerUseTraceTarget:
        observation = result.observation.as_dict() if result.observation else {}
        observation_meta = dict(observation.get("metadata") or {}) if isinstance(observation, dict) else {}
        target = dict(result.target or {})
        target_metadata = dict(target.get("metadata") or {}) if isinstance(target, dict) else {}
        window = {
            "title": (
                target.get("windowTitle")
                or action_payload.get("window_title")
                or observation.get("windowTitle")
            ),
            "className": (
                target.get("className")
                or action_payload.get("class_name")
                or observation_meta.get("className")
            ),
            "processName": (
                target.get("processName")
                or action_payload.get("process_name")
                or observation_meta.get("processName")
            ),
            "windowHandle": (
                target.get("windowHandle")
                or target.get("window_handle")
                or action_payload.get("window_handle")
                or observation_meta.get("windowHandle")
            ),
        }
        selector = {
            "selectorKey": action_payload.get("selector_key"),
            "elementId": target.get("elementId") or target.get("element_id") or action_payload.get("element_id"),
            "name": target.get("name") or action_payload.get("name"),
            "automationId": target.get("automationId") or action_payload.get("automation_id"),
            "controlType": target.get("role") or target.get("controlType") or action_payload.get("control_type") or action_payload.get("role"),
            "className": target.get("className") or action_payload.get("class_name"),
            "handle": target.get("handle"),
        }
        spatial_anchor = dict(
            action_payload.get("spatial_anchor")
            or action_payload.get("spatialAnchor")
            or target_metadata.get("spatialAnchor")
            or {}
        )
        if not spatial_anchor:
            spatial_anchor = build_spatial_anchor(target=target, observation=observation)
        return ComputerUseTraceTarget(
            window={key: value for key, value in window.items() if value not in (None, "")},
            selector={key: value for key, value in selector.items() if value not in (None, "")},
            spatial_anchor=spatial_anchor,
        )

    def _trace_recovery(
        self,
        *,
        action_payload: Dict[str, Any],
        result: ComputerUseActionResult,
    ) -> ComputerUseTraceRecovery:
        metadata = dict(result.metadata or {})
        visual_fallback = dict(metadata.get("visualFallback") or {}) if isinstance(metadata.get("visualFallback"), dict) else {}
        recovery_strategy = "retry" if result.attempt_count > 1 else "direct"
        if visual_fallback:
            recovery_strategy = "visual"
        app_id = self._infer_app_id_from_payloads(
            step=action_payload,
            target=result.target,
            observation=result.observation.as_dict() if result.observation else None,
        )
        action_name = str(action_payload.get("profile_action") or result.action_type or "").strip().lower()
        return ComputerUseTraceRecovery(
            transient=bool(action_payload.get("transient_selector")),
            fallback_order=recovery_fallback_order(
                high_risk=self._is_high_risk_action(app_id=app_id, action_name=action_name),
            ),
            performed=bool(result.attempt_count > 1 or visual_fallback),
            strategy=recovery_strategy,
            details={
                "attemptCount": result.attempt_count,
                "visualFallback": visual_fallback or None,
                "selectorStats": metadata.get("selectorStats"),
            },
        )

    def _trace_risk(
        self,
        *,
        action_type: str,
        action_payload: Dict[str, Any],
        result: ComputerUseActionResult,
        high_risk_action: bool,
        visual_guard_requested: bool,
        pre_action_guard_requested: bool,
    ) -> ComputerUseTraceRisk:
        level = "low"
        if high_risk_action:
            level = "high"
        elif visual_guard_requested or bool(action_payload.get("transient_selector")):
            level = "medium"
        return ComputerUseTraceRisk(
            level=level,
            high_risk_action=high_risk_action,
            requires_pre_guard=pre_action_guard_requested,
            requires_post_guard=visual_guard_requested,
            details={
                "actionType": action_type,
                "status": result.status,
                "profileAction": action_payload.get("profile_action"),
                "visualExpectation": action_payload.get("visual_expectation"),
                "targetText": action_payload.get("target_text"),
                "postActionSettleTimeoutMs": action_payload.get("post_action_settle_timeout_ms"),
                "postActionSettlePollMs": action_payload.get("post_action_settle_poll_ms"),
                "postActionStableRounds": action_payload.get("post_action_stable_rounds"),
                "abortOnMajorDeviation": self._should_abort_on_major_deviation(action=action_type, step=action_payload),
                "clipboardPayload": dict(result.metadata.get("clipboardPayload") or {}) if isinstance(result.metadata, dict) else {},
                "targetStrategyApplied": dict(result.metadata.get("targetStrategyApplied") or {}) if isinstance(result.metadata, dict) else {},
                "updateRequest": dict(result.metadata.get("updateRequest") or {}) if isinstance(result.metadata, dict) else {},
                "learnedInteraction": dict(result.metadata.get("learnedInteraction") or {}) if isinstance(result.metadata, dict) else {},
                "visualGuardSkipped": dict(result.metadata.get("visualGuardSkipped") or {}) if isinstance(result.metadata, dict) else {},
                "feedbackSuggestions": dict(result.metadata.get("feedbackSuggestions") or {}) if isinstance(result.metadata, dict) else {},
            },
        )

    def _trace_artifacts(self, *, result: ComputerUseActionResult) -> List[Dict[str, Any]]:
        artifacts: List[Dict[str, Any]] = []
        if isinstance(result.artifact, dict) and result.artifact:
            artifacts.append(dict(result.artifact))
        observation_artifact = (
            result.observation.screenshot_artifact
            if isinstance(result.observation, ComputerUseObservation)
            else None
        )
        if isinstance(observation_artifact, dict) and observation_artifact:
            artifacts.append(dict(observation_artifact))
        deduped: List[Dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for item in artifacts:
            key = (str(item.get("artifactId") or ""), str(item.get("path") or item.get("file_path") or ""))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

    def _trace_phase(
        self,
        *,
        action_type: str,
        result: ComputerUseActionResult,
        recovery: ComputerUseTraceRecovery,
    ) -> str:
        normalized_action = str(action_type or "").strip().lower()
        verification = self._normalize_verification(result.verification)
        if recovery.performed:
            return "recovery"
        if normalized_action in {"observe", "find", "screenshot"}:
            return "observation"
        if normalized_action in {"open_app", "focus_window", "switch_window"}:
            return "decision"
        if normalized_action in {"wait", "wait_for_element"}:
            return "verification"
        if verification.level in {"verified", "soft_verified", "executed_only", "review_required", "failed"}:
            return "verification"
        return "action"

    def _trace_failure_category(
        self,
        *,
        result: ComputerUseActionResult,
        scene_payload: Dict[str, Any],
        binding_decision: AppBindingDecision | None,
    ) -> str:
        status = str(result.status or "").strip().lower()
        verification = self._normalize_verification(result.verification).as_dict()
        verification_status = str(verification.get("status") or "").strip().lower()
        blocker_state = str(scene_payload.get("blockerState") or "").strip().lower()
        binding_confidence = float(getattr(binding_decision, "binding_confidence", 0.0) or 0.0)
        binding_mode = str(getattr(binding_decision, "binding_mode", "none") or "none").strip().lower()
        visual_fallback = (
            dict(result.metadata.get("visualFallback") or {})
            if isinstance(result.metadata, dict) and isinstance(result.metadata.get("visualFallback"), dict)
            else {}
        )
        if blocker_state and blocker_state not in {"none", "ready"}:
            return "preflight"
        if status in {"failed", "error", "blocked", "update_requested"} and visual_fallback:
            return "visual_fallback"
        if status in {"failed", "error", "blocked"} and binding_mode in {"heuristic", "none"} and binding_confidence < 0.6:
            return "binding"
        if verification_status in {
            "visual_guard_unconfirmed",
            "high_risk_visual_confirmation_required",
            "high_risk_pre_action_confirmation_required",
            "review_required",
            "verification_failed",
        }:
            return "verification"
        if status in {"failed", "error", "blocked"}:
            return "semantic_action"
        return "unknown"

    def _trace_signals(
        self,
        *,
        action_payload: Dict[str, Any],
        result: ComputerUseActionResult,
        verification: Dict[str, Any],
        recovery: ComputerUseTraceRecovery,
        scene_payload: Dict[str, Any],
        binding_decision: AppBindingDecision | None,
        invocation,
    ) -> Dict[str, Any]:
        result_metadata = dict(result.metadata or {})
        recovery_policy = dict(result_metadata.get("recoveryPolicy") or {})
        blocked_reason = result_metadata.get("blockedReason")
        visual_guard_skipped = dict(result_metadata.get("visualGuardSkipped") or {})
        binding_evidence = dict(getattr(binding_decision, "binding_evidence", {}) or {})
        target = dict(result.target or {})
        observation_payload = result.observation.as_dict() if result.observation else {}
        target_window = dict(target.get("window") or {})
        focus_confirmed = bool(
            observation_payload.get("focusedElementId")
            or result_metadata.get("focusKnown")
            or dict(result_metadata.get("environmentProbe") or {}).get("focusKnown")
        )
        window_bound = bool(
            target_window
            or target.get("windowHandle")
            or target.get("window_handle")
            or binding_evidence.get("windowTitle")
            or binding_evidence.get("className")
        )
        scene_bound = bool(scene_payload.get("pageIdentity") or window_bound)
        blocker_state = str(scene_payload.get("blockerState") or "none").strip().lower() or "none"
        failure_category = self._trace_failure_category(
            result=result,
            scene_payload=scene_payload,
            binding_decision=binding_decision,
        )
        return {
            "binding": {
                "requestedAppId": getattr(binding_decision, "requested_app_id", None),
                "resolvedAppId": getattr(binding_decision, "resolved_app_id", None),
                "bindingMode": getattr(binding_decision, "binding_mode", "none"),
                "bindingConfidence": round(float(getattr(binding_decision, "binding_confidence", 0.0) or 0.0), 3),
                "bindingEvidence": binding_evidence,
            },
            "preflight": {
                "focusConfirmed": focus_confirmed,
                "windowBound": window_bound,
                "sceneBound": scene_bound,
                "blockerDetected": blocker_state not in {"", "none", "ready"},
                "riskDowngraded": bool(blocked_reason or visual_guard_skipped or getattr(invocation, "compat_debug", False)),
            },
            "verification": {
                "passed": bool(verification.get("passed")),
                "status": str(verification.get("status") or "").strip().lower() or None,
                "level": str(verification.get("level") or "").strip().lower() or None,
                "reason": verification.get("reason"),
                "blockedReason": blocked_reason,
            },
            "recovery": {
                "semanticPathTried": True,
                "controlledFallbackTried": bool(recovery.performed and recovery.strategy in {"retry", "direct"}),
                "visualFallbackTried": bool(recovery.performed and recovery.strategy == "visual"),
                "strictVerificationApplied": bool(
                    verification.get("level") in {"verified", "review_required", "failed"}
                    or recovery_policy.get("highRisk")
                ),
                "finalRecoveryStage": (
                    "visual_fallback"
                    if recovery.performed and recovery.strategy == "visual"
                    else "controlled_fallback"
                    if recovery.performed
                    else "semantic_path"
                ),
                "fallbackOrder": list(recovery.fallback_order),
            },
            "failureCategory": failure_category,
        }

    def _record_trace_step(
        self,
        *,
        run_handle,
        goal: str | None,
        action_type: str,
        action_payload: Dict[str, Any],
        result: ComputerUseActionResult,
        snapshot: Dict[str, Any] | None,
        high_risk_action: bool,
        visual_guard_requested: bool,
        pre_action_guard_requested: bool,
        max_attempts: int,
        invocation,
        invocation_metadata: Optional[Dict[str, Any]] = None,
        binding_decision: AppBindingDecision | None,
    ) -> None:
        trace_action_payload = dict(action_payload or {})
        if (
            action_type == "type_text"
            and trace_action_payload.get("text") in (None, "")
            and isinstance(result.metadata, dict)
            and result.metadata.get("text") not in (None, "")
        ):
            trace_action_payload["text"] = result.metadata.get("text")
        app_id = self._infer_app_id_from_payloads(
            step=trace_action_payload,
            target=result.target,
            observation=result.observation.as_dict() if result.observation else None,
        ) or "desktop"
        primitive_payload = dict(result.metadata.get("primitive") or resolve_computer_use_primitive(action_type, trace_action_payload).as_dict())
        primitive_payload["supportsRpaPromotion"] = promotion_allowed_for_invocation(
            primitive_payload=primitive_payload,
            invocation=invocation,
        )
        scene_payload = dict(result.metadata.get("scene") or {})
        budget_payload = dict(result.metadata.get("budget") or {})
        templated_params, raw_params, variables = self._trace_params(action_payload=trace_action_payload)
        verification = self._normalize_verification(result.verification).as_dict()
        recovery = self._trace_recovery(action_payload=trace_action_payload, result=result)
        phase = self._trace_phase(action_type=action_type, result=result, recovery=recovery)
        signals = self._trace_signals(
            action_payload=trace_action_payload,
            result=result,
            verification=verification,
            recovery=recovery,
            scene_payload=scene_payload,
            binding_decision=binding_decision,
            invocation=invocation,
        )
        step = ComputerUseTraceStep(
            step_id=str(result.action_id or f"trace_{uuid.uuid4().hex[:8]}"),
            app_id=app_id,
            action=action_type,
            intent=str(trace_action_payload.get("profile_action") or action_type),
            phase=phase,
            target=self._trace_target(action_payload=trace_action_payload, result=result),
            params=templated_params,
            raw_params=raw_params,
            variables=variables,
            verification=verification,
            recovery=recovery,
            risk=self._trace_risk(
                action_type=action_type,
                action_payload=trace_action_payload,
                result=result,
                high_risk_action=high_risk_action,
                visual_guard_requested=visual_guard_requested,
                pre_action_guard_requested=pre_action_guard_requested,
            ),
            artifacts=self._trace_artifacts(result=result),
            timing=ComputerUseTraceTiming(
                wait_timeout_ms=int(
                    action_payload.get("timeout_ms")
                    or action_payload.get("wait_timeout_ms")
                    or action_payload.get("post_action_settle_timeout_ms")
                    or 6000
                ),
                retry_limit=max_attempts,
                attempt_count=max(1, int(result.attempt_count or 1)),
                elapsed_ms=int(budget_payload.get("elapsedMs") or 0),
            ),
            primitive=ComputerUseTracePrimitive(
                primitive_id=str(primitive_payload.get("id") or "custom.unknown"),
                category=str(primitive_payload.get("category") or "custom"),
                action=str(primitive_payload.get("action") or action_type),
                affordances=list(primitive_payload.get("affordances") or []),
                requires_page_identity=bool(primitive_payload.get("requiresPageIdentity", True)),
                requires_verification_contract=bool(primitive_payload.get("requiresVerificationContract", True)),
                requires_recovery_policy=bool(primitive_payload.get("requiresRecoveryPolicy", True)),
                supports_rpa_promotion=bool(primitive_payload.get("supportsRpaPromotion", False)),
                notes=list(primitive_payload.get("notes") or []),
            ),
            scene=ComputerUseTraceScene(
                page_identity=str(scene_payload.get("pageIdentity") or ""),
                blocker_state=str(scene_payload.get("blockerState") or "none"),
                transition_state=str(scene_payload.get("transitionState") or "unknown"),
                affordances=list(scene_payload.get("affordances") or []),
                confidence=str(scene_payload.get("confidence") or "low"),
                reasons=list(scene_payload.get("reasons") or []),
            ),
            budget=ComputerUseTraceBudget(
                time_budget_ms=int(budget_payload.get("timeBudgetMs") or 0),
                retry_budget=int(budget_payload.get("retryBudget") or 0),
                vision_budget=int(budget_payload.get("visionBudget") or 0),
                token_budget=int(budget_payload.get("tokenBudget") or 0),
                fallback_budget=int(budget_payload.get("fallbackBudget") or 0),
                settle_budget_ms=int(budget_payload.get("settleBudgetMs") or 0),
                elapsed_ms=int(budget_payload.get("elapsedMs") or 0),
                attempts_used=int(budget_payload.get("attemptsUsed") or 0),
                vision_calls_used=int(budget_payload.get("visionCallsUsed") or 0),
                token_usage=int(budget_payload.get("tokenUsage") or 0),
                fallbacks_used=int(budget_payload.get("fallbacksUsed") or 0),
                within_budget=bool(budget_payload.get("withinBudget", True)),
                exceeded=list(budget_payload.get("exceeded") or []),
                source=str(budget_payload.get("source") or "default"),
            ),
            signals=signals,
            metadata={
                "status": result.status,
                "message": result.message,
                "goal": goal or action_type,
                "snapshotKind": snapshot.get("kind") if isinstance(snapshot, dict) else None,
                "invocation": invocation.as_dict() if invocation is not None else {},
                "bindingMode": getattr(binding_decision, "binding_mode", "none"),
                "bindingConfidence": round(float(getattr(binding_decision, "binding_confidence", 0.0) or 0.0), 3),
                "requestedAppId": getattr(binding_decision, "requested_app_id", None),
                "resolvedAppId": getattr(binding_decision, "resolved_app_id", None),
                "bindingEvidence": dict(getattr(binding_decision, "binding_evidence", {}) or {}),
                "selectorStats": dict(result.metadata.get("selectorStats") or {}) if isinstance(result.metadata, dict) else {},
                "stabilityWait": dict(result.metadata.get("stabilityWait") or {}) if isinstance(result.metadata, dict) else {},
                "clipboardPayload": dict(result.metadata.get("clipboardPayload") or {}) if isinstance(result.metadata, dict) else {},
                "targetStrategyApplied": dict(result.metadata.get("targetStrategyApplied") or {}) if isinstance(result.metadata, dict) else {},
                "updateRequest": dict(result.metadata.get("updateRequest") or {}) if isinstance(result.metadata, dict) else {},
                "learnedInteraction": dict(result.metadata.get("learnedInteraction") or {}) if isinstance(result.metadata, dict) else {},
                "visualGuardSkipped": dict(result.metadata.get("visualGuardSkipped") or {}) if isinstance(result.metadata, dict) else {},
            },
        )
        trace_payload = self.trace_store.append_step(
            run_id=run_handle.run_id,
            session_id=run_handle.session_id,
            goal=goal or action_type,
            runtime_kind="computer_use",
            step=step,
            metadata={
                "traceSchemaVersion": 2,
                "appId": app_id,
                "invocationSource": getattr(invocation, "invocation_source", None),
                "executionIntent": getattr(invocation, "execution_intent", None),
                "bindingMode": getattr(binding_decision, "binding_mode", "none"),
                "bindingConfidence": round(float(getattr(binding_decision, "binding_confidence", 0.0) or 0.0), 3),
                "requestedAppId": getattr(binding_decision, "requested_app_id", None),
                "resolvedAppId": getattr(binding_decision, "resolved_app_id", None),
                "requestedGoal": str(dict(invocation_metadata or {}).get("requestedGoal") or "").strip() or None,
                "rootGoal": str(dict(invocation_metadata or {}).get("rootGoal") or "").strip() or None,
            },
        )
        run_handle.emit(
            "computer_use.trace.step_recorded",
            {
                "runId": run_handle.run_id,
                "stepId": step.step_id,
                "stepCount": int(trace_payload.get("stepCount") or 0),
                "appId": app_id,
                "invocation": invocation.as_dict() if invocation is not None else {},
                "binding": binding_decision.as_dict() if binding_decision is not None else self._binding_metadata(None),
            },
        )

    def _verification_target(self, action_payload: Dict[str, Any], result: ComputerUseActionResult) -> Dict[str, Any]:
        target = dict(result.target or {})
        for key in (
            "element_id",
            "name",
            "name_contains",
            "automation_id",
            "control_type",
            "class_name",
            "window_title",
            "window_handle",
        ):
            if action_payload.get(key) is not None and key not in target:
                target[key] = action_payload[key]
        if "elementId" not in target and target.get("element_id"):
            target["elementId"] = target["element_id"]
        if "handle" in target and target.get("window_handle") is None:
            target["window_handle"] = target.get("handle")
        if "windowHandle" not in target and target.get("window_handle") is not None:
            target["windowHandle"] = target["window_handle"]
        if "title" in target and not target.get("window_title"):
            target["window_title"] = target.get("title")
        if "windowTitle" not in target and target.get("window_title"):
            target["windowTitle"] = target["window_title"]
        return target

    def _visual_locator_request_from_payload(
        self,
        payload: Dict[str, Any],
        *,
        prefix: str = "",
    ) -> Dict[str, Any] | None:
        snake_prefix = str(prefix or "")
        prefix_segments = [segment for segment in snake_prefix.strip("_").split("_") if segment]

        def _pick(name: str, camel_name: str | None = None) -> Any:
            snake_key = f"{snake_prefix}{name}" if snake_prefix else name
            name_segments = [segment for segment in name.split("_") if segment]
            camel_tail = camel_name or "".join(part.capitalize() for part in name_segments)
            camel_segments = prefix_segments + ([camel_tail[0].lower() + camel_tail[1:]] if camel_tail else [])
            camel_key = ""
            if camel_segments:
                camel_key = camel_segments[0]
                if len(camel_segments) > 1:
                    camel_key += "".join(part[:1].upper() + part[1:] for part in camel_segments[1:])
            if snake_key in payload:
                return payload.get(snake_key)
            if camel_key and camel_key in payload:
                return payload.get(camel_key)
            return None

        locator = str(_pick("visual_locator", "VisualLocator") or "").strip()
        if not locator:
            return None
        scope_locator = str(_pick("visual_locator_scope", "VisualLocatorScope") or "").strip()
        scope_padding = _pick("visual_locator_scope_padding", "VisualLocatorScopePadding")
        scope_seed_strategy = str(_pick("visual_locator_scope_seed_strategy", "VisualLocatorScopeSeedStrategy") or "").strip()
        role_hint = str(_pick("visual_locator_role_hint", "VisualLocatorRoleHint") or "").strip()
        confidence = _pick("visual_locator_confidence", "VisualLocatorConfidence")
        timeout_ms = _pick("visual_locator_timeout_ms", "VisualLocatorTimeoutMs")
        multiple = bool(_pick("visual_locator_multiple", "VisualLocatorMultiple"))
        read_text = bool(_pick("visual_locator_read_text", "VisualLocatorReadText"))
        return {
            "locator": locator,
            "scope_locator": scope_locator or None,
            "scope_padding": list(scope_padding) if isinstance(scope_padding, (list, tuple)) and len(scope_padding) == 4 else None,
            "scope_seed_strategy": scope_seed_strategy or None,
            "role_hint": role_hint or None,
            "confidence": confidence,
            "timeout_ms": int(timeout_ms) if str(timeout_ms or "").strip() else 2500,
            "offset": None,
            "multiple": multiple,
            "read_text": read_text,
        }

    def _build_semantic_visual_resolution(
        self,
        *,
        locator: str,
        locator_role: str,
        scope_bounds: List[int] | None,
        capture_bounds: List[int] | None,
    ) -> Dict[str, Any] | None:
        candidates = build_semantic_visual_candidates(
            role=locator_role,
            scope_bounds=list(scope_bounds) if isinstance(scope_bounds, list) else None,
            capture_bounds=list(capture_bounds) if isinstance(capture_bounds, list) else None,
        )
        if not candidates:
            return None
        return semantic_candidates_to_resolution(
            locator=locator,
            role=locator_role,
            scope_bounds=list(scope_bounds) if isinstance(scope_bounds, list) else list(capture_bounds) if isinstance(capture_bounds, list) else None,
            candidates=candidates,
        )

    def _visual_locator_cache_key(
        self,
        payload: Dict[str, Any],
        *,
        locator: str,
        prefix: str = "",
    ) -> tuple[str, str, str]:
        normalized_locator = str(locator or "").strip().lower()
        window_handle = str(payload.get("window_handle") or payload.get("windowHandle") or "")
        window_title = str(payload.get("window_title") or payload.get("windowTitle") or "").strip().lower()
        return (str(prefix or ""), normalized_locator, window_handle or window_title)

    def _remember_visual_locator_resolution(
        self,
        *,
        cache_key: tuple[str, str, str, str],
        resolved: Dict[str, Any],
    ) -> None:
        if not cache_key or not isinstance(resolved, dict):
            return
        if not list(resolved.get("matches") or []):
            return
        self._recent_visual_locator_hits[cache_key] = {
            "resolved": dict(resolved),
            "ts": time.time(),
        }

    def _recent_visual_locator_resolution(
        self,
        *,
        cache_key: tuple[str, str, str, str],
        ttl_seconds: float = 30.0,
    ) -> Dict[str, Any] | None:
        cached = self._recent_visual_locator_hits.get(cache_key)
        if not isinstance(cached, dict):
            return None
        ts = float(cached.get("ts") or 0.0)
        age = time.time() - ts
        if age < 0 or age > ttl_seconds:
            self._recent_visual_locator_hits.pop(cache_key, None)
            return None
        resolved = dict(cached.get("resolved") or {})
        resolved["status"] = "reused_recent_hit"
        resolved["reused"] = True
        resolved["reuseAgeMs"] = int(round(age * 1000.0))
        return resolved

    def _locate_visual_locator_candidates(
        self,
        *,
        locator_candidates: List[str],
        locator_scope_bounds: List[int] | None,
        locator_role: str,
        preferred_bounds: List[int] | None,
        scope_first: bool,
        payload: Dict[str, Any],
        timeout_ms: int,
        confidence: Any,
        multiple: bool,
        read_text: bool,
        search_image_path: str | None,
        search_bounds: List[int] | None,
    ) -> Dict[str, Any]:
        if not locator_candidates:
            raise DesktopDriverError("visual locator 候选为空。")
        first_empty: Dict[str, Any] | None = None
        first_ambiguous: Dict[str, Any] | None = None
        successful_resolutions: List[Dict[str, Any]] = []
        errors: List[str] = []
        for index, candidate in enumerate(locator_candidates):
            try:
                resolved = self.visual_locator_runtime.locate(
                    locator=candidate,
                    timeout_ms=int(timeout_ms or 2500),
                    confidence=confidence,
                    multiple=bool(multiple),
                    read_text=bool(read_text),
                    search_image_path=search_image_path,
                    search_bounds=search_bounds,
                )
                if (
                    _normalize_ocr_query(candidate)
                    and not list(resolved.get("matches") or [])
                    and not bool(scope_first)
                    and search_image_path
                    and (payload.get("window_handle") not in (None, "") or str(payload.get("window_title") or "").strip())
                ):
                    region_resolved = self._resolve_ocr_locator_from_observation_regions(
                        locator=candidate,
                        payload=payload,
                        search_image_path=search_image_path,
                        timeout_ms=int(timeout_ms or 2500),
                    )
                    if isinstance(region_resolved, dict) and list(region_resolved.get("matches") or []):
                        resolved = region_resolved
                resolved = rank_visual_locator_resolution(
                    resolved,
                    locator=candidate,
                    scope_bounds=locator_scope_bounds,
                    role=locator_role,
                    preferred_bounds=preferred_bounds,
                )
                match_count = int(resolved.get("matchCount") or len(list(resolved.get("matches") or [])))
                ranking = dict(resolved.get("semanticRanking") or {})
                selected_strong = bool(ranking.get("selectedStrong"))
                if _normalize_ocr_query(candidate) and match_count > 1 and not selected_strong and index < len(locator_candidates) - 1:
                    if first_ambiguous is None:
                        first_ambiguous = dict(resolved or {})
                        first_ambiguous["ambiguousOcrCandidate"] = candidate
                        first_ambiguous["ambiguousOcrMatchCount"] = match_count
                    enriched = dict(resolved or {})
                    if len(locator_candidates) > 1:
                        enriched["locatorChain"] = list(locator_candidates)
                        enriched["locatorCandidateIndex"] = index
                        enriched["locatorCandidate"] = candidate
                    successful_resolutions.append(enriched)
                    continue
                if list(resolved.get("matches") or []):
                    enriched = dict(resolved or {})
                    if len(locator_candidates) > 1:
                        enriched["locatorChain"] = list(locator_candidates)
                        enriched["locatorCandidateIndex"] = index
                        enriched["locatorCandidate"] = candidate
                    successful_resolutions.append(enriched)
                if first_empty is None:
                    first_empty = dict(resolved or {})
            except Exception as exc:
                errors.append(f"{candidate}: {exc.__class__.__name__}: {exc}")
        if successful_resolutions:
            return merge_visual_locator_candidate_resolutions(
                successful_resolutions,
                locator_candidates=locator_candidates,
                scope_bounds=locator_scope_bounds,
                role=locator_role,
                preferred_bounds=preferred_bounds,
            )
        if first_ambiguous is not None:
            if len(locator_candidates) > 1:
                first_ambiguous["locatorChain"] = list(locator_candidates)
            return first_ambiguous
        if first_empty is not None:
            if len(locator_candidates) > 1:
                first_empty["locatorChain"] = list(locator_candidates)
            return first_empty
        if errors:
            raise DesktopDriverError(" ; ".join(errors))
        raise DesktopDriverError("visual locator 未返回任何匹配结果。")

    def _resolve_visual_locator_points(
        self,
        payload: Dict[str, Any],
        *,
        prefix: str = "",
    ) -> tuple[List[List[int]], Dict[str, Any]]:
        request = self._visual_locator_request_from_payload(payload, prefix=prefix)
        if not request:
            raise DesktopDriverError("visual locator 请求为空。")
        if not self.visual_locator_runtime.is_available():
            raise DesktopDriverError("在线视觉定位层当前不可用。")
        locator = str(request.get("locator") or "")
        window_handle = payload.get("window_handle") or payload.get("windowHandle")
        window_title = payload.get("window_title") or payload.get("windowTitle")
        search_image_path: str | None = None
        search_bounds: List[int] | None = None
        capture_image_path: str | None = None
        capture_bounds: List[int] | None = None
        temporary_capture_path: str | None = None
        temporary_scope_paths: List[str] = []
        observer_resolution: Dict[str, Any] | None = None
        if window_handle not in (None, "") or str(window_title or "").strip():
            try:
                foreground = self.driver.foreground_window() if hasattr(self.driver, "foreground_window") else None
                foreground_handle = (foreground or {}).get("handle") if isinstance(foreground, dict) else None
                expected_handle = None
                if window_handle not in (None, ""):
                    try:
                        expected_handle = int(window_handle)
                    except Exception:
                        expected_handle = None
                if expected_handle is None or foreground_handle != expected_handle:
                    self.driver.focus_window(
                        window_title=str(window_title or "").strip() or None,
                        window_handle=expected_handle,
                    )
            except Exception:
                pass
            try:
                temporary_capture = tempfile.NamedTemporaryFile(
                    prefix="v8chat-visual-locator-",
                    suffix=".png",
                    delete=False,
                    dir=str(ensure_v8_agent_os_tmp_path(scope="computer_use")),
                )
                temporary_capture_path = temporary_capture.name
                temporary_capture.close()
                capture = self.driver.capture_screenshot(
                    temporary_capture_path,
                    window_title=str(window_title or "").strip() or None,
                    window_handle=expected_handle,
                )
                search_image_path = str(capture.get("path") or "").strip() or temporary_capture_path
                bounds = capture.get("bounds")
                if isinstance(bounds, list) and len(bounds) == 4:
                    try:
                        search_bounds = [int(bounds[0]), int(bounds[1]), int(bounds[2]), int(bounds[3])]
                    except Exception:
                        search_bounds = None
                capture_image_path = search_image_path
                capture_bounds = list(search_bounds) if isinstance(search_bounds, list) else None
            except Exception:
                search_image_path = None
                search_bounds = None
                capture_image_path = None
                capture_bounds = None
        cache_key = self._visual_locator_cache_key(
            payload,
            locator=locator,
            prefix=prefix,
        )
        scope_resolution: Dict[str, Any] | None = None
        scope_locator = str(request.get("scope_locator") or "").strip()
        scope_seed_strategy = str(request.get("scope_seed_strategy") or "").strip().lower() or None
        locator_role = str(request.get("role_hint") or "").strip().lower() or infer_visual_locator_chain_role(split_locator_candidates(locator))
        observer_zone_bounds: List[int] | None = None
        if capture_image_path and scope_seed_strategy == "centered_dialog":
            observer_resolution, observer_temp_paths = observe_centered_dialog_scope(
                visual_locator_runtime=self.visual_locator_runtime,
                capture_image_path=capture_image_path,
                capture_bounds=capture_bounds,
            )
            observer_confident = str((observer_resolution or {}).get("dialogConfidenceLevel") or "").strip().lower() in {"medium", "high"}
            temporary_scope_paths.extend([item for item in list(observer_temp_paths or []) if item])
            observed_dialog_bounds = list((observer_resolution or {}).get("dialogBounds") or [])
            observer_zone_bounds = self._visual_observer_zone_bounds(
                observer_resolution,
                role=locator_role,
            )
            if observer_confident and len(observed_dialog_bounds) == 4:
                cropped_dialog_path, temp_dialog_path = crop_capture_image_to_bounds(
                    image_path=capture_image_path,
                    capture_bounds=capture_bounds,
                    target_bounds=observed_dialog_bounds,
                )
                if cropped_dialog_path:
                    search_image_path = cropped_dialog_path
                    search_bounds = list(observed_dialog_bounds)
                    if temp_dialog_path and temp_dialog_path not in temporary_scope_paths:
                        temporary_scope_paths.append(temp_dialog_path)
                if not scope_locator and isinstance(observer_zone_bounds, list) and len(observer_zone_bounds) == 4:
                    cropped_zone_path, temp_zone_path = crop_capture_image_to_bounds(
                        image_path=search_image_path,
                        capture_bounds=observed_dialog_bounds,
                        target_bounds=observer_zone_bounds,
                    )
                    if cropped_zone_path:
                        search_image_path = cropped_zone_path
                        search_bounds = list(observer_zone_bounds)
                        if temp_zone_path and temp_zone_path not in temporary_scope_paths:
                            temporary_scope_paths.append(temp_zone_path)
            elif isinstance((observer_resolution or {}).get("seedBounds"), list) and len((observer_resolution or {}).get("seedBounds") or []) == 4:
                centered_seed_bounds = list((observer_resolution or {}).get("seedBounds") or [])
                cropped_seed_path, temp_seed_path = crop_capture_image_to_bounds(
                    image_path=capture_image_path,
                    capture_bounds=capture_bounds,
                    target_bounds=centered_seed_bounds,
                )
                if cropped_seed_path:
                    search_image_path = cropped_seed_path
                    search_bounds = list(centered_seed_bounds)
                    if temp_seed_path and temp_seed_path not in temporary_scope_paths:
                        temporary_scope_paths.append(temp_seed_path)
        if scope_locator and search_image_path:
            try:
                scope_search_image_path = search_image_path
                scope_search_bounds = list(search_bounds) if isinstance(search_bounds, list) else None
                scope_resolution = self._locate_visual_locator_candidates(
                    locator_candidates=split_locator_candidates(scope_locator),
                    locator_scope_bounds=scope_search_bounds,
                    locator_role="generic",
                    preferred_bounds=None,
                    scope_first=bool(scope_search_bounds),
                    payload=payload,
                    timeout_ms=int(request.get("timeout_ms") or 2500),
                    confidence=request.get("confidence"),
                    multiple=False,
                    read_text=True,
                    search_image_path=scope_search_image_path,
                    search_bounds=scope_search_bounds,
                )
                scope_matches = list(scope_resolution.get("matches") or [])
                if scope_matches:
                    scoped_bounds = expand_scope_bounds(
                        match=dict(scope_matches[0] or {}),
                        capture_bounds=capture_bounds,
                        scope_padding=request.get("scope_padding"),
                    )
                    cropped_scope_path, temp_scope_path = crop_capture_image_to_bounds(
                        image_path=capture_image_path,
                        capture_bounds=capture_bounds,
                        target_bounds=scoped_bounds,
                    )
                    if cropped_scope_path and scoped_bounds:
                        search_image_path = cropped_scope_path
                        search_bounds = list(scoped_bounds)
                        if temp_scope_path:
                            temporary_scope_paths.append(temp_scope_path)
                    elif scoped_bounds:
                        search_image_path = capture_image_path
                        search_bounds = list(scoped_bounds)
                else:
                    raise DesktopDriverError(f"visual locator scope 未命中：{scope_locator}")
            except Exception:
                raise
        try:
            try:
                scope_first = bool(scope_seed_strategy == "centered_dialog" or scope_locator or (search_bounds and len(search_bounds) == 4))
                resolved = self._locate_visual_locator_candidates(
                    locator_candidates=split_locator_candidates(locator),
                    locator_scope_bounds=list(search_bounds) if isinstance(search_bounds, list) else None,
                    locator_role=locator_role,
                    preferred_bounds=list(observer_zone_bounds) if isinstance(observer_zone_bounds, list) and len(observer_zone_bounds) == 4 else None,
                    scope_first=scope_first,
                    payload=payload,
                    timeout_ms=int(request.get("timeout_ms") or 2500),
                    confidence=request.get("confidence"),
                    multiple=bool(request.get("multiple")),
                    read_text=bool(request.get("read_text")),
                    search_image_path=search_image_path,
                    search_bounds=search_bounds,
                )
                ranking = dict(resolved.get("semanticRanking") or {})
                if (
                    locator_role == "search_box"
                    and (
                        not list(resolved.get("matches") or [])
                        or not bool(ranking.get("selectedStrong"))
                    )
                ):
                    semantic_resolution = self._build_semantic_visual_resolution(
                        locator=locator,
                        locator_role=locator_role,
                        scope_bounds=list(search_bounds) if isinstance(search_bounds, list) else None,
                        capture_bounds=list(capture_bounds) if isinstance(capture_bounds, list) else None,
                    )
                    if isinstance(semantic_resolution, dict):
                        resolved = semantic_resolution
                if isinstance(scope_resolution, dict):
                    resolved = dict(resolved)
                    resolved["scopeLocator"] = dict(scope_resolution)
                    if isinstance(search_bounds, list) and len(search_bounds) == 4:
                        resolved["scopeBounds"] = list(search_bounds)
                if isinstance(observer_resolution, dict):
                    resolved = dict(resolved)
                    resolved["visualObserver"] = dict(observer_resolution)
                resolved = dict(resolved)
                resolved["visualObservation"] = summarize_visual_observation(
                    locator=locator,
                    role=locator_role,
                    observer_resolution=observer_resolution,
                    locator_resolution=resolved,
                )
                judge_suggestion = build_visual_judge_suggestion(
                    observation=resolved.get("visualObservation"),
                    locator_resolution=resolved,
                )
                if judge_suggestion is not None:
                    resolved["visualJudgeSuggestion"] = judge_suggestion
                    resolved = run_visual_judge(
                        resolution=resolved,
                        current_search_image_path=search_image_path,
                        capture_image_path=capture_image_path,
                        capture_bounds=capture_bounds,
                        invoke=self._invoke_visual_judge,
                        available=bool(self._computer_use_visual_judge_state().get("available")),
                    )
                    resolved["visualObservation"] = summarize_visual_observation(
                        locator=locator,
                        role=locator_role,
                        observer_resolution=observer_resolution,
                        locator_resolution=resolved,
                    )
                candidate_board = self.build_candidate_board(
                    goal=locator,
                    locator_resolution=resolved,
                    visual_observation=dict(resolved.get("visualObservation") or {}),
                )
                resolved["candidateBoard"] = candidate_board
                resolved["visualActorProposal"] = self.propose_visual_actor_action(
                    goal=locator,
                    candidate_board=candidate_board,
                    screenshot_path=search_image_path or capture_image_path,
                    display_bounds=_display_bounds_from_capture(capture_bounds),
                    previous_frame_summary=str((resolved.get("visualObservation") or {}).get("summary") or ""),
                )
                self._remember_visual_locator_resolution(cache_key=cache_key, resolved=resolved)
            except Exception:
                reused = self._recent_visual_locator_resolution(cache_key=cache_key)
                if reused is None:
                    raise
                resolved = reused
        finally:
            if temporary_capture_path:
                try:
                    Path(temporary_capture_path).unlink(missing_ok=True)
                except Exception:
                    pass
            for temp_scope_path in temporary_scope_paths:
                try:
                    Path(temp_scope_path).unlink(missing_ok=True)
                except Exception:
                    pass
        matches = list(resolved.get("matches") or [])
        absolute_points: List[List[int]] = []
        for match in matches:
            center = list(match.get("center") or [])
            if len(center) != 2:
                continue
            try:
                absolute_points.append([int(round(float(center[0]))), int(round(float(center[1])))])
            except Exception:
                continue
        if not absolute_points:
            raise DesktopDriverError("visual locator 未解析到可用落点。")
        return absolute_points, resolved

    def _expand_visual_locator_scope_bounds(
        self,
        *,
        match: Dict[str, Any],
        capture_bounds: List[int] | None,
        scope_padding: Any,
    ) -> List[int] | None:
        resolved = expand_scope_bounds(
            match=match,
            capture_bounds=capture_bounds,
            scope_padding=list(scope_padding) if isinstance(scope_padding, (list, tuple)) and len(scope_padding) == 4 else None,
        )
        if resolved is not None:
            return resolved
        return capture_bounds if isinstance(capture_bounds, list) and len(capture_bounds) == 4 else None

    def _visual_observer_zone_bounds(
        self,
        observer_resolution: Dict[str, Any] | None,
        *,
        role: str,
    ) -> List[int] | None:
        payload = dict(observer_resolution or {})
        if role == "action_button":
            for key in ("primaryActionButtonBounds", "primaryActionZoneBounds", "actionZoneBounds"):
                bounds = list(payload.get(key) or [])
                if len(bounds) == 4:
                    return [int(item) for item in bounds]
            return None
        if role == "dialog_title":
            bounds = list(payload.get("titleZoneBounds") or [])
            if len(bounds) == 4:
                return [int(item) for item in bounds]
        return None

    def _resolve_ocr_locator_from_observation_regions(
        self,
        *,
        locator: str,
        payload: Dict[str, Any],
        search_image_path: str,
        timeout_ms: int,
    ) -> Dict[str, Any] | None:
        query = _normalize_ocr_query(locator)
        if not query:
            return None
        try:
            observation = self.driver.observe_desktop(
                window_title=payload.get("window_title"),
                window_handle=payload.get("window_handle"),
                depth_limit=3,
                element_limit=120,
                use_cache=False,
            ).as_dict()
        except Exception:
            return None
        window_meta = dict((observation or {}).get("metadata") or {})
        window_bounds = list(window_meta.get("windowBounds") or [])
        if len(window_bounds) != 4:
            return None
        candidate_regions: List[Dict[str, Any]] = []
        normalized_query = str(query).strip().lower()
        for element in list((observation or {}).get("elements") or []):
            bounds = list(element.get("bounds") or [])
            if len(bounds) != 4:
                continue
            try:
                left, top, right, bottom = [int(v) for v in bounds]
            except Exception:
                continue
            if right <= left or bottom <= top:
                continue
            width = right - left
            height = bottom - top
            if width < 24 or height < 18:
                continue
            role = str(element.get("role") or "").strip().lower()
            name = str(element.get("name") or "").strip()
            priority = 3
            if normalized_query and normalized_query in name.lower():
                priority = 0
            elif role == "button":
                priority = 1
            elif role in {"text", "edit", "combobox"}:
                priority = 2
            else:
                continue
            candidate_regions.append(
                {
                    "bounds": [left, top, right, bottom],
                    "priority": priority,
                    "name": name,
                    "role": role,
                    "area": width * height,
                }
            )
        if not candidate_regions:
            return None
        candidate_regions.sort(key=lambda item: (int(item["priority"]), -int(item["area"])))
        search_image = Path(search_image_path)
        if not search_image.exists():
            return None
        for candidate in candidate_regions[:16]:
            local_left = candidate["bounds"][0] - int(window_bounds[0])
            local_top = candidate["bounds"][1] - int(window_bounds[1])
            local_right = candidate["bounds"][2] - int(window_bounds[0])
            local_bottom = candidate["bounds"][3] - int(window_bounds[1])
            temp_path: Path | None = None
            try:
                with Image.open(search_image) as screenshot:
                    cropped = screenshot.crop((local_left, local_top, local_right, local_bottom))
                    fd, temp_name = tempfile.mkstemp(
                        prefix="v8chat-ocr-region-",
                        suffix=".png",
                        dir=str(ensure_v8_agent_os_tmp_path(scope="computer_use")),
                    )
                    os.close(fd)
                    temp_path = Path(temp_name)
                    cropped.save(temp_path)
                resolved = self.visual_locator_runtime.locate(
                    locator=locator,
                    timeout_ms=timeout_ms,
                    confidence=None,
                    multiple=False,
                    read_text=True,
                    search_image_path=str(temp_path),
                    search_bounds=list(candidate["bounds"]),
                )
                if list(resolved.get("matches") or []):
                    metadata = dict(resolved)
                    metadata["searchMode"] = "observation_region_ocr"
                    metadata["semanticRegionHint"] = {
                        "role": candidate.get("role"),
                        "name": candidate.get("name"),
                        "bounds": list(candidate.get("bounds") or []),
                    }
                    return metadata
            except Exception:
                continue
            finally:
                if temp_path is not None:
                    try:
                        temp_path.unlink(missing_ok=True)
                    except Exception:
                        pass
        return None

    def _collect_post_action_visual_locator_check(
        self,
        *,
        action_type: str,
        action_payload: Dict[str, Any],
        verification: ComputerUseVerification | None = None,
        observation_bundle: Dict[str, Any] | None = None,
    ) -> Dict[str, Any] | None:
        request = self._visual_locator_request_from_payload(action_payload, prefix="post_action_")
        if not request:
            return None
        expected_texts = normalize_expected_texts(
            action_payload.get("post_action_expect_text")
            or action_payload.get("postActionExpectText")
            or action_payload.get("post_action_expect_texts")
            or action_payload.get("postActionExpectTexts")
        )
        if expected_texts and not bool(request.get("read_text")):
            request["read_text"] = True
        provider_id = str(self.visual_locator_runtime.provider_id or "rpa_desktop_visual_locator")
        if not self.visual_locator_runtime.is_available():
            return summarize_post_action_visual_check(
                provider_id=provider_id,
                locator=str(request.get("locator") or ""),
                resolved={},
                expected_texts=expected_texts,
                error="在线统一视觉定位层当前不可用。",
                action_type=action_type,
                action_payload=action_payload,
                verification_details=(verification.details if isinstance(verification, ComputerUseVerification) else None),
                observation_bundle=observation_bundle,
            )
        try:
            resolved = self.visual_locator_runtime.locate(
                locator=str(request.get("locator") or ""),
                timeout_ms=int(request.get("timeout_ms") or 2500),
                confidence=request.get("confidence"),
                multiple=bool(request.get("multiple")),
                read_text=bool(request.get("read_text")),
            )
            return summarize_post_action_visual_check(
                provider_id=provider_id,
                locator=str(request.get("locator") or ""),
                resolved=resolved,
                expected_texts=expected_texts,
                action_type=action_type,
                action_payload=action_payload,
                verification_details=(verification.details if isinstance(verification, ComputerUseVerification) else None),
                observation_bundle=observation_bundle,
            )
        except Exception as exc:
            return summarize_post_action_visual_check(
                provider_id=provider_id,
                locator=str(request.get("locator") or ""),
                resolved={},
                expected_texts=expected_texts,
                error=str(exc),
                action_type=action_type,
                action_payload=action_payload,
                verification_details=(verification.details if isinstance(verification, ComputerUseVerification) else None),
                observation_bundle=observation_bundle,
            )

    def _merge_post_action_visual_locator_verification(
        self,
        *,
        action_type: str,
        verification: ComputerUseVerification,
        post_action_visual_locator: Dict[str, Any] | None,
    ) -> ComputerUseVerification:
        if not isinstance(post_action_visual_locator, dict) or not post_action_visual_locator:
            return verification
        details = dict(verification.details or {})
        details["postActionVisualLocator"] = dict(post_action_visual_locator)
        expected_texts = list(post_action_visual_locator.get("expectedTexts") or [])
        status = str(post_action_visual_locator.get("status") or "").strip().lower()
        if status == "verified":
            return ComputerUseVerification(
                passed=True,
                status="verified",
                reason=(
                    "动作后统一视觉定位已确认预期文本。"
                    if expected_texts
                    else "动作后统一视觉定位已确认目标区域。"
                ),
                details=details,
                level="verified",
            )
        if expected_texts and status in {"text_mismatch", "not_found", "error"}:
            return ComputerUseVerification(
                passed=False,
                status="post_action_visual_confirmation_failed",
                reason=str(post_action_visual_locator.get("reason") or "动作后视觉复核未通过。"),
                details=details,
                level="review_required",
            )
        return ComputerUseVerification(
            passed=verification.passed,
            status=verification.status,
            reason=verification.reason,
            details=details,
            level=verification.level,
        )

    def _resolve_runtime_click_points(
        self,
        payload: Dict[str, Any],
    ) -> tuple[List[List[int]], Dict[str, Any] | None, List[List[float]], Dict[str, Any] | None]:
        point = payload.get("point")
        point_candidates = self._normalize_runtime_point_candidates(
            payload.get("point_candidates"),
            payload.get("pointCandidates"),
        )
        point_rect = payload.get("point_rect") if isinstance(payload.get("point_rect"), list) else None
        point_bias = payload.get("point_bias") if isinstance(payload.get("point_bias"), list) else None
        point_biases = payload.get("point_biases") if isinstance(payload.get("point_biases"), list) else None
        spatial_anchor = payload.get("spatial_anchor") or payload.get("spatialAnchor")
        observation = None
        visual_locator_resolution: Dict[str, Any] | None = None
        center_only = self._center_preferred_click_target(payload)
        if center_only:
            point_bias = None
            point_biases = None
        visual_locator_request = self._visual_locator_request_from_payload(payload)
        if visual_locator_request:
            absolute_points, visual_locator_resolution = self._resolve_visual_locator_points(payload)
            return absolute_points, observation, [], visual_locator_resolution
        if payload.get("window_title") or payload.get("window_handle"):
            try:
                observation = self.driver.observe_desktop(
                    window_title=payload.get("window_title"),
                    window_handle=payload.get("window_handle"),
                    depth_limit=2,
                    element_limit=24,
                    use_cache=False,
                ).as_dict()
            except Exception:
                observation = None
        normalized_points = build_relative_point_candidates(
            suggested_point=point if isinstance(point, list) else None,
            point_rect=point_rect if isinstance(point_rect, list) else None,
            point_bias=point_bias,
            point_biases=point_biases,
            center_only=center_only,
        )
        if point_candidates:
            normalized_points = self._normalize_runtime_point_candidates(point_candidates, normalized_points)
        if not normalized_points and isinstance(point, list):
            biased = offset_relative_point(point, point_bias)
            if isinstance(biased, list):
                normalized_points = [list(biased)]
        absolute_points: List[List[int]] = []
        seen: set[tuple[int, int]] = set()
        if normalized_points:
            for normalized_point in normalized_points:
                absolute_point = resolve_absolute_click_point(
                    suggested_point=normalized_point,
                    spatial_anchor=spatial_anchor if isinstance(spatial_anchor, dict) else None,
                    observation=observation,
                )
                if not absolute_point:
                    continue
                key = (int(absolute_point[0]), int(absolute_point[1]))
                if key in seen:
                    continue
                seen.add(key)
                absolute_points.append([int(absolute_point[0]), int(absolute_point[1])])
        else:
            absolute_point = resolve_absolute_click_point(
                suggested_point=None,
                spatial_anchor=spatial_anchor if isinstance(spatial_anchor, dict) else None,
                observation=observation,
            )
            if absolute_point:
                absolute_points.append([int(absolute_point[0]), int(absolute_point[1])])
        if not absolute_points:
            raise DesktopDriverError("无法根据坐标锚点解析实际点击坐标。")
        return absolute_points, observation, normalized_points, visual_locator_resolution

    def _resolve_runtime_click_point(self, payload: Dict[str, Any]) -> tuple[List[int], Dict[str, Any] | None]:
        absolute_points, observation, _, _ = self._resolve_runtime_click_points(payload)
        return list(absolute_points[0]), observation

    def _resolve_pure_visual_click_point(
        self,
        payload: Dict[str, Any],
        *,
        default_strategy: str,
    ) -> tuple[List[int], Dict[str, Any], str]:
        absolute_points, _, _, visual_locator_resolution = self._resolve_runtime_click_points(payload)
        if not isinstance(visual_locator_resolution, dict):
            raise DesktopDriverError("显式 visual locator 未返回结构化解析结果。")
        matches = list(visual_locator_resolution.get("matches") or [])
        if not matches:
            raise DesktopDriverError("显式 visual locator 未返回匹配结果。")
        primary = dict(matches[0] or {})
        fallback_center = list(absolute_points[0]) if absolute_points else list(primary.get("center") or [])
        if len(fallback_center) != 2:
            raise DesktopDriverError("显式 visual locator 未返回可用中心点。")
        strategy = (
            str(
                payload.get("focus_strategy")
                or payload.get("focusStrategy")
                or payload.get("visual_focus_strategy")
                or payload.get("visualFocusStrategy")
                or default_strategy
            ).strip().lower()
            or default_strategy
        )
        click_point = resolve_pure_visual_click_point(
            primary=primary,
            fallback_center=[int(fallback_center[0]), int(fallback_center[1])],
            strategy=strategy,
        )
        return [int(click_point[0]), int(click_point[1])], visual_locator_resolution, strategy

    def _build_pure_visual_metadata(
        self,
        *,
        metadata: Dict[str, Any] | None = None,
        visual_locator_resolution: Dict[str, Any],
        coordinate_source: str,
        prefer_sendinput_click: bool | None = None,
        focus_strategy: str | None = None,
        clipboard_payload: Dict[str, Any] | None = None,
        focus_hotkey_metadata: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        normalized = dict(metadata or {})
        normalized["coordinateFallback"] = True
        normalized["coordinateSource"] = str(coordinate_source or "visual_locator_pure_center")
        normalized["pureVisualLocator"] = True
        if prefer_sendinput_click is not None:
            normalized["sendInputPreferred"] = bool(prefer_sendinput_click)
        if str(focus_strategy or "").strip():
            normalized["focusStrategy"] = str(focus_strategy).strip().lower()
        if isinstance(clipboard_payload, dict):
            normalized["clipboardPayload"] = dict(clipboard_payload)
        if isinstance(focus_hotkey_metadata, dict):
            normalized["focusHotkey"] = dict(focus_hotkey_metadata)
        normalized["visualLocator"] = dict(visual_locator_resolution)
        return normalized

    def _has_structured_click_selector(self, payload: Dict[str, Any]) -> bool:
        for key in ("element_id", "name", "name_contains", "automation_id", "control_type", "class_name"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return True
            if value not in (None, "", []):
                return True
        return False

    def _has_explicit_visual_locator(self, payload: Dict[str, Any], *, prefix: str = "") -> bool:
        return self._visual_locator_request_from_payload(payload, prefix=prefix) is not None

    def _center_preferred_click_target(self, payload: Dict[str, Any]) -> bool:
        if bool(payload.get("allow_edge_click")):
            return False
        for key in (
            "point",
            "point_bias",
            "point_biases",
            "point_candidates",
            "pointCandidates",
        ):
            value = payload.get(key)
            if value not in (None, "", []):
                return False
        control_type = str(payload.get("control_type") or "").strip().lower()
        target_name = str(
            payload.get("target")
            or payload.get("selector_key")
            or payload.get("profile_action")
            or payload.get("action_name")
            or ""
        ).strip().lower()
        if control_type == "button":
            return True
        return target_name.endswith("_button") or target_name in {
            "button",
            "primary_button",
            "confirm_button",
            "pay_button",
            "send_button",
            "new_folder",
            "refresh",
            "back",
        }

    def _click_target_from_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        browser_decision = self._browser_lane_decision(
            action_type="click",
            action_payload=payload,
        )
        if browser_decision.available:
            try:
                return self.browser_automation.click_target(
                    payload=payload,
                    decision=browser_decision,
                )
            except Exception as exc:
                _raise_if_workbench_browser_control_error(exc)
                pass
        point = payload.get("point")
        point_candidates = self._normalize_runtime_point_candidates(
            payload.get("point_candidates"),
            payload.get("pointCandidates"),
        )
        point_rect = payload.get("point_rect")
        spatial_anchor = payload.get("spatial_anchor") or payload.get("spatialAnchor")
        prefer_sendinput_click = bool(payload.get("prefer_sendinput_click"))
        has_visual_locator = self._has_explicit_visual_locator(payload)
        has_coordinate_fallback = (
            has_visual_locator
            or bool(point_candidates)
            or isinstance(point, list)
            or isinstance(point_rect, list)
            or isinstance(spatial_anchor, dict)
        )
        has_structured_selector = self._has_structured_click_selector(payload)
        target_text = str(payload.get("target_text") or "").strip() or None
        selector_error: str | None = None
        if has_visual_locator:
            click_point, visual_locator_resolution, focus_strategy = self._resolve_pure_visual_click_point(
                payload,
                default_strategy="center",
            )
            clicked = self.driver.click_point(
                point=click_point,
                window_title=payload.get("window_title"),
                window_handle=payload.get("window_handle"),
                double=bool(payload.get("double", False)),
                prefer_sendinput_click=prefer_sendinput_click,
            )
            clicked["metadata"] = self._build_pure_visual_metadata(
                metadata=dict(clicked.get("metadata") or {}),
                visual_locator_resolution=visual_locator_resolution,
                coordinate_source="visual_locator_pure_center",
                prefer_sendinput_click=prefer_sendinput_click,
                focus_strategy=focus_strategy,
            )
            clicked["windowTitle"] = clicked.get("title") or payload.get("window_title")
            clicked["windowHandle"] = clicked.get("handle") or payload.get("window_handle")
            clicked["role"] = clicked.get("role") or "CoordinatePoint"
            return clicked
        if has_structured_selector:
            try:
                clicked = self.driver.click_element(
                    element_id=payload.get("element_id"),
                    window_title=payload.get("window_title"),
                    window_handle=payload.get("window_handle"),
                    name=payload.get("name"),
                    name_contains=payload.get("name_contains"),
                    target_text=target_text,
                    automation_id=payload.get("automation_id"),
                    control_type=payload.get("control_type"),
                    class_name=payload.get("class_name"),
                    double=bool(payload.get("double", False)),
                    prefer_sendinput_click=prefer_sendinput_click,
                ).as_dict()
                metadata = dict(clicked.get("metadata") or {})
                metadata["sendInputPreferred"] = prefer_sendinput_click
                if has_coordinate_fallback:
                    metadata["coordinateFallbackAvailable"] = True
                clicked["metadata"] = metadata
                return clicked
            except Exception as exc:
                selector_error = str(exc)
                if not has_coordinate_fallback:
                    raise
        if has_coordinate_fallback:
            absolute_points, observation, normalized_points, visual_locator_resolution = self._resolve_runtime_click_points(payload)
            absolute_point = absolute_points[0]
            clicked = self.driver.click_point(
                point=absolute_point,
                window_title=payload.get("window_title"),
                window_handle=payload.get("window_handle"),
                double=bool(payload.get("double", False)),
                prefer_sendinput_click=prefer_sendinput_click,
            )
            metadata = dict(clicked.get("metadata") or {})
            metadata["coordinateFallback"] = True
            metadata["coordinateSource"] = str(payload.get("coordinate_source") or "coordinate_anchor")
            metadata["sendInputPreferred"] = prefer_sendinput_click
            if isinstance(point, list):
                metadata["suggestedPoint"] = list(point)
            if isinstance(point_rect, list):
                metadata["pointRect"] = list(point_rect)
            if isinstance(payload.get("point_bias"), list):
                metadata["pointBias"] = list(payload.get("point_bias"))
            if isinstance(payload.get("point_biases"), list):
                metadata["pointBiases"] = list(payload.get("point_biases"))
            if normalized_points:
                metadata["pointCandidates"] = [list(item) for item in normalized_points]
            if len(absolute_points) > 1:
                metadata["resolvedPointCandidates"] = [list(item) for item in absolute_points]
            if isinstance(spatial_anchor, dict):
                metadata["spatialAnchor"] = dict(spatial_anchor)
            if isinstance(observation, dict):
                metadata["observationWindow"] = {
                    "windowTitle": observation.get("windowTitle"),
                    "windowHandle": (observation.get("metadata") or {}).get("windowHandle"),
                }
            if selector_error:
                metadata["selectorFallbackError"] = selector_error
            if isinstance(visual_locator_resolution, dict):
                metadata["visualLocator"] = dict(visual_locator_resolution)
            clicked["metadata"] = metadata
            clicked["windowTitle"] = clicked.get("title") or payload.get("window_title")
            clicked["windowHandle"] = clicked.get("handle") or payload.get("window_handle")
            clicked["role"] = clicked.get("role") or "CoordinatePoint"
            return clicked
        return self.driver.click_element(
            element_id=payload.get("element_id"),
            window_title=payload.get("window_title"),
            window_handle=payload.get("window_handle"),
            name=payload.get("name"),
            name_contains=payload.get("name_contains"),
            target_text=target_text,
            automation_id=payload.get("automation_id"),
            control_type=payload.get("control_type"),
            class_name=payload.get("class_name"),
            double=bool(payload.get("double", False)),
            prefer_sendinput_click=prefer_sendinput_click,
        ).as_dict()

    def _type_target_from_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        clipboard_payload = normalize_clipboard_payload(
            payload=payload,
            text=payload.get("text"),
            file_path=payload.get("file_path"),
            file_paths=payload.get("file_paths"),
            attachment_paths=payload.get("attachment_paths"),
        )
        focus_hotkey_sequence = str(
            payload.get("focus_hotkey_sequence") or payload.get("focusHotkeySequence") or ""
        ).strip()
        window_typing_focus_mode = str(
            payload.get("window_typing_focus_mode") or payload.get("windowTypingFocusMode") or ""
        ).strip().lower()
        file_paste_strategy = str(
            payload.get("file_paste_strategy") or payload.get("filePasteStrategy") or ""
        ).strip().lower()
        prefer_sendinput_text = bool(
            payload.get("prefer_sendinput_text")
            if payload.get("prefer_sendinput_text") is not None
            else payload.get("preferSendInputText")
        )
        focus_hotkey_metadata: Dict[str, Any] | None = None
        if focus_hotkey_sequence:
            try:
                focused = self.driver.hotkey(
                    focus_hotkey_sequence,
                    window_title=payload.get("window_title"),
                    window_handle=payload.get("window_handle"),
                )
                focus_hotkey_metadata = {
                    "sequence": focus_hotkey_sequence,
                    "status": "applied",
                    "target": focused,
                }
            except Exception as exc:
                focus_hotkey_metadata = {
                    "sequence": focus_hotkey_sequence,
                    "status": "failed",
                    "error": str(exc),
                }
        browser_decision = self._browser_lane_decision(
            action_type="type_text",
            action_payload=payload,
        )
        preflight = None
        text_value = str(clipboard_payload.get("text") or "")
        target_input_kind = classify_target_input_kind(
            action_payload=payload,
            text=text_value,
            browser_lane_active=bool(browser_decision.available),
            browser_family=browser_decision.family,
        )
        if browser_decision.available:
            try:
                browser_payload = dict(payload)
                if clipboard_payload.get("file_paths"):
                    browser_payload["file_paths"] = list(clipboard_payload.get("file_paths") or [])
                if clipboard_payload.get("text") not in (None, ""):
                    browser_payload["text"] = text_value
                if clipboard_payload.get("file_paths") and (
                    browser_payload.get("browser_selector")
                    or browser_payload.get("browserSelector")
                    or browser_payload.get("dom_selector")
                    or browser_payload.get("domSelector")
                    or browser_payload.get("css_selector")
                    or browser_payload.get("cssSelector")
                ):
                    return self._finalize_type_result(
                        typed=self.browser_automation.set_files(
                            payload=browser_payload,
                            decision=browser_decision,
                        ),
                        clipboard_payload=clipboard_payload,
                        focus_hotkey_metadata=focus_hotkey_metadata,
                    )
                return self._finalize_type_result(
                    typed=self.browser_automation.type_text(
                        payload=browser_payload,
                        decision=browser_decision,
                        target_input_kind=target_input_kind,
                    ),
                    clipboard_payload=clipboard_payload,
                    focus_hotkey_metadata=focus_hotkey_metadata,
                )
            except Exception as exc:
                _raise_if_workbench_browser_control_error(exc)
                pass
        preflight = self._prepare_input_preflight(
            action_payload=payload,
            browser_decision=browser_decision,
        )
        point = payload.get("point")
        point_candidates = self._normalize_runtime_point_candidates(
            payload.get("point_candidates"),
            payload.get("pointCandidates"),
        )
        point_rect = payload.get("point_rect")
        spatial_anchor = payload.get("spatial_anchor") or payload.get("spatialAnchor")
        prefer_sendinput_click = bool(payload.get("prefer_sendinput_click"))
        has_visual_locator = self._has_explicit_visual_locator(payload)
        if bool(payload.get("window_typing")):
            if has_visual_locator:
                click_point, visual_locator_resolution, focus_strategy = self._resolve_pure_visual_click_point(
                    payload,
                    default_strategy="text_input",
                )
                typed = self.driver.type_text_in_window(
                    text=str(clipboard_payload.get("text") or ""),
                    file_paths=list(clipboard_payload.get("file_paths") or []),
                    window_title=payload.get("window_title"),
                    window_handle=payload.get("window_handle"),
                    point=click_point,
                    clear_first=bool(payload.get("clear_first", False)),
                    press_enter=bool(payload.get("press_enter", False)),
                    prefer_sendinput_click=prefer_sendinput_click,
                    prefer_sendinput_text=prefer_sendinput_text,
                    focus_probe_mode=window_typing_focus_mode or None,
                    file_paste_strategy=file_paste_strategy or None,
                )
                typed["metadata"] = self._build_pure_visual_metadata(
                    metadata=dict(typed.get("metadata") or {}),
                    visual_locator_resolution=visual_locator_resolution,
                    coordinate_source="visual_locator_pure_text_input",
                    prefer_sendinput_click=prefer_sendinput_click,
                    focus_strategy=focus_strategy,
                    clipboard_payload=clipboard_payload,
                    focus_hotkey_metadata=focus_hotkey_metadata,
                )
                return self._finalize_type_result(
                    typed=typed,
                    clipboard_payload=clipboard_payload,
                    focus_hotkey_metadata=focus_hotkey_metadata,
                    preflight=preflight,
                )
            resolved_point = None
            resolved_points = None
            observation = None
            normalized_points: List[List[float]] = []
            visual_locator_resolution = None
            if point_candidates or isinstance(point, list) or isinstance(point_rect, list) or isinstance(spatial_anchor, dict):
                resolved_points, observation, normalized_points, visual_locator_resolution = self._resolve_runtime_click_points(payload)
                resolved_point = list(resolved_points[0]) if resolved_points else None
            typed = self.driver.type_text_in_window(
                text=str(clipboard_payload.get("text") or ""),
                file_paths=list(clipboard_payload.get("file_paths") or []),
                window_title=payload.get("window_title"),
                window_handle=payload.get("window_handle"),
                point=resolved_point,
                point_candidates=resolved_points,
                clear_first=bool(payload.get("clear_first", False)),
                press_enter=bool(payload.get("press_enter", False)),
                prefer_sendinput_click=prefer_sendinput_click,
                prefer_sendinput_text=prefer_sendinput_text,
                focus_probe_mode=window_typing_focus_mode or None,
                file_paste_strategy=file_paste_strategy or None,
            )
            metadata = dict(typed.get("metadata") or {})
            metadata["sendInputPreferred"] = prefer_sendinput_click
            if isinstance(point, list):
                metadata["suggestedPoint"] = list(point)
            if isinstance(point_rect, list):
                metadata["pointRect"] = list(point_rect)
            if isinstance(payload.get("point_bias"), list):
                metadata["pointBias"] = list(payload.get("point_bias"))
            if isinstance(payload.get("point_biases"), list):
                metadata["pointBiases"] = list(payload.get("point_biases"))
            if normalized_points:
                metadata["pointCandidates"] = [list(item) for item in normalized_points]
            if isinstance(resolved_points, list) and len(resolved_points) > 1:
                metadata["resolvedPointCandidates"] = [list(item) for item in resolved_points]
            if isinstance(spatial_anchor, dict):
                metadata["spatialAnchor"] = dict(spatial_anchor)
            if isinstance(observation, dict):
                metadata["observationWindow"] = {
                    "windowTitle": observation.get("windowTitle"),
                    "windowHandle": (observation.get("metadata") or {}).get("windowHandle"),
                }
            metadata["clipboardPayload"] = dict(clipboard_payload)
            if focus_hotkey_metadata is not None:
                metadata["focusHotkey"] = dict(focus_hotkey_metadata)
            if isinstance(visual_locator_resolution, dict):
                metadata["visualLocator"] = dict(visual_locator_resolution)
                metadata["coordinateSource"] = "visual_locator"
            typed["metadata"] = metadata
            return self._finalize_type_result(
                typed=typed,
                clipboard_payload=clipboard_payload,
                focus_hotkey_metadata=focus_hotkey_metadata,
                preflight=preflight,
            )
        if has_visual_locator:
            click_point, visual_locator_resolution, focus_strategy = self._resolve_pure_visual_click_point(
                payload,
                default_strategy="text_input",
            )
            typed = self.driver.type_text_in_window(
                text=str(clipboard_payload.get("text") or ""),
                file_paths=list(clipboard_payload.get("file_paths") or []),
                window_title=payload.get("window_title"),
                window_handle=payload.get("window_handle"),
                point=click_point,
                clear_first=bool(payload.get("clear_first", False)),
                press_enter=bool(payload.get("press_enter", False)),
                prefer_sendinput_click=prefer_sendinput_click,
                prefer_sendinput_text=prefer_sendinput_text,
                focus_probe_mode=window_typing_focus_mode or None,
                file_paste_strategy=file_paste_strategy or None,
            )
            typed["metadata"] = self._build_pure_visual_metadata(
                metadata=dict(typed.get("metadata") or {}),
                visual_locator_resolution=visual_locator_resolution,
                coordinate_source="visual_locator_pure_text_input",
                prefer_sendinput_click=prefer_sendinput_click,
                focus_strategy=focus_strategy,
                clipboard_payload=clipboard_payload,
                focus_hotkey_metadata=focus_hotkey_metadata,
            )
            return self._finalize_type_result(
                typed=typed,
                clipboard_payload=clipboard_payload,
                focus_hotkey_metadata=focus_hotkey_metadata,
                preflight=preflight,
            )
        if point_candidates or isinstance(point, list) or isinstance(point_rect, list) or isinstance(spatial_anchor, dict):
            absolute_points, observation, normalized_points, visual_locator_resolution = self._resolve_runtime_click_points(payload)
            absolute_point = absolute_points[0]
            typed = self.driver.type_text_in_window(
                text=str(clipboard_payload.get("text") or ""),
                file_paths=list(clipboard_payload.get("file_paths") or []),
                window_title=payload.get("window_title"),
                window_handle=payload.get("window_handle"),
                point=absolute_point,
                point_candidates=absolute_points,
                clear_first=bool(payload.get("clear_first", False)),
                press_enter=bool(payload.get("press_enter", False)),
                prefer_sendinput_click=prefer_sendinput_click,
                prefer_sendinput_text=prefer_sendinput_text,
                focus_probe_mode=window_typing_focus_mode or None,
                file_paste_strategy=file_paste_strategy or None,
            )
            metadata = dict(typed.get("metadata") or {})
            metadata["coordinateSource"] = "visual_locator" if isinstance(visual_locator_resolution, dict) else str(payload.get("coordinate_source") or "coordinate_anchor")
            metadata["sendInputPreferred"] = prefer_sendinput_click
            if isinstance(point, list):
                metadata["suggestedPoint"] = list(point)
            if isinstance(point_rect, list):
                metadata["pointRect"] = list(point_rect)
            if isinstance(payload.get("point_bias"), list):
                metadata["pointBias"] = list(payload.get("point_bias"))
            if isinstance(payload.get("point_biases"), list):
                metadata["pointBiases"] = list(payload.get("point_biases"))
            if normalized_points:
                metadata["pointCandidates"] = [list(item) for item in normalized_points]
            if len(absolute_points) > 1:
                metadata["resolvedPointCandidates"] = [list(item) for item in absolute_points]
            if isinstance(spatial_anchor, dict):
                metadata["spatialAnchor"] = dict(spatial_anchor)
            if isinstance(observation, dict):
                metadata["observationWindow"] = {
                    "windowTitle": observation.get("windowTitle"),
                    "windowHandle": (observation.get("metadata") or {}).get("windowHandle"),
                }
            metadata["clipboardPayload"] = dict(clipboard_payload)
            if focus_hotkey_metadata is not None:
                metadata["focusHotkey"] = dict(focus_hotkey_metadata)
            if isinstance(visual_locator_resolution, dict):
                metadata["visualLocator"] = dict(visual_locator_resolution)
            typed["metadata"] = metadata
            return self._finalize_type_result(
                typed=typed,
                clipboard_payload=clipboard_payload,
                focus_hotkey_metadata=focus_hotkey_metadata,
                preflight=preflight,
            )
        typed = self.driver.type_text(
            text=str(clipboard_payload.get("text") or ""),
            file_paths=list(clipboard_payload.get("file_paths") or []),
            element_id=payload.get("element_id"),
            window_title=payload.get("window_title"),
            window_handle=payload.get("window_handle"),
            name=payload.get("name"),
            automation_id=payload.get("automation_id"),
            control_type=payload.get("control_type"),
            class_name=payload.get("class_name"),
            clear_first=bool(payload.get("clear_first", False)),
            press_enter=bool(payload.get("press_enter", False)),
        ).as_dict()
        return self._finalize_type_result(
            typed=typed,
            clipboard_payload=clipboard_payload,
            focus_hotkey_metadata=focus_hotkey_metadata,
            preflight=preflight,
        )

    def _finalize_type_result(
        self,
        *,
        typed: Dict[str, Any],
        clipboard_payload: Dict[str, Any],
        focus_hotkey_metadata: Dict[str, Any] | None,
        preflight: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        typed_metadata = dict(typed.get("metadata") or {})
        typed_metadata.setdefault("clipboardPayload", dict(clipboard_payload))
        if focus_hotkey_metadata is not None:
            typed_metadata.setdefault("focusHotkey", dict(focus_hotkey_metadata))
        typed["metadata"] = typed_metadata
        restored_preflight = self._restore_input_preflight(preflight)
        return self._attach_input_preflight_metadata(result=typed, preflight=restored_preflight)

    def _right_click_target_from_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if self._has_explicit_visual_locator(payload):
            click_point, visual_locator_resolution, focus_strategy = self._resolve_pure_visual_click_point(
                payload,
                default_strategy="center",
            )
            result = self.driver.right_click_point(
                point=list(click_point),
                window_title=payload.get("window_title"),
                window_handle=payload.get("window_handle"),
            )
            result["metadata"] = self._build_pure_visual_metadata(
                metadata=dict(result.get("metadata") or {}),
                visual_locator_resolution=visual_locator_resolution,
                coordinate_source="visual_locator_pure_center",
                focus_strategy=focus_strategy,
            )
            return result
        if self._has_structured_click_selector(payload):
            return self.driver.right_click_element(
                element_id=payload.get("element_id"),
                window_title=payload.get("window_title"),
                window_handle=payload.get("window_handle"),
                name=payload.get("name"),
                name_contains=payload.get("name_contains"),
                target_text=payload.get("target_text"),
                automation_id=payload.get("automation_id"),
                control_type=payload.get("control_type"),
                class_name=payload.get("class_name"),
            ).as_dict()
        absolute_points, _, _, visual_locator_resolution = self._resolve_runtime_click_points(payload)
        result = self.driver.right_click_point(
            point=list(absolute_points[0]),
            window_title=payload.get("window_title"),
            window_handle=payload.get("window_handle"),
        )
        metadata = dict(result.get("metadata") or {})
        if isinstance(visual_locator_resolution, dict):
            metadata["visualLocator"] = dict(visual_locator_resolution)
        result["metadata"] = metadata
        return result

    def _hover_target_from_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if self._has_explicit_visual_locator(payload):
            hover_point, visual_locator_resolution, focus_strategy = self._resolve_pure_visual_click_point(
                payload,
                default_strategy="center",
            )
            result = self.driver.hover_point(
                point=list(hover_point),
                window_title=payload.get("window_title"),
                window_handle=payload.get("window_handle"),
            )
            result["metadata"] = self._build_pure_visual_metadata(
                metadata=dict(result.get("metadata") or {}),
                visual_locator_resolution=visual_locator_resolution,
                coordinate_source="visual_locator_pure_center",
                focus_strategy=focus_strategy,
            )
            return result
        absolute_points, _, _, visual_locator_resolution = self._resolve_runtime_click_points(payload)
        result = self.driver.hover_point(
            point=list(absolute_points[0]),
            window_title=payload.get("window_title"),
            window_handle=payload.get("window_handle"),
        )
        metadata = dict(result.get("metadata") or {})
        if isinstance(visual_locator_resolution, dict):
            metadata["visualLocator"] = dict(visual_locator_resolution)
        result["metadata"] = metadata
        return result

    def _drag_target_from_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        start_point = payload.get("start_point") or payload.get("point") or payload.get("from_point")
        end_point = payload.get("end_point") or payload.get("to_point")
        start_visual_locator_resolution = None
        end_visual_locator_resolution = None
        if not isinstance(start_point, (list, tuple)) or len(start_point) != 2:
            start_payload = {
                **payload,
                "visual_locator": payload.get("start_visual_locator") or payload.get("startVisualLocator"),
                "visual_locator_confidence": payload.get("start_visual_locator_confidence") or payload.get("startVisualLocatorConfidence"),
                "visual_locator_timeout_ms": payload.get("start_visual_locator_timeout_ms") or payload.get("startVisualLocatorTimeoutMs"),
            }
            start_point, start_visual_locator_resolution, _ = self._resolve_pure_visual_click_point(
                start_payload,
                default_strategy="center",
            )
        if not isinstance(end_point, (list, tuple)) or len(end_point) != 2:
            end_payload = {
                **payload,
                "visual_locator": payload.get("end_visual_locator") or payload.get("endVisualLocator"),
                "visual_locator_confidence": payload.get("end_visual_locator_confidence") or payload.get("endVisualLocatorConfidence"),
                "visual_locator_timeout_ms": payload.get("end_visual_locator_timeout_ms") or payload.get("endVisualLocatorTimeoutMs"),
            }
            end_point, end_visual_locator_resolution, _ = self._resolve_pure_visual_click_point(
                end_payload,
                default_strategy="center",
            )
        if not isinstance(start_point, (list, tuple)) or len(start_point) != 2:
            raise DesktopDriverError("drag 动作缺少 start_point。")
        if not isinstance(end_point, (list, tuple)) or len(end_point) != 2:
            raise DesktopDriverError("drag 动作缺少 end_point。")
        result = self.driver.drag_between_points(
            start_point=[int(start_point[0]), int(start_point[1])],
            end_point=[int(end_point[0]), int(end_point[1])],
            window_title=payload.get("window_title"),
            window_handle=payload.get("window_handle"),
            steps=int(payload.get("drag_steps") or 12),
        )
        metadata = dict(result.get("metadata") or {})
        if isinstance(start_visual_locator_resolution, dict):
            metadata["startVisualLocator"] = dict(start_visual_locator_resolution)
        if isinstance(end_visual_locator_resolution, dict):
            metadata["endVisualLocator"] = dict(end_visual_locator_resolution)
        result["metadata"] = metadata
        return result

    def _page_scroll_target_from_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.driver.page_scroll(
            direction=str(payload.get("direction") or "down"),
            count=int(payload.get("count") or 1),
            window_title=payload.get("window_title"),
            window_handle=payload.get("window_handle"),
        )

    def _scroll_target_from_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        browser_decision = self._browser_lane_decision(
            action_type="scroll",
            action_payload=payload,
        )
        if browser_decision.available:
            try:
                return self.browser_automation.scroll_view(
                    payload=payload,
                    decision=browser_decision,
                )
            except Exception as exc:
                _raise_if_workbench_browser_control_error(exc)
                pass
        return self.driver.scroll(
            amount=int(payload["amount"]),
            element_id=payload.get("element_id"),
            window_title=payload.get("window_title"),
            window_handle=payload.get("window_handle"),
        )

    def _verify_action_result(
        self,
        *,
        action_type: str,
        action_payload: Dict[str, Any],
        result: ComputerUseActionResult,
        before_observation: Dict[str, Any] | None = None,
        after_observation: Dict[str, Any] | None = None,
    ) -> ComputerUseVerification:
        target_metadata = dict(result.target.get("metadata") or {}) if isinstance(result.target, dict) else {}
        if str(target_metadata.get("route") or "").strip().lower() == "browser_automation":
            browser_result = dict(target_metadata.get("browserResult") or {})
            return ComputerUseVerification(
                passed=True,
                status=f"browser_{action_type}_executed",
                reason="已通过浏览器自动化专项通道执行动作。",
                details={
                    "browserLaneProvider": target_metadata.get("browserLaneProvider"),
                    "browserTargetFamily": target_metadata.get("browserTargetFamily"),
                    "browserTargetId": target_metadata.get("browserTargetId"),
                    "browserResult": browser_result,
                    "beforeTreeHash": (before_observation or {}).get("treeHash"),
                    "afterTreeHash": (after_observation or {}).get("treeHash"),
                    "beforeScreenHash": (before_observation or {}).get("screenHash"),
                    "afterScreenHash": (after_observation or {}).get("screenHash"),
                    "observationBundle": dict(result.metadata.get("observationBundle") or {}),
                },
                level="executed_only",
            )
        text_input_status = str(((target_metadata.get("textInputCapability") or {}).get("status")) or "").strip().lower()
        payload_file_paths = list(action_payload.get("file_paths") or action_payload.get("attachment_paths") or [])
        if not payload_file_paths and action_payload.get("file_path"):
            payload_file_paths = [str(action_payload.get("file_path"))]
        if action_type == "type_text" and payload_file_paths and (
            bool(target_metadata.get("coordinateFallback")) or text_input_status == "coordinate_window_file_receiver"
        ):
            details = {
                "clickedPoint": result.target.get("clickedPoint"),
                "coordinateSource": target_metadata.get("coordinateSource"),
                "spatialAnchor": target_metadata.get("spatialAnchor"),
                "visualLocator": dict(target_metadata.get("visualLocator") or {}),
                "beforeTreeHash": (before_observation or {}).get("treeHash"),
                "afterTreeHash": (after_observation or {}).get("treeHash"),
                "beforeScreenHash": (before_observation or {}).get("screenHash"),
                "afterScreenHash": (after_observation or {}).get("screenHash"),
                "filePaths": list(payload_file_paths),
                "inputStrategy": (target_metadata.get("inputStrategy") if isinstance(target_metadata, dict) else None),
            }
            return ComputerUseVerification(
                passed=True,
                status="coordinate_file_paste_executed",
                reason="已在目标窗口内容区执行文件粘贴，建议结合文件系统或界面结果继续确认。",
                details=details,
                level="executed_only",
            )
        if action_type == "type_text" and (
            bool(target_metadata.get("coordinateFallback")) or text_input_status == "coordinate_window_target"
        ):
            details = {
                "clickedPoint": result.target.get("clickedPoint"),
                "coordinateSource": target_metadata.get("coordinateSource"),
                "spatialAnchor": target_metadata.get("spatialAnchor"),
                "visualLocator": dict(target_metadata.get("visualLocator") or {}),
                "beforeTreeHash": (before_observation or {}).get("treeHash"),
                "afterTreeHash": (after_observation or {}).get("treeHash"),
                "beforeScreenHash": (before_observation or {}).get("screenHash"),
                "afterScreenHash": (after_observation or {}).get("screenHash"),
            }
            return ComputerUseVerification(
                passed=True,
                status="coordinate_text_executed",
                reason="已通过窗口聚焦和坐标点击执行文本输入，建议结合业务结果继续观察。",
                details=details,
                level="executed_only",
            )
        if action_type in {"click", "double_click"} and bool(target_metadata.get("coordinateFallback")):
            details = {
                "clickedPoint": result.target.get("clickedPoint"),
                "coordinateSource": target_metadata.get("coordinateSource"),
                "spatialAnchor": target_metadata.get("spatialAnchor"),
                "visualLocator": dict(target_metadata.get("visualLocator") or {}),
                "beforeTreeHash": (before_observation or {}).get("treeHash"),
                "afterTreeHash": (after_observation or {}).get("treeHash"),
                "beforeScreenHash": (before_observation or {}).get("screenHash"),
                "afterScreenHash": (after_observation or {}).get("screenHash"),
            }
            return ComputerUseVerification(
                passed=True,
                status="coordinate_click_executed",
                reason="已执行坐标回退点击，建议结合视觉确认或业务结果确认。",
                details=details,
                level="executed_only",
            )
        if action_type in {"open_app", "focus_window"}:
            target = self._verification_target(action_payload, result)
            expected_title = str(
                target.get("windowTitle")
                or target.get("window_title")
                or action_payload.get("window_title")
                or ""
            ).strip()
            expected_class = str(
                target.get("className")
                or target.get("class_name")
                or action_payload.get("class_name")
                or ""
            ).strip()
            process_ids = []
            process_id = target.get("processId") or target.get("process_id")
            if process_id not in (None, ""):
                try:
                    process_ids.append(int(process_id))
                except Exception:
                    process_ids = []
            try:
                focused_window = self.driver.focus_window(
                    window_title=expected_title or None,
                    window_handle=target.get("windowHandle") or target.get("window_handle"),
                    class_name=expected_class or None,
                    process_ids=process_ids or None,
                )
                details = {
                    "window": focused_window,
                    "expectedTitle": expected_title or None,
                    "expectedClassName": expected_class or None,
                }
                return ComputerUseVerification(
                    passed=True,
                    status="verified",
                    reason="目标窗口已存在并成功聚焦。",
                    details=details,
                    level="verified",
                )
            except Exception as exc:
                return ComputerUseVerification(
                    passed=False,
                    status="window_unresolved",
                    reason=f"未能确认目标窗口状态：{exc}",
                    details={
                        "expectedTitle": expected_title or None,
                        "expectedClassName": expected_class or None,
                    },
                    level="failed",
                )
        verification = self.driver.verify_action(
            action_type=action_type,
            target=self._verification_target(action_payload, result),
            text=action_payload.get("text"),
            window_title=action_payload.get("window_title"),
            window_handle=action_payload.get("window_handle"),
            before_observation=before_observation,
            after_observation=after_observation,
        )
        return self._normalize_verification(verification)

    def _emit_review_required_event(
        self,
        *,
        run_handle,
        index: int,
        action: str,
        result: Dict[str, Any] | None,
    ) -> None:
        verification_payload = (((result or {}).get("result") or {}).get("verification") or {})
        verification = self._normalize_verification(verification_payload)
        if verification.level != "review_required":
            return
        run_handle.emit(
            "computer_use.step.review_required",
            {
                "index": index,
                "action": action,
                "verification": verification.as_dict(),
                "updateRequest": ((((result or {}).get("result") or {}).get("metadata") or {}).get("updateRequest")),
            },
        )

    def _maybe_abort_plan_for_update_request(
        self,
        *,
        run_handle,
        index: int,
        action: str,
        step: Dict[str, Any],
        step_result: Dict[str, Any],
    ) -> None:
        update_request = self._extract_update_request(step_result.get("result") if isinstance(step_result, dict) else None)
        if update_request is None:
            return
        step_result["status"] = "update_requested"
        step_result["updateRequest"] = update_request
        run_handle.emit(
            "computer_use.step.update_requested",
            {
                "index": index,
                "action": action,
                "reason": update_request.get("reason"),
                "request": update_request,
            },
        )
        if not self._should_abort_on_major_deviation(action=action, step=step):
            return
        reason = str(update_request.get("reason") or "检测到页面结构变化，需要更新步骤。")
        run_handle.fail(reason, node="computer_use_runtime")
        raise DesktopDriverError(reason)

    def _extract_plan_payload(self, raw_text: str) -> List[Dict[str, Any]]:
        text = (raw_text or "").strip()
        if not text:
            raise DesktopDriverError("Computer Use planner 没有返回可解析内容。")

        candidates: List[str] = []
        fenced_matches = re.findall(r"```(?:json)?\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
        candidates.extend(item.strip() for item in fenced_matches if item.strip())

        array_start = text.find("[")
        array_end = text.rfind("]")
        if array_start >= 0 and array_end > array_start:
            candidates.append(text[array_start : array_end + 1].strip())

        object_start = text.find("{")
        object_end = text.rfind("}")
        if object_start >= 0 and object_end > object_start:
            candidates.append(text[object_start : object_end + 1].strip())

        candidates.append(text)

        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except Exception:
                continue
            if isinstance(parsed, list):
                return [dict(step) for step in parsed if isinstance(step, dict)]
            if isinstance(parsed, dict):
                steps = parsed.get("steps")
                if isinstance(steps, list):
                    return [dict(step) for step in steps if isinstance(step, dict)]
        raise DesktopDriverError("Computer Use planner 返回内容不是有效的 JSON steps。")

    def _planner_response_text(self, response: Any) -> str:
        return sanitize_background_model_output(response).text

    def _observation_summary(self, observation: Dict[str, Any], *, max_elements: int = 18) -> str:
        if not isinstance(observation, dict):
            return "无可用观察结果。"
        elements = list(observation.get("elements") or [])[: max(1, max_elements)]
        lines = [
            f"窗口标题: {observation.get('windowTitle') or ''}",
            f"应用: {observation.get('app') or ''}",
            f"元素数量(截断后): {len(elements)}",
            "可用元素:",
        ]
        for index, element in enumerate(elements, start=1):
            if not isinstance(element, dict):
                continue
            lines.append(
                f"{index}. role={element.get('role') or ''} "
                f"name={element.get('name') or ''} "
                f"automationId={element.get('automationId') or ''} "
                f"className={element.get('className') or ''} "
                f"elementId={element.get('elementId') or ''}"
            )
        return "\n".join(lines)

    def _normalize_planned_steps(
        self,
        *,
        raw_steps: List[Dict[str, Any]],
        observation: Dict[str, Any],
        max_steps: int,
        window_title: str | None = None,
        window_handle: int | None = None,
        goal_app_id: str | None = None,
        goal_app_name: str | None = None,
    ) -> List[Dict[str, Any]]:
        resolved_window_title = window_title or observation.get("windowTitle")
        resolved_window_handle = window_handle
        if resolved_window_handle is None:
            resolved_window_handle = (observation.get("metadata") or {}).get("windowHandle")
        profile_id = self._infer_app_id(
            window_title=resolved_window_title,
            class_name=(observation.get("metadata") or {}).get("className"),
            app_name=observation.get("app"),
        )

        steps: List[Dict[str, Any]] = []
        allowed_actions = {
            "observe",
            "find",
            "click",
            "double_click",
            "type_text",
            "hotkey",
            "scroll",
            "wait",
            "screenshot",
            "open_app",
            "focus_window",
            "find_and_type",
            "scroll_list",
            "click_toolbar_action",
        }
        for raw_step in raw_steps:
            step = dict(raw_step or {})
            action = str(step.get("action") or "").strip().lower()
            if action not in allowed_actions:
                continue
            step["action"] = action
            inference_window_title = step.get("window_title")
            inference_class_name = step.get("class_name")
            inference_app_name = step.get("app_name") or step.get("app")
            if action not in {"open_app", "focus_window"}:
                inference_window_title = inference_window_title or resolved_window_title
                inference_class_name = inference_class_name or (observation.get("metadata") or {}).get("className")
            elif not inference_app_name and goal_app_name:
                inference_app_name = goal_app_name
            step_app_id = self._infer_app_id(
                explicit_app_id=step.get("app_id"),
                window_title=inference_window_title,
                class_name=inference_class_name,
                app_name=inference_app_name,
            )
            if step_app_id and step.get("app_id") is None and action in {"open_app", "focus_window", "find_and_type", "scroll_list", "click_toolbar_action"}:
                step["app_id"] = step_app_id
            elif goal_app_id and step.get("app_id") is None and action in {"open_app", "focus_window", "find_and_type", "scroll_list", "click_toolbar_action"}:
                step["app_id"] = goal_app_id
            elif profile_id and step.get("app_id") is None and action in {"find_and_type", "scroll_list", "click_toolbar_action"}:
                step["app_id"] = profile_id
            if (
                action == "open_app"
                and goal_app_name
                and not (step.get("app_name") or step.get("app"))
            ):
                step["app_name"] = goal_app_name
            if self._step_uses_observation_context(action=action, step=step) and resolved_window_title and step.get("window_title") is None:
                step["window_title"] = resolved_window_title
            if self._step_uses_observation_context(action=action, step=step) and resolved_window_handle is not None and step.get("window_handle") is None:
                step["window_handle"] = resolved_window_handle
            if action == "type_text":
                step["text"] = str(step.get("text") or "")
            if action == "find_and_type":
                step["text"] = str(step.get("text") or "")
            if action == "click_toolbar_action":
                step["action_name"] = str(step.get("action_name") or "")
                if self._requires_pre_action_guard(app_id=step.get("app_id"), action_name=step.get("action_name")):
                    step.setdefault("require_pre_action_visual_guard", True)
                    step.setdefault("require_visual_guard", True)
            steps.append(step)
            if len(steps) >= max(1, max_steps):
                break
        if not steps:
            raise DesktopDriverError("Planner 没有生成任何可执行的 Computer Use 步骤。")
        return steps

    def _profile_planner_context(self, *, profile_id: str | None) -> str:
        profile = self.app_profiles.get(profile_id)
        if profile is None:
            return "无 profile 特殊约束。"
        selector_keys = ", ".join(sorted(profile.selectors.keys())[:8]) or "无"
        toolbar_actions = ", ".join(sorted(profile.toolbar_actions.keys())[:8]) or "无"
        high_risk = ", ".join(profile.high_risk_actions) or "无"
        transient = ", ".join(profile.transient_selectors[:8]) or "无"
        return (
            f"profile={profile.display_name}\n"
            f"常用 selector: {selector_keys}\n"
            f"工具栏动作: {toolbar_actions}\n"
            f"高风险动作: {high_risk}\n"
            f"易 transient selector: {transient}"
        )

    def _goal_app_candidates(self, *, goal: str, limit: int = 6) -> List[Dict[str, Any]]:
        if not str(goal or "").strip():
            return []
        payload = self.app_catalog.list_apps(
            query=goal,
            limit=max(1, limit),
            include_running=True,
            force_refresh=False,
        )
        candidates: List[Dict[str, Any]] = []
        for item in list(payload.get("apps") or []):
            if not isinstance(item, dict):
                continue
            if int(item.get("matchScore") or 0) <= 0:
                continue
            candidates.append(item)
        return candidates

    def _goal_app_candidates_context(self, *, goal: str) -> str:
        candidates = self._goal_app_candidates(goal=goal)
        if not candidates:
            return "无明显目标应用候选。"
        lines: List[str] = []
        for index, item in enumerate(candidates[:6], start=1):
            lines.append(
                f"{index}. appId={item.get('appId')} displayName={item.get('displayName')} "
                f"running={bool(item.get('isRunning'))} launchable={bool(item.get('launchable'))} "
                f"profileId={item.get('profileId') or ''} matchScore={item.get('matchScore') or 0}"
            )
        return "\n".join(lines)

    def _plan_steps(
        self,
        *,
        goal: str,
        observation: Dict[str, Any],
        max_steps: int,
        window_title: str | None = None,
        window_handle: int | None = None,
    ) -> Dict[str, Any]:
        planner_model = llm_factory.create_for_role("computer_use_planner", temperature=0.1, streaming=False)
        system_prompt = (
            "你是 Windows Computer Use 的短视距规划器。\n"
            "目标：基于当前观察结果，只规划接下来 2 到 5 步的结构化桌面动作。\n"
            "约束：\n"
            "1. 只能输出 JSON，不要输出解释。\n"
            "2. 顶层必须是数组，数组中每个对象必须包含 action。\n"
            "3. 允许的 action: observe, find, click, double_click, type_text, hotkey, scroll, wait, screenshot, open_app, focus_window, find_and_type, scroll_list, click_toolbar_action。\n"
            "4. 优先使用 automation_id / 精确 name / control_type / class_name，避免模糊描述。\n"
            "5. 如果需要输入文本，优先使用 find_and_type；只有已经拿到精准 selector 时才使用 type_text。\n"
            "6. 不要规划超过 5 步。\n"
            "7. 如果信息不足，先规划 observe 或 find，不要猜。\n"
            "8. 如果目标是启动应用，优先使用 open_app；如果只是切回已存在窗口，优先使用 focus_window。\n"
            "9. 资源列表滚动优先使用 scroll_list；工具栏按钮优先使用 click_toolbar_action。\n"
            "10. 如果目标文本里已经命中候选应用，优先复用候选 appId，不要自己虚构命令或沿用当前窗口标题。"
        )
        goal_candidates = self._goal_app_candidates(goal=goal)
        goal_app = goal_candidates[0] if goal_candidates else None
        profile_id = self._infer_app_id(
            window_title=window_title or observation.get("windowTitle"),
            class_name=(observation.get("metadata") or {}).get("className"),
            app_name=observation.get("app"),
        )
        user_prompt = (
            f"任务目标:\n{goal}\n\n"
            f"应用 profile:\n{profile_id or '未指定'}\n\n"
            f"目标候选应用:\n{self._goal_app_candidates_context(goal=goal)}\n\n"
            f"profile 约束:\n{self._profile_planner_context(profile_id=profile_id)}\n\n"
            f"窗口限定:\nwindowTitle={window_title or observation.get('windowTitle') or ''}\n"
            f"windowHandle={window_handle or (observation.get('metadata') or {}).get('windowHandle') or ''}\n\n"
            f"当前观察:\n{self._observation_summary(observation)}\n\n"
            "请直接返回 JSON 数组。"
        )
        prepared = prepare_background_model_messages(
            system_prompt=system_prompt,
            instruction="根据已准备的桌面观察材料输出唯一 JSON 数组。",
            materials=[
                {
                    "title": "Computer Use short-horizon planning context",
                    "kind": "computer_use_planning_context",
                    "content": user_prompt,
                }
            ],
            runtime_kind="computer_use",
            target_role="computer_use:planner",
            resolved_model_id="computer_use_planner",
            component="computer_use",
            node="planner_context",
        )
        response = planner_model.invoke(
            prepared.messages
        )
        planner_text = self._planner_response_text(response)
        raw_steps = self._extract_plan_payload(planner_text)
        steps = self._normalize_planned_steps(
            raw_steps=raw_steps,
            observation=observation,
            max_steps=max_steps,
            window_title=window_title,
            window_handle=window_handle,
            goal_app_id=str((goal_app or {}).get("appId") or "").strip() or None,
            goal_app_name=str((goal_app or {}).get("displayName") or "").strip() or None,
        )
        return {
            "plannerOutput": planner_text,
            "steps": steps,
        }

    def _preflight(self, *, run_handle, goal: str | None = None, context: Dict[str, Any] | None = None) -> None:
        decision = safety_guardian.preflight_runtime(
            runtime_kind="computer_use",
            trigger_source="computer_use",
            session_id=run_handle.session_id,
            run_id=run_handle.run_id,
            resolved_scope=None,
            user_id=run_handle.descriptor.user_id,
        )
        if decision.is_block():
            raise DesktopDriverError(decision.reason)
        run_handle.emit(
            "safety.preflight.checked",
            {
                "runtime": "computer_use",
                "goal": goal,
                "context": dict(context or {}),
                "decision": decision.to_payload(),
            },
        )

    def _runtime_action_safety_target(
        self,
        *,
        action_type: str,
        action_payload: Dict[str, Any] | None,
    ) -> Dict[str, Any]:
        payload = dict(action_payload or {})
        target = {
            "app_id": payload.get("app_id") or payload.get("resolved_app_id"),
            "selector_key": payload.get("selector_key"),
            "profile_action": payload.get("profile_action"),
            "element_id": payload.get("element_id"),
            "name": payload.get("name"),
            "name_contains": payload.get("name_contains"),
            "target_text": payload.get("target_text"),
            "automation_id": payload.get("automation_id"),
            "control_type": payload.get("control_type"),
            "class_name": payload.get("class_name"),
            "window_title": payload.get("window_title"),
            "window_handle": payload.get("window_handle"),
            "point": payload.get("point"),
            "spatial_anchor": payload.get("spatial_anchor") or payload.get("spatialAnchor"),
            "sequence": payload.get("sequence"),
            "amount": payload.get("amount"),
        }
        if str(action_type or "").strip().lower() == "type_text":
            target["text_preview"] = str(payload.get("text") or "")[:80]
        return {key: value for key, value in target.items() if value not in (None, "", [])}

    def _assess_runtime_action_safety(
        self,
        *,
        run_handle,
        action_type: str,
        action_payload: Dict[str, Any] | None,
    ) -> Dict[str, Any]:
        normalized_action = str(action_type or "").strip().lower()
        guarded_actions = {
            "click",
            "double_click",
            "right_click",
            "type_text",
            "hotkey",
            "scroll",
            "page_scroll",
            "drag",
            "hover",
            "find_and_type",
            "scroll_list",
            "click_toolbar_action",
        }
        if normalized_action not in guarded_actions:
            return {"applied": False, "reason": "non_mutating_or_untracked_action"}
        effective_action = {
            "find_and_type": "type_text",
            "scroll_list": "scroll",
            "page_scroll": "scroll",
            "click_toolbar_action": "click",
        }.get(normalized_action, normalized_action)
        target = self._runtime_action_safety_target(
            action_type=effective_action,
            action_payload=action_payload,
        )
        runtime_context = {
            **self._run_context(run_handle=run_handle),
            "runtime_kind": "computer_use",
            "trigger_source": "computer_use_runtime",
        }
        decision = safety_guardian.assess_computer_use_action(
            action_type=effective_action,
            target=target,
            runtime_context=runtime_context,
        )
        safety_guardian.log_decision_event(
            action="computer_use_runtime_action",
            decision=decision,
            subject=effective_action,
            metadata={
                "runId": run_handle.run_id,
                "sessionId": run_handle.session_id,
                "target": target,
            },
        )
        payload = {
            "applied": True,
            "actionType": effective_action,
            "target": target,
            "decision": decision.to_payload(),
        }
        try:
            run_handle.emit("computer_use.safety.action_checked", payload)
        except Exception:
            pass
        if decision.is_block():
            raise DesktopDriverError(decision.reason or "Safety Guardian blocked computer use action.")
        if decision.is_review():
            request = safety_guardian.build_runtime_preflight_request(
                runtime_kind="computer_use",
                trigger_source="computer_use_runtime_action",
                decision=decision,
                subject=json.dumps(target, ensure_ascii=False),
            )
            approval = run_handle.request_approval(
                approval_kind=str(request.get("approvalKind") or "safety_review"),
                request=request,
            )
            if str(approval.get("status") or "").strip().lower() == "pending":
                raise DesktopDriverError(decision.reason or "Safety review required for computer use action.")
        return payload

    def observe(
        self,
        *,
        session_id: str | None = None,
        run_id: str | None = None,
        user_id: str = "anonymous",
        project_id: str | None = None,
        workspace_id: str | None = None,
        workspace_path: str | None = None,
        goal: str | None = None,
        app_id: str | None = None,
        window_title: str | None = None,
        window_handle: int | None = None,
        depth_limit: int = 4,
        element_limit: int = 80,
        include_screenshot: bool = True,
        observe_notifications: bool = False,
        observe_sound: bool = False,
        environment_probe_mode: str | None = None,
        invocation_metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        self._ensure_runtime_ready()
        invocation = self._classify_invocation(invocation_metadata, default_trigger_source="computer_use_api")
        binding_decision = self._resolve_app_binding(
            explicit_app_id=app_id,
            window_title=window_title,
            app_name=None,
            class_name=None,
            include_running=True,
        )
        run_handle = self.begin_or_attach_run(
            session_id=session_id,
            run_id=run_id,
            user_id=user_id,
            project_id=project_id,
            workspace_id=workspace_id,
            workspace_path=workspace_path,
            goal=goal or "observe_desktop",
            trigger_source=invocation.trigger_source,
            metadata={
                "computer_use_goal": goal or "observe_desktop",
                "invocation": invocation.as_dict(),
                **self._binding_metadata(binding_decision),
            },
        )
        try:
            self._preflight(
                run_handle=run_handle,
                goal=goal,
                context=build_preflight_context(
                    action_type="observe",
                    goal=goal or "observe_desktop",
                    action_payload={
                        "app_id": app_id,
                        "window_title": window_title,
                        "window_handle": window_handle,
                    },
                ),
            )
            self._raise_if_controlled(run_handle=run_handle)
        except RuntimeControlInterruption as exc:
            return self._build_controlled_result(
                run_handle=run_handle,
                signal=dict(exc.signal),
            )

        with bind_runtime_context(**self._run_context(run_handle=run_handle)):
            prepared_payload, binding_block = self._prepare_action_window_context(
                run_handle=run_handle,
                action_type="observe",
                action_payload={
                    "app_id": app_id,
                    "window_title": window_title,
                    "window_handle": window_handle,
                    "observe_notifications": observe_notifications,
                    "observe_sound": observe_sound,
                    "environment_probe_mode": environment_probe_mode,
                },
            )
            prepared_payload = self._attach_binding_to_payload(prepared_payload, binding_decision)
            window_title = prepared_payload.get("window_title")
            window_handle = prepared_payload.get("window_handle")
            app_id = prepared_payload.get("app_id")
            observation = self.driver.observe_desktop(
                window_title=window_title,
                window_handle=window_handle,
                depth_limit=depth_limit,
                element_limit=element_limit,
            )
            payload = observation.as_dict()
            metadata = dict(payload.get("metadata") or {})
            observed_binding = self._resolve_app_binding(
                explicit_app_id=app_id,
                window_title=payload.get("windowTitle"),
                class_name=metadata.get("className"),
                app_name=payload.get("app"),
                include_running=True,
            )
            resolved_app_id = observed_binding.resolved_app_id
            if resolved_app_id:
                metadata["profileId"] = resolved_app_id
                payload["metadata"] = metadata
                observation.metadata["profileId"] = resolved_app_id
            catalog_entry = dict(observed_binding.catalog_entry or {}) if observed_binding.catalog_entry else None
            expected_titles: List[str] = []
            explicit_window_title = str(window_title or "").strip()
            if explicit_window_title:
                expected_titles.append(explicit_window_title)
            for item in list((catalog_entry or {}).get("titlePatterns") or []):
                normalized = str(item or "").strip()
                if normalized and normalized not in expected_titles:
                    expected_titles.append(normalized)
            page_identity_hint = infer_window_page_identity(
                {
                    "title": payload.get("windowTitle"),
                    "className": metadata.get("className"),
                    "processName": metadata.get("processName"),
                    "handle": metadata.get("windowHandle"),
                },
                app_id=resolved_app_id,
                expected_titles=expected_titles,
                platform=self.driver.platform,
            )
            binding_assessment = build_window_binding_assessment(
                {
                    "title": payload.get("windowTitle"),
                    "className": metadata.get("className"),
                    "processName": metadata.get("processName"),
                    "handle": metadata.get("windowHandle"),
                    "isVisible": metadata.get("isVisible"),
                },
                expected_titles=expected_titles,
                expected_classes=list((catalog_entry or {}).get("classNames") or []),
                expected_process_names=list((catalog_entry or {}).get("processNames") or []),
                preferred_handle=window_handle,
                app_id=resolved_app_id,
                platform=self.driver.platform,
            )
            metadata["pageIdentity"] = page_identity_hint.get("pageIdentity")
            metadata["pageIdentityConfidence"] = page_identity_hint.get("confidence")
            metadata["bindingAssessment"] = binding_assessment
            metadata.update(build_action_policy_metadata(binding_decision=observed_binding, invocation=invocation))
            browser_decision = self._browser_lane_decision(
                action_type="observe",
                action_payload={
                    "app_id": resolved_app_id,
                    "resolved_app_id": resolved_app_id,
                    "window_title": payload.get("windowTitle"),
                    "window_handle": metadata.get("windowHandle"),
                    "class_name": metadata.get("className"),
                },
                app_id=resolved_app_id,
                process_name=metadata.get("processName"),
            )
            browser_metadata = self._build_browser_lane_metadata(browser_decision)
            if browser_decision.available:
                try:
                    browser_metadata.update(
                        self.browser_automation.observe(
                            window_title=payload.get("windowTitle"),
                            decision=browser_decision,
                        )
                    )
                except Exception as exc:
                    browser_metadata["available"] = False
                    browser_metadata["error"] = str(exc)
            metadata["browserAutomation"] = browser_metadata
            environment_probe = self._collect_environment_probe_snapshot(
                action_payload={
                    "observe_notifications": observe_notifications,
                    "observe_sound": observe_sound,
                    "environment_probe_mode": environment_probe_mode,
                }
            )
            if environment_probe:
                metadata["environmentProbe"] = dict(environment_probe)
                payload["environmentProbe"] = dict(environment_probe)
                observation.metadata["environmentProbe"] = dict(environment_probe)
            payload["metadata"] = metadata
            observation.metadata["pageIdentity"] = page_identity_hint.get("pageIdentity")
            observation.metadata["pageIdentityConfidence"] = page_identity_hint.get("confidence")
            observation.metadata["bindingAssessment"] = binding_assessment
            if binding_block:
                metadata["bindingBlock"] = dict(binding_block)
                payload["metadata"] = metadata
                observation.metadata["bindingBlock"] = dict(binding_block)
            scene_assessment = build_scene_assessment(
                app_id=resolved_app_id,
                action_type="observe",
                action_payload={
                    "app_id": resolved_app_id,
                    "window_title": explicit_window_title or payload.get("windowTitle"),
                    "window_handle": metadata.get("windowHandle"),
                },
                observation=payload,
                target={
                    "windowTitle": payload.get("windowTitle"),
                    "windowHandle": metadata.get("windowHandle"),
                    "appId": resolved_app_id,
                    "pageIdentity": metadata.get("pageIdentity"),
                },
                before_observation=payload,
                verification={
                    "passed": binding_assessment.get("status") == "verified",
                    "status": binding_assessment.get("status"),
                    "reason": "; ".join(list(binding_assessment.get("reasons") or [])),
                    "level": "verified" if binding_assessment.get("status") == "verified" else "review_required",
                },
                update_request={"requested": True} if binding_assessment.get("requiresUpdateRequest") else None,
                visual_guard=None,
            )
            metadata["sceneAssessment"] = scene_assessment
            payload["metadata"] = metadata
            payload["sceneAssessment"] = scene_assessment
            payload["bindingAssessment"] = binding_assessment
            observation.metadata["sceneAssessment"] = scene_assessment
            window_handle_value = metadata.get("windowHandle")
            if window_handle_value not in (None, ""):
                self._prime_selector_context(
                    window_handle=int(window_handle_value),
                    app_id=resolved_app_id,
                    window_title=payload.get("windowTitle"),
                    class_name=metadata.get("className"),
                )
            artifact = None
            if include_screenshot:
                artifact = self._record_observation_screenshot(
                    run_handle=run_handle,
                    workspace_path=workspace_path,
                    window_title=window_title,
                    window_handle=window_handle,
                )
                observation.screenshot_artifact = artifact
                payload["screenshotArtifact"] = artifact
            blocker_state = str((scene_assessment or {}).get("blockerState") or "none").strip().lower()
            binding_status = str((binding_assessment or {}).get("status") or "").strip().lower()
            signal_metadata = {
                "pageIdentity": metadata.get("pageIdentity"),
                "blockerState": blocker_state,
                "windowHandle": metadata.get("windowHandle"),
                "bindingStatus": binding_status,
            }
            self._emit_environment_signal(
                run_handle=run_handle,
                signal_kind="desktop_observation",
                summary=str(payload.get("windowTitle") or payload.get("app") or goal or "desktop_observation"),
                blocking=bool(binding_block) or blocker_state not in {"", "none"},
                metadata=signal_metadata,
            )
            if binding_block or blocker_state not in {"", "none"} or bool(binding_assessment.get("requiresUpdateRequest")):
                interrupt_summary = "检测到阻塞式桌面环境变化"
                if binding_block:
                    interrupt_summary = "目标窗口绑定失效，已请求中断"
                elif blocker_state not in {"", "none"}:
                    interrupt_summary = f"检测到阻塞界面：{blocker_state}"
                self._request_environment_interrupt(
                    run_handle=run_handle,
                    signal_kind="desktop_blocker",
                    summary=interrupt_summary,
                    metadata=signal_metadata,
                    cooldown_seconds=5.0 if binding_block else 10.0,
                )
            run_handle.emit("computer_use.observation.captured", payload)
            snapshot = self._refresh_snapshot(run_handle=run_handle, observation=observation)
            return {
                "sessionId": run_handle.session_id,
                "runId": run_handle.run_id,
                "observation": payload,
                "snapshot": snapshot,
                "invocation": invocation.as_dict(),
                "binding": observed_binding.as_dict(),
            }

    def list_windows(self, *, title_filter: str | None = None, limit: int = 20) -> Dict[str, Any]:
        self._ensure_runtime_ready()
        return {
            "platform": self.driver.platform,
            "backend": self.driver.backend,
            "windows": self.driver.list_windows(title_filter=title_filter, limit=limit),
        }

    def list_apps(
        self,
        *,
        query: str | None = None,
        limit: int = 20,
        include_running: bool = True,
        force_refresh: bool = False,
        include_learned: bool = True,
    ) -> Dict[str, Any]:
        self._ensure_runtime_ready()
        payload = self.app_catalog.list_apps(
            query=query,
            limit=max(1, min(limit, 100)),
            include_running=include_running,
            force_refresh=force_refresh,
            include_learned=include_learned,
        )
        return {
            "platform": self.driver.platform,
            "backend": self.driver.backend,
            **payload,
        }

    def open_app(
        self,
        *,
        app_id: str | None = None,
        app_name: str | None = None,
        command: str | None = None,
        launch_target_path: str | None = None,
        window_title: str | None = None,
        window_title_candidates: List[str] | None = None,
        strict_window_title_match: bool | None = None,
        class_name: str | None = None,
        wait_timeout_ms: int = 12000,
        poll_ms: int = 250,
        require_visual_guard: bool | None = None,
        prefer_fast_path: bool | None = None,
        post_action_settle_timeout_ms: int | None = None,
        post_action_settle_poll_ms: int | None = None,
        post_action_stable_rounds: int | None = None,
        abort_on_major_deviation: bool | None = None,
        observe_notifications: bool = False,
        observe_sound: bool = False,
        environment_probe_mode: str | None = None,
        session_id: str | None = None,
        run_id: str | None = None,
        user_id: str = "anonymous",
        project_id: str | None = None,
        workspace_id: str | None = None,
        workspace_path: str | None = None,
        goal: str | None = None,
        invocation_metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        self._ensure_runtime_ready()
        invocation = self._classify_invocation(invocation_metadata, default_trigger_source="computer_use_api")
        binding_decision = self._resolve_app_binding(
            explicit_app_id=app_id,
            window_title=window_title,
            class_name=class_name,
            app_name=app_name,
            include_running=True,
        )
        resolved_app_id = binding_decision.resolved_app_id
        catalog_entry = dict(binding_decision.catalog_entry or {}) if binding_decision.catalog_entry else None
        adapter_match = self.app_adapters.match(
            app_id=resolved_app_id or app_id,
            app_name=app_name or (catalog_entry or {}).get("displayName"),
            process_names=list((catalog_entry or {}).get("processNames") or []),
            title_patterns=list((catalog_entry or {}).get("titlePatterns") or []),
            launch_candidates=list((catalog_entry or {}).get("launchCandidates") or []),
            catalog_entry=catalog_entry,
        )
        profile_lookup_app_id = self._profile_lookup_app_id_for_binding(
            binding_decision=binding_decision,
            fallback_app_id=app_id,
        )
        profile = self.app_profiles.get(profile_lookup_app_id)
        launch_selection = self._resolve_launch_selection(
            app_id=resolved_app_id,
            app_name=app_name,
            window_title=window_title,
            command=command,
            profile=profile,
        )
        launch_command = launch_selection["command"]
        adapter_open = None
        if adapter_match is not None:
            try:
                adapter_open = adapter_match.adapter.build_open_command(
                    app_id=resolved_app_id or app_id,
                    app_name=app_name or (catalog_entry or {}).get("displayName"),
                    launch_target_path=launch_target_path,
                )
            except Exception:
                adapter_open = None
        if adapter_open and list(adapter_open.get("command") or []):
            launch_command = list(adapter_open.get("command") or [])
            launch_selection = {
                **dict(launch_selection or {}),
                "command": launch_command,
                "selectionReason": adapter_open.get("selectionReason") or "app_adapter_open",
                "launchCandidateSource": "app_adapter",
                "launchCandidateRole": "structured_open",
                "launchCandidateScore": 999,
            }
            catalog_entry = {
                **dict(catalog_entry or {}),
                "appAdapterId": adapter_open.get("appAdapterId") or (catalog_entry or {}).get("appAdapterId"),
                "controlClass": adapter_open.get("controlClass") or (catalog_entry or {}).get("controlClass"),
                "processNames": list(adapter_open.get("processNames") or (catalog_entry or {}).get("processNames") or []),
                "titlePatterns": list(adapter_open.get("windowTitleHints") or (catalog_entry or {}).get("titlePatterns") or []),
            }
        if not launch_command:
            raise DesktopDriverError(
                f"未提供可执行的应用启动命令。app_id={resolved_app_id or ''} app_name={str(app_name or '').strip()}".strip()
            )
        catalog_titles = list((catalog_entry or {}).get("titlePatterns") or [])
        catalog_classes = list((catalog_entry or {}).get("classNames") or [])
        catalog_processes = list((catalog_entry or {}).get("processNames") or [])
        profile_titles = list(profile.title_patterns) if profile else []
        profile_classes = list(profile.class_names) if profile else []
        profile_processes = list(profile.process_names) if profile else []
        combined_processes = list((catalog_entry or {}).get("processNames") or []) or profile_processes or catalog_processes
        expected_title = window_title or (profile_titles[0] if profile_titles else None) or (catalog_titles[0] if catalog_titles else None)
        expected_class = class_name or (profile_classes[0] if profile_classes else None) or (catalog_classes[0] if catalog_classes else None)
        launch_title_hints = derive_window_title_hints(
            app_id=resolved_app_id,
            command=launch_command,
            explicit_title=expected_title,
        )
        strict_title_binding = bool(strict_window_title_match and str(expected_title or "").strip())
        binding_candidates = self._window_binding_candidates(
            app_id=resolved_app_id,
            window_title=expected_title,
            class_name=expected_class,
            process_names=combined_processes or self.app_profiles.process_names_for(profile_lookup_app_id),
            fallback_titles=(
                [*(window_title_candidates or []), *launch_title_hints]
                if strict_title_binding
                else [*(window_title_candidates or []), *launch_title_hints, *profile_titles, *catalog_titles]
            ),
            fallback_classes=([*profile_classes, *catalog_classes] if not strict_title_binding else [expected_class] if expected_class else []),
        )
        expected_titles = list(binding_candidates["titles"])
        expected_classes = list(binding_candidates["classes"])
        expected_process_names = list(binding_candidates["processNames"])
        browser_lane_decision = self.browser_automation.decide_lane(
            action_type="open_app",
            action_payload={
                "app_id": resolved_app_id,
                "app_name": app_name,
                "window_title": expected_title,
                "class_name": expected_class,
            },
            app_id=resolved_app_id,
            window_title=expected_title,
            class_name=expected_class,
        )
        control_class = self._control_class_for_action(
            binding_decision=binding_decision,
            catalog_entry=catalog_entry,
        )
        browser_window_preferences = self._browser_window_preferences(
            app_id=resolved_app_id,
            app_name=app_name,
            launch_command=launch_command,
            window_title=expected_title,
            class_name=expected_class,
            lane_decision=browser_lane_decision,
        )
        browser_title_hint_only = bool(browser_window_preferences and not str(window_title or "").strip())
        running_lookup_title = None if browser_title_hint_only else expected_title
        running_lookup_titles = [] if browser_title_hint_only else list(expected_titles)
        visual_expectation = self._visual_expectation(app_id=profile_lookup_app_id, action_name="open_app")

        def _runner(_run_handle, _runtime_payload=None):
            explorer_targeted_launch = bool(
                resolved_app_id == "explorer"
                and str(launch_target_path or "").strip()
            )
            existing_window = None
            restore_strategy = None
            tray_restore_attempted = False
            tray_restore_matched_label = None
            running_process_ids = self._running_process_ids(process_names=expected_process_names)
            if not explorer_targeted_launch:
                existing_window = self._pick_running_window(
                    catalog_entry=catalog_entry,
                    profile=profile,
                    expected_process_names=expected_process_names,
                    expected_title=running_lookup_title,
                    expected_class=expected_class,
                    browser_window_preferences=browser_window_preferences,
                    title_as_hint_only=browser_title_hint_only,
                )
                if existing_window is not None:
                    restore_strategy = "direct_window"
                if existing_window is None:
                    existing_window = self._restore_existing_window(
                        expected_titles=expected_titles,
                        expected_classes=expected_classes,
                        expected_process_names=expected_process_names,
                    )
                    if existing_window is not None:
                        restore_strategy = "process_window"
                if existing_window is None:
                    existing_window = self._probe_existing_window(
                        expected_title=running_lookup_title,
                        expected_titles=running_lookup_titles,
                        expected_class=expected_class,
                        expected_classes=expected_classes,
                        expected_process_names=expected_process_names,
                        browser_window_preferences=browser_window_preferences,
                        title_as_hint_only=browser_title_hint_only,
                    )
                    if existing_window is not None:
                        restore_strategy = "direct_window"
                if existing_window is None:
                    tray_restore_attempted = bool(running_process_ids)
                    existing_window = self._restore_existing_window_from_tray(
                        catalog_entry=catalog_entry,
                        expected_titles=expected_titles,
                        expected_classes=expected_classes,
                        expected_process_names=expected_process_names,
                    )
                    if existing_window is not None:
                        restore_strategy = "tray_icon"
                        tray_restore_matched_label = (
                            dict(existing_window.get("metadata") or {}).get("trayRestoreMatchedLabel")
                        )
            process = None
            process_ids = None
            effective_launch_command = launch_command
            effective_launch_environment = None
            effective_browser_lane: Dict[str, Any] = dict(browser_lane_decision.as_dict())
            if existing_window is not None:
                window = dict(existing_window)
            else:
                try:
                    (
                        effective_launch_command,
                        effective_launch_environment,
                        launch_browser_lane,
                    ) = self.browser_automation.prepare_launch(
                        app_id=resolved_app_id,
                        launch_command=launch_command,
                        environment=None,
                    )
                    if launch_browser_lane:
                        effective_browser_lane.update(dict(launch_browser_lane))
                except Exception as exc:
                    effective_browser_lane.update(
                        {
                            "managedLaunch": False,
                            "error": str(exc),
                        }
                    )
                process = self._spawn_process(
                    effective_launch_command,
                    app_id=resolved_app_id,
                    launch_target_path=launch_target_path,
                    environment=effective_launch_environment,
                )
                if process is not None:
                    self._record_resource_lease(
                        run_handle=_run_handle,
                        kind="process",
                        resource={
                            "pid": int(getattr(process, "pid", 0) or 0),
                            "appId": resolved_app_id,
                            "command": effective_launch_command
                            if isinstance(effective_launch_command, list)
                            else [effective_launch_command],
                        },
                        cleanup_on_complete=False,
                        preserve_on_human_input=True,
                        reason="open_app_spawned_process",
                    )
                process_ids = (
                    [int(process.pid)]
                    if process is not None and (profile is None or profile.bind_process_ids)
                    else None
                )
                _run_handle.emit(
                    "computer_use.step.waiting_for_window",
                    {
                        "action": "open_app",
                        "appId": resolved_app_id,
                        "appName": str((catalog_entry or {}).get("displayName") or app_name or "").strip() or None,
                        "expectedTitle": expected_title,
                        "expectedClassName": expected_class,
                        "waitTimeoutMs": wait_timeout_ms,
                        "processNames": list(expected_process_names),
                    },
                )
                window = self._wait_for_window_after_launch(
                    expected_title=expected_title,
                    expected_titles=expected_titles,
                    expected_class=expected_class,
                    expected_classes=expected_classes,
                    expected_process_names=expected_process_names,
                    process_ids=process_ids,
                    timeout_ms=wait_timeout_ms,
                    poll_ms=poll_ms,
                )
            focused = self.driver.focus_window(
                window_handle=window.get("handle"),
                window_title_candidates=expected_titles,
                class_name_candidates=expected_classes,
                process_names=expected_process_names,
            )
            resolved_window = dict(focused or window)
            resolved_window = self._ensure_app_ready_window(
                run_handle=_run_handle,
                app_id=resolved_app_id,
                window=resolved_window,
                workspace_path=workspace_path,
                expected_titles=expected_titles,
                expected_classes=expected_classes,
                expected_process_names=expected_process_names,
                wait_timeout_ms=wait_timeout_ms,
            )
            if resolved_app_id == "explorer" and str(launch_target_path or "").strip():
                resolved_window = self._navigate_explorer_to_target_path(
                    run_handle=_run_handle,
                    window=resolved_window,
                    target_path=str(launch_target_path).strip(),
                    expected_title=expected_title,
                    expected_classes=expected_classes,
                    expected_process_names=expected_process_names,
                    wait_timeout_ms=wait_timeout_ms,
                    poll_ms=poll_ms,
                )
            resolved_window["windowTitle"] = resolved_window.get("title")
            resolved_window["windowHandle"] = resolved_window.get("handle")
            if process is not None:
                resolved_window["processId"] = int(getattr(process, "pid", 0) or 0)
            resolved_window["processNames"] = list(expected_process_names)
            resolved_window["appId"] = resolved_app_id
            resolved_window["profileId"] = self._used_profile_id_for_binding(
                binding_decision=binding_decision,
                profile=profile,
                catalog_entry=catalog_entry,
            )
            resolved_window["controlClass"] = control_class
            resolved_window["appAdapterId"] = (catalog_entry or {}).get("appAdapterId")
            if visual_expectation:
                resolved_window["visualExpectation"] = visual_expectation
            self._record_resource_lease(
                run_handle=_run_handle,
                kind="window",
                resource={
                    "windowHandle": resolved_window.get("handle") or resolved_window.get("windowHandle"),
                    "windowTitle": resolved_window.get("title") or resolved_window.get("windowTitle"),
                    "appId": resolved_app_id,
                    "processId": resolved_window.get("processId"),
                },
                cleanup_on_complete=False,
                preserve_on_human_input=True,
                reason="open_app_window",
            )
            if browser_window_preferences:
                effective_browser_lane.setdefault("family", browser_window_preferences.get("family"))
                effective_browser_lane.setdefault("preferredExistingBrowserWindow", True)
                effective_browser_lane.setdefault(
                    "preferredProcessNames",
                    list(browser_window_preferences.get("preferredProcessNames") or []),
                )
            if existing_window is not None and browser_window_preferences:
                effective_browser_lane["reusedExistingBrowserWindow"] = True
                effective_browser_lane.setdefault("profilePersistenceMode", "reused_existing_window")
                if str(browser_lane_decision.reason or "").strip().lower() == "attached_existing_debug_browser":
                    effective_browser_lane["attachedExistingBrowser"] = True
                    effective_browser_lane["profilePersistenceMode"] = "attached_existing_debug_browser"
            elif existing_window is not None and control_class == "electron_shell_app":
                effective_browser_lane.setdefault("profilePersistenceMode", "attached_existing_window_without_debug")
            elif existing_window is None and control_class == "electron_shell_app":
                if bool(browser_lane_decision.available):
                    effective_browser_lane.setdefault("profilePersistenceMode", "managed_launch_debuggable")
                else:
                    effective_browser_lane.setdefault("profilePersistenceMode", "managed_launch_shell_only")
            if browser_window_preferences:
                if existing_window is not None:
                    effective_browser_lane["selectedBrowserOwner"] = "existing_window"
                    effective_browser_lane["suppressedBrowserOwners"] = ["managed_cdp", "system_default"]
                elif bool(effective_browser_lane.get("managedLaunch")):
                    effective_browser_lane["selectedBrowserOwner"] = "managed_cdp"
                    effective_browser_lane["suppressedBrowserOwners"] = ["system_default"]
                else:
                    effective_browser_lane["selectedBrowserOwner"] = "system_or_profile_launch"
                    effective_browser_lane["suppressedBrowserOwners"] = ["managed_cdp"]
            self._remember_window_binding(
                app_id=resolved_app_id,
                window=resolved_window,
                source="open_app_binding",
                reason="open_app_success",
                weight=42,
            )
            self._prime_selector_context(
                window_handle=resolved_window.get("handle"),
                app_id=resolved_app_id,
                window_title=resolved_window.get("title"),
                class_name=resolved_window.get("className"),
            )
            self.app_catalog.record_runtime_window(
                app_id=resolved_app_id,
                display_name=str((catalog_entry or {}).get("displayName") or app_name or resolved_window.get("title") or "").strip() or None,
                profile_id=self._used_profile_id_for_binding(
                    binding_decision=binding_decision,
                    profile=profile,
                    catalog_entry=catalog_entry,
                ),
                launch_command=effective_launch_command if isinstance(effective_launch_command, list) else [effective_launch_command],
                window=resolved_window,
            )
            return ComputerUseActionResult(
                action_id=f"open_app_{uuid.uuid4().hex[:8]}",
                action_type="open_app",
                status="completed",
                message="应用已启动并聚焦。",
                target=resolved_window,
                metadata={
                    "appId": resolved_app_id,
                    "launchCommand": effective_launch_command if isinstance(effective_launch_command, list) else [effective_launch_command],
                    "processNames": list(expected_process_names),
                    "reusedRunningWindow": existing_window is not None,
                    "launchSelectionReason": launch_selection.get("selectionReason"),
                    "launchCandidateSource": launch_selection.get("launchCandidateSource"),
                    "launchCandidateRole": launch_selection.get("launchCandidateRole"),
                    "launchCandidateScore": launch_selection.get("launchCandidateScore"),
                    "restoreStrategy": restore_strategy,
                    "trayRestoreAttempted": tray_restore_attempted,
                    "trayRestoreMatchedLabel": tray_restore_matched_label,
                    "spawnSuppressedByRestore": bool(existing_window is not None and restore_strategy in {"process_window", "tray_icon"}),
                    "visualExpectation": visual_expectation or None,
                    "catalogDisplayName": (catalog_entry or {}).get("displayName"),
                    "controlClass": control_class,
                    "appAdapterId": (catalog_entry or {}).get("appAdapterId"),
                    "appAdapter": (
                        {
                            "id": adapter_match.adapter_id,
                            "selectionReason": adapter_open.get("selectionReason") if isinstance(adapter_open, dict) else None,
                            "targetKind": adapter_open.get("targetKind") if isinstance(adapter_open, dict) else None,
                        }
                        if adapter_match is not None
                        else None
                    ),
                    "browserAutomation": dict(effective_browser_lane),
                },
            )

        result = self._run_action(
            action_type="open_app",
            session_id=session_id,
            run_id=run_id,
            user_id=user_id,
            project_id=project_id,
            workspace_id=workspace_id,
            workspace_path=workspace_path,
            goal=goal or f"open_app:{resolved_app_id or command or 'unknown'}",
            action_payload=self._attach_binding_to_payload({
                "app_id": resolved_app_id,
                "app_name": app_name,
                "launch_target_path": str(launch_target_path or "").strip() or None,
                "window_title": expected_title,
                "window_title_candidates": list(expected_titles),
                "class_name": expected_class,
                "class_name_candidates": list(expected_classes),
                "process_names": list(expected_process_names),
                "control_class": control_class,
                "app_adapter_id": (catalog_entry or {}).get("appAdapterId"),
                "visual_expectation": visual_expectation or None,
                "strict_window_title_match": strict_title_binding,
                "require_visual_guard": require_visual_guard,
                "prefer_fast_path": prefer_fast_path,
                "post_action_settle_timeout_ms": post_action_settle_timeout_ms,
                "post_action_settle_poll_ms": post_action_settle_poll_ms,
                "post_action_stable_rounds": post_action_stable_rounds,
                "abort_on_major_deviation": abort_on_major_deviation,
                "observe_notifications": observe_notifications,
                "observe_sound": observe_sound,
                "environment_probe_mode": environment_probe_mode,
            }, binding_decision),
            invocation_metadata=invocation.as_dict(),
            runner=_runner,
        )
        result["profileId"] = self._used_profile_id_for_binding(
            binding_decision=binding_decision,
            profile=profile,
            catalog_entry=catalog_entry,
        )
        result["binding"] = binding_decision.as_dict()
        result["invocation"] = invocation.as_dict()
        return result

    def focus_window(
        self,
        *,
        app_id: str | None = None,
        app_name: str | None = None,
        target_path: str | None = None,
        window_title: str | None = None,
        window_title_candidates: List[str] | None = None,
        window_handle: int | None = None,
        class_name: str | None = None,
        require_visual_guard: bool | None = None,
        prefer_fast_path: bool | None = None,
        post_action_settle_timeout_ms: int | None = None,
        post_action_settle_poll_ms: int | None = None,
        post_action_stable_rounds: int | None = None,
        abort_on_major_deviation: bool | None = None,
        observe_notifications: bool = False,
        observe_sound: bool = False,
        environment_probe_mode: str | None = None,
        session_id: str | None = None,
        run_id: str | None = None,
        user_id: str = "anonymous",
        project_id: str | None = None,
        workspace_id: str | None = None,
        workspace_path: str | None = None,
        goal: str | None = None,
        invocation_metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        self._ensure_runtime_ready()
        invocation = self._classify_invocation(invocation_metadata, default_trigger_source="computer_use_api")
        binding_decision = self._resolve_app_binding(
            explicit_app_id=app_id,
            window_title=window_title,
            class_name=class_name,
            app_name=app_name,
            include_running=True,
        )
        resolved_app_id = binding_decision.resolved_app_id
        catalog_entry = dict(binding_decision.catalog_entry or {}) if binding_decision.catalog_entry else None
        profile_lookup_app_id = self._profile_lookup_app_id_for_binding(
            binding_decision=binding_decision,
            fallback_app_id=app_id,
        )
        profile = self.app_profiles.get(profile_lookup_app_id)
        catalog_titles = list((catalog_entry or {}).get("titlePatterns") or [])
        catalog_classes = list((catalog_entry or {}).get("classNames") or [])
        catalog_processes = list((catalog_entry or {}).get("processNames") or [])
        profile_titles = list(profile.title_patterns) if profile else []
        profile_classes = list(profile.class_names) if profile else []
        profile_processes = list(profile.process_names) if profile else []
        expected_title = window_title or (profile_titles[0] if profile_titles else None) or (catalog_titles[0] if catalog_titles else None)
        expected_class = class_name or (profile_classes[0] if profile_classes else None) or (catalog_classes[0] if catalog_classes else None)
        binding_candidates = self._window_binding_candidates(
            app_id=resolved_app_id,
            window_title=expected_title,
            class_name=expected_class,
            process_names=profile_processes or catalog_processes or self.app_profiles.process_names_for(profile_lookup_app_id),
            fallback_titles=[*(window_title_candidates or []), *profile_titles, *catalog_titles],
            fallback_classes=[*profile_classes, *catalog_classes],
        )
        expected_titles = list(binding_candidates["titles"])
        expected_classes = list(binding_candidates["classes"])
        expected_process_names = list(binding_candidates["processNames"])
        visual_expectation = self._visual_expectation(app_id=profile_lookup_app_id, action_name="focus_window")

        def _runner(_run_handle, _runtime_payload=None):
            focused = self.driver.focus_window(
                window_title=expected_title,
                window_title_candidates=expected_titles,
                window_handle=window_handle,
                class_name=expected_class,
                class_name_candidates=expected_classes,
                process_names=expected_process_names,
                include_titleless=bool(expected_process_names),
            )
            focused = self._ensure_app_ready_window(
                run_handle=_run_handle,
                app_id=resolved_app_id,
                window=focused,
                workspace_path=workspace_path,
                expected_titles=expected_titles,
                expected_classes=expected_classes,
                expected_process_names=expected_process_names,
                wait_timeout_ms=max(1200, min(post_action_settle_timeout_ms or 4500, 6000)),
            )
            if resolved_app_id == "explorer" and str(target_path or "").strip():
                focused = self._navigate_explorer_to_target_path(
                    run_handle=_run_handle,
                    window=focused,
                    target_path=str(target_path).strip(),
                    expected_title=expected_title,
                    expected_classes=expected_classes,
                    expected_process_names=expected_process_names,
                    wait_timeout_ms=max(1200, min(post_action_settle_timeout_ms or 4500, 6000)),
                    poll_ms=max(120, min(post_action_settle_poll_ms or 180, 260)),
                )
            focused["windowTitle"] = focused.get("title")
            focused["windowHandle"] = focused.get("handle")
            focused["appId"] = resolved_app_id
            focused["profileId"] = self._used_profile_id_for_binding(
                binding_decision=binding_decision,
                profile=profile,
                catalog_entry=catalog_entry,
            )
            focused["processNames"] = list(expected_process_names)
            if visual_expectation:
                focused["visualExpectation"] = visual_expectation
            self._remember_window_binding(
                app_id=resolved_app_id,
                window=focused,
                source="focus_window_binding",
                reason="focus_window_success",
                weight=36,
            )
            self._prime_selector_context(
                window_handle=focused.get("handle"),
                app_id=resolved_app_id,
                window_title=focused.get("title"),
                class_name=focused.get("className"),
            )
            self.app_catalog.record_runtime_window(
                app_id=resolved_app_id,
                display_name=str((catalog_entry or {}).get("displayName") or app_name or focused.get("title") or "").strip() or None,
                profile_id=self._used_profile_id_for_binding(
                    binding_decision=binding_decision,
                    profile=profile,
                    catalog_entry=catalog_entry,
                ),
                launch_command=[],
                window=focused,
            )
            return ComputerUseActionResult(
                action_id=f"focus_window_{uuid.uuid4().hex[:8]}",
                action_type="focus_window",
                status="completed",
                message="目标窗口已聚焦。",
                target=focused,
                metadata={
                    "appId": resolved_app_id,
                    "processNames": list(expected_process_names),
                    "visualExpectation": visual_expectation or None,
                    "catalogDisplayName": (catalog_entry or {}).get("displayName"),
                },
            )

        result = self._run_action(
            action_type="focus_window",
            session_id=session_id,
            run_id=run_id,
            user_id=user_id,
            project_id=project_id,
            workspace_id=workspace_id,
            workspace_path=workspace_path,
            goal=goal or f"focus_window:{resolved_app_id or window_title or window_handle or 'unknown'}",
            action_payload=self._attach_binding_to_payload({
                "app_id": resolved_app_id,
                "app_name": app_name,
                "target_path": str(target_path or "").strip() or None,
                "window_title": expected_title,
                "window_handle": window_handle,
                "class_name": expected_class,
                "window_title_candidates": list(expected_titles),
                "class_name_candidates": list(expected_classes),
                "process_names": list(expected_process_names),
                "visual_expectation": visual_expectation or None,
                "require_visual_guard": require_visual_guard,
                "prefer_fast_path": prefer_fast_path,
                "post_action_settle_timeout_ms": post_action_settle_timeout_ms,
                "post_action_settle_poll_ms": post_action_settle_poll_ms,
                "post_action_stable_rounds": post_action_stable_rounds,
                "abort_on_major_deviation": abort_on_major_deviation,
                "observe_notifications": observe_notifications,
                "observe_sound": observe_sound,
                "environment_probe_mode": environment_probe_mode,
            }, binding_decision),
            invocation_metadata=invocation.as_dict(),
            runner=_runner,
        )
        result["profileId"] = self._used_profile_id_for_binding(
            binding_decision=binding_decision,
            profile=profile,
            catalog_entry=catalog_entry,
        )
        result["binding"] = binding_decision.as_dict()
        result["invocation"] = invocation.as_dict()
        return result

    def find_and_type(self, **kwargs: Any) -> Dict[str, Any]:
        payload = dict(kwargs)
        self._ensure_runtime_ready()
        invocation_metadata = self._pop_invocation_metadata(payload)
        binding_decision = self._resolve_app_binding(
            explicit_app_id=payload.get("app_id"),
            window_title=payload.get("window_title"),
            class_name=payload.get("class_name"),
            app_name=payload.get("app_name") or payload.get("app"),
        )
        resolved_app_id = binding_decision.resolved_app_id
        profile_lookup_app_id = self._profile_lookup_app_id_for_binding(
            binding_decision=binding_decision,
            fallback_app_id=payload.get("app_id"),
        )
        profile = self.app_profiles.get(profile_lookup_app_id)
        expected_title = payload.get("window_title") or (profile.title_patterns[0] if profile and profile.title_patterns else None)
        expected_class = payload.get("class_name") or (profile.class_names[0] if profile and profile.class_names else None)
        selector = self._resolve_profile_selector(
            app_id=profile_lookup_app_id,
            selector_key=payload.get("selector_key"),
            fallbacks=input_selector_fallback_keys(),
        )
        merged = {
            **selector,
            **{
                k: v
                for k, v in payload.items()
                if v not in (None, "") and k not in {"app_id", "visual_expectation", "require_visual_guard"}
            },
        }
        if expected_title and not merged.get("window_title"):
            merged["window_title"] = expected_title
        if expected_class and not merged.get("class_name"):
            merged["class_name"] = expected_class
        visual_expectation = (
            payload.get("visual_expectation")
            or self._visual_expectation(app_id=profile_lookup_app_id, action_name="find_and_type")
        )
        selector_key = payload.get("selector_key") or next(
            (item for item in input_selector_fallback_keys() if selector == self._profile_selector(app_id=profile_lookup_app_id, selector_key=item)),
            None,
        )
        result = self.type_text(
            **merged,
            app_id=resolved_app_id,
            invocation_metadata=invocation_metadata,
            profile_action="find_and_type",
            visual_expectation=visual_expectation,
            require_visual_guard=payload.get("require_visual_guard"),
            transient_selector=self._is_transient_selector_key(app_id=resolved_app_id, selector_key=selector_key),
        )
        result["profileId"] = resolved_app_id if binding_allows_profile(binding_decision) else None
        result["resolvedSelector"] = selector
        result["binding"] = binding_decision.as_dict()
        return result

    def scroll_list(self, **kwargs: Any) -> Dict[str, Any]:
        payload = dict(kwargs)
        self._ensure_runtime_ready()
        invocation_metadata = self._pop_invocation_metadata(payload)
        binding_decision = self._resolve_app_binding(
            explicit_app_id=payload.get("app_id"),
            window_title=payload.get("window_title"),
            class_name=payload.get("class_name"),
            app_name=payload.get("app_name") or payload.get("app"),
        )
        resolved_app_id = binding_decision.resolved_app_id
        profile_lookup_app_id = self._profile_lookup_app_id_for_binding(
            binding_decision=binding_decision,
            fallback_app_id=payload.get("app_id"),
        )
        profile = self.app_profiles.get(profile_lookup_app_id)
        expected_title = payload.get("window_title") or (profile.title_patterns[0] if profile and profile.title_patterns else None)
        expected_class = payload.get("class_name") or (profile.class_names[0] if profile and profile.class_names else None)
        selector = self._resolve_profile_selector(
            app_id=profile_lookup_app_id,
            selector_key=payload.get("selector_key"),
            fallbacks=list_selector_fallback_keys(),
        )
        merged = {
            **selector,
            **{
                k: v
                for k, v in payload.items()
                if v not in (None, "") and k not in {"app_id", "visual_expectation", "require_visual_guard"}
            },
        }
        if expected_title and not merged.get("window_title"):
            merged["window_title"] = expected_title
        if expected_class and not merged.get("class_name"):
            merged["class_name"] = expected_class
        visual_expectation = (
            payload.get("visual_expectation")
            or self._visual_expectation(app_id=profile_lookup_app_id, action_name="scroll_list")
        )
        result = self.scroll(
            **merged,
            app_id=resolved_app_id,
            invocation_metadata=invocation_metadata,
            profile_action="scroll_list",
            visual_expectation=visual_expectation,
            require_visual_guard=payload.get("require_visual_guard"),
            transient_selector=self._is_transient_selector_key(
                app_id=resolved_app_id,
                selector_key=payload.get("selector_key") or list_selector_fallback_keys()[0],
            ),
        )
        result["profileId"] = resolved_app_id if binding_allows_profile(binding_decision) else None
        result["resolvedSelector"] = selector
        result["binding"] = binding_decision.as_dict()
        return result

    def click_toolbar_action(self, **kwargs: Any) -> Dict[str, Any]:
        payload = dict(kwargs)
        self._ensure_runtime_ready()
        invocation_metadata = self._pop_invocation_metadata(payload)
        binding_decision = self._resolve_app_binding(
            explicit_app_id=payload.get("app_id"),
            window_title=payload.get("window_title"),
            class_name=payload.get("class_name"),
            app_name=payload.get("app_name") or payload.get("app"),
        )
        resolved_app_id = binding_decision.resolved_app_id
        profile_lookup_app_id = self._profile_lookup_app_id_for_binding(
            binding_decision=binding_decision,
            fallback_app_id=payload.get("app_id"),
        )
        profile = self.app_profiles.get(profile_lookup_app_id)
        expected_title = payload.get("window_title") or (profile.title_patterns[0] if profile and profile.title_patterns else None)
        expected_class = payload.get("class_name") or (profile.class_names[0] if profile and profile.class_names else None)
        action_name = str(payload.get("action_name") or "").strip()
        selector = self._toolbar_selector(app_id=profile_lookup_app_id, action_name=action_name)
        if not selector:
            selector = self._resolve_profile_selector(
                app_id=profile_lookup_app_id,
                selector_key=payload.get("selector_key") or action_name,
                fallbacks=self.app_profiles.action_selector_keys_for(profile_lookup_app_id, action_name),
            )
        merged = {
            **selector,
            **{
                k: v
                for k, v in payload.items()
                if v not in (None, "") and k not in {"app_id", "visual_expectation", "require_visual_guard"}
            },
            **({"toolbar_action_name": action_name} if action_name else {}),
        }
        if expected_title and not merged.get("window_title"):
            merged["window_title"] = expected_title
        if expected_class and not merged.get("class_name"):
            merged["class_name"] = expected_class
        visual_expectation = (
            payload.get("visual_expectation")
            or self._visual_expectation(app_id=profile_lookup_app_id, action_name="click_toolbar_action")
            or self._visual_expectation(app_id=profile_lookup_app_id, action_name=action_name)
        )
        result = self.click(
            **merged,
            app_id=resolved_app_id,
            invocation_metadata=invocation_metadata,
            profile_action=action_name or "click_toolbar_action",
            visual_expectation=visual_expectation,
            require_visual_guard=payload.get("require_visual_guard"),
            transient_selector=self._is_transient_selector_key(app_id=resolved_app_id, selector_key=action_name),
        )
        result["profileId"] = resolved_app_id if binding_allows_profile(binding_decision) else None
        result["resolvedSelector"] = selector
        result["actionName"] = action_name or None
        result["binding"] = binding_decision.as_dict()
        return result

    def find_elements(self, **kwargs: Any) -> Dict[str, Any]:
        self._ensure_runtime_ready()
        matches = self.driver.find_elements(**kwargs)
        return {
            "elements": [item.as_dict() for item in matches],
            "count": len(matches),
            "selectorStats": self.driver.selector_metrics(),
        }

    def _run_action(
        self,
        *,
        action_type: str,
        runner,
        session_id: str | None = None,
        run_id: str | None = None,
        user_id: str = "anonymous",
        project_id: str | None = None,
        workspace_id: str | None = None,
        workspace_path: str | None = None,
        goal: str | None = None,
        action_payload: Optional[Dict[str, Any]] = None,
        invocation_metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        self._ensure_runtime_ready()
        invocation = self._classify_invocation(invocation_metadata, default_trigger_source="computer_use_api")
        normalized_payload = dict(action_payload or {})
        binding_decision = self._resolve_app_binding(
            explicit_app_id=normalized_payload.get("app_id") or normalized_payload.get("resolved_app_id"),
            window_title=normalized_payload.get("window_title"),
            class_name=normalized_payload.get("class_name"),
            app_name=normalized_payload.get("app_name") or normalized_payload.get("app"),
            include_running=True,
        )
        normalized_payload = self._attach_binding_to_payload(normalized_payload, binding_decision)
        normalized_payload, governance_feedback = self._apply_governance_feedback_patch(
            action_type=action_type,
            action_payload=normalized_payload,
        )
        normalized_payload, target_strategy = self._apply_target_strategy_patch(
            action_type=action_type,
            action_payload=normalized_payload,
        )
        normalized_payload, learned_interaction = self._apply_learned_interaction_patch(
            action_type=action_type,
            action_payload=normalized_payload,
        )
        run_handle = self.begin_or_attach_run(
            session_id=session_id,
            run_id=run_id,
            user_id=user_id,
            project_id=project_id,
            workspace_id=workspace_id,
            workspace_path=workspace_path,
            goal=goal or action_type,
            trigger_source=invocation.trigger_source,
            metadata={
                "computer_use_goal": goal or action_type,
                "invocation": invocation.as_dict(),
                **self._binding_metadata(binding_decision),
            },
        )
        try:
            self._preflight(
                run_handle=run_handle,
                goal=goal or action_type,
                context=build_preflight_context(
                    action_type=action_type,
                    goal=goal or action_type,
                    action_payload=normalized_payload,
                ),
            )
            self._raise_if_controlled(run_handle=run_handle)
        except RuntimeControlInterruption as exc:
            return self._build_controlled_result(
                run_handle=run_handle,
                signal=dict(exc.signal),
            )
        normalized_payload, window_context_patch = self._prepare_action_window_context(
            run_handle=run_handle,
            action_type=action_type,
            action_payload=normalized_payload,
        )
        primitive_live_feedback = primitive_live_feedback_for_action(action_type)
        normalized_payload = self._apply_live_matrix_budget_feedback(
            action_type=action_type,
            action_payload=normalized_payload,
            feedback=primitive_live_feedback,
        )
        app_id_for_action = self._infer_app_id_from_payloads(step=normalized_payload)
        requested_action = str(normalized_payload.get("profile_action") or action_type).strip().lower()
        high_risk_action = self._is_high_risk_action(app_id=app_id_for_action, action_name=requested_action)
        visual_guard_requested = self._should_run_visual_guard(
            action_type=action_type,
            action_payload=normalized_payload,
        )
        primitive_payload = resolve_computer_use_primitive(action_type, normalized_payload).as_dict()
        primitive_payload["supportsRpaPromotion"] = promotion_allowed_for_invocation(
            primitive_payload=primitive_payload,
            invocation=invocation,
        )
        step_budget = resolve_step_budget(
            action_type=action_type,
            action_payload=normalized_payload,
            high_risk_action=high_risk_action,
            visual_guard_requested=visual_guard_requested,
        )
        action_id = f"cua_{uuid.uuid4().hex[:10]}"
        run_handle.emit(
            "computer_use.action.started",
            {
                "actionId": action_id,
                "actionType": action_type,
                "payload": normalized_payload,
                "invocation": invocation.as_dict(),
                "binding": binding_decision.as_dict(),
                "templateGovernance": governance_feedback,
                "targetStrategy": target_strategy,
                "learnedInteraction": learned_interaction,
                "windowContextPatch": window_context_patch,
                "primitive": primitive_payload,
                "primitiveLiveBaseline": primitive_live_feedback,
                "budget": step_budget,
            },
        )
        runtime_action_safety = self._assess_runtime_action_safety(
            run_handle=run_handle,
            action_type=action_type,
            action_payload=normalized_payload,
        )
        with bind_runtime_context(**self._run_context(run_handle=run_handle)):
            window_binding_block = normalized_payload.pop("_window_binding_block", None)
            if governance_feedback is not None:
                run_handle.emit(
                    "computer_use.action.template_governance_applied",
                    {
                        "actionType": action_type,
                        "selectorKey": governance_feedback.get("selectorKey"),
                        "changes": governance_feedback.get("changes"),
                        "event": governance_feedback.get("event"),
                    },
                )
            if target_strategy is not None:
                run_handle.emit(
                    "computer_use.action.target_strategy_applied",
                    {
                        "actionType": action_type,
                        "selectorKey": target_strategy.get("selectorKey"),
                        "targetText": target_strategy.get("targetText"),
                        "changes": target_strategy.get("changes"),
                        "strategy": target_strategy.get("strategy"),
                    },
                )
            if learned_interaction is not None:
                run_handle.emit(
                    "computer_use.action.learned_interaction_applied",
                    {
                        "actionType": action_type,
                        "selectorKey": learned_interaction.get("selectorKey"),
                        "targetText": learned_interaction.get("targetText"),
                        "matches": learned_interaction.get("matches"),
                        "patch": learned_interaction.get("patch"),
                    },
                )
            transient_selector = bool(normalized_payload.get("transient_selector"))
            max_attempts = (
                2
                if action_type in {"click", "double_click", "right_click", "hover", "drag", "page_scroll", "type_text", "hotkey", "scroll"}
                or transient_selector
                or (visual_guard_requested and requested_action not in {"open_app"})
                else 1
            )
            if action_type in {"click", "double_click", "right_click", "hover"} and (
                self._has_explicit_visual_locator(normalized_payload)
                or normalized_payload.get("point_candidates")
                or normalized_payload.get("pointCandidates")
            ):
                max_attempts = max(max_attempts, 3)
            pre_action_guard_requested = self._should_run_pre_action_visual_guard(
                action_type=action_type,
                action_payload=normalized_payload,
            )
            capture_pre_action_observation = self._should_capture_pre_action_observation(
                action_type=action_type,
                action_payload=normalized_payload,
            )
            action_started_at = time.time()
            last_error: Exception | None = None
            result: ComputerUseActionResult | None = None
            action_side_effect_receipt = None
            before_observation = None
            before_observed: ComputerUseObservation | None = None
            for attempt_index in range(1, max_attempts + 1):
                try:
                    self._raise_if_controlled(run_handle=run_handle)
                    if attempt_index == 1 and isinstance(window_binding_block, dict):
                        reason = "执行前未能把动作绑定到预期应用窗口，已阻止继续操作。"
                        self._request_environment_interrupt(
                            run_handle=run_handle,
                            signal_kind="window_binding_unresolved",
                            summary=reason,
                            metadata={
                                "windowHandle": normalized_payload.get("window_handle"),
                                "bindingStatus": "window_context_binding_unresolved",
                                "pageIdentity": normalized_payload.get("window_title"),
                            },
                            cooldown_seconds=5.0,
                        )
                        verification = ComputerUseVerification(
                            passed=False,
                            status="window_binding_unresolved",
                            reason=reason,
                            details={"windowBinding": dict(window_binding_block)},
                            level="review_required",
                        )
                        target = {
                            "windowTitle": normalized_payload.get("window_title"),
                            "windowHandle": normalized_payload.get("window_handle"),
                            "appId": app_id_for_action,
                            "profileId": app_id_for_action,
                            "metadata": {"windowBinding": dict(window_binding_block)},
                        }
                        result = ComputerUseActionResult(
                            action_id=f"{action_type}_{uuid.uuid4().hex[:8]}",
                            action_type=action_type,
                            status="blocked",
                            message=reason,
                            target=target,
                            attempt_count=attempt_index,
                            metadata={
                                "windowBinding": dict(window_binding_block),
                                "updateRequest": {
                                    "requested": True,
                                    "kind": "ui_update_request",
                                    "reason": reason,
                                    "actionType": action_type,
                                    "profileAction": normalized_payload.get("profile_action") or action_type,
                                    "appId": app_id_for_action,
                                    "windowTitle": normalized_payload.get("window_title"),
                                    "windowHandle": normalized_payload.get("window_handle"),
                                    "selectorKey": normalized_payload.get("selector_key"),
                                    "targetText": self._action_target_text_hint(
                                        action_type=action_type,
                                        action_payload=normalized_payload,
                                    ),
                                    "windowBinding": dict(window_binding_block),
                                    "verification": verification.as_dict(),
                                },
                            },
                            verification=verification,
                        )
                        run_handle.emit(
                            "computer_use.action.blocked_by_window_binding",
                            {
                                "actionType": action_type,
                                "reason": reason,
                                "binding": window_binding_block,
                            },
                        )
                        break
                    if transient_selector:
                        self.driver.invalidate_window_cache(normalized_payload.get("window_handle"))
                        self.driver.invalidate_element_cache(normalized_payload.get("element_id"))
                    if capture_pre_action_observation or pre_action_guard_requested:
                        try:
                            before_observed = self.driver.observe_desktop(
                                window_title=normalized_payload.get("window_title"),
                                window_handle=normalized_payload.get("window_handle"),
                                depth_limit=3 if pre_action_guard_requested else 2,
                                element_limit=60 if pre_action_guard_requested else 40,
                                use_cache=False,
                            )
                            before_observation = before_observed.as_dict()
                        except Exception:
                            before_observed = None
                            before_observation = None
                    pre_action_scene = self._pre_action_scene_assessment(
                        action_type=action_type,
                        action_payload=normalized_payload,
                        observation=before_observation,
                        app_id=app_id_for_action,
                    )
                    if self._should_skip_for_already_in_target_state(
                        action_type=action_type,
                        scene_assessment=pre_action_scene,
                    ):
                        reason = "当前界面已处于目标状态，跳过重复动作。"
                        result = self._build_pre_action_scene_result(
                            action_type=action_type,
                            action_payload=normalized_payload,
                            status="completed",
                            message=reason,
                            verification=ComputerUseVerification(
                                passed=True,
                                status="already_in_target_state",
                                reason=reason,
                                details={"scene": dict(pre_action_scene or {})},
                                level="verified",
                            ),
                            scene_assessment=dict(pre_action_scene or {}),
                            observation=before_observed,
                            app_id=app_id_for_action,
                        )
                        run_handle.emit(
                            "computer_use.action.skipped_already_verified",
                            {
                                "actionType": action_type,
                                "reason": reason,
                                "scene": pre_action_scene,
                            },
                        )
                        break
                    if self._should_block_for_pre_action_scene(
                        action_type=action_type,
                        scene_assessment=pre_action_scene,
                    ):
                        blocker_state = str((pre_action_scene or {}).get("blockerState") or "unknown").strip()
                        reason = f"执行前检测到阻塞界面：{blocker_state}"
                        self._request_environment_interrupt(
                            run_handle=run_handle,
                            signal_kind="pre_action_blocker",
                            summary=reason,
                            metadata={
                                "blockerState": blocker_state,
                                "windowHandle": normalized_payload.get("window_handle"),
                                "pageIdentity": normalized_payload.get("window_title"),
                            },
                        )
                        result = self._build_pre_action_scene_result(
                            action_type=action_type,
                            action_payload=normalized_payload,
                            status="blocked",
                            message=reason,
                            verification=ComputerUseVerification(
                                passed=False,
                                status="pre_action_blocker_detected",
                                reason=reason,
                                details={"scene": dict(pre_action_scene or {})},
                                level="review_required",
                            ),
                            scene_assessment=dict(pre_action_scene or {}),
                            observation=before_observed,
                            app_id=app_id_for_action,
                        )
                        run_handle.emit(
                            "computer_use.action.blocked_by_scene",
                            {
                                "actionType": action_type,
                                "reason": reason,
                                "scene": pre_action_scene,
                            },
                        )
                        break
                    pre_action_visual_guard = None
                    if pre_action_guard_requested:
                        pre_observed = before_observed
                        pre_action_visual_guard = self._collect_visual_guard(
                            run_handle=run_handle,
                            stage="pre_action",
                            action=requested_action,
                            action_payload=normalized_payload,
                            workspace_path=workspace_path,
                            observation=before_observation,
                        )
                        if isinstance(pre_action_visual_guard, dict):
                            suggested_selector = dict(pre_action_visual_guard.get("suggestedSelector") or {})
                            if suggested_selector:
                                self._remember_selector_hint(
                                    step={
                                        **normalized_payload,
                                        "profile_action": requested_action,
                                    },
                                    target=suggested_selector,
                                    observation=before_observation,
                                    source=(
                                        "visual_guard_pre_confirmed"
                                        if pre_action_visual_guard.get("confirmed") is True
                                        else "visual_guard_pre_unconfirmed"
                                    ),
                                    reason=pre_action_visual_guard.get("reason") or requested_action,
                                    weight=64 if pre_action_visual_guard.get("confirmed") is True else 30,
                                )
                                patched_payload = self._apply_visual_guard_selector_patch(
                                    action_payload=normalized_payload,
                                    visual_guard=pre_action_visual_guard,
                                    observation=before_observation,
                                )
                                if patched_payload is not None:
                                    normalized_payload = patched_payload
                            if pre_action_visual_guard.get("status") != "analyzed" or pre_action_visual_guard.get("confirmed") is not True:
                                result = ComputerUseActionResult(
                                    action_id=f"{action_type}_{uuid.uuid4().hex[:8]}",
                                    action_type=action_type,
                                    status="blocked",
                                    message="高风险动作缺少执行前视觉确认，已阻止执行。",
                                    target={},
                                    metadata={
                                        "preActionVisualGuard": pre_action_visual_guard,
                                        "highRiskAction": high_risk_action,
                                    },
                                    attempt_count=attempt_index,
                                    verification=ComputerUseVerification(
                                        passed=False,
                                        status="high_risk_pre_action_confirmation_required",
                                        reason=str(
                                            pre_action_visual_guard.get("reason")
                                            or normalized_payload.get("visual_expectation")
                                            or "高风险动作在执行前未通过视觉保底确认。"
                                        ),
                                        details={"visualGuard": pre_action_visual_guard, "highRiskAction": high_risk_action},
                                        level="review_required",
                                    ),
                                    observation=pre_observed,
                                )
                                break
                    if high_risk_action:
                        action_side_effect_receipt = side_effect_idempotency_service.begin(
                            run_handle=run_handle,
                            effect_kind="computer_use.high_risk_action",
                            step_key=f"computer_use.{requested_action or action_type}",
                            target_identity=self._high_risk_action_target_identity(
                                action_type=action_type,
                                requested_action=requested_action,
                                action_payload=normalized_payload,
                                app_id=app_id_for_action,
                            ),
                            payload={
                                "actionType": action_type,
                                "requestedAction": requested_action,
                                "appId": app_id_for_action,
                                "windowHandle": normalized_payload.get("window_handle"),
                                "selectorKey": normalized_payload.get("selector_key"),
                                "targetText": normalized_payload.get("target_text") or normalized_payload.get("text"),
                                "point": normalized_payload.get("point"),
                            },
                            node="computer_use_runtime",
                            metadata={"actionType": action_type, "requestedAction": requested_action},
                        )
                        if not action_side_effect_receipt.execute:
                            result = ComputerUseActionResult(
                                action_id=f"{action_type}_{uuid.uuid4().hex[:8]}",
                                action_type=action_type,
                                status="completed",
                                message="高风险动作已按幂等键去重，跳过重复执行。",
                                target={
                                    "windowTitle": normalized_payload.get("window_title"),
                                    "windowHandle": normalized_payload.get("window_handle"),
                                    "appId": app_id_for_action,
                                    "selectorKey": normalized_payload.get("selector_key"),
                                },
                                metadata={"sideEffectReceipt": action_side_effect_receipt.as_dict()},
                                verification=ComputerUseVerification(
                                    passed=True,
                                    status="side_effect_deduplicated",
                                    reason="高风险动作已按幂等键去重。",
                                    details={"receipt": action_side_effect_receipt.as_dict()},
                                    level="verified",
                                ),
                            )
                            break
                    result = runner(run_handle, normalized_payload)
                    if high_risk_action and action_side_effect_receipt is not None:
                        side_effect_idempotency_service.complete(
                            run_handle=run_handle,
                            receipt=action_side_effect_receipt,
                            node="computer_use_runtime",
                            result={"status": result.status, "actionType": action_type, "actionId": result.action_id},
                        )
                    result.attempt_count = attempt_index
                    target_metadata = dict(result.target.get("metadata") or {}) if isinstance(result.target, dict) else {}
                    for metadata_key in (
                        "clipboardPayload",
                        "visualLocator",
                        "startVisualLocator",
                        "endVisualLocator",
                        "coordinateFallback",
                        "coordinateFallbackAvailable",
                        "coordinateSource",
                        "sendInputPreferred",
                        "pointCandidates",
                        "resolvedPointCandidates",
                        "pointRect",
                        "pointBias",
                        "pointBiases",
                        "spatialAnchor",
                        "observationWindow",
                        "focusHotkey",
                        "selectorFallbackError",
                    ):
                        if metadata_key in result.metadata:
                            continue
                        if metadata_key not in target_metadata:
                            continue
                        value = target_metadata.get(metadata_key)
                        if isinstance(value, dict):
                            result.metadata[metadata_key] = dict(value)
                        elif isinstance(value, list):
                            result.metadata[metadata_key] = list(value)
                        else:
                            result.metadata[metadata_key] = value
                    if pre_action_visual_guard is not None:
                        result.metadata["preActionVisualGuard"] = pre_action_visual_guard
                    post_observation = None
                    post_window_handle = (
                        result.target.get("windowHandle")
                        or result.target.get("window_handle")
                        or result.target.get("handle")
                        or normalized_payload.get("window_handle")
                    )
                    post_window_title = (
                        result.target.get("windowTitle")
                        or result.target.get("window_title")
                        or result.target.get("title")
                        or normalized_payload.get("window_title")
                    )
                    settle_observation = None
                    settle_details = None
                    try:
                        settle_observation, settle_details = self._wait_for_post_action_stability(
                            run_handle=run_handle,
                            action_type=action_type,
                            action_payload=normalized_payload,
                            window_title=post_window_title,
                            window_handle=post_window_handle,
                            app_id=(
                                result.target.get("appId")
                                or result.target.get("profileId")
                                or app_id_for_action
                            ),
                            high_risk_action=high_risk_action,
                            visual_guard_requested=visual_guard_requested,
                        )
                    except Exception as settle_exc:
                        settle_details = {
                            "status": "failed",
                            "error": str(settle_exc),
                        }
                    if isinstance(settle_details, dict):
                        result.metadata["stabilityWait"] = settle_details
                    try:
                        observed = settle_observation or self.driver.observe_desktop(
                            window_title=post_window_title,
                            window_handle=post_window_handle,
                            depth_limit=3,
                            element_limit=60,
                            use_cache=False,
                        )
                        observed_app_id = (
                            result.target.get("appId")
                            or result.target.get("profileId")
                            or app_id_for_action
                        )
                        if observed_app_id:
                            observed.metadata["appId"] = observed_app_id
                            observed.metadata["profileId"] = observed_app_id
                        result.observation = observed
                        post_observation = observed.as_dict()
                    except Exception:
                        result.observation = result.observation
                    observation_policy = self._observation_policy_config()
                    if bool(observation_policy.get("frameSequenceEnabled", True)):
                        result.metadata["observationBundle"] = build_observation_bundle(
                            action_type=action_type,
                            action_payload=normalized_payload,
                            route=str(
                                (dict(result.target.get("metadata") or {}) if isinstance(result.target, dict) else {}).get("route")
                                or ""
                            ).strip().lower() or None,
                            before_observation=before_observation,
                            mid_observation=(settle_observation.as_dict() if settle_observation is not None else None),
                            after_observation=post_observation,
                            desktop_live_context=self._desktop_live_observation_context(),
                        )
                    verification = self._verify_action_result(
                        action_type=action_type,
                        action_payload=normalized_payload,
                        result=result,
                        before_observation=before_observation,
                        after_observation=post_observation,
                    )
                    verification = self._merge_semantic_verification(
                        action_type=action_type,
                        verification=verification,
                        observation_bundle=(result.metadata.get("observationBundle") if isinstance(result.metadata.get("observationBundle"), dict) else None),
                        action_payload=normalized_payload,
                    )
                    if isinstance(result.metadata.get("observationBundle"), dict):
                        verification_details = dict(verification.details or {})
                        verification_details["observationBundle"] = dict(result.metadata.get("observationBundle") or {})
                        verification = ComputerUseVerification(
                            passed=verification.passed,
                            status=verification.status,
                            reason=verification.reason,
                            details=verification_details,
                            level=verification.level,
                        )
                    post_action_visual_locator = self._collect_post_action_visual_locator_check(
                        action_type=action_type,
                        action_payload=normalized_payload,
                        verification=verification,
                        observation_bundle=(result.metadata.get("observationBundle") if isinstance(result.metadata.get("observationBundle"), dict) else None),
                    )
                    if isinstance(post_action_visual_locator, dict):
                        result.metadata["postActionVisualLocator"] = dict(post_action_visual_locator)
                        verification = self._merge_post_action_visual_locator_verification(
                            action_type=action_type,
                            verification=verification,
                            post_action_visual_locator=post_action_visual_locator,
                        )
                    visual_guard = None
                    if post_observation and self._should_run_visual_guard(
                        action_type=action_type,
                        action_payload=normalized_payload,
                        target=result.target,
                        observation=post_observation,
                    ):
                        visual_guard_skip = self._post_action_visual_guard_skip_payload(
                            action_type=action_type,
                            action_payload=normalized_payload,
                            verification=verification,
                            post_action_visual_locator=post_action_visual_locator,
                            post_observation=post_observation,
                            post_window_title=post_window_title,
                            post_window_handle=post_window_handle,
                            app_id=(
                                result.target.get("appId")
                                or result.target.get("profileId")
                                or app_id_for_action
                            ),
                            high_risk_action=high_risk_action,
                        )
                        if isinstance(visual_guard_skip, dict):
                            result.metadata["visualGuardSkipped"] = visual_guard_skip
                            run_handle.emit(
                                "computer_use.action.visual_guard_skipped",
                                {
                                    "action": requested_action,
                                    "reason": visual_guard_skip.get("reason"),
                                    "mode": visual_guard_skip.get("mode"),
                                    "verificationStatus": verification.status,
                                    "verificationLevel": verification.level,
                                },
                            )
                        else:
                            guard_payload = dict(normalized_payload)
                            if guard_payload.get("window_handle") is None and post_window_handle is not None:
                                guard_payload["window_handle"] = post_window_handle
                            if not guard_payload.get("window_title") and post_window_title:
                                guard_payload["window_title"] = post_window_title
                            visual_guard = self._collect_visual_guard(
                                run_handle=run_handle,
                                stage="post_action",
                                action=requested_action,
                                action_payload=guard_payload,
                                workspace_path=workspace_path,
                                observation=post_observation,
                            )
                            if isinstance(visual_guard, dict):
                                result.metadata["visualGuard"] = visual_guard
                                if high_risk_action:
                                    result.metadata["highRiskAction"] = True
                                if self._is_visual_guard_desktop_capture_mismatch(visual_guard):
                                    wake_result = self._attempt_screen_wake_recovery(
                                        run_handle=run_handle,
                                        visual_guard=visual_guard,
                                        action=requested_action,
                                        window_title=guard_payload.get("window_title"),
                                        window_handle=guard_payload.get("window_handle"),
                                    )
                                    result.metadata["screenWakeRecovery"] = self._screen_wake_public_payload(wake_result)
                                    if wake_result.get("requiresHumanAttention"):
                                        visual_guard = {
                                            **dict(visual_guard),
                                            "status": "screen_wake_requires_human_attention",
                                            "confirmed": False,
                                            "reason": "Screen wake reached a login/credential boundary; human attention is required.",
                                            "screenWakeRecovery": self._screen_wake_public_payload(wake_result),
                                        }
                                        result.metadata["visualGuard"] = visual_guard
                                        verification = ComputerUseVerification(
                                            passed=False,
                                            status="screen_wake_requires_human_attention",
                                            reason=str(visual_guard.get("reason")),
                                            details={
                                                "visualGuard": visual_guard,
                                                "structuredVerification": verification.as_dict(),
                                            },
                                            level="review_required",
                                        )
                                    elif wake_result.get("attempted") and isinstance(wake_result.get("observation"), dict):
                                        wake_visual_guard = self._collect_visual_guard(
                                            run_handle=run_handle,
                                            stage="post_action",
                                            action=requested_action,
                                            action_payload=guard_payload,
                                            workspace_path=workspace_path,
                                            observation=dict(wake_result.get("observation") or {}),
                                        )
                                        if isinstance(wake_visual_guard, dict):
                                            wake_visual_guard = dict(wake_visual_guard)
                                            wake_visual_guard["screenWakeRetried"] = True
                                            wake_visual_guard["screenWakeRecovery"] = self._screen_wake_public_payload(wake_result)
                                            wake_visual_guard["previousReason"] = visual_guard.get("reason")
                                            visual_guard = wake_visual_guard
                                            result.metadata["visualGuard"] = visual_guard
                                suggested_selector = dict(visual_guard.get("suggestedSelector") or {})
                                if suggested_selector:
                                    self._remember_selector_hint(
                                        step={
                                            **normalized_payload,
                                            "profile_action": requested_action,
                                        },
                                        target=suggested_selector,
                                        observation=post_observation,
                                        source=(
                                            "visual_guard_post_confirmed"
                                            if visual_guard.get("confirmed") is True
                                            else "visual_guard_post_unconfirmed"
                                        ),
                                        reason=visual_guard.get("reason") or requested_action,
                                        weight=60 if visual_guard.get("confirmed") is True else 34,
                                    )
                                if visual_guard.get("status") == "analyzed" and visual_guard.get("confirmed") is False:
                                    if verification.passed and not high_risk_action and self._should_soften_visual_guard_conflict(
                                        visual_guard=visual_guard,
                                        verification=verification,
                                    ):
                                        details = dict(verification.details or {})
                                        details["visualGuard"] = visual_guard
                                        details["visualGuardOverride"] = {
                                            "mode": "desktop_capture_mismatch",
                                            "reason": visual_guard.get("reason"),
                                            "actionType": action_type,
                                        }
                                        verification = ComputerUseVerification(
                                            passed=verification.passed,
                                            status=verification.status,
                                            reason=verification.reason,
                                            details=details,
                                            level=verification.level,
                                        )
                                        run_handle.emit(
                                            "computer_use.action.visual_guard_softened",
                                            {
                                                "action": requested_action,
                                                "reason": visual_guard.get("reason"),
                                                "verificationStatus": verification.status,
                                                "verificationLevel": verification.level,
                                            },
                                        )
                                    else:
                                        verification = ComputerUseVerification(
                                            passed=False,
                                            status="visual_guard_unconfirmed",
                                            reason=str(
                                                visual_guard.get("reason")
                                                or normalized_payload.get("visual_expectation")
                                                or "视觉保底确认未通过。"
                                            ),
                                            details={
                                                "visualGuard": visual_guard,
                                                "structuredVerification": verification.as_dict(),
                                            },
                                            level="review_required",
                                        )
                                else:
                                    details = dict(verification.details or {})
                                    details["visualGuard"] = visual_guard
                                    verification = ComputerUseVerification(
                                        passed=verification.passed,
                                        status=verification.status,
                                        reason=verification.reason,
                                        details=details,
                                        level=verification.level,
                                    )
                                if high_risk_action and (
                                    visual_guard.get("status") != "analyzed" or visual_guard.get("confirmed") is not True
                                ):
                                    verification = ComputerUseVerification(
                                        passed=False,
                                        status="high_risk_visual_confirmation_required",
                                    reason=str(
                                        visual_guard.get("reason")
                                        or normalized_payload.get("visual_expectation")
                                        or "高风险动作缺少明确的视觉确认。"
                                    ),
                                    details={
                                        "visualGuard": visual_guard,
                                        "structuredVerification": verification.as_dict(),
                                        "highRiskAction": True,
                                    },
                                        level="review_required",
                                    )
                    result.verification = verification
                    update_request = self._build_update_request(
                        action_type=action_type,
                        action_payload=normalized_payload,
                        result=result,
                    )
                    elapsed_ms = int((time.time() - action_started_at) * 1000)
                    budget_usage = collect_budget_usage(
                        budget=step_budget,
                        result_metadata=result.metadata,
                        elapsed_ms=elapsed_ms,
                        attempts_used=max(1, int(result.attempt_count or attempt_index)),
                    )
                    budget_update_request = build_budget_update_request(
                        action_type=action_type,
                        budget=step_budget,
                        usage=budget_usage,
                        verification=verification.as_dict(),
                    )
                    if budget_update_request is not None:
                        if update_request is None:
                            update_request = budget_update_request
                        else:
                            update_request["budget"] = dict(step_budget)
                            update_request["budgetUsage"] = dict(budget_usage)
                            update_request["budgetExceeded"] = list(budget_usage.get("exceeded") or [])
                    scene_assessment = build_scene_assessment(
                        app_id=(
                            result.target.get("appId")
                            or result.target.get("profileId")
                            or app_id_for_action
                        ),
                        action_type=action_type,
                        action_payload=normalized_payload,
                        observation=post_observation,
                        target=result.target,
                        before_observation=before_observation,
                        verification=verification.as_dict(),
                        update_request=update_request,
                        visual_guard=visual_guard,
                    )
                    result.metadata["primitive"] = dict(primitive_payload)
                    result.metadata["budget"] = {
                        **dict(step_budget),
                        **dict(budget_usage),
                    }
                    result.metadata["scene"] = dict(scene_assessment)
                    if update_request is not None:
                        result.metadata["updateRequest"] = update_request
                        if result.status == "completed":
                            result.status = "update_requested"
                        run_handle.emit(
                            "computer_use.action.update_requested",
                            {
                                "actionType": action_type,
                                "reason": update_request.get("reason"),
                                "request": update_request,
                            },
                        )
                    if verification.passed or update_request is not None or attempt_index >= max_attempts:
                        break
                    retry_points = []
                    try:
                        retry_points = list((result.metadata or {}).get("resolvedPointCandidates") or [])
                    except Exception:
                        retry_points = []
                    if action_type in {"click", "double_click", "right_click", "hover"} and retry_points:
                        result.metadata["shortSequenceVerification"] = build_short_sequence_verification(
                            goal=str(normalized_payload.get("goal") or normalized_payload.get("target_text") or action_type),
                            candidate={"candidateId": f"candidate_{attempt_index}", "point": retry_points[min(attempt_index - 1, len(retry_points) - 1)]},
                            pre_state={"verification": "before_action"},
                            action={"actionType": action_type, "attempt": attempt_index},
                            post_state={"verificationStatus": verification.status, "passed": verification.passed},
                            expected_state_change=str(normalized_payload.get("post_action_expect_text") or normalized_payload.get("visual_expectation") or ""),
                        )
                        next_index = attempt_index
                        if next_index < len(retry_points):
                            next_point = retry_points[next_index]
                            if isinstance(next_point, list) and len(next_point) == 2:
                                normalized_payload["point"] = list(next_point)
                                for key in (
                                    "visual_locator",
                                    "VisualLocator",
                                    "visual_locator_scope",
                                    "VisualLocatorScope",
                                    "visual_locator_role_hint",
                                    "VisualLocatorRoleHint",
                                ):
                                    normalized_payload.pop(key, None)
                                run_handle.emit(
                                    "computer_use.visual_short_sequence.retry_next_candidate",
                                    {
                                        "attempt": attempt_index + 1,
                                        "previousStatus": verification.status,
                                        "nextPoint": list(next_point),
                                        "maxAttempts": max_attempts,
                                    },
                                )
                    self.driver.invalidate_window_cache(
                        normalized_payload.get("window_handle")
                        or result.target.get("windowHandle")
                        or result.target.get("window_handle")
                    )
                    self.driver.invalidate_element_cache(
                        result.target.get("elementId") or result.target.get("element_id")
                    )
                except DesktopDriverError as exc:
                    last_error = exc
                    if attempt_index >= max_attempts:
                        raise
                    self.driver.invalidate_window_cache(normalized_payload.get("window_handle"))
                    self.driver.invalidate_element_cache(normalized_payload.get("element_id"))
                    continue
                except RuntimeControlInterruption as exc:
                    return self._build_controlled_result(
                        run_handle=run_handle,
                        signal=dict(exc.signal),
                    )
            if result is None:
                raise last_error or DesktopDriverError("computer use 动作未生成结果。")
        final_elapsed_ms = int((time.time() - action_started_at) * 1000)
        if "primitive" not in result.metadata:
            result.metadata["primitive"] = dict(primitive_payload)
        if "budget" not in result.metadata:
            fallback_budget_usage = collect_budget_usage(
                budget=step_budget,
                result_metadata=result.metadata,
                elapsed_ms=final_elapsed_ms,
                attempts_used=max(1, int(result.attempt_count or 1)),
            )
            result.metadata["budget"] = {**dict(step_budget), **dict(fallback_budget_usage)}
        if "scene" not in result.metadata:
            result.metadata["scene"] = build_scene_assessment(
                app_id=(
                    result.target.get("appId")
                    or result.target.get("profileId")
                    or app_id_for_action
                ),
                action_type=action_type,
                action_payload=normalized_payload,
                observation=result.observation.as_dict() if result.observation else None,
                target=result.target,
                before_observation=before_observation,
                verification=self._normalize_verification(result.verification).as_dict(),
                update_request=dict(result.metadata.get("updateRequest") or {}) if isinstance(result.metadata, dict) else None,
                visual_guard=dict(result.metadata.get("visualGuard") or {}) if isinstance(result.metadata, dict) else None,
            )
        normalized_verification = self._normalize_verification(result.verification)
        normalized_verification = self._apply_live_matrix_verification_gate(
            feedback=primitive_live_feedback,
            verification=normalized_verification,
        )
        result.verification = normalized_verification
        if result.status in {"completed", "update_requested"} and normalized_verification.passed:
            self._resolve_environment_interrupt_if_pending(
                run_handle=run_handle,
                summary=f"owner runtime 已完成本地恢复并继续执行：{action_type}",
                metadata={
                    "actionType": action_type,
                    "windowHandle": result.target.get("windowHandle") or result.target.get("window_handle"),
                    "pageIdentity": result.target.get("windowTitle") or result.target.get("title"),
                },
            )
        environment_probe = self._collect_environment_probe_snapshot(action_payload=normalized_payload)
        if environment_probe:
            result.metadata["environmentProbe"] = dict(environment_probe)
        normalized_update_request = (
            dict(result.metadata.get("updateRequest") or {})
            if isinstance(result.metadata, dict) and isinstance(result.metadata.get("updateRequest"), dict)
            else None
        )
        scene_payload = dict(result.metadata.get("scene") or {}) if isinstance(result.metadata, dict) else {}
        execution_mode = self._execution_mode(
            action_payload=normalized_payload,
            scene=scene_payload,
        )
        result.metadata["executionMode"] = execution_mode
        result.metadata["executionRoute"] = self._resolve_execution_route(
            action_type=action_type,
            action_payload=normalized_payload,
            result=result,
            verification=normalized_verification,
            update_request=normalized_update_request,
        )
        if normalized_verification.status in {"screen_wake_requires_human_attention", "credential_boundary"}:
            result.metadata["recommendedNextAction"] = "ask_user"
            result.metadata["humanInputRequest"] = self._human_input_request_payload(
                reason=normalized_verification.status,
                target_url=normalized_payload.get("url") or normalized_payload.get("window_title"),
                browser_target=None,
            )
        if isinstance(result.metadata.get("observationBundle"), dict):
            result.metadata["observationBundle"]["route"] = result.metadata["executionRoute"].get("route")
        result_contract = build_result_contract(
            action_type=action_type,
            execution_mode=execution_mode,
            result=result,
            verification=normalized_verification,
            update_request=normalized_update_request,
            primitive_live_baseline=primitive_live_feedback,
        )
        result.metadata["blockedReason"] = result_contract["blockedReason"]
        result.metadata["recommendedNextAction"] = (
            "ask_user"
            if normalized_verification.status in {"screen_wake_requires_human_attention", "credential_boundary"}
            else result_contract["recommendedNextAction"]
        )
        result.metadata["evidenceSummary"] = result_contract["evidenceSummary"]
        result.metadata["runtimeControl"] = result_contract["runtimeControl"]
        result.metadata["learningLoop"] = result_contract["learningLoop"]
        result.metadata["invocation"] = invocation.as_dict()
        if runtime_action_safety.get("applied"):
            result.metadata["safetyDecision"] = dict(runtime_action_safety.get("decision") or {})
        result.metadata.update(build_action_policy_metadata(binding_decision=binding_decision, invocation=None))
        result.metadata["recoveryPolicy"] = build_recovery_policy_metadata(
            high_risk=high_risk_action,
            visual_fallback=dict(result.metadata.get("visualFallback") or {}) if isinstance(result.metadata, dict) else None,
            attempt_count=int(result.attempt_count or 1),
        )
        feedback_suggestions = build_feedback_suggestions(
            action_type=action_type,
            action_payload=normalized_payload,
            result=result,
            binding_decision=binding_decision,
            invocation=invocation,
        )
        if feedback_suggestions:
            result.metadata["feedbackSuggestions"] = feedback_suggestions
            run_handle.emit(
                "computer_use.feedback.generated",
                {
                    "actionType": action_type,
                    "feedback": feedback_suggestions,
                    "invocation": invocation.as_dict(),
                    "binding": binding_decision.as_dict(),
                },
            )
        if (
            result.status != "update_requested"
            and self._normalize_verification(result.verification).passed
            and not bool(invocation.compat_debug)
        ):
            self._remember_selector_hint(
                step=normalized_payload,
                target=result.target,
                observation=result.observation.as_dict() if result.observation else None,
                source="successful_action",
                reason=action_type,
                weight=20,
            )
            self._remember_target_strategy(
                action_type=action_type,
                action_payload=normalized_payload,
                result=result,
            )
            self._remember_learned_interaction(
                action_type=action_type,
                action_payload=normalized_payload,
                result=result,
            )
        result.metadata["selectorStats"] = self.driver.selector_metrics()
        if target_strategy is not None:
            result.metadata["targetStrategyApplied"] = dict(target_strategy)
        if governance_feedback is not None:
            result.metadata["templateGovernanceApplied"] = dict(governance_feedback)
        if learned_interaction is not None:
            result.metadata["learnedInteraction"] = dict(learned_interaction)
        snapshot = self._refresh_snapshot(
            run_handle=run_handle,
            observation=result.observation,
            action=result.as_dict(),
        )
        run_handle.emit("computer_use.action.completed", result.as_dict())
        safety_guardian.observe_post_action(
            action_family="computer_use_action",
            summary=f"已执行 computer use 动作：{action_type}",
            details=result.as_dict(),
            runtime_context=self._run_context(run_handle=run_handle),
        )
        try:
            self._record_trace_step(
                run_handle=run_handle,
                goal=goal or action_type,
                action_type=action_type,
                action_payload=normalized_payload,
                result=result,
                snapshot=snapshot,
                high_risk_action=high_risk_action,
                visual_guard_requested=visual_guard_requested,
                pre_action_guard_requested=pre_action_guard_requested,
                max_attempts=max_attempts,
                invocation=invocation,
                invocation_metadata=invocation_metadata,
                binding_decision=binding_decision,
            )
        except Exception as trace_exc:
            run_handle.emit(
                "computer_use.trace.record_failed",
                {
                    "actionType": action_type,
                    "error": str(trace_exc),
                },
            )
        return {
            "sessionId": run_handle.session_id,
            "runId": run_handle.run_id,
            "result": result.as_dict(),
            "snapshot": snapshot,
            "invocation": invocation.as_dict(),
            "binding": binding_decision.as_dict(),
        }

    def click(self, **kwargs: Any) -> Dict[str, Any]:
        payload = dict(kwargs)
        workspace_path = payload.pop("workspace_path", None)
        invocation_metadata = self._pop_invocation_metadata(payload)
        return self._run_action(
            action_type="click",
            session_id=payload.pop("session_id", None),
            run_id=payload.pop("run_id", None),
            user_id=payload.pop("user_id", "anonymous"),
            project_id=payload.pop("project_id", None),
            workspace_id=payload.pop("workspace_id", None),
            workspace_path=workspace_path,
            goal=payload.pop("goal", None),
            action_payload=payload,
            invocation_metadata=invocation_metadata,
            runner=lambda run_handle, runtime_payload: ComputerUseActionResult(
                action_id=f"click_{uuid.uuid4().hex[:8]}",
                action_type="click",
                status="completed",
                message="点击动作已完成。",
                target=self._click_target_from_payload(runtime_payload),
            ),
        )

    def right_click(self, **kwargs: Any) -> Dict[str, Any]:
        payload = dict(kwargs)
        workspace_path = payload.pop("workspace_path", None)
        invocation_metadata = self._pop_invocation_metadata(payload)
        return self._run_action(
            action_type="right_click",
            session_id=payload.pop("session_id", None),
            run_id=payload.pop("run_id", None),
            user_id=payload.pop("user_id", "anonymous"),
            project_id=payload.pop("project_id", None),
            workspace_id=payload.pop("workspace_id", None),
            workspace_path=workspace_path,
            goal=payload.pop("goal", None),
            action_payload=payload,
            invocation_metadata=invocation_metadata,
            runner=lambda run_handle, runtime_payload: ComputerUseActionResult(
                action_id=f"right_click_{uuid.uuid4().hex[:8]}",
                action_type="right_click",
                status="completed",
                message="右键动作已完成。",
                target=self._right_click_target_from_payload(runtime_payload),
            ),
        )

    def hover(self, **kwargs: Any) -> Dict[str, Any]:
        payload = dict(kwargs)
        workspace_path = payload.pop("workspace_path", None)
        invocation_metadata = self._pop_invocation_metadata(payload)
        return self._run_action(
            action_type="hover",
            session_id=payload.pop("session_id", None),
            run_id=payload.pop("run_id", None),
            user_id=payload.pop("user_id", "anonymous"),
            project_id=payload.pop("project_id", None),
            workspace_id=payload.pop("workspace_id", None),
            workspace_path=workspace_path,
            goal=payload.pop("goal", None),
            action_payload=payload,
            invocation_metadata=invocation_metadata,
            runner=lambda run_handle, runtime_payload: ComputerUseActionResult(
                action_id=f"hover_{uuid.uuid4().hex[:8]}",
                action_type="hover",
                status="completed",
                message="悬停动作已完成。",
                target=self._hover_target_from_payload(runtime_payload),
            ),
        )

    def drag(self, **kwargs: Any) -> Dict[str, Any]:
        payload = dict(kwargs)
        workspace_path = payload.pop("workspace_path", None)
        invocation_metadata = self._pop_invocation_metadata(payload)
        return self._run_action(
            action_type="drag",
            session_id=payload.pop("session_id", None),
            run_id=payload.pop("run_id", None),
            user_id=payload.pop("user_id", "anonymous"),
            project_id=payload.pop("project_id", None),
            workspace_id=payload.pop("workspace_id", None),
            workspace_path=workspace_path,
            goal=payload.pop("goal", None),
            action_payload=payload,
            invocation_metadata=invocation_metadata,
            runner=lambda run_handle, runtime_payload: ComputerUseActionResult(
                action_id=f"drag_{uuid.uuid4().hex[:8]}",
                action_type="drag",
                status="completed",
                message="拖拽动作已完成。",
                target=self._drag_target_from_payload(runtime_payload),
            ),
        )

    def type_text(self, **kwargs: Any) -> Dict[str, Any]:
        payload = dict(kwargs)
        workspace_path = payload.pop("workspace_path", None)
        invocation_metadata = self._pop_invocation_metadata(payload)
        payload_file_paths = list(payload.get("file_paths") or payload.get("attachment_paths") or [])
        if not payload_file_paths and payload.get("file_path"):
            payload_file_paths = [str(payload.get("file_path"))]
        return self._run_action(
            action_type="type_text",
            session_id=payload.pop("session_id", None),
            run_id=payload.pop("run_id", None),
            user_id=payload.pop("user_id", "anonymous"),
            project_id=payload.pop("project_id", None),
            workspace_id=payload.pop("workspace_id", None),
            workspace_path=workspace_path,
            goal=payload.pop("goal", None),
            action_payload=payload,
            invocation_metadata=invocation_metadata,
            runner=lambda run_handle, runtime_payload: ComputerUseActionResult(
                action_id=f"type_{uuid.uuid4().hex[:8]}",
                action_type="type_text",
                status="completed",
                message="文本输入已完成。",
                target=self._type_target_from_payload(runtime_payload),
                metadata={
                    "text": runtime_payload.get("text"),
                    "filePath": runtime_payload.get("file_path"),
                    "filePaths": list(runtime_payload.get("file_paths") or runtime_payload.get("attachment_paths") or payload_file_paths),
                },
            ),
        )

    def hotkey(self, **kwargs: Any) -> Dict[str, Any]:
        payload = dict(kwargs)
        workspace_path = payload.pop("workspace_path", None)
        invocation_metadata = self._pop_invocation_metadata(payload)
        return self._run_action(
            action_type="hotkey",
            session_id=payload.pop("session_id", None),
            run_id=payload.pop("run_id", None),
            user_id=payload.pop("user_id", "anonymous"),
            project_id=payload.pop("project_id", None),
            workspace_id=payload.pop("workspace_id", None),
            workspace_path=workspace_path,
            goal=payload.pop("goal", None),
            action_payload=payload,
            invocation_metadata=invocation_metadata,
            runner=lambda run_handle, runtime_payload: ComputerUseActionResult(
                action_id=f"hotkey_{uuid.uuid4().hex[:8]}",
                action_type="hotkey",
                status="completed",
                message="热键已发送。",
                target=self.driver.hotkey(
                    runtime_payload["sequence"],
                    window_title=runtime_payload.get("window_title"),
                    window_handle=runtime_payload.get("window_handle"),
                ),
            ),
        )

    def scroll(self, **kwargs: Any) -> Dict[str, Any]:
        payload = dict(kwargs)
        workspace_path = payload.pop("workspace_path", None)
        invocation_metadata = self._pop_invocation_metadata(payload)
        return self._run_action(
            action_type="scroll",
            session_id=payload.pop("session_id", None),
            run_id=payload.pop("run_id", None),
            user_id=payload.pop("user_id", "anonymous"),
            project_id=payload.pop("project_id", None),
            workspace_id=payload.pop("workspace_id", None),
            workspace_path=workspace_path,
            goal=payload.pop("goal", None),
            action_payload=payload,
            invocation_metadata=invocation_metadata,
            runner=lambda run_handle, runtime_payload: ComputerUseActionResult(
                action_id=f"scroll_{uuid.uuid4().hex[:8]}",
                action_type="scroll",
                status="completed",
                message="滚动动作已完成。",
                target=self._scroll_target_from_payload(runtime_payload),
            ),
        )

    def page_scroll(self, **kwargs: Any) -> Dict[str, Any]:
        payload = dict(kwargs)
        workspace_path = payload.pop("workspace_path", None)
        invocation_metadata = self._pop_invocation_metadata(payload)
        return self._run_action(
            action_type="page_scroll",
            session_id=payload.pop("session_id", None),
            run_id=payload.pop("run_id", None),
            user_id=payload.pop("user_id", "anonymous"),
            project_id=payload.pop("project_id", None),
            workspace_id=payload.pop("workspace_id", None),
            workspace_path=workspace_path,
            goal=payload.pop("goal", None),
            action_payload=payload,
            invocation_metadata=invocation_metadata,
            runner=lambda run_handle, runtime_payload: ComputerUseActionResult(
                action_id=f"page_scroll_{uuid.uuid4().hex[:8]}",
                action_type="page_scroll",
                status="completed",
                message="分页滚动已完成。",
                target=self._page_scroll_target_from_payload(runtime_payload),
            ),
        )

    def wait_for_element(self, **kwargs: Any) -> Dict[str, Any]:
        payload = dict(kwargs)
        workspace_path = payload.pop("workspace_path", None)
        invocation_metadata = self._pop_invocation_metadata(payload)
        return self._run_action(
            action_type="wait_for_element",
            session_id=payload.pop("session_id", None),
            run_id=payload.pop("run_id", None),
            user_id=payload.pop("user_id", "anonymous"),
            project_id=payload.pop("project_id", None),
            workspace_id=payload.pop("workspace_id", None),
            workspace_path=workspace_path,
            goal=payload.pop("goal", None),
            action_payload=payload,
            invocation_metadata=invocation_metadata,
            runner=lambda run_handle, runtime_payload: ComputerUseActionResult(
                action_id=f"wait_{uuid.uuid4().hex[:8]}",
                action_type="wait_for_element",
                status="completed",
                message="目标元素已出现。",
                target=self.driver.wait_for_element(
                    timeout_ms=int(runtime_payload.get("timeout_ms", 10000)),
                    poll_ms=int(runtime_payload.get("poll_ms", 300)),
                    element_id=runtime_payload.get("element_id"),
                    window_title=runtime_payload.get("window_title"),
                    window_handle=runtime_payload.get("window_handle"),
                    name=runtime_payload.get("name"),
                    name_contains=runtime_payload.get("name_contains"),
                    target_text=runtime_payload.get("target_text"),
                    automation_id=runtime_payload.get("automation_id"),
                    control_type=runtime_payload.get("control_type"),
                    class_name=runtime_payload.get("class_name"),
                ).as_dict(),
            ),
        )

    def capture_screenshot(self, **kwargs: Any) -> Dict[str, Any]:
        payload = dict(kwargs)
        workspace_path = payload.pop("workspace_path", None)
        invocation_metadata = self._pop_invocation_metadata(payload)

        def _runner(run_handle, runtime_payload):
            runtime_context = get_runtime_context()
            output_path, workspace_rel, preview_url, workspace_root = self._artifact_output_path(
                session_id=run_handle.session_id,
                run_id=run_handle.run_id,
                kind="capture",
                workspace_path=workspace_path,
            )
            capture = self.driver.capture_screenshot(
                output_path,
                window_title=runtime_payload.get("window_title"),
                window_handle=runtime_payload.get("window_handle"),
                element_id=runtime_payload.get("element_id"),
            )
            artifact = artifact_store.record_local_file(
                file_path=output_path,
                session_id=run_handle.session_id,
                run_id=run_handle.run_id,
                workspace_path=workspace_rel,
                preview_url=preview_url,
                metadata={
                    "runtime": "computer_use",
                    "origin": "computer_use_screenshot",
                    "capture": capture,
                    "capturedAt": utc_now_iso(),
                    "ephemeral": True,
                    "projectId": str(runtime_context.get("project_id") or "") or None,
                    "workspaceId": str(runtime_context.get("workspace_id") or "") or None,
                    "workspaceRoot": str(workspace_root),
                    "workspaceRelativePath": workspace_rel,
                    "storageClass": "workspace",
                    "surfaceVisible": True,
                    "canonicalPath": workspace_rel,
                    "pathPlane": "workspace_artifact",
                },
                source_component="computer_use_runtime",
                node="artifact_store",
            )
            return ComputerUseActionResult(
                action_id=f"shot_{uuid.uuid4().hex[:8]}",
                action_type="capture_screenshot",
                status="completed",
                message="截图已保存。",
                target=capture,
                artifact=artifact,
            )

        return self._run_action(
            action_type="capture_screenshot",
            session_id=payload.pop("session_id", None),
            run_id=payload.pop("run_id", None),
            user_id=payload.pop("user_id", "anonymous"),
            project_id=payload.pop("project_id", None),
            workspace_id=payload.pop("workspace_id", None),
            workspace_path=workspace_path,
            goal=payload.pop("goal", None),
            action_payload=payload,
            invocation_metadata=invocation_metadata,
            runner=_runner,
        )

    def _apply_observation_window_context(
        self,
        *,
        step: Dict[str, Any],
        last_observation: Dict[str, Any] | None,
    ) -> Dict[str, Any]:
        normalized = dict(step or {})
        if not last_observation:
            return normalized
        action = str(normalized.get("action") or "").strip().lower()
        if not self._step_uses_observation_context(action=action, step=normalized):
            return normalized
        latest_observation = last_observation.get("observation") or {}
        metadata = latest_observation.get("metadata") or {}
        step_app_id = self._infer_app_id_from_payloads(step=normalized)
        observed_app_id = self._infer_app_id(
            explicit_app_id=metadata.get("profileId") or metadata.get("appId"),
            window_title=latest_observation.get("windowTitle"),
            class_name=metadata.get("className"),
            app_name=latest_observation.get("app"),
        )
        if step_app_id and observed_app_id and step_app_id != observed_app_id:
            return normalized
        if normalized.get("window_handle") is None:
            window_handle = metadata.get("windowHandle")
            if window_handle is not None:
                normalized["window_handle"] = window_handle
        if not normalized.get("window_title") and latest_observation.get("windowTitle"):
            normalized["window_title"] = latest_observation["windowTitle"]
        if not normalized.get("app_id"):
            app_id = metadata.get("profileId")
            if app_id:
                normalized["app_id"] = app_id
        if not normalized.get("visual_expectation") and normalized.get("app_id"):
            default_expectation = self._visual_expectation(
                app_id=normalized.get("app_id"),
                action_name=normalized.get("action"),
            )
            if default_expectation:
                normalized["visual_expectation"] = default_expectation
        return normalized

    def _execute_plan_step(
        self,
        *,
        index: int,
        step: Dict[str, Any],
        base_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        action = str(step.get("action") or "").strip().lower()
        resolved_app_id = self._infer_app_id_from_payloads(step=step)
        selector_key = str(step.get("selector_key") or "").strip() or None

        def _merged_profile_step(default_fallbacks: List[str] | None = None) -> Dict[str, Any]:
            if not selector_key or not resolved_app_id:
                return dict(step)
            selector = self._resolve_profile_selector(
                app_id=resolved_app_id,
                selector_key=selector_key,
                fallbacks=list(default_fallbacks or []),
            )
            if not selector:
                return dict(step)
            return {
                **selector,
                **dict(step),
            }

        if action == "computer_use_playbook":
            selected_playbook = str(step.get("selectedPlaybook") or step.get("selected_playbook") or "").strip()
            if selected_playbook == "github.star_repository":
                desired = str(step.get("desiredState") or step.get("desired_state") or "starred")
                repo_goal_target = str(step.get("repoUrl") or step.get("repo_url") or step.get("repoOwner") or step.get("repo_owner") or "").strip()
                default_goal = (
                    f"去 GitHub 给 {repo_goal_target or '目标仓库'} 取消星标"
                    if self._normalize_github_star_desired_state(desired) == "not_starred"
                    else f"去 GitHub 给 {repo_goal_target or '目标仓库'} 点星标"
                )
                return self.execute_github_star_playbook(
                    goal=step.get("goal") or default_goal,
                    allow_real_click=bool(step.get("allowRealClick") if step.get("allowRealClick") is not None else step.get("allow_real_click")),
                    desired_state=desired,
                    session_id=base_context.get("session_id"),
                    run_id=base_context.get("run_id"),
                    user_id=str(base_context.get("user_id") or "anonymous"),
                    project_id=base_context.get("project_id"),
                    workspace_id=base_context.get("workspace_id"),
                    workspace_path=base_context.get("workspace_path"),
                    invocation_metadata={
                        "source": "computer_use_execute_plan",
                        "stepIndex": index,
                        "selectedPlaybook": selected_playbook,
                    },
                )
            raise DesktopDriverError(f"不支持的 ComputerUse playbook: {selected_playbook or '<missing>'}")

        if action == "observe":
            return self.observe(
                **base_context,
                goal=step.get("goal") or f"plan_step_{index}_observe",
                window_title=step.get("window_title"),
                window_handle=step.get("window_handle"),
                depth_limit=int(step.get("depth_limit", 4)),
                element_limit=int(step.get("element_limit", 80)),
                include_screenshot=bool(step.get("include_screenshot", True)),
            )
        if action == "find":
            return self.find_elements(
                window_title=step.get("window_title"),
                window_handle=step.get("window_handle"),
                name=step.get("name"),
                name_contains=step.get("name_contains"),
                target_text=step.get("target_text"),
                automation_id=step.get("automation_id"),
                control_type=step.get("control_type"),
                class_name=step.get("class_name"),
                depth_limit=int(step.get("depth_limit", 6)),
                limit=int(step.get("limit", 20)),
            )
        if action == "click":
            merged_step = _merged_profile_step()
            return self.click(
                **base_context,
                goal=step.get("goal") or f"plan_step_{index}_click",
                app_id=resolved_app_id,
                selector_key=selector_key,
                profile_action=step.get("profile_action") or action,
                transient_selector=self._is_transient_selector_key(app_id=resolved_app_id, selector_key=selector_key),
                visual_expectation=step.get("visual_expectation"),
                element_id=merged_step.get("element_id"),
                window_title=merged_step.get("window_title"),
                window_handle=merged_step.get("window_handle"),
                name=merged_step.get("name"),
                name_contains=merged_step.get("name_contains"),
                target_text=merged_step.get("target_text"),
                automation_id=merged_step.get("automation_id"),
                control_type=merged_step.get("control_type"),
                class_name=merged_step.get("class_name"),
                point=merged_step.get("point"),
                point_rect=merged_step.get("point_rect"),
                spatial_anchor=merged_step.get("spatial_anchor") or merged_step.get("spatialAnchor"),
                coordinate_source=merged_step.get("coordinate_source"),
                point_bias=merged_step.get("point_bias"),
                point_biases=merged_step.get("point_biases"),
                visual_locator=merged_step.get("visual_locator") or merged_step.get("visualLocator"),
                visual_locator_confidence=merged_step.get("visual_locator_confidence") or merged_step.get("visualLocatorConfidence"),
                visual_locator_timeout_ms=merged_step.get("visual_locator_timeout_ms") or merged_step.get("visualLocatorTimeoutMs"),
                visual_locator_multiple=bool(merged_step.get("visual_locator_multiple") or merged_step.get("visualLocatorMultiple")),
                visual_locator_read_text=bool(merged_step.get("visual_locator_read_text") or merged_step.get("visualLocatorReadText")),
                post_action_visual_locator=merged_step.get("post_action_visual_locator") or merged_step.get("postActionVisualLocator"),
                post_action_visual_locator_confidence=merged_step.get("post_action_visual_locator_confidence") or merged_step.get("postActionVisualLocatorConfidence"),
                post_action_visual_locator_timeout_ms=merged_step.get("post_action_visual_locator_timeout_ms") or merged_step.get("postActionVisualLocatorTimeoutMs"),
                post_action_visual_locator_multiple=bool(merged_step.get("post_action_visual_locator_multiple") or merged_step.get("postActionVisualLocatorMultiple")),
                post_action_visual_locator_read_text=bool(merged_step.get("post_action_visual_locator_read_text") or merged_step.get("postActionVisualLocatorReadText")),
                post_action_expect_text=merged_step.get("post_action_expect_text") or merged_step.get("postActionExpectText"),
                prefer_sendinput_click=bool(merged_step.get("prefer_sendinput_click", False)),
                post_action_settle_timeout_ms=merged_step.get("post_action_settle_timeout_ms"),
                post_action_settle_poll_ms=merged_step.get("post_action_settle_poll_ms"),
                post_action_stable_rounds=merged_step.get("post_action_stable_rounds"),
                abort_on_major_deviation=merged_step.get("abort_on_major_deviation"),
                double=bool(merged_step.get("double", False)),
            )
        if action == "double_click":
            merged_step = _merged_profile_step()
            return self.click(
                **base_context,
                goal=step.get("goal") or f"plan_step_{index}_double_click",
                app_id=resolved_app_id,
                selector_key=selector_key,
                profile_action=step.get("profile_action") or action,
                transient_selector=self._is_transient_selector_key(app_id=resolved_app_id, selector_key=selector_key),
                visual_expectation=step.get("visual_expectation"),
                element_id=merged_step.get("element_id"),
                window_title=merged_step.get("window_title"),
                window_handle=merged_step.get("window_handle"),
                name=merged_step.get("name"),
                name_contains=merged_step.get("name_contains"),
                target_text=merged_step.get("target_text"),
                automation_id=merged_step.get("automation_id"),
                control_type=merged_step.get("control_type"),
                class_name=merged_step.get("class_name"),
                point=merged_step.get("point"),
                point_rect=merged_step.get("point_rect"),
                spatial_anchor=merged_step.get("spatial_anchor") or merged_step.get("spatialAnchor"),
                coordinate_source=merged_step.get("coordinate_source"),
                point_bias=merged_step.get("point_bias"),
                point_biases=merged_step.get("point_biases"),
                visual_locator=merged_step.get("visual_locator") or merged_step.get("visualLocator"),
                visual_locator_confidence=merged_step.get("visual_locator_confidence") or merged_step.get("visualLocatorConfidence"),
                visual_locator_timeout_ms=merged_step.get("visual_locator_timeout_ms") or merged_step.get("visualLocatorTimeoutMs"),
                visual_locator_multiple=bool(merged_step.get("visual_locator_multiple") or merged_step.get("visualLocatorMultiple")),
                visual_locator_read_text=bool(merged_step.get("visual_locator_read_text") or merged_step.get("visualLocatorReadText")),
                post_action_visual_locator=merged_step.get("post_action_visual_locator") or merged_step.get("postActionVisualLocator"),
                post_action_visual_locator_confidence=merged_step.get("post_action_visual_locator_confidence") or merged_step.get("postActionVisualLocatorConfidence"),
                post_action_visual_locator_timeout_ms=merged_step.get("post_action_visual_locator_timeout_ms") or merged_step.get("postActionVisualLocatorTimeoutMs"),
                post_action_visual_locator_multiple=bool(merged_step.get("post_action_visual_locator_multiple") or merged_step.get("postActionVisualLocatorMultiple")),
                post_action_visual_locator_read_text=bool(merged_step.get("post_action_visual_locator_read_text") or merged_step.get("postActionVisualLocatorReadText")),
                post_action_expect_text=merged_step.get("post_action_expect_text") or merged_step.get("postActionExpectText"),
                prefer_sendinput_click=bool(merged_step.get("prefer_sendinput_click", False)),
                post_action_settle_timeout_ms=merged_step.get("post_action_settle_timeout_ms"),
                post_action_settle_poll_ms=merged_step.get("post_action_settle_poll_ms"),
                post_action_stable_rounds=merged_step.get("post_action_stable_rounds"),
                abort_on_major_deviation=merged_step.get("abort_on_major_deviation"),
                double=True,
            )
        if action == "right_click":
            merged_step = _merged_profile_step()
            return self.right_click(
                **base_context,
                goal=step.get("goal") or f"plan_step_{index}_right_click",
                app_id=resolved_app_id,
                selector_key=selector_key,
                profile_action=step.get("profile_action") or action,
                transient_selector=self._is_transient_selector_key(app_id=resolved_app_id, selector_key=selector_key),
                visual_expectation=step.get("visual_expectation"),
                element_id=merged_step.get("element_id"),
                window_title=merged_step.get("window_title"),
                window_handle=merged_step.get("window_handle"),
                name=merged_step.get("name"),
                name_contains=merged_step.get("name_contains"),
                target_text=merged_step.get("target_text"),
                automation_id=merged_step.get("automation_id"),
                control_type=merged_step.get("control_type"),
                class_name=merged_step.get("class_name"),
                point=merged_step.get("point"),
                point_candidates=merged_step.get("point_candidates") or merged_step.get("pointCandidates"),
                point_rect=merged_step.get("point_rect"),
                spatial_anchor=merged_step.get("spatial_anchor") or merged_step.get("spatialAnchor"),
                coordinate_source=merged_step.get("coordinate_source"),
                visual_locator=merged_step.get("visual_locator") or merged_step.get("visualLocator"),
                visual_locator_confidence=merged_step.get("visual_locator_confidence") or merged_step.get("visualLocatorConfidence"),
                visual_locator_timeout_ms=merged_step.get("visual_locator_timeout_ms") or merged_step.get("visualLocatorTimeoutMs"),
                visual_locator_multiple=bool(merged_step.get("visual_locator_multiple") or merged_step.get("visualLocatorMultiple")),
                visual_locator_read_text=bool(merged_step.get("visual_locator_read_text") or merged_step.get("visualLocatorReadText")),
                post_action_visual_locator=merged_step.get("post_action_visual_locator") or merged_step.get("postActionVisualLocator"),
                post_action_visual_locator_confidence=merged_step.get("post_action_visual_locator_confidence") or merged_step.get("postActionVisualLocatorConfidence"),
                post_action_visual_locator_timeout_ms=merged_step.get("post_action_visual_locator_timeout_ms") or merged_step.get("postActionVisualLocatorTimeoutMs"),
                post_action_visual_locator_multiple=bool(merged_step.get("post_action_visual_locator_multiple") or merged_step.get("postActionVisualLocatorMultiple")),
                post_action_visual_locator_read_text=bool(merged_step.get("post_action_visual_locator_read_text") or merged_step.get("postActionVisualLocatorReadText")),
                post_action_expect_text=merged_step.get("post_action_expect_text") or merged_step.get("postActionExpectText"),
                abort_on_major_deviation=merged_step.get("abort_on_major_deviation"),
            )
        if action == "hover":
            merged_step = _merged_profile_step()
            return self.hover(
                **base_context,
                goal=step.get("goal") or f"plan_step_{index}_hover",
                app_id=resolved_app_id,
                selector_key=selector_key,
                profile_action=step.get("profile_action") or action,
                visual_expectation=step.get("visual_expectation"),
                point=merged_step.get("point"),
                point_candidates=merged_step.get("point_candidates") or merged_step.get("pointCandidates"),
                point_rect=merged_step.get("point_rect"),
                spatial_anchor=merged_step.get("spatial_anchor") or merged_step.get("spatialAnchor"),
                coordinate_source=merged_step.get("coordinate_source"),
                visual_locator=merged_step.get("visual_locator") or merged_step.get("visualLocator"),
                visual_locator_confidence=merged_step.get("visual_locator_confidence") or merged_step.get("visualLocatorConfidence"),
                visual_locator_timeout_ms=merged_step.get("visual_locator_timeout_ms") or merged_step.get("visualLocatorTimeoutMs"),
                visual_locator_multiple=bool(merged_step.get("visual_locator_multiple") or merged_step.get("visualLocatorMultiple")),
                visual_locator_read_text=bool(merged_step.get("visual_locator_read_text") or merged_step.get("visualLocatorReadText")),
                post_action_visual_locator=merged_step.get("post_action_visual_locator") or merged_step.get("postActionVisualLocator"),
                post_action_visual_locator_confidence=merged_step.get("post_action_visual_locator_confidence") or merged_step.get("postActionVisualLocatorConfidence"),
                post_action_visual_locator_timeout_ms=merged_step.get("post_action_visual_locator_timeout_ms") or merged_step.get("postActionVisualLocatorTimeoutMs"),
                post_action_visual_locator_multiple=bool(merged_step.get("post_action_visual_locator_multiple") or merged_step.get("postActionVisualLocatorMultiple")),
                post_action_visual_locator_read_text=bool(merged_step.get("post_action_visual_locator_read_text") or merged_step.get("postActionVisualLocatorReadText")),
                post_action_expect_text=merged_step.get("post_action_expect_text") or merged_step.get("postActionExpectText"),
                window_title=merged_step.get("window_title"),
                window_handle=merged_step.get("window_handle"),
                abort_on_major_deviation=merged_step.get("abort_on_major_deviation"),
            )
        if action == "drag":
            return self.drag(
                **base_context,
                goal=step.get("goal") or f"plan_step_{index}_drag",
                app_id=resolved_app_id,
                profile_action=step.get("profile_action") or action,
                visual_expectation=step.get("visual_expectation"),
                start_point=step.get("start_point") or step.get("point") or step.get("from_point"),
                end_point=step.get("end_point") or step.get("to_point"),
                drag_steps=step.get("drag_steps"),
                window_title=step.get("window_title"),
                window_handle=step.get("window_handle"),
                abort_on_major_deviation=step.get("abort_on_major_deviation"),
            )
        if action == "type_text":
            merged_step = _merged_profile_step()
            return self.type_text(
                **base_context,
                goal=step.get("goal") or f"plan_step_{index}_type_text",
                app_id=resolved_app_id,
                selector_key=selector_key,
                profile_action=step.get("profile_action") or action,
                transient_selector=self._is_transient_selector_key(app_id=resolved_app_id, selector_key=selector_key),
                visual_expectation=step.get("visual_expectation"),
                text=str(merged_step.get("text") or ""),
                file_path=merged_step.get("file_path"),
                file_paths=merged_step.get("file_paths"),
                attachment_paths=merged_step.get("attachment_paths"),
                element_id=merged_step.get("element_id"),
                window_title=merged_step.get("window_title"),
                window_handle=merged_step.get("window_handle"),
                name=merged_step.get("name"),
                target_text=merged_step.get("target_text"),
                automation_id=merged_step.get("automation_id"),
                control_type=merged_step.get("control_type"),
                class_name=merged_step.get("class_name"),
                point=merged_step.get("point"),
                point_rect=merged_step.get("point_rect"),
                spatial_anchor=merged_step.get("spatial_anchor") or merged_step.get("spatialAnchor"),
                coordinate_source=merged_step.get("coordinate_source"),
                point_bias=merged_step.get("point_bias"),
                point_biases=merged_step.get("point_biases"),
                point_candidates=merged_step.get("point_candidates") or merged_step.get("pointCandidates"),
                visual_locator=merged_step.get("visual_locator") or merged_step.get("visualLocator"),
                visual_locator_confidence=merged_step.get("visual_locator_confidence") or merged_step.get("visualLocatorConfidence"),
                visual_locator_timeout_ms=merged_step.get("visual_locator_timeout_ms") or merged_step.get("visualLocatorTimeoutMs"),
                visual_locator_multiple=bool(merged_step.get("visual_locator_multiple") or merged_step.get("visualLocatorMultiple")),
                visual_locator_read_text=bool(merged_step.get("visual_locator_read_text") or merged_step.get("visualLocatorReadText")),
                post_action_visual_locator=merged_step.get("post_action_visual_locator") or merged_step.get("postActionVisualLocator"),
                post_action_visual_locator_confidence=merged_step.get("post_action_visual_locator_confidence") or merged_step.get("postActionVisualLocatorConfidence"),
                post_action_visual_locator_timeout_ms=merged_step.get("post_action_visual_locator_timeout_ms") or merged_step.get("postActionVisualLocatorTimeoutMs"),
                post_action_visual_locator_multiple=bool(merged_step.get("post_action_visual_locator_multiple") or merged_step.get("postActionVisualLocatorMultiple")),
                post_action_visual_locator_read_text=bool(merged_step.get("post_action_visual_locator_read_text") or merged_step.get("postActionVisualLocatorReadText")),
                post_action_expect_text=merged_step.get("post_action_expect_text") or merged_step.get("postActionExpectText"),
                prefer_sendinput_click=bool(merged_step.get("prefer_sendinput_click", False)),
                prefer_sendinput_text=bool(
                    merged_step.get("prefer_sendinput_text")
                    if merged_step.get("prefer_sendinput_text") is not None
                    else merged_step.get("preferSendInputText")
                ),
                focus_hotkey_sequence=merged_step.get("focus_hotkey_sequence") or merged_step.get("focusHotkeySequence"),
                window_typing_focus_mode=merged_step.get("window_typing_focus_mode") or merged_step.get("windowTypingFocusMode"),
                file_paste_strategy=merged_step.get("file_paste_strategy") or merged_step.get("filePasteStrategy"),
                window_typing=bool(merged_step.get("window_typing", False)),
                clear_first=bool(merged_step.get("clear_first", False)),
                press_enter=bool(merged_step.get("press_enter", False)),
                post_action_settle_timeout_ms=merged_step.get("post_action_settle_timeout_ms"),
                post_action_settle_poll_ms=merged_step.get("post_action_settle_poll_ms"),
                post_action_stable_rounds=merged_step.get("post_action_stable_rounds"),
                abort_on_major_deviation=merged_step.get("abort_on_major_deviation"),
            )
        if action == "open_app":
            return self.open_app(
                **base_context,
                goal=step.get("goal") or f"plan_step_{index}_open_app",
                app_id=step.get("app_id"),
                app_name=step.get("app_name") or step.get("app"),
                command=step.get("command"),
                window_title=step.get("window_title"),
                window_title_candidates=list(step.get("window_title_candidates") or []),
                class_name=step.get("class_name"),
                wait_timeout_ms=int(step.get("wait_timeout_ms", 12000)),
                poll_ms=int(step.get("poll_ms", 250)),
                require_visual_guard=step.get("require_visual_guard"),
                post_action_settle_timeout_ms=step.get("post_action_settle_timeout_ms"),
                post_action_settle_poll_ms=step.get("post_action_settle_poll_ms"),
                post_action_stable_rounds=step.get("post_action_stable_rounds"),
                abort_on_major_deviation=step.get("abort_on_major_deviation"),
            )
        if action == "focus_window":
            return self.focus_window(
                **base_context,
                goal=step.get("goal") or f"plan_step_{index}_focus_window",
                app_id=step.get("app_id"),
                app_name=step.get("app_name") or step.get("app"),
                window_title=step.get("window_title"),
                window_title_candidates=list(step.get("window_title_candidates") or []),
                window_handle=step.get("window_handle"),
                class_name=step.get("class_name"),
                require_visual_guard=step.get("require_visual_guard"),
                post_action_settle_timeout_ms=step.get("post_action_settle_timeout_ms"),
                post_action_settle_poll_ms=step.get("post_action_settle_poll_ms"),
                post_action_stable_rounds=step.get("post_action_stable_rounds"),
                abort_on_major_deviation=step.get("abort_on_major_deviation"),
            )
        if action == "find_and_type":
            return self.find_and_type(
                **base_context,
                goal=step.get("goal") or f"plan_step_{index}_find_and_type",
                app_id=step.get("app_id"),
                selector_key=step.get("selector_key"),
                window_title=step.get("window_title"),
                window_handle=step.get("window_handle"),
                name=step.get("name"),
                name_contains=step.get("name_contains"),
                automation_id=step.get("automation_id"),
                control_type=step.get("control_type"),
                class_name=step.get("class_name"),
                text=str(step.get("text") or ""),
                clear_first=bool(step.get("clear_first", False)),
                press_enter=bool(step.get("press_enter", False)),
                target_text=step.get("target_text"),
                visual_expectation=step.get("visual_expectation"),
                post_action_settle_timeout_ms=step.get("post_action_settle_timeout_ms"),
                post_action_settle_poll_ms=step.get("post_action_settle_poll_ms"),
                post_action_stable_rounds=step.get("post_action_stable_rounds"),
                abort_on_major_deviation=step.get("abort_on_major_deviation"),
            )
        if action == "scroll_list":
            return self.scroll_list(
                **base_context,
                goal=step.get("goal") or f"plan_step_{index}_scroll_list",
                app_id=step.get("app_id"),
                selector_key=step.get("selector_key"),
                window_title=step.get("window_title"),
                window_handle=step.get("window_handle"),
                name=step.get("name"),
                name_contains=step.get("name_contains"),
                automation_id=step.get("automation_id"),
                control_type=step.get("control_type"),
                class_name=step.get("class_name"),
                amount=int(step.get("amount", 0)),
                visual_expectation=step.get("visual_expectation"),
                post_action_settle_timeout_ms=step.get("post_action_settle_timeout_ms"),
                post_action_settle_poll_ms=step.get("post_action_settle_poll_ms"),
                post_action_stable_rounds=step.get("post_action_stable_rounds"),
            )
        if action == "click_toolbar_action":
            return self.click_toolbar_action(
                **base_context,
                goal=step.get("goal") or f"plan_step_{index}_click_toolbar_action",
                app_id=step.get("app_id"),
                action_name=step.get("action_name"),
                window_title=step.get("window_title"),
                window_handle=step.get("window_handle"),
                class_name=step.get("class_name"),
                visual_expectation=step.get("visual_expectation"),
                target_text=step.get("target_text"),
                post_action_settle_timeout_ms=step.get("post_action_settle_timeout_ms"),
                post_action_settle_poll_ms=step.get("post_action_settle_poll_ms"),
                post_action_stable_rounds=step.get("post_action_stable_rounds"),
                abort_on_major_deviation=step.get("abort_on_major_deviation"),
            )
        if action == "hotkey":
            return self.hotkey(
                **base_context,
                goal=step.get("goal") or f"plan_step_{index}_hotkey",
                sequence=str(step.get("sequence") or ""),
                window_title=step.get("window_title"),
                window_handle=step.get("window_handle"),
            )
        if action == "scroll":
            return self.scroll(
                **base_context,
                goal=step.get("goal") or f"plan_step_{index}_scroll",
                amount=int(step.get("amount", 0)),
                element_id=step.get("element_id"),
                window_title=step.get("window_title"),
                window_handle=step.get("window_handle"),
            )
        if action == "page_scroll":
            return self.page_scroll(
                **base_context,
                goal=step.get("goal") or f"plan_step_{index}_page_scroll",
                direction=step.get("direction") or "down",
                count=int(step.get("count", 1)),
                window_title=step.get("window_title"),
                window_handle=step.get("window_handle"),
            )
        if action == "wait":
            return self.wait_for_element(
                **base_context,
                goal=step.get("goal") or f"plan_step_{index}_wait",
                element_id=step.get("element_id"),
                window_title=step.get("window_title"),
                window_handle=step.get("window_handle"),
                name=step.get("name"),
                name_contains=step.get("name_contains"),
                target_text=step.get("target_text"),
                automation_id=step.get("automation_id"),
                control_type=step.get("control_type"),
                class_name=step.get("class_name"),
                timeout_ms=int(step.get("timeout_ms", 10000)),
                poll_ms=int(step.get("poll_ms", 300)),
            )
        if action == "screenshot":
            return self.capture_screenshot(
                **base_context,
                goal=step.get("goal") or f"plan_step_{index}_screenshot",
                element_id=step.get("element_id"),
                window_title=step.get("window_title"),
                window_handle=step.get("window_handle"),
            )
        raise DesktopDriverError(f"不支持的 computer use 步骤类型：{action}")

    def _find_observation_element(
        self,
        observation: Dict[str, Any] | None,
        *,
        element_id: str | None = None,
        role: str | None = None,
    ) -> Dict[str, Any] | None:
        if not isinstance(observation, dict):
            return None
        elements = list(observation.get("elements") or [])
        if element_id:
            for element in elements:
                if isinstance(element, dict) and element.get("elementId") == element_id:
                    return element
        focused_id = observation.get("focusedElementId")
        if focused_id:
            for element in elements:
                if isinstance(element, dict) and element.get("elementId") == focused_id:
                    if role is None or str(element.get("role") or "").lower() == role.lower():
                        return element
        if role:
            for element in elements:
                if isinstance(element, dict) and str(element.get("role") or "").lower() == role.lower():
                    return element
        return None

    def _recover_plan_step(
        self,
        *,
        run_handle,
        index: int,
        action: str,
        step: Dict[str, Any],
        last_observation: Dict[str, Any] | None,
        error: Exception,
    ) -> tuple[Dict[str, Any], Dict[str, Any] | None, Dict[str, Any]]:
        prepared_step = self._apply_observation_window_context(step=step, last_observation=last_observation)
        run_handle.emit(
            "computer_use.step.recovery_started",
            {
                "index": index,
                "action": action,
                "reason": str(error),
                "payload": prepared_step,
            },
        )
        observed = self.driver.observe_desktop(
            window_title=prepared_step.get("window_title"),
            window_handle=prepared_step.get("window_handle"),
            depth_limit=4,
            element_limit=80,
            use_cache=False,
        )
        snapshot = self._refresh_snapshot(run_handle=run_handle, observation=observed)
        observation_payload = {
            "observation": observed.as_dict(),
            "snapshot": snapshot,
        }
        recovered_step = dict(prepared_step)
        selector_source = self._find_observation_element(
            observation_payload["observation"],
            element_id=recovered_step.get("element_id"),
            role="Edit" if action == "type_text" else None,
        ) or self._find_observation_element(
            (last_observation or {}).get("observation") or {},
            element_id=recovered_step.get("element_id"),
            role="Edit" if action == "type_text" else None,
        )

        selectors_refreshed = False
        if selector_source:
            for source_key, target_key in (
                ("name", "name"),
                ("automationId", "automation_id"),
                ("role", "control_type"),
                ("className", "class_name"),
            ):
                if not recovered_step.get(target_key) and selector_source.get(source_key):
                    recovered_step[target_key] = selector_source[source_key]
                    selectors_refreshed = True
            if selectors_refreshed:
                recovered_step.pop("element_id", None)
                self._remember_selector_hint(
                    step=recovered_step,
                    observation=observation_payload["observation"],
                    source="recovery_refresh",
                    reason=str(error),
                    weight=26,
                )

        resolved_app_id = self._infer_app_id_from_payloads(
            step=recovered_step,
            observation=observation_payload["observation"],
        )
        profile_selector_key = None
        recovered_from_profile, profile_selector_key = self._apply_profile_recovery_selector(
            app_id=resolved_app_id,
            step=recovered_step,
            action=action,
        )
        if recovered_from_profile != recovered_step:
            recovered_step = recovered_from_profile
            selectors_refreshed = True
            self._remember_selector_hint(
                step=recovered_step,
                observation=observation_payload["observation"],
                source="recovery_profile_selector",
                reason=profile_selector_key or str(error),
                weight=34,
            )

        metadata = observation_payload["observation"].get("metadata") or {}
        if recovered_step.get("window_handle") is None and metadata.get("windowHandle") is not None:
            recovered_step["window_handle"] = metadata["windowHandle"]
        if not recovered_step.get("window_title") and observation_payload["observation"].get("windowTitle"):
            recovered_step["window_title"] = observation_payload["observation"]["windowTitle"]

        recovery = {
            "performed": True,
            "reason": str(error),
            "selectorsRefreshed": selectors_refreshed,
            "windowHandle": recovered_step.get("window_handle"),
            "windowTitle": recovered_step.get("window_title"),
            "snapshotId": observation_payload["observation"].get("snapshotId"),
            "profileSelectorKey": profile_selector_key,
            "appId": resolved_app_id,
        }
        run_handle.emit(
            "computer_use.step.recovery_completed",
            {
                "index": index,
                "action": action,
                "recovery": recovery,
                "payload": recovered_step,
            },
        )
        return recovered_step, observation_payload, recovery

    def execute_plan(
        self,
        *,
        steps: List[Dict[str, Any]],
        session_id: str | None = None,
        run_id: str | None = None,
        user_id: str = "anonymous",
        project_id: str | None = None,
        workspace_id: str | None = None,
        workspace_path: str | None = None,
        goal: str | None = None,
        continue_on_error: bool = False,
        max_steps: int = 5,
    ) -> Dict[str, Any]:
        self._ensure_runtime_ready()
        if not steps:
            raise DesktopDriverError("执行计划不能为空。")
        if len(steps) > max_steps:
            raise DesktopDriverError(f"单次短视距执行计划最多支持 {max_steps} 步。")
        invocation = self._classify_invocation(default_trigger_source="computer_use_api")

        run_handle = self.begin_or_attach_run(
            session_id=session_id,
            run_id=run_id,
            user_id=user_id,
            project_id=project_id,
            workspace_id=workspace_id,
            workspace_path=workspace_path,
            goal=goal or "computer_use_execute_plan",
            trigger_source=invocation.trigger_source,
            metadata={
                "computer_use_goal": goal or "computer_use_execute_plan",
                "invocation": invocation.as_dict(),
            },
        )
        workflow_ledger_service.activate_runtime_step(
            run_handle.run_id,
            owner_runtime="computer_use",
            step_key="computer_use.execute_plan",
            title="ComputerUse 执行计划",
            owner_agent_id="computer_use_runtime",
            input_payload={
                "goal": goal or "computer_use_execute_plan",
                "stepCount": len(steps),
                "continueOnError": continue_on_error,
            },
        )
        self._preflight(
            run_handle=run_handle,
            goal=goal or "computer_use_execute_plan",
            context=build_preflight_context(
                action_type="execute_plan",
                goal=goal or "computer_use_execute_plan",
                action_payload={"step_count": len(steps)},
            ),
        )
        controlled = self._consume_control_signal(run_handle=run_handle)
        if controlled is not None:
            return self._build_controlled_result(
                run_handle=run_handle,
                signal=controlled,
                steps=[],
            )
        run_handle.emit(
            "computer_use.plan.started",
            {
                "stepCount": len(steps),
                "continueOnError": continue_on_error,
            },
        )

        base_context = {
            "session_id": run_handle.session_id,
            "run_id": run_handle.run_id,
            "user_id": user_id,
            "project_id": project_id,
            "workspace_id": workspace_id,
            "workspace_path": workspace_path,
        }
        last_observation: Dict[str, Any] | None = None
        step_results: List[Dict[str, Any]] = []
        recoverable_actions = {"find", "click", "double_click", "type_text", "hotkey", "scroll", "wait", "screenshot"}

        for index, raw_step in enumerate(steps, start=1):
            controlled = self._consume_control_signal(run_handle=run_handle)
            if controlled is not None:
                return self._build_controlled_result(
                    run_handle=run_handle,
                    signal=controlled,
                    steps=step_results,
                )
            step = dict(raw_step or {})
            action = str(step.get("action") or "").strip().lower()
            if not action:
                raise DesktopDriverError(f"第 {index} 步缺少 action。")

            step = self._apply_observation_window_context(step=step, last_observation=last_observation)
            step_app_id = self._infer_app_id_from_payloads(step=step)

            run_handle.emit(
                "computer_use.step.started",
                {
                    "index": index,
                    "action": action,
                    "appId": step_app_id,
                    "selectorKey": step.get("selector_key"),
                    "payload": step,
                },
            )
            heartbeat_stop, heartbeat_thread, step_started_at = self._start_step_heartbeat(
                run_handle=run_handle,
                index=index,
                action=action,
                step=step,
            )
            try:
                try:
                    result = self._execute_plan_step(index=index, step=step, base_context=base_context)
                except Exception as exc:
                    recovery: Dict[str, Any] | None = None
                    recovered_observation: Dict[str, Any] | None = None
                    if action in recoverable_actions:
                        try:
                            recovered_step, recovered_observation, recovery = self._recover_plan_step(
                                run_handle=run_handle,
                                index=index,
                                action=action,
                                step=step,
                                last_observation=last_observation,
                                error=exc,
                            )
                            run_handle.emit(
                                "computer_use.step.retrying",
                                {
                                    "index": index,
                                    "action": action,
                                    "payload": recovered_step,
                                    "recovery": recovery,
                                },
                            )
                            run_handle.emit(
                                "computer_use.step.retried",
                                {
                                    "index": index,
                                    "action": action,
                                    "payload": recovered_step,
                                    "recovery": recovery,
                                },
                            )
                            result = self._execute_plan_step(index=index, step=recovered_step, base_context=base_context)
                            if isinstance(result, dict):
                                self._remember_selector_hint(
                                    step=recovered_step,
                                    target=(result.get("result") or {}).get("target"),
                                    observation=(result.get("result") or {}).get("observation"),
                                    source="recovery_success",
                                    reason=action,
                                    weight=30,
                                )
                            step_result = {
                                "index": index,
                                "action": action,
                                "status": "completed",
                                "attemptCount": 2,
                                "elapsedSeconds": round(time.time() - step_started_at, 3),
                                "result": result,
                                "recovery": recovery,
                            }
                            step_results.append(step_result)
                            self._maybe_abort_plan_for_update_request(
                                run_handle=run_handle,
                                index=index,
                                action=action,
                                step=recovered_step,
                                step_result=step_result,
                            )
                            if step_result.get("status") != "update_requested":
                                run_handle.emit("computer_use.step.completed", step_result)
                            self._emit_review_required_event(
                                run_handle=run_handle,
                                index=index,
                                action=action,
                                result=result,
                            )
                            if action == "observe":
                                last_observation = result
                            elif isinstance(result, dict) and result.get("result", {}).get("observation"):
                                last_observation = {
                                    "observation": result["result"]["observation"],
                                    "snapshot": result.get("snapshot"),
                                }
                            elif recovered_observation is not None:
                                last_observation = recovered_observation
                            continue
                        except Exception as retry_exc:
                            exc = retry_exc

                    explicit_visual_locator = self._has_explicit_visual_locator(step)
                    visual_fallback = None
                    visual_recovery = None
                    if action in recoverable_actions and not explicit_visual_locator:
                        visual_fallback = self._collect_visual_fallback(
                            run_handle=run_handle,
                            index=index,
                            action=action,
                            step=step,
                            error=exc,
                            workspace_path=workspace_path,
                            observation=(last_observation or {}).get("observation"),
                        )
                    if isinstance(visual_fallback, dict):
                        self._remember_selector_hint(
                            step=step,
                            target=visual_fallback.get("suggestedSelector"),
                            observation=(last_observation or {}).get("observation"),
                            source="visual_fallback",
                            reason=visual_fallback.get("suggestedReason") or str(exc),
                            weight=36,
                        )
                    visual_step = self._apply_visual_selector_patch(
                        step=step,
                        visual_fallback=visual_fallback,
                        observation=(last_observation or {}).get("observation"),
                    )
                    if visual_step is not None:
                        try:
                            run_handle.emit(
                                "computer_use.step.visual_recovery_started",
                                {
                                    "index": index,
                                    "action": action,
                                    "payload": visual_step,
                                    "visualFallback": visual_fallback,
                                },
                            )
                            result = self._execute_plan_step(index=index, step=visual_step, base_context=base_context)
                            visual_recovery = {
                                "performed": True,
                                "status": "completed",
                                "payload": visual_step,
                                "reason": visual_fallback.get("suggestedReason"),
                            }
                            if isinstance(result, dict):
                                self._remember_selector_hint(
                                    step=visual_step,
                                    target=(result.get("result") or {}).get("target"),
                                    observation=(result.get("result") or {}).get("observation"),
                                    source="visual_recovery_success",
                                    reason=visual_fallback.get("suggestedReason"),
                                    weight=52,
                                )
                            step_result = {
                                "index": index,
                                "action": action,
                                "status": "completed",
                                "attemptCount": 3 if recovery is not None else 2,
                                "elapsedSeconds": round(time.time() - step_started_at, 3),
                                "result": result,
                                "visualFallback": visual_fallback,
                                "visualRecovery": visual_recovery,
                            }
                            if recovery is not None:
                                step_result["recovery"] = recovery
                            step_results.append(step_result)
                            self._maybe_abort_plan_for_update_request(
                                run_handle=run_handle,
                                index=index,
                                action=action,
                                step=visual_step,
                                step_result=step_result,
                            )
                            if step_result.get("status") != "update_requested":
                                run_handle.emit("computer_use.step.completed", step_result)
                            self._emit_review_required_event(
                                run_handle=run_handle,
                                index=index,
                                action=action,
                                result=result,
                            )
                            if action == "observe":
                                last_observation = result
                            elif isinstance(result, dict) and result.get("result", {}).get("observation"):
                                last_observation = {
                                    "observation": result["result"]["observation"],
                                    "snapshot": result.get("snapshot"),
                                }
                            continue
                        except Exception as visual_exc:
                            visual_recovery = {
                                "performed": True,
                                "status": "failed",
                                "payload": visual_step,
                                "error": str(visual_exc),
                                "reason": visual_fallback.get("suggestedReason"),
                            }
                            run_handle.emit(
                                "computer_use.step.visual_recovery_failed",
                                {
                                    "index": index,
                                    "action": action,
                                    "visualRecovery": visual_recovery,
                                },
                            )
                    coordinate_step = None
                    if action in {"click", "double_click", "find_and_type", "type_text"}:
                        coordinate_step = self._apply_coordinate_click_patch(
                            step=step,
                            visual_fallback=visual_fallback,
                            observation=(last_observation or {}).get("observation"),
                        )
                    if coordinate_step is not None:
                        try:
                            run_handle.emit(
                                "computer_use.step.coordinate_fallback_started",
                                {
                                    "index": index,
                                    "action": action,
                                    "payload": coordinate_step,
                                    "visualFallback": visual_fallback,
                                },
                            )
                            result = self._execute_plan_step(index=index, step=coordinate_step, base_context=base_context)
                            visual_recovery = {
                                "performed": True,
                                "status": "completed",
                                "mode": "coordinate",
                                "payload": coordinate_step,
                                "reason": visual_fallback.get("suggestedReason"),
                            }
                            if isinstance(result, dict):
                                self._remember_selector_hint(
                                    step=coordinate_step,
                                    target=(result.get("result") or {}).get("target"),
                                    observation=(result.get("result") or {}).get("observation"),
                                    source="coordinate_recovery_success",
                                    reason=visual_fallback.get("suggestedReason"),
                                    weight=42,
                                )
                            step_result = {
                                "index": index,
                                "action": action,
                                "status": "completed",
                                "attemptCount": 3 if recovery is not None else 2,
                                "elapsedSeconds": round(time.time() - step_started_at, 3),
                                "result": result,
                                "visualFallback": visual_fallback,
                                "visualRecovery": visual_recovery,
                            }
                            if recovery is not None:
                                step_result["recovery"] = recovery
                            step_results.append(step_result)
                            self._maybe_abort_plan_for_update_request(
                                run_handle=run_handle,
                                index=index,
                                action=action,
                                step=coordinate_step,
                                step_result=step_result,
                            )
                            if step_result.get("status") != "update_requested":
                                run_handle.emit("computer_use.step.completed", step_result)
                            self._emit_review_required_event(
                                run_handle=run_handle,
                                index=index,
                                action=action,
                                result=result,
                            )
                            if action == "observe":
                                last_observation = result
                            elif isinstance(result, dict) and result.get("result", {}).get("observation"):
                                last_observation = {
                                    "observation": result["result"]["observation"],
                                    "snapshot": result.get("snapshot"),
                                }
                            continue
                        except Exception as coordinate_exc:
                            visual_recovery = {
                                "performed": True,
                                "status": "failed",
                                "mode": "coordinate",
                                "payload": coordinate_step,
                                "error": str(coordinate_exc),
                                "reason": visual_fallback.get("suggestedReason"),
                            }
                    step_result = {
                        "index": index,
                        "action": action,
                        "status": "failed",
                        "elapsedSeconds": round(time.time() - step_started_at, 3),
                        "error": str(exc),
                    }
                    if recovery is not None:
                        step_result["recovery"] = recovery
                    if visual_fallback is not None:
                        step_result["visualFallback"] = visual_fallback
                    if visual_recovery is not None:
                        step_result["visualRecovery"] = visual_recovery
                    step_results.append(step_result)
                    run_handle.emit("computer_use.step.failed", step_result)
                    if not continue_on_error:
                        run_handle.fail(str(exc), node="computer_use_runtime")
                        raise
                    continue

                step_result = {
                    "index": index,
                    "action": action,
                    "status": "completed",
                    "attemptCount": 1,
                    "elapsedSeconds": round(time.time() - step_started_at, 3),
                    "result": result,
                }
                if isinstance(result, dict):
                    self._remember_selector_hint(
                        step=step,
                        target=(result.get("result") or {}).get("target"),
                        observation=(result.get("result") or {}).get("observation"),
                        source="successful_plan_step",
                        reason=action,
                        weight=18,
                    )
                step_results.append(step_result)
                self._maybe_abort_plan_for_update_request(
                    run_handle=run_handle,
                    index=index,
                    action=action,
                    step=step,
                    step_result=step_result,
                )
                if step_result.get("status") != "update_requested":
                    run_handle.emit("computer_use.step.completed", step_result)
                self._emit_review_required_event(
                    run_handle=run_handle,
                    index=index,
                    action=action,
                    result=result,
                )
                if action == "observe":
                    last_observation = result
                elif isinstance(result, dict) and result.get("result", {}).get("observation"):
                    last_observation = {
                        "observation": result["result"]["observation"],
                        "snapshot": result.get("snapshot"),
                    }
            finally:
                self._stop_step_heartbeat(heartbeat_stop, heartbeat_thread)
        controlled = self._consume_control_signal(run_handle=run_handle)
        if controlled is not None:
            return self._build_controlled_result(
                run_handle=run_handle,
                signal=controlled,
                steps=step_results,
            )

        run_handle.transition("completed", reason="computer_use_plan_complete", node="computer_use_runtime")
        run_service.transition_run(run_handle.run_id, status="completed")
        resource_lease = self._cleanup_resource_lease(
            run_handle=run_handle,
            status="succeeded",
            reason="computer_use_plan_complete",
        )
        return {
            "sessionId": run_handle.session_id,
            "runId": run_handle.run_id,
            "stepCount": len(step_results),
            "continueOnError": continue_on_error,
            "steps": step_results,
            "resourceLease": resource_lease,
        }

    def plan(
        self,
        *,
        goal: str,
        app_id: str | None = None,
        session_id: str | None = None,
        run_id: str | None = None,
        user_id: str = "anonymous",
        project_id: str | None = None,
        workspace_id: str | None = None,
        workspace_path: str | None = None,
        window_title: str | None = None,
        window_handle: int | None = None,
        max_steps: int = 5,
        include_screenshot: bool = False,
    ) -> Dict[str, Any]:
        if not goal.strip():
            raise DesktopDriverError("planner 目标不能为空。")

        task_loop = self.prepare_task_loop(goal=goal, app_id=app_id)
        if self.playbook_executor_registry.can_handle(task_loop):
            return {
                "sessionId": session_id,
                "runId": run_id,
                "goal": goal,
                "appId": app_id,
                "taskLoop": task_loop,
                "planner": {
                    "role": "computer_use_task_loop",
                    "plannerOutput": "runtime_native_playbook_selected",
                    "selectedPlaybookExecutor": str((task_loop.get("domain") or {}).get("selectedPlaybook") or ""),
                    "stepCount": len(((task_loop.get("plan") or {}).get("steps") or [])),
                    "steps": [],
                },
            }

        observation_result = self.observe(
            session_id=session_id,
            run_id=run_id,
            user_id=user_id,
            project_id=project_id,
            workspace_id=workspace_id,
            workspace_path=workspace_path,
            goal=f"{goal} · observe",
            app_id=app_id,
            window_title=window_title,
            window_handle=window_handle,
            depth_limit=4,
            element_limit=80,
            include_screenshot=include_screenshot,
        )
        observation = dict(observation_result.get("observation") or {})
        planning = self._plan_steps(
            goal=goal,
            observation=observation,
            max_steps=max_steps,
            window_title=window_title,
            window_handle=window_handle,
        )
        return {
            "sessionId": observation_result.get("sessionId"),
            "runId": observation_result.get("runId"),
            "goal": goal,
            "appId": app_id,
            "initialObservation": observation,
            "planner": {
                "role": "computer_use_planner",
                "plannerOutput": planning["plannerOutput"],
                "stepCount": len(planning["steps"]),
                "steps": planning["steps"],
            },
        }

    def plan_and_execute(
        self,
        *,
        goal: str,
        app_id: str | None = None,
        session_id: str | None = None,
        run_id: str | None = None,
        user_id: str = "anonymous",
        project_id: str | None = None,
        workspace_id: str | None = None,
        workspace_path: str | None = None,
        window_title: str | None = None,
        window_handle: int | None = None,
        continue_on_error: bool = False,
        max_steps: int = 5,
        include_screenshot: bool = False,
        playbook_inputs: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        task_loop = self.prepare_task_loop(goal=goal, app_id=app_id)
        if self.playbook_executor_registry.can_handle(task_loop):
            execution = self.execute_selected_playbook(
                goal=goal,
                task_loop=task_loop,
                allow_real_click=True,
                playbook_inputs=playbook_inputs,
                session_id=session_id,
                run_id=run_id,
                user_id=user_id,
                project_id=project_id,
                workspace_id=workspace_id,
                workspace_path=workspace_path,
            )
            return {
                "sessionId": execution.get("sessionId") or session_id,
                "runId": execution.get("runId") or run_id,
                "goal": goal,
                "appId": app_id,
                "taskLoop": task_loop,
                "execution": execution,
            }
        planning = self.plan(
            goal=goal,
            app_id=app_id,
            session_id=session_id,
            run_id=run_id,
            user_id=user_id,
            project_id=project_id,
            workspace_id=workspace_id,
            workspace_path=workspace_path,
            window_title=window_title,
            window_handle=window_handle,
            max_steps=max_steps,
            include_screenshot=include_screenshot,
        )
        execution = self.execute_plan(
            session_id=planning.get("sessionId"),
            run_id=planning.get("runId"),
            user_id=user_id,
            project_id=project_id,
            workspace_id=workspace_id,
            workspace_path=workspace_path,
            goal=goal,
            steps=(planning.get("planner") or {}).get("steps") or [],
            continue_on_error=continue_on_error,
            max_steps=max_steps,
        )
        return {
            **planning,
            "sessionId": execution.get("sessionId") or planning.get("sessionId"),
            "runId": execution.get("runId") or planning.get("runId"),
            "execution": execution,
        }

    def execute_selected_playbook(
        self,
        *,
        goal: str,
        task_loop: Dict[str, Any] | None = None,
        allow_real_click: bool = False,
        playbook_inputs: Optional[Dict[str, Any]] = None,
        session_id: str | None = None,
        run_id: str | None = None,
        user_id: str = "anonymous",
        project_id: str | None = None,
        workspace_id: str | None = None,
        workspace_path: str | None = None,
        invocation_metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        self._ensure_runtime_ready()
        prepared = dict(task_loop or self.prepare_task_loop(goal=goal, app_id="browser_checkout"))
        context = PlaybookExecutionContext(
            runtime=self,
            task_loop=prepared,
            goal=goal,
            session_id=session_id,
            run_id=run_id,
            user_id=user_id,
            project_id=project_id,
            workspace_id=workspace_id,
            workspace_path=workspace_path,
            playbook_inputs=dict(playbook_inputs or {}),
            allow_real_click=allow_real_click,
            invocation_metadata=dict(invocation_metadata or {}),
        )
        return self.playbook_executor_registry.execute(context)

    def availability(self) -> Dict[str, Any]:
        vision_state = self._vision_fallback_state()
        self.browser_automation.configure(self._computer_use_config())
        capability_matrix = self._runtime_capability_matrix()
        browser_lane = self.browser_automation.availability_summary()
        try:
            app_catalog_summary = self.app_catalog.summary(include_running=True)
        except Exception:
            app_catalog_summary = {}
        capability_truth = dict(capability_matrix.get("truth") or {})
        capability_truth_payload = self._capability_truth_payload(
            capability_matrix=capability_matrix,
            browser_lane=browser_lane,
            app_catalog_summary=app_catalog_summary,
        )
        resolution_payload = self._resolution_policy_payload()
        current_matrix = dict(capability_matrix.get("current") or {})
        capabilities = dict(current_matrix.get("facets") or {})
        capabilities["execution"] = {
            **dict(current_matrix.get("execution") or {}),
            **self._platform_route_policy_summary(capability_truth=capability_truth),
        }
        return {
            "platform": self.driver.platform,
            "backend": self.driver.backend,
            "available": self.driver.is_available(),
            "details": {
                "driver": "pywinauto.uia+win32_fallback",
                "backends": {
                    "primary": "uia",
                    "fallback": "win32",
                },
                "requires": ["pywinauto", "pywin32", "mss", "Pillow"],
                "capabilities": capabilities,
                "capabilityMatrix": capability_matrix,
                "capabilityTruth": capability_truth_payload,
                "evidenceRefs": list(capability_truth_payload.get("evidenceRefs") or []),
                "knownGaps": list(capability_truth_payload.get("knownGaps") or []),
                "portableChecklist": list(capability_truth_payload.get("portableChecklist") or []),
                "browserLaneTruth": dict(capability_truth_payload.get("browserLaneTruth") or {}),
                "screenWakePolicy": dict(capability_truth_payload.get("screenWakePolicy") or {}),
                "resolutionPolicy": dict(resolution_payload.get("resolutionPolicy") or {}),
                "currentDisplay": dict(resolution_payload.get("currentDisplay") or {}),
                "coordinateAnchorPolicy": dict(resolution_payload.get("coordinateAnchorPolicy") or {}),
                "resourceCleanupPolicy": dict(resolution_payload.get("resourceCleanupPolicy") or {}),
                "experienceAssets": dict(capability_truth_payload.get("experienceAssets") or {}),
                "builtInPlaybookSeeds": list(capability_truth_payload.get("builtInPlaybookSeeds") or []),
                "visualActor": self._visual_actor_descriptor(),
                "candidateBoardSources": candidate_board_source_catalog(),
                "browserProfilePersistence": self._browser_profile_persistence_payload(browser_lane),
                "platformProbeMatrix": self._platform_probe_matrix_payload(browser_lane=browser_lane),
                "routePolicy": capabilities.get("execution"),
                "visionFallback": vision_state,
                "offlineVisualBenchmark": self._offline_visual_benchmark_descriptor(),
                "onlineVisualLocator": self._online_visual_locator_descriptor(),
                "browserLane": browser_lane,
                "appAdapter": self._app_adapter_summary(),
                "selectorStats": self.driver.selector_metrics(),
                "appCatalog": app_catalog_summary,
            },
        }

    def prepare_task_loop(
        self,
        *,
        goal: str,
        app_id: str | None = None,
    ) -> Dict[str, Any]:
        self.browser_automation.configure(self._computer_use_config())
        browser_decision = self._browser_lane_decision(
            action_type="type_text",
            action_payload={
                "app_id": app_id or "browser_checkout",
                "app_name": "browser",
                "text": "https://github.com/",
            },
            app_id=app_id or "browser_checkout",
        )
        return prepare_task_loop(
            goal,
            browser_decision=browser_decision.as_dict(),
            web_searcher=self._task_loop_web_searcher,
        ).as_dict()

    def _task_loop_web_searcher(self, query: str) -> Any:
        try:
            from core.tools.web_fetcher import web_search

            if hasattr(web_search, "func"):
                return web_search.func(query=query, limit=5, search_engine="auto", tool_call_id="computer_use_task_loop")
            return web_search(query=query, limit=5)
        except Exception as exc:
            return {
                "ok": False,
                "query": query,
                "failureClass": "web_search_failed",
                "error": str(exc),
                "recommendedNextAction": "该 Computer Use 事实解析搜索失败；请要求用户提供明确 URL，或先用 research_broker/web_broker 获取可访问来源。",
                "source": "computer_use_task_loop_web_search",
            }

    def _github_star_strict_dom_state(self, state: Dict[str, Any], *, target_url: str) -> Dict[str, Any]:
        payload = dict(state or {})
        current_url = str(payload.get("url") or "").strip().lower().rstrip("/")
        expected_url = str(target_url or "").strip().lower().rstrip("/")
        expected_path = "/".join(str(target_url or "").strip().rstrip("/").split("/")[-2:])
        repo_path = str(payload.get("repoPath") or "").strip()
        strict_state = str(payload.get("strictDomState") or "").strip().lower()
        has_target = bool(payload.get("hasStarTarget"))
        url_matches = bool(expected_url and (current_url == expected_url or current_url.startswith(expected_url + "/")))
        repo_matches = bool(expected_path and repo_path.lower() == expected_path.lower())
        accepted = bool(has_target and strict_state in {"starred", "not_starred"} and (url_matches or repo_matches))
        return {
            "accepted": accepted,
            "state": strict_state if accepted else "ambiguous",
            "urlMatches": url_matches,
            "repoMatches": repo_matches,
            "hasStarTarget": has_target,
            "reason": None if accepted else "strict_dom_state_not_accepted",
        }

    def _read_github_star_dom_state(self, *, target_id: str, target_url: str) -> tuple[Dict[str, Any], Dict[str, Any]]:
        state = dict(
            (self.browser_automation._evaluate(target_id=target_id, expression=github_star_dom_probe_script()).get("value"))
            or {}
        )
        return state, self._github_star_strict_dom_state(state, target_url=target_url)

    def _wait_for_github_star_dom_state(
        self,
        *,
        target_id: str,
        target_url: str,
        desired_state: str | None = None,
        timeout_s: float = 8.0,
        poll_s: float = 0.5,
    ) -> tuple[Dict[str, Any], Dict[str, Any]]:
        deadline = time.time() + max(float(timeout_s or 0.0), 0.1)
        last_state: Dict[str, Any] = {}
        last_dom: Dict[str, Any] = {"accepted": False, "state": "ambiguous", "reason": "not_polled"}
        while True:
            last_state, last_dom = self._read_github_star_dom_state(target_id=target_id, target_url=target_url)
            if last_dom.get("accepted") and (not desired_state or last_dom.get("state") == desired_state):
                return last_state, last_dom
            if time.time() >= deadline:
                return last_state, last_dom
            time.sleep(max(float(poll_s or 0.1), 0.1))

    def _normalize_github_star_desired_state(self, value: str | None) -> str:
        lowered = str(value or "").strip().lower()
        if lowered in {"unstarred", "unstar", "remove_star", "remove-star", "not_starred", "not-starred", "消星", "取消星标", "取消收藏"}:
            return "not_starred"
        return "starred"

    def execute_github_star_playbook(
        self,
        *,
        goal: str,
        allow_real_click: bool = False,
        desired_state: str = "starred",
        session_id: str | None = None,
        run_id: str | None = None,
        user_id: str = "anonymous",
        project_id: str | None = None,
        workspace_id: str | None = None,
        workspace_path: str | None = None,
        invocation_metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        self._ensure_runtime_ready()
        task_loop = self.prepare_task_loop(goal=goal, app_id="browser_checkout")
        selected = str((task_loop.get("domain") or {}).get("selectedPlaybook") or "")
        if selected != "github.star_repository":
            return {
                "status": "not_applicable",
                "taskLoop": task_loop,
            }
        plan_payload = dict(task_loop.get("plan") or {})
        normalized_desired_state = self._normalize_github_star_desired_state(
            desired_state or str(plan_payload.get("desiredState") or "") or str((task_loop.get("intent") or {}).get("desiredState") or "")
        )
        run_handle = self.begin_or_attach_run(
            session_id=session_id,
            run_id=run_id,
            user_id=user_id,
            project_id=project_id,
            workspace_id=workspace_id,
            workspace_path=workspace_path,
            goal=goal,
            trigger_source="computer_use_task_loop",
            metadata={
                "computer_use_goal": goal,
                "taskLoop": {
                    "selectedPlaybook": selected,
                    "status": task_loop.get("status"),
                    "desiredState": normalized_desired_state,
                },
                "invocation": dict(invocation_metadata or {}),
            },
        )
        run_handle.emit("computer_use.task_loop.prepared", task_loop)
        target_url = str(plan_payload.get("targetUrl") or "").strip()
        if not target_url:
            run_handle.emit(
                "computer_use.task_loop.human_attention",
                {"reason": "canonical_repo_url_not_resolved"},
            )
            run_handle.transition("completed", reason="computer_use_fact_resolution_required", node="computer_use_runtime")
            run_service.transition_run(run_handle.run_id, status="completed")
            return {
                "status": "needs_human_attention",
                "reason": "canonical_repo_url_not_resolved",
                "selectedPlaybook": selected,
                "taskLoop": task_loop,
                "runId": run_handle.run_id,
                "sessionId": run_handle.session_id,
            }

        decision = self._browser_lane_decision(
            action_type="type_text",
            action_payload={
                "app_id": "browser_checkout",
                "app_name": "browser",
                "text": target_url,
            },
            app_id="browser_checkout",
        )
        run_handle.emit("computer_use.task_loop.lane_decision", decision.as_dict())
        if not decision.available:
            run_handle.emit(
                "computer_use.task_loop.human_attention",
                {"reason": decision.reason or "browser_lane_unavailable"},
            )
            run_handle.transition("completed", reason="computer_use_browser_lane_unavailable", node="computer_use_runtime")
            run_service.transition_run(run_handle.run_id, status="completed")
            return {
                "status": "needs_human_attention",
                "reason": decision.reason or "browser_lane_unavailable",
                "canonicalUrl": target_url,
                "desiredState": normalized_desired_state,
                "selectedPlaybook": selected,
                "taskLoop": task_loop,
                "runId": run_handle.run_id,
                "sessionId": run_handle.session_id,
            }

        opened = self.browser_automation.open_tab(url=target_url, decision=decision)
        target_id = str(opened.get("targetId") or "").strip()
        try:
            from runtimes.computer_use.browser_session_service import browser_session_service

            workbench_browser = browser_session_service.register_existing_target(
                session_id=run_handle.session_id,
                run_id=run_handle.run_id,
                provider=self.browser_automation,
                opened=opened,
            )
            run_handle.emit(
                "computer_use.workbench.browser_registered",
                {"browserSessionId": workbench_browser.get("browserSessionId")},
            )
        except Exception as exc:
            run_handle.emit(
                "computer_use.workbench.browser_registration_failed",
                {"errorClass": exc.__class__.__name__},
            )
        self._record_resource_lease(
            run_handle=run_handle,
            kind="browser_tab",
            resource={
                "targetId": target_id,
                "url": target_url,
                "provider": opened.get("provider"),
                "family": opened.get("family"),
                "targetPort": opened.get("targetPort"),
            },
            cleanup_on_complete=True,
            preserve_on_human_input=True,
            delayed_cleanup_seconds=60,
            reason="github_star_playbook_opened_tab",
        )
        run_handle.emit("computer_use.github_star.opened", {"targetUrl": target_url, "targetId": target_id})
        pre_state, pre_dom = self._wait_for_github_star_dom_state(
            target_id=target_id,
            target_url=target_url,
            desired_state=None,
            timeout_s=10.0,
            poll_s=0.4,
        )
        run_handle.emit("computer_use.github_star.pre_state", pre_state)
        if pre_state.get("loggedOut") or pre_state.get("needsLoginForStar"):
            run_handle.emit("computer_use.task_loop.human_attention", {"reason": "needs_human_login", "preState": pre_state})
            human_input_request = self._human_input_request_payload(
                reason="needs_human_login",
                target_url=target_url,
                browser_target=opened,
            )
            run_handle.transition("completed", reason="computer_use_needs_human_login", node="computer_use_runtime")
            run_service.transition_run(run_handle.run_id, status="completed")
            resource_lease = self._cleanup_resource_lease(
                run_handle=run_handle,
                status="needs_human_login",
                reason="needs_human_login",
            )
            return {
                "status": "needs_human_login",
                "recommendedNextAction": "ask_user",
                "humanInputRequest": human_input_request,
                "canonicalUrl": target_url,
                "desiredState": normalized_desired_state,
                "selectedPlaybook": selected,
                "browserTarget": opened,
                "preState": pre_state,
                "taskLoop": task_loop,
                "resourceLease": resource_lease,
                "runId": run_handle.run_id,
                "sessionId": run_handle.session_id,
            }
        if pre_dom.get("state") == normalized_desired_state:
            already_reason = "already_starred" if normalized_desired_state == "starred" else "already_unstarred"
            run_handle.emit("computer_use.github_star.verified", {"state": already_reason, "preState": pre_state, "strictDom": pre_dom, "desiredState": normalized_desired_state})
            run_handle.transition("completed", reason=f"computer_use_github_star_{already_reason}", node="computer_use_runtime")
            run_service.transition_run(run_handle.run_id, status="completed")
            resource_lease = self._cleanup_resource_lease(
                run_handle=run_handle,
                status="succeeded",
                reason=already_reason,
            )
            return {
                "status": "succeeded",
                "canonicalUrl": target_url,
                "desiredState": normalized_desired_state,
                "selectedPlaybook": selected,
                "browserTarget": opened,
                "preState": pre_state,
                "postState": pre_state,
                "strictDom": pre_dom,
                "action": already_reason,
                "taskLoop": task_loop,
                "resourceLease": resource_lease,
                "runId": run_handle.run_id,
                "sessionId": run_handle.session_id,
            }
        opposite_state = "not_starred" if normalized_desired_state == "starred" else "starred"
        if pre_dom.get("state") != opposite_state:
            run_handle.emit("computer_use.task_loop.human_attention", {"reason": pre_dom.get("reason") or "strict_dom_state_ambiguous", "preState": pre_state, "strictDom": pre_dom})
            run_handle.transition("completed", reason="computer_use_github_star_strict_dom_ambiguous", node="computer_use_runtime")
            run_service.transition_run(run_handle.run_id, status="completed")
            resource_lease = self._cleanup_resource_lease(
                run_handle=run_handle,
                status="needs_human_attention",
                reason="strict_dom_state_ambiguous",
            )
            return {
                "status": "needs_human_attention",
                "reason": "strict_dom_state_ambiguous",
                "recommendedNextAction": "ask_user",
                "canonicalUrl": target_url,
                "desiredState": normalized_desired_state,
                "selectedPlaybook": selected,
                "browserTarget": opened,
                "preState": pre_state,
                "strictDom": pre_dom,
                "taskLoop": task_loop,
                "resourceLease": resource_lease,
                "runId": run_handle.run_id,
                "sessionId": run_handle.session_id,
            }
        if not allow_real_click:
            run_handle.emit("computer_use.task_loop.human_attention", {"reason": "real_click_not_allowed", "preState": pre_state})
            run_handle.transition("completed", reason="computer_use_real_click_not_allowed", node="computer_use_runtime")
            run_service.transition_run(run_handle.run_id, status="completed")
            resource_lease = self._cleanup_resource_lease(
                run_handle=run_handle,
                status="needs_human_attention",
                reason="real_click_not_allowed",
            )
            return {
                "status": "needs_human_attention",
                "reason": "real_click_not_allowed",
                "recommendedNextAction": "ask_user",
                "canonicalUrl": target_url,
                "desiredState": normalized_desired_state,
                "selectedPlaybook": selected,
                "browserTarget": opened,
                "preState": pre_state,
                "taskLoop": task_loop,
                "resourceLease": resource_lease,
                "runId": run_handle.run_id,
                "sessionId": run_handle.session_id,
            }

        self._assess_runtime_action_safety(
            run_handle=run_handle,
            action_type="click",
            action_payload={
                "app_id": "browser_checkout",
                "target_text": "GitHub Star" if normalized_desired_state == "starred" else "GitHub Unstar",
                "window_title": target_url,
                "profile_action": "github.star_repository" if normalized_desired_state == "starred" else "github.unstar_repository",
                "url": target_url,
            },
        )
        click_result = dict(
            (self.browser_automation._evaluate(target_id=target_id, expression=github_star_click_script(desired_state=normalized_desired_state)).get("value"))
            or {}
        )
        run_handle.emit("computer_use.github_star.click", click_result)
        post_state, post_dom = self._wait_for_github_star_dom_state(
            target_id=target_id,
            target_url=target_url,
            desired_state=normalized_desired_state,
            timeout_s=8.0,
            poll_s=0.5,
        )
        run_handle.emit("computer_use.github_star.post_state", post_state)
        if post_dom.get("state") == normalized_desired_state:
            run_handle.transition("completed", reason="computer_use_github_star_completed", node="computer_use_runtime")
            run_service.transition_run(run_handle.run_id, status="completed")
            resource_lease = self._cleanup_resource_lease(
                run_handle=run_handle,
                status="succeeded",
                reason="github_star_completed",
            )
            return {
                "status": "succeeded",
                "canonicalUrl": target_url,
                "desiredState": normalized_desired_state,
                "selectedPlaybook": selected,
                "browserTarget": opened,
                "preState": pre_state,
                "clickAction": click_result,
                "postState": post_state,
                "strictDom": post_dom,
                "action": "clicked_star" if normalized_desired_state == "starred" else "clicked_unstar",
                "taskLoop": task_loop,
                "resourceLease": resource_lease,
                "runId": run_handle.run_id,
                "sessionId": run_handle.session_id,
            }
        run_handle.emit("computer_use.task_loop.human_attention", {"reason": "post_state_not_strictly_starred", "postState": post_state, "strictDom": post_dom})
        run_handle.fail("GitHub Star 状态未进入严格 Starred DOM 状态。", node="computer_use_runtime")
        resource_lease = self._cleanup_resource_lease(
            run_handle=run_handle,
            status="needs_human_attention",
            reason="post_state_not_starred",
        )
        return {
            "status": "needs_human_attention",
            "reason": "post_state_not_starred" if normalized_desired_state == "starred" else "post_state_not_unstarred",
            "recommendedNextAction": "ask_user",
            "canonicalUrl": target_url,
            "desiredState": normalized_desired_state,
            "selectedPlaybook": selected,
            "browserTarget": opened,
            "preState": pre_state,
            "clickAction": click_result,
            "postState": post_state,
            "strictDom": post_dom,
            "taskLoop": task_loop,
            "resourceLease": resource_lease,
            "runId": run_handle.run_id,
            "sessionId": run_handle.session_id,
        }


computer_use_runtime = runtime_registry.register(ComputerUseRuntime())
