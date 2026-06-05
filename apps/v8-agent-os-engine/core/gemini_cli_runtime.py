from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import threading
import time
import uuid
from pathlib import Path
from typing import Any, AsyncIterator, Iterator, Mapping, Sequence
from urllib.parse import unquote, urlparse

import requests
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from core.gemini_cli_oauth import _build_headers, bootstrap_gemini_cli_runtime
from core.reasoning_payload_contract import is_reasoning_block_type, is_reasoning_key


GEMINI_CLI_GENERATE_ENDPOINT = "v1internal:generateContent"
GEMINI_CLI_STREAM_ENDPOINT = "v1internal:streamGenerateContent?alt=sse"


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


def _guess_extension(mime_type: str, fallback: str = ".bin") -> str:
    guessed = mimetypes.guess_extension(mime_type or "")
    return guessed or fallback


def _guess_mime_type(path_like: str, fallback: str = "application/octet-stream") -> str:
    guessed, _ = mimetypes.guess_type(str(path_like or ""))
    return guessed or fallback


def _extract_data_url(payload: str) -> tuple[str, bytes] | None:
    raw = str(payload or "")
    if not raw.startswith("data:") or "," not in raw:
        return None
    header, encoded = raw.split(",", 1)
    mime_type = header[5:].split(";", 1)[0] or "application/octet-stream"
    if ";base64" not in header:
        return mime_type, encoded.encode("utf-8")
    return mime_type, base64.b64decode(encoded)


def _extract_error_message(response: requests.Response) -> str:
    try:
        payload = response.json()
    except Exception:
        payload = None
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()
        message = payload.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
    text = response.text.strip()
    if text:
        return text[:240]
    return f"HTTP {response.status_code} {response.reason}"


def _resolve_binary_blob(raw_url: str, *, mime_type: str = "") -> tuple[str, bytes] | None:
    value = str(raw_url or "").strip()
    if not value:
        return None

    data_url = _extract_data_url(value)
    if data_url is not None:
        detected_mime, content = data_url
        return mime_type or detected_mime or "application/octet-stream", content

    if value.startswith("file://"):
        parsed = urlparse(value)
        candidate = Path(unquote(parsed.path.lstrip("/")))
        if candidate.exists():
            return mime_type or _guess_mime_type(str(candidate)), candidate.read_bytes()

    candidate = Path(value)
    if candidate.exists():
        return mime_type or _guess_mime_type(str(candidate)), candidate.read_bytes()

    lowered = value.lower()
    if lowered.startswith("http://") or lowered.startswith("https://"):
        response = requests.get(value, timeout=30)
        response.raise_for_status()
        detected_mime = mime_type or response.headers.get("Content-Type", "application/octet-stream").split(";", 1)[0].strip()
        return detected_mime or "application/octet-stream", response.content

    return None


def _build_inline_data_part(raw_url: str, *, mime_type: str = "") -> dict[str, Any] | None:
    blob = _resolve_binary_blob(raw_url, mime_type=mime_type)
    if blob is None:
        return None
    detected_mime, content = blob
    return {
        "inlineData": {
            "mimeType": detected_mime or mime_type or "application/octet-stream",
            "data": base64.b64encode(content).decode("ascii"),
        }
    }


def _tool_call_summary(message: BaseMessage) -> str:
    tool_calls = list(getattr(message, "tool_calls", None) or [])
    if not tool_calls:
        return ""
    return "[Assistant Tool Calls]\n" + json.dumps(tool_calls, ensure_ascii=False)


def _render_message_parts(content: Any) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    if isinstance(content, list):
        for item in content:
            if isinstance(item, str):
                text_value = item.strip()
                if text_value:
                    parts.append({"text": text_value})
                continue
            if not isinstance(item, Mapping):
                rendered = _stringify_content(item).strip()
                if rendered:
                    parts.append({"text": rendered})
                continue

            item_type = str(item.get("type") or "").strip().lower()
            if item_type == "text":
                text_value = str(item.get("text") or "").strip()
                if text_value:
                    parts.append({"text": text_value})
                continue
            if item_type == "image_url":
                image_url = item.get("image_url") or {}
                if isinstance(image_url, Mapping):
                    inline_part = _build_inline_data_part(str(image_url.get("url") or ""), mime_type="image/png")
                    if inline_part:
                        parts.append(inline_part)
                continue
            if item_type == "media":
                inline_part = _build_inline_data_part(
                    str(item.get("file_uri") or ""),
                    mime_type=str(item.get("mime_type") or "application/octet-stream"),
                )
                if inline_part:
                    parts.append(inline_part)
                continue
            if item_type in {"file_url", "video_url"}:
                blob = item.get(item_type) or {}
                if isinstance(blob, Mapping):
                    inline_part = _build_inline_data_part(
                        str(blob.get("url") or ""),
                        mime_type=str(blob.get("mime_type") or "application/octet-stream"),
                    )
                    if inline_part:
                        parts.append(inline_part)
                continue

            rendered = _stringify_content(item).strip()
            if rendered:
                parts.append({"text": rendered})
        return parts

    rendered = _stringify_content(content).strip()
    if rendered:
        parts.append({"text": rendered})
    return parts


