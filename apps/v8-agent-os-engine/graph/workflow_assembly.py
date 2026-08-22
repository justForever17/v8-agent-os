import asyncio
import json
import os
import time
from datetime import datetime, timezone

from langgraph.graph import StateGraph
from langchain_core.messages import HumanMessage
from langgraph.types import Command

from core.database import db
from core.runtime_tool_access import filter_visible_tools_for_actor
from core.runtime_episodes import (
    ACTIVE_EPISODE_STATES,
    TERMINAL_EPISODE_STATES,
    append_handoff_ref,
    emit_runtime_episode_event,
    resolve_runtime_episode_current_handoff,
    runtime_episode_parent_id,
    superseded_runtime_episode_ids,
    transition_runtime_episode,
)
from core.time_truth import utc_now_iso
from erc.runtime_context import get_runtime_context
from erc.runtime_stability import runtime_stability_service
from .parallel_support import (
    RUNTIME_EPISODE_WAIT_NODE,
    build_parallel_delegate_join_node,
    build_parallel_delegate_task_node,
)


RUNTIME_EPISODE_WAIT_SECONDS = float(os.getenv("V8_RUNTIME_EPISODE_WAIT_SECONDS", "600"))
RUNTIME_EPISODE_QUEUE_GRACE_SECONDS = float(os.getenv("V8_RUNTIME_EPISODE_QUEUE_GRACE_SECONDS", "60"))
RUNTIME_EPISODE_POLL_SECONDS = float(os.getenv("V8_RUNTIME_EPISODE_POLL_SECONDS", "0.8"))


def _merged_tool_command_update(commands: list[Command]) -> dict:
    """Merge ToolNode Command updates for one deterministic runtime transition."""
    merged: dict = {}
    messages: list = []
    for item in commands:
        update = dict(getattr(item, "update", None) or {})
        for key, value in update.items():
            if key == "messages":
                messages.extend(list(value or []))
            elif isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = {**dict(merged[key]), **value}
            else:
                merged[key] = value
    if messages:
        merged["messages"] = messages
    return merged


def _route_runtime_tool_commands(command):
    """Convert ToolNode ``Command[]`` runtime routes into graph-owned waits.

    LangGraph returns a list whenever any executed tool returns ``Command``.
    Treating that list as opaque left the queued episode stranded at the
    Supervisor node.  We preserve ordinary multi-command routing unchanged and
    collapse only the explicit runtime wait transition.
    """
    command_items = [item for item in command if isinstance(item, Command)] if isinstance(command, list) else []
    update = _merged_tool_command_update(command_items) if command_items else dict(getattr(command, "update", None) or {})
    runtime_statuses = [
        dict((getattr(item, "update", None) or {}).get("runtime_dispatch_status") or {})
        for item in command_items
        if isinstance((getattr(item, "update", None) or {}).get("runtime_dispatch_status"), dict)
    ]
    runtime_statuses = [status for status in runtime_statuses if status]
    runtime_status = dict(update.get("runtime_dispatch_status") or {})
    messages = list(update.get("messages") or [])
    waiting_status = next(
        (
            status
            for status in runtime_statuses
            if str(status.get("nextAction") or "").strip() == "wait_episode"
            and bool(status.get("dispatched", True))
        ),
        None,
    )
    should_wait = waiting_status is not None or str(runtime_status.get("nextAction") or "").strip() == "wait_episode"
    for message in messages:
        additional = dict(getattr(message, "additional_kwargs", None) or {})
        if str(additional.get("recommendedNextAction") or "").strip() == "wait_episode":
            should_wait = True
            break
    if should_wait:
        if waiting_status is not None:
            update["runtime_dispatch_status"] = waiting_status
        return Command(goto="runtime_episode", update=update)
    # Multiple runtime broker calls in one provider turn are executed by
    # ToolNode concurrently. Returning their Command[] unchanged makes
    # LangGraph apply more than one value to runtime_dispatch_status in the
    # same step. Collapse only runtime-bearing commands; ordinary multi-tool
    # routing keeps its existing semantics.
    if len(command_items) > 1 and runtime_statuses:
        return Command(goto="supervisor", update=update)
    return command


def _workflow_entry_command(state):
    runtime_status = dict((state or {}).get("runtime_dispatch_status") or {})
    if str(runtime_status.get("nextAction") or "").strip() == "wait_episode":
        return Command(goto="runtime_episode")
    return Command(goto="supervisor")


def _string_value(*values) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _state_runtime_identity(state: dict | None) -> tuple[str | None, str | None, str | None]:
    runtime_context = get_runtime_context()
    state_dict = dict(state or {})
    route_context = dict(state_dict.get("current_route_context") or {})
    session_id = _string_value(
        state_dict.get("session_id"),
        state_dict.get("sessionId"),
        route_context.get("session_id"),
        route_context.get("sessionId"),
        runtime_context.get("session_id"),
        runtime_context.get("sessionId"),
    ) or None
    run_id = _string_value(
        state_dict.get("run_id"),
        state_dict.get("runId"),
        route_context.get("run_id"),
        route_context.get("runId"),
        runtime_context.get("run_id"),
        runtime_context.get("runId"),
    ) or None
    workspace_path = _string_value(
        state_dict.get("workspace_path"),
        state_dict.get("workspacePath"),
        route_context.get("workspace_path"),
        route_context.get("workspacePath"),
        runtime_context.get("workspace_path"),
        runtime_context.get("workspacePath"),
    ) or None
    return session_id, run_id, workspace_path


