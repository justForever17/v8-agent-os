from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import time
import urllib.parse
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


DEFAULT_WEB_URL = "http://127.0.0.1:9527"
DEFAULT_ENGINE_URL = "http://127.0.0.1:9530"
TERMINAL_RUN_STATES = {"completed", "failed", "cancelled", "interrupted"}
INITIAL_PROMPT = (
    "帮我做个真能用的小工具：先查清楚《中国居民膳食指南（2022）》里成年人每天蔬菜、"
    "水果、全谷物和饮水的权威建议，给出能追溯的中文来源；然后在这个空工作区做成一个"
    "本地可运行的 React 单页‘每日膳食搭配器’，用户能输入人数、勾选是否包含全谷物，"
    "页面即时算出一天建议总量。请把源码、启动命令和最小验证都落在项目里，做完后自己检查，"
    "不要只在聊天里贴代码。"
)
DEBUG_PROMPTS = {
    "debug1": (
        "第一阶段调试：请先复现再修复。关闭‘今日是否包含全谷物和杂豆’后，卡片仍显示并按人数倍增 "
        "50–150 g，与‘未计入’语义矛盾。关闭时应明确显示未计入（或 0 g），重新开启后恢复按人数换算。"
        "请只改相关源码，补最小回归测试并运行验证，不要盲改其他样式。"
    ),
    "debug2": (
        "第二阶段调试：请先在浏览器复现再修复。当前可见的全谷物开关滑块和状态文字本身不可点击，"
        "只有上方标题标签能切换；这会误导鼠标用户，也缺少清晰的 switch 交互语义。请让整个可见开关区域可点击、"
        "保留键盘与读屏可访问性，且不要破坏第一阶段的关闭/开启计算逻辑。补最小回归测试并运行验证。"
    ),
    "debug2_retry": (
        "重做。你上一条已经明确给出这个继续选项；这是新的用户指令和新 run。不要再让我贴源码或重复确认。"
        "请实际写入全谷物开关修复，让整个可见开关区域可点击并保留键盘、读屏语义；保持第一阶段计算逻辑，"
        "实际运行现有回归测试，只按本轮真实写入和测试证据验收。"
    ),
    "debug2_css": (
        "继续修复真实浏览器复现的问题：Edge 中 wholeGrainTrack 不可见、点击等待超时。已确认是 "
        "`.field label { display: block }` 的更高 specificity 覆盖 `.toggle { display: flex }`，让空的 "
        "`.toggle__pill` 维持 inline，48×26 宽高没有形成真实命中区。请只修相关 CSS 与必要回归检查，"
        "保留现有 React 行为；完成后运行测试，不要用 jsdom 事件通过替代可见尺寸和真实点击验收。"
    ),
    "debug2_verify": (
        "继续完成验收，不要再次改源码。CSS 命中区修复已经落盘；请派一个只读验证任务，在绑定工作区实际执行 "
        "`npm test -- --run`，要求退出码 0 并回传真实输出。不要运行 npm install，不要用静态检查或既有 handoff "
        "替代本轮命令证据。"
    ),
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:16]


