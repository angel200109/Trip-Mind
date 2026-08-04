"""
偏好提取器 — LLM 驱动（Pydantic 结构化输出）

正则无法覆盖的隐含偏好（"带着爸妈"→家庭游、"别太赶"→慢节奏）由 LLM 提取。
字段名与 db.models.ALLOWED_PREF_FIELDS 白名单保持一致。
"""
import json
from typing import List, Optional

from pydantic import BaseModel, Field
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from config.settings import QWEN3_MODEL, QWEN3_API_BASE, DASHSCOPE_API_KEY


class PreferenceExtraction(BaseModel):
    """LLM 提取的偏好更新（字段名与 ALLOWED_PREF_FIELDS 一致）"""
    travel_style: List[str] = Field(default_factory=list, description="旅行风格（慢节奏/家庭游/古镇/自然风光...）")
    budget_level: Optional[str] = Field(None, description="经济型/舒适型/高端型/奢华型，仅当用户明确提到预算")
    hotel_preference: List[str] = Field(default_factory=list)
    liked_activities: List[str] = Field(default_factory=list)
    disliked_activities: List[str] = Field(default_factory=list)
    cuisine_preference: List[str] = Field(default_factory=list)
    transport_priority: List[str] = Field(default_factory=list)
    dietary_restrictions: List[str] = Field(default_factory=list)
    destination_types: List[str] = Field(default_factory=list)
    travel_season_preference: List[str] = Field(default_factory=list)
    daily_schedule_preference: Optional[str] = Field(None, description="随性/紧凑/悠闲")
    max_daily_budget: Optional[float] = Field(None)
    has_preference: bool = Field(default=False, description="用户消息是否真的包含偏好信息")
    confidence: float = Field(default=0.0, ge=0, le=1, description="提取置信度")


async def extract_preferences_with_llm(
    user_msg: str, assistant_msg: str, current_prefs: dict
) -> dict:
    """LLM 提取偏好，返回字段与 ALLOWED_PREF_FIELDS 对齐的 dict；无偏好或低置信返回 {}"""
    llm = ChatOpenAI(
        model=QWEN3_MODEL,
        base_url=QWEN3_API_BASE,
        api_key=DASHSCOPE_API_KEY,
        temperature=0.1,
    )
    structured = llm.with_structured_output(PreferenceExtraction)

    prompt = ChatPromptTemplate.from_messages([
        ("system", """你是用户旅行偏好分析师。从用户消息中提取明确的偏好信息。

规则：
- 只提取用户明确表达的内容，不要臆测
- 隐含语义也要提取（"带着爸妈"→travel_style=["家庭游","慢节奏"]；"别太赶"→travel_style=["慢节奏"]）
- 否定表达要正确归类（"不喜欢热闹"→disliked_activities=["热闹"]，不要放进 liked）
- 无偏好信息时 has_preference=false
- confidence 低于 0.6 时调用方应丢弃结果
- 数组字段返回新出现的偏好项（调用方负责与旧画像合并去重）"""),
        ("human", "用户消息：{user_msg}\n\n助手回复：{assistant_msg}\n\n当前画像：{current_prefs}")
    ])

    chain = prompt | structured
    result = await chain.ainvoke({
        "user_msg": user_msg,
        "assistant_msg": assistant_msg,
        "current_prefs": json.dumps(current_prefs, ensure_ascii=False, default=str),
    })

    if not result.has_preference or result.confidence < 0.6:
        return {}

    return result.model_dump(exclude={"has_preference", "confidence"})
