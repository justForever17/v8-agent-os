from __future__ import annotations

import argparse
import json
import sys
import time
from copy import deepcopy
from pathlib import Path
from typing import Any


ENGINE_ROOT = Path(__file__).resolve().parents[2]
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

from core.model_connection_tester import model_connection_tester  # noqa: E402
from core.model_control_plane import model_control_plane  # noqa: E402
from core.llm_factory import llm_factory  # noqa: E402
from langchain_core.messages import HumanMessage  # noqa: E402


GEMINI_PROVIDER_ID = "gemini"
CODEX_PROVIDER_ID = "codex"
GEMINI_OAUTH_PATH = Path.home() / ".gemini" / "oauth_creds.json"
CODEX_OAUTH_PATH = Path.home() / ".codex" / "auth.json"
CODEX_MODELS_CACHE_PATH = Path.home() / ".codex" / "models_cache.json"
GEMINI_CANDIDATE_MODELS = [
    "gemini-3-flash-preview",
    "gemini-3.1-pro-preview",
    "gemini-3.1-flash-lite-preview",
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-2.5-flash-lite",
]


def _chat_model_meta(
    *,
    name: str,
    context_window: int = 1_000_000,
    max_tokens: int = 8192,
    reasoning: bool = True,
    vision: bool = False,
) -> dict[str, Any]:
    return {
        "type": "MULTIMODAL" if vision else "TEXT",
        "contextWindow": context_window,
        "maxTokens": max_tokens,
        "priority": 50,
        "stabilityTier": "stable",
        "isEnabled": True,
        "capabilities": {
            "chat": True,
            "reasoning": reasoning,
            "toolCalling": True,
            "vision": vision,
            "multimodal": vision,
            "streaming": True,
            "image": False,
            "video": False,
            "audio": False,
            "embedding": False,
            "rerank": False,
            "workflow": False,
        },
    }


def _provider_meta(
    *,
    name: str,
    base_url: str,
    api_standard: str,
    oauth_preset: str,
    oauth_path: Path,
) -> dict[str, Any]:
    return {
        "name": name,
        "description": "V8OS built-in OAuth platform preset. Stored only after a live connection test passes.",
        "icon": None,
        "base_url": base_url,
        "api_key": f"oauth:{oauth_path}",
        "api_standard": api_standard,
        "type": "PLATFORM",
        "is_enabled": True,
        "credential_mode": "oauthFile",
        "oauth_preset": oauth_preset,
        "oauth_ref": "",
        "local_backend_preset": "",
    }


