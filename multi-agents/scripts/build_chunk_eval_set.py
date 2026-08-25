"""Build a chunk-level evaluation set from the finalized chunk catalog."""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = PROJECT_ROOT / "data" / "dataRAG" / "citydata_chunk_catalog.json"
OUTPUT_PATH = PROJECT_ROOT / "tests" / "eval_rag_chunk_cases.json"

QUERY_TEMPLATES = [
    "第一次去{spot}，应该怎么安排游玩路线？",
    "请介绍{spot}的开放时间、门票、特色和游玩注意事项。",
    "去{spot}旅游有哪些实用攻略和推荐玩法？",
    "如果想完整游玩{spot}，应该重点了解哪些信息？",
    "{spot}有哪些值得体验的项目，游玩时需要注意什么？",
]


def build_cases(catalog: list[dict], per_count: int = 10) -> list[dict]:
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for item in catalog:
        key = (item.get("source_city", ""), item.get("spot_name", ""))
        grouped[key].append(item)

    cases = []
    for chunk_count in range(1, 6):
        candidates = sorted(
            (
                (city, spot, sorted(items, key=lambda x: x["chunk_id"]))
                for (city, spot), items in grouped.items()
                if len(items) == chunk_count
            ),
            key=lambda item: (item[0], item[1]),
        )
        if len(candidates) < per_count:
            raise ValueError(
                f"Only {len(candidates)} spots have exactly {chunk_count} chunks; "
                f"need {per_count}"
            )

        for index, (city, spot, items) in enumerate(candidates[:per_count], 1):
            query_template = QUERY_TEMPLATES[(chunk_count + index - 1) % len(QUERY_TEMPLATES)]
            cases.append({
                "id": f"chunk_guide_{chunk_count}_{index:02d}",
                "city": city,
                "spot_name": spot,
                "query": query_template.format(spot=spot),
                "relevant_chunk_ids": [item["chunk_id"] for item in items],
                "relevant_chunk_count": chunk_count,
            })

    return cases


def main() -> None:
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    cases = build_cases(catalog)
    OUTPUT_PATH.write_text(
        json.dumps(cases, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Built {len(cases)} chunk-level cases at {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
