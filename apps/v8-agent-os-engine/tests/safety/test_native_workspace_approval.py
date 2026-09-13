from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.database import db
from core.model_governance_exceptions import ModelGovernanceInterventionRequired
from core.tools.native import command, tool_governance, workspace_file
from core import workspace_capability
from erc.command_service import CommandService
from erc.safety_guardian import DEFAULT_SAFETY_GUARDIAN_CONFIG, SafetyGuardian


@pytest.fixture
def native_scope(tmp_path, monkeypatch):
    root, outside = tmp_path / "workspace", tmp_path / "external"
    root.mkdir()
    outside.mkdir()
    context = {"runtime_kind": "chat", "agent_id": "supervisor", "workspace_path": str(root),
               "safety_approval_mode": "manual", "run_id": tmp_path.name, "session_id": tmp_path.name}
    db.create_or_update_session(tmp_path.name, "Workspace approval fixture")
    db.create_run_record(tmp_path.name, tmp_path.name, status="running")
    monkeypatch.setattr(workspace_capability.workspace_authority_service, "resolve_from_context", lambda *_a, **_k:
        SimpleNamespace(as_dict=lambda: {"workspaceRoot": str(root), "mainWorkspaceRoot": str(root),
                                        "sideEffectsAllowed": True, "trustState": "trusted"}))
    guardian = SafetyGuardian()
    config = deepcopy(DEFAULT_SAFETY_GUARDIAN_CONFIG)
    monkeypatch.setattr(guardian, "_config", lambda: config)
    monkeypatch.setattr(guardian, "log_decision_event", lambda **_kwargs: None)
    monkeypatch.setattr(guardian, "observe_post_action", lambda **_kwargs: None)
    for module in (command, workspace_file, tool_governance):
        monkeypatch.setattr(module, "get_runtime_context", lambda: context)
        monkeypatch.setattr(module, "safety_guardian", guardian)
    monkeypatch.setattr(workspace_file, "_record_agent_written_file_artifact", lambda *_a, **_k: None)
    monkeypatch.setattr(workspace_file, "mark_workspace_state_stale", lambda *_a, **_k: None)
    return context, root, outside, guardian


def remember(context, error, *, approved=True):
    from erc.models import ApprovalRequest
    service = CommandService()
    pending = service.request_approval(ApprovalRequest(approval_id=context["run_id"] + "-approval",
        session_id=context["session_id"], run_id=context["run_id"], approval_kind="safety_review",
        request=error.request_payload))
    if approved:
        service.approve(pending["approval_id"], {"approved": True})
    else:
        service.reject(pending["approval_id"], {"approved": False})


@pytest.mark.parametrize("mode", ["manual", "reduced", "minimal"])
@pytest.mark.parametrize("action", ["read", "write", "grep", "command"])
def test_native_external_operation_uses_current_approval_mode(native_scope, monkeypatch, mode, action):
    context, _root, outside, guardian = native_scope
    context["safety_approval_mode"] = mode
    target = outside / "fixture.txt"
    target.write_text("READ_PROOF", encoding="utf-8")
    launches = []
    def stop_at_process(command_text, dialect, runtime_context):
        launches.append(command_text)
        raise OSError("fixture process boundary")
    monkeypatch.setattr(command, "_shell_subprocess_launch", stop_at_process)
    # A pre-existing broad path allowlist must not authorize this exact scope.
    monkeypatch.setattr(guardian, "is_allowlisted", lambda _d: pytest.fail("external access must not reuse a broad allowlist"))
    new_file = outside / "created.txt"
    def invoke():
        if action == "read":
            return workspace_file.read_native_file.func(str(target), tool_call_id="read")
        if action == "grep":
            return workspace_file.grep_search.func("READ_PROOF", str(outside), tool_call_id="grep")
        if action == "write":
            return workspace_file.write_native_file.func(str(new_file), "WRITE_PROOF", tool_call_id="write")
        return command.execute_system_command.func(f'Remove-Item -LiteralPath "{target}"', shell_dialect="powershell", tool_call_id="cmd")
    if mode != "minimal":
        with pytest.raises(ModelGovernanceInterventionRequired) as waiting:
            invoke()
        assert waiting.value.request_payload["riskCode"] == "workspace_external_access"
        assert not new_file.exists() and launches == []
        remember(context, waiting.value)
    result = invoke()
    if action in {"read", "grep"}:
        assert "READ_PROOF" in result
    elif action == "write":
        assert new_file.read_text(encoding="utf-8") == "WRITE_PROOF"
    else:
        assert len(launches) == 1 and "launch_failed" in result


