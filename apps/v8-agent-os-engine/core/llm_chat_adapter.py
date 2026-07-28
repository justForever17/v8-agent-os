from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any, AsyncIterator, Callable, Iterator, Mapping, Sequence

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.runnables.base import RunnableSerializable
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import BaseModel as PydanticModel, ConfigDict, PrivateAttr

from core.model_capability_matrix import build_effective_capability_matrix
from core.llm_exceptions import V8LLMStructuredOutputError, raise_as_v8_llm_error
from core.prompt_cache_gateway import PreparedPromptCacheRequest, prompt_cache_gateway
from core.provider_hosted_tools import provider_hosted_tool_schemas
from core.provider_compatibility import normalize_provider_error
from core.response_normalizer import extract_text_and_reasoning, normalize_tool_calls, sanitize_model_tool_calls


_V8_CHUNK_IDENTITY_METADATA_KEYS = (
    "v8_provider_adapter",
    "v8_model_id",
    "v8_model_ref",
    "v8_provider_adapter_label",
    "v8_tool_calling_mode",
    "v8_structured_output_mode",
    "v8_stream_mode",
    "v8_effective_capability_matrix",
    "v8_provider_hosted_tools",
)


def _stringify_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
                continue
            if isinstance(item, Mapping):
                text_value = item.get("text") or item.get("content") or item.get("value") or ""
                if text_value:
                    parts.append(str(text_value))
                continue
            text_value = getattr(item, "text", "") or getattr(item, "content", "") or getattr(item, "value", "")
            if text_value:
                parts.append(str(text_value))
        return "\n".join(part for part in parts if part)
    if value is None:
        return ""
    return str(value)


def _message_text(message: Any) -> str:
    text, _reasoning = extract_text_and_reasoning(message)
    if text:
        return text
    return _stringify_content(getattr(message, "content", ""))


def _extract_json_payload(text: str) -> Any:
    payload = (text or "").strip()
    if not payload:
        raise ValueError("empty structured output")
    if payload.startswith("```"):
        lines = payload.splitlines()
        if len(lines) >= 3 and lines[0].startswith("```") and lines[-1].startswith("```"):
            payload = "\n".join(lines[1:-1]).strip()
    decoder = json.JSONDecoder()
    try:
        obj, _index = decoder.raw_decode(payload)
        return obj
    except Exception:
        pass
    start_candidates = [payload.find("{"), payload.find("[")]
    start_candidates = [value for value in start_candidates if value >= 0]
    for start in sorted(start_candidates):
        try:
            obj, _index = decoder.raw_decode(payload[start:])
            return obj
        except Exception:
            continue
    raise ValueError("structured output is not valid JSON")


def _schema_instruction(schema: Any) -> str:
    if isinstance(schema, dict):
        return json.dumps(schema, ensure_ascii=False, indent=2)
    if isinstance(schema, type) and issubclass(schema, PydanticModel):
        return json.dumps(schema.model_json_schema(), ensure_ascii=False, indent=2)
    tool_schema = convert_to_openai_tool(schema)
    return json.dumps(tool_schema.get("function", {}).get("parameters") or tool_schema, ensure_ascii=False, indent=2)


def _coerce_structured_output(schema: Any, payload: Any) -> Any:
    if isinstance(schema, type) and issubclass(schema, PydanticModel):
        return schema.model_validate(payload)
    return payload


class BaseProviderSurface:
    provider_standard: str = "openai"

    def __init__(self, meta: Mapping[str, Any] | None = None) -> None:
        self.meta = dict(meta or {})

    def normalize_messages(self, messages: Sequence[BaseMessage]) -> list[BaseMessage]:
        normalized: list[BaseMessage] = []
        for message in messages:
            if isinstance(message, AIMessage) and getattr(message, "tool_calls", None) and not self.supports_native_tools():
                visible_text, _reasoning = extract_text_and_reasoning(message)
                rendered_calls = [
                    f"[Previously Executed Tool Request: {str(call.get('name') or 'tool')}]\n"
                    f"{json.dumps(call.get('args') or {}, ensure_ascii=False, default=str)}"
                    for call in list(message.tool_calls or [])
                    if isinstance(call, Mapping)
                ]
                additional_kwargs = dict(getattr(message, "additional_kwargs", {}) or {})
                additional_kwargs.pop("tool_calls", None)
                additional_kwargs.pop("function_call", None)
                normalized.append(
                    AIMessage(
                        content="\n\n".join(
                            part for part in [visible_text.strip(), *rendered_calls] if part
                        ),
                        additional_kwargs=additional_kwargs,
                        response_metadata=dict(getattr(message, "response_metadata", {}) or {}),
                        usage_metadata=getattr(message, "usage_metadata", None),
                    )
                )
                continue
            if isinstance(message, ToolMessage) and not self.supports_native_tools():
                rendered = _stringify_content(getattr(message, "content", ""))
                tool_name = str(getattr(message, "name", "") or getattr(message, "tool_call_id", "") or "tool")
                normalized.append(HumanMessage(content=f"[Tool Result: {tool_name}]\n{rendered}"))
                continue
            content = getattr(message, "content", None)
            if not _stringify_content(content).strip() and not getattr(message, "tool_calls", None):
                continue
            normalized.append(message)
        return normalized

    def supports_native_tools(self) -> bool:
        return bool(self.effective_capability_matrix().get("supports_native_tools"))

    def supports_native_structured_output(self) -> bool:
        return bool(self.effective_capability_matrix().get("supports_native_structured_output"))

    def tool_calling_mode(self) -> str:
        return "native" if self.supports_native_tools() else "prompt_emulated"

    def structured_output_mode(self) -> str:
        return "native" if self.supports_native_structured_output() else "prompt_fallback"

    def effective_capability_matrix(self) -> dict[str, Any]:
        matrix = dict(self.meta.get("effective_capability_matrix") or self.meta.get("effectiveCapabilityMatrix") or {})
        if matrix:
            return matrix
        return build_effective_capability_matrix(
            capability_class=str(self.meta.get("capability_class") or self.meta.get("capabilityClass") or ""),
            capabilities=self.meta.get("capabilities") or self.meta,
            api_standard=self.provider_standard,
            runtime_ready=bool(self.meta.get("runtime_ready", self.meta.get("runtimeReady", True))),
        )


class OpenAICompatibleSurface(BaseProviderSurface):
    provider_standard = "openai"


