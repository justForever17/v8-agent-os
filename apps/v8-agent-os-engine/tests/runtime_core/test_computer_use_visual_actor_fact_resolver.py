from __future__ import annotations

from pathlib import Path

from core.database import DatabaseManager
from runtimes.computer_use.candidate_board import build_candidate_board, candidate_board_source_catalog
import runtimes.computer_use.fact_resolver as fact_resolver_module
from runtimes.computer_use.fact_resolver import classify_goal, resolve_goal_facts
from runtimes.computer_use.platform_probe_runner import build_platform_probe_matrix
from runtimes.computer_use.playbooks import built_in_playbook_seeds
from runtimes.computer_use.short_sequence_verifier import ShortSequenceVisualExecutor, build_short_sequence_verification
from runtimes.computer_use.visual_actor_provider import VisualActorRequest, create_visual_actor_provider


def test_candidate_board_merges_sources_with_stable_ids():
    board_a = build_candidate_board(
        goal="点击 Star 按钮",
        observation={
            "elements": [
                {"role": "button", "name": "Star", "bounds": {"x": 100, "y": 50, "width": 80, "height": 32}},
            ]
        },
        locator_resolution={
            "matches": [
                {"source": "ocr_text", "text": "Star", "bbox": {"x": 101, "y": 51, "width": 78, "height": 30}},
            ],
            "semanticRanking": {
                "rankedCandidates": [
                    {"role": "action_button", "label": "Star repository", "bbox": {"x": 100, "y": 50, "width": 80, "height": 32}},
                ]
            },
        },
        browser_candidates=[
            {"role": "button", "label": "Star", "bbox": {"x": 100, "y": 50, "width": 80, "height": 32}},
        ],
    )
    board_b = build_candidate_board(
        goal="点击 Star 按钮",
        observation={
            "elements": [
                {"role": "button", "name": "Star", "bounds": {"x": 100, "y": 50, "width": 80, "height": 32}},
            ]
        },
        locator_resolution={
            "matches": [
                {"source": "ocr_text", "text": "Star", "bbox": {"x": 101, "y": 51, "width": 78, "height": 30}},
            ]
        },
        browser_candidates=[
            {"role": "button", "label": "Star", "bbox": {"x": 100, "y": 50, "width": 80, "height": 32}},
        ],
    )

    payload = board_a.as_dict()
    assert payload["summary"]["count"] >= 3
    assert "browser_dom" in payload["sources"]
    assert board_a.candidates[0].candidateId == board_b.candidates[0].candidateId
    assert board_a.candidates[0].center == {"x": 140.0, "y": 66.0}


def test_visual_actor_provider_only_proposes_top_candidate(monkeypatch):
    provider = create_visual_actor_provider()
    monkeypatch.setattr(
        provider,
        "_role_state",
        lambda: {
            "available": False,
            "role": "computer_use_visual_actor",
            "modelId": None,
            "providerId": None,
            "fallbackRoles": ["computer_use_visual_judge", "vision"],
        },
    )
    board = build_candidate_board(
        goal="给仓库点星标",
        browser_candidates=[
            {"role": "button", "label": "Star", "bbox": {"x": 100, "y": 50, "width": 80, "height": 32}},
        ],
    )

    proposal = provider.propose(
        VisualActorRequest(
            goal="给仓库点星标",
            candidateBoard=board,
            displayBounds={"width": 200, "height": 100},
        )
    ).as_dict()

    assert proposal["status"] == "proposed"
    assert proposal["actionType"] == "click"
    assert proposal["normalizedPoint"] == {"x": 0.7, "y": 0.66}
    assert proposal["metadata"]["proposalPolicy"] == "no_direct_execution"


