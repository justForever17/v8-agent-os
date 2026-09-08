from copy import deepcopy
import hashlib
import json
import re

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from core.tool_surface import apply_tool_surface_budget
from core.tools import research_broker as broker, research_ledger as ledger
from core.tools.research_quality import research_answer_is_usable
from runtimes.research.agent import ResearchAgent


@pytest.fixture(autouse=True)
def isolated_ledger(monkeypatch, tmp_path):
    from tests.core.research_scope_fixture import research_sessions
    from erc.runtime_context import bind_runtime_context

    monkeypatch.setenv("V8_RESEARCH_LEDGER_PATH", str(tmp_path / "research.json"))
    _, create, _ = research_sessions(monkeypatch, tmp_path)
    with bind_runtime_context(**create("research-test-session")):
        yield


def accepted_bundle(size, partial):
    """Actual researcher/reviewer contract and broker nesting; no provider call."""
    tail = "\nANSWER_END [S1]"
    answer = ("保留换行与原始引用 [S1]。🧪\n" * size)[:size - len(tail)] + tail
    # Keep the repeated fixture's last citation syntactically intact.
    cut = answer.rfind("\n", 0, size - len(tail))
    answer = answer[:cut] + "补" * (size - len(tail) - cut) + tail
    limitations = ["仅适用于已查阅版本：" + "完整限制🧪 " * 110 + " [S1]", "边界说明第一行\n第二行仍须保留 [S1]"] if partial else []
    coverage = "partial" if partial else "complete"
    def call(name, **args):
        return AIMessage(content="", tool_calls=[{"id": name, "name": name, "args": args}])
    writer = iter([
        call("read_research_source", sourceKey="S1"),
        call("submit_research_answer", answer=answer, coverage=coverage, limitations=limitations),
    ])
    reviewer = iter([call("review_research_answer", decision="accept", coverage=coverage, limitations=limitations, corrections=[])])
    calls = []
    def invoke(messages, tools, **kwargs):
        calls.append(kwargs["reviewer"])
        return next(reviewer if kwargs["reviewer"] else writer)
    agent = ResearchAgent(invoke=invoke, acquire=None, progress=lambda **_: None,
                          writer_id="configured-writer", reviewer_id="configured-reviewer")
    url = "https://example.org/spec/v2?edition=2&layout=full"
    agent.store.add([{"ok": True, "url": url, "title": "原始规范", "text": "The selected version retains the original format.",
                      "retrievedAt": "2026-09-08T00:00:00Z"}])
    pack = agent.run(question="说明原始格式与适用限制。")
    assert pack["answer"] == answer and calls == [False, False, True]
    bundle = {**pack, "kind": "research_evidence_bundle", "evidenceBundleId": f"long-{size}-{partial}",
              "researchResult": pack, "finalExperiencePack": pack, "sourceMatrix": pack["sourceUrls"],
              "researchEvidenceBank": {"sources": list(agent.store.sources.values())}}
    answer_pack = broker._research_answer_pack(bundle)
    bundle.update(researchAnswerPack=answer_pack, ok=answer_pack["usableAnswer"],
                  deliveryReady=answer_pack["score"]["deliveryReady"], usableAnswer=answer_pack["usableAnswer"])
    assert research_answer_is_usable(bundle)
    expected = (answer + "\n\n## Limitations\n" + ("\n".join(f"- {item}" for item in limitations) or "None recorded.")
                + "\n\n## Original read observations\n"
                "Copy claimId exactly; these identify reads, not semantic approval. "
                "For independent verification open the relevant saved source bodies with "
                "research_broker(mode='get_evidence', evidenceBundleId="
                f"{bundle['evidenceBundleId']!r}, sourceKey='S#', startChar=0). "
                "Reading this answer alone is not a source check.\n"
                "| claimId | Citation | Actual URL |\n|---|---|---|\n"
                f"| read_S1:R1 | [S1] | {url} |"
                + f"\n\n## Sources\n- [S1] 原始规范: {url}")
    return bundle, expected, limitations


@pytest.mark.parametrize("key", ["S19", "[S19]"])
def test_answer_directory_preserves_original_citation_marker(monkeypatch, key):
    from runtimes.research import answer_read
    monkeypatch.setattr(answer_read, "research_selected_sources", lambda _: [
        {"citationKey": key, "title": "原发布页", "url": "https://example.org/original"}])
    document = answer_read.saved_answer_document({"answer": "有边界的结论 [S19]"})
    assert document.endswith("## Sources\n- [S19] 原发布页: https://example.org/original")
    assert "[[S19]]" not in document


def visible_call(*, budget=1400, **args):
    result = broker.research_broker.invoke({"type": "tool_call", "name": "research_broker", "id": "read-saved", "args": args})
    assert isinstance(result, ToolMessage)
    raw = json.loads(result.content)
    visible = apply_tool_surface_budget(result, {"agentVisibleBudget": budget}, tool_name="research_broker").content
    return raw, visible


