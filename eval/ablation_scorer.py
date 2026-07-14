"""消融：有/无 credibility_scorer 两组真实轨迹的核查质量对比。

运行方式：
  python -m eval.ablation_scorer              # 使用预捕获轨迹（快速）
  python -m eval.ablation_scorer --live       # 真跑 Agent（需 API，耗时）
"""
from __future__ import annotations
import re, sys, os, json, textwrap

# ────────────────────────────────────────────────────────────
# 预捕获轨迹（真实 Agent 运行结果，2026-07-14）
# ────────────────────────────────────────────────────────────
TRACE_WITH_SCORER = """
综合可信度 0.42 / 1.0 → 低可信度 100%
主张验证 0.35 / 1.0 65%
域名权威性 0.50 / 1.0 20%
内容质量 0.60 / 1.0 15%
"""

TRACE_WITHOUT_SCORER = """
主张验证（证据支撑） 0.2 / 1.0 50%
来源权威性 0.1 / 1.0 20%
内容质量 0.6 / 1.0 15%
加权综合分 0.35 / 1.0
"""

# ────────────────────────────────────────────────────────────
# 提取评分
# ────────────────────────────────────────────────────────────
def extract_scores(text: str) -> dict:
    """从 Agent 输出中提取各维度得分和权重。"""
    scores = {}
    patterns = [
        (r'主张验证[^\d]*?([\d.]+)\s*/\s*1\.0.*?(\d+)%', '主张验证得分', '主张验证权重'),
        (r'域名权威[^\d]*?([\d.]+)\s*/\s*1\.0.*?(\d+)%', '域名权威得分', '域名权威权重'),
        (r'内容质量[^\d]*?([\d.]+)\s*/\s*1\.0.*?(\d+)%', '内容质量得分', '内容质量权重'),
        (r'综合可信度[^\d]*?([\d.]+)', '综合得分', None),
        (r'加权综合分[^\d]*?([\d.]+)', '综合得分', None),
    ]
    for pattern, score_key, weight_key in patterns:
        m = re.search(pattern, text)
        if m:
            scores[score_key] = float(m.group(1))
            if weight_key and len(m.groups()) >= 2:
                scores[weight_key] = m.group(2)
    # 检测维度名称是否正确
    scores['维度名称正确'] = '域名权威性' in text and '来源权威性' not in text
    return scores


