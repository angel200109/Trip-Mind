import asyncio
import json
import os
import unittest

from agents.mcp import MCPServerSse


def _load_12306_server_config():
    with open("config/servers_config.json", "r", encoding="utf-8") as file:
        config = json.load(file)

    for server in config.get("mcp_servers", []):
        if server.get("name") == "12306 Server":
            return server

    raise AssertionError("12306 Server is not configured")


def _connect_12306_server():
    server_config = _load_12306_server_config()
    url = server_config.get("url")
    if not url:
        raise AssertionError("12306 Server URL is empty")

    os.environ["NO_PROXY"] = (
        os.environ.get("NO_PROXY", "") + ",modelscope.net,api-inference.modelscope.net"
    )


    return MCPServerSse(name="12306 Server", params={"url": url})


async def _call_12306_current_date():
    async with _connect_12306_server() as server:
        tools = await server.list_tools()
        tool_names = {getattr(tool, "name", str(tool)) for tool in tools}

        if "get-current-date" not in tool_names:
            raise AssertionError(f"get-current-date not found; available tools: {sorted(tool_names)}")

        result = await server.call_tool("get-current-date", arguments={})
        if not getattr(result, "content", None):
            raise AssertionError(f"get-current-date returned empty result: {result}")

        return result.content[0].text


async def _call_12306_station_codes(citys: str):
    async with _connect_12306_server() as server:
        tools = await server.list_tools()
        tool_names = {getattr(tool, "name", str(tool)) for tool in tools}

        if "get-station-code-of-citys" not in tool_names:
            raise AssertionError(
                f"get-station-code-of-citys not found; available tools: {sorted(tool_names)}"
            )

        result = await server.call_tool(
            "get-station-code-of-citys",
            arguments={"citys": citys},
        )
        if not getattr(result, "content", None):
            raise AssertionError(f"get-station-code-of-citys returned empty result: {result}")

        return result.content[0].text


@unittest.skipUnless(
    os.getenv("RUN_12306_MCP_TEST") == "1",
    "Set RUN_12306_MCP_TEST=1 to run the external 12306 MCP integration test.",
)
class Test12306MCPIntegration(unittest.TestCase):
    def test_12306_mcp_can_call_current_date(self):
        result_text = asyncio.run(_call_12306_current_date())

        print(f"\n12306 get-current-date result: {result_text}")
        self.assertTrue(result_text.strip())

    def test_12306_mcp_can_get_station_codes(self):
        result_text = asyncio.run(_call_12306_station_codes("上海|苏州"))

        print(f"\n12306 get-station-code-of-citys result: {result_text}")
        self.assertTrue(result_text.strip())
        self.assertIn("上海", result_text)
        self.assertIn("苏州", result_text)
        self.assertNotIn("error", result_text.lower())


if __name__ == "__main__":
    unittest.main()