def test_visual_actor_provider_calls_multimodal_model_when_available(monkeypatch, tmp_path: Path):
    class _FakeModel:
        def __init__(self):
            self.messages = []

        def invoke(self, messages):
            self.messages.append(messages)

            class _Response:
                content = '{"status":"proposed","actionType":"click","candidateId":"wanted","confidence":0.82,"expectedStateChange":"button becomes active","reason":"matches goal","pureVisual":null}'

            return _Response()

    fake_model = _FakeModel()
    provider = create_visual_actor_provider()
    monkeypatch.setattr(
        provider,
        "_role_state",
        lambda: {
            "available": True,
            "role": "computer_use_visual_actor",
            "modelId": "vision-model",
            "providerId": "provider-a",
            "fallbackRoles": [],
        },
    )
    monkeypatch.setattr(
        "runtimes.computer_use.visual_actor_provider.llm_factory.create_for_role",
        lambda *_args, **_kwargs: fake_model,
    )
    screenshot = tmp_path / "screen.png"
    screenshot.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc```\x00\x00\x00\x04\x00\x01"
        b"\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    board = build_candidate_board(
        goal="点击确认",
        browser_candidates=[
            {"role": "button", "label": "确认", "bbox": {"x": 10, "y": 20, "width": 100, "height": 40}},
        ],
    )
    board.candidates[0].candidateId = "wanted"

    proposal = provider.propose(
        VisualActorRequest(
            goal="点击确认",
            screenshotPath=str(screenshot),
            candidateBoard=board,
            displayBounds={"width": 200, "height": 100},
        )
    ).as_dict()

    assert fake_model.messages
    assert proposal["source"] == "visual_actor_model"
    assert proposal["candidateId"] == "wanted"
    assert proposal["confidence"] == 0.82
    assert proposal["metadata"]["proposalPolicy"] == "no_direct_execution"


def test_general_fact_resolver_handles_turix_and_unknown_targets():
    intent = classify_goal("去 GitHub 给 TuriX 点个星标")
    result = resolve_goal_facts("去 GitHub 给 TuriX 点个星标", intent=intent)

    assert intent["operation"] == "star_repository"
    assert result.status == "resolved"
    assert result.canonicalTarget["url"] == "https://github.com/TurixAI/TuriX-CUA"

    unresolved = resolve_goal_facts(
        "去 GitHub 给完全不存在的某个项目点星标",
        intent=classify_goal("去 GitHub 给完全不存在的某个项目点星标"),
    )
    assert unresolved.status == "needs_fact_resolution"
    assert unresolved.reason == "canonical_repo_url_not_resolved"


def test_fact_resolver_persists_public_targets_without_private_fields(monkeypatch, tmp_path: Path):
    test_db = DatabaseManager(tmp_path / "state.db")
    monkeypatch.setattr(fact_resolver_module, "db", test_db)
    fact_resolver_module._FACT_CACHE.clear()
    query = "打开 https://example.com/docs?token=secret"
    private = resolve_goal_facts(query, intent=classify_goal(query))
    assert private.status == "resolved"
    with test_db.get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM computer_use_fact_ledger").fetchone()[0] == 0

    public_query = "打开 https://example.com/docs"
    public = resolve_goal_facts(public_query, intent=classify_goal(public_query))
    assert public.status == "resolved"
    fact_resolver_module._FACT_CACHE.clear()
    from_ledger = resolve_goal_facts(public_query, intent=classify_goal(public_query))

    assert from_ledger.status == "resolved"
    assert from_ledger.reason == "persistent_fact_ledger"
    assert from_ledger.canonicalTarget["url"] == "https://example.com/docs"


def test_runtime_native_playbook_catalog_expands_without_external_skill_dependency():
    ids = {item["id"] for item in built_in_playbook_seeds()}

    assert "github.star_repository" in ids
    assert "web.search_and_open_result" in ids
    assert "browser.login_gate" in ids
    assert "web.form_submit" in ids
    assert "web.file_upload" in ids
    assert "download_and_open" in ids
    assert "settings.toggle_option" in ids
    github = next(item for item in built_in_playbook_seeds() if item["id"] == "github.star_repository")
    assert github["runtimeNative"] is True
    assert github["sourceRefs"][0]["usedAs"] == "experience_pattern_rewritten_as_v8_runtime_native_seed"


def test_platform_probe_matrix_does_not_claim_non_host_real_passed():
    matrix = build_platform_probe_matrix(
        current_platform="Windows",
        driver_summary={"available": True},
        browser_summary={"helperScriptExists": False},
    )

    assert matrix["platforms"]["windows"]["currentHost"] is True
    assert matrix["platforms"]["macos"]["currentHost"] is False
    assert "real_host_passed" not in matrix["platforms"]["macos"]["statusCounts"]
    browser_check = next(item for item in matrix["platforms"]["windows"]["checks"] if item["key"] == "browser_cdp")
    assert browser_check["status"] == "blocked_by_missing_helper"


def test_short_sequence_verifier_requires_observed_progress():
    no_progress = build_short_sequence_verification(
        goal="点击自绘按钮",
        candidate={"candidateId": "cand_1"},
        pre_state={"dialog": "open"},
        action={"actionType": "click"},
        post_state={"dialog": "open"},
    )
    progressed = build_short_sequence_verification(
        goal="点击自绘按钮",
        candidate={"candidateId": "cand_1"},
        pre_state={"dialog": "open"},
        action={"actionType": "click"},
        post_state={"dialog": "closed"},
    )

    assert no_progress["status"] == "no_observed_progress"
    assert no_progress["nextStep"] == "try_next_candidate_or_ask_human"
    assert progressed["status"] == "advanced"


def test_short_sequence_executor_retries_until_progress(monkeypatch):
    states = [
        {"screenHash": "a"},
        {"screenHash": "a"},
        {"screenHash": "a"},
        {"screenHash": "a"},
        {"screenHash": "a"},
        {"screenHash": "b", "visibleText": "完成"},
    ]
    actions: list[str] = []

    def observe():
        return states.pop(0)

    def act(candidate):
        actions.append(candidate["candidateId"])
        return {"ok": True, "actionType": "click"}

    monkeypatch.setattr("runtimes.computer_use.short_sequence_verifier.time.sleep", lambda _seconds: None)
    result = ShortSequenceVisualExecutor(observe=observe, act=act, settle_seconds=0).run(
        goal="点击自绘按钮完成任务",
        candidates=[
            {"candidateId": "bad-1", "role": "button"},
            {"candidateId": "bad-2", "role": "button"},
            {"candidateId": "good-3", "role": "button"},
        ],
        expected_state_change="完成",
    ).as_dict()

    assert result["status"] == "succeeded"
    assert result["selectedCandidateId"] == "good-3"
    assert actions == ["bad-1", "bad-2", "good-3"]
    assert len(result["attempts"]) == 3


def test_candidate_board_source_catalog_includes_browser_profile_relevant_sources():
    sources = {item["source"] for item in candidate_board_source_catalog()}
    assert {"accessibility", "ocr_text", "browser_dom", "selector_memory", "history"}.issubset(sources)
