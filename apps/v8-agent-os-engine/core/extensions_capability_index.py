from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from core.v8_agent_os_paths import V8_AGENT_OS_HOME


_INDEX_VERSION = 1
_KNOWN_PUBLIC_DOMAINS = {
    "github.com",
    "gitlab.com",
    "bitbucket.org",
    "x.com",
    "twitter.com",
    "docs.github.com",
    "learn.microsoft.com",
    "developer.mozilla.org",
    "stackoverflow.com",
    "npmjs.com",
    "pypi.org",
}
_MCP_RISK_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("raw_http", ("http_request", "raw http", "fetch url", "request url", "webhook")),
    ("shell_or_process", ("shell", "command", "exec", "spawn", "process", "terminal")),
    ("filesystem_mutation", ("write_file", "delete", "remove", "rename", "move", "filesystem", "fs")),
    ("credential_or_secret", ("api key", "apikey", "secret", "token", "credential", "password", "cookie")),
    ("memory_mutation", ("mem_update", "mem_delete", "memory mutation", "delete memory")),
)


def extensions_cache_dir() -> Path:
    configured = str(os.getenv("V8_AGENT_OS_EXTENSIONS_CACHE_DIR") or "").strip()
    root = Path(configured).expanduser() if configured else V8_AGENT_OS_HOME / "cache" / "extensions"
    root.mkdir(parents=True, exist_ok=True)
    return root


def skill_inventory_cache_path() -> Path:
    configured = str(os.getenv("V8_AGENT_OS_SKILLS_CACHE_FILE") or "").strip()
    if configured:
        return Path(configured).expanduser()
    return extensions_cache_dir() / "skills_inventory_cache.json"


def legacy_skill_inventory_cache_path() -> Path:
    return V8_AGENT_OS_HOME / "skills_inventory_cache.json"


def extensions_runtime_cache_path() -> Path:
    configured = str(os.getenv("V8_AGENT_OS_EXTENSIONS_CACHE_FILE") or "").strip()
    if configured:
        return Path(configured).expanduser()
    return extensions_cache_dir() / "extensions_runtime_cache.json"


def legacy_extensions_runtime_cache_path() -> Path:
    return V8_AGENT_OS_HOME / "extensions_runtime_cache.json"


