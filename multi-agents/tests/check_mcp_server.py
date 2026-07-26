"""
MCP 服务器连接检测脚本
用于测试所有 MCP 服务器是否可达，并列出各服务器的工具

用法：
    python tests/check_mcp_server.py               # 完整检测
    python tests/check_mcp_server.py --quick        # 只检测 DNS
"""
import asyncio
import json
import os
import socket
import sys
import time
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def check_dns(name: str, url: str) -> dict:
    """检测 DNS 解析"""
    result = {"name": name, "dns": "?", "ip": "", "dns_time": 0}
    try:
        host = url.split("://")[1].split("/")[0]
        start = time.time()
        ip = socket.gethostbyname(host)
        elapsed = time.time() - start
        result["dns"] = "OK"
        result["ip"] = ip
        result["dns_time"] = round(elapsed, 2)
    except Exception as e:
        result["dns"] = f"FAIL ({e})"
    return result


async def check_mcp_server(name: str, url: str, timeout_sec: float = 20.0) -> dict:
    """
    通过 langchain-mcp-adapters 连接 MCP 服务器并获取工具列表
    使用 asyncio.wait_for 避免单个服务器挂起
    """
    import httpx
    from langchain_mcp_adapters.client import MultiServerMCPClient
    from langchain_mcp_adapters.sessions import SSEConnection

    result = {
        "name": name,
        "status": "?",
        "tool_count": 0,
        "tools": [],
        "error": "",
        "time": 0,
    }

    def _make_client(**kw) -> httpx.AsyncClient:
        timeout = kw.pop("timeout", httpx.Timeout(30.0, connect=15.0))
        return httpx.AsyncClient(verify=False, timeout=timeout, **kw)

    start = time.time()
    try:
        client = MultiServerMCPClient(
            {
                name: SSEConnection(
                    url=url,
                    transport="sse",
                    timeout=min(timeout_sec, 15.0),
                    sse_read_timeout=timeout_sec,
                    httpx_client_factory=_make_client,
                )
            },
            tool_name_prefix=False,
        )

        tools = await asyncio.wait_for(
            client.get_tools(server_name=name), timeout=timeout_sec
        )
        result["status"] = "OK"
        result["tool_count"] = len(tools)
        result["tools"] = sorted([t.name for t in tools])
    except asyncio.TimeoutError:
        result["status"] = "TIMEOUT"
        result["error"] = f"超过 {timeout_sec}s 未响应"
    except Exception as e:
        result["status"] = "FAIL"
        msg = str(e)
        result["error"] = f"{type(e).__name__}: {msg[:200]}"

    result["time"] = round(time.time() - start, 1)
    return result


def print_report(
    dns_results: list[dict],
    mcp_results: list[dict] | None = None,
):
    """打印检测报告"""
    print()
    print("=" * 68)
    print("  MCP 服务器连接检测报告")
    print("=" * 68)
    print()

    for dr in dns_results:
        dns_icon = "OK" if dr["dns"] == "OK" else "FAIL"
        print(f"  [{dns_icon}] {dr['name']}")
        print(f"         URL: {dr.get('_url', '')}")
        if dr["dns"] == "OK":
            print(f"        DNS: {dr['ip']} ({dr['dns_time']}s)")
        else:
            print(f"        DNS: {dr['dns']}")

    if mcp_results:
        print()
        print("-" * 68)
        print("  MCP 工具加载结果（完整模式）")
        print("-" * 68)
        for mr in mcp_results:
            icon = "OK" if mr["status"] == "OK" else ("TIMEOUT" if mr["status"] == "TIMEOUT" else "FAIL")
            print(f"\n  [{icon}] {mr['name']} ({mr['time']}s)")
            if mr["status"] == "OK":
                print(f"       Tools ({mr['tool_count']}):")
                for t in mr["tools"]:
                    print(f"         - {t}")
            else:
                print(f"       Error: {mr['error']}")

    # 汇总
    ok_count = sum(1 for dr in dns_results if dr["dns"] == "OK")
    print()
    print("-" * 68)
    print(f"  总计: {len(dns_results)} 服务器, {ok_count} DNS 可达")
    if mcp_results:
        mcp_ok = sum(1 for mr in mcp_results if mr["status"] == "OK")
        print(f"  MCP 工具加载: {mcp_ok}/{len(mcp_results)} 成功")
    print("=" * 68)
    print()


async def main():
    parser = argparse.ArgumentParser(description="MCP 服务器连接检测工具")
    parser.add_argument(
        "--quick",
        action="store_true",
        help="只检测 DNS，不实际连接 MCP",
    )
    parser.add_argument(
        "--server",
        type=str,
        default=None,
        help="只检测指定服务器（name 关键词匹配）",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=20.0,
        help="单服务器超时秒数（默认 20s）",
    )
    args = parser.parse_args()

    from config.settings import MCP_CONFIG_PATH

    with open(MCP_CONFIG_PATH, encoding="utf-8") as f:
        config = json.load(f)

    servers = config.get("mcp_servers", [])
    if args.server:
        servers = [s for s in servers if args.server.lower() in s["name"].lower()]
        if not servers:
            print(f"未找到匹配 '{args.server}' 的服务器")
            sys.exit(1)

    print(f"\n  共 {len(servers)} 个 MCP 服务器")

    # Step 1: DNS 检测
    print(f"\n  --- DNS 检测 ---")
    dns_tasks = []
    for s in servers:
        dns_tasks.append(check_dns(s["name"], s["url"]))
    dns_results = await asyncio.gather(*dns_tasks)
    for i, s in enumerate(servers):
        dns_results[i]["_url"] = s["url"]

    if args.quick:
        print_report(dns_results)
        return

    # Step 2: 完整 MCP 连接检测
    print(f"\n  --- MCP 工具加载（每服务器 {args.timeout}s 超时） ---")
    mcp_results = []
    for s in servers:
        result = await check_mcp_server(s["name"], s["url"], args.timeout)
        mcp_results.append(result)
        icon = "OK" if result["status"] == "OK" else "SKIP"
        print(f"  [{icon}] {s['name']}: {result['status']} ({result['time']}s, {result['tool_count']} tools)")

    print_report(dns_results, mcp_results)


if __name__ == "__main__":
    asyncio.run(main())
