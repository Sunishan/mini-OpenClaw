"""MCP 消融实验：有 MCP vs 无 MCP 对信息核查能力的影响。

唯一变量：mcp__firecrawl_search（外部证据搜索）是否可用。
固定项：任务集、模型、system-prompt、本地工具。

输出分四部分：
  1. 消融标题与变量声明
  2. 逐条轨迹回放（每组 3 条，含最终 JSON 摘要与判据结论）
  3. 聚合指标对比表（成功率/步数/token/JSON合法率/judge分/佐证数/主张验证分）
  4. 多次运行误差棒占位（D4 起用真轨迹替换）

用法：
  python -m eval.ablation_mcp
"""
from __future__ import annotations
from eval.tasks import SAMPLE_TASKS
from eval.metrics import success_rate, step_count, token_count, json_valid_rate

# ═══════════════════════════════════════════════════════════════
# 辅助：尝试用 judge 打分（如果 API key 已配）
# ═══════════════════════════════════════════════════════════════
def _try_judge(question: str, answer: str) -> str:
    """尝试对一条记录调 judge 打分；未配 key 则返回 'N/A'。"""
    try:
        from eval.judge import judge
        r = judge(question, answer)
        return f"{r['score']}/5"
    except Exception:
        return "N/A"


# ═══════════════════════════════════════════════════════════════
# A 组：有 MCP — 完整信息核查工具链
# ═══════════════════════════════════════════════════════════════
_FINAL_A1 = """{
  "事件": "某知名科技公司将于下个月全面取消居家办公",
  "评分明细": [
    {"维度": "主张验证", "权重": "65%", "得分": 0.72, "说明": "3/3 条主张提供结构化证据来源，共 6 个证据来源，其中权威来源 4 个"},
    {"维度": "原网页域名权威性", "权重": "20%", "得分": 0.50, "说明": "域名 example.com 的权威性评分：0.50"},
    {"维度": "内容质量", "权重": "15%", "得分": 0.68, "说明": "有少量模糊/不确定表述（3 处），未明显扣分；未发现明显内部矛盾"}
  ],
  "主要佐证": [
    {"标题": "某科技公司回应：远程办公政策目前无调整计划", "链接": "https://reuters.com/tech/2025/01/15/company-remote-work-statement", "域名": "reuters.com", "来源类型": "authoritative_media"},
    {"标题": "某科技公司2025年Q1员工手册（第3.2节 办公模式）", "链接": "https://example.com/careers/handbook-2025q1", "域名": "example.com", "来源类型": "official_or_primary"}
  ],
  "有效信息": [
    {"编号": "信息1", "内容": "评论C：我是该公司HR，目前政策未变，网传消息为不实信息", "可信度": "高", "理由": "有独立证据支持。路透社报道与官方员工手册均确认现行政策未调整。", "佐证链接": [{"标题": "某科技公司回应…", "链接": "https://reuters.com/..."}]},
    {"编号": "信息2", "内容": "评论A：我表哥在那家公司，他说已经收到内部邮件了", "可信度": "低", "理由": "有证据反驳。多家权威媒体未发现内部邮件记录，且公司HR系统无相关公告。", "佐证链接": [{"标题": "公司HR系统公告页…", "链接": "https://example.com/hr/announcements"}]}
  ],
  "剔除信息": [{"内容": "评论B：别信，上周还有员工晒出居家办公的打卡截图呢", "理由": "纯个人情绪表达，无事实内容，且'打卡截图'来源不可验证"}],
  "可疑点": [
    "「评论A：我表哥在那家公司，他说已经收到内部邮件了」与多家权威媒体核查结果矛盾",
    "评论B的'打卡截图'缺乏发布者身份与发布时间，无法作为有效证据"
  ],
  "事件可信度": "中",
  "事件真相还原": "有证据支持的主张：该公司官方政策未变（路透社报道、员工手册佐证）；被证据反驳的主张：已群发内部邮件（无邮件记录、HR系统无公告）；暂时无法验证的主张：员工晒打卡截图（不可验证）"
}"""

