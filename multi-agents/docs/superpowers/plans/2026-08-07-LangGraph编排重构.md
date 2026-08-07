# LangGraph 编排重构：IntentRouter + 双执行路径

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有 5 节点架构（Main→Planner→Executor→Summarizer→FinalOutput）重构为 6 节点双路径架构（IntentRouter→ReactExecutor/Planner+StepExecutor→Summarizer→FinalOutput），减少一次 LLM 调用，提升首字响应速度，职责更单一。

**Architecture:** IntentRouter 通过一次结构化 LLM 调用完成三分类（greeting/simple_travel/full_travel）+ 参数提取，根据分类结果路由到不同执行路径。记忆加载改为分级策略：分类前只加载轻量记忆（working+short_term），分类后按需加载 preferences/travel_history。Executor 拆分为两个独立节点 ReactExecutor 和 Planner+StepExecutor，Summarizer 和 FinalOutput 共用。

**Tech Stack:** LangGraph StateGraph, LangChain ChatOpenAI (Qwen3 + DeepSeek R1), Pydantic structured output, langchain-mcp-adapters

## Global Constraints

- Python 3.11+，所有 agent 节点函数签名统一为 `async def xxx_node(state: GlobalState) -> Dict[str, Any]`
- 全局单例模式保持不变：`get_tool_provider()`、`get_memory_manager()`
- 现有 SSE 流式推送协议（chat_service.py）不能 break，前端不改动
- 现有测试 `tests/test_memory_router.py` 需继续通过
- Prompt 模板集中定义在 `config/prompts.py`
- 模型配置：Qwen3 用于分类/对话/总结，DeepSeek R1 用于 Plan 阶段

---

### Task 1: 更新 GlobalState 状态定义

**Files:**
- Modify: `graph/state.py:1-68`

**Interfaces:**
- Produces: `GlobalState` TypedDict（新增 `query_type: Optional[str]`，移除 `PlannerContext.needs_clarification`/`clarification_question`，新增 `IntentContext`）
- Produces: `IntentContext` TypedDict（替代原 `PlannerContext` 的分类字段）

- [ ] **Step 1: 重写 state.py**

将 `PlannerContext` 拆分为 `IntentContext`（IntentRouter 的输出）和保留 `PlannerContext`（仅用于 Planner 节点的执行计划）：

```python
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
```

- [ ] **Step 2: 运行现有测试确保无导入错误**

Run: `cd multi-agents && python -c "from graph.state import GlobalState, IntentContext, ExecutorContext, SummarizerContext; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add multi-agents/graph/state.py
git commit -m "refactor(state): 替换 PlannerContext 为 IntentContext，适配新 6 节点架构"
```

---

### Task 2: 实现 IntentRouter 节点

**Files:**
- Create: `agent_nodes/intent_router.py`
- Modify: `config/prompts.py`（新增 `INTENT_ROUTER_PROMPT`）

**Interfaces:**
- Consumes: `GlobalState.user_query`, `GlobalState.messages`, `GlobalState.session_id`, `GlobalState.user_id`
- Produces: `GlobalState.intent_context` (IntentContext), `GlobalState.memory_context`, `GlobalState.final_answer`（greeting 时）, `GlobalState.is_complete`（greeting/追问时）

- [ ] **Step 1: 在 config/prompts.py 中添加 INTENT_ROUTER_PROMPT**

```python
INTENT_ROUTER_PROMPT = """你是一个旅行助手的意图识别器。请分析用户查询并提取信息。

分类标准（三选一）：
- greeting: 日常问候、闲聊、感谢、对上次回答的评价。不涉及任何地理/出行/旅行要素。
- simple_travel: 简单出行查询。用户询问天气、单次交通（火车票/航班）、某地景点/美食推荐、附近POI等，不涉及多日行程规划。
- full_travel: 旅游规划。用户需要多日行程安排、完整旅行方案。关键词：行程安排、几天几夜、怎么安排/规划。必须有出发地，如果用户未提供出发地则标记 needs_clarification=true。

判断优先级：
1. 含"行程安排""几天几夜""旅行规划"等 → full_travel
2. 含地点/出行要素但无多日规划需求 → simple_travel
3. 纯社交对话 → greeting

对于 simple_travel 和 full_travel，请同时提取旅行参数。未提及的字段留空或设为0。

今天日期：{today}
"""
```

