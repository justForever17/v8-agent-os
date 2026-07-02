from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


ENGINE_ROOT = Path(__file__).resolve().parents[2]
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

from core.llm_factory import llm_factory  # noqa: E402
from core.model_thinking_control import resolve_reasoning_effort_control_for_metadata  # noqa: E402

try:  # noqa: E402
    from langchain_google_genai import ChatGoogleGenerativeAI  # type: ignore
except ImportError:  # pragma: no cover - optional dependency may be absent in partial dev envs
    ChatGoogleGenerativeAI = None  # type: ignore


REPORT_ROOT = Path.home() / ".v8-agent-os" / "reports" / "model_reasoning_effort_request_dry_run"
LEVELS = ("auto", "low", "medium", "high")
ANTHROPIC_BUDGET_BY_LEVEL = {"low": 4096, "medium": 8192, "high": 16000}
GEMINI_BUDGET_BY_LEVEL = {"low": 1024, "medium": 4096, "high": 8192}


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            if str(key).lower() in {"api_key", "google_api_key", "openai_api_key", "anthropic_api_key"}:
                redacted[key] = "<redacted-dry-run-key>"
            else:
                redacted[key] = _redact(item)
        return redacted
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _request_shape(kwargs: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "model",
        "model_name",
        "reasoning",
        "thinking",
        "effort",
        "thinking_level",
        "thinking_budget",
        "max_tokens",
        "max_tokens_to_sample",
        "max_output_tokens",
        "extra_body",
    )
    return {key: _redact(kwargs[key]) for key in keys if key in kwargs}


def _has_any_reasoning_knob(kwargs: dict[str, Any]) -> bool:
    return any(key in kwargs for key in ("reasoning", "thinking", "effort", "thinking_level", "thinking_budget"))


def _build_openai(model_id: str, meta: dict[str, Any], *, level: str) -> dict[str, Any]:
    return llm_factory._build_openai_kwargs(
        model_id,
        {**meta, "request_reasoning_effort": level},
        temperature=0,
    )


def _build_anthropic(
    model_id: str,
    meta: dict[str, Any],
    *,
    level: str,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"temperature": 0}
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    return llm_factory._build_anthropic_kwargs(
        model_id,
        {**meta, "request_reasoning_effort": level},
        **kwargs,
    )


def _build_gemini(model_id: str, meta: dict[str, Any], *, level: str) -> dict[str, Any]:
    return llm_factory._build_gemini_kwargs(
        model_id,
        {**meta, "request_reasoning_effort": level},
        temperature=0,
    )


def _base_meta(
    *,
    provider_id: str,
    api_standard: str,
    model_id: str,
    model_record: dict[str, Any] | None = None,
    capabilities: Any | None = None,
    capability_class: str = "chat_reasoning",
) -> dict[str, Any]:
    metadata = {
        "provider_id": provider_id,
        "api_standard": api_standard,
        "model_id": model_id,
        "model_record": model_record or {},
        "capabilities": capabilities if capabilities is not None else {"chat": True, "reasoning": True},
        "capability_class": capability_class,
    }
    control = resolve_reasoning_effort_control_for_metadata(metadata)
    return {
        "api_key": f"dry-run-{provider_id}",
        "provider_id": provider_id,
        "api_standard": api_standard,
        "model_id": model_id,
        "model_record": model_record or {},
        "capabilities": metadata["capabilities"],
        "capability_class": capability_class,
        "reasoning_effort_control": control,
    }


def _expect_openai_effort(level: str, kwargs: dict[str, Any]) -> bool:
    if level == "auto":
        return "reasoning" not in kwargs
    return kwargs.get("reasoning") == {"effort": level}


def _expect_anthropic_budget(level: str, kwargs: dict[str, Any]) -> bool:
    if level == "auto":
        return "thinking" not in kwargs
    thinking = kwargs.get("thinking") or {}
    budget = ANTHROPIC_BUDGET_BY_LEVEL[level]
    return (
        thinking == {"type": "enabled", "budget_tokens": budget}
        and int(kwargs.get("max_tokens_to_sample") or 0) > budget
        and "effort" not in kwargs
    )


def _expect_anthropic_effort(level: str, kwargs: dict[str, Any]) -> bool:
    if level == "auto":
        return "effort" not in kwargs
    return kwargs.get("effort") == level and "thinking" not in kwargs


def _expect_gemini_level(level: str, kwargs: dict[str, Any]) -> bool:
    if level == "auto":
        return "thinking_level" not in kwargs
    return kwargs.get("thinking_level") == level and "thinking_budget" not in kwargs


