from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

ENGINE_ROOT = Path(__file__).resolve().parents[1]
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))


def _engine_venv_python() -> Path:
    scripts_dir = "Scripts" if os.name == "nt" else "bin"
    executable = "python.exe" if os.name == "nt" else "python"
    return ENGINE_ROOT / ".venv" / scripts_dir / executable


def _ensure_engine_python() -> None:
    expected = _engine_venv_python()
    if not expected.exists():
        return
    current = Path(sys.executable).resolve()
    if current == expected.resolve():
        return
    if importlib.util.find_spec("chromadb") is not None:
        return
    os.execv(str(expected), [str(expected), *sys.argv])


_ensure_engine_python()

from agents import memory_agent  # noqa: E402
from core.database import db  # noqa: E402
from core.storage import MEMORY_DURABLE_POLICY_DEFAULTS  # noqa: E402
from erc.runtime_context import bind_runtime_context  # noqa: E402
from runtimes.memory.scope_resolution import build_scope_chain, scope_resolution_service, session_scope_binding_service  # noqa: E402


def _default_policy() -> Dict[str, Any]:
    return {
        "extraction_enabled": True,
        "preference_importance_threshold": int(MEMORY_DURABLE_POLICY_DEFAULTS["preference_importance_threshold"]),
        "preference_confidence_threshold": float(MEMORY_DURABLE_POLICY_DEFAULTS["preference_confidence_threshold"]),
        "knowledge_importance_threshold": int(MEMORY_DURABLE_POLICY_DEFAULTS["knowledge_importance_threshold"]),
        "knowledge_confidence_threshold": float(MEMORY_DURABLE_POLICY_DEFAULTS["knowledge_confidence_threshold"]),
        "global_knowledge_importance_threshold": int(MEMORY_DURABLE_POLICY_DEFAULTS["global_knowledge_importance_threshold"]),
        "global_knowledge_confidence_threshold": float(MEMORY_DURABLE_POLICY_DEFAULTS["global_knowledge_confidence_threshold"]),
        "global_operational_importance_threshold": int(MEMORY_DURABLE_POLICY_DEFAULTS["global_operational_importance_threshold"]),
        "global_operational_confidence_threshold": float(MEMORY_DURABLE_POLICY_DEFAULTS["global_operational_confidence_threshold"]),
    }


def _load_policy(source: str) -> Dict[str, Any]:
    return memory_agent._load_memory_policy() if source == "current" else _default_policy()


def _resolve_scope(session_id: str, chat_history_text: str) -> tuple[Any, str, List[str], str, str, str]:
    binding = session_scope_binding_service.get_binding(session_id)
    if binding and binding.status == "active":
        scope = binding.resolved_scope
        scope_chain = build_scope_chain(
            resolved_scope=binding.resolved_scope,
            channel_type=binding.channel_type,
            channel_remote_id=binding.channel_remote_id,
            workspace_id=binding.workspace_id,
            project_id=binding.project_id,
            workflow_id=binding.workflow_id,
        )
    else:
        scope_hints = memory_agent._session_scope_hints(session_id)
        resolved = scope_resolution_service.resolve(
            session_id=session_id,
            conversation_id=session_id,
            user_query=chat_history_text,
            scope_mode="explicit",
            project_id=scope_hints.get("project_id"),
            workspace_id=scope_hints.get("workspace_id"),
            workspace_path=scope_hints.get("workspace_path"),
            workflow_id=scope_hints.get("workflow_id"),
            channel_type=scope_hints.get("channel_type"),
            channel_remote_id=scope_hints.get("channel_remote_id"),
            scope_hint=scope_hints.get("scope_hint"),
        )
        binding = resolved.binding
        scope = binding.resolved_scope
        scope_chain = resolved.scope_chain
    effective_memory_scope = memory_agent._effective_memory_scope(binding, scope)
    provenance_class = memory_agent._memory_provenance_class("SYSTEM")
    memory_policy = memory_agent._memory_policy_for_provenance(provenance_class)
    return binding, scope, scope_chain, effective_memory_scope, provenance_class, memory_policy


def _build_chat_history_text(messages: List[Dict[str, Any]]) -> str:
    text = ""
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if content:
            text += f"{role.upper()}: {content}\n"
    return text