@pytest.mark.parametrize("change", ["target", "content", "cancel", "reject"])
def test_external_approval_does_not_authorize_changed_or_cancelled_write(native_scope, change):
    context, _root, outside, _guardian = native_scope
    target = outside / "first.txt"
    with pytest.raises(ModelGovernanceInterventionRequired) as waiting:
        workspace_file.write_native_file.func(str(target), "one", tool_call_id="same-call")
    remember(context, waiting.value, approved=change != "reject")
    next_target = outside / "other.txt" if change == "target" else target
    if change == "cancel":
        db.update_run_record(context["run_id"], status="cancelled")
        result = workspace_file.write_native_file.func(str(next_target), "one", tool_call_id="same-call")
        assert "取消" in result
    else:
        with pytest.raises(ModelGovernanceInterventionRequired):
            workspace_file.write_native_file.func(str(next_target), "two" if change == "content" else "one", tool_call_id="same-call")
    assert not target.exists() and not next_target.exists()


@pytest.mark.parametrize("overrides", [
    {"runtime_kind": "subagent", "actor_role": "direct_subagent", "agent_id": "worker"},
    {"runtime_kind": "subagent", "actor_role": "grandchild", "agent_id": "worker", "delegation_depth": 2},
    {"engineering_task_capsule": {"writeSet": []}},
    {"allowed_write_paths": []},
])
def test_delegated_contract_cannot_expand_via_workspace_approval(native_scope, overrides):
    context, _root, outside, _guardian = native_scope
    context.update(overrides)
    context["safety_approval_mode"] = "minimal"
    target = outside / "blocked.txt"
    result = workspace_file.write_native_file.func(str(target), "never")
    assert "scope_block" in result or "workspace_boundary_block" in result
    assert not target.exists()


def test_external_secret_read_stays_blocked_even_in_minimal(native_scope):
    context, _root, outside, _guardian = native_scope
    context["safety_approval_mode"] = "minimal"
    secret = outside / ".ssh" / "id_ed25519"
    secret.parent.mkdir()
    secret.write_text("PRIVATE_TEST_MARKER", encoding="utf-8")
    result = workspace_file.read_native_file.func(str(secret))
    assert "PRIVATE_TEST_MARKER" not in result and "阻止" in result


def test_full_tool_call_preserves_id_and_reuses_only_identical_read(native_scope):
    context, _root, outside, _guardian = native_scope
    target = outside / "source.txt"
    target.write_text("TOOL_CALL_PROOF", encoding="utf-8")
    call = {"type": "tool_call", "name": "read_native_file", "id": "exact-read", "args": {"path": str(target)}}
    with pytest.raises(ModelGovernanceInterventionRequired) as waiting:
        workspace_file.read_native_file.invoke(call)
    assert waiting.value.request_payload["toolCallId"] == "exact-read"
    assert "tool_call_id" not in workspace_file.read_native_file.tool_call_schema.model_json_schema()["properties"]
    remember(context, waiting.value)
    for _ in range(2):
        result = workspace_file.read_native_file.invoke(call)
        assert result.tool_call_id == "exact-read" and "TOOL_CALL_PROOF" in result.content
    with pytest.raises(ModelGovernanceInterventionRequired):
        workspace_file.read_native_file.invoke({**call, "args": {"path": str(target), "start_line": 1}})


def test_approved_command_does_not_follow_changed_cwd(native_scope):
    context, root, outside, _guardian = native_scope
    target = outside / "first.txt"
    text = f'Remove-Item -LiteralPath "{target}"'
    with pytest.raises(ModelGovernanceInterventionRequired) as waiting:
        command.execute_system_command.func(text, cwd=str(root), shell_dialect="powershell", tool_call_id="same")
    remember(context, waiting.value)
    with pytest.raises(ModelGovernanceInterventionRequired):
        command.execute_system_command.func(text, cwd=str(outside), shell_dialect="powershell", tool_call_id="same")


