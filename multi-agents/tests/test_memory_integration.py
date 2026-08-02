"""
记忆系统集成测试
验证记忆管理器单例、组件实例化、GlobalState 字段扩展
"""
import pytest
from unittest.mock import patch, AsyncMock, MagicMock


# ---------------------------------------------------------------------------
# Test 1: MemoryManager singleton
# ---------------------------------------------------------------------------

def test_memory_manager_singleton():
    """get_memory_manager() 每次返回同一个实例"""
    # Reset the singleton before the test to ensure clean state
    import memory.manager as manager_module
    original_instance = manager_module._instance
    manager_module._instance = None  # reset

    try:
        from memory import get_memory_manager
        instance_a = get_memory_manager()
        instance_b = get_memory_manager()
        assert instance_a is instance_b, "get_memory_manager() should return the same singleton instance"
    finally:
        # Restore original instance state
        manager_module._instance = original_instance


# ---------------------------------------------------------------------------
# Test 2: MemoryManager has all components
# ---------------------------------------------------------------------------

def test_memory_manager_has_all_components():
    """MemoryManager 实例化后包含所有 5 个子组件"""
    import memory.manager as manager_module
    original_instance = manager_module._instance
    manager_module._instance = None  # reset to force new instantiation

    try:
        from memory import get_memory_manager
        from memory import WorkingMemory, ShortTermMemory, LongTermMemory, MemoryRouter, MemoryPromotion

        mgr = get_memory_manager()
        assert hasattr(mgr, "working"), "MemoryManager should have 'working' attribute"
        assert hasattr(mgr, "short_term"), "MemoryManager should have 'short_term' attribute"
        assert hasattr(mgr, "long_term"), "MemoryManager should have 'long_term' attribute"
        assert hasattr(mgr, "router"), "MemoryManager should have 'router' attribute"
        assert hasattr(mgr, "promotion"), "MemoryManager should have 'promotion' attribute"

        assert isinstance(mgr.working, WorkingMemory)
        assert isinstance(mgr.short_term, ShortTermMemory)
        assert isinstance(mgr.long_term, LongTermMemory)
        assert isinstance(mgr.router, MemoryRouter)
        assert isinstance(mgr.promotion, MemoryPromotion)
    finally:
        manager_module._instance = original_instance


# ---------------------------------------------------------------------------
# Test 3: GlobalState accepts memory fields
# ---------------------------------------------------------------------------

def test_global_state_has_memory_fields():
    """GlobalState TypedDict 接受 memory_context, session_id, user_id 字段"""
    from graph.state import GlobalState
    from langchain_core.messages import HumanMessage

    # Build a valid GlobalState dict including the new memory fields
    state: GlobalState = {
        "messages": [HumanMessage(content="test")],
        "user_query": "去北京旅游",
        "planner_context": None,
        "executor_context": None,
        "summarizer_context": None,
        "current_agent": None,
        "next_agent": None,
        "is_complete": False,
        "final_answer": None,
        "needs_replan": None,
        "feedback_type": None,
        "confirmation_message": None,
        "preference_updates": None,
        # new fields
        "session_id": "sess-abc-123",
        "user_id": "user-xyz",
        "memory_context": {"working": {}, "short_term": [], "preferences": {}},
    }

    assert state["session_id"] == "sess-abc-123"
    assert state["user_id"] == "user-xyz"
    assert state["memory_context"] is not None
    assert "working" in state["memory_context"]


# ---------------------------------------------------------------------------
# Test 4: memory fields are present in GlobalState __annotations__
# ---------------------------------------------------------------------------

def test_global_state_annotations_include_memory_fields():
    """GlobalState 的 __annotations__ 包含 memory_context, session_id, user_id"""
    from graph.state import GlobalState

    annotations = GlobalState.__annotations__
    assert "memory_context" in annotations, "GlobalState must declare memory_context field"
    assert "session_id" in annotations, "GlobalState must declare session_id field"
    assert "user_id" in annotations, "GlobalState must declare user_id field"


# ---------------------------------------------------------------------------
# Test 5: MemoryManager and get_memory_manager exported from memory package
# ---------------------------------------------------------------------------

def test_memory_package_exports():
    """memory.__init__ 导出 MemoryManager 和 get_memory_manager"""
    import memory
    assert hasattr(memory, "MemoryManager"), "memory package must export MemoryManager"
    assert hasattr(memory, "get_memory_manager"), "memory package must export get_memory_manager"
    assert callable(memory.get_memory_manager)