- [ ] **Step 2: 创建 agent_nodes/intent_router.py**

```python
"""
IntentRouter - 意图识别 + 参数提取 + 路由
一次结构化 LLM 调用完成三分类和参数提取
记忆分级加载：分类前轻量加载，分类后按需加载
"""
from typing import Dict, Any, Literal, Optional, List
from datetime import datetime

from pydantic import BaseModel, Field
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, AIMessage

from config.settings import QWEN3_MODEL, QWEN3_API_BASE, DASHSCOPE_API_KEY
from config.prompts import INTENT_ROUTER_PROMPT
from graph.state import GlobalState
from memory import get_memory_manager


class IntentClassification(BaseModel):
    """三分类 + 参数提取的结构化输出"""
    query_type: Literal["greeting", "simple_travel", "full_travel"] = Field(
        description="查询类型：greeting=日常问候, simple_travel=简单出行, full_travel=旅游规划"
    )
    destination: str = Field(default="", description="目的地城市")
    origin: str = Field(default="", description="出发地城市")
    travel_days: int = Field(default=0, description="旅行天数")
    budget: float = Field(default=0, description="预算（元）")
    travel_date: str = Field(default="", description="出发日期 YYYY-MM-DD")
    preferences: List[str] = Field(default_factory=list, description="旅行偏好")


def _detect_multi_destination(user_query: str, destination: str, origin: str) -> dict:
    """检测是否为多目的地场景"""
    roundtrip_keywords = ["往返", "来回", "回程", "返程", "返回"]
    if any(kw in user_query for kw in roundtrip_keywords):
        return {"is_multi_destination": False}

    multi_dest_keywords = ["再去", "然后去", "接着去", "顺便去", "再到", "然后到", "接着到", "之后去", "之后到"]
    if any(kw in user_query for kw in multi_dest_keywords):
        return {"is_multi_destination": True}

    norm = (destination or "").replace(",", "，").replace("、", "，")
    cities = [c.strip() for c in norm.split("，") if c.strip()]
    unique = list(dict.fromkeys(cities))

    if len(unique) >= 3:
        return {"is_multi_destination": True}
    if len(unique) == 2 and origin and origin not in unique:
        return {"is_multi_destination": True}

    return {"is_multi_destination": False}


async def intent_router_node(state: GlobalState) -> Dict[str, Any]:
    """
    IntentRouter 节点
    1. 轻量加载记忆（working + short_term）
    2. LLM 三分类 + 参数提取
    3. 按分类按需加载 preferences/travel_history
    4. greeting → 直接回复；full_travel 缺字段 → 追问；否则路由到下游
    """
    user_query = state.get("user_query", "") or ""
    messages = state.get("messages") or []
    session_id = state.get("session_id", "default")
    user_id = state.get("user_id", "default_user")

    # ── 1. 轻量加载记忆（分类前） ──
    memory_mgr = get_memory_manager()
    try:
        working = memory_mgr.working.get_context(session_id)
        short_term = await memory_mgr.short_term.get_history(session_id)
    except Exception:
        working, short_term = {}, []

    memory_mgr.working.update(session_id, {"last_query": user_query, "agent": "intent_router"})

    # ── 2. 结构化 LLM 分类 + 参数提取 ──
    llm = ChatOpenAI(
        model=QWEN3_MODEL,
        base_url=QWEN3_API_BASE,
        api_key=DASHSCOPE_API_KEY,
        temperature=0.3,
    )
    structured_llm = llm.with_structured_output(IntentClassification)

    today = datetime.now().strftime("%Y-%m-%d")
    prompt = ChatPromptTemplate.from_messages([
        ("system", INTENT_ROUTER_PROMPT.format(today=today)),
        ("human", "用户查询：{user_query}"),
    ])
    chain = prompt | structured_llm
    classification = await chain.ainvoke(
        {"user_query": user_query},
        config={"tags": ["intent_classifier"]},
    )

    query_type = classification.query_type

    # ── 3. 按分类按需加载记忆 ──
    memory_context: Dict[str, Any] = {"working": working}
    if short_term:
        memory_context["short_term"] = short_term

    if query_type != "greeting":
        try:
            prefs = await memory_mgr.long_term.get_preferences(user_id)
            memory_context["preferences"] = prefs
        except Exception:
            memory_context["preferences"] = {}

        if any(kw in user_query for kw in ["上次", "之前", "去过", "历史", "以前", "曾经"]):
            try:
                memory_context["travel_history"] = await memory_mgr.long_term.get_travel_history(user_id, limit=5)
            except Exception:
                pass

    # ── 4. 路由决策 ──
    intent_context = {
        "query_type": query_type,
        "destination": classification.destination,
        "origin": classification.origin,
        "travel_days": classification.travel_days,
        "budget": classification.budget,
        "travel_date": classification.travel_date,
        "preferences": classification.preferences,
        "needs_clarification": False,
        "clarification_question": None,
        "scenario_type": None,
    }

    # greeting → 直接回复
    if query_type == "greeting":
        conversation_llm = ChatOpenAI(
            model=QWEN3_MODEL,
            base_url=QWEN3_API_BASE,
            api_key=DASHSCOPE_API_KEY,
            temperature=0.7,
        )
        conv_prompt = ChatPromptTemplate.from_messages([
            ("system", "你是一个友好的旅游助手。请根据对话上下文给出简短、亲切的回应。"),
            ("human", "{user_query}"),
        ])
        conv_chain = conv_prompt | conversation_llm
        response = await conv_chain.ainvoke({"user_query": user_query})

        return {
            "intent_context": intent_context,
            "memory_context": memory_context,
            "current_agent": "intent_router",
            "is_complete": True,
            "final_answer": response.content.strip(),
            "messages": [AIMessage(content=response.content.strip())],
        }

    # full_travel → 检查必填字段
    if query_type == "full_travel":
        multi_dest = _detect_multi_destination(user_query, classification.destination, classification.origin)
        intent_context["scenario_type"] = "multi_destination" if multi_dest["is_multi_destination"] else "standard"

        if classification.destination and not classification.origin:
            clarification = "您想从哪个城市出发呢？这样我可以帮您规划交通和完整行程。"
            intent_context["needs_clarification"] = True
            intent_context["clarification_question"] = clarification
            return {
                "intent_context": intent_context,
                "memory_context": memory_context,
                "current_agent": "intent_router",
                "is_complete": True,
                "final_answer": clarification,
            }

    # simple_travel / full_travel（信息完整） → 路由到下游
    return {
        "intent_context": intent_context,
        "memory_context": memory_context,
        "current_agent": "intent_router",
        "is_complete": False,
    }
```

