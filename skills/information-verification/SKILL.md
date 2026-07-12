---
name: information-verification
description: 当用户要求判断网页、事件、评论、传闻、新闻、截图或信息的真假/可信度/事实依据时使用。
---

# 信息核查与网页可信度 Skill

## 何时使用

当用户要求你判断以下内容的真假、可信度、事实依据或可靠性时，先加载并遵循本 Skill：

- 网页、新闻、文章、帖子、公告、截图、视频标题
- 事件、传闻、爆料、评论区说法
- “这是真的吗”“可信度如何”“帮我核查”“还原真相”

## 总原则

- 证据链优先，不凭印象判断。
- 区分原文内容、搜索结果摘要、转载内容、权威来源。
- 如果无法读取原文，要明确说明这一限制。
- 如果证据不足，只能标注“无证据”或“无法验证”，不能把猜测写成事实。
- `claim_extractor` 只是可选辅助工具，不是主张提取的主路径。

## 工具使用策略

### 目标网页正文

1. 优先用 `webpage_reader` 读取目标 URL（`output_format="markdown"` 可获取纯文本）。
2. 如果目标 URL 是 MSN、动态新闻页，或普通读取失败/正文为空，并且工具列表里有 `mcp__firecrawl_scrape`：
   - 对目标 URL 最多调用一次 `mcp__firecrawl_scrape`。
   - 参数使用 `formats=["markdown"]`、`onlyMainContent=true`。
   - 不要重复 scrape 同一目标 URL。
3. 如果 Firecrawl 返回了包含目标文章标题和正文段落的 markdown，就把它视为目标原文。
4. 不要抓取目标页的推荐文章、导航页、广告页或重复转载页来替代目标原文。

### 证据搜索

1. 优先用 `web_search` 或 `mcp__firecrawl_search` 搜索独立证据。
2. 对交叉验证来源，默认只使用标题和摘要判断候选价值。
3. 只有摘要不足以判断关键矛盾，且该来源非常权威时，才抓取 1 个证据页正文。
4. 优先来源顺序：
   - 官方机构、一手公告、政府/监管/气象/法院等原始来源
   - 权威媒体或通讯社
   - 研究机构、论文、数据源
   - 普通转载和自媒体只作弱证据

### 浏览器工具

如果工具列表中存在 `mcp__browser_*`，它们主要用于：

- 查看页面实际状态
- 点击 cookie/继续阅读/展开按钮
- 截图或确认页面是否不可用
- 调试普通读取和 Firecrawl 结果

不要把浏览器快照当作正文抽取的默认路径。

### 结构化验证工具链

信息核查应尽量使用结构化工具链，而不是完全依赖自然语言判断。

优先工具链：

```text
大模型提取 claims
  ↓
kb_retriever / web_search 获取 evidence_results
  ↓
cross_validator 生成 verdicts
  ↓
credibility_scorer 生成 credibility_result
  ↓
report_generator 生成报告草稿
  ↓
你整合为最终 JSON
```

工具使用要求：

- `claim_extractor` 仍只是可选辅助，不是主张提取主路径。
- 只要已经有 `claims` 和 `evidence_results`，且 `cross_validator` 可用，应优先调用 `cross_validator`。
- 只要已经有 `verdicts` 和 `page_metadata`，且 `credibility_scorer` 可用，应优先调用 `credibility_scorer`。
- 只要已经有 `credibility_result`、`page_metadata` 和 `verdicts`，且 `report_generator` 可用，应尽量调用 `report_generator` 生成报告草稿。
- 如果工具因权限层、输入不足或调用失败而无法使用，必须说明限制，并由你基于已获得证据完成兜底判断。

## 工作流程

### 步骤 1：整理输入信息

列出用户给出的所有信息条目，编号为 `信息1`、`信息2`。
对每条信息标注类型：

- 事实陈述
- 个人观点
- 传闻
- 官方声明
- 网页/新闻
- 其他

如果用户只给了一个 URL，就把该 URL 对应网页视为 `信息1`。

### 步骤 2：剔除无效信息

剔除以下内容：

- 纯情绪表达，没有事实内容
- 无来源猜测
- 与事件无关的评论
- 导航、广告、推荐阅读、页脚、天气卡片、站点菜单
- 重复转载标题

对每条剔除内容说明原因。

### 步骤 3：大模型提取原子主张

直接基于目标正文和有效信息，由你自己拆出 1-5 条可验证的原子主张。

