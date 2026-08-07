"""
StepExecutor 节点 — 按计划逐步执行工具调用（容错处理）
读取 executor_context 中的 plan_steps，按序执行工具，收集结果
"""
from typing import Dict, Any, List
from langchain_core.tools import BaseTool

from graph.state import GlobalState
from tools.tool_provider import get_tool_provider


async def step_executor_node(state: GlobalState) -> Dict[str, Any]:
    """
    StepExecutor 节点函数 — 按 Planner 生成的计划逐步执行工具

    流程：
    1. 从 state["executor_context"] 读取 plan_steps, tool_results, rag_results_history, collected_info
    2. 获取 tool_provider.get_tool_map()
    3. 遍历 plan_steps，对每个 step：
       - 从 tool_map 查找 tool，找不到记录失败并 continue
       - await tool.ainvoke(params) 执行，try/except 包裹
       - 成功：追加到 tool_results（含 success=True），如果是 rag_search 追加到 rag_results_history，存入 collected_info
       - 失败：追加到 tool_results（含 success=False），continue 不中断
    4. 更新 executor_context 并返回 {"executor_context": ..., "current_agent": "step_executor"}

    Args:
        state: LangGraph GlobalState

    Returns:
        Dict 更新，写入 executor_context, current_agent
    """
    print(f"\n{'='*60}")
    print("▶️ StepExecutor 开始逐步执行计划")
    print(f"{'='*60}")

    # 读取 executor_context
    executor_context = state.get("executor_context") or {
        "tool_results": [],
        "rag_results_history": [],
        "collected_info": {},
        "plan_steps": []
    }

    plan_steps: List[Dict[str, Any]] = executor_context.get("plan_steps") or []
    tool_results: List[Dict[str, Any]] = executor_context.get("tool_results") or []
    rag_results_history: List[str] = executor_context.get("rag_results_history") or []
    collected_info: Dict[str, Any] = executor_context.get("collected_info") or {}

    print(f"📋 计划步骤数: {len(plan_steps)}")
    print(f"📊 当前 tool_results 数: {len(tool_results)}")

    # 获取工具提供者
    tool_provider = await get_tool_provider()
    tool_map: Dict[str, BaseTool] = tool_provider.get_tool_map()

    print(f"🔧 可用工具数: {len(tool_map)}")
    print(f"   工具列表: {', '.join(tool_map.keys())}")

    # 跟踪失败和成功的步骤
    failed_steps: List[Dict[str, Any]] = []
    successful_steps = 0

    # 遍历每个计划步骤
    for i, step in enumerate(plan_steps):
        tool_name = step.get("tool", "")
        params = step.get("params", {})
        description = step.get("description", "")

        print(f"\n{'='*60}")
        print(f"📋 执行计划步骤 {i+1}/{len(plan_steps)}: {tool_name}")
        print(f"{'='*60}")
        print(f"  描述: {description}")
        print(f"  参数: {params}")

        # 从 tool_map 查找工具
        tool: BaseTool = tool_map.get(tool_name)
        if tool is None:
            error_msg = f"未知工具: {tool_name}"
            print(f"⚠️ {error_msg}")
            failed_steps.append({
                "step": i + 1,
                "tool": tool_name,
                "error": error_msg,
                "description": description
            })
            tool_results.append({
                "tool": tool_name,
                "params": params,
                "result": error_msg,
                "description": description,
                "success": False
            })
            continue

        # 执行工具调用（try/except 包裹）
        try:
            print(f"🔨 执行工具...")
            observation = await tool.ainvoke(params)
            print(f"✅ 工具执行完成")

            # 成功：追加到 tool_results
            tool_results.append({
                "tool": tool_name,
                "params": params,
                "result": observation,
                "description": description,
                "success": True
            })

            # 如果是 rag_search，追加到 rag_results_history
            if tool_name == "rag_search":
                rag_results_history.append(str(observation))
                print(f"   RAG 结果已添加到历史记录")

            # 存入 collected_info
            collected_info[tool_name] = observation

            successful_steps += 1

        except Exception as step_error:
            print(f"⚠️ 工具执行失败: {step_error}")
            print(f"   继续执行后续步骤...")

            failed_steps.append({
                "step": i + 1,
                "tool": tool_name,
                "error": str(step_error),
                "description": description
            })

            tool_results.append({
                "tool": tool_name,
                "params": params,
                "result": f"工具执行失败: {str(step_error)}",
                "description": description,
                "success": False
            })

    # 总结执行结果
    print(f"\n{'='*60}")
    print(f"✅ StepExecutor 执行完成")
    print(f"  成功步骤: {successful_steps}/{len(plan_steps)}")
    if failed_steps:
        print(f"  失败步骤: {len(failed_steps)}/{len(plan_steps)}")
        for failed in failed_steps:
            print(f"    步骤 {failed['step']}: {failed['tool']} - {failed['error']}")
    print(f"  工具结果总数: {len(tool_results)}")
    print(f"  RAG 历史记录数: {len(rag_results_history)}")
    print(f"{'='*60}\n")

    # 更新 executor_context
    executor_context["tool_results"] = tool_results
    executor_context["rag_results_history"] = rag_results_history
    executor_context["collected_info"] = collected_info

    return {
        "executor_context": executor_context,
        "current_agent": "step_executor"
    }
