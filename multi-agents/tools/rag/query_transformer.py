"""
Query 改写与多路扩展

- rewrite_query: 将口语化查询重写为检索友好格式
- expand_queries: 生成 n 个不同角度的查询变体，提高召回覆盖
"""
from typing import List

from langchain_openai import ChatOpenAI

from config.prompts import RAG_QUERY_REWRITE_PROMPT


RAG_MULTI_QUERY_PROMPT = """你是一个旅游搜索助手。基于用户的原始查询，生成 {n} 个不同角度的检索查询，用于从旅游攻略知识库中检索相关信息。

原始查询：{query}

要求：
- 覆盖同义词和不同表达方式
- 从不同角度切入（如具体项目名、体验类型、适合人群等）
- 保持与原始查询的语义相关性
- 每行输出一个查询，不要编号，不要多余解释

生成 {n} 个查询："""


class QueryTransformer:
    """Query 改写与扩展"""

    def __init__(self, llm: ChatOpenAI):
        self._llm = llm

    async def rewrite_query(self, query: str) -> str:
        """将用户查询重写为更适合检索的格式"""
        prompt = RAG_QUERY_REWRITE_PROMPT.format(query=query)
        response = await self._llm.ainvoke(prompt)
        rewritten = response.content.strip()
        # 去除可能的前缀标记
        rewritten = rewritten.removeprefix("重写为：").removeprefix("[").removesuffix("]").strip()
        return rewritten if rewritten else query

    async def expand_queries(self, query: str, n: int = 3) -> List[str]:
        """生成 n 个变体查询（含原始 query 的重写版本）"""
        prompt = RAG_MULTI_QUERY_PROMPT.format(query=query, n=n)
        response = await self._llm.ainvoke(prompt)
        lines = [line.strip() for line in response.content.strip().split("\n") if line.strip()]
        # 去掉可能的编号前缀
        queries = []
        for line in lines[:n]:
            cleaned = line.lstrip("0123456789.、-) ").strip()
            if cleaned:
                queries.append(cleaned)

        # 确保至少包含原始 query
        if not queries:
            queries = [query]
        elif query not in queries:
            queries.insert(0, query)
            queries = queries[:n]

        return queries