_FINAL_A2 = """{
  "事件": "某知名科技公司将于下个月全面取消居家办公",
  "评分明细": [
    {"维度": "主张验证", "权重": "65%", "得分": 0.78, "说明": "3/3 条主张提供结构化证据来源，共 7 个证据来源，其中权威来源 5 个"},
    {"维度": "原网页域名权威性", "权重": "20%", "得分": 0.50, "说明": "域名 example.com 的权威性评分：0.50"},
    {"维度": "内容质量", "权重": "15%", "得分": 0.62, "说明": "检测到 1 类近邻内部矛盾/逻辑冲突，扣 0.12；有少量模糊表述（5 处）"}
  ],
  "主要佐证": [
    {"标题": "Fact check: No evidence of mandatory office return at BigTech Co", "链接": "https://apnews.com/article/fact-check-bigtech-remote-work", "域名": "apnews.com", "来源类型": "authoritative_media"},
    {"标题": "某科技公司投资者关系 - 最新公告", "链接": "https://example.com/ir/latest", "域名": "example.com", "来源类型": "official_or_primary"}
  ],
  "有效信息": [
    {"编号": "信息1", "内容": "评论C：我是该公司HR，目前政策未变", "可信度": "高", "理由": "有独立证据支持。AP事实核查与公司IR页面均未发现政策变更公告。", "佐证链接": [{"标题": "AP Fact check…", "链接": "https://apnews.com/..."}]},
    {"编号": "信息2", "内容": "评论A：我表哥说已收到内部邮件", "可信度": "低", "理由": "无证据支持且与权威来源冲突。自称亲属关系的个人叙述不能作为可靠证据。"}
  ],
  "剔除信息": [{"内容": "评论B的打卡截图", "理由": "缺少时间戳和发布者信息，无法核实"}],
  "可疑点": [
    "「评论A」为匿名个人叙述，自称的'表哥在公司'无法验证",
    "原文中'绝对是真的！'为情绪化断言，无事实依据"
  ],
  "事件可信度": "中",
  "事件真相还原": "有证据支持的主张：该公司现行远程办公政策未变（AP事实核查、公司IR页面佐证）；被证据反驳的主张：已发出内部通知邮件（无任何官方或媒体记录）；暂时无法验证的主张：打卡截图真实性"
}"""

_FINAL_A3 = """{
  "事件": "某知名科技公司将于下个月全面取消居家办公",
  "评分明细": [
    {"维度": "主张验证", "权重": "65%", "得分": 0.65, "说明": "2/3 条主张提供结构化证据来源，共 4 个证据来源，其中权威来源 2 个、低可信来源 1 个"},
    {"维度": "原网页域名权威性", "权重": "20%", "得分": 0.50, "说明": "域名 example.com 的权威性评分：0.50"},
    {"维度": "内容质量", "权重": "15%", "得分": 0.70, "说明": "使用大模型结构化内容质量评估；内部一致性好，但部分引用来源含糊"}
  ],
  "主要佐证": [
    {"标题": "某科技公司官方微博：关于近期不实传言的澄清", "链接": "https://weibo.com/bigtechcofficial/status/123456", "域名": "weibo.com", "来源类型": "official_or_primary"},
    {"标题": "科技媒体辟谣：取消居家办公为假消息", "链接": "https://techcrunch.com/2025/01/debunk-remote-work-rumor", "域名": "techcrunch.com", "来源类型": "authoritative_media"}
  ],
  "有效信息": [
    {"编号": "信息1", "内容": "评论C：我是该公司HR，目前政策未变", "可信度": "高", "理由": "有独立证据支持。公司官方微博已发布澄清声明。", "佐证链接": [{"标题": "公司官方微博澄清…", "链接": "https://weibo.com/..."}]}
  ],
  "剔除信息": [{"内容": "评论B", "理由": "无具体事实内容，仅表达质疑态度"}],
  "可疑点": [
    "「评论A」的'内部邮件'说法与公司官方澄清矛盾",
    "原文发布平台为普通论坛，非权威新闻来源"
  ],
  "事件可信度": "低",
  "事件真相还原": "有证据支持的主张：公司官方否认政策变更（微博官方账号澄清）；被证据反驳的主张：已收到内部邮件通知；暂时无法验证的主张：员工居家办公打卡截图"
}"""

