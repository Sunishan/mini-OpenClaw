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

from agent.prompts import SYSTEM_PROMPT
from tools.base import build_default_registry

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
except ImportError:  # pragma: no cover - only used before optional UI dependency is installed
    Console = None  # type: ignore[assignment]
    Panel = None  # type: ignore[assignment]
    Table = None  # type: ignore[assignment]


RICH_AVAILABLE = Console is not None
console = Console() if RICH_AVAILABLE else None


def _print(message: object = "") -> None:
    if console is not None:
        console.print(message)
    else:
        print(message)


def _notice(message: str) -> None:
    if console is not None:
        console.print(f"[dim]{message}[/dim]")
    else:
        print(message)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError:
        return default


def welcome() -> None:
    """显示交互模式欢迎页。"""
    cat = r"""
        /\_____/\
       /  o   o  \
      ( ==  ^  == )
       )         (
      (           )
     ( (  )   (  ) )
    (__(__)___(__)__)
     |           |
     |  -------  |
     | |       | |
     | |  mini | |
     |_| OpenClaw|_|
"""
    if console is not None and Panel is not None:
        console.print(f"[bold magenta]{cat}[/bold magenta]")
        console.print(Panel(
            "[bold cyan]mini-OpenClaw 交互模式[/bold cyan]\n"
            "[dim]直接输入问题或任务；输入 new 清空历史，exit 退出。[/dim]",
            border_style="magenta",
            padding=(1, 1),
            expand=False,
        ))
        console.print("  [green]▸[/green] [yellow]判断这个网页的可信度 https://example.com[/yellow]")
        console.print("  [green]▸[/green] [yellow]提取这篇文章里的核心事实主张[/yellow]")
        console.print("  [green]▸[/green] [yellow]继续解释上一轮证据中最关键的矛盾点[/yellow]")
        console.print()
        return

    print(cat)
    print("mini-OpenClaw 交互模式")
    print("直接输入问题或任务；输入 new 清空历史，exit 退出。")
    print("示例：判断这个网页的可信度 https://example.com")
    print("示例：提取这篇文章里的核心事实主张")
    print("示例：继续解释上一轮证据中最关键的矛盾点")
    print()


def selfcheck() -> int:
    """骨架自检：验证核心模块能否正常导入和运行。"""
    if console is not None and Panel is not None:
        console.print(Panel("[bold]mini-OpenClaw 自检[/bold]", border_style="blue"))
    else:
        print("== mini-OpenClaw 自检 ==")

    ok = True
    checks: list[tuple[str, str, str]] = []

    try:
        reg = build_default_registry()
        checks.append(("工具注册表", "ok", f"加载成功，当前内置工具数：{len(reg)}"))
    except Exception as e:  # noqa: BLE001
        checks.append(("工具注册表", "fail", str(e)))
        ok = False

    try:
        from backend.fake_backend import FakeBackend

        FakeBackend().chat([{"role": "user", "content": "hi"}], tools=[])
        checks.append(("FakeBackend", "ok", "离线占位后端可用"))
    except Exception as e:  # noqa: BLE001
        checks.append(("FakeBackend", "fail", str(e)))
        ok = False

    try:
        from agent.loop import AgentLoop  # noqa: F401

        checks.append(("主循环模块", "ok", "可导入"))
    except Exception as e:  # noqa: BLE001
        checks.append(("主循环模块", "fail", str(e)))
        ok = False

    try:
        from skills.loader import load_skills

        skills = load_skills()
        names = ", ".join(s.name for s in skills) or "(无)"
        checks.append(("Skills", "ok", f"可加载：{names}"))
    except Exception as e:  # noqa: BLE001
        checks.append(("Skills", "fail", str(e)))
        ok = False

    if RICH_AVAILABLE:
        checks.append(("Rich 终端展示", "ok", "已安装"))
    else:
        checks.append(("Rich 终端展示", "warn", "未安装，将使用纯文本降级；运行 pip install -r requirements.txt"))

    if console is not None and Table is not None and Panel is not None:
        table = Table(title="自检结果", border_style="blue")
        table.add_column("检查项", style="cyan", width=18)
        table.add_column("状态", width=10)
        table.add_column("详情", style="dim")
        status_text = {
            "ok": "[green]通过[/green]",
            "warn": "[yellow]降级[/yellow]",
            "fail": "[red]失败[/red]",
        }
        for name, status, detail in checks:
            table.add_row(name, status_text[status], detail)
        console.print(table)
        if ok:
            console.print(Panel("[green bold]自检通过[/green bold]", border_style="green"))
        else:
            console.print(Panel("[red bold]自检未通过[/red bold]", border_style="red"))
    else:
        for name, status, detail in checks:
            label = {"ok": "[ok]", "warn": "[warn]", "fail": "[FAIL]"}[status]
            print(f"{label} {name}：{detail}")
        print("== 自检", "通过" if ok else "未通过", "==")

    return 0 if ok else 1


