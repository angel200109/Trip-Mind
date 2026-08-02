"""
LangGraph 工作流定义
主 Agent 控制全局
双模式架构：
- 简单模式：Main → Planner → ReAct循环 → Summarizer
- 复杂模式：Main → Planner → Plan-then-Execute → Summarizer
- 对话类查询：Main 直接处理
- 用户反馈：Main → Feedback → Main（决策）
"""
from typing import Literal, Dict, Any
from langgraph.graph import StateGraph, END
from graph.state import GlobalState
from agent_nodes import (
    main_agent_node,
    planner_agent_node,
    executor_agent_node,
    summarizer_agent_node,
    feedback_agent_node
)
from memory import get_memory_manager


def route_after_main(state: GlobalState) -> Literal["planner", "feedback", "final_output"]:
    """
    Main之后的路由决策
    """
    if state.get("is_complete", False):
        return "final_output"
    if state.get("needs_clarification", False):
        return "final_output"
    next_agent = state.get("next_agent", "planner")
    if next_agent == "feedback":
        return "feedback"
    return "planner"


def route_after_feedback(state: GlobalState) -> Literal["main", "final_output"]:
    """
    Feedback之后的路由决策
    Feedback 总是回到 Main，让 Main 做决策
    """
    if state.get("is_complete", False):
        return "final_output"
    return "main"


def route_after_planner(state: GlobalState) -> Literal["executor", "final_output"]:
    """
    Planner之后的路由决策
    """
    if state.get("needs_clarification", False):
        return "final_output"
    return "executor"


async def final_output_node(state: GlobalState) -> Dict[str, Any]:
    """
    统一最终输出层 - 所有模式的回答汇聚于此
    不调用 LLM，仅确保 final_answer 已生成并标记完成
    + 触发记忆写回 pipeline
    """
    answer = state.get("final_answer") or ""
    if not answer:
        # 兜底：从各上下文提取
        summarizer_ctx = state.get("summarizer_context") or {}
        answer = summarizer_ctx.get("final_summary", "")
    if not answer:
        planner_ctx = state.get("planner_context") or {}
        answer = planner_ctx.get("clarification_question", "")

    # 记忆写回
    user_query = state.get("user_query", "")
    if user_query and answer:
        session_id = state.get("session_id", "default")
        user_id = state.get("user_id", "default_user")
        memory_mgr = get_memory_manager()
        try:
            await memory_mgr.promotion.promote(
                session_id=session_id,
                user_id=user_id,
                user_message=user_query,
                assistant_response=answer,
            )
            # 清除本次请求的工作记忆
            memory_mgr.working.clear(session_id)
        except Exception as e:
            print(f"  ⚠️ 记忆写回失败（不影响输出）: {e}")

    return {
        "final_answer": answer,
        "is_complete": True,
        "next_agent": None,
    }


def create_travel_planning_graph():
    """
    创建旅游规划工作流图
    """
    workflow = StateGraph(GlobalState)
    
    workflow.add_node("main", main_agent_node)
    workflow.add_node("planner", planner_agent_node)
    workflow.add_node("executor", executor_agent_node)
    workflow.add_node("summarizer", summarizer_agent_node)
    workflow.add_node("feedback", feedback_agent_node)
    workflow.add_node("final_output", final_output_node)

    workflow.set_entry_point("main")

    workflow.add_conditional_edges(
        "main",
        route_after_main,
        {
            "planner": "planner",
            "feedback": "feedback",
            "final_output": "final_output",
        }
    )

    workflow.add_conditional_edges(
        "feedback",
        route_after_feedback,
        {
            "main": "main",
            "final_output": "final_output",
        }
    )

    workflow.add_conditional_edges(
        "planner",
        route_after_planner,
        {
            "executor": "executor",
            "final_output": "final_output",
        }
    )

    workflow.add_edge("executor", "summarizer")
    workflow.add_edge("summarizer", "final_output")
    workflow.add_edge("final_output", END)

    return workflow.compile()


travel_graph = create_travel_planning_graph()
