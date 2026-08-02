"""测试 memory/working.py 工作记忆"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory.working import WorkingMemory


def test_update_and_get_context():
    """update 后 get_context 应返回写入的数据"""
    mem = WorkingMemory()
    mem.update("s1", {"destination": "Beijing", "budget": 5000})
    ctx = mem.get_context("s1")
    assert ctx["destination"] == "Beijing"
    assert ctx["budget"] == 5000


def test_context_merges():
    """多次 update 应合并，后写覆盖同名 key"""
    mem = WorkingMemory()
    mem.update("s1", {"destination": "Beijing", "budget": 5000})
    mem.update("s1", {"budget": 8000, "days": 5})
    ctx = mem.get_context("s1")
    assert ctx["destination"] == "Beijing"  # 保留旧 key
    assert ctx["budget"] == 8000            # 后写覆盖
    assert ctx["days"] == 5                 # 新 key


def test_get_history():
    """history 条目应包含 timestamp 和 data，last_n 应限制数量"""
    mem = WorkingMemory()
    for i in range(5):
        mem.update("s1", {"step": i})

    # 默认 last_n=10，返回全部 5 条
    history = mem.get_history("s1")
    assert len(history) == 5
    for entry in history:
        assert "timestamp" in entry
        assert "data" in entry

    # last_n=2，只返回最近 2 条
    recent = mem.get_history("s1", last_n=2)
    assert len(recent) == 2
    assert recent[-1]["data"]["step"] == 4
    assert recent[0]["data"]["step"] == 3


def test_max_entries_trim():
    """超过 max_entries_per_session 时，最旧的条目应被裁剪"""
    mem = WorkingMemory(max_entries_per_session=3)
    for i in range(6):
        mem.update("s1", {"step": i})

    history = mem.get_history("s1", last_n=100)
    assert len(history) == 3
    # 保留的是最新的 3 条: step=3, 4, 5
    steps = [e["data"]["step"] for e in history]
    assert steps == [3, 4, 5]


def test_session_isolation():
    """两个 session 之间的数据不应相互影响"""
    mem = WorkingMemory()
    mem.update("session_a", {"key": "value_a"})
    mem.update("session_b", {"key": "value_b"})

    ctx_a = mem.get_context("session_a")
    ctx_b = mem.get_context("session_b")

    assert ctx_a["key"] == "value_a"
    assert ctx_b["key"] == "value_b"

    hist_a = mem.get_history("session_a")
    hist_b = mem.get_history("session_b")
    assert len(hist_a) == 1
    assert len(hist_b) == 1


def test_clear():
    """clear 后，context 和 history 应均为空"""
    mem = WorkingMemory()
    mem.update("s1", {"destination": "Shanghai"})
    mem.clear("s1")

    assert mem.get_context("s1") == {}
    assert mem.get_history("s1") == []


def test_export_for_persistence():
    """export_for_persistence 应返回正确结构"""
    mem = WorkingMemory()
    mem.update("s1", {"destination": "Chengdu"})
    mem.update("s1", {"days": 3})

    result = mem.export_for_persistence("s1")

    assert result["session_id"] == "s1"
    assert result["context"]["destination"] == "Chengdu"
    assert result["context"]["days"] == 3
    assert len(result["history"]) == 2
    assert "exported_at" in result

    # 验证导出的是副本，修改不影响原始数据
    result["context"]["tamper"] = True
    assert "tamper" not in mem.get_context("s1")
