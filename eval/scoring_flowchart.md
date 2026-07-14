# credibility_scorer 评分流程图

```mermaid
flowchart TD
    START["_credibility_scorer(verdicts, page_metadata)"]
    START --> SPLIT["page_metadata 分两路"]

    SPLIT --> DA["_get_domain_authority(domain)"]
    DA --> DA_SCORE["da_score = 0.10~0.95"]
    DA --> DA_DETAIL["查 HIGH_AUTHORITY_DOMAINS<br/>查 AUTHORITATIVE_MEDIA_DOMAINS<br/>查 LOW_AUTHORITY_DOMAINS"]

    SPLIT --> CQ["_score_content_quality(page_metadata)"]
    CQ --> CQ_DETAIL["1. 内部矛盾 → _count_nearby_contradictions → −0.30封顶"]
    CQ_DETAIL --> CQ_DETAIL2["2. 模糊表述 → _count_pattern_terms → −0.30封顶"]
    CQ_DETAIL2 --> CQ_DETAIL3["3. 提示注入 → 7种模式检测 → −0.45封顶"]
    CQ_DETAIL3 --> CQ_DETAIL4["4. 绝对化用语 → −0.15封顶"]
    CQ_DETAIL4 --> CQ_SCORE["cq_score = 0~1"]

    START --> CV["_score_claim_verification(verdicts)"]
    CV --> LOOP["for verdict in verdicts:"]
    LOOP --> WEIGHT["weight = _claim_importance_weight(verdict)"]
    WEIGHT --> WEIGHT_VAL["core=1.0 | key_detail=0.7<br/>background=0.3 | minor=0.15"]

    LOOP --> SINGLE["_score_verdict_with_evidence(verdict)"]
    SINGLE --> CHECK_SRC{"evidence_sources<br/>为空？"}

    CHECK_SRC -->|"是 → 兜底"| STATUS_SCORE["_status_score(status)"]
    STATUS_SCORE --> STATUS_VAL["supported=0.50<br/>contradicted=0.20<br/>unsupported=0.35<br/>unverifiable=0.45"]
    STATUS_VAL --> VERDICT_SCORE["verdict_score = status_score"]

    CHECK_SRC -->|"否 → 计算"| FOR_SRC["for source in evidence_sources:"]
    FOR_SRC --> AUTH["authority = _score_evidence_source(source)"]
    AUTH --> AUTH_PATH["优先: source.authority_score<br/>其次: source.source_type<br/>兜底: _get_domain_authority(domain)"]

    FOR_SRC --> REL["relevance_score = _score_evidence_relevance(source)"]
    REL --> REL_DEF["有则读，无则默认 0.75"]

    FOR_SRC --> CONF["relation_confidence = _score_relation_confidence(source)"]
    CONF --> CONF_DEF["有则读，无则默认 0.75"]

    AUTH --> STRENGTH
    REL --> STRENGTH
    CONF --> STRENGTH

    STRENGTH["strength = relevance_score × authority × relation_confidence"]

    FOR_SRC --> RELATION["relation = _evidence_relation(source, verdict.status)"]
    RELATION --> REL_DETAIL["以 verdict.status 为准：<br/>supported → support<br/>contradicted → contradict<br/>unsupported → neutral"]

    STRENGTH --> SPLIT_EVID{"relation ?"}

    SPLIT_EVID -->|"support"| SUPP["support_scores.append(strength)<br/>support_domains.add(domain)"]
    SPLIT_EVID -->|"contradict"| CONT["contradict_scores.append(strength)"]
    SPLIT_EVID -->|"neutral"| DISCARD["丢弃"]

    SUPP --> TOP_SUPP["support_strength = _top_strength(support_scores)"]
    TOP_SUPP --> TOP_DETAIL["top3 指数衰减加权：<br/>s1×1.0 + s2×0.6 + s3×0.36<br/>÷ (1.0+0.6+0.36)"]

    CONT --> TOP_CONT["contradict_strength = _top_strength(contradict_scores)"]

    TOP_SUPP --> BONUS["_source_diversity_bonus(len(support_scores), len(support_domains))"]
    BONUS --> BONUS_DETAIL["≥2个不同域名：<br/>support_strength ×= 1+0.08×log₂(n_domains)<br/>封顶 ×1.15"]

    BONUS --> VERDICT_EQ["verdict_score = 0.45 + 0.5 × (support_strength − contradict_strength)"]
    TOP_CONT --> VERDICT_EQ
    VERDICT_EQ --> VERDICT_SCORE2["clamp(verdict_score, 0, 1)"]

    VERDICT_SCORE2 --> WEIGHTED["weighted_total += verdict_score × weight<br/>weight_total += weight"]
    VERDICT_SCORE --> WEIGHTED

    WEIGHTED --> NEXT{"还有<br/>下一条？"}
    NEXT -->|"是"| LOOP
    NEXT -->|"否"| CV_SCORE["cv_score = weighted_total / weight_total"]

    CV_SCORE --> CAP{"_has_strong_core_contradiction？<br/>core + contradicted +<br/>authority≥0.85 + relevance×confidence≥0.6"}
    CAP -->|"是"| CAP_APPLY["cv_score = min(cv_score, 0.40)"]
    CAP -->|"否"| CV_DONE["cv_score 不变"]

    CAP_APPLY --> CV_DONE

    DA_SCORE --> FINAL
    CQ_SCORE --> FINAL
    CV_DONE --> FINAL

    FINAL["overall = 0.65 × cv_score + 0.20 × da_score + 0.15 × cq_score"]
    FINAL --> LABEL{"overall ≥ 0.70 ?"}
    LABEL -->|"是"| HIGH["score_label = 'High Credibility'"]
    LABEL -->|"否"| MEDIUM{"overall ≥ 0.40 ?"}
    MEDIUM -->|"是"| MID["score_label = 'Medium Credibility'"]
    MEDIUM -->|"否"| LOW["score_label = 'Low Credibility'"]

    HIGH --> OUTPUT["输出 CredibilityResult:<br/>overall_score, score_label,<br/>signals(claim_verification,<br/>domain_authority, content_quality),<br/>verdict_summary"]
    MID --> OUTPUT
    LOW --> OUTPUT

    style STRENGTH fill:#1a56db,color:#fff
    style VERDICT_EQ fill:#1a56db,color:#fff
    style FINAL fill:#dc2626,color:#fff
    style STATUS_SCORE fill:#f59e0b,color:#000
    style CV_SCORE fill:#059669,color:#fff
```

