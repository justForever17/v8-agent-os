from __future__ import annotations

import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from runtimes.computer_use.browser_automation import BrowserLaneDecision
from runtimes.computer_use.browser_automation import BrowserAutomationProvider
from runtimes.computer_use.task_loop import (
    github_star_dom_probe_script,
    normalize_intent,
    prepare_task_loop,
    resolve_facts,
)
from runtimes.computer_use.runtime import ComputerUseRuntime


ENGINE_ROOT = Path(__file__).resolve().parents[2]


def test_fact_resolver_maps_turix_to_canonical_github_repo():
    intent = normalize_intent("去 GitHub 给 TuriX 点个星标")
    facts = resolve_facts(intent)

    assert intent["operation"] == "star_repository"
    assert facts[0]["url"] == "https://github.com/TurixAI/TuriX-CUA"
    assert facts[0]["source"] == "built_in_alias"


def test_fact_resolver_keeps_compound_browser_download_out_of_settings_mode():
    intent = normalize_intent(
        "打开 https://metaso.cn，提问后下载页面图片，最后关闭浏览器。"
    )

    assert intent["explicitUrl"] == "https://metaso.cn"
    assert intent["operation"] == "download_and_open"
    assert intent["domain"] == "web"


def test_task_loop_requires_fact_resolution_before_gui_for_unknown_repo():
    loop = prepare_task_loop(
        "去 GitHub 给完全不存在的某个项目点星标",
        browser_decision={"available": True, "reason": "fake_browser"},
    ).as_dict()

    assert loop["status"] == "needs_fact_resolution"
    assert loop["plan"]["status"] == "blocked_before_gui"
    assert loop["humanAttentionReason"] == "canonical_repo_url_not_resolved"


def test_task_loop_selects_github_playbook_and_browser_lane_for_turix():
    loop = prepare_task_loop(
        "去 GitHub 给 TuriX 点星标",
        browser_decision={"available": True, "reason": "fake_browser"},
    ).as_dict()

    assert loop["status"] == "ready"
    assert loop["domain"]["selectedPlaybook"] == "github.star_repository"
    assert loop["laneDecision"]["lane"] == "browser_cdp_dom"
    assert loop["plan"]["targetUrl"] == "https://github.com/TurixAI/TuriX-CUA"
    assert loop["plan"]["desiredState"] == "starred"
    assert "opened_repo_without_starred_state" in loop["verifier"]["notEnough"]


def test_task_loop_understands_github_unstar_intent():
    loop = prepare_task_loop(
        "去 GitHub 给 TuriX 取消星标",
        browser_decision={"available": True, "reason": "fake_browser"},
    ).as_dict()

    assert loop["status"] == "ready"
    assert loop["domain"]["selectedPlaybook"] == "github.star_repository"
    assert loop["intent"]["desiredState"] == "unstarred"
    assert loop["plan"]["desiredState"] == "unstarred"
    assert loop["verifier"]["desiredState"] == "unstarred"


def test_github_star_probe_tracks_logged_out_and_starred_states():
    script = github_star_dom_probe_script()

    assert "loggedOut" in script
    assert "needsLoginForStar" in script
    assert "must be signed in" in script
    assert "isStarred" in script
    assert "Starred" in script
    assert "Join GitHub" in script


