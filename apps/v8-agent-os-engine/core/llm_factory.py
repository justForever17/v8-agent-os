from typing import Dict, Any, Optional, Type
import time

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
from core.model_budget_service import model_budget_service
from core.model_control_plane import model_control_plane
from core.model_telemetry import model_telemetry_service
from core.oauth_credentials import resolve_oauth_reference, resolve_provider_oauth_credential
from core.provider_compatibility import normalize_provider_error
from erc.runtime_context import get_runtime_context
from langchain_core.embeddings import Embeddings

# Re-implementing the embedding and reranker wrappers cleanly
try:
    from core.vector_store import BaseEmbedding, BaseReranker
except ImportError:
    class BaseEmbedding(Embeddings):
        pass
    class BaseReranker:
        pass


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
    
    def _truncate_text(self, text: str) -> str:
        """Truncate text to stay within model's context window. Rough estimate: 1 token ≈ 3 chars for CJK."""
        if not self.max_tokens or not text:
            return text
        max_chars = int(self.max_tokens * 2.5 * 0.9)
        if len(text) > max_chars:
            print(f"[Embedding] ⚠️ Truncating text from {len(text)} to {max_chars} chars (model limit: {self.max_tokens} tokens)")
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
        texts = [self._truncate_text(t) for t in texts]
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
                metadata={"documents": len(texts)},
            )
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
            metadata={"documents": len(texts), "dimensions": len(data[0]["embedding"]) if data else 0},
        )
        return [item["embedding"] for item in data]
        
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._call_api(texts)
        
    def embed_query(self, text: str) -> list[float]:
        return self._call_api([text])[0]


class RestReranker(BaseReranker):
    def __init__(self, model_name: str, api_key: str, base_url: str, max_tokens: int = None, provider_id: str = "", provider_name: str = "", role: str = "reranker", capability_class: str = "reranker"):
        self.model_name = model_name
        self.api_key = api_key
        self.max_tokens = int(max_tokens) if max_tokens else None
        self.endpoint = base_url.rstrip("/") + "/rerank" if base_url else "https://api.siliconflow.cn/v1/rerank"
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
        
        res = requests.post(self.endpoint, json=payload, headers=headers)
        if res.status_code != 200:
            print(f"[Reranker Error] {res.status_code}: {res.text}")
            model_telemetry_service.record_aux_model_invocation(
                model_id=self.model_name,
                provider_id=self.provider_id,
                provider_name=self.provider_name,
                role=self.role,
                capability_class=self.capability_class,
                request_kind="reranker",
                latency_ms=(time.perf_counter() - started) * 1000,
                status="failed",
                error_code=str(res.status_code),
                error_message=res.text,
                metadata={"documents": len(documents), "top_k": top_k},
            )
        res.raise_for_status()
        
        results = res.json().get("results", [])
        out = []
        for r in results:
            doc_text = ""
            if isinstance(r.get("document"), dict):
                doc_text = r["document"].get("text", "")
            else:
                doc_text = r.get("document", "")
                
            if not doc_text and "index" in r:
                doc_text = documents[r["index"]]
                
            out.append({
                "index": r.get("index"),
                "document": doc_text,
                "relevance_score": r.get("relevance_score", 0.0)
            })
        model_telemetry_service.record_aux_model_invocation(
            model_id=self.model_name,
            provider_id=self.provider_id,
            provider_name=self.provider_name,
            role=self.role,
            capability_class=self.capability_class,
            request_kind="reranker",
            latency_ms=(time.perf_counter() - started) * 1000,
            status="completed",
            metadata={"documents": len(documents), "top_k": top_k, "results": len(out)},
        )
        return out


