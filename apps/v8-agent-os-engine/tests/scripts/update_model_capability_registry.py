#!/usr/bin/env python
"""Build V8OS model capability registry from BenchLM plus supplemental metadata."""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List
from urllib.request import Request, urlopen


ENGINE_ROOT = Path(__file__).resolve().parents[2]
CATALOG_DIR = ENGINE_ROOT / "core" / "model_catalog"
REGISTRY_PATH = CATALOG_DIR / "model_capability_registry.json"
REPORT_PATH = CATALOG_DIR / "model_capability_registry_unresolved_report.json"
BENCHLM_MODELS_URL = "https://benchlm.ai/models"
BENCHLM_PRICING_URL = "https://benchlm.ai/api/data/pricing?limit=500"
BENCHLM_LEADERBOARD_URL = "https://benchlm.ai/api/data/leaderboard?limit=200"
DATAL_EARNER_LIST_URL = "https://www.datalearner.com/ai-models/pretrained-models?page={page}"


def fetch_text(url: str, *, timeout: int = 30) -> str:
    request = Request(url, headers={"User-Agent": "V8OS model capability registry updater"})
    with urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def fetch_json(url: str) -> Dict[str, Any]:
    return json.loads(fetch_text(url))


def strip_tags(value: str) -> str:
    cleaned = re.sub(r"<!--\s*-->", "", value)
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    cleaned = html.unescape(cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def normalize_key(value: Any) -> str:
    text = str(value or "").strip().lower().replace("_", "-")
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"[^a-z0-9.+()-]+", "-", text)
    return re.sub(r"-+", "-", text).strip("-")


def slug_to_name(slug: str) -> str:
    return " ".join(part for part in str(slug or "").split("-") if part).strip()


def parse_token_count(value: Any) -> int | None:
    text = str(value or "").strip().replace(",", "")
    if not text or text.upper() in {"N/A", "NA", "—", "-", "NONE"}:
        return None
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*([KkMm])?", text)
    if not match:
        return None
    number = float(match.group(1))
    suffix = (match.group(2) or "").lower()
    if suffix == "k":
        number *= 1_000
    elif suffix == "m":
        number *= 1_000_000
    return int(number)


