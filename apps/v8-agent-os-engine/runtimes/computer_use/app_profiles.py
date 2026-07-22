from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Dict, List, Optional


@dataclass(slots=True)
class ComputerUseAppProfile:
    app_id: str
    display_name: str
    control_class: str = "native_window_app"
    app_adapter_id: str = ""
    launch_command: List[str] = field(default_factory=list)
    process_names: List[str] = field(default_factory=list)
    scenario_tags: List[str] = field(default_factory=list)
    title_patterns: List[str] = field(default_factory=list)
    class_names: List[str] = field(default_factory=list)
    app_names: List[str] = field(default_factory=list)
    selectors: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    toolbar_actions: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    visual_expectations: Dict[str, str] = field(default_factory=dict)
    visual_guard_actions: List[str] = field(default_factory=list)
    pre_action_guard_actions: List[str] = field(default_factory=list)
    high_risk_actions: List[str] = field(default_factory=list)
    transient_selectors: List[str] = field(default_factory=list)
    window_probe_selector_keys: List[str] = field(default_factory=list)
    startup_transition_selector_key: str = ""
    startup_transition_error_message: str = ""
    bind_process_ids: bool = True
    notes: str = ""

    def selector_for(self, selector_key: str | None) -> Dict[str, Any]:
        if not selector_key:
            return {}
        return dict(self.selectors.get(str(selector_key).strip(), {}))

    def toolbar_selector_for(self, action_name: str | None) -> Dict[str, Any]:
        if not action_name:
            return {}
        return dict(self.toolbar_actions.get(str(action_name).strip(), {}))

    def visual_expectation_for(self, action_name: str | None) -> str:
        if not action_name:
            return ""
        return str(self.visual_expectations.get(str(action_name).strip(), "")).strip()

    def as_dict(self) -> Dict[str, Any]:
        return {
            "appId": self.app_id,
            "displayName": self.display_name,
            "controlClass": self.control_class,
            "appAdapterId": self.app_adapter_id or None,
            "launchCommand": list(self.launch_command),
            "processNames": list(self.process_names),
            "scenarioTags": list(self.scenario_tags),
            "titlePatterns": list(self.title_patterns),
            "classNames": list(self.class_names),
            "appNames": list(self.app_names),
            "selectors": {key: dict(value) for key, value in self.selectors.items()},
            "toolbarActions": {key: dict(value) for key, value in self.toolbar_actions.items()},
            "visualExpectations": dict(self.visual_expectations),
            "visualGuardActions": list(self.visual_guard_actions),
            "preActionGuardActions": list(self.pre_action_guard_actions),
            "highRiskActions": list(self.high_risk_actions),
            "transientSelectors": list(self.transient_selectors),
            "windowProbeSelectorKeys": list(self.window_probe_selector_keys),
            "startupTransitionSelectorKey": self.startup_transition_selector_key,
            "startupTransitionErrorMessage": self.startup_transition_error_message,
            "bindProcessIds": self.bind_process_ids,
            "notes": self.notes,
        }


