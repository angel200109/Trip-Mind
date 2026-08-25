"""Evaluate retrieval against relevant chunk IDs.

Run from multi-agents:
    python -X utf8 tests/eval_rag_chunk.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.eval_rag_recall import (
    compute_chunk_retrieval_metrics,
    disable_proxy_for_dashscope,
)


CASES_PATH = Path(__file__).with_name("eval_rag_chunk_cases.json")
OUTPUT_PATH = Path(__file__).with_name("eval_rag_chunk_results.json")


async def run_evaluation():
    disable_proxy_for_dashscope()

    from tools.rag_tool import get_rag_instance

    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    rag = get_rag_instance()
    retrieved_by_case_id = {}

    for case in cases:
        docs = await rag.retrieve_documents(
            case["query"],
            k=5,
            filters={"source_city": case["city"]},
        )
        retrieved_by_case_id[case["id"]] = [
            doc.metadata.get("chunk_id", "") for doc in docs
        ]

    metrics = compute_chunk_retrieval_metrics(
        cases,
        retrieved_by_case_id,
        k=5,
    )
    OUTPUT_PATH.write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return metrics


def main() -> None:
    metrics = asyncio.run(run_evaluation())
    k = 5
    print(f"Total cases: {metrics['total_cases']}")
    print(f"Hit Rate@{k}: {metrics[f'hit_rate_at_{k}']:.2%}")
    print(f"Recall@{k}: {metrics[f'recall_at_{k}']:.2%}")
    print(f"Precision@{k}: {metrics[f'precision_at_{k}']:.2%}")
    print(f"MRR@{k}: {metrics[f'mrr_at_{k}']:.4f}")
    print(f"NDCG@{k}: {metrics[f'ndcg_at_{k}']:.4f}")
    failed = [item for item in metrics["details"] if not item["hit"]]
    print(f"Failed cases: {len(failed)}")
    for item in failed[:10]:
        print(f"- {item['id']}: {item['query']}")
        print(f"  relevant: {item['relevant_chunk_ids']}")
        print(f"  retrieved: {item['retrieved_chunk_ids']}")
    print(f"Results written to: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