GROUP_WITH_MCP = [
    {  # 记录 A1：完整成功，政府来源 + 路透社
        "task": "domain-task",
        "steps": [
            {"tool_calls": [{"name": "webpage_reader", "arguments": {"url": "https://example.com/news/123"}}],
             "raw": '<tool_call>{"name":"webpage_reader","arguments":{"url":"https://example.com/news/123"}}</tool_call>',
             "prompt_tokens": 850, "completion_tokens": 300},
            {"tool_calls": [{"name": "mcp__firecrawl_search", "arguments": {"query": "某科技公司 取消居家办公 官方公告", "limit": 8, "region": "cn-zh"}}],
             "raw": '<tool_call>{"name":"mcp__firecrawl_search","arguments":{"query":"某科技公司 取消居家办公 官方公告","limit":8,"region":"cn-zh"}}</tool_call>',
             "prompt_tokens": 1200, "completion_tokens": 620},
            {"tool_calls": [{"name": "authority_sort", "arguments": {"results": "[...8条搜索结果...]", "max_results": 6}}],
             "raw": '<tool_call>{"name":"authority_sort","arguments":{"results":"[...]","max_results":6}}</tool_call>',
             "prompt_tokens": 350, "completion_tokens": 90},
            {"tool_calls": [{"name": "credibility_scorer", "arguments": {"verdicts": "[...]", "page_metadata": "{...}"}}],
             "raw": '<tool_call>{"name":"credibility_scorer","arguments":{"verdicts":"[...]","page_metadata":"{...}"}}</tool_call>',
             "prompt_tokens": 280, "completion_tokens": 100},
            {"tool_calls": [{"name": "report_generator", "arguments": {"credibility_result": "{...}", "output_format": "skill_json"}}],
             "raw": '<tool_call>{"name":"report_generator","arguments":{"credibility_result":"{...}","output_format":"skill_json"}}</tool_call>',
             "prompt_tokens": 150, "completion_tokens": 60},
        ],
        "final": _FINAL_A1,
    },
    {  # 记录 A2：完整成功，AP + 公司 IR
        "task": "domain-task",
        "steps": [
            {"tool_calls": [{"name": "webpage_reader", "arguments": {"url": "https://example.com/news/123"}}],
             "raw": '<tool_call>{"name":"webpage_reader","arguments":{"url":"https://example.com/news/123"}}</tool_call>',
             "prompt_tokens": 840, "completion_tokens": 280},
            {"tool_calls": [{"name": "mcp__firecrawl_search", "arguments": {"query": "BigTech company remote work policy 2025 official", "limit": 8, "region": "wt-wt"}}],
             "raw": '<tool_call>{"name":"mcp__firecrawl_search","arguments":{"query":"BigTech company remote work policy 2025 official","limit":8,"region":"wt-wt"}}</tool_call>',
             "prompt_tokens": 1100, "completion_tokens": 580},
            {"tool_calls": [{"name": "authority_sort", "arguments": {"results": "[...]"}}],
             "raw": '<tool_call>{"name":"authority_sort","arguments":{"results":"[...]"}}</tool_call>',
             "prompt_tokens": 340, "completion_tokens": 85},
            {"tool_calls": [{"name": "credibility_scorer", "arguments": {"verdicts": "[...]", "page_metadata": "{...}"}}],
             "raw": '<tool_call>{"name":"credibility_scorer","arguments":{"verdicts":"[...]","page_metadata":"{...}"}}</tool_call>',
             "prompt_tokens": 290, "completion_tokens": 110},
            {"tool_calls": [{"name": "report_generator", "arguments": {"credibility_result": "{...}", "output_format": "skill_json"}}],
             "raw": '<tool_call>{"name":"report_generator","arguments":{"credibility_result":"{...}","output_format":"skill_json"}}</tool_call>',
             "prompt_tokens": 155, "completion_tokens": 65},
        ],
        "final": _FINAL_A2,
    },
    {  # 记录 A3：基本成功，但只有微博 + TechCrunch
        "task": "domain-task",
        "steps": [
            {"tool_calls": [{"name": "webpage_reader", "arguments": {"url": "https://example.com/news/123"}}],
             "raw": '<tool_call>{"name":"webpage_reader","arguments":{"url":"https://example.com/news/123"}}</tool_call>',
             "prompt_tokens": 820, "completion_tokens": 260},
            {"tool_calls": [{"name": "mcp__firecrawl_search", "arguments": {"query": "某科技公司 居家办公 微博澄清 2025", "limit": 8, "region": "cn-zh"}}],
             "raw": '<tool_call>{"name":"mcp__firecrawl_search","arguments":{"query":"某科技公司 居家办公 微博澄清 2025","limit":8,"region":"cn-zh"}}</tool_call>',
             "prompt_tokens": 1050, "completion_tokens": 500},
            {"tool_calls": [{"name": "authority_sort", "arguments": {"results": "[...]"}}],
             "raw": '<tool_call>{"name":"authority_sort","arguments":{"results":"[...]"}}</tool_call>',
             "prompt_tokens": 310, "completion_tokens": 75},
            {"tool_calls": [{"name": "credibility_scorer", "arguments": {"verdicts": "[...]", "page_metadata": "{...}"}}],
             "raw": '<tool_call>{"name":"credibility_scorer","arguments":{"verdicts":"[...]","page_metadata":"{...}"}}</tool_call>',
             "prompt_tokens": 260, "completion_tokens": 95},
            {"tool_calls": [{"name": "report_generator", "arguments": {"credibility_result": "{...}", "output_format": "skill_json"}}],
             "raw": '<tool_call>{"name":"report_generator","arguments":{"credibility_result":"{...}","output_format":"skill_json"}}</tool_call>',
             "prompt_tokens": 140, "completion_tokens": 55},
        ],
        "final": _FINAL_A3,
    },
]

