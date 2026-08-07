"""
Summarizer Agent - 负责整合结果生成最终回答
使用自己的上下文：summarizer_context
支持用户偏好个性化（从 state.memory_context 统一读取）
"""
from typing import Dict, Any
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from config.settings import QWEN3_MODEL, QWEN3_API_BASE, DASHSCOPE_API_KEY, QWEN3_TEMPERATURE
from graph.state import GlobalState


def format_preferences_for_prompt(prefs: Dict[str, Any]) -> str:
    """将偏好 dict 格式化为提示词字符串（基于 PG user_preferences 字段）"""
    if not prefs:
        return ""

    lines = ["========== 用户偏好（请根据这些偏好调整推荐） =========="]

    if prefs.get("travel_style"):
        lines.append(f"- 旅行风格：{', '.join(prefs['travel_style'])}")
    if prefs.get("budget_level"):
        lines.append(f"- 预算水平：{prefs['budget_level']}")
    if prefs.get("max_daily_budget"):
        lines.append(f"- 每日预算上限：{prefs['max_daily_budget']}元")
    if prefs.get("hotel_preference"):
        lines.append(f"- 住宿偏好：{', '.join(prefs['hotel_preference'])}")
    if prefs.get("dietary_restrictions"):
        lines.append(f"- 饮食禁忌：{', '.join(prefs['dietary_restrictions'])}")
    if prefs.get("cuisine_preference"):
        lines.append(f"- 菜系偏好：{', '.join(prefs['cuisine_preference'])}")
    if prefs.get("liked_activities"):
        lines.append(f"- 喜欢的活动：{', '.join(prefs['liked_activities'])}")
    if prefs.get("disliked_activities"):
        lines.append(f"- 不喜欢的活动：{', '.join(prefs['disliked_activities'])}")
    if prefs.get("transport_priority"):
        lines.append(f"- 交通优先级：{', '.join(prefs['transport_priority'])}")

    lines.append("=" * 50)
    return "\n".join(lines)


