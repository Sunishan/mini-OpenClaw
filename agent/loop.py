"""ReAct 主循环（Agent 的心脏）。

  while 没到最终答复:
      assistant = backend.chat(messages, tools)      # 模型这一步：思考 or 调工具
      if assistant 有 tool_calls:
          for call in tool_calls:
              obs = registry.get(call.name).run(**call.arguments)   # 执行工具
              messages.append(tool_result(obs))                     # 注入 observation
      else:
          return assistant.content                                 # 最终答复

支持单次 run()（持久化历史，跨进程续聊）和多次 chat()（同进程多轮）两种模式。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent.context import maybe_compact, truncate_observation
from agent import permissions
from agent.url_safety import extract_urls, is_safe_public_url, normalize_url
from tools.base import ToolRegistry

try:
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.markup import escape
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
except ImportError:  # pragma: no cover - only used before optional UI dependency is installed
    Console = None  # type: ignore[assignment]
    Markdown = None  # type: ignore[assignment]
    Panel = None  # type: ignore[assignment]
    Table = None  # type: ignore[assignment]
    Text = None  # type: ignore[assignment]

    def escape(value: str) -> str:
        return value


console = Console() if Console is not None else None
HISTORY_FILE = Path.home() / ".mini-openclaw" / "history.json"


def _preview(value: object, limit: int) -> str:
    text = str(value)
    return text[:limit] + "..." if len(text) > limit else text


def _print_task(user_task: str) -> None:
    if console is not None and Panel is not None and Text is not None:
        console.print(Panel(Text(user_task), title="任务", border_style="cyan"))
    else:
        print(f"[任务] {user_task}")


def _print_history_loaded(count: int) -> None:
    if console is not None:
        console.print(f"[dim][历史] 载入 {count} 条历史消息，继续对话[/dim]")
    else:
        print(f"[历史] 载入 {count} 条历史消息，继续对话")


def _print_turn(turn: int, max_turns: int, message_count: int) -> None:
    if console is not None:
        console.print(f"[dim]── 第 {turn}/{max_turns} 轮 · 当前消息数 {message_count} ──[/dim]")
    else:
        print(f"\n── 第 {turn}/{max_turns} 轮 ──")
        print(f"[任务] 当前消息数: {message_count}")


def _print_thought(thought: str) -> None:
    if not thought:
        return
    if console is not None and Panel is not None and Text is not None:
        console.print(Panel(Text(thought.strip()), title="思考", border_style="magenta"))
    else:
        for line in thought.strip().splitlines():
            print(f"  [思考] {line}")


def _print_tool_results(rows: list[tuple[str, str, str, str]]) -> None:
    if console is not None and Table is not None:
        table = Table(title="工具调用", border_style="blue", show_header=True, header_style="bold white")
        table.add_column("#", style="dim", width=3)
        table.add_column("工具", style="cyan", width=28)
        table.add_column("参数", style="yellow")
        table.add_column("结果", style="green")
        for index, name, args, result in rows:
            table.add_row(index, escape(name), escape(args), escape(result))
        console.print(table)
        return

    for index, name, args, result in rows:
        print(f"\n  [工具 {index}] 调用: {name}")
        if args:
            print(f"      参数: {args}")
        print(f"  [结果] {result}")


def _print_final(answer: str) -> None:
    if console is not None and Panel is not None and Markdown is not None:
        console.print(Panel(Markdown(answer or ""), title="最终回答", border_style="green"))
    else:
        print("\n[完成] 最终回答:")
        print(answer)


def _print_warning(message: str) -> None:
    if console is not None and Panel is not None:
        console.print(Panel(f"[bold red]{escape(message)}[/bold red]", border_style="red"))
    else:
        print(f"\n[警告] {message}")


def _backend_chat(backend: Any, messages: list[dict[str, Any]], tools: list[dict]) -> dict[str, Any]:
    if console is not None:
        with console.status("[bold yellow]思考中...[/bold yellow]", spinner="dots"):
            return backend.chat(messages, tools=tools)
    return backend.chat(messages, tools=tools)


def _load_history() -> list[dict[str, Any]]:
    """从文件加载历史消息。"""
    try:
        if HISTORY_FILE.exists():
            data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
    except Exception:
        pass
    return []


def _save_history(messages: list[dict[str, Any]]) -> None:
    """保存历史消息到文件（只保留非 tool 消息，节省空间）。

    注意：由于 tool 结果被丢弃，assistant 消息中的 tool_calls
    也必须清空，否则 DeepSeek/OpenAI API 会拒绝请求（
    "assistant message with tool_calls must be followed
     by tool messages"）。
    """
    try:
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        condensed = []
        for m in messages:
            if m.get("role") not in ("system", "user", "assistant"):
                continue
            if m.get("role") == "assistant" and m.get("tool_calls"):
                # 丢弃 tool_calls，因为对应的 tool 结果已被删除
                m = {"role": "assistant", "content": m.get("content", "")}
            condensed.append(m)
        HISTORY_FILE.write_text(json.dumps(condensed, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


class AgentLoop:
    def __init__(
        self,
        backend: Any,
        registry: ToolRegistry,
        system_prompt: str,
        max_turns: int = 20,
        auto_approve: bool = False,
        interactive_mode: bool = False,
    ):
        self.backend = backend
        self.registry = registry
        self.system_prompt = system_prompt
        self.max_turns = max_turns
        self.auto_approve = auto_approve
        self.interactive_mode = interactive_mode
        self.messages: list[dict[str, Any]] = []   # 跨轮对话的消息历史
        self._history_loaded = False               # 防止重复加载
        self._trusted_domains: set[str] = set()    # 交互式确认中信任的域名
        self._user_allowed_urls: set[str] = set()  # 用户显式提供且通过安全检查的 URL

    def _record_user_urls(self, text: str) -> None:
        """Remember safe URLs that appeared explicitly in user input."""
        for url in extract_urls(text):
            safe, _reason = is_safe_public_url(url)
            if safe:
                self._user_allowed_urls.add(normalize_url(url))

    def run(self, user_task: str) -> str:
        """单次执行：自动加载历史消息，实现跨进程续聊。

        首次执行：system prompt + 用户输入
        后续执行：从文件加载历史 + 追加新的用户输入
        """
        _print_task(user_task)
        self._record_user_urls(user_task)
        if not self._history_loaded:
            history = _load_history()
            if history and history[0].get("role") == "system":
                # 有历史，追加当前用户输入
                self.messages = history
                self.messages.append({"role": "user", "content": user_task})
                _print_history_loaded(len(history))
            else:
                # 无历史，从头开始
                self.messages = [
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": user_task},
                ]
            self._history_loaded = True
        else:
            # 同一个进程内再次调 run，直接追加
            self.messages.append({"role": "user", "content": user_task})

        result = self._execute()

        # 执行完后保存历史
        _save_history(self.messages)
        return result

    def chat(self, user_input: str) -> str:
        """多轮对话：保留历史消息，追加用户输入后继续执行。

        首次调用会自动插入 system prompt。
        """
        _print_task(user_input)
        self._record_user_urls(user_input)
        if not self.messages:
            self.messages = [{"role": "system", "content": self.system_prompt}]
        self.messages.append({"role": "user", "content": user_input})
        result = self._execute()

        # 执行完后保存历史（交互模式也持久化）
        _save_history(self.messages)
        return result

    def reset(self) -> None:
        """清空对话历史（内存 + 文件）。"""
        self.messages = []
        self._history_loaded = False
        self._trusted_domains.clear()
        self._user_allowed_urls.clear()
        try:
            HISTORY_FILE.unlink(missing_ok=True)
        except Exception:
            pass

    def _confirm_tool(self, name: str, args: dict) -> bool:
        """交互式确认：用户实时决定是否放行工具调用。

        支持 'a' = always allow this domain（加入信任列表）。
        """
        from urllib.parse import urlparse

        if name == "remember":
            note = str(args.get("note", "")).strip()
            lines = [
                "[确认] 工具「remember」请求写入长期记忆",
                f"  内容: {_preview(note, 500)}",
                "  影响: 该记忆会写入 MEMORY.md，并在后续会话中影响 Agent 行为",
            ]
            prompt_text = "\n".join(lines)
            if console is not None:
                console.print(f"[yellow]{prompt_text}[/yellow]")
                try:
                    answer = console.input("[bold]是否允许？(y/N) [/bold]").strip().lower()
                except (KeyboardInterrupt, EOFError):
                    return False
            else:
                print(prompt_text)
                try:
                    answer = input("是否允许？(y/N) ").strip().lower()
                except (KeyboardInterrupt, EOFError):
                    return False
            return answer in ("y", "yes")

        url = args.get("url", "")
        parsed_host = urlparse(url).hostname if url else ""
        domain = parsed_host.lower() if parsed_host else ""

        # 检查域名是否已信任
        if domain and domain in self._trusted_domains:
            return True

        # 构建提示信息
        lines = [f"[确认] 工具「{name}」请求执行"]
        if url:
            lines.append(f"  URL: {url}")
        if domain:
            lines.append(f"  域名: {domain}")
        prompt_text = "\n".join(lines)

        # 根据是否安装 rich 选择提示方式
        if console is not None:
            console.print(f"[yellow]{prompt_text}[/yellow]")
            choices = "(y/N"
            if domain:
                choices += "/a=信任此域名不再询问"
            choices += ") "
            try:
                answer = console.input(f"[bold]是否允许？{choices}[/bold]").strip().lower()
            except (KeyboardInterrupt, EOFError):
                return False
        else:
            print(prompt_text)
            choices = "(y/N"
            if domain:
                choices += "/a=trust domain"
            choices += ") "
            try:
                answer = input(f"是否允许？{choices}").strip().lower()
            except (KeyboardInterrupt, EOFError):
                return False

        if answer == "a" and domain:
            self._trusted_domains.add(domain)
            if console is not None:
                console.print(f"[dim]域名 {domain} 已加入信任列表，本次会话不再询问[/dim]")
            else:
                print(f"[info] 域名 {domain} 已加入信任列表，本次会话不再询问")
            return True
        if answer in ("y", "yes"):
            return True
        return False

    def _execute(self) -> str:
        """ReAct 主循环：从当前 messages 开始执行，返回最终答复。"""
        if not self.messages:
            return ""

        for turn in range(self.max_turns):
            _print_turn(turn + 1, self.max_turns, len(self.messages))
            assistant = _backend_chat(self.backend, self.messages, self.registry.schemas())
            self.messages.append({
                "role": "assistant",
                "content": assistant.get("content", ""),
                "tool_calls": assistant.get("tool_calls", []),
            })

            # 显示模型的思考内容
            thought = assistant.get("content", "")
            _print_thought(thought)

            tool_calls = assistant.get("tool_calls") or []
            if not tool_calls:
                _print_final(thought)
                return thought

            rows: list[tuple[str, str, str, str]] = []

            # 分发并执行工具
            for i, call in enumerate(tool_calls):
                call_id = call.get("id") or f"call_{i}"
                name = call["name"]
                args = call.get("arguments", {})
                args_str = ", ".join(f"{k}={_preview(v, 120)}" for k, v in args.items())

                verdict, reason = permissions.check_with_reason(
                    name,
                    args,
                    Path.cwd(),
                    user_allowed_urls=self._user_allowed_urls,
                )
                tool_blocked = verdict == "deny"

                if verdict == "deny":
                    obs = f"[权限层] 拒绝：{reason or '越界写入 / 危险操作'}"
                elif verdict == "confirm" and not self.auto_approve:
                    if self.interactive_mode:
                        allowed = self._confirm_tool(name, args)
                        if not allowed:
                            obs = f"[权限层] 用户拒绝：{name}({args})"
                            tool_blocked = True
                    else:
                        detail = f"（{reason}）" if reason else ""
                        obs = f"[权限层] 需确认{detail}：{name}({args}) —— 已拦截（非交互模式，默认不放行）"
                        tool_blocked = True
                if not tool_blocked:
                    tool = self.registry.get(name)
                    if tool is None:
                        obs = f"错误：未知工具 {name}"
                    else:
                        try:
                            obs = tool.run(**args)
                        except Exception as e:  # noqa: BLE001
                            obs = f"工具 {name} 执行出错：{e}"

                rows.append((str(i + 1), name, args_str, _preview(obs, 500)))
                self.messages.append({
                    "role": "tool",
                    "name": name,
                    "tool_call_id": call_id,
                    "content": truncate_observation(str(obs)),
                })

            _print_tool_results(rows)
            self.messages = maybe_compact(self.messages, self.backend)

        _print_warning("已达到最大轮数上限")
        return "[达到最大轮数上限，未完成任务]"