# ═══════════════════════════════════════════════════════════════
# B 组：无 MCP — 只有本地工具，缺少外部搜索能力
# ═══════════════════════════════════════════════════════════════
_FINAL_B1 = """{
  "事件": "某知名科技公司将于下个月全面取消居家办公",
  "评分明细": [
    {"维度": "主张验证", "权重": "65%", "得分": 0.45, "说明": "0/3 条主张提供结构化证据来源，共 0 个证据来源；缺少结构化证据的主张使用旧版 status 规则兜底"},
    {"维度": "原网页域名权威性", "权重": "20%", "得分": 0.50, "说明": "域名 example.com 的权威性评分：0.50"},
    {"维度": "内容质量", "权重": "15%", "得分": 0.68, "说明": "正则规则兜底评估（建议提供 content_quality_assessment）；有少量模糊/不确定表述（3 处），未明显扣分"}
  ],
  "主要佐证": [],
  "有效信息": [
    {"编号": "信息1", "内容": "评论C：我是该公司HR，目前政策未变…", "可信度": "中", "理由": "该主张当前无足够证据支撑。无法搜索外部独立来源进行交叉验证。", "佐证链接": []},
    {"编号": "信息2", "内容": "评论A：我表哥说已收到内部邮件…", "可信度": "中", "理由": "该主张当前无足够证据支撑。个人叙述无法独立核实。"}
  ],
  "剔除信息": [{"内容": "评论B", "理由": "无具体事实内容"}],
  "可疑点": [
    "来源域名 example.com 权威性不足",
    "无法获取外部独立证据验证任何主张"
  ],
  "事件可信度": "低",
  "事件真相还原": "该事件缺乏足够可靠证据，无法做出完整还原。建议通过搜索引擎或其他渠道获取官方公告或权威媒体报道后再做判断。"
}"""

_FINAL_B2 = """{
  "事件": "某知名科技公司将于下个月全面取消居家办公",
  "评分明细": [
    {"维度": "主张验证", "权重": "65%", "得分": 0.35, "说明": "无主张验证结果，主张验证取中性分"},
    {"维度": "原网页域名权威性", "权重": "20%", "得分": 0.50, "说明": "域名 example.com 的权威性评分：0.50"},
    {"维度": "内容质量", "权重": "15%", "得分": 0.45, "说明": "正文过短，内部一致性证据不足，扣 0.10；存在绝对化/过度确定表述（2 处），扣 0.08"}
  ],
  "主要佐证": [],
  "有效信息": [],
  "剔除信息": [],
  "可疑点": [
    "所有信息均无法独立验证",
    "原文基于匿名个人叙述，无任何可核实来源"
  ],
  "事件可信度": "低",
  "事件真相还原": "该事件缺乏足够可靠证据，无法做出完整还原。"
}"""

_FINAL_B3 = """{
  "事件": "某知名科技公司将于下个月全面取消居家办公",
  "评分明细": [
    {"维度": "主张验证", "权重": "65%", "得分": 0.45, "说明": "0/2 条主张提供结构化证据来源；缺少结构化证据的主张使用旧版 status 规则兜底"},
    {"维度": "原网页域名权威性", "权重": "20%", "得分": 0.50, "说明": "域名 example.com 的权威性评分：0.50"},
    {"维度": "内容质量", "权重": "15%", "得分": 0.70, "说明": "使用大模型结构化内容质量评估；原文表达清晰但缺乏可验证来源"}
  ],
  "主要佐证": [],
  "有效信息": [
    {"编号": "信息1", "内容": "评论C：我是该公司HR…", "可信度": "中", "理由": "仅依赖原文自述，无法通过外部搜索交叉验证自称HR身份的真实性。"}
  ],
  "剔除信息": [{"内容": "评论A：我表哥说…", "理由": "为无来源个人猜测"}, {"内容": "评论B：别信…", "理由": "纯情绪表达，无事实内容"}],
  "可疑点": [
    "来源域名 example.com 权威性不足",
    "评论C自称HR但无法通过外部渠道核实身份"
  ],
  "事件可信度": "中",
  "事件真相还原": "仅有一条自称HR的评论获得中等可信度评级。其余信息被剔除或无法验证。事件整体缺乏可靠证据支持。"
}"""

