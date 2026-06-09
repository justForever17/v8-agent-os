from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from api import model_cache_routes
from core import prompt_cache_gateway as gateway_module
from core import model_telemetry as telemetry_module
from core.native_tools import grep_search
from core.observability_db import ObservabilityDatabaseManager
from core.model_telemetry import ModelTelemetryCallback, _find_cached_input_tokens
from core.prompt_cache_gateway import PromptCacheGateway, load_prompt_cache_profiles, prompt_cache_profile_for_provider
from core.prompt_cache_segments import build_prompt_segments_from_parts


class FakePromptCacheDb:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self.segments: list[tuple[str, list[dict]]] = []
        self.cache: dict[str, dict] = {}
        self.hit_count = 0

    def add_prompt_cache_event(self, record: dict) -> None:
        self.events.append(dict(record))

    def add_prompt_cache_segments(self, event_id: str, segments: list[dict]) -> None:
        self.segments.append((event_id, list(segments)))

    def get_llm_response_cache(self, response_cache_key: str):
        return self.cache.get(response_cache_key)

    def upsert_llm_response_cache(self, record: dict) -> None:
        self.cache[str(record["response_cache_key"])] = {
            **record,
            "response": dict(record.get("response") or {}),
            "metadata": dict(record.get("metadata") or {}),
        }

    def increment_llm_response_cache_hit(self, response_cache_key: str) -> None:
        self.hit_count += 1

    def get_prompt_cache_stats(self, limit: int = 50, days: int = 1) -> dict:
        return {"eventsByDecision": [], "eventsBySkipReason": [], "responseCache": {"entries": len(self.cache), "hits": self.hit_count}, "recentEvents": []}

    def purge_prompt_cache(self) -> dict:
        counts = {"prompt_cache_segments": len(self.segments), "prompt_cache_events": len(self.events), "llm_response_cache": len(self.cache)}
        self.events.clear()
        self.segments.clear()
        self.cache.clear()
        return {"deleted": counts}


def _messages(user_text: str = "帮我做一个摘要。"):
    return [
        SystemMessage(
            content=(
                "Stable supervisor prompt.\n"
                "<capability_registry>chat, memory, creative_media</capability_registry>\n"
                "<direct_tool_registry>runtime_broker, read_native_file</direct_tool_registry>\n"
                "<current_time>2026-04-27T10:00:00+08:00</current_time>"
            )
        ),
        HumanMessage(content=user_text),
    ]


def test_prompt_cache_profiles_include_kimi_automatic_and_legacy_disabled():
    profiles = load_prompt_cache_profiles()
    ids = {profile["id"] for profile in profiles["profiles"]}
    assert "moonshot_kimi_auto_context_cache" in ids
    assert "xiaomi_mimo_implicit_prompt_cache" in ids
    assert "deepseek_implicit_disk_cache" in ids
    kimi = prompt_cache_profile_for_provider("moonshot")
    assert kimi["requestStyle"] == "implicit_observe_only"
    assert kimi["legacyExplicitCaching"]["enabledByDefault"] is False
    mimo = prompt_cache_profile_for_provider("xiaomi-mimo")
    assert mimo["requestStyle"] == "implicit_observe_only"
    assert mimo["supportsResponseUsage"] is False
    deepseek = prompt_cache_profile_for_provider("deepseek")
    assert deepseek["requestStyle"] == "observe_only"
    assert deepseek["supportsResponseUsage"] is True
    assert deepseek["usageFields"] == ["prompt_cache_hit_tokens", "prompt_cache_miss_tokens"]


def test_provider_patch_shapes_do_not_mutate_observe_only_profiles():
    gateway = PromptCacheGateway()
    openai = gateway.dry_run(messages=_messages(), provider_id="openai", model_id="gpt-5.5", kwargs={"temperature": 0})
    assert "prompt_cache_key" in openai["cacheDiagnostics"]["providerRequestPatch"]

    xai = gateway.dry_run(messages=_messages(), provider_id="xai", model_id="grok-4", kwargs={"temperature": 0})
    assert "prompt_cache_key" in xai["cacheDiagnostics"]["providerRequestPatch"]
    assert "x-grok-conv-id" in xai["cacheDiagnostics"]["providerRequestPatch"]["extra_headers"]

    anthropic = gateway.dry_run(messages=_messages(), provider_id="anthropic", model_id="claude", kwargs={"temperature": 0})
    assert anthropic["cacheDiagnostics"]["providerRequestPatch"]["cache_control"]["breakpoints"] == 1

    volcengine = gateway.dry_run(messages=_messages(), provider_id="volcengine-ark", model_id="doubao", kwargs={"temperature": 0})
    assert volcengine["cacheDiagnostics"]["providerRequestPatch"]["extra_body"]["caching"]["prefix"] is True

    kimi = gateway.dry_run(messages=_messages(), provider_id="moonshot", model_id="kimi-k2.6", kwargs={"temperature": 0})
    patch = kimi["cacheDiagnostics"]["providerRequestPatch"]
    assert patch["observeOnly"] is True
    assert "prompt_cache_key" not in patch


