from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse


_PYTHON_DOC_VERSION_RE = re.compile(r"(?:3(?:\.\d+)?|dev)", re.IGNORECASE)
_LOCALE_RE = re.compile(r"[a-z]{2,3}(?:-[a-z]{2})?", re.IGNORECASE)
_QUESTION_VERSION_RE = re.compile(r"(?<!\d)(?:python\s*)?(3\.\d+)(?!\d)", re.IGNORECASE)
_KNOWN_DOC_LOCALES = {
    "ar",
    "de",
    "en",
    "es",
    "fr",
    "id",
    "it",
    "ja",
    "ko",
    "pl",
    "pt",
    "pt-br",
    "ru",
    "tr",
    "uk",
    "vi",
    "zh",
    "zh-cn",
    "zh-tw",
}


def canonical_source_url(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw)
        path = parsed.path.rstrip("/") or "/"
        return parsed._replace(
            scheme=parsed.scheme.lower(),
            netloc=parsed.netloc.lower(),
            path=path,
            fragment="",
        ).geturl()
    except Exception:
        return raw.split("#", 1)[0].rstrip("/")


def _python_doc_parts(value: Any) -> tuple[str, str, str] | None:
    try:
        parsed = urlparse(canonical_source_url(value))
    except Exception:
        return None
    host = (parsed.hostname or "").lower().removeprefix("www.")
    if host != "docs.python.org":
        return None
    parts = [part for part in parsed.path.split("/") if part]
    locale = "en"
    if len(parts) >= 2 and _LOCALE_RE.fullmatch(parts[0]):
        locale = parts.pop(0).lower()
    version = ""
    if parts and _PYTHON_DOC_VERSION_RE.fullmatch(parts[0]):
        version = parts.pop(0).lower()
    return "/".join(parts).lower(), version, locale


def _question_versions(question: Any) -> set[str]:
    return {match.lower() for match in _QUESTION_VERSION_RE.findall(str(question or ""))}


def _question_preserves_locale(question: Any) -> bool:
    question_text = str(question or "").lower()
    return any(
        term in question_text
        for term in (
            "translation",
            "translated",
            "localization",
            "locale",
            "language version",
            "翻译",
            "本地化",
            "语言版本",
        )
    )


def _localized_product_doc_parts(value: Any) -> tuple[str, str, str] | None:
    try:
        parsed = urlparse(canonical_source_url(value))
    except Exception:
        return None
    host = (parsed.hostname or "").lower().removeprefix("www.")
    if host not in {"docs.anthropic.com", "docs.github.com", "code.claude.com"}:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    locale = "en"
    if host in {"docs.anthropic.com", "docs.github.com"}:
        if parts and parts[0].lower() in _KNOWN_DOC_LOCALES:
            locale = parts.pop(0).lower()
        if (
            host == "docs.github.com"
            and parts
            and parts[0].lower() == "enterprise-cloud@latest"
        ):
            parts.pop(0)
    elif len(parts) >= 2 and parts[0].lower() == "docs" and parts[1].lower() in _KNOWN_DOC_LOCALES:
        locale = parts.pop(1).lower()
    return host, "/".join(part.lower() for part in parts), locale


def research_document_identity(value: Any, *, question: Any = "") -> str:
    canonical = canonical_source_url(value)
    if not canonical:
        return ""
    python_doc = _python_doc_parts(canonical)
    if python_doc:
        path, version, locale = python_doc
        identity = f"python-doc:{path}"
        versions = _question_versions(question)
        if versions:
            requested_version = version if version in versions else (next(iter(versions)) if version == "3" and len(versions) == 1 else "")
            if requested_version:
                identity += f"|version:{requested_version}"
        question_text = str(question or "").lower()
        if any(term in question_text for term in ("translation", "localization", "locale", "翻译", "本地化", "语言版本")):
            identity += f"|locale:{locale}"
        return identity
    localized_product_doc = _localized_product_doc_parts(canonical)
    if localized_product_doc:
        host, path, locale = localized_product_doc
        identity = f"localized-product-doc:{host}:{path}"
        if _question_preserves_locale(question):
            identity += f"|locale:{locale}"
        return identity
    try:
        parsed = urlparse(canonical)
        host = (parsed.hostname or "").lower().removeprefix("www.")
        if host == "packaging.python.org":
            parts = [part.lower() for part in parsed.path.split("/") if part]
            if len(parts) >= 2 and _LOCALE_RE.fullmatch(parts[0]) and parts[1] in {"latest", "stable"}:
                parts = parts[2:]
            elif parts and parts[0] in {"latest", "stable"}:
                parts = parts[1:]
            return f"python-packaging-doc:{'/'.join(parts)}"
        if host == "peps.python.org" or host == "python.org" or host.endswith(".python.org"):
            pep_match = re.search(r"/(?:dev/peps/)?pep-0*(\d+)(?:/|$)", parsed.path, re.IGNORECASE)
            if pep_match:
                return f"python-pep:{int(pep_match.group(1))}"
        return parsed._replace(params="", query="", fragment="").geturl()
    except Exception:
        return canonical.split("?", 1)[0]


def research_document_priority(value: Any, *, question: Any = "") -> int:
    canonical = canonical_source_url(value)
    python_doc = _python_doc_parts(canonical)
    if python_doc:
        _, version, locale = python_doc
        score = 0
        versions = _question_versions(question)
        if version in versions:
            score += 600
        elif version == "3":
            score += 300
        elif re.fullmatch(r"3\.\d+", version):
            try:
                score += min(199, int(version.split(".", 1)[1]))
            except (TypeError, ValueError):
                pass
        elif version == "dev":
            score -= 100
        if locale == "en":
            score += 100
        else:
            score -= 300
        return score + 5
    localized_product_doc = _localized_product_doc_parts(canonical)
    if localized_product_doc:
        return 105 if localized_product_doc[2] == "en" else -295
    return 5 if canonical and not urlparse(canonical).query else 0


def research_source_is_navigation(value: Any, *, title: Any = "") -> bool:
    canonical = canonical_source_url(value)
    python_doc = _python_doc_parts(canonical)
    if python_doc:
        path = python_doc[0]
        if path in {
            "",
            "index.html",
            "contents.html",
            "genindex.html",
            "py-modindex.html",
            "search.html",
            "library",
            "library/index.html",
        }:
            return True
    try:
        parsed = urlparse(canonical)
        host = (parsed.hostname or "").lower().removeprefix("www.")
        path = parsed.path.rstrip("/").lower()
    except Exception:
        host = ""
        path = ""
    normalized_title = re.sub(r"\s+", " ", str(title or "")).strip().lower()
    if host == "peps.python.org" and (path in {"", "/"} or re.fullmatch(r"/pep-0*0", path)):
        return True
    return bool(
        "index of python enhancement proposals" in normalized_title
        or re.match(r"^pep\s+0\b", normalized_title)
    )