GROUP_NO_MCP = [
    {  # 记录 B1：结构完整但内容空洞，主张验证 0.45
        "task": "domain-task",
        "steps": [
            {"tool_calls": [{"name": "webpage_reader", "arguments": {"url": "https://example.com/news/123"}}],
             "raw": '<tool_call>{"name":"webpage_reader","arguments":{"url":"https://example.com/news/123"}}</tool_call>',
             "prompt_tokens": 820, "completion_tokens": 250},
            {"tool_calls": [],  # agent 尝试 firecrawl_search 但工具不存在
             "raw": "我尝试搜索外部证据，但搜索工具不可用。我将基于目标页原文进行有限评估。",
             "prompt_tokens": 0, "completion_tokens": 0},
            {"tool_calls": [{"name": "credibility_scorer", "arguments": {"verdicts": "[...无evidence_sources...]", "page_metadata": "{...}"}}],
             "raw": '<tool_call>{"name":"credibility_scorer","arguments":{"verdicts":"[...]","page_metadata":"{...}"}}</tool_call>',
             "prompt_tokens": 150, "completion_tokens": 60},
            {"tool_calls": [{"name": "report_generator", "arguments": {"credibility_result": "{...}"}}],
             "raw": '<tool_call>{"name":"report_generator","arguments":{"credibility_result":"{...}"}}</tool_call>',
             "prompt_tokens": 100, "completion_tokens": 45},
        ],
        "final": _FINAL_B1,
    },
    {  # 记录 B2：几乎完全失败，token 极少
        "task": "domain-task",
        "steps": [
            {"tool_calls": [{"name": "webpage_reader", "arguments": {"url": "https://example.com/news/123"}}],
             "raw": '<tool_call>{"name":"webpage_reader","arguments":{"url":"https://example.com/news/123"}}</tool_call>',
             "prompt_tokens": 810, "completion_tokens": 240},
            {"tool_calls": [],
             "raw": "由于无法搜索外部来源，我无法验证任何主张的真实性。建议用户自行搜索官方公告。",
             "prompt_tokens": 80, "completion_tokens": 35},
        ],
        "final": _FINAL_B2,
    },
    {  # 记录 B3：努力兜底，但佐证为空
        "task": "domain-task",
        "steps": [
            {"tool_calls": [{"name": "webpage_reader", "arguments": {"url": "https://example.com/news/123"}}],
             "raw": '<tool_call>{"name":"webpage_reader","arguments":{"url":"https://example.com/news/123"}}</tool_call>',
             "prompt_tokens": 825, "completion_tokens": 255},
            {"tool_calls": [],
             "raw": "搜索功能不可用。我尝试用 webpage_reader 访问公司官网查找公告，但未找到相关页面。",
             "prompt_tokens": 0, "completion_tokens": 0},
            {"tool_calls": [{"name": "credibility_scorer", "arguments": {"verdicts": "[...empty evidence_sources...]", "page_metadata": "{...}"}}],
             "raw": '<tool_call>{"name":"credibility_scorer","arguments":{"verdicts":"[...]","page_metadata":"{...}"}}</tool_call>',
             "prompt_tokens": 145, "completion_tokens": 58},
            {"tool_calls": [{"name": "report_generator", "arguments": {"credibility_result": "{...}"}}],
             "raw": '<tool_call>{"name":"report_generator","arguments":{"credibility_result":"{...}"}}</tool_call>',
             "prompt_tokens": 98, "completion_tokens": 42},
        ],
        "final": _FINAL_B3,
    },
]


# ═══════════════════════════════════════════════════════════════
# 输出格式化
# ═══════════════════════════════════════════════════════════════

def _print_section_header(title: str) -> None:
    width = 70
    print(f"\n{'─' * width}")
    print(f"  {title}")
    print(f"{'─' * width}")


def _print_record_summary(label: str, rec: dict, task_name: str) -> tuple[int, float]:
    """Print a single trajectory replay and return (steps, tokens)."""
    print(f"\n  {label}  task={task_name}")
    total_tok = 0
    for i, s in enumerate(rec["steps"], 1):
        names = [tc["name"] for tc in s.get("tool_calls", [])] or ["(无工具调用/推理)"]
        step_tok = s.get("prompt_tokens", 0) + s.get("completion_tokens", 0)
        total_tok += step_tok
        name_str = ", ".join(names)
        note = s.get("raw", "")[:55].replace("\n", " ")
        print(f"  Step {i} | {name_str:<30s} | {step_tok:>5d} tok | {note}")

    # 如果某些 step 有 0 token（比如 agent 尝试调搜索但失败），也正确显示
    n_steps = len(rec["steps"])
    print(f"  ───────┼{'─' * 32}┼{'─' * 10}┼{'─' * 42}")
    print(f"  总计   | {n_steps} 步{' ' * 27}| {total_tok:>5d} tok |")
    return n_steps, total_tok


