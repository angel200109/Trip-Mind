"""
Main Agent - 协调者，负责路由和控制流程
支持从 Feedback 返回后的决策
"""
from typing import Dict, Any
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, AIMessage, BaseMessage
from config.settings import QWEN3_MODEL, QWEN3_API_BASE, DASHSCOPE_API_KEY, QWEN3_TEMPERATURE
from graph.state import GlobalState
from memory import get_memory_manager


def format_messages(messages: list) -> str:
    """格式化消息历史用于提示词"""
    formatted = []
    for msg in messages:
        try:
            if isinstance(msg, HumanMessage):
                role = "用户"
                content = msg.content or ""
            elif isinstance(msg, AIMessage):
                role = "助手"
                content = msg.content or ""
            elif isinstance(msg, dict):
                role = msg.get("role", "未知")
                if role == "user":
                    role = "用户"
                elif role == "assistant":
                    role = "助手"
                content = msg.get("content", "")
            elif hasattr(msg, 'type'):
                if msg.type == 'human':
                    role = "用户"
                elif msg.type == 'ai':
                    role = "助手"
                else:
                    role = "未知"
                content = getattr(msg, 'content', "")
            else:
                continue
            formatted.append(f"{role}: {content}")
        except Exception:
            continue
    return "\n".join(formatted)


def build_conversation_context(conversation_history: str, user_preferences: str) -> str:
    """Build the context used by direct conversation replies."""
    return f"""用户画像：
{user_preferences}

对话历史：
{conversation_history}"""


async def regenerate_with_summarizer(state: GlobalState, confirmation_message: str) -> str:
    """
    直接调用 Summarizer 重新生成回答
    """
    from agent_nodes.summarizer_agent import summarizer_agent_node
    
    print(f"\n🔄 复用之前的工具结果，调用 Summarizer 重新生成...")
    
    result = await summarizer_agent_node(state)
    
    summarizer_context = result.get("summarizer_context", {})
    regenerated_answer = summarizer_context.get("final_summary", "")
    
    if regenerated_answer:
        return f"{confirmation_message}\n\n根据您的反馈，我重新为您生成了回答：\n\n{regenerated_answer}"
    
    return f"{confirmation_message}\n\n请您重新提问，我会根据您的新偏好来回答！"


