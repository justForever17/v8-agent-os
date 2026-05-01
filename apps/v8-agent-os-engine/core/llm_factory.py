from typing import Dict, Any, Optional, Type, List
import time
import re

from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic
try:
    from langchain_google_genai import ChatGoogleGenerativeAI
except ImportError:  # pragma: no cover - optional dependency in dev env
    ChatGoogleGenerativeAI = None

# Forward-declare base classes from vector_store to prevent circular imports if necessary, 
# or import them cleanly if they are standalone
# In v8chat, memory_router defined OpenAICompatibleEmbedding and RestReranker.
# We will bring those standard wrapper classes here since this is the global factory.

from core.storage import storage
from core.llm_chat_adapter import V8ChatModelAdapter
from core.gemini_cli_runtime import GeminiCliRuntimeModel
from core.openai_codex_runtime import OpenAICodexResponsesRuntimeModel
from core.model_capability_matrix import build_effective_capability_matrix, normalize_capability_metadata
from core.llm_exceptions import V8LLMCapabilityMismatchError, raise_as_v8_llm_error
from core.provider_runtime_profiles import (
    is_anthropic_compat_provider,
    is_codex_oauth_provider,
    is_gemini_cli_provider,
    resolve_provider_adapter,
    runtime_readiness_for_provider,
)
from core.model_budget_service import model_budget_service
from core.model_control_plane import model_control_plane, normalize_config_temperature
from core.model_telemetry import model_telemetry_service
from core.oauth_credentials import resolve_oauth_reference, resolve_provider_oauth_credential
from core.provider_compatibility import normalize_provider_error
from erc.runtime_context import get_runtime_context
from langchain_core.embeddings import Embeddings

_EMBEDDING_OBSERVED_LIMITS: Dict[str, int] = {}
_TOKEN_LIMIT_RE = re.compile(r"(?:maximum token length|max(?:imum)?(?: input)? tokens?|token limit)[^\d]{0,40}(\d{3,7})", re.IGNORECASE)


def _extract_observed_token_limit(error_text: str) -> int | None:
    text = str(error_text or "")
    matches = [int(match.group(1)) for match in _TOKEN_LIMIT_RE.finditer(text) if match.group(1).isdigit()]
    if not matches:
        return None
    return min(matches)

# Re-implementing the embedding and reranker wrappers cleanly
try:
    from core.vector_store import BaseEmbedding, BaseReranker
except ImportError:
    class BaseEmbedding(Embeddings):
        pass
    class BaseReranker:
        pass


def normalize_rerank_api_flavor(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"vllm", "nexa"}:
        return normalized
    return "generic"


def _join_endpoint(base_url: str, suffix: str) -> str:
    root = (base_url or "").rstrip("/")
    tail = suffix.lstrip("/")
    if not root:
        return tail
    if root.endswith(f"/{tail}") or root == tail:
        return root
    if tail.startswith("v1/") and root.endswith("/v1"):
        return f"{root}/{tail.removeprefix('v1/')}"
    return f"{root}/{tail}"


def build_rerank_endpoint_candidates(base_url: str, api_flavor: Any) -> List[str]:
    flavor = normalize_rerank_api_flavor(api_flavor)
    root = (base_url or "https://api.siliconflow.cn/v1").rstrip("/")
    if flavor == "vllm":
        paths = ["v1/rerank", "rerank"]
    elif flavor == "nexa":
        paths = ["v1/reranking", "v1/rerank", "rerank"]
    else:
        paths = ["rerank"]
    candidates: List[str] = []
    for path in paths:
        endpoint = _join_endpoint(root, path)
        if endpoint not in candidates:
            candidates.append(endpoint)
    return candidates


def parse_rerank_response_payload(payload: Dict[str, Any], documents: List[str]) -> List[Dict[str, Any]]:
    rows = payload.get("results") or payload.get("data") or []
    if not isinstance(rows, list):
        return []

    parsed: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue

        index = row.get("index")
        if not isinstance(index, int):
            index = None

        document_value = row.get("document")
        if isinstance(document_value, dict):
            document_text = (
                document_value.get("text")
                or document_value.get("content")
                or document_value.get("document")
                or document_value.get("page_content")
                or ""
            )
        elif isinstance(document_value, str):
            document_text = document_value
        else:
            document_text = str(row.get("text") or row.get("content") or "")

        if not document_text and index is not None and 0 <= index < len(documents):
            document_text = documents[index]

        raw_score = (
            row.get("relevance_score")
            or row.get("relevanceScore")
            or row.get("score")
            or row.get("similarity")
            or 0.0
        )
        try:
            score = float(raw_score)
        except (TypeError, ValueError):
            score = 0.0

        parsed.append(
            {
                "index": index,
                "document": str(document_text or ""),
                "relevance_score": score,
            }
        )
    return parsed


