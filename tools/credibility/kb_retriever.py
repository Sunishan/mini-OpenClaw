"""工具3：知识库检索工具。

根据主张列表，从本地知识库 JSON 文件中检索相关证据。
使用 Jaccard 关键词重叠、数字匹配和实体匹配计算相关性。
"""
from __future__ import annotations
import json
import os
import re
from pathlib import Path

from tools.base import Tool
from tools.credibility.models import Evidence, ClaimEvidenceResult, to_json


# 停用词（用于分词过滤）
STOPWORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "dare", "ought",
    "to", "of", "in", "for", "on", "with", "at", "by", "from", "as",
    "into", "through", "during", "before", "after", "above", "below",
    "between", "out", "off", "over", "under", "again", "further", "then",
    "once", "here", "there", "when", "where", "why", "how", "all", "each",
    "every", "both", "few", "more", "most", "other", "some", "such", "no",
    "nor", "not", "only", "own", "same", "so", "than", "too", "very",
    "just", "because", "and", "but", "or", "if", "while", "although",
    "this", "that", "these", "those", "it", "its", "they", "them", "their",
    "we", "us", "our", "you", "your", "he", "she", "him", "her", "his",
    "i", "me", "my", "myself", "yourself", "himself", "herself", "itself",
    "what", "which", "who", "whom",
    "的", "了", "在", "是", "我", "有", "和", "就", "不", "人", "都", "一",
    "一个", "上", "也", "很", "到", "说", "要", "去", "你", "会", "着",
    "没有", "看", "好", "自己", "这",
})


def _get_kb_path() -> Path:
    """定位知识库 JSON 文件路径。

    优先使用环境变量 CREDIBILITY_KB_PATH；
    否则在项目目录的 data/credibility/ 下寻找。
    """
    env_path = os.environ.get("CREDIBILITY_KB_PATH")
    if env_path:
        return Path(env_path)

    # 搜索可能的项目根目录
    candidates = [
        Path.cwd() / "data" / "credibility" / "knowledge_base.json",
        Path(__file__).resolve().parent.parent.parent.parent
        / "data" / "credibility" / "knowledge_base.json",
        Path(__file__).resolve().parent.parent.parent
        / ".." / "data" / "credibility" / "knowledge_base.json",
    ]

    # 向上扫描目录直到找到 knowledge_base.json
    start = Path(__file__).resolve().parent
    for parent in [start] + list(start.parents):
        candidate = parent / "data" / "credibility" / "knowledge_base.json"
        if candidate.exists():
            return candidate
        candidate = parent / "knowledge_base.json"
        if candidate.exists():
            return candidate

    # 返回默认候选
    return candidates[0]


def _load_knowledge_base() -> list[dict]:
    """加载知识库 JSON 文件。"""
    path = _get_kb_path()
    if not path.exists():
        raise FileNotFoundError(
            f"知识库文件未找到：{path}。"
            f"请创建该文件或设置 CREDIBILITY_KB_PATH 环境变量。"
        )
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("知识库文件格式错误：根元素应为 JSON 数组")
    return data


def _tokenize(text: str) -> set[str]:
    """分词并过滤停用词，返回有意义的词干集合。"""
    # 转小写
    text = text.lower()
    # 分割为单词（支持中英文）
    words = re.findall(r"[a-z]+(?:'[a-z]+)?|[一-鿿]+", text)
    # 过滤停用词和短词
    significant = {w for w in words if w not in STOPWORDS and len(w) > 1}
    return significant


def _extract_numbers(text: str) -> list[float]:
    """从文本中提取所有数值。"""
    nums = []
    # 匹配整数和小数
    for match in re.finditer(r"\d+[.,]?\d*", text):
        s = match.group().replace(",", "")
        try:
            nums.append(float(s))
        except ValueError:
            pass
    return nums


def _numbers_overlap(claim_nums: list[float], kb_nums: list[float]) -> bool:
    """检查两组数字是否在 5% 容忍度内重叠。"""
    for cn in claim_nums:
        for kn in kb_nums:
            if kn == 0:
                continue
            diff = abs(cn - kn) / kn
            if diff <= 0.05:
                return True
    return False


