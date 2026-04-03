from __future__ import annotations

import time
import uuid
from typing import Any, Dict

import requests
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

try:
    from langchain_google_genai import ChatGoogleGenerativeAI
except ImportError:  # pragma: no cover - optional dependency in dev env
    ChatGoogleGenerativeAI = None

from core.local_visual_support import probe_local_multimodal_capability
from core.database import db
from core.gemini_cli_oauth import probe_gemini_cli_connection
from core.llm_factory import (
    build_rerank_endpoint_candidates,
    llm_factory,
    normalize_rerank_api_flavor,
    parse_rerank_response_payload,
)
from core.model_control_plane import model_control_plane
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


class ModelConnectionTester:
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

    def _resolve_metadata(self, model_id: str) -> Dict[str, Any]:
        meta = llm_factory._resolve_model_metadata(model_id)  # noqa: SLF001 - internal service helper
        record = model_control_plane.get_model_record(model_id)
        if not meta.get("is_found") or not record:
            raise ValueError(f"模型 {model_id} 未在 models.json 中注册。")
        return {
            **meta,
            "provider_record": dict(record.get("provider") or {}),
            "model_record": dict(record.get("model") or {}),
        }

    def _test_chat_model(self, *, model_id: str, meta: Dict[str, Any]) -> Dict[str, Any]:
        api_standard = str(meta.get("api_standard") or "openai").lower()
        started = time.perf_counter()

        if api_standard in {"google", "gemini"} and str(meta.get("oauth_flavor") or "") == "gemini_cli":
            token = str(meta.get("oauth_access_token") or meta.get("api_key") or "")
            result = probe_gemini_cli_connection(
                access_token=token,
                base_url=str(meta.get("base_url") or ""),
                project_id=str(meta.get("project_id") or ""),
            )
            if not result.get("ok"):
                raise RuntimeError(str(result.get("message") or "Gemini CLI OAuth 连接失败"))
            return {
                "latencyMs": result.get("latencyMs", 0.0),
                "message": str(result.get("message") or "Gemini CLI OAuth 可用"),
                "requestKind": str(result.get("requestKind") or "gemini_cli_oauth"),
                "resolvedEndpoint": str(meta.get("base_url") or ""),
                "projectId": str(result.get("projectId") or ""),
                "runtimeMetadata": dict(result.get("metadata") or {}),
            }

        if api_standard == "anthropic":
            client = ChatAnthropic(
                model=model_id,
                api_key=meta.get("api_key") or "sk-dummy",
                base_url=meta.get("base_url") or None,
                max_tokens=int(meta.get("global_max_tokens") or 16),
                temperature=0,
            )
        elif api_standard in {"google", "gemini"}:
            if ChatGoogleGenerativeAI is None:
                raise RuntimeError("langchain-google-genai 未安装，无法测试 Gemini 连接。")
            client = ChatGoogleGenerativeAI(
                model=model_id,
                google_api_key=meta.get("api_key") or "",
                max_output_tokens=int(meta.get("global_max_tokens") or 16),
                temperature=0,
            )
        else:
            client = ChatOpenAI(
                model=model_id,
                api_key=meta.get("api_key") or "sk-dummy",
                base_url=meta.get("base_url") or None,
                max_tokens=int(meta.get("global_max_tokens") or 16),
                temperature=0,
            )

        response = client.invoke([HumanMessage(content="Reply with exact string: OK")])
        latency_ms = (time.perf_counter() - started) * 1000
        preview = _extract_text_preview(response) or "连接成功"
        return {
            "latencyMs": round(latency_ms, 2),
            "message": preview,
            "requestKind": "chat_completion",
            "resolvedEndpoint": str(meta.get("base_url") or ""),
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

    def test_model_connection(self, *, model_id: str) -> Dict[str, Any]:
        meta = self._resolve_metadata(model_id)
        provider_id = str(meta.get("provider_id") or "unknown")
        provider_name = str(meta.get("provider_name") or provider_id)
        capability_class = str(meta.get("capability_class") or "")
        model_type = str((meta.get("model_record") or {}).get("type") or "TEXT").upper()
        capability_probe = self._probe_local_capability(model_id=model_id, meta=meta)
        provider_preset = self._resolve_local_backend_preset(meta) if str((meta.get("provider_record") or {}).get("type") or "").upper() == "LOCAL" else ""
        started = time.perf_counter()

        try:
            if meta.get("oauth_error"):
                raise RuntimeError(str(meta["oauth_error"]))
            if capability_class == "embedding" or model_type == "EMBEDDING":
                result = self._test_embedding_model(model_id=model_id, meta=meta)
            elif capability_class == "reranker" or model_type in {"RERANK", "RERANKER"}:
                result = self._test_reranker_model(model_id=model_id, meta=meta)
            else:
                result = self._test_chat_model(model_id=model_id, meta=meta)

            self._record_health(
                provider_id=provider_id,
                provider_name=provider_name,
                model_id=model_id,
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
                },
            )
            return {
                "ok": True,
                "modelId": model_id,
                "providerId": provider_id,
                "providerName": provider_name,
                "capabilityClass": capability_class,
                "providerPreset": provider_preset,
                "resolvedEndpoint": result.get("resolvedEndpoint") or (capability_probe or {}).get("resolvedEndpoint"),
                "capabilityProbe": capability_probe,
                **result,
            }
        except Exception as exc:
            normalized = normalize_provider_error(exc, provider=provider_name, model=model_id)
            latency_ms = round((time.perf_counter() - started) * 1000, 2)
            self._record_health(
                provider_id=provider_id,
                provider_name=provider_name,
                model_id=model_id,
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
                },
            )
            return {
                "ok": False,
                "modelId": model_id,
                "providerId": provider_id,
                "providerName": provider_name,
                "capabilityClass": capability_class,
                "latencyMs": latency_ms,
                "providerPreset": provider_preset,
                "resolvedEndpoint": (capability_probe or {}).get("resolvedEndpoint") or str(meta.get("base_url") or ""),
                "capabilityProbe": capability_probe,
                "error": normalized,
            }


model_connection_tester = ModelConnectionTester()