## 关键变量说明

| 变量 | 类型 | 范围 | 含义 |
|------|------|:---:|------|
| `strength` | float | 0~1 | 单条证据的影响力强度 |
| `authority` (`authority_score`) | float | 0.10~0.95 | 证据来源权威性 |
| `relevance_score` | float | 0~1 | 证据与主张的相关程度 |
| `relation_confidence` | float | 0~1 | 支持/反驳判断的置信度 |
| `support_strength` | float | 0~1 | 所有支持证据聚合后的强度 |
| `contradict_strength` | float | 0~1 | 所有反驳证据聚合后的强度 |
| `verdict_score` | float | 0~1 | 单条主张得分 |
| `importance_weight` | float | 0.15~1.0 | 主张重要性权重 |
| `cv_score` | float | 0~1 | 主张验证总分（封顶0.40） |
| `da_score` | float | 0.10~0.95 | 域名权威分 |
| `cq_score` | float | 0~1 | 内容质量分 |
| `overall` | float | 0~1 | 综合可信度 |

## 关键函数说明

| 函数 | 作用 |
|------|------|
| `_score_evidence_source(source)` | 返回单条证据的权威性，按 authority_score → source_type → 域名白名单三级降级 |
| `_score_evidence_relevance(source)` | 返回证据相关性，有则读、无则默认 0.75 |
| `_score_relation_confidence(source)` | 返回关系置信度，有则读、无则默认 0.75 |
| `_evidence_relation(source, status)` | 返回 support/contradict/neutral，以 verdict.status 为准 |
| `_top_strength(scores)` | top-3 指数衰减聚合，γ=0.6，最强者权重最高不稀释 |
| `_source_diversity_bonus(n_scores, n_domains)` | 多源互证加分，≥2 域名触发，封顶 ×1.15 |
| `_status_score(status)` | 无 evidence_sources 时的兜底分 |
| `_claim_importance_weight(verdict)` | 按 claim_role 或 importance_weight 返回权重 |
| `_has_strong_core_contradiction(verdict)` | 检测 core 主张是否被高权威证据强力反驳 |
| `_get_domain_authority(domain)` | 域名查白/黑名单，返回权威分 |
| `_score_content_quality(meta)` | 从正文检测矛盾/模糊/注入/绝对化，只扣分 |
| `_score_claim_verification(verdicts)` | 所有主张加权聚合，返回总分 |
| `_credibility_scorer(verdicts, page_metadata)` | 主入口，调用以上全部，返回最终 JSON |
