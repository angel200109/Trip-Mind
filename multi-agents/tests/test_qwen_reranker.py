import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.rag.reranker import DashScopeReranker


def test_qwen_reranker_posts_to_maas_endpoint_and_parses_scores():
    class Response:
        status_code = 200

        def json(self):
            return {
                "output": {
                    "results": [
                        {"index": 1, "relevance_score": 0.91},
                        {"index": 0, "relevance_score": 0.42},
                    ]
                }
            }

    with patch("requests.post", return_value=Response()) as post:
        reranker = DashScopeReranker(api_key="test-key", model="qwen3-rerank", top_n=5)
        ranked = reranker._call_rerank_api("什么是文本排序模型", ["文档A", "文档B"])

    assert ranked == [(1, 0.91), (0, 0.42)]
    post.assert_called_once()
    _, kwargs = post.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer test-key"
    assert kwargs["headers"]["Content-Type"] == "application/json"
    assert kwargs["json"] == {
        "model": "qwen3-rerank",
        "input": {
            "query": "什么是文本排序模型",
            "documents": ["文档A", "文档B"],
        },
        "parameters": {
            "return_documents": True,
            "top_n": 5,
        },
    }
