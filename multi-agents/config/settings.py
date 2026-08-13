"""
全局配置文件
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent

# 加载环境变量（明确指定.env文件路径）
env_path = PROJECT_ROOT / ".env"
load_dotenv(dotenv_path=env_path, override=True)

# DeepSeek API配置（用于V4推理模型）
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"

# DashScope API配置（用于Qwen模型和Embedding）
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
QWEN3_API_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"

# LangSmith配置（可选，仅用于调试）
LANGCHAIN_TRACING_V2 = os.getenv("LANGCHAIN_TRACING_V2", "false")
LANGCHAIN_API_KEY = os.getenv("LANGCHAIN_API_KEY", "")

# 模型配置
QWEN3_MODEL = "qwen3.7-plus"  # 使用DashScope API
QWEN3_TEMPERATURE = 0.7

V4_MODEL = "deepseek-v4-flash"
V4_TEMPERATURE = 0.1

# Embedding模型 
EMBEDDING_MODEL = "text-embedding-v3"

# RAG配置
CHROMA_PERSIST_DIR = PROJECT_ROOT / "data" / "dataRAG" / "vectordb"
RAG_CHUNK_SIZE = 800
RAG_CHUNK_OVERLAP = 100
RAG_SEARCH_K = 3
RAG_BATCH_SIZE = 10  # ChromaDB批量载入大小，如遇到API限制可调小

# RAG v2 新增配置
RAG_RETRIEVE_K = 10              # rerank 前的候选数量
RAG_BM25_WEIGHT = 0.5            # 混合检索中 BM25 权重
RAG_VECTOR_WEIGHT = 0.5          # 混合检索中向量检索权重
RAG_RERANK_MODEL = "gte-rerank"  # DashScope reranker 模型
RAG_RERANK_TOP_N = 3             # rerank 后返回数量
RAG_CONFIDENCE_THRESHOLD = 0.3   # 低于此阈值标记为低置信度
RAG_ENABLE_QUERY_REWRITE = True  # 是否启用 query rewrite
RAG_MULTI_QUERY_COUNT = 3        # multi-query 扩展数量

# MCP配置
MCP_CONFIG_PATH = str(PROJECT_ROOT / "config" / "servers_config.json")

# PostgreSQL 配置
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/smart_travel")

# Redis 配置
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
