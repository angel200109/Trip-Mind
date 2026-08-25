"""DashScope TextEmbedding wrapper compatible with Chroma/LangChain."""
from __future__ import annotations

import time
from typing import List

from requests.exceptions import ConnectionError, ReadTimeout, Timeout


class DashScopeTextEmbedding:
    """Embedding function backed by dashscope.TextEmbedding.call."""

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        max_retries: int = 3,
        retry_sleep_seconds: float = 2.0,
    ):
        self.model = model
        self.api_key = api_key
        self.max_retries = max_retries
        self.retry_sleep_seconds = retry_sleep_seconds

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        return self._embed(texts)

    def embed_query(self, text: str) -> List[float]:
        embeddings = self._embed([text])
        return embeddings[0] if embeddings else []

    def _embed(self, inputs: List[str]) -> List[List[float]]:
        import dashscope
        from http import HTTPStatus

        kwargs = {
            "model": self.model,
            "input": inputs,
        }
        if self.api_key:
            kwargs["api_key"] = self.api_key

        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = dashscope.TextEmbedding.call(**kwargs)
                break
            except (ConnectionError, ReadTimeout, Timeout) as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    raise
                print(
                    f"DashScope TextEmbedding transient error; retry "
                    f"{attempt}/{self.max_retries}: {exc}",
                    flush=True,
                )
                time.sleep(self.retry_sleep_seconds)
        else:
            raise RuntimeError("DashScope TextEmbedding failed") from last_error

        status_code = getattr(response, "status_code", None)
        if status_code != HTTPStatus.OK:
            code = getattr(response, "code", "")
            message = getattr(response, "message", "")
            raise ValueError(
                f"DashScope TextEmbedding failed: status_code={status_code}, "
                f"code={code}, message={message}"
            )

        output = getattr(response, "output", None)
        if output is None and isinstance(response, dict):
            output = response.get("output")

        embeddings = _extract_embeddings(output)
        if len(embeddings) != len(inputs):
            raise ValueError(
                f"DashScope TextEmbedding returned {len(embeddings)} embeddings "
                f"for {len(inputs)} inputs"
            )
        return embeddings


def _extract_embeddings(output) -> List[List[float]]:
    if isinstance(output, dict):
        raw_embeddings = output.get("embeddings", [])
    else:
        raw_embeddings = getattr(output, "embeddings", [])

    result: List[List[float]] = []
    for item in raw_embeddings:
        if isinstance(item, dict):
            vector = item.get("embedding")
        else:
            vector = getattr(item, "embedding", None)
        if vector is None:
            raise ValueError("DashScope TextEmbedding response item missing embedding")
        result.append(list(vector))
    return result
