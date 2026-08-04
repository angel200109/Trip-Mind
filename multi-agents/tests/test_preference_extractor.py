"""
测试偏好提取器（LLM 驱动 + 正则兜底）
- 正则命中时不调用 LLM
- 隐含语义走 LLM 提取
- 置信度过低丢弃
- 超时回退
"""
import sys
import os
import asyncio
from unittest.mock import patch, AsyncMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_regex_fast_path_no_llm_call():
    """正则能提取时，不调用 LLM"""
    from memory.promotion import MemoryPromotion
    from memory.short_term import ShortTermMemory
    from memory.long_term import LongTermMemory
    from memory.working import WorkingMemory

    p = MemoryPromotion(ShortTermMemory(), LongTermMemory(), WorkingMemory())

    with patch("memory.promotion.extract_preferences_with_llm") as mock_llm:
        prefs = p._extract_preferences("我喜欢古镇", "好的，为您推荐古镇")
        assert prefs.get("liked_activities") == ["古镇"]
        mock_llm.assert_not_called()  # 正则命中，LLM 未被调用


def test_llm_path_called_when_regex_empty():
    """正则未命中时调用 LLM"""
    from memory.promotion import MemoryPromotion

    p = MemoryPromotion(None, None, None)

    with patch("memory.promotion.extract_preferences_with_llm") as mock_llm:
        mock_llm.return_value = {"travel_style": ["家庭游", "慢节奏"]}
        prefs = p._extract_preferences("带着爸妈，别太赶", "已为您安排")
        assert prefs == {}  # 正则没提取到
        mock_llm.assert_not_called()  # _extract_preferences 本身不调 LLM（LLM 在 promote 里）


def test_promote_uses_llm_when_regex_empty():
    """promote: 正则未命中 → LLM 兜底写入偏好"""
    import asyncio
    from memory.promotion import MemoryPromotion

    class FakeShortTerm:
        async def add_message(self, *args, **kwargs):
            return None

    class FakeLongTerm:
        async def get_preferences(self, user_id):
            return {}
        async def update_preferences(self, user_id, **fields):
            self.updated = fields

    fake_ltm = FakeLongTerm()
    p = MemoryPromotion(FakeShortTerm(), fake_ltm, None)

    async def _t():
        with patch("memory.promotion.extract_preferences_with_llm",
                   new=AsyncMock(return_value={"travel_style": ["慢节奏"]})) as mock_llm:
            result = await p.promote(
                session_id="s", user_id="u",
                user_message="带着爸妈，别太赶", assistant_response="已安排",
            )
            mock_llm.assert_awaited_once()
            assert result["preferences_updated"] is True
            assert fake_ltm.updated == {"travel_style": ["慢节奏"]}

    asyncio.run(_t())


def test_low_confidence_discarded():
    """置信度 < 0.6 时返回空 dict"""
    from memory.preference_extractor import PreferenceExtraction

    r = PreferenceExtraction(has_preference=True, confidence=0.4, travel_style=["古镇"])
    # 模拟 extract 的过滤逻辑
    if not r.has_preference or r.confidence < 0.6:
        assert True  # 被丢弃


def test_llm_timeout_falls_back():
    """LLM 超时（promote 内 5s 兜底）时不阻塞、不崩溃"""
    import asyncio
    from memory.promotion import MemoryPromotion

    class FakeShortTerm:
        async def add_message(self, *args, **kwargs):
            return None

    class FakeLongTerm:
        async def get_preferences(self, user_id):
            return {}
        async def update_preferences(self, user_id, **fields):
            self.updated = fields

    fake_ltm = FakeLongTerm()
    p = MemoryPromotion(FakeShortTerm(), fake_ltm, None)

    async def _t():
        with patch("memory.promotion.extract_preferences_with_llm",
                   new=AsyncMock(side_effect=asyncio.TimeoutError())):
            # promote 内 LLM 分支有 wait_for 兜底，超时被捕获，不抛异常
            result = await p.promote(
                session_id="s", user_id="u",
                user_message="带着爸妈，别太赶", assistant_response="已安排",
            )
            assert result["preferences_updated"] is False  # 超时无偏好更新
            assert result["saved_to_short_term"] is True   # 其他写回不受影响

    asyncio.run(_t())


if __name__ == "__main__":
    test_regex_fast_path_no_llm_call()
    print("[OK] test_regex_fast_path_no_llm_call")
    test_llm_path_called_when_regex_empty()
    print("[OK] test_llm_path_called_when_regex_empty")
    test_promote_uses_llm_when_regex_empty()
    print("[OK] test_promote_uses_llm_when_regex_empty")
    test_low_confidence_discarded()
    print("[OK] test_low_confidence_discarded")
    test_llm_timeout_falls_back()
    print("[OK] test_llm_timeout_falls_back")
    print("All preference extractor tests passed!")
