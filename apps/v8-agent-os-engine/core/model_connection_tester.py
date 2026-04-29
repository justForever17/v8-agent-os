from __future__ import annotations

import time
import uuid
from typing import Any, Dict

import requests
from langchain_core.messages import HumanMessage
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from core.local_visual_support import probe_local_multimodal_capability
from core.database import db
from core.llm_factory import (
    build_rerank_endpoint_candidates,
    llm_factory,
    normalize_rerank_api_flavor,
    parse_rerank_response_payload,
)
from core.multimodal_payload_adapter import build_multimodal_content
from core.model_control_plane import model_control_plane
from core.model_ref import make_model_ref
from core.provider_compatibility import normalize_provider_error


def _extract_text_preview(response: Any) -> str:
    content = getattr(response, "content", "")
    if isinstance(content, str):
        return content.strip()[:120]

    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if isinstance(item, dict):
                text_value = item.get("text") or item.get("content") or ""
                if text_value:
                    parts.append(str(text_value))
                continue
            text_value = getattr(item, "text", "") or getattr(item, "content", "")
            if text_value:
                parts.append(str(text_value))
        return " ".join(part.strip() for part in parts if str(part).strip())[:120]

    return str(content).strip()[:120]


def _extract_reasoning_signal(response: Any) -> Dict[str, Any]:
    additional_kwargs = dict(getattr(response, "additional_kwargs", {}) or {})
    response_metadata = dict(getattr(response, "response_metadata", {}) or {})
    token_usage = dict(response_metadata.get("token_usage") or {})
    completion_details = dict(token_usage.get("completion_tokens_details") or {})
    reasoning_preview = ""
    for key in ("reasoning_content", "reasoning", "thinking", "thought"):
        value = additional_kwargs.get(key)
        if isinstance(value, str) and value.strip():
            reasoning_preview = value.strip()[:120]
            break
    return {
        "finishReason": str(response_metadata.get("finish_reason") or ""),
        "reasoningTokens": int(completion_details.get("reasoning_tokens") or 0),
        "reasoningPreview": reasoning_preview,
    }


