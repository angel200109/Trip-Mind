"""
通用文档切分

设计原则:
- 切分层: 使用通用 RecursiveCharacterTextSplitter，不依赖特定文档格式
- 保留原始 Document 元数据
- 不绑定特定文档结构，换数据源无需改代码
"""
from typing import List

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter


class DocumentChunker:
    """通用文档切分器 + LLM 元数据抽取"""

    def __init__(
        self,
        chunk_size: int = 800,
        chunk_overlap: int = 100,
    ):
        self._splitter = RecursiveCharacterTextSplitter(
            separators=["\n\n", "\n", "。", "！", "？", "；", "，", " "],
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            keep_separator=True,
            length_function=len,
        )

    def chunk_documents(self, documents: List[Document]) -> List[Document]:
        """通用切分，适用于任何格式的文档"""
        chunks = self._splitter.split_documents(documents)

        # 确保每个 chunk 都有标准 metadata 字段
        for chunk in chunks:
            chunk.metadata.setdefault("source_city", "")
            chunk.metadata.setdefault("type", "")

        return chunks
