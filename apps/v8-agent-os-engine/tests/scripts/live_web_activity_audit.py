from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from urllib.parse import quote


_BROWSER_CANDIDATES = (
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
)


def _browser_executable(explicit: str | None) -> str | None:
    if explicit:
        candidate = Path(explicit).expanduser().resolve(strict=False)
        if not candidate.is_file():
            raise FileNotFoundError(f"browser executable not found: {candidate}")
        return str(candidate)
    for candidate in _BROWSER_CANDIDATES:
        if candidate.is_file():
            return str(candidate)
    return None


def _stable_runtime_cards(snapshot: dict[str, Any]) -> list[tuple[str, str, int]]:
    return sorted(
        (
            str(item.get("runtimeId") or ""),
            str(item.get("status") or ""),
            int(item.get("eventCount") or 0),
        )
        for item in list(snapshot.get("runtimeCards") or [])
        if str(item.get("runtimeId") or "")
    )


def _stable_subagent_cards(snapshot: dict[str, Any]) -> list[tuple[str, str, str]]:
    return sorted(
        (
            str(item.get("id") or ""),
            str(item.get("delegationId") or ""),
            str(item.get("status") or ""),
        )
        for item in list(snapshot.get("subagentCards") or [])
        if str(item.get("id") or "")
    )


def _stable_research_events(snapshot: dict[str, Any]) -> list[tuple[int, str]]:
    return sorted(
        (
            int(item.get("eventSeq") or 0),
            str(item.get("topic") or ""),
        )
        for item in list(snapshot.get("researchEvents") or [])
        if int(item.get("eventSeq") or 0) > 0
    )


