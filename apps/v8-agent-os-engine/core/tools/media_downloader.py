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
from urllib.parse import unquote, urlparse

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
_EXCLUDED_DOWNLOAD_SUFFIXES = {
    ".description",
    ".json",
    ".part",
    ".temp",
    ".txt",
    ".vtt",
    ".ytdl",
}
_URL_IN_TEXT_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)
_MEDIA_URL_RE = re.compile(
    r"https?://[^\s\"'<>]+?(?:\.mp4|\.mov|\.m4v|\.webm|\.m3u8|\.mpd|\.m4s|\.jpg|\.jpeg|\.png|\.webp)(?:[?#][^\s\"'<>]*)?",
    re.IGNORECASE,
)
_JIMENG_DIRECT_MEDIA_HINTS = ("mime_type=video_mp4", "/video/tos/", "dreamina", ".mp4")
_DOUYIN_DIRECT_MEDIA_HINTS = (
    "mime_type=video_mp4",
    "/video/tos/",
    "/tos-cn-ve-",
    ".ixigua.com/",
    ".douyinvod.com/",
    "douyin.com/aweme/v1/play",
    "aweme.snssdk.com/aweme/v1/play",
)
_DOUYIN_SHARE_API_HINTS = (
    "aweme/v1/web/aweme/detail",
    "aweme/v1/web/multi/aweme/detail",
    "aweme/v1/play",
)
_DOUYIN_LOW_VALUE_MEDIA_HINTS = (
    "avatar",
    "cover",
    "music",
    "poster",
    "douyinpic",
    "imagex",
)
_DOUBAO_SHARE_API_HINT = "get_video_share_info"
_SHORTLINK_HOSTS = {"xhslink.com", "b23.tv", "v.douyin.com"}
_DEFAULT_PLATFORM_PROFILES: dict[str, dict[str, Any]] = {
    "x": {
        "defaultPrefer": "video",
        "defaultReferer": "https://x.com/",
        "defaultCookiesFromBrowser": "",
        "cookieOptional": True,
        "retryOrder": ["no_cookie", "cookie_if_needed"],
    },
    "xiaohongshu": {
        "defaultPrefer": "all",
        "defaultReferer": "https://www.xiaohongshu.com/",
        "defaultCookiesFromBrowser": "chrome",
        "cookieOptional": False,
        "retryOrder": ["cookie_first", "referer_required"],
    },
    "douyin": {
        "defaultPrefer": "video",
        "defaultReferer": "https://www.douyin.com/",
        "defaultCookiesFromBrowser": "chrome",
        "cookieOptional": False,
        "retryOrder": ["cookie_first", "referer_required"],
    },
    "doubao": {
        "defaultPrefer": "video",
        "defaultReferer": "https://www.doubao.com/",
        "defaultCookiesFromBrowser": "",
        "cookieOptional": True,
        "retryOrder": ["no_cookie", "cookie_if_needed"],
    },
    "tiktok": {
        "defaultPrefer": "video",
        "defaultReferer": "https://www.tiktok.com/",
        "defaultCookiesFromBrowser": "chrome",
        "cookieOptional": True,
        "retryOrder": ["no_cookie", "cookie_if_needed"],
    },
    "bilibili": {
        "defaultPrefer": "video",
        "defaultReferer": "https://www.bilibili.com/",
        "defaultCookiesFromBrowser": "",
        "cookieOptional": True,
        "retryOrder": ["no_cookie", "cookie_if_needed"],
    },
    "instagram": {
        "defaultPrefer": "all",
        "defaultReferer": "https://www.instagram.com/",
        "defaultCookiesFromBrowser": "chrome",
        "cookieOptional": True,
        "retryOrder": ["no_cookie", "cookie_if_needed"],
    },
    "youtube": {
        "defaultPrefer": "video",
        "defaultReferer": "https://www.youtube.com/",
        "defaultCookiesFromBrowser": "",
        "cookieOptional": True,
        "retryOrder": ["no_cookie", "cookie_if_needed"],
    },
    "jimeng": {
        "defaultPrefer": "video",
        "defaultReferer": "https://jimeng.jianying.com/",
        "defaultCookiesFromBrowser": "",
        "cookieOptional": True,
        "retryOrder": ["no_cookie", "cookie_if_needed"],
    },
}


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


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

    merged: dict[str, Any] = {}
    keys = set(_DEFAULT_PLATFORM_PROFILES) | {str(key).strip() for key in configured.keys()}
    for key in keys:
        normalized = str(key or "").strip()
        if not normalized:
            continue
        merged[normalized] = {
            **(_DEFAULT_PLATFORM_PROFILES.get(normalized) or {}),
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
    if any(ext in normalized for ext in (".mp4", ".mov", ".m4v", ".webm", ".m3u8", ".mpd", ".m4s", "mime_type=video_mp4")):
        return "video"
    if any(hint in normalized for hint in _DOUYIN_DIRECT_MEDIA_HINTS):
        return "video"
    if any(ext in normalized for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif", "poster_url", "cover")):
        return "image"
    if any(ext in normalized for ext in (".mp3", ".wav", ".m4a", ".aac")):
        return "audio"
    return "file"


def _looks_like_direct_media(url: str) -> bool:
    normalized = url.lower()
    return _guess_kind_from_url(url) in {"video", "image", "audio"} or any(
        token in normalized
        for token in ("mime_type=video_mp4", "mime_type=image", "mime=image", "content-type=video", "content-type=image")
    )


def _looks_like_douyin_direct_media(url: str) -> bool:
    normalized = _safe_text(url).lower()
    return any(hint in normalized for hint in _DOUYIN_DIRECT_MEDIA_HINTS)


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
        if source == "doubao_share_api":
            kind_score += 25
        if source == "jimeng_network_capture":
            kind_score += 20
        if source.startswith("douyin_") or _looks_like_douyin_direct_media(_safe_text(item.get("url"))):
            url = _safe_text(item.get("url")).lower()
            if _looks_like_douyin_direct_media(url):
                kind_score += 40
            if "mime_type=video_mp4" in url:
                kind_score += 25
            if "/video/tos/" in url or "/tos-cn-ve-" in url:
                kind_score += 15
            if source in {"douyin_share_api", "douyin_share_api_text"}:
                kind_score += 20
            if kind == "image" and any(hint in url for hint in _DOUYIN_LOW_VALUE_MEDIA_HINTS):
                kind_score -= 35
            if kind == "audio" and "music" in url:
                kind_score -= 20
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


def _resolve_platform_share_page(
    url: str,
    *,
    platform: str,
    prefer: DownloadMediaPreference,
) -> dict[str, Any] | None:
    if platform not in {"jimeng", "doubao", "xiaohongshu", "douyin", "bilibili"}:
        return None

    sync_playwright, browser_engine, import_error = _load_browser_sync()
    if sync_playwright is None:
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
        if platform == "jimeng" and not any(hint in normalized.lower() for hint in _JIMENG_DIRECT_MEDIA_HINTS):
            if _guess_kind_from_url(normalized) != "image":
                return
        if platform == "douyin":
            guessed_kind = kind or _guess_kind_from_url(normalized)
            if guessed_kind == "video" and not _looks_like_douyin_direct_media(normalized):
                return
        if platform == "bilibili" and ".m4s" in normalized.lower():
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
                    if platform == "doubao" and _DOUBAO_SHARE_API_HINT in lowered:
                        try:
                            payload = response.json()
                        except Exception:
                            payload = None
                        if payload is not None:
                            for hit in _extract_media_urls_from_json_like(payload, source="doubao_share_api"):
                                _register_candidate(hit["url"], source=hit["source"], kind=hit["kind"])
                        else:
                            with contextlib.suppress(Exception):
                                body_text = response.text()
                                for hit in _extract_media_urls_from_json_like(body_text, source="doubao_share_api_text"):
                                    _register_candidate(hit["url"], source=hit["source"], kind=hit["kind"])
                    if platform == "douyin" and any(hint in lowered for hint in _DOUYIN_SHARE_API_HINTS):
                        try:
                            payload = response.json()
                        except Exception:
                            payload = None
                        if payload is not None:
                            for hit in _extract_media_urls_from_json_like(payload, source="douyin_share_api"):
                                _register_candidate(hit["url"], source=hit["source"], kind=hit["kind"])
                        else:
                            with contextlib.suppress(Exception):
                                body_text = response.text()
                                for hit in _extract_media_urls_from_json_like(body_text, source="douyin_share_api_text"):
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
    if host not in _SHORTLINK_HOSTS:
        return url, {"resolved": False, "host": host, "strategy": "not_shortlink"}

    requests, import_error = _load_requests()
    if requests is None:
        return url, {
            "resolved": False,
            "host": host,
            "strategy": "requests_unavailable",
            "error": f"无法解析短链：{import_error}",
        }

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
        )
    }
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
        response = requests.get(url, headers=headers or {}, stream=True, timeout=45)
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
    if "xiaohongshu" in host or "xhslink" in host:
        return "xiaohongshu"
    if "doubao.com" in host:
        return "doubao"
    if "douyin" in host:
        return "douyin"
    if "tiktok" in host:
        return "tiktok"
    if "instagram" in host:
        return "instagram"
    if "jimeng.jianying.com" in host or "dreamina" in host:
        return "jimeng"
    if "youtube" in host or "youtu.be" in host:
        return "youtube"
    if "bilibili" in host or "b23.tv" in host:
        return "bilibili"
    if host in {"x.com", "twitter.com"}:
        return "x"
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