class OpenAICompatibleEmbedding(BaseEmbedding):
    def __init__(self, model_name: str, api_key: str, base_url: str = None, max_tokens: int = None, provider_id: str = "", provider_name: str = "", role: str = "embedding", capability_class: str = "embedding"):
        self.model_name = model_name
        self.api_key = api_key
        self.max_tokens = int(max_tokens) if max_tokens else None
        self.endpoint = base_url.rstrip("/") + "/embeddings" if base_url else "https://api.openai.com/v1/embeddings"
        self.provider_id = provider_id
        self.provider_name = provider_name or provider_id
        self.role = role
        self.capability_class = capability_class

    def _observed_limit_key(self) -> str:
        return "|".join([str(self.provider_id or ""), str(self.endpoint or ""), str(self.model_name or ""), str(self.role or "embedding")])

    def _effective_max_tokens(self) -> int | None:
        observed = _EMBEDDING_OBSERVED_LIMITS.get(self._observed_limit_key())
        if observed and observed > 0:
            if self.max_tokens:
                return min(int(self.max_tokens), int(observed))
            return int(observed)
        return int(self.max_tokens) if self.max_tokens else None
    
    def _truncate_text(self, text: str, *, token_limit: int | None = None) -> str:
        """Truncate text to stay within model's context window. Rough estimate: 1 token ≈ 3 chars for CJK."""
        limit = int(token_limit or self._effective_max_tokens() or 0)
        if not limit or not text:
            return text
        max_chars = int(limit * 2.5 * 0.9)
        if len(text) > max_chars:
            print(f"[Embedding] ⚠️ Truncating text from {len(text)} to {max_chars} chars (model limit: {limit} tokens)")
            return text[:max_chars]
        return text
        
    def _call_api(self, texts: list[str]) -> list[list[float]]:
        import requests
        started = time.perf_counter()
        if not texts:
            return []
        ctx = get_runtime_context()
        model_budget_service.enforce_or_raise(
            config=model_control_plane.get_config(),
            run_id=ctx.get("run_id"),
            project_id=ctx.get("project_id"),
            role=self.role,
            capability_class=self.capability_class,
            model_id=self.model_name,
        )
        original_texts = list(texts)
        texts = [self._truncate_text(t) for t in original_texts]
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": self.model_name,
            "input": texts,
            "encoding_format": "float"
        }
        # In an async context, this synchronous request might block. 
        # But for Langchain compatibility, standard embed_documents often remains sync.
        res = requests.post(self.endpoint, json=payload, headers=headers)
        if res.status_code != 200:
            observed_limit = _extract_observed_token_limit(res.text)
            current_limit = self._effective_max_tokens()
            if observed_limit and observed_limit > 0 and (not current_limit or observed_limit < int(current_limit)):
                _EMBEDDING_OBSERVED_LIMITS[self._observed_limit_key()] = int(observed_limit)
                print(f"[Embedding] ℹ️ Observed provider input token limit {observed_limit}; retrying with smaller truncation.")
                retry_texts = [self._truncate_text(t, token_limit=int(observed_limit)) for t in original_texts]
                retry_payload = dict(payload)
                retry_payload["input"] = retry_texts
                retry_started = time.perf_counter()
                retry_res = requests.post(self.endpoint, json=retry_payload, headers=headers)
                if retry_res.status_code == 200:
                    res = retry_res
                    texts = retry_texts
                else:
                    res = retry_res
                    started = retry_started
            if res.status_code == 200:
                pass
            else:
                print(f"[Embedding Error] {res.status_code}: {res.text}")
                model_telemetry_service.record_aux_model_invocation(
                    model_id=self.model_name,
                    provider_id=self.provider_id,
                    provider_name=self.provider_name,
                    role=self.role,
                    capability_class=self.capability_class,
                    request_kind="embedding",
                    latency_ms=(time.perf_counter() - started) * 1000,
                    status="failed",
                    error_code=str(res.status_code),
                    error_message=res.text,
                    metadata={
                        "documents": len(texts),
                        "observedInputTokenLimit": observed_limit,
                    },
                )
        if res.status_code != 200:
            res.raise_for_status()
        
        data = res.json().get("data", [])
        data = sorted(data, key=lambda x: x.get("index", 0))
        model_telemetry_service.record_aux_model_invocation(
            model_id=self.model_name,
            provider_id=self.provider_id,
            provider_name=self.provider_name,
            role=self.role,
            capability_class=self.capability_class,
            request_kind="embedding",
            latency_ms=(time.perf_counter() - started) * 1000,
            status="completed",
            metadata={
                "documents": len(texts),
                "dimensions": len(data[0]["embedding"]) if data else 0,
                "observedInputTokenLimit": _EMBEDDING_OBSERVED_LIMITS.get(self._observed_limit_key()),
            },
        )
        return [item["embedding"] for item in data]
        
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._call_api(texts)
        
    def embed_query(self, text: str) -> list[float]:
        return self._call_api([text])[0]


