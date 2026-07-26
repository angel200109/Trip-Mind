"""Helpers for turning provider exceptions into actionable UI messages."""


def get_user_facing_llm_error(error: Exception) -> str:
    """Return a concise message for common LLM provider failures."""
    error_text = str(error)

    if "Arrearage" in error_text:
        return (
            "DashScope 调用失败：当前阿里云百炼/Model Studio 账号处于欠费或不可用状态。"
            "请检查账号余额、服务开通状态，以及 `DASHSCOPE_API_KEY` 是否指向可用账号。"
        )

    if "invalid_api_key" in error_text.lower() or "incorrect api key" in error_text.lower():
        return "LLM 调用失败：API Key 无效。请检查环境变量中的模型服务密钥配置。"

    return f"LLM 调用失败：{error_text}"