def test_approval_does_not_follow_retargeted_symlink(native_scope):
    context, _root, outside, _guardian = native_scope
    original, replacement, link = outside / "original.txt", outside / "replacement.txt", outside / "link.txt"
    original.write_text("FIRST", encoding="utf-8")
    replacement.write_text("OTHER", encoding="utf-8")
    try:
        link.symlink_to(original)
    except OSError:
        pytest.skip("Current platform does not permit symlink creation")
    with pytest.raises(ModelGovernanceInterventionRequired) as waiting:
        workspace_file.read_native_file.func(str(link), tool_call_id="same")
    remember(context, waiting.value)
    link.unlink()
    link.symlink_to(replacement)
    with pytest.raises(ModelGovernanceInterventionRequired):
        workspace_file.read_native_file.func(str(link), tool_call_id="same")


def test_old_workspace_hard_stop_cannot_satisfy_approval_oracle(native_scope, monkeypatch):
    _context, _root, outside, _guardian = native_scope
    target = outside / "counterexample.txt"
    with monkeypatch.context() as old:
        old.setattr(workspace_file, "workspace_scope_reviewable", lambda *_a: False)
        assert "workspace_boundary_block" in workspace_file.read_native_file.func(str(target))
    with pytest.raises(ModelGovernanceInterventionRequired):
        workspace_file.read_native_file.func(str(target))


@pytest.mark.parametrize("entry", ["session", "argv"])
def test_other_command_entries_request_same_exact_safety_owner(native_scope, monkeypatch, entry):
    context, _root, outside, _guardian = native_scope
    target = outside / "fixture.txt"
    launches = []
    def launch(*_args, **_kwargs):
        launches.append(entry)
        raise OSError("fixture process boundary")
    monkeypatch.setattr(command, "BackgroundProcess", launch)
    monkeypatch.setattr(command, "run_windowless_bounded", launch)
    def invoke():
        if entry == "argv":
            return command.execute_governed_argv(["fixture.exe", str(target)], tool_call_id="same")
        return command._launch_background_command(f'Remove-Item -LiteralPath "{target}"', shell_dialect="powershell",
            terminal_mode="pipe", tool_call_id="same")
    with pytest.raises(ModelGovernanceInterventionRequired) as waiting:
        invoke()
    assert launches == [] and waiting.value.request_payload["riskCode"] == "workspace_external_access"
    remember(context, waiting.value)
    if entry == "session":
        with pytest.raises(command.CommandSessionBackendError):
            invoke()
    else:
        assert invoke()["ok"] is False
    assert launches == [entry]


def test_grep_exact_reviewed_file_remains_readable_after_approval(native_scope, monkeypatch):
    from erc.safety_guardian import SafetyDecision
    context, _root, outside, guardian = native_scope
    target = outside / "reviewed.txt"
    target.write_text("EXACT_FILE_PROOF", encoding="utf-8")
    monkeypatch.setattr(guardian, "assess_file_read", lambda *_a, **_k: SafetyDecision(
        verdict="review", risk_code="sensitive_system_read_command", details={"path": str(target)}))
    with pytest.raises(ModelGovernanceInterventionRequired) as waiting:
        workspace_file.grep_search.func("EXACT_FILE_PROOF", str(target), tool_call_id="grep-exact")
    remember(context, waiting.value)
    result = workspace_file.grep_search.func("EXACT_FILE_PROOF", str(target), tool_call_id="grep-exact")
    assert "reviewed.txt:1:EXACT_FILE_PROOF" in result


def test_grep_directory_keeps_secrets_unread_and_reports_partial(native_scope):
    context, _root, outside, _guardian = native_scope
    context["safety_approval_mode"] = "minimal"
    secret = outside / ".ssh" / "id_ed25519"
    secret.parent.mkdir()
    secret.write_text("DO_NOT_READ_SECRET", encoding="utf-8")
    (outside / "ordinary.txt").write_text("ordinary", encoding="utf-8")
    result = workspace_file.grep_search.func("DO_NOT_READ", str(outside), tool_call_id="grep-dir")
    assert "DO_NOT_READ_SECRET" not in result and "id_ed25519" not in result
    assert "搜索范围不完整" in result and "已跳过 1 个" in result
    assert "No matches found" not in result