def _json_request(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8", errors="replace")
        return json.loads(raw) if raw.strip() else {}


def _wait_for_endpoint(url: str, *, timeout_s: float = 25.0) -> None:
    deadline = time.monotonic() + timeout_s
    last_error = ""
    while time.monotonic() < deadline:
        try:
            _json_request(url, timeout=3.0)
            return
        except Exception as exc:  # noqa: BLE001 - live harness reports the exact local connectivity class.
            last_error = f"{type(exc).__name__}: {exc}"
            time.sleep(0.5)
    raise RuntimeError(f"endpoint_unavailable: {last_error}")


def _engine_api_base(engine_url: str) -> str:
    value = engine_url.rstrip("/")
    return value if value.endswith("/v1") else f"{value}/v1"


def _as_list(value: Any, key: str) -> list[dict[str, Any]]:
    if isinstance(value, dict) and isinstance(value.get(key), list):
        return [item for item in value[key] if isinstance(item, dict)]
    return []


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return str(value)


def _event_topic(event: dict[str, Any]) -> str:
    return str(event.get("topic") or event.get("event_type") or event.get("type") or "").strip()


def _event_run_id(event: dict[str, Any]) -> str:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    return _first_identifier(event, "runId", "run_id") or _first_identifier(payload, "runId", "run_id")


def _runtime_progress_timeline_node(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    progress = payload.get("progress") if isinstance(payload.get("progress"), dict) else payload
    node = progress.get("timelineNode") if isinstance(progress.get("timelineNode"), dict) else {}
    return node


def _normalized_command(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _command_result_succeeded(value: Any) -> bool:
    text_value = _text(value).strip()
    if not text_value:
        return False
    try:
        payload = json.loads(text_value)
    except (TypeError, ValueError, json.JSONDecodeError):
        payload = None
    if isinstance(payload, dict):
        return payload.get("ok") is True and payload.get("returnCode") in {0, "0"}
    lowered = text_value.lower()
    failure_markers = (
        "[exit code:",
        "[still running]",
        "[git_parallel_isolation_required]",
        "[command_session_required]",
        "[sync_command_timed_out]",
        "[command_timeout]",
        "[verification_command_not_exact]",
    )
    if any(marker in lowered for marker in failure_markers):
        return False
    return text_value.startswith("$ ") and (
        re.search(r"\btests?\b[^\n]{0,80}\bpassed\b", lowered) is not None
        or re.search(r"\bpassed\b[^\n]{0,80}\btests?\b", lowered) is not None
    )


def _exact_command_evidence(
    events: list[dict[str, Any]],
    *,
    run_id: str,
    command: str,
) -> dict[str, Any]:
    expected = _normalized_command(command)
    call_ids: set[str] = set()
    matched_results: list[str] = []
    for event in events:
        if run_id and _event_run_id(event) != run_id:
            continue
        node = _runtime_progress_timeline_node(event)
        if str(node.get("topic") or "") != "subagent.tool.started":
            continue
        if str(node.get("toolName") or "") != "run_system_command":
            continue
        args = node.get("args") if isinstance(node.get("args"), dict) else {}
        if _normalized_command(args.get("command")) == expected:
            call_id = str(node.get("toolCallId") or "").strip()
            if call_id:
                call_ids.add(call_id)
    for event in events:
        if run_id and _event_run_id(event) != run_id:
            continue
        node = _runtime_progress_timeline_node(event)
        if str(node.get("topic") or "") != "subagent.tool.finished":
            continue
        if str(node.get("toolCallId") or "").strip() not in call_ids:
            continue
        matched_results.append(_text(node.get("agentVisibleResult")))
    succeeded = any(_command_result_succeeded(item) for item in matched_results)
    return {
        "command": command,
        "startedCount": len(call_ids),
        "finishedCount": len(matched_results),
        "succeeded": succeeded,
        "resultDigests": [_digest(item) for item in matched_results if item],
    }


def _event_elapsed_ms(event: dict[str, Any], started_epoch: float, observed_elapsed_ms: int) -> int:
    raw = str(event.get("ts") or event.get("event_ts") or "").strip()
    if raw:
        try:
            event_epoch = datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
            return max(0, int((event_epoch - started_epoch) * 1000))
        except ValueError:
            pass
    return observed_elapsed_ms


def _relative_workspace_files(workspace: Path) -> list[str]:
    ignored = {"node_modules", ".git", ".vite", "dist", "build", ".next"}
    files: list[str] = []
    for path in workspace.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(workspace)
        if any(part in ignored for part in relative.parts):
            continue
        files.append(relative.as_posix())
    return sorted(files)


def _workspace_hashes(workspace: Path) -> dict[str, str]:
    return {
        relative: hashlib.sha256((workspace / relative).read_bytes()).hexdigest()
        for relative in _relative_workspace_files(workspace)
    }


def _safe_web_payload(page, path: str) -> dict[str, Any]:
    result = page.evaluate(
        """async (path) => {
            const response = await fetch(path, { cache: 'no-store' });
            const payload = await response.json().catch(() => ({}));
            return { ok: response.ok, status: response.status, payload };
        }""",
        path,
    )
    if not isinstance(result, dict) or not result.get("ok"):
        status = result.get("status") if isinstance(result, dict) else "unknown"
        raise RuntimeError(f"web_api_failed:{path}:{status}")
    payload = result.get("payload")
    return payload if isinstance(payload, dict) else {"items": payload}


def _post_web_payload(page, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    result = page.evaluate(
        """async ({ path, payload }) => {
            const response = await fetch(path, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            const data = await response.json().catch(() => ({}));
            return { ok: response.ok, status: response.status, payload: data };
        }""",
        {"path": path, "payload": payload},
    )
    if not isinstance(result, dict) or not result.get("ok"):
        status = result.get("status") if isinstance(result, dict) else "unknown"
        detail = result.get("payload") if isinstance(result, dict) else {}
        raise RuntimeError(f"web_api_failed:{path}:{status}:{_digest(_text(detail))}")
    data = result.get("payload")
    return data if isinstance(data, dict) else {}


def _authenticate_if_needed(page) -> None:
    password_input = page.locator('input[type="password"]')
    if password_input.count() == 0:
        return
    username = os.environ.get("V8OS_LIVE_USERNAME", "").strip()
    password = os.environ.get("V8OS_LIVE_PASSWORD", "")
    if not username or not password:
        raise RuntimeError("web_auth_required:set_V8OS_LIVE_USERNAME_and_V8OS_LIVE_PASSWORD")
    username_input = page.locator('input[type="text"], input[type="email"]').first
    username_input.fill(username)
    password_input.first.fill(password)
    page.locator('button[type="submit"]').first.click()
    page.wait_for_timeout(500)
    page.wait_for_load_state("domcontentloaded", timeout=30_000)
    if page.locator('input[type="password"]').count():
        raise RuntimeError("web_authentication_failed")


def _wait_for_web_auth(page, *, timeout_s: float = 20.0) -> None:
    deadline = time.monotonic() + timeout_s
    last_status: int | str = "unknown"
    while time.monotonic() < deadline:
        result = page.evaluate(
            """async () => {
                const response = await fetch('/api/projects', { cache: 'no-store' });
                return { ok: response.ok, status: response.status };
            }"""
        )
        if isinstance(result, dict):
            last_status = result.get("status", "unknown")
            if result.get("ok"):
                return
        page.wait_for_timeout(250)
    raise RuntimeError(f"web_trusted_session_not_ready:{last_status}")


def _first_identifier(payload: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


@dataclass
class TimingLedger:
    milestones: dict[str, int] = field(default_factory=dict)
    response_events: list[dict[str, Any]] = field(default_factory=list)
    event_topics: Counter[str] = field(default_factory=Counter)
    event_times_ms: list[int] = field(default_factory=list)

    def mark(self, key: str, elapsed_ms: int) -> None:
        if key not in self.milestones:
            self.milestones[key] = max(0, int(elapsed_ms))


class EngineObserver:
    def __init__(self, engine_url: str, session_id: str, started_epoch: float, ledger: TimingLedger):
        self.api_base = _engine_api_base(engine_url)
        self.session_id = session_id
        self.started_epoch = started_epoch
        self.ledger = ledger
        self.latest_seq = 0
        self.events: list[dict[str, Any]] = []
        self.runs: list[dict[str, Any]] = []
        self.episodes: list[dict[str, Any]] = []
        self.handoffs: list[dict[str, Any]] = []
        self.artifacts: list[dict[str, Any]] = []
        self.milestone_episode_ids_to_ignore: set[str] = set()
        self.milestone_artifact_ids_to_ignore: set[str] = set()

    def poll_resilient(self, elapsed_ms: int) -> bool:
        try:
            self.poll(elapsed_ms)
            return True
        except Exception as exc:  # noqa: BLE001 - live harness records transient local API failures.
            self.ledger.response_events.append(
                {
                    "category": "engine_observer_error",
                    "status": type(exc).__name__,
                    "elapsedMs": max(0, int(elapsed_ms)),
                }
            )
            return False

    def poll(self, elapsed_ms: int) -> None:
        events_payload = _json_request(
            f"{self.api_base}/sessions/{urllib.parse.quote(self.session_id)}/runtime-events"
            f"?after_seq={self.latest_seq}&limit=500",
            timeout=8.0,
        )
        new_events = _as_list(events_payload, "events")
        for event in new_events:
            try:
                self.latest_seq = max(self.latest_seq, int(event.get("seq") or 0))
            except (TypeError, ValueError):
                pass
            topic = _event_topic(event)
            if topic:
                self.ledger.event_topics[topic] += 1
            event_ms = _event_elapsed_ms(event, self.started_epoch, elapsed_ms)
            self.ledger.event_times_ms.append(event_ms)
            self._mark_event_milestones(topic, event, event_ms)
            self.events.append(event)

        runs_payload = _json_request(
            f"{self.api_base}/runs?{urllib.parse.urlencode({'session_id': self.session_id, 'limit': 20})}",
            timeout=8.0,
        )
        self.runs = _as_list(runs_payload, "runs")
        if self.runs:
            self.ledger.mark("engine_run_visible", elapsed_ms)

        overview = _json_request(f"{self.api_base}/runtime-episodes/overview?limit=300", timeout=8.0)
        self.episodes = [
            item
            for item in _as_list(overview, "episodes")
            if str(item.get("sessionId") or item.get("session_id") or "") == self.session_id
        ]
        episode_ids = {
            str(item.get("episodeId") or item.get("episode_id") or item.get("id") or "")
            for item in self.episodes
        }
        self.handoffs = [
            item
            for item in _as_list(overview, "handoffs")
            if str(item.get("episodeId") or item.get("episode_id") or "") in episode_ids
        ]
        for episode in self.episodes:
            episode_id = _first_identifier(episode, "episodeId", "episode_id", "id")
            if episode_id in self.milestone_episode_ids_to_ignore:
                continue
            kind = str(episode.get("kind") or episode.get("runtimeKind") or "").strip().lower()
            target = str(episode.get("targetKind") or episode.get("target_kind") or "").strip().lower()
            combined = f"{kind} {target}"
            if "research" in combined:
                self.ledger.mark("engine_research_episode", elapsed_ms)
            if "engineering" in combined:
                self.ledger.mark("engine_engineering_episode", elapsed_ms)
            if "delegation" in combined or "subagent" in combined:
                self.ledger.mark("engine_delegation_episode", elapsed_ms)

        artifacts_payload = _json_request(
            f"{self.api_base}/sessions/{urllib.parse.quote(self.session_id)}/artifacts?limit=100",
            timeout=8.0,
        )
        self.artifacts = _as_list(artifacts_payload, "artifacts")
        if any(
            _first_identifier(item, "artifactId", "artifact_id", "id")
            not in self.milestone_artifact_ids_to_ignore
            for item in self.artifacts
        ):
            self.ledger.mark("engine_artifact", elapsed_ms)

    def _mark_event_milestones(
        self,
        topic: str,
        event: dict[str, Any],
        elapsed_ms: int,
    ) -> None:
        lower = topic.lower()
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        episode = payload.get("episode") if isinstance(payload.get("episode"), dict) else {}
        progress = payload.get("progress") if isinstance(payload.get("progress"), dict) else {}
        timeline_node = progress.get("timelineNode") if isinstance(progress.get("timelineNode"), dict) else {}
        tool = payload.get("tool") if isinstance(payload.get("tool"), dict) else {}
        routing_signal = " ".join(
            str(value or "").strip().lower()
            for value in (
                lower,
                payload.get("runtimeId"),
                payload.get("runtimeKind"),
                payload.get("ownerRuntimeId"),
                episode.get("kind"),
                episode.get("runtimeKind"),
                timeline_node.get("topic"),
                tool.get("toolName"),
            )
            if str(value or "").strip()
        )
        self.ledger.mark("engine_first_event", elapsed_ms)
        if lower in {"agent.started", "run.reasoning.delta", "run.text.delta", "run.completed", "run.failed"}:
            self.ledger.mark("engine_first_supervisor_activity", elapsed_ms)
        if "research" in routing_signal:
            self.ledger.mark("engine_first_research_activity", elapsed_ms)
        if "engineering" in routing_signal:
            self.ledger.mark("engine_first_engineering_activity", elapsed_ms)
        if "delegation" in routing_signal or "subagent" in routing_signal:
            self.ledger.mark("engine_first_delegation_activity", elapsed_ms)
        if lower.startswith("artifact.") or lower == "workbench.document.opened" or tool.get("toolName") == "write_native_file":
            self.ledger.mark("engine_first_artifact_activity", elapsed_ms)

    def latest_run(self) -> dict[str, Any] | None:
        return self.runs[0] if self.runs else None


def _install_dom_observer(page) -> None:
    page.evaluate(
        """() => {
            window.__v8LinkedAudit = {
                startedAt: performance.now(),
                mutations: [],
            };
            const record = (initial = false) => {
                const audit = window.__v8LinkedAudit;
                if (!audit || audit.mutations.length >= 4000) return;
                const body = document.body?.innerText || '';
                audit.mutations.push({
                    at: performance.now() - audit.startedAt,
                    initial,
                    textLength: body.length,
                    assistantCount: document.querySelectorAll('.group.mx-auto.mb-6.flex.w-full.max-w-4xl').length,
                    contextResourceCount: document.querySelectorAll('[data-v8-context-resource]').length,
                    runtimeEntryCount: document.querySelectorAll('[data-runtime-activity-runtime]').length,
                    runtimeEventCount: document.querySelectorAll('[data-runtime-activity-seq]').length,
                    toolVisible: /runtime_broker|research_broker|web_broker|write_native_file|run_system_command|delegation_broker/i.test(body),
                    researchVisible: /深度调研|Research Runtime|Research/i.test(body),
                    engineeringVisible: /编程模式|Engineering Runtime|Implementation Engineer/i.test(body),
                });
            };
            new MutationObserver(() => record(false)).observe(document.body, {
                subtree: true,
                childList: true,
                characterData: true,
                attributes: true,
                attributeFilter: ['aria-busy', 'data-state'],
            });
            record(true);
        }"""
    )


def _dom_audit(page) -> dict[str, Any]:
    result = page.evaluate("() => window.__v8LinkedAudit || { mutations: [] }")
    return result if isinstance(result, dict) else {"mutations": []}


def _derive_dom_milestones(ledger: TimingLedger, audit: dict[str, Any], baseline_assistant_count: int) -> None:
    mutations = audit.get("mutations") if isinstance(audit.get("mutations"), list) else []
    for item in mutations:
        if not isinstance(item, dict):
            continue
        elapsed_ms = int(float(item.get("at") or 0))
        if not item.get("initial"):
            ledger.mark("web_first_dom_mutation", elapsed_ms)
        if int(item.get("assistantCount") or 0) > baseline_assistant_count:
            ledger.mark("web_first_assistant_surface", elapsed_ms)
        if item.get("toolVisible"):
            ledger.mark("web_first_tool_surface", elapsed_ms)
        if item.get("researchVisible"):
            ledger.mark("web_first_research_surface", elapsed_ms)
        if item.get("engineeringVisible"):
            ledger.mark("web_first_engineering_surface", elapsed_ms)
        if int(item.get("contextResourceCount") or 0) > 0:
            ledger.mark("web_first_artifact_card", elapsed_ms)
        if int(item.get("runtimeEntryCount") or 0) > 0:
            ledger.mark("web_first_runtime_activity_entry", elapsed_ms)
        if int(item.get("runtimeEventCount") or 0) > 0:
            ledger.mark("web_first_runtime_activity_event", elapsed_ms)


def _open_runtime_activity(page, runtime_id: str) -> dict[str, Any]:
    detail = page.locator(f'[data-runtime-activity-detail="{runtime_id}"]')
    restored_without_reopen = detail.count() > 0
    row = page.locator(f'[data-runtime-activity-runtime="{runtime_id}"]')
    entry_visible = row.count() > 0 or restored_without_reopen
    if not restored_without_reopen and row.count() > 0:
        row.first.click()
        detail.wait_for(state="visible", timeout=8_000)
        page.wait_for_timeout(250)
    events = detail.locator("[data-runtime-activity-seq]") if detail.count() else None
    event_count = events.count() if events is not None else 0
    sequences: list[int] = []
    topics: list[str] = []
    if events is not None:
        for index in range(event_count):
            item = events.nth(index)
            raw_seq = item.get_attribute("data-runtime-activity-seq") or ""
            if raw_seq.isdigit():
                sequences.append(int(raw_seq))
            topic = str(item.get_attribute("data-runtime-activity-topic") or "").strip()
            if topic and topic not in topics:
                topics.append(topic)
    motion = detail.locator("[data-runtime-activity-motion]") if detail.count() else None
    return {
        "entryVisible": entry_visible,
        "detailVisible": detail.count() > 0,
        "restoredWithoutReopen": restored_without_reopen,
        "eventCount": event_count,
        "firstSeq": min(sequences) if sequences else None,
        "lastSeq": max(sequences) if sequences else None,
        "topicCount": len(topics),
        "topicDigests": [_digest(topic) for topic in topics[:24]],
        "hasSearchOrRead": any("source_search" in topic or "source_read" in topic for topic in topics),
        "activeMotionCount": motion.count() if motion is not None else 0,
    }


def _activate_workbench_overview(page) -> None:
    overview = page.get_by_role("tab", name=re.compile(r"^(?:概览|Overview)$", re.IGNORECASE))
    if overview.count():
        overview.first.click()
        page.wait_for_timeout(150)


def _exercise_artifact_disclosures(page) -> dict[str, Any]:
    results: dict[str, Any] = {}
    for surface, disclosure_selector, row_selector in (
        ("message", '[data-artifact-disclosure="message"]', "[data-v8-context-resource]"),
        ("workbench", '[data-artifact-disclosure="workbench"]', "[data-session-output-row]"),
    ):
        disclosure = page.locator(disclosure_selector).first
        if disclosure.count() == 0:
            results[surface] = {"present": False}
            continue
        container = disclosure.locator("xpath=..")
        collapsed_count = container.locator(row_selector).count()
        disclosure.click()
        page.wait_for_timeout(150)
        expanded_count = container.locator(row_selector).count()
        expanded_state = disclosure.get_attribute("aria-expanded")
        disclosure.click()
        page.wait_for_timeout(150)
        collapsed_again_count = container.locator(row_selector).count()
        results[surface] = {
            "present": True,
            "collapsedCount": collapsed_count,
            "expandedCount": expanded_count,
            "collapsedAgainCount": collapsed_again_count,
            "expandedState": expanded_state,
        }
    return results


def _network_category(url: str) -> str | None:
    for marker, category in (
        ("/api/chat-submit", "chat_submit"),
        ("/api/realtime/sessions/", "realtime"),
        ("/api/artifacts", "artifacts"),
        ("/processes", "processes"),
        ("/api/conversations/", "conversation"),
    ):
        if marker in url:
            return category
    return None


def _sanitize_artifacts(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in items:
        relative = str(
            item.get("workspaceRelativePath")
            or item.get("workspace_relative_path")
            or item.get("filename")
            or item.get("name")
            or ""
        ).replace("\\", "/")
        result.append(
            {
                "idDigest": _digest(str(item.get("id") or item.get("artifactId") or "")),
                "relativePath": relative,
                "status": str(item.get("status") or ""),
                "kind": str(item.get("kind") or item.get("artifactKind") or ""),
            }
        )
    return result


def _sanitize_episode(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "idDigest": _digest(str(item.get("episodeId") or item.get("episode_id") or item.get("id") or "")),
        "kind": str(item.get("kind") or item.get("runtimeKind") or ""),
        "targetKind": str(item.get("targetKind") or item.get("target_kind") or ""),
        "state": str(item.get("state") or item.get("status") or ""),
        "error": str(item.get("error") or item.get("errorCode") or item.get("error_code") or ""),
    }


def _max_gap(values: list[int]) -> int | None:
    ordered = sorted(set(values))
    if len(ordered) < 2:
        return None
    return max(right - left for left, right in zip(ordered, ordered[1:]))


def inspect_existing(
    *,
    state_file: Path | None,
    engine_url: str,
    session_id: str = "",
) -> dict[str, Any]:
    state = json.loads(state_file.read_text(encoding="utf-8")) if state_file is not None else {}
    session_id = str(session_id or state.get("sessionId") or "").strip()
    if not session_id:
        raise RuntimeError("state_file_missing_session_id")
    api_base = _engine_api_base(engine_url)
    runs_payload = _json_request(
        f"{api_base}/runs?{urllib.parse.urlencode({'session_id': session_id, 'limit': 20})}",
        timeout=10.0,
    )
    runs = _as_list(runs_payload, "runs")
    run_id = _first_identifier(runs[0], "id", "runId", "run_id") if runs else ""
    if not run_id:
        raise RuntimeError("state_session_has_no_run")

    observability_path = (Path.home() / ".v8-agent-os" / "observability.db").resolve()
    connection = sqlite3.connect(f"file:{observability_path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        invocation_rows = connection.execute(
            """
            SELECT provider_id, model_id, role, request_kind, status,
                   input_tokens, output_tokens, total_tokens, latency_ms,
                   error_code, error_message, metadata_json
            FROM model_invocation_logs
            WHERE run_id = ?
            ORDER BY started_at ASC
            """,
            (run_id,),
        ).fetchall()
        provider_rows = connection.execute(
            """
            SELECT provider_id, model_id, status, latency_ms, error_code,
                   error_message, detail_json
            FROM provider_health_logs
            WHERE run_id = ?
            ORDER BY created_at ASC
            """,
            (run_id,),
        ).fetchall()
    finally:
        connection.close()

    safe_invocations: list[dict[str, Any]] = []
    telemetry_keys = (
        "timeToFirstTokenMs",
        "firstContentMs",
        "streamChunkCount",
        "streamCharacterCount",
        "maxInterChunkGapMs",
        "finishReason",
        "toolCallCount",
        "streamUsageRequested",
        "usageReported",
        "requestedMaxTokens",
        "toolCallingMode",
        "providerAdapter",
        "effectiveCapabilityMatrix",
        "timeToFirstChunkMs",
        "timeToFirstContentChunkMs",
        "streamActiveMs",
        "tailAfterLastChunkMs",
        "streamChunkCharCount",
    )
    for row in invocation_rows:
        item = dict(row)
        error_message = str(item.pop("error_message") or "")
        try:
            metadata = json.loads(str(item.pop("metadata_json") or "{}"))
        except json.JSONDecodeError:
            metadata = {}
        safe_invocations.append(
            {
                **item,
                "errorDigest": _digest(error_message) if error_message else "",
                "errorLength": len(error_message),
                "httpCodes": sorted(set(re.findall(r"(?<!\d)(?:4\d\d|5\d\d)(?!\d)", error_message))),
                "metadataKeys": sorted(metadata.keys()) if isinstance(metadata, dict) else [],
                "telemetry": {
                    key: metadata.get(key)
                    for key in telemetry_keys
                    if isinstance(metadata, dict) and key in metadata
                },
            }
        )

    safe_provider_health: list[dict[str, Any]] = []
    for row in provider_rows:
        item = dict(row)
        error_message = str(item.pop("error_message") or "")
        detail_raw = str(item.pop("detail_json") or "")
        safe_provider_health.append(
            {
                **item,
                "errorDigest": _digest(error_message) if error_message else "",
                "errorLength": len(error_message),
                "httpCodes": sorted(set(re.findall(r"(?<!\d)(?:4\d\d|5\d\d)(?!\d)", error_message))),
                "detailDigest": _digest(detail_raw) if detail_raw else "",
                "detailLength": len(detail_raw),
            }
        )

    result = {
        "sessionIdDigest": _digest(session_id),
        "runIdDigest": _digest(run_id),
        "runStatus": str(runs[0].get("status") or ""),
        "invocations": safe_invocations,
        "providerHealth": safe_provider_health,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def _findings(
    *,
    run: dict[str, Any] | None,
    workspace_files: list[str],
    observer: EngineObserver,
    ledger: TimingLedger,
    before_reload_text: str,
    after_reload_text: str,
    runtime_activity_live: dict[str, Any],
    runtime_activity_reloaded: dict[str, Any],
    artifact_disclosures: dict[str, Any],
) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []

    def add(severity: str, code: str, summary: str) -> None:
        findings.append({"severity": severity, "code": code, "summary": summary})

    status = str((run or {}).get("status") or "missing").lower()
    if status != "completed":
        add("P0", "run_not_completed", f"真实 Supervisor run 终态为 {status}")
    if not any(path == "package.json" for path in workspace_files):
        add("P0", "react_project_not_written", "工作区没有 package.json")
    if not any(path.startswith("src/") for path in workspace_files):
        add("P0", "react_source_not_written", "工作区没有 React src 源码")

    kinds = " ".join(
        f"{item.get('kind', '')} {item.get('targetKind', '')} {item.get('target_kind', '')}".lower()
        for item in observer.episodes
    )
    if "research" not in kinds:
        add("P1", "research_episode_missing", "需要权威联网证据的任务未建立 Research episode")
    else:
        if not runtime_activity_live.get("entryVisible"):
            add("P1", "research_runtime_detail_missing", "Research episode 已运行，但右栏没有运行过程入口")
        elif int(runtime_activity_live.get("eventCount") or 0) < 3:
            add("P1", "research_runtime_detail_empty", "Research 运行详情没有形成可用的线性事件")
        elif not runtime_activity_live.get("hasSearchOrRead"):
            add("P2", "research_runtime_detail_too_generic", "Research 运行详情缺少网页搜索或读取阶段")
        if int(runtime_activity_reloaded.get("eventCount") or 0) < min(
            3,
            int(runtime_activity_live.get("eventCount") or 0),
        ):
            add("P1", "research_runtime_detail_lost_on_reload", "Research 线性事件在刷新后丢失")
    if "engineering" not in kinds:
        add("P1", "engineering_episode_missing", "需要落盘 React 项目的任务未建立 Engineering episode")
    if observer.artifacts and not ledger.milestones.get("web_first_artifact_card"):
        add("P1", "live_artifact_projection_missing", "Engine 已有产物，但 live Web 未显示产物卡")
    if len(observer.artifacts) > 5:
        for surface in ("message", "workbench"):
            disclosure = artifact_disclosures.get(surface) if isinstance(artifact_disclosures, dict) else None
            if not isinstance(disclosure, dict) or not disclosure.get("present"):
                add("P2", f"{surface}_artifact_disclosure_missing", f"{surface} 产物超过5项但没有展开入口")
                continue
            if int(disclosure.get("collapsedCount") or 0) > 5:
                add("P2", f"{surface}_artifact_default_overflow", f"{surface} 默认展示超过5项产物")
            if int(disclosure.get("expandedCount") or 0) <= int(disclosure.get("collapsedCount") or 0):
                add("P2", f"{surface}_artifact_disclosure_inert", f"{surface} 展开入口没有陈列更多产物")
            if int(disclosure.get("collapsedAgainCount") or 0) != int(disclosure.get("collapsedCount") or 0):
                add("P2", f"{surface}_artifact_recollapse_failed", f"{surface} 展开后不能恢复折叠状态")

    artifact_names = [item.get("relativePath", "") for item in _sanitize_artifacts(observer.artifacts)]
    expected_visible = [name for name in artifact_names if name]
    if expected_visible:
        before_hits = sum(name in before_reload_text for name in expected_visible)
        after_hits = sum(name in after_reload_text for name in expected_visible)
        if before_hits < after_hits:
            add("P1", "artifact_projection_requires_reload", "产物只在刷新后补齐，live/history 投影不一致")
        elif after_hits == 0:
            add("P1", "artifact_projection_absent_after_reload", "刷新后仍未投影 Engine 产物")

    web_first = ledger.milestones.get("web_first_dom_mutation")
    engine_first = ledger.milestones.get("engine_first_event")
    if web_first is None:
        add("P1", "web_no_live_mutation", "提交后 Web 页面没有记录到任何 live DOM 更新")
    elif web_first > 1500:
        add("P2", "web_running_feedback_slow", f"提交后首个页面变化耗时 {web_first}ms")
    if engine_first is not None and ledger.milestones.get("web_first_assistant_surface") is None:
        add("P1", "assistant_projection_missing", "Engine 已开始运行，但 Web 没有出现 Supervisor 消息面")
    return findings


def run_initial(
    *,
    web_url: str,
    engine_url: str,
    output_root: Path,
    timeout_s: float,
    headless: bool,
) -> tuple[dict[str, Any], int]:
    _wait_for_endpoint(f"{engine_url.rstrip('/')}/health")
    _wait_for_endpoint(f"{web_url.rstrip('/')}/api/connection")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    case_root = (output_root / stamp).resolve()
    workspace = case_root / "workspace"
    evidence_root = case_root / "evidence"
    workspace.mkdir(parents=True, exist_ok=False)
    evidence_root.mkdir(parents=True, exist_ok=False)

    console_errors: list[str] = []
    page_errors: list[str] = []
    ledger = TimingLedger()
    before_reload_text = ""
    after_reload_text = ""
    snapshot_before: dict[str, Any] = {}
    snapshot_after: dict[str, Any] = {}
    web_artifacts: dict[str, Any] = {}
    runtime_activity_active: dict[str, Any] = {}
    runtime_activity_live: dict[str, Any] = {}
    runtime_activity_reloaded: dict[str, Any] = {}
    artifact_disclosures: dict[str, Any] = {}

    with sync_playwright() as playwright:
        edge = Path(os.environ.get("V8_EDGE_PATH") or "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")
        browser = playwright.chromium.launch(
            executable_path=str(edge) if edge.is_file() else None,
            headless=headless,
            args=["--no-proxy-server"],
        )
        context = browser.new_context(viewport={"width": 1600, "height": 1000})
        page = context.new_page()
        page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
        page.on("pageerror", lambda error: page_errors.append(str(error)))

        page.goto(f"{web_url.rstrip('/')}/chat", wait_until="domcontentloaded", timeout=30_000)
        _authenticate_if_needed(page)
        _wait_for_web_auth(page)

        project = _post_web_payload(
            page,
            "/api/projects",
            {
                "name": f"Supervisor linked live {stamp}",
                "description": "Disposable governed live acceptance workspace",
                "workspacePath": str(workspace),
                "workspaceTrustState": "trusted",
                "workspaceTrustSource": "user_confirmed_live_harness",
                "tags": ["live_harness", "supervisor_project_linked"],
            },
        )
        project_id = _first_identifier(project, "id", "projectId", "project_id")
        workspace_id = _first_identifier(project, "workspaceId", "workspace_id") or project_id
        if not project_id:
            raise RuntimeError("project_creation_missing_id")

        conversation = _post_web_payload(
            page,
            "/api/conversations",
            {
                "title": f"联动验收 {stamp}",
                "projectId": project_id,
                "workspaceId": workspace_id,
                "workspacePath": str(workspace),
                "scopeHint": "project",
                "scopeMode": "explicit",
            },
        )
        session_id = _first_identifier(conversation, "id", "sessionId", "session_id")
        if not session_id:
            raise RuntimeError("conversation_creation_missing_id")

        page.goto(
            f"{web_url.rstrip('/')}/chat?{urllib.parse.urlencode({'id': session_id})}",
            wait_until="domcontentloaded",
            timeout=30_000,
        )
        composer = page.locator('textarea[data-v8os-chat-composer="true"]')
        composer.wait_for(state="visible", timeout=30_000)
        baseline_assistant_count = page.locator(".group.mx-auto.mb-6.flex.w-full.max-w-4xl").count()
        _install_dom_observer(page)
        started_epoch = time.time()
        started_monotonic = time.monotonic()

        def elapsed_ms() -> int:
            return int((time.monotonic() - started_monotonic) * 1000)

        def record_response(response) -> None:
            category = _network_category(response.url)
            if not category:
                return
            at = elapsed_ms()
            ledger.response_events.append({"category": category, "status": response.status, "elapsedMs": at})
            ledger.mark(f"web_first_{category}_response", at)

        page.on("response", record_response)
        composer.fill(INITIAL_PROMPT)
        form = composer.locator("xpath=ancestor::form")
        with page.expect_response(lambda response: "/api/chat-submit" in response.url, timeout=45_000) as submit_info:
            form.locator('button[type="submit"]').last.click()
        submit_response = submit_info.value
        submit_payload = submit_response.json()
        ledger.mark("web_chat_submit_response", elapsed_ms())
        run_id = _first_identifier(submit_payload if isinstance(submit_payload, dict) else {}, "runId", "run_id", "id")

        observer = EngineObserver(engine_url, session_id, started_epoch, ledger)
        last_heartbeat = -10.0
        terminal_seen_at: float | None = None
        while time.monotonic() - started_monotonic < timeout_s:
            page.wait_for_timeout(250)
            now_elapsed = elapsed_ms()
            observer.poll_resilient(now_elapsed)

            current_files = _relative_workspace_files(workspace)
            if current_files:
                ledger.mark("workspace_first_file", now_elapsed)
            if any(path == "package.json" for path in current_files):
                ledger.mark("workspace_package_json", now_elapsed)
            if any(path.startswith("src/") for path in current_files):
                ledger.mark("workspace_react_source", now_elapsed)

            run = observer.latest_run()
            if run and not run_id:
                run_id = _first_identifier(run, "id", "runId", "run_id")
            run_status = str((run or {}).get("status") or "pending").lower()
            if not runtime_activity_active and page.locator('[data-runtime-activity-runtime="research"]').count():
                runtime_activity_active = _open_runtime_activity(page, "research")
                page.screenshot(path=str(evidence_root / "research-runtime-active.png"), full_page=True)
                _activate_workbench_overview(page)
            if run_status in TERMINAL_RUN_STATES:
                if terminal_seen_at is None:
                    terminal_seen_at = time.monotonic()
                    ledger.mark("engine_run_terminal", now_elapsed)
                if time.monotonic() - terminal_seen_at >= 3.0:
                    break

            elapsed_s = time.monotonic() - started_monotonic
            if elapsed_s - last_heartbeat >= 10.0:
                last_heartbeat = elapsed_s
                print(
                    json.dumps(
                        {
                            "kind": "heartbeat",
                            "elapsedSeconds": round(elapsed_s, 1),
                            "runStatus": run_status,
                            "eventCount": len(observer.events),
                            "episodeKinds": sorted(
                                {
                                    str(item.get("kind") or item.get("runtimeKind") or "unknown")
                                    for item in observer.episodes
                                }
                            ),
                            "artifactCount": len(observer.artifacts),
                            "workspaceFileCount": len(current_files),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )

        dom_audit = _dom_audit(page)
        _derive_dom_milestones(ledger, dom_audit, baseline_assistant_count)
        before_reload_text = page.locator("body").inner_text()
        snapshot_before = _safe_web_payload(page, f"/api/realtime/sessions/{urllib.parse.quote(session_id)}/snapshot")
        web_artifacts = _safe_web_payload(page, f"/api/artifacts?sessionId={urllib.parse.quote(session_id)}&limit=100")
        _activate_workbench_overview(page)
        artifact_disclosures = _exercise_artifact_disclosures(page)
        page.screenshot(path=str(evidence_root / "initial-terminal-live.png"), full_page=True)
        runtime_activity_live = _open_runtime_activity(page, "research")
        page.screenshot(path=str(evidence_root / "research-runtime-terminal.png"), full_page=True)
        _activate_workbench_overview(page)

        page.reload(wait_until="domcontentloaded", timeout=30_000)
        try:
            page.wait_for_load_state("networkidle", timeout=8_000)
        except PlaywrightTimeoutError:
            pass
        page.wait_for_timeout(2_000)
        after_reload_text = page.locator("body").inner_text()
        snapshot_after = _safe_web_payload(page, f"/api/realtime/sessions/{urllib.parse.quote(session_id)}/snapshot")
        page.screenshot(path=str(evidence_root / "initial-terminal-reloaded.png"), full_page=True)
        runtime_activity_reloaded = _open_runtime_activity(page, "research")
        page.screenshot(path=str(evidence_root / "research-runtime-reloaded.png"), full_page=True)
        browser.close()

    run = observer.latest_run()
    workspace_files = _relative_workspace_files(workspace)
    findings = _findings(
        run=run,
        workspace_files=workspace_files,
        observer=observer,
        ledger=ledger,
        before_reload_text=before_reload_text,
        after_reload_text=after_reload_text,
        runtime_activity_live=runtime_activity_live,
        runtime_activity_reloaded=runtime_activity_reloaded,
        artifact_disclosures=artifact_disclosures,
    )
    event_times = sorted(ledger.event_times_ms)
    result = {
        "schemaVersion": 1,
        "createdAt": _utc_now(),
        "case": "natural_research_and_react_project",
        "live": True,
        "session": {
            "idDigest": _digest(session_id),
            "projectIdDigest": _digest(project_id),
            "runIdDigest": _digest(run_id),
        },
        "prompt": {"sha256Prefix": _digest(INITIAL_PROMPT), "characterCount": len(INITIAL_PROMPT)},
        "run": {
            "status": str((run or {}).get("status") or "missing"),
            "error": str((run or {}).get("error") or (run or {}).get("error_message") or ""),
        },
        "timingsMs": dict(sorted(ledger.milestones.items())),
        "engineEventMaxGapMs": _max_gap(event_times),
        "engineEventCount": len(observer.events),
        "engineTopics": dict(ledger.event_topics.most_common()),
        "episodes": [_sanitize_episode(item) for item in observer.episodes],
        "handoffs": {
            "count": len(observer.handoffs),
            "statuses": dict(Counter(str(item.get("status") or "unknown") for item in observer.handoffs)),
        },
        "artifacts": _sanitize_artifacts(observer.artifacts),
        "web": {
            "responseEvents": ledger.response_events,
            "consoleErrorDigests": [_digest(item) for item in console_errors],
            "pageErrorDigests": [_digest(item) for item in page_errors],
            "snapshotBeforeDigest": _digest(_text(snapshot_before)),
            "snapshotAfterDigest": _digest(_text(snapshot_after)),
            "artifactPayloadDigest": _digest(_text(web_artifacts)),
            "bodyBeforeDigest": _digest(before_reload_text),
            "bodyAfterDigest": _digest(after_reload_text),
            "bodyBeforeLength": len(before_reload_text),
            "bodyAfterLength": len(after_reload_text),
            "runtimeActivity": {
                "active": runtime_activity_active,
                "terminal": runtime_activity_live,
                "reloaded": runtime_activity_reloaded,
            },
            "artifactDisclosures": artifact_disclosures,
        },
        "workspaceFiles": workspace_files,
        "findings": findings,
    }
    report_path = case_root / "initial-report.json"
    state_path = case_root / "state.json"
    report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    state_path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "sessionId": session_id,
                "projectId": project_id,
                "workspaceId": workspace_id,
                "workspacePath": str(workspace),
                "caseRoot": str(case_root),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    summary = {
        "ok": not any(item["severity"] in {"P0", "P1"} for item in findings),
        "report": str(report_path),
        "state": str(state_path),
        "runStatus": result["run"]["status"],
        "timingsMs": result["timingsMs"],
        "episodeCount": len(observer.episodes),
        "artifactCount": len(observer.artifacts),
        "workspaceFileCount": len(workspace_files),
        "findings": findings,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return result, 0 if summary["ok"] else 1


def run_followup(
    *,
    phase: str,
    state_file: Path,
    web_url: str,
    engine_url: str,
    timeout_s: float,
    headless: bool,
) -> tuple[dict[str, Any], int]:
    state = json.loads(state_file.read_text(encoding="utf-8"))
    session_id = str(state.get("sessionId") or "").strip()
    workspace = Path(str(state.get("workspacePath") or "")).resolve()
    case_root = Path(str(state.get("caseRoot") or state_file.parent)).resolve()
    if not session_id or not workspace.is_dir():
        raise RuntimeError("followup_state_is_incomplete")
    prompt = DEBUG_PROMPTS[phase]
    evidence_root = case_root / "evidence"
    evidence_root.mkdir(parents=True, exist_ok=True)
    before_hashes = _workspace_hashes(workspace)
    ledger = TimingLedger()
    observer = EngineObserver(engine_url, session_id, time.time(), ledger)
    # Drain every historical event page before establishing the follow-up
    # baseline. A busy prior run can exceed one 500-event page.
    for _ in range(32):
        before_count = len(observer.events)
        if not observer.poll_resilient(0):
            time.sleep(0.25)
            continue
        if len(observer.events) - before_count < 500:
            break
    baseline_event_count = len(observer.events)
    baseline_episode_ids = {
        _first_identifier(item, "episodeId", "episode_id", "id") for item in observer.episodes
    }
    baseline_handoff_ids = {
        _first_identifier(item, "handoffId", "handoff_id", "id") for item in observer.handoffs
    }
    baseline_artifact_ids = {
        _first_identifier(item, "artifactId", "artifact_id", "id") for item in observer.artifacts
    }
    observer.milestone_episode_ids_to_ignore = set(baseline_episode_ids)
    observer.milestone_artifact_ids_to_ignore = set(baseline_artifact_ids)
    ledger.milestones.clear()
    ledger.response_events.clear()
    ledger.event_topics.clear()
    ledger.event_times_ms.clear()
    started_epoch = time.time()
    started_monotonic = time.monotonic()
    observer.started_epoch = started_epoch
    run_id = ""
    run: dict[str, Any] | None = None
    dom_audit: dict[str, Any] = {"mutations": []}
    console_errors: list[str] = []
    page_errors: list[str] = []

    with sync_playwright() as playwright:
        edge = Path(os.environ.get("V8_EDGE_PATH") or "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")
        browser = playwright.chromium.launch(
            executable_path=str(edge) if edge.is_file() else None,
            headless=headless,
            args=["--no-proxy-server"],
        )
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.goto(
            f"{web_url.rstrip('/')}/chat?{urllib.parse.urlencode({'id': session_id})}",
            wait_until="domcontentloaded",
            timeout=30_000,
        )
        _authenticate_if_needed(page)
        _wait_for_web_auth(page)
        page.goto(
            f"{web_url.rstrip('/')}/chat?{urllib.parse.urlencode({'id': session_id})}",
            wait_until="domcontentloaded",
            timeout=30_000,
        )
        composer = page.locator('textarea[data-v8os-chat-composer="true"]')
        composer.wait_for(state="visible", timeout=30_000)
        baseline_assistant_count = page.locator(".group.mx-auto.mb-6.flex.w-full.max-w-4xl").count()
        _install_dom_observer(page)
        composer.fill(prompt)
        form = composer.locator("xpath=ancestor::form")
        with page.expect_response(lambda response: "/api/chat-submit" in response.url, timeout=45_000) as submit_info:
            form.locator('button[type="submit"]').last.click()
        submit_payload = submit_info.value.json()
        run_id = _first_identifier(submit_payload if isinstance(submit_payload, dict) else {}, "runId", "run_id", "id")
        ledger.mark("web_chat_submit_response", int((time.monotonic() - started_monotonic) * 1000))
        terminal_seen_at: float | None = None
        while time.monotonic() - started_monotonic < timeout_s:
            page.wait_for_timeout(250)
            elapsed_ms = int((time.monotonic() - started_monotonic) * 1000)
            observer.poll_resilient(elapsed_ms)
            if not run_id and observer.runs:
                run_id = _first_identifier(observer.runs[0], "id", "runId", "run_id")
            run = next(
                (
                    item
                    for item in observer.runs
                    if _first_identifier(item, "id", "runId", "run_id") == run_id
                ),
                None,
            )
            status = str((run or {}).get("status") or "").lower()
            if status in TERMINAL_RUN_STATES:
                if terminal_seen_at is None:
                    terminal_seen_at = time.monotonic()
                    ledger.mark("engine_run_terminal", elapsed_ms)
                if time.monotonic() - terminal_seen_at >= 3.0:
                    break
        dom_audit = _dom_audit(page)
        _derive_dom_milestones(ledger, dom_audit, baseline_assistant_count)
        page.screenshot(path=str(evidence_root / f"{phase}-terminal.png"), full_page=True)
        browser.close()

    after_hashes = _workspace_hashes(workspace)
    changed_files = sorted(
        path
        for path in set(before_hashes).union(after_hashes)
        if before_hashes.get(path) != after_hashes.get(path)
    )
    new_events = observer.events[baseline_event_count:]
    new_episodes = [
        item
        for item in observer.episodes
        if _first_identifier(item, "episodeId", "episode_id", "id") not in baseline_episode_ids
    ]
    new_handoffs = [
        item
        for item in observer.handoffs
        if _first_identifier(item, "handoffId", "handoff_id", "id") not in baseline_handoff_ids
    ]
    new_artifacts = [
        item
        for item in observer.artifacts
        if _first_identifier(item, "artifactId", "artifact_id", "id") not in baseline_artifact_ids
    ]
    event_times = [
        _event_elapsed_ms(item, started_epoch, 0)
        for item in new_events
    ]
    findings: list[dict[str, str]] = []
    run_status = str((run or {}).get("status") or "missing")
    verification_evidence = (
        _exact_command_evidence(
            new_events,
            run_id=run_id,
            command="npm test -- --run",
        )
        if phase == "debug2_verify"
        else {}
    )
    if run_status != "completed":
        findings.append({"severity": "P0", "code": "debug_run_not_completed", "summary": run_status})
    if not changed_files and phase != "debug2_verify":
        findings.append({"severity": "P1", "code": "debug_source_not_changed", "summary": "Supervisor 未落盘修复"})
    if phase == "debug2_verify" and changed_files:
        findings.append(
            {
                "severity": "P0",
                "code": "read_only_verification_modified_workspace",
                "summary": "只读验证修改了工作区：" + ", ".join(changed_files[:12]),
            }
        )
    if phase == "debug2_verify" and not verification_evidence.get("succeeded"):
        findings.append(
            {
                "severity": "P0",
                "code": "required_verification_command_missing",
                "summary": (
                    "本轮没有 npm test -- --run 的精确成功工具证据；"
                    f"started={verification_evidence.get('startedCount', 0)}, "
                    f"finished={verification_evidence.get('finishedCount', 0)}"
                ),
            }
        )
    if phase == "debug2_verify" and int(verification_evidence.get("startedCount") or 0) > 1:
        findings.append(
            {
                "severity": "P1",
                "code": "required_verification_command_repeated",
                "summary": (
                    "本轮精确验证命令被重复执行；"
                    f"started={verification_evidence.get('startedCount', 0)}, "
                    f"finished={verification_evidence.get('finishedCount', 0)}"
                ),
            }
        )
    if ledger.milestones.get("web_first_dom_mutation", 0) > 1500:
        findings.append({"severity": "P2", "code": "debug_web_feedback_slow", "summary": str(ledger.milestones["web_first_dom_mutation"])})
    result = {
        "schemaVersion": 1,
        "createdAt": _utc_now(),
        "phase": phase,
        "live": True,
        "sessionIdDigest": _digest(session_id),
        "runIdDigest": _digest(run_id),
        "prompt": {"sha256Prefix": _digest(prompt), "characterCount": len(prompt)},
        "run": {"status": run_status, "error": str((run or {}).get("error") or "")},
        "timingsMs": dict(sorted(ledger.milestones.items())),
        "engineEventCount": len(new_events),
        "engineEventMaxGapMs": _max_gap(event_times),
        "engineTopics": dict(Counter(_event_topic(item) for item in new_events)),
        "engineObserverErrorCount": sum(
            1 for item in ledger.response_events if item.get("category") == "engine_observer_error"
        ),
        "episodes": [_sanitize_episode(item) for item in new_episodes],
        "handoffs": {
            "count": len(new_handoffs),
            "statuses": dict(Counter(str(item.get("status") or "unknown") for item in new_handoffs)),
        },
        "artifacts": _sanitize_artifacts(new_artifacts),
        "changedFiles": changed_files,
        "verificationEvidence": verification_evidence,
        "web": {
            "consoleErrorDigests": [_digest(item) for item in console_errors],
            "pageErrorDigests": [_digest(item) for item in page_errors],
            "domMutationCount": len(dom_audit.get("mutations") or []),
        },
        "findings": findings,
    }
    report_path = case_root / f"{phase}-report.json"
    report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {
        "ok": not any(item["severity"] in {"P0", "P1"} for item in findings),
        "report": str(report_path),
        "runStatus": run_status,
        "timingsMs": result["timingsMs"],
        "changedFiles": changed_files,
        "findings": findings,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return result, 0 if summary["ok"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a real Web-to-Supervisor research and React project linked acceptance case."
    )
    parser.add_argument("--live", action="store_true", help="Required: consumes the configured ModelHub provider and writes a disposable project.")
    parser.add_argument(
        "--phase",
        choices=[
            "initial",
            "inspect",
            "debug1",
            "debug2",
            "debug2_retry",
            "debug2_css",
            "debug2_verify",
        ],
        default="initial",
    )
    parser.add_argument("--web-url", default=DEFAULT_WEB_URL)
    parser.add_argument("--engine-url", default=DEFAULT_ENGINE_URL)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--state-file", type=Path)
    parser.add_argument("--session-id", default="")
    parser.add_argument("--timeout", type=float, default=900.0)
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()

    if not args.live:
        print("Refusing to run without --live; this harness consumes the configured provider and writes a project.")
        return 2
    if args.phase == "inspect":
        if args.state_file is None and not str(args.session_id or "").strip():
            raise RuntimeError("--state-file or --session-id is required for inspect")
        inspect_existing(
            state_file=args.state_file.resolve() if args.state_file is not None else None,
            engine_url=args.engine_url,
            session_id=args.session_id,
        )
        return 0
    if args.phase in DEBUG_PROMPTS:
        if args.state_file is None:
            raise RuntimeError("--state-file is required for debug followups")
        _, exit_code = run_followup(
            phase=args.phase,
            state_file=args.state_file.resolve(),
            web_url=args.web_url,
            engine_url=args.engine_url,
            timeout_s=max(30.0, args.timeout),
            headless=not args.headed,
        )
        return exit_code
    _, exit_code = run_initial(
        web_url=args.web_url,
        engine_url=args.engine_url,
        output_root=args.output_root.resolve(),
        timeout_s=max(30.0, args.timeout),
        headless=not args.headed,
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