def _structured_capability_messages(recommendation: str = "推荐路由: chat\n"):
    parts = [
        {"source": "base", "type": "stable_static", "text": "Stable supervisor prompt.\n\n"},
        {"source": "capability_registry.header", "type": "scoped_static", "text": "<capability_registry>\n"},
        {"source": "capability_registry.recommended_routes", "type": "dynamic", "text": recommendation},
        {"source": "capability_registry.descriptors", "type": "scoped_static", "text": "- kind=chat | ChatRuntime\n摘要: static descriptor\n</capability_registry>\n"},
        {"source": "current_time", "type": "dynamic", "text": "Current Time: 2026-04-27T10:00:00+08:00\n"},
    ]
    content = "".join(part["text"] for part in parts)
    return [
        SystemMessage(content=content, additional_kwargs={"v8_prompt_segments": build_prompt_segments_from_parts(parts)}),
        HumanMessage(content="hello"),
    ]


def test_structured_segments_keep_capability_recommendations_dynamic_and_cache_control_bounded():
    gateway = PromptCacheGateway()
    first = gateway.prepare_request(
        messages=_structured_capability_messages("推荐路由: chat\n"),
        kwargs={"temperature": 0},
        provider_id="anthropic",
        model_id="claude-test",
        record=False,
        lookup_response_cache=False,
    )
    second = gateway.prepare_request(
        messages=_structured_capability_messages("推荐路由: creative_media\n"),
        kwargs={"temperature": 0},
        provider_id="anthropic",
        model_id="claude-test",
        record=False,
        lookup_response_cache=False,
    )
    assert first.diagnostics["staticPrefixKey"] == second.diagnostics["staticPrefixKey"]
    assert first.diagnostics["dynamicRequestHash"] != second.diagnostics["dynamicRequestHash"]

    segments = first.diagnostics["segments"]
    recommendation = next(item for item in segments if item["source"] == "capability_registry.recommended_routes")
    descriptor = next(item for item in segments if item["source"] == "capability_registry.descriptors")
    assert recommendation["type"] == "dynamic"
    assert descriptor["type"] == "scoped_static"

    blocks = first.messages[0].content
    assert isinstance(blocks, list)
    dynamic_blocks = [block for block in blocks if "推荐路由" in block.get("text", "")]
    assert dynamic_blocks and all("cache_control" not in block for block in dynamic_blocks)
    cached_blocks = [block for block in blocks if block.get("cache_control")]
    assert cached_blocks
    assert len(cached_blocks) <= 4


def test_dynamic_user_time_and_memory_do_not_change_static_prefix_key():
    gateway = PromptCacheGateway()
    first = gateway.dry_run(messages=_messages("第一个问题"), provider_id="openai", model_id="gpt-5.5", kwargs={"temperature": 0})
    second = gateway.dry_run(messages=_messages("第二个问题"), provider_id="openai", model_id="gpt-5.5", kwargs={"temperature": 0})
    assert first["cacheDiagnostics"]["staticPrefixKey"] == second["cacheDiagnostics"]["staticPrefixKey"]
    assert first["cacheDiagnostics"]["dynamicRequestHash"] != second["cacheDiagnostics"]["dynamicRequestHash"]

    memory_messages = [
        SystemMessage(content="Stable prompt.\n<memory>volatile memory text</memory>"),
        HumanMessage(content="同一个问题"),
    ]
    changed_memory_messages = [
        SystemMessage(content="Stable prompt.\n<memory>changed memory text</memory>"),
        HumanMessage(content="同一个问题"),
    ]
    first_memory = gateway.dry_run(messages=memory_messages, provider_id="openai", model_id="gpt-5.5", kwargs={"temperature": 0})
    second_memory = gateway.dry_run(messages=changed_memory_messages, provider_id="openai", model_id="gpt-5.5", kwargs={"temperature": 0})
    assert first_memory["cacheDiagnostics"]["staticPrefixKey"] == second_memory["cacheDiagnostics"]["staticPrefixKey"]
    assert first_memory["cacheDiagnostics"]["dynamicRequestHash"] != second_memory["cacheDiagnostics"]["dynamicRequestHash"]


