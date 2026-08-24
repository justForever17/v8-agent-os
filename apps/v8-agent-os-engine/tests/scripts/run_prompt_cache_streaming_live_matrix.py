from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.database import db  # noqa: E402
from core.llm_factory import llm_factory  # noqa: E402
from core.model_control_plane import model_control_plane  # noqa: E402
from core.prompt_cache_segments import build_prompt_segments_from_parts  # noqa: E402
from erc.runtime_context import bind_runtime_context  # noqa: E402


TARGET_PROVIDERS: list[dict[str, Any]] = [
    {"id": "openai", "aliases": ["openai"]},
    {"id": "anthropic", "aliases": ["anthropic", "claude"]},
    {"id": "gemini", "aliases": ["gemini", "gemini-api", "google"]},
    {"id": "deepseek", "aliases": ["deepseek"]},
    {"id": "dashscope", "aliases": ["dashscope", "qwen", "aliyun", "aliyun-bailian", "alibaba", "bailian"]},
    {"id": "volcengine", "aliases": ["volcengine", "volcengine-ark", "volcengine-coding", "doubao"]},
    {"id": "minimax", "aliases": ["minimax"]},
    {"id": "zhipu", "aliases": ["zhipu", "bigmodel", "glm"]},
    {"id": "moonshot", "aliases": ["moonshot", "kimi"]},
    {"id": "xai", "aliases": ["xai", "grok"]},
    {"id": "mistral", "aliases": ["mistral"]},
    {"id": "openrouter", "aliases": ["openrouter"]},
]

NON_CHAT_TYPES = {"IMAGE", "VIDEO", "VOICE", "MUSIC", "WORKFLOW", "MODEL3D", "EMBEDDING", "RERANK", "RERANKER"}


def _write_json(path: str | None, payload: dict[str, Any]) -> None:
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _provider_text(model: dict[str, Any]) -> str:
    return " ".join(
        [
            str(model.get("providerId") or ""),
            str(model.get("providerName") or ""),
            str(model.get("modelId") or ""),
            str(model.get("modelRef") or ""),
        ]
    ).lower()


def _is_chat_streaming_model(model: dict[str, Any]) -> bool:
    if not bool(model.get("isEnabled", True)):
        return False
    if str(model.get("type") or "TEXT").upper() in NON_CHAT_TYPES:
        return False
    capabilities = dict(model.get("capabilities") or {})
    if capabilities.get("streaming") is False or capabilities.get("supportsStreaming") is False:
        return False
    return True


def _find_model_for_target(target: dict[str, Any], models: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, str]:
    aliases = [str(item).lower() for item in target.get("aliases") or []]
    matches = [
        model
        for model in models
        if _is_chat_streaming_model(model)
        and any(alias and alias in _provider_text(model) for alias in aliases)
    ]
    if not matches:
        return None, "missing_provider_or_streaming_model"
    matches.sort(key=lambda item: (len(item.get("assignedRoles") or []), str(item.get("modelRef") or "")), reverse=True)
    return matches[0], ""


def _build_live_messages(target_id: str) -> list[Any]:
    parts = [
        {
            "source": "live_matrix.base",
            "type": "stable_static",
            "text": "You are validating V8 prompt cache streaming telemetry.\n",
            "scope": "test_base",
        },
        {
            "source": "live_matrix.tool_registry",
            "type": "scoped_static",
            "text": "Available tools: none. Reply without tool calls.\n",
            "scope": "tool_registry",
        },
        {
            "source": "live_matrix.current_case",
            "type": "dynamic",
            "text": f"Current provider target: {target_id}. Current time is intentionally dynamic.\n",
            "scope": "matrix_case",
        },
    ]
    content = "".join(part["text"] for part in parts)
    return [
        SystemMessage(content=content, additional_kwargs={"v8_prompt_segments": build_prompt_segments_from_parts(parts)}),
        HumanMessage(content="Reply with exactly: OK"),
    ]


def _chunk_text(chunk: Any) -> str:
    content = getattr(chunk, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(item.get("text") or item.get("content") or "") if isinstance(item, dict) else str(item) for item in content)
    return str(content or "")


def _event_row(event_id: str) -> dict[str, Any] | None:
    if not event_id:
        return None
    with db.get_connection() as conn:
        row = conn.execute("SELECT * FROM prompt_cache_events WHERE id = ?", (event_id,)).fetchone()
        if not row:
            return None
        item = dict(row)
        item["providerPatch"] = json.loads(item.get("provider_patch_json") or "{}")
        item["metadata"] = json.loads(item.get("metadata_json") or "{}")
        return item


def _response_cache_count(response_cache_key: str) -> int:
    if not response_cache_key:
        return 0
    with db.get_connection() as conn:
        row = conn.execute("SELECT COUNT(*) AS count FROM llm_response_cache WHERE response_cache_key = ?", (response_cache_key,)).fetchone()
        return int(row["count"] or 0) if row else 0


def _create_live_run(target_id: str, session_id: str, run_id: str) -> None:
    db.create_or_update_session(
        session_id,
        title=f"Prompt Cache Live Matrix · {target_id}",
        user_id="prompt_cache_live",
        agent_id="prompt_cache_streaming_live_matrix",
        metadata={"source": "prompt_cache_streaming_live_matrix", "targetProvider": target_id},
    )
    db.create_run_record(
        run_id,
        session_id,
        user_id="prompt_cache_live",
        run_type="prompt_cache_live",
        status="running",
        trigger_source="test_script",
        agent_id="prompt_cache_streaming_live_matrix",
        metadata={"source": "prompt_cache_streaming_live_matrix", "targetProvider": target_id},
    )


