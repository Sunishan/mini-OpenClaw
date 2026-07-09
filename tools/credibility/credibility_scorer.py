"""工具5：可信度评分工具。

综合网页信号、来源信息和主张验证结果，输出可信度分数(0~1)和等级。
使用四维度加权评分模型。
"""
from __future__ import annotations
import json
import re

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
    ".gov", ".gov.cn", ".gov.uk", ".go.jp",
    # 教育
    ".edu", ".edu.cn", ".ac.uk", ".ac.cn",
    # 国际组织
    "who.int", "un.org", "unicef.org", "worldbank.org", "imf.org",
    "oecd.org", "iea.org", "bloomberg.org",
    # 权威媒体
    "reuters.com", "ap.org", "apnews.com", "bbc.com", "bbc.co.uk",
    "npr.org", "economist.com", "nature.com", "science.org",
    "nationalgeographic.com",
    # 权威研究机构
    "nasa.gov", "noaa.gov", "nih.gov", "cdc.gov", "nsf.gov",
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

    domain_lower = domain.lower()

    # 精确匹配优先
    if domain_lower in HIGH_AUTHORITY_DOMAINS:
        return 0.9
    if domain_lower in LOW_AUTHORITY_DOMAINS:
        return 0.1

    # 后缀匹配
    for high_domain in HIGH_AUTHORITY_DOMAINS:
        if high_domain.startswith(".") and domain_lower.endswith(high_domain):
            return 0.85
        if high_domain.startswith(".") and f".{domain_lower}".endswith(high_domain):
            return 0.85

    for low_domain in LOW_AUTHORITY_DOMAINS:
        if low_domain in domain_lower:
            return 0.1

    # 中性评估
    return 0.5


def _score_claim_verification(verdicts: list[dict]) -> float:
    """评估主张验证维度的分数。"""
    if not verdicts:
        return 0.5  # 无主张时取中性

    total = 0.0
    for v in verdicts:
        status = v.get("status", "unsupported")
        if status == "supported":
            total += 1.0
        elif status == "contradicted":
            total += 0.0
        elif status == "unsupported":
            total += 0.3
        elif status == "unverifiable":
            total += 0.5
    return total / len(verdicts)


def _score_transparency(meta: dict) -> float:
    """评估来源透明度。

    基于作者、日期、描述、内容长度四个指标。
    """
    score = 0.0
    details = []

    # 作者（权重 0.35）
    author = meta.get("author", "") or ""
    if author.strip():
        score += 1.0 * 0.35
        details.append(f"作者已找到：{author[:30]}")
    else:
        details.append("作者未标注")

    # 发布日期（权重 0.30）
    pub_date = meta.get("publication_date", "") or ""
    if pub_date.strip():
        score += 1.0 * 0.30
        details.append(f"发布日期已标注：{pub_date}")
    else:
        details.append("发布日期未标注")

    # 描述（权重 0.20）
    desc = meta.get("description", "") or ""
    if desc.strip() and len(desc) > 20:
        score += 1.0 * 0.20
        details.append("有详细页面描述")
    else:
        details.append("页面描述缺失或过短")

    # 内容长度（权重 0.15）
    word_count = meta.get("word_count", 0) or 0
    if word_count > 500:
        score += 1.0 * 0.15
        details.append(f"内容丰富（{word_count} 词）")
    elif word_count > 100:
        score += 0.5 * 0.15
        details.append(f"内容适中（{word_count} 词）")
    else:
        details.append(f"内容简短（{word_count} 词）")

    return round(score, 2)


def _score_content_quality(meta: dict) -> float:
    """评估内容质量。"""
    word_count = meta.get("word_count", 0) or 0
    text_content = meta.get("text_content", "") or ""

    # 字数评分（权重 0.5）
    length_score = min(word_count / 1000, 1.0)

    # 结构评分（权重 0.5）：检查是否有段落、列表等结构
    structure_score = 0.0
    if text_content:
        # 有段落（多个换行分隔的段落）
        paragraphs = [p.strip() for p in text_content.split("\n\n") if p.strip()]
        if len(paragraphs) >= 3:
            structure_score += 0.3
        elif len(paragraphs) >= 1:
            structure_score += 0.1

        # 有列表项
        if re.search(r"^[-*]\s", text_content, re.MULTILINE):
            structure_score += 0.2
        if re.search(r"^\d+\.\s", text_content, re.MULTILINE):
            structure_score += 0.2

        # 有引用或数据
        if re.search(r"\d+%|\$\d+|\d+\.\d+", text_content):
            structure_score += 0.15
        if re.search(r'"', text_content):
            structure_score += 0.15

    return round(length_score * 0.5 + min(structure_score, 0.5), 2)


def _credibility_scorer(verdicts: list[dict], page_metadata: dict) -> str:
    """核心函数：聚合所有信号计算可信度分数。

    返回 JSON 字符串（CredibilityResult 格式）。
    """
    # 信号 1：主张验证（权重 50%）
    cv_score = _score_claim_verification(verdicts)
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
            f"{n_unverifiable} 条无法判定"
        ),
    )

    # 信号 2：来源权威性（权重 20%）
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
    cq_score = _score_content_quality(page_metadata)
    cq_signal = SignalScore(
        weight=0.15,
        score=cq_score,
        details=f"内容质量评分：{cq_score:.2f}",
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
        "综合网页的多个信号（主张验证结果、域名权威性、来源透明度、内容质量）"
        "计算可信度分数（0~1）和等级标签（High/Medium/Low Credibility）。"
        "返回 JSON 格式，包含每项信号的权重和得分详情。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "verdicts": {
                "type": "array",
                "description": "交叉验证结果中的 verdicts 列表（来自 cross_validator 输出）",
                "items": {
                    "type": "object",
                    "properties": {
                        "claim_id": {"type": "string"},
                        "status": {"type": "string"},
                        "confidence": {"type": "number"},
                    },
                },
            },
            "page_metadata": {
                "type": "object",
                "description": "网页元数据（来自 webpage_reader 输出）",
                "properties": {
                    "url": {"type": "string"},
                    "domain": {"type": "string"},
                    "author": {"type": "string"},
                    "publication_date": {"type": "string"},
                    "word_count": {"type": "integer"},
                    "text_content": {"type": "string"},
                },
            },
        },
        "required": ["verdicts", "page_metadata"],
    },
    run=_credibility_scorer,
)
