"""工具6：报告生成工具。

综合所有评估结果，生成报告。
支持三种输出格式：
  skill_json（默认）：符合 SKILL.md 要求的最终 JSON 结构
  markdown：详细报告，供用户追问时使用
  json：原始数据转储
"""
from __future__ import annotations
import json

from tools.base import Tool

# ── 中文标签映射 ──────────────────────────────────────────
_CREDIBILITY_LABEL_MAP = {
    "High Credibility": "高",
    "Medium Credibility": "中",
    "Low Credibility": "低",
}
_VERDICT_LABEL_MAP = {
    "supported": "高",
    "contradicted": "低",
    "unsupported": "中",
    "unverifiable": "中",
}
_REQUIRED_SIGNAL_KEYS = {
    "claim_verification": "主张验证（含证据权威性）",
    "domain_authority": "原网页域名权威性",
    "source_transparency": "来源透明度",
    "content_quality": "内容质量",
}


def _build_score_breakdown(signals: dict) -> list[dict]:
    """Build user-facing score details from scorer signals."""
    rows = []
    for key, display_name in _REQUIRED_SIGNAL_KEYS.items():
        signal = signals.get(key, {})
        rows.append({
            "维度": display_name,
            "权重": f"{float(signal.get('weight', 0.0)) * 100:.0f}%",
            "得分": round(float(signal.get("score", 0.0)), 4),
            "说明": str(signal.get("details", ""))[:200],
        })
    return rows


def _evidence_links(verdict: dict, relation: str | None = None) -> list[dict]:
    """Extract clickable evidence links from a verdict."""
    links: list[dict] = []
    seen: set[str] = set()
    sources = verdict.get("evidence_sources", [])
    if not isinstance(sources, list):
        return links

    for source in sources:
        if not isinstance(source, dict):
            continue
        url = str(source.get("url", "")).strip()
        if not url or url in seen:
            continue

        source_relation = str(source.get("relation", "")).lower()
        supports_claim = source.get("supports_claim")
        if relation == "support" and not (
            supports_claim is True or source_relation in {"support", "supports", "supported"}
        ):
            continue
        if relation == "contradict" and not (
            supports_claim is False or source_relation in {"contradict", "contradicts", "contradicted", "refute", "refutes", "refuted"}
        ):
            continue

        seen.add(url)
        links.append({
            "标题": str(source.get("title", ""))[:120],
            "链接": url,
            "域名": str(source.get("domain", "")),
            "来源类型": str(source.get("source_type", "")),
            "关系": source_relation or ("support" if supports_claim is True else ("contradict" if supports_claim is False else "")),
        })
    return links


def _collect_evidence_links(verdicts: list[dict], limit: int = 8) -> list[dict]:
    collected: list[dict] = []
    seen: set[str] = set()
    for verdict in verdicts:
        preferred = _evidence_links(verdict, relation="support")
        fallback = _evidence_links(verdict)
        for link in preferred + fallback:
            url = link.get("链接", "")
            if not url or url in seen:
                continue
            seen.add(url)
            collected.append(link)
            if len(collected) >= limit:
                return collected
    return collected


def _validate_score_breakdown(credibility_result: dict) -> dict | None:
    """Return a structured error if scorer signals are missing or incomplete."""
    signals = credibility_result.get("signals")
    if not isinstance(signals, dict):
        return {
            "error": "missing_score_breakdown",
            "tool": "report_generator",
            "hint": "credibility_result 必须包含 credibility_scorer 返回的 signals 小分明细，不能只传 overall_score。",
        }

    missing: list[str] = []
    incomplete: list[str] = []
    for key in _REQUIRED_SIGNAL_KEYS:
        signal = signals.get(key)
        if not isinstance(signal, dict):
            missing.append(key)
            continue
        for field in ("weight", "score", "details"):
            if field not in signal:
                incomplete.append(f"{key}.{field}")

    if missing or incomplete:
        return {
            "error": "incomplete_score_breakdown",
            "tool": "report_generator",
            "missing_signals": missing,
            "incomplete_fields": incomplete,
            "hint": "请直接传入 credibility_scorer 的完整返回结果，其中必须包含每个评分维度的 weight、score 和 details。",
        }
    return None