def test_browser_cdp_proxy_helper_is_present_and_syntax_valid():
    helper = ENGINE_ROOT / "scripts" / "browser_cdp_proxy.mjs"
    assert helper.exists()
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available in this test environment")
    completed = subprocess.run(
        [node, "--check", str(helper)],
        cwd=str(ENGINE_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr


def test_browser_managed_launch_uses_dedicated_reusable_profile(tmp_path: Path):
    provider = BrowserAutomationProvider()
    provider.configure(
        {
            "browserLane": {
                "enabled": True,
                "proxyPort": 3456,
                "profileMode": "dedicated_debug_profile",
                "userDataDir": str(tmp_path),
            }
        }
    )

    command, _env, metadata = provider.prepare_launch(
        app_id="browser_checkout",
        launch_command=[
            "chrome.exe",
            "--user-data-dir=C:\\Users\\sunny\\AppData\\Local\\Google\\Chrome\\User Data",
            "--incognito",
            "--foo",
        ],
        environment={},
    )

    assert isinstance(command, list)
    assert "--foo" in command
    assert "--incognito" not in command
    assert not any(str(arg).startswith("--user-data-dir=C:\\Users") for arg in command)
    assert "--remote-debugging-port=9222" in command
    assert "--start-maximized" in command
    assert "--window-size=1600,1000" in command
    assert f"--user-data-dir={tmp_path / 'chrome'}" in command
    assert metadata["profilePersistenceMode"] == "dedicated_debug_profile"
    assert metadata["browserUserDataDir"] == str(tmp_path / "chrome")


def test_browser_family_recognizes_managed_agent_browser_ids():
    provider = BrowserAutomationProvider()

    assert provider.infer_family(app_id="browser_checkout", app_name="Agent Browser") == "chromium"
    assert provider.infer_family(app_id="agent_browser") == "chromium"


def test_browser_lane_auto_starts_managed_debug_browser_when_no_existing_port(monkeypatch, tmp_path: Path):
    helper = tmp_path / "browser_cdp_proxy.mjs"
    helper.write_text("console.log('ok')", encoding="utf-8")
    provider = BrowserAutomationProvider()
    provider.configure({"browserLane": {"enabled": True, "allowManagedLaunch": True}})
    provider._node_path = "node"
    monkeypatch.setattr(provider, "_helper_script_path", lambda: helper)
    monkeypatch.setattr(provider, "_probe_playwright_dependency", lambda: {"available": True})
    monkeypatch.setattr(provider, "_discover_existing_debug_port", lambda **_kwargs: None)
    monkeypatch.setattr(
        provider,
        "_start_managed_chromium_debug_browser",
        lambda **_kwargs: BrowserLaneDecision(
            enabled=True,
            available=True,
            family="chromium",
            reason="managed_debug_browser_started",
            target_port=3556,
            managed_launch=True,
        ),
    )

    decision = provider.decide_lane(
        action_type="type_text",
        action_payload={"app_id": "browser_checkout", "app_name": "browser"},
        app_id="browser_checkout",
    )

    assert decision.available is True
    assert decision.managed_launch is True
    assert decision.reason == "managed_debug_browser_started"


def test_agent_browser_attach_does_not_start_new_profile_when_closed(monkeypatch, tmp_path: Path):
    helper = tmp_path / "browser_cdp_proxy.mjs"
    helper.write_text("console.log('ok')", encoding="utf-8")
    provider = BrowserAutomationProvider()
    provider.configure({"browserLane": {"enabled": True, "allowManagedLaunch": True, "userDataDir": str(tmp_path)}})
    provider._node_path = "node"
    monkeypatch.setattr(provider, "_helper_script_path", lambda: helper)
    monkeypatch.setattr(provider, "_probe_playwright_dependency", lambda: {"available": True})
    monkeypatch.setattr(provider, "_is_debug_port_reachable", lambda _port: False)
    monkeypatch.setattr(
        provider,
        "_start_managed_chromium_debug_browser",
        lambda **_kwargs: pytest.fail("agent_browser_attach_context must not launch a new browser profile"),
    )

    result = provider.agent_browser_attach_context()

    assert result["ok"] is False
    assert result["status"] == "agent_browser_not_open"
    assert result["profileMode"] == "dedicated_debug_profile"


def test_agent_browser_attach_rejects_user_browser_without_explicit_allow(tmp_path: Path):
    provider = BrowserAutomationProvider()
    provider.configure({"browserLane": {"enabled": True, "userDataDir": str(tmp_path)}})

    result = provider.agent_browser_attach_context(browser_profile_policy="user_browser_explicit", allow_user_browser=False)

    assert result["ok"] is False
    assert result["status"] == "user_browser_attach_requires_explicit_request"


class _FakeRunHandle:
    run_id = "run-github-star"
    session_id = "session-github-star"

    def __init__(self):
        self.events: list[tuple[str, dict]] = []
        self.transitions: list[tuple[str, str | None]] = []
        self.failed: str | None = None

    def emit(self, event_type: str, payload: dict):
        self.events.append((event_type, payload))

    def transition(self, status: str, reason: str | None = None, node: str | None = None):
        self.transitions.append((status, reason))

    def fail(self, reason: str, node: str | None = None):
        self.failed = reason


class _FakeBrowser:
    def __init__(self, *, pre_state: dict, post_state: dict | None = None):
        self.pre_state = pre_state
        self.post_state = post_state or pre_state
        self.evaluations = 0
        self.clicks = 0
        self.closed_targets: list[str] = []

    def configure(self, _config):
        return None

    def decide_lane(self, **_kwargs):
        return BrowserLaneDecision(
            enabled=True,
            available=True,
            family="chromium",
            reason="fake_browser",
            target_port=9222,
        )

    def open_tab(self, *, url: str, decision: BrowserLaneDecision):
        return {"targetId": "target-1", "url": url, "family": decision.family, "targetPort": decision.target_port}

    def close_tab(self, *, target_id: str, **_kwargs):
        self.closed_targets.append(target_id)
        return {"targetId": target_id, "closed": True}

    def _evaluate(self, *, target_id: str, expression: str):
        if "target.click" in expression or "target.el.click" in expression:
            self.clicks += 1
            return {"value": {"ok": True, "text": "Star", "x": 10, "y": 10}}
        self.evaluations += 1
        return {"value": self.pre_state if self.evaluations == 1 else self.post_state}


def _runtime_with_fake_browser(fake_browser: _FakeBrowser, monkeypatch):
    runtime = ComputerUseRuntime.__new__(ComputerUseRuntime)
    runtime.browser_automation = fake_browser
    runtime._ensure_runtime_ready = lambda: None
    runtime._computer_use_config = lambda: {}
    runtime._resource_leases = {}
    runtime._resource_lease_lock = threading.Lock()
    runtime._assess_runtime_action_safety = lambda **_kwargs: {"applied": False}
    run_handle = _FakeRunHandle()
    runtime.begin_or_attach_run = lambda **_kwargs: run_handle
    monkeypatch.setattr("runtimes.computer_use.runtime.run_service.transition_run", lambda *_args, **_kwargs: None)
    return runtime, run_handle


def test_execute_github_star_playbook_succeeds_without_click_when_already_starred(monkeypatch):
    fake_browser = _FakeBrowser(
        pre_state={
            "url": "https://github.com/TurixAI/TuriX-CUA",
            "repoPath": "TurixAI/TuriX-CUA",
            "loggedOut": False,
            "isStarred": True,
            "strictDomState": "starred",
            "hasStarTarget": True,
            "starLabel": "Starred",
            "starAction": "/TurixAI/TuriX-CUA/unstar",
        }
    )
    runtime, run_handle = _runtime_with_fake_browser(fake_browser, monkeypatch)

    result = runtime.execute_github_star_playbook(goal="去 GitHub 给 TuriX 点星标", allow_real_click=True)

    assert result["status"] == "succeeded"
    assert result["action"] == "already_starred"
    assert fake_browser.clicks == 0
    assert fake_browser.closed_targets == []
    assert result["resourceLease"]["cleanup"]["status"] == "completed"
    assert result["resourceLease"]["cleanup"]["skipped"][0]["reason"] == "delayed_cleanup_scheduled"
    assert any(event[0] == "computer_use.github_star.pre_state" for event in run_handle.events)


def test_execute_github_star_playbook_clicks_only_after_precheck(monkeypatch):
    fake_browser = _FakeBrowser(
        pre_state={
            "url": "https://github.com/TurixAI/TuriX-CUA",
            "repoPath": "TurixAI/TuriX-CUA",
            "loggedOut": False,
            "isStarred": False,
            "strictDomState": "not_starred",
            "hasStarTarget": True,
            "starLabel": "Star",
            "starAction": "/TurixAI/TuriX-CUA/star",
        },
        post_state={
            "url": "https://github.com/TurixAI/TuriX-CUA",
            "repoPath": "TurixAI/TuriX-CUA",
            "loggedOut": False,
            "isStarred": True,
            "strictDomState": "starred",
            "hasStarTarget": True,
            "starLabel": "Starred",
            "starAction": "/TurixAI/TuriX-CUA/unstar",
        },
    )
    runtime, run_handle = _runtime_with_fake_browser(fake_browser, monkeypatch)
    monkeypatch.setattr("runtimes.computer_use.runtime.time.sleep", lambda _seconds: None)

    result = runtime.execute_github_star_playbook(goal="去 GitHub 给 TuriX 点星标", allow_real_click=True)

    assert result["status"] == "succeeded"
    assert result["action"] == "clicked_star"
    assert fake_browser.clicks == 1
    assert fake_browser.closed_targets == []
    assert result["postState"]["isStarred"] is True
    assert result["resourceLease"]["cleanup"]["skipped"][0]["reason"] == "delayed_cleanup_scheduled"


def test_execute_github_star_playbook_can_unstar_with_explicit_desired_state(monkeypatch):
    fake_browser = _FakeBrowser(
        pre_state={
            "url": "https://github.com/TurixAI/TuriX-CUA",
            "repoPath": "TurixAI/TuriX-CUA",
            "loggedOut": False,
            "isStarred": True,
            "strictDomState": "starred",
            "hasStarTarget": True,
            "starLabel": "Starred, click to unstar",
            "starAction": "/TurixAI/TuriX-CUA/unstar",
        },
        post_state={
            "url": "https://github.com/TurixAI/TuriX-CUA",
            "repoPath": "TurixAI/TuriX-CUA",
            "loggedOut": False,
            "isStarred": False,
            "strictDomState": "not_starred",
            "hasStarTarget": True,
            "starLabel": "Star",
            "starAction": "/TurixAI/TuriX-CUA/star",
        },
    )
    runtime, _run_handle = _runtime_with_fake_browser(fake_browser, monkeypatch)
    monkeypatch.setattr("runtimes.computer_use.runtime.time.sleep", lambda _seconds: None)

    result = runtime.execute_github_star_playbook(
        goal="去 GitHub 给 TuriX 取消星标",
        allow_real_click=True,
        desired_state="unstarred",
    )

    assert result["status"] == "succeeded"
    assert result["desiredState"] == "not_starred"
    assert result["action"] == "clicked_unstar"
    assert fake_browser.clicks == 1


def test_execute_github_star_playbook_does_not_accept_loose_starred_text(monkeypatch):
    fake_browser = _FakeBrowser(
        pre_state={
            "url": "https://github.com/TurixAI/TuriX-CUA",
            "repoPath": "TurixAI/TuriX-CUA",
            "loggedOut": False,
            "isStarred": True,
            "starLabel": "Starred by 2.7k people",
            "strictDomState": "unknown",
            "hasStarTarget": False,
        }
    )
    runtime, _run_handle = _runtime_with_fake_browser(fake_browser, monkeypatch)

    result = runtime.execute_github_star_playbook(goal="去 GitHub 给 TuriX 点星标", allow_real_click=True)

    assert result["status"] == "needs_human_attention"
    assert result["reason"] == "strict_dom_state_ambiguous"
    assert fake_browser.clicks == 0
    assert fake_browser.closed_targets == []
    assert result["resourceLease"]["cleanup"]["status"] == "skipped"


def test_execute_github_star_playbook_stops_for_login(monkeypatch):
    fake_browser = _FakeBrowser(pre_state={"loggedOut": True, "isStarred": False, "starLabel": ""})
    runtime, _run_handle = _runtime_with_fake_browser(fake_browser, monkeypatch)

    result = runtime.execute_github_star_playbook(goal="去 GitHub 给 TuriX 点星标", allow_real_click=True)

    assert result["status"] == "needs_human_login"
    assert result["recommendedNextAction"] == "ask_user"
    assert result["humanInputRequest"]["reason"] == "needs_human_login"
    assert fake_browser.clicks == 0
    assert fake_browser.closed_targets == []
    assert result["resourceLease"]["cleanup"]["status"] == "skipped"


def test_execute_github_star_playbook_stops_for_star_login_requirement(monkeypatch):
    fake_browser = _FakeBrowser(
        pre_state={
            "loggedOut": False,
            "needsLoginForStar": True,
            "isStarred": False,
            "starLabel": "Star 2.7k You must be signed in to star a repository",
        }
    )
    runtime, _run_handle = _runtime_with_fake_browser(fake_browser, monkeypatch)

    result = runtime.execute_github_star_playbook(goal="去 GitHub 给 TuriX 点星标", allow_real_click=True)

    assert result["status"] == "needs_human_login"
    assert result["recommendedNextAction"] == "ask_user"
    assert fake_browser.clicks == 0
