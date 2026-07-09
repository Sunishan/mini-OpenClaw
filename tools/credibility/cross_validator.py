"""工具4：交叉验证工具。

将主张与检索到的证据对比，判定每条主张的状态。
使用数字容忍度、关键词重叠、否定词检测等确定性算法。
"""
from __future__ import annotations
import json
import re

from tools.base import Tool
from tools.credibility.models import Verdict, VerdictSummary, to_json


def _extract_numbers(text: str) -> list[tuple[float, str]]:
    """从文本中提取数值及其可能的单位。"""
    results: list[tuple[float, str]] = []
    # 匹配金额：$X, X美元, X元
    for m in re.finditer(r"\$\s*([\d,]+(?:\.\d+)?)", text):
        try:
            val = float(m.group(1).replace(",", ""))
            results.append((val, "USD"))
        except ValueError:
            pass

    for m in re.finditer(r"([\d,]+(?:\.\d+)?)\s*(?:美元|元|€|¥)", text):
        try:
            val = float(m.group(1).replace(",", ""))
            results.append((val, "CNY"))
        except ValueError:
            pass

    # 匹配百分比
    for m in re.finditer(r"([\d,]+(?:\.\d+)?)\s*%", text):
        try:
            val = float(m.group(1).replace(",", ""))
            results.append((val, "%"))
        except ValueError:
            pass

    # 匹配普通数字（带可能的量词）
    for m in re.finditer(
        r"(?<!\w)([\d,]+(?:\.\d+)?)\s*(?:million|billion|trillion|万|亿|"
        r"GW|MW|kW|km|m|cm|inches|ppm|years|岁|人|次|家|例|户|元|美元|年|月|日|%)(?!\w)",
        text, re.IGNORECASE
    ):
        try:
            val = float(m.group(1).replace(",", ""))
            results.append((val, m.group(2) if len(m.groups()) > 1 else ""))
        except ValueError:
            pass

    # 匹配所有数字（兜底）
    for m in re.finditer(r"(?<!\w)(\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+\.\d+)(?!\w)", text):
        try:
            val = float(m.group(1).replace(",", ""))
            results.append((val, ""))
        except ValueError:
            pass

    return results


def _significant_terms(text: str) -> set[str]:
    """提取文本中的有意义的词（纯英文）。"""
    text = text.lower()
    words = re.findall(r"[a-z]+(?:'[a-z]+)?", text)
    stopwords = frozenset({
        "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "shall", "can", "need", "to", "of", "in",
        "for", "on", "with", "at", "by", "from", "as", "into", "through",
        "during", "before", "after", "above", "below", "between", "out",
        "off", "over", "under", "again", "further", "then", "once", "here",
        "there", "when", "where", "why", "how", "all", "each", "every",
        "both", "few", "more", "most", "other", "some", "such", "no", "nor",
        "not", "only", "own", "same", "so", "than", "too", "very", "just",
        "because", "and", "but", "or", "if", "while", "although", "this",
        "that", "these", "those", "it", "its", "they", "them", "their",
        "we", "us", "our", "you", "your", "he", "she", "him", "her", "his",
        "what", "which", "who", "whom",
    })
    return {w for w in words if w not in stopwords and len(w) > 2}


def _jaccard_similarity(a: set[str], b: set[str]) -> float:
    """计算两个集合的 Jaccard 相似度。"""
    if not a or not b:
        return 0.0
    intersection = a & b
    union = a | b
    return len(intersection) / max(len(union), 1)


def _has_negation(text: str, claim_terms: set[str]) -> bool:
    """检测文本中是否包含针对主张关键词的否定表达。"""
    negation_phrases = [
        r"no\s+evidence", r"not\s+supported", r"not\s+true",
        r"is\s+false", r"is\s+incorrect", r"inaccurate",
        r"misleading", r"debunked", r"contradict",
        r"没有证据", r"不属实", r"不准确", r"虚假", r"错误",
    ]
    text_lower = text.lower()
    for phrase in negation_phrases:
        if re.search(phrase, text_lower):
            # 检查否定是否针对主张的关键词
            for term in claim_terms:
                if term in text_lower:
                    return True
    return False


