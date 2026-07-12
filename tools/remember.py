"""长期记忆写入工具。

当用户明确要求 Agent 长期记住项目约定、用户偏好或关键决策时，
模型可以调用 remember 工具，把内容写入 MEMORY.md。
"""

from __future__ import annotations

import re

from agent.memory import Memory
from tools.base import Tool


_SENSITIVE_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----", re.IGNORECASE),
    re.compile(r"\b(api[_ -]?key|secret|password|passwd|cookie)\b", re.IGNORECASE),
    re.compile(r"\b(access|refresh|bearer)?[_ -]?token\s*[:=]", re.IGNORECASE),
    re.compile(r"\bbearer\s+[a-z0-9._\-]{8,}", re.IGNORECASE),
    re.compile(r"\bsk-[a-z0-9_\-]{6,}", re.IGNORECASE),
    re.compile(r"\bfc-[a-z0-9_\-]{6,}", re.IGNORECASE),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
]


def _looks_sensitive(note: str) -> bool:
    return any(pattern.search(note) for pattern in _SENSITIVE_PATTERNS)


def _remember(note: str) -> str:
    """把一条长期有效的信息写入 MEMORY.md。"""
    cleaned_note = note.strip()

    if not cleaned_note:
        return "记忆写入失败：记忆内容不能为空。"
    if _looks_sensitive(cleaned_note):
        return "记忆写入失败：内容疑似包含敏感凭据，已拒绝保存。"

    try:
        saved_note = Memory().write(cleaned_note)
    except Exception as exc:  # noqa: BLE001
        return f"记忆写入失败：{exc}"

    return f"已记住：{saved_note}"


remember_tool = Tool(
    name="remember",
    description=(
        "当用户明确要求你长期记住一条信息时调用此工具。"
        "适合保存跨会话仍然有效的项目约定、稳定偏好、关键决策和工作规则。"
        "例如：用户说「记住」「以后都要」「从现在开始」「本项目统一使用」。"
        "不要保存普通闲聊、临时任务、网页正文、一次性数据、密码、API Key、"
        "Cookie、Token 或其他敏感信息。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "note": {
                "type": "string",
                "description": (
                    "需要长期保存的完整信息。"
                    "应去掉「记住」等指令性前缀，只保留具体约定或偏好。"
                ),
            }
        },
        "required": ["note"],
        "additionalProperties": False,
    },
    run=_remember,
)
