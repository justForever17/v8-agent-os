from __future__ import annotations

import json

from tests.scripts import export_child_delegation_contract_dry_run as dry_run


def test_child_delegation_contract_preserves_executable_context() -> None:
    payload = dry_run.build_matrix()

    assert payload["passed"] is True
    assert payload["validations"]["child_request_has_full_task_brief"] is True
    assert payload["validations"]["child_task_goal_is_semantic_not_id"] is True
    assert payload["validations"]["grandchild_prompt_preserves_workspace_spec_task_and_evidence_refs"] is True

    child_request = json.dumps(payload["childRequest"], ensure_ascii=False)
    assert "绝区零角色“玲”" in child_request
    assert "spec_ling_nuwa_dry_run" in child_request
    assert "runtime-handoff://parent-evidence-pack" in child_request

    prompt = str(payload["grandchildPromptExcerpt"])
    assert "E:/Projects/test3" in prompt
    assert "TASK-003" in prompt
    assert "runtime-handoff://parent-evidence-pack" in prompt
    assert "ID-only" in prompt