- [ ] **Step 3: 验证模块可导入**

Run: `cd multi-agents && python -c "from agent_nodes.intent_router import intent_router_node; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add multi-agents/agent_nodes/intent_router.py multi-agents/config/prompts.py
git commit -m "feat(intent_router): 实现三分类+参数提取节点，分级记忆加载"
```

---

### Task 3: 实现 ReactExecutor 节点

**Files:**
- Create: `agent_nodes/react_executor.py`

**Interfaces:**
- Consumes: `GlobalState.intent_context`（destination, origin, travel_days 等）, `GlobalState.user_query`, `ToolProvider.get_tools()`
- Produces: `GlobalState.executor_context` (ExecutorContext)

- [ ] **Step 1: 创建 agent_nodes/react_executor.py**

从现有 `executor_agent.py` 的 `react_loop()` 提取为独立节点：

```python
"""
ReactExecutor - 简单出行查询的 ReAct 执行器
使用 LangGraph create_react_agent 自动循环调用工具
"""
from typing import Dict, Any, List

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.prebuilt import create_react_agent

from config.settings import QWEN3_MODEL, QWEN3_API_BASE, DASHSCOPE_API_KEY, QWEN3_TEMPERATURE
from graph.state import GlobalState
from tools.tool_provider import get_tool_provider


def _extract_executor_context(messages: List, existing_rag_history: List[str] = None) -> Dict[str, Any]:
    """从 create_react_agent 输出消息中提取 ExecutorContext"""
    tool_results = []
    rag_results_history = list(existing_rag_history or [])
    collected_info = {}

    for msg in messages:
        if isinstance(msg, ToolMessage):
            tool_name = msg.name or ""
            result_str = str(msg.content)
            tool_results.append({"tool": tool_name, "result": result_str})
            collected_info[tool_name] = result_str
            if tool_name == "rag_search":
                rag_results_history.append(result_str)

    return {
        "tool_results": tool_results,
        "rag_results_history": rag_results_history,
        "collected_info": collected_info,
    }


async def react_executor_node(state: GlobalState) -> Dict[str, Any]:
    """
    ReactExecutor 节点 - 使用 create_react_agent 自主决策工具调用
    """
    user_query = state.get("user_query", "")
    intent_ctx = state.get("intent_context") or {}

    destination = intent_ctx.get("destination", "")
    origin = intent_ctx.get("origin", "")
    travel_days = intent_ctx.get("travel_days", 0)
    budget = intent_ctx.get("budget", 0)
    travel_date = intent_ctx.get("travel_date", "")
    preferences = intent_ctx.get("preferences", [])

    tool_provider = await get_tool_provider()
    tools = tool_provider.get_tools()

    llm = ChatOpenAI(
        model=QWEN3_MODEL,
        base_url=QWEN3_API_BASE,
        api_key=DASHSCOPE_API_KEY,
        temperature=QWEN3_TEMPERATURE,
    )

    system_prompt = (
        f"你是一个智能旅行助手。\n\n"
        f"用户需求：{user_query}\n"
        f"已提取信息：目的地={destination}，出发地={origin}，"
        f"天数={travel_days}，预算={budget}，日期={travel_date}，偏好={preferences}\n\n"
        f"请使用可用工具查询必要信息，信息充分时直接给出最终回答。\n"
        f"不要重复调用已成功执行的工具。"
    )

    agent = create_react_agent(model=llm, tools=tools, prompt=system_prompt)

    result = await agent.ainvoke({"messages": [HumanMessage(content=user_query)]})
    messages = result.get("messages", [])

    executor_context = _extract_executor_context(messages)

    return {
        "executor_context": executor_context,
        "current_agent": "react_executor",
    }
```