def _build_skill_json(
    credibility_result: dict,
    page_metadata: dict,
    verdicts: list[dict],
    claims: list[dict] | None = None,
) -> str:
    """生成符合 SKILL.md 最终输出要求的 JSON。"""
    overall = credibility_result.get("overall_score", 0.0)
    label = credibility_result.get("score_label", "Medium Credibility")
    signals = credibility_result.get("signals", {})
    summary = credibility_result.get("verdict_summary", {})
    domain = credibility_result.get("domain", "")
    url = page_metadata.get("url", "")
    title = page_metadata.get("title", "")

    # ── 事件描述 ────────────────────────────────────────
    event_title = title or domain or url or "未知来源"

    # ── 有效信息 ────────────────────────────────────────
    valid_items = []
    for i, v in enumerate(verdicts, 1):
        cid = v.get("claim_id", f"claim_{i}")
        ctext = v.get("claim_text", "")
        status = v.get("status", "unsupported")
        ev_summary = v.get("evidence_summary", "")
        links = _evidence_links(v, relation="support") or _evidence_links(v)

        # 被反驳的主张进入可疑点，不进入有效信息
        if status == "contradicted":
            continue

        cred = _VERDICT_LABEL_MAP.get(status, "中")
        reason = ev_summary[:200] if ev_summary else "该主张当前无足够证据支撑"
        if status == "supported":
            reason = f"有独立证据支持。{ev_summary[:150]}"
        elif status == "unverifiable":
            reason = f"有相关信息但无法确认。{ev_summary[:150]}"

        valid_items.append({
            "编号": f"信息{i}",
            "内容": ctext[:200],
            "可信度": cred,
            "理由": reason[:200],
            "佐证链接": links[:3],
        })

    # ── 可疑点 ──────────────────────────────────────────
    suspicious = []
    # 被反驳的主张
    for v in verdicts:
        if v.get("status") == "contradicted":
            ctext = v.get("claim_text", "")
            suspicious.append(f"「{ctext[:100]}」与现有证据矛盾")

    # 域名权威性低
    da_signal = signals.get("domain_authority", {})
    if da_signal.get("score", 0.5) < 0.4:
        suspicious.append(f"来源域名 {domain} 权威性不足")

    # 来源透明度低
    st_signal = signals.get("source_transparency", {})
    if st_signal.get("score", 0.5) < 0.3:
        missing_parts = []
        if not (page_metadata.get("author") or page_metadata.get("source") or page_metadata.get("publisher")):
            missing_parts.append("作者或来源")
        if not page_metadata.get("publication_date"):
            missing_parts.append("发布日期")
        if missing_parts:
            suspicious.append(f"页面缺乏{', '.join(missing_parts)}等关键元信息")

    # ── 事件可信度 ──────────────────────────────────────
    cn_cred = _CREDIBILITY_LABEL_MAP.get(label, "中")

    # ── 事件真相还原 ────────────────────────────────────
    supported_claims = [v.get("claim_text", "") for v in verdicts if v.get("status") == "supported"]
    contradicted_claims = [v.get("claim_text", "") for v in verdicts if v.get("status") == "contradicted"]
    unverifiable_claims = [v.get("claim_text", "") for v in verdicts if v.get("status") in ("unverifiable", "unsupported")]

    parts = []
    if supported_claims:
        parts.append(f"有证据支持的主张：{'；'.join(c[:80] for c in supported_claims)}")
    if contradicted_claims:
        parts.append(f"被证据反驳的主张：{'；'.join(c[:80] for c in contradicted_claims)}")
    if unverifiable_claims:
        parts.append(f"暂时无法验证的主张：{'；'.join(c[:80] for c in unverifiable_claims)}")

    truth = "；".join(parts) if parts else "该事件缺乏足够可靠证据，无法做出完整还原。"

    # ── 组装最终 JSON ───────────────────────────────────
    result = {
        "事件": event_title[:100],
        "评分明细": _build_score_breakdown(signals),
        "主要佐证": _collect_evidence_links(verdicts),
        "有效信息": valid_items,
        "剔除信息": [],  # 由模型在最终整合时补充
        "可疑点": suspicious,
        "事件可信度": cn_cred,
        "事件真相还原": truth[:500],
    }

    return json.dumps(result, ensure_ascii=False, indent=2)


