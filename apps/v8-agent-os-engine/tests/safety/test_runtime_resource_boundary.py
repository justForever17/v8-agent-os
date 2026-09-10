from copy import deepcopy

import pytest

from core.v8_agent_os_paths import V8_AGENT_OS_HOME
from erc.safety_guardian import DEFAULT_SAFETY_GUARDIAN_CONFIG, SafetyGuardian


@pytest.fixture
def guardian(monkeypatch):
    service = SafetyGuardian()
    config = deepcopy(DEFAULT_SAFETY_GUARDIAN_CONFIG)
    # Reproduce an upgraded installation carrying the old generated home/tmp rule.
    config["fileRules"]["protectedPaths"] = [str(V8_AGENT_OS_HOME), str(V8_AGENT_OS_HOME / "tmp")]
    monkeypatch.setattr(service, "_config", lambda: config)
    return service


@pytest.mark.parametrize("relative", ["logs/task.log", "tmp/screenshot.png", "workspace/report.md", "runtime-data/research/answer.md"])
def test_old_home_rule_does_not_turn_ordinary_data_into_kernel(guardian, relative):
    path = V8_AGENT_OS_HOME / relative
    assert not guardian._is_under_protected_path(path)
    assert guardian.assess_file_write(str(path), append=False).is_allow()
    assert guardian.assess_system_command(f'Get-Content -LiteralPath "{path}"').is_allow()


@pytest.mark.parametrize("relative", ["config.json", "users.json", "state.db", "state.db-wal", "checkpoints.db", "checkpoints.db-shm", "core/oauth/token.json"])
def test_control_and_auth_state_remain_protected(guardian, relative):
    path = V8_AGENT_OS_HOME / relative
    assert guardian._is_under_protected_path(path)
    assert not guardian.assess_file_write(str(path), append=False).is_allow()
    assert guardian._touches_protected_path_in_command(f'Remove-Item "{path}"')


def test_removing_container_still_targets_its_core_children(guardian):
    assert guardian._touches_protected_path_in_command(f'Remove-Item "{V8_AGENT_OS_HOME}" -Recurse')
    assert not guardian._touches_protected_path_in_command(f'Remove-Item "{V8_AGENT_OS_HOME / "logs" / "old.log"}"')


def test_parent_traversal_cannot_hide_core_target(guardian):
    assert guardian._is_under_protected_path(V8_AGENT_OS_HOME / "tmp" / ".." / "config.json")


def test_workspace_binding_cannot_reclassify_core_as_ordinary_file(guardian):
    context = {"workspace_path": str(V8_AGENT_OS_HOME), "safety_approval_mode": "minimal"}
    assert not guardian.assess_file_write(str(V8_AGENT_OS_HOME / "config.json"), append=False, runtime_context=context).is_allow()
    assert guardian.assess_file_write(str(V8_AGENT_OS_HOME / "logs" / "note.txt"), append=False, runtime_context=context).is_allow()


@pytest.mark.parametrize("relative", ["v8-session-unlock/build/x64/v8-session-unlock.exe", "v8-session-unlock/install.ps1", "v8-system-operations/build/x64/v8-system-operations.exe", "v8-system-operations/install.ps1", "v8-native-common/windows.cpp"])
def test_privileged_components_cannot_be_replaced_as_workspace_artifacts(guardian, relative):
    from pathlib import Path
    engine = Path(__file__).resolve().parents[2]
    target = engine / "native" / relative
    context = {"workspace_path": str(engine), "safety_approval_mode": "minimal"}
    assert not guardian.assess_file_write(str(target), append=False, runtime_context=context).is_allow()


def test_installed_privileged_component_is_kernel_not_an_ordinary_program(guardian):
    import os
    from pathlib import Path
    if os.name != "nt":
        pytest.skip("Windows installed component")
    target = Path(os.environ.get("ProgramW6432") or os.environ.get("ProgramFiles")) / "V8AgentOS/SystemOperations/v8-system-operations.exe"
    assert not guardian.assess_file_write(str(target), append=False).is_allow()
    assert guardian.assess_system_command(f'Set-Content -LiteralPath "{target}" -Value broken').is_block()


