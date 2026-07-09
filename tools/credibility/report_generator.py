"""工具6：报告生成工具。

综合所有评估结果，生成面向普通用户的 Markdown 可信度解释报告。
支持 markdown 和 json 两种输出格式。
"""
from __future__ import annotations
import json

from tools.base import Tool


def _score_to_bar(score: float, width: int = 20) -> str:
    """生成一个简单的进度条字符串。"""
    filled = int(score * width)
    bar = "█" * filled + "░" * (width - filled)
    color = "🟢" if score >= 0.7 else ("🟡" if score >= 0.4 else "🔴")
    return f"{color} {bar} {score*100:.0f}%"


def _format_field(value: str, default: str = "未找到") -> str:
    """格式化字段，空值显示默认文本。"""
    if not value or not value.strip():
        return default
    return value.strip()


def _get_recommendation(overall_score: float, score_label: str) -> str:
    """基于评分生成建议。"""
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
    """生成 Markdown 格式报告。"""
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
    lines.append(f"| **作者** | {_format_field(page_metadata.get('author', ''))} |")
    lines.append(f"| **发布日期** | {_format_field(page_metadata.get('publication_date', ''))} |")
    lines.append(f"| **说明** | {_format_field(page_metadata.get('description', ''), '无页面描述')} |")
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
        "claim_verification": "主张验证",
        "domain_authority": "来源权威性",
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
        cid = v.get("claim_id", f"主张 {i}")
        ctext = v.get("claim_text", "")
        status = v.get("status", "unsupported")
        confidence = v.get("confidence", 0.0)
        ev_summary = v.get("evidence_summary", "")
        support_cnt = v.get("supporting_count", 0)
        contradict_cnt = v.get("contradicting_count", 0)

        # 状态图标
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
    credibility_result: dict,
    page_metadata: dict,
    verdicts: list[dict],
    claims: list[dict] | None = None,
    output_format: str = "markdown",
) -> str:
    """核心函数：生成最终报告。

    返回 Markdown 或 JSON 格式的报告字符串。
    """
    if output_format == "json":
        return json.dumps({
            "report_type": "credibility_assessment",
            "credibility_result": credibility_result,
            "page_metadata": page_metadata,
            "verdicts": verdicts,
            "claims": claims or [],
        }, ensure_ascii=False, indent=2)

    # 默认返回 Markdown
    return _generate_markdown_report(
        credibility_result=credibility_result,
        page_metadata=page_metadata,
        verdicts=verdicts,
        claims=claims,
    )


# ── 构造 Tool 实例 ────────────────────────────────────────
report_generator_tool = Tool(
    name="report_generator",
    description=(
        "综合所有评估结果，生成面向普通用户的最终报告。"
        "默认输出格式为 Markdown，适合直接展示；也可选择 JSON 格式。"
        "报告包含页面摘要、可信度评分、信号明细、主张判定分析和使用建议。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "credibility_result": {
                "type": "object",
                "description": "可信度评分结果（来自 credibility_scorer 输出）",
            },
            "page_metadata": {
                "type": "object",
                "description": "网页元数据（来自 webpage_reader 输出）",
            },
            "verdicts": {
                "type": "array",
                "description": "交叉验证结果中的 verdicts 列表（来自 cross_validator 输出）",
            },
            "claims": {
                "type": "array",
                "description": "提取的主张列表（来自 claim_extractor 输出，可选，用于丰富报告）",
            },
            "output_format": {
                "type": "string",
                "enum": ["markdown", "json"],
                "description": "输出格式：markdown（默认，适合展示）或 json（适合程序处理）",
                "default": "markdown",
            },
        },
        "required": ["credibility_result", "page_metadata", "verdicts"],
    },
    run=_report_generator,
)
