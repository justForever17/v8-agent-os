"""Opt-in six-request schema comparison; returned tools are never executed.

Fresh identical short prompts, one configured Supervisor, alternating captured
public / internal schemas. This does not reproduce the full runtime context.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import sqlite3
import sys
import time


def load_public_schema(path: Path) -> dict:
    schemas = []
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row.get("boundary") != "openai_final_request_payload":
            continue
        for schema in row.get("tools", []):
            if schema.get("name") != "delegation_broker":
                continue
            tasks = schema.get("parameters", {}).get("properties", {}).get("tasks", {})
            array = next((item for item in tasks.get("anyOf", []) if item.get("type") == "array"), {})
            variants = array.get("items", {}).get("anyOf", [])
            if any("targetAgentName" in item.get("required", []) for item in variants):
                schemas.append(schema)
    if not schemas or any(schema != schemas[0] for schema in schemas):
        raise ValueError("capture_requires_one_consistent_supervisor_public_schema")
    return {"type": "function", "function": schemas[0]}


def guard(args) -> dict:
    if not args.live:
        raise ValueError("explicit_live_required")
    state = args.state_root.resolve()
    configured = os.environ.get("V8_AGENT_OS_HOME", "")
    if (not configured or Path(configured).resolve() != state or state == (Path.home() / ".v8-agent-os").resolve()
            or not (state / "state.db").is_file()):
        raise ValueError("existing_isolated_state_and_matching_environment_required")
    if args.output_dir.exists():
        raise ValueError("output_exists_do_not_repeat_live")
    if "cross-graph-live" not in args.marker or len(args.marker) < 16:
        raise ValueError("synthetic_marker_required")
    with sqlite3.connect((state / "state.db").as_uri() + "?mode=ro", uri=True) as connection:
        row = connection.execute("SELECT status FROM run_records WHERE id=?", (args.source_run_id,)).fetchone()
    if not row or row[0] not in {"completed", "failed", "cancelled", "aborted", "error"}:
        raise ValueError("source_run_must_be_terminal")
    return load_public_schema(args.captured_schema)


def response_checks(response, public_validator, internal_validator, expected: dict) -> dict:
    calls = list(getattr(response, "tool_calls", None) or [])
    result = {"toolCallCount": len(calls), "publicValid": False, "internalValid": False, "matchesExplicitTask": False}
    if len(calls) != 1 or calls[0].get("name") != "delegation_broker":
        return result
    arguments = calls[0].get("args")
    # No JSON coercion, repair, tool invocation, or permission inference.
    for name, validator in (("public", public_validator), ("internal", internal_validator)):
        try:
            validator.model_validate(arguments)
        except Exception as error:
            result[name + "ErrorCategory"] = type(error).__name__
        else:
            result[name + "Valid"] = True
    result["matchesExplicitTask"] = arguments == expected
    return result


def run_comparison(args, public_schema: dict) -> dict:
    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_core.utils.function_calling import convert_to_openai_tool
    from core.llm_factory import llm_factory
    from core.model_token_policy import resolve_output_token_budget, OUTPUT_TOKEN_KEYS
    from core.tools.native.delegation import delegation_broker
    from core.tools.native.delegation_surface import supervisor_delegation_broker
    from tests.scripts.run_cross_graph_provider_capture import ScopedCapture, _hash

    internal_schema = convert_to_openai_tool(delegation_broker)
    current_public = convert_to_openai_tool(supervisor_delegation_broker)
    if public_schema["function"].get("parameters") != current_public["function"].get("parameters"):
        raise ValueError("captured_schema_and_public_validator_differ")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    capture = ScopedCapture(args.marker, args.output_dir / "capture.jsonl")
    capture.install()
    schemas = {"capturedPublic": public_schema, "internal": internal_schema}
    expected = {"mode": "dispatch", "tasks": [{"targetAgentName": "Verification Engineer", "taskBriefId": "schema-probe-A",
                "goal": "Read the synthetic input and return a verification summary.", "expectedOutputs": ["Verification summary"],
                "acceptanceContract": ["Report actual read evidence"], "readOnly": True, "writeRequired": False,
                "readSet": ["synthetic-input.txt"], "writeSet": [], "allowChildDelegation": False}]}
    messages = [SystemMessage(content=args.marker + ". Isolated schema diagnostic. Return exactly one native tool call; no tool will execute."),
                HumanMessage(content="Call delegation_broker with these exact complete arguments. Preserve JSON arrays/objects and all fields:\n"
                             + json.dumps(expected, ensure_ascii=False))]
    report = {"evidenceClass": "short synthetic prompt schema comparison; no runtime dispatch or joint acceptance",
              "sourceRunId": args.source_run_id, "model": args.expected_model, "results": [],
              "promptSha256": _hash("\n".join(message.content for message in messages)),
              "schemaSha256": {key: _hash(json.dumps(value, ensure_ascii=False, sort_keys=True)) for key, value in schemas.items()}}
    report_path = args.output_dir / "result.json"
    for repetition in range(3):
        for group in ("capturedPublic", "internal"):
            started = time.monotonic()
            row = {"group": group, "repetition": repetition + 1}
            try:
                # Disable transport retries explicitly; do not use failover or
                # a result-correction service. Each of the six samples is fresh.
                model = llm_factory.create_for_role("supervisor", streaming=True, max_retries=0)
                if model.model_id != args.expected_model:
                    raise ValueError("configured_model_changed")
                budget = resolve_output_token_budget(model._meta)
                if budget != {"mode": "auto", "maxTokens": None, "source": "provider_default"}:
                    raise ValueError("configured_output_policy_changed")
                if any(model._model_kwargs.get(key) is not None for key in OUTPUT_TOKEN_KEYS):
                    raise ValueError("constructor_output_cap_present")
                bound = model.bind_tools([deepcopy(schemas[group])], tool_choice="required")
                runtime_model = bound._get_runtime_model()
                sdk = getattr(runtime_model, "bound", runtime_model)
                if getattr(sdk, "max_retries", None) != 0:
                    raise ValueError("transport_retries_not_disabled")
                aggregate = None
                for chunk in bound.stream(deepcopy(messages)):
                    aggregate = chunk if aggregate is None else aggregate + chunk
                row.update(response_checks(aggregate, supervisor_delegation_broker.args_schema, delegation_broker.args_schema, expected))
                row["adapterAccepted"] = True
            except Exception as error:
                row.update(adapterAccepted=False, errorCategory=type(error).__name__)
                details = getattr(error, "details", None) or {}
                reason = details.get("reason")
                row["reason"] = reason if reason in {"incomplete_tool_arguments", "invalid_tool_arguments", "output_limit", "tool_protocol_in_text"} else None
                # Configuration drift is an experiment guard, not a failed
                # provider sample. Stop without issuing the remaining calls.
                if isinstance(error, ValueError) and str(error) in {"configured_model_changed", "configured_output_policy_changed",
                        "constructor_output_cap_present", "transport_retries_not_disabled"}:
                    row["guardFailure"] = str(error)
            finally:
                row["elapsedSeconds"] = round(time.monotonic() - started, 3)
                capture_id = (capture.invocation.get() or {}).get("captureId")
                row["captureId"] = capture_id
                row["rawArgumentsComplete"] = None
                if capture_id and capture.output.exists():
                    captured = [json.loads(line) for line in capture.output.read_text(encoding="utf-8").splitlines()]
                    result = next((item for item in reversed(captured) if item.get("captureId") == capture_id
                                   and item.get("boundary") == "openai_sdk_assembled_response"), None)
                    if result:
                        raw = result.get("argumentViews", {}).get("tool_call_chunks", [])
                        row["rawArgumentsComplete"] = bool(raw) and all(item["arguments"].get("completeObject") for item in raw)
                        row["finishReason"] = result.get("finishReason")
                        row["usage"] = result.get("usage")
                        row["streamEnd"] = result.get("streamEnd")
                report["results"].append(row)
                report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
            if row.get("guardFailure"):
                return report
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--source-run-id", required=True)
    parser.add_argument("--captured-schema", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--expected-model", required=True)
    parser.add_argument("--marker", required=True)
    args = parser.parse_args(argv)
    try:
        schema = guard(args)
    except (ValueError, OSError, sqlite3.Error) as error:
        parser.error(type(error).__name__ + ": diagnostic preflight failed")
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    report = run_comparison(args, schema)
    print(json.dumps({"output": str(args.output_dir / "result.json"), "samples": len(report["results"])}))
    return 1 if any(row.get("guardFailure") for row in report["results"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