- [ ] **Step 2: 验证模块可导入**

Run: `cd multi-agents && python -c "from agent_nodes.react_executor import react_executor_node; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add multi-agents/agent_nodes/react_executor.py
git commit -m "feat(react_executor): 从 executor_agent 提取独立 ReAct 节点"
```

---

### Task 4: 实现 Planner 节点（仅生成计划）

**Files:**
- Create: `agent_nodes/planner.py`

**Interfaces:**
- Consumes: `GlobalState.intent_context`（destination, origin, travel_days, budget, travel_date, preferences）, `GlobalState.user_query`, `ToolProvider.get_tool_map()`
- Produces: `GlobalState.executor_context.plan_steps: List[Dict]`（新增字段，存储 JSON 计划）

- [ ] **Step 1: 更新 ExecutorContext 添加 plan_steps 字段**

在 `graph/state.py` 的 `ExecutorContext` 中新增：

```python
class ExecutorContext(TypedDict):
    """Executor 节点的输出上下文"""
    tool_results: List[Dict[str, Any]]
    rag_results_history: List[str]
    collected_info: Optional[Dict[str, Any]]
    plan_steps: Optional[List[Dict[str, Any]]]
```

- [ ] **Step 2: 创建 agent_nodes/planner.py**

从现有 `executor_agent.py` 的 `plan_then_execute()` 中提取规划部分：