def test_deepseek_planner_observe_only_keeps_static_prefix_stable():
    gateway = PromptCacheGateway()
    first = gateway.dry_run(
        messages=_messages("把这个模糊需求整理成 runtime needs。"),
        provider_id="deepseek",
        model_id="deepseek-v4-flash",
        role="planner",
        kwargs={"temperature": 0},
    )
    second = gateway.dry_run(
        messages=_messages("把另一个工程续接需求整理成 runtime needs。"),
        provider_id="deepseek",
        model_id="deepseek-v4-flash",
        role="planner",
        kwargs={"temperature": 0},
    )

    assert first["cacheDiagnostics"]["staticPrefixKey"] == second["cacheDiagnostics"]["staticPrefixKey"]
    assert first["cacheDiagnostics"]["dynamicRequestHash"] != second["cacheDiagnostics"]["dynamicRequestHash"]
    assert first["cacheDiagnostics"]["providerRequestPatch"]["observeOnly"] is True
    assert first["cacheDiagnostics"]["usageFields"] == ["prompt_cache_hit_tokens", "prompt_cache_miss_tokens"]


def test_deepseek_usage_fields_are_parsed_as_cached_tokens():
    usage = {"usage": {"prompt_cache_hit_tokens": 900, "prompt_cache_miss_tokens": 124}}
    assert _find_cached_input_tokens(usage) == 900


def test_tool_schema_workspace_rules_and_runtime_grants_change_static_prefix_key():
    gateway = PromptCacheGateway()
    base = gateway.dry_run(messages=_messages(), provider_id="openai", model_id="gpt-5.5", kwargs={"temperature": 0})
    with_tool = gateway.dry_run(
        messages=_messages(),
        provider_id="openai",
        model_id="gpt-5.5",
        kwargs={"temperature": 0},
        bound_tools=[SimpleNamespace(name="creative_media_compile_recipe", description="Compile recipe.", args_schema={"type": "object"})],
    )
    with_rules = gateway.dry_run(
        messages=_messages(),
        provider_id="openai",
        model_id="gpt-5.5",
        kwargs={"temperature": 0},
        meta={"agents_hash": "changed-agents-hash"},
    )
    with_grant = gateway.dry_run(
        messages=_messages(),
        provider_id="openai",
        model_id="gpt-5.5",
        kwargs={"temperature": 0},
        meta={"runtimeToolGrants": ["creative_media.core"]},
    )
    assert base["cacheDiagnostics"]["staticPrefixKey"] != with_tool["cacheDiagnostics"]["staticPrefixKey"]
    assert base["cacheDiagnostics"]["staticPrefixKey"] != with_rules["cacheDiagnostics"]["staticPrefixKey"]
    assert base["cacheDiagnostics"]["staticPrefixKey"] != with_grant["cacheDiagnostics"]["staticPrefixKey"]


def test_response_cache_skip_reasons_and_exact_hit(monkeypatch):
    fake_db = FakePromptCacheDb()
    monkeypatch.setattr(gateway_module, "db", fake_db)
    gateway = PromptCacheGateway()

    assert gateway.dry_run(messages=_messages(), provider_id="openai", model_id="gpt-5.5", kwargs={"temperature": 0.7}, streaming=True)["cacheDiagnostics"]["skipReason"] == "streaming_request"
    assert gateway.dry_run(messages=_messages(), provider_id="openai", model_id="gpt-5.5", kwargs={"temperature": 0.7})["cacheDiagnostics"]["skipReason"] == "temperature_not_deterministic"
    assert gateway.dry_run(
        messages=_messages(),
        provider_id="openai",
        model_id="gpt-5.5",
        kwargs={"temperature": 0},
        bound_tools=[SimpleNamespace(name="runtime_broker", description="Grant runtime groups.", args_schema={})],
    )["cacheDiagnostics"]["skipReason"] == "tool_bound_request"
    multimodal = [SystemMessage(content="Stable prompt."), HumanMessage(content=[{"type": "image_url", "image_url": {"url": "https://example.test/a.png"}}])]
    assert gateway.dry_run(messages=multimodal, provider_id="openai", model_id="gpt-5.5", kwargs={"temperature": 0})["cacheDiagnostics"]["skipReason"] == "contains_file_or_image_content"
    unsafe = [SystemMessage(content="Stable prompt."), HumanMessage(content="my API key is sk-test")]
    assert gateway.dry_run(messages=unsafe, provider_id="openai", model_id="gpt-5.5", kwargs={"temperature": 0})["cacheDiagnostics"]["skipReason"] == "unsafe_or_secret_content"
    tool_result = [SystemMessage(content="Stable prompt."), ToolMessage(content="tool result", tool_call_id="tool-1")]
    assert gateway.prepare_request(messages=tool_result, kwargs={"temperature": 0}, provider_id="openai", model_id="gpt-5.5").diagnostics["skipReason"] == "contains_tool_result"

    prepared = gateway.prepare_request(messages=_messages(), kwargs={"temperature": 0}, provider_id="openai", model_id="gpt-5.5", role="internal")
    assert prepared.diagnostics["responseCacheDecision"] == "miss"
    gateway.store_response(AIMessage(content="cached answer"), prepared.diagnostics)

    second = gateway.prepare_request(messages=_messages(), kwargs={"temperature": 0}, provider_id="openai", model_id="gpt-5.5", role="internal")
    assert second.diagnostics["responseCacheDecision"] == "hit"
    assert second.cache_hit_message is not None
    assert second.cache_hit_message.content == "cached answer"
    assert fake_db.hit_count == 1


