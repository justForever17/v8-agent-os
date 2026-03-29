import logging
import os
from typing import List, Dict, Any
from langchain_text_splitters import RecursiveCharacterTextSplitter, Language

logger = logging.getLogger(__name__)

class CodeChunker:
    """
    Semantic Code Chunker using Language-specific splitters (AST/Regex heuristics).
    Produces complete contextual chunks containing full functions/classes when possible.
    """
    
    @classmethod
    def chunk_code(cls, code_text: str, filename: str, chunk_size: int = 1500, chunk_overlap: int = 200) -> List[Dict[str, Any]]:
        """
        Split source code files using language-specific syntax boundaries.
        Returns a list of dicts: {"text": str, "metadata": dict}
        """
        ext = os.path.splitext(filename)[1].lower()
        
        ext_map = {
            ".py": Language.PYTHON,
            ".js": Language.JS,
            ".ts": Language.TS,
            ".tsx": Language.TS,
            ".jsx": Language.JS,
            ".html": Language.HTML,
            ".htm": Language.HTML,
            ".go": Language.GO,
            ".java": Language.JAVA,
            ".cpp": Language.CPP,
            ".c": Language.C,
            ".cs": Language.CSHARP,
            ".rb": Language.RUBY,
            ".php": Language.PHP,
            ".rs": Language.RUST,
            ".md": Language.MARKDOWN,
            ".mdx": Language.MARKDOWN
        }
        
        lang = ext_map.get(ext)
        
        try:
            if lang:
                splitter = RecursiveCharacterTextSplitter.from_language(
                    language=lang, chunk_size=chunk_size, chunk_overlap=chunk_overlap
                )
            else:
                # Fallback generic code splitter
                splitter = RecursiveCharacterTextSplitter(
                    chunk_size=chunk_size, chunk_overlap=chunk_overlap
                )
                
            splits = splitter.split_text(code_text)
            
            final_chunks = []
            for split in splits:
                final_chunks.append({
                    "text": split,
                    "metadata": {"language": lang.value if lang else "unknown"}
                })
            return final_chunks
            
        except Exception as e:
            logger.error(f"[CodeChunker] Failed to perform code chunking for {filename}: {e}")
            fallback_splitter = RecursiveCharacterTextSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
            fallback_splits = fallback_splitter.split_text(code_text)
            return [{"text": txt, "metadata": {}} for txt in fallback_splits]

code_chunker = CodeChunker()