```python
"""
Planner - 为 full_travel 场景生成 JSON 执行计划
仅规划，不执行工具调用
"""
from typing import Dict, Any
import json

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage

from config.settings import R1_MODEL, DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, R1_TEMPERATURE
from graph.state import GlobalState
from tools.tool_provider import get_tool_provider


async def planner_node(state: GlobalState) -> Dict[str, Any]:
    """
    Planner 节点 - DeepSeek R1 生成 JSON 执行计划
    """
    user_query = state.get("user_query", "")
    intent_ctx = state.get("intent_context") or {}

    destination = intent_ctx.get("destination", "")
    origin = intent_ctx.get("origin", "")
    travel_days = intent_ctx.get("travel_days", 0)
    budget = intent_ctx.get("budget", 0)
    travel_date = intent_ctx.get("travel_date", "")
    preferences = intent_ctx.get("preferences", [])

    tool_provider = await get_tool_provider()
    tool_map = tool_provider.get_tool_map()

    r1_llm = ChatOpenAI(
        model=R1_MODEL,
        base_url=DEEPSEEK_BASE_URL,
        api_key=DEEPSEEK_API_KEY,
        temperature=R1_TEMPERATURE,
    )

    problem = f"""用户的旅行需求：
最新查询：{user_query}

已提取信息：
- 目的地：{destination}
- 出发地：{origin}
- 总天数：{travel_days}
- 总预算：{budget}元
- 出发日期：{travel_date}
- 偏好：{', '.join(preferences) if preferences else '无'}

请制定详细的查询计划，输出JSON格式：
{{
  "query_plan": [
    {{"tool": "工具名", "params": {{"参数名": "参数值"}}, "description": "这一步的目的"}}
  ]
}}

可用工具：{', '.join(tool_map.keys())}

建议包含：rag_search + train_query + gaode_hotel_search + gaode_weather + lucky_day
"""

    plan_steps = []
    try:
        response = await r1_llm.ainvoke([HumanMessage(content=problem)])
        content = response.content.strip()

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
        plan_steps = plan_data.get("query_plan", [])
    except Exception as e:
        print(f"Planner 计划生成失败: {e}")
        plan_steps = [
            {"tool": "rag_search", "params": {"query": f"{destination} 旅游攻略"}, "description": "检索攻略"},
            {"tool": "gaode_weather", "params": {"city": destination}, "description": "查询天气"},
        ]

    executor_context = state.get("executor_context") or {
        "tool_results": [],
        "rag_results_history": [],
        "collected_info": {},
        "plan_steps": None,
    }
    executor_context["plan_steps"] = plan_steps

    return {
        "executor_context": executor_context,
        "current_agent": "planner",
    }
```

- [ ] **Step 3: 验证模块可导入**

Run: `cd multi-agents && python -c "from agent_nodes.planner import planner_node; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add multi-agents/agent_nodes/planner.py multi-agents/graph/state.py
git commit -m "feat(planner): 独立规划节点，只生成 JSON 计划不执行"
```

---

### Task 5: 实现 StepExecutor 节点

**Files:**
- Create: `agent_nodes/step_executor.py`

**Interfaces:**
- Consumes: `GlobalState.executor_context.plan_steps`, `ToolProvider.get_tool_map()`
- Produces: `GlobalState.executor_context`（填充 tool_results, rag_results_history, collected_info）

- [ ] **Step 1: 创建 agent_nodes/step_executor.py**

从现有 `executor_agent.py` 的 `plan_then_execute()` 中提取执行部分：

