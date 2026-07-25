from __future__ import annotations

import asyncio
import base64
import json
import threading
import time
import uuid
from typing import Any, AsyncIterator, Iterator, Mapping, Sequence

import requests
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from core.provider_continuation import extract_provider_continuation, replay_content_blocks


CODEX_RESPONSES_ENDPOINT = "/codex/responses"
JWT_CLAIM_PATH = "https://api.openai.com/auth"


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


def _base64url_decode(segment: str) -> bytes:
    padded = segment + "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def _extract_account_id(token: str) -> str:
    parts = str(token or "").split(".")
    if len(parts) == 3:
        try:
            payload = json.loads(_base64url_decode(parts[1]).decode("utf-8"))
            account_id = payload.get(JWT_CLAIM_PATH, {}).get("chatgpt_account_id")
            if account_id:
                return str(account_id)
        except Exception:
            pass
    return ""


def _resolve_codex_url(base_url: str) -> str:
    raw = str(base_url or "https://chatgpt.com/backend-api").rstrip("/")
    if raw.endswith("/codex/responses"):
        return raw
    if raw.endswith("/codex"):
        return f"{raw}/responses"
    return f"{raw}{CODEX_RESPONSES_ENDPOINT}"


def _message_to_responses_items(message: BaseMessage) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if isinstance(message, AIMessage):
        # Responses store=false conversations must replay encrypted reasoning
        # items as top-level input items, before the visible assistant output.
        items.extend(replay_content_blocks(extract_provider_continuation(message)))
    content = _stringify_content(getattr(message, "content", "")).strip()
    if not content:
        return items
    if isinstance(message, ToolMessage):
        tool_name = str(getattr(message, "name", "") or getattr(message, "tool_call_id", "") or "tool")
        content = f"[Tool Result: {tool_name}]\n{content}"
    role = "assistant" if isinstance(message, AIMessage) else "user"
    content_type = "output_text" if role == "assistant" else "input_text"
    items.append({"role": role, "content": [{"type": content_type, "text": content}]})
    return items


def _build_messages(messages: Sequence[BaseMessage]) -> tuple[str, list[dict[str, Any]]]:
    system_parts: list[str] = []
    input_items: list[dict[str, Any]] = []
    for message in list(messages or []):
        if isinstance(message, SystemMessage):
            rendered = _stringify_content(getattr(message, "content", "")).strip()
            if rendered:
                system_parts.append(rendered)
            continue
        input_items.extend(_message_to_responses_items(message))
    return "\n\n".join(system_parts), input_items


