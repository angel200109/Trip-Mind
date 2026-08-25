"""
工具包
"""
__all__ = [
    "query_travel_knowledge",
    "ToolProvider",
    "get_tool_provider",
]


def __getattr__(name):
    if name == "query_travel_knowledge":
        from .rag_tool import query_travel_knowledge

        return query_travel_knowledge
    if name in {"ToolProvider", "get_tool_provider"}:
        from .tool_provider import ToolProvider, get_tool_provider

        return {"ToolProvider": ToolProvider, "get_tool_provider": get_tool_provider}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
