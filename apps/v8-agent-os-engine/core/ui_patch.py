from __future__ import annotations

import difflib
import html
import hashlib
import importlib.util
import json
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, unquote, urlparse
from urllib.request import Request, urlopen

from core.storage import storage
from core.workbench_files import workbench_file_service
from core.workspace_authority import workspace_authority_service


MAX_SOURCE_BYTES = 2 * 1024 * 1024
MAX_SELECTION_RULES = 120
MAX_CSS_SCAN_FILES = 400
PREVIEW_START_TIMEOUT_SECONDS = 6.0
PROJECT_DEV_START_TIMEOUT_SECONDS = 25.0
PROJECT_PROBE_TIMEOUT_SECONDS = 0.8
PREVIEW_SESSION_TTL_SECONDS = 4 * 60 * 60
SELECTION_TTL_SECONDS = 30 * 60

ALLOWED_STYLE_PROPERTIES = frozenset(
    {
        "width",
        "height",
        "min-width",
        "min-height",
        "max-width",
        "max-height",
        "margin",
        "margin-top",
        "margin-right",
        "margin-bottom",
        "margin-left",
        "padding",
        "padding-top",
        "padding-right",
        "padding-bottom",
        "padding-left",
        "display",
        "gap",
        "row-gap",
        "column-gap",
        "align-items",
        "align-content",
        "justify-items",
        "justify-content",
        "flex",
        "flex-direction",
        "flex-wrap",
        "flex-grow",
        "flex-shrink",
        "grid-template-columns",
        "grid-template-rows",
        "position",
        "top",
        "right",
        "bottom",
        "left",
        "z-index",
        "overflow",
        "border",
        "border-width",
        "border-style",
        "border-color",
        "border-radius",
        "box-shadow",
        "background",
        "background-color",
        "color",
        "opacity",
        "font-size",
        "font-weight",
        "line-height",
        "letter-spacing",
        "text-align",
        "__text_content",
    }
)

