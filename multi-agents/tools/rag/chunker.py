"""
通用文档切分 + LLM 元数据抽取

设计原则:
- 切分层: 使用通用 RecursiveCharacterTextSplitter，不依赖特定文档格式
- 元数据层: 构建时用 LLM 批量抽取结构化 metadata，对任何格式都适用
- 不绑定特定文档结构，换材料无需改代码
"""
import asyncio
from typing import List, Dict, Optional

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_openai import ChatOpenAI


METADATA_EXTRACTION_PROMPT = """你是旅游知识库的标注助手。请为以下文本片段提取结构化元数据。

文本片段：
{chunk_text}

请严格按以下 JSON 格式输出（不要输出其他内容）：
{{
  "source_city": "文中提到的城市名，如无法判断则留空",
  "type": "内容类型，从以下选择: attraction/food/transport/accommodation/info/tips",
}}"""

BATCH_METADATA_PROMPT = """你是旅游知识库的标注助手。请为以下 {count} 个文本片段分别提取结构化元数据。

{chunks_text}

请严格按以下 JSON 数组格式输出（不要输出其他内容），数组长度必须为 {count}：
[
  {{"source_city": "城市名或空", "type": "attraction/food/transport/accommodation/info/tips"}},
  ...
]"""


class DocumentChunker:
    """通用文档切分器 + LLM 元数据抽取"""

    def __init__(
        self,
        chunk_size: int = 800,
        chunk_overlap: int = 100,
        llm: Optional[ChatOpenAI] = None,
        batch_size: int = 10,
    ):
        self._splitter = RecursiveCharacterTextSplitter(
            separators=["\n\n", "\n", "。", "！", "？", "；", "，", " "],
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            keep_separator=True,
            length_function=len,
        )
        self._llm = llm
        self._batch_size = batch_size

    def chunk_documents(self, documents: List[Document]) -> List[Document]:
        """通用切分，适用于任何格式的文档"""
        chunks = self._splitter.split_documents(documents)

        # 确保每个 chunk 都有标准 metadata 字段
        for chunk in chunks:
            chunk.metadata.setdefault("source_city", "")
            chunk.metadata.setdefault("type", "")

        return chunks

    async def chunk_and_extract_metadata(self, documents: List[Document]) -> List[Document]:
        """切分 + LLM 批量抽取元数据（构建知识库时使用）"""
        chunks = self.chunk_documents(documents)

        if not self._llm:
            print("  ⚠️ 未配置 LLM，跳过元数据抽取")
            return chunks

        print(f"  🏷️ LLM 元数据抽取中 ({len(chunks)} chunks, batch={self._batch_size})...")
        enriched = await self._batch_extract_metadata(chunks)
        return enriched

    async def _batch_extract_metadata(self, chunks: List[Document]) -> List[Document]:
        """分批调用 LLM 抽取元数据"""
        results = list(chunks)

        for i in range(0, len(chunks), self._batch_size):
            batch = chunks[i:i + self._batch_size]
            try:
                metadata_list = await self._extract_batch(batch)
                for j, meta in enumerate(metadata_list):
                    idx = i + j
                    if idx < len(results) and meta:
                        results[idx].metadata.update(meta)
            except Exception as e:
                print(f"    ⚠️ 第 {i//self._batch_size + 1} 批元数据抽取失败: {e}")
                continue

            progress = min(i + self._batch_size, len(chunks))
            print(f"    进度: {progress}/{len(chunks)}")

        return results

    async def _extract_batch(self, batch: List[Document]) -> List[Dict]:
        """对一批 chunks 调用 LLM 抽取元数据"""
        if len(batch) == 1:
            return [await self._extract_single(batch[0])]

        # 构造批量 prompt
        chunks_text = ""
        for idx, doc in enumerate(batch, 1):
            preview = doc.page_content[:300]
            chunks_text += f"\n--- 片段 {idx} ---\n{preview}\n"

        prompt = BATCH_METADATA_PROMPT.format(count=len(batch), chunks_text=chunks_text)
        response = await self._llm.ainvoke(prompt)
        content = response.content.strip()

        # 解析 JSON 数组
        import json
        # 兼容 LLM 输出可能带 ```json 标记
        content = content.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        parsed = json.loads(content)

        if not isinstance(parsed, list) or len(parsed) != len(batch):
            print(f"    ⚠️ LLM 返回长度不匹配 (期望 {len(batch)}, 得到 {len(parsed) if isinstance(parsed, list) else 'non-list'})")
            # 尽量使用已有的
            if isinstance(parsed, list):
                while len(parsed) < len(batch):
                    parsed.append({})
                parsed = parsed[:len(batch)]
            else:
                return [{} for _ in batch]

        return [self._clean_metadata(m) for m in parsed]

    async def _extract_single(self, doc: Document) -> Dict:
        """单个 chunk 的元数据抽取"""
        import json
        prompt = METADATA_EXTRACTION_PROMPT.format(chunk_text=doc.page_content[:500])
        response = await self._llm.ainvoke(prompt)
        content = response.content.strip()
        content = content.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        try:
            parsed = json.loads(content)
            return self._clean_metadata(parsed)
        except (json.JSONDecodeError, TypeError):
            return {}

    @staticmethod
    def _clean_metadata(meta: Dict) -> Dict:
        """清理并标准化 metadata 字段"""
        if not isinstance(meta, dict):
            return {}

        valid_types = {"attraction", "food", "transport", "accommodation", "info", "tips"}
        cleaned = {
            "source_city": str(meta.get("source_city", "") or "").strip(),
            "type": str(meta.get("type", "") or "").strip(),
        }

        # 校验枚举值
        if cleaned["type"] not in valid_types:
            cleaned["type"] = ""

        return cleaned
