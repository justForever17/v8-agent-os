from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterator


ENGINE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT_ROOT = ENGINE_ROOT.parents[2] / "out" / "research-runtime-live"
DEFAULT_BUNDLE_PATH = DEFAULT_REPORT_ROOT / "pressure_ledger.json"
DEFAULT_ATTEMPT_LOG = DEFAULT_REPORT_ROOT / "pressure_acceptance_attempts.jsonl"
DEFAULT_CODE_PATHS = (
    ENGINE_ROOT / "core" / "tools" / "research_broker.py",
    ENGINE_ROOT / "core" / "tools" / "research_quality.py",
    ENGINE_ROOT / "core" / "tools" / "web_fetcher.py",
    Path(__file__).resolve(),
)
IMMUTABLE_BUNDLE_FIELDS = (
    "evidenceBundleId",
    "question",
    "sourcePolicy",
    "freshness",
    "confidence",
    "authorityScore",
    "sourceMatrix",
    "shards",
)
TERMINAL_EVENTS = {"attempt_finished", "attempt_abandoned"}
REQUIRED_RESEARCH_AGENT_IDS = {
    "verification-engineer",
    "web-research-architect",
}
FIXED_TARGET_EFFECTIVE_CHARS = 5_000
FIXED_TARGET_CITED_SOURCES = 8
FIXED_TARGET_DISTINCT_HOSTS = 5
FIXED_TARGET_REVIEW_COUNT = 2


class FixedBundleAcceptanceError(RuntimeError):
    pass


class AttemptLogError(FixedBundleAcceptanceError):
    pass


class EvidenceAcquisitionForbidden(FixedBundleAcceptanceError):
    pass


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: Any) -> str:
    return _sha256_bytes(_canonical_json_bytes(value))


def _immutable_bundle_projection(bundle: dict[str, Any]) -> dict[str, Any]:
    projection = {
        field: copy.deepcopy(bundle.get(field))
        for field in IMMUTABLE_BUNDLE_FIELDS
    }
    if not str(projection.get("evidenceBundleId") or "").strip():
        raise FixedBundleAcceptanceError("fixed bundle is missing evidenceBundleId")
    if not str(projection.get("question") or "").strip():
        raise FixedBundleAcceptanceError("fixed bundle is missing the exact question")
    if not isinstance(projection.get("sourceMatrix"), list) or not projection["sourceMatrix"]:
        raise FixedBundleAcceptanceError("fixed bundle has no sourceMatrix")
    if not isinstance(projection.get("shards"), list) or not projection["shards"]:
        raise FixedBundleAcceptanceError("fixed bundle has no shards")
    return projection


def bundle_digest(bundle: dict[str, Any]) -> str:
    return _sha256_json(_immutable_bundle_projection(bundle))


def question_digest(bundle: dict[str, Any]) -> str:
    question = str(bundle.get("question") or "")
    if not question.strip():
        raise FixedBundleAcceptanceError("fixed bundle is missing the exact question")
    return _sha256_bytes(question.encode("utf-8"))


def load_fixed_bundle(path: Path, *, bundle_id: str = "") -> dict[str, Any]:
    try:
        ledger = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FixedBundleAcceptanceError(f"cannot read fixed bundle ledger: {exc}") from exc
    bundles = ledger.get("evidenceBundles") if isinstance(ledger, dict) else None
    if not isinstance(bundles, list) or not bundles:
        raise FixedBundleAcceptanceError("fixed bundle ledger has no evidenceBundles")
    candidates = [item for item in bundles if isinstance(item, dict)]
    if bundle_id:
        candidates = [
            item
            for item in candidates
            if str(item.get("evidenceBundleId") or "").strip() == bundle_id
        ]
    if len(candidates) != 1:
        raise FixedBundleAcceptanceError(
            f"expected exactly one fixed bundle, found {len(candidates)}"
        )
    _immutable_bundle_projection(candidates[0])
    return candidates[0]