```python
"""
StepExecutor - 按 Planner 生成的 JSON 计划逐步执行工具
"""
from typing import Dict, Any

from graph.state import GlobalState
from tools.tool_provider import get_tool_provider


async def step_executor_node(state: GlobalState) -> Dict[str, Any]:
    """
    StepExecutor 节点 - 按计划逐步调用工具，容错执行
    """
    executor_context = state.get("executor_context") or {}
    plan_steps = executor_context.get("plan_steps") or []

    tool_results = executor_context.get("tool_results", [])
    rag_results_history = executor_context.get("rag_results_history", [])
    collected_info = executor_context.get("collected_info", {})

    tool_provider = await get_tool_provider()
    tool_map = tool_provider.get_tool_map()

    for i, step in enumerate(plan_steps):
        tool_name = step.get("tool", "")
        params = step.get("params", {})
        description = step.get("description", "")

        tool = tool_map.get(tool_name)
        if tool is None:
            tool_results.append({
                "tool": tool_name,
                "result": f"未知工具: {tool_name}",
                "step": description,
                "success": False,
            })
            continue

        try:
            observation = await tool.ainvoke(params)
            tool_results.append({
                "tool": tool_name,
                "result": observation,
                "step": description,
                "success": True,
            })
            if tool_name == "rag_search":
                rag_results_history.append(str(observation))
            collected_info[tool_name] = observation
        except Exception as e:
            tool_results.append({
                "tool": tool_name,
                "result": f"工具执行失败: {str(e)}",
                "step": description,
                "success": False,
            })

    executor_context["tool_results"] = tool_results
    executor_context["rag_results_history"] = rag_results_history
    executor_context["collected_info"] = collected_info

    return {
        "executor_context": executor_context,
        "current_agent": "step_executor",
    }
```

- [ ] **Step 2: 验证模块可导入**

Run: `cd multi-agents && python -c "from agent_nodes.step_executor import step_executor_node; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add multi-agents/agent_nodes/step_executor.py
git commit -m "feat(step_executor): 独立执行节点，按 JSON 计划逐步调工具"
```

---

### Task 6: 更新 Summarizer 节点适配新 state

**Files:**
- Modify: `agent_nodes/summarizer_agent.py:43-241`

**Interfaces:**
- Consumes: `GlobalState.intent_context`（替代原 planner_context）, `GlobalState.executor_context`, `GlobalState.memory_context`
- Produces: `GlobalState.summarizer_context`, `GlobalState.final_answer`, `GlobalState.is_complete`

- [ ] **Step 1: 修改 summarizer_agent.py 读取 intent_context**

将所有 `state.get("planner_context")` 替换为 `state.get("intent_context")`，并将 `query_mode` 的判断逻辑改为基于 `intent_context["query_type"]`：

```python
# 关键修改点（在 summarizer_agent_node 函数内）：

# 原来：
# planner_context = state.get("planner_context") or {}
# query_mode = planner_context.get("query_mode", "full")

# 改为：
intent_context = state.get("intent_context") or {}
query_type = intent_context.get("query_type", "full_travel")
query_mode = "simple" if query_type == "simple_travel" else "full"
destination = intent_context.get("destination", "")
origin = intent_context.get("origin", "")
travel_days = intent_context.get("travel_days", 0)
budget = intent_context.get("budget", 0)
travel_date = intent_context.get("travel_date", "")
preferences = intent_context.get("preferences", [])
```

其余逻辑（prompt 模板、LLM 调用、偏好注入）保持不变。

- [ ] **Step 2: 验证模块可导入**

Run: `cd multi-agents && python -c "from agent_nodes.summarizer_agent import summarizer_agent_node; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add multi-agents/agent_nodes/summarizer_agent.py
git commit -m "refactor(summarizer): 从 intent_context 读取参数，适配新架构"
```

---

### Task 7: 重写 workflow.py 图定义

**Files:**
- Modify: `graph/workflow.py:1-134`

**Interfaces:**
- Consumes: 所有节点函数（intent_router_node, react_executor_node, planner_node, step_executor_node, summarizer_agent_node, final_output_node）
- Produces: `travel_graph` 编译后的可执行图

- [ ] **Step 1: 重写 graph/workflow.py**

