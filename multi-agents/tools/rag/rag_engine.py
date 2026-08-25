"""
RAG 引擎 — 编排 chunker / retriever / reranker / query_transformer

保持与原 TravelRAG 相同的对外接口，内部升级为:
query_rewrite → hybrid_retrieve(BM25+Vector) → rerank → 返回带分数的结果
"""
import os
import hashlib
import uuid
import asyncio
from pathlib import Path
from typing import List, Optional, Dict, Any

from langchain_chroma import Chroma
from langchain_community.document_loaders import (
    PyPDFLoader, TextLoader, CSVLoader, DirectoryLoader,
)
from langchain_core.documents import Document
from langchain_openai import ChatOpenAI

from config.settings import (
    DASHSCOPE_API_KEY,
    EMBEDDING_MODEL,
    CHROMA_PERSIST_DIR,
    RAG_CHUNK_SIZE,
    RAG_CHUNK_OVERLAP,
    RAG_SEARCH_K,
    RAG_BATCH_SIZE,
    QWEN3_API_BASE,
    QWEN3_MODEL,
    QWEN3_TEMPERATURE,
)

from .chunker import DocumentChunker
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
        self.chunker = DocumentChunker(
            chunk_size=RAG_CHUNK_SIZE,
            chunk_overlap=RAG_CHUNK_OVERLAP,
            llm=self._create_llm(),
            batch_size=RAG_BATCH_SIZE,
        )
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
            print("   使用 build_knowledge_base() 方法创建知识库")

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
    # 知识库构建
    # ------------------------------------------------------------------

    def load_documents(self, source_path: str, file_type: str = "auto") -> List[Document]:
        """加载文档 (复用原有逻辑)"""
        documents = []
        path = Path(source_path)

        if file_type == "auto":
            if path.is_dir():
                file_type = "directory"
            else:
                ext = path.suffix.lower()
                type_map = {".txt": "txt", ".md": "md", ".pdf": "pdf", ".csv": "csv"}
                file_type = type_map.get(ext, "txt")

        if file_type in ("txt", "md"):
            loader = TextLoader(source_path, encoding="utf-8")
            documents = loader.load()
        elif file_type == "pdf":
            loader = PyPDFLoader(source_path)
            documents = loader.load()
        elif file_type == "csv":
            loader = CSVLoader(source_path, encoding="utf-8")
            documents = loader.load()
        elif file_type == "directory":
            for glob_pat, loader_cls, kwargs in [
                ("**/*.txt", TextLoader, {"encoding": "utf-8"}),
                ("**/*.md", TextLoader, {"encoding": "utf-8"}),
                ("**/*.pdf", PyPDFLoader, {}),
                ("**/*.csv", CSVLoader, {"encoding": "utf-8"}),
            ]:
                try:
                    dl = DirectoryLoader(
                        source_path, glob=glob_pat,
                        loader_cls=loader_cls,
                        loader_kwargs=kwargs,
                        show_progress=True,
                    )
                    docs = dl.load()
                    documents.extend(docs)
                    if docs:
                        print(f"  ✅ {glob_pat}: 加载 {len(docs)} 个文档")
                except Exception as e:
                    print(f"  ⚠️ {glob_pat} 加载失败: {e}")

        print(f"\n✅ 总共加载 {len(documents)} 个原始文档")
        return documents

    def build_knowledge_base(
        self,
        source_path: str,
        file_type: str = "directory",
        force_recreate: bool = False,
    ):
        """构建知识库: 通用切分 → LLM 元数据抽取 → ChromaDB 入库 → BM25 索引"""
        print(f"\n📂 正在加载文档: {source_path}")

        # 加载
        documents = self.load_documents(source_path, file_type)

        # 切分 + LLM 元数据抽取
        print(f"✂️ 切分 + 元数据抽取中...")
        split_docs = asyncio.run(self.chunker.chunk_and_extract_metadata(documents))
        print(f"✅ 切分完成: {len(split_docs)} 个 chunks")

        # 生成 UUID
        doc_ids = [self._generate_doc_id(doc, idx) for idx, doc in enumerate(split_docs)]

        # 去重
        if not force_recreate and self.imported_ids:
            new_mask = [doc_id not in self.imported_ids for doc_id in doc_ids]
            duplicate_count = sum(1 for m in new_mask if not m)
            if duplicate_count > 0:
                print(f"  ⚠️ 跳过 {duplicate_count} 个重复 chunks")
                split_docs = [d for d, m in zip(split_docs, new_mask) if m]
                doc_ids = [i for i, m in zip(doc_ids, new_mask) if m]
                print(f"  ✅ 新增 {len(split_docs)} 个 chunks")

        if not split_docs:
            print(f"⚠️ 没有新文档需要导入")
            return

        # 强制重建时清除旧库
        if force_recreate and os.path.exists(self.persist_directory):
            import gc
            import shutil
            import time
            if self.vector_store:
                try:
                    self.vector_store._client.close()
                except Exception:
                    pass
                self.vector_store = None
            gc.collect()  # 释放引用，便于 SQLite 句柄关闭 (Windows)
            # Windows 下 Chroma 的 sqlite 句柄可能延迟释放，带重试删除
            for attempt in range(5):
                try:
                    shutil.rmtree(self.persist_directory)
                    break
                except PermissionError:
                    if attempt == 4:
                        raise PermissionError(
                            f"无法删除旧向量库 {self.persist_directory}：文件被其他进程占用。\n"
                            f"请先停止占用该文件的进程（如运行中的 server.py / uvicorn / Jupyter），再重试。"
                        ) from None
                    time.sleep(1)
            self.imported_ids.clear()

        # 批量入库 ChromaDB
        print(f"📊 正在构建向量数据库...")
        for i in range(0, len(split_docs), RAG_BATCH_SIZE):
            batch = split_docs[i:i + RAG_BATCH_SIZE]
            batch_ids = doc_ids[i:i + RAG_BATCH_SIZE]

            if i == 0 and (force_recreate or not self.vector_store):
                self.vector_store = Chroma.from_documents(
                    documents=batch,
                    embedding=self.embeddings,
                    persist_directory=self.persist_directory,
                    collection_name="travel_knowledge",
                    ids=batch_ids,
                )
            else:
                self.vector_store.add_documents(documents=batch, ids=batch_ids)

            self.imported_ids.update(batch_ids)
            print(f"  进度: {min(i + RAG_BATCH_SIZE, len(split_docs))}/{len(split_docs)}")

        # 构建 BM25 索引
        print(f"📊 构建 BM25 索引...")
        self._hybrid_retriever = None  # reset
        retriever = self._get_hybrid_retriever()
        retriever.build_bm25_index(split_docs)

        print(f"✅ 知识库构建完成！总计 {len(self.imported_ids)} 条")

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
