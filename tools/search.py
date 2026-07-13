"""网页搜索工具（web_search）。

让模型可以搜索互联网，获取相关页面列表（标题 + URL + 摘要），
再配合 webpage_reader 抓取具体内容。
默认启用权威来源优先搜索，使用 DuckDuckGo（免费，无需 API Key，国内可用）。
"""
from __future__ import annotations
import json
from urllib.parse import urlparse

from tools.base import Tool


HIGH_AUTHORITY_DOMAINS: set[str] = {
    ".gov", ".gov.cn", ".gov.uk", ".go.jp",
    ".edu", ".edu.cn", ".ac.uk", ".ac.cn",
    "gov.cn", "who.int", "un.org", "unicef.org", "worldbank.org",
    "imf.org", "oecd.org", "iea.org",
    "nasa.gov", "noaa.gov", "nih.gov", "cdc.gov", "nsf.gov",
    "nhc.gov.cn", "samr.gov.cn", "mfa.gov.cn", "stats.gov.cn",
    "stanford.edu", "mit.edu", "harvard.edu", "ox.ac.uk",
    "cam.ac.uk", "tsinghua.edu.cn", "pku.edu.cn",
}

AUTHORITATIVE_MEDIA_DOMAINS: set[str] = {
    "reuters.com", "ap.org", "apnews.com", "bbc.com", "bbc.co.uk",
    "npr.org", "economist.com", "nature.com", "science.org",
    "nationalgeographic.com", "xinhuanet.com", "people.com.cn",
    "cctv.com",
}

LOW_AUTHORITY_DOMAINS: set[str] = {
    "infowars.com", "breitbart.com", "beforeitsnews.com",
    "naturalnews.com", "zerohedge.com",
}


def _extract_domain(url: str) -> str:
    """Extract a normalized hostname from a URL."""
    try:
        hostname = urlparse(url).hostname or ""
    except ValueError:
        return ""
    hostname = hostname.lower().strip(".")
    if hostname.startswith("www."):
        hostname = hostname[4:]
    return hostname


def _domain_matches(domain: str, patterns: set[str]) -> str:
    """Return the matched authority rule, or an empty string."""
    if not domain:
        return ""
    ordered_patterns = sorted(
        patterns,
        key=lambda item: (item.startswith("."), -len(item)),
    )
    for pattern in ordered_patterns:
        rule = pattern.lower()
        if rule.startswith("."):
            if domain.endswith(rule):
                return pattern
            continue
        if domain == rule or domain.endswith(f".{rule}"):
            return pattern
    return ""


def _score_authority(domain: str) -> tuple[float, str, str, str]:
    """Score a result domain for evidence search ordering."""
    low_rule = _domain_matches(domain, LOW_AUTHORITY_DOMAINS)
    if low_rule:
        return 0.10, "low", "low_credibility", low_rule

    official_rule = _domain_matches(domain, HIGH_AUTHORITY_DOMAINS)
    if official_rule:
        return 0.95, "high", "official_or_primary", official_rule

    media_rule = _domain_matches(domain, AUTHORITATIVE_MEDIA_DOMAINS)
    if media_rule:
        source_type = (
            "research_or_data"
            if media_rule in {"nature.com", "science.org"}
            else "authoritative_media"
        )
        return 0.90, "high", source_type, media_rule

    if not domain:
        return 0.30, "unknown", "unknown", ""
    return 0.50, "medium", "general_web", ""


def _build_authority_queries(query: str) -> list[str]:
    """Build staged authority-focused queries while preserving the original query."""
    queries = [
        query,
        f"{query} 官方 公告 回应 site:gov.cn",
        f"{query} site:gov.cn",
        f"{query} site:edu.cn OR site:ac.cn",
        f"{query} site:who.int OR site:un.org OR site:oecd.org",
        f"{query} Reuters OR AP OR BBC",
    ]

    deduped: list[str] = []
    seen: set[str] = set()
    for item in queries:
        key = item.strip().lower()
        if key and key not in seen:
            seen.add(key)
            deduped.append(item.strip())
    return deduped


def _search_once(ddgs: object, query: str, region: str, max_results: int) -> list[dict]:
    results = []
    for i, result in enumerate(ddgs.text(query, region=region, max_results=max_results)):  # type: ignore[attr-defined]
        if i >= max_results:
            break
        results.append({
            "title": result.get("title", ""),
            "url": result.get("href", ""),
            "snippet": result.get("body", ""),
            "source": "DuckDuckGo",
        })
    return results


def _web_search(
    query: str,
    max_results: int = 5,
    region: str = "wt-wt",
) -> str:
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
        final_limit = max(1, min(int(max_results or 5), 10))
        with DDGS() as ddgs:
            seen_urls: set[str] = set()
            ranked: list[dict] = []
            queries = _build_authority_queries(query)
            per_query_limit = max(final_limit, 5)
            order = 0
            for search_query in queries:
                for item in _search_once(ddgs, search_query, region, per_query_limit):
                    url = item.get("url", "")
                    key = url.strip().lower()
                    if not key or key in seen_urls:
                        continue
                    seen_urls.add(key)
                    domain = _extract_domain(url)
                    score, tier, source_type, rule = _score_authority(domain)
                    item.update({
                        "domain": domain,
                        "authority_score": score,
                        "authority_tier": tier,
                        "source_type": source_type,
                        "matched_authority_rule": rule,
                        "matched_query": search_query,
                        "_order": order,
                    })
                    order += 1
                    ranked.append(item)
            ranked.sort(key=lambda item: (-item["authority_score"], item["_order"]))
            results = []
            for item in ranked[:final_limit]:
                item.pop("_order", None)
                results.append(item)

        if not results:
            return json.dumps({
                "query": query,
                "results_count": 0,
                "results": [],
                "note": f"未找到与「{query}」相关的搜索结果",
            }, ensure_ascii=False)

        payload = {
            "query": query,
            "results_count": len(results),
            "results": results,
            "authority_prioritized": True,
        }
        return json.dumps(payload, ensure_ascii=False)

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
        "支持中英文。默认优先搜索权威来源，会自动扩展官方/权威媒体查询并按来源权威性重排结果。"
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
