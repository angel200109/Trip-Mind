"""
上下文压缩工具 — LLM 驱动的对话历史压缩

支持两种模式：
1. 首次压缩：将一组对话消息压缩为摘要
2. 增量压缩：在已有摘要基础上，追加新消息后重新生成摘要
"""
from typing import List, Optional
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langchain_openai import ChatOpenAI
from config.settings import DASHSCOPE_API_KEY, QWEN3_MODEL, QWEN3_API_BASE, QWEN3_TEMPERATURE

_llm = ChatOpenAI(
    model=QWEN3_MODEL,
    api_key=DASHSCOPE_API_KEY,
    base_url=QWEN3_API_BASE,
    temperature=0.3,
)

_COMPRESS_PROMPT = """你是一个对话摘要专家。请将以下对话历史压缩成一个简洁的摘要，保留关键信息。

{existing_summary_section}

需要压缩的对话：
{conversation_history}

要求：
- 摘要不超过 400 字
- 保留：用户核心需求、已确认的偏好、已查询的信息、重要决策
- 丢弃：寒暄、重复确认、中间思考过程
- 用中文输出纯文本摘要，不要 JSON 格式
"""


async def compress_messages(
    messages: List[dict],
    existing_summary: Optional[str] = None,
) -> str:
    """
    将消息列表压缩为摘要文本。

    Args:
        messages: [{"role": "user"|"assistant", "content": str}, ...]
        existing_summary: 已有的摘要（增量压缩时传入）

    Returns:
        压缩后的摘要文本
    """
    conversation_lines = []
    for msg in messages:
        role = "用户" if msg["role"] == "user" else "助手"
        content = msg["content"][:300] if len(msg.get("content", "")) > 300 else msg.get("content", "")
        conversation_lines.append(f"{role}: {content}")

    conversation_text = "\n".join(conversation_lines)

    if existing_summary:
        existing_section = f"已有摘要（请在此基础上融合新对话）：\n{existing_summary}"
    else:
        existing_section = ""

    prompt = _COMPRESS_PROMPT.format(
        existing_summary_section=existing_section,
        conversation_history=conversation_text,
    )

    try:
        response = await _llm.ainvoke([
            SystemMessage(content="你是一个对话摘要专家，只输出摘要文本，不要多余格式。"),
            HumanMessage(content=prompt),
        ])
        summary = response.content.strip()
        # 去掉 LLM 可能加的引号或 markdown 标记
        if summary.startswith(("```", '"')):
            summary = summary.strip("`\"' \n")
        print(f"  [Compressor] 生成摘要 ({len(summary)} 字)")
        return summary
    except Exception as e:
        print(f"  [Compressor] 压缩失败: {e}")
        if existing_summary:
            return existing_summary
        return "（上下文压缩失败，已保留最近消息）"
