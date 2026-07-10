"""网页搜索工具（web_search）。

让模型可以搜索互联网，获取相关页面列表（标题 + URL + 摘要），
再配合 webpage_reader 抓取具体内容。
使用 DuckDuckGo（免费，无需 API Key，国内可用）。
"""
from __future__ import annotations
import json

from tools.base import Tool


def _web_search(query: str, max_results: int = 5, region: str = " wt-wt") -> str:
    """核心函数：搜索互联网并返回结果列表。

    使用 DuckDuckGo 搜索，支持中英文。
    返回 JSON 字符串。
    """
    if not query or not query.strip():
        return json.dumps({
            "query": query,
            "results_count": 0,
            "results": [],
            "error": "搜索词为空",
        }, ensure_ascii=False)

    query = query.strip()

    try:
        from ddgs import DDGS
    except ImportError:
        return json.dumps({
            "query": query,
            "results_count": 0,
            "results": [],
            "error": "缺少依赖 ddgs，请执行 pip install ddgs",
        }, ensure_ascii=False)

    try:
        results = []
        with DDGS() as ddgs:
            for i, r in enumerate(ddgs.text(query, region=region, max_results=max_results)):
                if i >= max_results:
                    break
                results.append({
                    "title": r.get("title", ""),
                    "url": r.get("href", ""),
                    "snippet": r.get("body", ""),
                    "source": "DuckDuckGo",
                })

        if not results:
            return json.dumps({
                "query": query,
                "results_count": 0,
                "results": [],
                "note": f"未找到与「{query}」相关的搜索结果",
            }, ensure_ascii=False)

        return json.dumps({
            "query": query,
            "results_count": len(results),
            "results": results,
        }, ensure_ascii=False)

    except Exception as e:
        return json.dumps({
            "query": query,
            "results_count": 0,
            "results": [],
            "error": f"DuckDuckGo 搜索失败：{e}",
        }, ensure_ascii=False)


# ── 构造 Tool 实例 ────────────────────────────────────────
web_search_tool = Tool(
    name="web_search",
    description=(
        "搜索互联网，返回相关页面列表（标题、URL、摘要片段、来源）。"
        "支持中英文。用 DuckDuckGo 搜索。"
        "得到结果后可用 webpage_reader 抓取具体页面内容。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索关键词，越精确越好",
            },
            "max_results": {
                "type": "integer",
                "description": "最多返回的结果数（默认 5，最大 10）",
                "default": 5,
            },
            "region": {
                "type": "string",
                "description": "搜索地区，默认 wt-wt（全球），中文用 cn-zh",
                "default": "wt-wt",
            },
        },
        "required": ["query"],
    },
    run=_web_search,
)