class LLMFactory:
    """
    Centralized factory for resolving models.json provider logic and instantiating
    robust LLM, Embedding, and Reranker clients across all of v8chat.
    """
    
    @staticmethod
    def _resolve_api_key(raw_key: str) -> str:
        """Resolves OAuth file references in local paths"""
        if not raw_key:
            return ""
        resolved = resolve_oauth_reference(raw_key)
        return str(resolved.get("credential") or "")

    @staticmethod
    def _resolve_model_metadata(target_model_name: str) -> Dict[str, Any]:
        """
        Scans models.json to strictly retrieve:
        - base_url
        - api_key
        - provider_name
        - global model meta (temperature, maxTokens, contextWindow)
        Returns an empty map if model explicitly not found.
        """
        record = model_control_plane.get_model_record(target_model_name)
        if record:
            p_name = str(record.get("provider_id") or "")
            p_conf = dict(record.get("provider") or {})
            meta = dict(record.get("model") or {})

            oauth_resolution = resolve_provider_oauth_credential(
                provider_id=p_name,
                provider_config=p_conf,
            )
            t_api_key = str(oauth_resolution.get("credential") or "")
            t_base_url = p_conf.get("base_url", "")
            oauth_error = str(oauth_resolution.get("error") or "")

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
                "model_name": target_model_name,
                "provider_id": p_name,
                "provider_name": p_name,
                "base_url": t_base_url,
                "api_key": t_api_key,
                "api_standard": p_conf.get("api_standard", "openai"),
                "oauth_path": oauth_resolution.get("oauthPath") or "",
                "oauth_ref": oauth_resolution.get("oauthRef") or "",
                "credential_mode": oauth_resolution.get("credentialMode") or "",
                "oauth_error": oauth_error,
                "oauth_flavor": oauth_resolution.get("oauthFlavor") or "",
                "oauth_access_token": oauth_resolution.get("accessToken") or "",
                "account_id": oauth_resolution.get("accountId") or "",
                "project_id": oauth_resolution.get("projectId") or "",
                "global_temperature": meta.get("temperature", 0.0),
                "global_max_tokens": meta.get("maxTokens"),
                "global_context_window": meta.get("contextWindow"),
                "capabilities": meta.get("capabilities", {}),
                "capability_class": meta.get("capabilityClass"),
                "cost_per_input": meta.get("costPerInput"),
                "cost_per_output": meta.get("costPerOutput"),
                "governance": record.get("governance", {}),
            }
        
        # Unmapped ad-hoc model names handling
        return {"is_found": False, "model_name": target_model_name}

    @staticmethod
    def _build_chat_kwargs(model_id: str, meta: Dict[str, Any], **kwargs) -> Dict[str, Any]:
        final_kwargs = dict(model=model_id)

        if meta.get("base_url"):
            final_kwargs["base_url"] = meta["base_url"]

        final_kwargs["api_key"] = meta.get("api_key") or "sk-dummy"

        if "temperature" in kwargs:
            final_kwargs["temperature"] = kwargs["temperature"]
        else:
            final_kwargs["temperature"] = meta.get("global_temperature", 0.0)

        if "max_tokens" in kwargs:
            final_kwargs["max_tokens"] = kwargs["max_tokens"]
        elif meta.get("global_max_tokens"):
            final_kwargs["max_tokens"] = int(meta["global_max_tokens"])

        for key, value in kwargs.items():
            if key not in {"temperature", "max_tokens", "base_url", "api_key", "model"}:
                final_kwargs[key] = value

        return final_kwargs

    @staticmethod
    def _build_gemini_kwargs(model_id: str, meta: Dict[str, Any], **kwargs) -> Dict[str, Any]:
        final_kwargs = {
            "model": model_id,
            "google_api_key": meta.get("api_key") or "",
        }
        if "temperature" in kwargs:
            final_kwargs["temperature"] = kwargs["temperature"]
        elif meta.get("global_temperature") is not None:
            final_kwargs["temperature"] = meta.get("global_temperature", 0.0)

        max_tokens = kwargs.get("max_tokens") or meta.get("global_max_tokens")
        if max_tokens:
            final_kwargs["max_output_tokens"] = int(max_tokens)

        for key, value in kwargs.items():
            if key not in {"temperature", "max_tokens", "base_url", "api_key", "model"}:
                final_kwargs[key] = value
        return final_kwargs

    @staticmethod
    def _attach_telemetry(kwargs: Dict[str, Any], meta: Dict[str, Any], *, model_id: str, role: str = "") -> Dict[str, Any]:
        callbacks = list(kwargs.get("callbacks") or [])
        callbacks.append(
            model_telemetry_service.build_chat_callback(
                model_id=model_id,
                provider_id=str(meta.get("provider_id") or meta.get("provider_name") or "unknown"),
                provider_name=str(meta.get("provider_name") or meta.get("provider_id") or "unknown"),
                role=role,
                capability_class=str(meta.get("capability_class") or ""),
                cost_per_input=meta.get("cost_per_input"),
                cost_per_output=meta.get("cost_per_output"),
                is_streaming=bool(kwargs.get("streaming")),
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
        meta = cls._resolve_model_metadata(model_id)
        role = str(kwargs.pop("_role", "") or "")
        
        if not meta.get("is_found"):
            # If the user passed a model completely unregistered, we attempt to initialize it 
            # as OpenAI barebones just in case base_url/api_key are in standard env vars
            return ChatOpenAI(model=model_id, **kwargs)

        if meta.get("oauth_error"):
            raise RuntimeError(str(meta["oauth_error"]))

        api_standard = str(meta.get("api_standard", "openai")).lower()
        try:
            if api_standard == "anthropic":
                return ChatAnthropic(**cls._attach_telemetry(cls._build_chat_kwargs(model_id, meta, **kwargs), meta, model_id=model_id, role=role))
            if api_standard in {"google", "gemini"}:
                if ChatGoogleGenerativeAI is None:
                    raise ImportError("langchain-google-genai is not installed")
                return ChatGoogleGenerativeAI(**cls._attach_telemetry(cls._build_gemini_kwargs(model_id, meta, **kwargs), meta, model_id=model_id, role=role))
            return ChatOpenAI(**cls._attach_telemetry(cls._build_chat_kwargs(model_id, meta, **kwargs), meta, model_id=model_id, role=role))
        except Exception as exc:
            normalized = normalize_provider_error(
                exc,
                provider=meta.get("provider_name"),
                model=model_id,
            )
            raise RuntimeError(
                f"{normalized['code']}: {normalized['message']} ({normalized['userAction']})"
            ) from exc
            
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
            
        return OpenAICompatibleEmbedding(
            model_name=model_id,
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
            
        return RestReranker(
            model_name=model_id,
            api_key=api_key,
            base_url=meta.get("base_url"),
            max_tokens=meta.get("global_context_window"),
            provider_id=str(meta.get("provider_id") or meta.get("provider_name") or ""),
            provider_name=str(meta.get("provider_name") or meta.get("provider_id") or ""),
            role=role,
            capability_class=capability_class,
        )

    @classmethod
    def create_for_role(cls, role: str, **kwargs):
        """Create a chat model for a named system role (e.g. 'supervisor', 'vision', 'summary').
        
        Reads from models.json → roles → {role}, falling back to 'default' role.
        """
        model_id = model_control_plane.resolve_model_for_role(role).get("resolvedModelId") or ""
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
        model_id = model_control_plane.resolve_model_for_role("embedding").get("resolvedModelId") or ""
        if not model_id:
            raise ValueError("No embedding model configured. Set roles.embedding in models.json.")
        return cls.create_embedding_model(model_id, **kwargs)

    @classmethod
    def create_reranker_for_role(cls, role: str = "reranker", **kwargs) -> BaseReranker:
        """Create a reranker model using a named rerank role from models.json."""
        model_id = model_control_plane.resolve_model_for_role(role).get("resolvedModelId") or ""
        if not model_id:
            raise ValueError(f"No reranker model configured. Set roles.{role} in models.json.")
        return cls.create_reranker_model(model_id, role=role, **kwargs)

# Global singleton exporter
llm_factory = LLMFactory()
