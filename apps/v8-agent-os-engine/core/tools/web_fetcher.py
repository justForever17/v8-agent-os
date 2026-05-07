from __future__ import annotations

import json
import mimetypes
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import ssl
import tempfile
import time
from typing import Annotated, Any, Dict, List, Literal
from urllib.parse import quote_plus, urljoin, urlparse

from bs4 import BeautifulSoup
import certifi
from langchain_core.tools import InjectedToolCallId, tool
import requests
from scrapling.core.storage import SQLiteStorageSystem
from scrapling.parser import Selector

from core.agent_browser_profile import (
    agent_browser_profile_allowed_for_url,
    agent_browser_profile_summary,
    configured_agent_browser_profile_dir,
)
from core.system_base import get_web_fetch_config
from core.storage import storage
from erc.runtime_context import get_runtime_context
from erc.safety_guardian import safety_guardian


WebFetchMode = Literal["auto", "static", "dynamic", "stealth"]
WebExtractMode = Literal["article", "links", "metadata", "media"]
WebRefererMode = Literal["none", "google", "custom"]
WebFetchIntent = Literal["auto", "read", "extract", "search"]
WebSearchEngine = Literal["auto", "metaso", "bing", "google", "baidu", "duckduckgo"]
WebSearchVertical = Literal["all", "web", "document", "academic", "image", "video", "podcast"]
WEB_CONTAINER_SELECTOR = "main, article, [role='main'], body"
MAX_SELECTOR_CANDIDATES = 12
DEFAULT_CONTAINER_SELECTORS = (
    "main",
    "article",
    "[role='main']",
    "#main",
    "#content",
    "#main-content",
    ".main",
    ".content",
    ".main-content",
    ".article-content",
    ".post-content",
    ".entry-content",
    "body",
)
EXTRACT_CONTAINER_SELECTORS: dict[str, tuple[str, ...]] = {
    "article": (
        "article",
        "main article",
        "[itemprop='articleBody']",
        ".article-content",
        ".post-content",
        ".entry-content",
    ),
    "links": (
        "main",
        "nav",
        "article",
        ".content",
    ),
    "metadata": (),
    "media": (
        "main",
        "article",
        ".gallery",
        ".content",
        "body",
    ),
}

MAX_TEXT_CHARS = 12000
MAX_LINKS = 20
MAX_MEDIA = 12
WEB_READ_TIMEOUT_SECONDS = 45.0
WEB_SEARCH_PROVIDER_TIMEOUT_SECONDS = 20.0
WEB_SEARCH_TOTAL_TIMEOUT_SECONDS = 45.0
METASO_HOME_URL = "https://metaso.cn/"
METASO_SEARCH_ENDPOINT = "https://metaso.cn/api/searchV2"
METASO_VERTICAL_ENGINE_TYPES: dict[str, str] = {
    "all": "",
    "web": "",
    "document": "pdf",
    "academic": "scholar",
    "image": "image",
    "video": "video",
    "podcast": "podcast",
}
SEARCH_PROVIDER_URLS: dict[str, str] = {
    "metaso": "https://metaso.cn/?q={query}",
    "bing": "https://www.bing.com/search?q={query}",
    "google": "https://www.google.com/search?q={query}&hl=en",
    "baidu": "https://www.baidu.com/s?wd={query}",
    "duckduckgo": "https://html.duckduckgo.com/html/?q={query}",
}
# Prefer MetaSo and scrape-friendly lightweight HTML endpoints first. Bing is
# frequently unavailable behind some VPN/proxy routes; Google/Baidu may return
# challenge pages. All providers remain available explicitly or via config override.
SEARCH_PROVIDER_ORDER = ("metaso", "duckduckgo", "baidu", "bing", "google")
WINDOWS_CA_BUNDLE_NAME = "windows-system-ca.pem"
WINDOWS_CA_BUNDLE_MAX_AGE_SECONDS = 24 * 60 * 60
PROXY_ENV_KEYS = (
    "ALL_PROXY",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "all_proxy",
    "http_proxy",
    "https_proxy",
    "no_proxy",
)


@dataclass(slots=True)
class WebPagePayload:
    url: str
    final_url: str
    requested_mode: str
    referer_mode: str
    referer_url: str
    fetch_mode: str
    attempted_modes: List[str]
    available_modes: Dict[str, Dict[str, Any]]
    status: int | None
    tls_strategy: str
    ca_bundle_path: str
    proxy_bypass_used: bool
    title: str
    text: str
    html: str
    metadata: Dict[str, Any]
    links: List[Dict[str, str]]
    media: List[Dict[str, str]]
    warnings: List[str]
    agent_browser_profile_used: bool = False
    agent_browser_profile_host: str = ""
    agent_browser_profile_dir: str = ""


