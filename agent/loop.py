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
import re
import signal
import time
from pathlib import Path
from typing import Any, Callable, TypeVar

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
T = TypeVar("T")


_TRANSIENT_FAILURE_PATTERNS = (
    r"timeout",
    r"timed out",
    r"请求超时",
    r"temporarily unavailable",
    r"network error",
    r"网络错误",
    r"请求失败",
    r"connection reset",
    r"connection aborted",
    r"connection error",
    r"rate limit",
    r"\b429\b",
    r"\b500\b",
    r"\b502\b",
    r"\b503\b",
    r"\b504\b",
    r"MCP server 响应超时",
)
_RECOVERABLE_FAILURE_PATTERNS = (
    r"missing_required_arguments",
    r"invalid_arguments",
    r"未找到待替换文本",
    r"不唯一",
    r"文件不存在",
    r"No such file",
    r"未找到 rg",
    r"unknown option",
    r"JSONDecodeError",
    r"\[失败\]",
    r"\[错误\]",
    r"执行出错",
    r"returncode=",
)
_PERMANENT_FAILURE_PATTERNS = (
    r"\[权限层\] 拒绝",
    r"\[权限层\] 用户拒绝",
    r"\[权限层\] 需确认",
    r"URL 不安全",
    r"读取敏感路径",
    r"拒绝写入工作目录外文件",
    r"未知工具",
    r"高危命令",
)


def _preview(value: object, limit: int) -> str:
    text = str(value)
    return text[:limit] + "..." if len(text) > limit else text


def _matches_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def _classify_failure_text(text: str) -> str | None:
    """Return transient/recoverable/permanent when an observation looks failed."""
    if not text:
        return None
    if _matches_any(text, _PERMANENT_FAILURE_PATTERNS):
        return "permanent"
    if _matches_any(text, _TRANSIENT_FAILURE_PATTERNS):
        return "transient"
    if _matches_any(text, _RECOVERABLE_FAILURE_PATTERNS):
        return "recoverable"
    return None


def _is_transient_exception(exc: Exception) -> bool:
    return _classify_failure_text(str(exc)) == "transient"


