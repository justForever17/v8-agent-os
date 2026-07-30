from __future__ import annotations

from core.supervisor_identity import (
    apply_supervisor_identity_to_profile,
    non_identity_preferences,
    render_supervisor_identity_context,
    resolve_supervisor_identity,
)


def test_legacy_naming_placeholders_do_not_become_identity_truth() -> None:
    preferences = {
        "assistant_name": "Please help me come up with a name.",
        "user_call_name": "master",
        "preferred_language": "zh-CN",
    }

    identity = resolve_supervisor_identity(preferences)
    context = render_supervisor_identity_context(preferences)

    assert identity.self_name == "Supervisor"
    assert identity.user_address == "用户"
    assert "Current self-name: Supervisor" in context
    assert "Address the human as: 用户" in context
    assert "Address the human as: 主理人" not in context
    assert non_identity_preferences(preferences) == {"preferred_language": "zh-CN"}


def test_real_memory_identity_keeps_supervisor_role_and_uses_user_names() -> None:
    identity = resolve_supervisor_identity({
        "assistant_name": "小八",
        "user_call_name": "Sunny",
    })
    context = render_supervisor_identity_context({
        "assistant_name": "小八",
        "user_call_name": "Sunny",
    })

    assert identity.canonical_role == "Supervisor"
    assert identity.self_name == "小八"
    assert identity.user_address == "Sunny"
    assert "canonical role remains Supervisor" in context


def test_runtime_profile_preserves_admin_presentation_truth() -> None:
    admin_profile = {
        "name": "智能主管",
        "roleLabel": "主理人",
        "avatar": "/brand-mark.png",
    }

    default_profile = apply_supervisor_identity_to_profile(
        admin_profile,
        {
            "assistant_name": "Please help me come up with a name.",
            "user_call_name": "master",
        },
    )
    named_profile = apply_supervisor_identity_to_profile(
        admin_profile,
        {"assistant_name": "小八", "user_call_name": "Sunny"},
    )

    assert default_profile == {
        "name": "智能主管",
        "roleLabel": "主理人",
        "avatar": "/brand-mark.png",
    }
    assert named_profile["name"] == "智能主管"
    assert named_profile["roleLabel"] == "主理人"


def test_runtime_profile_uses_identity_fallback_only_when_presentation_is_blank() -> None:
    profile = apply_supervisor_identity_to_profile(
        {"name": "", "roleLabel": "", "avatar": "/brand-mark.png"},
        {"assistant_name": "小八", "user_call_name": "Sunny"},
    )

    assert profile == {
        "name": "小八",
        "roleLabel": "Supervisor",
        "avatar": "/brand-mark.png",
    }