class RestReranker(BaseReranker):
    def __init__(self, model_name: str, api_key: str, base_url: str, max_tokens: int = None, provider_id: str = "", provider_name: str = "", role: str = "reranker", capability_class: str = "reranker", api_flavor: str = "generic"):
        self.model_name = model_name
        self.api_key = api_key
        self.max_tokens = int(max_tokens) if max_tokens else None
        self.api_flavor = normalize_rerank_api_flavor(api_flavor)
        self.endpoints = build_rerank_endpoint_candidates(base_url or "https://api.siliconflow.cn/v1", self.api_flavor)
        self.provider_id = provider_id
        self.provider_name = provider_name or provider_id
        self.role = role
        self.capability_class = capability_class
        
    def rerank(self, query: str, documents: list[str], top_k: int = 3) -> list[Dict[str, Any]]:
        import requests
        started = time.perf_counter()
        if not documents:
            return []
        ctx = get_runtime_context()
        model_budget_service.enforce_or_raise(
            config=model_control_plane.get_config(),
            run_id=ctx.get("run_id"),
            project_id=ctx.get("project_id"),
            role=self.role,
            capability_class=self.capability_class,
            model_id=self.model_name,
        )
            
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": self.model_name,
            "query": query,
            "documents": documents,
            "top_n": top_k,
            "return_documents": True
        }
        
        out: List[Dict[str, Any]] = []
        resolved_endpoint = self.endpoints[0] if self.endpoints else ""
        last_error: tuple[int, str, str] | None = None
        for endpoint in self.endpoints:
            resolved_endpoint = endpoint
            res = requests.post(endpoint, json=payload, headers=headers, timeout=30)
            if res.status_code == 200:
                out = parse_rerank_response_payload(res.json(), documents)
                break
            last_error = (res.status_code, res.text, endpoint)
        else:
            status_code, error_text, failed_endpoint = last_error or (500, "Unknown rerank error", resolved_endpoint)
            print(f"[Reranker Error] {status_code}: {error_text}")
            model_telemetry_service.record_aux_model_invocation(
                model_id=self.model_name,
                provider_id=self.provider_id,
                provider_name=self.provider_name,
                role=self.role,
                capability_class=self.capability_class,
                request_kind="reranker",
                latency_ms=(time.perf_counter() - started) * 1000,
                status="failed",
                error_code=str(status_code),
                error_message=error_text,
                metadata={"documents": len(documents), "top_k": top_k, "endpoint": failed_endpoint, "apiFlavor": self.api_flavor},
            )
            raise requests.HTTPError(f"Rerank request failed ({status_code}) on {failed_endpoint}: {error_text}")
        model_telemetry_service.record_aux_model_invocation(
            model_id=self.model_name,
            provider_id=self.provider_id,
            provider_name=self.provider_name,
            role=self.role,
            capability_class=self.capability_class,
            request_kind="reranker",
            latency_ms=(time.perf_counter() - started) * 1000,
            status="completed",
            metadata={"documents": len(documents), "top_k": top_k, "results": len(out), "endpoint": resolved_endpoint, "apiFlavor": self.api_flavor},
        )
        return out