def _call_with_retry(
    fn: Callable[[], T],
    *,
    max_tries: int = 3,
    base_delay: float = 0.5,
) -> T:
    """Retry transient exceptions with exponential backoff."""
    last_exc: Exception | None = None
    for attempt in range(max_tries):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if not _is_transient_exception(exc) or attempt == max_tries - 1:
                raise
            time.sleep(base_delay * (2 ** attempt))
    assert last_exc is not None
    raise last_exc


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
    def call() -> dict[str, Any]:
        return backend.chat(messages, tools=tools)

    if console is not None:
        with console.status("[bold yellow]思考中...[/bold yellow]", spinner="dots"):
            return _call_with_retry(call)
    return _call_with_retry(call)


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
            if m.get("transient"):
                continue
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
        self._paused = False                       # Ctrl+Z 暂停标志
        self._failure_counts: dict[str, int] = {}
        self._reflection_counts: dict[str, int] = {}
        self._max_failures_per_key = 3
        self._max_reflections_per_key = 2
        self._setup_signal_handler()

    def _setup_signal_handler(self) -> None:
        """注册 Ctrl+Z (SIGTSTP) 信号处理器，用于暂停/恢复。"""
        if not hasattr(signal, "SIGTSTP"):
            return  # Windows 没有 SIGTSTP
        try:
            signal.signal(signal.SIGTSTP, self._handle_pause_resume)
        except (ValueError, OSError):
            pass  # 非主线程或信号不支持时静默跳过

    def _handle_pause_resume(self, signum: int, frame: object) -> None:
        """Ctrl+Z 信号处理：切换暂停/恢复。"""
        if self._paused:
            self._paused = False
            _print_warning("已恢复执行")
        else:
            self._paused = True
            _print_warning("已暂停（按 Ctrl+Z 恢复）")

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
                # 有历史，但用当前 system_prompt 替换旧的（更新时间戳等动态信息）
                self.messages = history
                self.messages[0]["content"] = self.system_prompt
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
        支持 Ctrl+C 中断当前执行。
        """
        _print_task(user_input)
        self._record_user_urls(user_input)
        if not self.messages:
            self.messages = [{"role": "system", "content": self.system_prompt}]
        self.messages.append({"role": "user", "content": user_input})
        try:
            result = self._execute()
        except KeyboardInterrupt:
            _print_warning("用户按 Ctrl+C 中断，当前任务已停止")
            result = "[已停止] 用户中断了当前任务"

        # 执行完后保存历史（交互模式也持久化）
        _save_history(self.messages)
        return result

    def reset(self) -> None:
        """清空对话历史（内存 + 文件）。"""
        self.messages = []
        self._history_loaded = False
        self._trusted_domains.clear()
        self._user_allowed_urls.clear()
        self._failure_counts.clear()
        self._reflection_counts.clear()
        try:
            HISTORY_FILE.unlink(missing_ok=True)
        except Exception:
            pass

    def _failure_key(self, name: str, args: dict) -> str:
        if "path" in args:
            return f"{name}:path:{args.get('path')}"
        if "url" in args:
            return f"{name}:url:{args.get('url')}"
        if "command" in args:
            return f"{name}:command:{args.get('command')}"
        return name

    def _clear_failure(self, key: str) -> None:
        self._failure_counts.pop(key, None)

    def _annotate_failed_observation(self, name: str, args: dict, obs: str) -> str:
        failure_kind = _classify_failure_text(obs)
        key = self._failure_key(name, args)
        if failure_kind is None:
            self._clear_failure(key)
            return obs

        count = self._failure_counts.get(key, 0) + 1
        self._failure_counts[key] = count

        if count >= self._max_failures_per_key:
            return (
                f"{obs}\n\n"
                f"[错误恢复] 同一操作已连续失败 {count} 次，标记为 blocked：{key}。\n"
                "请不要继续重复同一工具调用；应换一种方法、缩小目标，或向用户说明当前限制。"
            )

        if failure_kind == "permanent":
            return (
                f"{obs}\n\n"
                "[错误恢复] 该失败看起来是权限、安全策略或工具不存在导致的永久失败。"
                "不要重复同一操作；请重规划。"
            )
        if failure_kind == "transient":
            return (
                f"{obs}\n\n"
                f"[错误恢复] {name} 疑似瞬时失败，已重试但仍失败。"
                "下一步应换一种路径或降低目标。"
            )
        return (
            f"{obs}\n\n"
            "[错误恢复] 该失败可能可修复。请检查参数、路径、输入格式或前置步骤，"
            "下一步只做一个最小修正动作。"
        )

    def _run_tool_with_recovery(self, name: str, args: dict) -> str:
        tool = self.registry.get(name)
        if tool is None:
            return self._annotate_failed_observation(name, args, f"错误：未知工具 {name}")

        key = self._failure_key(name, args)

        def run_once() -> str:
            try:
                return str(tool.run(**args))
            except Exception as e:  # noqa: BLE001
                return f"工具 {name} 执行出错：{e}"

        obs = run_once()
        failure_kind = _classify_failure_text(obs)
        if failure_kind is None:
            self._clear_failure(key)
            return obs

        if failure_kind == "transient":
            for attempt in range(2):
                time.sleep(0.5 * (2 ** attempt))
                retry_obs = run_once()
                if _classify_failure_text(retry_obs) is None:
                    self._clear_failure(key)
                    return (
                        f"{retry_obs}\n\n"
                        f"[错误恢复] {name} 之前疑似瞬时失败，重试后成功。"
                    )

        return self._annotate_failed_observation(name, args, obs)

    def _build_reflection_prompt(self, name: str, args: dict, obs: str) -> dict[str, Any] | None:
        if _classify_failure_text(obs) is None:
            return None

        key = self._failure_key(name, args)
        count = self._reflection_counts.get(key, 0)
        if count >= self._max_reflections_per_key:
            return None

        self._reflection_counts[key] = count + 1
        return {
            "role": "system",
            "transient": True,
            "content": (
                f"反思检查 {count + 1}/{self._max_reflections_per_key}："
                f"刚才工具 {name} 返回失败或异常结果。"
                "请先判断失败属于瞬时失败、参数错误、权限限制、信息不足还是永久失败。"
                "如果能修正，下一步只做一个最小修正动作；"
                "如果同一操作反复失败，不要继续重复，应换路径或说明限制。\n"
                f"工具参数：{args}\n"
                f"工具结果：{obs[:1000]}"
            ),
        }

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
        """ReAct 主循环：从当前 messages 开始执行，返回最终答复。

        Ctrl+C  中断执行
        Ctrl+Z  暂停 / 恢复执行
        """
        import time as _time
        if not self.messages:
            return ""

        for turn in range(self.max_turns):
            # ── 暂停等待 ──
            while self._paused:
                _time.sleep(0.3)

            _print_turn(turn + 1, self.max_turns, len(self.messages))
            try:
                assistant = _backend_chat(self.backend, self.messages, self.registry.schemas())
            except KeyboardInterrupt:
                _print_warning("LLM 调用被 Ctrl+C 中断")
                return "[已停止] 用户中断了 LLM 调用"
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
            reflection_messages: list[dict[str, Any]] = []

            # 分发并执行工具
            for i, call in enumerate(tool_calls):
                call_id = call.get("id") or f"call_{i}"
                name = call["name"]
                args = call.get("arguments", {})
                if not isinstance(args, dict):
                    args = {}
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
                    obs = self._run_tool_with_recovery(name, args)
                else:
                    obs = self._annotate_failed_observation(name, args, str(obs))

                rows.append((str(i + 1), name, args_str, _preview(obs, 500)))
                self.messages.append({
                    "role": "tool",
                    "name": name,
                    "tool_call_id": call_id,
                    "content": truncate_observation(str(obs)),
                })
                reflection = self._build_reflection_prompt(name, args, str(obs))
                if reflection is not None:
                    reflection_messages.append(reflection)

            self.messages.extend(reflection_messages)
            _print_tool_results(rows)
            self.messages = maybe_compact(self.messages, self.backend)

        _print_warning("已达到最大轮数上限")
        return "[达到最大轮数上限，未完成任务]"
