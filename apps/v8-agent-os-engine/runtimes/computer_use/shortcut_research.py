from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict
from urllib.parse import urlparse

from runtimes.computer_use.shortcut_registry import normalize_shortcut_platform


class ShortcutResearchError(RuntimeError):
    pass


def _compact(value: Any, *, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "..."


def _default_broker(**kwargs: Any) -> str:
    from core.tools.web_fetcher import web_broker

    return str(web_broker.func(**kwargs))


class ComputerUseShortcutResearch:
    """One bounded Web Broker lookup for an app-local shortcut guide."""

    def __init__(self, *, broker: Callable[..., str] | None = None) -> None:
        self._broker = broker or _default_broker

    @staticmethod
    def _identity(app: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(app or {})
        windows = [dict(item) for item in list(payload.get("runningWindows") or []) if isinstance(item, dict)]
        process_names = [str(item or "").strip().lower() for item in list(payload.get("processNames") or [])]
        process_names.extend(
            str(item.get("processName") or "").strip().lower() for item in windows
        )
        process_names = list(dict.fromkeys(item for item in process_names if item))
        return {
            "appId": str(payload.get("appId") or "").strip(),
            "displayName": str(payload.get("displayName") or payload.get("appId") or "").strip(),
            "processNames": process_names[:4],
        }

    @staticmethod
    def _parse(raw: str, *, stage: str) -> Dict[str, Any]:
        try:
            payload = json.loads(str(raw or ""))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ShortcutResearchError(f"Web Broker returned invalid {stage} data") from exc
        if not isinstance(payload, dict):
            raise ShortcutResearchError(f"Web Broker returned invalid {stage} payload")
        return payload

    @staticmethod
    def _valid_url(value: Any) -> str:
        url = str(value or "").strip()
        parsed = urlparse(url)
        return url if parsed.scheme in {"http", "https"} and parsed.netloc else ""

    @staticmethod
    def _score(result: Dict[str, Any], *, app_tokens: set[str]) -> int:
        quality = dict(result.get("sourceQualityHints") or {})
        score = int(quality.get("authorityScore") or 0) + int(result.get("relevanceScore") or 0)
        haystack = " ".join(
            [
                str(result.get("title") or ""),
                str(result.get("snippet") or result.get("description") or ""),
                str(result.get("url") or ""),
            ]
        ).lower()
        score += sum(15 for token in app_tokens if len(token) >= 3 and token in haystack)
        if any(token in haystack for token in ("official", "support", "help", "docs", "manual")):
            score += 12
        return score

    def research(
        self,
        *,
        app: Dict[str, Any],
        action: str,
        platform: str | None,
        tool_call_id: str,
    ) -> Dict[str, Any]:
        identity = self._identity(app)
        if not identity["appId"] or not identity["displayName"] or not identity["processNames"]:
            raise ShortcutResearchError("An exact running application binding is required before shortcut research")
        normalized_action = _compact(action, limit=100)
        if not normalized_action or re.search(r"https?://", normalized_action, re.I):
            raise ShortcutResearchError("action must briefly describe the current app operation")
        resolved_platform = normalize_shortcut_platform(platform)
        process_stem = str(identity["processNames"][0]).rsplit(".", 1)[0]
        query = (
            f'"{identity["displayName"]}" {process_stem} {resolved_platform} '
            f'{normalized_action} keyboard shortcut official help'
        )
        search = self._parse(
            self._broker(
                target=query,
                mode="search",
                fetch_mode="static",
                limit=5,
                debug=False,
                tool_call_id=tool_call_id,
            ),
            stage="search",
        )
        results = [dict(item) for item in list(search.get("results") or []) if isinstance(item, dict)]
        app_tokens = {
            token
            for token in re.split(r"[^a-z0-9]+", f'{identity["displayName"]} {process_stem}'.lower())
            if token
        }
        candidates = []
        for item in results:
            url = self._valid_url(item.get("finalUrl") or item.get("url"))
            if not url:
                continue
            candidates.append(
                {
                    "title": _compact(item.get("title"), limit=150),
                    "url": url,
                    "snippet": _compact(item.get("snippet") or item.get("description") or item.get("text"), limit=260),
                    "authority": int(dict(item.get("sourceQualityHints") or {}).get("authorityScore") or 0),
                    "score": self._score(item, app_tokens=app_tokens),
                }
            )
        candidates.sort(key=lambda item: int(item.get("score") or 0), reverse=True)
        candidates = candidates[:3]
        if not candidates:
            return {
                "status": "not_found",
                "appBinding": identity,
                "platform": resolved_platform,
                "action": normalized_action,
                "query": query,
                "allowedSources": [],
                "instruction": "No usable source was found. Continue with semantic or visual controls; do not invent a shortcut.",
            }

        selected = dict(candidates[0])
        excerpt = ""
        try:
            page = self._parse(
                self._broker(
                    target=selected["url"],
                    mode="read",
                    fetch_mode="static",
                    limit=1,
                    debug=False,
                    tool_call_id=tool_call_id,
                ),
                stage="read",
            )
            excerpt = _compact(page.get("textPreview") or page.get("text") or page.get("summary"), limit=760)
        except ShortcutResearchError:
            excerpt = ""
        return {
            "status": "found",
            "appBinding": identity,
            "platform": resolved_platform,
            "action": normalized_action,
            "query": query,
            "allowedSources": [item["url"] for item in candidates],
            "candidates": candidates,
            "selectedSource": {
                "title": selected.get("title"),
                "url": selected.get("url"),
                "excerpt": excerpt,
            },
            "untrustedEvidence": True,
            "instruction": (
                "Use only a shortcut explicitly supported by this evidence. Call desktop_shortcut_learn once; "
                "the binding is persisted only if the focused app visibly changes state."
            ),
        }


shortcut_research = ComputerUseShortcutResearch()
