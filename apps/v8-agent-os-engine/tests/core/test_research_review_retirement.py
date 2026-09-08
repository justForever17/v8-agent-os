"""Current Agent behavior after private Research retirement; see research_private_retirements.md."""

import json

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from core.tools.research_quality import research_bundle_is_accepted
from runtimes.research.agent import ResearchAgent


def _call(name, **arguments):
    return AIMessage(content="", tool_calls=[{
        "id": f"fixture-{name}", "name": name, "args": arguments,
    }])


@pytest.mark.parametrize("accept_second_review", [True, False])
def test_legacy_cli_title_and_keywords_cannot_promote_revise_to_accept(monkeypatch, tmp_path, accept_second_review):
    monkeypatch.setenv("V8_RESEARCH_LEDGER_PATH", str(tmp_path / "research-ledger.json"))
    answer = (
        "## Evidence-backed practical guidance\n\n"
        "### CLI integration\n\n"
        "**Practical synthesis:** argparse, Click, and Typer expose different documented path-input contracts; "
        "choose by the required conversion and validation behavior. [S1]\n\n"
        "### Configuration and validation\n\n"
        "**Practical synthesis:** Use the documented application directory when its platform semantics fit, "
        "and apply exists/file_okay validation at the CLI boundary. [S1]"
    )
    objection = "The answer lacks cross-library integration guidance."
    revise = _call("review_research_answer", decision="revise", coverage="complete", corrections=[{
        "kind": "coverage", "reason": objection,
    }], limitations=[])
    accept = _call("review_research_answer", decision="accept", coverage="complete", corrections=[], limitations=[])
    writer = iter([
        _call("read_research_source", sourceKey="S1"),
        _call("submit_research_answer", answer=answer, coverage="complete"),
        _call("submit_research_answer", answer=answer, coverage="complete"),
    ])
    review_responses = iter([revise, accept if accept_second_review else revise])
    writer_inputs = []
    review_count = 0

    def invoke(messages, _tools, *, reviewer: bool, **_kwargs):
        nonlocal review_count
        if reviewer:
            review_count += 1
            return next(review_responses)
        writer_inputs.append(list(messages))
        return next(writer)

    agent = ResearchAgent(
        invoke=invoke, acquire=None, progress=lambda **_: None,
        writer_id="configured-writer", reviewer_id="configured-reviewer", max_revisions=1,
    )
    agent.store.add([{
        "ok": True, "url": "https://fixture.example/cli", "title": "CLI contracts",
        "text": "argparse accepts a callable converter. Click and Typer expose path conversion and validation. "
                "Application directories depend on the platform.",
        "retrievedAt": "2026-09-08T00:00:00Z",
    }])
    result = agent.run(question="What are current pathlib CLI best practices? Cite official sources.")

    assert review_count == 2  # Even an apparent false veto returns to the Agent.
    feedback = [json.loads(message.content) for message in writer_inputs[-1]
                if isinstance(message, ToolMessage) and "revision_requested" in message.content]
    assert feedback[0]["review"]["decision"] == "revise"
    assert feedback[0]["review"]["corrections"][0]["reason"] == objection
    assert research_bundle_is_accepted(result) is accept_second_review
    assert agent.searches == 0
    if accept_second_review:
        assert result["answer"] == answer
        assert result["modelSynthesis"]["revisionCount"] == 1
    else:
        assert result["answer"] == ""
        assert result["modelSynthesis"]["fallbackReason"] == "research_revision_budget_exhausted"


@pytest.mark.parametrize("mode", ["auto", "fixed"])
def test_current_broker_agent_owns_queries_and_full_submission_under_configured_budget(monkeypatch, tmp_path, mode):
    from core.tools import research_broker as broker
    from tests.core.test_research_agent import ANSWER, ScriptedTransport, approve, call, read, source, submit

    monkeypatch.setenv("V8_RESEARCH_LEDGER_PATH", str(tmp_path / "research-ledger.json"))
    transport = ScriptedTransport([
        call("search_research_sources", queries=["version 2 file format specification"], searchEngine="bing_cn"),
        read(), submit(),
    ], [approve()])
    wire_calls = []

    class Model:
        def __init__(self, reviewer):
            self.reviewer = reviewer
            self._meta = {"model_ref": "fixture-reviewer" if reviewer else "fixture-writer", "global_max_tokens": 1500,
                          "model_record": {"maxTokens": 1500, "outputTokenMode": mode}}

        def bind_tools(self, tools, **_kwargs):
            self.tools = tools
            return self

        def invoke(self, messages, **kwargs):
            if mode == "auto":
                assert "max_tokens" not in kwargs
            else:
                assert kwargs["max_tokens"] == 1500
            response = transport(messages, self.tools, reviewer=self.reviewer, seconds=kwargs["timeout"])
            wire_calls.append((self.reviewer, response.tool_calls[0]["name"]))
            return response

    monkeypatch.setattr(broker, "_create_web_research_architect_llm_candidates", lambda: [(Model(False), "fixture-writer", "web-research-architect")])
    monkeypatch.setattr(broker, "_create_web_research_reviewer_llm_candidates", lambda _: [(Model(True), "fixture-reviewer", "agent_reviewer:verification-engineer")])
    searches, reads = [], []
    monkeypatch.setattr(broker, "_source_router_search", lambda **kwargs: searches.append(kwargs) or json.dumps({
        "ok": True, "provider": "bing_cn", "results": [{"url": source()["url"], "title": "Format specification"}],
    }))
    monkeypatch.setattr(broker, "_source_router_read", lambda **kwargs: reads.append(kwargs) or json.dumps(source()))
    bundle = broker._run_agent_owned_research(
        question="Does version 2 change the file format?", research_intent="format comparison",
        source_policy="authoritative", freshness="auto", allowed_domains=["example.org"], blocked_domains=[],
        use_agent_browser_profile=False, tool_call_id="retirement-fixture", max_shards=1, max_rounds=2,
        preferred_language="en", seed_urls=[], deliverable="evidence_bundle", experience_reuse={},
    )
    assert wire_calls == [(False, "search_research_sources"), (False, "read_research_source"),
                          (False, "submit_research_answer"), (True, "review_research_answer")]
    assert len(searches) == len(reads) == 1
    assert searches[0]["query"] == "version 2 file format specification"
    assert searches[0]["search_engine"] == "bing_cn"
    assert bundle["answer"] == ANSWER
    assert bundle["usableAnswer"] is True and bundle["deliveryReady"] is True
    assert research_bundle_is_accepted(bundle)
    assert bundle["claimTable"][0]["verificationKind"] == "read_snapshot_ref_only"
