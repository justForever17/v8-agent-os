from __future__ import annotations

import contextlib
import html
import json
import mimetypes
import re
import shutil
import uuid
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import parse_qs, parse_qsl, unquote, urlencode, urlparse, urlunparse

from langchain_core.tools import InjectedToolCallId, tool

from core.artifact_store import artifact_store
from core.storage import storage
from core.v8_agent_os_paths import workspace_download_root
from core.workspace_guard import build_workspace_path_status, ensure_workspace_auto_create_allowed
from core.workspace_resolution import workspace_resolution_service
from erc.runtime_context import get_runtime_context
from erc.safety_guardian import safety_guardian


DownloadMediaPreference = Literal["auto", "video", "images", "all"]
_PLATFORM_PROFILE_FILENAME = "media_download_profiles.json"
_PLATFORM_STRATEGY_FILENAME = "media_download_platform_strategies.json"
_EXCLUDED_DOWNLOAD_SUFFIXES = {
    ".description",
    ".json",
    ".part",
    ".temp",
    ".txt",
    ".vtt",
    ".ytdl",
}
_URL_IN_TEXT_RE = re.compile(r"https?://[^\s\"'<>\\\]\}\)]+", re.IGNORECASE)
_MEDIA_URL_RE = re.compile(
    r"https?://[^\s\"'<>]+?(?:\.mp4|\.mov|\.m4v|\.webm|\.m3u8|\.mpd|\.m4s|\.jpg|\.jpeg|\.png|\.webp)(?:[?#][^\s\"'<>]*)?",
    re.IGNORECASE,
)
_PLATFORM_STRATEGY_CACHE: dict[str, Any] | None = None


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _as_text_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_safe_text(item) for item in value if _safe_text(item)]
    if isinstance(value, tuple):
        return [_safe_text(item) for item in value if _safe_text(item)]
    if _safe_text(value):
        return [_safe_text(value)]
    return []


def _load_platform_strategies() -> dict[str, Any]:
    global _PLATFORM_STRATEGY_CACHE
    if _PLATFORM_STRATEGY_CACHE is not None:
        return _PLATFORM_STRATEGY_CACHE

    strategy_path = Path(__file__).with_name(_PLATFORM_STRATEGY_FILENAME)
    try:
        with strategy_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception:
        payload = {}
    _PLATFORM_STRATEGY_CACHE = payload if isinstance(payload, dict) else {}
    return _PLATFORM_STRATEGY_CACHE


def _strategy_global() -> dict[str, Any]:
    payload = _load_platform_strategies().get("global")
    return dict(payload) if isinstance(payload, dict) else {}


def _strategy_platforms() -> dict[str, Any]:
    payload = _load_platform_strategies().get("platforms")
    return dict(payload) if isinstance(payload, dict) else {}


def _resolve_platform_alias(platform: str) -> str:
    normalized = _safe_text(platform)
    platforms = _strategy_platforms()
    seen: set[str] = set()
    while normalized and normalized not in seen:
        seen.add(normalized)
        payload = platforms.get(normalized)
        if not isinstance(payload, dict):
            break
        alias = _safe_text(payload.get("aliasOf"))
        if not alias:
            break
        normalized = alias
    return normalized


def _platform_strategy(platform: str) -> dict[str, Any]:
    normalized = _resolve_platform_alias(platform)
    payload = _strategy_platforms().get(normalized)
    return dict(payload) if isinstance(payload, dict) else {}


def _strategy_global_list(key: str) -> list[str]:
    return _as_text_list(_strategy_global().get(key))


def _platform_strategy_list(platform: str, key: str) -> list[str]:
    return _as_text_list(_platform_strategy(platform).get(key))


def _all_platform_strategy_hints(key: str) -> list[str]:
    values: list[str] = []
    for platform in _strategy_platforms():
        for hint in _platform_strategy_list(platform, key):
            if hint not in values:
                values.append(hint)
    return values


def _platform_profile_defaults() -> dict[str, dict[str, Any]]:
    defaults: dict[str, dict[str, Any]] = {}
    for platform, payload in _strategy_platforms().items():
        if not isinstance(payload, dict) or payload.get("aliasOf"):
            continue
        profile = payload.get("profile")
        if isinstance(profile, dict):
            defaults[platform] = dict(profile)
    return defaults


def _strategy_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _extract_first_url(value: str) -> str:
    raw = _safe_text(value)
    if not raw:
        return ""
    match = _URL_IN_TEXT_RE.search(raw)
    if not match:
        return raw
    return match.group(0).rstrip("，。；;!！?？）)]}>\"'")


def _enforce_safety_decision(decision, *, tool_call_id: str, question: str) -> tuple[bool, str | None]:
    safety_guardian.log_decision_event(
        action="media_download_safety",
        decision=decision,
        subject=question,
        metadata={"toolCallId": tool_call_id},
    )
    if decision.is_allow():
        return True, None

    from langgraph.types import interrupt

    response = interrupt(decision.to_interrupt_request(question=question, tool_call_id=tool_call_id))
    approved = True
    if isinstance(response, dict):
        approved = bool(response.get("approved", True))

    if decision.is_block() or not decision.allow_override:
        return False, f"Safety Guardian 已阻止媒体下载：{decision.reason}"

    if not approved:
        return False, f"Safety Guardian 未获得批准，媒体下载已取消：{decision.reason}"

    return True, None