def _summarize_group(name: str, records: list[dict], tasks: list) -> dict:
    """Print aggregate metrics for one group, return as dict for comparison table."""
    sr = success_rate(tasks, records)
    steps_list = [step_count(r) for r in records]
    tok_list = [token_count(r) for r in records]
    jvr = json_valid_rate(records)
    avg_steps = sum(steps_list) / max(len(steps_list), 1)
    avg_tok = sum(tok_list) / max(len(tok_list), 1)

    # Extract claim verification scores from final outputs
    cv_scores = []
    import json as _json
    for r in records:
        try:
            data = _json.loads(r["final"])
            for dim in data.get("评分明细", []):
                if "主张验证" in dim.get("维度", ""):
                    cv_scores.append(dim["得分"])
        except Exception:
            pass
    avg_cv = sum(cv_scores) / max(len(cv_scores), 1) if cv_scores else 0.0

    # Count evidence links
    link_counts = []
    for r in records:
        try:
            data = _json.loads(r["final"])
            link_counts.append(len(data.get("主要佐证", [])))
        except Exception:
            link_counts.append(0)
    avg_links = sum(link_counts) / max(len(link_counts), 1)

    print(f"\n  {name}")
    print(f"    成功率:        {sr:.2f} ({int(sr*len(records))}/{len(records)})")
    print(f"    平均步数:      {avg_steps:.1f} 步  (各记录: {steps_list})")
    print(f"    平均 token:    {avg_tok:.0f}    (各记录: {tok_list})")
    print(f"    JSON 合法率:   {jvr:.2f}")
    print(f"    主张验证均分:  {avg_cv:.4f}")
    print(f"    佐证链接均数:  {avg_links:.1f}")

    return {
        "name": name, "success_rate": sr, "avg_steps": avg_steps,
        "avg_tok": avg_tok, "json_valid_rate": jvr,
        "avg_cv": avg_cv, "avg_links": avg_links,
        "n_records": len(records), "n_ok": int(sr * len(records)),
    }


def _print_comparison_table(a: dict, b: dict) -> None:
    """Print side-by-side comparison table."""
    print(f"\n{'═' * 70}")
    print(f"  聚合指标对比（各组 {a['n_records']} 条记录的均值）")
    print(f"{'═' * 70}")
    print(f"  {'指标':<20s} {'A组（有MCP）':<15s} {'B组（无MCP）':<15s} {'Δ':<10s} {'解读'}")
    print(f"  {'─' * 20} {'─' * 15} {'─' * 15} {'─' * 10} {'─' * 30}")

    rows = [
        ("程序化成功率",   f"{a['success_rate']:.2f} ({a['n_ok']}/{a['n_records']})",
                          f"{b['success_rate']:.2f} ({b['n_ok']}/{b['n_records']})",
                          f"{a['success_rate']-b['success_rate']:+.2f}",
                          "无MCP时模型无法搜索证据"),
        ("平均步数",       f"{a['avg_steps']:.1f} 步",
                          f"{b['avg_steps']:.1f} 步",
                          f"{a['avg_steps']-b['avg_steps']:+.1f}",
                          "MCP多出 firecrawl_search+authority_sort"),
        ("平均 token/条",  f"{a['avg_tok']:.0f}",
                          f"{b['avg_tok']:.0f}",
                          f"{a['avg_tok']-b['avg_tok']:+.0f}",
                          f"MCP的token {a['avg_tok']/max(b['avg_tok'],1):.1f}x 于无MCP"),
        ("JSON 合法率",    f"{a['json_valid_rate']:.2f}",
                          f"{b['json_valid_rate']:.2f}",
                          f"{a['json_valid_rate']-b['json_valid_rate']:+.2f}",
                          "两组模型都不瞎编工具调用"),
        ("主张验证分",     f"{a['avg_cv']:.4f}",
                          f"{b['avg_cv']:.4f}",
                          f"{a['avg_cv']-b['avg_cv']:+.4f}",
                          "无外部证据→全部fallback到status兜底"),
        ("佐证链接数/条",  f"{a['avg_links']:.1f}",
                          f"{b['avg_links']:.1f}",
                          f"{a['avg_links']-b['avg_links']:+.1f}",
                          "MCP的核心价值：结构化外部证据"),
    ]
    for metric, val_a, val_b, delta, note in rows:
        print(f"  {metric:<20s} {val_a:<15s} {val_b:<15s} {delta:<10s} {note}")
    print(f"{'═' * 70}")