_BLOCKED_SOURCE_DIRS = frozenset(
    {
        ".git",
        ".next",
        ".nuxt",
        ".output",
        "build",
        "coverage",
        "dist",
        "node_modules",
        "out",
    }
)
_SUPPORTED_SOURCE_SUFFIXES = frozenset(
    {
        ".css",
        ".scss",
        ".sass",
        ".less",
        ".html",
        ".htm",
        ".vue",
        ".jsx",
        ".tsx",
        ".js",
        ".ts",
    }
)
_STYLE_SOURCE_SUFFIXES = frozenset({".css", ".scss", ".sass", ".less", ".html", ".htm", ".vue"})
_COMPONENT_SOURCE_SUFFIXES = frozenset({".jsx", ".tsx", ".js", ".ts", ".vue"})
_PROJECT_LOCKFILES = (
    ("pnpm-lock.yaml", "pnpm"),
    ("yarn.lock", "yarn"),
    ("bun.lockb", "bun"),
    ("bun.lock", "bun"),
    ("package-lock.json", "npm"),
)
_LOCAL_DEV_URL_RE = re.compile(
    r"(?i)(?:(?:https?|webpack|vite)://)?(?P<host>localhost|127\.0\.0\.1|0\.0\.0\.0|\[::\]|::1)(?::(?P<port>\d{2,5}))"
)
_LENGTH_TOKEN = re.compile(r"^-?(?:\d+(?:\.\d+)?|\.\d+)(?:px|rem|em|%|vw|vh|vmin|vmax|ch|ex)?$", re.I)
_NUMBER_TOKEN = re.compile(r"^-?(?:\d+(?:\.\d+)?|\.\d+)$")
_VAR_TOKEN = re.compile(r"^var\(--[a-zA-Z0-9_-]+(?:\s*,\s*[^{};]+)?\)$")
_COLOR_TOKEN = re.compile(
    r"^(?:#[0-9a-f]{3,8}|(?:rgb|rgba|hsl|hsla)\([^{};]+\)|[a-zA-Z]+|var\(--[a-zA-Z0-9_-]+(?:\s*,\s*[^{};]+)?\))$",
    re.I,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _resolve_node_executable() -> str | None:
    """Use the packaged Playwright Node driver on clean desktop installs."""

    for module_name in ("playwright", "patchright"):
        try:
            spec = importlib.util.find_spec(module_name)
        except (ImportError, AttributeError, ValueError):
            spec = None
        if spec is None or not spec.submodule_search_locations:
            continue
        for package_root in spec.submodule_search_locations:
            driver_root = Path(package_root) / "driver"
            for executable_name in ("node.exe", "node"):
                candidate = driver_root / executable_name
                if candidate.is_file():
                    return str(candidate)
    return shutil.which("node")


def _safe_preview(value: Any, limit: int = 180) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _camel_to_css(value: str) -> str:
    return re.sub(r"([A-Z])", lambda match: f"-{match.group(1).lower()}", str(value or "").strip()).lstrip("-")


def _css_to_js(value: str) -> str:
    return re.sub(r"-([a-z])", lambda match: match.group(1).upper(), str(value or "").strip())


def _style_block_spans(text: str) -> list[tuple[int, int, str]]:
    """Return style block content spans and their optional preprocessor language."""

    blocks: list[tuple[int, int, str]] = []
    pattern = re.compile(r"<style\b(?P<attrs>[^>]*)>(?P<body>[\s\S]*?)</style\s*>", flags=re.I)
    for match in pattern.finditer(text):
        attrs = str(match.group("attrs") or "")
        lang_match = re.search(r"(?:^|\s)lang\s*=\s*['\"]([^'\"]+)['\"]", attrs, flags=re.I)
        blocks.append((match.start("body"), match.end("body"), str(lang_match.group(1) if lang_match else "css").lower()))
    return blocks


def _selector_identity_tokens(selector: str) -> tuple[set[str], set[str]]:
    normalized = _normalize_selector(selector)
    ids = set(re.findall(r"#([\w:-]+)", normalized))
    classes = set(re.findall(r"\.([\w-]+)", normalized))
    return ids, classes


def _literal_selector_matches_context(selector: str, context: str) -> bool:
    ids, classes = _selector_identity_tokens(selector)
    if not ids and not classes:
        return False
    for value in ids:
        if not re.search(rf"\bid\s*=\s*['\"][^'\"]*\b{re.escape(value)}\b[^'\"]*['\"]", context):
            return False
    class_value = re.search(r"(?:className|class)\s*=\s*['\"]([^'\"]+)['\"]", context)
    class_tokens = set((class_value.group(1) if class_value else "").split())
    return classes.issubset(class_tokens)


def _parse_inline_style_body(body: str) -> dict[str, str]:
    declarations: dict[str, str] = {}
    # Keep this intentionally conservative: dynamic expressions and nested objects are read-only.
    for match in re.finditer(
        r"(?P<property>[A-Za-z_$][\w$-]*)\s*:\s*(?P<value>(?:\"[^\"]*\"|'[^']*'|`[^`]*`|[^,\n}])+)",
        body,
    ):
        raw_property = str(match.group("property") or "").strip()
        raw_value = str(match.group("value") or "").strip()
        if raw_value.startswith(("\"", "'", "`")) and raw_value[-1:] == raw_value[:1]:
            if raw_value.startswith("`") and "${" in raw_value:
                continue
            raw_value = raw_value[1:-1]
        elif not _NUMBER_TOKEN.fullmatch(raw_value):
            continue
        property_name = _camel_to_css(raw_property).lower()
        if property_name in ALLOWED_STYLE_PROPERTIES:
            declarations[property_name] = raw_value
    return declarations


def _find_react_inline_style_spans(text: str, selector: str) -> list[tuple[int, int, int, int, dict[str, str]]]:
    """Find static React style objects associated with a literal class/id selector."""

    spans: list[tuple[int, int, int, int, dict[str, str]]] = []
    pattern = re.compile(r"style\s*=\s*\{\{(?P<body>[\s\S]{0,2400}?)\}\}", flags=re.I)
    for match in pattern.finditer(text):
        context_start = text.rfind("<", max(0, match.start() - 1200), match.start())
        context_end = text.find(">", match.end(), min(len(text), match.end() + 1200))
        if context_start < 0 or context_end < 0:
            continue
        context = text[context_start:context_end]
        if not _literal_selector_matches_context(selector, context):
            continue
        body = str(match.group("body") or "")
        declarations = _parse_inline_style_body(body)
        if not declarations:
            continue
        spans.append((match.start("body"), match.end("body"), match.start(), match.end(), declarations))
    return spans


def _replace_react_inline_style_body(body: str, changes: dict[str, str]) -> str:
    static_declarations = _parse_inline_style_body(body)
    patched = body
    for property_name, value in changes.items():
        js_property = _css_to_js(property_name)
        if re.search(rf"(?:^|[,\n])\s*{re.escape(js_property)}\s*:", body) and property_name not in static_declarations:
            raise ValueError(f"React inline style {js_property} is dynamic and remains read-only")
        value_literal = json.dumps(value, ensure_ascii=False)
        property_pattern = re.compile(
            rf"(?P<prefix>(?:^|[,\n])\s*{re.escape(js_property)}\s*:\s*)(?P<value>(?:\"[^\"]*\"|'[^']*'|`[^`]*`|[^,\n}}]+))",
        )
        match = property_pattern.search(patched)
        if match:
            patched = patched[: match.start("value")] + value_literal + patched[match.end("value") :]
            continue
        trimmed = patched.rstrip()
        separator = "," if trimmed and not trimmed.endswith(",") else ""
        newline = "\n" if "\n" in patched else " "
        patched = f"{trimmed}{separator}{newline}{js_property}: {value_literal}{patched[len(trimmed):]}"
    return patched


def _find_static_component_text_spans(text: str, selector: str) -> list[tuple[int, int, str]]:
    """Find direct literal JSX/Vue text for a uniquely identified element."""

    matches: list[tuple[int, int, str]] = []
    pattern = re.compile(
        r"<(?P<tag>[A-Za-z][\w:.-]*)\b(?P<attrs>[^>]*)>(?P<body>[^<]{0,4000}?)</(?P=tag)\s*>",
        flags=re.I,
    )
    for match in pattern.finditer(text):
        opening = f"<{match.group('tag')} {match.group('attrs') or ''}>"
        if not _literal_selector_matches_context(selector, opening):
            continue
        body = str(match.group("body") or "")
        if "{" in body or "}" in body or re.search(r"\bv-(?:html|text)\s*=", opening, flags=re.I):
            continue
        if not body.strip():
            continue
        leading = len(body) - len(body.lstrip())
        trailing = len(body) - len(body.rstrip())
        start = match.start("body") + leading
        end = match.end("body") - trailing if trailing else match.end("body")
        matches.append((start, end, html.unescape(text[start:end])))
    return matches


def _strip_vue_scope_selector(selector: str) -> str:
    return re.sub(r"\[data-v-[a-z0-9]+\]", "", str(selector or ""), flags=re.I).strip()


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _normalize_selector(value: str) -> str:
    without_comments = re.sub(r"/\*.*?\*/", "", str(value or ""), flags=re.S)
    compact = re.sub(r"\s+", " ", without_comments.strip())
    return re.sub(r"\s*([>+~,])\s*", r"\1", compact)


@dataclass(frozen=True)
class CssRuleSpan:
    selector: str
    body_start: int
    body_end: int


def _iter_css_rule_spans(text: str) -> list[CssRuleSpan]:
    spans: list[CssRuleSpan] = []
    stack: list[dict[str, Any]] = []
    segment_starts = [0]
    index = 0
    quote = ""
    in_comment = False
    while index < len(text):
        char = text[index]
        next_char = text[index + 1] if index + 1 < len(text) else ""
        if in_comment:
            if char == "*" and next_char == "/":
                in_comment = False
                index += 2
                continue
            index += 1
            continue
        if quote:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                quote = ""
            index += 1
            continue
        if char == "/" and next_char == "*":
            in_comment = True
            index += 2
            continue
        if char in {"'", '"'}:
            quote = char
            index += 1
            continue
        if char == "{":
            prelude = text[segment_starts[-1] : index].strip()
            stack.append(
                {
                    "selector": prelude,
                    "body_start": index + 1,
                    "is_rule": bool(prelude) and not prelude.lstrip().startswith("@"),
                }
            )
            segment_starts.append(index + 1)
        elif char == ";":
            segment_starts[-1] = index + 1
        elif char == "}":
            if not stack:
                raise ValueError("CSS contains an unmatched closing brace")
            context = stack.pop()
            segment_starts.pop()
            if context["is_rule"]:
                spans.append(
                    CssRuleSpan(
                        selector=str(context["selector"]),
                        body_start=int(context["body_start"]),
                        body_end=index,
                    )
                )
            segment_starts[-1] = index + 1
        index += 1
    if quote or in_comment or stack:
        raise ValueError("CSS contains an unterminated string, comment, or block")
    return spans


def _matching_rule_spans(text: str, selector: str) -> list[CssRuleSpan]:
    normalized = _normalize_selector(selector)
    return [item for item in _iter_css_rule_spans(text) if _normalize_selector(item.selector) == normalized]


def _replace_declaration(body: str, property_name: str, value: str) -> tuple[str, bool]:
    property_pattern = re.compile(
        rf"(?P<prefix>(?:^|;|\n)\s*){re.escape(property_name)}\s*:\s*(?P<value>[^;}}]*)(?P<suffix>;?)",
        flags=re.I,
    )
    match = property_pattern.search(body)
    if match:
        suffix = ";" if match.group("suffix") or "\n" in body else ""
        replacement = f"{match.group('prefix')}{property_name}: {value}{suffix}"
        return body[: match.start()] + replacement + body[match.end() :], True
    multiline = "\n" in body
    if multiline:
        indentation_match = re.search(r"\n([ \t]+)\S", body)
        indentation = indentation_match.group(1) if indentation_match else "  "
        trimmed = body.rstrip()
        separator = "" if not trimmed else "\n"
        return f"{trimmed}{separator}{indentation}{property_name}: {value};\n", False
    separator = "" if not body.strip() or body.rstrip().endswith(";") else ";"
    return f"{body}{separator}{property_name}:{value};", False


def _apply_rule_changes(text: str, selector: str, changes: dict[str, str]) -> str:
    matches = _matching_rule_spans(text, selector)
    if not matches:
        raise LookupError("The mapped CSS selector no longer exists in source")
    if len(matches) != 1:
        raise ValueError("The mapped CSS selector is ambiguous in source")
    span = matches[0]
    body = text[span.body_start : span.body_end]
    for property_name, value in changes.items():
        body, _ = _replace_declaration(body, property_name, value)
    patched = text[: span.body_start] + body + text[span.body_end :]
    _iter_css_rule_spans(patched)
    return patched


def _html_style_spans(text: str) -> list[tuple[int, int]]:
    return [match.span(1) for match in re.finditer(r"<style\b[^>]*>([\s\S]*?)</style\s*>", text, flags=re.I)]


def _apply_html_style_changes(text: str, style_index: int, selector: str, changes: dict[str, str]) -> str:
    spans = _html_style_spans(text)
    if style_index < 0 or style_index >= len(spans):
        raise LookupError("The mapped inline style block no longer exists")
    start, end = spans[style_index]
    patched_style = _apply_rule_changes(text[start:end], selector, changes)
    return text[:start] + patched_style + text[end:]


def _apply_style_block_changes(text: str, style_index: int, selector: str, changes: dict[str, str]) -> str:
    blocks = _style_block_spans(text)
    if style_index < 0 or style_index >= len(blocks):
        raise LookupError("The mapped style block no longer exists")
    start, end, _lang = blocks[style_index]
    patched_style = _apply_rule_changes(text[start:end], selector, changes)
    return text[:start] + patched_style + text[end:]


def _html_element_spans(text: str, selector: str) -> list[tuple[int, int, int, int]]:
    """Return conservative spans for simple unique HTML element selectors."""

    normalized = _normalize_selector(selector)
    leaf = normalized.split(">")[-1].strip()
    leaf = re.sub(r":nth-of-type\(\d+\)", "", leaf)
    match = re.match(
        r"^(?P<tag>[a-zA-Z][\w:-]*|\*)?"
        r"(?P<qualifiers>(?:#[\w:-]+|\.[\w-]+)*)"
        r"(?P<attributes>(?:\[[^\]]+\])*)$",
        leaf,
    )
    if not match:
        return []
    tag = match.group("tag") or "*"
    qualifiers = match.group("qualifiers")
    attributes = match.group("attributes") or ""
    spans: list[tuple[int, int, int, int]] = []
    opening_pattern = re.compile(
        r"<(?P<tag>[a-zA-Z][\w:-]*)\b(?P<attrs>[^>]*)>",
        flags=re.IGNORECASE,
    )
    for opening in opening_pattern.finditer(text):
        element_tag = opening.group("tag")
        if tag != "*" and element_tag.lower() != tag.lower():
            continue
        attrs = opening.group("attrs") or ""
        if attrs.rstrip().endswith("/"):
            continue
        attr_values: dict[str, str] = {}
        for name, double_quoted, single_quoted, unquoted in re.findall(
            r"""([a-zA-Z_:][\w:.-]*)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?""",
            attrs,
        ):
            attr_values[name.lower()] = double_quoted or single_quoted or unquoted or ""
        id_values = re.findall(r"#[\w:-]+", qualifiers)
        if any(attr_values.get("id", "") != qualifier[1:] for qualifier in id_values):
            continue
        class_tokens = set(attr_values.get("class", "").split())
        if any(qualifier[1:] not in class_tokens for qualifier in re.findall(r"\.[\w-]+", qualifiers)):
            continue
        attribute_matches = re.findall(r"\[\s*([a-zA-Z_:][\w:.-]*)(?:\s*=\s*(?:['\"]([^'\"]*)['\"]|([^\]\s]+)))?\s*\]", attributes)
        attribute_mismatch = False
        for name, quoted_value, unquoted_value in attribute_matches:
            normalized_name = name.lower()
            expected_value = quoted_value or unquoted_value
            if normalized_name not in attr_values or (
                expected_value and attr_values.get(normalized_name, "") != expected_value
            ):
                attribute_mismatch = True
                break
        if attribute_mismatch:
            continue
        element_pattern = re.compile(
            rf"<\s*(?P<closing>/)?\s*{re.escape(element_tag)}\b(?P<attrs>[^>]*)>",
            flags=re.IGNORECASE,
        )
        depth = 0
        for token in element_pattern.finditer(text, opening.start()):
            if token.group("closing"):
                depth -= 1
                if depth == 0:
                    spans.append((opening.start(), token.end(), opening.end(), token.start()))
                    break
            elif not (token.group("attrs") or "").rstrip().endswith("/"):
                depth += 1
    return spans


def _apply_html_text_change(text: str, selector: str, value: str) -> str:
    spans = _html_element_spans(text, selector)
    if len(spans) != 1:
        raise LookupError("The selected HTML element is not uniquely writable")
    _start, _end, body_start, body_end = spans[0]
    body = text[body_start:body_end]
    if re.search(r"<\s*(?:script|style|iframe|object)\b", body, re.I):
        raise ValueError("Text editing is disabled for nested executable or embedded content")
    escaped = html.escape(value, quote=False)
    return text[:body_start] + escaped + text[body_end:]


def _validate_style_value(property_name: str, raw_value: Any) -> str:
    if property_name == "__text_content":
        value = str(raw_value if raw_value is not None else "")
        if len(value) > 2000 or "\x00" in value:
            raise ValueError("text content contains unsupported characters or is too long")
        return value
    value = str(raw_value or "").strip()
    if not value:
        raise ValueError(f"{property_name} requires a value")
    if len(value) > 220 or re.search(r"[;{}\r\n]", value):
        raise ValueError(f"{property_name} contains unsupported characters")
    if re.search(r"(?:url|expression|@import)\s*\(", value, flags=re.I):
        raise ValueError(f"{property_name} cannot reference external content")
    if value.lower() in {"inherit", "initial", "revert", "revert-layer", "unset"} or _VAR_TOKEN.fullmatch(value):
        return value

    length_properties = {
        "width",
        "height",
        "min-width",
        "min-height",
        "max-width",
        "max-height",
        "top",
        "right",
        "bottom",
        "left",
        "font-size",
        "letter-spacing",
        "gap",
        "row-gap",
        "column-gap",
        "border-width",
        "border-radius",
    }
    spacing_properties = {
        "margin",
        "margin-top",
        "margin-right",
        "margin-bottom",
        "margin-left",
        "padding",
        "padding-top",
        "padding-right",
        "padding-bottom",
        "padding-left",
    }
    if property_name in length_properties:
        if value.lower() in {"auto", "none", "fit-content", "min-content", "max-content", "normal"} or _LENGTH_TOKEN.fullmatch(value):
            return value
        raise ValueError(f"{property_name} must be a supported CSS length")
    if property_name in spacing_properties:
        tokens = value.split()
        if 1 <= len(tokens) <= 4 and all(token.lower() == "auto" or _LENGTH_TOKEN.fullmatch(token) or _VAR_TOKEN.fullmatch(token) for token in tokens):
            return value
        raise ValueError(f"{property_name} must contain one to four supported CSS lengths")
    if property_name in {"color", "background-color", "border-color"}:
        if _COLOR_TOKEN.fullmatch(value):
            return value
        raise ValueError(f"{property_name} must be a supported CSS color")
    if property_name == "opacity":
        if _NUMBER_TOKEN.fullmatch(value) and 0 <= float(value) <= 1:
            return value
        raise ValueError("opacity must be between 0 and 1")
    if property_name == "z-index":
        if value.lower() == "auto" or re.fullmatch(r"-?\d+", value):
            return value
        raise ValueError("z-index must be an integer or auto")

    enumerations = {
        "display": {"block", "inline", "inline-block", "flex", "inline-flex", "grid", "inline-grid", "none", "contents"},
        "position": {"static", "relative", "absolute", "fixed", "sticky"},
        "overflow": {"visible", "hidden", "clip", "scroll", "auto"},
        "flex-direction": {"row", "row-reverse", "column", "column-reverse"},
        "flex-wrap": {"nowrap", "wrap", "wrap-reverse"},
        "align-items": {"normal", "stretch", "center", "start", "end", "flex-start", "flex-end", "baseline"},
        "align-content": {"normal", "stretch", "center", "start", "end", "flex-start", "flex-end", "space-between", "space-around", "space-evenly"},
        "justify-items": {"normal", "stretch", "center", "start", "end", "legacy"},
        "justify-content": {"normal", "center", "start", "end", "left", "right", "flex-start", "flex-end", "space-between", "space-around", "space-evenly", "stretch"},
        "text-align": {"start", "end", "left", "right", "center", "justify"},
        "border-style": {"none", "hidden", "dotted", "dashed", "solid", "double", "groove", "ridge", "inset", "outset"},
    }
    if property_name in enumerations:
        if value.lower() in enumerations[property_name]:
            return value
        raise ValueError(f"{property_name} is not in the supported value set")
    if property_name == "font-weight":
        if value.lower() in {"normal", "bold", "bolder", "lighter"} or (value.isdigit() and 1 <= int(value) <= 1000):
            return value
        raise ValueError("font-weight must be a keyword or 1-1000")
    if property_name == "line-height":
        if value.lower() == "normal" or _NUMBER_TOKEN.fullmatch(value) or _LENGTH_TOKEN.fullmatch(value):
            return value
        raise ValueError("line-height must be a number or supported CSS length")
    return value


def _validate_changes(raw_changes: Any) -> dict[str, str]:
    if not isinstance(raw_changes, dict) or not raw_changes:
        raise ValueError("At least one style change is required")
    if len(raw_changes) > 24:
        raise ValueError("A single UI patch may change at most 24 properties")
    changes: dict[str, str] = {}
    for raw_property, raw_value in raw_changes.items():
        property_name = str(raw_property or "").strip().lower()
        if property_name not in ALLOWED_STYLE_PROPERTIES:
            raise ValueError(f"Unsupported UI patch property: {property_name or 'missing'}")
        changes[property_name] = _validate_style_value(property_name, raw_value)
    return changes


@dataclass
class SourceCandidate:
    candidate_id: str
    workspace_path: str
    absolute_path: Path
    selector: str
    source_kind: str
    style_index: int | None
    source_hash: str
    declarations: dict[str, str]
    reason: str
    source_start: int | None = None
    source_end: int | None = None
    runtime_selector: str | None = None

    def public(self) -> dict[str, Any]:
        return {
            "candidateId": self.candidate_id,
            "workspacePath": self.workspace_path,
            "selector": self.selector,
            "sourceKind": self.source_kind,
            "declarations": dict(self.declarations),
            "reason": self.reason,
            "runtimeSelector": self.runtime_selector or self.selector,
        }


@dataclass
class SelectionRecord:
    selection_ref: str
    patch_session_id: str
    session_id: str
    selector: str
    tag_name: str
    label: str
    text_content: str
    computed_styles: dict[str, str]
    candidates: dict[str, SourceCandidate]
    created_monotonic: float = field(default_factory=time.monotonic)


@dataclass
class PreviewSession:
    patch_session_id: str
    session_id: str
    workspace_root: Path
    mode: str
    entry_path: str
    target_url: str
    parent_origin: str
    process: subprocess.Popen[Any]
    port: int
    preview_url: str
    runtime_dir: Path
    project_path: str = ""
    project_framework: str = ""
    dev_session_id: str = ""
    dev_command: str = ""
    created_monotonic: float = field(default_factory=time.monotonic)


class UiPatchService:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sessions: dict[str, PreviewSession] = {}
        self._session_index: dict[str, str] = {}
        self._selections: dict[str, SelectionRecord] = {}
        self._runtime_root = Path(storage.base_dir) / "runtime" / "ui-patch"
        self._transactions_root = self._runtime_root / "transactions"

    def _workspace_root(self, session_id: str) -> Path:
        authority = workspace_authority_service.resolve(runtime_kind="chat", session_id=session_id)
        try:
            return Path(authority.workspace_root).expanduser().resolve(strict=True)
        except OSError as exc:
            raise FileNotFoundError("Active session workspace is unavailable") from exc

    @staticmethod
    def _source_scan_root(item: PreviewSession) -> Path:
        if not item.project_path or item.project_path == ".":
            return item.workspace_root
        candidate = (item.workspace_root / item.project_path).resolve()
        return candidate if _is_within(candidate, item.workspace_root) and candidate.is_dir() else item.workspace_root

    def _iter_source_files(self, item: PreviewSession, suffixes: frozenset[str]) -> Iterable[Path]:
        scan_root = self._source_scan_root(item).resolve()
        yielded = 0
        for directory, child_directories, filenames in os.walk(scan_root, topdown=True, followlinks=False):
            directory_path = Path(directory)
            child_directories[:] = sorted(
                name
                for name in child_directories
                if name.lower() not in _BLOCKED_SOURCE_DIRS and not (directory_path / name).is_symlink()
            )
            for filename in sorted(filenames):
                if Path(filename).suffix.lower() not in suffixes:
                    continue
                candidate = directory_path / filename
                try:
                    resolved = candidate.resolve(strict=True)
                except OSError:
                    continue
                if not _is_within(resolved, scan_root) or not _is_within(resolved, item.workspace_root):
                    continue
                yield resolved
                yielded += 1
                if yielded >= MAX_CSS_SCAN_FILES:
                    return

    @staticmethod
    def _validate_parent_origin(value: str) -> str:
        parsed = urlparse(str(value or "").strip())
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("UI Patch Workbench must run from a local V8OS Web origin")
        return f"{parsed.scheme}://{parsed.netloc}"

    @staticmethod
    def _validate_target_url(value: str) -> str:
        parsed = urlparse(str(value or "").strip())
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("UI Patch development targets must be loopback HTTP URLs")
        if parsed.username or parsed.password:
            raise ValueError("UI Patch development targets cannot contain credentials")
        return parsed.geturl()

    def _resolve_entry(self, session_id: str, requested_path: str) -> tuple[Path, str]:
        resolved = workbench_file_service.resolve(session_id=session_id, requested_path=requested_path)
        if resolved.absolute_path.suffix.lower() not in {".html", ".htm"}:
            raise ValueError("Static UI Patch previews require an HTML entry file")
        return resolved.absolute_path, resolved.workspace_relative_path

    def _resolve_project_root(self, session_id: str, requested_path: str = "") -> tuple[Path, str]:
        workspace_root = self._workspace_root(session_id)
        authority = workspace_authority_service.resolve(runtime_kind="chat", session_id=session_id)
        if hasattr(authority, "side_effects_allowed") and not bool(authority.side_effects_allowed):
            raise PermissionError("The active workspace does not allow project processes or source writes")
        raw = str(requested_path or ".").strip() or "."
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = workspace_root / candidate
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise FileNotFoundError("Project path is unavailable in the active workspace") from exc
        if resolved.is_file():
            resolved = resolved.parent
        if not _is_within(resolved, workspace_root):
            raise PermissionError("Project path must remain inside the active workspace")
        project_root = resolved
        while _is_within(project_root, workspace_root):
            if (project_root / "package.json").is_file():
                relative = project_root.relative_to(workspace_root).as_posix()
                return project_root, "." if relative == "." else relative
            if project_root == workspace_root:
                break
            project_root = project_root.parent
        raise ValueError("No package.json project was found at or above the selected path")

    @staticmethod
    def _project_manager(root: Path) -> str:
        for filename, manager in _PROJECT_LOCKFILES:
            if (root / filename).is_file():
                return manager
        return "npm"

    @staticmethod
    def _project_framework(package_payload: dict[str, Any], root: Path) -> str:
        dependencies: dict[str, Any] = {}
        for key in ("dependencies", "devDependencies", "peerDependencies"):
            value = package_payload.get(key)
            if isinstance(value, dict):
                dependencies.update({str(name).lower(): version for name, version in value.items()})
        if "next" in dependencies:
            return "next"
        if "nuxt" in dependencies:
            return "nuxt"
        if any(name == "vue" or name.startswith("@vue/") for name in dependencies):
            return "vue"
        if "react" in dependencies or "react-dom" in dependencies:
            return "react"
        if any(name == "svelte" or name.startswith("@sveltejs/") for name in dependencies):
            return "svelte"
        if "vite" in dependencies or (root / "vite.config.ts").is_file() or (root / "vite.config.js").is_file():
            return "vite"
        return "unknown"

    def inspect_project(self, *, session_id: str, project_path: str = "") -> dict[str, Any]:
        root, relative_root = self._resolve_project_root(session_id, project_path)
        package_path = root / "package.json"
        try:
            package = json.loads(package_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Project package.json is unreadable") from exc
        if not isinstance(package, dict):
            raise ValueError("Project package.json must contain an object")
        scripts = package.get("scripts") if isinstance(package.get("scripts"), dict) else {}
        dev_script = str(scripts.get("dev") or "").strip()
        if not dev_script:
            raise ValueError("Project package.json has no scripts.dev entry")
        manager = self._project_manager(root)
        framework = self._project_framework(package, root)
        entry_candidates: list[str] = []
        for candidate in ("index.html", "src/main.tsx", "src/main.jsx", "src/main.ts", "src/main.js", "src/App.vue"):
            if (root / candidate).is_file():
                entry_candidates.append(candidate)
        adapters = ["css"]
        if framework in {"react", "next"}:
            adapters.append("react-jsx-inline-style")
            adapters.append("react-jsx-text")
        if framework in {"vue", "nuxt"}:
            adapters.append("vue-sfc-style")
            adapters.append("vue-sfc-text")
        return {
            "sessionId": str(session_id),
            "projectPath": relative_root,
            "framework": framework,
            "packageManager": manager,
            "devCommand": f"{manager} {'dev' if manager in {'yarn', 'bun'} else 'run dev'}",
            "devScriptConfigured": True,
            "entryCandidates": entry_candidates,
            "sourceAdapters": adapters,
            "dynamicBindings": "read_only",
            "state": "ready",
        }

    @staticmethod
    def _project_url_candidates(output: str) -> list[str]:
        candidates: list[str] = []
        for match in _LOCAL_DEV_URL_RE.finditer(str(output or "")):
            port = int(match.group("port") or 0)
            if not 1 <= port <= 65535:
                continue
            candidates.append(f"http://127.0.0.1:{port}")
        return list(dict.fromkeys(candidates))

    @staticmethod
    def _probe_local_url(url: str) -> bool:
        try:
            request = Request(url, headers={"Accept": "text/html,application/xhtml+xml"})
            with urlopen(request, timeout=PROJECT_PROBE_TIMEOUT_SECONDS) as response:  # noqa: S310 - URL is loopback-validated.
                content_type = str(response.headers.get("content-type") or "").lower()
                return int(getattr(response, "status", 500) or 500) < 500 and (
                    "text/html" in content_type or "application/xhtml+xml" in content_type
                )
        except (HTTPError, URLError, TimeoutError, OSError):
            return False

    def _start_project_dev(self, *, session_id: str, project_path: str) -> tuple[dict[str, Any], str, str]:
        project = self.inspect_project(session_id=session_id, project_path=project_path)
        root, _ = self._resolve_project_root(session_id, project_path)
        authority = workspace_authority_service.resolve(runtime_kind="chat", session_id=session_id)
        from core.client_terminal_broker import create_terminal_session, send_terminal_input, read_terminal_session, terminate_terminal_session

        terminal = create_terminal_session(
            cwd=str(root),
            conversation_id=str(session_id),
            workspace_id=str(getattr(authority, "workspace_id", "") or "") or None,
            project_id=str(getattr(authority, "project_id", "") or "") or None,
        )
        terminal_id = str(terminal.get("sessionId") or "").strip()
        if not terminal_id:
            raise RuntimeError("Project terminal session could not be created")
        command = str(project["devCommand"])
        try:
            send_terminal_input(terminal_id, command + ("\r" if os.name == "nt" else "\n"))
            deadline = time.monotonic() + PROJECT_DEV_START_TIMEOUT_SECONDS
            output_parts: list[str] = []
            while time.monotonic() < deadline:
                snapshot = read_terminal_session(terminal_id)
                output = str(snapshot.get("outputDelta") or "")
                if output:
                    output_parts.append(output)
                combined = "".join(output_parts)[-12000:]
                candidates = self._project_url_candidates(combined)
                for candidate in candidates:
                    if self._probe_local_url(candidate):
                        return project, candidate, terminal_id
                if snapshot.get("isRunning") is False:
                    break
                time.sleep(0.25)
            safe_tail = _safe_preview("".join(output_parts)[-1600:], 700)
            raise RuntimeError(f"Project dev server did not become ready{': ' + safe_tail if safe_tail else ''}")
        except Exception:
            try:
                terminate_terminal_session(terminal_id)
            except Exception:
                pass
            raise

    @staticmethod
    def _terminate_dev_session(terminal_id: str) -> None:
        if not terminal_id:
            return
        try:
            from core.client_terminal_broker import terminate_terminal_session

            terminate_terminal_session(terminal_id)
        except Exception:
            pass

    def _cleanup_expired_locked(self) -> None:
        now = time.monotonic()
        expired_sessions = [
            patch_session_id
            for patch_session_id, item in self._sessions.items()
            if now - item.created_monotonic > PREVIEW_SESSION_TTL_SECONDS or item.process.poll() is not None
        ]
        for patch_session_id in expired_sessions:
            self._close_locked(patch_session_id)
        expired_selections = [
            selection_ref
            for selection_ref, item in self._selections.items()
            if now - item.created_monotonic > SELECTION_TTL_SECONDS or item.patch_session_id not in self._sessions
        ]
        for selection_ref in expired_selections:
            self._selections.pop(selection_ref, None)

    def create_preview(
        self,
        *,
        session_id: str,
        parent_origin: str,
        entry_path: str = "",
        target_url: str = "",
        project_path: str = "",
        start_dev_server: bool = True,
    ) -> dict[str, Any]:
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            raise ValueError("sessionId is required")
        normalized_parent = self._validate_parent_origin(parent_origin)
        workspace_root = self._workspace_root(normalized_session_id)
        node_path = _resolve_node_executable()
        script_path = Path(__file__).resolve().parents[1] / "scripts" / "ui_patch_preview_proxy.mjs"
        if not node_path:
            raise RuntimeError("Node.js is required by UI Patch Workbench")
        if not script_path.is_file():
            raise RuntimeError("UI Patch preview proxy is missing")
        normalized_entry = ""
        normalized_target = ""
        normalized_project = ""
        project_framework = ""
        dev_session_id = ""
        dev_command = ""
        mode = "static"
        if str(project_path or "").strip():
            mode = "project"
            project_info = self.inspect_project(session_id=normalized_session_id, project_path=project_path)
            normalized_project = str(project_info.get("projectPath") or "").strip()
            project_framework = str(project_info.get("framework") or "unknown")
            dev_command = str(project_info.get("devCommand") or "")
            if start_dev_server:
                with self._lock:
                    self._cleanup_expired_locked()
                    existing = self._session_index.get(normalized_session_id)
                    if existing:
                        self._close_locked(existing)
                project_info, normalized_target, dev_session_id = self._start_project_dev(
                    session_id=normalized_session_id,
                    project_path=project_path,
                )
                project_framework = str(project_info.get("framework") or project_framework)
                dev_command = str(project_info.get("devCommand") or dev_command)
            elif str(target_url or "").strip():
                normalized_target = self._validate_target_url(target_url)
            else:
                raise ValueError("Project previews require a target URL or startDevServer=true")
        elif str(target_url or "").strip():
            mode = "dev"
            normalized_target = self._validate_target_url(target_url)
        elif str(entry_path or "").strip():
            _, normalized_entry = self._resolve_entry(normalized_session_id, entry_path)
        else:
            raise ValueError("entryPath or targetUrl is required")

        with self._lock:
            self._cleanup_expired_locked()
            existing = self._session_index.get(normalized_session_id)
            if existing:
                self._close_locked(existing)

            patch_session_id = f"ui_patch_{uuid.uuid4().hex}"
            runtime_dir = self._runtime_root / "previews" / patch_session_id
            config_path = runtime_dir / "proxy-config.json"
            descriptor_path = runtime_dir / "proxy-descriptor.json"
            log_path = runtime_dir / "proxy.log"
            auth_token = secrets.token_urlsafe(32)
            bootstrap_ticket = secrets.token_urlsafe(24)
            config = {
                "version": 1,
                "patchSessionId": patch_session_id,
                "mode": mode,
                "workspaceRoot": str(workspace_root),
                "entryPath": normalized_entry,
                "targetUrl": normalized_target,
                "parentOrigin": normalized_parent,
                "authToken": auth_token,
                "bootstrapTicket": bootstrap_ticket,
                "descriptorPath": str(descriptor_path),
            }
            try:
                runtime_dir.mkdir(parents=True, exist_ok=True)
                config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
                try:
                    os.chmod(config_path, 0o600)
                except OSError:
                    pass
                log_handle = log_path.open("a", encoding="utf-8", errors="replace")
            except Exception:
                self._terminate_dev_session(dev_session_id)
                raise

            popen_kwargs: dict[str, Any] = {
                "stdin": subprocess.DEVNULL,
                "stdout": log_handle,
                "stderr": subprocess.STDOUT,
            }
            if os.name == "nt":
                popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            try:
                try:
                    process = subprocess.Popen(  # noqa: S603 - fixed local script and structured config.
                        [node_path, str(script_path), "--config", str(config_path)],
                        **popen_kwargs,
                    )
                except Exception as exc:
                    self._terminate_dev_session(dev_session_id)
                    config_path.unlink(missing_ok=True)
                    raise RuntimeError("UI Patch preview proxy could not start") from exc
            finally:
                log_handle.close()

            deadline = time.monotonic() + PREVIEW_START_TIMEOUT_SECONDS
            descriptor: dict[str, Any] = {}
            while time.monotonic() < deadline:
                if descriptor_path.is_file():
                    try:
                        descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        descriptor = {}
                    if int(descriptor.get("port") or 0) > 0:
                        break
                if process.poll() is not None:
                    break
                time.sleep(0.05)
            try:
                config_path.unlink(missing_ok=True)
            except OSError:
                pass
            port = int(descriptor.get("port") or 0)
            if port <= 0 or process.poll() is not None:
                self._terminate_dev_session(dev_session_id)
                try:
                    process.terminate()
                except OSError:
                    pass
                log_tail = ""
                try:
                    log_tail = _safe_preview(log_path.read_text(encoding="utf-8", errors="replace")[-1200:], 500)
                except OSError:
                    pass
                raise RuntimeError(f"UI Patch preview proxy failed to start{': ' + log_tail if log_tail else ''}")

            parent_host = urlparse(normalized_parent).hostname or "127.0.0.1"
            preview_host = "localhost" if parent_host == "localhost" else "127.0.0.1"
            preview_url = f"http://{preview_host}:{port}/__v8_ui_patch__/bootstrap?ticket={bootstrap_ticket}"
            item = PreviewSession(
                patch_session_id=patch_session_id,
                session_id=normalized_session_id,
                workspace_root=workspace_root,
                mode=mode,
                entry_path=normalized_entry,
                target_url=normalized_target,
                parent_origin=normalized_parent,
                process=process,
                port=port,
                preview_url=preview_url,
                runtime_dir=runtime_dir,
                project_path=normalized_project,
                project_framework=project_framework,
                dev_session_id=dev_session_id,
                dev_command=dev_command,
            )
            self._sessions[patch_session_id] = item
            self._session_index[normalized_session_id] = patch_session_id
            return self._public_session(item, include_preview_url=True)

    @staticmethod
    def _public_session(item: PreviewSession, *, include_preview_url: bool = False) -> dict[str, Any]:
        parsed_preview = urlparse(item.preview_url)
        return {
            "patchSessionId": item.patch_session_id,
            "sessionId": item.session_id,
            "mode": item.mode,
            "entryPath": item.entry_path or None,
            "targetUrl": item.target_url or None,
            "projectPath": item.project_path or None,
            "framework": item.project_framework or None,
            "devSessionId": item.dev_session_id or None,
            "devCommand": item.dev_command or None,
            "state": "ready" if item.process.poll() is None else "stopped",
            "previewOrigin": f"{parsed_preview.scheme}://{parsed_preview.netloc}",
            **({"previewUrl": item.preview_url} if include_preview_url else {}),
        }

    def get_preview(self, *, session_id: str, patch_session_id: str) -> dict[str, Any]:
        with self._lock:
            self._cleanup_expired_locked()
            item = self._require_session(session_id, patch_session_id)
            return self._public_session(item)

    def _require_session(self, session_id: str, patch_session_id: str) -> PreviewSession:
        item = self._sessions.get(str(patch_session_id or "").strip())
        if not item or item.session_id != str(session_id or "").strip():
            raise LookupError("UI Patch preview session was not found")
        return item

    def _close_locked(self, patch_session_id: str) -> bool:
        item = self._sessions.pop(patch_session_id, None)
        if not item:
            return False
        if self._session_index.get(item.session_id) == patch_session_id:
            self._session_index.pop(item.session_id, None)
        for selection_ref in [key for key, value in self._selections.items() if value.patch_session_id == patch_session_id]:
            self._selections.pop(selection_ref, None)
        if item.process.poll() is None:
            try:
                item.process.terminate()
                item.process.wait(timeout=1.5)
            except Exception:
                try:
                    item.process.kill()
                    item.process.wait(timeout=1.5)
                except OSError:
                    pass
                except subprocess.TimeoutExpired:
                    pass
        if item.dev_session_id:
            self._terminate_dev_session(item.dev_session_id)
        for secret_file in (item.runtime_dir / "proxy-config.json", item.runtime_dir / "proxy-descriptor.json"):
            try:
                secret_file.unlink(missing_ok=True)
            except OSError:
                pass
        return True

    def close_preview(self, *, session_id: str, patch_session_id: str) -> dict[str, Any]:
        with self._lock:
            item = self._require_session(session_id, patch_session_id)
            closed = self._close_locked(item.patch_session_id)
            return {"patchSessionId": patch_session_id, "closed": closed}

    def shutdown(self) -> None:
        with self._lock:
            for patch_session_id in list(self._sessions):
                self._close_locked(patch_session_id)

    @staticmethod
    def _read_source(path: Path) -> tuple[bytes, str, str]:
        raw = path.read_bytes()
        if len(raw) > MAX_SOURCE_BYTES:
            raise ValueError("UI Patch source files may not exceed 2 MiB")
        for encoding in ("utf-8-sig", "utf-8", "gb18030"):
            try:
                return raw, raw.decode(encoding), encoding
            except UnicodeDecodeError:
                continue
        raise ValueError("UI Patch source must be UTF-8 or GB18030 text")

    def _source_from_hint(self, item: PreviewSession, hint: dict[str, Any]) -> tuple[Path | None, str, int | None]:
        kind = str(hint.get("kind") or "").strip().lower()
        value = unquote(str(hint.get("value") or "").strip())
        if kind == "inline":
            if item.entry_path:
                try:
                    style_index = int(value)
                except (TypeError, ValueError):
                    style_index = 0
                entry_suffix = Path(item.entry_path).suffix.lower()
                return (item.workspace_root / item.entry_path).resolve(), "vue_style" if entry_suffix == ".vue" else "html_style", style_index
            return None, "", None
        if not value:
            return None, "", None
        parsed = urlparse(value)
        raw_path = parsed.path if parsed.scheme in {"http", "https", "file"} else value.split("?", 1)[0].split("#", 1)[0]
        if raw_path.startswith("/@fs/"):
            raw_path = raw_path[len("/@fs/") :]
        raw_path = raw_path.replace("\\", "/")
        if re.match(r"^/[a-zA-Z]:/", raw_path):
            raw_path = raw_path[1:]
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            candidate = item.workspace_root / raw_path.lstrip("/")
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            return None, "", None
        if not _is_within(resolved, item.workspace_root) or resolved.suffix.lower() not in _SUPPORTED_SOURCE_SUFFIXES:
            return None, "", None
        try:
            relative_parts = resolved.relative_to(item.workspace_root).parts
        except ValueError:
            return None, "", None
        if any(part.lower() in _BLOCKED_SOURCE_DIRS for part in relative_parts):
            return None, "", None
        suffix = resolved.suffix.lower()
        query = parse_qs(parsed.query) if parsed.query else {}
        if suffix in {".html", ".htm"}:
            return resolved, "html_style", int((query.get("index") or [0])[0] or 0)
        if suffix == ".vue":
            return resolved, "vue_style", int((query.get("index") or [0])[0] or 0)
        if suffix in {".css", ".scss", ".sass", ".less"}:
            return resolved, "css", None
        return None, "", None

    @staticmethod
    def _rule_exists(path: Path, *, source_kind: str, style_index: int | None, selector: str) -> bool:
        _, text, _ = UiPatchService._read_source(path)
        if source_kind in {"html_style", "vue_style"}:
            spans = _style_block_spans(text)
            if style_index is None or style_index < 0 or style_index >= len(spans):
                return False
            start, end, _lang = spans[style_index]
            return len(_matching_rule_spans(text[start:end], selector)) == 1
        return len(_matching_rule_spans(text, selector)) == 1

    def _scan_workspace_for_selector(self, item: PreviewSession, selector: str) -> list[tuple[Path, str, int | None]]:
        matches: list[tuple[Path, str, int | None]] = []
        for resolved_path in self._iter_source_files(item, _STYLE_SOURCE_SUFFIXES):
            try:
                _, text, _ = self._read_source(resolved_path)
                suffix = resolved_path.suffix.lower()
                if suffix in {".css", ".scss", ".sass", ".less"}:
                    source_selector = selector
                    rule_matches = _matching_rule_spans(text, source_selector)
                    if not rule_matches and ".module." in resolved_path.name:
                        source_selector = self._css_module_source_selector(text, selector)
                        rule_matches = _matching_rule_spans(text, source_selector) if source_selector else []
                    if len(rule_matches) == 1:
                        matches.append((resolved_path, "css", None))
                else:
                    for style_index, (start, end, _lang) in enumerate(_style_block_spans(text)):
                        source_selector = _strip_vue_scope_selector(selector) if suffix == ".vue" else selector
                        if len(_matching_rule_spans(text[start:end], source_selector)) == 1:
                            matches.append((resolved_path, "vue_style" if suffix == ".vue" else "html_style", style_index))
            except (OSError, UnicodeError, ValueError):
                continue
        return matches

    @staticmethod
    def _css_module_source_selector(text: str, runtime_selector: str) -> str:
        normalized = _normalize_selector(runtime_selector)
        pieces = re.findall(r"\.([\w-]+)", normalized)
        if not pieces:
            return ""
        candidates: list[str] = []
        for token in pieces:
            base = re.split(r"(?:_[a-z0-9]{4,}|__[a-z0-9]{4,})$", token, flags=re.I)[0]
            if base and base != token:
                candidates.append(base)
        if len(candidates) != len(pieces):
            return ""
        source = normalized
        for original, base in zip(pieces, candidates):
            source = source.replace(f".{original}", f".{base}", 1)
        return source if len(_matching_rule_spans(text, source)) == 1 else ""

    def _source_selector(self, path: Path, source_kind: str, runtime_selector: str) -> str:
        selector = str(runtime_selector or "").strip()
        if source_kind == "vue_style":
            return _strip_vue_scope_selector(selector)
        if source_kind == "css" and ".module." in path.name:
            try:
                _, text, _ = self._read_source(path)
                return self._css_module_source_selector(text, selector) or selector
            except (OSError, UnicodeError, ValueError):
                return selector
        return selector

    def _scan_workspace_for_inline_style(
        self,
        item: PreviewSession,
        selector: str,
    ) -> list[tuple[Path, str, int, int, dict[str, str]]]:
        matches: list[tuple[Path, str, int, int, dict[str, str]]] = []
        for resolved in self._iter_source_files(item, _COMPONENT_SOURCE_SUFFIXES):
            try:
                _, text, _ = self._read_source(resolved)
                for body_start, body_end, _attribute_start, _attribute_end, declarations in _find_react_inline_style_spans(text, selector):
                    matches.append((resolved, "react_inline_style", body_start, body_end, declarations))
            except (OSError, UnicodeError, ValueError):
                continue
        return matches

    def _scan_workspace_for_component_text(
        self,
        item: PreviewSession,
        selector: str,
        expected_text: str,
    ) -> list[tuple[Path, int, int, str]]:
        matches: list[tuple[Path, int, int, str]] = []
        for resolved in self._iter_source_files(item, _COMPONENT_SOURCE_SUFFIXES):
            try:
                _, text, _ = self._read_source(resolved)
                for start, end, value in _find_static_component_text_spans(text, selector):
                    if expected_text.strip() and value.strip() != expected_text.strip():
                        continue
                    matches.append((resolved, start, end, value))
            except (OSError, UnicodeError, ValueError):
                continue
        return matches

    def map_selection(
        self,
        *,
        session_id: str,
        patch_session_id: str,
        selection: dict[str, Any],
    ) -> dict[str, Any]:
        with self._lock:
            self._cleanup_expired_locked()
            item = self._require_session(session_id, patch_session_id)
            selector = str(selection.get("selector") or "").strip()
            if not selector or len(selector) > 600:
                raise ValueError("Selected element does not have a stable selector")
            raw_rules = list(selection.get("rules") or [])[:MAX_SELECTION_RULES]
            candidates: dict[str, SourceCandidate] = {}
            seen: set[tuple[str, str, int | None]] = set()
            for raw_rule in reversed(raw_rules):
                if not isinstance(raw_rule, dict):
                    continue
                rule_selector = str(raw_rule.get("selector") or "").strip()
                if not rule_selector or len(rule_selector) > 600:
                    continue
                declarations = {
                    str(key).strip().lower(): _safe_preview(value, 220)
                    for key, value in dict(raw_rule.get("declarations") or {}).items()
                    if str(key).strip().lower() in ALLOWED_STYLE_PROPERTIES
                }
                path_item, source_kind, style_index = self._source_from_hint(item, dict(raw_rule.get("sourceHint") or {}))
                mapped: list[tuple[Path, str, int | None, str]] = []
                source_selector = self._source_selector(path_item, source_kind, rule_selector) if path_item else rule_selector
                if path_item and self._rule_exists(path_item, source_kind=source_kind, style_index=style_index, selector=source_selector):
                    mapped.append((path_item, source_kind, style_index, "matched_local_stylesheet"))
                else:
                    fallback = self._scan_workspace_for_selector(item, rule_selector)
                    if len(fallback) == 1:
                        fallback_path, fallback_kind, fallback_index = fallback[0]
                        mapped.append((fallback_path, fallback_kind, fallback_index, "unique_workspace_selector_match"))
                for mapped_path, mapped_kind, mapped_index, reason in mapped:
                    workspace_path = mapped_path.relative_to(item.workspace_root).as_posix()
                    candidate_selector = self._source_selector(mapped_path, mapped_kind, rule_selector)
                    identity = (workspace_path, _normalize_selector(candidate_selector), mapped_index)
                    if identity in seen:
                        continue
                    seen.add(identity)
                    source_raw = mapped_path.read_bytes()
                    candidate_id = f"source_{uuid.uuid4().hex}"
                    candidates[candidate_id] = SourceCandidate(
                        candidate_id=candidate_id,
                        workspace_path=workspace_path,
                        absolute_path=mapped_path,
                        selector=candidate_selector,
                        source_kind=mapped_kind,
                        style_index=mapped_index,
                        source_hash=_sha256_bytes(source_raw),
                        declarations=declarations,
                        reason=reason,
                        runtime_selector=rule_selector if candidate_selector != rule_selector else None,
                    )
            inline_style = {
                str(key).strip().lower(): _safe_preview(value, 220)
                for key, value in dict(selection.get("inlineStyle") or {}).items()
                if str(key).strip().lower() in ALLOWED_STYLE_PROPERTIES
            }
            if inline_style and item.mode in {"project", "dev"}:
                inline_matches = self._scan_workspace_for_inline_style(item, selector)
                for path_item, source_kind, body_start, body_end, declarations in (inline_matches if len(inline_matches) == 1 else []):
                    workspace_path = path_item.relative_to(item.workspace_root).as_posix()
                    identity = (workspace_path, "react_inline_style", body_start)
                    if identity in seen:
                        continue
                    seen.add(identity)
                    source_raw = path_item.read_bytes()
                    candidate_id = f"source_{uuid.uuid4().hex}"
                    candidates[candidate_id] = SourceCandidate(
                        candidate_id=candidate_id,
                        workspace_path=workspace_path,
                        absolute_path=path_item,
                        selector=selector,
                        source_kind=source_kind,
                        style_index=None,
                        source_hash=_sha256_bytes(source_raw),
                        declarations=declarations,
                        reason="matched_unique_react_inline_style",
                        source_start=body_start,
                        source_end=body_end,
                    )
            text_content = str(selection.get("textContent") or "").replace("\x00", "")[:2000]
            if text_content.strip() and item.mode in {"project", "dev"}:
                text_matches = self._scan_workspace_for_component_text(item, selector, text_content)
                for path_item, body_start, body_end, source_text in (text_matches if len(text_matches) == 1 else []):
                    workspace_path = path_item.relative_to(item.workspace_root).as_posix()
                    identity = (workspace_path, "component_text", body_start)
                    if identity in seen:
                        continue
                    seen.add(identity)
                    source_raw = path_item.read_bytes()
                    candidate_id = f"source_{uuid.uuid4().hex}"
                    candidates[candidate_id] = SourceCandidate(
                        candidate_id=candidate_id,
                        workspace_path=workspace_path,
                        absolute_path=path_item,
                        selector=selector,
                        source_kind="component_text",
                        style_index=None,
                        source_hash=_sha256_bytes(source_raw),
                        declarations={},
                        reason="matched_unique_component_text",
                        source_start=body_start,
                        source_end=body_end,
                    )
            if not candidates and not raw_rules and item.entry_path:
                try:
                    html_path = (item.workspace_root / item.entry_path).resolve(strict=True)
                    html_bytes, html_text, _ = self._read_source(html_path)
                    if html_path.suffix.lower() in {".html", ".htm"} and len(_html_element_spans(html_text, selector)) == 1:
                        workspace_path = html_path.relative_to(item.workspace_root).as_posix()
                        candidate_id = f"source_{uuid.uuid4().hex}"
                        candidates[candidate_id] = SourceCandidate(
                            candidate_id=candidate_id,
                            workspace_path=workspace_path,
                            absolute_path=html_path,
                            selector=selector,
                            source_kind="html_text",
                            style_index=None,
                            source_hash=_sha256_bytes(html_bytes),
                            declarations={},
                            reason="matched_unique_html_text",
                        )
                except (OSError, UnicodeError, ValueError):
                    pass
            selection_ref = f"selection_{uuid.uuid4().hex}"
            computed_styles = {
                str(key).strip().lower(): _safe_preview(value, 220)
                for key, value in dict(selection.get("computedStyles") or {}).items()
                if str(key).strip().lower() in ALLOWED_STYLE_PROPERTIES
            }
            record = SelectionRecord(
                selection_ref=selection_ref,
                patch_session_id=item.patch_session_id,
                session_id=item.session_id,
                selector=selector,
                tag_name=_safe_preview(selection.get("tagName"), 80),
                label=_safe_preview(selection.get("label"), 160),
                text_content=text_content,
                computed_styles=computed_styles,
                candidates=candidates,
            )
            self._selections[selection_ref] = record
            return {
                "selectionRef": selection_ref,
                "selector": selector,
                "tagName": record.tag_name,
                "label": record.label,
                "textContent": record.text_content,
                "textEditable": any(candidate.source_kind in {"html_style", "html_text", "component_text"} for candidate in candidates.values()),
                "computedStyles": computed_styles,
                "sourceCandidates": [candidate.public() for candidate in candidates.values()],
                "writable": bool(candidates),
                "unsupportedReason": None if candidates else "No unique local source rule could be proven for this component.",
                "allowedProperties": sorted(property_name for property_name in ALLOWED_STYLE_PROPERTIES if property_name != "__text_content"),
            }

    def _require_selection(self, item: PreviewSession, selection_ref: str) -> SelectionRecord:
        record = self._selections.get(str(selection_ref or "").strip())
        if not record or record.patch_session_id != item.patch_session_id or record.session_id != item.session_id:
            raise LookupError("UI Patch selection is unavailable or expired")
        if time.monotonic() - record.created_monotonic > SELECTION_TTL_SECONDS:
            self._selections.pop(record.selection_ref, None)
            raise LookupError("UI Patch selection expired; select the component again")
        return record

    @staticmethod
    def _atomic_replace(path: Path, content: bytes) -> None:
        temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_bytes(content)
            try:
                os.chmod(temporary, path.stat().st_mode)
            except OSError:
                pass
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def _transaction_paths(self, transaction_id: str) -> tuple[Path, Path]:
        normalized_id = str(transaction_id or "").strip()
        safe_id = re.sub(r"[^a-zA-Z0-9_-]", "", normalized_id)
        if not safe_id or safe_id != normalized_id:
            raise ValueError("Invalid UI Patch transaction id")
        return self._transactions_root / f"{safe_id}.json", self._transactions_root / f"{safe_id}.before"

    def _write_transaction(self, transaction: dict[str, Any], before_bytes: bytes) -> None:
        journal_path, backup_path = self._transaction_paths(str(transaction["transactionId"]))
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        backup_temporary = backup_path.with_name(f".{backup_path.name}.{uuid.uuid4().hex}.tmp")
        backup_temporary.write_bytes(before_bytes)
        try:
            os.chmod(backup_temporary, 0o600)
        except OSError:
            pass
        os.replace(backup_temporary, backup_path)
        temporary = journal_path.with_name(f".{journal_path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(json.dumps(transaction, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, journal_path)

    def commit(
        self,
        *,
        session_id: str,
        patch_session_id: str,
        selection_ref: str,
        candidate_id: str,
        changes: dict[str, Any],
    ) -> dict[str, Any]:
        normalized_changes = _validate_changes(changes)
        with self._lock:
            self._cleanup_expired_locked()
            item = self._require_session(session_id, patch_session_id)
            selection = self._require_selection(item, selection_ref)
            candidate = selection.candidates.get(str(candidate_id or "").strip())
            if not candidate:
                raise LookupError("Selected source candidate is unavailable")
            resolved = workbench_file_service.resolve(session_id=session_id, requested_path=candidate.workspace_path)
            if resolved.absolute_path.resolve() != candidate.absolute_path.resolve():
                raise PermissionError("UI Patch source no longer resolves to the original workspace file")
            before_bytes, before_text, encoding = self._read_source(resolved.absolute_path)
            before_hash = _sha256_bytes(before_bytes)
            if before_hash != candidate.source_hash:
                raise RuntimeError("UI Patch source changed after selection; select the component again")
            text_change = normalized_changes.get("__text_content")
            style_changes = {
                key: value for key, value in normalized_changes.items() if key != "__text_content"
            }
            if text_change is not None and candidate.source_kind not in {"html_style", "html_text", "component_text"}:
                raise ValueError("Text editing requires a writable static component source")
            if candidate.source_kind in {"html_style", "vue_style"}:
                after_text = (
                    _apply_style_block_changes(
                        before_text,
                        int(candidate.style_index or 0),
                        candidate.selector,
                        style_changes,
                    )
                    if style_changes
                    else before_text
                )
                if text_change is not None:
                    after_text = _apply_html_text_change(after_text, candidate.selector, text_change)
            elif candidate.source_kind == "html_text":
                if style_changes:
                    raise ValueError("This HTML selection only supports text changes")
                after_text = _apply_html_text_change(before_text, candidate.selector, text_change or "")
            elif candidate.source_kind == "react_inline_style":
                if text_change is not None:
                    raise ValueError("React component text remains read-only")
                if candidate.source_start is None or candidate.source_end is None:
                    raise ValueError("React inline style source range is unavailable")
                if not style_changes:
                    raise ValueError("React inline style requires a style property change")
                body = before_text[candidate.source_start : candidate.source_end]
                after_body = _replace_react_inline_style_body(body, style_changes)
                after_text = before_text[: candidate.source_start] + after_body + before_text[candidate.source_end :]
            elif candidate.source_kind == "component_text":
                if style_changes:
                    raise ValueError("This component source candidate only supports text changes")
                if candidate.source_start is None or candidate.source_end is None:
                    raise ValueError("Component text source range is unavailable")
                replacement = html.escape(text_change or "", quote=False)
                after_text = before_text[: candidate.source_start] + replacement + before_text[candidate.source_end :]
            else:
                after_text = _apply_rule_changes(before_text, candidate.selector, style_changes)
            if after_text == before_text:
                raise ValueError("UI Patch did not produce a source change")
            codec = "utf-8-sig" if encoding == "utf-8-sig" else encoding
            after_bytes = after_text.encode(codec)
            after_hash = _sha256_bytes(after_bytes)
            transaction_id = f"ui_patch_tx_{uuid.uuid4().hex}"
            diff = "".join(
                difflib.unified_diff(
                    before_text.splitlines(keepends=True),
                    after_text.splitlines(keepends=True),
                    fromfile=f"a/{candidate.workspace_path}",
                    tofile=f"b/{candidate.workspace_path}",
                )
            )
            transaction = {
                "version": 1,
                "transactionId": transaction_id,
                "sessionId": item.session_id,
                "patchSessionId": item.patch_session_id,
                "workspacePath": candidate.workspace_path,
                "selector": candidate.selector,
                "runtimeSelector": selection.selector,
                "changes": normalized_changes,
                "beforeHash": before_hash,
                "afterHash": after_hash,
                "state": "prepared",
                "verificationStatus": "pending_preview_reload",
                "createdAt": _utc_now(),
                "updatedAt": _utc_now(),
            }
            try:
                self._write_transaction(transaction, before_bytes)
            except Exception as exc:
                journal_path, backup_path = self._transaction_paths(transaction_id)
                journal_path.unlink(missing_ok=True)
                backup_path.unlink(missing_ok=True)
                raise RuntimeError("UI Patch could not prepare an undo checkpoint; source was not changed") from exc
            try:
                self._atomic_replace(resolved.absolute_path, after_bytes)
                readback = resolved.absolute_path.read_bytes()
                if _sha256_bytes(readback) != after_hash:
                    raise RuntimeError("UI Patch source readback verification failed")
                transaction["state"] = "saved"
                transaction["updatedAt"] = _utc_now()
                self._rewrite_transaction(self._transaction_paths(transaction_id)[0], transaction)
            except Exception as exc:
                rollback_error: Exception | None = None
                try:
                    current_bytes = resolved.absolute_path.read_bytes()
                    if _sha256_bytes(current_bytes) != before_hash:
                        self._atomic_replace(resolved.absolute_path, before_bytes)
                    if _sha256_bytes(resolved.absolute_path.read_bytes()) != before_hash:
                        raise RuntimeError("restored source hash did not match the checkpoint")
                except Exception as restore_exc:  # pragma: no cover - platform I/O failure.
                    rollback_error = restore_exc
                transaction["state"] = "rollback_failed" if rollback_error else "rolled_back"
                transaction["updatedAt"] = _utc_now()
                try:
                    self._rewrite_transaction(self._transaction_paths(transaction_id)[0], transaction)
                except Exception:
                    pass
                if rollback_error:
                    raise RuntimeError("UI Patch save failed and the original source could not be restored") from rollback_error
                raise RuntimeError("UI Patch save failed; the original source was restored") from exc
            candidate.source_hash = after_hash
            return {
                "transactionId": transaction_id,
                "workspacePath": candidate.workspace_path,
                "selector": selection.selector,
                "changes": normalized_changes,
                "diff": diff[:256_000],
                "beforeHash": before_hash,
                "afterHash": after_hash,
                "verification": {
                    "sourceReadback": "verified",
                    "preview": "pending_reload",
                },
            }

    def _load_transaction(self, transaction_id: str) -> tuple[dict[str, Any], Path, Path]:
        journal_path, backup_path = self._transaction_paths(transaction_id)
        if not journal_path.is_file() or not backup_path.is_file():
            raise LookupError("UI Patch transaction was not found")
        try:
            transaction = json.loads(journal_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("UI Patch transaction journal is unreadable") from exc
        return transaction, journal_path, backup_path

    @staticmethod
    def _rewrite_transaction(journal_path: Path, transaction: dict[str, Any]) -> None:
        temporary = journal_path.with_name(f".{journal_path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(json.dumps(transaction, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, journal_path)

    def record_verification(
        self,
        *,
        session_id: str,
        transaction_id: str,
        status: str,
        observed_styles: dict[str, Any] | None = None,
        reason: str = "",
    ) -> dict[str, Any]:
        normalized_status = str(status or "").strip().lower()
        if normalized_status not in {"verified", "failed"}:
            raise ValueError("verification status must be verified or failed")
        with self._lock:
            transaction, journal_path, _ = self._load_transaction(transaction_id)
            if transaction.get("sessionId") != str(session_id or "").strip():
                raise PermissionError("UI Patch transaction belongs to another session")
            transaction["verificationStatus"] = normalized_status
            transaction["verificationReason"] = _safe_preview(reason, 240) or None
            transaction["observedStyles"] = {
                str(key): _safe_preview(value, 220)
                for key, value in dict(observed_styles or {}).items()
                if str(key) in ALLOWED_STYLE_PROPERTIES
            }
            transaction["updatedAt"] = _utc_now()
            self._rewrite_transaction(journal_path, transaction)
            return {
                "transactionId": transaction_id,
                "verificationStatus": normalized_status,
                "reason": transaction.get("verificationReason"),
            }

    def undo(self, *, session_id: str, transaction_id: str) -> dict[str, Any]:
        with self._lock:
            transaction, journal_path, backup_path = self._load_transaction(transaction_id)
            normalized_session_id = str(session_id or "").strip()
            if transaction.get("sessionId") != normalized_session_id:
                raise PermissionError("UI Patch transaction belongs to another session")
            if transaction.get("state") == "undone":
                return {
                    "transactionId": transaction_id,
                    "workspacePath": transaction.get("workspacePath"),
                    "state": "undone",
                    "alreadyUndone": True,
                }
            resolved = workbench_file_service.resolve(
                session_id=normalized_session_id,
                requested_path=str(transaction.get("workspacePath") or ""),
            )
            current_bytes = resolved.absolute_path.read_bytes()
            current_hash = _sha256_bytes(current_bytes)
            if current_hash != transaction.get("afterHash"):
                raise RuntimeError("UI Patch source changed after save; automatic undo was blocked")
            before_bytes = backup_path.read_bytes()
            if _sha256_bytes(before_bytes) != transaction.get("beforeHash"):
                raise RuntimeError("UI Patch undo backup failed integrity verification")
            self._atomic_replace(resolved.absolute_path, before_bytes)
            if _sha256_bytes(resolved.absolute_path.read_bytes()) != transaction.get("beforeHash"):
                raise RuntimeError("UI Patch undo readback verification failed")
            transaction["state"] = "undone"
            transaction["undoneAt"] = _utc_now()
            transaction["updatedAt"] = _utc_now()
            self._rewrite_transaction(journal_path, transaction)
            return {
                "transactionId": transaction_id,
                "workspacePath": transaction.get("workspacePath"),
                "state": "undone",
                "restoredHash": transaction.get("beforeHash"),
                "verification": {"sourceReadback": "verified", "preview": "pending_reload"},
            }


ui_patch_service = UiPatchService()
