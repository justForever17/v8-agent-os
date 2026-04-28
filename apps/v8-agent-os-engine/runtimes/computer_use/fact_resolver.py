from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urlparse


FactSearch = Callable[[str], Any]

GITHUB_REPO_ALIASES: dict[str, dict[str, str]] = {
    "turix": {
        "owner": "TurixAI",
        "repo": "TuriX-CUA",
        "url": "https://github.com/TurixAI/TuriX-CUA",
        "source": "built_in_alias",
    },
    "turix-cua": {
        "owner": "TurixAI",
        "repo": "TuriX-CUA",
        "url": "https://github.com/TurixAI/TuriX-CUA",
        "source": "built_in_alias",
    },
    "turixai/turix-cua": {
        "owner": "TurixAI",
        "repo": "TuriX-CUA",
        "url": "https://github.com/TurixAI/TuriX-CUA",
        "source": "built_in_alias",
    },
}

_FACT_CACHE: dict[str, dict[str, Any]] = {}
_FACT_CACHE_TTL_SECONDS = 900


@dataclass(slots=True)
class FactResolutionResult:
    status: str
    targetKind: str | None = None
    canonicalTarget: dict[str, Any] | None = None
    evidence: list[dict[str, Any]] = field(default_factory=list)
    query: str | None = None
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "targetKind": self.targetKind,
            "canonicalTarget": dict(self.canonicalTarget or {}),
            "evidence": [dict(item) for item in self.evidence],
            "query": self.query,
            "reason": self.reason,
        }


def _contains_any(text: str, needles: list[str]) -> bool:
    lowered = text.lower()
    return any(str(item).lower() in lowered for item in needles)


def normalize_repo_token(value: str | None) -> str:
    return str(value or "").strip().lower().replace("_", "-")


def classify_goal(goal: str) -> dict[str, Any]:
    normalized_goal = str(goal or "").strip()
    lowered = normalized_goal.lower()
    explicit_url = _first_url(normalized_goal)
    githubish = "github" in lowered or "git hub" in lowered or bool(explicit_url and "github.com" in explicit_url.lower())
    starish = _contains_any(lowered, ["star", "星标", "点星", "收藏", "加星"])
    loginish = _contains_any(lowered, ["login", "sign in", "登录", "登入"])
    uploadish = _contains_any(lowered, ["upload", "上传", "choose file", "选择文件"])
    formish = _contains_any(lowered, ["form", "submit", "填写", "表单", "提交"])
    settingish = _contains_any(lowered, ["toggle", "setting", "settings", "开启", "关闭", "启用", "禁用", "设置"])
    search_openish = (
        _contains_any(lowered, ["search", "搜索", "查找", "找一下", "官网", "文档", "documentation"])
        and _contains_any(lowered, ["open", "打开", "进入", "访问", "看看", "找到"])
    )
    downloadish = _contains_any(lowered, ["download", "下载", "install", "安装"])
    repo_match = re.search(r"github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", normalized_goal)
    owner_repo = None
    if repo_match:
        owner_repo = f"{repo_match.group(1)}/{repo_match.group(2)}"
    else:
        owner_repo_match = re.search(r"\b([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)\b", normalized_goal)
        if owner_repo_match and githubish:
            owner_repo = owner_repo_match.group(1)
    if not owner_repo and "turix" in lowered:
        owner_repo = "TurixAI/TuriX-CUA"
    if githubish and starish:
        operation = "star_repository"
        domain = "github"
        target_type = "github_repo"
        risk = "external_account_state_mutation"
    elif uploadish:
        operation = "file_upload"
        domain = "web"
        target_type = "upload_target"
        risk = "file_upload_side_effect"
    elif formish:
        operation = "form_submit"
        domain = "web"
        target_type = "form_page"
        risk = "form_submit_side_effect"
    elif settingish:
        operation = "toggle_option"
        domain = "settings"
        target_type = "setting_target"
        risk = "settings_mutation"
    elif search_openish:
        operation = "search_and_open_result"
        domain = "web"
        target_type = "website_url"
        risk = "navigation"
    elif loginish:
        operation = "login_gate"
        domain = "browser"
        target_type = "login_boundary"
        risk = "credential_boundary"
    elif downloadish:
        operation = "download_and_open"
        domain = "web"
        target_type = "download_page"
        risk = "download_side_effect"
    elif explicit_url:
        operation = "search_and_open_result"
        domain = "web"
        target_type = "website_url"
        risk = "navigation"
    else:
        operation = "unknown"
        domain = "unknown"
        target_type = None
        risk = "unknown"
    return {
        "rawGoal": normalized_goal,
        "operation": operation,
        "domain": domain,
        "targetType": target_type,
        "entity": owner_repo or ("TuriX-CUA" if "turix" in lowered else None),
        "explicitUrl": explicit_url,
        "requiresFactResolution": bool(
            (githubish and starish)
            or (
                operation in {"download_and_open", "search_and_open_result", "file_upload", "form_submit", "unknown"}
                and not explicit_url
            )
        ),
        "risk": risk,
    }