def test_model_cache_route_helpers(monkeypatch):
    fake_db = FakePromptCacheDb()
    monkeypatch.setattr(model_cache_routes, "db", fake_db)

    profiles = asyncio.run(model_cache_routes.get_model_cache_profiles())
    assert "profiles" in profiles

    dry_run = asyncio.run(
        model_cache_routes.dry_run_model_cache(
            {
                "providerId": "openai",
                "modelId": "gpt-5.5",
                "messages": [
                    {"role": "system", "content": "Stable prompt.\n<current_time>dynamic</current_time>"},
                    {"role": "user", "content": "hello"},
                ],
                "kwargs": {"temperature": 0},
            }
        )
    )
    assert dry_run["cacheDiagnostics"]["providerRequestPatch"]["prompt_cache_key"]

    stats = asyncio.run(model_cache_routes.get_model_cache_stats(limit=50, days=1))
    assert stats["responseCache"]["entries"] == 0

    purged = asyncio.run(model_cache_routes.purge_model_cache())
    assert "deleted" in purged


def test_prompt_cache_stats_window_rates(tmp_path):
    manager = ObservabilityDatabaseManager(tmp_path / "observability.db")
    for index, (decision, prefix) in enumerate((
        ("miss", "prefix-a"),
        ("hit", "prefix-a"),
        ("skipped", "prefix-b"),
    )):
        event_id = f"evt-{index}"
        manager.add_prompt_cache_event(
            {
                "id": event_id,
                "provider_id": "openai",
                "model_id": "gpt-test",
                "profile_id": "openai_implicit_prompt_cache",
                "static_prefix_key": prefix,
                "response_cache_key": f"response-{index}",
                "decision": decision,
                "skip_reason": "streaming_request" if decision == "skipped" else "",
                "provider_patch": {"prompt_cache_key": prefix} if decision != "skipped" else {},
            }
        )
        manager.add_prompt_cache_segments(
            event_id,
            [
                {"type": "stable_static", "source": "base", "hash": f"base-{index}", "estimatedTokens": 100},
                {"type": "dynamic", "source": "time", "hash": f"dyn-{index}", "estimatedTokens": 10},
            ],
        )

    stats = manager.get_prompt_cache_stats(days=1)
    assert stats["totals"]["events"] == 3
    assert stats["totals"]["providerPatchEvents"] == 2
    assert stats["totals"]["reusedPrefixKeys"] == 1
    assert stats["rates"]["providerPatchRate"] == 0.6667
    assert stats["rates"]["v8ExactResponseHitRate"] == 0.5
    assert stats["segmentTokenEstimate"]["stable_static"]["estimatedTokens"] == 300


def test_grep_search_description_is_content_search_not_path_finder():
    description = str(grep_search.description)
    assert "Search file contents" in description
    assert "Not for finding file names or paths" in description


def test_streaming_prompt_cache_diagnostics_are_accumulated(monkeypatch):
    class FakeTelemetryDb:
        def __init__(self) -> None:
            self.invocations: list[dict] = []

        def add_model_invocation_log(self, record: dict) -> None:
            self.invocations.append(dict(record))

        def upsert_usage_ledger(self, record: dict) -> None:
            pass

        def add_provider_health_log(self, record: dict) -> None:
            pass

    fake_db = FakeTelemetryDb()
    monkeypatch.setattr(telemetry_module, "db", fake_db)
    callback = ModelTelemetryCallback(
        model_id="fake-stream",
        provider_id="openai",
        provider_name="OpenAI",
        role="prompt_cache_streaming_live",
        is_streaming=True,
    )
    run_id = uuid.uuid4()
    callback.on_chat_model_start({}, [[SystemMessage(content="stable")]], run_id=run_id)
    callback.on_llm_new_token(
        "o",
        run_id=run_id,
        chunk=AIMessageChunk(
            content="o",
            response_metadata={
                "v8_prompt_cache": {
                    "eventId": "evt-stream",
                    "profileId": "openai_prompt_cache_key",
                    "skipReason": "streaming_request",
                }
            },
        ),
    )
    callback.on_llm_end(
        LLMResult(generations=[[ChatGeneration(message=AIMessage(content="ok"))]], llm_output={}),
        run_id=run_id,
    )
    assert fake_db.invocations
    metadata = fake_db.invocations[0]["metadata"]
    assert metadata["promptCache"]["eventId"] == "evt-stream"
    assert metadata["promptCache"]["skipReason"] == "streaming_request"