class WebActivityAuditObserver:
    """Observe real Web runtime cards without making product state changes."""

    def __init__(
        self,
        *,
        web_url: str,
        session_id: str,
        browser_executable: str | None = None,
        headless: bool = True,
    ) -> None:
        self.web_url = web_url.rstrip("/")
        self.session_id = session_id
        self.browser_executable = _browser_executable(browser_executable)
        self.headless = headless
        self._playwright: Any = None
        self._browser: Any = None
        self._page: Any = None
        self._last_sample_at = 0.0
        self._samples: list[dict[str, Any]] = []
        self._errors: list[str] = []

    def start(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError("Python Playwright is required for --web-url live activity audit") from exc
        self._playwright = sync_playwright().start()
        launch_options: dict[str, Any] = {"headless": self.headless}
        if self.browser_executable:
            launch_options["executable_path"] = self.browser_executable
        self._browser = self._playwright.chromium.launch(**launch_options)
        context = self._browser.new_context(viewport={"width": 1600, "height": 1000}, locale="zh-CN")
        self._page = context.new_page()
        target = f"{self.web_url}/chat?id={quote(self.session_id, safe='')}"
        self._page.goto(target, wait_until="load", timeout=45_000)
        self._page.locator("body").wait_for(state="visible", timeout=20_000)

    def _overview(self) -> None:
        if self._page is None:
            return
        overview = self._page.locator('[data-workbench-document-kind="session_overview"]')
        if overview.count() and overview.first.get_attribute("aria-selected") != "true":
            overview.first.click(timeout=5_000)

    def _snapshot(self, phase: str, *, include_research_detail: bool = False) -> dict[str, Any]:
        if self._page is None:
            return {"phase": phase, "runtimeCards": [], "subagentCards": [], "researchEvents": []}
        self._overview()
        runtime_cards = self._page.locator("[data-runtime-activity-runtime]").evaluate_all(
            """elements => elements.map(element => ({
                runtimeId: element.getAttribute('data-runtime-activity-runtime') || '',
                status: element.getAttribute('data-runtime-activity-status') || '',
                eventCount: Number(element.getAttribute('data-runtime-activity-count') || 0),
                text: (element.innerText || '').trim(),
            }))"""
        )
        subagent_cards = self._page.locator("[data-subagent-return-id]").evaluate_all(
            """elements => elements.map(element => ({
                id: element.getAttribute('data-subagent-return-id') || '',
                delegationId: element.getAttribute('data-subagent-delegation-id') || '',
                status: element.getAttribute('data-subagent-status') || '',
                text: (element.innerText || '').trim(),
            }))"""
        )
        research_events: list[dict[str, Any]] = []
        if include_research_detail:
            research_row = self._page.locator('[data-runtime-activity-runtime="research"]')
            if research_row.count():
                research_row.first.click(timeout=5_000)
                detail = self._page.locator('[data-runtime-activity-detail="research"]')
                detail.wait_for(state="visible", timeout=8_000)
                research_events = detail.locator("[data-runtime-activity-seq]").evaluate_all(
                    """elements => elements.map(element => ({
                        eventSeq: Number(element.getAttribute('data-runtime-activity-seq') || 0),
                        topic: element.getAttribute('data-runtime-activity-topic') || '',
                        text: (element.innerText || '').trim(),
                    }))"""
                )
        snapshot = {
            "phase": phase,
            "capturedAtMs": int(time.time() * 1000),
            "url": self._page.url,
            "runtimeCards": runtime_cards,
            "subagentCards": subagent_cards,
            "researchEvents": research_events,
        }
        self._samples.append(snapshot)
        return snapshot

    def sample_live(self) -> None:
        if time.monotonic() - self._last_sample_at < 0.75:
            return
        self._last_sample_at = time.monotonic()
        try:
            self._snapshot("live")
        except Exception as exc:  # noqa: BLE001 - preserve browser-side audit errors.
            self._errors.append(f"{type(exc).__name__}: {exc}")

    def finish(self) -> dict[str, Any]:
        terminal_live: dict[str, Any] = {}
        terminal_reload: dict[str, Any] = {}
        try:
            terminal_live = self._snapshot("terminal_live", include_research_detail=True)
            self._page.reload(wait_until="load", timeout=45_000)
            self._page.locator("body").wait_for(state="visible", timeout=20_000)
            deadline = time.time() + 15
            while time.time() < deadline:
                # A history message can render its summary card before the
                # authoritative snapshot hydrates the runtime detail. Wait for
                # the actual terminal evidence, not merely the first card.
                terminal_reload = self._snapshot("terminal_reload", include_research_detail=True)
                if (
                    _stable_runtime_cards(terminal_live) == _stable_runtime_cards(terminal_reload)
                    and _stable_subagent_cards(terminal_live) == _stable_subagent_cards(terminal_reload)
                    and _stable_research_events(terminal_live) == _stable_research_events(terminal_reload)
                ):
                    break
                time.sleep(0.25)
        except Exception as exc:  # noqa: BLE001
            self._errors.append(f"{type(exc).__name__}: {exc}")
        live_runtime_ids = sorted(
            {
                str(item.get("runtimeId") or "")
                for sample in self._samples
                if sample.get("phase") == "live"
                for item in list(sample.get("runtimeCards") or [])
                if str(item.get("runtimeId") or "")
            }
        )
        live_subagent_ids = sorted(
            {
                str(item.get("id") or "")
                for sample in self._samples
                if sample.get("phase") == "live"
                for item in list(sample.get("subagentCards") or [])
                if str(item.get("id") or "")
            }
        )
        parity = {
            "runtimeCards": _stable_runtime_cards(terminal_live) == _stable_runtime_cards(terminal_reload),
            "subagentCards": _stable_subagent_cards(terminal_live) == _stable_subagent_cards(terminal_reload),
            "researchEvents": _stable_research_events(terminal_live) == _stable_research_events(terminal_reload),
        }
        return {
            "performed": True,
            "sessionId": self.session_id,
            "browserExecutable": Path(self.browser_executable).name if self.browser_executable else "playwright-bundled",
            "liveSampleCount": sum(1 for sample in self._samples if sample.get("phase") == "live"),
            "liveRuntimeIds": live_runtime_ids,
            "liveSubagentIds": live_subagent_ids,
            "terminalLive": terminal_live,
            "terminalReload": terminal_reload,
            "parity": parity,
            "errors": self._errors,
        }

    def close(self) -> None:
        if self._browser is not None:
            self._browser.close()
        if self._playwright is not None:
            self._playwright.stop()
        self._browser = None
        self._playwright = None
        self._page = None
