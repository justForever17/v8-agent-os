from __future__ import annotations

import json
import re
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
    elif loginish:
        operation = "login_boundary"
        domain = _domain_from_url(explicit_url) or "web"
        target_type = "login_boundary"
        risk = "credential_boundary"
    elif downloadish:
        operation = "download_or_install"
        domain = _domain_from_url(explicit_url) or "web"
        target_type = "download_page"
        risk = "download_side_effect"
    elif explicit_url:
        operation = "open_url"
        domain = _domain_from_url(explicit_url) or "web"
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
        "requiresFactResolution": bool((githubish and starish) or (operation in {"download_or_install", "unknown"} and not explicit_url)),
        "risk": risk,
    }


def resolve_goal_facts(
    goal: str,
    *,
    intent: dict[str, Any] | None = None,
    web_searcher: FactSearch | None = None,
) -> FactResolutionResult:
    resolved_intent = dict(intent or classify_goal(goal))
    explicit_url = str(resolved_intent.get("explicitUrl") or _first_url(goal) or "").strip()
    if explicit_url:
        return _resolve_explicit_url(explicit_url, resolved_intent)
    if resolved_intent.get("operation") == "star_repository":
        return _resolve_github_star(resolved_intent, web_searcher=web_searcher)
    if resolved_intent.get("operation") == "login_boundary":
        evidence = [{
            "kind": "login_boundary",
            "confidence": 0.72,
            "source": "goal_language",
            "reason": "login_or_sign_in_intent_detected",
        }]
        return FactResolutionResult(
            status="resolved",
            targetKind="login_boundary",
            canonicalTarget={"type": "login_boundary"},
            evidence=evidence,
            reason="login_boundary_detected",
        )
    if resolved_intent.get("operation") == "download_or_install":
        query = f"{resolved_intent.get('rawGoal') or goal} official download"
        candidate = _first_url_from_search_payload(web_searcher(query)) if web_searcher else None
        if candidate:
            return _resolve_explicit_url(candidate, {**resolved_intent, "operation": "download_or_install"})
        return FactResolutionResult(
            status="needs_fact_resolution",
            targetKind="download_page",
            evidence=[],
            query=query,
            reason="download_target_url_not_resolved",
        )
    return FactResolutionResult(
        status="not_required" if not resolved_intent.get("requiresFactResolution") else "needs_fact_resolution",
        targetKind=resolved_intent.get("targetType"),
        evidence=[],
        reason="no_canonical_target_required" if not resolved_intent.get("requiresFactResolution") else "canonical_target_not_resolved",
    )


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
        "type": intent.get("targetType") or "website_url",
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