def _compare_numbers(
    claim_nums: list[tuple[float, str]],
    evidence_nums: list[tuple[float, str]],
) -> tuple[str, str]:
    """比较两组数值，返回 (判定, 描述)。

    返回值：
    - ("support", ...) 数字匹配
    - ("contradict", ...) 数字矛盾
    - ("neutral", ...) 无法判断
    """
    if not claim_nums or not evidence_nums:
        return ("neutral", "缺少数值信息进行对比")

    close_matches = []
    contradictions = []

    for cv, cu in claim_nums:
        for ev, eu in evidence_nums:
            # 单位匹配时才比较（如果一个有空单位则放宽）
            if cu and eu and cu != eu:
                continue

            if ev == 0:
                continue
            diff = abs(cv - ev) / ev

            if diff <= 0.05:
                close_matches.append((cv, ev, diff))
            elif diff > 0.20:
                contradictions.append((cv, ev, diff))

    if close_matches and not contradictions:
        nums_detail = "; ".join(
            f"主张={cv:.2f}, 证据={ev:.2f} (差异{diff*100:.1f}%)"
            for cv, ev, diff in close_matches[:3]
        )
        return ("support", f"数字匹配：{nums_detail}")

    if contradictions and not close_matches:
        nums_detail = "; ".join(
            f"主张={cv:.2f}, 证据={ev:.2f} (差异{diff*100:.1f}%)"
            for cv, ev, diff in contradictions[:3]
        )
        return ("contradict", f"数字矛盾：{nums_detail}")

    if close_matches and contradictions:
        nums_detail = (
            f"部分匹配 {len(close_matches)} 处, "
            f"矛盾 {len(contradictions)} 处"
        )
        return ("neutral", f"数字结果不一致：{nums_detail}")

    return ("neutral", "数字无法直接对比")