@pytest.mark.parametrize("template", [
    'Set-Content -LiteralPath "{target}" -Value fixture',
    'Set-Content -LiteralPath:"{target}" -Value fixture',
    'Add-Content -Path "{target}" -Value fixture',
    'Clear-Content "{target}"',
    'New-Item -Path "{target}" -ItemType File -Force',
    '"fixture" | Out-File -FilePath "{target}"',
    'echo fixture>"{target}"',
    'echo fixture>>"{target}"',
    'echo fixture&>"{target}"',
    'printf fixture | tee "{target}"',
    'cp ordinary.txt "{target}"',
    'Copy-Item -Path ordinary.txt -Destination "{target}"',
    'sudo -u root rm "{target}"',
])
def test_core_mutations_are_denied_including_redirection(guardian, template):
    from core.tools.native.tool_governance import should_auto_approve_safety_review
    target = V8_AGENT_OS_HOME / "config.json"
    decision = guardian.assess_system_command(template.format(target=target))
    assert decision.is_block() and not decision.allow_override
    assert decision.risk_code == "protected_path_command"
    assert not should_auto_approve_safety_review(decision, mode="minimal")


@pytest.mark.parametrize("command,context_key", [
    ('Set-Content config.json fixture', 'workspace_path'),
    ('echo fixture>config.json', 'command_cwd'),
    ('Add-Content ./tmp/../config.json fixture', 'cwd'),
    ('cd tmp; echo fixture>../config.json', 'command_cwd'),
    ('Set-Location tmp\nSet-Content ../config.json fixture', 'workspace_path'),
])
def test_relative_mutations_use_execution_cwd(guardian, command, context_key):
    decision = guardian.assess_system_command(command, runtime_context={context_key: str(V8_AGENT_OS_HOME)})
    assert decision.is_block() and decision.risk_code == "protected_path_command"


def test_explicit_cwd_precedes_workspace_and_unicode_path(guardian, tmp_path):
    target_dir = tmp_path / "受保护目录"
    guardian._config()["fileRules"]["protectedPaths"].append(str(target_dir / "凭据.json"))
    decision = guardian.assess_system_command('Set-Content "凭据.json" fixture', runtime_context={"workspace_path": str(tmp_path), "command_cwd": str(target_dir)})
    assert decision.is_block()


def test_environment_and_nested_shell_target_still_protected(guardian, monkeypatch):
    monkeypatch.setenv("V8_BOUNDARY_FIXTURE_ROOT", str(V8_AGENT_OS_HOME))
    commands = [
        'Set-Content "$env:V8_BOUNDARY_FIXTURE_ROOT/config.json" fixture',
        f"powershell -Command 'Set-Content \"{V8_AGENT_OS_HOME / 'config.json'}\" fixture'",
    ]
    for command in commands:
        assert guardian.assess_system_command(command).is_block()


@pytest.mark.parametrize("prefix", [
    'sudo echo fixture; ',
    'reg query "HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\ProfileList"; ',
    'Get-ChildItem ordinary; ',
])
def test_review_or_read_step_cannot_hide_later_core_deny(guardian, prefix):
    decision = guardian.assess_system_command(prefix + f'Remove-Item "{V8_AGENT_OS_HOME / "config.json"}"')
    assert decision.is_block() and decision.risk_code == "protected_path_command"


def test_explicit_block_rule_precedes_privilege_review(guardian):
    guardian._config()["commandRules"].insert(0, {"label": "fixture hard deny", "verdict": "block", "patterns": ["forbidden-fixture"]})
    decision = guardian.assess_system_command("sudo echo forbidden-fixture")
    assert decision.is_block() and decision.risk_code == "blocked_command_pattern"