def code_fingerprint(paths: tuple[Path, ...] | list[Path]) -> str:
    manifest: list[dict[str, str]] = []
    for path in sorted((Path(item).resolve() for item in paths), key=lambda item: str(item).lower()):
        if not path.is_file():
            raise FixedBundleAcceptanceError(f"code fingerprint input is missing: {path}")
        try:
            label = path.relative_to(ENGINE_ROOT).as_posix()
        except ValueError:
            label = str(path)
        manifest.append({"path": label, "sha256": _sha256_bytes(path.read_bytes())})
    return _sha256_json(manifest)


def _safe_provider_projection(provider: dict[str, Any]) -> dict[str, Any]:
    allowed = (
        "name",
        "type",
        "providerKind",
        "api_standard",
        "base_url",
        "defaultChannelId",
        "is_enabled",
    )
    return {key: copy.deepcopy(provider.get(key)) for key in allowed if key in provider}


def _safe_model_projection(model: dict[str, Any]) -> dict[str, Any]:
    allowed = (
        "type",
        "contextWindow",
        "maxTokens",
        "capabilities",
        "capabilityClass",
        "parameterProfile",
        "endpointBinding",
        "reasoningSurface",
        "reasoningEffortControl",
        "thinkingControl",
        "runtimeReady",
        "isEnabled",
    )
    return {key: copy.deepcopy(model.get(key)) for key in allowed if key in model}


def config_fingerprint(projection: dict[str, Any]) -> str:
    return _sha256_json(projection)


def _no_think_request_projection(request_kwargs: dict[str, Any]) -> dict[str, Any]:
    extra_body = dict(request_kwargs.get("extra_body") or {})
    thinking = dict(request_kwargs.get("thinking") or extra_body.get("thinking") or {})
    reasoning = dict(request_kwargs.get("reasoning") or {})
    projection = {
        "thinkingType": thinking.get("type"),
        "enableThinking": extra_body.get("enable_thinking"),
        "reasoningEffort": request_kwargs.get("reasoning_effort") or reasoning.get("effort"),
        "thinkingBudget": request_kwargs.get("thinking_budget"),
    }
    return {key: value for key, value in projection.items() if value is not None}


def _request_disables_thinking(request_kwargs: dict[str, Any]) -> bool:
    projection = _no_think_request_projection(request_kwargs)
    return bool(
        str(projection.get("thinkingType") or "").lower() == "disabled"
        or projection.get("enableThinking") is False
        or str(projection.get("reasoningEffort") or "").lower() == "none"
        or projection.get("thinkingBudget") == 0
    )


