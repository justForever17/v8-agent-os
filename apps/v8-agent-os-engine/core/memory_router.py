from typing import Dict, Any, List

# Relying entirely on the central LLMFactory for all model creation
from core.llm_factory import llm_factory
from core.vector_store import BaseEmbedding, BaseReranker


class MemoryRouter:
    """
    Dynamically loads model preferences from ~/.v8-agent-os/config.json#memory
    and uses the central LLMFactory for instantiation.
    Falls back to models.json roles for model IDs if not set in memory_config.
    """
    def __init__(self):
        from core.storage import storage

        self.memory_config = storage.get_memory_config()

    def _get_model_binding(self, role: str) -> str:
        from core.storage import storage

        return storage.get_role_model_id(role) or ""

    def get_extractor_llm(self):
        extractor_id = self._get_model_binding("extraction")
        if not extractor_id:
            raise ValueError("Extractor model not configured. Set roles.extraction in models.json.")
            
        kwargs = {}
        # Priority: Memory System Config > models.json global limits
        temperature = self.memory_config.get("extraction_temperature")
        if temperature is not None:
            kwargs["temperature"] = temperature
            
        return llm_factory.create_chat_model(extractor_id, **kwargs)

    def get_embedding_model(self) -> BaseEmbedding:
        embed_id = self._get_model_binding("embedding")
        if not embed_id:
            raise ValueError("Embedding model not configured. Set roles.embedding in models.json.")
            
        return llm_factory.create_embedding_model(embed_id)

    def get_reranker_model(self) -> BaseReranker:
        reranker_id = self._get_model_binding("reranker")
        if not reranker_id:
            raise ValueError("Reranker model not configured. Set roles.reranker in models.json.")
            
        return llm_factory.create_reranker_model(reranker_id)