def test_platform_read_review_cannot_hide_other_platform_auth_mutation(guardian):
    command = 'reg query "HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\ProfileList"; rm /etc/shadow'
    decision = guardian.assess_system_command(command)
    assert decision.is_block() and decision.risk_code == "linux_auth_store_mutation"


@pytest.mark.parametrize("template", [
    'Write-Output "Set-Content {core}"',
    'Set-Content -LiteralPath "{output}" -Value "{core}"',
    'Copy-Item -LiteralPath "{core}" -Destination "{output}"',
    'cp "{core}" "{output}"',
    'echo "literal > {core}"',
])
def test_text_values_and_copy_sources_are_not_mutation_targets(guardian, template):
    command = template.format(core=V8_AGENT_OS_HOME / "config.json", output=V8_AGENT_OS_HOME / "logs" / "note.txt")
    assert not guardian._touches_protected_path_in_command(command)


@pytest.mark.parametrize("relative", ["logs/old.log", ".agents/skills/example/SKILL.md"])
def test_single_ordinary_item_delete_not_a_bulk_core_delete(guardian, tmp_path, relative):
    from core.tools.native.tool_governance import should_auto_approve_safety_review
    context = {"workspace_path": str(tmp_path)}
    decision = guardian.assess_system_command(f'Remove-Item "{tmp_path / relative}"', runtime_context=context)
    assert not decision.is_block()
    assert decision.is_allow() or should_auto_approve_safety_review(decision, mode="minimal")


@pytest.mark.parametrize("suffix", ["", "/*"])
def test_whole_skill_root_delete_retains_bulk_review_not_kernel_deny(guardian, tmp_path, suffix):
    from core.tools.native.tool_governance import should_auto_approve_safety_review
    context = {"workspace_path": str(tmp_path)}
    command = f'Remove-Item "{tmp_path / ".agents" / "skills"}{suffix}" -Recurse'
    decision = guardian.assess_system_command(command, runtime_context=context)
    assert decision.is_review() and decision.risk_code == "bulk_skill_mutation"
    assert not should_auto_approve_safety_review(decision, mode="manual")
    assert not should_auto_approve_safety_review(decision, mode="reduced")
    assert should_auto_approve_safety_review(decision, mode="minimal")


def test_root_delete_pattern_does_not_match_ordinary_posix_path_prefix(guardian):
    assert guardian._matches_command_pattern("rm -rf /", "rm -rf /")
    assert guardian._matches_command_pattern('rm -rf "/"', "rm -rf /")
    assert not guardian._matches_command_pattern("rm -rf /tmp/ordinary-fixture", "rm -rf /")


def test_wildcard_matches_real_core_names_not_the_entire_parent(guardian):
    context = {"command_cwd": str(V8_AGENT_OS_HOME)}
    assert guardian._touches_protected_path_in_command('Remove-Item config*.json', context)
    assert guardian._touches_protected_path_in_command('Remove-Item core/*', context)
    assert not guardian._touches_protected_path_in_command('Remove-Item *.log', context)


def test_only_exact_generated_remove_rule_is_migrated(guardian):
    raw = deepcopy(DEFAULT_SAFETY_GUARDIAN_CONFIG)
    raw["commandRules"][0]["patterns"].append("remove-item")
    raw["commandRules"][1]["patterns"].remove("remove-item")
    result = guardian.normalize_config(raw)
    assert "remove-item" not in result["commandRules"][0]["patterns"]
    assert any(rule["verdict"] == "review" and "remove-item" in rule["patterns"] for rule in result["commandRules"])
    for custom in [{"id": "user_deny"}, {"patterns": ["remove-item"]}, {"patterns": [*raw["commandRules"][0]["patterns"], "user-command"]}]:
        config = deepcopy(raw)
        config["commandRules"][0].update(custom)
        result = guardian.normalize_config(config)
        assert result["commandRules"][0]["verdict"] == "block"
        assert "remove-item" in result["commandRules"][0]["patterns"]
