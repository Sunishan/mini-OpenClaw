"""最小 MCP 客户端（Day8）。

MCP（Model Context Protocol）让工具集从"写死在代码里"变成"可插拔的外部 server"。
本文件实现一个最小客户端：通过 stdio 跟 server 通信，做 JSON-RPC。

要实现的握手与调用：
  1. 启动 server 子进程（stdio transport）
  2. initialize 握手
  3. tools/list  —— 拉取 server 暴露的工具
  4. tools/call  —— 把某次调用转发给 server，拿回结果
然后在 agent/loop 里，把这些 MCP 工具**透明合并**进内置 ToolRegistry。
"""
from __future__ import annotations
import json
import os
import subprocess
from typing import Any

from tools.base import Tool, ToolRegistry


class MCPClient:
    def __init__(self, command: list[str], env: dict[str, str] | None = None):
        self.command = command
        self.env = env or {}
        self.proc: subprocess.Popen | None = None
        self._id = 0

    def start(self) -> None:
        # TODO[Day8] 启动子进程，stdin/stdout 接管，做 initialize 握手
        env = os.environ.copy()
        env.update(self.env)
        self.proc = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            text=True, bufsize=1,          # 行缓冲，配合一行一条消息
            env=env,
        )
        self._rpc("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "mini-openclaw", "version": "0.1"},
        })
        self._notify("notifications/initialized")   # 通知，无需等 result

    def _rpc(self, method: str, params: dict | None = None) -> Any:
        # TODO[Day8] 发一条 JSON-RPC 请求（带自增 id），读回对应响应
        if self.proc is None or self.proc.stdin is None or self.proc.stdout is None:
            raise RuntimeError("MCP server 尚未启动")
        self._id += 1
        request_id = self._id
        req = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
        self.proc.stdin.write(json.dumps(req) + "\n")
        self.proc.stdin.flush()
        while True:
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError(f"MCP server 已退出，等待 {method} 响应失败")
            try:
                resp = json.loads(line)
            except json.JSONDecodeError as e:
                raise RuntimeError(f"MCP server 返回了非 JSON 内容：{line[:200]}") from e

            # 有些 server 会在 stdout 上发送 notifications/resources/updated 等通知。
            # 这些消息没有本次请求 id，不能当作 RPC 响应处理。
            if resp.get("id") != request_id:
                continue
            if "error" in resp:
                raise RuntimeError(resp["error"])
            if "result" not in resp:
                raise RuntimeError(f"MCP server 响应缺少 result：{resp}")
            return resp["result"]
    
    def _notify(self, method: str, params: dict | None = None) -> None:
        req = {"jsonrpc": "2.0", "method": method, "params": params or {}}  # 无 id
        self.proc.stdin.write(json.dumps(req) + "\n")
        self.proc.stdin.flush()

    def list_tools(self) -> list[dict]:
        # TODO[Day8] 调 tools/list，返回工具描述列表
        return self._rpc("tools/list")["tools"]

    def call_tool(self, name: str, arguments: dict) -> str:
        # TODO[Day8] 调 tools/call，返回结果文本
        result = self._rpc("tools/call", {"name": name, "arguments": arguments})
        parts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
        return "\n".join(parts)


def register_mcp_tools(registry: ToolRegistry, client: MCPClient) -> None:
    """把一个 MCP server 的工具包装成内置 Tool 并注册，实现透明合并。"""
    for spec in client.list_tools():
        name = spec["name"]
        description = spec.get("description", "")
        if name.startswith("firecrawl_") and name not in {"firecrawl_search", "firecrawl_search_feedback"}:
            description += (
                "\n注意：Firecrawl 免费额度有限；仅在低成本抓取失败或确需目标页正文时使用，"
                "避免抓取重复页面、转载页或普通证据页。"
            )
        registry.register(Tool(
            name=f"mcp__{name}",            # 命名空间避免和内置工具撞名
            description=description,
            parameters=spec.get("inputSchema", {"type": "object", "properties": {}}),
            run=lambda _n=name, **kw: client.call_tool(_n, kw),
        ))