def _guard_url(url: str, *, tool_call_id: str) -> tuple[bool, str | None]:
    runtime_context = get_runtime_context()
    decision = safety_guardian.assess_http_request("GET", url, body=None, runtime_context=runtime_context)
    return _enforce_safety_decision(
        decision,
        tool_call_id=tool_call_id,
        question=f"Safety Guardian 检测到需要从外部站点下载媒体资源，是否继续？\n\nGET {url}",
    )


def _slugify(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", _safe_text(value))
    return cleaned.strip("-._") or "media"


def _resolve_workspace_download_root(runtime_context: dict[str, Any]) -> tuple[Path, Path, dict[str, Any]]:
    resolved_workspace_path = workspace_resolution_service.resolve_workspace_path(
        runtime_kind=str(runtime_context.get("runtime_kind") or "chat"),
        session_id=str(runtime_context.get("session_id") or "").strip() or None,
        explicit_workspace_id=str(runtime_context.get("workspace_id") or "").strip() or None,
        explicit_workspace_path=str(runtime_context.get("workspace_path") or "").strip() or None,
        explicit_project_id=str(runtime_context.get("project_id") or "").strip() or None,
    )
    workspace_status = build_workspace_path_status(resolved_workspace_path)
    workspace_root = ensure_workspace_auto_create_allowed(
        resolved_workspace_path,
        source="media_downloader.download_media_for_vision",
        allow_missing=True,
    )
    workspace_root.mkdir(parents=True, exist_ok=True)
    download_root = workspace_download_root(workspace_root)
    download_root.mkdir(parents=True, exist_ok=True)
    return workspace_root, download_root, workspace_status


def _build_download_dir(download_root: Path, url: str) -> Path:
    parsed = urlparse(url)
    host = _slugify(parsed.hostname or "unknown-host")
    target = download_root / host / uuid.uuid4().hex
    target.mkdir(parents=True, exist_ok=True)
    return target


def _build_direct_media_filename(url: str, *, kind: str, content_type: str = "") -> str:
    parsed = urlparse(url)
    raw_name = Path(unquote(parsed.path)).name
    safe_name = (_slugify(raw_name) or "media")[:80].rstrip("-._") or "media"
    if "." in safe_name:
        return safe_name
    extension = {
        "video": ".mp4",
        "image": ".jpg",
        "audio": ".mp3",
    }.get(kind, "")
    if not extension and content_type:
        guessed = mimetypes.guess_extension(content_type.split(";", 1)[0].strip())
        extension = guessed or ""
    return f"{safe_name or 'media'}{extension}"


def _load_platform_profiles() -> dict[str, Any]:
    payload = storage.read_json(_PLATFORM_PROFILE_FILENAME) or {}
    configured = payload.get("platforms") if isinstance(payload, dict) else None
    if not isinstance(configured, dict):
        configured = {}

    defaults = _platform_profile_defaults()
    merged: dict[str, Any] = {}
    keys = set(defaults) | {str(key).strip() for key in configured.keys()}
    for key in keys:
        normalized = str(key or "").strip()
        if not normalized:
            continue
        merged[normalized] = {
            **(defaults.get(normalized) or {}),
            **(configured.get(normalized) or {}),
        }
    return merged


def _resolve_platform_profile(url: str) -> tuple[str, dict[str, Any]]:
    platform = _platform_from_url(url)
    profiles = _load_platform_profiles()
    return platform, dict(profiles.get(platform) or {})


def _guess_kind(path: Path) -> tuple[str, str]:
    mime_type, _ = mimetypes.guess_type(str(path))
    mime_type = mime_type or "application/octet-stream"
    if mime_type.startswith("video/"):
        return "video", mime_type
    if mime_type.startswith("image/"):
        return "image", mime_type
    if mime_type.startswith("audio/"):
        return "audio", mime_type
    return "file", mime_type


def _guess_kind_from_url(url: str) -> str:
    normalized = url.lower()
    if any(ext in normalized for ext in _strategy_global_list("videoUrlHints")):
        return "video"
    if any(hint.lower() in normalized for hint in _all_platform_strategy_hints("directMediaHints")):
        return "video"
    if any(ext in normalized for ext in _strategy_global_list("imageUrlHints")):
        return "image"
    if any(ext in normalized for ext in _strategy_global_list("audioUrlHints")):
        return "audio"
    return "file"


def _looks_like_direct_media(url: str) -> bool:
    normalized = url.lower()
    return _guess_kind_from_url(url) in {"video", "image", "audio"} or any(
        token in normalized
        for token in _strategy_global_list("directMediaHints")
    )


def _looks_like_douyin_direct_media(url: str) -> bool:
    normalized = _safe_text(url).lower()
    return any(hint.lower() in normalized for hint in _platform_strategy_list("douyin", "directMediaHints"))


def _collect_media_files(download_dir: Path) -> list[Path]:
    files: list[Path] = []
    for candidate in sorted(download_dir.rglob("*")):
        if not candidate.is_file():
            continue
        if candidate.suffix.lower() in _EXCLUDED_DOWNLOAD_SUFFIXES:
            continue
        files.append(candidate)
    return files


def _dedupe_media_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in candidates:
        url = _safe_text(candidate.get("url"))
        if not url or url in seen:
            continue
        seen.add(url)
        normalized = dict(candidate)
        normalized["url"] = url
        normalized["kind"] = _safe_text(candidate.get("kind")) or _guess_kind_from_url(url)
        unique.append(normalized)
    return unique


def _choose_media_candidate(
    candidates: list[dict[str, Any]],
    *,
    prefer: DownloadMediaPreference,
) -> dict[str, Any] | None:
    deduped = _dedupe_media_candidates(candidates)
    if not deduped:
        return None

    def _score(item: dict[str, Any]) -> tuple[int, int]:
        kind = _safe_text(item.get("kind"))
        source = _safe_text(item.get("source"))
        kind_score = {
            "video": 100,
            "image": 80,
            "audio": 60,
            "file": 10,
        }.get(kind, 0)
        if prefer == "video":
            kind_score += 30 if kind == "video" else -20
        elif prefer == "images":
            kind_score += 30 if kind == "image" else -20
        elif prefer == "all":
            kind_score += 10
        source_weights = _strategy_global().get("candidateSourceWeights")
        if isinstance(source_weights, dict):
            kind_score += _strategy_int(source_weights.get(source))
        if source.startswith("douyin_") or _looks_like_douyin_direct_media(_safe_text(item.get("url"))):
            url = _safe_text(item.get("url")).lower()
            douyin_weight = _platform_strategy("douyin").get("candidateWeight")
            douyin_weight = douyin_weight if isinstance(douyin_weight, dict) else {}
            if _looks_like_douyin_direct_media(url):
                kind_score += _strategy_int(douyin_weight.get("directMediaHint"))
            hint_weights = douyin_weight.get("hintWeights")
            if isinstance(hint_weights, dict):
                for hint, weight in hint_weights.items():
                    if _safe_text(hint).lower() in url:
                        kind_score += _strategy_int(weight)
            douyin_source_weights = douyin_weight.get("sourceExact")
            if isinstance(douyin_source_weights, dict):
                kind_score += _strategy_int(douyin_source_weights.get(source))
            if kind == "image" and any(hint.lower() in url for hint in _platform_strategy_list("douyin", "lowValueMediaHints")):
                kind_score += _strategy_int(douyin_weight.get("lowValueImagePenalty"))
            if kind == "audio" and "music" in url:
                kind_score += _strategy_int(douyin_weight.get("audioMusicPenalty"))
        return kind_score, -len(_safe_text(item.get("url")))

    return sorted(deduped, key=_score, reverse=True)[0]


def _load_browser_sync():
    try:
        from patchright.sync_api import sync_playwright  # type: ignore

        return sync_playwright, "patchright", None
    except Exception:
        try:
            from playwright.sync_api import sync_playwright  # type: ignore

            return sync_playwright, "playwright", None
        except Exception as exc:  # pragma: no cover
            return None, "", str(exc)


def _normalize_embedded_media_url(url: str) -> str:
    normalized = _safe_text(url)
    replacements = {
        "\\/": "/",
        "\\u002F": "/",
        "\\u002f": "/",
        "\\u003A": ":",
        "\\u003a": ":",
        "\\u0026": "&",
        "\\u0026amp;": "&",
    }
    for old, new in replacements.items():
        normalized = normalized.replace(old, new)
    normalized = html.unescape(normalized)
    if re.match(r"^https?%3a", normalized, re.IGNORECASE):
        normalized = unquote(normalized)
    return normalized.rstrip("，。；;!！?？）)]}>\"'`,\\")


def _text_url_variants(value: str) -> list[str]:
    raw = _safe_text(value)
    variants: list[str] = []

    def _add(candidate: str) -> None:
        candidate = _safe_text(candidate)
        if candidate and candidate not in variants:
            variants.append(candidate)

    _add(raw)
    _add(html.unescape(raw))
    if re.search(r"https?%3a(?:%2f){2}", raw, re.IGNORECASE):
        _add(unquote(raw))
    _add(_normalize_embedded_media_url(raw))
    return variants


def _extract_media_urls_from_text(value: str, *, source: str) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    seen: set[str] = set()
    for variant in _text_url_variants(value):
        for pattern in (_MEDIA_URL_RE, _URL_IN_TEXT_RE):
            for match in pattern.findall(variant):
                normalized = _normalize_embedded_media_url(match)
                if not normalized or normalized in seen:
                    continue
                kind = _guess_kind_from_url(normalized)
                if kind not in {"video", "image", "audio"}:
                    continue
                seen.add(normalized)
                hits.append({"url": normalized, "source": source, "kind": kind})
    return hits


def _extract_media_urls_from_json_like(value: Any, *, source: str) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []

    def _walk(node: Any):
        if isinstance(node, dict):
            for nested in node.values():
                _walk(nested)
            return
        if isinstance(node, list):
            for nested in node:
                _walk(nested)
            return
        if not isinstance(node, str):
            return
        hits.extend(_extract_media_urls_from_text(node, source=source))

    _walk(value)
    return hits


def _douyin_aweme_id_from_url(url: str) -> str:
    parsed = urlparse(url)
    path_match = re.search(r"/video/(\d+)", parsed.path or "")
    if path_match:
        return path_match.group(1)
    query = parse_qs(parsed.query or "")
    for key in ("aweme_id", "item_ids", "item_id", "modal_id"):
        value = _safe_text((query.get(key) or [""])[0])
        if re.fullmatch(r"\d{12,24}", value):
            return value
    return ""


def _douyin_mobile_headers() -> dict[str, str]:
    resolver = _platform_strategy("douyin").get("sharePageResolver")
    if isinstance(resolver, dict) and isinstance(resolver.get("mobileHeaders"), dict):
        return {str(key): str(value) for key, value in resolver["mobileHeaders"].items()}
    return {}


def _resolve_douyin_mobile_share_page(
    url: str,
    *,
    prefer: DownloadMediaPreference,
) -> dict[str, Any] | None:
    aweme_id = _douyin_aweme_id_from_url(url)
    if not aweme_id:
        return None

    requests, import_error = _load_requests()
    if requests is None:
        return {
            "resolved": False,
            "platform": "douyin",
            "strategy": "douyin_mobile_share_page",
            "error": f"无法解析抖音移动分享页：{import_error}",
        }

    resolver = _platform_strategy("douyin").get("sharePageResolver")
    resolver = resolver if isinstance(resolver, dict) else {}
    template = _safe_text(resolver.get("mobileShareUrlTemplate")) or "https://www.iesdouyin.com/share/video/{awemeId}/"
    mobile_url = template.replace("{awemeId}", aweme_id)
    try:
        response = requests.get(
            mobile_url,
            headers=_douyin_mobile_headers(),
            allow_redirects=True,
            timeout=20,
        )
        status_code = int(getattr(response, "status_code", 0) or 0)
        if status_code >= 400:
            return {
                "resolved": False,
                "platform": "douyin",
                "strategy": "douyin_mobile_share_page",
                "error": f"抖音移动分享页返回 HTTP {status_code}。",
            }
        page_text = _safe_text(getattr(response, "text", ""))
    except Exception as exc:
        return {
            "resolved": False,
            "platform": "douyin",
            "strategy": "douyin_mobile_share_page",
            "error": f"抖音移动分享页解析失败：{exc}",
        }

    candidates = [
        hit
        for hit in _extract_media_urls_from_text(page_text, source="douyin_mobile_share_page")
        if hit.get("kind") != "video" or _looks_like_douyin_direct_media(hit.get("url"))
    ]
    best_candidate = _choose_media_candidate(candidates, prefer=prefer)
    deduped = _dedupe_media_candidates(candidates)
    metadata = {
        "platform": "douyin",
        "strategy": "douyin_mobile_share_page",
        "awemeId": aweme_id,
        "mobileUrl": mobile_url,
        "candidateCount": len(deduped),
        "candidates": deduped[:5],
    }
    if best_candidate is None:
        return {
            "resolved": False,
            "platform": "douyin",
            "strategy": "douyin_mobile_share_page",
            "error": "抖音移动分享页已打开，但未抽取到可下载的视频地址。",
            "metadata": metadata,
        }
    return {
        "resolved": True,
        "platform": "douyin",
        "strategy": "douyin_mobile_share_page",
        "downloadUrl": best_candidate["url"],
        "kind": best_candidate.get("kind") or _guess_kind_from_url(best_candidate["url"]),
        "referer": mobile_url,
        "metadata": metadata,
    }


def _resolve_platform_share_page(
    url: str,
    *,
    platform: str,
    prefer: DownloadMediaPreference,
) -> dict[str, Any] | None:
    platform_strategy = _platform_strategy(platform)
    resolver_strategy = platform_strategy.get("sharePageResolver")
    resolver_strategy = resolver_strategy if isinstance(resolver_strategy, dict) else {}
    if not resolver_strategy.get("enabled"):
        return None

    fallback_resolution: dict[str, Any] | None = None
    if resolver_strategy.get("fallback") == "douyin_mobile_share_page":
        fallback_resolution = _resolve_douyin_mobile_share_page(url, prefer=prefer)
        if fallback_resolution and fallback_resolution.get("resolved"):
            return fallback_resolution

    sync_playwright, browser_engine, import_error = _load_browser_sync()
    if sync_playwright is None:
        if fallback_resolution is not None:
            return fallback_resolution
        return {
            "resolved": False,
            "platform": platform,
            "strategy": "browser_capture_unavailable",
            "error": f"分享页解析依赖浏览器抓取能力，但当前不可用：{import_error}",
        }

    candidates: list[dict[str, Any]] = []
    metadata: dict[str, Any] = {
        "platform": platform,
        "strategy": "browser_network_capture",
        "browserEngine": browser_engine,
        "finalUrl": "",
        "title": "",
        "candidateCount": 0,
    }

    def _register_candidate(candidate_url: str, *, source: str, kind: str = "") -> None:
        normalized = _safe_text(candidate_url)
        if not normalized:
            return
        direct_hints = [hint.lower() for hint in _platform_strategy_list(platform, "directMediaHints")]
        lowered_url = normalized.lower()
        if direct_hints and not any(hint in lowered_url for hint in direct_hints):
            if _guess_kind_from_url(normalized) != "image":
                return
            if not platform_strategy.get("allowImageWithoutDirectHint"):
                return
        if platform == "douyin":
            guessed_kind = kind or _guess_kind_from_url(normalized)
            if guessed_kind == "video" and not _looks_like_douyin_direct_media(normalized):
                return
        if any(hint.lower() in lowered_url for hint in _platform_strategy_list(platform, "excludedMediaHints")):
            return
        candidates.append(
            {
                "url": normalized,
                "source": source,
                "kind": kind or _guess_kind_from_url(normalized),
            }
        )

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                context = browser.new_context()
                page = context.new_page()

                def _on_request(request) -> None:
                    request_url = _safe_text(getattr(request, "url", ""))
                    if request_url and (
                        _guess_kind_from_url(request_url) in {"video", "image"}
                        or (platform == "douyin" and _looks_like_douyin_direct_media(request_url))
                    ):
                        _register_candidate(request_url, source=f"{platform}_network_capture")

                def _on_response(response) -> None:
                    response_url = _safe_text(getattr(response, "url", ""))
                    lowered = response_url.lower()
                    if response_url and _guess_kind_from_url(response_url) in {"video", "image"}:
                        _register_candidate(response_url, source=f"{platform}_network_capture")
                    share_api_hints = [hint.lower() for hint in _platform_strategy_list(platform, "shareApiHints")]
                    if share_api_hints and any(hint in lowered for hint in share_api_hints):
                        source_name = f"{platform}_share_api"
                        try:
                            payload = response.json()
                        except Exception:
                            payload = None
                        if payload is not None:
                            for hit in _extract_media_urls_from_json_like(payload, source=source_name):
                                _register_candidate(hit["url"], source=hit["source"], kind=hit["kind"])
                        else:
                            with contextlib.suppress(Exception):
                                body_text = response.text()
                                for hit in _extract_media_urls_from_json_like(body_text, source=f"{source_name}_text"):
                                    _register_candidate(hit["url"], source=hit["source"], kind=hit["kind"])

                page.on("request", _on_request)
                page.on("response", _on_response)
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(5000)
                with contextlib.suppress(Exception):
                    html = page.content()
                    for hit in _extract_media_urls_from_json_like(html, source=f"{platform}_html_scan"):
                        _register_candidate(hit["url"], source=hit["source"], kind=hit["kind"])
                metadata["finalUrl"] = _safe_text(page.url)
                with contextlib.suppress(Exception):
                    metadata["title"] = _safe_text(page.title())
                context.close()
            finally:
                browser.close()
    except Exception as exc:
        return {
            "resolved": False,
            "platform": platform,
            "strategy": "browser_network_capture",
            "error": f"分享页解析失败：{exc}",
        }

    best_candidate = _choose_media_candidate(candidates, prefer=prefer)
    metadata["candidateCount"] = len(_dedupe_media_candidates(candidates))
    metadata["candidates"] = _dedupe_media_candidates(candidates)[:5]
    if best_candidate is None:
        return {
            "resolved": False,
            "platform": platform,
            "strategy": "browser_network_capture",
            "error": "分享页已打开，但未捕获到可下载的真实媒体地址。",
            "metadata": metadata,
        }

    final_url = _safe_text(metadata.get("finalUrl")) or url
    return {
        "resolved": True,
        "platform": platform,
        "strategy": "browser_network_capture",
        "downloadUrl": best_candidate["url"],
        "kind": best_candidate.get("kind") or _guess_kind_from_url(best_candidate["url"]),
        "referer": final_url,
        "metadata": metadata,
    }


def _parse_cookies_from_browser(value: str) -> tuple | None:
    raw = _safe_text(value)
    if not raw:
        return None
    if ":" in raw:
        browser, profile = raw.split(":", 1)
        browser = _safe_text(browser)
        profile = _safe_text(profile)
        if browser and profile:
            return (browser, None, None, profile)
    return (_safe_text(raw),)


def _load_requests():
    try:
        import requests  # type: ignore

        return requests, None
    except Exception as exc:  # pragma: no cover
        return None, str(exc)


def _resolve_short_link_target(url: str) -> tuple[str, dict[str, Any]]:
    host = (urlparse(url).hostname or "").lower()
    if host not in {item.lower() for item in _strategy_global_list("shortlinkHosts")}:
        return url, {"resolved": False, "host": host, "strategy": "not_shortlink"}

    requests, import_error = _load_requests()
    if requests is None:
        return url, {
            "resolved": False,
            "host": host,
            "strategy": "requests_unavailable",
            "error": f"无法解析短链：{import_error}",
        }

    configured_headers = _strategy_global().get("shortlinkHeaders")
    headers = dict(configured_headers) if isinstance(configured_headers, dict) else {}
    errors: list[str] = []
    for method in ("head", "get"):
        try:
            response = getattr(requests, method)(
                url,
                headers=headers,
                allow_redirects=True,
                timeout=20,
                stream=(method == "get"),
            )
            final_url = _safe_text(getattr(response, "url", ""))
            status_code = int(getattr(response, "status_code", 0) or 0)
            if final_url and (final_url != url or status_code < 400):
                parsed = urlparse(final_url)
                final_host = (parsed.hostname or "").lower()
                if "bilibili.com" in final_host and parsed.path:
                    final_url = f"{parsed.scheme or 'https'}://{final_host}{parsed.path}"
                elif "douyin.com" in final_host and parsed.path:
                    final_url = f"{parsed.scheme or 'https'}://{final_host}{parsed.path}"
                return final_url, {
                    "resolved": final_url != url,
                    "host": host,
                    "strategy": f"requests_{method}",
                    "statusCode": status_code or None,
                }
        except Exception as exc:
            errors.append(f"{method}:{exc}")

    return url, {
        "resolved": False,
        "host": host,
        "strategy": "requests_failed",
        "error": "; ".join(errors),
    }


def _canonicalize_platform_url(url: str) -> tuple[str, dict[str, Any]]:
    original = _safe_text(url)
    if not original or _looks_like_direct_media(original):
        return original, {"changed": False, "strategy": "skip_direct_media_or_empty"}

    parsed = urlparse(original)
    platform = _platform_from_url(original)
    strategy = _platform_strategy(platform)
    canonical = strategy.get("canonical")
    if not isinstance(canonical, dict):
        return original, {"changed": False, "platform": platform, "strategy": "no_canonical_strategy"}

    scheme = parsed.scheme or "https"
    host = (parsed.hostname or "").lower()
    netloc = parsed.netloc
    canonical_host = _safe_text(canonical.get("canonicalHost"))
    host_equals = {item.lower() for item in _as_text_list(strategy.get("hostEquals"))}
    wrapper_hosts = {item.lower() for item in _as_text_list(strategy.get("wrapperHosts"))}
    if canonical_host and (host in host_equals or host in wrapper_hosts):
        netloc = canonical_host

    path = parsed.path or "/"
    if canonical.get("dropStatusMediaSuffix"):
        status_match = re.match(r"^(.*/(?:status|statuses)/\d+|/i/web/status/\d+)", path)
        if status_match:
            path = status_match.group(1)

    query = ""
    if canonical.get("stripAllQuery"):
        query = ""
    else:
        query_allowlist = {item for item in _as_text_list(canonical.get("queryAllowlist"))}
        tracking_keys = {item.lower() for item in _strategy_global_list("trackingQueryKeys")}
        pairs: list[tuple[str, str]] = []
        for key, value in parse_qsl(parsed.query, keep_blank_values=True):
            lowered = key.lower()
            if query_allowlist:
                if key in query_allowlist:
                    pairs.append((key, value))
            elif lowered not in tracking_keys:
                pairs.append((key, value))
        query = urlencode(pairs, doseq=True)

    canonical_url = urlunparse((scheme, netloc, path, "", query, ""))
    return canonical_url, {
        "changed": canonical_url != original,
        "platform": platform,
        "strategy": "platform_canonical",
    }


def _load_yt_dlp():
    try:
        import yt_dlp  # type: ignore

        return yt_dlp, None
    except Exception as exc:  # pragma: no cover
        return None, str(exc)


def _should_retry_without_cookies(error: Exception | str) -> bool:
    text = _safe_text(error).lower()
    return (
        "failed to decrypt with dpapi" in text
        or "could not copy chrome cookie database" in text
        or ("cookies" in text and "decrypt" in text)
    )


def _download_direct_media(
    url: str,
    *,
    download_dir: Path,
    kind: str,
    headers: dict[str, str] | None = None,
) -> tuple[bool, str | None]:
    requests, import_error = _load_requests()
    if requests is None:
        return False, f"未安装 requests，无法直接下载真实媒体地址：{import_error}"

    try:
        configured_headers = _strategy_global().get("directDownloadHeaders")
        request_headers = dict(configured_headers) if isinstance(configured_headers, dict) else {}
        request_headers.update(headers or {})
        response = requests.get(url, headers=request_headers, stream=True, timeout=45)
        response.raise_for_status()
        content_type = _safe_text(response.headers.get("Content-Type"))
        if not (
            content_type.startswith("video/")
            or content_type.startswith("image/")
            or content_type.startswith("audio/")
            or _looks_like_direct_media(url)
        ):
            return False, f"直接媒体下载已跳过：目标响应类型不是媒体资源 ({content_type or 'unknown'})."
        filename = _build_direct_media_filename(url, kind=kind, content_type=content_type)
        target_path = download_dir / filename
        with target_path.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 256):
                if not chunk:
                    continue
                handle.write(chunk)
        return True, None
    except Exception as exc:
        return False, f"直接媒体下载失败：{exc}"


