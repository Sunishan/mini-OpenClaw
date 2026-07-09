"""可信度评估系统的共享数据模型。

所有工具通过 JSON 字符串通信，这些 dataclass 定义了序列化格式。
每个类提供 to_dict() 方法供 json.dumps 使用。
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any
import json


def to_json(obj: Any) -> str:
    """将 dataclass 或字典序列化为 JSON 字符串，确保中文等非 ASCII 字符正确保留。"""
    if hasattr(obj, "to_dict"):
        return json.dumps(obj.to_dict(), ensure_ascii=False, default=str, indent=2)
    if isinstance(obj, dict):
        return json.dumps(obj, ensure_ascii=False, default=str, indent=2)
    return json.dumps(obj, ensure_ascii=False, default=str, indent=2)


# ============================================================
# 工具1：网页读取 → PageMetadata
# ============================================================

@dataclass
class PageMetadata:
    url: str = ""
    title: str = ""
    description: str = ""
    domain: str = ""
    author: str = ""
    publication_date: str = ""
    word_count: int = 0
    text_content: str = ""
    extraction_success: bool = False
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ============================================================
# 工具2：主张提取 → Claim
# ============================================================

CLAIM_TYPES = ("numerical", "causal", "attribution", "factual")


@dataclass
class Claim:
    id: str = ""
    text: str = ""
    claim_type: str = "factual"      # numerical / causal / attribution / factual
    confidence: float = 0.0          # 0.0 ~ 1.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ============================================================
# 工具3：知识库检索 → Evidence / ClaimEvidenceResult
# ============================================================

@dataclass
class Evidence:
    source: str = ""
    snippet: str = ""
    relevance_score: float = 0.0     # 0.0 ~ 1.0
    supports_claim: bool | None = None  # True=支持, False=反驳, None=中性

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ClaimEvidenceResult:
    claim_id: str = ""
    matched: bool = False
    evidence: list[Evidence] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "matched": self.matched,
            "evidence": [e.to_dict() for e in self.evidence],
            "note": self.note,
        }


# ============================================================
# 工具4：交叉验证 → Verdict / VerdictSummary
# ============================================================

VERDICT_STATUSES = ("supported", "contradicted", "unsupported", "unverifiable")


@dataclass
class Verdict:
    claim_id: str = ""
    claim_text: str = ""
    status: str = "unsupported"      # supported / contradicted / unsupported / unverifiable
    confidence: float = 0.0          # 该判定的置信度
    evidence_summary: str = ""
    supporting_count: int = 0
    contradicting_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class VerdictSummary:
    supported: int = 0
    contradicted: int = 0
    unsupported: int = 0
    unverifiable: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ============================================================
# 工具5：可信度评分 → SignalScore / CredibilityResult
# ============================================================

@dataclass
class SignalScore:
    weight: float = 0.0
    score: float = 0.0               # 0.0 ~ 1.0
    details: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


CREDIBILITY_LABELS = ("High Credibility", "Medium Credibility", "Low Credibility")


@dataclass
class CredibilityResult:
    overall_score: float = 0.0
    score_label: str = "Low Credibility"
    signals: dict[str, SignalScore] = field(default_factory=dict)
    domain: str = ""
    url: str = ""
    verdict_summary: VerdictSummary = field(default_factory=VerdictSummary)

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall_score": self.overall_score,
            "score_label": self.score_label,
            "signals": {k: v.to_dict() for k, v in self.signals.items()},
            "domain": self.domain,
            "url": self.url,
            "verdict_summary": self.verdict_summary.to_dict(),
        }
