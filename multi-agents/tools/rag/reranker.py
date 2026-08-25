"""
DashScope Reranker — 使用 qwen3-rerank 模型对候选文档重新排序

- 调用阿里云百炼 MaaS text-rerank HTTP API
- 返回带分数和置信度标记的结果
"""
import asyncio
from typing import List, Dict

from langchain_core.documents import Document


class DashScopeReranker:
    """DashScope qwen3-rerank 重排序器"""

    DEFAULT_ENDPOINT = (
        "https://llm-n6wcd20lzrg8as0z.cn-beijing.maas.aliyuncs.com"
        "/api/v1/services/rerank/text-rerank/text-rerank"
    )

    def __init__(
        self,
        api_key: str,
        model: str = "qwen3-rerank",
        top_n: int = 5,
        confidence_threshold: float = 0.3,
        endpoint: str = DEFAULT_ENDPOINT,
    ):
        self._api_key = api_key
        self._model = model
        self._top_n = top_n
        self._confidence_threshold = confidence_threshold
        self._endpoint = endpoint

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
        """调用阿里云百炼 text-rerank HTTP API

        Returns:
            [(original_index, relevance_score), ...] 按分数降序排列
        """
        import requests

        response = requests.post(
            self._endpoint,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self._model,
                "input": {
                    "query": query,
                    "documents": documents,
                },
                "parameters": {
                    "return_documents": True,
                    "top_n": self._top_n,
                },
            },
            timeout=60,
        )

        if response.status_code != 200:
            try:
                error_body = response.json()
            except Exception:
                error_body = response.text
            print(f"  ⚠️ Reranker API 调用失败: {response.status_code} - {error_body}")
            # 回退: 返回原始顺序
            return [(i, 1.0 / (i + 1)) for i in range(len(documents))]

        results = []
        payload = response.json()
        output = payload.get("output", {})
        for item in output.get("results", []):
            results.append((item["index"], item["relevance_score"]))

        results.sort(key=lambda x: x[1], reverse=True)
        return results