def resolve_config_projection(
    transaction_ids: list[str] | tuple[str, ...],
    *,
    require_no_think: bool,
) -> dict[str, Any]:
    if str(ENGINE_ROOT) not in sys.path:
        sys.path.insert(0, str(ENGINE_ROOT))
    from core.config_broker_service import config_broker_service
    from core.llm_factory import llm_factory
    from core.model_control_plane import model_control_plane

    normalized_ids = sorted({str(item or "").strip() for item in transaction_ids if str(item or "").strip()})
    if not normalized_ids:
        raise FixedBundleAcceptanceError("at least one committed config transaction is required")
    if len(normalized_ids) != len(transaction_ids):
        raise FixedBundleAcceptanceError("config transaction IDs must be non-empty and unique")

    config = model_control_plane.get_config()
    bindings = dict(config.get("bindings") or {})
    agent_bindings = dict(bindings.get("agents") or {})
    raw_transactions: list[tuple[str, dict[str, Any]]] = []
    for transaction_id in normalized_ids:
        transaction = config_broker_service.get_transaction(transaction_id)
        if str(transaction.get("state") or "") != "committed":
            raise FixedBundleAcceptanceError(
                f"config transaction is not committed: {transaction_id}"
            )
        if str(transaction.get("targetKind") or "") not in {
            "agent_model_role",
            "model_thinking_control",
        }:
            raise FixedBundleAcceptanceError(
                f"unsupported config transaction target: {transaction_id}"
            )
        raw_transactions.append((transaction_id, transaction))

    agent_transactions = [
        item for item in raw_transactions if str(item[1].get("targetKind") or "") == "agent_model_role"
    ]
    thinking_transactions = [
        item for item in raw_transactions if str(item[1].get("targetKind") or "") == "model_thinking_control"
    ]
    if len(agent_transactions) != len(REQUIRED_RESEARCH_AGENT_IDS):
        raise FixedBundleAcceptanceError(
            "fixed acceptance requires exactly two Research agent model assignments"
        )

    transactions: list[dict[str, Any]] = []
    model_refs: set[str] = set()
    relevant_agents: dict[str, Any] = {}
    for transaction_id, transaction in agent_transactions:
        result = dict(transaction.get("result") or {})
        agent_id = str(result.get("agentId") or transaction.get("targetId") or "").strip()
        model_ref = str(result.get("modelRef") or "").strip()
        actual = dict(agent_bindings.get(agent_id) or {})
        actual_ref = str(actual.get("model_id") or actual.get("modelId") or "").strip()
        if not agent_id or not model_ref or actual_ref != model_ref:
            raise FixedBundleAcceptanceError(
                f"config transaction is no longer the effective binding: {transaction_id}"
            )
        model_refs.add(model_ref)
        relevant_agents[agent_id] = {"modelRef": actual_ref}
        transactions.append(
            {
                "transactionId": transaction_id,
                "targetKind": transaction.get("targetKind"),
                "targetId": transaction.get("targetId"),
                "operation": transaction.get("operation"),
                "state": transaction.get("state"),
                "planDigest": transaction.get("planDigest"),
                "result": {"agentId": agent_id, "modelRef": model_ref},
            }
        )
    if set(relevant_agents) != REQUIRED_RESEARCH_AGENT_IDS:
        raise FixedBundleAcceptanceError(
            "config transactions do not bind both required Research agents"
        )

    for transaction_id, transaction in thinking_transactions:
        result = dict(transaction.get("result") or {})
        model_ref = str(result.get("modelRef") or transaction.get("targetId") or "").strip()
        if not model_ref or model_ref not in model_refs:
            raise FixedBundleAcceptanceError(
                f"thinking-control transaction is unrelated to an effective Research model: {transaction_id}"
            )
        record = model_control_plane.get_model_record(model_ref, config)
        if not isinstance(record, dict) or not record:
            raise FixedBundleAcceptanceError(f"effective model record is missing: {model_ref}")
        actual_control = dict((record.get("model") or {}).get("thinkingControl") or {})
        expected_disabled = result.get("thinkingDisabled")
        if type(expected_disabled) is not bool or actual_control.get("disabled") is not expected_disabled:
            raise FixedBundleAcceptanceError(
                f"thinking-control transaction is no longer effective: {transaction_id}"
            )
        validation = dict(transaction.get("validation") or {})
        transactions.append(
            {
                "transactionId": transaction_id,
                "targetKind": transaction.get("targetKind"),
                "targetId": transaction.get("targetId"),
                "operation": transaction.get("operation"),
                "state": transaction.get("state"),
                "planDigest": transaction.get("planDigest"),
                "result": {
                    "modelRef": model_ref,
                    "thinkingDisabled": expected_disabled,
                    "verified": result.get("verified") is True,
                },
                "validation": {
                    "thinkingControl": dict(validation.get("thinkingControl") or {}),
                    "connection": dict(validation.get("connection") or {}),
                },
            }
        )

    models: dict[str, Any] = {}
    for model_ref in sorted(model_refs):
        record = model_control_plane.get_model_record(model_ref, config)
        if not isinstance(record, dict) or not record:
            raise FixedBundleAcceptanceError(f"effective model record is missing: {model_ref}")
        model = _safe_model_projection(dict(record.get("model") or {}))
        thinking = dict(model.get("thinkingControl") or {})
        if require_no_think and thinking.get("disabled") is not True:
            raise FixedBundleAcceptanceError(
                f"effective model is not configured with thinking disabled: {model_ref}"
            )
        models[model_ref] = {
            "providerId": record.get("provider_id"),
            "modelId": record.get("model_id"),
            "modelRef": record.get("model_ref"),
            "provider": _safe_provider_projection(dict(record.get("provider") or {})),
            "model": model,
        }

    effective_requests: dict[str, Any] = {}
    for agent_id, binding in sorted(relevant_agents.items()):
        model_ref = str(binding.get("modelRef") or "")
        llm = llm_factory.create_chat_model(
            model_ref,
            temperature=0.0,
            max_tokens=64,
            _role=agent_id,
        )
        request_kwargs = dict(getattr(llm, "_model_kwargs", {}) or {})
        request_projection = _no_think_request_projection(request_kwargs)
        if require_no_think and not _request_disables_thinking(request_kwargs):
            raise FixedBundleAcceptanceError(
                f"effective request does not carry a no-think control: {agent_id}"
            )
        effective_requests[agent_id] = request_projection

    roles = dict(config.get("roles") or {})
    return {
        "transactionIds": normalized_ids,
        "transactions": sorted(
            transactions,
            key=lambda item: (
                str(item.get("targetKind") or ""),
                str(item.get("targetId") or ""),
                str(item.get("transactionId") or ""),
            ),
        ),
        "agents": relevant_agents,
        "roles": {
            key: roles.get(key)
            for key in ("default", "supervisor", "subagent", "summary")
        },
        "models": models,
        "effectiveRequests": effective_requests,
        "requireNoThink": bool(require_no_think),
    }