```python
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

    # 注册节点
    workflow.add_node("intent_router", intent_router_node)
    workflow.add_node("react_executor", react_executor_node)
    workflow.add_node("planner", planner_node)
    workflow.add_node("step_executor", step_executor_node)
    workflow.add_node("summarizer", summarizer_agent_node)
    workflow.add_node("final_output", final_output_node)

    # 入口
    workflow.set_entry_point("intent_router")

    # 条件路由：IntentRouter → 三条路径
    workflow.add_conditional_edges(
        "intent_router",
        route_after_intent_router,
        {
            "react_executor": "react_executor",
            "planner": "planner",
            "final_output": "final_output",
        },
    )

    # 固定边
    workflow.add_edge("react_executor", "summarizer")
    workflow.add_edge("planner", "step_executor")
    workflow.add_edge("step_executor", "summarizer")
    workflow.add_edge("summarizer", "final_output")
    workflow.add_edge("final_output", END)

    return workflow.compile()


travel_graph = create_travel_planning_graph()
```

- [ ] **Step 2: 验证图可编译**

Run: `cd multi-agents && python -c "from graph.workflow import travel_graph; print('Graph compiled OK')"`
Expected: `Graph compiled OK`

- [ ] **Step 3: Commit**

```bash
git add multi-agents/graph/workflow.py
git commit -m "refactor(workflow): 6 节点双路径架构，IntentRouter 统一入口"
```

---

### Task 8: 更新 chat_service.py 适配新节点名

**Files:**
- Modify: `services/chat_service.py:24-29`（AGENT_STATUS_MAP）
- Modify: `services/chat_service.py:52-93`（build_state_from_messages，移除 planner_context 初始化）
- Modify: `services/chat_service.py:254-274`（LLM 流式 token 过滤逻辑）

**Interfaces:**
- Consumes: `travel_graph.astream_events()` 产出的事件（节点名变更为新名称）
- Produces: SSE 事件流（格式不变，前端无需修改）

- [ ] **Step 1: 更新 AGENT_STATUS_MAP**

```python
AGENT_STATUS_MAP = {
    "intent_router": "正在分析您的需求...",
    "react_executor": "正在查询相关信息...",
    "planner": "正在规划行程方案...",
    "step_executor": "正在执行查询任务...",
    "summarizer": "正在整理旅行方案...",
}
```

- [ ] **Step 2: 更新 build_state_from_messages**

移除 `planner_context` 初始化，改为 `intent_context`:

```python
return {
    "user_query": user_query,
    "messages": langchain_messages,
    "intent_context": None,
    "executor_context": None,
    "summarizer_context": None,
    "current_agent": None,
    "next_agent": None,
    "is_complete": False,
    "final_answer": None,
    "pg_session_id": pg_session_id,
}
```

- [ ] **Step 3: 更新 LLM 流式 token 过滤逻辑**

将 `node_name in ("summarizer", "main")` 改为 `node_name in ("summarizer", "intent_router")`，tag 过滤改为 `"intent_classifier"`:

```python
elif kind == "on_chat_model_stream":
    metadata = event.get("metadata", {})
    node_name = metadata.get("langgraph_node", "")
    tags = event.get("tags") or []
    if node_name in ("summarizer", "intent_router") and "intent_classifier" not in tags:
        # ... 输出 token
```

- [ ] **Step 4: 验证模块可导入**

Run: `cd multi-agents && python -c "from services.chat_service import stream_chat; print('OK')"`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add multi-agents/services/chat_service.py
git commit -m "refactor(chat_service): 适配新 6 节点名称，SSE 协议不变"
```

---

### Task 9: 更新 __init__.py 和清理旧文件

**Files:**
- Modify: `agent_nodes/__init__.py`
- Delete: `agent_nodes/main_agent.py`（被 intent_router.py 替代）
- Delete: `agent_nodes/planner_agent.py`（被 intent_router.py + planner.py 替代）
- Delete: `agent_nodes/executor_agent.py`（被 react_executor.py + step_executor.py 替代）

**Interfaces:**
- Produces: 清洁的 `agent_nodes` 包导出

- [ ] **Step 1: 重写 agent_nodes/__init__.py**

```python
"""Agent nodes package - 6 节点双路径架构"""
from .intent_router import intent_router_node
from .react_executor import react_executor_node
from .planner import planner_node
from .step_executor import step_executor_node
from .summarizer_agent import summarizer_agent_node

