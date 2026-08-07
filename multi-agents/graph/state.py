"""
LangGraph 全局状态定义
6 节点双路径架构：IntentRouter → ReactExecutor / (Planner → StepExecutor) → Summarizer → FinalOutput
"""
from typing import TypedDict, List, Optional, Annotated, Dict, Any, Literal
from langchain_core.messages import BaseMessage
import operator


class IntentContext(TypedDict):
    """IntentRouter 的输出上下文 - 三分类 + 参数提取"""
    query_type: Literal["greeting", "simple_travel", "full_travel"]
    destination: Optional[str]
    origin: Optional[str]
    travel_days: Optional[int]
    budget: Optional[float]
    travel_date: Optional[str]
    preferences: Optional[List[str]]
    needs_clarification: bool
    clarification_question: Optional[str]
    scenario_type: Optional[str]


class ExecutorContext(TypedDict):
    """Executor 节点的输出上下文"""
    tool_results: List[Dict[str, Any]]
    rag_results_history: List[str]
    collected_info: Optional[Dict[str, Any]]
    plan_steps: Optional[List[Dict[str, Any]]]


class SummarizerContext(TypedDict):
    """Summarizer Agent 自己的上下文"""
    final_summary: Optional[str]


class GlobalState(TypedDict):
    """全局状态"""
    # 对话历史
    messages: Annotated[List[BaseMessage], operator.add]
    user_query: Optional[str]

    # 各节点上下文
    intent_context: Optional[IntentContext]
    executor_context: Optional[ExecutorContext]
    summarizer_context: Optional[SummarizerContext]

    # 控制流
    current_agent: Optional[str]
    next_agent: Optional[str]
    is_complete: bool

    # 统一最终输出
    final_answer: Optional[str]

    # 会话标识
    pg_session_id: Optional[str]
    session_id: Optional[str]
    user_id: Optional[str]

    # 记忆系统
    memory_context: Optional[Dict[str, Any]]
