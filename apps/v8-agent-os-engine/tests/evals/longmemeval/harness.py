from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterator
from unittest.mock import patch


ENGINE_ROOT = Path(__file__).resolve().parents[3]
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

from core import memory_store as memory_store_module
from core.knowledge_db import KnowledgeDB


OFFICIAL_LONGMEMEVAL_REPO = "https://github.com/xiaowu0162/LongMemEval"
SUPPORTED_SPLITS = ("oracle", "longmemeval_s_cleaned", "longmemeval_m_cleaned")


@dataclass(slots=True)
class LongMemEvalInstance:
    question_id: str
    question: str
    question_type: str = ""
    answer: str | None = None
    question_date: str | None = None
    haystack_session_ids: list[str] = field(default_factory=list)
    haystack_dates: list[str] = field(default_factory=list)
    haystack_sessions: list[list[dict[str, Any]]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class LongMemEvalHypothesis:
    question_id: str
    hypothesis: str
    metadata: dict[str, Any] = field(default_factory=dict)


Answerer = Callable[[LongMemEvalInstance, str, list[dict[str, Any]]], str]


def load_longmemeval_dataset(path: str | Path, *, limit: int | None = None) -> list[LongMemEvalInstance]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    rows: list[Any]
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        candidate = payload.get("data") or payload.get("instances") or payload.get("examples") or []
        rows = candidate if isinstance(candidate, list) else []
    else:
        rows = []
    instances = [_normalize_instance(row) for row in rows if isinstance(row, dict)]
    if limit is not None:
        return instances[: max(0, int(limit))]
    return instances


def write_hypotheses_jsonl(hypotheses: list[LongMemEvalHypothesis], output_path: str | Path) -> Path:
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for item in hypotheses:
            handle.write(json.dumps({"question_id": item.question_id, "hypothesis": item.hypothesis}, ensure_ascii=False) + "\n")
    return target


def build_official_evaluation_command(
    *,
    official_repo_root: str | Path,
    hypothesis_file: str | Path,
    data_file: str | Path,
    judge_model: str = "gpt-4o",
) -> list[str]:
    repo_root = Path(official_repo_root)
    return [
        "python",
        str(repo_root / "src" / "evaluation" / "evaluate_qa.py"),
        judge_model,
        str(hypothesis_file),
        str(data_file),
    ]


def default_smoke_answerer(
    instance: LongMemEvalInstance,
    memory_context: str,
    retrieved_facts: list[dict[str, Any]],
) -> str:
    """Non-scoring answerer used only to validate the V8OS harness plumbing."""
    retrieved_preview = "; ".join(str(item.get("fact") or item.get("text") or "")[:120] for item in retrieved_facts[:2])
    context_preview = memory_context.replace("\n", " ")[:240]
    return (
        "V8OS LongMemEval smoke hypothesis. "
        f"question={instance.question[:160]} "
        f"retrieved={retrieved_preview or 'none'} "
        f"context={context_preview or 'none'}"
    ).strip()


def create_v8os_model_answerer(*, model_id: str, max_context_chars: int = 240000) -> Answerer:
    """Create a real V8OS model-backed answerer.

    This intentionally stays small: LongMemEval scoring truth is still the
    official evaluator, while this function only turns V8OS Memory Runtime
    evidence into an official-compatible hypothesis string.
    """
    normalized_model_id = str(model_id or "").strip()
    if not normalized_model_id:
        raise ValueError("--model-id is required when --answerer=v8os")

    from langchain_core.messages import HumanMessage, SystemMessage

    from core.llm_factory import llm_factory

    def _answer(instance: LongMemEvalInstance, memory_context: str, retrieved_facts: list[dict[str, Any]]) -> str:
        budget = max(0, int(max_context_chars))
        history_block = _render_history_evidence(instance, max_chars=max(1000, int(budget * 0.82)))
        retrieved_block = _render_retrieved_facts(retrieved_facts, limit=8, max_chars=max(1000, int(budget * 0.14)))
        context_block = str(memory_context or "")[: max(0, budget - len(history_block) - len(retrieved_block))]
        model = llm_factory.create_chat_model(
            normalized_model_id,
            temperature=0,
            max_tokens=768,
            streaming=False,
            _role="longmemeval_answerer",
        )
        response = model.invoke(
            [
                SystemMessage(
                    content=(
                        "You are answering LongMemEval questions using only the provided V8OS memory evidence. "
                        "Give a concise answer. If the evidence is insufficient, say you do not have enough evidence. "
                        "Do not mention implementation details, retrieval, or benchmark machinery."
                    )
                ),
                HumanMessage(
                    content=(
                        f"Question date: {instance.question_date or 'unknown'}\n"
                        f"Question type: {instance.question_type or 'unknown'}\n"
                        f"Question: {instance.question}\n\n"
                        "[TIMESTAMPED HISTORY]\n"
                        f"{history_block or '(empty)'}\n\n"
                        "[V8OS MEMORY CONTEXT]\n"
                        f"{context_block or '(empty)'}\n\n"
                        "[RETRIEVED FACTS]\n"
                        f"{retrieved_block or '(none)'}\n\n"
                        "Answer the question now."
                    )
                ),
            ]
        )
        return _extract_response_text(response) or "I do not have enough evidence."

    return _answer


class LongMemEvalV8Harness:
    def __init__(self, *, answerer: Answerer | None = None):
        self.answerer = answerer or default_smoke_answerer

    def run_dataset(
        self,
        *,
        input_path: str | Path,
        output_jsonl_path: str | Path,
        split: str = "oracle",
        limit: int | None = None,
    ) -> dict[str, Any]:
        instances = load_longmemeval_dataset(input_path, limit=limit)
        hypotheses = self.run_instances(instances=instances, split=split)
        output_path = write_hypotheses_jsonl(hypotheses, output_jsonl_path)
        return {
            "status": "completed",
            "split": split,
            "inputPath": str(input_path),
            "outputJsonlPath": str(output_path),
            "questionCount": len(instances),
            "hypothesisCount": len(hypotheses),
            "officialScore": None,
            "officialScoreAvailable": False,
            "note": "This adapter only generates official-compatible JSONL. Run LongMemEval evaluate_qa.py separately for official scoring.",
        }

    def run_instances(
        self,
        *,
        instances: list[LongMemEvalInstance],
        split: str = "oracle",
    ) -> list[LongMemEvalHypothesis]:
        if split not in SUPPORTED_SPLITS:
            raise ValueError(f"Unsupported LongMemEval split: {split}")
        hypotheses: list[LongMemEvalHypothesis] = []
        with isolated_v8_memory_store() as store:
            for instance in instances:
                scope = f"external_api_thread:longmemeval_{_safe_scope_token(instance.question_id)}"
                self._ingest_instance(store=store, instance=instance, scope=scope)
                retrieved = store.query_knowledge(
                    query=instance.question,
                    scopes=["global", scope],
                    category="longmemeval_session",
                    limit=8,
                )
                memory_context = store.build_session_context(
                    user_query=instance.question,
                    scope=scope,
                    scope_chain=["global", scope],
                    session_id=f"longmemeval:{instance.question_id}",
                    suppress_daily_memory=False,
                    suppress_memory_map=False,
                )
                hypothesis = self.answerer(instance, memory_context, retrieved)
                hypotheses.append(
                    LongMemEvalHypothesis(
                        question_id=instance.question_id,
                        hypothesis=str(hypothesis or "").strip(),
                        metadata={
                            "questionType": instance.question_type,
                            "retrievedCount": len(retrieved),
                            "memoryContextChars": len(memory_context),
                        },
                    )
                )
        return hypotheses

    def _ingest_instance(self, *, store: memory_store_module.MemoryStore, instance: LongMemEvalInstance, scope: str) -> None:
        for index, session in enumerate(_ordered_sessions(instance)):
            session_id = _session_id_for(instance, index)
            date_value = _session_date_for(instance, index)
            text = _render_session_text(session)
            if not text:
                continue
            store.add_knowledge(
                fact=f"LongMemEval session {session_id} at {date_value or 'unknown date'}:\n{text}",
                category="longmemeval_session",
                scope=scope,
                source_session=f"longmemeval:{instance.question_id}:{session_id}",
                tags=["longmemeval", instance.question_type or "unknown"],
            )


@contextmanager
def isolated_v8_memory_store() -> Iterator[memory_store_module.MemoryStore]:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        memory_root = root / "memory"
        knowledge_db = KnowledgeDB(db_path=root / "knowledge.db")
        with patch.object(memory_store_module, "CONFIG_DIR", root), patch.object(
            memory_store_module,
            "MEMORY_ROOT",
            memory_root,
        ), patch("core.knowledge_db.knowledge_db", knowledge_db), patch(
            "core.storage.storage.get_memory_config",
            return_value={
                "recall_strategy": "keyword",
                "fts_enabled": True,
                "graph_enabled": True,
                "retrieval_threshold": 0.05,
                "recall_top_k": 8,
                "max_context_tokens": 6000,
                "passive_summary_enabled": False,
                "passive_memory_map_enabled": False,
                "passive_recent_activity_teaser_enabled": False,
                "passive_knowledge_graph_summary_enabled": True,
            },
        ), patch("core.vector_store.get_vector_store", return_value=_NoopVectorStore()):
            yield memory_store_module.MemoryStore()


class _NoopVectorStore:
    collection = None

    def add_documents(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def _normalize_instance(row: dict[str, Any]) -> LongMemEvalInstance:
    question_id = str(row.get("question_id") or row.get("id") or "").strip()
    if not question_id:
        raise ValueError("LongMemEval instance missing question_id")
    return LongMemEvalInstance(
        question_id=question_id,
        question=str(row.get("question") or "").strip(),
        question_type=str(row.get("question_type") or row.get("type") or "").strip(),
        answer=str(row.get("answer")).strip() if row.get("answer") is not None else None,
        question_date=str(row.get("question_date") or "").strip() or None,
        haystack_session_ids=[str(item) for item in list(row.get("haystack_session_ids") or [])],
        haystack_dates=[str(item) for item in list(row.get("haystack_dates") or [])],
        haystack_sessions=[list(item or []) for item in list(row.get("haystack_sessions") or [])],
        raw=dict(row),
    )


def _ordered_sessions(instance: LongMemEvalInstance) -> list[list[dict[str, Any]]]:
    indexed = []
    for index, session in enumerate(instance.haystack_sessions):
        indexed.append((_session_date_for(instance, index), index, session))
    indexed.sort(key=lambda item: (item[0] or "", item[1]))
    return [session for _date_value, _index, session in indexed]


def _session_id_for(instance: LongMemEvalInstance, index: int) -> str:
    if index < len(instance.haystack_session_ids):
        return instance.haystack_session_ids[index]
    return f"session-{index + 1}"


def _session_date_for(instance: LongMemEvalInstance, index: int) -> str:
    if index < len(instance.haystack_dates):
        return instance.haystack_dates[index]
    return ""


def _render_session_text(session: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for turn in session:
        if not isinstance(turn, dict):
            continue
        role = str(turn.get("role") or turn.get("speaker") or "unknown").strip()
        content = str(turn.get("content") or "").strip()
        if not content:
            continue
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


def _render_history_evidence(instance: LongMemEvalInstance, *, max_chars: int) -> str:
    lines: list[str] = []
    for index, session in enumerate(_ordered_sessions(instance), start=1):
        date_value = _session_date_for(instance, index - 1) or "unknown date"
        session_id = _session_id_for(instance, index - 1)
        text = _render_session_text(session)
        if not text:
            continue
        lines.append(f"## Session {index}: {session_id} at {date_value}\n{text}")
    rendered = "\n\n".join(lines)
    if len(rendered) <= max_chars:
        return rendered
    head = int(max_chars * 0.72)
    tail = max_chars - head - 120
    return (
        rendered[: max(0, head)].rstrip()
        + "\n\n[... timestamped history truncated; preserving latest tail ...]\n\n"
        + rendered[-max(0, tail) :].lstrip()
    )


def _render_retrieved_facts(items: list[dict[str, Any]], *, limit: int, max_chars: int = 24000) -> str:
    lines: list[str] = []
    for index, item in enumerate(items[: max(0, int(limit))], start=1):
        fact = str(item.get("fact") or item.get("text") or item.get("content") or "").strip()
        if not fact:
            continue
        scope = str(item.get("scope") or "").strip()
        score = item.get("score")
        prefix = f"{index}."
        if scope:
            prefix += f" [{scope}]"
        if score is not None:
            prefix += f" score={score}"
        lines.append(f"{prefix} {fact}")
    rendered = "\n".join(lines)
    if len(rendered) <= max_chars:
        return rendered
    head = int(max_chars * 0.7)
    tail = max_chars - head - 80
    return rendered[: max(0, head)].rstrip() + "\n[... retrieved facts truncated ...]\n" + rendered[-max(0, tail) :].lstrip()


def _extract_response_text(response: Any) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content.strip()
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
        return " ".join(part.strip() for part in parts if str(part).strip()).strip()
    return str(content or "").strip()


def _safe_scope_token(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(value or "").strip()) or "unknown"


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate LongMemEval official-compatible hypothesis JSONL with V8OS Memory Runtime.")
    parser.add_argument("--input", required=True, help="Path to a LongMemEval cleaned JSON file.")
    parser.add_argument("--output", required=True, help="Path to write question_id/hypothesis JSONL.")
    parser.add_argument("--split", default="oracle", choices=SUPPORTED_SPLITS, help="Dataset split label for diagnostics.")
    parser.add_argument("--limit", type=int, default=None, help="Optional number of instances for smoke runs.")
    parser.add_argument("--answerer", choices=("smoke", "v8os"), default="smoke", help="Use smoke plumbing output or a real V8OS model-backed answerer.")
    parser.add_argument("--model-id", default="", help="Registered V8OS model id used when --answerer=v8os.")
    parser.add_argument("--max-context-chars", type=int, default=240000, help="Maximum LongMemEval evidence characters sent to the V8OS answerer.")
    args = parser.parse_args()
    answerer = (
        create_v8os_model_answerer(model_id=args.model_id, max_context_chars=args.max_context_chars)
        if args.answerer == "v8os"
        else None
    )
    result = LongMemEvalV8Harness(answerer=answerer).run_dataset(
        input_path=args.input,
        output_jsonl_path=args.output,
        split=args.split,
        limit=args.limit,
    )
    result["answerer"] = args.answerer
    if args.model_id:
        result["modelId"] = args.model_id
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
