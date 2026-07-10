"""Skills 加载器（Day9）。

Skill 与 Tool 的区别：
  - Tool 是一次函数调用（read 一个文件）。
  - Skill 是一包"领域知识 + 操作流程 + 可选脚本/资源"，用一个 SKILL.md 描述，
    在合适的时候被加载进上下文，告诉模型"面对这类任务该怎么一步步做"。

SKILL.md 结构（约定）：
  ---
  name: pdf-report
  description: 一句话说明何时该用这个 skill（用于召回判断）
  ---
  正文：步骤、注意事项、可调用的脚本路径、示例。

加载器要做：扫描 skills/ 下每个含 SKILL.md 的目录，解析 frontmatter，
按需把正文注入系统提示词 / 作为可发现的能力清单。
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tools.base import Tool


@dataclass
class Skill:
    name: str
    description: str
    body: str
    path: Path


def parse_skill_md(text: str, path: Path) -> Skill:
    """解析最小 YAML frontmatter：name / description + 正文。

    这里刻意不用 PyYAML，避免为 Day9 skill 加新依赖。
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError(f"{path} 缺少 frontmatter 起始标记 ---")

    end = None
    for i, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            end = i
            break
    if end is None:
        raise ValueError(f"{path} 缺少 frontmatter 结束标记 ---")

    meta: dict[str, str] = {}
    for line in lines[1:end]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            raise ValueError(f"{path} frontmatter 行格式错误：{line}")
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip().strip("\"'")

    name = meta.get("name", "").strip()
    description = meta.get("description", "").strip()
    if not name:
        raise ValueError(f"{path} 缺少 name")
    if not description:
        raise ValueError(f"{path} 缺少 description")

    body = "\n".join(lines[end + 1:]).strip()
    return Skill(name=name, description=description, body=body, path=path)


def load_skills(root: str = "skills") -> list[Skill]:
    """扫描 root 下所有 SKILL.md。"""
    skills: list[Skill] = []
    for md in sorted(Path(root).glob("*/SKILL.md")):
        if md.parent.name.startswith("example"):
            continue
        skills.append(parse_skill_md(md.read_text(encoding="utf-8"), md))
    return skills


def skills_catalog(skills: list[Skill]) -> str:
    """生成给模型看的可用 skill 清单（name + description），用于按需召回。"""
    if not skills:
        return "当前没有可用 Skills。"
    return "\n".join(f"- {s.name}: {s.description}" for s in skills)


def render_skill_catalog(skills: list[Skill]) -> str:
    """渲染系统提示词中的 skill catalog。"""
    return (
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "可用 Skills（按需加载）\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "当用户任务与某个 skill 描述匹配时，先调用 load_skill(name) 读取完整说明，"
        "再按该 skill 执行。\n"
        f"{skills_catalog(skills)}"
    )


def make_load_skill_tool(skills: list[Skill]) -> Tool:
    """构造 load_skill 工具，让模型自主加载某个 skill 的完整正文。"""
    index = {s.name: s for s in skills}

    def _load_skill(name: str) -> str:
        skill = index.get(name)
        if skill is None:
            available = ", ".join(index) or "(无)"
            return f"[失败] 未找到 skill={name}。可用 skills: {available}"
        return (
            f"# Skill: {skill.name}\n\n"
            f"Description: {skill.description}\n\n"
            f"Source: {skill.path}\n\n"
            f"{skill.body}"
        )

    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "要加载的 skill 名称。",
                "enum": list(index),
            },
        },
        "required": ["name"],
    }

    return Tool(
        name="load_skill",
        description="按名称加载一个本地 Skill 的完整说明。任务匹配某个 Skill 时，应先调用本工具再执行。",
        parameters=parameters,
        run=_load_skill,
    )
