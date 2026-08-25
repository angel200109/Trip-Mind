"""
Rebuild the RAG knowledge base from citydata CSV files.

Run from multi-agents:
    python scripts/rebuild_kb.py
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
import shutil
import sys
import time
import uuid
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_ROOT.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import RAG_BATCH_SIZE, RAG_CHUNK_OVERLAP, RAG_CHUNK_SIZE
from tools.rag.citydata_loader import load_citydata_documents
from tools.rag.chunker import DocumentChunker
from tools.rag_tool import get_rag_instance


CHUNK_CATALOG_PATH = PROJECT_ROOT / "data" / "dataRAG" / "citydata_chunk_catalog.json"


def _disable_proxy_for_dashscope() -> None:
    """Avoid broken local proxy settings when calling DashScope embeddings."""
    for key in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"]:
        os.environ.pop(key, None)
    no_proxy = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""
    entries = [item.strip() for item in no_proxy.split(",") if item.strip()]
    for host in ["dashscope.aliyuncs.com", "aliyuncs.com"]:
        if host not in entries:
            entries.append(host)
    os.environ["NO_PROXY"] = ",".join(entries)


def _remove_existing_vector_store(persist_dir: Path, rag=None) -> None:
    if rag and rag.vector_store:
        try:
            rag.vector_store._client.close()
        except Exception:
            pass
        rag.vector_store = None

    gc.collect()
    if not persist_dir.exists():
        return

    for attempt in range(5):
        try:
            shutil.rmtree(persist_dir)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(1)


def filter_existing_documents(documents, doc_ids, existing_ids):
    """Remove documents whose deterministic ids are already in Chroma."""
    remaining_docs = []
    remaining_ids = []
    skipped = 0

    for doc, doc_id in zip(documents, doc_ids):
        if doc_id in existing_ids:
            skipped += 1
            continue
        remaining_docs.append(doc)
        remaining_ids.append(doc_id)

    return remaining_docs, remaining_ids, skipped


def chunk_citydata_documents(documents):
    """Split citydata documents while preserving scenic-spot metadata."""
    chunker = DocumentChunker(
        chunk_size=RAG_CHUNK_SIZE,
        chunk_overlap=RAG_CHUNK_OVERLAP,
    )
    return chunker.chunk_documents(documents)


def write_chunk_catalog(documents, doc_ids, output_path: Path = CHUNK_CATALOG_PATH) -> None:
    """Persist chunk IDs and source metadata for later relevance annotation."""
    catalog = [
        {
            "chunk_id": doc_id,
            "spot_name": doc.metadata.get("spot_name", ""),
            "source_city": doc.metadata.get("source_city", ""),
            "source": doc.metadata.get("source", ""),
            "content_preview": doc.page_content[:160],
        }
        for doc, doc_id in zip(documents, doc_ids)
    ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def generate_unique_doc_ids(documents, id_factory=None):
    """Generate deterministic IDs and disambiguate identical chunks."""
    if id_factory is None:
        from tools.rag.rag_engine import TravelRAG

        id_factory = TravelRAG._generate_doc_id

    occurrences = {}
    doc_ids = []
    for doc in documents:
        base_id = id_factory(doc, 0)
        occurrence = occurrences.get(base_id, 0)
        occurrences[base_id] = occurrence + 1
        if occurrence == 0:
            doc_ids.append(base_id)
            continue
        suffix = hashlib.md5(f"{base_id}:{occurrence}".encode("utf-8")).hexdigest()
        doc_ids.append(str(uuid.UUID(suffix)))
    return doc_ids


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild RAG KB from citydata CSV files")
    parser.add_argument(
        "--source",
        default=str(REPO_ROOT / "citydata"),
        help="citydata CSV directory",
    )
    parser.add_argument(
        "--keep-existing",
        action="store_true",
        help="Append to the existing vector store instead of deleting it first",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Keep the existing vector store and skip documents already imported",
    )
    parser.add_argument(
        "--use-proxy",
        action="store_true",
        help="Keep HTTP(S)_PROXY environment variables for DashScope calls",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Only import the first N valid documents; useful for smoke tests",
    )
    args = parser.parse_args()

    if not args.use_proxy:
        _disable_proxy_for_dashscope()

    docs = load_citydata_documents(args.source)
    if args.limit > 0:
        docs = docs[:args.limit]
    if not docs:
        raise SystemExit(f"No valid citydata documents found in {args.source}")

    docs = chunk_citydata_documents(docs)
    print(
        f"Chunked citydata documents: {len(docs)} chunks "
        f"(size={RAG_CHUNK_SIZE}, overlap={RAG_CHUNK_OVERLAP})",
        flush=True,
    )

    if args.keep_existing and args.resume:
        raise SystemExit("--keep-existing and --resume cannot be used together")

    if not args.keep_existing and not args.resume:
        from config.settings import CHROMA_PERSIST_DIR

        _remove_existing_vector_store(Path(CHROMA_PERSIST_DIR))

    rag = get_rag_instance()
    if not args.keep_existing and not args.resume:
        rag.imported_ids.clear()

    print(f"Loaded {len(docs)} valid scenic spot documents from {args.source}", flush=True)
    print(f"Building Chroma vector store at {rag.persist_directory}", flush=True)

    doc_ids = generate_unique_doc_ids(docs, id_factory=rag._generate_doc_id)
    for doc, doc_id in zip(docs, doc_ids):
        doc.metadata["chunk_id"] = doc_id
    write_chunk_catalog(docs, doc_ids)
    print(f"Chunk catalog written to {CHUNK_CATALOG_PATH}", flush=True)
    if args.resume:
        docs, doc_ids, skipped = filter_existing_documents(docs, doc_ids, rag.imported_ids)
        print(f"Resume mode: skipped {skipped} existing documents", flush=True)
        if not docs:
            print("No new documents to import", flush=True)
            return

    for i in range(0, len(docs), RAG_BATCH_SIZE):
        batch = docs[i:i + RAG_BATCH_SIZE]
        batch_ids = doc_ids[i:i + RAG_BATCH_SIZE]
        if i == 0 and not rag.vector_store:
            from langchain_chroma import Chroma

            rag.vector_store = Chroma.from_documents(
                documents=batch,
                embedding=rag.embeddings,
                persist_directory=rag.persist_directory,
                collection_name="travel_knowledge",
                ids=batch_ids,
            )
        else:
            rag.vector_store.add_documents(documents=batch, ids=batch_ids)
        rag.imported_ids.update(batch_ids)
        print(f"  Progress: {min(i + RAG_BATCH_SIZE, len(docs))}/{len(docs)}", flush=True)

    rag._hybrid_retriever = None
    rag._get_hybrid_retriever().build_bm25_index(docs)
    print(f"City RAG knowledge base rebuilt: {len(docs)} documents", flush=True)


if __name__ == "__main__":
    main()
