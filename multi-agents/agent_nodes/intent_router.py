"""
IntentRouter 节点 — 三分类意图识别 + 参数提取 + 分级记忆加载

入口节点，替代原 main_agent + planner_agent 的分类功能。
流程：
  1. 轻量记忆加载（working + short_term）
  2. 一次结构化 LLM 调用（三分类 + 参数提取）
  3. 按分类按需加载 long_term 记忆
  4. 路由决策
     - greeting          → LLM 生成问候回复，is_complete=True
     - full_travel 缺起点 → 追问，is_complete=True
     - simple/full_travel → is_complete=False，让下游接管
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from config.prompts import INTENT_ROUTER_PROMPT
from config.settings import DASHSCOPE_API_KEY, QWEN3_API_BASE, QWEN3_MODEL
from graph.state import GlobalState, IntentContext
from memory import get_memory_manager


# ---------------------------------------------------------------------------
# Pydantic schema for structured LLM output
# ---------------------------------------------------------------------------

class IntentClassification(BaseModel):
    """结构化 LLM 输出：三分类 + 旅行参数提取"""

    query_type: Literal["greeting", "simple_travel", "full_travel"]
    destination: str = Field(default="", description="目的地城市")
    origin: str = Field(default="", description="出发地城市")
    travel_days: int = Field(default=0, description="旅行天数")
    budget: float = Field(default=0, description="预算（元）")
    travel_date: str = Field(default="", description="出发日期 YYYY-MM-DD")
    preferences: List[str] = Field(default_factory=list, description="旅行偏好")


# ---------------------------------------------------------------------------
# 多目的地检测（迁移自 planner_agent.py detect_multi_destination）
# ---------------------------------------------------------------------------

def _detect_multi_destination(user_query: str, destination: str, origin: str) -> dict:
    """检测是否为多目的地场景（排除往返/回程误判）

    Returns:
        dict with keys: is_multi_destination, detected_keywords, raw_destination_text,
                        detection_method
    """
    roundtrip_keywords = ["往返", "来回", "回程", "返程", "返回"]
    if any(kw in user_query for kw in roundtrip_keywords):
        return {
            "is_multi_destination": False,
            "detected_keywords": [],
            "raw_destination_text": destination,
            "detection_method": "roundtrip_excluded",
        }

    multi_dest_keywords = [
        "再去", "然后去", "接着去", "顺便去",
        "再到", "然后到", "接着到",
        "再去看看", "再看看",
        "之后去", "之后到",
    ]
    detected_keywords = [kw for kw in multi_dest_keywords if kw in user_query]
    if detected_keywords:
        return {
            "is_multi_destination": True,
            "detected_keywords": detected_keywords,
            "raw_destination_text": destination,
            "detection_method": "keyword",
        }

    norm = destination.replace(",", "，").replace("、", "，")
    cities = [c.strip() for c in norm.split("，") if c.strip()]
    unique_cities: list[str] = []
    for c in cities:
        if c not in unique_cities:
            unique_cities.append(c)

    if len(unique_cities) >= 3:
        return {
            "is_multi_destination": True,
            "detected_keywords": [],
            "raw_destination_text": destination,
            "detection_method": "comma_separated_3plus",
        }

    if len(unique_cities) == 2:
        if origin and origin in unique_cities:
            return {
                "is_multi_destination": False,
                "detected_keywords": [],
                "raw_destination_text": destination,
                "detection_method": "origin_pair_excluded",
            }
        return {
            "is_multi_destination": True,
            "detected_keywords": [],
            "raw_destination_text": destination,
            "detection_method": "comma_separated_2",
        }

    return {
        "is_multi_destination": False,
        "detected_keywords": [],
        "raw_destination_text": destination,
    }


# ---------------------------------------------------------------------------
# 主节点函数
# ---------------------------------------------------------------------------

async def intent_router_node(state: GlobalState) -> Dict[str, Any]:
    """
    IntentRouter 节点 — 三分类意图识别 + 分级记忆加载 + 路由决策。

    Args:
        state: LangGraph GlobalState

    Returns:
        Dict 更新，写入 intent_context, memory_context，
        以及路由控制字段 is_complete / final_answer。
    """
    print(f"\n{'='*60}")
    print("▶️ IntentRouter 开始执行")
    print(f"{'='*60}")

    user_query: str = state.get("user_query", "") or ""
    session_id: str = state.get("session_id", "") or ""
    user_id: str = state.get("user_id", "") or ""
    messages = state.get("messages") or []

    print(f"📝 用户查询: {user_query}")

    # ------------------------------------------------------------------
    # Step 1: 轻量记忆加载（working + short_term，分类前即加载）
    # ------------------------------------------------------------------
    mem = get_memory_manager()

    working_ctx: dict = {}
    short_history: list = []

    working_ctx = mem.working.get_context(session_id) if session_id else {}

    if session_id:
        try:
            short_history = await mem.short_term.get_history(session_id)
        except Exception as exc:
            print(f"⚠️ 短期记忆加载失败: {exc}")
            short_history = []

    # ------------------------------------------------------------------
    # Step 2: 构造 LLM messages
    # ------------------------------------------------------------------
    today = datetime.now().strftime("%Y-%m-%d")
    system_prompt = INTENT_ROUTER_PROMPT.format(today=today)

    llm_messages: list = [SystemMessage(content=system_prompt)]
    for msg in messages:
        if isinstance(msg, (HumanMessage, AIMessage)):
            llm_messages.append(msg)
        elif isinstance(msg, dict):
            role = msg.get("type") or msg.get("role", "")
            content = msg.get("content", "")
            if role in ("human", "user"):
                llm_messages.append(HumanMessage(content=content))
            elif role in ("ai", "assistant"):
                llm_messages.append(AIMessage(content=content))

    # 追加当前用户查询（如果 messages 里尚未包含）
    if user_query:
        llm_messages.append(HumanMessage(content=user_query))

    # ------------------------------------------------------------------
    # Step 3: 结构化 LLM 调用（分类 + 参数提取）
    # ------------------------------------------------------------------
    classifier_llm = ChatOpenAI(
        model=QWEN3_MODEL,
        base_url=QWEN3_API_BASE,
        api_key=DASHSCOPE_API_KEY,
        temperature=0.3,
        extra_body={"tags": ["intent_classifier"]},  # 防止 SSE 流出
    )

    classification: Optional[IntentClassification] = None
    try:
        structured_llm = classifier_llm.with_structured_output(IntentClassification)
        classification = await structured_llm.ainvoke(llm_messages)
        print(f"✅ 分类结果: {classification.query_type}")
        print(f"   目的地={classification.destination}  出发地={classification.origin}")
        print(f"   天数={classification.travel_days}  预算={classification.budget}")
    except Exception as exc:
        print(f"⚠️ 结构化 LLM 调用失败: {exc}，降级为 greeting")
        classification = IntentClassification(query_type="greeting")

    query_type = classification.query_type

    # ------------------------------------------------------------------
    # Step 4: 按分类按需加载长期记忆
    # ------------------------------------------------------------------
    preferences: dict = {}

    if query_type in ("simple_travel", "full_travel") and user_id:
        try:
            preferences = await mem.long_term.get_preferences(user_id)
        except Exception as exc:
            print(f"⚠️ 偏好加载失败: {exc}")
            preferences = {}

    memory_context: Dict[str, Any] = {
        "working": working_ctx,
        "short_history": short_history,
        "preferences": preferences,
    }

    # ------------------------------------------------------------------
    # Step 5: 构建 IntentContext
    # ------------------------------------------------------------------
    # 多目的地检测（full_travel 专属）
    scenario_type: Optional[str] = None
    if query_type == "full_travel":
        multi_result = _detect_multi_destination(
            user_query, classification.destination, classification.origin
        )
        if multi_result.get("is_multi_destination"):
            scenario_type = "multi_destination"
        else:
            scenario_type = "complex" if classification.travel_days > 0 else "simple"

    intent_ctx: IntentContext = {
        "query_type": query_type,
        "destination": classification.destination or None,
        "origin": classification.origin or None,
        "travel_days": classification.travel_days or None,
        "budget": classification.budget or None,
        "travel_date": classification.travel_date or None,
        "preferences": classification.preferences or None,
        "needs_clarification": False,
        "clarification_question": None,
        "scenario_type": scenario_type,
    }

    # ------------------------------------------------------------------
    # Step 6: 路由决策
    # ------------------------------------------------------------------

    # --- 6a: greeting ---
    if query_type == "greeting":
        print("💬 greeting 路由 → LLM 生成问候回复")
        greeting_llm = ChatOpenAI(
            model=QWEN3_MODEL,
            base_url=QWEN3_API_BASE,
            api_key=DASHSCOPE_API_KEY,
            temperature=0.7,
        )
        greeting_system = (
            "你是一个友好的旅行助手。请用简洁、热情的中文回复用户的问候或闲聊，"
            "并自然地引导用户说出旅行需求。回复不超过3句话。"
        )
        greeting_messages = [
            SystemMessage(content=greeting_system),
            HumanMessage(content=user_query),
        ]
        try:
            greeting_resp = await greeting_llm.ainvoke(greeting_messages)
            final_answer = greeting_resp.content
        except Exception as exc:
            print(f"⚠️ 问候 LLM 调用失败: {exc}")
            final_answer = "您好！我是您的旅行助手，有什么旅行计划需要帮助吗？"

        print(f"💬 问候回复: {final_answer[:50]}...")
        return {
            "intent_context": intent_ctx,
            "memory_context": memory_context,
            "is_complete": True,
            "final_answer": final_answer,
            "current_agent": "intent_router",
        }

    # --- 6b: full_travel 缺少出发地 → 追问 ---
    if query_type == "full_travel" and not classification.origin:
        intent_ctx["needs_clarification"] = True
        clarification = "请问您从哪个城市出发？提供出发地后，我可以为您规划详细的旅行方案（包括交通和行程）。"
        intent_ctx["clarification_question"] = clarification

        print(f"❓ full_travel 缺少出发地，追问: {clarification}")
        return {
            "intent_context": intent_ctx,
            "memory_context": memory_context,
            "is_complete": True,
            "final_answer": clarification,
            "current_agent": "intent_router",
        }

    # --- 6c: simple_travel / full_travel（信息完整）→ 下游接管 ---
    print(f"➡️ {query_type} 路由 → 下游节点接管，is_complete=False")
    return {
        "intent_context": intent_ctx,
        "memory_context": memory_context,
        "is_complete": False,
        "current_agent": "intent_router",
    }
