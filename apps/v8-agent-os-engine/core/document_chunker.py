import logging
from typing import List, Dict, Any
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter

logger = logging.getLogger(__name__)

class DocumentChunker:
    """
    Semantic Chunker based on Markdown Headers.
    If chunks are still too large after header splitting, it falls back to RecursiveCharacterTextSplitter.
    """
    
    @classmethod
    def chunk_markdown(cls, markdown_text: str, chunk_size: int = 1500, chunk_overlap: int = 200) -> List[Dict[str, Any]]:
        """
        Split markdown text based on headers, and return chunks with header metadata.
        Returns a list of dicts: {"text": str, "metadata": dict}
        """
        headers_to_split_on = [
            ("#", "Header 1"),
            ("##", "Header 2"),
            ("###", "Header 3"),
            ("####", "Header 4"),
        ]
        
        try:
            markdown_splitter = MarkdownHeaderTextSplitter(
                headers_to_split_on=headers_to_split_on,
                strip_headers=False
            )
            md_header_splits = markdown_splitter.split_text(markdown_text)
            
            # Additional layer to ensure no chunk exceeds the max chunk_size constraint
            recursive_splitter = RecursiveCharacterTextSplitter(
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                separators=["\n\n", "\n", " ", ""]
            )
            
            final_chunks = []
            for split in md_header_splits:
                if len(split.page_content) > chunk_size:
                    sub_splits = recursive_splitter.split_text(split.page_content)
                    for sub in sub_splits:
                        # Re-inject the retained metadata into the recursive slices
                        final_chunks.append({
                            "text": sub,
                            "metadata": split.metadata
                        })
                else:
                    final_chunks.append({
                        "text": split.page_content,
                        "metadata": split.metadata
                    })
                    
            return final_chunks
            
        except Exception as e:
            logger.error(f"[DocumentChunker] Failed to perform semantic chunking: {e}")
            # Ultimate Fallback if markdown splitting fails
            fallback_splitter = RecursiveCharacterTextSplitter(
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap
            )
            fallback_splits = fallback_splitter.split_text(markdown_text)
            return [{"text": txt, "metadata": {}} for txt in fallback_splits]

document_chunker = DocumentChunker()
