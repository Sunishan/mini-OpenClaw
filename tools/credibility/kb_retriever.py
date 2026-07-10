"""工具3：知识库检索工具（DuckDuckGo 搜索版）。

根据主张列表，使用 DuckDuckGo 搜索互联网获取相关证据。
支持中英文。不再依赖本地知识库文件或 Wikipedia。
"""
from __future__ import annotations
import json
import re
from typing import Any

from tools.base import Tool
from tools.credibility.models import ClaimEvidenceResult, to_json


def _search_duckduckgo(query: str, max_results: int) -> list[dict]:
    """调 DuckDuckGo 搜索，返回结果列表。"""
    from ddgs import DDGS

    results = []
    with DDGS() as ddgs:
        for i, r in enumerate(ddgs.text(query, max_results=max_results * 2)):
            if i >= max_results * 2:
                break
            results.append({
                "title": r.get("title", ""),
                "url": r.get("href", ""),
                "snippet": r.get("body", ""),
            })
    return results


def _extract_search_terms(claim_text: str) -> str:
    """从主张文本中提取核心搜索词。"""
    stopwords = frozenset({
        "的", "了", "在", "是", "我", "有", "和", "就", "不", "都", "也",
        "要", "让", "会", "可以", "已经", "正在", "这个", "那个", "这些",
        "the", "a", "an", "is", "are", "was", "were", "be", "been", "has",
        "have", "had", "do", "does", "did", "will", "would", "could", "may",
    })

    words = re.findall(r"[一-鿿\w]+", claim_text)
    keywords = [w for w in words if w.lower() not in stopwords and len(w) > 1]
    return " ".join(keywords[:8]) if keywords else claim_text[:100]


def _compute_relevance(claim_text: str, title: str, snippet: str) -> float:
    """粗略计算主张与搜索结果的关联度。"""
    score = 0.0

    claim_lower = claim_text.lower()
    title_lower = title.lower()

    title_words = set(re.findall(r"[一-鿿\w]+", title_lower))
    claim_words = set(re.findall(r"[一-鿿\w]+", claim_lower))

    if title_words and claim_words:
        overlap = title_words & claim_words
        jaccard = len(overlap) / max(len(title_words | claim_words), 1)
        score += jaccard * 0.5

    # 摘要匹配加分
    if snippet:
        for phrase in re.findall(r"[一-鿿]{3,}", claim_lower):
            if phrase in snippet.lower():
                score += 0.1

    return min(score, 1.0)


def _kb_retriever(claims: list[dict], top_k: int = 3) -> str:
    """核心函数：根据主张列表搜索互联网获取证据。

    claims: 来自 claim_extractor 输出的 claims 列表
    top_k: 每条主张最多返回的匹配结果数

    返回 JSON 字符串。
    """
    results: list[dict] = []

    for claim in claims:
        claim_id = claim.get("id", "")
        claim_text = claim.get("text", "")

        if not claim_text:
            results.append(ClaimEvidenceResult(
                claim_id=claim_id,
                matched=False,
                note="主张文本为空",
            ).to_dict())
            continue

        # 提取搜索词
        search_query = _extract_search_terms(claim_text)
        if not search_query:
            search_query = claim_text[:100]

        # 搜索 DuckDuckGo
        try:
            search_results = _search_duckduckgo(search_query, top_k)
        except ImportError:
            results.append(ClaimEvidenceResult(
                claim_id=claim_id,
                matched=False,
                note="缺少依赖 ddgs，请执行 pip install ddgs",
            ).to_dict())
            continue
        except Exception as e:
            results.append(ClaimEvidenceResult(
                claim_id=claim_id,
                matched=False,
                note=f"DuckDuckGo 搜索失败：{e}",
            ).to_dict())
            continue

        if not search_results:
            results.append(ClaimEvidenceResult(
                claim_id=claim_id,
                matched=False,
                note=f"未找到与「{search_query}」相关的内容",
            ).to_dict())
            continue

        # 构建证据列表
        evidence_list = []
        for r in search_results[:top_k]:
            title = r.get("title", "")
            url = r.get("url", "")
            snippet = r.get("snippet", "")
            relevance = _compute_relevance(claim_text, title, snippet)

            from tools.credibility.models import Evidence
            evidence_list.append(Evidence(
                source=f"{title} | {url}" if url else title,
                snippet=snippet[:500] + "..." if len(snippet) > 500 else snippet,
                relevance_score=round(relevance, 2),
                supports_claim=None,
            ))

        if not evidence_list:
            results.append(ClaimEvidenceResult(
                claim_id=claim_id,
                matched=False,
                note="搜索结果为空",
            ).to_dict())
            continue

        results.append(ClaimEvidenceResult(
            claim_id=claim_id,
            matched=True,
            evidence=evidence_list,
        ).to_dict())

    return json.dumps({
        "kb_name": "DuckDuckGo",
        "kb_entry_count": len(results),
        "results": results,
    }, ensure_ascii=False)


# ── 构造 Tool 实例 ────────────────────────────────────────
kb_retriever_tool = Tool(
    name="kb_retriever",
    description=(
        "搜索互联网获取与主张相关的证据。"
        "输入 claim_extractor 输出的主张列表，返回每个主张的匹配结果"
        "（标题、摘要、来源 URL、相关性分数）。"
        "使用 DuckDuckGo 搜索，不需要本地知识库文件或 API Key。"
        "返回 JSON 格式。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "claims": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "主张 ID，如 claim_1"},
                        "text": {"type": "string", "description": "主张文本"},
                        "claim_type": {"type": "string", "description": "主张类型"},
                        "confidence": {"type": "number", "description": "置信度"},
                    },
                    "required": ["id", "text"],
                },
                "description": "主张列表（来自 claim_extractor 输出的 claims 字段）",
            },
            "top_k": {
                "type": "integer",
                "description": "每条主张最多返回的搜索结果数（默认 3）",
                "default": 3,
            },
        },
        "required": ["claims"],
    },
    run=_kb_retriever,
)