@pytest.mark.parametrize("size,partial,budget", [(32000, False, 1400), (80000, True, 1400), (32000, True, 6000)])
def test_long_accepted_answer_is_losslessly_recoverable_through_public_tool_and_actual_surface(monkeypatch, size, partial, budget):
    bundle, expected, limitations = accepted_bundle(size, partial)
    stored = ledger.store_evidence_bundle(bundle, ttl_seconds=60, scope="research-test-session")
    assert stored["experienceUpdate"]["status"] == "created"
    def forbidden(*args, **kwargs):
        pytest.fail("saved answer recovery must not acquire sources or run a model")
    monkeypatch.setattr(broker, "_execute_research_agent", forbidden)
    monkeypatch.setattr(broker, "_source_router_read", forbidden)
    monkeypatch.setattr(ResearchAgent, "run", forbidden)
    raw, summary = visible_call(mode="get_experience", experiencePackId=stored["experienceUpdate"]["experiencePackId"])
    assert "readAnswer=True" in summary and "experiencePackId=..." not in summary
    _, summary = visible_call(mode="get_evidence", evidenceBundleId=stored["evidenceBundleId"])
    assert "preview" in summary.lower() and "Research evidence incomplete" not in summary
    assert "readAnswer=True" in summary
    if partial:
        assert "scope: partial" in summary
    pages, offset = [], 0
    while True:
        page, surface = visible_call(mode="get_evidence", evidenceBundleId=stored["evidenceBundleId"],
                                     readAnswer=True, startChar=offset, maxChars=12000, budget=budget)
        assert page["ok"] and page["deliveryScope"] == ("partial" if partial else "complete")
        assert page["offsetUnit"] == "unicode_code_points"
        assert page["answerChars"] == size and page["contentChars"] == len(expected)
        assert page["contentSha256"] == hashlib.sha256(expected.encode("utf-8")).hexdigest()
        assert len(surface) <= budget
        shown = surface.split("<answer-document>\n", 1)[1].rsplit("\n</answer-document>", 1)[0]
        pages.append(shown)
        match = re.search(r"nextOffset: (\d+|None)", surface)
        if match[1] == "None":
            break
        next_offset = int(match[1])
        assert next_offset == offset + len(shown) and next_offset > offset
        assert f"startChar={next_offset})" in surface
        offset = next_offset
    restored = "".join(pages)
    assert restored == expected
    assert restored[:size] == bundle["answer"] and "ANSWER_END [S1]" in restored
    assert all(limitation in restored for limitation in limitations)
    assert ledger.get_evidence_bundle(stored["evidenceBundleId"])["independentReview"] == bundle["independentReview"]


def test_answer_reader_rejects_unreviewed_answer_and_ambiguous_source_selector(monkeypatch):
    bundle, _, _ = accepted_bundle(32000, True)
    bundle = deepcopy(bundle)
    for payload in (bundle, bundle["finalExperiencePack"], bundle["researchResult"], bundle["researchAnswerPack"]):
        payload["reviewDecision"] = "retry"
        payload["independentReview"]["reviewDecision"] = "retry"
    monkeypatch.setattr(broker, "get_evidence_bundle", lambda _, **kwargs: bundle)
    raw, surface = visible_call(mode="get_evidence", evidenceBundleId=bundle["evidenceBundleId"], readAnswer=True)
    assert not raw["ok"] and raw["error"] == "research_answer_not_accepted"
    assert "ANSWER_END" not in surface and "<answer-document>" not in surface
    raw, _ = visible_call(mode="get_evidence", evidenceBundleId=bundle["evidenceBundleId"], readAnswer=True, sourceKey="S1")
    assert raw["error"] == "choose_answer_or_source_not_both"
    assert broker.research_broker.args_schema.model_fields["readAnswer"].default is False


def test_saved_source_reopen_preserves_original_observation_id_in_actual_agent_surface(monkeypatch):
    bundle, _, _ = accepted_bundle(2000, False)
    # The source was the fourth original read, not the first read in a new
    # transient EvidenceStore opened by get_evidence.
    # Supply the original canonical claims independently of the transient reader.
    monkeypatch.setattr("core.tools.research_quality.research_claims", lambda _: [{
        "claimId": "read_S1:R4", "supportingSources": [{"citationKey": "S1", "url": "https://example.org/spec/v2?edition=2&layout=full"}],
    }])
    monkeypatch.setattr(broker, "get_evidence_bundle", lambda _, **kwargs: bundle)
    raw, shown = visible_call(mode="get_evidence", evidenceBundleId="saved", sourceKey="S1")
    assert "evidenceRef" not in raw
    assert raw["originalReadBindings"][0]["claimId"] == "read_S1:R4"
    assert "read_S1:R4" in shown and "S1:R1" not in shown


def test_answer_observation_index_does_not_infer_ids_from_prose(monkeypatch):
    from runtimes.research import answer_read
    monkeypatch.setattr(answer_read, "research_claims", lambda _: [{
        "claimId": "read_S19:R42", "supportingSources": [{"citationKey": "S19", "url": "https://example.org/reprint?a=2"}],
    }])
    document = answer_read.saved_answer_document({"evidenceBundleId": "saved", "answer": "Prose calls this C1/S19:R1."})
    assert document.startswith("Prose calls this C1/S19:R1.")
    index = document.split("## Original read observations", 1)[1].split("## Sources", 1)[0]
    assert "| read_S19:R42 | [S19] | https://example.org/reprint?a=2 |" in index
    assert "S19:R1" not in index and "C1" not in index
    assert "sourceKey='S#'" in index


def test_preview_proof_cannot_bless_changed_answer_or_failed_transport():
    bundle, _, _ = accepted_bundle(32000, True)
    stored = ledger.store_evidence_bundle(bundle, ttl_seconds=60, scope="research-test-session")
    raw, _ = visible_call(mode="get_evidence", evidenceBundleId=stored["evidenceBundleId"])
    for changed in (False, True):
        payload = deepcopy(raw)
        if changed:
            payload["answer"] = payload["researchAnswerPack"]["answer"] = "Tampered claim [S1]"
        else:
            payload["ok"] = False
        shown = apply_tool_surface_budget(ToolMessage(content=json.dumps(payload), name="research_broker", tool_call_id="altered"),
                                          {"agentVisibleBudget": 1400}, tool_name="research_broker").content
        assert "Research evidence incomplete" in shown
        assert "Tampered claim" not in shown
