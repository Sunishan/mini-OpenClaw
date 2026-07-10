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
from tools.base import ToolRegistry


HISTORY_FILE = Path.home() / ".mini-openclaw" / "history.json"


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
    """保存历史消息到文件（只保留非 tool 消息，节省空间）。"""
    try:
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        # 只保留 system + user + assistant（含 tool_calls），去掉 tool 结果
        condensed = [m for m in messages if m.get("role") in ("system", "user", "assistant")]
        HISTORY_FILE.write_text(json.dumps(condensed, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


class AgentLoop:
    def __init__(self, backend: Any, registry: ToolRegistry, system_prompt: str,
                 max_turns: int = 20):
        self.backend = backend
        self.registry = registry
        self.system_prompt = system_prompt
        self.max_turns = max_turns
        self.messages: list[dict[str, Any]] = []   # 跨轮对话的消息历史
        self._history_loaded = False               # 防止重复加载

    def run(self, user_task: str) -> str:
        """单次执行：自动加载历史消息，实现跨进程续聊。

        首次执行：system prompt + 用户输入
        后续执行：从文件加载历史 + 追加新的用户输入
        """
        if not self._history_loaded:
            history = _load_history()
            if history and history[0].get("role") == "system":
                # 有历史，追加当前用户输入
                self.messages = history
                self.messages.append({"role": "user", "content": user_task})
                print(f"[历史] 载入 {len(history)} 条历史消息，继续对话")
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
        try:
            HISTORY_FILE.unlink(missing_ok=True)
        except Exception:
            pass

    def _execute(self) -> str:
        """ReAct 主循环：从当前 messages 开始执行，返回最终答复。"""
        if not self.messages:
            return ""

        print(f"[任务] 当前消息数: {len(self.messages)}")

        for turn in range(self.max_turns):
            print(f"\n── 第 {turn+1}/{self.max_turns} 轮 ──")
            assistant = self.backend.chat(self.messages, tools=self.registry.schemas())
            self.messages.append({"role": "assistant",
                                  "content": assistant.get("content", ""),
                                  "tool_calls": assistant.get("tool_calls", [])})

            # 显示模型的思考内容
            thought = assistant.get("content", "")
            if thought:
                for line in thought.strip().splitlines():
                    print(f"  [思考] {line}")

            tool_calls = assistant.get("tool_calls") or []
            if not tool_calls:
                print(f"\n[完成] 最终回答:")
                print(f"  {thought}")
                return thought

            # 分发并执行工具
            for i, call in enumerate(tool_calls):
                call_id = call.get("id") or f"call_{i}"
                name = call["name"]
                args = call.get("arguments", {})
                print(f"\n  [工具] 调用: {name}")
                for k, v in args.items():
                    v_preview = str(v)[:200] + "..." if len(str(v)) > 200 else str(v)
                    print(f"      {k} = {v_preview}")

                tool = self.registry.get(name)
                if tool is None:
                    obs = f"错误：未知工具 {name}"
                else:
                    try:
                        obs = tool.run(**args)
                    except Exception as e:  # noqa
                        obs = f"工具 {name} 执行出错：{e}"

                obs_preview = str(obs)[:500] + "..." if len(str(obs)) > 500 else str(obs)
                print(f"  [结果] {obs_preview}")

                self.messages.append({"role": "tool", "name": name,
                                      "tool_call_id": call_id,
                                      "content": truncate_observation(str(obs))})

            self.messages = maybe_compact(self.messages, self.backend)

        print("\n[警告] 已达到最大轮数上限")
        return "[达到最大轮数上限，未完成任务]"