def resolve_goal_facts(
    goal: str,
    *,
    intent: dict[str, Any] | None = None,
    web_searcher: FactSearch | None = None,
) -> FactResolutionResult:
    resolved_intent = dict(intent or classify_goal(goal))
    cache_key = _cache_key(goal, resolved_intent)
    cached = _cache_get(cache_key)
    if cached:
        return _result_from_dict(cached)
    explicit_url = str(resolved_intent.get("explicitUrl") or _first_url(goal) or "").strip()
    if explicit_url:
        result = _resolve_explicit_url(explicit_url, resolved_intent)
        _cache_put(cache_key, result)
        return result
    if resolved_intent.get("operation") == "star_repository":
        result = _resolve_github_star(resolved_intent, web_searcher=web_searcher)
        _cache_put(cache_key, result)
        return result
    if resolved_intent.get("operation") in {"login_gate", "login_boundary"}:
        evidence = [{
            "kind": "login_boundary",
            "confidence": 0.72,
            "source": "goal_language",
            "reason": "login_or_sign_in_intent_detected",
        }]
        result = FactResolutionResult(
            status="resolved",
            targetKind="login_boundary",
            canonicalTarget={"type": "login_boundary"},
            evidence=evidence,
            reason="login_boundary_detected",
        )
        _cache_put(cache_key, result)
        return result
    if resolved_intent.get("operation") in {"download_and_open", "download_or_install"}:
        query = f"{resolved_intent.get('rawGoal') or goal} official download"
        candidate = _first_url_from_search_payload(web_searcher(query)) if web_searcher else None
        if candidate:
            result = _resolve_explicit_url(candidate, {**resolved_intent, "operation": "download_and_open"})
            _cache_put(cache_key, result)
            return result
        result = FactResolutionResult(
            status="needs_fact_resolution",
            targetKind="download_page",
            evidence=[],
            query=query,
            reason="download_target_url_not_resolved",
        )
        _cache_put(cache_key, result)
        return result
    if resolved_intent.get("operation") in {"search_and_open_result", "form_submit", "file_upload"}:
        query = _query_for_intent(goal, resolved_intent)
        candidate = _first_url_from_search_payload(web_searcher(query)) if web_searcher and query else None
        if candidate:
            result = _resolve_explicit_url(candidate, resolved_intent)
            result.query = query
            result.reason = "web_search"
            _cache_put(cache_key, result)
            return result
        result = FactResolutionResult(
            status="needs_fact_resolution" if resolved_intent.get("requiresFactResolution") else "not_required",
            targetKind=resolved_intent.get("targetType"),
            evidence=[],
            query=query,
            reason="canonical_target_not_resolved",
        )
        _cache_put(cache_key, result)
        return result
    result = FactResolutionResult(
        status="not_required" if not resolved_intent.get("requiresFactResolution") else "needs_fact_resolution",
        targetKind=resolved_intent.get("targetType"),
        evidence=[],
        reason="no_canonical_target_required" if not resolved_intent.get("requiresFactResolution") else "canonical_target_not_resolved",
    )
    _cache_put(cache_key, result)
    return result


def _first_url(value: str | None) -> str | None:
    match = re.search(r"https?://[^\s<>)\"']+", str(value or ""))
    if not match:
        return None
    return match.group(0).rstrip(".,;，。)")


def _domain_from_url(value: str | None) -> str | None:
    if not value:
        return None
    try:
        host = urlparse(value).netloc.lower()
    except Exception:
        return None
    if host.startswith("www."):
        host = host[4:]
    return host or None


def _resolve_explicit_url(url: str, intent: dict[str, Any]) -> FactResolutionResult:
    github = re.match(r"https?://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)(?:[/#?].*)?$", url)
    if github:
        target = {
            "type": "github_repo",
            "owner": github.group(1),
            "repo": github.group(2),
            "url": f"https://github.com/{github.group(1)}/{github.group(2)}",
        }
        evidence = [{"kind": "canonical_github_repo", "confidence": 0.96, "source": "explicit_url", **target}]
        return FactResolutionResult(status="resolved", targetKind="github_repo", canonicalTarget=target, evidence=evidence)
    target = {
        "type": _target_type_from_operation(intent) or intent.get("targetType") or "website_url",
        "url": url,
        "domain": _domain_from_url(url),
    }
    evidence = [{"kind": "canonical_url", "confidence": 0.92, "source": "explicit_url", **target}]
    return FactResolutionResult(status="resolved", targetKind=str(target.get("type")), canonicalTarget=target, evidence=evidence)


