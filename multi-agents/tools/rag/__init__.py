"""
tools.rag — 旅游攻略 RAG 系统 v2

导出与原 tools.rag_tool 相同的接口，保持向后兼容。
"""

_rag_instance = None


def get_rag_instance():
    """获取全局 RAG 实例（单例）"""
    global _rag_instance
    if _rag_instance is None:
        from .rag_engine import TravelRAG

        _rag_instance = TravelRAG()
    return _rag_instance


async def query_travel_knowledge(query: str, k: int = 3, filters=None) -> str:
    """查询旅游知识库

    Args:
        query: 检索关键词
        k: 返回结果数量
        filters: 可选 metadata 过滤 (type)
    """
    rag = get_rag_instance()
    return await rag.search(query, k=k, filters=filters)


def __getattr__(name):
    if name == "TravelRAG":
        from .rag_engine import TravelRAG

        return TravelRAG
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