def _knowledge_preview_items(result: Any, policy: Dict[str, Any]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for fact in result.knowledge:
        passes, reason = memory_agent._evaluate_knowledge_persistence(fact, policy)
        items.append(
            {
                "scope": fact.scope,
                "category": fact.category,
                "fact": fact.fact,
                "durability": fact.durability,
                "importance": int(fact.importance or 0),
                "confidence": float(fact.confidence or 0.0),
                "decision": reason,
                "passes": bool(passes),
            }
        )
    return items


def _preference_preview_items(result: Any, policy: Dict[str, Any]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for pref in result.preferences:
        passes, reason = memory_agent._evaluate_preference_persistence(pref, policy)
        items.append(
            {
                "scope": pref.scope,
                "key": pref.key,
                "value": pref.value,
                "durability": pref.durability,
                "importance": int(pref.importance or 0),
                "confidence": float(pref.confidence or 0.0),
                "decision": reason,
                "passes": bool(passes),
            }
        )
    return items


def _graph_preview(result: Any, knowledge_items: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not any(item.get("passes") for item in knowledge_items):
        return {"wouldGrow": False, "entityCount": 0, "relationCount": 0}
    relation_count = 0
    for relation in result.relations:
        if (
            str(relation.subject or "").strip()
            and str(relation.predicate or "").strip()
            and str(relation.object or "").strip()
        ):
            relation_count += 1
    entity_names = {
        str(entity.name or "").strip().lower()
        for entity in result.entities
        if str(entity.name or "").strip()
    }
    return {
        "wouldGrow": relation_count > 0 or bool(entity_names),
        "entityCount": len(entity_names),
        "relationCount": relation_count,
    }


def build_session_replay_report(*, session_id: str, policy_source: str) -> Dict[str, Any]:
    durable_messages = db.get_messages(session_id)
    transcript = memory_agent._build_canonical_session_transcript(session_id, durable_messages)
    transcript_entries = transcript["entries"]
    incremental_messages, extraction_state, extraction_mode = memory_agent._resolve_incremental_messages(
        session_id=session_id,
        messages=transcript_entries,
    )
    chat_history_text = _build_chat_history_text(incremental_messages)
    binding, scope, scope_chain, effective_memory_scope, provenance_class, memory_policy = _resolve_scope(
        session_id,
        chat_history_text,
    )
    quick_summary = memory_agent._generate_quick_summary(chat_history_text) if chat_history_text.strip() else ""
    historical_context = memory_agent._build_historical_context(quick_summary=quick_summary, scope_chain=scope_chain)
    policy = _load_policy(policy_source)
    with bind_runtime_context(runtime_kind="memory", session_id=session_id):
        extraction_attempt = memory_agent._extract_with_llm(
            chat_history_text,
            historical_context,
            resolved_scope=scope,
            scope_chain=scope_chain,
        )

    report: Dict[str, Any] = {
        "sessionId": session_id,
        "policySource": policy_source,
        "policy": policy,
        "transcript": {
            "source": transcript.get("source"),
            "latestSeq": transcript.get("latest_seq"),
            "durableMessageCount": transcript.get("durable_message_count"),
            "runtimeEventCount": transcript.get("runtime_event_count"),
            "canonicalEntryCount": len(transcript_entries),
            "incrementalEntryCount": len(incremental_messages),
            "userMessageCount": transcript.get("user_message_count"),
            "extractionMode": extraction_mode,
            "previousCheckpoint": extraction_state or {},
            "chatHistoryLength": len(chat_history_text.strip()),
        },
        "scope": {
            "resolvedScope": scope,
            "scopeChain": scope_chain,
            "effectiveMemoryScope": effective_memory_scope,
            "projectId": getattr(binding, "project_id", None),
            "workspaceId": getattr(binding, "workspace_id", None),
            "provenanceClass": provenance_class,
            "memoryPolicy": memory_policy,
        },
        "quickSummary": quick_summary,
        "historicalContextPreview": memory_agent._safe_json_excerpt(historical_context, limit=1200),
        "extractor": {
            "extractorModel": extraction_attempt.extractor_model or None,
            "failureStage": extraction_attempt.failure_stage or None,
            "failureReason": extraction_attempt.failure_reason or None,
            "rawOutputPreview": extraction_attempt.raw_output_preview or None,
            "parserErrorPreview": extraction_attempt.parser_error_preview or None,
        },
        "recentModelInvocations": db.list_model_invocations(
            session_id=session_id,
            capability_class="memory_extraction",
            request_kind="memory_extraction",
            limit=8,
        ),
    }

    result = extraction_attempt.result
    if result is None:
        report["outcome"] = {
            "status": "failed",
            "reason": extraction_attempt.failure_stage or "llm_response_empty",
        }
        return report

    memory_agent._align_extraction_scopes(result, effective_memory_scope)
    preference_items = _preference_preview_items(result, policy)
    knowledge_items = _knowledge_preview_items(result, policy)
    graph_preview = _graph_preview(result, knowledge_items)
    extracted_total = len(result.preferences) + len(result.knowledge)
    persisted_preferences = sum(1 for item in preference_items if item["passes"])
    persisted_knowledge = sum(1 for item in knowledge_items if item["passes"])
    no_persisted_memory_reason = None
    if extracted_total <= 0:
        no_persisted_memory_reason = "model_empty"
    elif persisted_preferences + persisted_knowledge <= 0:
        no_persisted_memory_reason = "policy_filtered"

    report["outcome"] = {
        "status": "completed",
        "summary": result.summary,
        "tags": result.tags,
        "extractedPreferenceCount": len(result.preferences),
        "extractedKnowledgeCount": len(result.knowledge),
        "persistedPreferenceCount": persisted_preferences,
        "persistedKnowledgeCount": persisted_knowledge,
        "noPersistedMemoryReason": no_persisted_memory_reason,
        "graph": graph_preview,
        "preferences": preference_items,
        "knowledge": knowledge_items,
    }
    return report


def _render_markdown(report: Dict[str, Any]) -> str:
    lines = [
        "# Memory Session Replay Report",
        "",
        f"- sessionId: `{report['sessionId']}`",
        f"- policySource: `{report['policySource']}`",
        "",
        "## Transcript",
        f"- source: `{report['transcript']['source']}`",
        f"- latestSeq: `{report['transcript']['latestSeq']}`",
        f"- canonicalEntryCount: `{report['transcript']['canonicalEntryCount']}`",
        f"- incrementalEntryCount: `{report['transcript']['incrementalEntryCount']}`",
        f"- extractionMode: `{report['transcript']['extractionMode']}`",
        f"- chatHistoryLength: `{report['transcript']['chatHistoryLength']}`",
        "",
        "## Scope",
        f"- resolvedScope: `{report['scope']['resolvedScope']}`",
        f"- effectiveMemoryScope: `{report['scope']['effectiveMemoryScope']}`",
        f"- provenanceClass: `{report['scope']['provenanceClass']}`",
        f"- memoryPolicy: `{report['scope']['memoryPolicy']}`",
        "",
        "## Extractor",
        f"- extractorModel: `{report['extractor'].get('extractorModel') or 'unknown'}`",
        f"- failureStage: `{report['extractor'].get('failureStage') or 'none'}`",
    ]
    if report.get("quickSummary"):
        lines.extend(["", "## Quick Summary", report["quickSummary"]])
    if report["extractor"].get("failureReason"):
        lines.extend(["", "## Failure", str(report["extractor"]["failureReason"])])
    if report["extractor"].get("parserErrorPreview"):
        lines.extend(["", "## Parser Error Preview", "```text", str(report["extractor"]["parserErrorPreview"]), "```"])
    if report["extractor"].get("rawOutputPreview"):
        lines.extend(["", "## Raw Output Preview", "```text", str(report["extractor"]["rawOutputPreview"]), "```"])

    outcome = report.get("outcome") or {}
    lines.extend(["", "## Outcome", f"- status: `{outcome.get('status')}`"])
    if outcome.get("status") == "failed":
        lines.append(f"- reason: `{outcome.get('reason')}`")
    else:
        lines.extend(
            [
                f"- extractedPreferenceCount: `{outcome.get('extractedPreferenceCount')}`",
                f"- extractedKnowledgeCount: `{outcome.get('extractedKnowledgeCount')}`",
                f"- persistedPreferenceCount: `{outcome.get('persistedPreferenceCount')}`",
                f"- persistedKnowledgeCount: `{outcome.get('persistedKnowledgeCount')}`",
                f"- noPersistedMemoryReason: `{outcome.get('noPersistedMemoryReason') or 'none'}`",
                f"- graphWouldGrow: `{outcome.get('graph', {}).get('wouldGrow')}`",
            ]
        )
        preference_items = outcome.get("preferences") or []
        if preference_items:
            lines.extend(["", "### Preferences"])
            for item in preference_items:
                lines.append(
                    f"- `{item['key']}` [{item['scope']}] => `{item['decision']}` "
                    f"(importance={item['importance']}, confidence={item['confidence']:.2f})"
                )
        knowledge_items = outcome.get("knowledge") or []
        if knowledge_items:
            lines.extend(["", "### Knowledge"])
            for item in knowledge_items:
                lines.append(
                    f"- `{item['category']}` [{item['scope']}] => `{item['decision']}` "
                    f"(importance={item['importance']}, confidence={item['confidence']:.2f})"
                )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay a live session through Memory Agent extraction without persisting durable memory.")
    parser.add_argument("session_id", help="Target session id")
    parser.add_argument("--policy-source", choices=["current", "defaults"], default="current")
    parser.add_argument("--output-dir", help="Optional directory to write Markdown + JSON report")
    args = parser.parse_args()

    report = build_session_replay_report(session_id=args.session_id, policy_source=args.policy_source)
    markdown = _render_markdown(report)
    json_text = json.dumps(report, ensure_ascii=False, indent=2)

    if args.output_dir:
        output_dir = Path(args.output_dir).expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / f"memory-session-replay-{args.session_id}.md").write_text(markdown, encoding="utf-8")
        (output_dir / f"memory-session-replay-{args.session_id}.json").write_text(json_text, encoding="utf-8")
        print(f"wrote markdown -> {output_dir / f'memory-session-replay-{args.session_id}.md'}")
        print(f"wrote json -> {output_dir / f'memory-session-replay-{args.session_id}.json'}")
    else:
        print(markdown)
        print()
        print(json_text)


if __name__ == "__main__":
    main()