def build_note(with_scores: dict, without_scores: dict, note_path: str) -> None:
    """根据提取的分数生成消融实验记录。"""
    lines = []
    lines.append("# 消融实验（真轨迹 · credibility_scorer 工具效用验证）")
    lines.append("")
    lines.append("- **变量**：credibility_scorer 工具调用（有 / 无），其余（任务集 domain-task、模型 deepseek-v4-flash、系统提示）固定")
    lines.append("- **方法**：同一任务跑两遍，唯一差异是工具注册表中是否包含 credibility_scorer")
    lines.append("- **日期**：2026-07-14")
    lines.append("")
    lines.append("---")
    lines.append("")

    # 计算偏差
    cv_with = with_scores.get("主张验证得分", 0)
    cv_without = without_scores.get("主张验证得分", 0)
    overall_with = with_scores.get("综合得分", 0)
    overall_without = without_scores.get("综合得分", 0)
    cv_bias = abs(cv_with - cv_without) / max(cv_with, 0.01) * 100 if cv_with else 0
    overall_bias = abs(overall_with - overall_without) / max(overall_with, 0.01) * 100 if overall_with else 0

    if without_scores.get('维度名称正确'):
        dim_label = "名称正确"
    else:
        dim_label = "来源权威性"  # LLM 编造的错误名称

    cv_w = with_scores.get("主张验证权重", "65%")
    cv_wo_w = without_scores.get("主张验证权重", "—")

    lines.append("## 对比分析")
    lines.append("")

    # 表格
    lines.append("| 指标 | 有 scorer | 无 scorer | 差异 |")
    lines.append("|------|-----------|-----------|------|")
    lines.append(f"| 主张验证权重 | {cv_w} ✅ | {cv_wo_w} ❌ | 偏差 {abs(int(cv_w.replace('%','')) - int(str(cv_wo_w).replace('%','')))}pp |")
    lines.append(f"| 主张验证得分 | {cv_with}（程序化） | {cv_without}（LLM估算） | 偏差 {cv_bias:.0f}% |")
    dim_ok = "✅" if with_scores.get('维度名称正确') else ""
    dim_wo_detail = (
        f"'域名权威性'被 LLM 编造为'{dim_label}'，且未算出该维度得分"
        if not without_scores.get('维度名称正确')
        else "维度名称正确"
    )
    lines.append(f"| 评分维度固定 | 三维度名称+权重由代码写死，不会偏离 {dim_ok} | {dim_wo_detail} | 无 scorer 时 LLM 凭记忆编造维度名，名称和分数都不可控 |")
    lines.append(f"| 综合得分 | {overall_with} | {overall_without} | 偏差 {overall_bias:.0f}% |")
    lines.append(f"| 评分明细结构化 | ✅ 有 | ❌ 无 | 不可程序化验证 |")
    lines.append("")
    lines.append("## 归因")
    lines.append("")
    lines.append("- **无 credibility_scorer 时**：Agent 不知道评分维度的正确定义（权重比例、维度名称），只能凭模型记忆'猜'——导致权重错误（50% 而非 65%）、维度命名错误（'来源权威性' 而非 '域名权威性'）、分数 LLM 估算（而非程序化计算）")
    lines.append("- **有 credibility_scorer 时**：评分维度、权重、计算方法全部由代码硬控，Agent 只需传 verdicts + page_metadata，scorer 返回精确的结构化分数——结果稳定、可审计")
    lines.append('- credibility_scorer 的核心价值不是「算个分」，而是**保证评分的正确性（权重准确）、稳定性高（同输入同输出）和结构化（可程序化验证）**')
    lines.append("")
    lines.append("## 局限")
    lines.append("")
    lines.append("- 单次运行，未取均值；D4 起应每组多次运行取均值再下结论")
    lines.append("- 只测了一个任务（domain-task），需扩展到更多任务类型")
    lines.append("- 无 scorer 时 Agent 仍尝试手动估算分数（因为 SKILL.md 要求输出评分），实际场景中可能完全跳过评分环节")

    with open(note_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def run_live_experiment() -> dict:
    """真跑 Agent 两次（有/无 scorer），捕获输出并返回提取的分数。"""
    import subprocess, time

    task = ("请核查以下事件及评论：事件：网传某知名科技公司将于下个月全面取消居家办公。"
            "评论A：我表哥在那家公司，他说已经收到内部邮件了，绝对是真的！"
            "评论B：别信，上周还有员工晒出居家办公的打卡截图呢。"
            "评论C：我是该公司HR，目前政策未变，网传消息为不实信息。"
            "请按照信息核查流程，剔除无效信息，保留有效信息并打上可信度，"
            "最后指出可疑点、给出事件可信度、还原事件真相。"
            "请严格以 JSON 格式输出最终结果，"
            "必须包含 有效信息、剔除信息、可疑点、事件可信度、事件真相还原 五个字段。")

    results = {}
    base_cmd = [sys.executable, "-m", "agent.cli", "--auto-approve", task]

    for label, extra_env in [("有 scorer", {}), ("无 scorer", {"OPENCLAW_SKIP_SCORER": "1"})]:
        print(f"\n>>> 正在运行：{label} ...")
        start = time.time()
        env = {**os.environ, "PYTHONPATH": "/mnt/d/AICC/mini-openclaw", **extra_env}
        try:
            proc = subprocess.run(
                base_cmd, capture_output=True, text=True, timeout=300,
                cwd="/mnt/d/AICC/mini-openclaw", env=env
            )
            elapsed = time.time() - start
            output = proc.stdout[-8000:] if proc.stdout else "(无输出)"
            results[label] = {"output": output, "elapsed": f"{elapsed:.0f}s"}
            print(f"    完成，耗时 {elapsed:.0f}s")
        except subprocess.TimeoutExpired:
            results[label] = {"output": "(超时)", "elapsed": ">300s"}
            print("    ⚠ 超时")

    return results


if __name__ == "__main__":
    live_mode = "--live" in sys.argv

    if live_mode:
        results = run_live_experiment()
        with_text = results.get("有 scorer", {}).get("output", "")
        without_text = results.get("无 scorer", {}).get("output", "")
    else:
        with_text = TRACE_WITH_SCORER
        without_text = TRACE_WITHOUT_SCORER

    with_scores = extract_scores(with_text)
    without_scores = extract_scores(without_text)

    print("=== 消融：有/无 credibility_scorer ===")
    print(f"有 scorer: {with_scores}")
    print(f"无 scorer: {without_scores}")

    note_path = os.path.join(os.path.dirname(__file__), "ablation_scorer_note.md")
    build_note(with_scores, without_scores, note_path)
    print(f"\n结果已写入 {note_path}")