async def summarizer_agent_node(state: GlobalState) -> Dict[str, Any]:
    """
    总结Agent节点
    使用自己的上下文：summarizer_context
    
    职责：
    1. 整合所有工具执行结果
    2. 结合用户偏好和兴趣
    3. 生成最终的旅游规划回答
    """
    print(f"\n{'='*60}")
    print("▶️ Summarizer Agent 开始执行")
    print(f"{'='*60}")
    
    # 从全局状态读取需要的信息
    user_query = state.get("user_query", "")
    
    # 从 intent_context 中获取信息
    intent_context = state.get("intent_context") or {}
    query_type = intent_context.get("query_type", "full_travel")
    query_mode = "simple" if query_type == "simple_travel" else "full"
    destination = intent_context.get("destination", "")
    origin = intent_context.get("origin", "")
    travel_days = intent_context.get("travel_days", 0)
    budget = intent_context.get("budget", 0)
    travel_date = intent_context.get("travel_date", "")
    preferences = intent_context.get("preferences", [])
    
    # 从 Executor 的上下文中获取信息
    executor_context = state.get("executor_context") or {}
    tool_results = executor_context.get("tool_results", []) if executor_context else []
    rag_results_history = executor_context.get("rag_results_history", []) if executor_context else []
    
    # 初始化或获取自己的上下文
    summarizer_context = state.get("summarizer_context") or {
        "final_summary": None
    }
    
    # 获取用户偏好（统一从 state.memory_context 读取，入口 router 已注入）
    memory_context = state.get("memory_context") or {}
    prefs = memory_context.get("preferences") or {}
    user_preferences_str = format_preferences_for_prompt(prefs)
    
    print(f"📊 状态信息:")
    print(f"  query_mode: {query_mode}")
    print(f"  工具执行结果数: {len(tool_results)}")
    print(f"  RAG结果数: {len(rag_results_history)}")
    
    llm = ChatOpenAI(
        model=QWEN3_MODEL,
        base_url=QWEN3_API_BASE,
        api_key=DASHSCOPE_API_KEY,
        temperature=QWEN3_TEMPERATURE,
        streaming=True
    )
    
    # 简单查询模式的提示词 - 更简洁，专注于用户的具体问题
    simple_prompt = ChatPromptTemplate.from_messages([
        ("system", """你是一个专业的旅游助手。根据用户的查询和收集到的信息，为用户提供友好、直接的回答。

{user_preferences}

请参考以下信息：
1. 用户的原始查询
2. 从知识库中检索到的相关信息
3. 天气等工具查询结果

重要规则：
- 请根据用户偏好调整推荐
- 如果知识库检索结果与用户查询的目的地不相关，请明确告知用户"我的知识库中暂时没有{{目的地}}的详细攻略"，然后提供其他可行的建议
- 绝对不要编造任何不存在的景点、酒店、价格等信息
- 如果有工具查询结果（如天气），优先使用工具查询结果
- 用友好、简洁的语气回答，直接针对用户的问题，不要生成完整的旅游规划。"""),
        ("human", """用户查询：{user_query}

知识库检索结果：
{rag_results}

工具执行结果：
{tool_results}

请直接回答用户的问题：""")
    ])
    
    # 完整查询模式的提示词
    full_prompt = ChatPromptTemplate.from_messages([
        ("system", """你是一个专业的旅游规划师。根据收集到的信息，为用户生成一份详细、友好、实用的旅游规划。

{user_preferences}

请参考以下信息：
1. 用户的原始查询
2. 从知识库中检索到的相关信息
3. 天气、交通、住宿等工具查询结果
4. 用户的兴趣和偏好
5. 用户不喜欢的事物

重要规则：
- 请根据用户偏好调整推荐
- 如果知识库检索结果与用户查询的目的地不相关，请明确告知用户"我的知识库中暂时没有{{目的地}}的详细攻略"
- 绝对不要编造任何不存在的景点、酒店、价格等信息
- 如果有工具查询结果，优先使用工具查询结果
- 对于没有信息的部分，明确说明"暂无相关信息"

请给出一个完整的旅游规划，包括：
- 简要的行程概述
- 每日行程建议（如果有天数信息）
- 交通建议
- 住宿推荐
- 注意事项
- 根据用户兴趣的特别推荐

请用友好、专业的语气回答，确保信息准确、实用。"""),
        ("human", """用户查询：{user_query}

目的地：{destination}
出发地：{origin}
旅行天数：{travel_days}
预算：{budget}
出发日期：{travel_date}
用户偏好：{preferences}

知识库检索结果：
{rag_results}

工具执行结果：
{tool_results}

请生成旅游规划：""")
    ])
    
    rag_results = "\n---\n".join(rag_results_history)
    
    tool_results_str = ""
    for result in tool_results:
        if "error" in result:
            tool_results_str += f"【{result['tool']}】错误: {result['error']}\n"
        else:
            tool_results_str += f"【{result['tool']}】{result['result']}\n"
    
    print(f"\n📝 开始生成回答，模式: {query_mode}")
    print(f"📋 用户偏好已注入")
    
    # 构建操作提示
    operation_hints = []
    if rag_results:
        operation_hints.append("✅ 已找到知识库信息")
    if tool_results_str:
        operation_hints.append("✅ 已查询MCP工具")
    
    mode_hint = "🔍 简单查询模式" if query_mode == "simple" else "🧠 完整规划模式"
    
    if query_mode == "simple":
        print(f"  使用简单查询提示词")
        chain = simple_prompt | llm
        response = await chain.ainvoke({
            "user_query": user_query,
            "user_preferences": user_preferences_str,
            "rag_results": rag_results,
            "tool_results": tool_results_str
        })
    else:
        print(f"  使用完整规划提示词")
        chain = full_prompt | llm
        response = await chain.ainvoke({
            "user_query": user_query,
            "user_preferences": user_preferences_str,
            "destination": destination,
            "origin": origin,
            "travel_days": travel_days,
            "budget": budget,
            "travel_date": travel_date,
            "preferences": preferences,
            "rag_results": rag_results,
            "tool_results": tool_results_str
        })
    
    # 更新自己的上下文
    summarizer_context["final_summary"] = response.content
    
    print(f"\n✅ Summarizer Agent 执行完成")
    print(f"  回答长度: {len(response.content)} 字符")
    print(f"  下一步: 结束")
    print(f"{'='*60}\n")
    
    # 将操作提示添加到最终回答的开头
    hints_str = f"{mode_hint}\n"
    if operation_hints:
        hints_str += "\n".join(operation_hints) + "\n\n"
    
    final_answer = hints_str + response.content
    summarizer_context["final_summary"] = final_answer

    return {
        "summarizer_context": summarizer_context,
        "current_agent": "summarizer",
        "next_agent": None,
        "is_complete": True,
        "final_answer": final_answer
    }
