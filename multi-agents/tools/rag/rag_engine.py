"""
RAG 引擎 — 编排 chunker / retriever / reranker / query_transformer

保持与原 TravelRAG 相同的对外接口，内部升级为:
query_rewrite → hybrid_retrieve(BM25+Vector) → rerank → 返回带分数的结果
"""
import os
import hashlib
import uuid
import asyncio
from typing import List, Optional, Dict, Any

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_openai import ChatOpenAI

from config.settings import (
    DASHSCOPE_API_KEY,
    EMBEDDING_MODEL,
    CHROMA_PERSIST_DIR,
    RAG_SEARCH_K,
    QWEN3_API_BASE,
    QWEN3_MODEL,
    QWEN3_TEMPERATURE,
)

from .dashscope_text_embedding import DashScopeTextEmbedding
from .query_transformer import QueryTransformer
from .retriever import HybridRetriever
from .reranker import DashScopeReranker

# 延迟导入的新配置（兼容旧 settings.py 尚未添加新字段）
def _get_config(name: str, default):
    from config import settings
    return getattr(settings, name, default)


class TravelRAG:
    """旅游攻略 RAG 系统 (v2)

    pipeline: query_rewrite → hybrid_retrieve → rerank → format
    """

    def __init__(
        self,
        persist_directory: Optional[str] = None,
        embedding_api_key: Optional[str] = None,
    ):
        self.persist_directory = persist_directory or str(CHROMA_PERSIST_DIR)
        self.embedding_api_key = embedding_api_key or DASHSCOPE_API_KEY
        self.imported_ids: set = set()

        # Embeddings
        self.embeddings = DashScopeTextEmbedding(
            model=EMBEDDING_MODEL,
            api_key=self.embedding_api_key,
        )

        # 子组件
        self._query_transformer: Optional[QueryTransformer] = None
        self._hybrid_retriever: Optional[HybridRetriever] = None
        self._reranker: Optional[DashScopeReranker] = None

        # 向量库
        self.vector_store: Optional[Chroma] = None
        self._initialize_vector_store()

    # ------------------------------------------------------------------
    # 初始化
    # ------------------------------------------------------------------

    def _initialize_vector_store(self):
        """初始化向量数据库 + BM25 索引"""
        if os.path.exists(self.persist_directory):
            print(f"✅ 加载现有向量数据库: {self.persist_directory}")
            self.vector_store = Chroma(
                persist_directory=self.persist_directory,
                embedding_function=self.embeddings,
                collection_name="travel_knowledge",
            )
            # 加载已存在的 ID
            try:
                existing_data = self.vector_store.get()
                if existing_data and "ids" in existing_data:
                    self.imported_ids = set(existing_data["ids"])
                    print(f"  📊 已加载 {len(self.imported_ids)} 条现有数据")

                    # 从已有文档构建 BM25 索引
                    if existing_data.get("documents"):
                        docs = []
                        for i, content in enumerate(existing_data["documents"]):
                            meta = existing_data["metadatas"][i] if existing_data.get("metadatas") else {}
                            docs.append(Document(page_content=content, metadata=meta or {}))
                        self._get_hybrid_retriever().build_bm25_index(docs)
            except Exception as e:
                print(f"  ⚠️ 初始化辅助索引失败: {e}")
        else:
            print(f"⚠️ 向量数据库不存在: {self.persist_directory}")
            print("   请运行 scripts/rebuild_kb.py 创建知识库")

    def _create_llm(self) -> ChatOpenAI:
        """创建共用的 LLM 实例"""
        return ChatOpenAI(
            model=QWEN3_MODEL,
            openai_api_base=QWEN3_API_BASE,
            openai_api_key=DASHSCOPE_API_KEY,
            temperature=QWEN3_TEMPERATURE,
            timeout=30,
            max_retries=1,
        )

    def _get_query_transformer(self) -> QueryTransformer:
        """延迟初始化 QueryTransformer"""
        if self._query_transformer is None:
            self._query_transformer = QueryTransformer(self._create_llm())
        return self._query_transformer

    def _get_hybrid_retriever(self) -> HybridRetriever:
        """延迟初始化 HybridRetriever"""
        if self._hybrid_retriever is None:
            self._hybrid_retriever = HybridRetriever(
                vector_store=self.vector_store,
                bm25_weight=_get_config("RAG_BM25_WEIGHT", 0.5),
                vector_weight=_get_config("RAG_VECTOR_WEIGHT", 0.5),
            )
        return self._hybrid_retriever

    def _get_reranker(self) -> DashScopeReranker:
        """延迟初始化 Reranker"""
        if self._reranker is None:
            self._reranker = DashScopeReranker(
                api_key=self.embedding_api_key,
                model=_get_config("RAG_RERANK_MODEL", "gte-rerank"),
                top_n=_get_config("RAG_RERANK_TOP_N", 5),
                confidence_threshold=_get_config("RAG_CONFIDENCE_THRESHOLD", 0.3),
            )
        return self._reranker

    # ------------------------------------------------------------------
    # 检索 (核心 pipeline)
    # ------------------------------------------------------------------

    async def search(self, query: str, k: Optional[int] = None, filters: Optional[Dict[str, Any]] = None) -> str:
        """完整检索 pipeline: rewrite → hybrid → rerank → format

        Args:
            query: 用户查询
            k: 最终返回数量 (默认 RAG_SEARCH_K)
            filters: 可选 metadata 过滤 (type)

        Returns:
            格式化的检索结果字符串
        """
        print(f"\n{'='*60}")
        print(f"📚 RAG检索 (v2): {query}")
        if filters:
            print(f"  过滤条件: {filters}")
        print(f"{'='*60}")

        if not self.vector_store:
            print(f"❌ 知识库未初始化")
            return "知识库未初始化，请先构建知识库"

        ranked_docs = await self.retrieve_documents(query, k=k, filters=filters)
        if not ranked_docs:
            print(f"❌ 未找到相关结果")
            return "未找到相关旅游攻略"

        k = k or RAG_SEARCH_K

        # 4. 格式化输出
        results = []
        for i, doc in enumerate(ranked_docs[:k], 1):
            score = doc.metadata.get("rerank_score", 0.0)
            content = doc.page_content
            source = doc.metadata.get("source", "未知")
            spot_name = doc.metadata.get("spot_name", "")
            city = doc.metadata.get("source_city", "")

            meta_parts = [f"来源: {source}"]
            if city:
                meta_parts.append(f"城市: {city}")
            if spot_name:
                meta_parts.append(f"景点: {spot_name}")
            meta_parts.append(f"相关度: {score:.2f}")

            meta_str = " | ".join(meta_parts)
            confidence_tag = " [低置信度]" if doc.metadata.get("low_confidence") else ""

            print(f"\n  [{i}] {meta_str}{confidence_tag}")
            print(f"      内容预览: {content[:80]}...")

            results.append(f"[{i}] {meta_str}{confidence_tag}\n{content}")

        print(f"\n{'='*60}\n")
        return "\n\n".join(results)

    async def retrieve_documents(
        self,
        query: str,
        k: Optional[int] = None,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[Document]:
        """Return reranked Document objects for evaluation and programmatic use."""
        if not self.vector_store:
            return []

        k = k or RAG_SEARCH_K
        retrieve_k = max(_get_config("RAG_RETRIEVE_K", 10), k)

        # 1. Query 扩展
        enable_rewrite = _get_config("RAG_ENABLE_QUERY_REWRITE", True)
        if enable_rewrite:
            try:
                n = _get_config("RAG_MULTI_QUERY_COUNT", 3)
                queries = await asyncio.wait_for(
                    self._get_query_transformer().expand_queries(query, n=n),
                    timeout=30,
                )
                print(f"  🔄 Query 扩展: {queries}")
            except Exception as e:
                print(f"  ⚠️ Query 扩展失败或超时，使用原始查询: {e}")
                queries = [query]
        else:
            queries = [query]

        # 2. 混合检索
        retriever = self._get_hybrid_retriever()
        if not retriever.is_ready:
            print(f"  ⚠️ BM25 索引未就绪，仅使用向量检索")
            candidates = await retriever._vector_search(query, k=retrieve_k, filters=filters)
        else:
            candidates = await retriever.retrieve(queries, k=retrieve_k, filters=filters)

        if not candidates:
            return []

        print(f"  ✅ 混合检索返回 {len(candidates)} 条候选")

        # 3. Rerank
        try:
            reranker = self._get_reranker()
            ranked = await reranker.rerank(query, candidates)
            print(f"  ✅ Rerank 完成，返回 {len(ranked)} 条结果")
        except Exception as e:
            print(f"  ⚠️ Rerank 失败，使用原始排序: {e}")
            ranked = [{"document": doc, "score": 1.0 / (i + 1), "low_confidence": False}
                      for i, doc in enumerate(candidates[:k])]

        results = []
        for item in ranked[:k]:
            doc = item["document"]
            doc.metadata = dict(doc.metadata)
            doc.metadata["rerank_score"] = item["score"]
            doc.metadata["low_confidence"] = item.get("low_confidence", False)
            results.append(doc)
        return results

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def _generate_doc_id(doc: Document, chunk_index: int = 0) -> str:
        """生成稳定文档 ID"""
        content = doc.page_content
        source = doc.metadata.get("source", "")
        # Use source plus full chunk content so IDs do not shift when document order changes.
        hash_input = f"{source}:{content}"
        hash_hex = hashlib.md5(hash_input.encode("utf-8")).hexdigest()
        return str(uuid.UUID(hash_hex))

    def delete_by_source(self, source_path: str) -> int:
        """根据来源删除数据"""
        if not self.vector_store:
            return 0
        try:
            results = self.vector_store.get(where={"source": source_path})
            if results and results["ids"]:
                count = len(results["ids"])
                self.vector_store.delete(ids=results["ids"])
                self.imported_ids -= set(results["ids"])
                print(f"✅ 已删除 {count} 条 (来源: {source_path})")
                return count
        except Exception as e:
            print(f"❌ 删除失败: {e}")
        return 0

    def get_stats(self) -> dict:
        """获取知识库统计"""
        if not self.vector_store:
            return {"total": 0, "sources": []}
        try:
            results = self.vector_store.get()
            sources = set(m.get("source", "未知") for m in results.get("metadatas", []))
            return {"total": len(results.get("ids", [])), "sources": list(sources)}
        except Exception:
            return {"total": 0, "sources": []}
