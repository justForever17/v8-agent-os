from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping


CANONICAL_SUPERVISOR_ROLE = "Supervisor"
DEFAULT_USER_ADDRESS = "用户"
_IDENTITY_PREFERENCE_KEYS = frozenset({"assistant_name", "user_call_name"})
_LEGACY_ASSISTANT_PLACEHOLDERS = frozenset({
    "please help me come up with a name.",
    "please help me come up with a name",
    "please give me a name",
    "help me choose a name",
})
_LEGACY_USER_PLACEHOLDERS = frozenset({"master"})
_NAMING_INSTRUCTION_PATTERN = re.compile(
    r"(?:help|please|ask|choose|come up|give|name me|帮我|请|起名|取名).{0,24}(?:name|名字|名称)",
    flags=re.IGNORECASE,
)


def _single_line(value: Any, *, max_chars: int = 80) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:max_chars]


def _valid_assistant_name(value: Any) -> str:
    candidate = _single_line(value)
    if not candidate or candidate.casefold() in _LEGACY_ASSISTANT_PLACEHOLDERS:
        return ""
    if _NAMING_INSTRUCTION_PATTERN.search(candidate):
        return ""
    return candidate


def _valid_user_address(value: Any) -> str:
    candidate = _single_line(value)
    if not candidate or candidate.casefold() in _LEGACY_USER_PLACEHOLDERS:
        return ""
    return candidate


@dataclass(frozen=True, slots=True)
class SupervisorIdentity:
    canonical_role: str
    self_name: str
    user_address: str
    customized: bool


def resolve_supervisor_identity(preferences: Mapping[str, Any] | None) -> SupervisorIdentity:
    profile = preferences or {}
    custom_name = _valid_assistant_name(profile.get("assistant_name"))
    user_address = _valid_user_address(profile.get("user_call_name")) or DEFAULT_USER_ADDRESS
    return SupervisorIdentity(
        canonical_role=CANONICAL_SUPERVISOR_ROLE,
        self_name=custom_name or CANONICAL_SUPERVISOR_ROLE,
        user_address=user_address,
        customized=bool(custom_name),
    )


def apply_supervisor_identity_to_profile(
    profile: Mapping[str, Any] | None,
    preferences: Mapping[str, Any] | None,
) -> dict[str, Any]:
    identity = resolve_supervisor_identity(preferences)
    projected = dict(profile or {})
    projected["name"] = _valid_assistant_name(projected.get("name")) or identity.self_name
    projected["roleLabel"] = _single_line(projected.get("roleLabel")) or identity.canonical_role
    return projected


def non_identity_preferences(preferences: Mapping[str, Any] | None) -> dict[str, Any]:
    return {
        str(key): value
        for key, value in dict(preferences or {}).items()
        if str(key) not in _IDENTITY_PREFERENCE_KEYS
    }


def render_supervisor_identity_context(preferences: Mapping[str, Any] | None) -> str:
    identity = resolve_supervisor_identity(preferences)
    naming_rule = (
        f"The user has given you the name {identity.self_name!r}; use it consistently while your canonical role remains Supervisor."
        if identity.customized
        else "No valid user-given name is stored. Identify yourself consistently as Supervisor; do not invent or request a name unless the user chooses to name you."
    )
    return (
        "[SUPERVISOR IDENTITY]\n"
        f"Canonical role: {identity.canonical_role}\n"
        f"Current self-name: {identity.self_name}\n"
        f"Address the human as: {identity.user_address}\n"
        f"{naming_rule}\n"
        "`Supervisor` and the historical product label `主理人中枢` refer to you, never to the human. "
        "Never address the human as 主理人、主管、Supervisor, or 智能主管 unless the human explicitly chose that address.\n"
        "[/SUPERVISOR IDENTITY]"
    )
