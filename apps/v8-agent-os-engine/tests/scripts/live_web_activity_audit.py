from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit


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


def _stable_narratives(snapshot: dict[str, Any]) -> list[tuple[int, str]]:
    # turnId is optional and can appear/disappear during history hydration.
    # Compare the ordered rendered message contents, not that transient label.
    return [(int(item.get("chars") or 0), str(item.get("sha256") or ""))
            for item in snapshot.get("narratives") or []]


def _narratives_ready(snapshot: dict[str, Any]) -> bool:
    return snapshot.get("pageReady") is True and any(chars > 0 and digest for chars, digest in _stable_narratives(snapshot))


def _snapshot_signature(snapshot: dict[str, Any]) -> tuple[Any, ...]:
    return (_stable_runtime_cards(snapshot), _stable_subagent_cards(snapshot),
            _stable_research_events(snapshot), _stable_narratives(snapshot))


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
        self._narrative_changes: list[dict[str, Any]] = []

    def _narrative_snapshot(self) -> list[dict[str, Any]]:
        # Inspect the actual narrative wrapper in ContentDispatcher, excluding
        # thinking/tool cards. Store lengths/hashes, not another transcript.
        rows = self._page.locator('.v8-chat-viewport-surface [aria-live="polite"]').evaluate_all(
            """elements => elements.map((element, index) => ({
                turnId: element.closest('[data-turn-id]')?.getAttribute('data-turn-id') || '',
                position: index,
                texts: Array.from(element.children).filter(child =>
                    (child.classList.contains('mb-1.5') && child.classList.contains('mt-0.5'))
                    || child.classList.contains('prose')).map(child => child.innerText || ''),
            }))"""
        )
        result = []
        for row in rows:
            rendered = "\n\n".join(row.pop("texts"))
            result.append({**row, "chars": len(rendered), "sha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest()})
        return result

    def _page_ready(self) -> bool:
        current, expected = urlsplit(self._page.url), urlsplit(self.web_url)
        return (current.netloc == expected.netloc and current.path.rstrip("/") == "/chat"
                and parse_qs(current.query).get("id") == [self.session_id]
                and self._page.locator(".v8-chat-viewport-surface").is_visible())

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
        self._page.on("pageerror", lambda error: self._errors.append(f"browser_page_error: {str(error)[:300]}"))
        target = f"{self.web_url}/chat?id={quote(self.session_id, safe='')}"
        self._page.goto(target, wait_until="load", timeout=45_000)
        self._page.locator(".v8-chat-viewport-surface").wait_for(state="visible", timeout=20_000)
        if not self._page_ready():
            raise RuntimeError("web_audit_wrong_session_or_chat_unavailable")

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
            "pageReady": self._page_ready(),
            "runtimeCards": runtime_cards,
            "subagentCards": subagent_cards,
            "researchEvents": research_events,
            "narratives": self._narrative_snapshot(),
        }
        if not self._narrative_changes or self._narrative_changes[-1]["narratives"] != snapshot["narratives"]:
            self._narrative_changes.append({"capturedAtMs": snapshot["capturedAtMs"], "phase": phase,
                                            "narratives": snapshot["narratives"]})
        self._samples.append(snapshot)
        return snapshot

    def _wait_terminal_snapshot(self, phase: str, *, expected: dict[str, Any] | None = None) -> dict[str, Any]:
        deadline = time.monotonic() + 15
        previous, stable_since = None, None
        while True:
            snapshot = self._snapshot(phase, include_research_detail=True)
            signature = _snapshot_signature(snapshot)
            now = time.monotonic()
            eligible = _narratives_ready(snapshot) and (expected is None or signature == _snapshot_signature(expected))
            if eligible:
                if signature != previous or stable_since is None:
                    stable_since = now
                elif now - stable_since >= 1:
                    return snapshot
            else:
                stable_since = None
            previous = signature
            if now >= deadline:
                self._errors.append(f"{phase}_content_not_ready_or_stable")
                return snapshot
            time.sleep(0.25)

    def sample_live(self) -> None:
        if time.monotonic() - self._last_sample_at < 0.75:
            return
        self._last_sample_at = time.monotonic()
        try:
            self._snapshot("live")
        except Exception as exc:  # noqa: BLE001 - preserve browser-side audit errors.
            self._errors.append(f"{type(exc).__name__}: {exc}")

    def _terminal_runtime_details(self, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        """Collect each visible runtime once at termination, not in live sampling."""
        details = []
        for card in snapshot.get("runtimeCards") or []:
            runtime_id = str(card.get("runtimeId") or "")
            if not runtime_id:
                continue
            self._overview()
            row = self._page.locator(f'[data-runtime-activity-runtime="{runtime_id}"]')
            row.first.click(timeout=5_000)
            panel = self._page.locator(f'[data-runtime-activity-detail="{runtime_id}"]')
            panel.wait_for(state="visible", timeout=8_000)
            rows = panel.locator("ol > li").evaluate_all(
                """elements => elements.map((element, position) => ({
                    position,
                    eventSeq: Number(element.getAttribute('data-runtime-activity-seq') || 0),
                    topic: element.getAttribute('data-runtime-activity-topic') || '',
                    summary: element.querySelector('p')?.innerText || '',
                }))"""
            )
            for entry in rows:
                summary = str(entry.pop("summary", ""))
                entry["summarySha256"] = hashlib.sha256(summary.encode("utf-8")).hexdigest()
                entry["identitySource"] = "event_seq" if entry["eventSeq"] > 0 else "dom_position_and_summary_hash"
            details.append({"runtimeId": runtime_id, "capturedAtMs": int(time.time() * 1000),
                            "overviewEventCount": card.get("eventCount"), "observedEntryCount": len(rows), "entries": rows})
        self._overview()
        return details

    def finish(self) -> dict[str, Any]:
        terminal_live: dict[str, Any] = {}
        terminal_reload: dict[str, Any] = {}
        try:
            terminal_live = self._wait_terminal_snapshot("terminal_live")
            terminal_live["runtimeDetails"] = self._terminal_runtime_details(terminal_live)
            self._page.reload(wait_until="load", timeout=45_000)
            self._page.locator(".v8-chat-viewport-surface").wait_for(state="visible", timeout=20_000)
            terminal_reload = self._wait_terminal_snapshot("terminal_reload", expected=terminal_live)
            terminal_reload["runtimeDetails"] = self._terminal_runtime_details(terminal_reload)
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
        valid = _narratives_ready(terminal_live) and _narratives_ready(terminal_reload) and not self._errors
        parity = {
            "runtimeCards": valid and _stable_runtime_cards(terminal_live) == _stable_runtime_cards(terminal_reload),
            "subagentCards": valid and _stable_subagent_cards(terminal_live) == _stable_subagent_cards(terminal_reload),
            "researchEvents": valid and _stable_research_events(terminal_live) == _stable_research_events(terminal_reload),
            "renderedNarratives": valid and _stable_narratives(terminal_live) == _stable_narratives(terminal_reload),
        }
        live_samples = [sample for sample in self._samples if sample.get("phase") == "live"]
        observed = [sample for sample in live_samples if _narratives_ready(sample)]
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
            "narrativeChanges": self._narrative_changes,
            "narrativeSamplingSeconds": 0.75,
            "narrativeMeasurement": {
                "status": "observed" if observed and valid else "unverified",
                "method": "DOM samples; not exact first-token latency or a performance pass",
                "nonemptyLiveSamples": len(observed),
                "maxSamplingGapMs": max((right["capturedAtMs"] - left["capturedAtMs"]
                                         for left, right in zip(live_samples, live_samples[1:])), default=None),
            },
        }

    def close(self) -> None:
        if self._browser is not None:
            self._browser.close()
        if self._playwright is not None:
            self._playwright.stop()
        self._browser = None
        self._playwright = None
        self._page = None
