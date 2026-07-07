from __future__ import annotations

import hashlib
import html
import json
import os
import re
import time
import base64
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse

import httpx

from core.mcp_config_service import McpConfigValidationError, install_mcp_server_config
from core.skills_install_service import SkillInstallValidationError, install_skill_from_command
from core.storage import storage
from core.v8_agent_os_paths import V8_AGENT_OS_HOME


_CACHE_TTL_SECONDS = 30 * 60
_HTTP_TIMEOUT_SECONDS = 20.0
_SKILLS_SEARCH_URL = "https://skills.sh/api/search"
_SKILLS_DOWNLOAD_URL = "https://skills.sh/api/download"
_SKILLS_HOME_URL = "https://skills.sh/"
_GITHUB_MCP_URL = "https://github.com/mcp"
_USER_AGENT = "v8-agent-os-admin-extensions-store/1.0"
_SKILL_SOURCE_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SKILL_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
_INPUT_PLACEHOLDER_PATTERN = re.compile(r"\$\{input:([A-Za-z0-9_.-]+)\}")
_ENV_PLACEHOLDER_PATTERN = re.compile(r"\$\{env:([A-Za-z0-9_.-]+)\}")
_BRACE_PLACEHOLDER_PATTERN = re.compile(r"\{([A-Za-z][A-Za-z0-9_.-]*)\}")
_SECRET_HINT_PATTERN = re.compile(r"(api[_-]?key|apikey|token|secret|password|authorization|bearer|pat|credential)", re.I)


class ExtensionStoreError(ValueError):
    def __init__(self, code: str, message: str, *, status_code: int = 400, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}

    def to_payload(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


def _cache_dir() -> Path:
    configured = str(os.getenv("V8_AGENT_OS_EXTENSIONS_STORE_CACHE_DIR") or "").strip()
    root = Path(configured).expanduser() if configured else V8_AGENT_OS_HOME / "cache" / "extensions_store"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _cache_path(name: str) -> Path:
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip(".-") or "store"
    return _cache_dir() / f"{safe_name}.json"


def _cache_key(prefix: str, value: str) -> str:
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}"


def _read_cache(name: str, *, allow_stale: bool = False) -> tuple[Any | None, str]:
    path = _cache_path(name)
    if not path.exists():
        return None, "miss"
    try:
        envelope = json.loads(path.read_text(encoding="utf-8"))
        cached_at = float(envelope.get("cachedAt") or 0)
        if not allow_stale and time.time() - cached_at > _CACHE_TTL_SECONDS:
            return None, "stale"
        return envelope.get("payload"), "cached"
    except Exception:
        return None, "invalid"


