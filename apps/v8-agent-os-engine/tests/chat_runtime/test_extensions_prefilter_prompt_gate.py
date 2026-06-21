from __future__ import annotations

from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from graph.supervisor_turn import (
    _extensions_prefilter_signature,
    _should_include_extensions_prefilter_prompt,
)


def _bundle(*, skill_revision: str = "skills-a", root_signature: str = "roots-a", mcp_revision: str = "mcp-a"):
    return SimpleNamespace(
        candidate_summary={
            "skillInventoryRevision": skill_revision,
            "visibleRootSignature": root_signature,
            "visibleRootRevisionKey": root_signature,
            "mcpInventoryRevision": mcp_revision,
            "lexiconSignature": "lex-a",
            "changedRoots": [],
            "mcpChangedServers": {},
        }
    )


def test_extensions_prefilter_prompt_included_for_true_user_message() -> None:
    bundle = _bundle()

    assert _should_include_extensions_prefilter_prompt(
        state={"current_route_context": {"extensionsPrefilterSignature": _extensions_prefilter_signature(bundle)}},
        messages=[HumanMessage(content="请使用女娲生成 skill")],
        user_query="请使用女娲生成 skill",
        route_bundle=bundle,
    )


def test_extensions_prefilter_prompt_suppressed_during_tool_loop_when_inventory_static() -> None:
    bundle = _bundle()
    signature = _extensions_prefilter_signature(bundle)

    assert not _should_include_extensions_prefilter_prompt(
        state={
            "current_route_context": {
                "extensionsPrefilterSignature": signature,
                "extensionsPrefilterQuery": "请使用女娲生成 skill",
            }
        },
        messages=[
            HumanMessage(content="请使用女娲生成 skill"),
            AIMessage(content="", tool_calls=[{"name": "fetch_skill_instructions", "args": {"skill_name": "huashu-nuwa"}, "id": "call_v8_1"}]),
            ToolMessage(content="loaded", tool_call_id="call_v8_1"),
        ],
        user_query="请使用女娲生成 skill",
        route_bundle=bundle,
    )


def test_extensions_prefilter_prompt_reopens_when_inventory_signature_changes() -> None:
    old_bundle = _bundle(skill_revision="skills-old")
    new_bundle = _bundle(skill_revision="skills-new")

    assert _should_include_extensions_prefilter_prompt(
        state={
            "current_route_context": {
                "extensionsPrefilterSignature": _extensions_prefilter_signature(old_bundle),
                "extensionsPrefilterQuery": "继续",
            }
        },
        messages=[HumanMessage(content="继续"), AIMessage(content="ok")],
        user_query="继续",
        route_bundle=new_bundle,
    )