def _run_provider_cell(target: dict[str, Any], model: dict[str, Any]) -> dict[str, Any]:
    model_ref = str(model.get("modelRef") or "")
    provider_id = str(model.get("providerId") or target["id"])
    metadata = llm_factory.get_model_metadata(model_ref)
    if not metadata.get("is_found"):
        return {"status": "failed", "reason": "model_metadata_missing"}
    if not bool(metadata.get("runtime_ready", True)):
        return {
            "status": "failed",
            "reason": "provider_not_runtime_ready",
            "runtimeUnsupportedReason": str(metadata.get("runtime_unsupported_reason") or ""),
        }
    if not str(metadata.get("api_key") or "").strip():
        return {"status": "failed", "reason": "missing_credential"}

    session_id = f"prompt-cache-live-{target['id']}-{uuid.uuid4().hex[:8]}"
    run_id = f"run-{uuid.uuid4().hex}"
    chunks: list[str] = []
    started = time.perf_counter()
    _create_live_run(target["id"], session_id, run_id)
    try:
        with bind_runtime_context(
            runtime_kind="prompt_cache_live",
            session_id=session_id,
            run_id=run_id,
            agent_id="prompt_cache_streaming_live_matrix",
        ):
            model_kwargs: dict[str, Any] = {
                "streaming": True,
                "temperature": 0,
                "_role": "prompt_cache_streaming_live",
            }
            configured_limit = llm_factory.get_model_max_output_tokens(model_ref)
            if configured_limit:
                model_kwargs["max_tokens"] = int(configured_limit)
            model_instance = llm_factory.create_chat_model(model_ref, **model_kwargs)
            for chunk in model_instance.stream(_build_live_messages(target["id"])):
                text = _chunk_text(chunk)
                if text:
                    chunks.append(text)
    except Exception as exc:
        db.update_run_record(run_id, status="failed", error_message=str(exc)[:500])
        raise
    db.update_run_record(run_id, status="completed")

    response_text = "".join(chunks).strip()
    invocations = db.list_model_invocations(run_id=run_id, limit=5)
    invocation = invocations[0] if invocations else {}
    telemetry_metadata = dict(invocation.get("metadata") or {})
    prompt_cache = dict(telemetry_metadata.get("promptCache") or {})
    event = _event_row(str(prompt_cache.get("eventId") or ""))
    response_cache_count = _response_cache_count(str(prompt_cache.get("responseCacheKey") or ""))
    checks = {
        "responseNonEmpty": bool(response_text),
        "telemetryCompleted": invocation.get("status") == "completed",
        "isStreaming": bool(invocation.get("is_streaming")),
        "promptCachePresent": bool(prompt_cache),
        "skipReasonStreaming": prompt_cache.get("skipReason") == "streaming_request",
        "eventRecorded": bool(event),
        "eventSkipped": bool(event and event.get("decision") == "skipped"),
        "responseCacheNotWritten": response_cache_count == 0,
    }
    failed_checks = [key for key, ok in checks.items() if not ok]
    return {
        "status": "succeeded" if not failed_checks else "failed",
        "reason": ",".join(failed_checks),
        "providerId": provider_id,
        "modelRef": model_ref,
        "modelId": str(model.get("modelId") or ""),
        "profileId": str(prompt_cache.get("profileId") or ""),
        "eventId": str(prompt_cache.get("eventId") or ""),
        "responseCacheKey": str(prompt_cache.get("responseCacheKey") or ""),
        "skipReason": str(prompt_cache.get("skipReason") or ""),
        "responsePreview": response_text[:40],
        "latencyMs": round((time.perf_counter() - started) * 1000, 2),
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run live streaming prompt-cache telemetry matrix.")
    parser.add_argument("--live", action="store_true", help="Actually call configured providers.")
    parser.add_argument("--require-all", action="store_true", help="Return non-zero when any target provider is missing or failed.")
    parser.add_argument("--output", default="", help="Optional JSON output path.")
    args = parser.parse_args()

    if not args.live:
        print("Refusing to run provider calls without --live.")
        return 2

    models = model_control_plane.list_models()
    cells: list[dict[str, Any]] = []
    for target in TARGET_PROVIDERS:
        model, missing_reason = _find_model_for_target(target, models)
        if model is None:
            cells.append({"targetProvider": target["id"], "status": "failed", "reason": missing_reason})
            continue
        try:
            result = _run_provider_cell(target, model)
        except Exception as exc:
            result = {"status": "failed", "reason": "provider_error", "errorType": exc.__class__.__name__, "error": str(exc)[:500]}
        result["targetProvider"] = target["id"]
        cells.append(result)

    failed = [cell for cell in cells if cell.get("status") != "succeeded"]
    payload = {
        "matrix": "prompt_cache_streaming_live",
        "requiredProviders": [item["id"] for item in TARGET_PROVIDERS],
        "summary": {"total": len(cells), "succeeded": len(cells) - len(failed), "failed": len(failed)},
        "cells": cells,
    }
    _write_json(args.output, payload)
    print(json.dumps(payload["summary"], ensure_ascii=False))
    if failed:
        print(json.dumps([{"targetProvider": item.get("targetProvider"), "reason": item.get("reason")} for item in failed], ensure_ascii=False, indent=2))
    return 1 if failed and args.require_all else 0


if __name__ == "__main__":
    raise SystemExit(main())