def _write_cache(name: str, payload: Any) -> None:
    path = _cache_path(name)
    path.write_text(
        json.dumps({"cachedAt": time.time(), "payload": payload}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _fetch_text(url: str) -> str:
    with httpx.Client(timeout=_HTTP_TIMEOUT_SECONDS, follow_redirects=True, headers={"User-Agent": _USER_AGENT}) as client:
        response = client.get(url)
        response.raise_for_status()
        return response.text


def _fetch_json(url: str) -> dict[str, Any]:
    with httpx.Client(timeout=_HTTP_TIMEOUT_SECONDS, follow_redirects=True, headers={"User-Agent": _USER_AGENT}) as client:
        response = client.get(url)
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}


def _stable_hash(payload: Any) -> str:
    return hashlib.sha1(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _decode_jsonish_text(value: str) -> str:
    decoded = html.unescape(str(value or ""))
    replacements = {
        "\\u0026": "&",
        "\\u003d": "=",
        "\\u003D": "=",
        "\\u002F": "/",
        "\\/": "/",
        "\\u003c": "<",
        "\\u003C": "<",
        "\\u003e": ">",
        "\\u003E": ">",
        "\\u0022": '"',
        "\\u0027": "'",
    }
    for old, new in replacements.items():
        decoded = decoded.replace(old, new)
    return decoded


def _extract_json_object_at(text: str, start: int) -> str | None:
    if start < 0 or start >= len(text) or text[start] != "{":
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def _normalize_limit(limit: int | None, *, default: int = 24, maximum: int = 60) -> int:
    try:
        parsed = int(limit or default)
    except Exception:
        parsed = default
    return max(1, min(parsed, maximum))


def _installed_skill_ids() -> set[str]:
    root = Path.home() / ".agents" / "skills"
    if not root.exists():
        return set()
    installed: set[str] = set()
    for child in root.iterdir():
        if child.is_dir() and (child / "SKILL.md").exists():
            installed.add(child.name.lower())
    return installed


def _installed_mcp_server_names() -> set[str]:
    existing = storage.get_mcp_config() or {"mcpServers": {}}
    servers = existing.get("mcpServers", {})
    if not isinstance(servers, dict):
        return set()
    return {str(name).strip().lower() for name in servers if str(name).strip()}


def _skill_detail_url(source: str, skill_id: str) -> str:
    return f"https://skills.sh/{source.strip('/')}/{skill_id}"


def _skill_download_url(source: str, skill_id: str) -> str:
    owner, repo = source.split("/", 1)
    return f"{_SKILLS_DOWNLOAD_URL}/{quote(owner)}/{quote(repo)}/{quote(skill_id)}"


def _strip_skill_frontmatter(markdown: str) -> tuple[dict[str, str], str]:
    text = str(markdown or "").replace("\r\n", "\n")
    if not text.startswith("---\n"):
        return {}, text.strip()
    end = text.find("\n---", 4)
    if end < 0:
        return {}, text.strip()
    raw_frontmatter = text[4:end]
    body = text[text.find("\n", end + 1) + 1 :]
    meta: dict[str, str] = {}
    for line in raw_frontmatter.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip().strip("\"'")
    return meta, body.strip()


def parse_skill_download_response(payload: dict[str, Any], *, source: str, skill_id: str) -> dict[str, Any]:
    files = payload.get("files") if isinstance(payload, dict) else []
    skill_md = ""
    if isinstance(files, list):
        for file_item in files:
            if not isinstance(file_item, dict):
                continue
            path = str(file_item.get("path") or "").replace("\\", "/").lower()
            if path.endswith("skill.md"):
                skill_md = str(file_item.get("contents") or "")
                break
    meta, body = _strip_skill_frontmatter(skill_md)
    name = str(meta.get("name") or skill_id).strip()
    description = str(meta.get("description") or "").strip()
    if not description:
        for line in body.splitlines():
            candidate = line.strip().strip("# ").strip()
            if candidate and not candidate.startswith("```"):
                description = candidate[:240]
                break
    return {
        "id": f"{source}@{skill_id}",
        "kind": "skill",
        "provider": "skills.sh",
        "source": source,
        "skillId": skill_id,
        "name": name,
        "title": name,
        "description": description,
        "markdown": body,
        "detailUrl": _skill_detail_url(source, skill_id),
        "hash": str(payload.get("hash") or "") if isinstance(payload, dict) else "",
    }


def _skill_detail_cache_name(source: str, skill_id: str) -> str:
    return _cache_key("skill-detail", f"{source}@{skill_id}")


def get_store_skill_detail(*, source: str, skill_id: str, refresh: bool = False) -> dict[str, Any]:
    normalized_source = str(source or "").strip()
    normalized_skill_id = str(skill_id or "").strip()
    if not _SKILL_SOURCE_PATTERN.fullmatch(normalized_source):
        raise ExtensionStoreError("invalid_skill_source", "Skills 详情请求缺少合法的 owner/repo 来源。")
    if not _SKILL_ID_PATTERN.fullmatch(normalized_skill_id):
        raise ExtensionStoreError("invalid_skill_id", "Skills 详情请求缺少合法的 skillId。")
    cache_name = _skill_detail_cache_name(normalized_source, normalized_skill_id)
    if not refresh:
        cached, _ = _read_cache(cache_name)
        if isinstance(cached, dict):
            return {**cached, "freshness": "cached"}
    try:
        detail = parse_skill_download_response(
            _fetch_json(_skill_download_url(normalized_source, normalized_skill_id)),
            source=normalized_source,
            skill_id=normalized_skill_id,
        )
        _write_cache(cache_name, detail)
        return {**detail, "freshness": "live"}
    except Exception as exc:
        cached, _ = _read_cache(cache_name, allow_stale=True)
        if isinstance(cached, dict):
            return {**cached, "freshness": "cached"}
        raise ExtensionStoreError(
            "skill_detail_unavailable",
            "Skill 详情暂时不可用，请稍后重试。",
            status_code=502,
            details={"error": str(exc)},
        ) from exc


def _normalize_skill_item(raw: dict[str, Any]) -> dict[str, Any] | None:
    source = str(raw.get("source") or "").strip()
    skill_id = str(raw.get("skillId") or raw.get("skill_id") or raw.get("name") or "").strip()
    name = str(raw.get("name") or skill_id).strip()
    if not _SKILL_SOURCE_PATTERN.fullmatch(source) or not _SKILL_ID_PATTERN.fullmatch(skill_id):
        return None
    try:
        installs = int(raw.get("installs") or 0)
    except Exception:
        installs = 0
    weekly = raw.get("weeklyInstalls") or raw.get("weekly_installs") or []
    if not isinstance(weekly, list):
        weekly = []
    return {
        "id": f"{source}@{skill_id}",
        "kind": "skill",
        "provider": "skills.sh",
        "name": name,
        "title": name,
        "source": source,
        "skillId": skill_id,
        "installs": installs,
        "description": str(raw.get("description") or "").strip(),
        "weeklyInstalls": [int(value or 0) for value in weekly if isinstance(value, (int, float)) or str(value).isdigit()],
        "detailUrl": _skill_detail_url(source, skill_id),
        "installCommand": f"npx --yes skills add {source}@{skill_id} -g",
    }


def _dedupe_skill_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for item in items:
        normalized = _normalize_skill_item(item)
        if not normalized:
            continue
        key = normalized["id"].lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(normalized)
    return result


def parse_skills_home_items(page_html: str) -> list[dict[str, Any]]:
    text = html.unescape(str(page_html or ""))
    items: list[dict[str, Any]] = []
    patterns = [
        re.compile(
            r'\{"source":"(?P<source>[^"]+)","skillId":"(?P<skillId>[^"]+)","name":"(?P<name>[^"]+)",'
            r'"installs":(?P<installs>\d+),"weeklyInstalls":\[(?P<weekly>[0-9,\s]*)\]\}'
        ),
        re.compile(
            r'\{\\"source\\":\\"(?P<source>[^"\\]+)\\",\\"skillId\\":\\"(?P<skillId>[^"\\]+)\\",\\"name\\":\\"(?P<name>[^"\\]+)\\",'
            r'\\"installs\\":(?P<installs>\d+),\\"weeklyInstalls\\":\[(?P<weekly>[0-9,\s]*)\]\}'
        ),
    ]
    for pattern in patterns:
        for match in pattern.finditer(text):
            weekly = [int(part) for part in re.findall(r"\d+", match.group("weekly") or "")]
            items.append(
                {
                    "source": match.group("source"),
                    "skillId": match.group("skillId"),
                    "name": match.group("name"),
                    "installs": int(match.group("installs")),
                    "weeklyInstalls": weekly,
                }
            )
    return _dedupe_skill_items(items)


def parse_skills_search_response(payload: dict[str, Any]) -> list[dict[str, Any]]:
    skills = payload.get("skills") if isinstance(payload, dict) else []
    if not isinstance(skills, list):
        return []
    return _dedupe_skill_items([item for item in skills if isinstance(item, dict)])


def _enrich_skill_summary(item: dict[str, Any]) -> dict[str, Any]:
    if str(item.get("description") or "").strip():
        return item
    source = str(item.get("source") or "").strip()
    skill_id = str(item.get("skillId") or "").strip()
    cache_name = _skill_detail_cache_name(source, skill_id)
    cached, _ = _read_cache(cache_name)
    if isinstance(cached, dict) and str(cached.get("description") or "").strip():
        next_item = dict(item)
        next_item["description"] = str(cached.get("description") or "").strip()
        return next_item
    try:
        detail = get_store_skill_detail(source=source, skill_id=skill_id)
    except Exception:
        return item
    next_item = dict(item)
    next_item["description"] = str(detail.get("description") or "").strip()
    return next_item


def _decorate_skill_items(items: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    installed = _installed_skill_ids()
    sorted_items = sorted(items, key=lambda item: int(item.get("installs") or 0), reverse=True)
    selected_items = [dict(item) for item in sorted_items[:limit]]
    if selected_items:
        with ThreadPoolExecutor(max_workers=min(8, len(selected_items))) as executor:
            enriched_items = list(executor.map(_enrich_skill_summary, selected_items))
    else:
        enriched_items = []
    decorated: list[dict[str, Any]] = []
    for next_item in enriched_items:
        next_item["installed"] = str(next_item.get("skillId") or "").lower() in installed
        decorated.append(next_item)
    return decorated


def list_store_skills(*, query: str = "", limit: int = 24, refresh: bool = False) -> dict[str, Any]:
    normalized_query = str(query or "").strip()
    safe_limit = _normalize_limit(limit)
    warnings: list[str] = []
    if len(normalized_query) >= 2:
        params = urlencode({"q": normalized_query, "limit": str(safe_limit)})
        cache_name = _cache_key("skills-search", params)
        if not refresh:
            cached, state = _read_cache(cache_name)
            if cached is not None:
                return {
                    "provider": "skills.sh",
                    "sourceUrl": _SKILLS_HOME_URL,
                    "query": normalized_query,
                    "freshness": "cached",
                    "items": _decorate_skill_items(cached if isinstance(cached, list) else [], limit=safe_limit),
                    "warnings": [],
                }
        try:
            items = parse_skills_search_response(_fetch_json(f"{_SKILLS_SEARCH_URL}?{params}"))
            _write_cache(cache_name, items)
            freshness = "live"
        except Exception as exc:
            cached, _ = _read_cache(cache_name, allow_stale=True)
            if cached is None:
                raise ExtensionStoreError(
                    "skills_source_unavailable",
                    "Skills 商店暂时不可用，请稍后重试。",
                    status_code=502,
                    details={"error": str(exc)},
                ) from exc
            items = cached if isinstance(cached, list) else []
            freshness = "cached"
            warnings.append("当前展示上次可用的 Skills 结果。")
        return {
            "provider": "skills.sh",
            "sourceUrl": _SKILLS_HOME_URL,
            "query": normalized_query,
            "freshness": freshness,
            "items": _decorate_skill_items(items, limit=safe_limit),
            "warnings": warnings,
        }

    cache_name = "skills-home-popular"
    if not refresh:
        cached, _ = _read_cache(cache_name)
        if cached is not None:
            return {
                "provider": "skills.sh",
                "sourceUrl": _SKILLS_HOME_URL,
                "query": normalized_query,
                "freshness": "cached",
                "items": _decorate_skill_items(cached if isinstance(cached, list) else [], limit=safe_limit),
                "warnings": [],
            }
    try:
        items = parse_skills_home_items(_fetch_text(_SKILLS_HOME_URL))
        if not items:
            raise ValueError("skills.sh 首页没有解析到热门 skill 数据。")
        _write_cache(cache_name, items)
        freshness = "live"
    except Exception as exc:
        cached, _ = _read_cache(cache_name, allow_stale=True)
        if cached is None:
            raise ExtensionStoreError(
                "skills_home_unavailable",
                "Skills 商店暂时不可用，请稍后重试。",
                status_code=502,
                details={"error": str(exc)},
            ) from exc
        items = cached if isinstance(cached, list) else []
        freshness = "cached"
        warnings.append("当前展示上次可用的 Skills 结果。")
    return {
        "provider": "skills.sh",
        "sourceUrl": _SKILLS_HOME_URL,
        "query": normalized_query,
        "freshness": freshness,
        "items": _decorate_skill_items(items, limit=safe_limit),
        "warnings": warnings,
    }


def install_store_skill(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ExtensionStoreError("invalid_payload", "Skills 商店安装请求必须是 JSON 对象。")
    source = str(payload.get("source") or "").strip()
    skill_id = str(payload.get("skillId") or payload.get("skill_id") or "").strip()
    overwrite = bool(payload.get("overwrite", False))
    if not _SKILL_SOURCE_PATTERN.fullmatch(source):
        raise ExtensionStoreError("invalid_skill_source", "Skills 商店只允许安装 skills.sh 返回的 owner/repo 来源。")
    if not _SKILL_ID_PATTERN.fullmatch(skill_id):
        raise ExtensionStoreError("invalid_skill_id", "Skills 商店安装请求缺少合法的 skillId。")
    command = f"npx --yes skills add {source}@{skill_id} -g"
    if overwrite:
        command = f"{command} --overwrite"
    try:
        result = install_skill_from_command(command)
    except SkillInstallValidationError:
        raise
    except Exception as exc:
        raise ExtensionStoreError("skill_install_failed", str(exc), status_code=400) from exc
    result["store"] = {
        "provider": "skills.sh",
        "source": source,
        "skillId": skill_id,
        "detailUrl": _skill_detail_url(source, skill_id),
    }
    return result


def _server_name_from_mcp_name(value: str) -> str:
    last = str(value or "").strip().strip("/").split("/")[-1]
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "-", last).strip(".-")
    return normalized or "mcp-server"


def _normalize_mcp_card(raw: dict[str, Any]) -> dict[str, Any] | None:
    name = str(raw.get("name") or raw.get("name_with_owner") or raw.get("api_name") or raw.get("id") or "").strip().strip("/")
    if "/" not in name:
        return None
    repository = raw.get("repository") if isinstance(raw.get("repository"), dict) else {}
    url = str(raw.get("url") or repository.get("url") or "").strip()
    if not url.startswith("https://github.com/"):
        return None
    topics = raw.get("topics") if isinstance(raw.get("topics"), list) else []
    try:
        stars = int(raw.get("stargazer_count") or 0)
    except Exception:
        stars = 0
    server_name = _server_name_from_mcp_name(name)
    return {
        "id": name,
        "kind": "mcp",
        "provider": "github.com/mcp",
        "name": name,
        "title": str(raw.get("display_name") or name).strip(),
        "description": str(raw.get("description") or "").strip(),
        "repositoryUrl": url,
        "detailUrl": f"{_GITHUB_MCP_URL}/{quote(name, safe='/._-')}",
        "stars": stars,
        "avatarUrl": str(raw.get("owner_avatar_url") or raw.get("opengraph_image_url") or "").strip(),
        "language": str(raw.get("primary_language") or "").strip(),
        "license": str(raw.get("license") or "").strip(),
        "topics": [str(topic) for topic in topics[:8]],
        "updatedAt": str(raw.get("updated_at") or "").strip(),
        "serverName": server_name,
    }


def parse_github_mcp_cards(page_html: str) -> list[dict[str, Any]]:
    text = _decode_jsonish_text(page_html)
    cards: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in re.finditer(r'\{"id":"', text):
        object_text = _extract_json_object_at(text, match.start())
        if not object_text:
            continue
        try:
            raw = json.loads(object_text)
        except Exception:
            continue
        if not isinstance(raw, dict) or "repository" not in raw:
            continue
        card = _normalize_mcp_card(raw)
        if not card:
            continue
        key = str(card["id"]).lower()
        if key in seen:
            continue
        seen.add(key)
        cards.append(card)
    return cards


def _fuzzy_match_mcp(card: dict[str, Any], query: str) -> bool:
    normalized = query.strip().lower()
    if not normalized:
        return True
    haystack = " ".join(
        [
            str(card.get("name") or ""),
            str(card.get("title") or ""),
            str(card.get("description") or ""),
            str(card.get("language") or ""),
            " ".join(str(topic) for topic in card.get("topics") or []),
        ]
    ).lower()
    return all(token in haystack for token in re.split(r"\s+", normalized) if token)


def _decorate_mcp_cards(cards: list[dict[str, Any]], *, query: str, limit: int) -> list[dict[str, Any]]:
    installed = _installed_mcp_server_names()
    filtered = [card for card in cards if _fuzzy_match_mcp(card, query)]
    filtered.sort(key=lambda item: int(item.get("stars") or 0), reverse=True)
    decorated: list[dict[str, Any]] = []
    for card in filtered[:limit]:
        next_card = dict(card)
        next_card["installed"] = str(card.get("serverName") or "").lower() in installed
        decorated.append(next_card)
    return decorated


def list_store_mcp(*, query: str = "", limit: int = 24, refresh: bool = False) -> dict[str, Any]:
    normalized_query = str(query or "").strip()
    safe_limit = _normalize_limit(limit)
    warnings: list[str] = []
    cache_name = "github-mcp-list-v2"
    if not refresh:
        cached, _ = _read_cache(cache_name)
        if cached is not None:
            return {
                "provider": "github.com/mcp",
                "sourceUrl": _GITHUB_MCP_URL,
                "query": normalized_query,
                "freshness": "cached",
                "items": _decorate_mcp_cards(cached if isinstance(cached, list) else [], query=normalized_query, limit=safe_limit),
                "warnings": [],
            }
    try:
        cards = parse_github_mcp_cards(_fetch_text(_GITHUB_MCP_URL))
        if not cards:
            raise ValueError("GitHub MCP Registry 页面没有解析到卡片数据。")
        _write_cache(cache_name, cards)
        freshness = "live"
    except Exception as exc:
        cached, _ = _read_cache(cache_name, allow_stale=True)
        if cached is None:
            raise ExtensionStoreError(
                "github_mcp_source_unavailable",
                "MCP 商店暂时不可用，请稍后重试。",
                status_code=502,
                details={"error": str(exc)},
            ) from exc
        cards = cached if isinstance(cached, list) else []
        freshness = "cached"
        warnings.append("当前展示上次可用的 MCP 结果。")
    return {
        "provider": "github.com/mcp",
        "sourceUrl": _GITHUB_MCP_URL,
        "query": normalized_query,
        "freshness": freshness,
        "items": _decorate_mcp_cards(cards, query=normalized_query, limit=safe_limit),
        "warnings": warnings,
    }


def _is_secret_hint(*values: str) -> bool:
    return any(_SECRET_HINT_PATTERN.search(str(value or "")) for value in values)


def _placeholder_names(value: Any) -> list[str]:
    text = str(value or "")
    names: list[str] = []
    for pattern in (_INPUT_PLACEHOLDER_PATTERN, _ENV_PLACEHOLDER_PATTERN, _BRACE_PLACEHOLDER_PATTERN):
        for match in pattern.finditer(text):
            name = match.group(1)
            if name not in names:
                names.append(name)
    return names


def _input_definitions(payload: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    inputs = (payload or {}).get("inputs")
    if not isinstance(inputs, list):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for item in inputs:
        if not isinstance(item, dict):
            continue
        input_id = str(item.get("id") or "").strip()
        if input_id:
            result[input_id] = item
    return result


def _requirement(
    *,
    target: str,
    name: str,
    key: str,
    value_template: str,
    placeholder: str,
    input_defs: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    input_def = input_defs.get(placeholder) or {}
    label = str(input_def.get("description") or name or placeholder).strip()
    secret = bool(input_def.get("password")) or _is_secret_hint(target, name, placeholder, label, value_template)
    return {
        "key": key,
        "target": target,
        "name": name,
        "label": label,
        "placeholder": placeholder,
        "required": True,
        "secret": secret,
        "valueTemplate": value_template,
    }


def _requirements_from_config(config: dict[str, Any], *, input_defs: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    input_map = input_defs or {}
    requirements: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_requirement(req: dict[str, Any]) -> None:
        key = str(req.get("key") or "")
        if not key or key in seen:
            return
        seen.add(key)
        requirements.append(req)

    def scan_value(target: str, name: str, value: Any, key_prefix: str) -> None:
        text = str(value or "")
        placeholders = _placeholder_names(text)
        for placeholder in placeholders:
            add_requirement(
                _requirement(
                    target=target,
                    name=name,
                    key=f"{key_prefix}.{placeholder}",
                    value_template=text,
                    placeholder=placeholder,
                    input_defs=input_map,
                )
            )
        if not placeholders and _is_secret_hint(target, name) and (not text or "$" in text or "your_" in text.lower()):
            add_requirement(
                _requirement(
                    target=target,
                    name=name,
                    key=f"{key_prefix}.{name}",
                    value_template=text,
                    placeholder=name,
                    input_defs=input_map,
                )
            )

    url = str(config.get("url") or "")
    if url:
        for placeholder in _placeholder_names(url):
            add_requirement(
                _requirement(
                    target="url",
                    name=placeholder,
                    key=f"url.{placeholder}",
                    value_template=url,
                    placeholder=placeholder,
                    input_defs=input_map,
                )
            )
    env = config.get("env") if isinstance(config.get("env"), dict) else {}
    for name, value in env.items():
        scan_value("env", str(name), value, f"env.{name}")
    headers = config.get("headers") if isinstance(config.get("headers"), dict) else {}
    for name, value in headers.items():
        scan_value("header", str(name), value, f"header.{name}")
    args = config.get("args") if isinstance(config.get("args"), list) else []
    for index, value in enumerate(args):
        text = str(value or "")
        for placeholder in _placeholder_names(text):
            add_requirement(
                _requirement(
                    target="arg",
                    name=placeholder,
                    key=f"arg.{index}.{placeholder}",
                    value_template=text,
                    placeholder=placeholder,
                    input_defs=input_map,
                )
            )
    return requirements


def _transport_for_config(config: dict[str, Any]) -> str:
    raw_type = str(config.get("type") or config.get("transport") or "").strip().lower().replace("-", "_")
    if raw_type in {"http", "streamable_http"}:
        return "http"
    if raw_type == "sse":
        return "sse"
    if raw_type == "stdio":
        return "stdio"
    if str(config.get("url") or "").strip():
        return "http"
    return "stdio"


def _candidate_priority(config: dict[str, Any]) -> int:
    transport = _transport_for_config(config)
    if transport == "http":
        return 0
    if transport == "sse":
        return 1
    command = str(config.get("command") or "").strip().lower()
    if command in {"npx", "uvx"}:
        return 2
    if command == "docker":
        return 3
    return 4


def _candidate_label(config: dict[str, Any]) -> str:
    transport = _transport_for_config(config)
    if transport == "http":
        return "Remote HTTP"
    if transport == "sse":
        return "SSE"
    command = str(config.get("command") or "").strip()
    return f"{command or 'stdio'} stdio"


def _sanitize_server_config(config: dict[str, Any]) -> dict[str, Any]:
    server = deepcopy(config)
    server["type"] = _transport_for_config(server)
    server.pop("transport", None)
    server.pop("inputs", None)
    return server


def _candidate_from_config(
    *,
    server_name: str,
    config: dict[str, Any],
    source: str,
    input_defs: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    server_config = _sanitize_server_config(config)
    if not server_config.get("command") and not server_config.get("url"):
        return None
    candidate_hash = _stable_hash({"serverName": server_name, "config": server_config})[:12]
    env = server_config.get("env") if isinstance(server_config.get("env"), dict) else {}
    headers = server_config.get("headers") if isinstance(server_config.get("headers"), dict) else {}
    args = server_config.get("args") if isinstance(server_config.get("args"), list) else []
    return {
        "id": candidate_hash,
        "label": _candidate_label(server_config),
        "serverName": server_name,
        "transport": _transport_for_config(server_config),
        "source": source,
        "priority": _candidate_priority(server_config),
        "command": str(server_config.get("command") or ""),
        "url": str(server_config.get("url") or ""),
        "args": [str(arg) for arg in args[:16]],
        "envKeys": sorted(str(key) for key in env.keys()),
        "headerKeys": sorted(str(key) for key in headers.keys()),
        "requirements": _requirements_from_config(server_config, input_defs=input_defs),
        "_serverConfig": server_config,
    }


def _server_configs_from_payload(payload: dict[str, Any], *, default_server_name: str) -> list[tuple[str, dict[str, Any], dict[str, dict[str, Any]]]]:
    input_defs = _input_definitions(payload)
    result: list[tuple[str, dict[str, Any], dict[str, dict[str, Any]]]] = []
    if isinstance(payload.get("mcpServers"), dict):
        for name, config in payload["mcpServers"].items():
            if isinstance(config, dict):
                result.append((str(name or default_server_name).strip() or default_server_name, config, input_defs))
    if isinstance(payload.get("servers"), dict):
        for name, config in payload["servers"].items():
            if isinstance(config, dict):
                result.append((str(name or default_server_name).strip() or default_server_name, config, input_defs))
    if any(key in payload for key in ("command", "url", "type", "transport")):
        result.append((default_server_name, payload, input_defs))
    return result


def parse_mcp_install_redirect_candidates(detail_html: str, *, default_server_name: str) -> list[dict[str, Any]]:
    text = _decode_jsonish_text(detail_html)
    candidates: list[dict[str, Any]] = []
    url_pattern = re.compile(
        r"(?:https?|vscode):[^\"'<>\s)]*(?:redirect/mcp/install|mcp/install|install-mcp)[^\"'<>\s)]*",
        flags=re.I,
    )
    for match in url_pattern.finditer(text):
        raw_url = match.group(0).rstrip("\\")
        parsed = urlparse(raw_url)
        params = parse_qs(parsed.query)
        raw_config = (params.get("config") or [""])[0]
        if not raw_config:
            continue
        payload = _decode_install_config(raw_config)
        if not isinstance(payload, dict):
            continue
        name = str((params.get("name") or [""])[0] or default_server_name).strip() or default_server_name
        for server_name, config, input_defs in _server_configs_from_payload(payload, default_server_name=name):
            candidate = _candidate_from_config(
                server_name=server_name,
                config=config,
                source="vscode_install_link",
                input_defs=input_defs,
            )
            if candidate:
                candidates.append(candidate)
    return candidates


def _decode_install_config(raw_config: str) -> dict[str, Any] | None:
    decoded = unquote(str(raw_config or "").strip())
    if not decoded:
        return None
    try:
        payload = json.loads(decoded)
        return payload if isinstance(payload, dict) else None
    except Exception:
        pass
    try:
        padded = decoded + "=" * (-len(decoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("utf-8")).decode("utf-8"))
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def _readable_detail_text(detail_html: str) -> str:
    text = _decode_jsonish_text(detail_html)
    return text.replace("\\n", "\n").replace('\\"', '"')


def parse_mcp_readme_json_candidates(detail_html: str, *, default_server_name: str) -> list[dict[str, Any]]:
    text = _readable_detail_text(detail_html)
    payloads: list[dict[str, Any]] = []
    for match in re.finditer(r"```(?:json|jsonc)?\s*\n", text, flags=re.I):
        start = match.end()
        end = text.find("```", start)
        if end < 0:
            continue
        block = text[start:end].strip()
        try:
            payload = json.loads(block)
        except Exception:
            continue
        if isinstance(payload, dict):
            payloads.append(payload)
    for marker in ('"mcpServers"', '"servers"'):
        for match in re.finditer(marker, text):
            start = text.rfind("{", 0, match.start())
            object_text = _extract_json_object_at(text, start)
            if not object_text:
                continue
            try:
                payload = json.loads(object_text)
            except Exception:
                continue
            if isinstance(payload, dict):
                payloads.append(payload)

    candidates: list[dict[str, Any]] = []
    for payload in payloads:
        for server_name, config, input_defs in _server_configs_from_payload(payload, default_server_name=default_server_name):
            candidate = _candidate_from_config(
                server_name=server_name,
                config=config,
                source="readme_json",
                input_defs=input_defs,
            )
            if candidate:
                candidates.append(candidate)
    return _dedupe_candidates(candidates)


def _public_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in candidate.items() if not key.startswith("_") and key != "priority"}


def _dedupe_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for candidate in sorted(candidates, key=lambda item: (int(item.get("priority") or 99), str(item.get("label") or ""))):
        key = _stable_hash(candidate.get("_serverConfig") or candidate)
        if key in seen:
            continue
        seen.add(key)
        result.append(candidate)
    return result


def _mcp_detail_cache_name(mcp_id: str) -> str:
    return _cache_key("github-mcp-detail", mcp_id)


def get_store_mcp_detail(*, mcp_id: str, refresh: bool = False) -> dict[str, Any]:
    normalized_id = str(mcp_id or "").strip().strip("/")
    if not normalized_id or ".." in normalized_id or normalized_id.startswith(("http://", "https://")):
        raise ExtensionStoreError("invalid_mcp_id", "MCP 商店详情请求缺少合法的 GitHub MCP id。")
    detail_url = f"{_GITHUB_MCP_URL}/{quote(normalized_id, safe='/._-')}"
    cache_name = _mcp_detail_cache_name(normalized_id)
    warnings: list[str] = []
    if not refresh:
        cached, _ = _read_cache(cache_name)
        if isinstance(cached, dict):
            return {**cached, "freshness": "cached"}
    try:
        page_html = _fetch_text(detail_url)
        default_server_name = _server_name_from_mcp_name(normalized_id)
        candidates = _dedupe_candidates(
            [
                *parse_mcp_install_redirect_candidates(page_html, default_server_name=default_server_name),
                *parse_mcp_readme_json_candidates(page_html, default_server_name=default_server_name),
            ]
        )
        public_candidates = [_public_candidate(candidate) for candidate in candidates]
        if not public_candidates:
            warnings.append("该 MCP 条目没有解析到明确的一键安装配置。")
        payload = {
            "id": normalized_id,
            "provider": "github.com/mcp",
            "detailUrl": detail_url,
            "repositoryUrl": detail_url,
            "candidates": public_candidates,
            "canInstall": bool(public_candidates),
            "warnings": warnings,
        }
        _write_cache(cache_name, payload)
        return {**payload, "freshness": "live"}
    except Exception as exc:
        cached, _ = _read_cache(cache_name, allow_stale=True)
        if isinstance(cached, dict):
            cached_warnings = list(cached.get("warnings") or [])
            cached_warnings.append("当前展示上次可用的 MCP 详情。")
            return {**cached, "freshness": "cached", "warnings": cached_warnings}
        raise ExtensionStoreError(
            "github_mcp_detail_unavailable",
            "MCP 详情暂时不可用，请稍后重试。",
            status_code=502,
            details={"error": str(exc)},
        ) from exc


def _deep_replace(value: Any, replacements: dict[str, str]) -> Any:
    if isinstance(value, str):
        next_value = value
        for name, replacement in replacements.items():
            next_value = next_value.replace(f"${{input:{name}}}", replacement)
            next_value = next_value.replace(f"${{env:{name}}}", replacement)
            next_value = next_value.replace(f"{{{name}}}", replacement)
        return next_value
    if isinstance(value, list):
        return [_deep_replace(item, replacements) for item in value]
    if isinstance(value, dict):
        return {key: _deep_replace(item, replacements) for key, item in value.items()}
    return value


def _candidate_by_id(mcp_id: str, candidate_id: str) -> dict[str, Any]:
    normalized_id = str(mcp_id or "").strip().strip("/")
    detail_url = f"{_GITHUB_MCP_URL}/{quote(normalized_id, safe='/._-')}"
    page_html = _fetch_text(detail_url)
    default_server_name = _server_name_from_mcp_name(normalized_id)
    candidates = _dedupe_candidates(
        [
            *parse_mcp_install_redirect_candidates(page_html, default_server_name=default_server_name),
            *parse_mcp_readme_json_candidates(page_html, default_server_name=default_server_name),
        ]
    )
    for candidate in candidates:
        if str(candidate.get("id") or "") == candidate_id:
            return candidate
    raise ExtensionStoreError("mcp_candidate_not_found", "没有找到可安装的 MCP 候选配置。", status_code=404)


def _compiled_mcp_server_config(candidate: dict[str, Any], values: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    config = deepcopy(candidate.get("_serverConfig") or {})
    if not isinstance(config, dict):
        raise ExtensionStoreError("invalid_mcp_candidate", "MCP 候选配置无效。")
    requirements = candidate.get("requirements") or []
    replacements: dict[str, str] = {}
    direct_env: dict[str, str] = {}
    direct_headers: dict[str, str] = {}
    for requirement in requirements:
        if not isinstance(requirement, dict):
            continue
        key = str(requirement.get("key") or "").strip()
        placeholder = str(requirement.get("placeholder") or requirement.get("name") or "").strip()
        raw_value = values.get(key, values.get(placeholder, ""))
        value = str(raw_value or "").strip()
        if requirement.get("required") and not value:
            raise ExtensionStoreError(
                "missing_mcp_requirement",
                f"请填写 `{requirement.get('label') or placeholder or key}`。",
                details={"field": key},
            )
        if placeholder:
            replacements[placeholder] = value
        target = str(requirement.get("target") or "")
        name = str(requirement.get("name") or "").strip()
        template = str(requirement.get("valueTemplate") or "")
        if target == "env" and name and not _placeholder_names(template):
            direct_env[name] = value
        if target == "header" and name and not _placeholder_names(template):
            direct_headers[name] = value
    config = _deep_replace(config, replacements)
    if direct_env:
        config.setdefault("env", {})
        if isinstance(config["env"], dict):
            config["env"].update(direct_env)
    if direct_headers:
        config.setdefault("headers", {})
        if isinstance(config["headers"], dict):
            config["headers"].update(direct_headers)
    config = _sanitize_server_config(config)
    return str(candidate.get("serverName") or "mcp-server"), config


def install_store_mcp(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ExtensionStoreError("invalid_payload", "MCP 商店安装请求必须是 JSON 对象。")
    mcp_id = str(payload.get("id") or payload.get("mcpId") or "").strip().strip("/")
    candidate_id = str(payload.get("candidateId") or "").strip()
    values = payload.get("values") if isinstance(payload.get("values"), dict) else {}
    if not mcp_id or not candidate_id:
        raise ExtensionStoreError("invalid_mcp_install_payload", "MCP 商店安装请求缺少 id 或 candidateId。")
    candidate = _candidate_by_id(mcp_id, candidate_id)
    server_name, server_config = _compiled_mcp_server_config(candidate, values)
    try:
        result = install_mcp_server_config(
            {"mcpServers": {server_name: server_config}},
            refresh_reason="extensions_store_mcp_install",
        )
    except McpConfigValidationError:
        raise
    except Exception as exc:
        raise ExtensionStoreError("mcp_install_failed", str(exc), status_code=400) from exc
    result["store"] = {
        "provider": "github.com/mcp",
        "id": mcp_id,
        "candidateId": candidate_id,
        "serverName": server_name,
        "detailUrl": f"{_GITHUB_MCP_URL}/{quote(mcp_id, safe='/._-')}",
    }
    return result
