"""
混合检索: BM25 关键词检索 + ChromaDB 向量检索 + RRF 融合

- 使用 jieba 分词为 BM25 提供中文支持
- 使用 rank_bm25.BM25Okapi 做关键词检索
- 通过 Reciprocal Rank Fusion (RRF) 融合两路结果
- 支持 ChromaDB metadata 过滤
"""
import asyncio
import hashlib
from typing import List, Dict, Optional, Any

import jieba
from rank_bm25 import BM25Okapi
from langchain_core.documents import Document
from langchain_chroma import Chroma


class HybridRetriever:
    """BM25 + Vector 混合检索器"""

    def __init__(
        self,
        vector_store: Chroma,
        bm25_weight: float = 0.5,
        vector_weight: float = 0.5,
        rrf_k: int = 60,
    ):
        self._vector_store = vector_store
        self._bm25_weight = bm25_weight
        self._vector_weight = vector_weight
        self._rrf_k = rrf_k

        self._bm25: Optional[BM25Okapi] = None
        self._bm25_docs: List[Document] = []
        self._tokenized_corpus: List[List[str]] = []

    @property
    def is_ready(self) -> bool:
        return self._bm25 is not None and self._vector_store is not None

    def build_bm25_index(self, documents: List[Document]) -> None:
        """构建 BM25 索引（内存中）"""
        self._bm25_docs = documents
        self._tokenized_corpus = [self._tokenize(doc.page_content) for doc in documents]
        self._bm25 = BM25Okapi(self._tokenized_corpus)
        print(f"  📊 BM25 索引构建完成: {len(documents)} 条文档")

    async def retrieve(
        self,
        queries: List[str],
        k: int = 10,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[Document]:
        """多 query 混合检索 → RRF 融合 → 去重 → top-k"""
        if not self.is_ready:
            return []

        all_vector_results: Dict[str, tuple] = {}  # content_hash → (doc, best_rank)
        all_bm25_results: Dict[str, tuple] = {}

        for query in queries:
            # 向量检索
            v_docs = await self._vector_search(query, k=k, filters=filters)
            for rank, doc in enumerate(v_docs):
                h = self._doc_hash(doc)
                if h not in all_vector_results or rank < all_vector_results[h][1]:
                    all_vector_results[h] = (doc, rank)

            # BM25 检索
            b_docs = self._bm25_search(query, k=k)
            for rank, doc in enumerate(b_docs):
                h = self._doc_hash(doc)
                if h not in all_bm25_results or rank < all_bm25_results[h][1]:
                    all_bm25_results[h] = (doc, rank)

        # RRF 融合
        scores: Dict[str, float] = {}
        doc_map: Dict[str, Document] = {}

        for h, (doc, rank) in all_vector_results.items():
            rrf_score = self._vector_weight / (rank + self._rrf_k)
            scores[h] = scores.get(h, 0) + rrf_score
            doc_map[h] = doc

        for h, (doc, rank) in all_bm25_results.items():
            rrf_score = self._bm25_weight / (rank + self._rrf_k)
            scores[h] = scores.get(h, 0) + rrf_score
            if h not in doc_map:
                doc_map[h] = doc

        # 按融合分数排序，取 top-k
        sorted_hashes = sorted(scores.keys(), key=lambda h: scores[h], reverse=True)
        results = [doc_map[h] for h in sorted_hashes[:k]]
        return results

    async def _vector_search(
        self, query: str, k: int, filters: Optional[Dict[str, Any]]
    ) -> List[Document]:
        """ChromaDB 向量检索，支持 metadata 过滤"""
        kwargs = {"k": k}
        if filters:
            # 构建 ChromaDB where 条件
            where = self._build_where_clause(filters)
            if where:
                kwargs["filter"] = where

        docs = await asyncio.to_thread(
            self._vector_store.similarity_search, query, **kwargs
        )
        return docs

    def _bm25_search(self, query: str, k: int) -> List[Document]:
        """BM25 关键词检索"""
        if not self._bm25:
            return []

        tokenized_query = self._tokenize(query)
        scores = self._bm25.get_scores(tokenized_query)

        # 取 top-k 索引
        top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:k]
        return [self._bm25_docs[i] for i in top_indices if scores[i] > 0]

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        """jieba 搜索模式分词"""
        return list(jieba.cut_for_search(text))

    @staticmethod
    def _build_where_clause(filters: Dict[str, Any]) -> Optional[Dict]:
        """构建 ChromaDB where 过滤条件"""
        conditions = []
        for key, value in filters.items():
            if value:
                conditions.append({key: {"$eq": value}})

        if not conditions:
            return None
        if len(conditions) == 1:
            return conditions[0]
        return {"$and": conditions}

    @staticmethod
    def _doc_hash(doc: Document) -> str:
        """生成检索去重键，优先使用 chunk_id，避免跨景点误合并。"""
        chunk_id = doc.metadata.get("chunk_id")
        if chunk_id:
            return str(chunk_id)

        source = doc.metadata.get("source", "")
        fallback = f"{source}:{doc.page_content}"
        return hashlib.md5(fallback.encode("utf-8")).hexdigest()[:16]
