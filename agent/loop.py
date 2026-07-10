"""ReAct 主循环（Agent 的心脏）。

  while 没到最终答复:
      assistant = backend.chat(messages, tools)      # 模型这一步：思考 or 调工具
      if assistant 有 tool_calls:
          for call in tool_calls:
              obs = registry.get(call.name).run(**call.arguments)   # 执行工具
              messages.append(tool_result(obs))                     # 注入 observation
      else:
          return assistant.content                                 # 最终答复

Day5 你要把下面的 run() 真正实现出来（Day6 随工具集扩展完善）。骨架已给出结构与防呆上限。
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
        self.max_turns = max_turns          # 防死循环：硬上限

    def run(self, user_task: str) -> str:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_task},
        ]
        print(f"📋 任务: {user_task}")

        for turn in range(self.max_turns):
            print(f"\n── 第 {turn+1}/{self.max_turns} 轮 ──")
            assistant = self.backend.chat(messages, tools=self.registry.schemas())
            messages.append({"role": "assistant",
                             "content": assistant.get("content", ""),
                             "tool_calls": assistant.get("tool_calls", [])})

            # 显示模型的思考内容（思维链）
            thought = assistant.get("content", "")
            if thought:
                for line in thought.strip().splitlines():
                    print(f"  💭 {line}")

            tool_calls = assistant.get("tool_calls") or []
            if not tool_calls:
                print(f"\n✅ 最终回答:")
                print(f"  {thought}")
                return thought

            # 分发并执行工具，把每个结果作为 role="tool" 注入 messages：
            for i, call in enumerate(tool_calls):
                call_id = call.get("id") or f"call_{i}"
                name = call["name"]
                args = call.get("arguments", {})
                print(f"\n  🔧 调用工具: {name}")
                for k, v in args.items():
                    v_preview = str(v)[:200] + "…" if len(str(v)) > 200 else str(v)
                    print(f"      {k} = {v_preview}")

                tool = self.registry.get(name)
                if tool is None:
                    obs = f"错误：未知工具 {name}"
                else:
                    try:
                        obs = tool.run(**args)
                    except Exception as e:  # noqa
                        obs = f"工具 {name} 执行出错：{e}"

                # 截断过长的工具结果，只显示关键部分
                obs_preview = str(obs)[:500] + "…" if len(str(obs)) > 500 else str(obs)
                print(f"  📝 结果: {obs_preview}")

                messages.append({"role": "tool", "name": name,
                                 "tool_call_id": call_id,
                                 "content": truncate_observation(str(obs))})

            messages = maybe_compact(messages, self.backend)

        print("\n⚠️ 已达到最大轮数上限")
        return "[达到最大轮数上限，未完成任务]"
