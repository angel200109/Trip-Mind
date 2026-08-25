"""
Loader for citydata CSV files.

Each valid scenic spot row becomes one LangChain Document. The source CSV already
contains structured fields, so this loader derives metadata directly and skips
LLM-based metadata extraction.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, List

try:
    from langchain_core.documents import Document
except ModuleNotFoundError:
    @dataclass
    class Document:
        page_content: str
        metadata: dict[str, Any] = field(default_factory=dict)


CITYDATA_FIELDS = [
    "名字",
    "链接",
    "地址",
    "介绍",
    "开放时间",
    "评分",
    "建议游玩时间",
    "建议季节",
    "门票",
    "小贴士",
]


def normalize_spot_name(name: str) -> str:
    """Normalize spot names for exact-match evaluation."""
    return re.sub(r"\s+", "", (name or "").strip()).lower()


def load_citydata_documents(source_dir: str | Path) -> List[Document]:
    """Load all citydata CSV files as one Document per complete scenic spot."""
    root = Path(source_dir)
    documents: List[Document] = []

    for csv_path in sorted(root.glob("*.csv")):
        city = csv_path.stem
        for row in _read_csv_rows(csv_path):
            doc = _row_to_document(row, city=city, source_path=csv_path)
            if doc is not None:
                documents.append(doc)

    return _dedupe_documents(documents)


def _read_csv_rows(csv_path: Path) -> Iterable[dict[str, str]]:
    try:
        with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
            yield from csv.DictReader(f)
    except UnicodeDecodeError:
        with csv_path.open("r", encoding="gb18030", newline="") as f:
            yield from csv.DictReader(f)


def _row_to_document(row: dict[str, str], city: str, source_path: Path) -> Document | None:
    name = _clean(row.get("名字", ""))
    if not name:
        return None

    intro = _clean(row.get("介绍", ""))
    address = _clean(row.get("地址", ""))
    tips = _clean(row.get("小贴士", ""))
    open_time = _clean(row.get("开放时间", ""))
    ticket = _clean(row.get("门票", ""))
    duration = _clean(row.get("建议游玩时间", ""))
    season = _clean(row.get("建议季节", ""))
    rating = _clean(row.get("评分", ""))
    url = _clean(row.get("链接", ""))

    # Drop incomplete rows. A name alone is not useful RAG evidence.
    if not any([intro, address, tips, open_time, ticket]):
        return None

    parts = [
        ("城市", city),
        ("景点", name),
        ("地址", address),
        ("介绍", intro),
        ("开放时间", open_time),
        ("评分", rating),
        ("建议游玩时间", duration),
        ("建议季节", season),
        ("门票", ticket),
        ("小贴士", tips),
    ]
    page_content = "\n".join(f"{label}：{value}" for label, value in parts if value)

    return Document(
        page_content=page_content,
        metadata={
            "source_city": city,
            "spot_name": name,
            "spot_name_norm": normalize_spot_name(name),
            "type": "attraction",
            "rating": rating,
            "url": url,
            "source": str(source_path),
        },
    )


def _clean(value: object) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\ufeff", "")
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _dedupe_documents(documents: List[Document]) -> List[Document]:
    seen: set[tuple[str, str]] = set()
    unique: List[Document] = []
    for doc in documents:
        key = (doc.metadata.get("source_city", ""), doc.metadata.get("spot_name_norm", ""))
        if key in seen:
            continue
        seen.add(key)
        unique.append(doc)
    return unique