class LLMFactory:
    """
    Centralized factory for resolving models.json provider logic and instantiating
    robust LLM, Embedding, and Reranker clients across all of v8chat.
    """
    
    @staticmethod
    def _is_gemini_cli_provider(*, api_standard: str, provider_config: Dict[str, Any], oauth_flavor: str = "") -> bool:
        return is_gemini_cli_provider(
            api_standard=api_standard,
            provider_config=provider_config,
            oauth_flavor=oauth_flavor,
        )

    @staticmethod
    def _is_anthropic_compat_provider(*, api_standard: str, base_url: str) -> bool:
        return is_anthropic_compat_provider(
            api_standard=api_standard,
            base_url=base_url,
        )

    @staticmethod
    def _resolve_api_key(raw_key: str) -> str:
        """Resolves OAuth file references in local paths"""
        if not raw_key:
            return ""
        resolved = resolve_oauth_reference(raw_key)
        return str(resolved.get("credential") or "")

    @classmethod
    def _resolve_model_metadata(cls, target_model_name: str) -> Dict[str, Any]:
        """
        Scans models.json to strictly retrieve:
        - base_url
        - api_key
        - provider_name
        - global model meta (temperature, maxTokens, contextWindow)
        Returns an empty map if model explicitly not found.
        """
        lookup = model_control_plane._resolve_model_lookup(target_model_name)  # noqa: SLF001 - precise modelRef compatibility
        record = lookup.get("record")
        if record:
            p_name = str(record.get("provider_id") or "")
            p_conf = dict(record.get("provider") or {})
            meta = dict(record.get("model") or {})
            upstream_model_id = str(record.get("model_id") or target_model_name)
            model_ref = str(record.get("model_ref") or "")
            api_standard = str(p_conf.get("api_standard", "openai") or "openai")
            capability_class = str(meta.get("capabilityClass") or "")

            oauth_resolution = resolve_provider_oauth_credential(
                provider_id=p_name,
                provider_config=p_conf,
            )
            t_api_key = str(oauth_resolution.get("credential") or "")
            t_base_url = p_conf.get("base_url", "")
            oauth_error = str(oauth_resolution.get("error") or "")
            oauth_flavor = str(oauth_resolution.get("oauthFlavor") or "")
            is_gemini_cli = cls._is_gemini_cli_provider(
                api_standard=api_standard,
                provider_config=p_conf,
                oauth_flavor=oauth_flavor,
            )
            is_anthropic_compat = cls._is_anthropic_compat_provider(
                api_standard=api_standard,
                base_url=t_base_url,
            )
            is_codex_oauth = is_codex_oauth_provider(
                api_standard=api_standard,
                provider_config=p_conf,
                oauth_flavor=oauth_flavor,
            )
            capabilities = dict(meta.get("capabilities") or {})
            if is_gemini_cli:
                capabilities.update(
                    {
                        "supportsNativeTools": False,
                        "supportsPromptEmulatedTools": True,
                        "supportsNativeStructuredOutput": False,
                        "supportsPromptFallbackStructuredOutput": True,
                    }
                )
            if is_anthropic_compat:
                capabilities.update(
                    {
                        "vision": False,
                        "supportsMultimodal": False,
                    }
                )
            if is_codex_oauth:
                capabilities.update(
                    {
                        "supportsNativeTools": False,
                        "supportsPromptEmulatedTools": True,
                        "supportsNativeStructuredOutput": False,
                        "supportsPromptFallbackStructuredOutput": True,
                        "supportsMultimodal": False,
                    }
                )
            runtime_ready, runtime_unsupported_reason = runtime_readiness_for_provider(
                provider_id=p_name,
                api_standard=api_standard,
                provider_config=p_conf,
                oauth_flavor=oauth_flavor,
                credential=t_api_key,
                oauth_path=str(oauth_resolution.get("oauthPath") or ""),
            )
            effective_matrix = build_effective_capability_matrix(
                capability_class=capability_class,
                capabilities=capabilities,
                api_standard=api_standard,
                runtime_ready=runtime_ready,
            )

            provider_adapter, provider_adapter_label = resolve_provider_adapter(
                api_standard=api_standard,
                provider_config=p_conf,
                oauth_flavor=oauth_flavor,
            )
            if not t_api_key and p_name.lower() == "qwen-oauth":
                from core.credential_sniffer import QwenCredentialSniffer
                local_token = QwenCredentialSniffer.get_qwen_token()
                if local_token:
                    t_api_key = local_token
                    if not t_base_url:
                        t_base_url = "https://portal.qwen.ai/v1"

            if not t_base_url:
                pl = p_name.lower()
                if pl == "deepseek":
                    t_base_url = "https://api.deepseek.com/v1"
                elif pl in ["qwen", "dashscope"]:
                    t_base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
                elif pl == "siliconflow":
                    t_base_url = "https://api.siliconflow.cn/v1"
                elif pl == "modelscope":
                    t_base_url = "https://api-inference.modelscope.cn/v1"
                elif pl == "qwen-oauth":
                    t_base_url = "https://portal.qwen.ai/v1"

            return {
                "is_found": True,
                "model_name": upstream_model_id,
                "model_id": upstream_model_id,
                "model_ref": model_ref,
                "provider_id": p_name,
                "provider_name": p_name,
                "provider_record": p_conf,
                "model_record": meta,
                "base_url": t_base_url,
                "api_key": t_api_key,
                "api_standard": api_standard,
                "api_version": p_conf.get("api_version") or p_conf.get("apiVersion") or "",
                "organization_id": p_conf.get("organization_id") or p_conf.get("organizationId") or "",
                "extra_headers": p_conf.get("extra_headers") or p_conf.get("extraHeaders") or {},
                "proxy": p_conf.get("proxy") or p_conf.get("openai_proxy") or p_conf.get("anthropic_proxy") or "",
                "ssl": p_conf.get("ssl") or {},
                "timeouts": p_conf.get("timeouts") or {},
                "local_backend_preset": p_conf.get("local_backend_preset") or p_conf.get("localBackendPreset") or "",
                "oauth_path": oauth_resolution.get("oauthPath") or "",
                "oauth_ref": oauth_resolution.get("oauthRef") or "",
                "credential_mode": oauth_resolution.get("credentialMode") or "",
                "oauth_error": oauth_error,
                "oauth_flavor": oauth_flavor,
                "oauth_access_token": oauth_resolution.get("accessToken") or "",
                "account_id": oauth_resolution.get("accountId") or "",
                "project_id": oauth_resolution.get("projectId") or "",
                "runtime_ready": runtime_ready,
                "runtime_unsupported_reason": runtime_unsupported_reason,
                "provider_adapter": provider_adapter,
                "provider_adapter_label": provider_adapter_label,
                "global_temperature": normalize_config_temperature(meta.get("temperature")),
                "global_max_tokens": meta.get("maxTokens"),
                "global_context_window": meta.get("contextWindow"),
                "rerank_api_flavor": normalize_rerank_api_flavor(meta.get("rerank_api_flavor") or meta.get("rerankApiFlavor")),
                "capabilities": capabilities,
                "capability_class": capability_class,
                "cost_per_input": meta.get("costPerInput"),
                "cost_per_output": meta.get("costPerOutput"),
                "tokenizer_family": meta.get("tokenizerFamily") or meta.get("tokenizer_family") or "",
                "effective_capability_matrix": effective_matrix,
                **normalize_capability_metadata(
                    capabilities,
                    capability_class=capability_class,
                    api_standard=api_standard,
                    runtime_ready=runtime_ready,
                ),
                "governance": record.get("governance", {}),
            }
        
        # Unmapped ad-hoc model names handling
        return {
            "is_found": False,
            "model_name": target_model_name,
            "lookup_status": lookup.get("status"),
            "lookup_matches": lookup.get("matches") or [],
        }

    @staticmethod
    def _extract_timeout(meta: Dict[str, Any], **kwargs) -> Any:
        explicit = kwargs.get("timeout")
        if explicit is not None:
            return explicit
        timeouts = dict(meta.get("timeouts") or {})
        return timeouts.get("request") or timeouts.get("read") or None

    @classmethod
    def _build_openai_kwargs(cls, model_id: str, meta: Dict[str, Any], **kwargs) -> Dict[str, Any]:
        final_kwargs: Dict[str, Any] = dict(model=model_id)

        if meta.get("base_url"):
            final_kwargs["base_url"] = meta["base_url"]
        if meta.get("organization_id"):
            final_kwargs["organization"] = meta["organization_id"]
        if meta.get("proxy"):
            final_kwargs["openai_proxy"] = meta["proxy"]
        if meta.get("extra_headers"):
            final_kwargs["default_headers"] = meta["extra_headers"]

        timeout = cls._extract_timeout(meta, **kwargs)
        if timeout is not None:
            final_kwargs["timeout"] = timeout

        final_kwargs["api_key"] = meta.get("api_key") or "sk-dummy"

        if "temperature" in kwargs and kwargs.get("temperature") is not None:
            final_kwargs["temperature"] = kwargs["temperature"]
        else:
            global_temperature = normalize_config_temperature(meta.get("global_temperature"))
            if global_temperature is not None:
                final_kwargs["temperature"] = global_temperature

        model_kwargs = dict(kwargs.get("model_kwargs") or {})
        if "max_tokens" in kwargs:
            final_kwargs["max_tokens"] = int(kwargs["max_tokens"])
        elif meta.get("global_max_tokens"):
            final_kwargs["max_tokens"] = int(meta["global_max_tokens"])

        if model_kwargs:
            final_kwargs["model_kwargs"] = model_kwargs

        for key, value in kwargs.items():
            if key not in {"temperature", "max_tokens", "base_url", "api_key", "model", "model_kwargs", "timeout"}:
                final_kwargs[key] = value

        return final_kwargs

    @classmethod
    def _build_anthropic_kwargs(cls, model_id: str, meta: Dict[str, Any], **kwargs) -> Dict[str, Any]:
        final_kwargs: Dict[str, Any] = {
            "model_name": model_id,
            "api_key": meta.get("api_key") or "sk-dummy",
        }

        if meta.get("base_url"):
            final_kwargs["base_url"] = meta["base_url"]
        if meta.get("proxy"):
            final_kwargs["anthropic_proxy"] = meta["proxy"]
        if meta.get("extra_headers"):
            final_kwargs["default_headers"] = meta["extra_headers"]

        timeout = cls._extract_timeout(meta, **kwargs)
        if timeout is not None:
            final_kwargs["timeout"] = timeout

        if "temperature" in kwargs and kwargs.get("temperature") is not None:
            final_kwargs["temperature"] = kwargs["temperature"]
        else:
            global_temperature = normalize_config_temperature(meta.get("global_temperature"))
            if global_temperature is not None:
                final_kwargs["temperature"] = global_temperature

        max_tokens = kwargs.get("max_tokens") or meta.get("global_max_tokens")
        if max_tokens:
            final_kwargs["max_tokens_to_sample"] = int(max_tokens)

        if kwargs.get("stop"):
            final_kwargs["stop"] = kwargs["stop"]

        model_kwargs = dict(kwargs.get("model_kwargs") or {})
        if model_kwargs:
            final_kwargs["model_kwargs"] = model_kwargs

        for key, value in kwargs.items():
            if key not in {"temperature", "max_tokens", "base_url", "api_key", "model", "model_kwargs", "stop", "timeout"}:
                final_kwargs[key] = value

        return final_kwargs

    @classmethod
    def _build_gemini_kwargs(cls, model_id: str, meta: Dict[str, Any], **kwargs) -> Dict[str, Any]:
        final_kwargs = {
            "model": model_id,
            "google_api_key": meta.get("api_key") or "",
        }
        timeout = cls._extract_timeout(meta, **kwargs)
        if timeout is not None:
            final_kwargs["timeout"] = timeout
        if "temperature" in kwargs and kwargs.get("temperature") is not None:
            final_kwargs["temperature"] = kwargs["temperature"]
        else:
            global_temperature = normalize_config_temperature(meta.get("global_temperature"))
            if global_temperature is not None:
                final_kwargs["temperature"] = global_temperature

        max_tokens = kwargs.get("max_tokens") or meta.get("global_max_tokens")
        if max_tokens:
            final_kwargs["max_output_tokens"] = int(max_tokens)

        for key, value in kwargs.items():
            if key not in {"temperature", "max_tokens", "base_url", "api_key", "model", "timeout"}:
                final_kwargs[key] = value
        return final_kwargs

    @staticmethod
    def _attach_telemetry(
        kwargs: Dict[str, Any],
        meta: Dict[str, Any],
        *,
        model_id: str,
        role: str = "",
        request_kind: str = "chat",
        capability_class_override: str = "",
    ) -> Dict[str, Any]:
        callbacks = list(kwargs.get("callbacks") or [])
        provider_adapter = str(meta.get("provider_adapter") or "").strip() or (
            "gemini"
            if str(meta.get("api_standard") or "openai").lower() in {"google", "gemini"}
            else "anthropic"
            if str(meta.get("api_standard") or "openai").lower() == "anthropic"
            else "openai-compatible"
        )
        effective_capability_matrix = dict(meta.get("effective_capability_matrix") or {})
        tool_calling_mode = "native" if bool(
            effective_capability_matrix.get("supports_native_tools") or meta.get("supports_native_tools", meta.get("supportsTools", True))
        ) else "prompt_emulated"
        structured_output_mode = "native" if bool(
            effective_capability_matrix.get("supports_native_structured_output")
            or meta.get("supports_native_structured_output", meta.get("supportsStructuredOutput", True))
        ) else "prompt_fallback"
        stream_mode = "native" if bool(
            effective_capability_matrix.get("supports_streaming", True)
        ) else "unsupported"
        callback_capability_class = str(capability_class_override or meta.get("capability_class") or "")
        callbacks.append(
            model_telemetry_service.build_chat_callback(
                model_id=model_id,
                provider_id=str(meta.get("provider_id") or meta.get("provider_name") or "unknown"),
                provider_name=str(meta.get("provider_name") or meta.get("provider_id") or "unknown"),
                role=role,
                capability_class=callback_capability_class,
                request_kind=request_kind,
                cost_per_input=meta.get("cost_per_input"),
                cost_per_output=meta.get("cost_per_output"),
                is_streaming=bool(kwargs.get("streaming")),
                provider_adapter=provider_adapter,
                effective_capability_matrix=effective_capability_matrix,
                tool_calling_mode=tool_calling_mode,
                structured_output_mode=structured_output_mode,
                stream_mode=stream_mode,
            )
        )
        kwargs["callbacks"] = callbacks
        return kwargs

    @classmethod
    def create_chat_model(cls, model_id: str, **kwargs) -> Any:
        """
        Creates a ChatOpenAI / ChatAnthropic instance.
        
        Priority of configs:
        1. **kwargs explicitly passed bycaller (e.g. Agent's own specific temperature/streaming override)
        2. Global models.json `temperature` / `maxTokens`
        3. Fallback to `api_key="sk-dummy"` if missing so as not to immediately crash initialization.
        """
        role = str(kwargs.pop("_role", "") or "")
        request_kind = str(kwargs.pop("_request_kind", "") or "chat")
        capability_class_override = str(kwargs.pop("_capability_class", "") or "")
        if role and ("temperature" not in kwargs or kwargs.get("temperature") is None):
            role_temperature = model_control_plane.get_role_temperature(role)
            if role_temperature is not None:
                kwargs["temperature"] = role_temperature
        meta = cls._resolve_model_metadata(model_id)
        
        if not meta.get("is_found"):
            if meta.get("lookup_status") == "ambiguous":
                raise ValueError(
                    f"模型 '{model_id}' 存在多个 Provider，请使用 provider-qualified modelRef。"
                )
            # If the user passed a model completely unregistered, we attempt to initialize it 
            # as OpenAI barebones just in case base_url/api_key are in standard env vars
            provider_kwargs = cls._attach_telemetry(
                {"model": model_id, **kwargs},
                {"provider_id": "openai", "provider_name": "openai"},
                model_id=model_id,
                role=role,
                request_kind=request_kind,
                capability_class_override=capability_class_override,
            )
            return V8ChatModelAdapter(
                model_id=model_id,
                provider_standard="openai",
                role=role,
                meta={"provider_id": "openai", "provider_name": "openai", "api_standard": "openai"},
                model_kwargs=provider_kwargs,
                builder=lambda: ChatOpenAI(**provider_kwargs),
            )

        if meta.get("oauth_error"):
            raise RuntimeError(str(meta["oauth_error"]))
        if not bool(meta.get("runtime_ready", True)):
            raise V8LLMCapabilityMismatchError(
                code="capability_mismatch",
                message="当前 provider 尚未进入统一 LangChain 运行时或缺少本地 runtime 依赖。",
                provider=str(meta.get("provider_name") or meta.get("provider_id") or "unknown"),
                model=str(meta.get("model_id") or model_id),
                retryable=False,
                user_action="请补齐本地 runtime 依赖，或切换到当前已 runtime-ready 的 provider。",
                details={"runtimeUnsupportedReason": str(meta.get("runtime_unsupported_reason") or "")},
            )

        api_standard = str(meta.get("api_standard", "openai")).lower()
        wire_model_id = str(meta.get("model_id") or model_id)
        try:
            if api_standard == "anthropic":
                provider_kwargs = cls._attach_telemetry(
                    cls._build_anthropic_kwargs(wire_model_id, meta, **kwargs),
                    meta,
                    model_id=wire_model_id,
                    role=role,
                    request_kind=request_kind,
                    capability_class_override=capability_class_override,
                )
                builder = lambda: ChatAnthropic(**provider_kwargs)
            elif api_standard in {"google", "gemini"}:
                if cls._is_gemini_cli_provider(
                    api_standard=api_standard,
                    provider_config=dict(meta.get("provider_record") or {}),
                    oauth_flavor=str(meta.get("oauth_flavor") or ""),
                ):
                    provider_kwargs = cls._attach_telemetry(
                        cls._build_gemini_kwargs(wire_model_id, meta, **kwargs),
                        meta,
                        model_id=wire_model_id,
                        role=role,
                        request_kind=request_kind,
                        capability_class_override=capability_class_override,
                    )
                    builder = lambda: GeminiCliRuntimeModel(
                        model_id=wire_model_id,
                        meta=meta,
                        model_kwargs=provider_kwargs,
                    )
                else:
                    if ChatGoogleGenerativeAI is None:
                        raise ImportError("langchain-google-genai is not installed")
                    provider_kwargs = cls._attach_telemetry(
                        cls._build_gemini_kwargs(wire_model_id, meta, **kwargs),
                        meta,
                        model_id=wire_model_id,
                        role=role,
                        request_kind=request_kind,
                        capability_class_override=capability_class_override,
                    )
                    builder = lambda: ChatGoogleGenerativeAI(**provider_kwargs)
            elif str(meta.get("provider_adapter") or "") == "openai-codex-responses":
                provider_kwargs = cls._attach_telemetry(
                    cls._build_openai_kwargs(wire_model_id, meta, **kwargs),
                    meta,
                    model_id=wire_model_id,
                    role=role,
                    request_kind=request_kind,
                    capability_class_override=capability_class_override,
                )
                builder = lambda: OpenAICodexResponsesRuntimeModel(
                    model_id=wire_model_id,
                    meta=meta,
                    model_kwargs=provider_kwargs,
                )
            else:
                provider_kwargs = cls._attach_telemetry(
                    cls._build_openai_kwargs(wire_model_id, meta, **kwargs),
                    meta,
                    model_id=wire_model_id,
                    role=role,
                    request_kind=request_kind,
                    capability_class_override=capability_class_override,
                )
                builder = lambda: ChatOpenAI(**provider_kwargs)
            return V8ChatModelAdapter(
                model_id=wire_model_id,
                provider_standard=api_standard,
                role=role,
                meta=meta,
                model_kwargs=provider_kwargs,
                builder=builder,
            )
        except Exception as exc:
            raise_as_v8_llm_error(exc, provider=meta.get("provider_name"), model=model_id, details={"mode": "factory_init"})
            
    @classmethod
    def create_embedding_model(cls, model_id: str, **kwargs) -> BaseEmbedding:
        if not model_id:
            raise ValueError("Embedding model_id must be provided")
            
        meta = cls._resolve_model_metadata(model_id)
        if not meta.get("is_found"):
            raise ValueError(f"Embedding model '{model_id}' is not mapped in models.json")
        if meta.get("oauth_error"):
            raise ValueError(str(meta["oauth_error"]))
            
        api_key = meta.get("api_key")
        if not api_key:
            raise ValueError(f"Could not resolve API key for embedding model '{model_id}'")
            
        wire_model_id = str(meta.get("model_id") or model_id)
        return OpenAICompatibleEmbedding(
            model_name=wire_model_id,
            api_key=api_key,
            base_url=meta.get("base_url"),
            max_tokens=meta.get("global_context_window"),
            provider_id=str(meta.get("provider_id") or meta.get("provider_name") or ""),
            provider_name=str(meta.get("provider_name") or meta.get("provider_id") or ""),
            role="embedding",
            capability_class=str(meta.get("capability_class") or "embedding"),
        )

    @classmethod
    def create_reranker_model(cls, model_id: str, **kwargs) -> BaseReranker:
        if not model_id:
            raise ValueError("Reranker model_id must be provided")
            
        meta = cls._resolve_model_metadata(model_id)
        if not meta.get("is_found"):
            raise ValueError(f"Reranker model '{model_id}' is not mapped in models.json")
        if meta.get("oauth_error"):
            raise ValueError(str(meta["oauth_error"]))
            
        api_key = meta.get("api_key")
        if not api_key:
            raise ValueError(f"Could not resolve API key for reranker model '{model_id}'")
        role = str(kwargs.pop("role", "") or "reranker")
        capability_class = str(kwargs.pop("capability_class", "") or meta.get("capability_class") or "reranker")
        wire_model_id = str(meta.get("model_id") or model_id)
            
        return RestReranker(
            model_name=wire_model_id,
            api_key=api_key,
            base_url=meta.get("base_url"),
            max_tokens=meta.get("global_context_window"),
            provider_id=str(meta.get("provider_id") or meta.get("provider_name") or ""),
            provider_name=str(meta.get("provider_name") or meta.get("provider_id") or ""),
            role=role,
            capability_class=capability_class,
            api_flavor=str(kwargs.pop("api_flavor", "") or meta.get("rerank_api_flavor") or "generic"),
        )

    @classmethod
    def create_for_role(cls, role: str, **kwargs):
        """Create a chat model for a named system role (e.g. 'supervisor', 'vision', 'summary').
        
        Reads from models.json → roles → {role}, falling back to 'default' role.
        """
        resolution = model_control_plane.resolve_model_for_role(role)
        model_id = resolution.get("resolvedModelRef") or resolution.get("resolvedModelId") or ""
        if not model_id:
            raise ValueError(f"No model configured for role '{role}' in models.json. "
                             f"Please set roles.{role} in Admin → Models.")
        return cls.create_chat_model(model_id, _role=role, **kwargs)

    @classmethod
    def get_model_metadata(cls, model_id: str) -> Dict[str, Any]:
        return cls._resolve_model_metadata(model_id)

    @classmethod
    def get_model_context_window(cls, model_id: str) -> Optional[int]:
        meta = cls._resolve_model_metadata(model_id)
        if not meta.get("is_found"):
            return None
        try:
            value = meta.get("global_context_window")
            return int(value) if value else None
        except (TypeError, ValueError):
            return None

    @classmethod
    def create_embedding_for_role(cls, **kwargs) -> BaseEmbedding:
        """Create an embedding model using the 'embedding' role from models.json."""
        resolution = model_control_plane.resolve_model_for_role("embedding")
        model_id = resolution.get("resolvedModelRef") or resolution.get("resolvedModelId") or ""
        if not model_id:
            raise ValueError("No embedding model configured. Set roles.embedding in models.json.")
        return cls.create_embedding_model(model_id, **kwargs)

    @classmethod
    def create_reranker_for_role(cls, role: str = "reranker", **kwargs) -> BaseReranker:
        """Create a reranker model using a named rerank role from models.json."""
        resolution = model_control_plane.resolve_model_for_role(role)
        model_id = resolution.get("resolvedModelRef") or resolution.get("resolvedModelId") or ""
        if not model_id:
            raise ValueError(f"No reranker model configured. Set roles.{role} in models.json.")
        return cls.create_reranker_model(model_id, role=role, **kwargs)

# Global singleton exporter
llm_factory = LLMFactory()
