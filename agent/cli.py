"""命令行入口。

用法：
  python -m agent.cli --selfcheck                       # 自检骨架
  python -m agent.cli "创建 hello.py 并运行"              # 单次任务
  python -m agent.cli -i                                 # 交互模式（多轮对话）
"""
from __future__ import annotations
import argparse
import os
import sys

from datetime import datetime

from tools.base import build_default_registry
from agent.prompts import SYSTEM_PROMPT


def selfcheck() -> int:
    print("== mini-OpenClaw 自检 ==")
    ok = True
    try:
        reg = build_default_registry()
        print(f"[ok] 工具注册表加载成功，当前内置工具数：{len(reg)}")
    except Exception as e:  # noqa
        print(f"[FAIL] 工具注册表：{e}"); ok = False

    try:
        from backend.fake_backend import FakeBackend
        FakeBackend().chat([{"role": "user", "content": "hi"}], tools=[])
        print("[ok] FakeBackend 可用（未配 DEEPSEEK_API_KEY 时的离线占位后端）")
    except Exception as e:  # noqa
        print(f"[FAIL] FakeBackend：{e}"); ok = False

    try:
        from agent.loop import AgentLoop  # noqa
        print("[ok] 主循环模块可导入")
    except Exception as e:  # noqa
        print(f"[FAIL] 主循环：{e}"); ok = False

    print("== 自检", "通过" if ok else "未通过", "==")
    return 0 if ok else 1


def _init_agent():
    """初始化 backend 和 AgentLoop。"""
    from agent.loop import AgentLoop
    reg = build_default_registry()

    from mcp.client import MCPClient, register_mcp_tools
    try:
        mcp = MCPClient(["python", "mcp/echo_server.py"])
        mcp.start()
        register_mcp_tools(reg, mcp)
    except Exception as e:  # noqa
        print(f"[提示] MCP 未接入（{e}），仅用内置工具。")

    try:
        pw_mcp = MCPClient([
            "npx", "-y", "@playwright/mcp@latest",
            "--headless",
            "--isolated",
            "--ignore-https-errors",
        ])
        pw_mcp.start()
        register_mcp_tools(reg, pw_mcp)
        print("[ok] Playwright MCP 已接入。")
    except Exception as e:  # noqa
        print(f"[提示] Playwright MCP 未接入（{e}），继续使用其它工具。")

    firecrawl_key = os.environ.get("FIRECRAWL_API_KEY")
    if firecrawl_key:
        try:
            firecrawl_mcp = MCPClient(
                ["npx", "-y", "firecrawl-mcp"],
                env={"FIRECRAWL_API_KEY": firecrawl_key},
            )
            firecrawl_mcp.start()
            register_mcp_tools(reg, firecrawl_mcp)
            print("[ok] Firecrawl MCP 已接入。")
        except Exception as e:  # noqa
            print(f"[提示] Firecrawl MCP 未接入（{e}），继续使用其它工具。")
    else:
        print("[提示] 未设置 FIRECRAWL_API_KEY，跳过 Firecrawl MCP。")

    # 真正跑任务：优先用 DeepSeek API；没配 key 时回退到 FakeBackend（离线打通管道）
    try:
        from backend.client import DeepSeekBackend
        backend = DeepSeekBackend()
    except Exception as e:  # noqa
        from backend.fake_backend import FakeBackend
        print(f"[提示] 未启用真后端（{e}），回退 FakeBackend。")
        backend = FakeBackend()
    system_prompt = SYSTEM_PROMPT.format(
        current_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        current_location="浙江省 杭州市",
    )

    return AgentLoop(backend, reg, system_prompt)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="mini-openclaw")
    p.add_argument("task", nargs="?", help="要让 agent 完成的任务（自然语言）")
    p.add_argument("--selfcheck", action="store_true", help="只做骨架自检")
    p.add_argument("-i", "--interactive", action="store_true",
                   help="交互模式：多轮对话，history 保持不变")
    args = p.parse_args(argv)

    if args.selfcheck:
        return selfcheck()

    agent = _init_agent()

    if args.interactive:
        # ── 交互模式 ────────────────────────────────────────
        print("[交互] mini-OpenClaw 交互模式（输入 exit/quit 退出，new 清空历史）")
        print("─" * 50)
        while True:
            try:
                user_input = input(">>> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break

            if not user_input:
                continue
            if user_input.lower() in ("exit", "quit"):
                break
            if user_input.lower() == "new":
                agent.reset()
                print("[重置] 历史已清空")
                continue

            print(agent.chat(user_input))
            print("─" * 50)

        return 0

    # ── 单次任务模式 ────────────────────────────────────────
    if not args.task:
        return selfcheck()

    print(agent.run(args.task))
    return 0


if __name__ == "__main__":
    sys.exit(main())
