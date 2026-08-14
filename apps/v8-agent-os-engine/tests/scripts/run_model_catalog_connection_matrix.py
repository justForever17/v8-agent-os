from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlparse


ENGINE_ROOT = Path(__file__).resolve().parents[2]
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

from core.model_catalog_connection import build_catalog_model_connection_plan  # noqa: E402
from core.model_eligibility import evaluate_model_eligibility, model_kind  # noqa: E402
from core.model_provider_catalog import model_provider_catalog  # noqa: E402


_NO_ENDPOINT_ERROR = "catalog Provider does not expose a connectable HTTP(S) endpoint"
_NO_RUNTIME_ERROR = "catalog Provider does not expose an executable runtime adapter"


def _validate_plan(*, provider_id: str, model_id: str, plan: dict[str, object]) -> list[str]:
    failures: list[str] = []
    resolved_model_id = str(plan.get("modelId") or "")
    provider_patch = dict(plan.get("providerPatch") or {})
    model_patch = dict(plan.get("modelPatch") or {})
    endpoint_binding = dict(model_patch.get("endpointBinding") or {})
    auth_contract = dict(provider_patch.get("authContract") or {})
    selected_channel = dict(plan.get("selectedChannel") or {})

    if str(plan.get("providerId") or "") != provider_id:
        failures.append("provider identity drifted")
    if str(plan.get("catalogModelId") or "") != model_id:
        failures.append("catalog model identity drifted")
    if not resolved_model_id or "{model}" in resolved_model_id:
        failures.append("model route was not materialized")

    for label, raw_url in (
        ("provider", provider_patch.get("base_url")),
        ("channel", selected_channel.get("baseUrl")),
    ):
        parsed = urlparse(str(raw_url or ""))
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            failures.append(f"{label} endpoint is not executable")

    auth_type = str(auth_contract.get("type") or "api_key").lower()
    if auth_type not in {"api_key", "oauth_file", "none"}:
        failures.append("unsupported auth contract")
    if auth_type == "none" and plan.get("credentialRequired") is not False:
        failures.append("no-auth provider requires a credential")
    if auth_type == "api_key" and plan.get("credentialRequired") is not True:
        failures.append("api-key provider does not require a credential")

    if endpoint_binding:
        endpoint_path = str(endpoint_binding.get("endpointPath") or "")
        route = str(endpoint_binding.get("route") or "")
        if "{model}" in endpoint_path or "{model}" in route:
            failures.append("endpoint template was not materialized")
        if route and route != resolved_model_id:
            failures.append("endpoint route does not match model identity")
        if dict(endpoint_binding.get("authContract") or {}) != auth_contract:
            failures.append("endpoint auth contract drifted")

    return failures


def run_matrix(*, max_duration_ms: float, max_load_ms: float) -> dict[str, object]:
    started = time.perf_counter()
    providers = model_provider_catalog.list_providers()
    loaded = time.perf_counter()
    connectable = 0
    eligibility_blocked: list[dict[str, object]] = []
    expected_blocked: list[dict[str, str]] = []
    unexpected: list[dict[str, str]] = []

    for provider in providers:
        provider_id = str(provider.get("id") or "")
        for raw_model in provider.get("models") or []:
            if not isinstance(raw_model, dict):
                continue
            model_id = str(raw_model.get("id") or "")
            if not model_id:
                continue
            model = model_provider_catalog.normalize_model(provider, model_id)
            try:
                plan = build_catalog_model_connection_plan(
                    provider=provider,
                    model=model,
                    model_id=model_id,
                    use_catalog_default_channel=True,
                    source="catalog_matrix",
                )
            except ValueError as exc:
                row = {
                    "providerId": provider_id,
                    "modelId": model_id,
                    "reason": str(exc),
                }
                availability = dict(model.get("availability") or {})
                if str(exc) in {_NO_ENDPOINT_ERROR, _NO_RUNTIME_ERROR} and availability.get("catalogConnectable") is False:
                    expected_blocked.append(row)
                else:
                    unexpected.append(row)
            else:
                plan_failures = _validate_plan(
                    provider_id=provider_id,
                    model_id=model_id,
                    plan=plan,
                )
                if plan_failures:
                    unexpected.append(
                        {
                            "providerId": provider_id,
                            "modelId": model_id,
                            "reason": "; ".join(plan_failures),
                        }
                    )
                else:
                    connectable += 1
                    if model_kind(model) == "text_generation":
                        eligibility = evaluate_model_eligibility(model)
                        if eligibility.get("blocking"):
                            eligibility_blocked.append(
                                {
                                    "providerId": provider_id,
                                    "modelId": model_id,
                                    "status": eligibility.get("status"),
                                    "requiredFacts": list(eligibility.get("requiredFacts") or []),
                                    "reasonCodes": [
                                        str(item.get("code") or "")
                                        for item in list(eligibility.get("reasons") or [])
                                    ],
                                }
                            )
                            if "maxTokens" in list(eligibility.get("requiredFacts") or []):
                                unexpected.append(
                                    {
                                        "providerId": provider_id,
                                        "modelId": model_id,
                                        "reason": "connectable text/vision model is missing maxTokens",
                                    }
                                )

    finished = time.perf_counter()
    load_ms = (loaded - started) * 1000
    duration_ms = (finished - started) * 1000
    failures: list[str] = []
    if unexpected:
        failures.append("unexpected catalog planning failures")
    if load_ms > max_load_ms:
        failures.append(f"catalog load exceeded {max_load_ms:.0f} ms")
    if duration_ms > max_duration_ms:
        failures.append(f"catalog matrix exceeded {max_duration_ms:.0f} ms")
    return {
        "ok": not failures,
        "providerCount": len(providers),
        "connectableModelCount": connectable,
        "connectionReadyModelCount": connectable - len(eligibility_blocked),
        "eligibilityBlockedCount": len(eligibility_blocked),
        "expectedBlockedCount": len(expected_blocked),
        "unexpectedBlockedCount": len(unexpected),
        "loadMs": round(load_ms, 1),
        "durationMs": round(duration_ms, 1),
        "budgets": {
            "maxLoadMs": max_load_ms,
            "maxDurationMs": max_duration_ms,
        },
        "expectedBlocked": expected_blocked,
        "eligibilityBlocked": eligibility_blocked,
        "unexpectedBlocked": unexpected,
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate every Model Hub connection plan without network calls.")
    parser.add_argument("--max-duration-ms", type=float, default=5_000)
    parser.add_argument("--max-load-ms", type=float, default=2_000)
    args = parser.parse_args()
    result = run_matrix(
        max_duration_ms=max(1.0, args.max_duration_ms),
        max_load_ms=max(1.0, args.max_load_ms),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
