"""
重建 RAG 知识库脚本

用法:
    python scripts/rebuild_kb.py            # 全量重建（删旧库）
    python scripts/rebuild_kb.py --incremental   # 增量更新（跳过已导入的 chunks）
    python scripts/rebuild_kb.py --source data/dataRAG/docs/other  # 指定文档目录

说明:
    - 默认全量重建: 删除旧 ChromaDB 向量库后从头构建
    - 脚本已强制 stdout 为 UTF-8，Windows 下无需设置 PYTHONIOENCODING
"""
import sys
import os
from pathlib import Path

# 保证从任意目录执行时都能导入项目包
sys.path.insert(0, str(Path(__file__).parent.parent))

# Windows 控制台默认 GBK，无法打印 emoji，这里直接改 stdout 编码
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="重建 RAG 知识库")
    parser.add_argument(
        "--source",
        default=str(Path(__file__).parent.parent / "data" / "dataRAG" / "docs"),
        help="攻略文档目录 (默认: data/dataRAG/docs)",
    )
    parser.add_argument(
        "--incremental",
        action="store_true",
        help="增量更新，跳过已导入的 chunks (默认全量重建)",
    )
    args = parser.parse_args()

    from tools.rag_tool import get_rag_instance

    rag = get_rag_instance()
    try:
        rag.build_knowledge_base(args.source, force_recreate=not args.incremental)
    except PermissionError as e:
        print(f"\n❌ 重建失败: {e}", file=sys.stderr)
        print(
            "提示: chroma.sqlite3 被其他进程占用（如运行中的 server.py / uvicorn / Jupyter）。\n"
            "请先停止占用该文件的进程，再重新运行本脚本。",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
