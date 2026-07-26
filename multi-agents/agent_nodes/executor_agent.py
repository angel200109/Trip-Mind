"""
Executor Agent - 双模式架构
- 简单模式：create_react_agent（替代手写 ReAct 循环）
- 复杂模式：Plan-then-Execute，先列计划再执行
使用自己的上下文：executor_context
"""
from typing import Dict, Any, List, Optional
import json
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, ToolMessage
from langgraph.prebuilt import create_react_agent

from config.settings import (
    QWEN3_MODEL, QWEN3_API_BASE, DASHSCOPE_API_KEY, QWEN3_TEMPERATURE,
    R1_MODEL, DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, R1_TEMPERATURE
)
from graph.state import GlobalState
from tools.tool_provider import get_tool_provider


def _extract_executor_context(
    messages: List,
    existing_rag_history: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    从 create_react_agent 输出的消息列表中提取 ExecutorContext 格式：
    {tool_results, rag_results_history, collected_info}
    """
    tool_results = []
    rag_results_history = list(existing_rag_history or [])
    collected_info = {}

    for msg in messages:
        if isinstance(msg, ToolMessage):
            tool_name = msg.name or ""
            result_str = str(msg.content)
            tool_results.append({
                "tool": tool_name,
                "result": result_str,
            })
            collected_info[tool_name] = result_str
            if tool_name in ("rag_search",):
                rag_results_history.append(result_str)

    return {
        "tool_results": tool_results,
        "rag_results_history": rag_results_history,
        "collected_info": collected_info,
    }


async def react_loop(state: GlobalState, planner_context: Dict, executor_context: Dict) -> Dict[str, Any]:
    """
    简单模式：使用 create_react_agent 自动执行 ReAct 循环
    LangGraph 原生处理 tool calling、迭代和停止条件
    """
    print(f"\n{'='*60}")
    print("🔄 【简单模式】create_react_agent")
    print(f"{'='*60}")

    user_query = state.get("user_query", "")
    destination = planner_context.get("destination", "")
    origin = planner_context.get("origin", "")
    travel_days = planner_context.get("travel_days", 0)
    budget = planner_context.get("budget", 0)
    travel_date = planner_context.get("travel_date", "")
    preferences = planner_context.get("preferences", [])

    # 获取 ToolProvider 的所有工具
    tool_provider = await get_tool_provider()
    tools = tool_provider.get_tools()

    qwen3_llm = ChatOpenAI(
        model=QWEN3_MODEL,
        base_url=QWEN3_API_BASE,
        api_key=DASHSCOPE_API_KEY,
        temperature=QWEN3_TEMPERATURE
    )

    # 构建系统提示词
    system_prompt = (
        f"你是一个智能旅行规划助手。\n\n"
        f"用户需求：{user_query}\n"
        f"已提取的信息：\n"
        f"- 目的地：{destination}\n"
        f"- 出发地：{origin}\n"
        f"- 旅行天数：{travel_days}\n"
        f"- 预算：{budget}\n"
        f"- 出发日期：{travel_date}\n"
        f"- 偏好：{preferences}\n\n"
        f"请使用可用工具查询必要信息。当信息已充分时，直接给出最终回答。\n"
        f"工具使用建议：\n"
        f"- 黄历吉日 (lucky_day): 建议为所有完整旅行规划查询\n"
        f"- 航班查询 (flight_query): 距离>800km或老人儿童同行时建议\n"
        f"- 天气查询 (gaode_weather): 建议为所有规划查询\n"
        f"- 火车票查询 (train_query): 自动包含站点代码查询\n"
        f"- 知识库检索 (rag_search): 查询城市攻略和景点\n"
        f"- 不要重复调用已成功执行的工具。"
    )

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

    agent = create_react_agent(
        model=qwen3_llm,
        tools=tools,
        prompt=system_prompt,
        pre_model_hook=log_agent_turn,
        post_model_hook=log_model_output,
    )

    print(f"\n🤖 启动 create_react_agent...")
    print(f"📦 可用工具: {[t.name for t in tools]}")

    result = await agent.ainvoke({
        "messages": [HumanMessage(content=user_query)]
    })

    # 从输出消息中提取工具结果
    messages = result.get("messages", [])
    print(f"\n📝 Agent 执行完成，共 {len(messages)} 条消息")

    # 打印最终回答
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and msg.content:
            print(f"\n✅ 最终回答 (前200字符): {msg.content[:200]}")
            break

    # 转换为 ExecutorContext 格式
    new_ctx = _extract_executor_context(
        messages, executor_context.get("rag_results_history")
    )

    # 合并到现有上下文
    merged = dict(executor_context)
    merged["tool_results"] = (
        executor_context.get("tool_results", []) + new_ctx["tool_results"]
    )
    merged["rag_results_history"] = new_ctx["rag_results_history"]
    merged["collected_info"] = {
        **executor_context.get("collected_info", {}),
        **new_ctx["collected_info"],
    }

    print(f"\n{'='*60}")
    print(f"✅ create_react_agent 完成")
    print(f"  工具执行结果数: {len(merged['tool_results'])}")
    print(f"{'='*60}")

    return merged


async def plan_then_execute(state: GlobalState, planner_context: Dict, executor_context: Dict) -> Dict[str, Any]:
    """
    Plan-then-Execute - 复杂模式：先列计划再执行

    核心逻辑：
    - 先由 DeepSeek R1 制定详细的查询计划
    - 然后按计划依次执行每个步骤（通过 ToolProvider 调用工具）
    - 增加容错机制：某个工具失败不影响后续步骤
    - 完整执行完所有计划步骤（因为是预先规划好的）
    """
    print(f"\n{'='*60}")
    print("📋 【复杂模式】Plan-then-Execute开始")
    print(f"{'='*60}")

    destination = planner_context.get("destination", "")
    origin = planner_context.get("origin", "")
    travel_days = planner_context.get("travel_days", 0)
    budget = planner_context.get("budget", 0)
    travel_date = planner_context.get("travel_date", "")
    preferences = planner_context.get("preferences", [])
    user_query = state.get("user_query", "")

    tool_results = executor_context.get("tool_results", [])
    rag_results_history = executor_context.get("rag_results_history", [])
    collected_info = executor_context.get("collected_info", {})

    # 获取 ToolProvider（替换旧的 get_mcp_manager）
    tool_provider = await get_tool_provider()
    tool_map = tool_provider.get_tool_map()

    r1_llm = ChatOpenAI(
        model=R1_MODEL,
        base_url=DEEPSEEK_BASE_URL,
        api_key=DEEPSEEK_API_KEY,
        temperature=R1_TEMPERATURE
    )

    problem = f"""
用户的旅行需求：
最新查询：{user_query}

已提取的信息：
- 目的地：{destination}
- 出发地：{origin}
- 总天数：{travel_days}
- 总预算：{budget}元
- 出发日期：{travel_date}
- 偏好：{', '.join(preferences) if preferences else '无'}

请制定详细的查询计划，输出JSON格式：
{{
  "query_plan": [
    {{
      "tool": "工具名",
      "params": {{"参数名": "参数值"}},
      "description": "这一步的目的"
    }}
  ]
}}

可用工具：{', '.join(tool_map.keys())}

建议包含：rag_search + train_query + gaode_hotel_search + gaode_weather + lucky_day
"""

    try:
        print(f"\n🧠 DeepSeek R1开始制定计划...")
        response = await r1_llm.ainvoke([HumanMessage(content=problem)])
        content = response.content.strip()

        print(f"\n📋 R1返回原始内容:")
        print(content[:500])

        if "```json" in content:
            start = content.find("```json") + 7
            end = content.find("```", start)
            if end != -1:
                content = content[start:end]
        elif "```" in content:
            start = content.find("```") + 3
            end = content.find("```", start)
            if end != -1:
                content = content[start:end]

        plan_data = json.loads(content.strip())
        query_plan = plan_data.get("query_plan", [])

        print(f"\n✅ 计划制定完成，共 {len(query_plan)} 步")
        print(f"   📝 说明：将完整执行所有计划步骤（预先规划好的）")
        for i, step in enumerate(query_plan):
            print(f"  步骤 {i+1}: {step.get('tool')} - {step.get('description')}")

        failed_steps = []

        for i, step in enumerate(query_plan):
            tool_name = step.get("tool", "")
            params = step.get("params", {})
            description = step.get("description", "")

            print(f"\n{'='*60}")
            print(f"📋 执行计划步骤 {i+1}/{len(query_plan)}: {tool_name}")
            print(f"{'='*60}")
            print(f"  描述: {description}")
            print(f"  参数: {params}")

            # 通过 tool_map 直接调用工具（替代旧的 execute_tool）
            tool = tool_map.get(tool_name)
            if tool is None:
                error_msg = f"未知工具: {tool_name}"
                print(f"⚠️ {error_msg}")
                failed_steps.append({
                    "step": i + 1,
                    "tool": tool_name,
                    "error": error_msg
                })
                tool_results.append({
                    "tool": tool_name,
                    "result": error_msg,
                    "step": description,
                    "success": False
                })
                continue

            try:
                observation = await tool.ainvoke(params)
                print(f"✅ 工具执行完成")

                tool_results.append({
                    "tool": tool_name,
                    "result": observation,
                    "step": description,
                    "success": True
                })

                if tool_name == "rag_search":
                    rag_results_history.append(str(observation))

                collected_info[tool_name] = observation

            except Exception as step_error:
                print(f"⚠️ 步骤执行失败: {step_error}")
                print(f"   继续执行后续步骤...")

                failed_steps.append({
                    "step": i + 1,
                    "tool": tool_name,
                    "error": str(step_error)
                })

                tool_results.append({
                    "tool": tool_name,
                    "result": f"工具执行失败: {str(step_error)}",
                    "step": description,
                    "success": False
                })

        if failed_steps:
            print(f"\n⚠️ 部分步骤执行失败 ({len(failed_steps)}/{len(query_plan)}):")
            for failed in failed_steps:
                print(f"  步骤 {failed['step']}: {failed['tool']} - {failed['error']}")

        print(f"\n✅ Plan-then-Execute 执行完成")
        print(f"   成功步骤: {len(query_plan) - len(failed_steps)}/{len(query_plan)}")

    except Exception as e:
        print(f"❌ Plan-then-Execute异常: {e}")
        import traceback
        traceback.print_exc()

    # 更新自己的上下文
    executor_context["tool_results"] = tool_results
    executor_context["rag_results_history"] = rag_results_history
    executor_context["collected_info"] = collected_info

    return executor_context


async def executor_agent_node(state: GlobalState) -> Dict[str, Any]:
    """
    执行Agent节点 - 双模式选择
    使用自己的上下文：executor_context
    
    根据query_mode选择：
    - simple: ReAct循环，LLM自主决策
    - full: Plan-then-Execute，先列计划再执行
    """
    print(f"\n{'='*60}")
    print("▶️ Executor Agent 开始执行")
    print(f"{'='*60}")
    
    # 从 Planner 的上下文中获取信息
    planner_context = state.get("planner_context") or {}
    needs_deep_analysis = planner_context.get("needs_deep_analysis", False) if planner_context else False
    query_mode = planner_context.get("query_mode", "full") if planner_context else "full"
    
    # 初始化或获取自己的上下文
    executor_context = state.get("executor_context") or {
        "tool_results": [],
        "rag_results_history": [],
        "collected_info": {}
    }
    
    print(f"📊 状态信息:")
    print(f"  query_mode: {query_mode}")
    print(f"  needs_deep_analysis: {needs_deep_analysis}")
    
    if query_mode == "simple":
        print(f"\n✅ 【简单模式】ReAct循环，LLM自主决策")
        executor_context = await react_loop(state, planner_context, executor_context)
    else:
        print(f"\n✅ 【复杂模式】Plan-then-Execute，先列计划再执行")
        executor_context = await plan_then_execute(state, planner_context, executor_context)
    
    print(f"\n✅ Executor Agent 执行完成")
    print(f"  工具执行结果数: {len(executor_context.get('tool_results', []))}")
    print(f"  RAG结果数: {len(executor_context.get('rag_results_history', []))}")
    print(f"  下一步: summarizer")
    print(f"{'='*60}\n")
    
    return {
        "executor_context": executor_context,
        "current_agent": "executor",
        "next_agent": "summarizer"
    }