class ComputerUseAppProfiles:
    def __init__(self) -> None:
        self._profiles: Dict[str, ComputerUseAppProfile] = {
            "notepad": ComputerUseAppProfile(
                app_id="notepad",
                display_name="记事本",
                launch_command=["notepad.exe"],
                process_names=["notepad.exe"],
                scenario_tags=["editor", "text_input", "local_app"],
                title_patterns=["Notepad", "记事本", "notepad"],
                class_names=["Notepad"],
                app_names=["notepad", "Notepad", "记事本"],
                selectors={
                    "editor": {
                        "control_type": "Document",
                        "class_name": "RichEditD2DPT",
                    }
                },
                visual_expectations={
                    "open_app": "记事本主窗口应已经打开并位于前台。",
                    "find_and_type": "记事本编辑区内应出现新的文本内容。",
                },
                visual_guard_actions=["open_app", "find_and_type"],
                window_probe_selector_keys=["editor"],
                bind_process_ids=False,
                notes="适合验证输入类动作和窗口绑定。",
            ),
            "app_qqmusic": ComputerUseAppProfile(
                app_id="app_qqmusic",
                display_name="QQ音乐",
                process_names=["qqmusic.exe"],
                scenario_tags=["media_player", "custom_drawn", "text_input"],
                title_patterns=["QQ音乐", "QQMusic"],
                class_names=["TXGuiFoundation"],
                app_names=["QQ音乐", "QQ Music", "qqmusic"],
                selectors={
                    "search_box": {
                        "point_rect": [0.20, 0.012, 0.45, 0.065],
                        "target_hints": ["search", "搜索", "查找歌曲", "搜索音乐"],
                        "window_typing_focus_mode": "application_surface",
                        "prefer_sendinput_click": True,
                        "prefer_sendinput_text": True,
                    },
                    "search_result_first_song": {
                        "point_rect": [0.145, 0.36, 0.43, 0.47],
                        "preferred_point": [0.195, 0.40],
                        "activation": "double_click",
                        "target_hints": ["first result", "song result", "第一条", "搜索结果", "歌曲结果"],
                        "prefer_sendinput_click": True,
                    }
                },
                visual_expectations={
                    "find_and_type": "搜索框附近应出现查询文本或搜索结果页面。",
                },
                visual_guard_actions=["find_and_type"],
                transient_selectors=["search_box"],
                bind_process_ids=True,
                notes="QQ音乐 21.96 本机实测搜索框区域；播放控制使用独立快捷键目录。",
            ),
            "explorer": ComputerUseAppProfile(
                app_id="explorer",
                display_name="文件资源管理器",
                launch_command=["explorer.exe"],
                process_names=["explorer.exe"],
                scenario_tags=["file_browser", "list_navigation", "local_app"],
                title_patterns=["文件资源管理器", "explorer"],
                class_names=["CabinetWClass", "ExploreWClass"],
                app_names=["explorer", "文件资源管理器"],
                selectors={
                    "list": {
                        "control_type": "List",
                        "point_rect": [0.34, 0.19, 0.93, 0.86],
                        "point_biases": [[0.0, -0.06], [0.0, 0.12], [-0.12, 0.18]],
                        "prefer_sendinput_click": True,
                    },
                    "content_receiver": {
                        "control_type": "List",
                        "point_rect": [0.38, 0.22, 0.93, 0.84],
                        "point_biases": [[0.0, 0.0], [0.0, 0.18], [-0.16, 0.22], [0.16, 0.22], [0.0, 0.3]],
                        "window_typing_focus_mode": "content_receiver",
                        "file_paste_strategy": "sendinput",
                        "prefer_sendinput_click": True,
                    },
                    "address_bar": {
                        "automation_id": "41477",
                        "control_type": "Edit",
                        "point_rect": [0.18, 0.065, 0.82, 0.115],
                        "point_bias": [0.06, 0.0],
                        "point_biases": [[0.1, 0.0]],
                        "focus_shortcut_id": "app.focus_location",
                        "window_typing": True,
                        "prefer_sendinput_click": True,
                    },
                    "file_item": {
                        "control_type": "ListItem",
                    },
                },
                toolbar_actions={
                    "refresh": {
                        "name": "刷新",
                        "control_type": "Button",
                    },
                    "new_folder": {
                        "name": "新建文件夹",
                        "control_type": "Button",
                    },
                    "back": {
                        "name": "后退",
                        "control_type": "Button",
                    },
                },
                visual_expectations={
                    "open_app": "文件资源管理器窗口应已经打开并显示目录内容。",
                    "find_and_type": "地址栏输入后，当前目录内容或地址栏路径应发生变化。",
                    "click": "文件资源管理器列表中应选中目标文件或目录项。",
                    "scroll_list": "资源列表的可视区域应发生滚动变化。",
                    "click_toolbar_action": "工具栏按钮点击后，窗口内容或导航状态应发生变化。",
                    "refresh": "刷新动作执行后，窗口内容或导航状态应发生变化。",
                    "new_folder": "新建文件夹动作执行后，列表里应出现新文件夹项。",
                    "back": "后退动作执行后，地址栏或可见目录内容应发生变化。",
                },
                visual_guard_actions=["open_app", "scroll_list", "click_toolbar_action"],
                transient_selectors=["list", "content_receiver", "file_item", "refresh", "new_folder", "back"],
                window_probe_selector_keys=["list", "content_receiver", "address_bar", "file_item"],
                bind_process_ids=False,
                notes="Explorer 工具栏和列表控件经常 transient，需要更强恢复策略。",
            ),
            "mail_client": ComputerUseAppProfile(
                app_id="mail_client",
                display_name="邮件客户端",
                launch_command=["olk.exe"],
                process_names=["olk.exe", "outlook.exe"],
                scenario_tags=["email", "compose", "send"],
                title_patterns=["outlook", "邮箱", "邮件"],
                class_names=["rctrl_renwnd32"],
                app_names=["outlook", "mail", "邮箱", "邮件"],
                selectors={
                    "recipient_input": {
                        "name_contains": "收件人",
                        "control_type": "Edit",
                    },
                    "subject_input": {
                        "name_contains": "主题",
                        "control_type": "Edit",
                    },
                    "body_editor": {
                        "control_type": "Document",
                    },
                    "send_button": {
                        "name": "发送",
                        "control_type": "Button",
                    },
                },
                visual_expectations={
                    "open_app": "邮件客户端主窗口应已经打开，可见收件箱或撰写入口。",
                    "find_and_type": "邮件输入区域应出现新的收件人、主题或正文内容。",
                    "click_toolbar_action": "发送或工具栏动作执行后，邮件状态或窗口内容应变化。",
                    "send": "发送动作执行后，草稿状态或窗口内容应变化，消息不应停留在待发送状态。",
                },
                visual_guard_actions=["open_app", "find_and_type", "click_toolbar_action"],
                pre_action_guard_actions=["send"],
                high_risk_actions=["send"],
                transient_selectors=["recipient_input", "subject_input", "body_editor", "send_button"],
                window_probe_selector_keys=["recipient_input", "subject_input", "body_editor", "send_button"],
                bind_process_ids=True,
                notes="邮件场景默认要求视觉保底，避免误发或误填。",
            ),
            "chrome": ComputerUseAppProfile(
                app_id="chrome",
                display_name="Google Chrome",
                control_class="browser_host_app",
                launch_command=["chrome.exe"],
                process_names=["chrome.exe"],
                scenario_tags=["browser", "web", "rpa_capture_target"],
                title_patterns=["Google Chrome", "Chrome", "谷歌浏览器"],
                class_names=["Chrome_WidgetWin_1"],
                app_names=["chrome", "google chrome", "谷歌浏览器", "chromium"],
                selectors={
                    "address_bar": {
                        "control_type": "Edit",
                        "class_name": "OmniboxViewViews",
                        "focus_shortcut_id": "browser.focus_location",
                        "window_typing": True,
                        "prefer_sendinput_click": True,
                    }
                },
                visual_expectations={"open_app": "Chrome 浏览器窗口应已经打开并可见地址栏。"},
                visual_guard_actions=["open_app"],
                window_probe_selector_keys=["address_bar"],
                bind_process_ids=False,
                notes="通用 Chrome 浏览器入口，供 RPA Studio 应用选择和捕获目标使用。",
            ),
            "edge": ComputerUseAppProfile(
                app_id="edge",
                display_name="Microsoft Edge",
                control_class="browser_host_app",
                launch_command=["msedge.exe"],
                process_names=["msedge.exe"],
                scenario_tags=["browser", "web", "rpa_capture_target"],
                title_patterns=["Microsoft Edge", "Edge"],
                class_names=["Chrome_WidgetWin_1"],
                app_names=["edge", "microsoft edge", "msedge"],
                selectors={
                    "address_bar": {
                        "control_type": "Edit",
                        "class_name": "OmniboxViewViews",
                        "focus_shortcut_id": "browser.focus_location",
                        "window_typing": True,
                        "prefer_sendinput_click": True,
                    }
                },
                visual_expectations={"open_app": "Edge 浏览器窗口应已经打开并可见地址栏。"},
                visual_guard_actions=["open_app"],
                window_probe_selector_keys=["address_bar"],
                bind_process_ids=False,
                notes="通用 Edge 浏览器入口，供 RPA Studio 应用选择和捕获目标使用。",
            ),
            "brave": ComputerUseAppProfile(
                app_id="brave",
                display_name="Brave Browser",
                control_class="browser_host_app",
                launch_command=["brave.exe"],
                process_names=["brave.exe"],
                scenario_tags=["browser", "web", "rpa_capture_target"],
                title_patterns=["Brave", "Brave Browser"],
                class_names=["Chrome_WidgetWin_1"],
                app_names=["brave", "brave browser"],
                selectors={
                    "address_bar": {
                        "control_type": "Edit",
                        "class_name": "OmniboxViewViews",
                        "focus_shortcut_id": "browser.focus_location",
                        "window_typing": True,
                        "prefer_sendinput_click": True,
                    }
                },
                visual_expectations={"open_app": "Brave 浏览器窗口应已经打开并可见地址栏。"},
                visual_guard_actions=["open_app"],
                window_probe_selector_keys=["address_bar"],
                bind_process_ids=False,
                notes="通用 Brave 浏览器入口，供 RPA Studio 应用选择和捕获目标使用。",
            ),
            "firefox": ComputerUseAppProfile(
                app_id="firefox",
                display_name="Firefox",
                control_class="browser_host_app",
                launch_command=["firefox.exe"],
                process_names=["firefox.exe"],
                scenario_tags=["browser", "web", "rpa_capture_target"],
                title_patterns=["Mozilla Firefox", "Firefox", "火狐"],
                class_names=["MozillaWindowClass"],
                app_names=["firefox", "mozilla firefox", "火狐浏览器", "火狐"],
                selectors={
                    "address_bar": {
                        "control_type": "Edit",
                        "focus_shortcut_id": "browser.focus_location",
                        "window_typing": True,
                        "prefer_sendinput_click": True,
                    }
                },
                visual_expectations={"open_app": "Firefox 浏览器窗口应已经打开并可见地址栏。"},
                visual_guard_actions=["open_app"],
                window_probe_selector_keys=["address_bar"],
                bind_process_ids=False,
                notes="通用 Firefox 浏览器入口，供 RPA Studio 应用选择和捕获目标使用。",
            ),
            "browser_checkout": ComputerUseAppProfile(
                app_id="browser_checkout",
                display_name="浏览器支付/提交",
                control_class="browser_host_app",
                launch_command=["C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"],
                process_names=["chrome.exe", "msedge.exe", "firefox.exe"],
                scenario_tags=["browser", "payment", "form_submit"],
                title_patterns=["chrome", "google chrome", "microsoft edge", "firefox", "支付", "checkout"],
                class_names=["Chrome_WidgetWin_1", "MozillaWindowClass"],
                app_names=["chrome", "browser", "chromium", "edge"],
                selectors={
                    "address_bar": {
                        "control_type": "Edit",
                        "class_name": "OmniboxViewViews",
                        "point_rect": [0.14, 0.045, 0.8, 0.105],
                        "point_bias": [0.08, 0.0],
                        "point_biases": [[0.12, 0.0], [0.16, 0.0]],
                        "focus_shortcut_id": "browser.focus_location",
                        "window_typing": True,
                        "prefer_sendinput_click": True,
                    },
                    "primary_button": {
                        "control_type": "Button",
                        "name_contains": "继续",
                    },
                    "confirm_button": {
                        "control_type": "Button",
                        "name_contains": "确认",
                    },
                    "pay_button": {
                        "control_type": "Button",
                        "name_contains": "支付",
                    },
                },
                toolbar_actions={
                    "refresh": {
                        "name": "刷新",
                        "control_type": "Button",
                    },
                    "back": {
                        "name": "后退",
                        "control_type": "Button",
                    },
                },
                visual_expectations={
                    "open_app": "浏览器窗口应已经打开并可见地址栏。",
                    "find_and_type": "页面输入区域或地址栏内应出现新的文本内容。",
                    "click_toolbar_action": "浏览器工具栏动作执行后，页面或导航状态应变化。",
                    "confirm": "确认动作执行后，页面状态应推进到下一步，而不是停留在原表单页。",
                    "pay": "支付动作执行后，页面应进入支付处理中、结果页或受控确认页。",
                },
                visual_guard_actions=["open_app", "find_and_type", "click_toolbar_action", "focus_window"],
                pre_action_guard_actions=["confirm", "pay"],
                high_risk_actions=["confirm", "pay"],
                transient_selectors=["address_bar", "primary_button", "confirm_button", "pay_button"],
                window_probe_selector_keys=["address_bar", "primary_button", "confirm_button", "pay_button"],
                bind_process_ids=True,
                notes="支付、提交类网页场景要求更保守的视觉保底确认。",
            ),
            "vscode": ComputerUseAppProfile(
                app_id="vscode",
                display_name="VS Code",
                control_class="electron_shell_app",
                app_adapter_id="vscode",
                launch_command=["code.cmd"],
                process_names=["code.exe"],
                scenario_tags=["editor", "workspace", "developer_tool", "structured_open"],
                title_patterns=["Visual Studio Code", "VS Code", "Code"],
                class_names=["Chrome_WidgetWin_1"],
                app_names=["vscode", "visual studio code", "vs code", "code"],
                selectors={
                    "editor": {
                        "control_type": "Document",
                    }
                },
                visual_expectations={
                    "open_app": "VS Code 窗口应已经打开，并显示目标工作区、文件或编辑器。",
                },
                visual_guard_actions=["open_app"],
                window_probe_selector_keys=["editor"],
                bind_process_ids=False,
                notes="优先通过 CLI/URI 复用现有窗口并打开目标，而不是依赖桌面菜单点击。",
            ),
        }

    def list_profiles(self) -> List[ComputerUseAppProfile]:
        return list(self._profiles.values())

    def get(self, app_id: str | None) -> ComputerUseAppProfile | None:
        if not app_id:
            return None
        return self._profiles.get(str(app_id).strip().lower())

    def infer(
        self,
        *,
        explicit_app_id: str | None = None,
        window_title: str | None = None,
        class_name: str | None = None,
        app_name: str | None = None,
        process_name: str | None = None,
    ) -> str | None:
        explicit = self.get(explicit_app_id)
        if explicit is not None:
            return explicit.app_id

        def _normalize(value: str | None) -> str:
            return re.sub(r"[\s_\-]+", "", str(value or "").strip().lower())

        title_lower = str(window_title or "").strip().lower()
        class_lower = str(class_name or "").strip().lower()
        app_lower = _normalize(app_name)
        title_compact = _normalize(window_title)
        process_lower = str(process_name or "").strip().lower()
        process_stem = _normalize(process_name.rsplit(".", 1)[0] if process_name else "")

        for profile in self._profiles.values():
            if process_lower and any(process_lower == item.lower() for item in profile.process_names):
                return profile.app_id
            if process_stem and any(_normalize(item.rsplit(".", 1)[0]) == process_stem for item in profile.process_names):
                return profile.app_id
            if title_lower and any(token.lower() in title_lower for token in profile.title_patterns):
                return profile.app_id
            if title_compact and any(_normalize(token) in title_compact for token in profile.title_patterns):
                return profile.app_id
            if app_lower and any(_normalize(token) in app_lower for token in profile.app_names):
                return profile.app_id
            if class_lower and any(class_lower == item.lower() for item in profile.class_names):
                title_matched = bool(title_lower and any(token.lower() in title_lower for token in profile.title_patterns))
                compact_matched = bool(title_compact and any(_normalize(token) in title_compact for token in profile.title_patterns))
                app_matched = bool(app_lower and any(_normalize(token) in app_lower for token in profile.app_names))
                if title_matched or compact_matched or app_matched:
                    return profile.app_id
        return None

    def selector_for(self, app_id: str | None, selector_key: str | None) -> Dict[str, Any]:
        profile = self.get(app_id)
        if profile is None:
            return {}
        return profile.selector_for(selector_key)

    def selector_for_target(self, app_id: str | None, target: str | None) -> Dict[str, Any]:
        profile = self.get(app_id)
        normalized_target = str(target or "").strip().lower()
        if profile is None or not normalized_target:
            return {}
        for selector_key, selector in profile.selectors.items():
            hints = [str(item or "").strip().lower() for item in list(selector.get("target_hints") or [])]
            if any(hint and hint in normalized_target for hint in hints):
                return {"selector_key": selector_key, **dict(selector)}
        return {}

    def toolbar_selector_for(self, app_id: str | None, action_name: str | None) -> Dict[str, Any]:
        profile = self.get(app_id)
        if profile is None:
            return {}
        return profile.toolbar_selector_for(action_name)

    def visual_expectation_for(self, app_id: str | None, action_name: str | None) -> str:
        profile = self.get(app_id)
        if profile is None:
            return ""
        return profile.visual_expectation_for(action_name)

    def launch_command_for(self, app_id: str | None) -> List[str]:
        profile = self.get(app_id)
        if profile is None:
            return []
        return list(profile.launch_command)

    def requires_visual_guard(self, app_id: str | None, action_name: str | None) -> bool:
        profile = self.get(app_id)
        if profile is None or not action_name:
            return False
        normalized = str(action_name).strip().lower()
        return any(normalized == item.strip().lower() for item in profile.visual_guard_actions)

    def requires_pre_action_guard(self, app_id: str | None, action_name: str | None) -> bool:
        profile = self.get(app_id)
        if profile is None or not action_name:
            return False
        normalized = str(action_name).strip().lower()
        return any(normalized == item.strip().lower() for item in profile.pre_action_guard_actions)

    def is_high_risk_action(self, app_id: str | None, action_name: str | None) -> bool:
        profile = self.get(app_id)
        if profile is None or not action_name:
            return False
        normalized = str(action_name).strip().lower()
        return any(normalized == item.strip().lower() for item in profile.high_risk_actions)

    def is_transient_selector(self, app_id: str | None, selector_key: str | None) -> bool:
        profile = self.get(app_id)
        if profile is None or not selector_key:
            return False
        normalized = str(selector_key).strip().lower()
        return any(normalized == item.strip().lower() for item in profile.transient_selectors)

    def process_names_for(self, app_id: str | None) -> List[str]:
        profile = self.get(app_id)
        if profile is None:
            return []
        return list(profile.process_names)

    def window_probe_selector_keys_for(self, app_id: str | None) -> List[str]:
        profile = self.get(app_id)
        if profile is None:
            return []
        return list(profile.window_probe_selector_keys)

    def action_selector_keys_for(self, app_id: str | None, action_name: str | None) -> List[str]:
        profile = self.get(app_id)
        if profile is None or not action_name:
            return []
        normalized = str(action_name).strip().lower()
        candidates: List[str] = [normalized, f"{normalized}_button"]
        if normalized in {"confirm", "pay", "send", "play"}:
            candidates.append("primary_button")
        if normalized in {"refresh", "back", "new_folder"}:
            candidates.append(normalized)
        ordered: List[str] = []
        for key in candidates:
            if key in ordered:
                continue
            if key in profile.toolbar_actions or key in profile.selectors:
                ordered.append(key)
        return ordered