不要默认调用 `claim_extractor`。只有当正文很长、你需要粗略定位候选句时才可调用；如果它返回 0 条或返回过长复合句，直接忽略。

原子主张要求：

- 每条只表达一个可验证事实。
- 保留主体、时间、地点、数值、来源。
- 预测类主张必须写明“预计/预测/预警称”，不能当成已发生事实。
- 引述类主张要保留“谁说/谁发布”。
- 不提取标题党情绪词，例如“临时变卦”“炸锅”“冲上热搜”，除非它本身就是要核查的传播现象。

中间主张结构应按以下格式理解和组织：

```json
[
  {
    "id": "claim_1",
    "text": "可独立验证的一句话",
    "claim_type": "numerical|causal|attribution|factual",
    "source_sentence": "原文句子",
    "reason": "为什么这是核心主张"
  }
]
```

后续工具调用时，应把该列表作为 `claims` 输入。

### 步骤 4：搜索证据

围绕每条核心主张搜索证据。
一次任务通常只需要 1-3 个独立权威来源。
如果一个官方来源已经明确支持或反驳核心数据，不要继续抓取大量转载页。

搜索或检索后，应尽量整理成 `evidence_results` 结构，供 `cross_validator` 使用：

```json
[
  {
    "claim_id": "claim_1",
    "matched": true,
    "evidence": [
      {
        "source": "来源标题 | URL",
        "snippet": "与主张相关的证据摘要",
        "relevance_score": 0.8,
        "supports_claim": true
      }
    ]
  }
]
```

如果证据来自 `kb_retriever`，优先直接使用它返回的 `results`。
如果证据来自 `web_search` 或 MCP search，由你整理成上述结构。

### 步骤 5：交叉验证

如果已经有 `claims` 和 `evidence_results`，并且 `cross_validator` 可用，应优先调用：

```text
cross_validator(claims=claims, evidence_results=evidence_results)
```

使用 `cross_validator` 返回的 `verdicts` 作为后续可信度评分依据。

`cross_validator` 输出的状态包括：

- `supported`：有证据支持
- `contradicted`：有证据反驳
- `unsupported`：没有找到相关证据
- `unverifiable`：有相关信息但无法确认支持或反驳

不要把搜索结果摘要中的相似说法自动当成强证据。
官方来源、一手数据和独立权威来源权重更高。

如果没有足够结构化证据，或 `cross_validator` 被权限层拦截/调用失败，则由你手动完成交叉验证，并明确说明未使用工具的原因。

### 步骤 6：可信度判断

如果已经有 `verdicts` 和 `page_metadata`，并且 `credibility_scorer` 可用，应优先调用：

```text
credibility_scorer(verdicts=verdicts, page_metadata=page_metadata)
```

使用 `credibility_scorer` 返回的 `overall_score`、`score_label`、`signals` 和 `verdict_summary` 作为最终可信度判断依据。

如果评分工具不可用，则按以下规则对有效信息标注：

- 高可信度：有可靠来源，核心主张被独立证据支持，无明显反证。
- 中等可信度：部分证据支持，但来源、原文读取、时间或细节仍有限制。
- 低可信度：缺乏可靠来源、与证据冲突、依赖传闻或逻辑明显有问题。

如果目标原文无法完整读取，最终可信度通常不能直接给“高”，除非核心事实被多个高权威来源独立验证。

### 步骤 7：最终输出

如果已经有 `credibility_result`、`page_metadata` 和 `verdicts`，并且 `report_generator` 可用，应尽量调用：

```text
report_generator(
  credibility_result=credibility_result,
  page_metadata=page_metadata,
  verdicts=verdicts,
  claims=claims,
  output_format="markdown"
)
```

`report_generator` 的输出可作为报告草稿和结构化依据，但最终仍必须整理成下面的 JSON。

最终回答必须包含以下 JSON：

```json
{
  "事件": "事件的简要描述",
  "有效信息": [
    {
      "编号": "信息1",
      "内容": "原文摘要",
      "可信度": "高/中/低",
      "理由": "为什么给这个可信度"
    }
  ],
  "剔除信息": [
    {
      "编号": "信息X",
      "内容": "原文摘要",
      "剔除原因": "为什么无效"
    }
  ],
  "可疑点": [
    "可疑点1：...",
    "可疑点2：..."
  ],
  "事件可信度": "高/中/低",
  "事件真相还原": "综合有效信息和证据后，还原的真实情况"
}
```

JSON 后可以附一段简短自然语言总结，但不要改变 JSON 结论。
