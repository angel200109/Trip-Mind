"""
LangGraph 工作流定义 - 6 节点双路径架构

路由：
  START → IntentRouter
            ├─ greeting → FinalOutput → END
            ├─ simple_travel → ReactExecutor → Summarizer → FinalOutput → END
            └─ full_travel
                 ├─ 缺字段 → FinalOutput → END（追问）
                 └─ Planner → StepExecutor → Summarizer → FinalOutput → END
"""
from typing import Literal, Dict, Any
from langgraph.graph import StateGraph, END
from graph.state import GlobalState
from agent_nodes.intent_router import intent_router_node
from agent_nodes.react_executor import react_executor_node
from agent_nodes.planner import planner_node
from agent_nodes.step_executor import step_executor_node
from agent_nodes.summarizer_agent import summarizer_agent_node
from memory import get_memory_manager


def route_after_intent_router(state: GlobalState) -> Literal["react_executor", "planner", "final_output"]:
    """IntentRouter 之后的路由决策"""
    if state.get("is_complete", False):
        return "final_output"

    intent_ctx = state.get("intent_context") or {}
    query_type = intent_ctx.get("query_type", "greeting")

    if query_type == "simple_travel":
        return "react_executor"
    elif query_type == "full_travel":
        return "planner"
    else:
        return "final_output"


async def final_output_node(state: GlobalState) -> Dict[str, Any]:
    """
    统一最终输出层
    - 确保 final_answer 已生成
    - 触发记忆写回（promote Q&A 对）
    """
    answer = state.get("final_answer") or ""
    if not answer:
        summarizer_ctx = state.get("summarizer_context") or {}
        answer = summarizer_ctx.get("final_summary", "")
    if not answer:
        intent_ctx = state.get("intent_context") or {}
        answer = intent_ctx.get("clarification_question", "")

    user_query = state.get("user_query", "")
    if user_query and answer:
        session_id = state.get("session_id", "default")
        user_id = state.get("user_id", "default_user")
        pg_session_id = state.get("pg_session_id")
        pg_uuid = None
        if pg_session_id:
            try:
                import uuid as _uuid
                pg_uuid = _uuid.UUID(str(pg_session_id))
            except (ValueError, TypeError):
                pg_uuid = None
        memory_mgr = get_memory_manager()
        try:
            await memory_mgr.promotion.promote(
                session_id=session_id,
                user_id=user_id,
                user_message=user_query,
                assistant_response=answer,
                pg_session_id=pg_uuid,
            )
            memory_mgr.working.clear(session_id)
        except Exception as e:
            print(f"  记忆写回失败（不影响输出）: {e}")

    return {
        "final_answer": answer,
        "is_complete": True,
        "next_agent": None,
    }


def create_travel_planning_graph():
    """创建 6 节点双路径旅游规划工作流图"""
    workflow = StateGraph(GlobalState)

    workflow.add_node("intent_router", intent_router_node)
    workflow.add_node("react_executor", react_executor_node)
    workflow.add_node("planner", planner_node)
    workflow.add_node("step_executor", step_executor_node)
    workflow.add_node("summarizer", summarizer_agent_node)
    workflow.add_node("final_output", final_output_node)

    workflow.set_entry_point("intent_router")

    workflow.add_conditional_edges(
        "intent_router",
        route_after_intent_router,
        {
            "react_executor": "react_executor",
            "planner": "planner",
            "final_output": "final_output",
        },
    )

    workflow.add_edge("react_executor", "summarizer")
    workflow.add_edge("planner", "step_executor")
    workflow.add_edge("step_executor", "summarizer")
    workflow.add_edge("summarizer", "final_output")
    workflow.add_edge("final_output", END)

    return workflow.compile()


travel_graph = create_travel_planning_graph()
