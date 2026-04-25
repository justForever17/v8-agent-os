from __future__ import annotations

import json
from typing import Any, AsyncIterator, Callable, Iterator, Mapping, Sequence

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.runnables.base import RunnableSerializable
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import BaseModel as PydanticModel, ConfigDict, PrivateAttr

from core.model_capability_matrix import build_effective_capability_matrix
from core.llm_exceptions import V8LLMStructuredOutputError, raise_as_v8_llm_error
from core.provider_compatibility import normalize_provider_error
from core.response_normalizer import extract_text_and_reasoning, normalize_tool_calls, sanitize_model_tool_calls


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


class GeminiSurface(BaseProviderSurface):
    provider_standard = "gemini"

    def normalize_messages(self, messages: Sequence[BaseMessage]) -> list[BaseMessage]:
        normalized = super().normalize_messages(messages)
        system_instructions: list[str] = []
        out: list[BaseMessage] = []
        for message in normalized:
            if isinstance(message, SystemMessage):
                rendered = _stringify_content(message.content)
                if rendered:
                    system_instructions.append(rendered)
                continue
            if system_instructions:
                instruction_block = "[System Instructions]\n" + "\n\n".join(system_instructions)
                if isinstance(message, HumanMessage):
                    out.append(HumanMessage(content=f"{instruction_block}\n\n{_stringify_content(message.content)}"))
                else:
                    out.append(HumanMessage(content=instruction_block))
                    out.append(message)
                system_instructions = []
                continue
            out.append(message)
        if system_instructions:
            out.insert(0, HumanMessage(content="[System Instructions]\n" + "\n\n".join(system_instructions)))
        return out


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
        super().__init__(model_id=model_id, provider_standard=provider_standard, role=role)
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
        adapter = str(self._meta.get("provider_adapter") or self._meta.get("providerAdapter") or "").strip()
        if adapter:
            return adapter
        if self.provider_standard in {"google", "gemini"}:
            return "gemini"
        if self.provider_standard == "anthropic":
            return "anthropic"
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
            return self._provider_surface.normalize_messages(input_value)
        return input_value

    def _get_base_model(self) -> Any:
        if self._base_model is None:
            self._base_model = self._builder()
        return self._base_model

    def _get_runtime_model(self) -> Any:
        model = self._get_base_model()
        if not self._bound_tools:
            return model
        if self._bound_model is not None:
            return self._bound_model
        if self._provider_surface.supports_native_tools() and hasattr(model, "bind_tools"):
            self._bound_model = model.bind_tools(self._bound_tools, **self._bound_tool_kwargs)
            return self._bound_model
        return model

    def _build_native_structured_runnable(self, schema: Any, *, include_raw: bool = False, **kwargs: Any) -> Any | None:
        model = self._get_runtime_model()
        if self._provider_surface.supports_native_structured_output() and hasattr(model, "with_structured_output"):
            return model.with_structured_output(schema, include_raw=include_raw, **kwargs)
        return None

    def _decorate_message(self, message: Any, *, tool_mode: str | None = None, structured_mode: str | None = None) -> Any:
        normalized = sanitize_model_tool_calls(message, provider_standard=self.provider_standard)
        response_metadata = dict(getattr(normalized, "response_metadata", {}) or {})
        response_metadata.setdefault("v8_provider_adapter", self.provider_adapter())
        response_metadata.setdefault("v8_model_id", self.model_id)
        response_metadata.setdefault(
            "v8_provider_adapter_label",
            str(self._meta.get("provider_adapter_label") or self.provider_adapter()),
        )
        response_metadata.setdefault("v8_tool_calling_mode", tool_mode or self._provider_surface.tool_calling_mode())
        response_metadata.setdefault("v8_structured_output_mode", structured_mode or self._provider_surface.structured_output_mode())
        response_metadata.setdefault("v8_stream_mode", self.adapter_modes().get("streamMode"))
        response_metadata.setdefault("v8_effective_capability_matrix", self.effective_capability_matrix())
        if hasattr(normalized, "response_metadata"):
            normalized.response_metadata = response_metadata
        return normalized

    def _apply_prompt_emulated_tool_calls(self, message: Any, *, force: bool = False) -> Any:
        if not self._bound_tools or (self._provider_surface.supports_native_tools() and not force):
            return message
        content_text = _message_text(message)
        try:
            payload = _extract_json_payload(content_text)
        except Exception:
            return self._decorate_message(message, tool_mode="prompt_emulated")
        if not isinstance(payload, Mapping):
            return self._decorate_message(message, tool_mode="prompt_emulated")
        tool_name = str(payload.get("tool_name") or payload.get("name") or "").strip()
        if not tool_name:
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

    def _coerce_chunk(self, chunk: Any) -> AIMessageChunk:
        if isinstance(chunk, AIMessageChunk):
            normalized = self._decorate_message(chunk)
            return normalized if isinstance(normalized, AIMessageChunk) else AIMessageChunk(content=_stringify_content(getattr(normalized, "content", "")))
        if isinstance(chunk, AIMessage):
            return AIMessageChunk(
                content=chunk.content,
                additional_kwargs=dict(chunk.additional_kwargs or {}),
                response_metadata=dict(chunk.response_metadata or {}),
                tool_call_chunks=list(getattr(chunk, "tool_call_chunks", []) or []),
                tool_calls=list(chunk.tool_calls or []),
                usage_metadata=chunk.usage_metadata,
            )
        return AIMessageChunk(content=_stringify_content(chunk))

    def _tool_prompt_messages(self, messages: Sequence[BaseMessage]) -> list[BaseMessage]:
        tool_specs = []
        for tool in self._bound_tools or []:
            try:
                tool_specs.append(convert_to_openai_tool(tool))
            except Exception:
                tool_specs.append({"name": getattr(tool, "name", "tool"), "description": str(tool)})
        instruction = (
            "你当前处于工具调用兼容模式。"
            "如需调用工具，只能输出一个 JSON 对象，格式为 "
            '{"tool_name":"<name>","arguments":{...}}，不要输出额外解释。'
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

    def _generate(self, messages: list[BaseMessage], stop: list[str] | None = None, run_manager: Any | None = None, **kwargs: Any) -> ChatResult:
        normalized_messages = self._provider_surface.normalize_messages(messages)
        if self._bound_tools and not self._provider_surface.supports_native_tools():
            normalized_messages = self._tool_prompt_messages(normalized_messages)
        try:
            response = self._get_runtime_model().invoke(normalized_messages, stop=stop, **kwargs)
            return self._build_chat_result(self._coerce_ai_message(response))
        except Exception as exc:
            if self._bound_tools and self._provider_surface.supports_native_tools() and self._should_fallback_prompt_tools(exc):
                try:
                    response = self._get_base_model().invoke(self._tool_prompt_messages(normalized_messages), stop=stop, **kwargs)
                    return self._build_chat_result(self._coerce_ai_message(response, force_prompt_emulated_tools=True))
                except Exception as fallback_exc:
                    raise_as_v8_llm_error(
                        fallback_exc,
                        provider=self.provider_standard,
                        model=self.model_id,
                        details={"mode": "invoke", "fallback": "prompt_emulated_tools"},
                    )
            raise_as_v8_llm_error(exc, provider=self.provider_standard, model=self.model_id, details={"mode": "invoke"})

    async def _agenerate(self, messages: list[BaseMessage], stop: list[str] | None = None, run_manager: Any | None = None, **kwargs: Any) -> ChatResult:
        normalized_messages = self._provider_surface.normalize_messages(messages)
        if self._bound_tools and not self._provider_surface.supports_native_tools():
            normalized_messages = self._tool_prompt_messages(normalized_messages)
        try:
            response = await self._get_runtime_model().ainvoke(normalized_messages, stop=stop, **kwargs)
            return self._build_chat_result(self._coerce_ai_message(response))
        except Exception as exc:
            if self._bound_tools and self._provider_surface.supports_native_tools() and self._should_fallback_prompt_tools(exc):
                try:
                    response = await self._get_base_model().ainvoke(self._tool_prompt_messages(normalized_messages), stop=stop, **kwargs)
                    return self._build_chat_result(self._coerce_ai_message(response, force_prompt_emulated_tools=True))
                except Exception as fallback_exc:
                    raise_as_v8_llm_error(
                        fallback_exc,
                        provider=self.provider_standard,
                        model=self.model_id,
                        details={"mode": "ainvoke", "fallback": "prompt_emulated_tools"},
                    )
            raise_as_v8_llm_error(exc, provider=self.provider_standard, model=self.model_id, details={"mode": "ainvoke"})

    def _stream(self, messages: list[BaseMessage], stop: list[str] | None = None, run_manager: Any | None = None, **kwargs: Any) -> Iterator[ChatGenerationChunk]:
        normalized_messages = self._provider_surface.normalize_messages(messages)
        if self._bound_tools and not self._provider_surface.supports_native_tools():
            normalized_messages = self._tool_prompt_messages(normalized_messages)
        try:
            for chunk in self._get_runtime_model().stream(normalized_messages, stop=stop, **kwargs):
                ai_chunk = self._coerce_chunk(chunk)
                yield ChatGenerationChunk(
                    message=ai_chunk,
                    text=_message_text(ai_chunk),
                    generation_info=dict(getattr(ai_chunk, "response_metadata", {}) or {}),
                )
        except Exception as exc:
            if self._bound_tools and self._provider_surface.supports_native_tools() and self._should_fallback_prompt_tools(exc):
                try:
                    for chunk in self._get_base_model().stream(self._tool_prompt_messages(normalized_messages), stop=stop, **kwargs):
                        ai_chunk = self._coerce_chunk(chunk)
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
        normalized_messages = self._provider_surface.normalize_messages(messages)
        if self._bound_tools and not self._provider_surface.supports_native_tools():
            normalized_messages = self._tool_prompt_messages(normalized_messages)
        try:
            async for chunk in self._get_runtime_model().astream(normalized_messages, stop=stop, **kwargs):
                ai_chunk = self._coerce_chunk(chunk)
                yield ChatGenerationChunk(
                    message=ai_chunk,
                    text=_message_text(ai_chunk),
                    generation_info=dict(getattr(ai_chunk, "response_metadata", {}) or {}),
                )
        except Exception as exc:
            if self._bound_tools and self._provider_surface.supports_native_tools() and self._should_fallback_prompt_tools(exc):
                try:
                    async for chunk in self._get_base_model().astream(self._tool_prompt_messages(normalized_messages), stop=stop, **kwargs):
                        ai_chunk = self._coerce_chunk(chunk)
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
        clone = self.model_copy(deep=True)
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