def _compute_relevance(claim_text: str, kb_entry: dict) -> float:
    """计算主张与知识库条目的相关性分数。

    综合三个维度：
    - 关键词 Jaccard 重叠
    - 数字匹配
    - 实体匹配
    """
    score = 0.0

    # 1. 关键词 Jaccard 重叠
    claim_tokens = _tokenize(claim_text)
    kb_keywords = set(k.lower() for k in kb_entry.get("keywords", []))
    if claim_tokens and kb_keywords:
        intersection = claim_tokens & kb_keywords
        union = claim_tokens | kb_keywords
        jaccard = len(intersection) / max(len(union), 1)
        score += jaccard * 0.5

    # 2. 数字匹配
    claim_nums = _extract_numbers(claim_text)
    kb_fact = kb_entry.get("fact", "")
    kb_nums = _extract_numbers(kb_fact)
    if claim_nums and kb_nums and _numbers_overlap(claim_nums, kb_nums):
        score += 0.3

    # 3. 实体匹配
    claim_text_lower = claim_text.lower()
    kb_entities = kb_entry.get("entities", {})
    if isinstance(kb_entities, dict):
        for key, value in kb_entities.items():
            value_str = str(value).lower()
            if isinstance(value, str) and value_str in claim_text_lower:
                score += 0.2
            elif isinstance(value, (int, float)):
                # 如果是数字实体，检查是否在文本中提到
                if str(value) in claim_text:
                    score += 0.1

    return min(score, 1.0)  # 上限 1.0


def _determine_supports_claim(claim_text: str, kb_entry: dict) -> bool | None:
    """判断知识库条目是支持还是反驳主张。

    基于关键词重叠和数字的一致性判断。
    返回 True=支持, False=反驳, None=中性/不确定。
    """
    claim_tokens = _tokenize(claim_text)
    kb_tokens = _tokenize(kb_entry.get("fact", ""))

    # 检查关键词重叠
    if claim_tokens and kb_tokens:
        intersection = claim_tokens & kb_tokens
        union = claim_tokens | kb_tokens
        jaccard = len(intersection) / max(len(union), 1)
        if jaccard < 0.1:
            return None  # 相关性太低，无法判断

    # 数字比较：检查主张的数字与 KB 的数字是否一致
    claim_nums = _extract_numbers(claim_text)
    kb_nums = _extract_numbers(kb_entry.get("fact", ""))

    if claim_nums and kb_nums:
        has_close = False
        has_far = False
        for cn in claim_nums:
            for kn in kb_nums:
                if kn == 0:
                    continue
                diff = abs(cn - kn) / kn
                if diff <= 0.05:
                    has_close = True
                elif diff > 0.20:
                    has_far = True
        if has_close and not has_far:
            return True
        if has_far and not has_close:
            return False

    # 默认正向（不完全匹配但主题相关视为支持）
    if jaccard >= 0.15:
        return True
    return None


def _kb_retriever(claims: list[dict], top_k: int = 3) -> str:
    """核心函数：根据主张列表检索知识库。

    claims: 来自 claim_extractor 输出的 claims 列表
    top_k: 每条主张最多返回的匹配证据数

    返回 JSON 字符串。
    """
    # 1. 加载知识库
    try:
        knowledge_base = _load_knowledge_base()
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as e:
        return json.dumps({
            "kb_name": "default",
            "kb_entry_count": 0,
            "error": str(e),
            "results": [],
        }, ensure_ascii=False)

    if not knowledge_base:
        return json.dumps({
            "kb_name": "default",
            "kb_entry_count": 0,
            "results": [],
        }, ensure_ascii=False)

    # 2. 对每个主张检索证据
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

        # 计算每个 KB 条目的相关性
        scored_entries: list[tuple[float, dict]] = []
        for entry in knowledge_base:
            relevance = _compute_relevance(claim_text, entry)
            if relevance > 0.3:  # 相关性阈值
                scored_entries.append((relevance, entry))

        # 按相关性排序
        scored_entries.sort(key=lambda x: x[0], reverse=True)
        top_entries = scored_entries[:top_k]

        if not top_entries:
            results.append(ClaimEvidenceResult(
                claim_id=claim_id,
                matched=False,
                note="知识库中未找到相关证据",
            ).to_dict())
            continue

        # 构建证据列表
        evidence_list = []
        for relevance, entry in top_entries:
            supports = _determine_supports_claim(claim_text, entry)
            evidence_list.append(Evidence(
                source=entry.get("source", "未知来源"),
                snippet=entry.get("fact", ""),
                relevance_score=round(relevance, 2),
                supports_claim=supports,
            ))

        results.append(ClaimEvidenceResult(
            claim_id=claim_id,
            matched=True,
            evidence=evidence_list,
        ).to_dict())

    return json.dumps({
        "kb_name": "default",
        "kb_entry_count": len(knowledge_base),
        "results": results,
    }, ensure_ascii=False)


# ── 构造 Tool 实例 ────────────────────────────────────────
kb_retriever_tool = Tool(
    name="kb_retriever",
    description=(
        "在本地知识库中搜索与主张相关的证据。"
        "输入 claim_extractor 输出的主张列表，返回每个主张的匹配证据，"
        "包含证据来源、原文片段、相关性分数和支持/反驳标记。"
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
                "description": "每条主张最多返回的匹配证据数（默认 3）",
                "default": 3,
            },
        },
        "required": ["claims"],
    },
    run=_kb_retriever,
)