async def main_agent_node(state: GlobalState) -> Dict[str, Any]:
    """
    主协调者Agent节点
    
    职责：
    1. 判断是否是从 Feedback 返回
    2. 如果是从 Feedback 返回，决定是重新规划还是只重新生成
    3. 如果是新查询，判断类型并路由
    """
    print(f"\n{'='*60}")
    print("▶️ Main Agent 开始执行")
    print(f"{'='*60}")
    
    messages = state.get("messages") or []
    user_query = state.get("user_query", "") or ""
    current_agent = state.get("current_agent", "")
    
    print(f"📊 当前状态:")
    print(f"  上一个 Agent: {current_agent}")
    print(f"  对话历史长度: {len(messages)}")
    print(f"  用户查询: {user_query}")

    # ========== 加载记忆上下文 ==========
    memory_mgr = get_memory_manager()
    session_id = state.get("session_id", "default")
    user_id = state.get("user_id", "default_user")

    try:
        memory_context = await memory_mgr.router.load_context(session_id, user_id, user_query)
        # 更新工作记忆
        memory_mgr.working.update(session_id, {"last_query": user_query, "agent": "main"})
    except Exception as e:
        print(f"  ⚠️ 记忆加载失败（降级继续）: {e}")
        memory_context = {}

    # ========== 情况 1：从 Feedback 返回 ==========
    if current_agent == "feedback":
        print(f"\n🔙 从 Feedback Agent 返回")
        
        needs_replan = state.get("needs_replan", False)
        feedback_type = state.get("feedback_type", "neutral")
        confirmation_message = state.get("confirmation_message", "好的，我记住您的反馈了！")
        
        print(f"  feedback_type: {feedback_type}")
        print(f"  needs_replan: {needs_replan}")
        
        executor_context = state.get("executor_context") or {}
        tool_results = executor_context.get("tool_results", []) if executor_context else []
        rag_results = executor_context.get("rag_results_history", []) if executor_context else []
        
        if needs_replan:
            print(f"\n🔄 需要重新规划（核心需求改变）")
            
            # 重置各子 Agent 的上下文，重新走完整流程
            return {
                "current_agent": "main",
                "next_agent": "planner",
                "is_complete": False,
                "planner_context": None,
                "executor_context": None,
                "summarizer_context": None,
                "messages": [AIMessage(content=confirmation_message)],
                "memory_context": memory_context,
            }
        
        elif tool_results or rag_results:
            print(f"\n🔄 有之前的工具结果，直接调用 Summarizer 重新生成")

            final_answer = await regenerate_with_summarizer(state, confirmation_message)

            return {
                "current_agent": "main",
                "next_agent": None,
                "is_complete": True,
                "final_answer": final_answer,
                "messages": [AIMessage(content=final_answer)],
                "memory_context": memory_context,
            }

        else:
            print(f"\n💬 没有之前的工具结果，给出友好的确认回应")

            llm = ChatOpenAI(
                model=QWEN3_MODEL,
                base_url=QWEN3_API_BASE,
                api_key=DASHSCOPE_API_KEY,
                temperature=QWEN3_TEMPERATURE
            )

            friendly_prompt = ChatPromptTemplate.from_messages([
                ("system", """你是一个友好的旅游助手。用户刚刚给了你一个反馈，你已经记住了他们的偏好。

请给用户一个友好、温暖的回应，包括：
1. 确认你已经记住了他们的偏好
2. 询问他们现在有什么旅行需求，或者主动提供一些帮助
3. 语气要亲切、自然

不要说"请您重新提问"这样的话，要更主动地帮助用户。"""),
                ("human", """用户的反馈：{user_feedback}
确认消息：{confirmation_message}

请给出友好的回应：""")
            ])

            user_feedback = state.get("user_query", "")
            chain = friendly_prompt | llm
            friendly_response = (await chain.ainvoke({
                "user_feedback": user_feedback,
                "confirmation_message": confirmation_message
            })).content.strip()

            final_answer = f"{confirmation_message}\n\n{friendly_response}"

            return {
                "current_agent": "main",
                "next_agent": None,
                "is_complete": True,
                "final_answer": final_answer,
                "messages": [AIMessage(content=final_answer)],
                "memory_context": memory_context,
            }

    # ========== 情况 2：新查询 ==========
    print(f"\n🆕 处理新查询")
    
    llm = ChatOpenAI(
        model=QWEN3_MODEL,
        base_url=QWEN3_API_BASE,
        api_key=DASHSCOPE_API_KEY,
        temperature=0.3
    )
    
    prompt = ChatPromptTemplate.from_messages([
        ("system", """你是一个查询分类器。请判断用户的查询属于哪一类。

分类标准：
- feedback: 用户在给出反馈（例如"我喜欢古镇"、"下次别推荐寺庙"、"这个不错"、"预算有限"等）
- conversation: 对话类查询（问候、感谢、再见、追问"我刚刚说了什么"等）
- travel: 旅游规划类查询（询问景点、天气、美食、攻略、推荐、规划行程等）

请只返回分类结果（feedback/conversation/travel），不要返回其他内容。"""),
        ("human", "用户查询：{user_query}")
    ])
    
    chain = prompt | llm
    # tags 标记分类器调用，流式输出层据此跳过（避免输出 "travel/conversation" 等分类结果）
    classification_response = await chain.ainvoke(
        {"user_query": user_query}, config={"tags": ["query_classifier"]}
    )
    query_type = classification_response.content.strip().lower()
    
    print(f"\n🔍 查询类型判断: {query_type}")
    
    if "feedback" in query_type:
        print(f"\n💬 检测到用户反馈，路由给 Feedback Agent")
        print(f"{'='*60}\n")
        return {
            "current_agent": "main",
            "next_agent": "feedback",
            "memory_context": memory_context,
        }
    
    if "conversation" in query_type:
        print(f"\n💬 检测到对话类查询，直接回答")
        
        conversation_llm = ChatOpenAI(
            model=QWEN3_MODEL,
            base_url=QWEN3_API_BASE,
            api_key=DASHSCOPE_API_KEY,
            temperature=QWEN3_TEMPERATURE
        )
        
        conversation_prompt = ChatPromptTemplate.from_messages([
            ("system", """你是一个友好的旅游助手。请根据对话历史给出恰当的回应。

可用信息：
- 用户当前查询：{user_query}
- 已记录的用户画像和偏好：{user_preferences}

请直接给出回应，不要使用任何格式标记。"""),
            ("human", """{conversation_context}

请回应：""")
        ])
        
        conversation_history = format_messages(messages)
        # 用户偏好统一从 memory_context 读取（入口 router 已注入）
        from agent_nodes.summarizer_agent import format_preferences_for_prompt
        prefs = memory_context.get("preferences") or {}
        user_preferences = format_preferences_for_prompt(prefs) if prefs else "暂无记录"
        # 用短期记忆增强上下文
        if memory_context.get("short_term"):
            recent = memory_context["short_term"][-3:]
            recent_text = "\n".join([f"  {m.get('role','?')}: {m.get('content','')[:100]}" for m in recent])
            user_preferences += f"\n最近对话: \n{recent_text}"
        conversation_context = build_conversation_context(conversation_history, user_preferences)
        conversation_chain = conversation_prompt | conversation_llm
        response = (await conversation_chain.ainvoke({
            "user_query": user_query,
            "user_preferences": user_preferences,
            "conversation_context": conversation_context
        })).content.strip()
        
        print(f"\n✅ 直接回答用户问题")
        print(f"{'='*60}\n")
        return {
            "current_agent": "main",
            "next_agent": None,
            "is_complete": True,
            "final_answer": response,
            "messages": [AIMessage(content=response)],
            "memory_context": memory_context,
        }
    
    print(f"\n🔀 旅游查询，路由给 Planner Agent")
    print(f"{'='*60}\n")
    return {
        "current_agent": "main",
        "next_agent": "planner",
        "memory_context": memory_context,
    }
