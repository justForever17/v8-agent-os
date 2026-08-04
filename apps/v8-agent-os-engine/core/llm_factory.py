from typing import Dict, Any, Optional, Type, List
import logging
import sys
import time
import re
import math
from datetime import datetime, timezone

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
from core.openai_compatible_chat_model import V8OpenAICompatibleChatModel
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
from core.model_endpoint_binding import build_model_endpoint_binding
from core.provider_hosted_tools import normalize_provider_hosted_tools
from core.model_telemetry import model_telemetry_service
from core.model_thinking_control import (
    ensure_anthropic_thinking_budget_headroom,
    merge_model_request_patch,
    no_think_request_patch,
    provider_reasoning_transport_patch,
    reasoning_effort_request_patch,
    reasoning_summary_request_patch,
    resolve_reasoning_effort_control_for_metadata,
    resolve_thinking_control_for_metadata,
)
from core.oauth_credentials import resolve_oauth_reference, resolve_provider_oauth_credential
from core.provider_compatibility import normalize_provider_error
from core.reasoning_surface_contract import resolve_reasoning_surface_for_metadata
from erc.runtime_context import get_runtime_context
from langchain_core.embeddings import Embeddings

_EMBEDDING_OBSERVED_LIMITS: Dict[str, int] = {}
_RERANK_OBSERVED_QUERY_LIMITS: Dict[str, int] = {}
_TOKEN_LIMIT_RE = re.compile(r"(?:maximum token length|max(?:imum)?(?: input)? tokens?|token limit)[^\d]{0,40}(\d{3,7})", re.IGNORECASE)
logger = logging.getLogger(__name__)


def _safe_log_text(value: Any, *, limit: int = 1200) -> str:
    text = str(value or "")
    if len(text) > limit:
        text = text[: limit - 3].rstrip() + "..."
    encoding = getattr(sys.stderr, "encoding", None) or "utf-8"
    try:
        text.encode(encoding)
        return text
    except UnicodeEncodeError:
        return text.encode(encoding, errors="backslashreplace").decode(encoding, errors="replace")


def _extract_observed_token_limit(error_text: str) -> int | None:
    text = str(error_text or "")
    matches = [int(match.group(1)) for match in _TOKEN_LIMIT_RE.finditer(text) if match.group(1).isdigit()]
    if not matches:
        return None
    return min(matches)


def _classify_embedding_provider_error(status_code: int, error_text: str) -> str:
    text = str(error_text or "").lower()
    if _extract_observed_token_limit(error_text) or "input at index" in text or "maximum token length" in text:
        return "input_limit_exceeded"
    if int(status_code or 0) == 429 or "rate limit" in text or "too many requests" in text:
        return "rate_limited"
    if int(status_code or 0) == 401 or "invalid api key" in text or "unauthorized" in text:
        return "auth_failed"
    if int(status_code or 0) == 403:
        return "quota_exceeded" if "quota" in text or "billing" in text else "auth_failed"
    if "quota" in text or "insufficient" in text or "billing" in text:
        return "quota_exceeded"
    if int(status_code or 0) >= 500:
        return "network_error"
    return "provider_error"


def _classify_rerank_provider_error(status_code: int, error_text: str) -> str:
    text = str(error_text or "").lower()
    if _extract_observed_token_limit(error_text) or "query is too long" in text or "document is too long" in text or "maximum token length" in text:
        return "input_limit_exceeded"
    if int(status_code or 0) == 429 or "rate limit" in text or "too many requests" in text:
        return "rate_limited"
    if int(status_code or 0) == 401 or "invalid api key" in text or "unauthorized" in text:
        return "auth_failed"
    if int(status_code or 0) == 403:
        return "quota_exceeded" if "quota" in text or "billing" in text else "auth_failed"
    if "quota" in text or "insufficient" in text or "billing" in text:
        return "quota_exceeded"
    if int(status_code or 0) >= 500:
        return "network_error"
    return "provider_error"