def _expect_gemini_budget(level: str, kwargs: dict[str, Any]) -> bool:
    if level == "auto":
        return "thinking_budget" not in kwargs
    return kwargs.get("thinking_budget") == GEMINI_BUDGET_BY_LEVEL[level] and "thinking_level" not in kwargs


def _expect_unsupported(level: str, kwargs: dict[str, Any]) -> bool:
    return not _has_any_reasoning_knob(kwargs)


def _probe_gemini_adapter(level: str, kwargs: dict[str, Any], request_style: str) -> dict[str, Any]:
    if ChatGoogleGenerativeAI is None:
        return {"status": "skipped", "reason": "langchain_google_genai_not_installed"}
    try:
        client = ChatGoogleGenerativeAI(**kwargs)
    except Exception as exc:  # pragma: no cover - failure is surfaced in dry-run payload
        return {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
    observed = {
        "thinkingLevel": getattr(client, "thinking_level", None),
        "thinkingBudget": getattr(client, "thinking_budget", None),
        "maxOutputTokens": getattr(client, "max_output_tokens", None),
        "timeout": getattr(client, "timeout", None),
    }
    if request_style == "gemini_thinking_level":
        accepted = observed["thinkingLevel"] == (None if level == "auto" else level)
    elif request_style == "gemini_thinking_budget":
        accepted = observed["thinkingBudget"] == (None if level == "auto" else GEMINI_BUDGET_BY_LEVEL[level])
    else:
        accepted = True
    return {"status": "accepted" if accepted else "mismatch", "observed": observed}


def _cases() -> list[dict[str, Any]]:
    return [
        {
            "id": "openai_reasoning_effort",
            "provider": "openai",
            "model": "gpt-5.5",
            "officialStyle": "OpenAI reasoning.effort",
            "meta": _base_meta(
                provider_id="openai",
                api_standard="openai",
                model_id="gpt-5.5",
                model_record={"capabilityClass": "chat_reasoning", "capabilities": {"chat": True, "reasoning": True}},
            ),
            "builder": _build_openai,
            "expect": _expect_openai_effort,
        },
        {
            "id": "openrouter_reasoning_effort",
            "provider": "openrouter",
            "model": "openai/gpt-5.5",
            "officialStyle": "OpenRouter OpenAI-compatible reasoning.effort",
            "meta": _base_meta(
                provider_id="openrouter",
                api_standard="openai",
                model_id="openai/gpt-5.5",
                model_record={"capabilityClass": "chat_reasoning", "capabilities": {"chat": True, "reasoning": True}},
            ),
            "builder": _build_openai,
            "expect": _expect_openai_effort,
        },
        {
            "id": "anthropic_manual_thinking_budget",
            "provider": "anthropic",
            "model": "claude-sonnet-4-5",
            "officialStyle": "Anthropic extended thinking budget_tokens",
            "meta": _base_meta(
                provider_id="anthropic",
                api_standard="anthropic",
                model_id="claude-sonnet-4-5",
                model_record={
                    "capabilities": {"chat": True, "reasoning": True},
                    "reasoningSurface": {"requestStyle": "anthropic_thinking"},
                },
            ),
            "builder": lambda model_id, meta, *, level: _build_anthropic(
                model_id,
                meta,
                level=level,
                max_tokens=512,
            ),
            "expect": _expect_anthropic_budget,
        },
        {
            "id": "anthropic_official_effort",
            "provider": "anthropic",
            "model": "claude-opus-4-8",
            "officialStyle": "Anthropic effort",
            "meta": _base_meta(
                provider_id="anthropic",
                api_standard="anthropic",
                model_id="claude-opus-4-8",
                model_record={"capabilities": {"chat": True, "reasoning": True}},
            ),
            "builder": _build_anthropic,
            "expect": _expect_anthropic_effort,
        },
        {
            "id": "custom_gemini3_thinking_level",
            "provider": "my-gemini-provider",
            "model": "gemini-3.1-pro-preview",
            "officialStyle": "Gemini thinking_level",
            "meta": _base_meta(
                provider_id="my-gemini-provider",
                api_standard="gemini",
                model_id="gemini-3.1-pro-preview",
                capabilities=["chat", "reasoning"],
            ),
            "builder": _build_gemini,
            "expect": _expect_gemini_level,
            "adapterProbe": "gemini",
        },
        {
            "id": "gemini25_thinking_budget",
            "provider": "gemini-api",
            "model": "gemini-2.5-pro",
            "officialStyle": "Gemini 2.5 thinking_budget",
            "meta": _base_meta(
                provider_id="gemini-api",
                api_standard="gemini",
                model_id="gemini-2.5-pro",
                model_record={"capabilities": {"chat": True, "reasoning": True}},
            ),
            "builder": _build_gemini,
            "expect": _expect_gemini_budget,
            "adapterProbe": "gemini",
        },
        {
            "id": "embedding_excluded",
            "provider": "openai",
            "model": "text-embedding-3-large",
            "officialStyle": "unsupported embedding guard",
            "meta": _base_meta(
                provider_id="openai",
                api_standard="openai",
                model_id="text-embedding-3-large",
                model_record={"capabilityClass": "embedding", "capabilities": {"embedding": True, "reasoning": True}},
                capabilities={"embedding": True, "reasoning": True},
                capability_class="embedding",
            ),
            "builder": _build_openai,
            "expect": _expect_unsupported,
        },
    ]


def _run_case(case: dict[str, Any]) -> dict[str, Any]:
    requests_by_level = {}
    checks_by_level = {}
    shapes_by_level = {}
    adapter_probe_by_level = {}
    builder: Callable[..., dict[str, Any]] = case["builder"]
    expect: Callable[[str, dict[str, Any]], bool] = case["expect"]
    for level in LEVELS:
        kwargs = builder(case["model"], case["meta"], level=level)
        requests_by_level[level] = _redact(kwargs)
        shapes_by_level[level] = _request_shape(kwargs)
        checks_by_level[level] = expect(level, kwargs)
        if case.get("adapterProbe") == "gemini":
            adapter_probe = _probe_gemini_adapter(
                level,
                kwargs,
                str((case["meta"].get("reasoning_effort_control") or {}).get("requestStyle") or ""),
            )
            adapter_probe_by_level[level] = adapter_probe
            if adapter_probe.get("status") not in {"accepted", "skipped"}:
                checks_by_level[level] = False
    return {
        "id": case["id"],
        "provider": case["provider"],
        "model": case["model"],
        "officialStyle": case["officialStyle"],
        "control": case["meta"].get("reasoning_effort_control") or {},
        "passed": all(checks_by_level.values()),
        "checksByLevel": checks_by_level,
        "requestShapeByLevel": shapes_by_level,
        "adapterProbeByLevel": adapter_probe_by_level,
        "clientKwargsByLevel": requests_by_level,
    }


def _write_markdown(payload: dict[str, Any], path: Path) -> None:
    lines = [
        "# Reasoning Effort Request Dry Run",
        "",
        f"Overall: **{'PASS' if payload['passed'] else 'FAIL'}**",
        "",
        "This report is a local dry-run. It does not call providers, open network connections, or consume model quota.",
        "",
        "## Request Shapes",
    ]
    for item in payload["cases"]:
        lines.extend(
            [
                "",
                f"### {item['id']}",
                "",
                f"- Provider/model: `{item['provider']}:{item['model']}`",
                f"- Style: `{item['officialStyle']}`",
                f"- Control: `{json.dumps(item['control'], ensure_ascii=False)}`",
                f"- Passed: `{'yes' if item['passed'] else 'no'}`",
                "",
            ]
        )
        if item.get("adapterProbeByLevel"):
            lines.extend(
                [
                    "- Adapter probe:",
                    f"  `{json.dumps(item['adapterProbeByLevel'], ensure_ascii=False)}`",
                    "",
                ]
            )
        for level, shape in item["requestShapeByLevel"].items():
            lines.extend(
                [
                    f"#### {level}",
                    "",
                    "```json",
                    json.dumps(shape, ensure_ascii=False, indent=2),
                    "```",
                    "",
                ]
            )
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export dry-run client request kwargs for temporary supervisor reasoning effort controls.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(REPORT_ROOT),
        help="Directory for JSON/Markdown reports. Defaults to ~/.v8-agent-os/reports/model_reasoning_effort_request_dry_run.",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = output_dir / f"{stamp}_reasoning_effort_request_dry_run.json"
    md_path = output_dir / f"{stamp}_reasoning_effort_request_dry_run.md"

    cases = [_run_case(case) for case in _cases()]
    payload = {
        "mode": "dry_run",
        "generatedAt": stamp,
        "passed": all(case["passed"] for case in cases),
        "notes": [
            "No provider call is made; output is produced by current V8 resolver and LLMFactory kwargs builders.",
            "Request kwargs are redacted and should be read as adapter-facing request shapes.",
        ],
        "cases": cases,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_markdown(payload, md_path)
    print(
        json.dumps(
            {
                "passed": payload["passed"],
                "json": str(json_path),
                "markdown": str(md_path),
                "caseCount": len(cases),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