class AnthropicSurface(BaseProviderSurface):
    provider_standard = "anthropic"

    def normalize_messages(self, messages: Sequence[BaseMessage]) -> list[BaseMessage]:
        normalized = super().normalize_messages(messages)
        system_messages = [message for message in normalized if isinstance(message, SystemMessage)]
        if not system_messages:
            return normalized

        non_system_messages = [message for message in normalized if not isinstance(message, SystemMessage)]
        if len(system_messages) == 1:
            return [system_messages[0], *non_system_messages]

        content_blocks: list[Any] = []
        for message in system_messages:
            content = message.content
            if isinstance(content, list):
                content_blocks.extend(deepcopy(content))
            elif _stringify_content(content).strip():
                content_blocks.append({"type": "text", "text": _stringify_content(content)})
        merged_system = SystemMessage(
            content=content_blocks,
            additional_kwargs={"v8_system_message_count": len(system_messages)},
        )
        return [merged_system, *non_system_messages]


class GeminiSurface(BaseProviderSurface):
    provider_standard = "gemini"


def create_provider_surface(provider_standard: str, meta: Mapping[str, Any] | None = None) -> BaseProviderSurface:
    normalized = str(provider_standard or "openai").strip().lower()
    if normalized == "anthropic":
        return AnthropicSurface(meta)
    if normalized in {"google", "gemini"}:
        return GeminiSurface(meta)
    return OpenAICompatibleSurface(meta)


