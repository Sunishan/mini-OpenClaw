"""命令行入口。

用法：
  python -m agent.cli --selfcheck                     # Day1：自检骨架是否装好
  python -m agent.cli "创建 hello.py 并运行"             # Day5 起：真正跑任务（v1 在 Day6）
  python -m agent.cli --assess "https://example.com"   # 网页可信度评估
"""
from __future__ import annotations
import argparse
import sys

from tools.base import build_default_registry
from agent.prompts import SYSTEM_PROMPT


def selfcheck() -> int:
    print("== mini-OpenClaw 自检 ==")
    ok = True
    try:
        reg = build_default_registry()
        print(f"[ok] 工具注册表加载成功，当前内置工具数：{len(reg)}（Day5 起会变多）")
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
        print("[ok] 主循环模块可导入（Day5 实现 run 逻辑）")
    except Exception as e:  # noqa
        print(f"[FAIL] 主循环：{e}"); ok = False

    print("== 自检", "通过 ✅" if ok else "未通过 ❌", "==")
    print("\n下一步：按 dayNN 的 lab-guide 填 # TODO 标记。")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="mini-openclaw")
    p.add_argument("task", nargs="?", help="要让 agent 完成的任务（自然语言）")
    p.add_argument("--selfcheck", action="store_true", help="只做骨架自检")
    p.add_argument("--assess", type=str, metavar="URL", help="对指定 URL 进行网页可信度评估")
    args = p.parse_args(argv)

    if args.selfcheck or not (args.task or args.assess):
        return selfcheck()

    from agent.loop import AgentLoop
    reg = build_default_registry()

    from mcp.client import MCPClient, register_mcp_tools
    try:
        mcp = MCPClient(["python", "mcp/echo_server.py"])
        mcp.start()
        register_mcp_tools(reg, mcp)
    except Exception as e:  # noqa
        print(f"[提示] MCP 未接入（{e}），仅用内置工具。")

    if args.assess:
        # ── 加载可信度评估工具 ────────────────
        from tools.credibility import build_credibility_registry
        cred_reg = build_credibility_registry()
        for name in cred_reg.names():
            tool = cred_reg.get(name)
            if tool:
                reg.register(tool)
        # 使用可信度评估专用系统提示词
        from agent.prompts import CREDIBILITY_SYSTEM_PROMPT
        system_prompt = CREDIBILITY_SYSTEM_PROMPT
        user_task = f"请对网页进行完整的可信度评估：{args.assess}"
    else:
        system_prompt = SYSTEM_PROMPT
        user_task = args.task

    # 真正跑任务：优先用 DeepSeek API；没配 key 时回退到 FakeBackend（离线打通管道）
    try:
        from backend.client import DeepSeekBackend
        backend = DeepSeekBackend()                       # 需要 DEEPSEEK_API_KEY
    except Exception as e:  # noqa
        from backend.fake_backend import FakeBackend
        print(f"[提示] 未启用真后端（{e}），回退 FakeBackend。配置 DEEPSEEK_API_KEY 后即用真模型。")
        backend = FakeBackend()
    agent = AgentLoop(backend, reg, system_prompt)
    print(agent.run(user_task))
    return 0


if __name__ == "__main__":
    sys.exit(main())
