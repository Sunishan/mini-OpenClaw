"""ReAct 主循环（Agent 的心脏）。

  while 没到最终答复:
      assistant = backend.chat(messages, tools)      # 模型这一步：思考 or 调工具
      if assistant 有 tool_calls:
          for call in tool_calls:
              obs = registry.get(call.name).run(**call.arguments)   # 执行工具
              messages.append(tool_result(obs))                     # 注入 observation
      else:
          return assistant.content                                 # 最终答复

支持单次 run() 和多次 chat() 两种模式。
"""
from __future__ import annotations
from typing import Any

from agent.context import maybe_compact, truncate_observation
from tools.base import ToolRegistry


class AgentLoop:
    def __init__(self, backend: Any, registry: ToolRegistry, system_prompt: str,
                 max_turns: int = 20):
        self.backend = backend
        self.registry = registry
        self.system_prompt = system_prompt
        self.max_turns = max_turns
        self.messages: list[dict[str, Any]] = []   # 跨轮对话的消息历史

    def run(self, user_task: str) -> str:
        """单次执行：从头开始，跑完即止。"""
        self.messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_task},
        ]
        return self._execute()

    def chat(self, user_input: str) -> str:
        """多轮对话：保留历史消息，追加用户输入后继续执行。

        首次调用会自动插入 system prompt。
        """
        if not self.messages:
            self.messages = [{"role": "system", "content": self.system_prompt}]
        self.messages.append({"role": "user", "content": user_input})
        return self._execute()

    def reset(self) -> None:
        """清空对话历史。"""
        self.messages = []

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
