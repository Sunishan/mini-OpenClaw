"""搜索结果权威排序工具（authority_sort）。

从 web_search 中提取的独立权威评分和排序逻辑。
模型用 firecrawl_search 等工具完成搜索后，调用本工具对结果按权威性重排。
纯计算，不发起网络请求。
"""
from __future__ import annotations
import json
from urllib.parse import urlparse

from tools.base import Tool


# ── 权威域名列表（与搜索工具共享）─────────────────────────────
HIGH_AUTHORITY_DOMAINS: set[str] = {
    ".gov", ".gov.cn", ".gov.uk", ".gov.au", ".gov.sg", ".gov.in",
    ".gov.br", ".gov.hk", ".gov.tw", ".go.jp", ".go.kr", ".gouv.fr",
    ".gc.ca", ".govt.nz",
    ".edu", ".edu.cn", ".ac.uk", ".ac.cn",
    "gov.cn", "who.int", "un.org", "unicef.org", "worldbank.org",
    "imf.org", "oecd.org", "iea.org", "bloomberg.org",
    "wto.org", "ilo.org", "fao.org", "wmo.int", "ipcc.ch",
    "iaea.org", "unesco.org", "nato.int", "europa.eu", "ec.europa.eu",
    "canada.ca",
    "nasa.gov", "noaa.gov", "nih.gov", "cdc.gov", "nsf.gov",
    "fda.gov", "clinicaltrials.gov", "ncbi.nlm.nih.gov",
    "ema.europa.eu", "ecdc.europa.eu",
    "nhc.gov.cn", "samr.gov.cn", "mfa.gov.cn", "stats.gov.cn",
    "mot.gov.cn", "mwr.gov.cn", "cma.gov.cn", "mem.gov.cn",
    "mee.gov.cn", "moe.gov.cn",
    "sec.gov", "federalreserve.gov", "treasury.gov", "ecb.europa.eu",
    "bankofengland.co.uk", "bis.org", "pbc.gov.cn", "pboc.gov.cn",
    "csrc.gov.cn", "nfsa.gov.cn", "safe.gov.cn",
    "stanford.edu", "mit.edu", "harvard.edu", "ox.ac.uk",
    "cam.ac.uk", "tsinghua.edu.cn", "pku.edu.cn",
}

AUTHORITATIVE_MEDIA_DOMAINS: set[str] = {
    "reuters.com", "ap.org", "apnews.com", "bbc.com", "bbc.co.uk",
    "npr.org", "economist.com", "bloomberg.com", "ft.com", "wsj.com",
    "nytimes.com", "washingtonpost.com", "theguardian.com",
    "aljazeera.com", "dw.com", "france24.com", "nikkei.com",
    "scmp.com", "nature.com", "science.org", "nejm.org",
    "thelancet.com", "jamanetwork.com", "bmj.com",
    "nationalgeographic.com", "xinhuanet.com", "people.com.cn",
    "news.cn", "cctv.com", "chinanews.com.cn", "chinadaily.com.cn",
    "caixin.com", "thepaper.cn", "yicai.com",
}

LOW_AUTHORITY_DOMAINS: set[str] = {
    "infowars.com", "breitbart.com", "beforeitsnews.com",
    "naturalnews.com", "zerohedge.com",
}


# ── 核心函数 ────────────────────────────────────────────────


def _extract_domain(url: str) -> str:
    """从 URL 提取标准化域名。"""
    try:
        hostname = urlparse(url).hostname or ""
    except ValueError:
        return ""
    hostname = hostname.lower().strip(".")
    if hostname.startswith("www."):
        hostname = hostname[4:]
    return hostname


def _domain_matches(domain: str, patterns: set[str]) -> str:
    """检查域名是否匹配某个权威模式，返回匹配到的规则。"""
    if not domain:
        return ""
    ordered = sorted(patterns, key=lambda item: (item.startswith("."), -len(item)))
    for pattern in ordered:
        rule = pattern.lower()
        if rule.startswith("."):
            if domain.endswith(rule):
                return pattern
            continue
        if domain == rule or domain.endswith(f".{rule}"):
            return pattern
        # 也匹配子域名形式
        if f".{domain}" == rule:
            return pattern
    return ""


def _score_authority(domain: str) -> tuple[float, str, str, str]:
    """对域名进行权威评分。

    Returns:
        (score, tier, source_type, matched_rule)
    """
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


def _authority_sort(
    results: list,
    max_results: int = 10,
) -> str:
    """对搜索结果进行权威评分并排序。

    Args:
        results: 搜索结果的列表，每条应包含 url/title/description/snippet 等字段
        max_results: 最多返回的结果数（默认 10）

    Returns:
        JSON 字符串，包含按权威性排序后的结果
    """
    if not results:
        return json.dumps({
            "results_count": 0,
            "results": [],
            "authority_prioritized": True,
        }, ensure_ascii=False)

    scored: list[dict] = []
    seen_urls: set[str] = set()

    for item in results:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url", item.get("link", ""))).strip()
        if not url or url.lower() in seen_urls:
            continue
        seen_urls.add(url.lower())

        domain = _extract_domain(url)
        score, tier, source_type, rule = _score_authority(domain)

        scored.append({
            "title": item.get("title", ""),
            "url": url,
            "snippet": item.get("snippet", item.get("description", item.get("text", ""))),
            "domain": domain,
            "authority_score": score,
            "authority_tier": tier,
            "source_type": source_type,
            "matched_authority_rule": rule,
        })

    # 按权威分从高到低排序，同分保持原有顺序
    scored.sort(key=lambda x: (-x["authority_score"]))
    final = scored[:max_results]

    return json.dumps({
        "results_count": len(final),
        "results": final,
        "authority_prioritized": True,
    }, ensure_ascii=False)


# ── 构造 Tool 实例 ────────────────────────────────────────
authority_sort_tool = Tool(
    name="authority_sort",
    description=(
        "对搜索结果列表进行权威评分并按权威性从高到低排序。"
        "输入 firecrawl_search 或其他搜索工具返回的 results 列表，"
        "输出添加了 authority_score、authority_tier、source_type、"
        "matched_authority_rule 字段并按权威性重排的 JSON。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "description": "搜索结果列表，每条应包含 url、title、description/snippet 等字段",
                "items": {
                    "type": "object",
                },
            },
            "max_results": {
                "type": "integer",
                "description": "最多返回的结果数（默认 10）",
                "default": 10,
            },
        },
        "required": ["results"],
    },
    run=_authority_sort,
)
