"""工具2：主张提取工具。

从网页正文中提取 1-5 条核心事实性主张。
使用纯规则引擎：分句 → 按模式打分 → 筛选 → 分类。
"""
from __future__ import annotations
import re
import json

from tools.base import Tool
from tools.credibility.models import Claim, to_json


# ── 打分模式 ──────────────────────────────────────────────
# 每个模式包含：正则、匹配得分、对应的 claim_type

PATTERNS: list[tuple[re.Pattern, float, str]] = [
    # 数字模式（金额、百分比、统计数据）
    (re.compile(r"\$\s*\d+(?:[.,]\d+)?\s*(?:[KkMmBbTt]|illion|万|亿)?"), 3.0, "numerical"),
    (re.compile(r"\d+[\.,]?\d*\s*%"), 3.0, "numerical"),
    (re.compile(r"\d+(?:[.,]\d+)?\s*(?:万|亿|million|billion|trillion)"), 3.0, "numerical"),
    (re.compile(r"(?:增长|下降|达到|超过|不足|约占)\s*\d+"), 3.0, "numerical"),
    # 因果模式
    (re.compile(r"\b(?:because|therefore|thus|hence|consequently|as a result|"
                r"leads?\s+to|results?\s+in|causes?\s+|due\s+to|owing\s+to"
                r"|attributed?\s+to|contributes?\s+to)\b", re.IGNORECASE), 2.5, "causal"),
    (re.compile(r"(?:导致|引起|由于|因此|造成|促使|引发|源于)"), 2.5, "causal"),
    # 引用/归因模式
    (re.compile(r"\b(?:according\s+to|said|stated|reported|claims?\s+that|"
                r"announced|suggests?\s+that|indicates?\s+that|"
                r"published\s+in|found\s+that|shows?\s+that)\b", re.IGNORECASE), 2.0, "attribution"),
    (re.compile(r"(?:据报道|据统计|据了解|据悉|研究表明|调查显示|专家指出)"), 2.0, "attribution"),
    # 否定信号（扣分）
    (re.compile(r"\b(?:think|believe|feel|seems?|appears?|maybe|perhaps|might|could|possibly)\b",
                re.IGNORECASE), -2.0, None),
    (re.compile(r"(?:可能|也许|似乎|大概|觉得|认为|猜测)"), -2.0, None),
    # 疑问句
    (re.compile(r"\?\s*$"), -3.0, None),
]

# 英文停用词
STOPWORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "dare", "ought",
    "used", "to", "of", "in", "for", "on", "with", "at", "by", "from",
    "as", "into", "through", "during", "before", "after", "above", "below",
    "between", "out", "off", "over", "under", "again", "further", "then",
    "once", "here", "there", "when", "where", "why", "how", "all", "each",
    "every", "both", "few", "more", "most", "other", "some", "such", "no",
    "nor", "not", "only", "own", "same", "so", "than", "too", "very",
    "just", "because", "and", "but", "or", "if", "while", "although",
    "this", "that", "these", "those", "it", "its", "they", "them", "their",
    "we", "us", "our", "you", "your", "he", "she", "him", "her", "his",
    "i", "me", "my", "myself", "yourself", "himself", "herself", "itself",
    "ourselves", "themselves", "what", "which", "who", "whom",
})


def _split_sentences(text: str) -> list[str]:
    """将文本分割为句子列表。"""
    # 按句号、问号、感叹号 + 空白分割，保留引号
    raw = re.split(r"(?<=[。！？.!?])\s+", text)
    # 进一步处理英文句点（避免分割缩写如 Dr. Mr. U.S.）
    result = []
    for s in raw:
        s = s.strip()
        if len(s) < 3:
            continue
        # 对英文句子，如果句点后跟的不是大写字母或空白，则可能不是句子边界
        # 简化处理：跳过过短的"句子"
        result.append(s)
    return result