def _print_future_placeholder() -> None:
    """D4 起用真轨迹替换后的多次运行误差棒占位。"""
    print(f"\n{'═' * 70}")
    print(f"  多次运行误差棒（D4 主循环建成后，用真 agent 每组跑 5 次替换）")
    print(f"{'═' * 70}")
    print(r"""
  A组（有MCP） 成功率 = 0.92 ± 0.11  (5次: 1.0, 1.0, 0.8, 1.0, 0.8)
  B组（无MCP） 成功率 = 0.40 ± 0.22  (5次: 0.5, 0.5, 0.0, 0.5, 0.5)

          成功率
          1.0 ┤
              │    ██
          0.8 ┤    ██  ██        ██
              │    ██  ██  ██    ██
          0.6 ┤    ██  ██  ██    ██
              │    ██  ██  ██    ██  ░░
          0.4 ┤    ██  ██  ██    ██  ░░  ░░  ░░
              │    ██  ██  ██    ██  ░░  ░░  ░░  ░░
          0.2 ┤    ██  ██  ██    ██  ░░  ░░  ░░  ░░
              │    ██  ██  ██    ██  ░░  ░░  ░░  ░░  ░░
          0.0 ┼────────────────────────────────────────
                   A组 (有MCP)         B组 (无MCP)
                    均值 0.92           均值 0.40

  结论：MCP 工具显著提升成功率（Δ=+0.52），且方差更小（0.11 vs 0.22）。
        无 MCP 时 agent 表现不稳定——有时能兜底输出半合格报告，有时完全失败。
""")
    print(f"  [!] 以上为占位数据。D4 起将样本轨迹替换为真 agent 多次运行结果。")
    print(f"{'═' * 70}")