__all__ = [
    "intent_router_node",
    "react_executor_node",
    "planner_node",
    "step_executor_node",
    "summarizer_agent_node",
]
```

- [ ] **Step 2: 删除旧文件**

```bash
cd multi-agents
rm agent_nodes/main_agent.py
rm agent_nodes/planner_agent.py
rm agent_nodes/executor_agent.py
```

- [ ] **Step 3: 验证完整导入链**

Run: `cd multi-agents && python -c "from graph.workflow import travel_graph; from services.chat_service import stream_chat; print('ALL OK')"`
Expected: `ALL OK`

- [ ] **Step 4: Commit**

```bash
git add multi-agents/agent_nodes/
git commit -m "refactor(agent_nodes): 清理旧节点文件，更新包导出"
```

---

### Task 10: 更新 MemoryRouter 适配新架构

**Files:**
- Modify: `memory/router.py:46-74`

**Interfaces:**
- Consumes: 不变（session_id, user_id, user_query）
- Produces: 不变（dict with working/short_term/preferences/travel_history/knowledge）

由于 IntentRouter 已经实现了分级加载逻辑，MemoryRouter 的 `load_context()` 方法**不再被直接调用**。但为了向后兼容（测试、其他调用方），保留该方法不做破坏性修改。仅需确认 IntentRouter 中直接调用底层方法的写法与 MemoryRouter 对外接口一致。

- [ ] **Step 1: 确认现有测试通过**

Run: `cd multi-agents && python -m pytest tests/test_memory_router.py -v`
Expected: All tests PASS

- [ ] **Step 2: Commit（如有修改）**

如果测试通过无需修改，跳过此 commit。

---

### Task 11: 端到端集成验证

**Files:**
- 无新文件

**Interfaces:**
- 验证完整流程：server.py → chat_service.py → travel_graph → 各节点

- [ ] **Step 1: 启动服务器验证无导入错误**

Run: `cd multi-agents && timeout 5 python -c "import server; print('Server module OK')" || true`
Expected: `Server module OK`（或超时但无 ImportError）

- [ ] **Step 2: 运行所有现有测试**

Run: `cd multi-agents && python -m pytest tests/ -v --timeout=30 2>&1 | head -50`
Expected: 无新增失败（原有依赖外部服务的测试可能 skip）

- [ ] **Step 3: 更新 CLAUDE.md 架构文档**

在 `multi-agents/CLAUDE.md` 的 Architecture 部分更新节点表和图：

```markdown
### LangGraph 工作流 (`graph/workflow.py`)

```
IntentRouter → ReactExecutor → Summarizer → FinalOutput → END
IntentRouter → Planner → StepExecutor → Summarizer → FinalOutput → END
IntentRouter → FinalOutput（问候/追问直接回答）
```

| 节点 | 职责 |
|------|------|
| `intent_router` | 三分类（greeting/simple_travel/full_travel）+ 参数提取 + 分级记忆加载 |
| `react_executor` | ReAct 循环，LLM 自主决策工具调用（简单出行） |
| `planner` | DeepSeek R1 生成 JSON 执行计划（旅游规划） |
| `step_executor` | 按计划逐步调用工具，容错执行 |
| `summarizer` | 汇总工具结果 + 用户偏好，生成最终回答 |
| `final_output` | 记忆晋升、状态收尾 |
```

- [ ] **Step 4: Final commit**

```bash
git add multi-agents/CLAUDE.md
git commit -m "docs: 更新架构文档，反映 6 节点双路径重构"
```

---