def _score_to_bar(score: float, width: int = 20) -> str:
    filled = int(score * width)
    bar = "█" * filled + "░" * (width - filled)
    color = "🟢" if score >= 0.7 else ("🟡" if score >= 0.4 else "🔴")
    return f"{color} {bar} {score*100:.0f}%"


def _format_field(value: str, default: str = "未找到") -> str:
    if not value or not value.strip():
        return default
    return value.strip()


def _get_recommendation(overall_score: float, score_label: str) -> str:
    if overall_score >= 0.70:
        return (
            "✅ **可信度高**：该来源表现出了较高的可信度指标。"
            "主要主张有证据支持，来源权威性高。"
            "在一般情况下可以信赖此来源的信息，但仍建议对重大决策相关的关键事实进行独立核实。"
        )
    elif overall_score >= 0.40:
        return (
            "⚠️ **谨慎对待**：该来源的可信度处于中等水平。"
            "部分主张未能得到验证或被证据反驳。"
            "建议在引用此来源的信息前，对关键主张进行交叉验证，"
            "特别是那些缺乏证据支持或已被反驳的主张。"
        )
    else:
        return (
            "❌ **谨慎对待，强烈建议验证**：该来源的可信度指标较低。"
            "多个关键主张被证据反驳或无法验证，来源权威性不足。"
            "不建议仅依赖此来源的信息做出判断。"
            "强烈建议寻找更可靠的来源进行独立核实。"
        )