def _enforce_safety_decision(decision, *, tool_call_id: str, question: str) -> tuple[bool, str | None]:
    safety_guardian.log_decision_event(
        action="web_fetch_safety",
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
        return False, f"Safety Guardian 已阻止网页操作：{decision.reason}"

    if not approved:
        return False, f"Safety Guardian 未获得批准，网页操作已取消：{decision.reason}"

    return True, None


def _guard_url(url: str, *, tool_call_id: str) -> tuple[bool, str | None]:
    runtime_context = get_runtime_context()
    decision = safety_guardian.assess_http_request("GET", url, body=None, runtime_context=runtime_context)
    return _enforce_safety_decision(
        decision,
        tool_call_id=tool_call_id,
        question=f"Safety Guardian 检测到网页读取需要确认，是否继续？\n\nGET {url}",
    )


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _classify_web_fetch_failure(error: str, *, blocked: bool = False) -> str:
    if blocked:
        return "blocked_by_safety"
    lowered = _safe_text(error).lower()
    timeout_needles = (
        "timeout",
        "timed_out",
        "deadline_exceeded",
        "err_connection_timed_out",
        "net::err_connection_timed_out",
    )
    if any(needle in lowered for needle in timeout_needles):
        return "network_timeout"
    if "invalid argument" in lowered or "expected `int`" in lowered:
        return "tool_configuration_error"
    if "no active session available" in lowered:
        return "tool_context_unavailable"
    if "agent_browser_profile_not_allowed" in lowered:
        return "agent_browser_profile_not_allowed"
    if "needs_login" in lowered or "login_required" in lowered or "auth_required" in lowered:
        return "needs_login"
    return "web_fetch_failed"


def _search_provider_order(requested_provider: str) -> list[str]:
    if requested_provider != "auto":
        return [requested_provider] if requested_provider in SEARCH_PROVIDER_URLS else []

    config = get_web_fetch_config()
    configured = config.get("searchProviderOrder")
    if isinstance(configured, list):
        providers = [_safe_text(item).lower() for item in configured]
    else:
        providers = list(SEARCH_PROVIDER_ORDER)

    ordered: list[str] = []
    for provider in providers:
        if provider in SEARCH_PROVIDER_URLS and provider not in ordered:
            ordered.append(provider)
    for provider in SEARCH_PROVIDER_ORDER:
        if provider not in ordered:
            ordered.append(provider)
    return ordered


def _is_loopback_sink_proxy(value: str) -> bool:
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if host not in {"127.0.0.1", "localhost", "::1"}:
        return False
    return parsed.port in {0, 9}


def _should_bypass_proxy_env() -> bool:
    config = get_web_fetch_config()
    if bool(config.get("bypassProxyEnv")):
        return True

    values = [_safe_text(os.getenv(key)) for key in PROXY_ENV_KEYS]
    active = [value for value in values if value]
    return bool(active) and all(_is_loopback_sink_proxy(value) for value in active if "://" in value)


def _agent_browser_profile_allowed(url: str) -> tuple[bool, str | None]:
    config = get_web_fetch_config()
    if not bool(config.get("useAgentBrowserProfile")):
        return False, None
    return agent_browser_profile_allowed_for_url(url, config.get("agentBrowserProfileAllowlist") or [])


@contextmanager
def _bypass_proxy_env(enabled: bool):
    if not enabled:
        yield False
        return

    snapshot = {key: os.environ.pop(key, None) for key in PROXY_ENV_KEYS}
    try:
        yield True
    finally:
        for key, value in snapshot.items():
            if value is not None:
                os.environ[key] = value


def _web_fetch_cache_dir() -> Path:
    web_fetch_config = get_web_fetch_config()
    override = _safe_text(web_fetch_config.get("cacheDir"))
    candidates = [Path(override)] if override else []
    temp_dir_candidate = ""
    try:
        temp_dir_candidate = tempfile.gettempdir()
    except Exception:
        temp_dir_candidate = ""
    candidates.extend(
        [
            storage.base_dir / "web_fetch",
            Path(os.getenv("LOCALAPPDATA", "")) / "v8chat" / "web_fetch" if _safe_text(os.getenv("LOCALAPPDATA")) else Path(),
            Path(temp_dir_candidate) / "v8chat-web-fetch" if temp_dir_candidate else Path(),
        ]
    )
    last_error: Exception | None = None
    for candidate in candidates:
        if not str(candidate):
            continue
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            test_file = candidate / ".write-test"
            test_file.write_text("ok", encoding="utf-8")
            test_file.unlink(missing_ok=True)
            return candidate
        except Exception as exc:
            last_error = exc
            continue
    raise RuntimeError(f"无法创建网页抓取缓存目录：{last_error}")


def _export_windows_ca_bundle() -> str | None:
    if os.name != "nt" or not hasattr(ssl, "enum_certificates"):
        return None

    bundle_path = _web_fetch_cache_dir() / WINDOWS_CA_BUNDLE_NAME
    if bundle_path.exists():
        age_seconds = max(0.0, time.time() - bundle_path.stat().st_mtime)
        if age_seconds <= WINDOWS_CA_BUNDLE_MAX_AGE_SECONDS and bundle_path.stat().st_size > 0:
            return str(bundle_path)

    pem_chunks: list[str] = []
    seen: set[bytes] = set()
    for store_name in ("ROOT", "CA"):
        try:
            certificates = ssl.enum_certificates(store_name)
        except Exception:
            continue
        for cert_bytes, encoding, _trust in certificates:
            if encoding != "x509_asn" or cert_bytes in seen:
                continue
            seen.add(cert_bytes)
            pem_chunks.append(ssl.DER_cert_to_PEM_cert(cert_bytes))

    if not pem_chunks:
        return None

    bundle_path.write_text("".join(pem_chunks), encoding="ascii")
    return str(bundle_path)


def _resolve_verify_candidates() -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    seen: set[str] = set()

    for env_name in ("CURL_CA_BUNDLE", "REQUESTS_CA_BUNDLE", "SSL_CERT_FILE"):
        value = _safe_text(os.getenv(env_name))
        if value and os.path.exists(value) and value not in seen:
            candidates.append((f"env:{env_name}", value))
            seen.add(value)

    windows_bundle = _export_windows_ca_bundle()
    if windows_bundle and windows_bundle not in seen:
        candidates.append(("windows_root_store", windows_bundle))
        seen.add(windows_bundle)

    certifi_bundle = certifi.where()
    if certifi_bundle not in seen:
        candidates.append(("certifi", certifi_bundle))

    return candidates


def _dependency_status() -> dict[str, dict[str, Any]]:
    static_fetcher, static_error = _try_import_static_fetcher()
    dynamic_fetcher, dynamic_error = _try_import_dynamic_fetcher()
    stealth_fetcher, stealth_error = _try_import_stealth_fetcher()
    return {
        "static": {
            "available": static_fetcher is not None,
            "driver": "Fetcher",
            "error": static_error,
        },
        "dynamic": {
            "available": dynamic_fetcher is not None,
            "driver": "DynamicFetcher",
            "error": dynamic_error,
        },
        "stealth": {
            "available": stealth_fetcher is not None,
            "driver": "StealthyFetcher",
            "error": stealth_error,
        },
    }


def _try_import_static_fetcher():
    try:
        from scrapling.fetchers import Fetcher

        return Fetcher, None
    except Exception as exc:  # pragma: no cover - exercised by runtime environment
        return None, str(exc)


def _try_import_dynamic_fetcher():
    try:
        from scrapling.fetchers import DynamicFetcher

        return DynamicFetcher, None
    except Exception as exc:  # pragma: no cover
        return None, str(exc)


def _try_import_stealth_fetcher():
    try:
        from scrapling.fetchers import StealthyFetcher

        return StealthyFetcher, None
    except Exception as exc:  # pragma: no cover
        return None, str(exc)


def _fetch_with_scrapling(url: str, *, mode: WebFetchMode = "auto", headless: bool = True) -> WebPagePayload:
    return _fetch_with_scrapling_internal(
        url,
        mode=mode,
        headless=headless,
        referer_mode="none",
        referer_url="",
    )


def _build_fetch_options(
    *,
    headless: bool,
    referer_mode: WebRefererMode,
    referer_url: str,
    timeout_seconds: float,
    agent_browser_profile_dir: str = "",
) -> tuple[dict[str, Any], dict[str, Any]]:
    extra_headers: dict[str, str] = {}
    static_headers: dict[str, str] = {}
    if referer_mode == "none":
        static_headers["referer"] = ""
    elif referer_mode == "custom" and referer_url:
        static_headers["referer"] = referer_url
    if referer_mode == "custom" and referer_url:
        extra_headers["referer"] = referer_url
    shared = {
        "google_search": referer_mode == "google",
        "extra_headers": extra_headers or None,
        # Scrapling/Playwright validates this as an integer >= 1. Use one
        # attempt at the fetcher layer and keep higher-level retry decisions in
        # V8's tool envelope so the agent sees honest failures quickly.
        "retries": 1,
        "retry_delay": 0,
    }
    browser = {
        **shared,
        "headless": headless,
        "timeout": max(1000, int(timeout_seconds * 1000)),
    }
    if agent_browser_profile_dir:
        browser["user_data_dir"] = agent_browser_profile_dir
    static = {
        **shared,
        "headers": static_headers or None,
        "stealthy_headers": referer_mode == "google",
        "timeout": max(1.0, float(timeout_seconds)),
    }
    return static, browser


def _fetch_with_scrapling_internal(
    url: str,
    *,
    mode: WebFetchMode = "auto",
    headless: bool = True,
    referer_mode: WebRefererMode = "none",
    referer_url: str = "",
    timeout_seconds: float | None = None,
    use_agent_browser_profile: bool = False,
) -> WebPagePayload:
    attempted_modes: list[str] = []
    errors: dict[str, str] = {}
    warnings: list[str] = []
    available_modes = _dependency_status()
    started_at = time.monotonic()

    def _fetch_static() -> WebPagePayload:
        fetcher, error = _try_import_static_fetcher()
        if fetcher is None:
            raise RuntimeError(error or "静态 Fetcher 不可用。")
        bypass_proxy_env = _should_bypass_proxy_env()
        verify_errors: list[str] = []
        verify_candidates = _resolve_verify_candidates()

        with _bypass_proxy_env(bypass_proxy_env):
            for verify_label, verify_target in verify_candidates:
                try:
                    response = fetcher.get(url, verify=verify_target, **static_fetch_options)
                    return _build_payload(
                        response=response,
                        requested_url=url,
                        requested_mode=mode,
                        referer_mode=referer_mode,
                        referer_url=referer_url,
                        fetch_mode="static",
                        attempted_modes=list(attempted_modes),
                        available_modes=available_modes,
                        tls_strategy=verify_label,
                        ca_bundle_path=verify_target,
                        proxy_bypass_used=bypass_proxy_env,
                        warnings=list(warnings),
                    )
                except Exception as exc:
                    verify_errors.append(f"{verify_label}={exc}")
                    errors[f"static_tls:{verify_label}"] = str(exc)

            warnings.append(
                "静态抓取在当前环境无法通过证书链校验，已降级为 verify=False。"
            )
            if verify_errors:
                warnings.append(
                    "静态抓取证书链探测失败：" + " | ".join(verify_errors[:3])
                )
            response = fetcher.get(url, verify=False, **static_fetch_options)
            return _build_payload(
                response=response,
                requested_url=url,
                requested_mode=mode,
                referer_mode=referer_mode,
                referer_url=referer_url,
                fetch_mode="static",
                attempted_modes=list(attempted_modes),
                available_modes=available_modes,
                tls_strategy="verify_false_fallback",
                ca_bundle_path="",
                proxy_bypass_used=bypass_proxy_env,
                warnings=list(warnings),
            )

    def _fetch_dynamic() -> WebPagePayload:
        fetcher, error = _try_import_dynamic_fetcher()
        if fetcher is None:
            raise RuntimeError(error or "动态 Fetcher 不可用。")
        response = fetcher.fetch(url, **browser_fetch_options)
        return _build_payload(
            response=response,
            requested_url=url,
            requested_mode=mode,
            referer_mode=referer_mode,
            referer_url=referer_url,
            fetch_mode="dynamic",
            attempted_modes=list(attempted_modes),
            available_modes=available_modes,
            tls_strategy="browser_managed",
            ca_bundle_path="",
            proxy_bypass_used=False,
            warnings=list(warnings),
            agent_browser_profile_used=bool(agent_browser_profile_dir),
            agent_browser_profile_host=agent_browser_profile_host,
            agent_browser_profile_dir=agent_browser_profile_dir,
        )

    def _fetch_stealth() -> WebPagePayload:
        fetcher, error = _try_import_stealth_fetcher()
        if fetcher is None:
            raise RuntimeError(error or "Stealth Fetcher 不可用。")
        response = fetcher.fetch(url, **browser_fetch_options)
        return _build_payload(
            response=response,
            requested_url=url,
            requested_mode=mode,
            referer_mode=referer_mode,
            referer_url=referer_url,
            fetch_mode="stealth",
            attempted_modes=list(attempted_modes),
            available_modes=available_modes,
            tls_strategy="browser_managed",
            ca_bundle_path="",
            proxy_bypass_used=False,
            warnings=list(warnings),
            agent_browser_profile_used=bool(agent_browser_profile_dir),
            agent_browser_profile_host=agent_browser_profile_host,
            agent_browser_profile_dir=agent_browser_profile_dir,
        )

    plans: list[tuple[str, Any]]
    if mode == "static":
        plans = [("static", _fetch_static)]
    elif mode == "dynamic":
        plans = [("dynamic", _fetch_dynamic)]
    elif mode == "stealth":
        plans = [("stealth", _fetch_stealth)]
    else:
        plans = [
            ("static", _fetch_static),
            ("dynamic", _fetch_dynamic),
            ("stealth", _fetch_stealth),
        ]
    if use_agent_browser_profile:
        plans = [(label, runner) for label, runner in plans if label in {"dynamic", "stealth"}]
        if not plans:
            raise RuntimeError("agent_browser_profile_requires_browser_mode: use dynamic/stealth/auto when useAgentBrowserProfile=true.")
    total_timeout = float(timeout_seconds or WEB_READ_TIMEOUT_SECONDS)
    per_mode_timeout = max(5.0, total_timeout / max(len(plans), 1))
    agent_browser_profile_dir = ""
    agent_browser_profile_host = ""
    if use_agent_browser_profile:
        allowed, matched_host = _agent_browser_profile_allowed(url)
        if not allowed:
            raise RuntimeError(
                "agent_browser_profile_not_allowed:"
                " useAgentBrowserProfile=true requires systemBase.webFetch.useAgentBrowserProfile=true"
                " and a matching agentBrowserProfileAllowlist domain."
            )
        agent_browser_profile_dir = str(configured_agent_browser_profile_dir("chrome"))
        agent_browser_profile_host = matched_host or ""
    static_fetch_options, browser_fetch_options = _build_fetch_options(
        headless=headless,
        referer_mode=referer_mode,
        referer_url=referer_url,
        timeout_seconds=per_mode_timeout,
        agent_browser_profile_dir=agent_browser_profile_dir,
    )

    for label, runner in plans:
        if time.monotonic() - started_at >= total_timeout:
            errors[label] = f"deadline_exceeded_after_{round(time.monotonic() - started_at, 2)}s"
            break
        attempted_modes.append(label)
        try:
            return runner()
        except Exception as exc:
            errors[label] = str(exc)

    elapsed = round(time.monotonic() - started_at, 2)
    details = "; ".join(f"{key}={value}" for key, value in errors.items())
    raise RuntimeError(f"网页抓取失败。attempted={attempted_modes}; elapsed={elapsed}s; deadline={total_timeout}s; errors={details}")


def _build_payload(
    *,
    response: Any,
    requested_url: str,
    requested_mode: str,
    referer_mode: str,
    referer_url: str,
    fetch_mode: str,
    attempted_modes: list[str],
    available_modes: dict[str, dict[str, Any]],
    tls_strategy: str,
    ca_bundle_path: str,
    proxy_bypass_used: bool,
    warnings: list[str],
    agent_browser_profile_used: bool = False,
    agent_browser_profile_host: str = "",
    agent_browser_profile_dir: str = "",
) -> WebPagePayload:
    html = _safe_text(getattr(response, "html_content", "")) or _safe_text(getattr(response, "text", ""))
    final_url = _safe_text(getattr(response, "url", "")) or requested_url
    status = getattr(response, "status", None)
    soup = BeautifulSoup(html, "html.parser")

    title = _safe_text(soup.title.string if soup.title and soup.title.string else "")
    if not title and hasattr(response, "css"):
        try:
            title = _safe_text(response.css("title::text").get())
        except Exception:
            title = ""

    text = _extract_main_text(soup)
    links = _extract_links(soup, final_url)
    metadata = _extract_metadata(soup)
    media = _extract_media(soup, final_url)
    return WebPagePayload(
        url=requested_url,
        final_url=final_url,
        requested_mode=requested_mode,
        referer_mode=referer_mode,
        referer_url=referer_url,
        fetch_mode=fetch_mode,
        attempted_modes=attempted_modes,
        available_modes=available_modes,
        status=status,
        tls_strategy=tls_strategy,
        ca_bundle_path=ca_bundle_path,
        proxy_bypass_used=proxy_bypass_used,
        title=title,
        text=text,
        html=html,
        metadata=metadata,
        links=links,
        media=media,
        warnings=warnings,
        agent_browser_profile_used=agent_browser_profile_used,
        agent_browser_profile_host=agent_browser_profile_host,
        agent_browser_profile_dir=agent_browser_profile_dir,
    )


def _extract_main_text(soup: BeautifulSoup) -> str:
    candidate = soup.find("main") or soup.find("article") or soup.body or soup
    for tag in candidate(["script", "style", "noscript", "svg", "canvas"]):
        tag.decompose()
    text = "\n".join(line.strip() for line in candidate.get_text("\n").splitlines() if line.strip())
    if len(text) > MAX_TEXT_CHARS:
        return text[:MAX_TEXT_CHARS] + f"\n\n...[TRUNCATED] ({len(text)} chars total)"
    return text


def _extract_links(soup: BeautifulSoup, base_url: str) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    seen: set[str] = set()
    for anchor in soup.select("a[href]"):
        href = _safe_text(anchor.get("href"))
        if not href:
            continue
        absolute = urljoin(base_url, href)
        if absolute in seen:
            continue
        seen.add(absolute)
        label = _safe_text(anchor.get_text(" ", strip=True)) or absolute
        links.append({"text": label[:200], "url": absolute})
        if len(links) >= MAX_LINKS:
            break
    return links


def _extract_metadata(soup: BeautifulSoup) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for meta in soup.select("meta"):
        key = _safe_text(meta.get("name") or meta.get("property") or meta.get("http-equiv"))
        value = _safe_text(meta.get("content"))
        if key and value and key not in metadata:
            metadata[key] = value[:500]
    return metadata


def _extract_media(soup: BeautifulSoup, base_url: str) -> list[dict[str, str]]:
    media: list[dict[str, str]] = []
    seen: set[str] = set()
    selectors = [
        ("img[src]", "image", "src"),
        ("source[src]", "source", "src"),
        ("video[src]", "video", "src"),
        ("audio[src]", "audio", "src"),
    ]
    for selector, media_type, attr in selectors:
        for node in soup.select(selector):
            raw = _safe_text(node.get(attr))
            if not raw:
                continue
            absolute = urljoin(base_url, raw)
            if absolute in seen:
                continue
            seen.add(absolute)
            label = _safe_text(node.get("alt") or node.get("title") or node.get_text(" ", strip=True)) or absolute
            media.append({"type": media_type, "label": label[:200], "url": absolute})
            if len(media) >= MAX_MEDIA:
                return media
    return media


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _site_profile_key(url: str) -> str:
    parsed = urlparse(url)
    return (parsed.netloc or "unknown").lower()


def _site_path_key(url: str) -> str:
    parsed = urlparse(url)
    path = (parsed.path or "/").strip("/") or "root"
    return path.replace("/", ":")


def _selector_identifier_token(selector: str) -> str:
    normalized = selector.lower()
    return "".join(ch if ch.isalnum() else "_" for ch in normalized).strip("_") or "container"


def _load_web_fetch_profiles() -> dict[str, Any]:
    if hasattr(storage, "get_web_fetch_profiles"):
        return storage.get_web_fetch_profiles()
    data = storage.read_json("web_fetch_profiles.json")
    if not data:
        data = {"version": 1, "sites": {}}
    data.setdefault("version", 1)
    data.setdefault("sites", {})
    return data


def _save_web_fetch_profiles(data: dict[str, Any]) -> None:
    if hasattr(storage, "save_web_fetch_profiles"):
        storage.save_web_fetch_profiles(data)
        return
    payload = dict(data or {})
    payload.setdefault("version", 1)
    payload.setdefault("sites", {})
    storage.write_json("web_fetch_profiles.json", payload)


def _selector_score(entry: dict[str, Any]) -> int:
    direct_hits = int(entry.get("directHits") or 0)
    adaptive_hits = int(entry.get("adaptiveHits") or 0)
    misses = int(entry.get("misses") or 0)
    return direct_hits * 4 + adaptive_hits * 3 - misses


def _site_selector_candidates(url: str, extract: WebExtractMode) -> tuple[str, list[str], dict[str, dict[str, Any]]]:
    profile_key = _site_profile_key(url)
    profiles = _load_web_fetch_profiles()
    site_profile = ((profiles.get("sites") or {}).get(profile_key) or {})
    extract_profile = ((site_profile.get("extracts") or {}).get(extract) or {})
    selector_entries = dict(extract_profile.get("selectors") or {})
    ordered_profile_selectors = [
        selector
        for selector, _entry in sorted(
            selector_entries.items(),
            key=lambda item: (_selector_score(item[1]), int(item[1].get("successes") or 0), item[0]),
            reverse=True,
        )
    ]
    defaults = [*EXTRACT_CONTAINER_SELECTORS.get(extract, ()), *DEFAULT_CONTAINER_SELECTORS]
    candidates: list[str] = []
    seen: set[str] = set()
    for selector in [*ordered_profile_selectors, *defaults]:
        normalized = _safe_text(selector)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        candidates.append(normalized)
        if len(candidates) >= MAX_SELECTOR_CANDIDATES:
            break
    return profile_key, ordered_profile_selectors, selector_entries


def _selector_candidates_for_extract(url: str, extract: WebExtractMode) -> tuple[str, list[str], list[str], dict[str, dict[str, Any]]]:
    profile_key, profile_selectors, selector_entries = _site_selector_candidates(url, extract)
    defaults = [*EXTRACT_CONTAINER_SELECTORS.get(extract, ()), *DEFAULT_CONTAINER_SELECTORS]
    candidates: list[str] = []
    seen: set[str] = set()
    for selector in [*profile_selectors, *defaults]:
        normalized = _safe_text(selector)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        candidates.append(normalized)
        if len(candidates) >= MAX_SELECTOR_CANDIDATES:
            break
    if "body" not in seen:
        if len(candidates) >= MAX_SELECTOR_CANDIDATES:
            candidates[-1] = "body"
        else:
            candidates.append("body")
    return profile_key, candidates, profile_selectors, selector_entries


def _record_selector_signal(
    *,
    url: str,
    extract: WebExtractMode,
    selector: str,
    source: str,
    direct_hit: bool,
    adaptive_hit: bool,
    success: bool,
    selected_tag: str,
) -> dict[str, Any]:
    profiles = _load_web_fetch_profiles()
    sites = profiles.setdefault("sites", {})
    profile_key = _site_profile_key(url)
    site_profile = sites.setdefault(profile_key, {"updatedAt": "", "extracts": {}})
    extracts = site_profile.setdefault("extracts", {})
    extract_profile = extracts.setdefault(extract, {"updatedAt": "", "selectors": {}})
    selectors = extract_profile.setdefault("selectors", {})
    entry = selectors.setdefault(
        selector,
        {
            "selector": selector,
            "firstSeenAt": _utc_now_iso(),
            "lastUsedAt": "",
            "directHits": 0,
            "adaptiveHits": 0,
            "successes": 0,
            "misses": 0,
            "lastSource": "",
            "lastSelectedTag": "",
        },
    )
    if success:
        entry["successes"] = int(entry.get("successes") or 0) + 1
    else:
        entry["misses"] = int(entry.get("misses") or 0) + 1
    if direct_hit:
        entry["directHits"] = int(entry.get("directHits") or 0) + 1
    if adaptive_hit:
        entry["adaptiveHits"] = int(entry.get("adaptiveHits") or 0) + 1
    entry["lastUsedAt"] = _utc_now_iso()
    entry["lastSource"] = source
    entry["lastSelectedTag"] = selected_tag
    site_profile["updatedAt"] = entry["lastUsedAt"]
    extract_profile["updatedAt"] = entry["lastUsedAt"]
    _save_web_fetch_profiles(profiles)
    return {
        "profileKey": profile_key,
        "profileUpdatedAt": extract_profile["updatedAt"],
        "selectorScore": _selector_score(entry),
        "selectorStats": {
            "directHits": entry["directHits"],
            "adaptiveHits": entry["adaptiveHits"],
            "successes": entry["successes"],
            "misses": entry["misses"],
        },
    }


def _adaptive_storage_file(url: str = "") -> str:
    override = _safe_text(get_web_fetch_config().get("adaptiveStorageFile"))
    if override:
        override_path = Path(override)
        override_path.parent.mkdir(parents=True, exist_ok=True)
        return str(override_path)
    profile_key = _site_profile_key(url) if url else "global"
    storage_dir = _web_fetch_cache_dir() / "adaptive"
    storage_dir.mkdir(parents=True, exist_ok=True)
    return str(storage_dir / f"{profile_key}.db")


def _default_adaptive_id(url: str, extract: WebExtractMode) -> str:
    parsed = urlparse(url)
    host = (parsed.netloc or "unknown").lower()
    path = (parsed.path or "/").strip("/") or "root"
    stable_path = path.replace("/", ":")
    return f"{host}:{stable_path}:{extract}:container"


def _build_adaptive_selector(page: WebPagePayload) -> Selector:
    return Selector(
        content=page.html,
        url=page.final_url or page.url,
        adaptive=True,
        storage=SQLiteStorageSystem,
        storage_args={
            "storage_file": _adaptive_storage_file(page.final_url or page.url),
            "url": page.final_url or page.url,
        },
    )


def _resolve_extract_container(
    page: WebPagePayload,
    *,
    extract: WebExtractMode,
    adaptive_enabled: bool,
    adaptive_id: str,
    adaptive_threshold: int,
) -> tuple[BeautifulSoup, dict[str, Any], dict[str, Any]]:
    resolved_url = page.final_url or page.url
    profile_key, selector_candidates, profile_selectors, selector_entries = _selector_candidates_for_extract(resolved_url, extract)
    soup = BeautifulSoup(page.html, "html.parser")
    fallback_container = soup.find("main") or soup.find("article") or soup.find(attrs={"role": "main"}) or soup.body or soup

    if not adaptive_enabled:
        matched_selector = next((selector for selector in selector_candidates if soup.select_one(selector)), "")
        selected_selector = matched_selector or _safe_text(getattr(fallback_container, "name", "")) or WEB_CONTAINER_SELECTOR
        selected_node = soup.select_one(selected_selector) if matched_selector else fallback_container
        selector_source = (
            "site_profile" if selected_selector in profile_selectors else "default" if matched_selector else "fallback"
        )
        selector_meta = _record_selector_signal(
            url=resolved_url,
            extract=extract,
            selector=selected_selector,
            source=selector_source,
            direct_hit=selected_node is not None,
            adaptive_hit=False,
            success=selected_node is not None,
            selected_tag=_safe_text(getattr(selected_node, "name", "")),
        )
        return selected_node or fallback_container, {
            "adaptiveEnabled": False,
            "adaptiveId": "",
            "adaptiveThreshold": adaptive_threshold,
            "storageFile": "",
            "storagePresentBefore": False,
            "storagePresentAfter": False,
            "directSelectorMatched": False,
            "usedAdaptiveRecovery": False,
            "selector": WEB_CONTAINER_SELECTOR,
            "selectedNodeTag": _safe_text(getattr(selected_node, "name", "")) or _safe_text(getattr(fallback_container, "name", "")),
            "adaptiveFallback": False,
            "error": "",
        }, {
            "profileKey": profile_key,
            "selectorCandidates": selector_candidates,
            "profileSelectors": profile_selectors,
            "selectorChosen": selected_selector,
            "selectorSource": selector_source,
            "profileHit": selected_selector in profile_selectors,
            "profileSelectorCount": len(profile_selectors),
            "profileUpdatedAt": selector_meta["profileUpdatedAt"],
            "selectorScore": selector_meta["selectorScore"],
            "selectorStats": selector_meta["selectorStats"],
        }

    try:
        selector = _build_adaptive_selector(page)
        selected_html = page.html
        selected_node = None
        selected_selector = WEB_CONTAINER_SELECTOR
        selector_source = "fallback"
        profile_hit = False
        direct_selector_matched = False
        used_adaptive_recovery = False
        storage_present_before = False
        storage_present_after = False

        for candidate in selector_candidates:
            direct_node = soup.select_one(candidate)
            identifier = f"{adaptive_id}:{_selector_identifier_token(candidate)}"
            storage_present_before = storage_present_before or selector.retrieve(identifier) is not None
            if direct_node is not None:
                direct_selector_matched = True
                selector.css(
                    candidate,
                    identifier=identifier,
                    adaptive=True,
                    auto_save=True,
                    percentage=max(0, min(adaptive_threshold, 100)),
                )
                storage_present_after = storage_present_after or selector.retrieve(identifier) is not None
                selected_node = direct_node
                selected_html = str(direct_node)
                selected_selector = candidate
                selector_source = "site_profile" if candidate in profile_selectors else "default"
                profile_hit = candidate in profile_selectors
                break

        if selected_node is None:
            for candidate in selector_candidates:
                identifier = f"{adaptive_id}:{_selector_identifier_token(candidate)}"
                storage_present_before = storage_present_before or selector.retrieve(identifier) is not None
                adaptive_matches = selector.css(
                    candidate,
                    identifier=identifier,
                    adaptive=True,
                    auto_save=True,
                    percentage=max(0, min(adaptive_threshold, 100)),
                )
                storage_present_after = storage_present_after or selector.retrieve(identifier) is not None
                if not adaptive_matches:
                    continue
                selected_node = adaptive_matches[0]
                selected_html = selected_node.get()
                selected_selector = candidate
                selector_source = "site_profile" if candidate in profile_selectors else "default"
                profile_hit = candidate in profile_selectors
                used_adaptive_recovery = storage_present_before
                break

        if selected_node is None:
            fallback_selector = _safe_text(getattr(fallback_container, "name", "")) or "body"
            identifier = f"{adaptive_id}:{_selector_identifier_token(fallback_selector)}"
            storage_present_before = storage_present_before or selector.retrieve(identifier) is not None
            if fallback_selector in {"main", "article", "body"}:
                try:
                    selector.css(
                        fallback_selector,
                        identifier=identifier,
                        adaptive=True,
                        auto_save=True,
                        percentage=max(0, min(adaptive_threshold, 100)),
                    )
                    storage_present_after = storage_present_after or selector.retrieve(identifier) is not None
                except Exception:
                    pass
            selected_node = fallback_container
            selected_html = str(fallback_container)
            selected_selector = fallback_selector
            selector_source = "fallback"
            direct_selector_matched = bool(soup.select_one(fallback_selector))

        container = BeautifulSoup(str(selected_html), "html.parser") if selected_node is not None else fallback_container
        selected_tag = _safe_text(getattr(selected_node, "tag", "")) or _safe_text(getattr(selected_node, "name", "")) or _safe_text(getattr(container, "name", ""))
        selector_meta = _record_selector_signal(
            url=resolved_url,
            extract=extract,
            selector=selected_selector,
            source=selector_source,
            direct_hit=direct_selector_matched,
            adaptive_hit=used_adaptive_recovery,
            success=selected_node is not None,
            selected_tag=selected_tag,
        )
        return container, {
            "adaptiveEnabled": True,
            "adaptiveId": adaptive_id,
            "adaptiveThreshold": adaptive_threshold,
            "storageFile": _adaptive_storage_file(resolved_url),
            "storagePresentBefore": storage_present_before,
            "storagePresentAfter": storage_present_after,
            "directSelectorMatched": direct_selector_matched,
            "usedAdaptiveRecovery": used_adaptive_recovery,
            "selector": selected_selector,
            "selectedNodeTag": selected_tag,
            "adaptiveFallback": False,
            "error": "",
        }, {
            "profileKey": profile_key,
            "selectorCandidates": selector_candidates,
            "profileSelectors": profile_selectors,
            "selectorChosen": selected_selector,
            "selectorSource": selector_source,
            "profileHit": profile_hit,
            "profileSelectorCount": len(profile_selectors),
            "profileUpdatedAt": selector_meta["profileUpdatedAt"],
            "selectorScore": selector_meta["selectorScore"],
            "selectorStats": selector_meta["selectorStats"],
        }
    except Exception as exc:
        selector_meta = _record_selector_signal(
            url=resolved_url,
            extract=extract,
            selector=WEB_CONTAINER_SELECTOR,
            source="fallback",
            direct_hit=False,
            adaptive_hit=False,
            success=True,
            selected_tag=_safe_text(getattr(fallback_container, "name", "")),
        )
        return fallback_container, {
            "adaptiveEnabled": True,
            "adaptiveId": adaptive_id,
            "adaptiveThreshold": adaptive_threshold,
            "storageFile": _adaptive_storage_file(resolved_url),
            "storagePresentBefore": False,
            "storagePresentAfter": False,
            "directSelectorMatched": False,
            "usedAdaptiveRecovery": False,
            "selector": WEB_CONTAINER_SELECTOR,
            "selectedNodeTag": _safe_text(getattr(fallback_container, "name", "")),
            "adaptiveFallback": True,
            "error": str(exc),
        }, {
            "profileKey": profile_key,
            "selectorCandidates": selector_candidates,
            "profileSelectors": profile_selectors,
            "selectorChosen": WEB_CONTAINER_SELECTOR,
            "selectorSource": "fallback",
            "profileHit": False,
            "profileSelectorCount": len(profile_selectors),
            "profileUpdatedAt": selector_meta["profileUpdatedAt"],
            "selectorScore": selector_meta["selectorScore"],
            "selectorStats": selector_meta["selectorStats"],
        }


def _build_analysis_hints(page: WebPagePayload) -> list[str]:
    hints: list[str] = []
    if page.media:
        hints.append("页面包含图片或媒体资源；如果需要理解视觉内容，优先把媒体 URL 或截图交给 vision_media_analyzer。")
    if len(page.text.strip()) < 300 and len(page.media) >= 2:
        hints.append("该页面正文较少但媒体较多，可能更适合走视觉分析而不是纯文本抽取。")
    if page.warnings:
        hints.append("当前抓取存在降级或环境告警，必要时可改用 dynamic/stealth 或交给浏览器自动化链路。")
    if page.agent_browser_profile_used:
        hints.append("本次读取使用了 Agent 专用浏览器 profile；结果可能包含该 profile 的登录态视角。")
    return hints


def _detect_login_wall(page: WebPagePayload) -> dict[str, Any] | None:
    final_url = _safe_text(page.final_url or page.url).lower()
    title = _safe_text(page.title).lower()
    text = _safe_text(page.text).lower()
    haystack = " ".join([final_url, title, text[:2000]])
    status = int(page.status or 0)
    if status in {401, 407}:
        return {"failureClass": "needs_login", "reason": f"http_status_{status}"}
    login_needles = (
        "/login",
        "/signin",
        "/sign-in",
        "login required",
        "sign in to continue",
        "please sign in",
        "log in to continue",
        "需要登录",
        "请登录",
        "登录后",
        "账号登录",
        "登录 / 注册",
    )
    if any(needle in haystack for needle in login_needles):
        return {"failureClass": "needs_login", "reason": "login_wall_detected"}
    return None


def _guess_remote_mime(url: str, fallback: str = "application/octet-stream") -> str:
    guessed, _ = mimetypes.guess_type(urlparse(url).path)
    return guessed or fallback


def _build_vision_candidates(page: WebPagePayload, *, limit: int = 6) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _append_candidate(*, url: str, label: str, media_type: str, source: str):
        normalized = _safe_text(url)
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        mime_type = _guess_remote_mime(normalized)
        kind = media_type or ("image" if mime_type.startswith("image/") else "video" if mime_type.startswith("video/") else "file")
        candidates.append(
            {
                "sourceUrl": normalized,
                "mimeTypeHint": mime_type,
                "kind": kind,
                "label": _safe_text(label)[:200] or normalized,
                "source": source,
                "promptSuggestion": (
                    "提取其中的文字、界面结构和关键视觉元素。"
                    if kind == "image"
                    else "总结媒体里的关键内容、文字和视觉变化。"
                ),
            }
        )

    for item in page.media:
        _append_candidate(
            url=item.get("url") or "",
            label=item.get("label") or item.get("url") or "",
            media_type=item.get("type") or "",
            source="page_media",
        )
        if len(candidates) >= limit:
            return candidates

    for meta_key in ("og:image", "twitter:image", "og:video", "twitter:player:stream"):
        meta_url = _safe_text(page.metadata.get(meta_key))
        if not meta_url:
            continue
        _append_candidate(
            url=urljoin(page.final_url or page.url, meta_url),
            label=meta_key,
            media_type="image" if "image" in meta_key else "video",
            source="page_metadata",
        )
        if len(candidates) >= limit:
            break

    return candidates


def _render_page_summary(page: WebPagePayload) -> dict[str, Any]:
    vision_candidates = _build_vision_candidates(page)
    return {
        "ok": True,
        "url": page.url,
        "finalUrl": page.final_url,
        "requestedMode": page.requested_mode,
        "refererMode": page.referer_mode,
        "refererUrl": page.referer_url,
        "status": page.status,
        "fetchMode": page.fetch_mode,
        "tlsStrategy": page.tls_strategy,
        "caBundlePath": page.ca_bundle_path,
        "proxyBypassUsed": page.proxy_bypass_used,
        "attemptedModes": page.attempted_modes,
        "availableModes": page.available_modes,
        "fallbackUsed": page.requested_mode == "auto" and page.fetch_mode != "static",
        "warnings": page.warnings,
        "title": page.title,
        "text": page.text,
        "metadata": page.metadata,
        "links": page.links,
        "media": page.media,
        "analysisHints": _build_analysis_hints(page),
        "visionCandidates": vision_candidates,
        "visionRecommended": bool(vision_candidates),
        "agentBrowserProfile": (
            {
                "used": True,
                "matchedHost": page.agent_browser_profile_host,
                "profile": agent_browser_profile_summary("chrome", include_security_note=False),
            }
            if page.agent_browser_profile_used
            else {"used": False}
        ),
    }


def _render_error_payload(
    *,
    url: str,
    requested_mode: str,
    referer_mode: str,
    referer_url: str,
    error: str,
    blocked: bool = False,
    failure_class: str = "",
    attempted_modes: list[str] | None = None,
    elapsed_ms: int | None = None,
    retryable: bool | None = None,
) -> str:
    normalized_error = _safe_text(error)
    if not failure_class:
        failure_class = _classify_web_fetch_failure(normalized_error, blocked=blocked)
    return json.dumps(
        {
            "ok": False,
            "blocked": blocked,
            "url": url,
            "requestedMode": requested_mode,
            "refererMode": referer_mode,
            "refererUrl": referer_url,
            "availableModes": _dependency_status(),
            "failureClass": failure_class,
            "attemptedModes": attempted_modes or [],
            "elapsedMs": elapsed_ms,
            "retryable": bool(retryable) if retryable is not None else failure_class in {"network_timeout", "web_fetch_failed"},
            "recommendedNextAction": (
                "该 URL 当前不可达；不要等待 watchdog。请换可访问来源、改用 research_broker 多源调研，或把失败源标记为 unavailable。"
                if failure_class == "network_timeout"
                else "目标可能需要登录。请在 Admin / Desktop Automation 打开 Agent 专用浏览器完成登录；若要让 web/research 使用该登录态，需要显式 useAgentBrowserProfile=true 且域名命中 allowlist。"
                if failure_class == "needs_login"
                else "Agent 浏览器 profile 未启用或目标域名未命中 allowlist；请在 Admin / System Base 配置 useAgentBrowserProfile 与 allowlist，或改用无登录公开来源。"
                if failure_class == "agent_browser_profile_not_allowed"
                else "根据 failureClass 决定换源、缩小请求或停止该工具链。"
            ),
            "error": normalized_error,
        },
        ensure_ascii=False,
        indent=2,
    )


def _render_needs_login_payload(*, page: WebPagePayload, use_agent_browser_profile: bool) -> str:
    login_wall = _detect_login_wall(page) or {"failureClass": "needs_login", "reason": "login_required"}
    text_preview = "" if page.agent_browser_profile_used else _safe_text(page.text)[:500]
    return json.dumps(
        {
            "ok": False,
            "failureClass": "needs_login",
            "reason": login_wall.get("reason"),
            "url": page.url,
            "finalUrl": page.final_url,
            "title": page.title,
            "status": page.status,
            "fetchMode": page.fetch_mode,
            "attemptedModes": page.attempted_modes,
            "useAgentBrowserProfile": bool(use_agent_browser_profile),
            "agentBrowserProfile": (
                {
                    "used": True,
                    "matchedHost": page.agent_browser_profile_host,
                    "profile": agent_browser_profile_summary("chrome", include_security_note=False),
                }
                if page.agent_browser_profile_used
                else {"used": False}
            ),
            "retryable": True,
            "recommendedNextAction": "请在 Admin / Desktop Automation 点击“打开 Agent 专用浏览器”并手动登录目标网站；登录后可用 useAgentBrowserProfile=true 重试，目标域名必须在 systemBase.webFetch.agentBrowserProfileAllowlist 中。",
            "textPreview": text_preview,
        },
        ensure_ascii=False,
        indent=2,
    )


def _error_attempted_modes(error: str) -> list[str]:
    match = re.search(r"attempted=\[([^\]]*)\]", str(error or ""))
    if not match:
        return []
    return [
        item.strip().strip("'\"")
        for item in match.group(1).split(",")
        if item.strip().strip("'\"")
    ]


def _error_elapsed_ms(error: str) -> int | None:
    match = re.search(r"elapsed=([0-9.]+)s", str(error or ""))
    if not match:
        return None
    try:
        return int(float(match.group(1)) * 1000)
    except Exception:
        return None


def _trim_broker_text(value: Any, *, limit: int = 2400) -> tuple[str, bool]:
    normalized = _safe_text(value)
    if len(normalized) <= limit:
        return normalized, False
    return normalized[:limit].rstrip(), True


def _search_result_quality_hints(url: str) -> dict[str, Any]:
    host = (urlparse(url).hostname or "").lower()
    authoritative = any(
        hint in host or hint in str(url or "").lower()
        for hint in (
            "docs.",
            "developer.",
            "developers.",
            "platform.",
            "api.",
            "learn.microsoft.com",
            "cloud.google.com",
            "docs.aws.amazon.com",
            "github.com",
        )
    )
    low_quality = any(
        hint in host or hint in str(url or "").lower()
        for hint in (
            "pinterest.",
            "quora.",
            "reddit.",
            "medium.",
            "zhihu.",
            "csdn.",
        )
    )
    score = 70 if authoritative else 50
    if low_quality:
        score -= 25
    score = max(0, min(score, 100))
    signals = []
    if authoritative:
        signals.append("authoritative_host_hint")
    if low_quality:
        signals.append("low_quality_host_hint")
    return {
        "host": host,
        "authorityScore": score,
        "tier": "primary" if score >= 70 else ("secondary" if score >= 45 else "weak"),
        "signals": signals,
    }


def _search_relevance_score(query: str, result: dict[str, Any]) -> int:
    haystack = " ".join(
        _safe_text(result.get(key)).lower()
        for key in ("title", "snippet", "url")
    )
    query_text = _safe_text(query).lower()
    if not query_text or not haystack:
        return 0
    latin_terms = [term for term in re.split(r"[^a-z0-9]+", query_text) if len(term) >= 3]
    cjk_terms = re.findall(r"[\u4e00-\u9fff]{2,}", query_text)
    terms = latin_terms + cjk_terms
    if not terms:
        return 0
    hits = sum(1 for term in terms if term in haystack)
    return int(round((hits / max(len(terms), 1)) * 100))


def _compact_web_broker_payload(payload: dict[str, Any], *, requested_mode: str, debug: bool) -> dict[str, Any]:
    ok = bool(payload.get("ok"))
    resolved_mode = requested_mode
    if requested_mode == "fetch":
        if "query" in payload or "results" in payload:
            resolved_mode = "search"
        elif "extract" in payload:
            resolved_mode = "extract"
        else:
            resolved_mode = "read"

    compact: dict[str, Any] = {
        "ok": payload.get("ok"),
        "mode": resolved_mode,
    }
    debug_payload: dict[str, Any] = {}

    if ok:
        if resolved_mode == "search":
            query = _safe_text(payload.get("query"))
            provider = _safe_text(payload.get("provider"))
            results = payload.get("results") if isinstance(payload.get("results"), list) else []
            ranked_results = []
            relevance_scores = []
            authority_scores = []
            for index, result in enumerate(results, start=1):
                item = dict(result or {})
                url = _safe_text(item.get("url"))
                item["resultRank"] = index
                item["finalUrl"] = item.get("finalUrl") or url
                if url:
                    item["sourceQualityHints"] = _search_result_quality_hints(url)
                    authority_scores.append(int(item["sourceQualityHints"].get("authorityScore") or 0))
                relevance = _search_relevance_score(query, item)
                item["relevanceScore"] = relevance
                relevance_scores.append(relevance)
                ranked_results.append(item)
            average_relevance = int(round(sum(relevance_scores) / len(relevance_scores))) if relevance_scores else 0
            average_authority = int(round(sum(authority_scores) / len(authority_scores))) if authority_scores else 0
            quality = "weak" if not results or average_relevance < 20 or average_authority < 35 else "usable"
            compact.update(
                {
                    "summary": f"搜索到 {len(results)} 条结果。" if results else "没有找到可用结果。",
                    "query": query,
                    "provider": provider or None,
                    "searchVertical": payload.get("searchVertical") or None,
                    "resultCount": payload.get("resultCount") if payload.get("resultCount") is not None else len(results),
                    "quality": quality,
                    "sourceQualitySummary": {
                        "averageRelevance": average_relevance,
                        "averageAuthority": average_authority,
                        "recommendedNextAction": (
                            "结果相关性较弱；请换关键词、限定官方/权威域名，或改用 research_broker 汇总多源证据。"
                            if quality == "weak"
                            else "可作为单次搜索线索；复杂事实仍建议用 research_broker。"
                        ),
                    },
                    "results": ranked_results,
                    "omitted": {
                        "fullSearchHtml": "omitted",
                        "sourceRanking": "heuristic; use research_broker for multi-source confidence and evidence bundles",
                    },
                }
            )
        else:
            final_url = payload.get("finalUrl") or payload.get("url")
            title = _safe_text(payload.get("title"))
            text = payload.get("text")
            text_preview, text_truncated = _trim_broker_text(text, limit=2200) if text not in (None, "") else ("", False)
            compact.update(
                {
                    "summary": title or ("网页提取完成。" if resolved_mode == "extract" else "网页读取完成。"),
                    "url": payload.get("url"),
                    "finalUrl": final_url,
                    "title": title or None,
                }
            )
            if text_preview:
                if text_truncated:
                    compact["textPreview"] = text_preview
                    compact["textTruncated"] = True
                else:
                    compact["text"] = text_preview
            if resolved_mode == "extract":
                compact["extract"] = payload.get("extract")
                if "links" in payload:
                    compact["links"] = payload.get("links")
                if "media" in payload:
                    compact["media"] = payload.get("media")
                if "metadata" in payload:
                    compact["metadata"] = payload.get("metadata")
            else:
                if "links" in payload:
                    compact["links"] = payload.get("links")
                if "media" in payload:
                    compact["media"] = payload.get("media")
        analysis_hints = payload.get("analysisHints")
        if analysis_hints not in (None, "", [], {}):
            compact["analysisHints"] = analysis_hints
        vision_candidates = payload.get("visionCandidates")
        if vision_candidates not in (None, "", [], {}):
            compact["visionCandidates"] = vision_candidates
        warnings = payload.get("warnings")
        if isinstance(warnings, list) and warnings:
            compact["warnings"] = warnings
        if isinstance(payload.get("agentBrowserProfile"), dict) and payload["agentBrowserProfile"].get("used"):
            compact["agentBrowserProfile"] = payload.get("agentBrowserProfile")
    else:
        failure_class = str(payload.get("failureClass") or "").strip()
        if failure_class == "needs_login":
            summary = "目标页面需要登录；请在 Admin 打开 Agent 专用浏览器完成登录后重试。"
        elif failure_class == "agent_browser_profile_not_allowed":
            summary = "Agent 浏览器 profile 未启用或目标域名未命中 allowlist。"
        elif failure_class == "network_timeout":
            summary = "目标网络请求超时；请换源或稍后重试。"
        else:
            summary = _safe_text(payload.get("error")) or "Web broker 执行失败。"
        compact.update(
            {
                "summary": summary,
                "error": payload.get("error"),
                "failureClass": payload.get("failureClass"),
                "attemptedModes": payload.get("attemptedModes"),
                "elapsedMs": payload.get("elapsedMs"),
                "retryable": payload.get("retryable"),
                "recommendedNextAction": payload.get("recommendedNextAction"),
            }
        )
        if payload.get("blocked") is not None:
            compact["blocked"] = payload.get("blocked")
        if payload.get("url") not in (None, ""):
            compact["url"] = payload.get("url")
        if payload.get("query") not in (None, ""):
            compact["query"] = payload.get("query")
        if payload.get("attemptedProviders") not in (None, "", [], {}):
            compact["attemptedProviders"] = payload.get("attemptedProviders")
        if isinstance(payload.get("agentBrowserProfile"), dict):
            compact["agentBrowserProfile"] = payload.get("agentBrowserProfile")

    for key in (
        "requestedMode",
        "refererMode",
        "refererUrl",
        "fetchMode",
        "tlsStrategy",
        "caBundlePath",
        "proxyBypassUsed",
        "attemptedModes",
        "availableModes",
        "adaptiveSignals",
        "selectorSignals",
        "requestedProvider",
        "attemptedProviders",
        "searchUrl",
        "status",
        "fallbackUsed",
        "visionRecommended",
    ):
        value = payload.get(key)
        if value not in (None, "", [], {}):
            debug_payload[key] = value

    if debug and debug_payload:
        compact["debug"] = debug_payload

    return {key: value for key, value in compact.items() if value not in (None, "", [], {})}


def _looks_like_url(value: str) -> bool:
    normalized = _safe_text(value).lower()
    return normalized.startswith("http://") or normalized.startswith("https://")


def _extract_search_results(soup: BeautifulSoup, *, provider: str, limit: int) -> list[dict[str, str]]:
    selectors = {
        "bing": [
            ("li.b_algo", "h2 a", ".b_caption p"),
        ],
        "google": [
            ("div.g", "a", ".VwiC3b, .yXK7lf, .MUxGbd"),
        ],
        "baidu": [
            ("div.result, div.c-container, div.result-op", "h3 a", ".c-abstract, .content-right_8Zs40, .c-span-last"),
        ],
        "duckduckgo": [
            (".result", ".result__a", ".result__snippet"),
        ],
    }.get(provider, [])
    results: list[dict[str, str]] = []
    seen: set[str] = set()
    for node_selector, anchor_selector, snippet_selector in selectors:
        for result_node in soup.select(node_selector):
            anchor = result_node.select_one(anchor_selector) or result_node.select_one("a[href]")
            if not anchor:
                continue
            href = _safe_text(anchor.get("href"))
            title = _safe_text(anchor.get_text(" ", strip=True))
            if not href or href in seen:
                continue
            seen.add(href)
            snippet_node = result_node.select_one(snippet_selector) if snippet_selector else None
            snippet = _safe_text(snippet_node.get_text(" ", strip=True) if snippet_node else "")
            results.append({"title": title[:300], "url": href, "snippet": snippet[:600]})
            if len(results) >= max(1, min(limit, 10)):
                return results
        if results:
            return results
    return results


def _search_page_failure(payload: WebPagePayload, soup: BeautifulSoup, *, provider: str, result_count: int) -> dict[str, Any] | None:
    if result_count > 0:
        return None
    final_url = _safe_text(payload.final_url or payload.url).lower()
    title = _safe_text(payload.title).lower()
    text_preview = _safe_text(soup.get_text(" ", strip=True))[:1000].lower()
    if int(payload.status or 0) in {403, 429}:
        return {"status": "challenge", "failureClass": "provider_challenge", "reason": f"http_status_{payload.status}"}
    if provider == "google" and (
        "/sorry/" in final_url
        or "/httpservice/retry/enablejs" in text_preview
        or "please click here if you are not redirected" in text_preview
    ):
        return {"status": "challenge", "failureClass": "provider_challenge", "reason": "google_requires_js_or_captcha"}
    if provider == "baidu" and (
        "wappass.baidu.com" in final_url
        or "百度安全验证" in title
        or "网络不给力，请稍后重试" in text_preview
    ):
        return {"status": "challenge", "failureClass": "provider_challenge", "reason": "baidu_safety_verification"}
    return {"status": "empty", "failureClass": "no_results", "reason": "no_search_results_extracted"}


def _normalize_search_vertical(value: str) -> str:
    normalized = _safe_text(value).lower().replace("-", "_")
    aliases = {
        "": "all",
        "auto": "all",
        "default": "all",
        "metaso": "all",
        "all": "all",
        "web": "web",
        "网页": "web",
        "全网": "all",
        "document": "document",
        "documents": "document",
        "doc": "document",
        "pdf": "document",
        "library": "document",
        "文库": "document",
        "academic": "academic",
        "scholar": "academic",
        "paper": "academic",
        "papers": "academic",
        "学术": "academic",
        "image": "image",
        "images": "image",
        "图片": "image",
        "video": "video",
        "videos": "video",
        "视频": "video",
        "podcast": "podcast",
        "podcasts": "podcast",
        "播客": "podcast",
    }
    return aliases.get(normalized, normalized if normalized in METASO_VERTICAL_ENGINE_TYPES else "all")


def _metaso_extract_token(html: str) -> str:
    match = re.search(r'<meta[^>]+id=["\']meta-token["\'][^>]+content=["\']([^"\']+)["\']', html)
    return match.group(1) if match else ""


def _metaso_result_from_item(item: dict[str, Any], *, rank: int, vertical: str) -> dict[str, str]:
    url = _safe_text(item.get("link") or item.get("url") or item.get("origin_url") or item.get("previewUrl"))
    title = _safe_text(item.get("title") or item.get("orig_title") or item.get("orig_o_title") or item.get("displaySource"))
    snippet = _safe_text(
        item.get("matched_snippet")
        or item.get("snippet")
        or item.get("abstract")
        or item.get("caption")
        or item.get("export")
    )
    source = _safe_text(item.get("displaySource") or item.get("source") or item.get("institution"))
    date = _safe_text(item.get("date") or item.get("publish_date_str") or item.get("publish_date"))
    result: dict[str, str] = {
        "title": title[:300],
        "url": url,
        "snippet": snippet[:700],
        "source": source[:160],
        "date": date[:80],
        "vertical": vertical,
        "rank": str(rank),
    }
    if vertical == "image":
        image_url = _safe_text(
            item.get("thumbnail")
            or item.get("pic")
            or item.get("image")
            or item.get("imageUrl")
            or item.get("url")
        )
        if image_url:
            result["imageUrl"] = image_url[:700]
    return {key: value for key, value in result.items() if value not in ("", None)}


def _metaso_search_public(query: str, *, limit: int, vertical: str, timeout_seconds: float) -> dict[str, Any]:
    deadline = time.monotonic() + max(1.0, timeout_seconds)
    normalized_vertical = _normalize_search_vertical(vertical)
    engine_type = METASO_VERTICAL_ENGINE_TYPES.get(normalized_vertical, "")
    session = requests.Session()
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
        ),
        "metaso-pc": "pc",
    }
    with _bypass_proxy_env(_should_bypass_proxy_env()):
        home_timeout = max(1.0, min(8.0, deadline - time.monotonic()))
        home = session.get(METASO_HOME_URL, headers=headers, timeout=home_timeout)
        token = _metaso_extract_token(home.text)
        if not token:
            return {
                "ok": False,
                "failureClass": "provider_challenge",
                "reason": "metaso_token_not_found",
                "retryable": True,
            }

        payload = {
            "question": query,
            "mode": "detail",
            "model": "fast_thinking",
            "deepResearchModel": "fast",
            "engineType": engine_type,
            "scholarSearchDomain": "all",
            "debug": False,
            "url": METASO_HOME_URL,
            "lang": "zh",
            "enableMix": True,
            "newEngine": True,
            "enableImage": normalized_vertical == "image",
            "metaso-pc": "pc",
            "token": token,
        }
        request_headers = {
            **headers,
            "accept": "text/event-stream",
            "content-type": "application/json",
            "origin": "https://metaso.cn",
            "referer": METASO_HOME_URL,
            "token": token,
        }
        stream_timeout = max(1.0, deadline - time.monotonic())
        response = session.post(
            METASO_SEARCH_ENDPOINT,
            headers=request_headers,
            json=payload,
            stream=True,
            timeout=stream_timeout,
        )

        results: list[dict[str, str]] = []
        seen: set[str] = set()
        events_seen = 0
        error_event: dict[str, Any] | None = None
        result_id = ""
        group_id = ""
        max_results = max(1, min(int(limit or 5), 10))
        for raw_line in response.iter_lines(decode_unicode=True):
            if time.monotonic() >= deadline:
                error_event = {"failureClass": "deadline_exceeded", "reason": "metaso_provider_deadline_exceeded"}
                break
            if not raw_line:
                continue
            line = raw_line.strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            if data == "[TOO_MANY_REQUESTS]":
                error_event = {"failureClass": "provider_rate_limited", "reason": "metaso_too_many_requests"}
                break
            try:
                event = json.loads(data)
            except Exception:
                continue
            events_seen += 1
            event_type = _safe_text(event.get("type"))
            if event_type == "error":
                error_event = {
                    "failureClass": "provider_rate_limited" if int(event.get("code") or 0) == 4001 else "provider_error",
                    "reason": _safe_text(event.get("msg") or event.get("message") or "metaso_error"),
                    "code": event.get("code"),
                }
                break
            if event_type == "session-created" and isinstance(event.get("data"), dict):
                group_id = _safe_text(event["data"].get("groupId") or event["data"].get("id"))
            if event_type in {"query", "set-reference"}:
                result_id = _safe_text(event.get("debugId") or event.get("resultId") or result_id)
            if event_type in {"set-reference", "update-reference"} and isinstance(event.get("list"), list):
                for item in event["list"]:
                    if not isinstance(item, dict):
                        continue
                    result = _metaso_result_from_item(item, rank=len(results) + 1, vertical=normalized_vertical)
                    identity = _safe_text(result.get("url") or item.get("id") or item.get("docId"))
                    if not identity or identity in seen:
                        continue
                    seen.add(identity)
                    results.append(result)
                    if len(results) >= max_results:
                        return {
                            "ok": True,
                            "provider": "metaso",
                            "searchVertical": normalized_vertical,
                            "engineType": engine_type,
                            "resultId": result_id,
                            "groupId": group_id,
                            "eventsSeen": events_seen,
                            "results": results,
                        }
        if results:
            return {
                "ok": True,
                "provider": "metaso",
                "searchVertical": normalized_vertical,
                "engineType": engine_type,
                "resultId": result_id,
                "groupId": group_id,
                "eventsSeen": events_seen,
                "results": results[:max_results],
            }
        if error_event:
            return {"ok": False, **error_event, "eventsSeen": events_seen, "searchVertical": normalized_vertical}
        return {
            "ok": False,
            "failureClass": "no_results",
            "reason": "metaso_no_public_results",
            "eventsSeen": events_seen,
            "searchVertical": normalized_vertical,
        }