def _truncate_text_for_token_limit(text: str, token_limit: int | None, *, label: str = "Text") -> str:
    limit = int(token_limit or 0)
    if not limit or not text:
        return text
    cjk_heavy = bool(re.search(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]", str(text)[:4000]))
    chars_per_token = 1.0 if cjk_heavy else 2.5
    max_chars = int(limit * chars_per_token * 0.9)
    if len(text) <= max_chars:
        return text
    logger.info("[%s] Truncating text from %s to %s chars (model limit: %s tokens)", label, len(text), max_chars, limit)
    return text[:max_chars]

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

    def _load_persisted_observed_limit(self) -> int | None:
        provider_id = str(self.provider_id or "").strip()
        model_name = str(self.model_name or "").strip()
        if not provider_id or not model_name:
            return None
        try:
            config = model_control_plane.get_config()
            provider = dict((config.get("providers") or {}).get(provider_id) or {})
            models = dict(provider.get("models") or {})
            meta = dict(models.get(model_name) or {})
            observed = meta.get("observedInputTokenLimit")
            if observed:
                observed_int = int(observed)
                if observed_int > 0:
                    _EMBEDDING_OBSERVED_LIMITS[self._observed_limit_key()] = observed_int
                    return observed_int
        except Exception:
            return None
        return None

    def _persist_observed_limit(self, observed_limit: int) -> None:
        provider_id = str(self.provider_id or "").strip()
        model_name = str(self.model_name or "").strip()
        if not provider_id or not model_name or int(observed_limit or 0) <= 0:
            return
        try:
            config = model_control_plane.get_config()
            providers = dict(config.get("providers") or {})
            provider = dict(providers.get(provider_id) or {})
            models = dict(provider.get("models") or {})
            if model_name not in models:
                return
            meta = dict(models.get(model_name) or {})
            current = meta.get("observedInputTokenLimit")
            if current and int(current) > 0 and int(current) <= int(observed_limit):
                return
            meta["observedInputTokenLimit"] = int(observed_limit)
            meta["observedInputTokenLimitSource"] = "provider_error"
            meta["observedInputTokenLimitAt"] = datetime.now(timezone.utc).isoformat()
            meta["observedInputTokenLimitEndpoint"] = self.endpoint
            models[model_name] = meta
            provider["models"] = models
            providers[provider_id] = provider
            config["providers"] = providers
            model_control_plane.save_config(config)
        except Exception as exc:
            logger.warning("[Embedding] Failed to persist observed input token limit: %s", _safe_log_text(exc))

    def _effective_max_tokens(self) -> int | None:
        if not self.max_tokens:
            return None
        observed = _EMBEDDING_OBSERVED_LIMITS.get(self._observed_limit_key()) or self._load_persisted_observed_limit()
        if observed and observed > 0:
            return min(int(self.max_tokens), int(observed))
        return int(self.max_tokens)
    
    def _truncate_text(self, text: str, *, token_limit: int | None = None) -> str:
        """Truncate text to stay within model's context window. Rough estimate: 1 token ≈ 3 chars for CJK."""
        limit = int(token_limit or self._effective_max_tokens() or 0)
        if not limit or not text:
            return text
        cjk_heavy = bool(re.search(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]", text[:4000]))
        chars_per_token = 1.0 if cjk_heavy else 2.5
        max_chars = int(limit * chars_per_token * 0.9)
        if len(text) > max_chars:
            logger.info("[Embedding] Truncating text from %s to %s chars (model limit: %s tokens)", len(text), max_chars, limit)
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
        if not self._effective_max_tokens():
            raise ValueError(
                f"missing_context_window: embedding model '{self.model_name}' must define contextWindow before retrieval can run"
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
                self._persist_observed_limit(int(observed_limit))
                logger.info("[Embedding] Observed provider input token limit %s; retrying with smaller truncation.", observed_limit)
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
                logger.warning("[Embedding Error] %s: %s", res.status_code, _safe_log_text(res.text))
                error_kind = _classify_embedding_provider_error(int(res.status_code or 0), res.text)
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
                        "errorKind": error_kind,
                    },
                )
        if res.status_code != 200:
            res.raise_for_status()
        
        response_payload = res.json()
        data = response_payload.get("data") if isinstance(response_payload, dict) else None
        valid_data = (
            isinstance(data, list)
            and len(data) == len(texts)
            and all(isinstance(item, dict) and isinstance(item.get("embedding"), list) for item in data)
        )
        if not valid_data:
            model_telemetry_service.record_aux_model_invocation(
                model_id=self.model_name,
                provider_id=self.provider_id,
                provider_name=self.provider_name,
                role=self.role,
                capability_class=self.capability_class,
                request_kind="embedding",
                latency_ms=(time.perf_counter() - started) * 1000,
                status="failed",
                error_code="invalid_response",
                error_message="Embedding provider returned an invalid data array",
                metadata={
                    "documents": len(texts),
                    "resultCount": len(data) if isinstance(data, list) else None,
                    "errorKind": "invalid_response",
                },
            )
            raise RuntimeError(
                f"embedding_provider_invalid_response: expected {len(texts)} vector rows"
            )
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


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = 0.0
    left_norm = 0.0
    right_norm = 0.0
    for left_value, right_value in zip(left, right):
        left_float = float(left_value or 0.0)
        right_float = float(right_value or 0.0)
        dot += left_float * right_float
        left_norm += left_float * left_float
        right_norm += right_float * right_float
    denominator = math.sqrt(left_norm) * math.sqrt(right_norm)
    if denominator <= 0:
        return 0.0
    return dot / denominator


class EmbeddingSimilarityReranker(BaseReranker):
    """Use an embedding model as a deterministic reranker via cosine similarity."""

    api_flavor = "embedding_similarity"

    def __init__(self, embedding_model: BaseEmbedding, model_name: str, provider_id: str = "", provider_name: str = "", role: str = "reranker", capability_class: str = "embedding"):
        self.embedding_model = embedding_model
        self.model_name = model_name
        self.provider_id = provider_id
        self.provider_name = provider_name or provider_id
        self.role = role
        self.capability_class = capability_class

    def rerank(self, query: str, documents: list[str], top_k: int = 3) -> list[Dict[str, Any]]:
        if not documents:
            return []
        try:
            effective_top_k = max(0, min(int(top_k), len(documents)))
        except (TypeError, ValueError):
            effective_top_k = min(3, len(documents))
        if effective_top_k <= 0:
            return []

        vectors = self.embedding_model.embed_documents([str(query or ""), *[str(doc or "") for doc in documents]])
        if len(vectors) < len(documents) + 1:
            raise ValueError(
                f"embedding_reranker_vector_count_mismatch: expected {len(documents) + 1}, got {len(vectors)}"
            )
        query_vector = vectors[0]
        ranked = [
            {
                "index": index,
                "document": documents[index],
                "relevance_score": _cosine_similarity(query_vector, vectors[index + 1]),
            }
            for index in range(len(documents))
        ]
        ranked.sort(key=lambda row: (-float(row.get("relevance_score") or 0.0), int(row.get("index") or 0)))
        return ranked[:effective_top_k]


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

    def _observed_query_limit_key(self) -> str:
        return "|".join([str(self.provider_id or ""), str(self.model_name or ""), str(self.role or "reranker")])

    def _load_persisted_observed_query_limit(self) -> int | None:
        provider_id = str(self.provider_id or "").strip()
        model_name = str(self.model_name or "").strip()
        if not provider_id or not model_name:
            return None
        try:
            config = model_control_plane.get_config()
            provider = dict((config.get("providers") or {}).get(provider_id) or {})
            models = dict(provider.get("models") or {})
            meta = dict(models.get(model_name) or {})
            observed = meta.get("observedRerankQueryTokenLimit")
            if observed:
                observed_int = int(observed)
                if observed_int > 0:
                    _RERANK_OBSERVED_QUERY_LIMITS[self._observed_query_limit_key()] = observed_int
                    return observed_int
        except Exception:
            return None
        return None

    def _persist_observed_query_limit(self, observed_limit: int, *, endpoint: str = "") -> None:
        provider_id = str(self.provider_id or "").strip()
        model_name = str(self.model_name or "").strip()
        if not provider_id or not model_name or int(observed_limit or 0) <= 0:
            return
        try:
            config = model_control_plane.get_config()
            providers = dict(config.get("providers") or {})
            provider = dict(providers.get(provider_id) or {})
            models = dict(provider.get("models") or {})
            if model_name not in models:
                return
            meta = dict(models.get(model_name) or {})
            current = meta.get("observedRerankQueryTokenLimit")
            if current and int(current) > 0 and int(current) <= int(observed_limit):
                return
            meta["observedRerankQueryTokenLimit"] = int(observed_limit)
            meta["observedRerankQueryTokenLimitSource"] = "provider_error"
            meta["observedRerankQueryTokenLimitAt"] = datetime.now(timezone.utc).isoformat()
            meta["observedRerankQueryTokenLimitEndpoint"] = endpoint or (self.endpoints[0] if self.endpoints else "")
            models[model_name] = meta
            provider["models"] = models
            providers[provider_id] = provider
            config["providers"] = providers
            model_control_plane.save_config(config)
        except Exception as exc:
            logger.warning(
                "[Reranker] Failed to persist observed query token limit: %s",
                _safe_log_text(exc),
            )

    def _effective_query_token_limit(self) -> int:
        if not self.max_tokens:
            raise ValueError(
                f"missing_context_window: reranker model '{self.model_name}' must define contextWindow before rerank can run"
            )
        observed = _RERANK_OBSERVED_QUERY_LIMITS.get(self._observed_query_limit_key()) or self._load_persisted_observed_query_limit()
        configured = int(self.max_tokens)
        default_query = max(1, configured // 8)
        if observed and observed > 0:
            return min(default_query, int(observed))
        return default_query

    def _prepare_payload_documents(self, query: str, documents: list[str], *, query_limit: int | None = None) -> tuple[str, list[str], dict[str, Any]]:
        if not self.max_tokens:
            raise ValueError(
                f"missing_context_window: reranker model '{self.model_name}' must define contextWindow before rerank can run"
            )
        total_budget = int(self.max_tokens)
        effective_query_limit = int(query_limit or self._effective_query_token_limit())
        trimmed_query = _truncate_text_for_token_limit(str(query or ""), effective_query_limit, label="Reranker")
        remaining = max(1, total_budget - effective_query_limit)
        per_doc_limit = max(1, remaining // max(1, len(documents)))
        trimmed_docs = [
            _truncate_text_for_token_limit(str(doc or ""), per_doc_limit, label="Reranker")
            for doc in list(documents or [])
        ]
        return trimmed_query, trimmed_docs, {
            "queryTokenLimit": effective_query_limit,
            "documentTokenLimit": per_doc_limit,
            "configuredInputTokenLimit": total_budget,
        }

    def _post_rerank(self, endpoint: str, payload: dict[str, Any], headers: dict[str, str]):
        import requests
        return requests.post(endpoint, json=payload, headers=headers, timeout=30)

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
        trimmed_query, trimmed_documents, limit_meta = self._prepare_payload_documents(query, documents)
        payload = {
            "model": self.model_name,
            "query": trimmed_query,
            "documents": trimmed_documents,
            "top_n": top_k,
            "return_documents": True
        }
        
        out: List[Dict[str, Any]] = []
        resolved_endpoint = self.endpoints[0] if self.endpoints else ""
        last_error: tuple[int, str, str] | None = None
        for endpoint in self.endpoints:
            resolved_endpoint = endpoint
            res = self._post_rerank(endpoint, payload, headers)
            if res.status_code != 200 and _classify_rerank_provider_error(res.status_code, res.text) == "input_limit_exceeded":
                observed_limit = _extract_observed_token_limit(res.text)
                current_query_limit = int(limit_meta.get("queryTokenLimit") or 0)
                retry_query_limit = int(observed_limit) if observed_limit and observed_limit > 0 else max(1, current_query_limit // 2)
                if retry_query_limit and retry_query_limit > 0 and retry_query_limit < current_query_limit:
                    if observed_limit and observed_limit > 0:
                        _RERANK_OBSERVED_QUERY_LIMITS[self._observed_query_limit_key()] = int(observed_limit)
                        self._persist_observed_query_limit(int(observed_limit), endpoint=endpoint)
                    retry_query, retry_documents, retry_limit_meta = self._prepare_payload_documents(
                        query,
                        documents,
                        query_limit=int(retry_query_limit),
                    )
                    retry_payload = dict(payload)
                    retry_payload["query"] = retry_query
                    retry_payload["documents"] = retry_documents
                    logger.info(
                        "[Reranker] Retrying with smaller query token limit %s.",
                        retry_query_limit,
                    )
                    retry_res = self._post_rerank(endpoint, retry_payload, headers)
                    if retry_res.status_code == 200:
                        res = retry_res
                        payload = retry_payload
                        limit_meta = retry_limit_meta
            if res.status_code == 200:
                out = parse_rerank_response_payload(res.json(), documents)
                break
            last_error = (res.status_code, res.text, endpoint)
        else:
            status_code, error_text, failed_endpoint = last_error or (500, "Unknown rerank error", resolved_endpoint)
            logger.warning(
                "[Reranker Error] %s: %s",
                status_code,
                _safe_log_text(error_text),
            )
            error_kind = _classify_rerank_provider_error(int(status_code or 0), error_text)
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
                metadata={
                    "documents": len(documents),
                    "top_k": top_k,
                    "endpoint": failed_endpoint,
                    "apiFlavor": self.api_flavor,
                    "errorKind": error_kind,
                    **limit_meta,
                },
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
            metadata={
                "documents": len(documents),
                "top_k": top_k,
                "results": len(out),
                "endpoint": resolved_endpoint,
                "apiFlavor": self.api_flavor,
                "observedRerankQueryTokenLimit": _RERANK_OBSERVED_QUERY_LIMITS.get(self._observed_query_limit_key()),
                **limit_meta,
            },
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
            endpoint_binding = build_model_endpoint_binding(
                p_name,
                str(record.get("model_id") or target_model_name),
                p_conf,
                meta,
            )
            upstream_model_id = str(record.get("model_id") or target_model_name)
            model_ref = str(record.get("model_ref") or "")
            api_standard = str(
                endpoint_binding.get("apiStandard")
                or p_conf.get("api_standard", "openai")
                or "openai"
            )
            capability_class = str(meta.get("capabilityClass") or "")

            oauth_resolution = resolve_provider_oauth_credential(
                provider_id=p_name,
                provider_config=p_conf,
            )
            t_api_key = str(oauth_resolution.get("credential") or "")
            t_base_url = str(
                endpoint_binding.get("baseUrl")
                or p_conf.get("base_url", "")
                or ""
            )
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
                "endpoint_binding": endpoint_binding,
                "wire_protocol": str(endpoint_binding.get("wireProtocol") or endpoint_binding.get("wire_protocol") or "").strip(),
                "provider_hosted_tools": normalize_provider_hosted_tools(endpoint_binding.get("providerHostedTools")),
                "base_url": t_base_url,
                "api_key": t_api_key,
                "api_standard": api_standard,
                "api_version": endpoint_binding.get("apiVersion") or p_conf.get("api_version") or p_conf.get("apiVersion") or "",
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
                "reasoning_surface": resolve_reasoning_surface_for_metadata(
                    {
                        "provider_id": p_name,
                        "model_id": upstream_model_id,
                        "model_ref": model_ref,
                        "provider_record": p_conf,
                        "model_record": meta,
                        "api_standard": api_standard,
                        "capabilities": capabilities,
                    }
                ),
                "thinking_control": resolve_thinking_control_for_metadata(
                    {
                        "provider_id": p_name,
                        "model_id": upstream_model_id,
                        "model_ref": model_ref,
                        "provider_record": p_conf,
                        "model_record": meta,
                        "api_standard": api_standard,
                        "capabilities": capabilities,
                    }
                ),
                "reasoning_effort_control": resolve_reasoning_effort_control_for_metadata(
                    {
                        "provider_id": p_name,
                        "model_id": upstream_model_id,
                        "model_ref": model_ref,
                        "provider_record": p_conf,
                        "model_record": meta,
                        "api_standard": api_standard,
                        "capabilities": capabilities,
                        "capability_class": capability_class,
                    }
                ),
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

        final_kwargs = merge_model_request_patch(final_kwargs, no_think_request_patch(meta.get("thinking_control")))
        final_kwargs = merge_model_request_patch(
            final_kwargs,
            provider_reasoning_transport_patch(meta),
        )
        final_kwargs = merge_model_request_patch(
            final_kwargs,
            reasoning_effort_request_patch(
                meta.get("reasoning_effort_control"),
                meta.get("request_reasoning_effort"),
            ),
        )
        final_kwargs = merge_model_request_patch(
            final_kwargs,
            reasoning_summary_request_patch(meta),
        )
        # Protocol is a model binding, never inferred from a route at request
        # time. Only an explicit Responses binding opts into that schema, and
        # request kwargs cannot silently override the persisted choice.
        if str(meta.get("wire_protocol") or "").strip() == "openai.responses":
            final_kwargs["use_responses_api"] = True
            final_kwargs["store"] = False
            final_kwargs["use_previous_response_id"] = False
        elif str(meta.get("wire_protocol") or "").strip() == "openai.chat_completions":
            final_kwargs["use_responses_api"] = False
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

        final_kwargs = merge_model_request_patch(
            final_kwargs,
            no_think_request_patch(meta.get("thinking_control")),
        )
        final_kwargs = merge_model_request_patch(
            final_kwargs,
            reasoning_effort_request_patch(
                meta.get("reasoning_effort_control"),
                meta.get("request_reasoning_effort"),
            ),
        )
        return ensure_anthropic_thinking_budget_headroom(final_kwargs)

    @classmethod
    def _build_gemini_kwargs(cls, model_id: str, meta: Dict[str, Any], **kwargs) -> Dict[str, Any]:
        final_kwargs = {
            "model": model_id,
            "google_api_key": meta.get("api_key") or "",
        }
        if meta.get("base_url"):
            # langchain-google-genai forwards client_options to google-genai's
            # HttpOptions.base_url. Without it, a custom Gemini-compatible
            # Provider silently falls back to Google's public endpoint.
            gemini_base_url = str(meta["base_url"]).rstrip("/")
            final_kwargs["client_options"] = gemini_base_url
            # A configured channel base URL is exact. V8OS never rewrites /v1
            # to /v1beta. apiVersion is only appended when the user explicitly
            # configured it on that channel.
            final_kwargs["api_version"] = str(meta.get("api_version") or "").strip()
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

        reasoning_surface = meta.get("reasoning_surface")
        if isinstance(reasoning_surface, dict) and (
            str(reasoning_surface.get("mode") or "").strip() == "reasoning_summary"
            and str(reasoning_surface.get("trust") or "").strip() == "adapter_verified"
            and str(reasoning_surface.get("requestStyle") or "").strip() == "gemini_include_thoughts"
            and "content[type=thinking]" in {
                str(item or "").strip()
                for item in list(reasoning_surface.get("responseFields") or [])
            }
        ):
            final_kwargs.setdefault("include_thoughts", True)
        final_kwargs = merge_model_request_patch(
            final_kwargs,
            no_think_request_patch(meta.get("thinking_control")),
        )
        final_kwargs = merge_model_request_patch(
            final_kwargs,
            reasoning_effort_request_patch(
                meta.get("reasoning_effort_control"),
                meta.get("request_reasoning_effort"),
            ),
        )
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
        wire_protocol = str(meta.get("wire_protocol") or meta.get("wireProtocol") or "").strip().lower()
        if wire_protocol == "gemini.generate_content":
            provider_adapter = "gemini"
        elif wire_protocol == "anthropic.messages":
            provider_adapter = "anthropic"
        else:
            provider_adapter = str(meta.get("provider_adapter") or "").strip() or (
                "gemini"
                if str(meta.get("api_standard") or "openai").lower() in {"google", "gemini"}
                else "anthropic"
                if str(meta.get("api_standard") or "openai").lower() == "anthropic"
                else "openai-compatible"
            )
        effective_capability_matrix = dict(meta.get("effective_capability_matrix") or {})
        supports_native_tools = effective_capability_matrix.get("supports_native_tools")
        if supports_native_tools is None:
            supports_native_tools = meta.get("supports_native_tools", meta.get("supportsTools", True))
        supports_native_structured_output = effective_capability_matrix.get("supports_native_structured_output")
        if supports_native_structured_output is None:
            supports_native_structured_output = meta.get(
                "supports_native_structured_output",
                meta.get("supportsStructuredOutput", True),
            )
        tool_calling_mode = "native" if bool(supports_native_tools) else "prompt_emulated"
        structured_output_mode = "native" if bool(supports_native_structured_output) else "prompt_fallback"
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
        requested_reasoning_effort = str(kwargs.pop("_reasoning_effort", "") or "")
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
                builder=lambda: V8OpenAICompatibleChatModel(
                    v8_model_ref=f"openai::{model_id}",
                    **provider_kwargs,
                ),
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
        if requested_reasoning_effort:
            meta = {**meta, "request_reasoning_effort": requested_reasoning_effort}
        else:
            reasoning_control = meta.get("reasoning_effort_control")
            configured_reasoning_effort = str(
                (reasoning_control.get("selectedLevel") if isinstance(reasoning_control, dict) else "") or ""
            ).strip()
            if configured_reasoning_effort and configured_reasoning_effort != "auto":
                meta = {**meta, "request_reasoning_effort": configured_reasoning_effort}
        wire_model_id = str(
            (meta.get("endpoint_binding") or {}).get("providerModelId")
            or meta.get("model_id")
            or model_id
        )
        try:
            wire_protocol = str(meta.get("wire_protocol") or "").strip()
            runtime_provider_standard = (
                "anthropic" if wire_protocol == "anthropic.messages"
                else "gemini" if wire_protocol == "gemini.generate_content"
                else "openai" if wire_protocol.startswith("openai.")
                else api_standard
            )
            if wire_protocol == "anthropic.messages" or (not wire_protocol and api_standard == "anthropic"):
                provider_kwargs = cls._attach_telemetry(
                    cls._build_anthropic_kwargs(wire_model_id, meta, **kwargs),
                    meta,
                    model_id=wire_model_id,
                    role=role,
                    request_kind=request_kind,
                    capability_class_override=capability_class_override,
                )
                builder = lambda: ChatAnthropic(**provider_kwargs)
            elif wire_protocol == "gemini.generate_content" or (not wire_protocol and api_standard in {"google", "gemini"}):
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
                resolved_model_ref = str(meta.get("model_ref") or model_id)
                builder = lambda: V8OpenAICompatibleChatModel(
                    v8_model_ref=resolved_model_ref,
                    **provider_kwargs,
                )
            return V8ChatModelAdapter(
                model_id=wire_model_id,
                provider_standard=runtime_provider_standard,
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
            
        role = str(kwargs.pop("role", "") or "embedding")
        capability_class = str(kwargs.pop("capability_class", "") or meta.get("capability_class") or "embedding")
        wire_model_id = str(meta.get("model_id") or model_id)
        return OpenAICompatibleEmbedding(
            model_name=wire_model_id,
            api_key=api_key,
            base_url=meta.get("base_url"),
            max_tokens=meta.get("global_context_window"),
            provider_id=str(meta.get("provider_id") or meta.get("provider_name") or ""),
            provider_name=str(meta.get("provider_name") or meta.get("provider_id") or ""),
            role=role,
            capability_class=capability_class,
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

        model_record = dict(meta.get("model_record") or {})
        model_type = str(model_record.get("type") or meta.get("type") or "").strip().upper()
        capabilities = dict(meta.get("capabilities") or {})
        is_embedding_model = (
            capability_class.lower() == "embedding"
            or model_type == "EMBEDDING"
            or bool(capabilities.get("embedding") or capabilities.get("supportsEmbedding"))
        )
        if is_embedding_model:
            embedding_model = cls.create_embedding_model(
                model_id,
                role=role,
                capability_class="embedding",
            )
            return EmbeddingSimilarityReranker(
                embedding_model=embedding_model,
                model_name=wire_model_id,
                provider_id=str(meta.get("provider_id") or meta.get("provider_name") or ""),
                provider_name=str(meta.get("provider_name") or meta.get("provider_id") or ""),
                role=role,
                capability_class="embedding",
            )
            
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
    def get_model_max_output_tokens(cls, model_id: str) -> Optional[int]:
        meta = cls._resolve_model_metadata(model_id)
        if not meta.get("is_found"):
            return None

        def _positive_int(value: Any) -> Optional[int]:
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                return None
            return parsed if parsed > 0 else None

        model_record = dict(meta.get("model_record") or {})
        configured = (
            _positive_int(meta.get("global_max_tokens"))
            or _positive_int(model_record.get("maxTokens"))
            or _positive_int(model_record.get("maxOutputTokens"))
        )
        if configured:
            return configured

        provider_id = str(meta.get("provider_id") or meta.get("provider_name") or "").strip()
        wire_model_id = str(meta.get("model_id") or model_id or "").strip()
        try:
            from core.model_provider_catalog import model_provider_catalog

            provider = model_provider_catalog.get_provider(provider_id) if provider_id else None
            if provider and wire_model_id:
                catalog_model = model_provider_catalog.normalize_model(provider, wire_model_id)
                catalog_limit = (
                    _positive_int(catalog_model.get("maxTokens"))
                    or _positive_int(catalog_model.get("maxOutputTokens"))
                )
                if catalog_limit:
                    return catalog_limit
        except Exception:
            pass

        try:
            from core.model_capability_registry import model_capability_registry

            registry_model = model_capability_registry.find(wire_model_id)
            return _positive_int((registry_model or {}).get("maxOutputTokens"))
        except Exception:
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
