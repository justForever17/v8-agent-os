from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

ENGINE_ROOT = Path(__file__).resolve().parents[2]
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

from core.database import db  # noqa: E402
from core.llm_factory import llm_factory  # noqa: E402
from core.model_control_plane import model_control_plane  # noqa: E402
from core.prompt_cache_segments import build_prompt_segments_from_parts  # noqa: E402
from core.storage import storage  # noqa: E402
from erc.runtime_context import bind_runtime_context  # noqa: E402


DEFAULT_REPORT_ROOT = Path(os.environ.get("V8_AGENT_OS_REPORTS_ROOT") or (Path.home() / ".v8-agent-os" / "reports"))


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_markdown(path: Path, payload: dict[str, Any]) -> None:
    lines = [
        "# Planner Prompt Cache Live Audit",
        "",
        f"- status: `{payload.get('status')}`",
        f"- modelRef: `{payload.get('modelRef')}`",
        f"- originalPlannerBinding: `{payload.get('originalPlannerBinding') or '(empty)'}`",
        f"- restoredPlannerBinding: `{payload.get('restoredPlannerBinding') or '(empty)'}`",
        f"- staticPrefixStable: `{payload.get('staticPrefixStable')}`",
        f"- hitTokensImproved: `{payload.get('hitTokensImproved')}`",
        f"- providerCacheObservation: `{payload.get('providerCacheObservation') or ''}`",
        "",
        "## Runs",
    ]
    for item in payload.get("runs") or []:
        lines.extend(
            [
                "",
                f"### {item.get('caseId')}",
                f"- latencyMs: `{item.get('latencyMs')}`",
                f"- responsePreview: {item.get('responsePreview')}",
                f"- staticPrefixKeyShort: `{str(item.get('staticPrefixKey') or '')[:16]}`",
                f"- providerCachedInputTokens: `{item.get('providerCachedInputTokens')}`",
                f"- providerCacheHitTokens: `{item.get('providerCacheHitTokens')}`",
                f"- providerCacheMissTokens: `{item.get('providerCacheMissTokens')}`",
                f"- promptCacheEventId: `{item.get('promptCacheEventId')}`",
            ]
        )
    if payload.get("findings"):
        lines.extend(["", "## Findings"])
        for finding in payload["findings"]:
            lines.append(f"- `{finding.get('severity')}` {finding.get('code')}: {finding.get('summary')}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _resolve_model_ref(model_profile: str) -> str:
    requested = str(model_profile or "").strip()
    models = model_control_plane.list_models()
    for model in models:
        if requested in {str(model.get("modelRef") or ""), str(model.get("modelId") or ""), str(model.get("id") or "")}:
            return str(model.get("modelRef") or model.get("id") or requested)
    if "::" in requested:
        return requested
    matches = [model for model in models if str(model.get("modelId") or "") == requested]
    if len(matches) == 1:
        return str(matches[0].get("modelRef") or requested)
    raise RuntimeError(f"model_not_found_or_ambiguous: {requested}")


def _set_planner_binding(model_ref: str) -> str:
    config = model_control_plane.get_config()
    roles = dict(config.get("roles") or {})
    original = str(roles.get("planner") or "")
    roles["planner"] = model_ref
    config["roles"] = roles
    storage.save_models_config(config)
    return original


def _restore_planner_binding(original: str) -> str:
    config = model_control_plane.get_config()
    roles = dict(config.get("roles") or {})
    roles["planner"] = str(original or "")
    config["roles"] = roles
    storage.save_models_config(config)
    return str(roles.get("planner") or "")


def _messages(task_text: str, *, static_salt: str) -> list[Any]:
    stable_lines = [
        "You are the V8OS Planner role.",
        "Convert user intent into a compact runtime-needs JSON object.",
        "Do not execute tasks. Do not write files. Do not call tools.",
        "Output JSON only with keys: runtimeNeeds, taskBriefs, qualityFlags.",
        "runtimeNeeds items use kind from: research, engineering, delegation, creative_media.",
        "taskBriefs must contain taskBriefId, goal, acceptanceContract, writeSet, riskFlags.",
        "Keep static instructions byte-stable for DeepSeek prompt-cache validation.",
        "Schema example:",
        '{"runtimeNeeds":[{"kind":"research","taskBriefId":"task-1","reason":"collect facts"}],"taskBriefs":[{"taskBriefId":"task-1","goal":"Research source-backed facts","acceptanceContract":{"must":["cite sources"],"should":[],"nice":[]},"writeSet":[],"riskFlags":[]}],"qualityFlags":[]}',
        "End of static Planner contract.",
    ]
    if static_salt:
        stable_lines.insert(1, f"Cache audit stable salt: {static_salt}.")
    stable_contract = "\n".join(stable_lines)
    parts = [
        {"source": "planner_live.base", "type": "stable_static", "text": stable_contract + "\n", "scope": "planner_contract"},
    ]
    return [
        SystemMessage(content=stable_contract, additional_kwargs={"v8_prompt_segments": build_prompt_segments_from_parts(parts)}),
        HumanMessage(content=task_text),
    ]


def _extract_usage(metadata: dict[str, Any]) -> dict[str, int | None]:
    candidates: list[Any] = [
        metadata,
        metadata.get("usage"),
        metadata.get("token_usage"),
        metadata.get("usage_metadata"),
        metadata.get("response_metadata"),
    ]
    hit = None
    miss = None
    for item in candidates:
        if not isinstance(item, dict):
            continue
        if hit is None:
            value = item.get("prompt_cache_hit_tokens") or item.get("promptCacheHitTokens")
            hit = int(value) if isinstance(value, (int, float)) else hit
        if miss is None:
            value = item.get("prompt_cache_miss_tokens") or item.get("promptCacheMissTokens")
            miss = int(value) if isinstance(value, (int, float)) else miss
    return {"hit": hit, "miss": miss}


def _last_invocation(run_id: str) -> dict[str, Any]:
    rows = db.list_model_invocations(run_id=run_id, limit=5)
    return dict(rows[0]) if rows else {}


def _run_case(case_id: str, task_text: str, *, session_id: str, run_id: str, static_salt: str) -> dict[str, Any]:
    db.create_or_update_session(
        session_id,
        title="Planner Prompt Cache Live Audit",
        user_id="prompt_cache_live",
        agent_id="planner_prompt_cache_live",
        metadata={"source": "planner_prompt_cache_live"},
    )
    db.create_run_record(
        run_id,
        session_id,
        user_id="prompt_cache_live",
        run_type="planner_prompt_cache_live",
        status="running",
        trigger_source="test_script",
        agent_id="planner_prompt_cache_live",
        metadata={"source": "planner_prompt_cache_live", "caseId": case_id},
    )
    started = time.perf_counter()
    try:
        with bind_runtime_context(
            runtime_kind="planner_live",
            session_id=session_id,
            run_id=run_id,
            agent_id="planner_prompt_cache_live",
        ):
            model = llm_factory.create_for_role("planner", temperature=0, max_tokens=900, _request_kind="planner")
            response = model.invoke(_messages(task_text, static_salt=static_salt))
        db.update_run_record(run_id, status="completed")
    except Exception as exc:
        db.update_run_record(run_id, status="failed", error_message=str(exc)[:500])
        raise
    latency_ms = round((time.perf_counter() - started) * 1000, 2)
    metadata = dict(getattr(response, "response_metadata", {}) or {})
    prompt_cache = dict(metadata.get("v8_prompt_cache") or {})
    invocation = _last_invocation(run_id)
    invocation_metadata = dict(invocation.get("metadata") or {})
    public_cache = dict(invocation_metadata.get("promptCache") or {})
    usage = _extract_usage(metadata)
    content = str(getattr(response, "content", "") or "")
    return {
        "caseId": case_id,
        "runId": run_id,
        "latencyMs": latency_ms,
        "responsePreview": content[:220].replace("\n", " "),
        "promptCacheEventId": prompt_cache.get("eventId") or public_cache.get("eventId") or "",
        "profileId": prompt_cache.get("profileId") or public_cache.get("profileId") or "",
        "staticPrefixKey": prompt_cache.get("staticPrefixKey") or "",
        "responseCacheDecision": prompt_cache.get("responseCacheDecision") or "",
        "providerCachedInputTokens": public_cache.get("providerCachedInputTokens"),
        "providerCacheHitTokens": usage.get("hit"),
        "providerCacheMissTokens": usage.get("miss"),
        "metadataKeys": sorted(metadata.keys()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run two real Planner calls against DeepSeek and inspect prompt-cache usage.")
    parser.add_argument("--live", action="store_true", help="Actually call the configured model.")
    parser.add_argument("--model-profile", default="deepseek-v4-flash", help="Model id or provider-qualified modelRef.")
    parser.add_argument("--output-dir", default="", help="Report directory. Defaults to ~/.v8-agent-os/reports/planner_prompt_cache_live/<timestamp>.")
    parser.add_argument("--unique-static-salt", action="store_true", help="Use a fresh static prefix for cold-cache experiments. Default validates the production Planner prefix.")
    args = parser.parse_args()

    if not args.live:
        print("Refusing to call a real model without --live.", file=sys.stderr)
        return 2

    timestamp = _utc_stamp()
    output_dir = Path(args.output_dir) if args.output_dir else DEFAULT_REPORT_ROOT / "planner_prompt_cache_live" / timestamp
    model_ref = _resolve_model_ref(args.model_profile)
    original = _set_planner_binding(model_ref)
    restored = ""
    payload: dict[str, Any] = {
        "status": "running",
        "modelRef": model_ref,
        "originalPlannerBinding": original,
        "runs": [],
        "findings": [],
    }
    try:
        session_id = f"planner-prompt-cache-live-{uuid.uuid4().hex[:8]}"
        audit_nonce = uuid.uuid4().hex[:12]
        static_salt = audit_nonce if args.unique_static_salt else ""
        first = _run_case(
            "warmup",
            f"把一个模糊的工程需求整理成 Research + Engineering 的 runtime needs：用户想为 test3 工作区做一个可验证的小功能，但不要求现在写代码。\nLive audit nonce: {audit_nonce}-a",
            session_id=session_id,
            run_id=f"run_{uuid.uuid4().hex}",
            static_salt=static_salt,
        )
        time.sleep(3.0)
        second = _run_case(
            "similar",
            f"把另一个相似的工程续接需求整理成 Research + Engineering 的 runtime needs：用户想调研实现细节后让工程运行时产出小型可验证交付。\nLive audit nonce: {audit_nonce}-b",
            session_id=session_id,
            run_id=f"run_{uuid.uuid4().hex}",
            static_salt=static_salt,
        )
        payload["runs"] = [first, second]
        payload["staticPrefixStable"] = bool(first.get("staticPrefixKey") and first.get("staticPrefixKey") == second.get("staticPrefixKey"))
        first_hit = first.get("providerCacheHitTokens")
        first_miss = first.get("providerCacheMissTokens")
        second_hit = second.get("providerCacheHitTokens")
        second_miss = second.get("providerCacheMissTokens")
        payload["hitTokensImproved"] = (
            isinstance(first_hit, int)
            and isinstance(second_hit, int)
            and second_hit > first_hit
        ) or (
            first_hit is None
            and isinstance(first_miss, int)
            and isinstance(second_hit, int)
            and second_hit > 0
        )
        cache_signal_present = any(isinstance(value, int) and value > 0 for value in [first_hit, second_hit])
        payload["providerCacheSignal"] = "improved" if payload["hitTokensImproved"] else "already_warm" if cache_signal_present else "miss_only"
        if isinstance(second_hit, int) and isinstance(second_miss, int):
            payload["providerCacheObservation"] = f"second_call_hit={second_hit}, second_call_miss={second_miss}"
        if not payload["staticPrefixStable"]:
            payload["findings"].append({"severity": "P1", "code": "static_prefix_unstable", "summary": "两次相似 Planner 请求的 staticPrefixKey 不一致。"})
        if first_hit is None and first_miss is None and second_hit is None and second_miss is None:
            payload["findings"].append({"severity": "P2", "code": "provider_cache_usage_missing", "summary": "响应未暴露 DeepSeek prompt_cache_hit_tokens，无法直接判断 provider 侧命中。"})
        elif not cache_signal_present:
            payload["findings"].append({"severity": "P1", "code": "provider_cache_signal_absent", "summary": "两次调用均未观察到 provider prompt-cache hit tokens。"})
        payload["status"] = "passed" if not [f for f in payload["findings"] if f.get("severity") in {"P0", "P1"}] else "failed"
    except Exception as exc:
        payload["status"] = "failed"
        payload["findings"].append({"severity": "P0", "code": "live_call_failed", "summary": f"{exc.__class__.__name__}: {str(exc)[:500]}"})
    finally:
        restored = _restore_planner_binding(original)
        payload["restoredPlannerBinding"] = restored
        _write_json(output_dir / "planner_prompt_cache_live_result.json", payload)
        _write_markdown(output_dir / "PLANNER_PROMPT_CACHE_LIVE_ZH.md", payload)

    print(json.dumps({"status": payload["status"], "report": str(output_dir), "findings": payload["findings"]}, ensure_ascii=False))
    return 0 if payload["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
