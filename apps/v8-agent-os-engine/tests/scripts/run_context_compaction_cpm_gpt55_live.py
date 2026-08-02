from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage


ENGINE_ROOT = Path(__file__).resolve().parents[2]
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

from core.context_orchestrator import ContextOrchestrator  # noqa: E402
from core.llm_factory import llm_factory  # noqa: E402
from core.provider_continuation import (  # noqa: E402
    PRIVATE_PROVIDER_CONTINUATION_KEY,
    extract_provider_continuation,
    provider_continuation_from_metadata,
    seal_provider_continuation,
)
from core.response_normalizer import extract_text_and_reasoning  # noqa: E402
from core.v8_agent_os_paths import CONFIG_JSON_PATH  # noqa: E402


MODEL_ID = "gpt-5.5"
NONCE = "CTX-CPM-55-8F31"
EXACT_TIMESTAMP = "2026-08-01T09:30:00+08:00"
EXACT_VERSION = "atlas-api-v3.7.2"
REJECTED_OPTION = "full rollout on 2026-08-05"
UNRESOLVED_QUESTION = "owner of rollback approval"
EXPECTED_DECISION = "phased launch on 2026-08-15 only if error rate <=0.1%"
SOURCE_URLS = [
    "https://evidence.example.test/atlas/release-notes",
    "https://evidence.example.test/atlas/error-budget",
    "https://evidence.example.test/atlas/security-review",
    "https://evidence.example.test/atlas/rollback-drill",
    "https://evidence.example.test/atlas/client-compatibility",
    "https://evidence.example.test/atlas/operations-signoff",
]


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "missing"


