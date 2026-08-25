import sys
from pathlib import Path
from unittest.mock import patch
from requests.exceptions import ConnectionError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.rag.dashscope_text_embedding import DashScopeTextEmbedding


def test_embed_documents_uses_text_embedding_call_and_returns_embeddings():
    class Response:
        status_code = 200
        output = {
            "embeddings": [
                {"embedding": [0.1, 0.2]},
                {"embedding": [0.3, 0.4]},
            ]
        }

    with patch("dashscope.TextEmbedding.call", return_value=Response()) as call:
        embeddings = DashScopeTextEmbedding(model="qwen3.7-text-embedding")
        result = embeddings.embed_documents(["第一段", "第二段"])

    assert result == [[0.1, 0.2], [0.3, 0.4]]
    call.assert_called_once_with(
        model="qwen3.7-text-embedding",
        input=["第一段", "第二段"],
    )


def test_embed_query_returns_single_embedding():
    class Response:
        status_code = 200
        output = {
            "embeddings": [
                {"embedding": [0.5, 0.6]},
            ]
        }

    with patch("dashscope.TextEmbedding.call", return_value=Response()):
        embeddings = DashScopeTextEmbedding(model="qwen3.7-text-embedding")
        result = embeddings.embed_query("查询")

    assert result == [0.5, 0.6]


def test_embed_documents_retries_transient_connection_errors():
    class Response:
        status_code = 200
        output = {"embeddings": [{"embedding": [0.7, 0.8]}]}

    with patch(
        "dashscope.TextEmbedding.call",
        side_effect=[ConnectionError("read timed out"), Response()],
    ) as call:
        embeddings = DashScopeTextEmbedding(
            model="qwen3.7-text-embedding",
            max_retries=2,
            retry_sleep_seconds=0,
        )
        result = embeddings.embed_documents(["第一段"])

    assert result == [[0.7, 0.8]]
    assert call.call_count == 2