class ModelConnectionTester:
    _TEST_IMAGE_DATA_URL = (
        "data:image/png;base64,"
        "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAIAAACQkWg2AAAAFklEQVR4nGP43+BAEmIY1TCq"
        "YfhqAABiM78Q2qxszwAAAABJRU5ErkJggg=="
    )

    class _StructuredProbe(BaseModel):
        status: str = Field(description="must be ok")

    def _resolve_local_backend_preset(self, meta: Dict[str, Any]) -> str:
        provider_record = dict(meta.get("provider_record") or {})
        preset = str(provider_record.get("local_backend_preset") or meta.get("local_backend_preset") or "").strip().lower()
        if preset in {"ollama", "nexa", "vllm", "lmstudio"}:
            return preset
        base_url = str(meta.get("base_url") or "").lower()
        if ":11434" in base_url or "ollama" in base_url:
            return "ollama"
        if ":8000" in base_url or "vllm" in base_url:
            return "vllm"
        if ":18181" in base_url or "nexa" in base_url:
            return "nexa"
        return "lmstudio"

    def _resolve_local_probe_root(self, base_url: str) -> str:
        root = str(base_url or "").rstrip("/")
        if root.endswith("/v1"):
            return root[:-3]
        return root

    def _probe_local_capability(self, *, model_id: str, meta: Dict[str, Any]) -> Dict[str, Any] | None:
        provider_record = dict(meta.get("provider_record") or {})
        provider_type = str(provider_record.get("type") or "API").upper()
        if provider_type != "LOCAL":
            return None
        preset = self._resolve_local_backend_preset(meta)
        base_url = str(meta.get("base_url") or "")
        if preset == "ollama":
            endpoint = f"{self._resolve_local_probe_root(base_url).rstrip('/')}/api/tags"
            response = requests.get(endpoint, timeout=10)
            response.raise_for_status()
            models = response.json().get("models") or []
            return {
                "preset": preset,
                "available": True,
                "requestKind": "ollama_tags",
                "resolvedEndpoint": endpoint,
                "modelCount": len(models),
                "models": [str((item or {}).get("name") or "") for item in models[:8] if (item or {}).get("name")],
            }
        if preset in {"vllm", "nexa"}:
            endpoint = f"{self._resolve_local_probe_root(base_url).rstrip('/')}/v1/models"
            response = requests.get(endpoint, headers={"Authorization": f"Bearer {meta.get('api_key') or ''}"}, timeout=10)
            response.raise_for_status()
            models = response.json().get("data") or []
            return {
                "preset": preset,
                "available": True,
                "requestKind": "openai_models",
                "resolvedEndpoint": endpoint,
                "modelCount": len(models),
                "models": [str((item or {}).get("id") or "") for item in models[:8] if (item or {}).get("id")],
            }
        probe = probe_local_multimodal_capability(
            model_id=model_id,
            provider_type=provider_type,
            base_url=base_url,
            api_key=str(meta.get("api_key") or ""),
        ) or {}
        return {
            "preset": preset,
            "resolvedEndpoint": base_url,
            **probe,
        }

    def _record_health(
        self,
        *,
        provider_id: str,
        provider_name: str,
        model_id: str,
        status: str,
        latency_ms: float,
        error_code: str | None = None,
        error_message: str | None = None,
        detail: Dict[str, Any] | None = None,
    ) -> None:
        db.add_provider_health_log(
            {
                "id": str(uuid.uuid4()),
                "provider_id": provider_id or "unknown",
                "provider_name": provider_name or provider_id or "unknown",
                "model_id": model_id,
                "run_id": None,
                "session_id": None,
                "status": status,
                "error_code": error_code,
                "error_message": error_message,
                "latency_ms": latency_ms,
                "detail": detail or {},
            }
        )

    def _resolve_metadata(self, model_id: str, *, provider_id: str = "") -> Dict[str, Any]:
        target_model_id = make_model_ref(provider_id, model_id) if provider_id and "::" not in model_id else model_id
        meta = llm_factory._resolve_model_metadata(target_model_id)  # noqa: SLF001 - internal service helper
        record = model_control_plane.get_model_record(target_model_id, provider_id=provider_id)
        if not meta.get("is_found") or not record:
            raise ValueError(f"模型 {model_id} 未在 models.json 中注册，或存在重名模型需要指定 Provider。")
        return {
            **meta,
            "provider_record": dict(record.get("provider") or {}),
            "model_record": dict(record.get("model") or {}),
        }

    def _test_chat_model(self, *, model_id: str, meta: Dict[str, Any]) -> Dict[str, Any]:
        started = time.perf_counter()
        client = llm_factory.create_chat_model(
            model_id,
            temperature=0,
            # Connection probes should stay tiny. Some providers expose very
            # large max output values, but using those here can exhaust quota or
            # trigger provider-specific validation paths during a health check.
            max_tokens=16,
            streaming=False,
            _role="connection_test",
        )
        response = client.invoke([HumanMessage(content="Reply with exact string: OK")])
        latency_ms = (time.perf_counter() - started) * 1000
        preview = _extract_text_preview(response) or "连接成功"
        return {
            "latencyMs": round(latency_ms, 2),
            "message": preview,
            "requestKind": "chat_completion",
            "resolvedEndpoint": str(meta.get("base_url") or ""),
            "runtimeMetadata": dict(getattr(response, "response_metadata", {}) or {}),
        }

    def _test_streaming_capability(self, *, model_id: str, meta: Dict[str, Any]) -> Dict[str, Any]:
        started = time.perf_counter()
        client = llm_factory.create_chat_model(
            model_id,
            temperature=0,
            max_tokens=16,
            streaming=True,
            _role="connection_test_stream",
        )
        chunks: list[str] = []
        for index, chunk in enumerate(client.stream([HumanMessage(content="Reply with exact string: OK")])):
            content = getattr(chunk, "content", "")
            if isinstance(content, str) and content:
                chunks.append(content)
            if index >= 4:
                break
        latency_ms = (time.perf_counter() - started) * 1000
        return {
            "latencyMs": round(latency_ms, 2),
            "message": ("".join(chunks).strip() or "stream ok")[:120],
            "requestKind": "streaming",
            "resolvedEndpoint": str(meta.get("base_url") or ""),
        }

    def _test_tool_calling_capability(self, *, model_id: str, meta: Dict[str, Any]) -> Dict[str, Any]:
        def echo_status(status: str) -> str:
            return status

        started = time.perf_counter()
        client = llm_factory.create_chat_model(
            model_id,
            temperature=0,
            max_tokens=96,
            streaming=False,
            _role="connection_test_tools",
        ).bind_tools(
            [StructuredTool.from_function(echo_status, name="echo_status", description="Echo the provided status string.")],
            tool_choice="required",
        )
        response = client.invoke(
            [
                HumanMessage(
                    content=(
                        "你必须调用名为 echo_status 的工具，"
                        '把参数设置为 {"status":"ok"}，'
                        "不要直接回答自然语言。"
                    )
                )
            ]
        )
        tool_calls = list(getattr(response, "tool_calls", None) or [])
        if not tool_calls:
            raise RuntimeError("模型未返回工具调用。")
        response_metadata = dict(getattr(response, "response_metadata", {}) or {})
        latency_ms = (time.perf_counter() - started) * 1000
        return {
            "latencyMs": round(latency_ms, 2),
            "message": f"tool call ok · {tool_calls[0].get('name') or 'unknown'}",
            "requestKind": "tool_calling",
            "resolvedEndpoint": str(meta.get("base_url") or ""),
            "toolCallingMode": str(response_metadata.get("v8_tool_calling_mode") or "native"),
            "toolCallCount": len(tool_calls),
        }

    def _test_structured_output_capability(self, *, model_id: str, meta: Dict[str, Any]) -> Dict[str, Any]:
        started = time.perf_counter()
        client = llm_factory.create_chat_model(
            model_id,
            temperature=0,
            max_tokens=96,
            streaming=False,
            _role="connection_test_structured",
        ).with_structured_output(self._StructuredProbe)
        result = client.invoke(
            [
                HumanMessage(
                    content='Return a JSON object with one field exactly equal to {"status":"ok"}.'
                )
            ]
        )
        parsed_status = getattr(result, "status", None) if isinstance(result, self._StructuredProbe) else None
        if not parsed_status and isinstance(result, dict):
            parsed_status = result.get("status")
        if str(parsed_status or "").strip().lower() != "ok":
            raise RuntimeError("结构化输出未返回预期的 status=ok。")
        latency_ms = (time.perf_counter() - started) * 1000
        return {
            "latencyMs": round(latency_ms, 2),
            "message": "structured output ok",
            "requestKind": "structured_output",
            "resolvedEndpoint": str(meta.get("base_url") or ""),
        }

    def _test_multimodal_capability(self, *, model_id: str, meta: Dict[str, Any]) -> Dict[str, Any]:
        started = time.perf_counter()
        client = llm_factory.create_chat_model(
            model_id,
            temperature=0,
            max_tokens=512,
            streaming=False,
            _role="connection_test_multimodal",
        )
        content = build_multimodal_content(
            prompt="Describe this image in one short word.",
            media_url=self._TEST_IMAGE_DATA_URL,
            mime_type="image/png",
            api_standard=str(meta.get("api_standard") or "openai"),
            transport_mode="inline_base64_image",
        )
        response = client.invoke([HumanMessage(content=content)])
        preview = _extract_text_preview(response)
        reasoning_signal = _extract_reasoning_signal(response)
        if not preview:
            # Some reasoning-heavy multimodal providers return only reasoning tokens
            # through the LangChain adapter when the underlying HTTP response is still successful.
            if reasoning_signal["reasoningTokens"] > 0:
                preview = reasoning_signal["reasoningPreview"] or "multimodal reasoning-only response"
            else:
                raise RuntimeError("多模态调用返回为空内容，未产出可验证正文。")
        latency_ms = (time.perf_counter() - started) * 1000
        return {
            "latencyMs": round(latency_ms, 2),
            "message": preview,
            "requestKind": "multimodal_reasoning_only" if not _extract_text_preview(response) else "multimodal",
            "resolvedEndpoint": str(meta.get("base_url") or ""),
            "finishReason": reasoning_signal["finishReason"],
            "reasoningTokens": reasoning_signal["reasoningTokens"],
        }

    def _protocol_skip(self, *, name: str, meta: Dict[str, Any]) -> Dict[str, Any] | None:
        api_standard = str(meta.get("api_standard") or "openai").strip().lower()
        base_url = str(meta.get("base_url") or "").strip().lower().rstrip("/")
        if name == "multimodal" and api_standard == "anthropic" and base_url.startswith("https://api.deepseek.com/anthropic"):
            return {
                "status": "not_supported_by_protocol",
                "reason": "anthropic_compat_image_input_unsupported",
            }
        return None

    def _is_basic_connection_probe_only(self, meta: Dict[str, Any]) -> bool:
        provider_record = dict(meta.get("provider_record") or {})
        provider_adapter = str(meta.get("provider_adapter") or "").strip().lower()
        oauth_preset = str(provider_record.get("oauth_preset") or meta.get("oauth_preset") or "").strip().lower()
        oauth_flavor = str(meta.get("oauth_flavor") or provider_record.get("oauth_flavor") or "").strip().lower()
        provider_type = str(provider_record.get("type") or "").strip().upper()
        return (
            provider_adapter == "gemini-cli-runtime"
            or oauth_preset in {"geminicli", "gemini_cli"}
            or oauth_flavor in {"geminicli", "gemini_cli"}
            or provider_type == "PLATFORM"
        )

    def _basic_probe_only_capability_checks(self, reason: str) -> Dict[str, Any]:
        skipped = {"status": "skipped", "reason": reason}
        return {
            "streaming": dict(skipped),
            "toolCalling": dict(skipped),
            "structuredOutput": dict(skipped),
            "multimodal": dict(skipped),
        }

    def _run_capability_checks(self, *, model_id: str, meta: Dict[str, Any]) -> Dict[str, Any]:
        matrix = dict(meta.get("effective_capability_matrix") or {})
        checks: Dict[str, Any] = {}
        failures: list[str] = []

        def _capture(name: str, enabled: bool, probe) -> None:
            protocol_skip = self._protocol_skip(name=name, meta=meta)
            if protocol_skip is not None:
                checks[name] = protocol_skip
                return
            if not enabled:
                checks[name] = {"status": "skipped"}
                return
            try:
                checks[name] = {"status": "passed", **probe()}
            except Exception as exc:
                checks[name] = {"status": "failed", "error": str(exc)}
                failures.append(f"{name}: {exc}")

        _capture("streaming", bool(matrix.get("supports_streaming")), lambda: self._test_streaming_capability(model_id=model_id, meta=meta))
        _capture(
            "toolCalling",
            bool(matrix.get("supports_native_tools") or matrix.get("supports_prompt_emulated_tools")),
            lambda: self._test_tool_calling_capability(model_id=model_id, meta=meta),
        )
        _capture(
            "structuredOutput",
            bool(matrix.get("supports_native_structured_output") or matrix.get("supports_prompt_fallback_structured_output")),
            lambda: self._test_structured_output_capability(model_id=model_id, meta=meta),
        )
        _capture("multimodal", bool(matrix.get("supports_multimodal")), lambda: self._test_multimodal_capability(model_id=model_id, meta=meta))

        if failures:
            raise RuntimeError("; ".join(failures))
        return checks

    def _skipped_runtime_capability_checks(self, reason: str) -> Dict[str, Any]:
        skipped = {"status": "skipped", "reason": reason}
        return {
            "streaming": dict(skipped),
            "toolCalling": dict(skipped),
            "structuredOutput": dict(skipped),
            "multimodal": dict(skipped),
        }

    def _test_embedding_model(self, *, model_id: str, meta: Dict[str, Any]) -> Dict[str, Any]:
        endpoint = (meta.get("base_url") or "https://api.openai.com/v1").rstrip("/") + "/embeddings"
        headers = {
            "Authorization": f"Bearer {meta.get('api_key') or ''}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model_id,
            "input": "connection test",
            "encoding_format": "float",
        }
        started = time.perf_counter()
        response = requests.post(endpoint, json=payload, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()
        embedding_count = len((data.get("data") or [{}])[0].get("embedding") or [])
        latency_ms = (time.perf_counter() - started) * 1000
        return {
            "latencyMs": round(latency_ms, 2),
            "message": f"Embedding 返回 {embedding_count} 维向量",
            "requestKind": "embedding",
            "resolvedEndpoint": endpoint,
        }

    def _test_reranker_model(self, *, model_id: str, meta: Dict[str, Any]) -> Dict[str, Any]:
        api_flavor = normalize_rerank_api_flavor(meta.get("rerank_api_flavor"))
        endpoints = build_rerank_endpoint_candidates(str(meta.get("base_url") or "https://api.siliconflow.cn/v1"), api_flavor)
        headers = {
            "Authorization": f"Bearer {meta.get('api_key') or ''}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model_id,
            "query": "connection test",
            "documents": ["connection test", "fallback document"],
            "top_n": 1,
            "return_documents": True,
        }
        started = time.perf_counter()
        resolved_endpoint = endpoints[0] if endpoints else ""
        results = []
        last_error: tuple[int, str, str] | None = None
        for endpoint in endpoints:
            resolved_endpoint = endpoint
            response = requests.post(endpoint, json=payload, headers=headers, timeout=30)
            if response.status_code == 200:
                results = parse_rerank_response_payload(response.json(), payload["documents"])
                break
            last_error = (response.status_code, response.text, endpoint)
        else:
            status_code, error_text, failed_endpoint = last_error or (500, "Unknown rerank error", resolved_endpoint)
            raise RuntimeError(f"Rerank request failed ({status_code}) on {failed_endpoint}: {error_text}")
        top_score = 0.0
        if results:
            top_score = float((results[0] or {}).get("relevance_score") or 0.0)
        latency_ms = (time.perf_counter() - started) * 1000
        return {
            "latencyMs": round(latency_ms, 2),
            "message": f"Rerank 返回 {len(results)} 条结果，最高分 {top_score:.4f}",
            "requestKind": "reranker",
            "resolvedEndpoint": resolved_endpoint,
            "rerankApiFlavor": api_flavor,
        }

    def _test_media_generation_provider(self, *, model_id: str, meta: Dict[str, Any]) -> Dict[str, Any]:
        provider_record = dict(meta.get("provider_record") or {})
        api_standard = str(meta.get("api_standard") or provider_record.get("apiStandard") or provider_record.get("api_standard") or "").strip().lower()
        provider_kind = str(provider_record.get("providerKind") or provider_record.get("provider_kind") or "").strip().lower()
        model_record = dict(meta.get("model_record") or {})
        model_type = str(model_record.get("type") or "").strip().upper()
        base_url = str(meta.get("base_url") or provider_record.get("baseUrl") or provider_record.get("base_url") or "").strip().rstrip("/")
        if not base_url:
            raise RuntimeError("媒体 Provider 缺少 baseURL，无法执行健康探测。")
        started = time.perf_counter()
        if api_standard == "comfyui":
            endpoint = f"{base_url}/object_info"
            response = requests.get(endpoint, timeout=15)
            response.raise_for_status()
            payload = response.json()
            node_count = len(payload) if isinstance(payload, dict) else 0
            checkpoint_inputs = (
                ((payload or {}).get("CheckpointLoaderSimple") or {})
                .get("input", {})
                .get("required", {})
                .get("ckpt_name")
                if isinstance(payload, dict)
                else None
            )
            checkpoint_count = len(checkpoint_inputs[0]) if isinstance(checkpoint_inputs, list) and checkpoint_inputs else 0
            latency_ms = (time.perf_counter() - started) * 1000
            return {
                "latencyMs": round(latency_ms, 2),
                "message": f"ComfyUI 节点 {node_count} 个，checkpoint {checkpoint_count} 个",
                "requestKind": "media_generation_probe",
                "resolvedEndpoint": endpoint,
                "nodeCount": node_count,
                "checkpointCount": checkpoint_count,
            }
        if model_type in {"VOICE", "AUDIO"}:
            raise RuntimeError(
                "not_supported_by_adapter: 该语音模型已可由模型列表发现，但 V8 尚未确认可用音频生成 endpoint；"
                "需要接入专用 voice/TTS adapter 后再执行连接测试。"
            )
        if model_type == "MUSIC":
            raise RuntimeError(
                "not_supported_by_adapter: 该音乐模型已登记为 Creative Media music/cue 能力，"
                "当前不走旧 MusicTrack，也未接真实音乐生成 adapter。"
            )
        if model_type in {"IMAGE", "VIDEO", "WORKFLOW", "MODEL3D", "MEDIA"} or provider_kind == "media_generation":
            raise RuntimeError(
                f"not_supported_by_adapter: {model_type or 'MEDIA'} 模型需要对应 Creative Media adapter/live smoke；"
                "通用连接测试不会伪装成生成成功。"
            )
        raise RuntimeError(f"not_supported_by_adapter: 暂无该模型类型的连接测试：{api_standard or provider_kind or model_id}")

    def test_model_connection(self, *, model_id: str, provider_id: str = "", model_ref: str = "") -> Dict[str, Any]:
        runtime_model_id = str(model_ref or (make_model_ref(provider_id, model_id) if provider_id else model_id) or "").strip()
        meta = self._resolve_metadata(runtime_model_id, provider_id=provider_id)
        wire_model_id = str(meta.get("model_id") or model_id or runtime_model_id)
        model_ref = str(meta.get("model_ref") or runtime_model_id)
        provider_id = str(meta.get("provider_id") or "unknown")
        provider_name = str(meta.get("provider_name") or provider_id)
        capability_class = str(meta.get("capability_class") or "")
        model_type = str((meta.get("model_record") or {}).get("type") or "TEXT").upper()
        effective_capability_matrix = dict(meta.get("effective_capability_matrix") or {})
        runtime_ready = bool(meta.get("runtime_ready", True))
        runtime_unsupported_reason = str(meta.get("runtime_unsupported_reason") or "")
        capability_probe = self._probe_local_capability(model_id=wire_model_id, meta=meta)
        provider_preset = self._resolve_local_backend_preset(meta) if str((meta.get("provider_record") or {}).get("type") or "").upper() == "LOCAL" else ""
        provider_adapter = str(meta.get("provider_adapter") or "")
        tool_calling_mode = "native" if bool(effective_capability_matrix.get("supports_native_tools")) else "prompt_emulated" if bool(effective_capability_matrix.get("supports_prompt_emulated_tools")) else "unsupported"
        structured_output_mode = "native" if bool(effective_capability_matrix.get("supports_native_structured_output")) else "prompt_fallback" if bool(effective_capability_matrix.get("supports_prompt_fallback_structured_output")) else "unsupported"
        stream_mode = "native" if bool(effective_capability_matrix.get("supports_streaming")) else "unsupported"
        started = time.perf_counter()
        capability_checks: Dict[str, Any] = {}

        try:
            if meta.get("oauth_error"):
                raise RuntimeError(str(meta["oauth_error"]))
            if capability_class == "embedding" or model_type == "EMBEDDING":
                result = self._test_embedding_model(model_id=wire_model_id, meta=meta)
            elif capability_class == "reranker" or model_type in {"RERANK", "RERANKER"}:
                result = self._test_reranker_model(model_id=wire_model_id, meta=meta)
            elif capability_class == "media_generation" or model_type in {"MEDIA", "IMAGE", "VIDEO", "AUDIO", "VOICE", "MUSIC", "WORKFLOW", "MODEL3D"}:
                result = self._test_media_generation_provider(model_id=wire_model_id, meta=meta)
                capability_checks = self._skipped_runtime_capability_checks("media_generation_provider_probe_only")
            else:
                result = self._test_chat_model(model_id=runtime_model_id, meta=meta)
                if runtime_ready and self._is_basic_connection_probe_only(meta):
                    capability_checks = self._basic_probe_only_capability_checks("basic_connection_probe_only_for_oauth_quota_safety")
                elif runtime_ready:
                    capability_checks = self._run_capability_checks(model_id=runtime_model_id, meta=meta)
                else:
                    capability_checks = self._skipped_runtime_capability_checks(runtime_unsupported_reason or "runtime_not_ready")

            observed_tool_mode = str(((capability_checks.get("toolCalling") or {}).get("toolCallingMode") or "")).strip()
            observed_structured_mode = str(((capability_checks.get("structuredOutput") or {}).get("structuredOutputMode") or "")).strip()
            if observed_tool_mode:
                tool_calling_mode = observed_tool_mode
            if observed_structured_mode:
                structured_output_mode = observed_structured_mode
            degrade_applied = tool_calling_mode != "native" or structured_output_mode != "native"

            self._record_health(
                provider_id=provider_id,
                provider_name=provider_name,
                model_id=wire_model_id,
                status="healthy",
                latency_ms=float(result["latencyMs"]),
                detail={
                    "requestKind": result["requestKind"],
                    "capabilityClass": capability_class,
                    "source": "manual_connection_test",
                    "capabilityProbe": capability_probe,
                    "providerPreset": provider_preset,
                    "resolvedEndpoint": result.get("resolvedEndpoint") or (capability_probe or {}).get("resolvedEndpoint"),
                    "runtimeMetadata": result.get("runtimeMetadata"),
                    "projectId": result.get("projectId"),
                    "effectiveCapabilityMatrix": effective_capability_matrix,
                    "capabilityChecks": capability_checks,
                    "providerAdapter": provider_adapter,
                    "toolCallingMode": tool_calling_mode,
                    "structuredOutputMode": structured_output_mode,
                    "streamMode": stream_mode,
                    "degradeApplied": degrade_applied,
                    "runtimeReady": runtime_ready,
                    "runtimeUnsupportedReason": runtime_unsupported_reason,
                    "modelRef": model_ref,
                },
            )
            return {
                "ok": True,
                "modelId": wire_model_id,
                "modelRef": model_ref,
                "providerId": provider_id,
                "providerName": provider_name,
                "capabilityClass": capability_class,
                "effectiveCapabilityMatrix": effective_capability_matrix,
                "capabilityChecks": capability_checks,
                "providerAdapter": provider_adapter,
                "toolCallingMode": tool_calling_mode,
                "structuredOutputMode": structured_output_mode,
                "streamMode": stream_mode,
                "degradeApplied": degrade_applied,
                "providerPreset": provider_preset,
                "resolvedEndpoint": result.get("resolvedEndpoint") or (capability_probe or {}).get("resolvedEndpoint"),
                "capabilityProbe": capability_probe,
                "runtimeReady": runtime_ready,
                "runtimeUnsupportedReason": runtime_unsupported_reason,
                **result,
            }
        except Exception as exc:
            normalized = normalize_provider_error(exc, provider=provider_name, model=wire_model_id)
            latency_ms = round((time.perf_counter() - started) * 1000, 2)
            self._record_health(
                provider_id=provider_id,
                provider_name=provider_name,
                model_id=wire_model_id,
                status="failed",
                latency_ms=float(latency_ms),
                error_code=normalized["code"],
                error_message=normalized["message"],
                detail={
                    "requestKind": "connection_test",
                    "capabilityClass": capability_class,
                    "source": "manual_connection_test",
                    "capabilityProbe": capability_probe,
                    "providerPreset": provider_preset,
                    "effectiveCapabilityMatrix": effective_capability_matrix,
                    "capabilityChecks": capability_checks,
                    "providerAdapter": provider_adapter,
                    "toolCallingMode": tool_calling_mode,
                    "structuredOutputMode": structured_output_mode,
                    "streamMode": stream_mode,
                    "degradeApplied": False,
                    "runtimeReady": runtime_ready,
                    "runtimeUnsupportedReason": runtime_unsupported_reason,
                    "modelRef": model_ref,
                },
            )
            return {
                "ok": False,
                "modelId": wire_model_id,
                "modelRef": model_ref,
                "providerId": provider_id,
                "providerName": provider_name,
                "capabilityClass": capability_class,
                "effectiveCapabilityMatrix": effective_capability_matrix,
                "capabilityChecks": capability_checks,
                "providerAdapter": provider_adapter,
                "toolCallingMode": tool_calling_mode,
                "structuredOutputMode": structured_output_mode,
                "streamMode": stream_mode,
                "degradeApplied": False,
                "latencyMs": latency_ms,
                "providerPreset": provider_preset,
                "resolvedEndpoint": (capability_probe or {}).get("resolvedEndpoint") or str(meta.get("base_url") or ""),
                "capabilityProbe": capability_probe,
                "runtimeReady": runtime_ready,
                "runtimeUnsupportedReason": runtime_unsupported_reason,
                "error": normalized,
            }


model_connection_tester = ModelConnectionTester()