def _has_live_bound_episode_lease() -> bool:
    """Return true when a runner is actively working a canonical session-bound episode."""
    now_iso = utc_now_iso()
    try:
        with db.get_connection() as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM runtime_episode_queue
                WHERE state = 'leased'
                  AND COALESCE(session_id, '') <> ''
                  AND COALESCE(run_id, '') <> ''
                  AND COALESCE(lease_expires_at, '') > ?
                LIMIT 1
                """,
                (now_iso,),
            ).fetchone()
            return bool(row)
    except Exception:
        return False


def build_runtime_episode_wait_node():
    def _route_context_episode_ids(route_context: dict) -> list[str]:
        ids: list[str] = []
        for item in list(route_context.get("capabilityEpisodes") or []):
            if not isinstance(item, dict):
                continue
            episode_id = _string_value(item.get("episodeId"), item.get("needId"), item.get("id"))
            state = str(item.get("state") or "").strip()
            if episode_id and state in ACTIVE_EPISODE_STATES and episode_id not in ids:
                ids.append(episode_id)
        return ids

    def _load_relevant_episodes(*, route_context: dict, session_id: str | None, run_id: str | None) -> list[dict]:
        by_id: dict[str, dict] = {}
        for episode_id in _route_context_episode_ids(route_context):
            try:
                episode = db.get_runtime_episode(episode_id)
            except Exception:
                episode = None
            if episode:
                route_episode = {}
                for item in list(route_context.get("capabilityEpisodes") or []):
                    if _string_value(item.get("episodeId"), item.get("needId"), item.get("id")) == episode_id:
                        route_episode = dict(item)
                        break
                by_id[str(episode.get("episodeId") or episode.get("id") or episode_id)] = {**route_episode, **dict(episode)}
            else:
                for item in list(route_context.get("capabilityEpisodes") or []):
                    if _string_value(item.get("episodeId"), item.get("needId"), item.get("id")) == episode_id:
                        by_id[episode_id] = dict(item)
                        break
        try:
            db_rows = db.list_runtime_episodes(run_id=run_id, limit=100) if run_id else []
            if not db_rows and session_id:
                db_rows = db.list_runtime_episodes(session_id=session_id, limit=100)
        except Exception:
            db_rows = []
        for episode in db_rows:
            episode_id = _string_value(episode.get("episodeId"), episode.get("id"), episode.get("needId"))
            if episode_id:
                by_id[episode_id] = {**dict(by_id.get(episode_id) or {}), **dict(episode)}
        return list(by_id.values())

    def _active_episodes(episodes: list[dict]) -> list[dict]:
        return [
            episode
            for episode in episodes
            if str(episode.get("state") or "").strip() in ACTIVE_EPISODE_STATES
        ]

    def _terminal_episodes(episodes: list[dict]) -> list[dict]:
        return [
            episode
            for episode in episodes
            if str(episode.get("state") or "").strip() in TERMINAL_EPISODE_STATES
        ]

    def _episode_queue_age_seconds(episode: dict, *, default_started_wall: float) -> float:
        raw_value = _string_value(
            episode.get("updatedAt"),
            episode.get("updated_at"),
            episode.get("createdAt"),
            episode.get("created_at"),
        )
        if raw_value:
            try:
                parsed = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return max(0.0, time.time() - parsed.timestamp())
            except Exception:
                pass
        return max(0.0, time.time() - default_started_wall)

    def _merge_handoffs(route_context: dict, episodes: list[dict]) -> tuple[dict, list[dict]]:
        updated = dict(route_context or {})
        available: list[dict] = []
        available_ids: set[str] = set()
        delivery_diagnostics = {
            str(item.get("episodeId") or "").strip(): dict(item)
            for item in list(updated.get("runtimeDeliveryDiagnostics") or [])
            if isinstance(item, dict) and str(item.get("episodeId") or "").strip()
        }
        for episode in episodes:
            episode_id = _string_value(episode.get("episodeId"), episode.get("id"), episode.get("needId"))
            if not episode_id:
                continue
            try:
                handoff_rows = db.list_runtime_episode_handoffs(episode_id)
            except Exception:
                handoff_rows = []
            payload, diagnostic = resolve_runtime_episode_current_handoff(
                episode,
                handoff_rows,
            )
            if payload is not None:
                delivery_diagnostics.pop(episode_id, None)
                handoff_id = _string_value(
                    payload.get("handoffRefId"),
                    payload.get("handoffId"),
                    payload.get("artifactId"),
                )
                if not handoff_id or handoff_id not in available_ids:
                    available.append(payload)
                    if handoff_id:
                        available_ids.add(handoff_id)
                # Re-appending the selected delivery intentionally replaces any
                # historical handoff for this producer in route_context.
                updated = append_handoff_ref(updated, payload)
                if handoff_id:
                    available_ids.add(handoff_id)
                continue

            resolution = str(diagnostic.get("resolution") or "missing_handoff")
            state = str(episode.get("state") or "").strip().lower()
            should_surface = bool(handoff_rows) or state in TERMINAL_EPISODE_STATES
            if not should_surface:
                continue
            error_code = (
                "runtime_handoff_payload_corrupted"
                if resolution == "current_handoff_payload_corrupted"
                else "runtime_result_handoff_missing"
                if resolution in {"missing_handoff", "result_ref_not_found"}
                else "runtime_result_handoff_invalid"
            )
            typed_diagnostic = {
                **diagnostic,
                "producerEpisodeId": episode_id,
                "kind": "runtime_delivery_diagnostic",
                "status": "failed",
                "optional": _is_optional_episode(episode),
                "errorCode": error_code,
                "compactSummary": (
                    f"Runtime episode {episode_id} has no safely resolvable current delivery "
                    f"({resolution})."
                ),
                "recoverable": resolution != "current_handoff_payload_corrupted",
            }
            delivery_diagnostics[episode_id] = typed_diagnostic
            available.append(typed_diagnostic)
            updated["handoffRefs"] = [
                dict(item)
                for item in list(updated.get("handoffRefs") or [])
                if isinstance(item, dict)
                and str(item.get("producerEpisodeId") or "").strip() != episode_id
            ]
        if delivery_diagnostics:
            updated["runtimeDeliveryDiagnostics"] = list(delivery_diagnostics.values())[-50:]
        else:
            updated.pop("runtimeDeliveryDiagnostics", None)
        return updated, available

    def _compact_handoff_projection(handoff: dict) -> dict:
        def _compact_source_acquisition(value: object) -> dict:
            if not isinstance(value, dict):
                return {}
            providers = [
                {
                    "provider": _string_value(item.get("provider"))[:80],
                    "state": _string_value(item.get("state"))[:80],
                    "attemptCount": int(item.get("attemptCount") or 0),
                    "failureClasses": [
                        str(reason)[:80]
                        for reason in list(item.get("failureClasses") or [])[:8]
                        if str(reason).strip()
                    ],
                }
                for item in list(value.get("providers") or [])[:16]
                if isinstance(item, dict) and _string_value(item.get("provider"))
            ]
            return {
                "state": _string_value(value.get("state"))[:80],
                "stopReason": _string_value(value.get("stopReason"))[:120],
                "exhaustedForRun": bool(value.get("exhaustedForRun")),
                "reachableButIrrelevant": bool(value.get("reachableButIrrelevant")),
                "providerCount": int(value.get("providerCount") or len(providers)),
                "readableSourceCount": int(value.get("readableSourceCount") or 0),
                "selectedSourceCount": int(value.get("selectedSourceCount") or 0),
                "failureClasses": [
                    str(reason)[:80]
                    for reason in list(value.get("failureClasses") or [])[:16]
                    if str(reason).strip()
                ],
                "providers": providers,
                "recommendedNextAction": _string_value(value.get("recommendedNextAction"))[:360],
            }

        def _is_research_result(item: dict) -> bool:
            return bool(
                item.get("answer")
                or item.get("researchRef")
                or item.get("evidenceBundleId")
                or item.get("sourceUrls")
            )

        def _is_accepted_research_result(item: dict) -> bool:
            return bool(
                _is_research_result(item)
                and item.get("acceptancePassed") is True
                and str(item.get("qualityTier") or "").strip() == "high_quality"
                and _string_value(item.get("answer"))
            )

        def _research_detail_tool(item: dict) -> str:
            detail_tool = _string_value(item.get("detailTool"))
            if detail_tool:
                return detail_tool
            evidence_bundle_id = _string_value(item.get("evidenceBundleId"))
            if not evidence_bundle_id:
                return ""
            escaped_bundle_id = evidence_bundle_id.replace("\\", "\\\\").replace("'", "\\'")
            return (
                "research_broker(mode='get_evidence', "
                f"evidenceBundleId='{escaped_bundle_id}')"
            )

        def _collect_results(value: object, *, depth: int = 0) -> list[dict]:
            if depth > 8 or not isinstance(value, dict):
                return []
            collected = [item for item in list(value.get("results") or []) if isinstance(item, dict)]
            collected.extend(
                item for item in list(value.get("taskBriefResults") or []) if isinstance(item, dict)
            )
            nested = value.get("delegationHandoff")
            if isinstance(nested, dict):
                collected.extend(_collect_results(nested, depth=depth + 1))
            for child in list(value.get("childHandoffs") or []):
                if isinstance(child, dict):
                    collected.extend(_collect_results(child, depth=depth + 1))
            return collected

        def _result_is_optional(item: dict) -> bool:
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            if item.get("required") is False or metadata.get("required") is False:
                return True
            return any(
                bool(source.get("optional") or source.get("optionalLane") or source.get("degradedOk"))
                or str(source.get("dependencyMode") or "").strip().lower()
                in {"optional", "degraded_ok"}
                for source in (item, metadata)
            )

        def _result_block_reason(item: dict) -> str:
            status = _string_value(item.get("status"), item.get("workerStatus")).strip().lower()
            accepted_research_result = _is_accepted_research_result(item)
            if status in {
                "error",
                "failed",
                "blocked",
                "dependency_failed",
                "cancelled",
                "canceled",
                "recoverable_failed",
            }:
                return f"status:{status}"
            if status == "degraded" and not accepted_research_result:
                return "status:degraded"
            if _is_research_result(item) and not accepted_research_result:
                return "research_result_not_accepted"
            error = _string_value(item.get("error"), item.get("errorCode"), item.get("errorMessage"))
            if error:
                return "typed_error"
            sandbox = item.get("sandboxEvidence") if isinstance(item.get("sandboxEvidence"), dict) else {}
            sandbox_state = _string_value(sandbox.get("state")).strip().lower()
            if sandbox_state in {"failed", "merge_failed"}:
                return f"sandbox:{sandbox_state}"
            if item.get("artifactRefsAccepted") is False:
                return "artifact_refs_rejected"
            if "acceptancePassed" in item and item.get("acceptancePassed") is False:
                return "acceptance_failed"
            return ""

        results: list[dict] = []
        result_keys: set[tuple[str, str, str]] = set()
        for item in _collect_results(handoff):
            delegation_identity = _string_value(item.get("delegationId"), item.get("invocationId"))
            task_identity = _string_value(item.get("taskBriefId"), item.get("taskId"))
            if not delegation_identity and not task_identity:
                # Anonymous results have no stable identity that makes
                # equality safe to infer. Preserve each one in arrival order.
                results.append(item)
                continue
            key = (
                delegation_identity,
                task_identity,
                _string_value(item.get("status")),
            )
            if key in result_keys:
                continue
            result_keys.add(key)
            results.append(item)
        research_handoff = bool(
            _string_value(handoff.get("kind")).strip().lower() == "research"
            or any(_is_research_result(item) for item in results)
        )
        handoff_kind = _string_value(handoff.get("kind")).strip().lower()
        delegation_handoff = bool(
            "delegation" in handoff_kind
            or isinstance(handoff.get("delegationHandoff"), dict)
            or list(handoff.get("childHandoffs") or [])
        )
        projected_result_limit = 24 if research_handoff else 8
        projected_results = results[:projected_result_limit]
        omitted_result_count = max(0, len(results) - len(projected_results))
        omitted_results = results[len(projected_results) :]
        delegation_result_failures = [
            (item, _result_block_reason(item))
            for item in results
            if delegation_handoff and _result_block_reason(item)
        ]
        required_delegation_failures = [
            (item, reason)
            for item, reason in delegation_result_failures
            if not _result_is_optional(item)
        ]
        optional_delegation_failures = [
            (item, reason)
            for item, reason in delegation_result_failures
            if _result_is_optional(item)
        ]
        omitted_required_delegation_failures = [
            (item, _result_block_reason(item))
            for item in omitted_results
            if delegation_handoff
            and _result_block_reason(item)
            and not _result_is_optional(item)
        ]
        omitted_result_identities = {id(item) for item in omitted_results}
        def _compact_failure_detail(item: dict, reason: str) -> dict:
            sandbox = item.get("sandboxEvidence") if isinstance(item.get("sandboxEvidence"), dict) else {}
            return {
                "taskBriefId": _string_value(item.get("taskBriefId"), item.get("taskId")),
                "delegationId": _string_value(item.get("delegationId"), item.get("invocationId")),
                "status": _string_value(item.get("status"), item.get("workerStatus"), "failed"),
                "reason": reason,
                "error": _string_value(item.get("error"), item.get("errorMessage"))[:900],
                "errorCode": _string_value(
                    item.get("errorCode"),
                    sandbox.get("errorCode"),
                ),
                "repairAction": _string_value(
                    item.get("repairAction"),
                    sandbox.get("repairAction"),
                )[:900],
                "omittedFromProjection": id(item) in omitted_result_identities,
            }

        blocking_result_details = [
            _compact_failure_detail(item, reason)
            for item, reason in required_delegation_failures[:24]
        ]
        optional_failure_details = [
            _compact_failure_detail(item, reason)
            for item, reason in optional_delegation_failures[:24]
        ]
        accepted_research_result_count = sum(
            1 for item in results if _is_accepted_research_result(item)
        )
        visible_research_result_index = next(
            (
                index
                for index, item in enumerate(projected_results)
                if _is_accepted_research_result(item)
            ),
            None,
        )
        compact_results: list[dict] = []
        for result_index, item in enumerate(projected_results):
            raw_artifact_refs = list(item.get("artifactRefs") or item.get("artifacts") or [])[:8]
            proof_refs = list(item.get("proofRefs") or [])[:8]
            status = _string_value(item.get("status"))
            status_lower = status.lower()
            sandbox_evidence = (
                dict(item.get("sandboxEvidence"))
                if isinstance(item.get("sandboxEvidence"), dict)
                else {}
            )
            blocking_statuses = {
                "error",
                "failed",
                "blocked",
                "dependency_failed",
                "cancelled",
            }
            is_research_result = _is_research_result(item)
            accepted_research_result = _is_accepted_research_result(item)
            research_answer_visible = bool(
                accepted_research_result
                and result_index == visible_research_result_index
            )
            blocking_result = bool(
                status_lower in blocking_statuses
                or (is_research_result and not accepted_research_result)
                or (status_lower == "degraded" and not accepted_research_result)
                or item.get("error")
                or str(sandbox_evidence.get("state") or "").strip().lower() in {"failed", "merge_failed"}
                or item.get("artifactRefsAccepted") is False
                or (delegation_handoff and _result_block_reason(item))
            )
            optional_result = _result_is_optional(item)
            artifact_refs: list[dict | str] = []
            for ref in raw_artifact_refs:
                if not blocking_result:
                    artifact_refs.append(ref)
                    continue
                candidate = dict(ref) if isinstance(ref, dict) else {"ref": str(ref)}
                candidate.update({"accepted": False, "state": "quarantined_unmerged"})
                artifact_refs.append(candidate)
            verification_results = [
                dict(result)
                for result in list(item.get("verificationResults") or [])
                if isinstance(result, dict)
            ][:4]
            verification_evidence = (
                dict(item.get("verificationEvidence"))
                if isinstance(item.get("verificationEvidence"), dict)
                else dict(item.get("verification"))
                if isinstance(item.get("verification"), dict)
                else {}
            )
            verification_passed = (not blocking_result) and (bool(verification_evidence.get("passed")) or any(
                result.get("passed") is True
                or str(result.get("status") or "").strip().lower() in {"verified", "passed", "success", "completed"}
                for result in verification_results
            ))
            verification_lines: list[str] = []
            evidence_source = verification_evidence or (verification_results[0] if verification_results else {})
            for observation in ([] if blocking_result else list(evidence_source.get("observations") or [])[:4]):
                if not isinstance(observation, dict):
                    continue
                if observation.get("path"):
                    verification_lines.append(f"read={observation.get('path')}")
                if observation.get("command"):
                    verification_lines.append(
                        f"command={observation.get('command')}; exit={observation.get('returnCode')}; "
                        f"stdout={str(observation.get('stdout') or '')!r}; stderr={str(observation.get('stderr') or '')!r}"
                    )
            evidence_complete = bool(
                not blocking_result
                and status_lower in {"ok", "completed", "ready", "success"}
                and (artifact_refs or verification_passed or accepted_research_result)
                and not item.get("error")
                and not item.get("missingArtifactEvidence")
                and not item.get("blockers")
            )
            # Research results use `answer` rather than delegation's
            # resultText/localSelfCheck. Keep one accepted answer intact and
            # project additional briefs as bounded ledger references.
            if blocking_result:
                result_text = _string_value(
                    item.get("error"),
                    item.get("errorMessage"),
                    item.get("localSelfCheck"),
                    item.get("repairAction"),
                )[:1800]
                if not result_text:
                    result_text = "Delegated result was not accepted because its execution contract or evidence failed."
            else:
                if accepted_research_result and not research_answer_visible:
                    result_text = ""
                elif research_answer_visible:
                    result_text = _string_value(item.get("answer"))
                else:
                    result_text = _string_value(
                        item.get("resultText"),
                        item.get("summary"),
                        item.get("localSelfCheck"),
                    )[:1800]
            if verification_lines:
                verification_prefix = "; ".join(verification_lines)
                if research_answer_visible:
                    result_text = verification_prefix + (f"; {result_text}" if result_text else "")
                elif not accepted_research_result:
                    result_text = (verification_prefix + (f"; {result_text}" if result_text else ""))[:1800]
            verification_summary: list[str] = []
            if blocking_result:
                verification_summary.append("accepted=false; candidate evidence is quarantined and unmerged")
            elif evidence_source:
                verification_summary.append(f"passed={bool(evidence_source.get('passed'))}")
                for file_item in list(evidence_source.get("files") or [])[:3]:
                    if not isinstance(file_item, dict):
                        continue
                    file_label = _string_value(
                        file_item.get("workspacePath"),
                        file_item.get("path"),
                        file_item.get("artifactRef"),
                    )
                    details = [file_label] if file_label else []
                    if file_item.get("mime"):
                        details.append(f"mime={file_item.get('mime')}")
                    if file_item.get("magic"):
                        details.append(f"magic={file_item.get('magic')}")
                    if file_item.get("sha256"):
                        details.append(f"sha256={str(file_item.get('sha256'))[:16]}…")
                    if details:
                        verification_summary.append("file=" + "; ".join(details))
                for flag in ("browserClosed", "applicationClosed"):
                    if flag in evidence_source:
                        verification_summary.append(f"{flag}={bool(evidence_source.get(flag))}")
                missing = [str(value) for value in list(evidence_source.get("missing") or []) if str(value).strip()]
                if missing:
                    verification_summary.append("missing=" + ", ".join(missing[:4]))
            compact_results.append(
                {
                    "taskBriefId": _string_value(item.get("taskBriefId"), item.get("taskId")),
                    "delegationId": _string_value(item.get("delegationId")),
                    "parentDelegationId": _string_value(item.get("parentDelegationId")),
                    "producerEpisodeId": _string_value(item.get("producerEpisodeId")),
                    "delegationDepth": item.get("delegationDepth"),
                    "targetLabel": _string_value(item.get("targetLabel"), item.get("agentName"), item.get("agentId")),
                    "status": status,
                    "optional": optional_result,
                    "requiredBlocking": bool(blocking_result and not optional_result),
                    "blockingReason": _result_block_reason(item) if blocking_result else "",
                    "result": result_text,
                    "artifactRefs": artifact_refs,
                    "artifactRefsAccepted": (
                        False if blocking_result else item.get("artifactRefsAccepted", True)
                    ),
                    "proofRefs": [] if blocking_result else proof_refs,
                    "missingArtifactEvidence": [
                        str(value).strip()
                        for value in list(item.get("missingArtifactEvidence") or item.get("missingExpectedArtifacts") or [])[:12]
                        if str(value).strip()
                    ],
                    "error": _string_value(item.get("error"), item.get("errorMessage")) if blocking_result else "",
                    "repairAction": _string_value(
                        item.get("repairAction"), sandbox_evidence.get("repairAction")
                    )[:900],
                    "sandboxEvidence": {
                        key: sandbox_evidence.get(key)
                        for key in (
                            "state",
                            "candidateState",
                            "errorCode",
                            "violations",
                            "writeSet",
                            "repairAction",
                        )
                        if sandbox_evidence.get(key) not in (None, "", [], {})
                    },
                    "workerReport": (
                        _string_value(item.get("workerReportedSummary"), item.get("workerReportedResultText"))[:900]
                        if blocking_result
                        else ""
                    ),
                    "toolsUsed": list(item.get("toolsUsed") or [])[:12],
                    "verificationResults": verification_results,
                    "verificationSummary": "; ".join(verification_summary)[:1200],
                    "verificationPassed": verification_passed,
                    "blockers": list(item.get("blockers") or item.get("residualRisks") or [])[:6],
                    "evidenceComplete": evidence_complete,
                    "researchRef": _string_value(item.get("researchRef"), item.get("evidenceBundleId")),
                    "evidenceBundleId": _string_value(item.get("evidenceBundleId")),
                    "detailTool": _research_detail_tool(item) if is_research_result else _string_value(item.get("detailTool")),
                    "deliveryVisible": research_answer_visible if accepted_research_result else bool(result_text),
                    "answerProjection": (
                        "full"
                        if research_answer_visible
                        else "omitted_bounded_multi_brief"
                        if accepted_research_result
                        else "rejected"
                        if is_research_result
                        else "bounded"
                    ),
                    "sourceUrls": [
                        str(value).strip()
                        for value in list(item.get("sourceUrls") or [])[:8]
                        if str(value).strip()
                    ],
                    "sources": [
                        dict(value)
                        for value in list(item.get("sources") or [])[:8]
                        if isinstance(value, dict)
                    ],
                    "sourceCount": int(item.get("sourceCount") or 0),
                    "claimCount": int(item.get("claimCount") or 0),
                    "acceptancePassed": bool(item.get("acceptancePassed")),
                    "reviewDecision": _string_value(item.get("reviewDecision")),
                    "qualityTier": _string_value(item.get("qualityTier")),
                    "qualityMetrics": dict(item.get("qualityMetrics") or {}) if isinstance(item.get("qualityMetrics"), dict) else {},
                    "asOf": _string_value(item.get("asOf")),
                    "limitations": [str(value)[:500] for value in list(item.get("limitations") or [])[:6]],
                    "criticalMissingEvidence": [
                        str(value)[:500] for value in list(item.get("criticalMissingEvidence") or [])[:8]
                    ],
                    "recommendedNextQueries": [
                        str(value)[:500] for value in list(item.get("recommendedNextQueries") or [])[:8]
                    ],
                    "evidenceStatusReasons": [
                        str(value)[:160]
                        for value in list(item.get("evidenceStatusReasons") or [])[:6]
                    ],
                    "sourceAcquisition": _compact_source_acquisition(item.get("sourceAcquisition")),
                }
            )
        visible_research_answer_count = sum(
            1
            for item in compact_results
            if item.get("acceptancePassed") is True
            and item.get("answerProjection") == "full"
        )
        omitted_research_answer_count = max(
            0,
            accepted_research_result_count - visible_research_answer_count,
        )
        projection_limited = bool(omitted_result_count or omitted_research_answer_count)
        projected_blocking_results_present = any(
            item.get("requiredBlocking") is True for item in compact_results
        )
        blocking_results_present = bool(
            required_delegation_failures
            if delegation_handoff
            else projected_blocking_results_present
        )
        nested_handoff = handoff.get("delegationHandoff")
        nested_handoff = dict(nested_handoff) if isinstance(nested_handoff, dict) else {}
        nested_refs = list(nested_handoff.get("refs") or nested_handoff.get("artifactRefs") or [])
        nested_proof_refs = list(nested_handoff.get("proofRefs") or nested_handoff.get("verificationRefs") or [])
        runtime_only_ref_values = bool(
            isinstance(handoff.get("creativeExecutionEvidence"), dict)
            or _string_value(handoff.get("kind")).strip().lower() in {"creative_media", "asset_bundle"}
        )
        evidence_coverage_complete = bool(handoff.get("coverageComplete"))
        declared_delivery_complete = (
            bool(handoff.get("deliveryComplete"))
            if "deliveryComplete" in handoff
            else evidence_coverage_complete
        )
        raw_detail_ref = _string_value(handoff.get("detailRef"), handoff.get("rawRef"))
        projected_detail_ref = raw_detail_ref if raw_detail_ref.startswith("toolobs://") else ""
        projected_detail_tool = _string_value(handoff.get("detailTool"))
        omitted_research_results = [
            item
            for item in results
            if _is_accepted_research_result(item)
            and (
                visible_research_result_index is None
                or item is not projected_results[visible_research_result_index]
            )
        ]
        research_projection_recovery_available = bool(
            omitted_research_results
            and all(_research_detail_tool(item) for item in omitted_research_results)
        )
        delegation_projection_recovery_available = bool(
            projected_detail_ref and projected_detail_tool
        )
        projection_kind = (
            "delegation"
            if delegation_handoff
            else "research"
            if research_handoff
            else "runtime"
        )
        projection_recovery_available = bool(
            projection_limited
            and (
                delegation_projection_recovery_available
                if delegation_handoff
                else research_projection_recovery_available
                if research_handoff
                else projected_detail_ref and projected_detail_tool
            )
        )
        projected_coverage_complete = bool(
            evidence_coverage_complete
            and not blocking_results_present
            and not (research_handoff and projection_limited)
            and not (
                delegation_handoff
                and projection_limited
                and not projection_recovery_available
            )
        )
        projected_delivery_complete = bool(
            declared_delivery_complete
            and not blocking_results_present
            and not (research_handoff and projection_limited)
            and (not research_handoff or evidence_coverage_complete)
            and not (
                delegation_handoff
                and projection_limited
                and not projection_recovery_available
            )
        )
        return {
            "handoffRefId": _string_value(handoff.get("handoffRefId"), handoff.get("handoffId")),
            # Only a real tool observation rawRef is legal for
            # tool_observation_detail.  Research evidence identities use
            # `researchRefs` and an explicit research_broker(get_evidence)
            # detailTool instead; never project research:// as rawRef.
            "detailRef": projected_detail_ref,
            "detailTool": projected_detail_tool,
            "producerEpisodeId": _string_value(handoff.get("producerEpisodeId"), handoff.get("episodeId")),
            "kind": _string_value(handoff.get("kind"), "runtime_handoff"),
            "status": _string_value(handoff.get("status")),
            "runtimeOnlyRefValues": runtime_only_ref_values,
            "summary": (
                "Delegated execution contains required child result failures. Review the typed blocking results "
                "and repair or retry only the affected briefs; failed child evidence remains unaccepted."
                if blocking_results_present and delegation_handoff
                else "Runtime execution contains required result failures. Review the typed blocking results "
                "before accepting delivery."
                if blocking_results_present
                else "Governed Creative Media execution evidence is available for Supervisor acceptance."
                if runtime_only_ref_values
                else _string_value(handoff.get("compactSummary"), handoff.get("summary"))[:1200]
            ),
            "refs": list(handoff.get("refs") or handoff.get("artifactRefs") or nested_refs)[:10],
            "proofRefs": list(handoff.get("proofRefs") or handoff.get("verificationRefs") or nested_proof_refs)[:10],
            "researchRefs": list(handoff.get("researchRefs") or [])[:10],
            "results": compact_results,
            "resultCount": len(results),
            "projectedResultCount": len(compact_results),
            "omittedResultCount": omitted_result_count,
            "blockingResultCount": len(required_delegation_failures),
            "omittedBlockingResultCount": len(omitted_required_delegation_failures),
            "optionalFailedResultCount": len(optional_delegation_failures),
            "omittedOptionalFailedResultCount": sum(
                1
                for item, _reason in optional_delegation_failures
                if id(item) in omitted_result_identities
            ),
            "hasBlockingResults": bool(required_delegation_failures),
            "hasBlockingOmittedResults": bool(omitted_required_delegation_failures),
            "blockingTaskBriefIds": list(
                dict.fromkeys(
                    _string_value(item.get("taskBriefId"), item.get("taskId"))
                    for item, _reason in required_delegation_failures
                    if _string_value(item.get("taskBriefId"), item.get("taskId"))
                )
            )[:24],
            "blockingResults": blocking_result_details,
            "optionalFailureResults": optional_failure_details,
            "visibleResearchAnswerCount": visible_research_answer_count,
            "omittedResearchAnswerCount": omitted_research_answer_count,
            "projectionLimited": projection_limited,
            "projectionKind": projection_kind,
            "projectionRecoveryAvailable": projection_recovery_available,
            "consumerHint": _string_value(handoff.get("consumerHint"), handoff.get("recommendedNextAction"))[:600],
            "recommendedNextAction": _string_value(handoff.get("recommendedNextAction"))[:160],
            "sourceAcquisition": _compact_source_acquisition(handoff.get("sourceAcquisition")),
            "taskBriefIds": [
                str(item).strip()
                for item in list(handoff.get("taskBriefIds") or [])
                if str(item).strip()
            ][:24],
            "coveredTaskBriefIds": [
                str(item).strip()
                for item in list(handoff.get("coveredTaskBriefIds") or [])
                if str(item).strip()
            ][:24],
            "missingTaskBriefIds": [
                str(item).strip()
                for item in list(handoff.get("missingTaskBriefIds") or [])
                if str(item).strip()
            ][:24],
            "claimBlockers": [
                str(item).strip()
                for item in list(handoff.get("claimBlockers") or [])
                if str(item).strip()
            ][:24],
            "evidenceGaps": [
                {
                    "taskBriefId": _string_value(item.get("taskBriefId"), item.get("taskId")),
                    "status": _string_value(item.get("status"), "unverified"),
                    "blocksClaim": bool(item.get("blocksClaim", True)),
                    "blocksDownstream": bool(item.get("blocksDownstream", False)),
                    "limitations": [
                        str(value)[:500]
                        for value in list(item.get("limitations") or [])[:6]
                        if str(value).strip()
                    ],
                    "evidenceStatusReasons": [
                        str(value)[:160]
                        for value in list(item.get("evidenceStatusReasons") or [])[:6]
                        if str(value).strip()
                    ],
                }
                for item in list(handoff.get("evidenceGaps") or [])
                if isinstance(item, dict)
                and _string_value(item.get("taskBriefId"), item.get("taskId"))
            ][:24],
            "downstreamAllowed": bool(handoff.get("downstreamAllowed")),
            "continuationPolicy": (
                dict(handoff.get("continuationPolicy"))
                if isinstance(handoff.get("continuationPolicy"), dict)
                else {}
            ),
            "evidenceCoverageComplete": evidence_coverage_complete,
            "coverageComplete": projected_coverage_complete,
            "deliveryComplete": projected_delivery_complete,
            "taskBriefCount": int(handoff.get("taskBriefCount") or 0),
            "sourceCount": int(handoff.get("sourceCount") or 0),
            "limitations": [str(item)[:500] for item in list(handoff.get("limitations") or [])[:6]],
            "terminalEpisode": bool(handoff.get("terminalEpisode")),
            "remainingHandoffsExpected": int(handoff.get("remainingHandoffsExpected") or 0),
            "requiredInputs": [
                dict(item)
                for item in list(handoff.get("requiredInputs") or [])
                if isinstance(item, dict)
            ][:12],
            "continuationRequest": (
                dict(handoff.get("continuationRequest"))
                if isinstance(handoff.get("continuationRequest"), dict)
                else None
            ),
        }

    def _summary_message(*, episodes: list[dict], handoffs: list[dict], status: str, reason: str = "") -> HumanMessage:
        lines = [
            f"[Runtime Episode {status}]",
            "The following runtime-owned evidence is for verification and routing only. "
            "Do not copy internal identifiers, proof refs, or absolute paths into the user-facing reply.",
        ]
        compact_handoffs = [_compact_handoff_projection(handoff) for handoff in handoffs[:8]]
        if reason:
            lines.append(f"Reason: {reason}")
        if compact_handoffs:
            lines.append("Typed handoffs:")
            for handoff in compact_handoffs:
                kind = _string_value(handoff.get("kind"), "runtime_handoff")
                summary = _string_value(handoff.get("compactSummary"), handoff.get("summary"))[:800]
                status_label = _string_value(handoff.get("status"))
                runtime_only_ref_values = bool(handoff.get("runtimeOnlyRefValues"))
                display_kind = "Creative Media" if runtime_only_ref_values else kind
                lines.append(f"- {display_kind}{f' / {status_label}' if status_label else ''}: {summary}")
                direct_artifact_refs = [
                    label
                    for item in list(handoff.get("refs") or [])[:8]
                    for label in [
                        _string_value(
                            item.get("ref"),
                            item.get("artifactId"),
                            item.get("workspacePath"),
                            item.get("path"),
                        )
                        if isinstance(item, dict)
                        else str(item or "").strip()
                    ]
                    if label
                ]
                direct_proof_refs = [
                    label
                    for item in list(handoff.get("proofRefs") or [])[:8]
                    for label in [
                        _string_value(item.get("ref"), item.get("id"))
                        if isinstance(item, dict)
                        else str(item or "").strip()
                    ]
                    if label
                ]
                if runtime_only_ref_values and (direct_artifact_refs or direct_proof_refs):
                    lines.append(
                        "  governed Creative Media evidence is ready: "
                        f"artifacts={len(direct_artifact_refs)}, proofRefs={len(direct_proof_refs)}. "
                        "Exact reference values remain in the structured Runtime Surface; do not re-read or echo them."
                    )
                else:
                    if direct_artifact_refs:
                        lines.append("  artifact refs: " + ", ".join(direct_artifact_refs))
                    if direct_proof_refs:
                        lines.append("  proof refs: " + ", ".join(direct_proof_refs))
                consumer_hint = _string_value(handoff.get("consumerHint"))
                if consumer_hint and not runtime_only_ref_values:
                    lines.append("  consumer hint: " + consumer_hint[:600])
                task_brief_ids = list(handoff.get("taskBriefIds") or [])
                if handoff.get("terminalEpisode"):
                    coverage = (
                        f"{len(task_brief_ids)} declared brief(s)"
                        if runtime_only_ref_values
                        else ", ".join(str(item) for item in task_brief_ids) or "no declared task brief ids"
                    )
                    lines.append(
                        "  terminal episode: no further handoffs will arrive from this episode; "
                        f"declared brief coverage={coverage}; remainingHandoffsExpected={handoff.get('remainingHandoffsExpected', 0)}"
                    )
                if handoff.get("sourceCount") or handoff.get("limitations"):
                    lines.append(
                        f"  research evidence: sources={handoff.get('sourceCount', 0)}; "
                        f"limitations={len(list(handoff.get('limitations') or []))}"
                    )
                covered_ids = list(handoff.get("coveredTaskBriefIds") or [])
                missing_ids = list(handoff.get("missingTaskBriefIds") or [])
                if covered_ids or missing_ids:
                    if runtime_only_ref_values:
                        lines.append(
                            "  brief coverage: "
                            f"covered={len(covered_ids)}; missing={len(missing_ids)}; "
                            f"complete={bool(handoff.get('coverageComplete'))}"
                        )
                    else:
                        lines.append(
                            "  brief coverage: "
                            f"covered={', '.join(covered_ids) or 'none'}; "
                            f"missing={', '.join(missing_ids) or 'none'}; "
                            f"complete={bool(handoff.get('coverageComplete'))}"
                        )
                if handoff.get("projectionLimited"):
                    projection_kind = _string_value(handoff.get("projectionKind"), "runtime").lower()
                    recovery_available = bool(handoff.get("projectionRecoveryAvailable"))
                    if projection_kind == "research":
                        projection_line = (
                            "  bounded Research delivery projection: "
                            f"results={handoff.get('projectedResultCount', 0)}/{handoff.get('resultCount', 0)}; "
                            f"visibleAnswers={handoff.get('visibleResearchAnswerCount', 0)}; "
                            f"omittedAnswers={handoff.get('omittedResearchAnswerCount', 0)}; "
                            f"deliveryComplete={bool(handoff.get('deliveryComplete'))}; "
                            f"coverageComplete={bool(handoff.get('coverageComplete'))}."
                        )
                        if recovery_available:
                            projection_line += (
                                " Omitted answer bodies remain available only through each brief's governed detail call."
                            )
                        else:
                            projection_line += (
                                " No governed detail reference is present for the omitted answer bodies; "
                                "do not infer or claim review of them."
                            )
                    elif projection_kind == "delegation":
                        projection_line = (
                            "  bounded Delegation result projection: "
                            f"results={handoff.get('projectedResultCount', 0)}/{handoff.get('resultCount', 0)}; "
                            f"requiredFailures={handoff.get('blockingResultCount', 0)}; "
                            f"omittedRequiredFailures={handoff.get('omittedBlockingResultCount', 0)}; "
                            f"optionalFailures={handoff.get('optionalFailedResultCount', 0)}; "
                            f"omittedOptionalFailures={handoff.get('omittedOptionalFailedResultCount', 0)}; "
                            f"deliveryComplete={bool(handoff.get('deliveryComplete'))}."
                        )
                        if recovery_available:
                            projection_line += (
                                " Omitted child result bodies are recoverable only through the exact governed "
                                "detailRef and detailTool in the Runtime Surface."
                            )
                        else:
                            projection_line += (
                                " No governed detailRef/detailTool pair is present for omitted child result bodies; "
                                "do not infer them or claim full review."
                            )
                    else:
                        projection_line = (
                            "  bounded Runtime result projection: "
                            f"results={handoff.get('projectedResultCount', 0)}/{handoff.get('resultCount', 0)}; "
                            f"deliveryComplete={bool(handoff.get('deliveryComplete'))}."
                        )
                        if not recovery_available:
                            projection_line += (
                                " No governed detailRef/detailTool pair is present for omitted result bodies; "
                                "do not infer them or claim full review."
                            )
                    lines.append(projection_line)
                blocking_result_details = [
                    item
                    for item in list(handoff.get("blockingResults") or [])
                    if isinstance(item, dict)
                ]
                if blocking_result_details:
                    lines.append(
                        "  required child failures from the full canonical result set "
                        f"({handoff.get('blockingResultCount', len(blocking_result_details))} total):"
                    )
                    for item in blocking_result_details[:12]:
                        details = [
                            f"brief={_string_value(item.get('taskBriefId'), 'unknown')}",
                            f"status={_string_value(item.get('status'), 'failed')}",
                            f"reason={_string_value(item.get('reason'), 'delegated_task_failed')}",
                            f"omittedFromProjection={bool(item.get('omittedFromProjection'))}",
                        ]
                        error_code = _string_value(item.get("errorCode"))
                        error = _string_value(item.get("error"))
                        if error_code:
                            details.append(f"errorCode={error_code}")
                        if error:
                            details.append(f"error={error[:500]}")
                        lines.append("    - " + "; ".join(details))
                optional_failure_details = [
                    item
                    for item in list(handoff.get("optionalFailureResults") or [])
                    if isinstance(item, dict)
                ]
                if optional_failure_details:
                    lines.append(
                        "  optional child failures from the full canonical result set "
                        f"({handoff.get('optionalFailedResultCount', len(optional_failure_details))} total; non-blocking):"
                    )
                    for item in optional_failure_details[:12]:
                        details = [
                            f"brief={_string_value(item.get('taskBriefId'), 'unknown')}",
                            f"status={_string_value(item.get('status'), 'failed')}",
                            f"reason={_string_value(item.get('reason'), 'delegated_task_failed')}",
                            f"omittedFromProjection={bool(item.get('omittedFromProjection'))}",
                        ]
                        error_code = _string_value(item.get("errorCode"))
                        error = _string_value(item.get("error"))
                        if error_code:
                            details.append(f"errorCode={error_code}")
                        if error:
                            details.append(f"error={error[:500]}")
                        lines.append("    - " + "; ".join(details))
                if missing_ids:
                    lines.append(
                        "  next action: retry only the missing brief IDs once through a new managed Research episode; "
                        "do not inspect the earlier runtime route receipt or replace the gap with direct web tools."
                    )
                if handoff.get("researchRefs") or any(
                    result.get("researchRef") for result in list(handoff.get("results") or [])
                ):
                    lines.append(
                        "  research reference contract: research:// is evidence lineage, not a toolobs:// rawRef. "
                        "Consume the bounded answers/sources below. If more detail is genuinely required, use only "
                        "the exact per-brief research_broker(mode='get_evidence', evidenceBundleId=...) call shown "
                        "below; never pass research:// to tool_observation_detail."
                    )
                if runtime_only_ref_values and handoff.get("results"):
                    result_count = len(list(handoff.get("results") or []))
                    verified_count = sum(
                        1
                        for result in list(handoff.get("results") or [])
                        if isinstance(result, dict) and result.get("verificationPassed")
                    )
                    lines.append(
                        "  execution results remain structured Runtime Surface evidence: "
                        f"results={result_count}; semanticallyVerified={verified_count}."
                    )
                handoff_results = list(handoff.get("results") or [])
                research_projection = any(
                    result.get("researchRef") or result.get("evidenceBundleId")
                    for result in handoff_results
                    if isinstance(result, dict)
                )
                visible_results = (
                    []
                    if runtime_only_ref_values
                    else handoff_results
                    if research_projection
                    else handoff_results[:4]
                )
                for result in visible_results:
                    task_id = _string_value(result.get("taskBriefId"), "task")
                    target = _string_value(result.get("targetLabel"), "worker")
                    result_status = _string_value(result.get("status"), "unknown")
                    accepted_research_result = bool(
                        result.get("acceptancePassed") is True
                        and _string_value(result.get("qualityTier")) == "high_quality"
                    )
                    result_text = _string_value(result.get("result"))
                    if not accepted_research_result:
                        result_text = result_text[:1600]
                    result_display = result_text
                    if accepted_research_result and result.get("deliveryVisible") is False:
                        result_display = (
                            "Answer body omitted from this bounded multi-brief projection; "
                            "use the governed detail call below for this brief."
                        )
                    lines.append(
                        f"  - {task_id} · {target} · {result_status}: "
                        f"{result_display or '已回传结构化结果。'}"
                    )
                    if result.get("delegationDepth") or result.get("parentDelegationId"):
                        lines.append(
                            "    lineage: "
                            f"depth={result.get('delegationDepth') or 'unknown'}; "
                            f"delegation={_string_value(result.get('delegationId')) or 'unknown'}; "
                            f"parent={_string_value(result.get('parentDelegationId')) or 'none'}"
                        )
                    artifact_count = len(list(result.get("artifactRefs") or []))
                    proof_count = len(list(result.get("proofRefs") or []))
                    if artifact_count or proof_count or result.get("verificationPassed"):
                        evidence_state = "complete" if result.get("evidenceComplete") else "review_required"
                        lines.append(
                            f"    evidence: {evidence_state}; artifacts={artifact_count}, proofRefs={proof_count}, "
                            f"semanticVerification={bool(result.get('verificationPassed'))}"
                        )
                    artifact_labels = []
                    for ref in list(result.get("artifactRefs") or [])[:4]:
                        if isinstance(ref, dict):
                            label = _string_value(
                                ref.get("ref"), ref.get("workspacePath"), ref.get("path"), ref.get("artifactId")
                            )
                        else:
                            label = str(ref or "").strip()
                        if label:
                            artifact_labels.append(label)
                    if artifact_labels:
                        artifact_label = (
                            "quarantined candidates (unmerged): "
                            if result.get("artifactRefsAccepted") is False
                            else "artifacts: "
                        )
                        lines.append("    " + artifact_label + ", ".join(artifact_labels))
                    verification_summary = _string_value(result.get("verificationSummary"))
                    if verification_summary:
                        lines.append("    verification: " + verification_summary)
                    if result.get("researchRef") or result.get("sourceCount") or result.get("evidenceStatusReasons"):
                        lines.append(
                            "    research: "
                            f"evidenceRef={_string_value(result.get('researchRef')) or 'none'}; "
                            f"evidenceBundleId={_string_value(result.get('evidenceBundleId')) or 'none'}; "
                            f"sources={int(result.get('sourceCount') or 0)}; "
                            f"claims={int(result.get('claimCount') or 0)}; "
                            f"answerProjection={_string_value(result.get('answerProjection')) or 'unknown'}; "
                            f"evidenceStatus={','.join(list(result.get('evidenceStatusReasons') or [])) or 'ready'}"
                        )
                        source_urls = [str(value).strip() for value in list(result.get("sourceUrls") or []) if str(value).strip()]
                        if source_urls and result.get("deliveryVisible") is not False:
                            lines.append("    sources: " + ", ".join(source_urls[:8]))
                        detail_tool = _string_value(result.get("detailTool"))
                        if detail_tool:
                            lines.append("    detail: " + detail_tool)
                    source_acquisition = (
                        result.get("sourceAcquisition")
                        if isinstance(result.get("sourceAcquisition"), dict)
                        else {}
                    )
                    if source_acquisition.get("state"):
                        lines.append(
                            "    source acquisition: "
                            f"state={source_acquisition.get('state')}; "
                            f"stopReason={source_acquisition.get('stopReason') or 'unknown'}; "
                            f"readableSources={int(source_acquisition.get('readableSourceCount') or 0)}; "
                            f"failureClasses={','.join(list(source_acquisition.get('failureClasses') or [])) or 'none'}"
                        )
                    if result.get("limitations"):
                        lines.append(
                            "    limitations: "
                            + " | ".join(str(value) for value in list(result.get("limitations") or [])[:3])
                        )
        else:
            lines.append("Episodes:")
            for episode in episodes[:8]:
                lines.append(
                    "- "
                    f"{_string_value(episode.get('kind'), 'runtime')} "
                    f"{_string_value(episode.get('episodeId'), episode.get('id'), episode.get('needId'))} "
                    f"state={_string_value(episode.get('state'))}"
                )
        lines.append(
            "A result marked evidence=complete is the governed execution proof for this acceptance step. "
            "Consume it directly; do not re-read the same artifact or route a duplicate verification episode unless "
            "the handoff explicitly reports missing evidence, a blocker, or contradictory values."
        )
        lines.append("Supervisor must use these runtime facts and must not retry direct mutating tools while active episodes remain.")
        return HumanMessage(
            content="\n".join(lines),
            additional_kwargs={
                "v8_governance_type": "runtime_handoff",
                "v8_runtime_handoffs": compact_handoffs,
            },
        )

    def _failed_handoffs(handoffs: list[dict]) -> list[dict]:
        return [
            handoff
            for handoff in handoffs
            if str(handoff.get("status") or "").strip().lower() in {"failed", "blocked", "error", "recoverable_failed"}
        ]

    def _degraded_handoffs(handoffs: list[dict]) -> list[dict]:
        degraded: list[dict] = []
        for handoff in handoffs:
            status = str(handoff.get("status") or "").strip().lower()
            kind = str(handoff.get("kind") or "").strip().lower()
            dispatch_status = str(handoff.get("dispatchStatus") or handoff.get("dispatch_status") or "").strip().lower()
            if (
                status == "degraded"
                or kind.endswith("_degraded")
                or bool(handoff.get("degraded") or handoff.get("degradedReason") or handoff.get("degraded_reason"))
                or dispatch_status in {"delegation_degraded", "missing_tasks"}
            ):
                degraded.append(handoff)
        return degraded

    def _failure_summary_key(
        *,
        episodes: list[dict],
        handoffs: list[dict],
        reason: str,
    ) -> str:
        episode_id = ""
        if episodes:
            episode_id = _string_value(
                episodes[0].get("episodeId"),
                episodes[0].get("id"),
                episodes[0].get("needId"),
            )
        if not episode_id and handoffs:
            episode_id = _string_value(
                handoffs[0].get("producerEpisodeId"),
                handoffs[0].get("episodeId"),
            )
        return f"{episode_id or 'runtime'}:{reason or 'failure'}"

    def _is_optional_episode(episode: dict) -> bool:
        inputs = dict(episode.get("inputs") or {}) if isinstance(episode.get("inputs"), dict) else {}
        metadata = dict(episode.get("metadata") or {}) if isinstance(episode.get("metadata"), dict) else {}
        if any(
            bool(source.get("optional") or source.get("optionalLane") or source.get("degradedOk"))
            for source in (episode, inputs, metadata)
            if isinstance(source, dict)
        ):
            return True
        return str(inputs.get("dependencyMode") or metadata.get("dependencyMode") or episode.get("dependencyMode") or "").strip().lower() in {
            "optional",
            "degraded_ok",
        }

    def _episode_map(episodes: list[dict]) -> dict[str, dict]:
        mapped: dict[str, dict] = {}
        for episode in episodes:
            episode_id = _string_value(episode.get("episodeId"), episode.get("id"), episode.get("needId"))
            if episode_id:
                mapped[episode_id] = episode
        return mapped

    def _required_failed_handoffs(handoffs: list[dict], episodes: list[dict]) -> list[dict]:
        by_id = _episode_map(episodes)
        required: list[dict] = []
        for handoff in _failed_handoffs(handoffs):
            episode_id = _string_value(handoff.get("producerEpisodeId"), handoff.get("episodeId"))
            episode = by_id.get(episode_id) if episode_id else None
            if episode and _is_optional_episode(episode):
                continue
            required.append(handoff)
        return required

    def _failed_episodes(episodes: list[dict]) -> list[dict]:
        return [
            episode
            for episode in episodes
            if str(episode.get("state") or "").strip().lower() in {"failed", "cancelled", "canceled"}
        ]

    def _required_failed_episodes(episodes: list[dict]) -> list[dict]:
        return [episode for episode in _failed_episodes(episodes) if not _is_optional_episode(episode)]

    async def runtime_episode_wait_node(state):
        session_id, run_id, workspace_path = _state_runtime_identity(state)
        route_context = dict((state or {}).get("current_route_context") or {})
        if session_id:
            route_context.setdefault("session_id", session_id)
            route_context.setdefault("sessionId", session_id)
        if run_id:
            route_context.setdefault("run_id", run_id)
            route_context.setdefault("runId", run_id)
        if workspace_path:
            route_context.setdefault("workspace_path", workspace_path)
            route_context.setdefault("workspacePath", workspace_path)
        identity_update = {
            **({"session_id": session_id, "sessionId": session_id} if session_id else {}),
            **({"run_id": run_id, "runId": run_id} if run_id else {}),
            **({"workspace_path": workspace_path, "workspacePath": workspace_path} if workspace_path else {}),
        }
        started_at = time.monotonic()
        wait_started_wall = time.time()
        queue_deadline = started_at + max(0.1, RUNTIME_EPISODE_QUEUE_GRACE_SECONDS)
        deadline = started_at + max(queue_deadline - started_at, RUNTIME_EPISODE_WAIT_SECONDS)
        last_episodes: list[dict] = []

        while True:
            episodes = _load_relevant_episodes(route_context=route_context, session_id=session_id, run_id=run_id)
            if episodes:
                last_episodes = episodes
            route_context, handoffs = _merge_handoffs(route_context, episodes)
            active = _active_episodes(episodes)
            terminal = _terminal_episodes(episodes)
            delivery_integrity_failures = [
                handoff
                for handoff in handoffs
                if str(handoff.get("kind") or "").strip() == "runtime_delivery_diagnostic"
                and str(handoff.get("status") or "").strip().lower() == "failed"
                and not bool(handoff.get("optional"))
            ]
            if delivery_integrity_failures:
                failure_reason = _string_value(
                    delivery_integrity_failures[0].get("errorCode"),
                    "runtime_result_handoff_invalid",
                )
                failure_key = _failure_summary_key(
                    episodes=episodes,
                    handoffs=delivery_integrity_failures,
                    reason=failure_reason,
                )
                notified_keys = {
                    str(item).strip()
                    for item in list(route_context.get("runtimeFailureSummaryKeys") or [])
                    if str(item).strip()
                }
                first_notification = failure_key not in notified_keys
                route_context["runtimeFailureSummaryKeys"] = list(
                    dict.fromkeys([*notified_keys, failure_key])
                )[-50:]
                return Command(
                    goto="supervisor",
                    update={
                        "current_route_context": route_context,
                        **identity_update,
                        "runtime_dispatch_status": {
                            "mode": "runtime_episode",
                            "nextAction": "recoverable_failure",
                            "state": "delivery_integrity_failed",
                            "episodeCount": len(episodes),
                            "handoffCount": 0,
                            "failedHandoffCount": len(delivery_integrity_failures),
                            "reason": failure_reason,
                            "failureSummaryInjected": first_notification,
                        },
                        "messages": (
                            [
                                _summary_message(
                                    episodes=episodes,
                                    handoffs=delivery_integrity_failures,
                                    status="Delivery Integrity Failure",
                                    reason=failure_reason,
                                )
                            ]
                            if first_notification
                            else []
                        ),
                    },
                )
            waiting_input_episodes = [
                episode
                for episode in active
                if str(episode.get("state") or "").strip().lower() == "waiting_input"
            ]
            if waiting_input_episodes:
                waiting_ids = {
                    _string_value(episode.get("episodeId"), episode.get("id"), episode.get("needId"))
                    for episode in waiting_input_episodes
                }
                input_handoffs = [
                    handoff
                    for handoff in handoffs
                    if _string_value(handoff.get("producerEpisodeId"), handoff.get("episodeId")) in waiting_ids
                    and (
                        str(handoff.get("status") or "").strip().lower()
                        in {"waiting_input", "awaiting_input", "needs_input"}
                        or handoff.get("requiredInputs")
                        or handoff.get("continuationRequest")
                    )
                ]
                selected_handoff = input_handoffs[-1] if input_handoffs else {}
                required_inputs = [
                    dict(item)
                    for item in list(selected_handoff.get("requiredInputs") or [])
                    if isinstance(item, dict)
                ][:12]
                continuation_request = (
                    dict(selected_handoff.get("continuationRequest") or {})
                    if isinstance(selected_handoff.get("continuationRequest"), dict)
                    else {"requiredInputs": required_inputs, "resumePolicy": "same_episode"}
                )
                continuation_request.setdefault("requiredInputs", required_inputs)
                continuation_request["resumePolicy"] = "same_episode"
                continuation_request_id = str(continuation_request.get("requestId") or "").strip()
                waiting_episode = waiting_input_episodes[-1]
                waiting_episode_id = _string_value(
                    waiting_episode.get("episodeId"), waiting_episode.get("id"), waiting_episode.get("needId")
                )
                notification_key = f"{waiting_episode_id}:{_string_value(selected_handoff.get('handoffRefId'), selected_handoff.get('handoffId'), waiting_episode.get('updatedAt'))}"
                notified_keys = {
                    str(item).strip()
                    for item in list(route_context.get("runtimeInputRequestKeys") or [])
                    if str(item).strip()
                }
                first_notification = notification_key not in notified_keys
                route_context["runtimeInputRequestKeys"] = list(dict.fromkeys([*notified_keys, notification_key]))[-50:]
                input_guidance = HumanMessage(
                    content=(
                        "[Runtime input required]\n"
                        "A runtime episode is paused for an explicit missing choice. Keep the same episode active. "
                        "Ask the user one concise, ordinary question using the requiredInputs below. After the answer, "
                        "call runtime_broker(mode='resume', episode_id=..., continuation_request_id=..., continuation_inputs=...) exactly once; do not create a new route, "
                        "call provider tools directly, or mark the task complete.\n"
                        f"episodeId: {waiting_episode_id}\n"
                        f"continuationRequestId: {continuation_request_id}\n"
                        f"requiredInputs: {json.dumps(required_inputs, ensure_ascii=False)}"
                    ),
                    additional_kwargs={
                        "v8_governance_type": "runtime_input_required",
                        "v8_runtime_episode_id": waiting_episode_id,
                        "v8_continuation_request": continuation_request,
                    },
                )
                return Command(
                    goto="supervisor",
                    update={
                        "current_route_context": route_context,
                        **identity_update,
                        "runtime_dispatch_status": {
                            "mode": "runtime_episode",
                            "nextAction": "request_runtime_input",
                            "state": "waiting_input",
                            "episodeId": waiting_episode_id,
                            "episodeKind": _string_value(waiting_episode.get("kind"), "runtime"),
                            "requiredInputs": required_inputs,
                            "continuationRequest": continuation_request,
                            "inputRequestInjected": first_notification,
                        },
                        "messages": [input_guidance] if first_notification else [],
                    },
                )
            if episodes and not active:
                handoffs_by_episode: dict[str, list[dict]] = {}
                for handoff in handoffs:
                    producer_id = _string_value(handoff.get("producerEpisodeId"), handoff.get("episodeId"))
                    if producer_id:
                        handoffs_by_episode.setdefault(producer_id, []).append(handoff)
                superseded_ids = superseded_runtime_episode_ids(terminal or episodes, handoffs_by_episode)
                if superseded_ids:
                    route_context["supersededRuntimeEpisodeIds"] = sorted(superseded_ids)
                effective_terminal = [
                    episode
                    for episode in (terminal or episodes)
                    if not runtime_episode_parent_id(episode)
                    and _string_value(episode.get("episodeId"), episode.get("id"), episode.get("needId"))
                    not in superseded_ids
                ]
                effective_episode_ids = {
                    _string_value(episode.get("episodeId"), episode.get("id"), episode.get("needId"))
                    for episode in effective_terminal
                }
                effective_handoffs = [
                    handoff
                    for handoff in handoffs
                    if _string_value(handoff.get("producerEpisodeId"), handoff.get("episodeId"))
                    in effective_episode_ids
                ]
                failed_handoffs = _failed_handoffs(effective_handoffs)
                degraded_handoffs = _degraded_handoffs(effective_handoffs)
                failed_episodes = _failed_episodes(effective_terminal)
                required_failed_handoffs = _required_failed_handoffs(effective_handoffs, effective_terminal)
                required_failed_episodes = _required_failed_episodes(effective_terminal)
                if required_failed_handoffs or required_failed_episodes:
                    failure_reason = _string_value(
                        (required_failed_episodes[0] if required_failed_episodes else {}).get("errorCode"),
                        (required_failed_episodes[0] if required_failed_episodes else {}).get("error_code"),
                        (required_failed_episodes[0] if required_failed_episodes else {}).get("errorMessage"),
                        (required_failed_episodes[0] if required_failed_episodes else {}).get("error_message"),
                        (required_failed_handoffs[0] if required_failed_handoffs else {}).get("errorCode"),
                        (required_failed_handoffs[0] if required_failed_handoffs else {}).get("compactSummary"),
                        "runtime_episode_failed",
                    )
                    failure_key = _failure_summary_key(
                        episodes=required_failed_episodes or effective_terminal,
                        handoffs=required_failed_handoffs,
                        reason=failure_reason,
                    )
                    notified_keys = {
                        str(item).strip()
                        for item in list(route_context.get("runtimeFailureSummaryKeys") or [])
                        if str(item).strip()
                    }
                    first_notification = failure_key not in notified_keys
                    route_context["runtimeFailureSummaryKeys"] = list(dict.fromkeys([*notified_keys, failure_key]))[-50:]
                    return Command(
                        goto="supervisor",
                        update={
                            "current_route_context": route_context,
                            **identity_update,
                            "runtime_dispatch_status": {
                                "mode": "runtime_episode",
                                "nextAction": "recoverable_failure",
                                "state": "episode_failed",
                                "episodeCount": len(effective_terminal),
                                "handoffCount": len(effective_handoffs),
                                "failedEpisodeCount": len(required_failed_episodes),
                                "failedHandoffCount": len(required_failed_handoffs),
                                "degradedEpisodeCount": len(failed_episodes) - len(required_failed_episodes),
                                "degradedHandoffCount": len(degraded_handoffs),
                                "reason": failure_reason,
                                "failureSummaryInjected": first_notification,
                            },
                            "messages": (
                                [
                                    _summary_message(
                                        episodes=required_failed_episodes or effective_terminal,
                                        handoffs=required_failed_handoffs or effective_handoffs,
                                        status="Recoverable Failure",
                                        reason=failure_reason,
                                    )
                                ]
                                if first_notification
                                else []
                            ),
                        },
                    )
                degraded_count = len(failed_episodes) + len(failed_handoffs) + len(degraded_handoffs)
                return Command(
                    goto="supervisor",
                    update={
                        "current_route_context": route_context,
                        **identity_update,
                        "runtime_dispatch_status": {
                            "mode": "runtime_episode",
                            "nextAction": "resume_supervisor",
                            "state": "degraded_handoff_ready" if degraded_count else ("handoff_ready" if handoffs else "episode_terminal"),
                            "episodeCount": len(effective_terminal),
                            "handoffCount": len(effective_handoffs),
                            "degradedEpisodeCount": len(failed_episodes),
                            "degradedHandoffCount": len(failed_handoffs) + len(degraded_handoffs),
                        },
                        "messages": [
                            _summary_message(
                                episodes=effective_terminal,
                                handoffs=effective_handoffs,
                                status="Degraded Handoff Ready" if degraded_count else "Handoff Ready",
                                reason="optional_lane_degraded" if degraded_count else "",
                            )
                        ],
                    },
                )

            if active:
                active_states = {str(episode.get("state") or "") for episode in active}
                only_unclaimed_queue = active_states <= {"detected", "routed", "queued"}
                queue_grace_elapsed = all(
                    _episode_queue_age_seconds(episode, default_started_wall=wait_started_wall) >= RUNTIME_EPISODE_QUEUE_GRACE_SECONDS
                    for episode in active
                )
                if only_unclaimed_queue and queue_grace_elapsed:
                    if _has_live_bound_episode_lease() and time.monotonic() < deadline:
                        await asyncio.sleep(max(0.1, RUNTIME_EPISODE_POLL_SECONDS))
                        continue
                    failed_episodes: list[dict] = []
                    claim_race_detected = False
                    for episode in active:
                        episode_id = _string_value(episode.get("episodeId"), episode.get("id"), episode.get("needId"))
                        if not episode_id:
                            continue
                        failed = db.complete_runtime_episode(
                            episode_id,
                            state="failed",
                            error_code="episode_runner_unavailable",
                            error_message="Runtime episode stayed queued and was not claimed by EpisodeRunner within the queue grace window.",
                            metadata={"recoverable": True, "source": "runtime_episode_wait"},
                            expected_state=str(episode.get("state") or "queued"),
                        )
                        if failed is None:
                            # A worker may claim the queue between the read
                            # above and this CAS. Re-read instead of reporting
                            # a false terminal failure.
                            claim_race_detected = True
                            break
                        failed_episodes.append(dict(failed))
                        emit_runtime_episode_event("runtime.episode.failed", {"episode": failed})
                    if claim_race_detected:
                        await asyncio.sleep(max(0.1, RUNTIME_EPISODE_POLL_SECONDS))
                        continue
                    return Command(
                        goto="supervisor",
                        update={
                            "current_route_context": route_context,
                            **identity_update,
                            "runtime_dispatch_status": {
                                "mode": "runtime_episode",
                                "nextAction": "recoverable_failure",
                                "state": "episode_runner_unavailable",
                                "episodeCount": len(failed_episodes),
                            },
                            "messages": [
                                _summary_message(
                                    episodes=failed_episodes,
                                    handoffs=[],
                                    status="Recoverable Failure",
                                    reason="episode_runner_unavailable",
                                )
                            ],
                        },
                    )
                if time.monotonic() >= deadline:
                    return Command(
                        goto="supervisor",
                        update={
                            "current_route_context": route_context,
                            **identity_update,
                            "runtime_dispatch_status": {
                                "mode": "runtime_episode",
                                "nextAction": "recoverable_failure",
                                "state": "episode_stalled",
                                "episodeCount": len(active),
                                "activeEpisodeIds": [
                                    _string_value(episode.get("episodeId"), episode.get("id"), episode.get("needId"))
                                    for episode in active
                                ],
                            },
                            "messages": [
                                _summary_message(
                                    episodes=active,
                                    handoffs=[],
                                    status="Recoverable Failure",
                                    reason="episode_stalled",
                                )
                            ],
                        },
                    )
                await asyncio.sleep(max(0.1, RUNTIME_EPISODE_POLL_SECONDS))
                continue

            return Command(
                goto="supervisor",
                update={
                    "current_route_context": route_context,
                    **identity_update,
                    "runtime_dispatch_status": {
                        "mode": "runtime_episode",
                        "nextAction": "resume_supervisor",
                        "state": "no_active_episode",
                        "episodeCount": len(last_episodes),
                    },
                },
            )

    return runtime_episode_wait_node


def compile_supervisor_workflow(
    *,
    agent_state_type,
    supervisor_node,
    supervisor_tools: list,
    agent_nodes_map: dict,
    resolve_agent_node=None,
    create_routed_tool_node,
    checkpointer=None,
):
    workflow = StateGraph(agent_state_type)
    parallel_task_node = build_parallel_delegate_task_node(
        agent_nodes_map,
        resolve_agent_node=resolve_agent_node,
    )
    parallel_join_node = build_parallel_delegate_join_node()

    workflow.add_node("workflow_entry", _workflow_entry_command)
    workflow.add_node(RUNTIME_EPISODE_WAIT_NODE, build_runtime_episode_wait_node())
    workflow.add_node("supervisor", supervisor_node)

    async def supervisor_tools_node(state):
        visible_tools = filter_visible_tools_for_actor(
            supervisor_tools,
            actor="supervisor",
            route_context=dict((state or {}).get("current_route_context") or {}),
        )
        routed = create_routed_tool_node(visible_tools, name="supervisor_tools", fallback_goto="supervisor")
        command = await routed(state)
        return _route_runtime_tool_commands(command)

    workflow.add_node("supervisor_tools", supervisor_tools_node)
    workflow.add_node("parallel_delegate_task", parallel_task_node)
    workflow.add_node("parallel_delegate_join", parallel_join_node)
    workflow.set_entry_point("workflow_entry")

    for agent_id, agent_data in agent_nodes_map.items():
        workflow.add_node(agent_id, agent_data["node_func"])

        tool_node_name = f"{agent_id}_tools"
        if agent_data["tools"]:
            workflow.add_node(tool_node_name, agent_data.get("tool_node_func") or create_routed_tool_node(agent_data["tools"], name=tool_node_name, fallback_goto=agent_id))

        if agent_data.get("reflection_enabled") and agent_data.get("reviewer_func"):
            workflow.add_node(f"{agent_id}_reviewer", agent_data["reviewer_func"])

    if checkpointer is None:
        if runtime_stability_service.strict_supervisor_durability():
            raise RuntimeError("Supervisor workflow requires an explicit durable checkpointer; MemorySaver fallback is disabled.")
        from langgraph.checkpoint.memory import MemorySaver

        checkpointer = MemorySaver()

    return workflow.compile(checkpointer=checkpointer)