def binding_key(
    *,
    bundle_sha256: str,
    question_sha256: str,
    code_sha256: str,
    config_sha256: str,
) -> str:
    return _sha256_json(
        {
            "bundleDigest": bundle_sha256,
            "questionDigest": question_sha256,
            "codeFingerprint": code_sha256,
            "configFingerprint": config_sha256,
        }
    )


def build_binding_snapshot(
    *,
    bundle: dict[str, Any],
    config_transaction_ids: list[str] | tuple[str, ...],
    require_no_think: bool,
    code_paths: tuple[Path, ...] | list[Path] = DEFAULT_CODE_PATHS,
) -> dict[str, Any]:
    config_projection = resolve_config_projection(
        config_transaction_ids,
        require_no_think=require_no_think,
    )
    snapshot = {
        "bundleDigest": bundle_digest(bundle),
        "questionDigest": question_digest(bundle),
        "codeFingerprint": code_fingerprint(code_paths),
        "configFingerprint": config_fingerprint(config_projection),
        "configTransactionIds": config_projection["transactionIds"],
        "effectiveAgentModels": {
            key: value.get("modelRef")
            for key, value in config_projection["agents"].items()
        },
        "thinkingDisabled": {
            model_ref: dict(value.get("model") or {})
            .get("thinkingControl", {})
            .get("disabled")
            is True
            for model_ref, value in config_projection["models"].items()
        },
    }
    snapshot["bindingKey"] = binding_key(
        bundle_sha256=snapshot["bundleDigest"],
        question_sha256=snapshot["questionDigest"],
        code_sha256=snapshot["codeFingerprint"],
        config_sha256=snapshot["configFingerprint"],
    )
    return snapshot


def load_attempt_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            raise AttemptLogError(f"blank attempt-log line at {line_number}")
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AttemptLogError(f"corrupt attempt-log line {line_number}: {exc}") from exc
        if not isinstance(event, dict):
            raise AttemptLogError(f"attempt-log line {line_number} is not an object")
        events.append(event)
    _attempt_index(events)
    return events


