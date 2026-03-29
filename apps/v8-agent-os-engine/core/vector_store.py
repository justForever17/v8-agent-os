import abc
from typing import List, Dict, Any
import logging
import uuid
import chromadb

from core.v8_agent_os_paths import V8_AGENT_OS_HOME

# We defer import of MemoryRouter to the class to avoid circular imports if memory_router imports these base classes
# wait, memory_router imports BaseEmbedding, BaseReranker from core.vector_store
logger = logging.getLogger(__name__)

class BaseEmbedding(abc.ABC):
    @abc.abstractmethod
    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        pass
        
    @abc.abstractmethod
    def embed_query(self, text: str) -> List[float]:
        pass

class BaseReranker(abc.ABC):
    @abc.abstractmethod
    def rerank(self, query: str, documents: List[str], top_k: int = 3) -> List[Dict[str, Any]]:
        """Return list of dicts with 'index', 'relevance_score', and 'document'"""
        pass

class VectorStore:
    def __init__(self):
        from core.memory_router import MemoryRouter
        self.router = MemoryRouter()
        
        # Handle cases where models might not be fully configured yet
        try:
            self.embedding_model = self.router.get_embedding_model()
        except ValueError as e:
            logger.warning(f"Embedding model not loaded: {e}")
            self.embedding_model = None
            
        try:
            self.reranker_model = self.router.get_reranker_model()
        except ValueError as e:
            logger.warning(f"Reranker model not loaded: {e}")
            self.reranker_model = None
            
        self.db_dir = V8_AGENT_OS_HOME / "memory" / ".index" / "chroma_db"
        self.db_dir.parent.mkdir(parents=True, exist_ok=True)
        
        # Initialize PersistentClient
        self.client = chromadb.PersistentClient(path=str(self.db_dir))
        
        self.collection_name = "v8_agent_os_memory"
        self.collection = self.client.get_or_create_collection(name=self.collection_name)
        
    def add_documents(self, documents: List[Dict[str, Any]]):
        """
        documents format expected:
        [{"id": "optional", "text": "fact content", "metadata": {"source": "..."}}]
        """
        if not self.collection:
            logger.error("Vector collection not initialized. Cannot add documents.")
            return []
            
        ids = []
        texts = []
        metadatas = []
        
        for doc in documents:
            doc_id = doc.get("id") or str(uuid.uuid4())
            ids.append(doc_id)
            texts.append(doc.get("text") or doc.get("fact", ""))
            
            meta = doc.get("metadata", {})
            # Chroma filters out non-primitive metadata, so clean it
            clean_meta = {k: v for k, v in meta.items() if isinstance(v, (str, int, float, bool))}
            metadatas.append(clean_meta)

        if not self.embedding_model:
            logger.error("Embedding model not initialized. Cannot add vector documents.")
            return []

        embeddings = self.embedding_model.embed_documents(texts)
        self.collection.upsert(
            ids=ids,
            embeddings=embeddings,
            documents=texts,
            metadatas=metadatas
        )
        return ids

    def delete_by_ids(self, ids: List[str]):
        """Delete documents from vector store by their IDs"""
        if not self.collection:
            logger.error("Vector collection not initialized. Cannot delete.")
            return
        if ids:
            self.collection.delete(ids=ids)
        
    def similarity_search_with_rerank(self, query: str, top_k: int = 3, fetch_k: int = 20) -> List[Dict[str, Any]]:
        """
        Retrieves top `fetch_k` from chroma, then reranks to return top `top_k`.
        Returns format: [{"id": id, "text": text, "metadata": meta, "relevance_score": score}]
        """
        if not self.collection:
            logger.error("Vector collection not initialized. Cannot search.")
            return []

        if not self.embedding_model:
            logger.error("Embedding model not initialized. Cannot search vector store.")
            return []

        query_embedding = self.embedding_model.embed_query(query)

        if not self.reranker_model:
            logger.warning("Reranker model not initialized. Performing regular search.")
            top_k_results = self.collection.query(
                query_embeddings=[query_embedding],
                n_results=top_k
            )
            return self._format_chroma_results(top_k_results)

        # 1. First-pass retrieval
        chroma_res = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=fetch_k
        )
        
        if not chroma_res or not chroma_res["documents"] or not chroma_res["documents"][0]:
            return []
            
        docs = chroma_res["documents"][0]
        ids = chroma_res["ids"][0]
        metadatas = chroma_res["metadatas"][0]
        
        # 2. Rerank 
        results = self.reranker_model.rerank(query, docs, top_k=top_k)
        
        reranked_facts = []
        for res in results:
            idx = res["index"]
            fact_data = {
                "id": ids[idx],
                "text": docs[idx],
                "metadata": metadatas[idx] if metadatas else {},
                "relevance_score": res.get("relevance_score", 0.0)
            }
            reranked_facts.append(fact_data)
            
        return reranked_facts
        
    def _format_chroma_results(self, chroma_res: dict) -> List[Dict[str, Any]]:
        if not chroma_res or not chroma_res.get("documents") or not chroma_res["documents"][0]:
            return []
            
        out = []
        docs = chroma_res["documents"][0]
        ids = chroma_res["ids"][0]
        metadatas = chroma_res["metadatas"][0] if chroma_res.get("metadatas") else [{} for _ in docs]
        
        for i, doc in enumerate(docs):
            out.append({
                "id": ids[i],
                "text": doc,
                "metadata": metadatas[i],
                "relevance_score": 0.0 # Unknown without reranker
            })
        return out

# Lazy singleton to avoid circular import with memory_router
_vector_store_instance = None

def get_vector_store() -> VectorStore:
    global _vector_store_instance
    if _vector_store_instance is None:
        _vector_store_instance = VectorStore()
    return _vector_store_instance
