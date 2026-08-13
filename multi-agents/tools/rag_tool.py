"""
RAG 检索工具 — 兼容 shim

原始实现已迁移到 tools/rag/ 包。此文件保留原有导入路径的兼容性:
  from tools.rag_tool import get_rag_instance, query_travel_knowledge, TravelRAG
"""
from tools.rag import TravelRAG, get_rag_instance, query_travel_knowledge

__all__ = ["TravelRAG", "get_rag_instance", "query_travel_knowledge"]
