"""
统一工具提供者 - 基于 langchain-mcp-adapters
自动从 MCP 服务器获取工具并转换为 LangChain BaseTool

替代：mcp_tools.py (MCPToolManager) + tool_registry.py (ToolDefinition)
"""
import json
import os
import httpx
from typing import Dict, List, Optional
from langchain_core.tools import BaseTool, tool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.sessions import SSEConnection

from config.settings import MCP_CONFIG_PATH
from tools.rag_tool import query_travel_knowledge


# 绕过代理直连 ModelScope（避免 VPN 代理导致连接问题）
os.environ['NO_PROXY'] = os.environ.get('NO_PROXY', '') + ',modelscope.net,api-inference.modelscope.net'


def create_insecure_httpx_client(**kwargs) -> httpx.AsyncClient:
    """创建禁用 SSL 验证的 httpx 客户端（仅用于开发/测试）"""
    timeout = kwargs.pop("timeout", httpx.Timeout(60.0, connect=30.0))
    return httpx.AsyncClient(
        verify=False,
        timeout=timeout,
        limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
        **kwargs,
    )



class ToolProvider:
    """
    统一工具提供者
    通过 langchain-mcp-adapters 连接所有 MCP 服务器并自动生成工具
    """

    def __init__(self, config_path: Optional[str] = None):
        self.config_path = config_path or MCP_CONFIG_PATH
        self._client: Optional[MultiServerMCPClient] = None
        self._tools: List[BaseTool] = []
        self._tool_map: Dict[str, BaseTool] = {}
        self._initialized = False

    async def initialize(self):
        """连接所有 MCP 服务器并构建工具列表"""
        if self._initialized:
            return

        config = self._load_config()

        # 转换为 MultiServerMCPClient 需要的格式（使用 SSEConnection）
        server_config = {}
        for server in config.get("mcp_servers", []):
            name = server.get("name")
            url = server.get("url")
            if not name or not url:
                continue
            server_config[name] = SSEConnection(
                url=url,
                transport="sse",
                timeout=30.0,
                sse_read_timeout=300.0,
                httpx_client_factory=create_insecure_httpx_client,
            )

        if not server_config:
            print("[WARN] No MCP servers configured")
            self._initialized = True
            return

        self._client = MultiServerMCPClient(server_config, tool_name_prefix=True)

        # 逐个服务器加载工具，单个失败不影响其他
        all_tools: List[BaseTool] = []
        for name, connection in server_config.items():
            try:
                server_tools = await self._client.get_tools(server_name=name)
                all_tools.extend(server_tools)
                print(f"  [OK] {name}: {len(server_tools)} tools")
            except Exception as e:
                print(f"  [SKIP] {name}: {type(e).__name__}")

        if not all_tools and not any(
            server.get("required", False) for server in config.get("mcp_servers", [])
        ):
            print("[WARN] 所有 MCP 服务器都不可用，仅使用custom工具")

        # 所有 MCP 工具直接暴露，不进行过滤
        mcp_tools: List[BaseTool] = list(all_tools)

        # 构建自定义工具（目前只有 RAG）
        custom_tools = [
            self._create_rag_tool(),
        ]

        self._tools = custom_tools + mcp_tools
        self._tool_map = {t.name: t for t in self._tools}
        self._initialized = True

        print(
            f"[OK] ToolProvider 初始化完成: {len(self._tools)} tools "
            f"({len(custom_tools)} custom + {len(mcp_tools)} MCP)"
        )

    def get_tools(self) -> List[BaseTool]:
        """返回所有工具（MCP + custom）"""
        if not self._initialized:
            raise RuntimeError("ToolProvider 未初始化，请先调用 initialize()")
        return self._tools

    def get_tool_map(self) -> Dict[str, BaseTool]:
        """返回 name -> tool 的快速查找字典"""
        if not self._initialized:
            raise RuntimeError("ToolProvider 未初始化，请先调用 initialize()")
        return self._tool_map

    async def cleanup(self):
        """关闭所有 MCP 连接"""
        if self._client:
            self._client = None
            self._initialized = False
            self._tools = []
            self._tool_map = {}

    # ---- 私有辅助方法 ----

    def _load_config(self) -> Dict:
        with open(self.config_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _create_rag_tool(self) -> BaseTool:
        @tool
        async def rag_search(query: str = "", k: int = 3) -> str:
            """从知识库中检索旅游攻略和景点信息。
            当需要了解某个城市的旅游攻略、景点推荐、特色美食、最佳游玩时间等信息时使用。
            """
            return await query_travel_knowledge(query, k=k)

        rag_search.name = "rag_search"
        rag_search.description = (
            "从知识库中检索旅游攻略和景点信息。"
            "当需要了解某个城市的旅游攻略、景点推荐、特色美食、最佳游玩时间等信息时使用。"
            "参数: query(检索关键词), k(返回数量,默认3)"
        )
        return rag_search



# ---- 全局单例 ----

_tool_provider: Optional[ToolProvider] = None


async def get_tool_provider() -> ToolProvider:
    """获取全局 ToolProvider 单例"""
    global _tool_provider
    if _tool_provider is None:
        _tool_provider = ToolProvider()
        await _tool_provider.initialize()
    return _tool_provider
