"""RAG 检索评测公共函数。"""
from __future__ import annotations

import math
import os
import sys
from typing import Any, Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def disable_proxy_for_dashscope() -> None:
    """Avoid broken local proxy settings when calling DashScope during eval."""
    for key in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"]:
        os.environ.pop(key, None)
    no_proxy = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""
    entries = [item.strip() for item in no_proxy.split(",") if item.strip()]
    for host in ["dashscope.aliyuncs.com", "aliyuncs.com"]:
        if host not in entries:
            entries.append(host)
    os.environ["NO_PROXY"] = ",".join(entries)


def _dcg_at_k(retrieved: List[str], expected_set: set[str], k: int) -> float:
    score = 0.0
    for rank, name in enumerate(retrieved[:k], 1):
        relevance = 1 if name in expected_set else 0
        if relevance:
            score += relevance / math.log2(rank + 1)
    return score


def _dedupe_preserving_order(names: List[str]) -> List[str]:
    """Treat multiple chunks from one scenic spot as one retrieval result."""
    seen: set[str] = set()
    unique_names: List[str] = []
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        unique_names.append(name)
    return unique_names


def compute_chunk_retrieval_metrics(
    cases: List[Dict[str, Any]],
    retrieved_by_case_id: Dict[str, List[str]],
    k: int = 5,
) -> Dict[str, Any]:
    """Compute retrieval metrics against explicitly labeled relevant chunk IDs."""
    details = []
    total_relevant = 0
    total_hits = 0
    hit_cases = 0
    reciprocal_ranks = []
    ndcg_scores = []

    for case in cases:
        case_id = case["id"]
        relevant = set(case.get("relevant_chunk_ids", []))
        retrieved = _dedupe_preserving_order(
            retrieved_by_case_id.get(case_id, [])
        )[:k]
        hits = relevant & set(retrieved)
        total_relevant += len(relevant)
        total_hits += len(hits)
        hit = bool(hits)
        if hit:
            hit_cases += 1

        first_hit_rank = next(
            (rank for rank, chunk_id in enumerate(retrieved, 1) if chunk_id in relevant),
            None,
        )
        reciprocal_ranks.append(1 / first_hit_rank if first_hit_rank else 0)

        dcg = _dcg_at_k(retrieved, relevant, k)
        idcg = _dcg_at_k(list(relevant)[: min(len(relevant), k)], relevant, k)
        ndcg_scores.append(dcg / idcg if idcg else 0)
        details.append({
            "id": case_id,
            "query": case.get("query", ""),
            "relevant_chunk_ids": list(case.get("relevant_chunk_ids", [])),
            "retrieved_chunk_ids": retrieved,
            "hit": hit,
            "recall": len(hits) / len(relevant) if relevant else 0,
            "precision": len(hits) / k if k else 0,
            "mrr": reciprocal_ranks[-1],
            "ndcg": ndcg_scores[-1],
        })

    total_cases = len(cases)
    return {
        "total_cases": total_cases,
        f"hit_rate_at_{k}": hit_cases / total_cases if total_cases else 0,
        f"recall_at_{k}": total_hits / total_relevant if total_relevant else 0,
        f"precision_at_{k}": total_hits / (total_cases * k) if total_cases and k else 0,
        f"mrr_at_{k}": sum(reciprocal_ranks) / total_cases if total_cases else 0,
        f"ndcg_at_{k}": sum(ndcg_scores) / total_cases if total_cases else 0,
        "details": details,
    }