@tool
def web_read(
    url: str,
    mode: WebFetchMode = "auto",
    headless: bool = True,
    referer_mode: WebRefererMode = "none",
    referer_url: str = "",
    useAgentBrowserProfile: bool = False,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> str:
    """Read a webpage with Scrapling and return a compact, structured article-style result.

    Use this for a known URL. For research questions that need several sources or confidence scoring, request
    research.core and use research_broker.

    mode:
    - auto: 先走静态抓取，再按需尝试 dynamic / stealth
    - static: 仅静态抓取
    - dynamic: 仅动态页面抓取
    - stealth: 仅反反爬抓取

    useAgentBrowserProfile:
    - 默认 false。只有目标域名命中 systemBase.webFetch.agentBrowserProfileAllowlist 且 Admin 已开启该能力时，才可设为 true 使用 Agent 专用浏览器登录态。
    """
    allowed, error_message = _guard_url(url, tool_call_id=tool_call_id)
    if not allowed:
        return _render_error_payload(
            url=url,
            requested_mode=mode,
            referer_mode=referer_mode,
            referer_url=referer_url,
            error=error_message or "Safety Guardian 已阻止网页读取。",
            blocked=True,
        )

    try:
        payload = _fetch_with_scrapling_internal(
            url,
            mode=mode,
            headless=headless,
            referer_mode=referer_mode,
            referer_url=referer_url,
            timeout_seconds=WEB_READ_TIMEOUT_SECONDS,
            use_agent_browser_profile=bool(useAgentBrowserProfile),
        )
        if _detect_login_wall(payload):
            return _render_needs_login_payload(page=payload, use_agent_browser_profile=bool(useAgentBrowserProfile))
        return json.dumps(_render_page_summary(payload), ensure_ascii=False, indent=2)
    except Exception as exc:
        error = str(exc)
        return _render_error_payload(
            url=url,
            requested_mode=mode,
            referer_mode=referer_mode,
            referer_url=referer_url,
            error=f"Error reading webpage with Scrapling: {error}",
            attempted_modes=_error_attempted_modes(error),
            elapsed_ms=_error_elapsed_ms(error),
        )


@tool
def web_extract(
    url: str,
    extract: WebExtractMode = "article",
    mode: WebFetchMode = "auto",
    headless: bool = True,
    referer_mode: WebRefererMode = "none",
    referer_url: str = "",
    adaptive: bool = False,
    adaptive_id: str = "",
    adaptive_threshold: int = 70,
    useAgentBrowserProfile: bool = False,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> str:
    """Extract structured webpage content with Scrapling.

    extract:
    - article: 提取正文、标题与摘要信息
    - links: 提取页面主要链接
    - metadata: 提取 meta 数据

    useAgentBrowserProfile 同 web_read：必须显式为 true，且目标域名命中 allowlist。
    """
    allowed, error_message = _guard_url(url, tool_call_id=tool_call_id)
    if not allowed:
        return _render_error_payload(
            url=url,
            requested_mode=mode,
            referer_mode=referer_mode,
            referer_url=referer_url,
            error=error_message or "Safety Guardian 已阻止网页提取。",
            blocked=True,
        )

    try:
        payload = _fetch_with_scrapling_internal(
            url,
            mode=mode,
            headless=headless,
            referer_mode=referer_mode,
            referer_url=referer_url,
            timeout_seconds=WEB_READ_TIMEOUT_SECONDS,
            use_agent_browser_profile=bool(useAgentBrowserProfile),
        )
        if _detect_login_wall(payload):
            return _render_needs_login_payload(page=payload, use_agent_browser_profile=bool(useAgentBrowserProfile))
        resolved_adaptive_id = adaptive_id.strip() or _default_adaptive_id(payload.final_url or payload.url, extract)
        container, adaptive_signals, selector_signals = _resolve_extract_container(
            payload,
            extract=extract,
            adaptive_enabled=adaptive,
            adaptive_id=resolved_adaptive_id,
            adaptive_threshold=adaptive_threshold,
        )
        result = {
            "ok": True,
            "url": payload.url,
            "finalUrl": payload.final_url,
            "requestedMode": payload.requested_mode,
            "refererMode": payload.referer_mode,
            "refererUrl": payload.referer_url,
            "status": payload.status,
            "fetchMode": payload.fetch_mode,
            "tlsStrategy": payload.tls_strategy,
            "caBundlePath": payload.ca_bundle_path,
            "proxyBypassUsed": payload.proxy_bypass_used,
            "attemptedModes": payload.attempted_modes,
            "availableModes": payload.available_modes,
            "fallbackUsed": payload.requested_mode == "auto" and payload.fetch_mode != "static",
            "warnings": payload.warnings,
            "analysisHints": _build_analysis_hints(payload),
            "visionCandidates": _build_vision_candidates(payload),
            "visionRecommended": bool(_build_vision_candidates(payload, limit=1)),
            "extract": extract,
            "adaptiveSignals": adaptive_signals,
            "selectorSignals": selector_signals,
        }
        if extract == "links":
            result["links"] = _extract_links(container, payload.final_url)
        elif extract == "media":
            result["media"] = _extract_media(container, payload.final_url)
        elif extract == "metadata":
            result["metadata"] = payload.metadata
            if adaptive:
                result["warnings"] = [
                    *payload.warnings,
                    "metadata 提取对 adaptive 的增益有限，当前仅对页面主容器定位做稳定性记录。",
                ]
        else:
            title = payload.title
            if not title:
                title_node = container.select_one("h1, title")
                title = _safe_text(title_node.get_text(" ", strip=True) if title_node else "")
            result["title"] = title
            result["text"] = _extract_main_text(container)
            result["metadata"] = payload.metadata
            result["media"] = _extract_media(container, payload.final_url)
        if adaptive and adaptive_signals.get("adaptiveFallback"):
            result["warnings"] = [
                *result.get("warnings", []),
                "adaptive 容器定位未能稳定启用，已自动回退到普通抽取。",
            ]
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as exc:
        error = str(exc)
        return _render_error_payload(
            url=url,
            requested_mode=mode,
            referer_mode=referer_mode,
            referer_url=referer_url,
            error=f"Error extracting webpage with Scrapling: {error}",
            attempted_modes=_error_attempted_modes(error),
            elapsed_ms=_error_elapsed_ms(error),
        )


@tool
def web_search(
    query: str,
    limit: int = 5,
    search_engine: WebSearchEngine = "auto",
    search_vertical: WebSearchVertical = "all",
    mode: WebFetchMode = "auto",
    referer_mode: WebRefererMode = "none",
    referer_url: str = "",
    useAgentBrowserProfile: bool = False,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> str:
    """Search the public web with a lightweight search provider and return structured results.

    For multi-source research, current facts, source confidence, or parallel query decomposition, request
    research.core and use research_broker instead of doing ad-hoc one-shot searches.

    search_vertical is honored by MetaSo public search:
    - all/web: 全网
    - document: 文库
    - academic: 学术
    - image/video/podcast: 图片/视频/播客

    useAgentBrowserProfile 仅用于需要登录态的 browser-backed 搜索/读取路径；默认不用 Agent 浏览器 profile。
    """
    requested_provider = str(search_engine or "auto").strip().lower()
    requested_vertical = _normalize_search_vertical(str(search_vertical or "all"))
    providers = _search_provider_order(requested_provider)
    attempted_providers: list[dict[str, Any]] = []
    started_at = time.monotonic()
    last_error = ""

    if not providers:
        return json.dumps(
            {
                "ok": False,
                "query": query,
                "requestedProvider": requested_provider,
                "searchVertical": requested_vertical,
                "attemptedProviders": attempted_providers,
                "failureClass": "unsupported_operation",
                "retryable": False,
                "recommendedNextAction": "使用 search_engine=auto，或选择 metaso/duckduckgo/baidu/bing/google 中的一个。",
                "error": f"Unsupported search provider: {requested_provider}",
            },
            ensure_ascii=False,
            indent=2,
        )

    for provider in providers:
        elapsed = time.monotonic() - started_at
        remaining = WEB_SEARCH_TOTAL_TIMEOUT_SECONDS - elapsed
        if remaining <= 0:
            attempted_providers.append(
                {
                    "provider": provider,
                    "status": "skipped",
                    "failureClass": "deadline_exceeded",
                    "reason": "search_total_deadline_exceeded",
                    "elapsedMs": int(elapsed * 1000),
                }
            )
            break
        search_url = SEARCH_PROVIDER_URLS[provider].format(query=quote_plus(query))
        allowed, error_message = _guard_url(search_url, tool_call_id=tool_call_id)
        if not allowed:
            attempted_providers.append({"provider": provider, "status": "blocked", "reason": error_message or "blocked"})
            last_error = error_message or "Safety Guardian 已阻止网页搜索。"
            continue

        try:
            provider_timeout = max(1.0, min(WEB_SEARCH_PROVIDER_TIMEOUT_SECONDS, remaining))
            if provider == "metaso":
                metaso_result = _metaso_search_public(
                    query,
                    limit=limit,
                    vertical=requested_vertical,
                    timeout_seconds=provider_timeout,
                )
                if not bool(metaso_result.get("ok")):
                    attempted_providers.append(
                        {
                            "provider": provider,
                            "status": "error",
                            "failureClass": metaso_result.get("failureClass") or "search_failed",
                            "reason": metaso_result.get("reason") or "metaso_public_search_failed",
                            "searchVertical": requested_vertical,
                            "eventsSeen": metaso_result.get("eventsSeen"),
                        }
                    )
                    last_error = _safe_text(metaso_result.get("reason") or metaso_result.get("failureClass"))
                    if requested_provider == "auto":
                        continue
                    return json.dumps(
                        {
                            "ok": False,
                            "query": query,
                            "requestedProvider": requested_provider,
                            "searchVertical": requested_vertical,
                            "attemptedProviders": attempted_providers,
                            "failureClass": metaso_result.get("failureClass") or "search_failed",
                            "elapsedMs": int((time.monotonic() - started_at) * 1000),
                            "retryable": metaso_result.get("failureClass") in {"provider_rate_limited", "network_timeout", "deadline_exceeded", "no_results"},
                            "recommendedNextAction": "MetaSo 公共搜索当前限流或无结果；请稍后重试、换 search_vertical，或让 auto 降级到其他搜索源。",
                            "error": last_error,
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                results = metaso_result.get("results") if isinstance(metaso_result.get("results"), list) else []
                attempted_providers.append(
                    {
                        "provider": provider,
                        "status": "ok",
                        "resultCount": len(results),
                        "searchVertical": requested_vertical,
                        "resultId": metaso_result.get("resultId"),
                    }
                )
                response = {
                    "ok": True,
                    "query": query,
                    "provider": provider,
                    "requestedProvider": requested_provider,
                    "searchVertical": requested_vertical,
                    "attemptedProviders": attempted_providers,
                    "searchUrl": search_url,
                    "resultCount": len(results),
                    "results": results,
                    "metaso": {
                        "engineType": metaso_result.get("engineType"),
                        "resultId": metaso_result.get("resultId"),
                        "groupId": metaso_result.get("groupId"),
                        "eventsSeen": metaso_result.get("eventsSeen"),
                    },
                }
                return json.dumps(response, ensure_ascii=False, indent=2)
            payload = _fetch_with_scrapling_internal(
                search_url,
                mode=mode,
                headless=True,
                referer_mode=referer_mode,
                referer_url=referer_url,
                timeout_seconds=provider_timeout,
                use_agent_browser_profile=bool(useAgentBrowserProfile),
            )
            soup = BeautifulSoup(payload.html, "html.parser")
            results = _extract_search_results(soup, provider=provider, limit=limit)
            page_failure = _search_page_failure(payload, soup, provider=provider, result_count=len(results))
            if page_failure:
                attempted_providers.append(
                    {
                        "provider": provider,
                        "status": page_failure["status"],
                        "failureClass": page_failure["failureClass"],
                        "reason": page_failure["reason"],
                        "resultCount": 0,
                        "finalUrl": payload.final_url,
                        "statusCode": payload.status,
                    }
                )
                last_error = str(page_failure["reason"])
                if requested_provider == "auto":
                    continue
                return json.dumps(
                    {
                        "ok": False,
                        "query": query,
                        "requestedProvider": requested_provider,
                        "searchVertical": requested_vertical,
                        "attemptedProviders": attempted_providers,
                        "failureClass": page_failure["failureClass"],
                        "elapsedMs": int((time.monotonic() - started_at) * 1000),
                        "retryable": page_failure["failureClass"] in {"provider_challenge", "no_results"},
                        "recommendedNextAction": "该搜索源返回验证页或没有可抽取结果；请换 provider、换关键词，或使用 research_broker 多源调研。",
                        "error": str(page_failure["reason"]),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            attempted_providers.append({"provider": provider, "status": "ok", "resultCount": len(results)})
            if not results and requested_provider == "auto":
                continue

            response = {
                "ok": True,
                "query": query,
                "provider": provider,
                "requestedProvider": requested_provider,
                "searchVertical": requested_vertical,
                "attemptedProviders": attempted_providers,
                "searchUrl": search_url,
                "requestedMode": payload.requested_mode,
                "refererMode": payload.referer_mode,
                "refererUrl": payload.referer_url,
                "fetchMode": payload.fetch_mode,
                "tlsStrategy": payload.tls_strategy,
                "caBundlePath": payload.ca_bundle_path,
                "proxyBypassUsed": payload.proxy_bypass_used,
                "attemptedModes": payload.attempted_modes,
                "availableModes": payload.available_modes,
                "fallbackUsed": payload.requested_mode == "auto" and payload.fetch_mode != "static",
                "warnings": payload.warnings,
                "analysisHints": _build_analysis_hints(payload),
                "resultCount": len(results),
                "results": results,
            }
            return json.dumps(response, ensure_ascii=False, indent=2)
        except Exception as exc:
            error = str(exc)
            last_error = error
            failure_class = _classify_web_fetch_failure(error)
            attempted_providers.append({
                "provider": provider,
                "status": "error",
                "failureClass": failure_class,
                "reason": error[:1000],
                "elapsedMs": _error_elapsed_ms(error),
            })

    aggregate_failure = "search_failed"
    non_blocked_attempts = [item for item in attempted_providers if item.get("status") != "blocked"]
    if non_blocked_attempts and all(item.get("failureClass") == "network_timeout" for item in non_blocked_attempts):
        aggregate_failure = "network_timeout"
    elif any(item.get("failureClass") == "tool_configuration_error" for item in attempted_providers):
        aggregate_failure = "tool_configuration_error"
    elif any(item.get("failureClass") == "tool_context_unavailable" for item in attempted_providers):
        aggregate_failure = "tool_context_unavailable"
    elif any(item.get("failureClass") == "provider_challenge" for item in attempted_providers):
        aggregate_failure = "provider_challenge"
    elif attempted_providers and all(item.get("failureClass") == "no_results" for item in attempted_providers):
        aggregate_failure = "no_results"
    elif any(item.get("status") == "blocked" for item in attempted_providers):
        aggregate_failure = "blocked_by_safety"

    return json.dumps(
        {
            "ok": False,
            "query": query,
            "requestedProvider": requested_provider,
            "searchVertical": requested_vertical,
            "attemptedProviders": attempted_providers,
            "failureClass": aggregate_failure,
            "elapsedMs": int((time.monotonic() - started_at) * 1000),
            "retryable": aggregate_failure in {"network_timeout", "search_failed", "provider_challenge", "no_results"},
            "recommendedNextAction": (
                "部分搜索源当前不可用；优先使用 MetaSo/DuckDuckGo，或换 search_vertical/缩小关键词，或改用 research_broker 记录 failed_source。"
                if aggregate_failure in {"network_timeout", "search_failed", "provider_challenge", "no_results"}
                else "检查工具配置/安全审批上下文；不要继续盲等 watchdog。"
            ),
            "error": last_error or "No search provider returned usable results.",
        },
        ensure_ascii=False,
        indent=2,
    )


@tool
def web_fetch(
    target: str,
    intent: WebFetchIntent = "auto",
    extract: WebExtractMode = "article",
    search_engine: WebSearchEngine = "auto",
    search_vertical: WebSearchVertical = "all",
    mode: WebFetchMode = "auto",
    headless: bool = True,
    referer_mode: WebRefererMode = "none",
    referer_url: str = "",
    adaptive: bool = False,
    adaptive_id: str = "",
    adaptive_threshold: int = 70,
    limit: int = 5,
    useAgentBrowserProfile: bool = False,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> str:
    """Unified web entrypoint for read / extract / search.

    intent:
    - auto: URL 走 read，非 URL 走 search
    - read: 返回网页摘要
    - extract: 返回结构化内容
    - search: 返回公开搜索结果；search_vertical 可选择 MetaSo 的全网/文库/学术/图片/视频/播客

    useAgentBrowserProfile 必须显式为 true，并且目标域名命中 Admin/System Base 的 allowlist。
    """
    normalized_intent = str(intent or "auto").strip().lower()
    if normalized_intent == "auto":
        normalized_intent = "read" if _looks_like_url(target) else "search"

    if normalized_intent == "read":
        return web_read.func(
            url=target,
            mode=mode,
            headless=headless,
            referer_mode=referer_mode,
            referer_url=referer_url,
            useAgentBrowserProfile=bool(useAgentBrowserProfile),
            tool_call_id=tool_call_id,
        )
    if normalized_intent == "extract":
        return web_extract.func(
            url=target,
            extract=extract,
            mode=mode,
            headless=headless,
            referer_mode=referer_mode,
            referer_url=referer_url,
            adaptive=adaptive,
            adaptive_id=adaptive_id,
            adaptive_threshold=adaptive_threshold,
            useAgentBrowserProfile=bool(useAgentBrowserProfile),
            tool_call_id=tool_call_id,
        )
    if normalized_intent == "search":
        return web_search.func(
            query=target,
            limit=limit,
            search_engine=search_engine,
            search_vertical=search_vertical,
            mode=mode,
            referer_mode=referer_mode,
            referer_url=referer_url,
            useAgentBrowserProfile=bool(useAgentBrowserProfile),
            tool_call_id=tool_call_id,
        )
    return json.dumps(
        {"ok": False, "intent": normalized_intent, "error": f"Unsupported web_fetch intent: {normalized_intent}"},
        ensure_ascii=False,
        indent=2,
    )


@tool
def web_broker(
    target: str,
    mode: str = "fetch",
    extract: WebExtractMode = "article",
    search_engine: WebSearchEngine = "auto",
    search_vertical: WebSearchVertical = "all",
    fetch_mode: WebFetchMode = "auto",
    headless: bool = True,
    referer_mode: WebRefererMode = "none",
    referer_url: str = "",
    adaptive: bool = False,
    adaptive_id: str = "",
    adaptive_threshold: int = 70,
    limit: int = 5,
    useAgentBrowserProfile: bool = False,
    debug: bool = False,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> str:
    """Unified web broker for public-web work: search finds results, fetch auto-routes URL vs query, read returns cleaned page text, and extract returns structured article/links/metadata/media output; add debug=true only for transport diagnostics.

    For multi-source facts, fresh provider/API details, source ranking, or complex research, request research.core and use research_broker. web_broker remains the single-page / single-query utility.

    mode:
    - fetch: smart unified entrypoint; URLs auto-route to read, non-URLs auto-route to search
    - read: read a single page and return compact text/title/link results
    - extract: 抽取结构化内容，适合 article / links / metadata / media
    - search: 公开搜索，返回搜索结果列表；search_vertical 可选择 MetaSo 的全网/文库/学术/图片/视频/播客

    debug:
    - 默认 false，只返回对 agent 真正有价值的精简结果
    - true 时把 transport / TLS / fallback / selector 等调试字段放进 debug 子对象

    useAgentBrowserProfile:
    - 默认 false。遇到登录墙时工具会返回 needs_login；需要登录态时，先让用户在 Admin 打开 Agent 专用浏览器登录，再显式传 true。
    """
    normalized_mode = str(mode or "fetch").strip().lower()
    if normalized_mode not in {"fetch", "read", "extract", "search"}:
        return json.dumps(
            {
                "ok": False,
                "mode": normalized_mode,
                "summary": f"Unsupported web_broker mode: {normalized_mode}",
                "error": f"Unsupported web_broker mode: {normalized_mode}",
            },
            ensure_ascii=False,
            indent=2,
        )

    intent = "auto" if normalized_mode == "fetch" else normalized_mode
    raw_result = web_fetch.func(
        target=target,
        intent=intent,
        extract=extract,
        search_engine=search_engine,
        search_vertical=search_vertical,
        mode=fetch_mode,
        headless=headless,
        referer_mode=referer_mode,
        referer_url=referer_url,
        adaptive=adaptive,
        adaptive_id=adaptive_id,
        adaptive_threshold=adaptive_threshold,
        limit=limit,
        useAgentBrowserProfile=bool(useAgentBrowserProfile),
        tool_call_id=tool_call_id,
    )
    try:
        parsed = json.loads(raw_result)
    except Exception:
        return raw_result
    if not isinstance(parsed, dict):
        return raw_result
    compact = _compact_web_broker_payload(parsed, requested_mode=normalized_mode, debug=bool(debug))
    return json.dumps(compact, ensure_ascii=False, indent=2)
