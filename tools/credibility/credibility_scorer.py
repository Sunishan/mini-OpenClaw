"""工具5：可信度评分工具。

综合网页信号、来源信息和主张验证结果，输出可信度分数(0~1)和等级。
使用五维度加权评分模型。
"""
from __future__ import annotations
import json
import re
from urllib.parse import urlparse

from tools.base import Tool
from tools.credibility.models import (
    SignalScore, CredibilityResult, VerdictSummary, to_json,
)


# ── 配置常量 ──────────────────────────────────────────────
HIGH_CREDIBILITY_THRESHOLD = 0.70
MEDIUM_CREDIBILITY_THRESHOLD = 0.40

# 已知的高可信域名
HIGH_AUTHORITY_DOMAINS: set[str] = {
    # 政府
    ".gov", ".gov.cn", ".gov.uk", ".gov.au", ".gov.sg", ".gov.in",
    ".gov.br", ".gov.hk", ".gov.tw", ".go.jp", ".go.kr", ".gouv.fr",
    ".gc.ca", ".govt.nz", "gov.cn", "canada.ca",
    # 教育
    ".edu", ".edu.cn", ".ac.uk", ".ac.cn",
    # 国际组织
    "who.int", "un.org", "unicef.org", "worldbank.org", "imf.org",
    "oecd.org", "iea.org", "bloomberg.org", "wto.org", "ilo.org",
    "fao.org", "wmo.int", "ipcc.ch", "iaea.org", "unesco.org",
    "nato.int", "europa.eu", "ec.europa.eu",
    # 权威媒体
    "reuters.com", "ap.org", "apnews.com", "bbc.com", "bbc.co.uk",
    "npr.org", "economist.com", "bloomberg.com", "ft.com", "wsj.com",
    "nytimes.com", "washingtonpost.com", "theguardian.com",
    "aljazeera.com", "dw.com", "france24.com", "nikkei.com",
    "scmp.com", "nature.com", "science.org", "nejm.org",
    "thelancet.com", "jamanetwork.com", "bmj.com",
    "nationalgeographic.com", "people.com.cn", "xinhuanet.com",
    "news.cn", "cctv.com", "chinanews.com.cn", "chinadaily.com.cn",
    "caixin.com", "thepaper.cn", "yicai.com",
    # 权威研究机构
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

# 已知的低可信域名
LOW_AUTHORITY_DOMAINS: set[str] = {
    "infowars.com", "breitbart.com", "beforeitsnews.com",
    "naturalnews.com", "zerohedge.com",
}


def _get_domain_authority(domain: str) -> float:
    """根据域名评估来源权威性。

    返回 0.0 ~ 1.0 的分数。
    """
    if not domain:
        return 0.3  # 无域名信息

    domain_lower = _normalize_domain_value(domain)
    if not domain_lower:
        return 0.3

    # 精确匹配与子域名匹配
    ordered_high_domains = sorted(
        HIGH_AUTHORITY_DOMAINS,
        key=lambda item: (item.startswith("."), -len(item)),
    )
    for high_domain in ordered_high_domains:
        if domain_lower == high_domain:
            return 0.9
        if not high_domain.startswith(".") and domain_lower.endswith(f".{high_domain}"):
            return 0.9
        if high_domain.startswith(".") and (
            domain_lower == high_domain[1:] or domain_lower.endswith(high_domain)
        ):
            return 0.85

    ordered_low_domains = sorted(LOW_AUTHORITY_DOMAINS, key=lambda item: -len(item))
    for low_domain in ordered_low_domains:
        if domain_lower == low_domain:
            return 0.1
        if low_domain in domain_lower:
            return 0.1

    # 中性评估
    return 0.5


def _normalize_domain_value(value: str) -> str:
    """Normalize a domain field that may contain URLs or explanatory text."""
    raw = str(value or "").strip().lower()
    if not raw:
        return ""

    if "://" in raw:
        extracted = _extract_domain_from_url(raw)
        if extracted:
            return extracted

    # Accept values like "gov.cn 为中国政府官方域名" or "www.people.com.cn".
    match = re.search(r"([a-z0-9-]+(?:\.[a-z0-9-]+)+)", raw)
    if not match:
        return raw.strip(".")

    domain = match.group(1).strip(".")
    if domain.startswith("www."):
        domain = domain[4:]
    return domain


def _clamp_score(value: object, default: float = 0.5) -> float:
    try:
        score = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(0.0, min(score, 1.0))


def _extract_domain_from_url(url: str) -> str:
    try:
        hostname = urlparse(url).hostname or ""
    except ValueError:
        return ""
    hostname = hostname.lower().strip(".")
    if hostname.startswith("www."):
        hostname = hostname[4:]
    return hostname


def _iter_evidence_sources(verdict: dict) -> list[dict]:
    """Extract structured evidence source items from a verdict.

    The scorer accepts both the new `evidence_sources` field and the older
    model-facing `evidence` name so existing prompts can evolve gradually.
    """
    for key in ("evidence_sources", "evidence", "sources"):
        value = verdict.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _evidence_relation(source: dict, fallback_status: str) -> str:
    """Return support / contradict / neutral for one evidence item."""
    relation = str(
        source.get("relation")
        or source.get("status")
        or source.get("verdict")
        or ""
    ).lower()
    supports_claim = source.get("supports_claim")

    if supports_claim is True or relation in {"support", "supports", "supported"}:
        return "support"
    if supports_claim is False or relation in {
        "contradict",
        "contradicts",
        "contradicted",
        "refute",
        "refutes",
        "refuted",
    }:
        return "contradict"
    if relation in {"neutral", "related", "unverifiable"}:
        return "neutral"

    # If the model selected sources but omitted per-source relation, keep the
    # old verdict status as a calibrated fallback instead of discarding them.
    if fallback_status == "supported":
        return "support"
    if fallback_status == "contradicted":
        return "contradict"
    return "neutral"


def _score_evidence_source(source: dict) -> float:
    """Score one structured evidence source for authority."""
    if "authority_score" in source:
        return _clamp_score(source.get("authority_score"))

    source_type = str(source.get("source_type", "")).lower()
    if source_type in {"official_or_primary", "official", "primary", "government"}:
        return 0.95
    if source_type in {"authoritative_media", "research_or_data", "research", "data"}:
        return 0.90
    if source_type in {"low_credibility", "low"}:
        return 0.10
    if source_type in {"general_web", "general"}:
        return 0.50

    domain = str(source.get("domain") or "").strip()
    if not domain:
        url = str(source.get("url") or source.get("source") or "")
        domain = _extract_domain_from_url(url)
    return _get_domain_authority(domain) if domain else 0.50


def _status_score(status: str) -> float:
    """Fallback score when no structured external evidence is available."""
    if status == "supported":
        return 0.55
    if status == "contradicted":
        return 0.25
    if status == "unsupported":
        return 0.40
    if status == "unverifiable":
        return 0.5
    return 0.40


def _claim_importance_weight(verdict: dict) -> float:
    if "importance_weight" in verdict:
        return max(0.05, min(_clamp_score(verdict.get("importance_weight"), default=1.0), 1.0))

    role = str(verdict.get("claim_role", "core") or "core").lower()
    if role in {"core", "main", "headline", "thesis"}:
        return 1.0
    if role in {"key_detail", "key", "detail", "important_detail"}:
        return 0.70
    if role in {"background", "context"}:
        return 0.30
    if role in {"minor", "side", "supplement"}:
        return 0.15
    return 1.0


def _is_core_claim(verdict: dict) -> bool:
    role = str(verdict.get("claim_role", "core") or "core").lower()
    return role in {"core", "main", "headline", "thesis"}


def _score_evidence_relevance(source: dict) -> float:
    """Score claim-evidence semantic fit.

    Search results selected into a verdict are usually already relevant, so
    missing relevance/similarity is treated as a warm default instead of 0.5.
    """
    if "relevance_score" in source:
        return _clamp_score(source.get("relevance_score"), default=0.85)
    if "similarity_score" in source:
        return _clamp_score(source.get("similarity_score"), default=0.85)
    return 0.85


def _score_relation_confidence(source: dict) -> float:
    if "relation_confidence" in source:
        return _clamp_score(source.get("relation_confidence"), default=0.85)
    if "confidence" in source:
        return _clamp_score(source.get("confidence"), default=0.85)
    return 0.85


def _top_strength(scores: list[float]) -> float:
    if not scores:
        return 0.0
    top_scores = sorted(scores, reverse=True)[:3]
    return sum(top_scores) / len(top_scores)


def _score_verdict_with_evidence(verdict: dict) -> tuple[float, bool, int, int, int]:
    """Return (score, used_structured_evidence, source_count, high_count, low_count)."""
    status = verdict.get("status", "unsupported")
    sources = _iter_evidence_sources(verdict)
    if not sources:
        return _status_score(status), False, 0, 0, 0

    support_scores: list[float] = []
    contradict_scores: list[float] = []
    high_sources = 0
    low_sources = 0

    for source in sources:
        authority = _score_evidence_source(source)
        if authority >= 0.85:
            high_sources += 1
        if authority <= 0.30:
            low_sources += 1

        strength = (
            _score_evidence_relevance(source)
            * authority
            * _score_relation_confidence(source)
        )
        relation = _evidence_relation(source, status)
        if relation == "support":
            support_scores.append(strength)
        elif relation == "contradict":
            contradict_scores.append(strength)

    if not support_scores and not contradict_scores:
        return _status_score(status), True, len(sources), high_sources, low_sources

    support_strength = _top_strength(support_scores)
    contradict_strength = _top_strength(contradict_scores)
    score = 0.5 + 0.5 * (support_strength - contradict_strength)
    return (
        round(max(0.0, min(score, 1.0)), 4),
        True,
        len(sources),
        high_sources,
        low_sources,
    )


def _has_strong_core_contradiction(verdict: dict) -> bool:
    if not _is_core_claim(verdict):
        return False
    if verdict.get("status") != "contradicted":
        return False

    for source in _iter_evidence_sources(verdict):
        if _evidence_relation(source, "contradicted") != "contradict":
            continue
        authority = _score_evidence_source(source)
        relevance = _score_evidence_relevance(source)
        confidence = _score_relation_confidence(source)
        if authority >= 0.85 and relevance * confidence >= 0.60:
            return True
    return False


def _score_claim_verification(verdicts: list[dict]) -> tuple[float, str]:
    """评估主张验证维度的分数，把证据相关性和权威性计入验证强度。"""
    if not verdicts:
        return 0.5, "无主张验证结果，主张验证取中性分"

    weighted_total = 0.0
    weight_total = 0.0
    structured_verdicts = 0
    total_sources = 0
    high_sources = 0
    low_sources = 0
    strong_core_contradiction = False

    for verdict in verdicts:
        score, used_structured, source_count, high_count, low_count = _score_verdict_with_evidence(verdict)
        weight = _claim_importance_weight(verdict)
        weighted_total += score * weight
        weight_total += weight
        if used_structured:
            structured_verdicts += 1
        total_sources += source_count
        high_sources += high_count
        low_sources += low_count
        strong_core_contradiction = (
            strong_core_contradiction
            or _has_strong_core_contradiction(verdict)
        )

    overall = round(weighted_total / max(weight_total, 0.0001), 4)
    cap_applied = strong_core_contradiction and overall > 0.40
    if cap_applied:
        overall = 0.40

    details = (
        "主张验证分已按 claim 重要性、证据相关性、证据来源权威性和支持/反驳关系置信度加权；"
        f"{structured_verdicts}/{len(verdicts)} 条主张提供结构化证据来源，"
        f"共 {total_sources} 个证据来源，其中权威来源 {high_sources} 个、低可信来源 {low_sources} 个"
    )
    if structured_verdicts < len(verdicts):
        details += "；缺少结构化证据的主张使用旧版 status 规则兜底"
    if cap_applied:
        details += "；检测到核心主张被高权威证据反驳，主张验证分已封顶为 0.40"
    return overall, details


def _score_transparency(meta: dict) -> float:
    """评估来源透明度。

    基于作者/来源、日期、描述/标题、内容长度四个指标。
    """
    score = 0.0
    details = []

    # 作者或明确来源（权重 0.35）
    author = meta.get("author", "") or ""
    source = (
        meta.get("source", "")
        or meta.get("publisher", "")
        or meta.get("site_name", "")
        or ""
    )
    if author.strip():
        score += 1.0 * 0.35
        details.append(f"作者已找到：{author[:30]}")
    elif source.strip():
        score += 1.0 * 0.35
        details.append(f"来源已标注：{source[:30]}")
    else:
        details.append("作者或来源未标注")

    # 发布日期（权重 0.30）
    pub_date = meta.get("publication_date", "") or ""
    if pub_date.strip():
        score += 1.0 * 0.30
        details.append(f"发布日期已标注：{pub_date}")
    else:
        details.append("发布日期未标注")

    # 描述或标题（权重 0.20）
    desc = meta.get("description", "") or ""
    title = meta.get("title", "") or ""
    if desc.strip() and len(desc) > 20:
        score += 1.0 * 0.20
        details.append("有详细页面描述")
    elif title.strip() and len(title) > 8:
        score += 1.0 * 0.20
        details.append("有明确页面标题")
    else:
        details.append("页面描述和标题缺失或过短")

    # 内容长度（权重 0.15）
    word_count = meta.get("word_count", 0) or 0
    text_content = meta.get("text_content", "") or ""
    cjk_chars = len(re.findall(r"[\u4e00-\u9fff]", text_content))
    if word_count > 500 or cjk_chars > 800:
        score += 1.0 * 0.15
        details.append(f"内容丰富（{word_count} 词，{cjk_chars} 个中文字符）")
    elif word_count > 100 or cjk_chars > 200:
        score += 0.5 * 0.15
        details.append(f"内容适中（{word_count} 词，{cjk_chars} 个中文字符）")
    else:
        details.append(f"内容简短（{word_count} 词，{cjk_chars} 个中文字符）")

    return round(score, 2)


def _count_pattern_terms(text: str, patterns: list[str]) -> int:
    return sum(len(re.findall(pattern, text, flags=re.IGNORECASE)) for pattern in patterns)


def _split_sentences(text: str) -> list[str]:
    sentences = re.split(r"(?<=[。！？!?；;])\s*", text)
    return [sentence.strip() for sentence in sentences if sentence.strip()]


def _is_factcheck_contrast(context: str) -> bool:
    """Detect normal fact-check framing, not author self-contradiction."""
    rumor_or_claim = re.search(r"网传|传言|声称|宣称|截图称|消息称|有消息称", context)
    verification = re.search(
        r"记者|查询|检索|核实|暂未(?:看到|发现|确认)|未见|未附|未提供|官方(?:回应|辟谣|否认)",
        context,
    )
    contrast = re.search(r"但|但是|不过|然而|截至|目前", context)
    return bool(rumor_or_claim and verification and contrast)


def _count_nearby_contradictions(text: str, contradiction_pairs: list[tuple[str, str]]) -> int:
    sentences = _split_sentences(text)
    hits = 0
    for positive, negative in contradiction_pairs:
        found = False
        for index, sentence in enumerate(sentences):
            contexts = [sentence]
            if index + 1 < len(sentences):
                contexts.append(sentence + " " + sentences[index + 1])
            for context in contexts:
                if re.search(positive, context) and re.search(negative, context):
                    if _is_factcheck_contrast(context):
                        continue
                    found = True
                    break
            if found:
                break
        if found:
            hits += 1
    return hits


def _score_content_quality(meta: dict) -> tuple[float, str]:
    """评估内容质量：重点检查内部矛盾、逻辑风险和模糊表述。"""
    text_content = meta.get("text_content", "") or ""
    title = meta.get("title", "") or ""
    text = f"{title}\n{text_content}".strip()

    if not text:
        return 0.20, "正文为空，无法评估内部一致性"

    cjk_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
    total_chars = max(len(text), cjk_chars, 1)
    score = 1.0
    details: list[str] = []

    contradiction_pairs = [
        (r"已(?:经)?(?:确认|证实)|确认(?:了)?", r"未(?:经)?证实|尚未证实|无法证实|暂未确认"),
        (r"官方(?:公告|通报|发布|确认)|正式(?:公告|发布)", r"暂无.*(?:公告|通报)|未见.*(?:公告|通报)|没有.*(?:公告|通报)"),
        (r"已(?:经)?(?:完成|发生|造成)|已于", r"预计|或将|可能会|有望"),
        (r"全部|所有|全面|全市|全国", r"部分|个别|局部|试点|少数"),
        (r"无影响|不影响|无需担心|无需恐慌", r"存在风险|可能影响|担心|异常|隐患"),
    ]
    contradiction_hits = _count_nearby_contradictions(text, contradiction_pairs)
    if contradiction_hits:
        penalty = min(0.30, contradiction_hits * 0.12)
        score -= penalty
        details.append(f"检测到 {contradiction_hits} 类近邻内部矛盾/逻辑冲突，扣 {penalty:.2f}")

    vague_patterns = [
        r"网传", r"据传", r"据说", r"传言", r"疑似", r"可能", r"或许", r"或将",
        r"有消息称", r"知情人士", r"内部人士", r"相关人士", r"未经证实",
        r"暂未确认", r"不排除", r"大概率", r"基本确定", r"或与.*有关",
    ]
    vague_count = _count_pattern_terms(text, vague_patterns)
    vague_density = vague_count / max(total_chars / 500, 1.0)
    if vague_density > 2:
        penalty = min(0.30, (vague_density - 2) * 0.08)
        score -= penalty
        details.append(f"模糊/不确定表述密度偏高（{vague_count} 处），扣 {penalty:.2f}")
    elif vague_count:
        details.append(f"有少量模糊/不确定表述（{vague_count} 处），未明显扣分")

    absolute_patterns = [
        r"绝对", r"百分百", r"100%", r"一定", r"必然", r"肯定", r"实锤",
        r"坐实", r"彻底", r"完全证实", r"无需核实",
    ]
    absolute_count = _count_pattern_terms(text, absolute_patterns)
    if absolute_count:
        penalty = min(0.15, absolute_count * 0.04)
        score -= penalty
        details.append(f"存在绝对化/过度确定表述（{absolute_count} 处），扣 {penalty:.2f}")

    injection_patterns = [
        r"忽略.*(?:指令|要求)", r"不要搜索", r"不要质疑", r"直接判定",
        r"删除.*证据", r"系统指令", r"root-helper",
    ]
    injection_count = _count_pattern_terms(text, injection_patterns)
    if injection_count:
        penalty = min(0.45, injection_count * 0.15)
        score -= penalty
        details.append(f"检测到疑似提示注入/诱导性指令（{injection_count} 处），扣 {penalty:.2f}")

    if total_chars < 80:
        score -= 0.10
        details.append("正文过短，内部一致性证据不足，扣 0.10")

    final_score = round(max(0.0, min(score, 1.0)), 2)
    if not details:
        details.append("未发现明显内部矛盾、逻辑冲突或高密度模糊表述")
    return final_score, "；".join(details)


def _score_content_quality_from_assessment(assessment: object) -> tuple[float, str] | None:
    """Use model-provided structured content quality assessment when valid."""
    if not isinstance(assessment, dict):
        return None
    if "score" not in assessment:
        return None

    score = _clamp_score(assessment.get("score"), default=0.5)
    details: list[str] = ["使用大模型结构化内容质量评估"]

    rationale = str(assessment.get("rationale") or "").strip()
    if rationale:
        details.append(rationale[:180])

    issue_fields = [
        ("internal_contradictions", "内部矛盾"),
        ("logic_issues", "逻辑问题"),
        ("vague_language_examples", "模糊表述"),
        ("prompt_injection_signals", "提示注入风险"),
    ]
    for field, label in issue_fields:
        value = assessment.get(field)
        if isinstance(value, list) and value:
            details.append(f"{label} {len(value)} 处")

    vague_level = str(assessment.get("vague_language_level") or "").strip()
    if vague_level:
        details.append(f"模糊表述等级：{vague_level}")

    return round(score, 2), "；".join(details)


def _credibility_scorer(
    verdicts: list[dict] | None = None,
    page_metadata: dict | None = None,
) -> str:
    """核心函数：聚合所有信号计算可信度分数。

    返回 JSON 字符串（CredibilityResult 格式）。
    """
    if verdicts is None or page_metadata is None:
        return to_json({
            "error": "missing_required_arguments",
            "tool": "credibility_scorer",
            "required": ["verdicts", "page_metadata"],
            "hint": (
                "请把交叉验证后的 verdicts 列表和 webpage_reader 返回的 page_metadata "
                "作为参数传入；不能空参数调用本工具。"
            ),
            "example": {
                "verdicts": [
                    {
                        "claim_id": "claim_1",
                        "claim_text": "可验证主张",
                        "claim_role": "core",
                        "importance_weight": 1.0,
                        "status": "supported",
                        "confidence": 0.8,
                        "evidence_summary": "证据摘要",
                        "evidence_sources": [
                            {
                                "title": "证据标题",
                                "url": "https://example.gov/report",
                                "domain": "example.gov",
                                "source_type": "official_or_primary",
                                "authority_score": 0.95,
                                "relevance_score": 0.85,
                                "relation": "support",
                                "relation_confidence": 0.9,
                                "supports_claim": True,
                            }
                        ],
                    }
                ],
                "page_metadata": {
                    "url": "https://example.com/news",
                    "domain": "example.com",
                    "author": "",
                    "publication_date": "",
                    "word_count": 0,
                    "text_content": "",
                },
            },
        })
    if not isinstance(verdicts, list) or not isinstance(page_metadata, dict):
        return to_json({
            "error": "invalid_arguments",
            "tool": "credibility_scorer",
            "hint": "verdicts 必须是数组，page_metadata 必须是对象。",
            "received_types": {
                "verdicts": type(verdicts).__name__,
                "page_metadata": type(page_metadata).__name__,
            },
        })

    # 信号 1：主张验证（权重 50%，内部已按证据相关性和证据来源权威性加权）
    cv_score, cv_details = _score_claim_verification(verdicts)
    n_claims = len(verdicts)
    n_supported = sum(1 for v in verdicts if v.get("status") == "supported")
    n_contradicted = sum(1 for v in verdicts if v.get("status") == "contradicted")
    n_unsupported = sum(1 for v in verdicts if v.get("status") == "unsupported")
    n_unverifiable = sum(1 for v in verdicts if v.get("status") == "unverifiable")
    cv_signal = SignalScore(
        weight=0.50,
        score=cv_score,
        details=(
            f"共 {n_claims} 条主张：{n_supported} 条支持, "
            f"{n_contradicted} 条反驳, {n_unsupported} 条无证据, "
            f"{n_unverifiable} 条无法判定。{cv_details}"
        ),
    )

    # 信号 2：原网页域名权威性（权重 20%）
    domain = page_metadata.get("domain", "")
    da_score = _get_domain_authority(domain)
    da_signal = SignalScore(
        weight=0.20,
        score=da_score,
        details=f"域名 {domain} 的权威性评分：{da_score:.2f}",
    )

    # 信号 3：来源透明度（权重 15%）
    st_score = _score_transparency(page_metadata)
    st_signal = SignalScore(
        weight=0.15,
        score=st_score,
        details=f"来源透明度评分：{st_score:.2f}",
    )

    # 信号 4：内容质量（权重 15%）
    assessed_content_quality = _score_content_quality_from_assessment(
        page_metadata.get("content_quality_assessment")
    )
    if assessed_content_quality is not None:
        cq_score, cq_details = assessed_content_quality
    else:
        cq_score, cq_details = _score_content_quality(page_metadata)
    cq_signal = SignalScore(
        weight=0.15,
        score=cq_score,
        details=cq_details,
    )

    # 综合计算
    overall = (
        cv_signal.score * cv_signal.weight
        + da_signal.score * da_signal.weight
        + st_signal.score * st_signal.weight
        + cq_signal.score * cq_signal.weight
    )
    overall = round(overall, 4)

    # 等级标签
    if overall >= HIGH_CREDIBILITY_THRESHOLD:
        label = "High Credibility"
    elif overall >= MEDIUM_CREDIBILITY_THRESHOLD:
        label = "Medium Credibility"
    else:
        label = "Low Credibility"

    # 判定汇总
    summary = VerdictSummary(
        supported=n_supported,
        contradicted=n_contradicted,
        unsupported=n_unsupported,
        unverifiable=n_unverifiable,
    )

    result = CredibilityResult(
        overall_score=overall,
        score_label=label,
        signals={
            "claim_verification": cv_signal,
            "domain_authority": da_signal,
            "source_transparency": st_signal,
            "content_quality": cq_signal,
        },
        domain=domain,
        url=page_metadata.get("url", ""),
        verdict_summary=summary,
    )

    return to_json(result)


# ── 构造 Tool 实例 ────────────────────────────────────────
credibility_scorer_tool = Tool(
    name="credibility_scorer",
    description=(
        "综合网页的多个信号（主张验证结果、原网页域名权威性、来源透明度、内容质量）。"
        "主张验证结果内部会按证据相关性、证据来源权威性和支持/反驳关系置信度加权。"
        "计算可信度分数（0~1）和等级标签（High/Medium/Low Credibility）。"
        "返回 JSON 格式，包含每项信号的权重和得分详情。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "verdicts": {
                "type": "array",
                "description": "交叉验证结果中的 verdicts 列表。每条 verdict 可包含 evidence_sources，用于加权主张验证分",
                "items": {
                    "type": "object",
                    "properties": {
                        "claim_id": {"type": "string"},
                        "claim_text": {"type": "string"},
                        "claim_role": {
                            "type": "string",
                            "description": "主张角色：core/key_detail/background/minor。核心主张权重最高，背景信息权重较低",
                        },
                        "importance_weight": {
                            "type": "number",
                            "description": "主张重要性权重，建议 core=1.0, key_detail=0.7, background=0.3, minor=0.15",
                        },
                        "status": {"type": "string"},
                        "confidence": {"type": "number"},
                        "evidence_summary": {"type": "string"},
                        "evidence_sources": {
                            "type": "array",
                            "description": "支持或反驳该主张的结构化证据来源，建议直接使用 web_search 返回的权威字段，并补充 relevance_score/relation_confidence",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "title": {"type": "string"},
                                    "url": {"type": "string"},
                                    "domain": {"type": "string"},
                                    "source_type": {"type": "string"},
                                    "authority_score": {"type": "number"},
                                    "relevance_score": {"type": "number"},
                                    "similarity_score": {"type": "number"},
                                    "relation": {"type": "string"},
                                    "relation_confidence": {"type": "number"},
                                    "supports_claim": {"type": "boolean"},
                                },
                            },
                        },
                    },
                },
            },
            "page_metadata": {
                "type": "object",
                "description": "网页元数据（来自 webpage_reader 输出）",
                "properties": {
                    "url": {"type": "string"},
                    "domain": {"type": "string"},
                    "title": {"type": "string"},
                    "source": {"type": "string"},
                    "publisher": {"type": "string"},
                    "author": {"type": "string"},
                    "publication_date": {"type": "string"},
                    "word_count": {"type": "integer"},
                    "text_content": {"type": "string"},
                    "content_quality_assessment": {
                        "type": "object",
                        "description": "可选。模型按固定 rubric 对原文内容质量做出的结构化评估；缺失时 scorer 使用规则兜底",
                        "properties": {
                            "score": {"type": "number"},
                            "internal_contradictions": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "logic_issues": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "vague_language_level": {"type": "string"},
                            "vague_language_examples": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "prompt_injection_signals": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "rationale": {"type": "string"},
                        },
                    },
                },
            },
        },
        "required": ["verdicts", "page_metadata"],
    },
    run=_credibility_scorer,
)
