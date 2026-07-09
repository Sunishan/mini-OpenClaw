"""上下文管理（Day7）：token 预算、滑动窗口、自动摘要 / compaction。

模型上下文窗口有限。长任务里 messages 会越堆越长，迟早超预算。
策略：
  - 估算当前 messages 的 token 数；
  - 超过阈值时触发 compaction：把较早的对话摘要成一条 system 备忘，
    保留最近 K 轮原文 + 关键工具结果；
  - tool result 过长时先截断/摘要再注入。
"""
from __future__ import annotations
from typing import Any


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    # TODO[Day7] 粗估即可（字符数/4 或用 tokenizer 精确数）
    return sum(len(str(m.get("content", ""))) for m in messages) // 4


def _summarize(backend: Any, chunk: list[dict[str, Any]]) -> str:
    text = "\n".join(f"{m['role']}: {m.get('content', '')}" for m in chunk)
    prompt = "把下面的对话历史压缩成要点，保留任务目标、关键发现、已完成步骤：\n" + text
    resp = backend.chat([{"role": "user", "content": prompt}], tools=[])
    return resp.get("content", "")


def maybe_compact(messages: list[dict[str, Any]], backend: Any,
                  budget: int = 6000, keep_recent: int = 4) -> list[dict[str, Any]]:
    """超预算则压缩历史，返回新的 messages。"""
    if estimate_tokens(messages) <= budget:
        return messages
    if not messages:
        return messages

    system = messages[0]
    start = max(1, len(messages) - keep_recent) if keep_recent > 0 else len(messages)
    # OpenAI/DeepSeek 要求 role=tool 必须紧跟对应 assistant.tool_calls。
    # 如果滑动窗口切在 tool 消息中间，就向前扩到该组 tool_calls 的 assistant。
    while start > 1 and messages[start].get("role") == "tool":
        start -= 1
    recent = messages[start:]
    middle = messages[1:start]
    if not middle:
        return messages

    memo = _summarize(backend, middle)
    memo_message = {"role": "system", "content": "历史备忘：" + memo}
    return [system, memo_message] + recent


def truncate_observation(text: str, max_chars: int = 4000) -> str:
    """工具结果过长时截断并提示。"""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n...[已截断，共 {len(text)} 字符]"
