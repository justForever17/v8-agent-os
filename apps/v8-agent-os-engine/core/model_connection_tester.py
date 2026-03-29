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
from core.llm_factory import llm_factory
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
    def _probe_local_capability(self, *, model_id: str, meta: Dict[str, Any]) -> Dict[str, Any] | None:
        provider_record = dict(meta.get("provider_record") or {})
        model_record = dict(meta.get("model_record") or {})
        provider_type = str(provider_record.get("type") or "API").upper()
        model_type = str(model_record.get("type") or "TEXT").upper()
        capability_class = str(meta.get("capability_class") or "")
        if provider_type != "LOCAL":
            return None
        if capability_class != "vision_multimodal" and model_type != "MULTIMODAL":
            return None
        return probe_local_multimodal_capability(
            model_id=model_id,
            provider_type=provider_type,
            base_url=str(meta.get("base_url") or ""),
            api_key=str(meta.get("api_key") or ""),
        )

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
        }

    def _test_reranker_model(self, *, model_id: str, meta: Dict[str, Any]) -> Dict[str, Any]:
        endpoint = (meta.get("base_url") or "https://api.siliconflow.cn/v1").rstrip("/") + "/rerank"
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
        response = requests.post(endpoint, json=payload, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()
        results = data.get("results") or []
        top_score = 0.0
        if results:
            top_score = float((results[0] or {}).get("relevance_score") or 0.0)
        latency_ms = (time.perf_counter() - started) * 1000
        return {
            "latencyMs": round(latency_ms, 2),
            "message": f"Rerank 返回 {len(results)} 条结果，最高分 {top_score:.4f}",
            "requestKind": "reranker",
        }

    def test_model_connection(self, *, model_id: str) -> Dict[str, Any]:
        meta = self._resolve_metadata(model_id)
        provider_id = str(meta.get("provider_id") or "unknown")
        provider_name = str(meta.get("provider_name") or provider_id)
        capability_class = str(meta.get("capability_class") or "")
        model_type = str((meta.get("model_record") or {}).get("type") or "TEXT").upper()
        capability_probe = self._probe_local_capability(model_id=model_id, meta=meta)
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
                },
            )
            return {
                "ok": False,
                "modelId": model_id,
                "providerId": provider_id,
                "providerName": provider_name,
                "capabilityClass": capability_class,
                "latencyMs": latency_ms,
                "capabilityProbe": capability_probe,
                "error": normalized,
            }


model_connection_tester = ModelConnectionTester()
