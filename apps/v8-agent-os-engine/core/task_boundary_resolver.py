from __future__ import annotations

from typing import Any


def _text(value: Any) -> str:
    return str(value or "").strip()


def _lower(value: Any) -> str:
    return _text(value).lower()


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term and term in text for term in terms)


_NEGATION_TERMS = (
    "不要",
    "不含",
    "不包含",
    "不包括",
    "不调用",
    "不走",
    "不用",
    "禁止",
    "排除",
    "不要用",
    "不要走",
    "不需要",
    "without",
    "exclude",
    "excluding",
    "except",
    " no ",
    " not ",
    "never",
)


def _contains_positive_any(text: str, terms: tuple[str, ...]) -> bool:
    for term in terms:
        if not term:
            continue
        start = 0
        while True:
            index = text.find(term, start)
            if index < 0:
                break
            prefix = text[max(0, index - 18):index]
            if not any(negation in prefix for negation in _NEGATION_TERMS):
                return True
            start = index + len(term)
    return False


def _explicitly_excludes_runtime(text: str, runtime_terms: tuple[str, ...]) -> bool:
    """Return true when the user explicitly excludes a runtime family.

    This is intentionally stricter than generic negation: route choices are
    safety/ownership decisions, so "不含 Computer Use/RPA" must override
    broader workflow/planner signals in the same request.
    """

    for term in runtime_terms:
        if not term:
            continue
        start = 0
        while True:
            index = text.find(term, start)
            if index < 0:
                break
            prefix = text[max(0, index - 28):index]
            suffix = text[index + len(term): index + len(term) + 12]
            if any(negation in prefix for negation in _NEGATION_TERMS):
                return True
            if suffix.strip().startswith(("除外", "除去", "排除")):
                return True
            start = index + len(term)
    return False


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        item = str(value or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


_VIDEO_TERMS = (
    "video",
    "movie",
    "视频",
    "影片",
    "短片",
    "短视频",
)
_EXPLAINER_VIDEO_TERMS = (
    "explainer",
    "tutorial",
    "course",
    "lecture",
    "walkthrough",
    "product demo",
    "科普",
    "讲解",
    "课程",
    "教程",
    "解说",
    "产品介绍",
    "培训",
    "教学",
)
_CODE_VIDEO_TERMS = (
    "remotion",
    "hyperframes",
    "manim",
    "html video",
    "html 做视频",
    "代码视频",
    "代码生成视频",
)
_PROVIDER_VIDEO_TERMS = (
    "seedance",
    "doubao-seedance",
    "seedream",
    "sora",
    "veo",
    "kling",
    "runway",
    "luma",
    "图生视频",
    "文生视频",
    "参考视频",
    "首帧",
    "尾帧",
    "首尾帧",
    "视频模型",
)
_TERMINAL_TERMS = (
    "terminal",
    "console",
    "cmd",
    "powershell",
    "pwsh",
    "bash",
    "shell",
    "命令行",
    "终端",
    "控制台",
)
_TERMINAL_ACTION_TERMS = (
    "open",
    "launch",
    "start",
    "run",
    "install",
    "启动",
    "打开",
    "运行",
    "执行",
    "安装",
)
_GUI_TERMINAL_TERMS = (
    "真实终端",
    "桌面终端",
    "终端窗口",
    "让我看着",
    "可视化",
    "图形界面",
    "gui",
    "desktop terminal",
    "visible terminal",
    "登录态",
    "手动登录",
)
_BROWSER_DOM_TERMS = (
    "dom",
    "selector",
    "xpath",
    "ui snapshot",
    "raw html",
    "页面结构",
    "网页结构",
    "选择器",
    "登录态页面",
)
_RPA_TERMS = (
    "rpa",
    "录制流程",
    "对象库",
    "重复执行",
    "自动化流程",
    "可复用流程",
)
_COMPUTER_USE_RUNTIME_TERMS = (
    "computer use",
    "computer_use",
    "computer-use",
    "电脑操作",
    "桌面操作",
    "桌面控制",
    "真实桌面",
)
_RPA_RUNTIME_TERMS = (
    "rpa",
    "robot",
    "机器人流程",
    "流程自动化",
)
_DESKTOP_GUI_TERMS = (
    "桌面应用",
    "窗口",
    "点击界面",
    "真实桌面",
    "gui 应用",
    "desktop app",
)
_RESEARCH_COMPLEX_TERMS = (
    "多源",
    "带来源",
    "引用来源",
    "官方文档",
    "最新",
    "冲突",
    "交叉验证",
    "source",
    "citations",
    "fresh",
)


def resolve_task_boundary(
    user_query: str,
    *,
    task_shape_hint: dict[str, Any] | None = None,
    planner_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve ambiguous execution ownership into a compact, auditable hint.

    This resolver is intentionally deterministic and side-effect free. It is a
    routing contract helper, not a runtime grant surface.
    """

    hint = dict(task_shape_hint or {})
    text = "\n".join(part for part in (_lower(user_query), _lower(planner_plan)) if part)
    primary_shape = _text(hint.get("primaryTaskShape")) or "general_chat"
    secondary_shapes = [_text(item) for item in _as_list(hint.get("secondaryTaskShapes")) if _text(item)]
    writing_route = hint.get("writingRoute") if isinstance(hint.get("writingRoute"), dict) else {}

    primary_runtime = "supervisor"
    supporting: list[str] = []
    execution_mode = "direct_or_chat"
    reason = "default_supervisor_or_existing_task_shape"
    ask_user_needed = False
    forbidden_routes: list[str] = []
    route_corrections: list[dict[str, str]] = []
    signals: list[str] = []
    excluded_computer_use = _explicitly_excludes_runtime(text, _COMPUTER_USE_RUNTIME_TERMS)
    excluded_rpa = _explicitly_excludes_runtime(text, _RPA_RUNTIME_TERMS)
    if excluded_computer_use:
        forbidden_routes.append("computer_use_explicitly_excluded")
        signals.append("exclude:computer_use")
    if excluded_rpa:
        forbidden_routes.append("rpa_explicitly_excluded")
        signals.append("exclude:rpa")

    if primary_shape == "project_coding":
        primary_runtime = "engineering"
        execution_mode = "engineering_runtime"
        reason = "task_shape_project_coding"
    elif primary_shape == "creative_media":
        primary_runtime = "creative_media"
        execution_mode = "creative_media_provider"
        reason = "task_shape_creative_media"
    elif primary_shape == "research":
        primary_runtime = "research"
        execution_mode = "research_runtime"
        reason = "task_shape_research"
    elif primary_shape == "writing":
        mode = _text(writing_route.get("mode"))
        if mode == "artifact_runtime":
            primary_runtime = "engineering"
            execution_mode = "writing_artifact_runtime"
            reason = "writing_requires_file_or_artifact_side_effect"
        elif mode == "research_then_write":
            primary_runtime = "research"
            supporting.append("writing")
            execution_mode = "research_then_write"
            reason = "source_backed_writing_requires_research_first"
        elif mode == "skill_subagent":
            primary_runtime = "delegation"
            supporting.append("writing")
            execution_mode = "skill_aware_writing_subagent"
            reason = "skill_method_execution_requires_subagent_contract"
        elif mode == "ask_user_clarify":
            primary_runtime = "supervisor"
            execution_mode = "clarify_writing_route"
            reason = "ambiguous_writing_request"
            ask_user_needed = True
        else:
            primary_runtime = "supervisor"
            execution_mode = "direct_supervisor_writing"
            reason = "bounded_direct_writing"

    if "research" in secondary_shapes and primary_runtime not in {"research"}:
        supporting.append("research")
    if "creative_media" in secondary_shapes and primary_runtime != "creative_media":
        supporting.append("creative_media")
    if "delegation" in secondary_shapes and primary_runtime != "delegation":
        supporting.append("delegation")

    has_video = _contains_any(text, _VIDEO_TERMS)
    if has_video and _contains_any(text, _CODE_VIDEO_TERMS):
        primary_runtime = "engineering"
        execution_mode = "code_video_runtime"
        reason = "explicit_code_video_framework"
        supporting.append("creative_media")
        forbidden_routes.append("creative_media_as_primary_provider_only")
        signals.append("video:code_framework")
    elif has_video and _contains_any(text, _PROVIDER_VIDEO_TERMS):
        primary_runtime = "creative_media"
        execution_mode = "provider_video_generation"
        reason = "explicit_media_provider_or_reference_video_request"
        signals.append("video:provider_generation")
    elif has_video and _contains_any(text, _EXPLAINER_VIDEO_TERMS):
        primary_runtime = "engineering"
        execution_mode = "code_video_runtime"
        reason = "explainer_or_course_video_prefers_editable_code_timeline"
        supporting.append("creative_media")
        forbidden_routes.append("creative_media_as_primary_unless_provider_named")
        signals.append("video:explainer_code_video")
    elif has_video and primary_shape == "creative_media" and "output_modality_only" in set(_as_list(hint.get("ambiguityFlags"))):
        primary_runtime = "supervisor"
        execution_mode = "clarify_video_route"
        reason = "ambiguous_video_output_without_method_or_delivery_constraints"
        ask_user_needed = True
        supporting.append("creative_media")
        supporting.append("engineering")
        signals.append("video:ambiguous_output_only")

    if _contains_any(text, _TERMINAL_TERMS) and _contains_any(text, _TERMINAL_ACTION_TERMS):
        if _contains_any(text, _GUI_TERMINAL_TERMS) and not excluded_computer_use:
            primary_runtime = "computer_use"
            execution_mode = "gui_terminal_session"
            reason = "explicit_visible_desktop_terminal_required"
            forbidden_routes.append("native_command_if_visual_terminal_required")
            signals.append("terminal:gui_required")
        else:
            primary_runtime = "engineering"
            execution_mode = "native_terminal_command"
            reason = "logical_terminal_request_prefers_native_command_session"
            forbidden_routes.append("computer_use_for_literal_terminal_only")
            route_corrections.append(
                {
                    "from": "computer_use",
                    "to": "run_system_command_or_command_session_broker",
                    "reason": "native command sessions expose stdout/stderr and are recoverable; GUI terminal is only for visual desktop requirements.",
                }
            )
            signals.append("terminal:native_command_preferred")

    if _contains_positive_any(text, _RPA_TERMS) and not excluded_rpa:
        primary_runtime = "rpa"
        execution_mode = "rpa_workflow_or_template"
        reason = "repeatable_workflow_or_object_library_request"
        forbidden_routes.append("computer_use_as_primary_for_reusable_workflow")
        signals.append("desktop:rpa_reusable")
    elif _contains_any(text, _BROWSER_DOM_TERMS):
        primary_runtime = "computer_use" if "登录态" in text and not excluded_computer_use else primary_runtime
        supporting.append("web_extract")
        execution_mode = "browser_dom_or_agent_browser" if primary_runtime == "computer_use" else execution_mode
        signals.append("browser:dom_or_login_context")
    elif _contains_any(text, _DESKTOP_GUI_TERMS) and primary_runtime == "supervisor" and not excluded_computer_use:
        primary_runtime = "computer_use"
        execution_mode = "desktop_gui_observation"
        reason = "real_desktop_gui_interaction_request"
        signals.append("desktop:gui_interaction")

    if _contains_any(text, _RESEARCH_COMPLEX_TERMS) and primary_runtime not in {"research"}:
        supporting.append("research")
        signals.append("web:research_support_required")

    return {
        "schema": "v8.task_boundary.v1",
        "primaryRuntime": primary_runtime,
        "supportingRuntimes": _unique(supporting),
        "executionMode": execution_mode,
        "reason": reason,
        "askUserNeeded": ask_user_needed,
        "forbiddenRoutes": _unique(forbidden_routes),
        "routeCorrections": route_corrections,
        "signals": _unique(signals),
        "source": "task_boundary_resolver",
        "policy": "code_decision_hint_no_runtime_grant",
    }


def attach_task_boundary_decision(
    task_shape_hint: dict[str, Any] | None,
    *,
    user_query: str,
    planner_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    hint = dict(task_shape_hint or {})
    hint["boundaryDecision"] = resolve_task_boundary(
        user_query,
        task_shape_hint=hint,
        planner_plan=planner_plan,
    )
    return hint


def render_task_boundary_hint(boundary: dict[str, Any] | None) -> str:
    if not isinstance(boundary, dict) or not boundary:
        return ""
    support = ", ".join(_as_list(boundary.get("supportingRuntimes"))) or "none"
    forbidden = ", ".join(_as_list(boundary.get("forbiddenRoutes"))) or "none"
    signals = ", ".join(_as_list(boundary.get("signals"))) or "none"
    lines = [
        "<task_boundary>",
        f"primaryRuntime={boundary.get('primaryRuntime') or 'unknown'}; supportingRuntimes={support}; executionMode={boundary.get('executionMode') or 'unknown'}",
        f"askUserNeeded={bool(boundary.get('askUserNeeded'))}; reason={boundary.get('reason') or 'unspecified'}",
        f"forbiddenRoutes={forbidden}; signals={signals}; source=task_boundary_resolver",
        "policy=code decision hint; Planner and tool gates must respect route corrections, but this does not grant runtime tools.",
        "</task_boundary>",
    ]
    return "\n".join(lines) + "\n"
