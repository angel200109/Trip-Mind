"""
Planner 节点 — 独立规划阶段（仅生成JSON计划，不执行工具）
使用 DeepSeek R1 为 full_travel 查询生成执行计划
"""
import json
from typing import Dict, Any

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage

from config.settings import (
    R1_MODEL, DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, R1_TEMPERATURE
)
from graph.state import GlobalState
from tools.tool_provider import get_tool_provider


async def planner_node(state: GlobalState) -> Dict[str, Any]:
    """
    Planner 节点函数 — 为 full_travel 查询生成JSON执行计划

    流程：
    1. 从 state["intent_context"] 读取旅行参数（destination, origin, travel_days, budget, travel_date, preferences）
    2. 从 state["user_query"] 读取用户查询
    3. 获取 tool_provider.get_tool_map() 的所有工具名
    4. 用 DeepSeek R1 输出 JSON 格式的查询计划
    5. 解析 JSON（处理 markdown code block 包裹）
    6. 如果解析失败，提供 fallback 默认计划（rag_search + gaode_weather）
    7. 将 plan_steps 存入 executor_context
    8. 返回更新的状态

    Args:
        state: LangGraph GlobalState

    Returns:
        Dict 更新，写入 executor_context, current_agent
    """
    print(f"\n{'='*60}")
    print("📋 Planner 开始生成计划")
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

    # 获取工具列表
    tool_provider = await get_tool_provider()
    tool_map = tool_provider.get_tool_map()
    available_tools = ", ".join(tool_map.keys())

    print(f"📦 可用工具: {available_tools}")

    # 创建 DeepSeek R1 LLM 实例
    r1_llm = ChatOpenAI(
        model=R1_MODEL,
        base_url=DEEPSEEK_BASE_URL,
        api_key=DEEPSEEK_API_KEY,
        temperature=R1_TEMPERATURE
    )

    # 构建 prompt
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

可用工具：{available_tools}

建议包含：rag_search（查询城市攻略和景点）+ 交通查询 + 酒店搜索 + 天气查询 + 黄历吉日

请确保输出为有效的JSON，不要添加任何Markdown代码块包装或额外的解释文本。
"""

    try:
        print(f"\n🧠 DeepSeek R1 开始制定计划...")
        response = await r1_llm.ainvoke([HumanMessage(content=problem)])
        content = response.content.strip()

        print(f"\n📋 R1 返回原始内容（前200字符）:")
        print(content[:200])

        # 处理 markdown code block 包裹
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

        # 解析 JSON
        plan_data = json.loads(content.strip())
        query_plan = plan_data.get("query_plan", [])

        print(f"\n✅ 计划制定完成，共 {len(query_plan)} 步")
        for i, step in enumerate(query_plan):
            tool_name = step.get("tool", "")
            description = step.get("description", "")
            print(f"  步骤 {i+1}: {tool_name} - {description}")

        plan_steps = query_plan

    except json.JSONDecodeError as e:
        print(f"\n⚠️ JSON 解析失败: {e}")
        print(f"   使用默认 fallback 计划")

        # Fallback: 默认计划（rag_search + gaode_weather）
        plan_steps = [
            {
                "tool": "rag_search",
                "params": {"query": f"{destination} 旅游攻略景点推荐"},
                "description": f"查询 {destination} 的旅游攻略、景点推荐和特色美食"
            },
            {
                "tool": "gaode_weather",
                "params": {"city": destination},
                "description": f"查询 {destination} 的天气预报"
            }
        ]

    except Exception as e:
        print(f"\n❌ 计划生成异常: {e}")
        import traceback
        traceback.print_exc()

        # 继续使用默认 fallback
        plan_steps = [
            {
                "tool": "rag_search",
                "params": {"query": f"{destination} 旅游攻略景点推荐"},
                "description": f"查询 {destination} 的旅游攻略、景点推荐和特色美食"
            },
            {
                "tool": "gaode_weather",
                "params": {"city": destination},
                "description": f"查询 {destination} 的天气预报"
            }
        ]

    # 初始化或更新 executor_context
    executor_context = state.get("executor_context") or {
        "tool_results": [],
        "rag_results_history": [],
        "collected_info": {},
        "plan_steps": None
    }

    # 将 plan_steps 存入 executor_context
    executor_context["plan_steps"] = plan_steps

    print(f"\n{'='*60}")
    print(f"✅ Planner 完成")
    print(f"  生成计划步骤数: {len(plan_steps)}")
    print(f"  下一步: executor")
    print(f"{'='*60}\n")

    return {
        "executor_context": executor_context,
        "current_agent": "planner",
    }