def _init_agent():
    """初始化 backend 和 AgentLoop。"""
    from agent.loop import AgentLoop

    reg = build_default_registry()
    base_prompt = SYSTEM_PROMPT.format(
        current_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        current_location="浙江省 杭州市",
    )
    system_prompt = base_prompt

    try:
        from skills.loader import load_skills, make_load_skill_tool, render_skill_catalog

        skills = load_skills()
        if skills:
            reg.register(make_load_skill_tool(skills))
            system_prompt = base_prompt + "\n\n" + render_skill_catalog(skills)
            _print(
                f"[green][ok][/green] Skills 已发现：{', '.join(s.name for s in skills)}"
                if console
                else f"[ok] Skills 已发现：{', '.join(s.name for s in skills)}"
            )
    except Exception as e:  # noqa: BLE001
        _notice(f"[提示] Skills 未接入（{e}），继续使用基础提示词。")

    from mcp.client import MCPClient, register_mcp_tools

    try:
        mcp = MCPClient(
            ["python", "mcp/echo_server.py"],
            timeout_seconds=_env_float("MCP_ECHO_TIMEOUT_SECONDS", 5),
        )
        mcp.start()
        register_mcp_tools(reg, mcp)
    except Exception as e:  # noqa: BLE001
        _notice(f"[提示] MCP 未接入（{e}），仅用内置工具。")

    try:
        pw_mcp = MCPClient([
            "npx",
            "-y",
            "@playwright/mcp@latest",
            "--headless",
            "--isolated",
            "--ignore-https-errors",
        ], timeout_seconds=_env_float("MCP_NPX_TIMEOUT_SECONDS", 12))
        pw_mcp.start()
        register_mcp_tools(reg, pw_mcp)
        _print("[green][ok][/green] Playwright MCP 已接入。" if console else "[ok] Playwright MCP 已接入。")
    except Exception as e:  # noqa: BLE001
        _notice(f"[提示] Playwright MCP 未接入（{e}），继续使用其它工具。")

    firecrawl_key = os.environ.get("FIRECRAWL_API_KEY")
    if firecrawl_key:
        try:
            firecrawl_mcp = MCPClient(
                ["npx", "-y", "firecrawl-mcp"],
                env={"FIRECRAWL_API_KEY": firecrawl_key},
                timeout_seconds=_env_float("MCP_NPX_TIMEOUT_SECONDS", 12),
            )
            firecrawl_mcp.start()
            register_mcp_tools(reg, firecrawl_mcp)
            _print("[green][ok][/green] Firecrawl MCP 已接入。" if console else "[ok] Firecrawl MCP 已接入。")
        except Exception as e:  # noqa: BLE001
            _notice(f"[提示] Firecrawl MCP 未接入（{e}），继续使用其它工具。")
    else:
        _notice("[提示] 未设置 FIRECRAWL_API_KEY，跳过 Firecrawl MCP。")

    # 真正跑任务：优先用 DeepSeek API；没配 key 时回退到 FakeBackend（离线打通管道）
    try:
        from backend.client import DeepSeekBackend

        backend = DeepSeekBackend()
    except Exception as e:  # noqa: BLE001
        from backend.fake_backend import FakeBackend

        _notice(f"[提示] 未启用真后端（{e}），回退 FakeBackend。")
        backend = FakeBackend()

    return AgentLoop(backend, reg, system_prompt)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="mini-openclaw")
    p.add_argument("task", nargs="?", help="要让 agent 完成的任务（自然语言）")
    p.add_argument("--selfcheck", action="store_true", help="只做骨架自检")
    p.add_argument("-i", "--interactive", action="store_true", help="交互模式：多轮对话，history 保持不变")
    args = p.parse_args(argv)

    if args.selfcheck:
        return selfcheck()
    if not args.interactive and not args.task:
        p.print_help()
        return 2

    agent = _init_agent()

    if args.interactive:
        welcome()
        while True:
            try:
                if console is not None:
                    user_input = console.input("[bold green]▸[/bold green] ").strip()
                else:
                    user_input = input(">>> ").strip()
            except (EOFError, KeyboardInterrupt):
                _print()
                break

            if not user_input:
                continue
            if user_input.lower() in ("exit", "quit", "q"):
                break
            if user_input.lower() == "new":
                agent.reset()
                _print("[cyan][重置][/cyan] 历史已清空" if console else "[重置] 历史已清空")
                continue

            agent.chat(user_input)
            _print()

        return 0

    agent.run(args.task)
    return 0


if __name__ == "__main__":
    sys.exit(main())