def parse_price(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def capability_tags(reasoning_kind: str, category_scores: Dict[str, Any] | None = None, model_name: str = "") -> List[str]:
    scores = category_scores or {}
    tags = {"text", "chat"}
    if str(reasoning_kind or "").lower() == "reasoning":
        tags.update({"reasoning", "thinking"})
    if scores.get("multimodalGrounded") is not None:
        tags.update({"vision", "multimodal"})
    if scores.get("coding") is not None:
        tags.add("coding")
    if scores.get("agentic") is not None:
        tags.add("agentic")
    ident = model_name.lower()
    if any(token in ident for token in ("tool", "function")):
        tags.add("toolCalling")
    if any(token in ident for token in ("vl", "vision", "multimodal", "omni")):
        tags.update({"vision", "multimodal"})
    if any(token in ident for token in ("embed", "embedding")):
        tags = {"embedding"}
    if "rerank" in ident:
        tags = {"rerank"}
    return sorted(tags)


def parse_benchlm_models_page(html_text: str) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    card_pattern = re.compile(r'<a class="group text-left.*?href="/models/([^"]+)">(.*?)</a>', re.S)
    sibling_pattern = re.compile(r'<span class="truncate max-w-\[80px\]">(.*?)</span><span class="font-mono">(.*?)</span>', re.S)
    for match in card_pattern.finditer(html_text):
        slug = match.group(1)
        body = match.group(2)
        text = strip_tags(body)
        display_match = re.search(r"(?:#\d+|Unranked)\s+(.+?)(?:Canonical:|[A-Za-z].*?\s+[0-9.]+[KM]\s+·)", text)
        display_name = ""
        if display_match:
            display_name = display_match.group(1).strip()
        title_match = re.search(r'text-sm font-semibold[^>]*>(.*?)</span>', body, re.S)
        if title_match:
            display_name = strip_tags(title_match.group(1))
        display_name = display_name or slug_to_name(slug)
        canonical_raw_match = re.search(r"<p[^>]*>\s*Canonical:\s*(.*?)</p>", body, re.S)
        canonical = strip_tags(canonical_raw_match.group(1)) if canonical_raw_match else display_name
        info_match = re.search(r"([0-9.]+[KM])\s+·\s+(Reasoning|Standard)\s+·\s+([A-Za-z ]+)", text)
        context_label = info_match.group(1) if info_match else ""
        reasoning_kind = info_match.group(2) if info_match else ""
        status = info_match.group(3).strip() if info_match else ""
        creator = ""
        # Creator is usually the first muted p after optional Canonical line.
        p_texts = [strip_tags(item) for item in re.findall(r"<p[^>]*>(.*?)</p>", body, re.S)]
        for item in p_texts:
            if item and not item.startswith("Canonical:") and "·" not in item and "avg / 1M" not in item and "ranked" not in item and "Pricing" not in item:
                creator = item
                break
        aliases = {canonical, normalize_key(canonical), slug}
        if normalize_key(display_name) == normalize_key(canonical):
            aliases.update({display_name, normalize_key(display_name)})
        entries.append(
            {
                "displayName": display_name,
                "canonicalModelId": normalize_key(canonical),
                "benchlmSlug": slug,
                "creator": creator,
                "contextWindowTokens": parse_token_count(context_label),
                "benchlmReasoningKind": reasoning_kind.lower() if reasoning_kind else "",
                "status": status,
                "aliases": sorted(alias for alias in aliases if alias),
                "sourceRefs": [{"source": "benchlm_models", "url": BENCHLM_MODELS_URL}],
                "isSiblingSku": False,
            }
        )
        for sibling_name_raw, sibling_score_raw in sibling_pattern.findall(body):
            sibling_name = strip_tags(sibling_name_raw)
            if not sibling_name:
                continue
            entries.append(
                {
                    "displayName": sibling_name,
                    "canonicalModelId": normalize_key(sibling_name),
                    "benchlmFamilyId": normalize_key(canonical),
                    "creator": creator,
                    "contextWindowTokens": parse_token_count(context_label),
                    "benchlmReasoningKind": reasoning_kind.lower() if reasoning_kind else "",
                    "status": status,
                    "benchlmSiblingScore": None if strip_tags(sibling_score_raw) in {"—", ""} else strip_tags(sibling_score_raw),
                    "aliases": sorted({sibling_name, normalize_key(sibling_name)}),
                    "sourceRefs": [{"source": "benchlm_models", "url": BENCHLM_MODELS_URL}],
                    "isSiblingSku": True,
                }
            )
    return entries


def build_index(entries: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    index: Dict[str, Dict[str, Any]] = {}
    for item in entries:
        for key in [item.get("displayName"), item.get("canonicalModelId"), *(item.get("aliases") or [])]:
            normalized = normalize_key(key)
            if normalized:
                index.setdefault(normalized, item)
    return index


def merge_pricing(entries: List[Dict[str, Any]], pricing_payload: Dict[str, Any]) -> None:
    index = build_index(entries)
    for row in pricing_payload.get("models") or []:
        key = normalize_key(row.get("model"))
        item = index.get(key)
        if not item:
            item = {
                "displayName": row.get("model"),
                "canonicalModelId": key,
                "creator": row.get("creator") or "",
                "aliases": [row.get("model"), key],
                "sourceRefs": [],
                "isPricingOnly": True,
            }
            entries.append(item)
            index[key] = item
        item.setdefault("creator", row.get("creator") or "")
        item["inputPricePerMillionTokens"] = parse_price(row.get("inputPrice"))
        item["outputPricePerMillionTokens"] = parse_price(row.get("outputPrice"))
        item["sourceType"] = row.get("sourceType") or item.get("sourceType")
        if not item.get("contextWindowTokens"):
            item["contextWindowTokens"] = parse_token_count(row.get("contextWindow"))
        item.setdefault("sourceRefs", []).append({"source": "benchlm_pricing", "url": BENCHLM_PRICING_URL})


def merge_leaderboard(entries: List[Dict[str, Any]], leaderboard_payload: Dict[str, Any]) -> None:
    index = build_index(entries)
    for row in leaderboard_payload.get("models") or []:
        key = normalize_key(row.get("model"))
        item = index.get(key)
        if not item:
            continue
        item["benchlmRank"] = row.get("rank")
        item["overallScore"] = row.get("overallScore")
        item["categoryScores"] = row.get("categoryScores") or {}
        item.setdefault("sourceRefs", []).append({"source": "benchlm_leaderboard", "url": BENCHLM_LEADERBOARD_URL})


def merge_datalearner_list(entries: List[Dict[str, Any]], pages: int, delay: float) -> None:
    index = build_index(entries)
    for page in range(1, pages + 1):
        try:
            text = fetch_text(DATAL_EARNER_LIST_URL.format(page=page))
        except Exception:
            continue
        for match in re.finditer(r'"model_code":"([^"]+)"', text):
            window = text[match.start() : match.start() + 4000]
            key = normalize_key(match.group(1))
            item = index.get(key)
            if not item:
                continue
            max_output_match = re.search(r'"maxOutput":([0-9]+)', window)
            publish_match = re.search(r'"publish_time":"([^"]+)"', window)
            type_match = re.search(r'"model_TYPE_NAME":"([^"]+)"', window)
            reasoning_match = re.search(r'"reasoningModel":([0-9]+|true|false|null)', window)
            max_output = int(max_output_match.group(1)) if max_output_match and int(max_output_match.group(1)) > 0 else None
            if max_output and not item.get("maxOutputTokens"):
                item["maxOutputTokens"] = max_output
            if publish_match and not item.get("releaseDate"):
                item["releaseDate"] = publish_match.group(1)
            if type_match:
                item["datalearnerModelType"] = type_match.group(1)
            if reasoning_match and reasoning_match.group(1) in {"1", "true"}:
                item["benchlmReasoningKind"] = "reasoning"
            item.setdefault("sourceRefs", []).append({"source": "datalearner_list", "url": DATAL_EARNER_LIST_URL.format(page=page)})
        if delay:
            time.sleep(delay)


def finalize_entries(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    dedup: Dict[str, Dict[str, Any]] = {}
    for item in entries:
        key = normalize_key(item.get("canonicalModelId") or item.get("displayName"))
        if not key:
            continue
        existing = dedup.get(key)
        if existing:
            aliases = set(existing.get("aliases") or [])
            aliases.update(item.get("aliases") or [])
            existing["aliases"] = sorted(alias for alias in aliases if alias)
            for field in ("contextWindowTokens", "maxOutputTokens", "releaseDate", "creator", "inputPricePerMillionTokens", "outputPricePerMillionTokens"):
                if existing.get(field) in (None, "", []):
                    existing[field] = item.get(field)
            existing.setdefault("sourceRefs", []).extend(item.get("sourceRefs") or [])
            continue
        item["canonicalModelId"] = key
        dedup[key] = item
    result: List[Dict[str, Any]] = []
    for item in dedup.values():
        scores = item.get("categoryScores") if isinstance(item.get("categoryScores"), dict) else {}
        item["capabilities"] = capability_tags(item.get("benchlmReasoningKind") or "", scores, item.get("displayName") or "")
        if item.get("contextWindowTokens") and item["contextWindowTokens"] >= 128000:
            item["capabilities"] = sorted(set(item["capabilities"]) | {"longContext"})
        missing = []
        for field in ("contextWindowTokens", "maxOutputTokens"):
            if item.get(field) in (None, "", 0):
                missing.append(field)
        item["missingFields"] = missing
        item["confidence"] = "benchlm_plus_supplemental" if not missing else "benchlm_partial"
        item["sourceRefs"] = list({json.dumps(ref, sort_keys=True): ref for ref in item.get("sourceRefs", [])}.values())
        result.append(item)
    return sorted(result, key=lambda row: (row.get("benchlmRank") is None, row.get("benchlmRank") or 9999, row.get("displayName") or ""))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(REGISTRY_PATH))
    parser.add_argument("--report", default=str(REPORT_PATH))
    parser.add_argument("--datalearner-pages", type=int, default=18)
    parser.add_argument("--delay", type=float, default=0.0)
    args = parser.parse_args()

    models_html = fetch_text(BENCHLM_MODELS_URL)
    pricing = fetch_json(BENCHLM_PRICING_URL)
    leaderboard = fetch_json(BENCHLM_LEADERBOARD_URL)
    entries = parse_benchlm_models_page(models_html)
    merge_pricing(entries, pricing)
    merge_leaderboard(entries, leaderboard)
    merge_datalearner_list(entries, args.datalearner_pages, args.delay)
    models = finalize_entries(entries)
    payload = {
        "schemaVersion": 1,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "sourcePolicy": {
            "primaryDirectory": BENCHLM_MODELS_URL,
            "pricing": BENCHLM_PRICING_URL,
            "leaderboard": BENCHLM_LEADERBOARD_URL,
            "maxOutputPolicy": "official_or_online_metadata_then_datalearner_else_null",
            "matchingPolicy": "exact_alias_only",
        },
        "stats": {
            "models": len(models),
            "benchlmModelsPageEntries": len(entries),
            "benchlmPricingModels": len(pricing.get("models") or []),
            "benchlmLeaderboardModels": len(leaderboard.get("models") or []),
        },
        "models": models,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    unresolved = {
        "generatedAt": payload["generatedAt"],
        "stats": payload["stats"],
        "missingContextWindow": [item["displayName"] for item in models if "contextWindowTokens" in item.get("missingFields", [])],
        "missingMaxOutputTokens": [item["displayName"] for item in models if "maxOutputTokens" in item.get("missingFields", [])],
        "pricingOnly": [item["displayName"] for item in models if item.get("isPricingOnly")],
    }
    Path(args.report).write_text(json.dumps(unresolved, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "models": len(models), "output": str(output), "report": args.report}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