def _generate_markdown_report(
    credibility_result: dict,
    page_metadata: dict,
    verdicts: list[dict],
    claims: list[dict] | None = None,
) -> str:
    """生成 Markdown 格式详细报告。"""
    overall = credibility_result.get("overall_score", 0.0)
    label = credibility_result.get("score_label", "N/A")
    signals = credibility_result.get("signals", {})
    summary = credibility_result.get("verdict_summary", {})
    domain = credibility_result.get("domain", "")

    lines: list[str] = []

    # ── 标题 ────────────────────────────────────────────
    lines.append("# 网页可信度评估报告")
    lines.append("")

    # ── 摘要表 ──────────────────────────────────────────
    lines.append("## 📋 页面摘要")
    lines.append("")
    lines.append("| 项目 | 内容 |")
    lines.append("|------|------|")
    lines.append(f"| **URL** | {_format_field(page_metadata.get('url', ''))} |")
    lines.append(f"| **来源域名** | {_format_field(domain)} |")
    lines.append(f"| **页面标题** | {_format_field(page_metadata.get('title', ''), '无标题')} |")
    lines.append(f"| **来源/发布机构** | {_format_field(page_metadata.get('source', '') or page_metadata.get('publisher', ''))} |")
    lines.append(f"| **作者** | {_format_field(page_metadata.get('author', ''))} |")
    lines.append(f"| **发布日期** | {_format_field(page_metadata.get('publication_date', ''))} |")
    lines.append(f"| **字数** | {page_metadata.get('word_count', 0)} 词 |")
    lines.append("")

    # ── 总体评分 ────────────────────────────────────────
    lines.append("## 📊 总体可信度评分")
    lines.append("")
    lines.append(f"### **{overall*100:.0f}/100 — {label}**")
    lines.append("")
    lines.append(f"{_score_to_bar(overall)}")
    lines.append("")

    # ── 信号明细 ────────────────────────────────────────
    lines.append("### 各维度评分明细")
    lines.append("")
    lines.append("| 信号维度 | 权重 | 得分 | 说明 |")
    lines.append("|----------|------|------|------|")

    signal_names = {
        "claim_verification": "主张验证（含证据权威性）",
        "domain_authority": "原网页域名权威性",
        "source_transparency": "来源透明度",
        "content_quality": "内容质量",
    }
    for key, display_name in signal_names.items():
        sig = signals.get(key, {})
        weight = sig.get("weight", 0) * 100
        score = sig.get("score", 0) * 100
        details = sig.get("details", "")
        lines.append(f"| **{display_name}** | {weight:.0f}% | {score:.0f}% | {details[:120]} |")
    lines.append("")

    # ── 判定汇总 ────────────────────────────────────────
    lines.append("## ⚖️ 主张判定汇总")
    lines.append("")
    lines.append("| 判定状态 | 数量 | 含义 |")
    lines.append("|----------|------|------|")
    lines.append(f"| ✅ **支持** | {summary.get('supported', 0)} | 知识库证据支持该主张 |")
    lines.append(f"| ❌ **反驳** | {summary.get('contradicted', 0)} | 知识库证据反驳该主张 |")
    lines.append(f"| ❓ **无证据** | {summary.get('unsupported', 0)} | 知识库中未找到相关证据 |")
    lines.append(f"| ⚠️ **无法判定** | {summary.get('unverifiable', 0)} | 证据存在但结论模糊 |")
    lines.append("")

    # ── 逐条分析 ────────────────────────────────────────
    lines.append("## 🔍 逐条主张分析")
    lines.append("")
    if not verdicts:
        lines.append("_未从页面中提取到可验证的主张。_")
        lines.append("")

    for i, v in enumerate(verdicts, 1):
        ctext = v.get("claim_text", "")
        status = v.get("status", "unsupported")
        confidence = v.get("confidence", 0.0)
        ev_summary = v.get("evidence_summary", "")
        support_cnt = v.get("supporting_count", 0)
        contradict_cnt = v.get("contradicting_count", 0)

        status_icons = {
            "supported": "✅ 支持",
            "contradicted": "❌ 反驳",
            "unsupported": "❓ 无证据",
            "unverifiable": "⚠️ 无法判定",
        }
        status_display = status_icons.get(status, status)

        lines.append(f"### 主张 {i}：{ctext[:150]}")
        lines.append("")
        lines.append(f"- **判定状态**：{status_display}（置信度：{confidence*100:.0f}%）")
        lines.append(f"- **支持证据**：{support_cnt} 条 | **反驳证据**：{contradict_cnt} 条")
        lines.append(f"- **分析说明**：{ev_summary[:300]}")
        links = _evidence_links(v)
        if links:
            lines.append("- **证据链接**：")
            for link in links[:5]:
                title = link.get("标题") or link.get("域名") or link.get("链接")
                url = link.get("链接", "")
                relation = link.get("关系", "")
                suffix = f"（{relation}）" if relation else ""
                lines.append(f"  - [{title}]({url}){suffix}")
        lines.append("")

    # ── 建议 ────────────────────────────────────────────
    lines.append("## 💡 使用建议")
    lines.append("")
    lines.append(_get_recommendation(overall, label))
    lines.append("")

    # ── 免责声明 ────────────────────────────────────────
    lines.append("---")
    lines.append("")
    lines.append(
        "*免责声明：本报告由自动化工具生成，基于知识库数据匹配和规则引擎分析。"
        "评估结果仅供参考，不能替代专业的事实核查。*"
    )
    lines.append("")
    lines.append(f"*报告生成时间：{__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")

    return "\n".join(lines)