def append_attempt_event(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _canonical_json_bytes(event) + b"\n"
    descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextmanager
def attempt_log_lock(path: Path) -> Iterator[None]:
    lock_path = path.with_name(f"{path.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+b")
    if handle.seek(0, os.SEEK_END) == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:  # pragma: no cover - exercised by Linux CI.
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        raise AttemptLogError("another fixed-bundle acceptance process holds the log lock") from exc
    try:
        yield
    finally:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:  # pragma: no cover - exercised by Linux CI.
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _attempt_index(
    events: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    started: dict[str, dict[str, Any]] = {}
    terminal: dict[str, dict[str, Any]] = {}
    for index, event in enumerate(events, start=1):
        event_name = str(event.get("event") or "")
        attempt_id = str(event.get("attemptId") or "").strip()
        event_binding = str(event.get("bindingKey") or "").strip()
        if not attempt_id or not event_binding:
            raise AttemptLogError(f"attempt-log event {index} lacks attemptId or bindingKey")
        if event_name == "attempt_started":
            if attempt_id in started:
                raise AttemptLogError(f"duplicate attempt_started for {attempt_id}")
            started[attempt_id] = event
            continue
        if event_name not in TERMINAL_EVENTS:
            raise AttemptLogError(f"unknown attempt-log event: {event_name}")
        if attempt_id not in started:
            raise AttemptLogError(f"terminal event has no start: {attempt_id}")
        if attempt_id in terminal:
            raise AttemptLogError(f"duplicate terminal event for {attempt_id}")
        if event_binding != str(started[attempt_id].get("bindingKey") or ""):
            raise AttemptLogError(f"binding changed inside attempt {attempt_id}")
        terminal[attempt_id] = event
    return started, terminal


def unfinished_attempts(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    started, terminal = _attempt_index(events)
    return [event for key, event in started.items() if key not in terminal]


def attempt_qualified(event: dict[str, Any]) -> bool:
    search_calls = event.get("evidenceSearchCalls")
    read_calls = event.get("evidenceReadCalls")
    issues = event.get("highQualityIssues")
    return bool(
        event.get("event") == "attempt_finished"
        and event.get("terminalStatus") == "completed"
        and event.get("reviewDecision") == "accept"
        and event.get("qualified") is True
        and event.get("bindingValid") is True
        and event.get("formalRetry") is False
        and type(search_calls) is int
        and search_calls == 0
        and type(read_calls) is int
        and read_calls == 0
        and type(issues) is list
        and not issues
        and event.get("zeroAdditionalEvidenceAcquisition") is True
        and event.get("zeroNetworkClaimPermitted") is False
    )


def result_artifact_valid(event: dict[str, Any]) -> bool:
    result_ref = str(event.get("resultRef") or "").strip()
    expected_digest = str(event.get("resultSha256") or "").strip().lower()
    if not result_ref or len(expected_digest) != 64:
        return False
    path = Path(result_ref)
    try:
        return path.is_file() and _sha256_bytes(path.read_bytes()) == expected_digest
    except OSError:
        return False


def replay_streak(
    events: list[dict[str, Any]],
    *,
    expected_binding: str,
    verify_artifacts: bool = False,
) -> int:
    started, terminal = _attempt_index(events)
    if len(started) != len(terminal):
        return 0
    streak = 0
    current_binding = ""
    for event in events:
        if str(event.get("event") or "") not in TERMINAL_EVENTS:
            continue
        event_binding = str(event.get("bindingKey") or "")
        if event_binding != current_binding:
            current_binding = event_binding
            streak = 0
        if attempt_qualified(event) and (
            not verify_artifacts or result_artifact_valid(event)
        ):
            streak += 1
        else:
            streak = 0
    return streak if current_binding == expected_binding else 0


def abandoned_events(events: list[dict[str, Any]], *, abandoned_at: str) -> list[dict[str, Any]]:
    return [
        {
            "event": "attempt_abandoned",
            "attemptId": str(started.get("attemptId") or ""),
            "bindingKey": str(started.get("bindingKey") or ""),
            "terminalStatus": "abandoned",
            "qualified": False,
            "reason": "previous process ended without a terminal event",
            "finishedAt": abandoned_at,
        }
        for started in unfinished_attempts(events)
    ]


@contextmanager
def forbid_evidence_acquisition(research_module: Any) -> Iterator[dict[str, int]]:
    counters = {"search": 0, "read": 0}
    original_router_search = research_module._source_router_search
    original_web_search = research_module.web_search.func
    original_web_read = research_module.web_read.func

    def forbidden_search(*_args: Any, **_kwargs: Any) -> str:
        counters["search"] += 1
        raise EvidenceAcquisitionForbidden("fixed bundle forbids evidence search")

    def forbidden_read(*_args: Any, **_kwargs: Any) -> str:
        counters["read"] += 1
        raise EvidenceAcquisitionForbidden("fixed bundle forbids evidence read")

    research_module._source_router_search = forbidden_search
    research_module.web_search.func = forbidden_search
    research_module.web_read.func = forbidden_read
    try:
        yield counters
    finally:
        research_module._source_router_search = original_router_search
        research_module.web_search.func = original_web_search
        research_module.web_read.func = original_web_read


def _result_assessment(result: dict[str, Any]) -> dict[str, Any]:
    if str(ENGINE_ROOT) not in sys.path:
        sys.path.insert(0, str(ENGINE_ROOT))
    from core.tools.research_quality import (
        research_acceptance_metrics,
        research_high_quality_issues,
        research_review_decision,
    )

    metrics = research_acceptance_metrics(result)
    model_synthesis = (
        result.get("modelSynthesis")
        if isinstance(result.get("modelSynthesis"), dict)
        else {}
    )
    issues = list(research_high_quality_issues(result))
    writer_mode = str(model_synthesis.get("writerMode") or "").strip()
    writer_section_count = model_synthesis.get("writerSectionCount")
    if not writer_mode.startswith("segmented"):
        issues.append(f"fixed_writer_not_segmented:{writer_mode or 'missing'}")
    if (
        isinstance(writer_section_count, bool)
        or not isinstance(writer_section_count, int)
        or writer_section_count < 2
    ):
        issues.append("fixed_writer_section_count_invalid")
    fixed_metric_floors = (
        ("effectiveAnswerChars", FIXED_TARGET_EFFECTIVE_CHARS, "fixed_answer_depth_not_met"),
        ("answerCitedSourceCount", FIXED_TARGET_CITED_SOURCES, "fixed_cited_source_target_not_met"),
        ("distinctHostCount", FIXED_TARGET_DISTINCT_HOSTS, "fixed_host_target_not_met"),
        ("independentReviewCount", FIXED_TARGET_REVIEW_COUNT, "fixed_review_count_not_met"),
    )
    for metric_name, floor, issue_name in fixed_metric_floors:
        value = metrics.get(metric_name)
        if isinstance(value, bool) or not isinstance(value, int) or value < floor:
            issues.append(f"{issue_name}:{floor}")
    return {
        "reviewDecision": research_review_decision(result),
        "highQualityIssues": list(dict.fromkeys(issues)),
        "qualityMetrics": metrics,
        "writerMode": writer_mode,
        "writerSectionCount": writer_section_count,
        "providerModels": [
            str(value)
            for value in (
                model_synthesis.get("modelId"),
                model_synthesis.get("reviewerModelId"),
            )
            if str(value or "").strip()
        ],
    }


def _write_result_artifact(result_dir: Path, attempt_id: str, result: dict[str, Any]) -> tuple[Path, str]:
    result_dir.mkdir(parents=True, exist_ok=True)
    path = result_dir / f"{attempt_id}.result.json"
    payload = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
    path.write_bytes(payload)
    return path, _sha256_bytes(payload)


def run_attempt(
    *,
    bundle: dict[str, Any],
    snapshot: dict[str, Any],
    result_dir: Path,
    binding_probe: Callable[[], dict[str, Any]],
    on_started: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if str(ENGINE_ROOT) not in sys.path:
        sys.path.insert(0, str(ENGINE_ROOT))
    from core.tools import research_broker as research_module

    attempt_id = f"fixed-{uuid.uuid4().hex}"
    started = {
        "event": "attempt_started",
        "attemptId": attempt_id,
        **snapshot,
        "startedAt": _utc_now_iso(),
        "providerNetworkMode": "live-allowed",
    }
    if on_started is not None:
        on_started(started)
    counters = {"search": 0, "read": 0}
    terminal_status = "exception"
    result: dict[str, Any] = {}
    error: dict[str, str] | None = None
    try:
        with forbid_evidence_acquisition(research_module) as guarded_counters:
            counters = guarded_counters
            result = research_module._web_research_architect_pack(
                question=str(bundle.get("question") or ""),
                source_matrix=copy.deepcopy(list(bundle.get("sourceMatrix") or [])),
                shards=copy.deepcopy(list(bundle.get("shards") or [])),
                confidence=str(bundle.get("confidence") or "medium"),
                average_authority=float(bundle.get("authorityScore") or 0.0),
                freshness=str(bundle.get("freshness") or "auto"),
                architect_call_state={},
            )
        terminal_status = "completed"
    except Exception as exc:  # noqa: BLE001 - terminal evidence must be recorded fail-closed.
        error = {
            "type": type(exc).__name__,
            "message": str(exc)[:1000],
        }

    assessment = _result_assessment(result) if result else {
        "reviewDecision": "",
        "highQualityIssues": ["result_missing"],
        "qualityMetrics": {},
        "providerModels": [],
        "writerMode": "",
        "writerSectionCount": None,
    }
    binding_valid = False
    try:
        binding_valid = binding_probe().get("bindingKey") == snapshot.get("bindingKey")
    except Exception as exc:  # noqa: BLE001 - binding drift is a failed formal attempt.
        terminal_status = "binding_recheck_failed"
        error = {"type": type(exc).__name__, "message": str(exc)[:1000]}

    result_ref = ""
    result_sha256 = ""
    if result:
        try:
            result_path, result_sha256 = _write_result_artifact(result_dir, attempt_id, result)
            result_ref = str(result_path.resolve())
        except Exception as exc:  # noqa: BLE001
            terminal_status = "artifact_write_failed"
            error = {"type": type(exc).__name__, "message": str(exc)[:1000]}

    qualified = bool(
        terminal_status == "completed"
        and assessment["reviewDecision"] == "accept"
        and not assessment["highQualityIssues"]
        and counters["search"] == 0
        and counters["read"] == 0
        and binding_valid
    )
    finished = {
        "event": "attempt_finished",
        "attemptId": attempt_id,
        "bindingKey": snapshot["bindingKey"],
        "terminalStatus": terminal_status,
        "reviewDecision": assessment["reviewDecision"],
        "highQualityIssues": assessment["highQualityIssues"],
        "qualityMetrics": assessment["qualityMetrics"],
        "writerMode": assessment["writerMode"],
        "writerSectionCount": assessment["writerSectionCount"],
        "providerModels": assessment["providerModels"],
        "evidenceSearchCalls": counters["search"],
        "evidenceReadCalls": counters["read"],
        "providerCallsObserved": None,
        "providerNetworkMode": "live-allowed",
        "zeroAdditionalEvidenceAcquisition": counters["search"] == 0 and counters["read"] == 0,
        "zeroNetworkClaimPermitted": False,
        "formalRetry": False,
        "bindingValid": binding_valid,
        "qualified": qualified,
        "resultRef": result_ref or None,
        "resultSha256": result_sha256 or None,
        "error": error,
        "finishedAt": _utc_now_iso(),
    }
    return started, finished


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one append-only fixed-bundle Research acceptance attempt."
    )
    parser.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE_PATH)
    parser.add_argument("--bundle-id", default="")
    parser.add_argument("--attempt-log", type=Path, default=DEFAULT_ATTEMPT_LOG)
    parser.add_argument("--result-dir", type=Path, default=None)
    parser.add_argument("--config-transaction", action="append", default=[])
    parser.add_argument("--required-streak", type=int, default=2)
    parser.add_argument("--require-no-think", action="store_true")
    return parser


def _execute_main(args: argparse.Namespace, *, result_dir: Path) -> int:
    existing_events = load_attempt_events(args.attempt_log)
    for abandoned in abandoned_events(existing_events, abandoned_at=_utc_now_iso()):
        append_attempt_event(args.attempt_log, abandoned)
        existing_events.append(abandoned)

    def probe() -> dict[str, Any]:
        current_bundle = load_fixed_bundle(args.bundle, bundle_id=args.bundle_id)
        return build_binding_snapshot(
            bundle=current_bundle,
            config_transaction_ids=args.config_transaction,
            require_no_think=args.require_no_think,
        )

    bundle = load_fixed_bundle(args.bundle, bundle_id=args.bundle_id)
    snapshot = build_binding_snapshot(
        bundle=bundle,
        config_transaction_ids=args.config_transaction,
        require_no_think=args.require_no_think,
    )

    def record_started(event: dict[str, Any]) -> None:
        append_attempt_event(args.attempt_log, event)
        existing_events.append(event)

    _started, finished = run_attempt(
        bundle=bundle,
        snapshot=snapshot,
        result_dir=result_dir,
        binding_probe=probe,
        on_started=record_started,
    )
    finished["streakAfter"] = replay_streak(
        [*existing_events, finished],
        expected_binding=snapshot["bindingKey"],
        verify_artifacts=True,
    )
    append_attempt_event(args.attempt_log, finished)
    events = [*existing_events, finished]
    streak = replay_streak(
        events,
        expected_binding=snapshot["bindingKey"],
        verify_artifacts=True,
    )
    summary = {
        "ok": finished["qualified"] is True,
        "attemptId": finished["attemptId"],
        "bindingKey": snapshot["bindingKey"],
        "reviewDecision": finished["reviewDecision"],
        "highQualityIssues": finished["highQualityIssues"],
        "writerMode": finished["writerMode"],
        "writerSectionCount": finished["writerSectionCount"],
        "evidenceSearchCalls": finished["evidenceSearchCalls"],
        "evidenceReadCalls": finished["evidenceReadCalls"],
        "zeroAdditionalEvidenceAcquisition": finished["zeroAdditionalEvidenceAcquisition"],
        "providerNetworkMode": "live-allowed",
        "zeroNetworkClaimPermitted": False,
        "streak": streak,
        "requiredStreak": args.required_streak,
        "reachedRequiredStreak": streak >= args.required_streak,
        "resultRef": finished.get("resultRef"),
        "configTransactionIds": snapshot["configTransactionIds"],
        "effectiveAgentModels": snapshot["effectiveAgentModels"],
        "thinkingDisabled": snapshot["thinkingDisabled"],
        "terminalStatus": finished["terminalStatus"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if finished["terminalStatus"] != "completed":
        return 4
    if finished["qualified"] is not True:
        return 3
    return 0 if streak >= args.required_streak else 2


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    if args.required_streak < 1:
        print(json.dumps({"ok": False, "error": "required streak must be positive"}))
        return 4
    result_dir = args.result_dir or (args.attempt_log.parent / "attempts")
    try:
        with attempt_log_lock(args.attempt_log):
            return _execute_main(args, result_dir=result_dir)
    except Exception as exc:  # noqa: BLE001 - a malformed ledger must fail closed.
        print(
            json.dumps(
                {
                    "ok": False,
                    "terminalStatus": "preflight_failed",
                    "error": {"type": type(exc).__name__, "message": str(exc)[:1000]},
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