class V8StructuredOutputRunnable(RunnableSerializable):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    model_id: str
    provider_standard: str

    _adapter: "V8ChatModelAdapter" = PrivateAttr()
    _schema: Any = PrivateAttr()
    _include_raw: bool = PrivateAttr(default=False)
    _kwargs: dict[str, Any] = PrivateAttr(default_factory=dict)

    def __init__(
        self,
        *,
        adapter: "V8ChatModelAdapter",
        schema: Any,
        include_raw: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(model_id=adapter.model_id, provider_standard=adapter.provider_standard)
        self._adapter = adapter
        self._schema = schema
        self._include_raw = include_raw
        self._kwargs = dict(kwargs)

    def invoke(self, input: Any, config: Any | None = None, **kwargs: Any) -> Any:
        try:
            native = self._adapter._build_native_structured_runnable(self._schema, include_raw=self._include_raw, **self._kwargs)
            if native is not None:
                normalized_input = self._adapter.normalize_input_for_provider(input)
                try:
                    return native.invoke(normalized_input, config=config, **kwargs)
                except Exception as exc:
                    if not self._adapter._should_fallback_structured_output(exc):
                        raise
                    return self._adapter._invoke_structured_fallback(
                        normalized_input,
                        schema=self._schema,
                        include_raw=self._include_raw,
                        config=config,
                        **kwargs,
                    )

            result = self._adapter._invoke_structured_fallback(input, schema=self._schema, include_raw=self._include_raw, config=config, **kwargs)
            return result
        except Exception as exc:
            raise_as_v8_llm_error(exc, provider=self.provider_standard, model=self.model_id, details={"mode": "structured_output"})  # type: ignore[arg-type]

    async def ainvoke(self, input: Any, config: Any | None = None, **kwargs: Any) -> Any:
        try:
            native = self._adapter._build_native_structured_runnable(self._schema, include_raw=self._include_raw, **self._kwargs)
            if native is not None:
                normalized_input = self._adapter.normalize_input_for_provider(input)
                try:
                    return await native.ainvoke(normalized_input, config=config, **kwargs)
                except Exception as exc:
                    if not self._adapter._should_fallback_structured_output(exc):
                        raise
                    return await self._adapter._ainvoke_structured_fallback(
                        normalized_input,
                        schema=self._schema,
                        include_raw=self._include_raw,
                        config=config,
                        **kwargs,
                    )

            return await self._adapter._ainvoke_structured_fallback(input, schema=self._schema, include_raw=self._include_raw, config=config, **kwargs)
        except Exception as exc:
            raise_as_v8_llm_error(exc, provider=self.provider_standard, model=self.model_id, details={"mode": "structured_output"})  # type: ignore[arg-type]


class V8ChatModelAdapter(BaseChatModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    model_id: str
    provider_standard: str = "openai"
    role: str = ""

    _meta: dict[str, Any] = PrivateAttr(default_factory=dict)
    _model_kwargs: dict[str, Any] = PrivateAttr(default_factory=dict)
    _builder: Callable[[], Any] = PrivateAttr()
    _provider_surface: BaseProviderSurface = PrivateAttr()
    _bound_tools: Sequence[Any] | None = PrivateAttr(default=None)
    _bound_tool_kwargs: dict[str, Any] = PrivateAttr(default_factory=dict)
    _base_model: Any = PrivateAttr(default=None)
    _bound_model: Any = PrivateAttr(default=None)

    def __init__(
        self,
        *,
        model_id: str,
        provider_standard: str,
        role: str,
        meta: Mapping[str, Any],
        model_kwargs: Mapping[str, Any],
        builder: Callable[[], Any],
    ) -> None:
        adapter_callbacks = list((model_kwargs or {}).get("callbacks") or [])
        if isinstance(model_kwargs, dict):
            model_kwargs.pop("callbacks", None)
        super().__init__(
            model_id=model_id,
            provider_standard=provider_standard,
            role=role,
            callbacks=adapter_callbacks or None,
        )
        self._meta = dict(meta or {})
        self._model_kwargs = dict(model_kwargs or {})
        self._builder = builder
        self._provider_surface = create_provider_surface(provider_standard, meta)

    @property
    def _llm_type(self) -> str:
        return f"v8_{self.provider_standard}_adapter"

    @property
    def lc_attributes(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "provider_standard": self.provider_standard,
            "role": self.role,
        }

    def provider_adapter(self) -> str:
        if self.provider_standard in {"google", "gemini"}:
            return "gemini"
        if self.provider_standard == "anthropic":
            return "anthropic"
        adapter = str(self._meta.get("provider_adapter") or self._meta.get("providerAdapter") or "").strip()
        if adapter:
            return adapter
        return "openai-compatible"

    def effective_capability_matrix(self) -> dict[str, Any]:
        return self._provider_surface.effective_capability_matrix()

    def adapter_modes(self) -> dict[str, Any]:
        matrix = self.effective_capability_matrix()
        return {
            "providerAdapter": self.provider_adapter(),
            "effectiveCapabilityMatrix": matrix,
            "toolCallingMode": self._provider_surface.tool_calling_mode(),
            "structuredOutputMode": self._provider_surface.structured_output_mode(),
            "streamMode": "native" if bool(matrix.get("supports_streaming")) else "unsupported",
        }

    def normalize_input_for_provider(self, input_value: Any) -> Any:
        if isinstance(input_value, list) and all(isinstance(item, BaseMessage) for item in input_value):
            return self._normalize_messages_for_provider(input_value)
        return input_value

    def _normalize_messages_for_provider(self, messages: Sequence[BaseMessage]) -> list[BaseMessage]:
        normalized = self._provider_surface.normalize_messages(messages)
        wire_protocol = str(self._meta.get("wire_protocol") or self._meta.get("wireProtocol") or "").strip()
        is_anthropic_messages = wire_protocol == "anthropic.messages" or (
            not wire_protocol and self.provider_standard == "anthropic"
        )
        if wire_protocol != "openai.responses" and not is_anthropic_messages:
            return normalized

        if wire_protocol == "openai.responses" or self._provider_surface.supports_native_tools():
            normalized = self._project_provider_tool_call_ids(normalized)
        if is_anthropic_messages and self._provider_surface.supports_native_tools():
            self._assert_anthropic_tool_result_contract(normalized)
        return normalized

    @staticmethod
    def _project_content_tool_call_ids(
        content: Any,
        provider_id_by_canonical: Mapping[str, str],
    ) -> Any:
        if not isinstance(content, list):
            return content
        projected: list[Any] = []
        for raw_block in content:
            if not isinstance(raw_block, Mapping):
                projected.append(raw_block)
                continue
            block = deepcopy(dict(raw_block))
            block_type = str(block.get("type") or "").strip()
            if block_type == "tool_use":
                canonical_id = str(block.get("id") or "").strip()
                provider_id = provider_id_by_canonical.get(canonical_id)
                if provider_id:
                    block["id"] = provider_id
            elif block_type == "tool_result":
                canonical_id = str(block.get("tool_use_id") or "").strip()
                provider_id = provider_id_by_canonical.get(canonical_id)
                if provider_id:
                    block["tool_use_id"] = provider_id
            projected.append(block)
        return projected

    @classmethod
    def _project_provider_tool_call_ids(cls, messages: Sequence[BaseMessage]) -> list[BaseMessage]:
        # V8 owns stable canonical tool-call ids inside LangGraph/checkpoints,
        # while Responses and Anthropic Messages must continue with the exact
        # provider-issued id. Re-project the shadow id only at the provider
        # boundary so canonical state remains provider-neutral and resumable.
        provider_id_by_canonical: dict[str, str] = {}
        projected: list[BaseMessage] = []
        for message in messages:
            if isinstance(message, AIMessage) and list(getattr(message, "tool_calls", None) or []):
                clean_message = deepcopy(message)
                clean_tool_calls: list[dict[str, Any]] = []
                for raw_call in list(clean_message.tool_calls or []):
                    call = dict(raw_call or {})
                    canonical_id = str(call.get("id") or "").strip()
                    provider_id = str(
                        call.get("providerToolCallId")
                        or call.get("provider_tool_call_id")
                        or ""
                    ).strip()
                    if canonical_id and provider_id:
                        provider_id_by_canonical[canonical_id] = provider_id
                        call["id"] = provider_id
                    clean_tool_calls.append(call)
                clean_message.tool_calls = clean_tool_calls

                additional_kwargs = dict(clean_message.additional_kwargs or {})
                additional_calls = additional_kwargs.get("tool_calls")
                if isinstance(additional_calls, list):
                    wire_calls: list[Any] = []
                    for raw_call in additional_calls:
                        if not isinstance(raw_call, Mapping):
                            wire_calls.append(raw_call)
                            continue
                        call = dict(raw_call)
                        canonical_id = str(call.get("id") or call.get("tool_call_id") or "").strip()
                        provider_id = str(
                            call.get("providerToolCallId")
                            or call.get("provider_tool_call_id")
                            or provider_id_by_canonical.get(canonical_id)
                            or ""
                        ).strip()
                        if provider_id:
                            if "tool_call_id" in call and "id" not in call:
                                call["tool_call_id"] = provider_id
                            else:
                                call["id"] = provider_id
                        wire_calls.append(call)
                    additional_kwargs["tool_calls"] = wire_calls
                    clean_message.additional_kwargs = additional_kwargs
                clean_message.content = cls._project_content_tool_call_ids(
                    clean_message.content,
                    provider_id_by_canonical,
                )
                projected.append(clean_message)
                continue

            if isinstance(message, ToolMessage):
                canonical_id = str(message.tool_call_id or "").strip()
                provider_id = provider_id_by_canonical.get(canonical_id)
                if provider_id:
                    clean_message = deepcopy(message)
                    clean_message.tool_call_id = provider_id
                    projected.append(clean_message)
                    continue
            projected_content = cls._project_content_tool_call_ids(
                getattr(message, "content", None),
                provider_id_by_canonical,
            )
            if projected_content is not getattr(message, "content", None):
                clean_message = deepcopy(message)
                clean_message.content = projected_content
                projected.append(clean_message)
            else:
                projected.append(message)
        return projected

    @staticmethod
    def _content_tool_result_ids(message: BaseMessage) -> set[str]:
        content = getattr(message, "content", None)
        if not isinstance(content, list):
            return set()
        return {
            str(block.get("tool_use_id") or "").strip()
            for block in content
            if isinstance(block, Mapping)
            and str(block.get("type") or "").strip() == "tool_result"
            and str(block.get("tool_use_id") or "").strip()
        }

    @classmethod
    def _assert_anthropic_tool_result_contract(cls, messages: Sequence[BaseMessage]) -> None:
        for index, message in enumerate(messages):
            if not isinstance(message, AIMessage):
                continue
            expected_ids = {
                str(tool_call.get("id") or "").strip()
                for tool_call in list(getattr(message, "tool_calls", None) or [])
                if str(tool_call.get("id") or "").strip()
            }
            if not expected_ids:
                continue

            result_ids: set[str] = set()
            cursor = index + 1
            while cursor < len(messages):
                candidate = messages[cursor]
                if isinstance(candidate, ToolMessage):
                    tool_call_id = str(candidate.tool_call_id or "").strip()
                    if tool_call_id:
                        result_ids.add(tool_call_id)
                    cursor += 1
                    continue
                content_result_ids = cls._content_tool_result_ids(candidate)
                if content_result_ids:
                    result_ids.update(content_result_ids)
                    cursor += 1
                    continue
                break

            missing_ids = sorted(expected_ids - result_ids)
            if missing_ids:
                raise ValueError(
                    "Anthropic message contract rejected an incomplete tool turn: "
                    f"missing immediate tool_result for {', '.join(missing_ids)}."
                )

    def _get_base_model(self) -> Any:
        if self._base_model is None:
            self._base_model = self._builder()
        return self._base_model

    def _provider_hosted_tools(self) -> list[dict[str, Any]]:
        return provider_hosted_tool_schemas(
            wire_protocol=self._meta.get("wire_protocol") or self._meta.get("wireProtocol"),
            config=self._meta.get("provider_hosted_tools") or self._meta.get("providerHostedTools"),
        )

    def _runtime_bound_tools(self) -> list[Any]:
        return [*(self._bound_tools or []), *self._provider_hosted_tools()]

    def _get_runtime_model(self) -> Any:
        model = self._get_base_model()
        runtime_tools = self._runtime_bound_tools()
        if not runtime_tools:
            return model
        if self._bound_model is not None:
            return self._bound_model
        if self._provider_surface.supports_native_tools() and hasattr(model, "bind_tools"):
            self._bound_model = model.bind_tools(runtime_tools, **self._bound_tool_kwargs)
            return self._bound_model
        return model

    def _build_native_structured_runnable(self, schema: Any, *, include_raw: bool = False, **kwargs: Any) -> Any | None:
        model = self._get_runtime_model()
        if self._provider_surface.supports_native_structured_output() and hasattr(model, "with_structured_output"):
            return model.with_structured_output(schema, include_raw=include_raw, **kwargs)
        return None

    def _decorate_message(
        self,
        message: Any,
        *,
        tool_mode: str | None = None,
        structured_mode: str | None = None,
        include_identity_metadata: bool = True,
    ) -> Any:
        normalized = sanitize_model_tool_calls(message, provider_standard=self.provider_standard)
        normalized = self._enforce_bound_tool_surface(normalized)
        response_metadata = dict(getattr(normalized, "response_metadata", {}) or {})
        if include_identity_metadata:
            response_metadata["v8_provider_adapter"] = self.provider_adapter()
            response_metadata["v8_model_id"] = self.model_id
            response_metadata["v8_model_ref"] = str(self._meta.get("model_ref") or self.model_id)
            response_metadata["v8_provider_adapter_label"] = (
                "Gemini GenerateContent"
                if self.provider_standard in {"google", "gemini"}
                else str(self._meta.get("provider_adapter_label") or self.provider_adapter())
            )
            response_metadata.setdefault("v8_tool_calling_mode", tool_mode or self._provider_surface.tool_calling_mode())
            response_metadata.setdefault("v8_structured_output_mode", structured_mode or self._provider_surface.structured_output_mode())
            response_metadata.setdefault("v8_stream_mode", self.adapter_modes().get("streamMode"))
            response_metadata.setdefault("v8_effective_capability_matrix", self.effective_capability_matrix())
            response_metadata.setdefault(
                "v8_provider_hosted_tools",
                [str(item.get("type") or "") for item in self._provider_hosted_tools()],
            )
        else:
            for key in _V8_CHUNK_IDENTITY_METADATA_KEYS:
                response_metadata.pop(key, None)
        if hasattr(normalized, "response_metadata"):
            normalized.response_metadata = response_metadata
        return normalized

    def _bound_tool_names(self) -> set[str]:
        names: set[str] = set()
        for tool in list(self._bound_tools or []):
            direct_name = ""
            if isinstance(tool, Mapping):
                function = tool.get("function") if isinstance(tool.get("function"), Mapping) else {}
                direct_name = str(tool.get("name") or function.get("name") or "").strip()
            else:
                direct_name = str(getattr(tool, "name", "") or "").strip()
            if direct_name:
                names.add(direct_name)
                continue
            try:
                schema = convert_to_openai_tool(tool)
                function = schema.get("function") if isinstance(schema.get("function"), Mapping) else {}
                schema_name = str(schema.get("name") or function.get("name") or "").strip()
                if schema_name:
                    names.add(schema_name)
            except Exception:
                continue
        return names

    def _enforce_bound_tool_surface(self, message: Any) -> Any:
        """Drop provider-emitted calls that were never exposed for this invocation."""

        if not self._bound_tools:
            return message
        allowed = self._bound_tool_names()
        calls = list(getattr(message, "tool_calls", None) or [])
        rejected = [
            str(call.get("name") or "").strip()
            for call in calls
            if isinstance(call, Mapping) and str(call.get("name") or "").strip() not in allowed
        ]
        if not rejected:
            return message
        filtered = [
            call
            for call in calls
            if isinstance(call, Mapping) and str(call.get("name") or "").strip() in allowed
        ]
        if hasattr(message, "tool_calls"):
            message.tool_calls = filtered
        additional_kwargs = dict(getattr(message, "additional_kwargs", {}) or {})
        raw_calls = list(additional_kwargs.get("tool_calls") or [])
        if raw_calls:
            def _raw_name(call: Any) -> str:
                if not isinstance(call, Mapping):
                    return ""
                function = call.get("function") if isinstance(call.get("function"), Mapping) else {}
                return str(call.get("name") or function.get("name") or "").strip()

            additional_kwargs["tool_calls"] = [call for call in raw_calls if _raw_name(call) in allowed]
        function_call = additional_kwargs.get("function_call")
        if isinstance(function_call, Mapping) and str(function_call.get("name") or "").strip() not in allowed:
            additional_kwargs.pop("function_call", None)
        if hasattr(message, "additional_kwargs"):
            message.additional_kwargs = additional_kwargs
        response_metadata = dict(getattr(message, "response_metadata", {}) or {})
        response_metadata["v8_rejected_unbound_tool_calls"] = list(dict.fromkeys(rejected))
        if hasattr(message, "response_metadata"):
            message.response_metadata = response_metadata
        return message

    def _apply_prompt_emulated_tool_calls(self, message: Any, *, force: bool = False) -> Any:
        if not self._bound_tools or (self._provider_surface.supports_native_tools() and not force):
            return message
        content_text = _message_text(message)
        legacy_match = re.fullmatch(
            r"\s*\[Tool Call:\s*([A-Za-z0-9_.:-]+)\s*\]\s*(\{.*\})\s*",
            content_text,
            flags=re.DOTALL,
        )
        legacy_tool_name = ""
        legacy_arguments: Any = None
        if legacy_match:
            legacy_tool_name = str(legacy_match.group(1) or "").strip()
            try:
                legacy_arguments = json.loads(legacy_match.group(2))
            except Exception:
                legacy_arguments = None
        try:
            payload = (
                {"tool_name": legacy_tool_name, "arguments": legacy_arguments}
                if legacy_tool_name and isinstance(legacy_arguments, Mapping)
                else _extract_json_payload(content_text)
            )
        except Exception:
            return self._decorate_message(message, tool_mode="prompt_emulated")
        if not isinstance(payload, Mapping):
            return self._decorate_message(message, tool_mode="prompt_emulated")
        tool_name = str(payload.get("tool_name") or payload.get("name") or "").strip()
        if not tool_name:
            return self._decorate_message(message, tool_mode="prompt_emulated")
        bound_tool_names = self._bound_tool_names()
        if tool_name not in bound_tool_names:
            return self._decorate_message(message, tool_mode="prompt_emulated")
        arguments = payload.get("arguments") or payload.get("args") or {}
        normalized_calls = normalize_tool_calls(
            [{"name": tool_name, "args": arguments}],
            provider_standard=self.provider_standard,
        )
        langchain_tool_calls = [
            {
                "name": str(call.get("name") or tool_name),
                "args": call.get("args") or {},
                "id": str(call.get("id") or call.get("providerToolCallId") or ""),
            }
            for call in normalized_calls
        ]
        ai_message = AIMessage(
            content="",
            tool_calls=langchain_tool_calls,
            additional_kwargs={"tool_emulated": True},
        )
        return self._decorate_message(ai_message, tool_mode="prompt_emulated")

    def _required_bound_tool_name(self) -> str:
        choice = self._bound_tool_kwargs.get("tool_choice")
        if isinstance(choice, Mapping):
            function = choice.get("function") if isinstance(choice.get("function"), Mapping) else {}
            return str(choice.get("name") or function.get("name") or "").strip()
        normalized = str(choice or "").strip()
        if normalized.lower() in {"", "auto", "any", "required", "none", "true", "false"}:
            return ""
        return normalized

    def _requires_bound_tool_call(self) -> bool:
        if not self._bound_tools:
            return False
        choice = self._bound_tool_kwargs.get("tool_choice")
        if choice is True:
            return True
        if isinstance(choice, Mapping):
            choice_type = str(choice.get("type") or "").strip().lower()
            return choice_type != "none"
        return str(choice or "").strip().lower() not in {"", "auto", "none", "false"}

    def _missing_required_tool_call(self, message: Any) -> bool:
        if not self._requires_bound_tool_call():
            return False
        if list(getattr(message, "tool_calls", None) or []):
            return False
        additional_kwargs = dict(getattr(message, "additional_kwargs", None) or {})
        return not list(additional_kwargs.get("tool_calls") or [])

    def _can_prompt_retry_required_tool(self, message: Any) -> bool:
        return bool(
            self._missing_required_tool_call(message)
            and self.effective_capability_matrix().get("supports_prompt_emulated_tools")
        )

    def _coerce_ai_message(self, response: Any, *, force_prompt_emulated_tools: bool = False) -> AIMessage:
        if isinstance(response, AIMessage):
            return self._apply_prompt_emulated_tool_calls(
                self._decorate_message(
                    response,
                    tool_mode="prompt_emulated" if force_prompt_emulated_tools else None,
                ),
                force=force_prompt_emulated_tools,
            )
        if hasattr(response, "content"):
            content = getattr(response, "content", "")
            additional_kwargs = dict(getattr(response, "additional_kwargs", {}) or {})
            tool_calls = normalize_tool_calls(
                getattr(response, "tool_calls", None),
                provider_standard=self.provider_standard,
            )
            coerced = AIMessage(
                content=content,
                additional_kwargs=additional_kwargs,
                response_metadata=dict(getattr(response, "response_metadata", {}) or {}),
                tool_calls=tool_calls,
                usage_metadata=getattr(response, "usage_metadata", None),
            )
            return self._apply_prompt_emulated_tool_calls(
                self._decorate_message(
                    coerced,
                    tool_mode="prompt_emulated" if force_prompt_emulated_tools else None,
                ),
                force=force_prompt_emulated_tools,
            )
        coerced = AIMessage(content=_stringify_content(response))
        return self._apply_prompt_emulated_tool_calls(
            self._decorate_message(
                coerced,
                tool_mode="prompt_emulated" if force_prompt_emulated_tools else None,
            ),
            force=force_prompt_emulated_tools,
        )

    def _coerce_chunk(self, chunk: Any, *, include_identity_metadata: bool = True) -> AIMessageChunk:
        if isinstance(chunk, AIMessageChunk):
            normalized = self._decorate_message(
                chunk,
                include_identity_metadata=include_identity_metadata,
            )
            return normalized if isinstance(normalized, AIMessageChunk) else AIMessageChunk(content=_stringify_content(getattr(normalized, "content", "")))
        if isinstance(chunk, AIMessage):
            normalized = self._decorate_message(
                chunk,
                include_identity_metadata=include_identity_metadata,
            )
            chunk_tool_calls = [
                {
                    "name": str(call.get("name") or ""),
                    "args": call.get("args") or {},
                    "id": str(call.get("id") or ""),
                    **({"type": call.get("type")} if call.get("type") else {}),
                }
                for call in list(normalized.tool_calls or [])
                if isinstance(call, Mapping)
            ]
            return AIMessageChunk(
                content=normalized.content,
                additional_kwargs=dict(normalized.additional_kwargs or {}),
                response_metadata=dict(normalized.response_metadata or {}),
                tool_call_chunks=list(getattr(normalized, "tool_call_chunks", []) or []),
                tool_calls=chunk_tool_calls,
                usage_metadata=normalized.usage_metadata,
            )
        normalized = self._decorate_message(
            AIMessageChunk(content=_stringify_content(chunk)),
            include_identity_metadata=include_identity_metadata,
        )
        return normalized if isinstance(normalized, AIMessageChunk) else AIMessageChunk(content=_stringify_content(chunk))

    def _tool_prompt_messages(self, messages: Sequence[BaseMessage]) -> list[BaseMessage]:
        tool_specs = []
        for tool in self._bound_tools or []:
            try:
                tool_specs.append(convert_to_openai_tool(tool))
            except Exception:
                tool_specs.append({"name": getattr(tool, "name", "tool"), "description": str(tool)})
        required_tool_name = self._required_bound_tool_name()
        if self._requires_bound_tool_call():
            required_instruction = (
                f"本次必须调用工具 {required_tool_name}。"
                if required_tool_name
                else "本次必须调用一个可用工具。"
            )
            required_instruction += "不要回答任务正文，也不要用自然语言描述将要调用工具。"
        else:
            required_instruction = "如需调用工具，请使用下述格式。"
        instruction = (
            "你当前处于工具调用兼容模式。"
            f"{required_instruction}只能输出一个 JSON 对象，格式为 "
            '{"tool_name":"<name>","arguments":{...}}，不要输出 Markdown 或额外解释。'
            f"\n可用工具：{json.dumps(tool_specs, ensure_ascii=False)}"
        )
        return [SystemMessage(content=instruction), *messages]

    def _should_fallback_structured_output(self, exc: Exception) -> bool:
        matrix = self.effective_capability_matrix()
        if not bool(matrix.get("supports_prompt_fallback_structured_output")):
            return False
        normalized = normalize_provider_error(exc, provider=self.provider_standard, model=self.model_id)
        if str(normalized.get("code") or "") in {"capability_mismatch", "invalid_request"}:
            return True
        raw = str(exc or "").lower()
        return any(
            token in raw
            for token in (
                "response_format.type",
                "json_schema",
                "structured output",
                "not supported by this model",
                "does not support structured",
            )
        )

    def _should_fallback_prompt_tools(self, exc: Exception) -> bool:
        matrix = self.effective_capability_matrix()
        if not bool(matrix.get("supports_prompt_emulated_tools")):
            return False
        normalized = normalize_provider_error(exc, provider=self.provider_standard, model=self.model_id)
        if str(normalized.get("code") or "") in {"capability_mismatch", "invalid_request"}:
            return True
        raw = str(exc or "").lower()
        return any(
            token in raw
            for token in (
                "tool",
                "function",
                "tool_calls",
                "function call",
                "not supported by this model",
                "does not support tools",
            )
        )

    def _structured_prompt_messages(self, messages: Sequence[BaseMessage], schema: Any) -> list[BaseMessage]:
        instruction = (
            "请严格返回 JSON，不要使用 Markdown，不要输出解释。"
            f"\nJSON Schema:\n{_schema_instruction(schema)}"
        )
        return [SystemMessage(content=instruction), *messages]

    def _build_chat_result(self, message: AIMessage) -> ChatResult:
        return ChatResult(
            generations=[
                ChatGeneration(
                    message=message,
                    text=_message_text(message),
                    generation_info=dict(getattr(message, "response_metadata", {}) or {}),
                )
            ],
            llm_output=dict(getattr(message, "response_metadata", {}) or {}),
        )

    @staticmethod
    def _provider_internal_config() -> dict[str, Any]:
        # The adapter is the sole canonical model-event boundary. Provider
        # clients still emit telemetry callbacks, but their nested LangChain
        # model events must never be projected as a second assistant stream.
        return {
            "metadata": {"v8_model_scope": "runtime_internal"},
            "tags": ["v8:provider-internal"],
        }

    def _prepare_prompt_cache_request(
        self,
        messages: Sequence[BaseMessage],
        *,
        stop: list[str] | None = None,
        streaming: bool = False,
        **kwargs: Any,
    ) -> PreparedPromptCacheRequest:
        return prompt_cache_gateway.prepare_request(
            messages=messages,
            kwargs=kwargs,
            stop=stop,
            provider_id=str(self._meta.get("provider_id") or self._meta.get("provider_name") or self.provider_standard or ""),
            model_id=self.model_id,
            model_ref=str(self._meta.get("model_ref") or ""),
            role=self.role,
            model_kwargs=self._model_kwargs,
            meta=self._meta,
            bound_tools=self._runtime_bound_tools(),
            streaming=streaming,
        )

    def _finalize_prompt_cache_response(self, message: AIMessage, prepared: PreparedPromptCacheRequest) -> AIMessage:
        prompt_cache_gateway.decorate_response(message, prepared.diagnostics)
        prompt_cache_gateway.store_response(message, prepared.diagnostics)
        return message

    def _decorate_prompt_cache_chunk(
        self,
        chunk: AIMessageChunk,
        diagnostics: Mapping[str, Any],
        *,
        include_diagnostics: bool = True,
    ) -> AIMessageChunk:
        response_metadata = dict(getattr(chunk, "response_metadata", {}) or {})
        if include_diagnostics:
            response_metadata.setdefault("v8_prompt_cache", dict(diagnostics or {}))
        else:
            response_metadata.pop("v8_prompt_cache", None)
        chunk.response_metadata = response_metadata
        return chunk

    def _generate(self, messages: list[BaseMessage], stop: list[str] | None = None, run_manager: Any | None = None, **kwargs: Any) -> ChatResult:
        normalized_messages = self._normalize_messages_for_provider(messages)
        if self._bound_tools and not self._provider_surface.supports_native_tools():
            normalized_messages = self._tool_prompt_messages(normalized_messages)
        prepared = self._prepare_prompt_cache_request(normalized_messages, stop=stop, **kwargs)
        if prepared.cache_hit_message:
            return self._build_chat_result(self._coerce_ai_message(prepared.cache_hit_message))
        try:
            response = self._get_runtime_model().invoke(
                prepared.messages,
                config=self._provider_internal_config(),
                stop=stop,
                **prepared.kwargs,
            )
            native_message = self._coerce_ai_message(response)
            if self._can_prompt_retry_required_tool(native_message):
                fallback_prepared = self._prepare_prompt_cache_request(
                    self._tool_prompt_messages(normalized_messages),
                    stop=stop,
                    **kwargs,
                )
                fallback_response = self._get_base_model().invoke(
                    fallback_prepared.messages,
                    config=self._provider_internal_config(),
                    stop=stop,
                    **fallback_prepared.kwargs,
                )
                fallback_message = self._coerce_ai_message(
                    fallback_response,
                    force_prompt_emulated_tools=True,
                )
                return self._build_chat_result(
                    self._finalize_prompt_cache_response(fallback_message, fallback_prepared)
                )
            return self._build_chat_result(self._finalize_prompt_cache_response(native_message, prepared))
        except Exception as exc:
            if self._bound_tools and self._provider_surface.supports_native_tools() and self._should_fallback_prompt_tools(exc):
                try:
                    fallback_prepared = self._prepare_prompt_cache_request(self._tool_prompt_messages(normalized_messages), stop=stop, **kwargs)
                    response = self._get_base_model().invoke(
                        fallback_prepared.messages,
                        config=self._provider_internal_config(),
                        stop=stop,
                        **fallback_prepared.kwargs,
                    )
                    return self._build_chat_result(
                        self._finalize_prompt_cache_response(
                            self._coerce_ai_message(response, force_prompt_emulated_tools=True),
                            fallback_prepared,
                        )
                    )
                except Exception as fallback_exc:
                    raise_as_v8_llm_error(
                        fallback_exc,
                        provider=self.provider_standard,
                        model=self.model_id,
                        details={"mode": "invoke", "fallback": "prompt_emulated_tools"},
                    )
            raise_as_v8_llm_error(exc, provider=self.provider_standard, model=self.model_id, details={"mode": "invoke"})

    async def _agenerate(self, messages: list[BaseMessage], stop: list[str] | None = None, run_manager: Any | None = None, **kwargs: Any) -> ChatResult:
        normalized_messages = self._normalize_messages_for_provider(messages)
        if self._bound_tools and not self._provider_surface.supports_native_tools():
            normalized_messages = self._tool_prompt_messages(normalized_messages)
        prepared = self._prepare_prompt_cache_request(normalized_messages, stop=stop, **kwargs)
        if prepared.cache_hit_message:
            return self._build_chat_result(self._coerce_ai_message(prepared.cache_hit_message))
        try:
            response = await self._get_runtime_model().ainvoke(
                prepared.messages,
                config=self._provider_internal_config(),
                stop=stop,
                **prepared.kwargs,
            )
            native_message = self._coerce_ai_message(response)
            if self._can_prompt_retry_required_tool(native_message):
                fallback_prepared = self._prepare_prompt_cache_request(
                    self._tool_prompt_messages(normalized_messages),
                    stop=stop,
                    **kwargs,
                )
                fallback_response = await self._get_base_model().ainvoke(
                    fallback_prepared.messages,
                    config=self._provider_internal_config(),
                    stop=stop,
                    **fallback_prepared.kwargs,
                )
                fallback_message = self._coerce_ai_message(
                    fallback_response,
                    force_prompt_emulated_tools=True,
                )
                return self._build_chat_result(
                    self._finalize_prompt_cache_response(fallback_message, fallback_prepared)
                )
            return self._build_chat_result(self._finalize_prompt_cache_response(native_message, prepared))
        except Exception as exc:
            if self._bound_tools and self._provider_surface.supports_native_tools() and self._should_fallback_prompt_tools(exc):
                try:
                    fallback_prepared = self._prepare_prompt_cache_request(self._tool_prompt_messages(normalized_messages), stop=stop, **kwargs)
                    response = await self._get_base_model().ainvoke(
                        fallback_prepared.messages,
                        config=self._provider_internal_config(),
                        stop=stop,
                        **fallback_prepared.kwargs,
                    )
                    return self._build_chat_result(
                        self._finalize_prompt_cache_response(
                            self._coerce_ai_message(response, force_prompt_emulated_tools=True),
                            fallback_prepared,
                        )
                    )
                except Exception as fallback_exc:
                    raise_as_v8_llm_error(
                        fallback_exc,
                        provider=self.provider_standard,
                        model=self.model_id,
                        details={"mode": "ainvoke", "fallback": "prompt_emulated_tools"},
                    )
            raise_as_v8_llm_error(exc, provider=self.provider_standard, model=self.model_id, details={"mode": "ainvoke"})

    def _stream(self, messages: list[BaseMessage], stop: list[str] | None = None, run_manager: Any | None = None, **kwargs: Any) -> Iterator[ChatGenerationChunk]:
        normalized_messages = self._normalize_messages_for_provider(messages)
        prompt_emulated_tools = bool(self._bound_tools and not self._provider_surface.supports_native_tools())
        if prompt_emulated_tools:
            normalized_messages = self._tool_prompt_messages(normalized_messages)
        prepared = self._prepare_prompt_cache_request(
            normalized_messages,
            stop=stop,
            streaming=not prompt_emulated_tools,
            **kwargs,
        )
        if prompt_emulated_tools:
            try:
                response = prepared.cache_hit_message or self._get_base_model().invoke(
                    prepared.messages,
                    config=self._provider_internal_config(),
                    stop=stop,
                    **prepared.kwargs,
                )
                message = self._finalize_prompt_cache_response(
                    self._coerce_ai_message(response, force_prompt_emulated_tools=True),
                    prepared,
                )
                ai_chunk = self._decorate_prompt_cache_chunk(self._coerce_chunk(message), prepared.diagnostics)
                yield ChatGenerationChunk(
                    message=ai_chunk,
                    text=_message_text(ai_chunk),
                    generation_info=dict(getattr(ai_chunk, "response_metadata", {}) or {}),
                )
                return
            except Exception as exc:
                raise_as_v8_llm_error(
                    exc,
                    provider=self.provider_standard,
                    model=self.model_id,
                    details={"mode": "stream", "toolMode": "prompt_emulated"},
                )
        try:
            include_stream_identity = True
            for chunk in self._get_runtime_model().stream(
                prepared.messages,
                config=self._provider_internal_config(),
                stop=stop,
                **prepared.kwargs,
            ):
                ai_chunk = self._decorate_prompt_cache_chunk(
                    self._coerce_chunk(
                        chunk,
                        include_identity_metadata=include_stream_identity,
                    ),
                    prepared.diagnostics,
                    include_diagnostics=include_stream_identity,
                )
                include_stream_identity = False
                yield ChatGenerationChunk(
                    message=ai_chunk,
                    text=_message_text(ai_chunk),
                    generation_info=dict(getattr(ai_chunk, "response_metadata", {}) or {}),
                )
        except Exception as exc:
            if self._bound_tools and self._provider_surface.supports_native_tools() and self._should_fallback_prompt_tools(exc):
                try:
                    fallback_prepared = self._prepare_prompt_cache_request(self._tool_prompt_messages(normalized_messages), stop=stop, streaming=False, **kwargs)
                    response = self._get_base_model().invoke(
                        fallback_prepared.messages,
                        config=self._provider_internal_config(),
                        stop=stop,
                        **fallback_prepared.kwargs,
                    )
                    message = self._finalize_prompt_cache_response(
                        self._coerce_ai_message(response, force_prompt_emulated_tools=True),
                        fallback_prepared,
                    )
                    ai_chunk = self._decorate_prompt_cache_chunk(self._coerce_chunk(message), fallback_prepared.diagnostics)
                    yield ChatGenerationChunk(
                        message=ai_chunk,
                        text=_message_text(ai_chunk),
                        generation_info=dict(getattr(ai_chunk, "response_metadata", {}) or {}),
                    )
                    return
                except Exception as fallback_exc:
                    raise_as_v8_llm_error(
                        fallback_exc,
                        provider=self.provider_standard,
                        model=self.model_id,
                        details={"mode": "stream", "fallback": "prompt_emulated_tools"},
                    )
            raise_as_v8_llm_error(exc, provider=self.provider_standard, model=self.model_id, details={"mode": "stream"})

    async def _astream(self, messages: list[BaseMessage], stop: list[str] | None = None, run_manager: Any | None = None, **kwargs: Any) -> AsyncIterator[ChatGenerationChunk]:
        normalized_messages = self._normalize_messages_for_provider(messages)
        prompt_emulated_tools = bool(self._bound_tools and not self._provider_surface.supports_native_tools())
        if prompt_emulated_tools:
            normalized_messages = self._tool_prompt_messages(normalized_messages)
        prepared = self._prepare_prompt_cache_request(
            normalized_messages,
            stop=stop,
            streaming=not prompt_emulated_tools,
            **kwargs,
        )
        if prompt_emulated_tools:
            try:
                response = prepared.cache_hit_message or await self._get_base_model().ainvoke(
                    prepared.messages,
                    config=self._provider_internal_config(),
                    stop=stop,
                    **prepared.kwargs,
                )
                message = self._finalize_prompt_cache_response(
                    self._coerce_ai_message(response, force_prompt_emulated_tools=True),
                    prepared,
                )
                ai_chunk = self._decorate_prompt_cache_chunk(self._coerce_chunk(message), prepared.diagnostics)
                yield ChatGenerationChunk(
                    message=ai_chunk,
                    text=_message_text(ai_chunk),
                    generation_info=dict(getattr(ai_chunk, "response_metadata", {}) or {}),
                )
                return
            except Exception as exc:
                raise_as_v8_llm_error(
                    exc,
                    provider=self.provider_standard,
                    model=self.model_id,
                    details={"mode": "astream", "toolMode": "prompt_emulated"},
                )
        try:
            include_stream_identity = True
            async for chunk in self._get_runtime_model().astream(
                prepared.messages,
                config=self._provider_internal_config(),
                stop=stop,
                **prepared.kwargs,
            ):
                ai_chunk = self._decorate_prompt_cache_chunk(
                    self._coerce_chunk(
                        chunk,
                        include_identity_metadata=include_stream_identity,
                    ),
                    prepared.diagnostics,
                    include_diagnostics=include_stream_identity,
                )
                include_stream_identity = False
                yield ChatGenerationChunk(
                    message=ai_chunk,
                    text=_message_text(ai_chunk),
                    generation_info=dict(getattr(ai_chunk, "response_metadata", {}) or {}),
                )
        except Exception as exc:
            if self._bound_tools and self._provider_surface.supports_native_tools() and self._should_fallback_prompt_tools(exc):
                try:
                    fallback_prepared = self._prepare_prompt_cache_request(self._tool_prompt_messages(normalized_messages), stop=stop, streaming=False, **kwargs)
                    response = await self._get_base_model().ainvoke(
                        fallback_prepared.messages,
                        config=self._provider_internal_config(),
                        stop=stop,
                        **fallback_prepared.kwargs,
                    )
                    message = self._finalize_prompt_cache_response(
                        self._coerce_ai_message(response, force_prompt_emulated_tools=True),
                        fallback_prepared,
                    )
                    ai_chunk = self._decorate_prompt_cache_chunk(self._coerce_chunk(message), fallback_prepared.diagnostics)
                    yield ChatGenerationChunk(
                        message=ai_chunk,
                        text=_message_text(ai_chunk),
                        generation_info=dict(getattr(ai_chunk, "response_metadata", {}) or {}),
                    )
                    return
                except Exception as fallback_exc:
                    raise_as_v8_llm_error(
                        fallback_exc,
                        provider=self.provider_standard,
                        model=self.model_id,
                        details={"mode": "astream", "fallback": "prompt_emulated_tools"},
                    )
            raise_as_v8_llm_error(exc, provider=self.provider_standard, model=self.model_id, details={"mode": "astream"})

    def bind_tools(self, tools: Sequence[Any], *, tool_choice: str | None = None, **kwargs: Any):  # type: ignore[override]
        # Do not deep-copy provider runtime clients here. LangChain/OpenAI
        # clients keep httpx clients and thread locks in private attrs; deep
        # copying them can crash long supervisor runs with
        # `cannot pickle '_thread.RLock' object`.
        clone = self.model_copy(deep=False)
        clone._meta = dict(self._meta)
        clone._model_kwargs = dict(self._model_kwargs)
        clone._builder = self._builder
        clone._provider_surface = create_provider_surface(self.provider_standard, self._meta)
        clone._bound_tools = list(tools)
        clone._bound_tool_kwargs = {"tool_choice": tool_choice, **kwargs}
        clone._base_model = None
        clone._bound_model = None
        return clone

    def with_structured_output(self, schema: dict[str, Any] | type, *, include_raw: bool = False, **kwargs: Any):  # type: ignore[override]
        return V8StructuredOutputRunnable(
            adapter=self,
            schema=schema,
            include_raw=include_raw,
            **kwargs,
        )

    def _invoke_structured_fallback(self, input_value: Any, *, schema: Any, include_raw: bool = False, config: Any | None = None, **kwargs: Any) -> Any:
        normalized_input = self.normalize_input_for_provider(input_value)
        if not isinstance(normalized_input, list) or not all(isinstance(item, BaseMessage) for item in normalized_input):
            raise V8LLMStructuredOutputError(
                code="structured_output_invalid",
                message="Structured output fallback requires message list input.",
                provider=self.provider_standard,
                model=self.model_id,
                user_action="请改用消息列表输入或启用支持原生结构化输出的模型。",
            )
        message = self.invoke(self._structured_prompt_messages(normalized_input, schema), config=config, **kwargs)
        payload = _extract_json_payload(_message_text(message))
        parsed = _coerce_structured_output(schema, payload)
        if include_raw:
            return {"raw": message, "parsed": parsed, "parsing_error": None}
        return parsed

    async def _ainvoke_structured_fallback(self, input_value: Any, *, schema: Any, include_raw: bool = False, config: Any | None = None, **kwargs: Any) -> Any:
        normalized_input = self.normalize_input_for_provider(input_value)
        if not isinstance(normalized_input, list) or not all(isinstance(item, BaseMessage) for item in normalized_input):
            raise V8LLMStructuredOutputError(
                code="structured_output_invalid",
                message="Structured output fallback requires message list input.",
                provider=self.provider_standard,
                model=self.model_id,
                user_action="请改用消息列表输入或启用支持原生结构化输出的模型。",
            )
        message = await self.ainvoke(self._structured_prompt_messages(normalized_input, schema), config=config, **kwargs)
        payload = _extract_json_payload(_message_text(message))
        parsed = _coerce_structured_output(schema, payload)
        if include_raw:
            return {"raw": message, "parsed": parsed, "parsing_error": None}
        return parsed