def _resolve_github_star(intent: dict[str, Any], *, web_searcher: FactSearch | None = None) -> FactResolutionResult:
    entity = str(intent.get("entity") or "").strip()
    evidence: list[dict[str, Any]] = []
    alias = GITHUB_REPO_ALIASES.get(normalize_repo_token(entity))
    if alias:
        evidence.append({"kind": "canonical_github_repo", "confidence": 1.0, **alias})
        return FactResolutionResult(
            status="resolved",
            targetKind="github_repo",
            canonicalTarget={"type": "github_repo", **alias},
            evidence=evidence,
            reason="built_in_alias",
        )
    owner_repo = re.match(r"^([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)$", entity)
    if owner_repo:
        target = {
            "type": "github_repo",
            "owner": owner_repo.group(1),
            "repo": owner_repo.group(2),
            "url": f"https://github.com/{owner_repo.group(1)}/{owner_repo.group(2)}",
            "source": "explicit_owner_repo",
        }
        evidence.append({"kind": "canonical_github_repo", "confidence": 0.88, **target})
        return FactResolutionResult(status="resolved", targetKind="github_repo", canonicalTarget=target, evidence=evidence)
    query = f"site:github.com {entity or intent.get('rawGoal') or ''} GitHub repository".strip()
    if web_searcher and query:
        candidate = _candidate_from_search_payload(web_searcher(query))
        if candidate:
            evidence.append({"kind": "canonical_github_repo", "confidence": 0.72, **candidate, "query": query})
            return FactResolutionResult(
                status="resolved",
                targetKind="github_repo",
                canonicalTarget={"type": "github_repo", **candidate},
                evidence=evidence,
                query=query,
                reason="web_search",
            )
    return FactResolutionResult(
        status="needs_fact_resolution",
        targetKind="github_repo",
        evidence=evidence,
        query=query,
        reason="canonical_repo_url_not_resolved",
    )


def _coerce_search_payload(payload: Any) -> dict[str, Any]:
    if payload is None:
        return {}
    if isinstance(payload, str):
        try:
            decoded = json.loads(payload)
            return decoded if isinstance(decoded, dict) else {}
        except Exception:
            return {"raw": payload}
    return payload if isinstance(payload, dict) else {}


def _candidate_from_search_payload(payload: Any) -> dict[str, str] | None:
    data = _coerce_search_payload(payload)
    for item in list(data.get("results") or data.get("items") or []):
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or item.get("href") or "").strip()
        match = re.match(r"https?://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)(?:[/#?].*)?$", url)
        if not match:
            continue
        return {
            "owner": match.group(1),
            "repo": match.group(2),
            "url": f"https://github.com/{match.group(1)}/{match.group(2)}",
            "source": "web_search",
        }
    return None


def _first_url_from_search_payload(payload: Any) -> str | None:
    data = _coerce_search_payload(payload)
    for item in list(data.get("results") or data.get("items") or []):
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or item.get("href") or "").strip()
        if url.startswith(("http://", "https://")):
            return url
    raw = str(data.get("raw") or "")
    return _first_url(raw)


def _query_for_intent(goal: str, intent: dict[str, Any]) -> str:
    raw = str(intent.get("rawGoal") or goal or "").strip()
    operation = str(intent.get("operation") or "").strip()
    if operation == "file_upload":
        return f"{raw} upload page"
    if operation == "form_submit":
        return f"{raw} form page"
    return f"{raw} official site documentation"


def _target_type_from_operation(intent: dict[str, Any]) -> str | None:
    operation = str(intent.get("operation") or "").strip()
    return {
        "download_or_install": "download_page",
        "download_and_open": "download_page",
        "search_and_open_result": "website_url",
        "file_upload": "upload_target",
        "form_submit": "form_page",
    }.get(operation)


def _cache_key(goal: str, intent: dict[str, Any]) -> str:
    return json.dumps(
        {
            "goal": str(goal or "").strip().lower(),
            "operation": intent.get("operation"),
            "targetType": intent.get("targetType"),
            "entity": intent.get("entity"),
            "explicitUrl": intent.get("explicitUrl"),
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _cache_get(key: str) -> dict[str, Any] | None:
    item = _FACT_CACHE.get(key)
    if not isinstance(item, dict):
        return None
    ts = float(item.get("cachedAt") or 0)
    if time.time() - ts > _FACT_CACHE_TTL_SECONDS:
        _FACT_CACHE.pop(key, None)
        return None
    payload = dict(item.get("result") or {})
    if payload:
        payload["cache"] = {"hit": True, "ttlSeconds": _FACT_CACHE_TTL_SECONDS}
    return payload


def _cache_put(key: str, result: FactResolutionResult) -> None:
    if result.status not in {"resolved", "not_required"}:
        return
    _FACT_CACHE[key] = {
        "cachedAt": time.time(),
        "result": result.as_dict(),
    }


def _result_from_dict(payload: dict[str, Any]) -> FactResolutionResult:
    return FactResolutionResult(
        status=str(payload.get("status") or ""),
        targetKind=payload.get("targetKind"),
        canonicalTarget=dict(payload.get("canonicalTarget") or {}) or None,
        evidence=[dict(item) for item in list(payload.get("evidence") or []) if isinstance(item, dict)],
        query=payload.get("query"),
        reason=payload.get("reason"),
    )