def _response_output_items(response_payload: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    output = dict(response_payload or {}).get("output") or []
    return [dict(item) for item in output if isinstance(item, Mapping)]


def _reasoning_output_items(response_payload: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    return [item for item in _response_output_items(response_payload) if str(item.get("type") or "") == "reasoning"]


def _completed_output_text(response_payload: Mapping[str, Any] | None) -> str:
    parts: list[str] = []
    for item in _response_output_items(response_payload):
        if str(item.get("type") or "") != "message":
            continue
        for block in list(item.get("content") or []):
            if not isinstance(block, Mapping) or str(block.get("type") or "") not in {"output_text", "text"}:
                continue
            text_value = str(block.get("text") or "")
            if text_value:
                parts.append(text_value)
    return "".join(parts)


def _reasoning_summary_block(summary_text: str) -> dict[str, Any] | None:
    text_value = str(summary_text or "")
    if not text_value:
        return None
    return {
        "type": "reasoning",
        "summary": [{"type": "summary_text", "text": text_value}],
    }


def _truncate_with_stop(text: str, stop: Sequence[str] | None) -> tuple[str, bool]:
    rendered = str(text or "")
    stops = [str(item) for item in list(stop or []) if str(item or "")]
    if not stops:
        return rendered, False
    indexes = [rendered.find(marker) for marker in stops if rendered.find(marker) >= 0]
    if not indexes:
        return rendered, False
    return rendered[: min(indexes)], True


def _usage_metadata(response_payload: Mapping[str, Any] | None) -> dict[str, Any] | None:
    response = dict(response_payload or {})
    usage = dict(response.get("usage") or {})
    if not usage:
        return None
    input_tokens = int(usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    total_tokens = int(usage.get("total_tokens") or input_tokens + output_tokens)
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


def _parse_sse_line(line: str) -> dict[str, Any] | None:
    rendered = str(line or "").strip()
    if not rendered.startswith("data: "):
        return None
    payload = rendered[6:].strip()
    if not payload or payload == "[DONE]":
        return None
    try:
        parsed = json.loads(payload)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


class OpenAICodexResponsesRuntimeModel:
    def __init__(
        self,
        *,
        model_id: str,
        meta: Mapping[str, Any],
        model_kwargs: Mapping[str, Any] | None = None,
    ) -> None:
        self.model_id = str(model_id)
        self.meta = dict(meta or {})
        self.model_kwargs = dict(model_kwargs or {})
        self.timeout = float(
            self.model_kwargs.get("timeout")
            or self.meta.get("timeout")
            or self.meta.get("timeouts", {}).get("request")
            or 180.0
        )
        self._session_id = str(uuid.uuid4())

    def _headers(self) -> dict[str, str]:
        token = str(self.meta.get("oauth_access_token") or self.meta.get("api_key") or "").strip()
        if not token:
            raise RuntimeError("Codex OAuth runtime 缺少 access token。")
        account_id = str(self.meta.get("account_id") or "").strip() or _extract_account_id(token)
        if not account_id:
            raise RuntimeError("Codex OAuth runtime 缺少 account id。")
        return {
            "Authorization": f"Bearer {token}",
            "chatgpt-account-id": account_id,
            "originator": "pi",
            "OpenAI-Beta": "responses=experimental",
            "accept": "text/event-stream",
            "content-type": "application/json",
            "User-Agent": "pi (win32; x64)",
            "session_id": self._session_id,
        }

    def _build_body(
        self,
        messages: Sequence[BaseMessage],
        *,
        stop: Sequence[str] | None = None,
        kwargs: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        merged = {**self.model_kwargs, **dict(kwargs or {})}
        instructions, input_items = _build_messages(messages)
        if not input_items:
            input_items = [{"role": "user", "content": [{"type": "input_text", "text": ""}]}]
        body: dict[str, Any] = {
            "model": self.model_id,
            "store": False,
            "stream": True,
            "instructions": instructions,
            "input": input_items,
            "text": {"verbosity": str(merged.get("textVerbosity") or merged.get("text_verbosity") or "medium")},
            "include": list(
                dict.fromkeys(
                    [
                        *list(merged.get("include") or []),
                        "reasoning.encrypted_content",
                    ]
                )
            ),
            "prompt_cache_key": str(merged.get("prompt_cache_key") or self._session_id),
            "tool_choice": "auto",
            "parallel_tool_calls": True,
            "reasoning": {
                "effort": str(merged.get("reasoning_effort") or merged.get("reasoningEffort") or "low"),
                "summary": str(merged.get("reasoning_summary") or merged.get("reasoningSummary") or "auto"),
            },
        }
        if stop:
            body["stop"] = [str(item) for item in stop if str(item or "")]
        return body

    def _post_stream(self, body: Mapping[str, Any]) -> requests.Response:
        response = requests.post(
            _resolve_codex_url(str(self.meta.get("base_url") or "")),
            headers=self._headers(),
            json=dict(body),
            stream=True,
            timeout=(10, self.timeout),
        )
        if not response.ok:
            text = response.text.strip()
            raise RuntimeError(f"{response.status_code}: {text[:600]}")
        return response

    def _stream_events(self, body: Mapping[str, Any]) -> Iterator[dict[str, Any]]:
        response = self._post_stream(body)
        try:
            for raw_line in response.iter_lines(decode_unicode=True):
                if raw_line is None:
                    continue
                line = raw_line.decode("utf-8", errors="ignore") if isinstance(raw_line, bytes) else str(raw_line)
                event = _parse_sse_line(line)
                if event:
                    yield event
        finally:
            response.close()

    def invoke(self, messages: Sequence[BaseMessage], stop: Sequence[str] | None = None, **kwargs: Any) -> AIMessage:
        body = self._build_body(messages, stop=stop, kwargs=kwargs)
        text_parts: list[str] = []
        summary_parts: list[str] = []
        streamed_reasoning_items: list[dict[str, Any]] = []
        response_payload: dict[str, Any] = {}
        response_id = ""
        status = ""
        for event in self._stream_events(body):
            event_type = str(event.get("type") or "")
            if event_type == "response.output_text.delta":
                text_parts.append(str(event.get("delta") or ""))
            elif event_type == "response.reasoning_summary_text.delta":
                summary_parts.append(str(event.get("delta") or ""))
            elif event_type in {"response.output_item.added", "response.output_item.done"}:
                item = event.get("item")
                if isinstance(item, Mapping) and str(item.get("type") or "") == "reasoning":
                    streamed_reasoning_items.append(dict(item))
            elif event_type == "response.completed":
                response_payload = dict(event.get("response") or {})
                response_id = str(response_payload.get("id") or response_id)
                status = str(response_payload.get("status") or status)
            elif event_type == "response.failed":
                response_payload = dict(event.get("response") or {})
                error = response_payload.get("error") or {}
                raise RuntimeError(str(error.get("message") if isinstance(error, dict) else error))
        completed_text = _completed_output_text(response_payload)
        text, _reached_stop = _truncate_with_stop("".join(text_parts) or completed_text, stop)
        reasoning_items = _reasoning_output_items(response_payload) or streamed_reasoning_items
        content: str | list[dict[str, Any]] = text
        if reasoning_items or summary_parts:
            content_blocks = [dict(item) for item in reasoning_items]
            if not content_blocks:
                summary_block = _reasoning_summary_block("".join(summary_parts))
                if summary_block:
                    content_blocks.append(summary_block)
            if text:
                content_blocks.append({"type": "text", "text": text})
            content = content_blocks
        return AIMessage(
            content=content,
            response_metadata={
                "codexResponsesRuntime": True,
                "codexResponseId": response_id,
                "codexResponseStatus": status,
                "codexSessionId": self._session_id,
                "model": self.model_id,
                "v8_provider_standard": "openai",
            },
            usage_metadata=_usage_metadata(response_payload),
        )

    async def ainvoke(self, messages: Sequence[BaseMessage], stop: Sequence[str] | None = None, **kwargs: Any) -> AIMessage:
        return await asyncio.to_thread(self.invoke, messages, stop, **kwargs)

    def stream(self, messages: Sequence[BaseMessage], stop: Sequence[str] | None = None, **kwargs: Any) -> Iterator[AIMessageChunk]:
        body = self._build_body(messages, stop=stop, kwargs=kwargs)
        emitted = ""
        final_payload: dict[str, Any] = {}
        response_id = ""
        status = ""
        for event in self._stream_events(body):
            event_type = str(event.get("type") or "")
            if event_type == "response.output_text.delta":
                emitted += str(event.get("delta") or "")
                text, _reached_stop = _truncate_with_stop(emitted, stop)
                yield AIMessageChunk(
                    content=text[-len(str(event.get("delta") or "")):],
                    response_metadata={
                        "codexResponsesRuntime": True,
                        "model": self.model_id,
                        "v8_provider_standard": "openai",
                    },
                )
            elif event_type == "response.reasoning_summary_text.delta":
                summary_block = _reasoning_summary_block(str(event.get("delta") or ""))
                if summary_block:
                    yield AIMessageChunk(
                        content=[summary_block],
                        response_metadata={
                            "codexResponsesRuntime": True,
                            "model": self.model_id,
                            "v8_provider_standard": "openai",
                        },
                    )
            elif event_type in {"response.output_item.added", "response.output_item.done"}:
                item = event.get("item")
                if isinstance(item, Mapping) and str(item.get("type") or "") == "reasoning":
                    yield AIMessageChunk(
                        content=[dict(item)],
                        response_metadata={
                            "codexResponsesRuntime": True,
                            "model": self.model_id,
                            "v8_provider_standard": "openai",
                        },
                    )
            elif event_type == "response.completed":
                final_payload = dict(event.get("response") or {})
                response_id = str(final_payload.get("id") or response_id)
                status = str(final_payload.get("status") or status)
                for reasoning_item in _reasoning_output_items(final_payload):
                    yield AIMessageChunk(
                        content=[reasoning_item],
                        response_metadata={
                            "codexResponsesRuntime": True,
                            "model": self.model_id,
                            "v8_provider_standard": "openai",
                        },
                    )
            elif event_type == "response.failed":
                final_payload = dict(event.get("response") or {})
                error = final_payload.get("error") or {}
                raise RuntimeError(str(error.get("message") if isinstance(error, dict) else error))
        if final_payload:
            yield AIMessageChunk(
                content="",
                response_metadata={
                    "codexResponsesRuntime": True,
                    "codexResponseId": response_id,
                    "codexResponseStatus": status,
                    "codexSessionId": self._session_id,
                    "model": self.model_id,
                    "v8_provider_standard": "openai",
                },
                usage_metadata=_usage_metadata(final_payload),
            )

    async def astream(self, messages: Sequence[BaseMessage], stop: Sequence[str] | None = None, **kwargs: Any) -> AsyncIterator[AIMessageChunk]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Any] = asyncio.Queue()
        sentinel = object()

        def _worker() -> None:
            try:
                for chunk in self.stream(messages, stop=stop, **kwargs):
                    asyncio.run_coroutine_threadsafe(queue.put(chunk), loop).result()
            except Exception as exc:
                asyncio.run_coroutine_threadsafe(queue.put(exc), loop).result()
            finally:
                asyncio.run_coroutine_threadsafe(queue.put(sentinel), loop).result()

        threading.Thread(target=_worker, name=f"codex-responses-stream-{self.model_id}", daemon=True).start()
        while True:
            item = await queue.get()
            if item is sentinel:
                break
            if isinstance(item, Exception):
                raise item
            yield item
