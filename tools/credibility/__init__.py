"""可信度评估工具包注册。

提供 register_credibility_tools() 将全部 6 个工具注册到现有的 ToolRegistry 中。
"""
from __future__ import annotations
from tools.base import ToolRegistry
from tools.credibility.webpage_reader import webpage_reader_tool
from tools.credibility.claim_extractor import claim_extractor_tool
from tools.credibility.kb_retriever import kb_retriever_tool
from tools.credibility.cross_validator import cross_validator_tool
from tools.credibility.credibility_scorer import credibility_scorer_tool
from tools.credibility.report_generator import report_generator_tool


def register_credibility_tools(registry: ToolRegistry) -> None:
    """将所有可信度评估工具注册到指定的注册表中。"""
    for tool in (
        webpage_reader_tool,
        claim_extractor_tool,
        kb_retriever_tool,
        cross_validator_tool,
        credibility_scorer_tool,
        report_generator_tool,
    ):
        registry.register(tool)


def build_credibility_registry() -> ToolRegistry:
    """创建一个仅包含可信度评估工具的独立注册表。"""
    registry = ToolRegistry()
    register_credibility_tools(registry)
    return registry
