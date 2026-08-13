"""
DashScope Reranker — 使用 gte-rerank 模型对候选文档重新排序

- 调用阿里云 DashScope TextReRank API
- 返回带分数和置信度标记的结果
"""
import asyncio
from typing import List, Dict

from langchain_core.documents import Document


class DashScopeReranker:
    """DashScope gte-rerank 重排序器"""

    def __init__(
        self,
        api_key: str,
        model: str = "gte-rerank",
        top_n: int = 3,
        confidence_threshold: float = 0.3,
    ):
        self._api_key = api_key
        self._model = model
        self._top_n = top_n
        self._confidence_threshold = confidence_threshold

    async def rerank(self, query: str, documents: List[Document]) -> List[Dict]:
        """对文档列表进行重排序

        Returns:
            [{"document": Document, "score": float, "low_confidence": bool}, ...]
        """
        if not documents:
            return []

        texts = [doc.page_content for doc in documents]
        ranked_indices = await asyncio.to_thread(self._call_rerank_api, query, texts)

        results = []
        for idx, score in ranked_indices[: self._top_n]:
            results.append({
                "document": documents[idx],
                "score": score,
                "low_confidence": score < self._confidence_threshold,
            })
        return results

    def _call_rerank_api(self, query: str, documents: List[str]) -> List[tuple]:
        """调用 DashScope TextReRank API

        Returns:
            [(original_index, relevance_score), ...] 按分数降序排列
        """
        import dashscope
        from dashscope import TextReRank

        response = TextReRank.call(
            model=self._model,
            query=query,
            documents=documents,
            top_n=len(documents),
            api_key=self._api_key,
        )

        if response.status_code != 200:
            print(f"  ⚠️ Reranker API 调用失败: {response.code} - {response.message}")
            # 回退: 返回原始顺序
            return [(i, 1.0 / (i + 1)) for i in range(len(documents))]

        results = []
        for item in response.output.results:
            results.append((item.index, item.relevance_score))

        results.sort(key=lambda x: x[1], reverse=True)
        return results
