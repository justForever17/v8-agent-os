from __future__ import annotations

from pathlib import Path

from runtimes.rpa.default_templates import GITHUB_STAR_TEMPLATE_ID, ensure_system_rpa_seed_templates
from runtimes.rpa.store import RPAScriptStore
from runtimes.rpa.template_service import RPATemplateService


def test_github_star_system_seed_template_is_idempotent(tmp_path: Path):
    store = RPAScriptStore(root_dir=tmp_path)

    first = ensure_system_rpa_seed_templates(store)
    second = ensure_system_rpa_seed_templates(store)
    templates = store.list_templates(limit=20)

    assert len([item for item in templates if item["id"] == GITHUB_STAR_TEMPLATE_ID]) == 1
    assert first[0]["id"] == second[0]["id"] == GITHUB_STAR_TEMPLATE_ID
    template = store.get_template(GITHUB_STAR_TEMPLATE_ID)
    assert template is not None
    assert template["steps"][0]["use"] == "computer_use_playbook"
    assert template["steps"][0]["params"]["selectedPlaybook"] == "github.star_repository"
    assert {item["name"] for item in template["variables"]} >= {"repo_owner", "repo_name", "repo_url", "desired_state"}
    desired = next(item for item in template["variables"] if item["name"] == "desired_state")
    assert desired["enum"] == ["starred", "unstarred"]
    serialized = str(template).lower()
    assert "github_token" not in serialized
    assert "password" not in serialized


def test_rpa_route_recommends_github_star_delegate_template(tmp_path: Path):
    store = RPAScriptStore(root_dir=tmp_path)
    ensure_system_rpa_seed_templates(store)
    service = RPATemplateService(script_store=store)

    route = service.recommend_execution_route(
        goal="去 GitHub 给 TuriX 点星标",
        app_id=None,
        variables={"repo_url": "https://github.com/TurixAI/TuriX-CUA"},
    )

    assert route["recommendedTemplateId"] == GITHUB_STAR_TEMPLATE_ID
    assert route["recommendedMatch"]["id"] == GITHUB_STAR_TEMPLATE_ID
    assert route["recommendedMode"] in {"hybrid_mode", "learn_mode"}
    assert route["recommendedAction"] in {"run_hybrid_with_computer_use", "start_computer_use_learning"}
    assert route["recommendedMatch"]["executionPath"] in {"candidate_shadow", "computer_use_first"}


def test_rpa_route_recommends_github_star_delegate_template_for_unstar(tmp_path: Path):
    store = RPAScriptStore(root_dir=tmp_path)
    ensure_system_rpa_seed_templates(store)
    service = RPATemplateService(script_store=store)

    route = service.recommend_execution_route(
        goal="去 GitHub 给 TuriX 取消星标",
        app_id=None,
        variables={"repo_url": "https://github.com/TurixAI/TuriX-CUA", "desired_state": "unstarred"},
    )

    assert route["recommendedTemplateId"] == GITHUB_STAR_TEMPLATE_ID
    assert route["recommendedMatch"]["id"] == GITHUB_STAR_TEMPLATE_ID
