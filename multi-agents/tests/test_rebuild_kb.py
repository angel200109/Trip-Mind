import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.rebuild_kb import (
    chunk_citydata_documents,
    filter_existing_documents,
    generate_unique_doc_ids,
)
from tools.rag.citydata_loader import Document


def test_filter_existing_documents_skips_ids_already_in_vector_store():
    docs = [
        Document(page_content="城市：北京\n景点：故宫", metadata={"spot_name": "故宫"}),
        Document(page_content="城市：北京\n景点：长城", metadata={"spot_name": "长城"}),
    ]
    ids = ["id-1", "id-2"]

    remaining_docs, remaining_ids, skipped = filter_existing_documents(
        docs,
        ids,
        existing_ids={"id-1"},
    )

    assert remaining_docs == [docs[1]]
    assert remaining_ids == ["id-2"]
    assert skipped == 1


def test_chunk_citydata_documents_splits_long_spot_and_preserves_metadata():
    doc = Document(
        page_content="景点：故宫博物院\n介绍：" + "北京历史文化攻略。" * 120,
        metadata={
            "source_city": "北京",
            "spot_name": "故宫博物院The Palace Museum",
            "spot_name_norm": "故宫博物院thepalacemuseum",
            "type": "attraction",
        },
    )

    chunks = chunk_citydata_documents([doc])

    assert len(chunks) > 1
    assert all(chunk.metadata["source_city"] == "北京" for chunk in chunks)
    assert all(chunk.metadata["spot_name"] == "故宫博物院The Palace Museum" for chunk in chunks)


def test_generate_unique_doc_ids_handles_duplicate_chunk_content():
    docs = [
        Document(page_content="相同内容", metadata={"source": "北京.csv"}),
        Document(page_content="相同内容", metadata={"source": "北京.csv"}),
    ]

    ids = generate_unique_doc_ids(docs)

    assert len(ids) == 2
    assert len(set(ids)) == 2