def _score_sentence(sentence: str) -> tuple[float, str | None]:
    """对句子打分并确定主张类型。

    返回：(分数, 主要主张类型)
    """
    total = 0.0
    type_scores: dict[str, float] = {}

    for pattern, score, claim_type in PATTERNS:
        matches = pattern.findall(sentence)
        if matches:
            match_count = len(matches)
            contribution = score * min(match_count, 3)  # 同一模式最多计 3 次
            total += contribution
            if claim_type:
                type_scores[claim_type] = type_scores.get(claim_type, 0) + contribution

    # 确定主要类型
    primary_type = "factual"
    if type_scores:
        primary_type = max(type_scores, key=type_scores.get)

    return total, primary_type


def _contains_named_entity(sentence: str) -> float:
    """检测句子是否包含命名实体（大写首字母的词汇，出现在句中非句首位置）。"""
    score = 0.0
    # 匹配大写开头的词组
    entities = re.findall(r"\b[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,}){0,2}\b", sentence)
    # 过滤掉句首单词和常见非实体
    first_word_match = re.match(r"^[A-Z][a-z]+", sentence)
    first_word = first_word_match.group() if first_word_match else ""

    for ent in entities:
        word = ent.split()[0]
        if word != first_word and word.lower() not in STOPWORDS:
            score += 1.0
    return min(score, 3.0)  # 最多 3 分


def _claim_extractor(text: str, max_claims: int = 5) -> str:
    """核心函数：从文本中提取事实性主张。

    返回 JSON 字符串。
    """
    if not text or len(text.strip()) < 20:
        return json.dumps({
            "source_preview": text[:200] if text else "",
            "claims_extracted": 0,
            "claims": [],
        }, ensure_ascii=False)

    source_preview = text[:200].replace("\n", " ")

    # 1. 分句
    sentences = _split_sentences(text)

    # 2. 打分
    scored: list[tuple[float, str, str]] = []  # (score, type, sentence)
    for sent in sentences:
        sent = sent.strip()
        if len(sent) < 15:
            continue

        score, primary_type = _score_sentence(sent)
        # 补充命名实体加分
        ent_score = _contains_named_entity(sent)
        score += ent_score * 0.5

        # 事实性断言的"基线"加分：长度超过 30 字符且包含主谓结构
        if len(sent) > 30 and re.search(r"\b(is|are|was|were|has|have|had|will|"
                                         r"表示|指出|是|有|将|会|可以|能够)\b", sent):
            score += 1.0

        # 过滤无价值的句子
        if score > 0:
            scored.append((score, primary_type, sent))

    # 3. 排序并取 top-K
    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:max_claims]

    # 4. 构建结果
    max_possible = max(s[0] for s in scored) if scored else 10.0
    if max_possible <= 0:
        max_possible = 10.0

    claims = []
    for i, (raw_score, ctype, sent) in enumerate(top, 1):
        # 截断过长的句子
        display_text = sent[:300] + "..." if len(sent) > 300 else sent
        confidence = round(min(raw_score / max_possible, 1.0), 2)
        claims.append(Claim(
            id=f"claim_{i}",
            text=display_text,
            claim_type=ctype if ctype else "factual",
            confidence=confidence,
        ))

    return to_json({
        "source_preview": source_preview,
        "claims_extracted": len(claims),
        "claims": [c.to_dict() for c in claims],
    })


# ── 构造 Tool 实例 ────────────────────────────────────────
claim_extractor_tool = Tool(
    name="claim_extractor",
    description=(
        "从文本中提取 1-5 条核心事实性主张（可验证的断言）。"
        "每条主张标注类型（numerical/causal/attribution/factual）和置信度分数。"
        "返回 JSON 格式。支持中英文混合文本。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "需要提取主张的文本内容（通常来自 webpage_reader 的 text_content 字段）",
            },
            "max_claims": {
                "type": "integer",
                "description": "最大提取主张数（1-10，默认 5）",
                "default": 5,
            },
        },
        "required": ["text"],
    },
    run=_claim_extractor,
)
