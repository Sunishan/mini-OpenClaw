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
    prompt = (
        "把下面的对话历史压缩成要点，保留：\n"
        "1. 任务目标\n"
        "2. 已完成的步骤和输出（提取的 claims、搜索到的证据、各项判定结论）\n"
        "3. 已使用的工具和结果\n"
        "4. 当前进度（下一步要做什么）\n"
        "不要遗漏关键事实或已验证的结论。\n"
        "对话历史：\n" + text
    )
    resp = backend.chat([{"role": "user", "content": prompt}], tools=[])
    return resp.get("content", "")


def _find_protected_skill_ranges(messages: list[dict[str, Any]]) -> set[int]:
    """查找 load_skill 消息的索引范围，返回受保护的索引集合。

    load_skill 返回的 SKILL.md 正文很长，被压缩后模型会重新加载。
    找到 assistant 调用 → tool 结果的配对行，标记为受保护。
    """
    protected: set[int] = set()
    for i, msg in enumerate(messages):
        if msg.get("role") != "assistant":
            continue
        calls = msg.get("tool_calls") or []
        if any(c.get("name") == "load_skill" for c in calls):
            protected.add(i)
            j = i + 1
            while j < len(messages) and messages[j].get("role") == "tool":
                protected.add(j)
                j += 1
    return protected


def maybe_compact(messages: list[dict[str, Any]], backend: Any,
                  budget: int = 12000, keep_recent: int = 10) -> list[dict[str, Any]]:
    """超预算则压缩历史，返回新的 messages。

    优化策略（2026-07）：
    - budget 提升至 12000，降低压缩频率
    - keep_recent 提升至 10，保留更多上下文
    - 保护 load_skill 相关消息不被压缩，避免模型重新加载 SKILL.md
    - 压缩摘要更清晰地保留已完成的步骤、已使用的工具、已获得的关键发现
    """
    if estimate_tokens(messages) <= budget:
        return messages
    if not messages:
        return messages

    system = messages[0]
    protected_indices = _find_protected_skill_ranges(messages)

    # 滑动窗口的起点：从末尾向前数 keep_recent 条
    start = max(1, len(messages) - keep_recent) if keep_recent > 0 else len(messages)

    # OpenAI/DeepSeek 要求 role=tool 必须紧跟对应 assistant.tool_calls。
    # 如果滑动窗口切在 tool 消息中间，就向前扩到该组 tool_calls 的 assistant。
    while start > 1 and messages[start].get("role") == "tool":
        start -= 1

    # 如果滑动窗口切到了受保护的 load_skill 消息中间，向前扩展到保护范围外
    for p in sorted(protected_indices):
        if start <= p:
            start = min(start, max(1, p - 1))
            while start > 1 and messages[start].get("role") == "tool":
                start -= 1
            break

    recent = messages[start:]
    middle = messages[1:start]
    if not middle:
        return messages

    # 只对非保护的消息做摘要
    protected_ids = {id(messages[i]) for i in protected_indices}
    summarizable = [m for m in middle if id(m) not in protected_ids]
    if not summarizable:
        return messages

    memo = _summarize(backend, summarizable)
    memo_message = {"role": "system", "content": "历史备忘：" + memo}
    return [system, memo_message] + recent


def truncate_observation(text: str, max_chars: int = 4000) -> str:
    """工具结果过长时截断并提示。"""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n...[已截断，共 {len(text)} 字符]"