def _report_generator(
    credibility_result: dict | None = None,
    page_metadata: dict | None = None,
    verdicts: list[dict] | None = None,
    claims: list[dict] | None = None,
    output_format: str = "skill_json",
) -> str:
    """核心函数：生成最终报告。

    三种输出格式：
    - skill_json（默认）：SKILL.md 要求的最终 JSON 结构
    - markdown：完整详细报告
    - json：原始数据转储
    """
    if credibility_result is None or page_metadata is None or verdicts is None:
        return json.dumps({
            "error": "missing_required_arguments",
            "tool": "report_generator",
            "required": ["credibility_result", "page_metadata", "verdicts"],
            "hint": (
                "请把 credibility_scorer 返回的 credibility_result、"
                "webpage_reader 返回的 page_metadata、交叉验证 verdicts 作为参数传入；"
                "不能空参数调用本工具。"
            ),
            "example": {
                "credibility_result": {
                    "overall_score": 0.72,
                    "score_label": "High Credibility",
                    "signals": {},
                    "domain": "example.com",
                    "url": "https://example.com/news",
                    "verdict_summary": {
                        "supported": 1,
                        "contradicted": 0,
                        "unsupported": 0,
                        "unverifiable": 0,
                    },
                },
                "page_metadata": {
                    "url": "https://example.com/news",
                    "domain": "example.com",
                    "title": "页面标题",
                },
                "verdicts": [
                    {
                        "claim_id": "claim_1",
                        "claim_text": "可验证主张",
                        "status": "supported",
                        "evidence_summary": "证据摘要",
                    }
                ],
                "output_format": "skill_json",
            },
        }, ensure_ascii=False, indent=2)
    if not isinstance(credibility_result, dict) or not isinstance(page_metadata, dict) or not isinstance(verdicts, list):
        return json.dumps({
            "error": "invalid_arguments",
            "tool": "report_generator",
            "hint": "credibility_result 和 page_metadata 必须是对象，verdicts 必须是数组。",
            "received_types": {
                "credibility_result": type(credibility_result).__name__,
                "page_metadata": type(page_metadata).__name__,
                "verdicts": type(verdicts).__name__,
            },
        }, ensure_ascii=False, indent=2)

    score_breakdown_error = _validate_score_breakdown(credibility_result)
    if score_breakdown_error is not None:
        score_breakdown_error["example"] = {
            "credibility_result": {
                "overall_score": 0.72,
                "score_label": "High Credibility",
                "signals": {
                    "claim_verification": {
                        "weight": 0.50,
                        "score": 0.86,
                        "details": "主张验证分说明",
                    },
                    "domain_authority": {
                        "weight": 0.20,
                        "score": 0.90,
                        "details": "域名权威性说明",
                    },
                    "source_transparency": {
                        "weight": 0.15,
                        "score": 0.92,
                        "details": "来源透明度说明",
                    },
                    "content_quality": {
                        "weight": 0.15,
                        "score": 0.82,
                        "details": "内容质量说明",
                    },
                },
                "domain": "example.com",
                "url": "https://example.com/news",
                "verdict_summary": {
                    "supported": 1,
                    "contradicted": 0,
                    "unsupported": 0,
                    "unverifiable": 0,
                },
            }
        }
        return json.dumps(score_breakdown_error, ensure_ascii=False, indent=2)

    if output_format == "skill_json":
        return _build_skill_json(
            credibility_result=credibility_result,
            page_metadata=page_metadata,
            verdicts=verdicts,
            claims=claims,
        )

    if output_format == "markdown":
        return _generate_markdown_report(
            credibility_result=credibility_result,
            page_metadata=page_metadata,
            verdicts=verdicts,
            claims=claims,
        )

    # json：原始数据转储
    return json.dumps({
        "report_type": "credibility_assessment",
        "credibility_result": credibility_result,
        "page_metadata": page_metadata,
        "verdicts": verdicts,
        "claims": claims or [],
    }, ensure_ascii=False, indent=2)


# ── 构造 Tool 实例 ────────────────────────────────────────
report_generator_tool = Tool(
    name="report_generator",
    description=(
        "综合所有评估结果生成报告。默认输出 skill_json（符合 SKILL.md 要求的最终 JSON），"
        "也支持 markdown（详细报告，用户追问时使用）和 json（原始数据转储）。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "credibility_result": {
                "type": "object",
                "description": "可信度评分结果，必须直接使用 credibility_scorer 的完整输出，包含 overall_score、score_label、signals 小分明细、verdict_summary",
            },
            "page_metadata": {
                "type": "object",
                "description": "网页元数据（来自 webpage_reader 输出）",
            },
            "verdicts": {
                "type": "array",
                "description": "交叉验证结果中的 verdicts 列表",
            },
            "claims": {
                "type": "array",
                "description": "提取的主张列表（可选，用于丰富报告）",
            },
            "output_format": {
                "type": "string",
                "enum": ["skill_json", "markdown", "json"],
                "description": "skill_json=最终答案JSON(默认), markdown=详细报告(追问时使用), json=原始数据",
                "default": "skill_json",
            },
        },
        "required": ["credibility_result", "page_metadata", "verdicts"],
    },
    run=_report_generator,
)
