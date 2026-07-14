# 消融笔记：有/无 MCP 工具对信息核查能力的影响（Day3 · 样本轨迹）

## 变量
- 唯一变量：`mcp__firecrawl_search`（外部证据搜索 MCP）是否可用
- 不可用时，agent 仍能调用所有本地工具（webpage_reader / credibility_scorer /
  report_generator / authority_sort）

## 固定项
- 模型：deepseek-v4-flash
- 任务：domain-task（核查"某科技公司取消居家办公"传闻及三条评论）
- system-prompt：完整的 information-verification skill（包含强制工具链门禁）
- 本地工具：webpage_reader / credibility_scorer / report_generator / authority_sort

## 结果

| 指标 | 有 MCP | 无 MCP | 差异 |
|------|--------|--------|------|
| 程序化成功率 | 1.00 | 0.33 | Δ=+0.67 |
| LLM-judge 均分 (1-5) | 4.3 | 2.0 | Δ=+2.3 |
| 平均 token | 3850 | 1180 | 3.3× |
| 平均步数 | 5.0 | 3.3 | +1.7 |
| 主张验证分 | 0.72 | 0.45 | Δ=+0.27 |
| 佐证链接数 | 2~3 | 0 | — |

## 归因

1. **无 MCP = 无外部证据**：agent 缺少搜索能力后，verdicts 的 evidence_sources
   全部为空，credibility_scorer 退化到 status_score 兜底值（supported=0.50、
   unverifiable=0.45），主张验证分被压在 0.45 左右。

2. **有 MCP 的 token 成本是 3.3 倍**：firecrawl_search 返回的搜索结果（摘要 +
   URL）消耗大量 prompt token。每条记录中约 42% 的 token 花在搜索结果上下文中。
   这是一个典型的"成本 vs 质量"权衡。

3. **程序化判据的盲区**：_check_domain 只检查输出 JSON 的结构完整性（有无有效信息、
   可疑点、可信度标签、真相还原），不检查证据质量和佐证链接。这导致 B 组某些记录
   "结构合法但内容空洞"也能通过判据。LLM-as-judge 的 2.0 vs 4.3 分差更能反映
   真实的信息质量差距。

4. **authority_sort 在没有搜索结果时是空操作**：B 组的 agent 仍然可以调用
   authority_sort，但没有搜索结果可排，这是浪费的一步。

## 局限

- **样本量小**（各组 3 条，手工构造）：结论不能做统计推断。D4 主循环建成后应
  每组至少跑 5 次真轨迹，并报告均值 ± 标准差。
- **只有 1 个任务**：domain-task 是信息核查任务，结论不能外推到其他任务类型
  （如代码生成、文件操作）。
- **只消融了 firecrawl_search**：未单独消融 firecrawl_scrape 和 browser 类
  MCP。后续可以进一步分解"搜索 MCP vs 抓取 MCP vs 浏览器 MCP"。
- **样本轨迹是构造的**：真实 agent 的行为变异（搜索词选择、交叉验证推理质量）
  未体现在构造数据中。D4 起用真轨迹替换。
- **token 计数是估算值**：构造轨迹中的 token 数是根据典型搜索返回长度估算的，
  真实值依赖搜索结果页的具体大小。

## 对 D5 的启示

D5 的上下文压缩实验可以基于这个消融框架扩展：
- 变量从"有/无 MCP"改为"压缩预算 4K / 8K / 12K / 无限"
- 观察点：随着预算收紧，成功率和 judge 分如何退化
- 这直接复用今天的 metric + judge + tracer 基础设施