def _message_role(message: BaseMessage) -> str:
    if isinstance(message, HumanMessage):
        return "user"
    if isinstance(message, ToolMessage):
        return "user"
    return "model"


def _append_content_block(contents: list[dict[str, Any]], *, role: str, parts: list[dict[str, Any]]) -> None:
    if not parts:
        return
    if contents and str(contents[-1].get("role") or "") == role:
        contents[-1]["parts"].extend(parts)
        return
    contents.append({"role": role, "parts": parts})


def _build_request_messages(messages: Sequence[BaseMessage]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    system_parts: list[dict[str, Any]] = []
    contents: list[dict[str, Any]] = []
    for message in list(messages or []):
        if isinstance(message, SystemMessage):
            system_parts.extend(_render_message_parts(getattr(message, "content", "")))
            continue
        parts = _render_message_parts(getattr(message, "content", ""))
        tool_summary = _tool_call_summary(message)
        if tool_summary:
            parts.append({"text": tool_summary})
        if isinstance(message, ToolMessage):
            tool_name = str(getattr(message, "name", "") or getattr(message, "tool_call_id", "") or "tool")
            rendered = _stringify_content(getattr(message, "content", "")).strip()
            if rendered:
                parts = [{"text": f"[Tool Result: {tool_name}]\n{rendered}"}]
        _append_content_block(contents, role=_message_role(message), parts=parts)
    return contents, system_parts


def _truncate_with_stop(text: str, stop: Sequence[str] | None) -> tuple[str, bool]:
    rendered = str(text or "")
    stops = [str(item) for item in list(stop or []) if str(item or "")]
    if not stops:
        return rendered, False
    indexes = [rendered.find(marker) for marker in stops if rendered.find(marker) >= 0]
    if not indexes:
        return rendered, False
    return rendered[: min(indexes)], True


def _usage_metadata(usage: Mapping[str, Any] | None) -> dict[str, Any] | None:
    payload = dict(usage or {})
    if not payload:
        return None
    normalized: dict[str, Any] = {}
    prompt_tokens = payload.get("promptTokenCount")
    candidate_tokens = payload.get("candidatesTokenCount")
    total_tokens = payload.get("totalTokenCount")
    if prompt_tokens is not None:
        normalized["input_tokens"] = int(prompt_tokens)
    if total_tokens is not None:
        normalized["total_tokens"] = int(total_tokens)
    if candidate_tokens is not None:
        normalized["output_tokens"] = int(candidate_tokens)
    elif total_tokens is not None and prompt_tokens is not None:
        normalized["output_tokens"] = max(int(total_tokens) - int(prompt_tokens), 0)
    elif total_tokens is not None:
        normalized["output_tokens"] = int(total_tokens)
    if payload.get("cachedContentTokenCount") is not None:
        normalized["cached_tokens"] = int(payload["cachedContentTokenCount"])
    return normalized or None


def _extract_text_and_reasoning(parts: Sequence[Mapping[str, Any]] | None) -> tuple[str, str]:
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    for part in list(parts or []):
        text_value = str(part.get("text") or "").strip()
        is_reasoning = bool(
            any(is_reasoning_key(key) and bool(part.get(key)) for key in part.keys())
            or is_reasoning_block_type(part.get("type"))
        )
        if is_reasoning:
            if text_value:
                reasoning_parts.append(text_value)
            continue
        if text_value:
            text_parts.append(text_value)
    return "".join(text_parts), "\n".join(part for part in reasoning_parts if part)


def _parse_response_payload(payload: Mapping[str, Any], *, project_id: str, session_id: str) -> dict[str, Any]:
    response = payload.get("response") if isinstance(payload.get("response"), Mapping) else payload
    response = dict(response or {})
    candidates = response.get("candidates") or []
    candidate = candidates[0] if isinstance(candidates, list) and candidates else {}
    content = dict(candidate.get("content") or {})
    parts = list(content.get("parts") or [])
    text, reasoning = _extract_text_and_reasoning(parts)
    usage = dict(response.get("usageMetadata") or {})
    response_metadata = {
        "geminiCliRuntime": True,
        "geminiCliSessionId": session_id,
        "geminiCliProjectId": project_id,
        "geminiCliRequestKind": "cloud_code_http",
        "geminiCliModelVersion": str(response.get("modelVersion") or ""),
        "geminiCliResponseId": str(response.get("responseId") or ""),
        "geminiCliTraceId": str(payload.get("traceId") or ""),
        "geminiCliCreateTime": str(response.get("createTime") or ""),
        "geminiCliTrafficType": str(usage.get("trafficType") or ""),
        "geminiCliFinishReason": str(candidate.get("finishReason") or ""),
    }
    remote_context = payload.get("metadata") if isinstance(payload.get("metadata"), Mapping) else {}
    if remote_context:
        response_metadata["geminiCliMetadata"] = dict(remote_context)
    return {
        "text": text,
        "reasoning": reasoning,
        "usage": usage,
        "response_metadata": response_metadata,
        "finish_reason": str(candidate.get("finishReason") or ""),
    }


class GeminiCliRuntimeModel:
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
            or 120.0
        )
        self._bootstrap_cache: dict[str, Any] | None = None
        self._session_id = str(uuid.uuid4())

    def _bootstrap(self, *, force_refresh: bool = False) -> dict[str, Any]:
        if self._bootstrap_cache is not None and not force_refresh:
            return dict(self._bootstrap_cache)
        result = bootstrap_gemini_cli_runtime(
            oauth_path=str(self.meta.get("oauth_path") or ""),
            access_token=str(self.meta.get("oauth_access_token") or self.meta.get("api_key") or ""),
            base_url=str(self.meta.get("base_url") or ""),
            project_id=str(self.meta.get("project_id") or ""),
            timeout=min(self.timeout, 30.0),
            force_refresh=force_refresh,
        )
        self._bootstrap_cache = dict(result or {})
        return dict(self._bootstrap_cache)

    def _build_generation_config(self, *, stop: Sequence[str] | None = None, kwargs: Mapping[str, Any] | None = None) -> dict[str, Any]:
        merged = {**self.model_kwargs, **dict(kwargs or {})}
        config: dict[str, Any] = {}
        temperature = merged.get("temperature")
        if temperature is not None:
            config["temperature"] = float(temperature)
        max_output_tokens = merged.get("max_output_tokens") or merged.get("max_tokens")
        if max_output_tokens:
            config["maxOutputTokens"] = int(max_output_tokens)
        stop_sequences = list(stop or merged.get("stop") or merged.get("stop_sequences") or [])
        if stop_sequences:
            config["stopSequences"] = [str(item) for item in stop_sequences if str(item or "")]
        top_p = merged.get("top_p")
        if top_p is not None:
            config["topP"] = float(top_p)
        top_k = merged.get("top_k")
        if top_k is not None:
            config["topK"] = int(top_k)
        return config

    def _build_request_payload(self, messages: Sequence[BaseMessage], *, stop: Sequence[str] | None = None, kwargs: Mapping[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
        bootstrap = self._bootstrap()
        if not bootstrap.get("ok"):
            raise RuntimeError(str(bootstrap.get("message") or "Gemini CLI OAuth 凭据不可用。"))
        contents, system_parts = _build_request_messages(messages)
        request_payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": self._build_generation_config(stop=stop, kwargs=kwargs),
            "session_id": self._session_id,
        }
        if system_parts:
            request_payload["systemInstruction"] = {"parts": system_parts}
        payload = {
            "model": self.model_id,
            "project": str(bootstrap.get("projectId") or self.meta.get("project_id") or ""),
            "user_prompt_id": str(uuid.uuid4()),
            "request": request_payload,
        }
        return payload, bootstrap

    def _request(self, endpoint: str, *, payload: Mapping[str, Any], stream: bool = False, allow_refresh: bool = True) -> tuple[requests.Response, dict[str, Any]]:
        bootstrap = self._bootstrap()
        base_url = str(bootstrap.get("metadata", {}).get("baseUrl") or bootstrap.get("baseUrl") or self.meta.get("base_url") or "").rstrip("/")
        if not base_url:
            raise RuntimeError("Gemini CLI runtime 缺少 base_url。")
        headers = _build_headers(
            str(bootstrap.get("accessToken") or ""),
            dict((bootstrap.get("metadata") or {}).get("clientMetadata") or {}),
        )
        response = requests.post(
            f"{base_url}/{endpoint}",
            headers=headers,
            json=payload,
            timeout=(10, self.timeout),
            stream=stream,
        )
        if response.status_code == 401 and allow_refresh:
            self._bootstrap(force_refresh=True)
            return self._request(endpoint, payload=payload, stream=stream, allow_refresh=False)
        if not response.ok:
            raise RuntimeError(f"{response.status_code}: {_extract_error_message(response)}")
        return response, bootstrap

    def invoke(self, messages: Sequence[BaseMessage], stop: Sequence[str] | None = None, **kwargs: Any) -> AIMessage:
        payload, bootstrap = self._build_request_payload(messages, stop=stop, kwargs=kwargs)
        response, _ = self._request(GEMINI_CLI_GENERATE_ENDPOINT, payload=payload, stream=False)
        parsed = _parse_response_payload(
            response.json() if response.content else {},
            project_id=str(bootstrap.get("projectId") or ""),
            session_id=self._session_id,
        )
        truncated_text, _reached_stop = _truncate_with_stop(parsed["text"], stop)
        additional_kwargs = {"reasoning_content": parsed["reasoning"]} if parsed["reasoning"] else {}
        return AIMessage(
            content=truncated_text,
            additional_kwargs=additional_kwargs,
            response_metadata=parsed["response_metadata"],
            usage_metadata=_usage_metadata(parsed["usage"]),
        )

    async def ainvoke(self, messages: Sequence[BaseMessage], stop: Sequence[str] | None = None, **kwargs: Any) -> AIMessage:
        return await asyncio.to_thread(self.invoke, messages, stop, **kwargs)

    def stream(self, messages: Sequence[BaseMessage], stop: Sequence[str] | None = None, **kwargs: Any) -> Iterator[AIMessageChunk]:
        payload, bootstrap = self._build_request_payload(messages, stop=stop, kwargs=kwargs)
        response, _ = self._request(GEMINI_CLI_STREAM_ENDPOINT, payload=payload, stream=True)
        emitted_text = ""
        emitted_reasoning = ""
        final_usage: dict[str, Any] = {}
        final_metadata: dict[str, Any] = {}
        stop_reached = False
        try:
            for raw_line in response.iter_lines(decode_unicode=True):
                if raw_line is None:
                    continue
                line = str(raw_line).strip()
                if not line or not line.startswith("data: "):
                    continue
                payload_json = json.loads(line[6:])
                parsed = _parse_response_payload(
                    payload_json,
                    project_id=str(bootstrap.get("projectId") or ""),
                    session_id=self._session_id,
                )
                current_text = str(parsed["text"] or "")
                current_reasoning = str(parsed["reasoning"] or "")
                truncated_text, reached_stop = _truncate_with_stop(current_text, stop)
                text_delta = truncated_text[len(emitted_text):] if truncated_text.startswith(emitted_text) else truncated_text
                reasoning_delta = (
                    current_reasoning[len(emitted_reasoning):]
                    if current_reasoning.startswith(emitted_reasoning)
                    else current_reasoning
                )
                emitted_text = truncated_text
                emitted_reasoning = current_reasoning
                final_usage = dict(parsed["usage"] or {})
                final_metadata = dict(parsed["response_metadata"] or {})
                if text_delta or reasoning_delta:
                    additional_kwargs = {"thinking_delta": reasoning_delta} if reasoning_delta else {}
                    yield AIMessageChunk(
                        content=text_delta,
                        additional_kwargs=additional_kwargs,
                        response_metadata=final_metadata,
                    )
                stop_reached = stop_reached or reached_stop
                if stop_reached and parsed["finish_reason"]:
                    break
        finally:
            response.close()
        if final_metadata or final_usage:
            yield AIMessageChunk(
                content="",
                response_metadata=final_metadata,
                usage_metadata=_usage_metadata(final_usage),
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

        thread = threading.Thread(target=_worker, name=f"gemini-cli-stream-{self.model_id}", daemon=True)
        thread.start()
        while True:
            item = await queue.get()
            if item is sentinel:
                break
            if isinstance(item, Exception):
                raise item
            yield item
