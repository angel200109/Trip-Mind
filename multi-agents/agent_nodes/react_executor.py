"""
ReactExecutor 节点 — 处理 simple_travel 类查询
使用 LangGraph 的 create_react_agent 自主循环调用工具
"""
from typing import Dict, Any, List, Optional

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.prebuilt import create_react_agent

from config.settings import (
    QWEN3_MODEL, QWEN3_API_BASE, DASHSCOPE_API_KEY, QWEN3_TEMPERATURE
)
from graph.state import GlobalState, ExecutorContext
from tools.tool_provider import get_tool_provider


def _extract_executor_context(
    messages: List,
    existing_rag_history: Optional[List[str]] = None,
) -> ExecutorContext:
    """
    从 create_react_agent 输出的消息列表中提取 ExecutorContext：

    遍历 messages，找 ToolMessage：
    - 提取 tool_name, result 到 tool_results list
    - rag_search 的结果追加到 rag_results_history
    - 所有工具结果放入 collected_info dict

    Args:
        messages: create_react_agent 返回的消息列表
        existing_rag_history: 已有的 RAG 结果历史

    Returns:
        ExecutorContext dict with tool_results, rag_results_history, collected_info
    """
    tool_results: List[Dict[str, Any]] = []
    rag_results_history: List[str] = list(existing_rag_history or [])
    collected_info: Dict[str, Any] = {}

    for msg in messages:
        if isinstance(msg, ToolMessage):
            tool_name = msg.name or ""
            result_str = str(msg.content)

            # 添加到 tool_results
            tool_results.append({
                "tool": tool_name,
                "result": result_str,
            })

            # 添加到 collected_info
            collected_info[tool_name] = result_str

            # RAG 结果特殊处理：追加到历史
            if tool_name == "rag_search":
                rag_results_history.append(result_str)

    return {
        "tool_results": tool_results,
        "rag_results_history": rag_results_history,
        "collected_info": collected_info,
        "plan_steps": None,
    }


async def react_executor_node(state: GlobalState) -> Dict[str, Any]:
    """
    ReactExecutor 节点函数 — 处理 simple_travel 查询

    流程：
    1. 从 state["intent_context"] 读取参数
    2. 构建 system_prompt 包含用户查询和已提取参数
    3. 调用 create_react_agent 执行
    4. 用 _extract_executor_context 提取结果
    5. 返回 executor_context 和控制流信息

    Args:
        state: LangGraph GlobalState

    Returns:
        Dict 更新，写入 executor_context, current_agent
    """
    print(f"\n{'='*60}")
    print("▶️ ReactExecutor 开始执行（simple_travel）")
    print(f"{'='*60}")

    # 读取参数
    user_query: str = state.get("user_query", "") or ""
    intent_context = state.get("intent_context") or {}

    destination = intent_context.get("destination", "")
    origin = intent_context.get("origin", "")
    travel_days = intent_context.get("travel_days", 0)
    budget = intent_context.get("budget", 0)
    travel_date = intent_context.get("travel_date", "")
    preferences = intent_context.get("preferences", [])

    print(f"📝 用户查询: {user_query}")
    print(f"📊 已提取信息:")
    print(f"   目的地: {destination}, 出发地: {origin}")
    print(f"   天数: {travel_days}, 预算: {budget}")
    print(f"   日期: {travel_date}, 偏好: {preferences}")

    # 获取工具
    tool_provider = await get_tool_provider()
    tools = tool_provider.get_tools()

    print(f"📦 可用工具数: {len(tools)}")

    # 创建 LLM 实例
    qwen3_llm = ChatOpenAI(
        model=QWEN3_MODEL,
        base_url=QWEN3_API_BASE,
        api_key=DASHSCOPE_API_KEY,
        temperature=QWEN3_TEMPERATURE,
        timeout=90,
        max_retries=1,
    )

    # 构建 system_prompt
    system_prompt = (
        f"你是一个智能旅行规划助手。\n\n"
        f"用户查询：{user_query}\n\n"
        f"已提取的信息：\n"
        f"- 目的地：{destination}\n"
        f"- 出发地：{origin}\n"
        f"- 旅行天数：{travel_days}\n"
        f"- 预算：{budget}元\n"
        f"- 出发日期：{travel_date}\n"
        f"- 偏好：{', '.join(preferences) if preferences else '无'}\n\n"
        f"请使用可用工具查询必要信息。当信息已充分时，直接给出最终回答。\n"
        f"工具使用建议：\n"
        f"- 天气查询 (gaode_weather): 查询目的地天气信息\n"
        f"- 知识库检索 (rag_search): 查询城市攻略和景点推荐\n"
        f"- 酒店搜索 (gaode_hotel_search): 查询住宿选项\n"
        f"- 黄历吉日 (lucky_day): 查询出行吉日\n"
        f"- 不要重复调用已成功执行的工具。"
    )

    # 定义日志钩子
    def log_agent_turn(state):
        """打印 Agent 推理轮次"""
        messages = state.get("messages", [])
        tool_count = sum(1 for m in messages if isinstance(m, ToolMessage))
        turn = tool_count + 1
        print(f"\n--- Agent 第 {turn} 次思考 ---")

    def log_model_output(state):
        """打印 Agent 本轮决策内容"""
        messages = state.get("messages", [])
        if not messages:
            return
        last = messages[-1]
        if hasattr(last, 'tool_calls') and last.tool_calls:
            for tc in last.tool_calls:
                print(f"  -> 调用工具: {tc['name']}({tc['args']})")
        elif hasattr(last, 'content') and last.content:
            short = last.content[:100].replace('\n', ' ')
            print(f"  -> 生成回答: {short}...")

    # 创建 ReAct agent
    agent = create_react_agent(
        model=qwen3_llm,
        tools=tools,
        prompt=system_prompt,
        pre_model_hook=log_agent_turn,
        post_model_hook=log_model_output,
    )

    print(f"\n🤖 启动 create_react_agent...")

    # 调用 agent
    result = await agent.ainvoke({
        "messages": [HumanMessage(content=user_query)]
    }, config={"recursion_limit": 8})

    # 提取消息
    messages = result.get("messages", [])
    print(f"\n📝 Agent 执行完成，共 {len(messages)} 条消息")

    # 打印最终回答
    for msg in reversed(messages):
        if hasattr(msg, 'content') and msg.content and not isinstance(msg, ToolMessage):
            content_preview = msg.content[:200] if isinstance(msg.content, str) else str(msg.content)[:200]
            print(f"\n✅ 最终回答 (前200字符): {content_preview}")
            break

    # 提取 ExecutorContext
    executor_context = _extract_executor_context(messages)

    print(f"\n{'='*60}")
    print(f"✅ ReactExecutor 执行完成")
    print(f"  工具执行结果数: {len(executor_context['tool_results'])}")
    print(f"  RAG结果数: {len(executor_context['rag_results_history'])}")
    print(f"{'='*60}\n")

    return {
        "executor_context": executor_context,
        "current_agent": "react_executor",
    }