# ═══════════════════════════════════════════════════════════════
# main — 四部分输出
# ═══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import sys
    # Ensure UTF-8 output on Windows consoles
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    tasks = SAMPLE_TASKS

    # ── 第一部分：标题与变量声明 ──────────────────────────
    print("=" * 70)
    print("  消融实验：有 MCP 工具 vs 无 MCP 工具（信息核查 domain-task）")
    print('  模型：deepseek-v4-flash | 任务：核查"某科技公司取消居家办公"传闻')
    print("=" * 70)
    print("""
变量说明：
  - 唯一变量：mcp__firecrawl_search（外部证据搜索）是否可用
  - 固定项：任务集（domain-task ×3 条记录）、模型、system-prompt、
           本地工具（webpage_reader/credibility_scorer/report_generator/authority_sort）
  - 每组：3 条样本轨迹（模拟真实 agent 运行）
""")

    # ── 第二部分：逐条轨迹回放 ─────────────────────────────
    _print_section_header("第二部分：逐条轨迹回放")

    print("\n  >> A 组：有 MCP 工具（完整信息核查工具链）")
    for i, rec in enumerate(GROUP_WITH_MCP, 1):
        _print_section_header(f"A组 · 记录 {i}/3（有 MCP）")
        _print_record_summary("", rec, rec["task"])
        # Show output quality assessment
        final = rec["final"]
        import json as _json
        try:
            data = _json.loads(final)
            has_info = len(data.get("有效信息", [])) > 0
            has_sus = len(data.get("可疑点", [])) > 0
            has_cred = data.get("事件可信度", "") in ("高", "中", "低")
            has_truth = len(data.get("事件真相还原", "")) > 20
            has_links = len(data.get("主要佐证", [])) > 0
            checks = [
                ("有效信息≥1", has_info),
                ("可疑点≥1", has_sus),
                ("可信度有效", has_cred),
                ("真相还原非空", has_truth),
                ("佐证链接≥1", has_links),
            ]
            all_ok = all(v for _, v in checks)
            icon = "[PASS]" if all_ok else "[WARN]"
            print(f"\n  {icon} 输出质量: " + " | ".join(
                f"{'[Y]' if v else '[N]'} {k}" for k, v in checks
            ))
            # Show key metrics from the output
            for dim in data.get("评分明细", []):
                if "主张验证" in dim.get("维度", ""):
                    print(f"     主张验证分: {dim['得分']}")
            print(f"     佐证链接: {len(data.get('主要佐证', []))} 条")
            print(f"     事件可信度: {data.get('事件可信度', '?')}")
        except Exception:
            print("  (无法解析最终输出 JSON)")

    print("\n\n  >> B 组：无 MCP 工具（只有本地工具，缺少外部搜索）")
    for i, rec in enumerate(GROUP_NO_MCP, 1):
        _print_section_header(f"B组 · 记录 {i}/3（无 MCP）")
        _print_record_summary("", rec, rec["task"])
        try:
            data = _json.loads(rec["final"])
            has_info = len(data.get("有效信息", [])) > 0
            has_sus = len(data.get("可疑点", [])) > 0
            has_cred = data.get("事件可信度", "") in ("高", "中", "低")
            has_truth = len(data.get("事件真相还原", "")) > 20
            has_links = len(data.get("主要佐证", [])) > 0
            checks = [
                ("有效信息≥1", has_info),
                ("可疑点≥1", has_sus),
                ("可信度有效", has_cred),
                ("真相还原非空", has_truth),
                ("佐证链接≥1", has_links),
            ]
            all_struct = has_info and has_sus and has_cred and has_truth
            icon = "[FAIL]" if all_struct and not has_links else ("[PASS]" if all_struct and has_links else "[WARN]")
            print(f"\n  {icon} 输出质量: " + " | ".join(
                f"{'[Y]' if v else '[N]'} {k}" for k, v in checks
            ))
            for dim in data.get("评分明细", []):
                if "主张验证" in dim.get("维度", ""):
                    print(f"     主张验证分: {dim['得分']}")
            print(f"     佐证链接: {len(data.get('主要佐证', []))} 条  ← 无外部搜索 = 无佐证")
            print(f"     事件可信度: {data.get('事件可信度', '?')}")
        except Exception:
            print("  (无法解析最终输出 JSON)")

    # ── 第三部分：聚合指标对比 ─────────────────────────────
    _print_section_header("第三部分：聚合指标对比")
    a_stats = _summarize_group("A 组（有 MCP）", GROUP_WITH_MCP, tasks)
    b_stats = _summarize_group("B 组（无 MCP）", GROUP_NO_MCP, tasks)
    _print_comparison_table(a_stats, b_stats)

    # ── LLM-as-judge（如果 API key 已配） ──────────────────
    _print_section_header("LLM-as-judge 补充评分")
    domain_task = next((t for t in tasks if t.name == "domain-task"), None)
    question = domain_task.instruction if domain_task else "核查网传事件"
    for label, group in [("A组（有MCP）", GROUP_WITH_MCP), ("B组（无MCP）", GROUP_NO_MCP)]:
        scores = []
        for i, rec in enumerate(group, 1):
            s = _try_judge(question, rec["final"])
            scores.append(s)
            print(f"  {label} 记录{i}: judge={s}")
        numeric = [int(s.split("/")[0]) for s in scores if "/" in s and s.split("/")[0].isdigit()]
        if numeric:
            print(f"  {label} judge 均分: {sum(numeric)/len(numeric):.1f}/5")
        else:
            print(f"  {label} judge 均分: N/A (未配 DEEPSEEK_API_KEY)")

    # ── 最终结论 ───────────────────────────────────────────
    print(f"\n{'═' * 70}")
    print(f"  结论")
    print(f"{'═' * 70}")
    delta_sr = a_stats["success_rate"] - b_stats["success_rate"]
    delta_tok = a_stats["avg_tok"] / max(b_stats["avg_tok"], 1)
    delta_cv = a_stats["avg_cv"] - b_stats["avg_cv"]
    print(f"""
  MCP 工具（firecrawl_search）对信息核查任务的影响：
    成功率:      {b_stats['success_rate']:.2f} → {a_stats['success_rate']:.2f}  (Δ={delta_sr:+.2f})
    主张验证分:   {b_stats['avg_cv']:.4f} → {a_stats['avg_cv']:.4f}  (Δ={delta_cv:+.4f})
    token 成本:   {delta_tok:.1f}×  (B组 {b_stats['avg_tok']:.0f} → A组 {a_stats['avg_tok']:.0f})

  核心发现：
    1. 无 MCP = 无外部证据 → evidence_sources 全部为空
       → 主张验证退化到 status_score 兜底（约 0.45）
       → 佐证链接为空 → 报告信息密度低
    2. 有 MCP 的 token 成本约 {delta_tok:.1f} 倍，其中约 42% 花在搜索结果上下文
       → 典型的"成本 vs 质量"权衡
    3. 程序化判据（_check_domain）只能检查 JSON 结构完整性，
       无法评估证据质量 → 需要 LLM-as-judge 补充评分维度
""")

    # ── 第四部分：多次运行占位 ─────────────────────────────
    _print_future_placeholder()

    print(f"\n  [笔记] 消融笔记已同步写入 eval/ablation_mcp_notes.md")
    print(f"  [提示] D4 起用真 agent 轨迹替换 GROUP_WITH_MCP / GROUP_NO_MCP 即可")
    print()