def _json_object(text: str) -> dict[str, Any]:
    normalized = str(text or "").strip()
    normalized = re.sub(r"^```(?:json)?\s*", "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\s*```$", "", normalized)
    try:
        value = json.loads(normalized)
    except json.JSONDecodeError:
        start = normalized.find("{")
        end = normalized.rfind("}")
        if start < 0 or end <= start:
            raise
        value = json.loads(normalized[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("reader response is not a JSON object")
    return value


def _evidence_messages() -> list[Any]:
    return [
        HumanMessage(
            content=(
                f"USER GOAL: Decide the Atlas rollout using only the evidence below. "
                f"Correlation nonce={NONCE}. Preserve all exact identifiers, URLs, dates, rejected options, "
                "uncertainty, and unresolved questions."
            )
        ),
        HumanMessage(
            content=(
                f"SOURCE 1 URL={SOURCE_URLS[0]}; published=2026-07-20; "
                f"version={EXACT_VERSION}; fact=release candidate passed functional verification."
            )
        ),
        AIMessage(
            content=(
                f"SOURCE 2 URL={SOURCE_URLS[1]}; observed={EXACT_TIMESTAMP}; "
                "fact=canary error rate was 0.08%; launch gate is <=0.1%."
            )
        ),
        HumanMessage(
            content=(
                f"SOURCE 3 URL={SOURCE_URLS[2]}; reviewed=2026-07-28; "
                "fact=security review found no launch blocker, but requires phased exposure."
            )
        ),
        AIMessage(
            content=(
                f"SOURCE 4 URL={SOURCE_URLS[3]}; drill=2026-07-30; "
                "fact=rollback completed in 11 minutes; unresolved question="
                f"{UNRESOLVED_QUESTION}."
            )
        ),
        HumanMessage(
            content=(
                f"SOURCE 5 URL={SOURCE_URLS[4]}; updated=2026-07-31; "
                "fact=legacy client 2.4 remains compatible with the phased route."
            )
        ),
        AIMessage(
            content=(
                f"SOURCE 6 URL={SOURCE_URLS[5]}; signed=2026-08-01; "
                f"fact=operations rejected '{REJECTED_OPTION}' and recommends '{EXPECTED_DECISION}'."
            )
        ),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Live CPM GPT-5.5 context compaction acceptance.")
    parser.add_argument("--live", action="store_true", help="Required before billable provider calls.")
    parser.add_argument("--model-ref", required=True, help="Exact provider-qualified CPM GPT-5.5 model ref.")
    args = parser.parse_args()
    if not args.live:
        parser.error("--live is required")

    metadata = llm_factory.get_model_metadata(args.model_ref)
    provider_id = str(metadata.get("provider_id") or "")
    provider_name = str(metadata.get("provider_name") or "")
    model_id = str(metadata.get("model_id") or "")
    if model_id != MODEL_ID or "cpm" not in f"{provider_id} {provider_name}".lower():
        raise SystemExit("Refusing live audit: --model-ref must resolve to CPM gpt-5.5.")
    if not metadata.get("is_found") or not metadata.get("runtime_ready"):
        raise SystemExit("Configured CPM gpt-5.5 is not runtime-ready.")

    config_digest_before = _file_digest(CONFIG_JSON_PATH)
    started = time.perf_counter()
    summary, chunked = ContextOrchestrator()._build_llm_summary(
        to_compress=_evidence_messages(),
        model_id=args.model_ref,
        max_input_tokens=12_000,
        max_input_messages=30,
        max_output_tokens=1_800,
        compression_model_safety_ratio=0.90,
        effective_context_window_tokens=12_000,
    )
    if not summary:
        raise SystemExit("CPM gpt-5.5 did not produce a context summary.")

    reader = llm_factory.create_chat_model(
        args.model_ref,
        temperature=0,
        max_tokens=1_200,
        timeout=120,
        _role="context_compaction_cpm_gpt55_live_reader",
    )
    response = reader.invoke(
        [
            HumanMessage(
                content=(
                    "Use only the persistent summary below. Return one JSON object with keys: "
                    "nonce (string), sourceUrls (array), exactTimestamp (string), version (string), "
                    "rejectedOption (string), unresolvedQuestion (string), decisionMode (string), "
                    "decisionDate (string), decisionGate (string). "
                    "Do not infer missing values; write MISSING instead.\n\n"
                    f"PERSISTENT SUMMARY:\n{summary}"
                )
            )
        ]
    )
    response_text, _reasoning = extract_text_and_reasoning(response)
    recovered = _json_object(response_text)
    recovered_urls = {str(item) for item in list(recovered.get("sourceUrls") or [])}
    retained_urls = [url for url in SOURCE_URLS if url in recovered_urls]
    normalized_gate = (
        str(recovered.get("decisionGate") or "")
        .lower()
        .replace("≤", "<=")
        .replace(" ", "")
    )
    checks = {
        "summaryNonEmpty": bool(summary.strip()),
        "nonceRecovered": recovered.get("nonce") == NONCE,
        "fiveSourcesRecovered": len(retained_urls) >= 5,
        "timestampRecovered": recovered.get("exactTimestamp") == EXACT_TIMESTAMP,
        "versionRecovered": recovered.get("version") == EXACT_VERSION,
        "rejectedOptionRecovered": str(recovered.get("rejectedOption") or "").lower() == REJECTED_OPTION,
        "unresolvedQuestionRecovered": str(recovered.get("unresolvedQuestion") or "").lower() == UNRESOLVED_QUESTION,
        "decisionRecovered": (
            str(recovered.get("decisionMode") or "").strip().lower() == "phased launch"
            and recovered.get("decisionDate") == "2026-08-15"
            and normalized_gate == "errorrate<=0.1%"
        ),
    }

    continuation = extract_provider_continuation(response)
    continuation_check = "not_exposed_by_channel"
    if continuation:
        sealed = seal_provider_continuation(continuation)
        restored = provider_continuation_from_metadata(
            {PRIVATE_PROVIDER_CONTINUATION_KEY: sealed}
        )
        continuation_check = "authenticated_round_trip" if restored == continuation else "round_trip_failed"
        checks["providerContinuationRoundTrip"] = restored == continuation

    config_digest_after = _file_digest(CONFIG_JSON_PATH)
    checks["configReadIsPure"] = config_digest_before == config_digest_after
    failed = [name for name, passed in checks.items() if not passed]
    payload = {
        "audit": "context_compaction_cpm_gpt55_live",
        "modelRef": args.model_ref,
        "providerId": provider_id,
        "modelId": model_id,
        "providerAdapter": str(metadata.get("provider_adapter") or ""),
        "wireProtocol": str(metadata.get("wire_protocol") or ""),
        "summaryChars": len(summary),
        "chunked": chunked,
        "sourcesRecovered": len(retained_urls),
        "providerContinuation": continuation_check,
        "latencyMs": round((time.perf_counter() - started) * 1000, 2),
        "checks": checks,
        "failedChecks": failed,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