def _load_codex_candidate_models() -> list[str]:
    if not CODEX_MODELS_CACHE_PATH.exists():
        return ["gpt-5.5"]
    try:
        payload = json.loads(CODEX_MODELS_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return ["gpt-5.5"]
    rows = payload.get("models") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return ["gpt-5.5"]
    ids: list[str] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id") or item.get("slug") or item.get("name") or "").strip()
        if model_id and model_id not in ids:
            ids.append(model_id)
    if "gpt-5.5" not in ids:
        ids.insert(0, "gpt-5.5")
    return ids


def _merge_provider(config: dict[str, Any], provider_id: str, provider_meta: dict[str, Any], models: dict[str, dict[str, Any]]) -> dict[str, Any]:
    next_config = deepcopy(config)
    next_config.setdefault("providers", {})
    existing = dict((next_config["providers"].get(provider_id) or {}))
    existing_models = dict(existing.get("models") or {})
    existing_models.update(models)
    next_config["providers"][provider_id] = {
        "provider": {
            **dict(existing.get("provider") or {}),
            **provider_meta,
        },
        "models": existing_models,
    }
    return next_config


def _replace_provider_models(config: dict[str, Any], provider_id: str, provider_meta: dict[str, Any], models: dict[str, dict[str, Any]]) -> dict[str, Any]:
    next_config = deepcopy(config)
    next_config.setdefault("providers", {})
    existing = dict((next_config["providers"].get(provider_id) or {}))
    next_config["providers"][provider_id] = {
        "provider": {
            **dict(existing.get("provider") or {}),
            **provider_meta,
        },
        "models": dict(models),
    }
    return next_config


def _extract_text_preview(response: Any) -> str:
    content = getattr(response, "content", "")
    if isinstance(content, str):
        return content.strip()[:120]
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if isinstance(item, dict):
                value = item.get("text") or item.get("content") or ""
                if value:
                    parts.append(str(value))
                continue
            value = getattr(item, "text", "") or getattr(item, "content", "")
            if value:
                parts.append(str(value))
        return " ".join(part.strip() for part in parts if str(part).strip())[:120]
    return str(content).strip()[:120]


def _save_and_light_test(config: dict[str, Any], model_id: str) -> dict[str, Any]:
    model_control_plane.save_config(config)
    started = time.perf_counter()
    client = llm_factory.create_chat_model(
        model_id,
        temperature=0,
        max_tokens=256,
        streaming=False,
        _role="oauth_bootstrap_probe",
        _request_kind="oauth_bootstrap_light_probe",
    )
    response = client.invoke([HumanMessage(content="Reply with exact string: OK")])
    latency_ms = (time.perf_counter() - started) * 1000
    return {
        "ok": True,
        "status": "success",
        "probeMode": "light",
        "latencyMs": round(latency_ms, 2),
        "message": _extract_text_preview(response) or "连接成功",
        "runtimeMetadata": dict(getattr(response, "response_metadata", {}) or {}),
    }


def _save_and_test(config: dict[str, Any], model_id: str, *, probe_mode: str) -> dict[str, Any]:
    model_control_plane.save_config(config)
    if probe_mode == "light":
        return _save_and_light_test(config, model_id)
    result = model_connection_tester.test_model_connection(model_id=model_id)
    return result


def _sanitize_result(result: dict[str, Any]) -> dict[str, Any]:
    def _sanitize_value(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: _sanitize_value(item)
                for key, item in value.items()
                if "token" not in str(key).lower()
            }
        if isinstance(value, list):
            return [_sanitize_value(item) for item in value[:20]]
        if isinstance(value, str):
            rendered = value
            lowered = rendered.lower()
            if "<html" in lowered or "cf_chl" in lowered or len(rendered) > 600:
                return rendered[:600] + "...<truncated>"
            return rendered
        return value

    sanitized = _sanitize_value(deepcopy(result))
    for key in ("apiKey", "accessToken", "credential"):
        if isinstance(sanitized, dict) and key in sanitized:
            sanitized[key] = "<redacted>"
    if isinstance(sanitized, dict) and isinstance(sanitized.get("runtimeMetadata"), dict):
        sanitized["runtimeMetadata"] = {
            key: value for key, value in sanitized["runtimeMetadata"].items()
            if "token" not in str(key).lower()
        }
    return sanitized


def bootstrap_oauth_presets(*, apply: bool, allow_partial: bool, probe_mode: str) -> dict[str, Any]:
    original_config = model_control_plane.get_config()
    working_config = deepcopy(original_config)
    gemini_provider_meta = _provider_meta(
        name="Gemini OAuth",
        base_url="https://cloudcode-pa.googleapis.com",
        api_standard="gemini",
        oauth_preset="geminiCli",
        oauth_path=GEMINI_OAUTH_PATH,
    )
    codex_provider_meta = _provider_meta(
        name="Codex OAuth",
        base_url="https://chatgpt.com/backend-api",
        api_standard="openai",
        oauth_preset="codex",
        oauth_path=CODEX_OAUTH_PATH,
    )
    results: dict[str, Any] = {
        "apply": apply,
        "allowPartial": allow_partial,
        "gemini": {"oauthPath": str(GEMINI_OAUTH_PATH), "candidates": GEMINI_CANDIDATE_MODELS, "passed": []},
        "codex": {"oauthPath": str(CODEX_OAUTH_PATH), "candidates": _load_codex_candidate_models(), "passed": []},
        "applied": False,
        "probeMode": probe_mode,
    }

    try:
        if not GEMINI_OAUTH_PATH.exists():
            results["gemini"]["error"] = "Gemini OAuth file missing."
        else:
            for model_id in GEMINI_CANDIDATE_MODELS:
                candidate_config = _merge_provider(
                    working_config,
                    GEMINI_PROVIDER_ID,
                    gemini_provider_meta,
                    {model_id: _chat_model_meta(name=model_id, context_window=1_000_000, max_tokens=8192)},
                )
                try:
                    test_result = _save_and_test(candidate_config, model_id, probe_mode=probe_mode)
                    if test_result.get("ok"):
                        results["gemini"]["passed"].append(model_id)
                        results["gemini"].setdefault("testResults", {})[model_id] = _sanitize_result(test_result)
                        working_config = candidate_config
                    else:
                        results["gemini"].setdefault("failed", {})[model_id] = _sanitize_result(test_result)
                except Exception as exc:
                    results["gemini"].setdefault("failed", {})[model_id] = {"error": str(exc)}

        if not CODEX_OAUTH_PATH.exists():
            results["codex"]["error"] = "Codex auth file missing."
        else:
            codex_candidates = list(results["codex"]["candidates"])
            for model_id in codex_candidates:
                candidate_config = _merge_provider(
                    working_config,
                    CODEX_PROVIDER_ID,
                    codex_provider_meta,
                    {model_id: _chat_model_meta(name=model_id, context_window=1_000_000, max_tokens=8192)},
                )
                try:
                    test_result = _save_and_test(candidate_config, model_id, probe_mode=probe_mode)
                    if test_result.get("ok"):
                        results["codex"]["passed"].append(model_id)
                        results["codex"].setdefault("testResults", {})[model_id] = _sanitize_result(test_result)
                        working_config = candidate_config
                    else:
                        results["codex"].setdefault("failed", {})[model_id] = _sanitize_result(test_result)
                except Exception as exc:
                    results["codex"].setdefault("failed", {})[model_id] = _sanitize_result({"error": str(exc)})

        gemini_ok = bool(results["gemini"].get("passed"))
        codex_ok = bool(results["codex"].get("passed"))
        should_apply = apply and ((gemini_ok and codex_ok) or (allow_partial and (gemini_ok or codex_ok)))
        if should_apply:
            final_config = deepcopy(original_config)
            if gemini_ok:
                final_config = _replace_provider_models(
                    final_config,
                    GEMINI_PROVIDER_ID,
                    gemini_provider_meta,
                    {
                        model_id: _chat_model_meta(name=model_id, context_window=1_000_000, max_tokens=8192)
                        for model_id in results["gemini"].get("passed", [])
                    },
                )
            if codex_ok:
                codex_candidates = list(results["codex"].get("savedModels") or results["codex"].get("passed") or [])
                final_config = _replace_provider_models(
                    final_config,
                    CODEX_PROVIDER_ID,
                    codex_provider_meta,
                    {
                        model_id: _chat_model_meta(name=model_id, context_window=1_000_000, max_tokens=8192)
                        for model_id in codex_candidates
                    },
                )
            model_control_plane.save_config(final_config)
            results["applied"] = True
        else:
            model_control_plane.save_config(original_config)
            results["restoredOriginalConfig"] = True
        return results
    except Exception:
        model_control_plane.save_config(original_config)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="Test and optionally solidify Gemini/Codex OAuth ModelHub presets.")
    parser.add_argument("--apply", action="store_true", help="Persist passing presets to config.json#models.")
    parser.add_argument("--allow-partial", action="store_true", help="Persist any passing provider even if the other provider fails.")
    parser.add_argument(
        "--probe-mode",
        choices=("light", "full"),
        default="light",
        help="Use a single chat request by default; full runs the heavier ModelHub capability test matrix.",
    )
    args = parser.parse_args()
    result = bootstrap_oauth_presets(apply=args.apply, allow_partial=args.allow_partial, probe_mode=args.probe_mode)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
