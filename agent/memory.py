"""Agent 长期记忆模块。

负责将跨会话仍然有效的项目约定、用户偏好和关键决策
持久化到项目根目录下的 MEMORY.md。

这里的长期记忆不同于对话历史：
- 对话历史用于续聊；
- 长期记忆用于保存未来仍需遵守的信息。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


# agent/memory.py 的上一级是 agent/，
# 再上一级就是 mini-openclaw 项目根目录。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MEMORY_PATH = PROJECT_ROOT / "MEMORY.md"
DEFAULT_KV_MEMORY_PATH = PROJECT_ROOT / "memory.json"


class Memory:
    """基于 Markdown 文件的最小长期记忆。"""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else DEFAULT_MEMORY_PATH

    def write(self, note: str) -> str:
        """追加写入一条长期记忆。

        Args:
            note: 需要长期保存的项目约定、偏好或关键决策。

        Returns:
            实际写入的记忆文本。

        Raises:
            ValueError: note 为空时抛出。
        """
        cleaned_note = note.strip()

        if not cleaned_note:
            raise ValueError("记忆内容不能为空")

        # 防止路径所在目录不存在。
        self.path.parent.mkdir(parents=True, exist_ok=True)

        # MEMORY.md 第一次创建时补充标题。
        if not self.path.exists() or self.path.stat().st_size == 0:
            self.path.write_text(
                "# 项目长期记忆\n\n",
                encoding="utf-8",
            )

        with self.path.open("a", encoding="utf-8") as file:
            file.write(f"- {cleaned_note}\n")

        return cleaned_note

    def recall(self, query: str = "") -> str:
        """读取长期记忆。

        当前使用最简单的召回策略：
        不对 query 做筛选，直接返回完整 MEMORY.md。

        Args:
            query: 预留的查询参数，后续可扩展为关键词检索。

        Returns:
            MEMORY.md 的全部文本；文件不存在时返回空字符串。
        """
        del query

        if not self.path.exists():
            return ""

        return self.path.read_text(encoding="utf-8").strip()

    def clear(self) -> None:
        """清空全部长期记忆。

        主要用于测试，不建议由模型随意调用。
        """
        if self.path.exists():
            self.path.unlink()


class KVMemory:
    """基于 JSON 文件的结构化长期记忆。

    每条记忆由唯一 key 标识：
    - 相同 key 再次写入时，覆盖旧值；
    - forget() 可以根据 key 删除记忆；
    - 数据保存在项目根目录的 memory.json 中。
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = (
            Path(path)
            if path is not None
            else DEFAULT_KV_MEMORY_PATH
        )
        self.data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        """从 JSON 文件读取已有结构化记忆。"""
        if not self.path.exists():
            return {}

        raw_text = self.path.read_text(encoding="utf-8").strip()

        if not raw_text:
            return {}

        try:
            loaded_data = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"结构化记忆文件不是合法 JSON：{self.path}"
            ) from exc

        if not isinstance(loaded_data, dict):
            raise ValueError(
                f"结构化记忆文件的顶层必须是对象：{self.path}"
            )

        return loaded_data

    def _save(self) -> None:
        """把当前结构化记忆保存到 JSON 文件。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)

        content = json.dumps(
            self.data,
            ensure_ascii=False,
            indent=2,
        )

        # 先写入临时文件，再替换正式文件，
        # 避免写入中断时损坏原来的 memory.json。
        temp_path = self.path.with_suffix(
            self.path.suffix + ".tmp"
        )
        temp_path.write_text(content, encoding="utf-8")
        temp_path.replace(self.path)

    def remember(self, key: str, value: Any) -> Any:
        """写入或更新一条结构化记忆。

        相同 key 已存在时，新值会覆盖旧值。
        """
        cleaned_key = key.strip()

        if not cleaned_key:
            raise ValueError("记忆 key 不能为空")

        self.data[cleaned_key] = value
        self._save()

        return value

    def forget(self, key: str) -> bool:
        """根据 key 删除一条记忆。

        Returns:
            True：原来存在该 key，已经删除；
            False：原来不存在该 key。
        """
        cleaned_key = key.strip()

        if not cleaned_key:
            raise ValueError("记忆 key 不能为空")

        existed = cleaned_key in self.data
        self.data.pop(cleaned_key, None)

        if existed:
            self._save()

        return existed

    def recall(self, key: str | None = None) -> Any:
        """读取结构化记忆。

        不传 key 时返回全部记忆；
        传入 key 时只返回该 key 对应的值。
        """
        if key is None or not key.strip():
            return dict(self.data)

        return self.data.get(key.strip())