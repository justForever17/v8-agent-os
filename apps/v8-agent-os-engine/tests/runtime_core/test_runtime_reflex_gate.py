from __future__ import annotations

from types import SimpleNamespace

from core.runtime import reflex_gate
from core.runtime.reflex_gate import (
    RuntimeEvidenceFeedbackService,
    RuntimePreflightGate,
    RuntimeReflexService,
    render_gate_prompt_addition,
    render_reflex_prompt_addition,
)


def _route_bundle(**summary):
    return SimpleNamespace(
        selected_skill_names=summary.pop("selected_skill_names", []),
        selected_skill_ids=[],
        exposed_mcp_tool_names=summary.pop("exposed_mcp_tool_names", []),
        skill_root_descriptors=[],
        candidate_summary=summary,
    )


def test_reflex_bias_is_low_token_and_non_executing():
    decision = RuntimeReflexService().evaluate(
        user_query="请用语音播报这段内容，然后修复这个 Python bug",
        scope="project:test1",
        scope_chain=["global", "project:test1"],
        session_id="s1",
        route_bundle=_route_bundle(selected_skill_names=["python-helper"]),
        state={"engineeringMode": "force"},
    )

    assert decision.mode == "bias"
    assert "voice_output_discipline" in decision.matchedReflexes
    assert "engineering_read_before_write" in decision.matchedReflexes
    assert len(decision.promptPatch) <= 400
    assert "自动执行工具" not in decision.promptPatch
    assert decision.mode != "block"
    rendered = render_reflex_prompt_addition(decision)
    assert "[RUNTIME REFLEX]" in rendered
    assert "python-helper" in rendered


def test_gate_detects_route_candidate_spread_without_inventing_task_contracts():
    route = _route_bundle(
        selected_skill_names=["a", "b", "c"],
        skillStage1Entries=[{"id": str(i)} for i in range(9)],
        skillEntries=[{"id": "a"}, {"id": "b"}, {"id": "c"}],
    )
    decision = RuntimePreflightGate().evaluate(
        user_query="修复代码并跑测试",
        scope="project:test1",
        scope_chain=["global", "project:test1"],
        session_id="s1",
        route_bundle=route,
        state={},
    )

    assert decision.status == "watch"
    assert decision.riskLevel == "low"
    assert "route_candidate_spread" in decision.reasons
    assert "[RUNTIME GATE]" in render_gate_prompt_addition(decision)


def test_gate_preserves_existing_hard_stale_barrier_signal():
    decision = RuntimePreflightGate().evaluate(
        user_query="用新复制的 skill 写公众号文章",
        scope="project:test1",
        scope_chain=["global", "project:test1"],
        session_id="s1",
        route_bundle=_route_bundle(
            inventoryReadyState="refreshing",
            inventoryBarrierTimedOut=True,
            dirtyVisibleRoots=["E:/Projects/test1/.agents/skills"],
        ),
        state={},
    )

    assert decision.status == "blocked"
    assert decision.riskLevel == "high"
    assert "inventory_barrier_timed_out" in decision.reasons


def test_read_only_reviewer_without_write_set_is_safe():
    decision = RuntimePreflightGate().evaluate(
        user_query="审查这次代码变更",
        scope="workspace:main",
        scope_chain=["global", "workspace:main"],
        session_id="s1",
        route_bundle=_route_bundle(),
        state={},
    )

    assert decision.status in {"clean", "watch"}


def test_read_only_multi_runtime_plan_does_not_ask_user_because_workspace_is_dirty():
    decision = RuntimePreflightGate().evaluate(
        user_query=(
            "先做多源调研，再产出工程方案，并派子代理复核；"
            "不要真实写项目文件。"
        ),
        scope="workspace:main",
        scope_chain=["global", "workspace:main"],
        session_id="s1",
        route_bundle=_route_bundle(
            selected_skill_names=["a", "b", "c"],
            skillStage1Entries=[{"id": str(i)} for i in range(9)],
        ),
        state={
            "task_shape_hint": {
                "writingRoute": {"present": True, "requiresArtifact": False},
                "boundaryDecision": {
                    "executionMode": "engineering_runtime",
                    "askUserNeeded": False,
                },
            },
            "engineering_context": {
                "worksetSoftGateDecision": {
                    "risk": "unknown_write_set",
                    "warning": True,
                    "changedFiles": ["existing-user-change.py"],
                }
            },
        },
    )

    assert decision.status == "watch"
    assert decision.diagnostics["readOnlyExecutionIntent"] is True
    assert "engineering_workset_risk_not_applicable_to_read_only" in decision.reasons
    assert "engineering_workset_risk" not in decision.reasons


def test_evidence_feedback_emits_only_signal_events(monkeypatch):
    class FakeDb:
        def __init__(self):
            self.events = []

        def get_next_runtime_seq(self, _session_id):
            return len(self.events) + 1

        def add_runtime_event(self, event):
            self.events.append(event)

    fake_db = FakeDb()
    monkeypatch.setattr(reflex_gate, "db", fake_db)
    reflex = RuntimeReflexService().evaluate(
        user_query="微信文章",
        scope="project:test1",
        scope_chain=["global", "project:test1"],
        session_id="s1",
        route_bundle=_route_bundle(selected_skill_names=["wechat-account-articles"]),
        state={},
    )
    gate = RuntimePreflightGate().evaluate(
        user_query="微信文章",
        scope="project:test1",
        scope_chain=["global", "project:test1"],
        session_id="s1",
        route_bundle=_route_bundle(selected_skill_names=["wechat-account-articles"]),
        state={},
    )

    packet = RuntimeEvidenceFeedbackService().record(
        session_id="s1",
        run_id="r1",
        scope="project:test1",
        reflex_decision=reflex,
        gate_decision=gate,
        memory_diagnostics={"graphSummaryInjected": True, "graphSummaryRelationCount": 2},
        route_bundle=_route_bundle(selected_skill_names=["wechat-account-articles"]),
        state={},
    )

    assert packet.graphSummary["injected"] is True
    assert [event["topic"] for event in fake_db.events] == [
        "runtime.reflex.decision",
        "memory.evidence.feedback",
    ]


def test_evidence_feedback_skips_clean_noise(monkeypatch):
    class FakeDb:
        def __init__(self):
            self.events = []

        def get_next_runtime_seq(self, _session_id):
            return len(self.events) + 1

        def add_runtime_event(self, event):
            self.events.append(event)

    fake_db = FakeDb()
    monkeypatch.setattr(reflex_gate, "db", fake_db)
    reflex = RuntimeReflexService().evaluate(
        user_query="你好",
        scope="global",
        scope_chain=["global"],
        session_id="s1",
        route_bundle=_route_bundle(),
        state={},
    )
    gate = RuntimePreflightGate().evaluate(
        user_query="你好",
        scope="global",
        scope_chain=["global"],
        session_id="s1",
        route_bundle=_route_bundle(),
        state={},
    )

    RuntimeEvidenceFeedbackService().record(
        session_id="s1",
        run_id="r1",
        scope="global",
        reflex_decision=reflex,
        gate_decision=gate,
        memory_diagnostics={},
        route_bundle=_route_bundle(),
        state={},
    )

    assert fake_db.events == []