def _cross_validator(claims: list[dict], evidence_results: list[dict]) -> str:
    """核心函数：对比主张与证据，生成判定。

    返回 JSON 字符串。
    """
    # 构建 evidence 索引（按 claim_id）
    evidence_map: dict[str, list[dict]] = {}
    for er in evidence_results:
        eid = er.get("claim_id", "")
        evidence_map[eid] = er.get("evidence", [])

    verdicts: list[dict] = []
    for claim in claims:
        cid = claim.get("id", "")
        ctext = claim.get("text", "")
        ev_list = evidence_map.get(cid, [])

        if not ev_list:
            # 无证据
            verdicts.append(Verdict(
                claim_id=cid,
                claim_text=ctext[:300],
                status="unsupported",
                confidence=1.0,
                evidence_summary="知识库中未找到相关证据，无法验证该主张",
            ).to_dict())
            continue

        # 分析每个证据
        support_count = 0
        contradict_count = 0
        neutral_count = 0
        details: list[str] = []

        for ev in ev_list:
            ev_snippet = ev.get("snippet", "")
            ev_relevance = ev.get("relevance_score", 0.0)
            ev_source = ev.get("source", "")
            ev_supports = ev.get("supports_claim")

            # 证据本身已标记支持/反驳
            if ev_supports is True:
                support_count += 1
                details.append(f"[支持] {ev_source}：{ev_snippet[:100]}")
                continue
            elif ev_supports is False:
                contradict_count += 1
                details.append(f"[反驳] {ev_source}：{ev_snippet[:100]}")
                continue

            # 未明确标记时，自行判定
            claim_terms = _significant_terms(ctext)
            ev_terms = _significant_terms(ev_snippet)
            similarity = _jaccard_similarity(claim_terms, ev_terms)

            # 数值对比
            claim_nums = _extract_numbers(ctext)
            ev_nums = _extract_numbers(ev_snippet)
            num_verdict, num_detail = _compare_numbers(claim_nums, ev_nums)

            # 否定检测
            has_neg = _has_negation(ev_snippet, claim_terms)

            # 综合判定
            if num_verdict == "support":
                support_count += 1
                details.append(f"[支持-数字] {ev_source}：{num_detail}")
            elif num_verdict == "contradict":
                contradict_count += 1
                details.append(f"[反驳-数字] {ev_source}：{num_detail}")
            elif has_neg:
                contradict_count += 1
                details.append(f"[反驳-否定] {ev_source}：包含否定表达")
            elif similarity > 0.4:
                support_count += 1
                details.append(f"[支持-主题] {ev_source}：关键词相似度 {similarity:.2f}")
            elif similarity > 0.1:
                neutral_count += 1
                details.append(f"[中性] {ev_source}：主题相关但无法直接判定")
            else:
                neutral_count += 1
                details.append(f"[中性] {ev_source}：相关性不足")

        # 多数投票
        total = support_count + contradict_count + neutral_count
        evidence_summary = "; ".join(details[:5])
        if len(details) > 5:
            evidence_summary += f"\n... 以及另外 {len(details) - 5} 条"

        if support_count > contradict_count and support_count > neutral_count:
            status = "supported"
            confidence = round(0.5 + 0.4 * (support_count / max(total, 1)), 2)
            summary_text = f"多数证据支持该主张（{support_count}/{total}）"
        elif contradict_count >= support_count and contradict_count >= neutral_count and contradict_count > 0:
            status = "contradicted"
            confidence = round(0.5 + 0.4 * (contradict_count / max(total, 1)), 2)
            summary_text = f"证据反驳该主张（{contradict_count}/{total}）"
        elif neutral_count > 0:
            status = "unverifiable"
            confidence = 0.5
            summary_text = "证据存在但结论模糊，无法确认支持或反驳"
        else:
            status = "unsupported"
            confidence = 1.0
            summary_text = "无足够证据进行判定"

        verdicts.append(Verdict(
            claim_id=cid,
            claim_text=ctext[:300],
            status=status,
            confidence=min(confidence, 1.0),
            evidence_summary=f"{summary_text}。{evidence_summary[:400]}" if details else summary_text,
            supporting_count=support_count,
            contradicting_count=contradict_count,
        ).to_dict())

    # 汇总
    summary = VerdictSummary()
    for v in verdicts:
        s = v["status"]
        if s == "supported":
            summary.supported += 1
        elif s == "contradicted":
            summary.contradicted += 1
        elif s == "unsupported":
            summary.unsupported += 1
        elif s == "unverifiable":
            summary.unverifiable += 1

    return json.dumps({
        "total_claims": len(verdicts),
        "verdicts": verdicts,
        "summary": summary.to_dict(),
    }, ensure_ascii=False)


# ── 构造 Tool 实例 ────────────────────────────────────────
cross_validator_tool = Tool(
    name="cross_validator",
    description=(
        "将提取的主张与知识库检索到的证据进行交叉验证，"
        "输出每条主张的判定状态（supported/contradicted/unsupported/unverifiable）、"
        "置信度和详细的对比分析说明。返回 JSON 格式。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "claims": {
                "type": "array",
                "description": "主张列表（来自 claim_extractor 输出的 claims 字段）",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "text": {"type": "string"},
                    },
                    "required": ["id", "text"],
                },
            },
            "evidence_results": {
                "type": "array",
                "description": "证据检索结果（来自 kb_retriever 输出的 results 字段）",
                "items": {
                    "type": "object",
                    "properties": {
                        "claim_id": {"type": "string"},
                        "matched": {"type": "boolean"},
                        "evidence": {"type": "array"},
                    },
                },
            },
        },
        "required": ["claims", "evidence_results"],
    },
    run=_cross_validator,
)