def _select_primary_file(files: list[dict[str, Any]], prefer: DownloadMediaPreference) -> dict[str, Any] | None:
    if not files:
        return None
    if prefer == "images":
        return next((item for item in files if item.get("kind") == "image"), None) or files[0]
    if prefer == "video":
        return next((item for item in files if item.get("kind") == "video"), None) or files[0]
    if prefer == "all":
        return files[0]
    for preferred_kind in ("video", "image", "audio"):
        hit = next((item for item in files if item.get("kind") == preferred_kind), None)
        if hit:
            return hit
    return files[0]


def _platform_from_url(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    for platform, payload in _strategy_platforms().items():
        if not isinstance(payload, dict):
            continue
        if host in {item.lower() for item in _as_text_list(payload.get("hostEquals"))}:
            return _resolve_platform_alias(platform)
        if host in {item.lower() for item in _as_text_list(payload.get("wrapperHosts"))}:
            return _resolve_platform_alias(platform)
        if any(hint.lower() in host for hint in _as_text_list(payload.get("hostContains"))):
            return _resolve_platform_alias(platform)
    return host or "unknown"


@tool
def download_media_for_vision(
    url: str,
    prefer: DownloadMediaPreference = "auto",
    cookies_from_browser: str = "",
    referer: str = "",
    max_items: int = 6,
    auto_chain_to_vision: bool = False,
    analysis_prompt: str = "",
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> str:
    """Resolve share pages and download remote media into the current workspace.

    Important:
    - Downloaded media is written directly into the resolved workspace `downloaded_media` directory.
    - The returned artifact is already the canonical surface artifact for chat/web/phone display.
    - Do not use shell commands to move or rename the file unless the user explicitly asks for it.
    """

    raw_input = _safe_text(url)
    normalized_url = _extract_first_url(raw_input)
    if not normalized_url:
        return json.dumps({"ok": False, "error": "URL 不能为空。"}, ensure_ascii=False, indent=2)

    allowed, error_message = _guard_url(normalized_url, tool_call_id=tool_call_id)
    if not allowed:
        return json.dumps(
            {
                "ok": False,
                "blocked": True,
                "error": error_message or "Safety Guardian 已阻止媒体下载。",
                "message": "Safety Guardian 已阻止媒体下载。",
            },
            ensure_ascii=False,
            indent=2,
        )

    resolved_url, shortlink_resolution = _resolve_short_link_target(normalized_url)
    if resolved_url != normalized_url:
        allowed, error_message = _guard_url(resolved_url, tool_call_id=tool_call_id)
        if not allowed:
            return json.dumps(
                {
                    "ok": False,
                    "blocked": True,
                    "error": error_message or "Safety Guardian 已阻止短链跳转后的媒体下载。",
                    "message": "Safety Guardian 已阻止短链跳转后的媒体下载。",
                },
                ensure_ascii=False,
                indent=2,
            )

    canonical_url, canonical_resolution = _canonicalize_platform_url(resolved_url)
    if canonical_url != resolved_url:
        allowed, error_message = _guard_url(canonical_url, tool_call_id=tool_call_id)
        if not allowed:
            return json.dumps(
                {
                    "ok": False,
                    "blocked": True,
                    "error": error_message or "Safety Guardian 已阻止清洗后的媒体页面下载。",
                    "message": "Safety Guardian 已阻止清洗后的媒体页面下载。",
                },
                ensure_ascii=False,
                indent=2,
            )
        resolved_url = canonical_url

    platform, platform_profile = _resolve_platform_profile(resolved_url)
    effective_prefer = prefer
    if effective_prefer == "auto":
        candidate_prefer = _safe_text(platform_profile.get("defaultPrefer"))
        if candidate_prefer in {"auto", "video", "images", "all"}:
            effective_prefer = candidate_prefer  # type: ignore[assignment]

    effective_referer = _safe_text(referer) or _safe_text(platform_profile.get("defaultReferer"))
    effective_cookies_from_browser = _safe_text(cookies_from_browser) or _safe_text(
        platform_profile.get("defaultCookiesFromBrowser")
    )
    if effective_cookies_from_browser and _safe_text(cookies_from_browser):
        profile_source = "explicit"
    elif effective_cookies_from_browser or effective_referer:
        profile_source = "platform_profile"
    else:
        profile_source = "none"

    share_resolution = _resolve_platform_share_page(
        resolved_url,
        platform=platform,
        prefer=effective_prefer,
    )
    if share_resolution and share_resolution.get("resolved"):
        effective_referer = _safe_text(share_resolution.get("referer")) or effective_referer
    download_target_url = _safe_text((share_resolution or {}).get("downloadUrl")) or resolved_url

    yt_dlp, import_error = _load_yt_dlp()
    if yt_dlp is None:
        return json.dumps(
            {
                "ok": False,
                "error": f"未安装 yt-dlp，无法下载平台媒体：{import_error}",
                "message": "当前环境缺少 yt-dlp，媒体下载不可用。",
            },
            ensure_ascii=False,
            indent=2,
        )

    runtime_context = get_runtime_context()
    try:
        workspace_root, download_root, workspace_status = _resolve_workspace_download_root(runtime_context)
    except ValueError as exc:
        resolved_workspace_path = workspace_resolution_service.resolve_workspace_path(
            runtime_kind=str(runtime_context.get("runtime_kind") or "chat"),
            session_id=str(runtime_context.get("session_id") or "").strip() or None,
            explicit_workspace_id=str(runtime_context.get("workspace_id") or "").strip() or None,
            explicit_workspace_path=str(runtime_context.get("workspace_path") or "").strip() or None,
            explicit_project_id=str(runtime_context.get("project_id") or "").strip() or None,
        )
        workspace_status = build_workspace_path_status(resolved_workspace_path)
        return json.dumps(
            {
                "ok": False,
                "blocked": True,
                "workspacePath": resolved_workspace_path,
                "recommendedWorkspacePath": workspace_status.get("recommendedPath"),
                "error": str(exc),
                "message": "当前工作区不适合媒体落盘，请改用推荐的 canonical workspace 后重试。",
            },
            ensure_ascii=False,
            indent=2,
        )

    download_dir = _build_download_dir(download_root, normalized_url)
    warnings: list[str] = []
    ffmpeg_available = shutil.which("ffmpeg") is not None
    if not ffmpeg_available:
        warnings.append("系统中未发现 ffmpeg；某些流媒体/合并下载可能会失败或无法转封装。")

    cookie_option = _parse_cookies_from_browser(effective_cookies_from_browser)
    http_headers: dict[str, str] = {}
    if effective_referer:
        http_headers["Referer"] = effective_referer

    ydl_opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "windowsfilenames": True,
        "restrictfilenames": True,
        "outtmpl": str(download_dir / "%(title).120B [%(id)s].%(ext)s"),
        "playlist_items": f"1:{max(1, min(max_items, 20))}",
        "merge_output_format": "mp4",
        "http_headers": http_headers or None,
    }
    if cookie_option:
        ydl_opts["cookiesfrombrowser"] = cookie_option

    info: dict[str, Any] | None = None
    direct_kind = _guess_kind_from_url(download_target_url)
    used_direct_download = False
    direct_headers = dict(http_headers)
    if direct_kind in {"video", "image", "audio"}:
        success, direct_error = _download_direct_media(
            download_target_url,
            download_dir=download_dir,
            kind=direct_kind,
            headers=direct_headers,
        )
        if success:
            used_direct_download = True
            info = {"id": "direct-media", "title": "resolved-media"}
        elif direct_error:
            warnings.append(direct_error)

    if not used_direct_download:
        ydl_error: Exception | None = None
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(download_target_url, download=True)
        except Exception as exc:
            ydl_error = exc
            if cookie_option and _should_retry_without_cookies(exc):
                retry_opts = dict(ydl_opts)
                retry_opts.pop("cookiesfrombrowser", None)
                warnings.append("浏览器 cookies 读取失败，已自动回退为无 cookies 下载重试。")
                try:
                    with yt_dlp.YoutubeDL(retry_opts) as ydl:
                        info = ydl.extract_info(download_target_url, download=True)
                        ydl_error = None
                        cookie_option = None
                        effective_cookies_from_browser = ""
                        profile_source = "fallback_no_cookie"
                except Exception as retry_exc:
                    ydl_error = retry_exc
            if ydl_error is not None:
                return json.dumps(
                    {
                        "ok": False,
                        "error": f"yt-dlp 下载失败：{ydl_error}",
                        "message": "媒体下载失败，未生成可展示产物。",
                    },
                    ensure_ascii=False,
                    indent=2,
                )

    media_files = _collect_media_files(download_dir)
    if not media_files:
        return json.dumps(
            {
                "ok": False,
                "error": "yt-dlp 已执行，但未发现可供视觉分析的本地媒体文件。",
                "message": "媒体下载未产出有效本地文件。",
            },
            ensure_ascii=False,
            indent=2,
        )

    file_entries: list[dict[str, Any]] = []
    for file_path in media_files:
        kind, mime_type = _guess_kind(file_path)
        try:
            workspace_relative_path = file_path.relative_to(workspace_root).as_posix()
        except ValueError:
            workspace_relative_path = file_path.name
        canonical_path = str(file_path)
        artifact = artifact_store.record_local_file(
            file_path=file_path,
            session_id=runtime_context.get("session_id"),
            run_id=runtime_context.get("run_id"),
            workspace_path=canonical_path,
            metadata={
                "source": "download_media_for_vision",
                "storageClass": "workspace",
                "surfaceVisible": True,
                "pathPlane": "workspace_download",
                "canonicalPath": canonical_path,
                "workspaceRoot": str(workspace_root),
                "workspaceRelativePath": workspace_relative_path,
                "projectId": str(runtime_context.get("project_id") or "").strip() or None,
                "workspaceId": str(runtime_context.get("workspace_id") or "").strip() or None,
                "sourceUrl": normalized_url,
                "resolvedUrl": resolved_url,
                "shortlinkResolution": shortlink_resolution,
                "canonicalResolution": canonical_resolution,
                "downloadTargetUrl": download_target_url,
                "platform": platform,
                "prefer": effective_prefer,
                "referer": effective_referer,
                "cookiesFromBrowser": bool(cookie_option),
                "profileSource": profile_source,
            },
            source_component="download_media_for_vision",
            node="download_media_for_vision",
        )
        file_entries.append(
            {
                "path": str(file_path),
                "fileName": file_path.name,
                "kind": kind,
                "mimeType": mime_type,
                "sizeBytes": file_path.stat().st_size,
                "artifactId": artifact.get("artifactId"),
                "workspacePath": canonical_path,
                "workspaceRelativePath": workspace_relative_path,
                "canonicalPath": canonical_path,
            }
        )

    primary = _select_primary_file(file_entries, effective_prefer)
    if primary is None:
        return json.dumps(
            {
                "ok": False,
                "error": "下载完成，但未能选择可供视觉分析的主文件。",
                "message": "媒体已落盘，但未能确定主文件。",
            },
            ensure_ascii=False,
            indent=2,
        )

    if effective_prefer == "video" and primary.get("kind") != "video":
        warnings.append("未找到视频文件，已回退使用第一个可用媒体文件。")
    if effective_prefer == "images" and primary.get("kind") != "image":
        warnings.append("未找到图片文件，已回退使用第一个可用媒体文件。")

    wants_analysis = bool(auto_chain_to_vision or _safe_text(analysis_prompt))
    message = "媒体已下载到当前工作区，可直接在聊天中展示。"
    if wants_analysis:
        message += " 如需继续理解内容，请显式调用 vision_media_analyzer。"
    if len(file_entries) > 1:
        message += f" 本次共下载 {len(file_entries)} 个文件，当前返回的是主文件。"

    result = {
        "ok": True,
        "artifactId": primary.get("artifactId"),
        "kind": primary["kind"],
        "mimeType": primary["mimeType"],
        "fileName": primary["fileName"],
        "workspacePath": primary["workspacePath"],
        "workspaceRelativePath": primary["workspaceRelativePath"],
        "message": message,
    }
    return json.dumps(result, ensure_ascii=False, indent=2)