def capability_index_path() -> Path:
    configured = str(os.getenv("V8_AGENT_OS_EXTENSIONS_CAPABILITY_INDEX_FILE") or "").strip()
    if configured:
        return Path(configured).expanduser()
    return extensions_cache_dir() / "extensions_capability_index.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_hash(payload: Any) -> str:
    return hashlib.sha1(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _normalize_list(value: Any, *, limit: int = 24) -> list[str]:
    if isinstance(value, str):
        raw = re.split(r"[,\n/|]+", value)
    else:
        raw = list(value or [])
    seen: set[str] = set()
    items: list[str] = []
    for item in raw:
        text = str(item or "").strip()
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        items.append(text)
        if len(items) >= limit:
            break
    return items


def _safe_json_obj(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _extract_public_domains(text: str) -> list[str]:
    domains: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(r"https?://[^\s)>\]\"']+", str(text or ""), flags=re.IGNORECASE):
        try:
            host = urlparse(match.group(0)).hostname or ""
        except Exception:
            host = ""
        host = host.lower().removeprefix("www.")
        if host and host in _KNOWN_PUBLIC_DOMAINS and host not in seen:
            seen.add(host)
            domains.append(host)
    return domains


def _skill_safety_annotation(skill: dict[str, Any], *, record_reviews: bool) -> dict[str, Any]:
    try:
        from erc.safety_guardian import safety_guardian

        skill_id = str(skill.get("skillId") or "").strip()
        skill_name = str(skill.get("name") or skill.get("skillName") or "").strip()
        skill_root = str(skill.get("path") or skill.get("skillRoot") or "").strip()
        instruction_path = str(skill.get("instructionPath") or "").strip()
        review = safety_guardian.get_skill_safety_review(
            skill_id=skill_id,
            skill_name=skill_name,
            skill_root=skill_root,
            instruction_path=instruction_path,
        )
        if review:
            scan_payload = safety_guardian.skill_review_to_scan_payload(review)
        elif record_reviews:
            scan_payload = safety_guardian.assess_skill_directory(
                skill_name=skill_name or skill_id,
                skill_root=skill_root,
                instruction_path=instruction_path,
            )
            scan_payload["reviewMode"] = "rules_only_index"
            review = safety_guardian.record_skill_safety_review(
                skill_id=skill_id,
                skill_name=skill_name or skill_id,
                skill_root=skill_root,
                instruction_path=instruction_path,
                scan_payload=scan_payload,
                llm_review=None,
            )
            scan_payload = safety_guardian.skill_review_to_scan_payload(review)
        else:
            scan_payload = safety_guardian.assess_skill_directory(
                skill_name=skill_name or skill_id,
                skill_root=skill_root,
                instruction_path=instruction_path,
            )
    except Exception as exc:
        return {
            "verdict": "review",
            "effectiveVerdict": "review",
            "approvalRequired": True,
            "disabled": False,
            "trustScore": 35,
            "riskCodes": ["safety_index_unavailable"],
            "reasons": [f"无法完成 skill 安全索引：{exc}"],
            "fromLedger": False,
        }

    effective_verdict = str(scan_payload.get("effectiveVerdict") or scan_payload.get("verdict") or "review").strip().lower()
    static_verdict = str(scan_payload.get("staticVerdict") or scan_payload.get("verdict") or effective_verdict).strip().lower()
    user_override = str(scan_payload.get("userOverride") or "").strip().lower()
    risk_codes = _normalize_list(scan_payload.get("findingCategories"), limit=20)
    known_domains = _extract_public_domains(
        "\n".join(
            [
                str(skill.get("description") or ""),
                str(skill.get("instructions") or "")[:4000],
                json.dumps(scan_payload.get("flaggedFiles") or [], ensure_ascii=False),
            ]
        )
    )
    trust_score = int(scan_payload.get("skillTrustScore") or 0)
    disabled = bool(scan_payload.get("disabled")) or effective_verdict == "block"
    approval_required = (effective_verdict == "review" and user_override != "approved") or disabled
    return {
        "ledgerId": scan_payload.get("ledgerId") or scan_payload.get("auditId"),
        "verdict": effective_verdict,
        "effectiveVerdict": effective_verdict,
        "staticVerdict": static_verdict,
        "approvalRequired": bool(approval_required),
        "disabled": bool(disabled),
        "trustScore": trust_score,
        "riskCodes": risk_codes,
        "reasons": list(scan_payload.get("reasons") or [])[:6],
        "flaggedFileCount": len(list(scan_payload.get("flaggedFiles") or [])),
        "knownPublicDomains": known_domains,
        "userOverride": user_override or None,
        "fromLedger": bool(scan_payload.get("fromLedger")),
        "contentHash": scan_payload.get("contentHash"),
    }


def annotate_skill_entries(
    skills: list[dict[str, Any]] | dict[str, dict[str, Any]],
    *,
    record_reviews: bool = True,
) -> list[dict[str, Any]] | dict[str, dict[str, Any]]:
    is_mapping = isinstance(skills, dict)
    entries = list(skills.values()) if is_mapping else list(skills or [])
    annotated: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        next_entry = dict(entry)
        existing_safety = next_entry.get("safety") if isinstance(next_entry.get("safety"), dict) else None
        next_entry["safety"] = dict(existing_safety) if existing_safety else _skill_safety_annotation(next_entry, record_reviews=record_reviews)
        annotated.append(next_entry)
    if is_mapping:
        return {str(item.get("skillId") or item.get("path") or item.get("name") or index): item for index, item in enumerate(annotated)}
    return annotated


def _skill_index_entry(skill: dict[str, Any]) -> dict[str, Any]:
    capability_profile = _safe_json_obj(skill.get("capabilityProfile"))
    theme_profile = _safe_json_obj(skill.get("themeProfile"))
    capability_tags = _safe_json_obj(skill.get("capabilityTags"))
    aliases = _normalize_list(
        [
            *list(skill.get("aliases") or []),
            *list(skill.get("triggers") or []),
            *list(skill.get("keywords") or []),
            *list(skill.get("tags") or []),
            *list(capability_tags.get("languageAliases") or []),
        ],
        limit=64,
    )
    return {
        "skillId": str(skill.get("skillId") or "").strip(),
        "name": str(skill.get("name") or skill.get("skillName") or "").strip(),
        "manifestKey": str(skill.get("manifestKey") or skill.get("instructionPath") or "").strip(),
        "contentHash": str(skill.get("contentHash") or (skill.get("safety") or {}).get("contentHash") or "").strip(),
        "manifestHash": str(skill.get("manifestHash") or "").strip(),
        "sourceType": str(skill.get("sourceType") or "").strip(),
        "visibility": str(skill.get("visibility") or "").strip(),
        "rootPath": str(skill.get("rootPath") or skill.get("path") or "").strip(),
        "instructionPath": str(skill.get("instructionPath") or "").strip(),
        "aliasSnapshot": dict(skill.get("aliasSnapshot") or {}),
        "aliases": aliases,
        "prefilter": {
            "skillClass": capability_profile.get("skillClass"),
            "artifactTypes": list(capability_profile.get("primaryArtifactTypes") or [])[:4],
            "operations": list(capability_profile.get("primaryOperations") or [])[:6],
            "themes": list(theme_profile.get("primaryThemes") or [])[:6],
            "runtimeAffinity": list(capability_tags.get("runtimeAffinity") or [])[:6],
        },
        "safety": dict(skill.get("safety") or {}),
        "approvalRequired": bool((skill.get("safety") or {}).get("approvalRequired")),
    }


def _mcp_risk_codes(server_name: str, tools: list[dict[str, Any]], target: str) -> list[str]:
    corpus = " ".join(
        [
            str(server_name or ""),
            str(target or ""),
            *[
                " ".join([str(tool.get("name") or ""), str(tool.get("description") or "")])
                for tool in tools
                if isinstance(tool, dict)
            ],
        ]
    ).lower()
    risks: list[str] = []
    for code, needles in _MCP_RISK_PATTERNS:
        if any(needle in corpus for needle in needles):
            risks.append(code)
    return risks


def _mcp_index_entry(server: dict[str, Any]) -> dict[str, Any]:
    tools = [dict(tool) for tool in list(server.get("tools") or []) if isinstance(tool, dict)]
    target = str(server.get("target") or "").strip()
    server_name = str(server.get("name") or "").strip()
    risk_codes = _mcp_risk_codes(server_name, tools, target)
    return {
        "serverName": server_name,
        "status": str(server.get("status") or "").strip(),
        "transport": str(server.get("transport") or "").strip(),
        "targetHash": hashlib.sha1(target.encode("utf-8", errors="ignore")).hexdigest() if target else "",
        "configHash": _stable_hash({"transport": server.get("transport"), "target": target, "disabled": server.get("disabled")}),
        "toolSchemaHash": _stable_hash(
            [
                {
                    "name": str(tool.get("name") or ""),
                    "description": str(tool.get("description") or ""),
                }
                for tool in tools
            ]
        ),
        "toolCount": len(tools),
        "appsSupported": bool(server.get("appsSupported")),
        "appToolCount": int(server.get("appToolCount") or 0),
        "uiResourceCount": int(server.get("uiResourceCount") or 0),
        "prefilterFields": {
            "serverName": server_name,
            "toolNames": [str(tool.get("name") or "").strip() for tool in tools if str(tool.get("name") or "").strip()],
            "descriptions": [str(tool.get("description") or "").strip() for tool in tools if str(tool.get("description") or "").strip()][:8],
            "riskCodes": risk_codes,
        },
        "tools": [
            {
                "name": str(tool.get("name") or "").strip(),
                "description": str(tool.get("description") or "").strip()[:240],
            }
            for tool in tools
        ],
        "riskCodes": risk_codes,
        "trustScore": max(0, 100 - len(risk_codes) * 15),
        "approvalRequired": False,
    }


def _lexicon_snapshot(lexicon_state: dict[str, Any] | None) -> dict[str, Any]:
    state = dict(lexicon_state or {})
    return {
        "signature": str(state.get("signature") or "lexicon:unknown"),
        "coreSignature": str(state.get("coreSignature") or ""),
        "marketSignature": str(state.get("marketSignature") or ""),
        "locales": list(state.get("locales") or []),
        "marketLocales": list(state.get("marketLocales") or []),
        "loadErrors": list(state.get("loadErrors") or []),
        "marketLoadErrors": list(state.get("marketLoadErrors") or []),
        "querySynonyms": state.get("querySynonyms") or {},
        "artifactIntentSynonyms": state.get("artifactIntentSynonyms") or {},
        "operationIntentSynonyms": state.get("operationIntentSynonyms") or {},
        "primaryThemeSynonyms": state.get("primaryThemeSynonyms") or {},
        "secondaryThemeSynonyms": state.get("secondaryThemeSynonyms") or {},
    }


def write_capability_index(
    *,
    skills: list[dict[str, Any]],
    mcp_servers: list[dict[str, Any]] | None = None,
    lexicon_state: dict[str, Any] | None = None,
    source: str = "extensions_runtime",
) -> dict[str, Any]:
    annotated_skills = [dict(item) for item in annotate_skill_entries(skills, record_reviews=True)]  # type: ignore[arg-type]
    mcp_entries = [_mcp_index_entry(server) for server in list(mcp_servers or []) if isinstance(server, dict)]
    payload = {
        "version": _INDEX_VERSION,
        "updatedAt": _now_iso(),
        "source": source,
        "summary": {
            "skillCount": len(annotated_skills),
            "mcpServerCount": len(mcp_entries),
            "mcpToolCount": sum(int(item.get("toolCount") or 0) for item in mcp_entries),
            "approvalRequiredSkillCount": sum(1 for item in annotated_skills if (item.get("safety") or {}).get("approvalRequired")),
            "disabledSkillCount": sum(1 for item in annotated_skills if (item.get("safety") or {}).get("disabled")),
        },
        "skills": [_skill_index_entry(skill) for skill in annotated_skills],
        "mcp": {"servers": mcp_entries},
        "aliases": _lexicon_snapshot(lexicon_state),
    }
    path = capability_index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def load_capability_index() -> dict[str, Any] | None:
    path = capability_index_path()
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None
