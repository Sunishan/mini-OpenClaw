from __future__ import annotations
from dataclasses import dataclass
from typing import Callable
import json
# 一条"轨迹记录"长这样（步骤 2 会给出完整样本）：
#   {"task": "任务名", "steps": [ {tool_calls, raw, prompt_tokens, completion_tokens}, ... ],
#    "final": "agent 的最终自然语言答复"}
Trajectory = dict

@dataclass
class Task:
    name: str
    instruction: str                       # 给 agent 的指令
    check: Callable[[Trajectory], bool]    # 成功判据：吃一条轨迹，判成败

# ---- 成功判据（程序化优先）----
def _check_read_config(traj: Trajectory) -> bool:
    # 成功 = 期间调用过 read 且最终答复里报出了 timeout 的值
    used_read = any(
        tc["name"] == "read"
        for s in traj["steps"] for tc in s.get("tool_calls", [])
    )
    return used_read and "30" in traj.get("final", "")

def _check_list_dir(traj: Trajectory) -> bool:
    return any(
        tc["name"] == "bash" and "ls" in str(tc.get("arguments", {}))
        for s in traj["steps"] for tc in s.get("tool_calls", [])
    )

def _check_domain(traj: Trajectory) -> bool:
    """
    信息核查助手的程序化判据：
    要求 Agent 最终输出包含以下关键字段：
    - 有效信息（数组，至少 1 条）
    - 可疑点（数组，至少 1 个）
    - 事件可信度（字符串）
    - 事件真相还原（字符串）
    """
    final_answer = traj.get("final", "")
    try:
        # 尝试从 Agent 的最终答复中提取 JSON
        data = json.loads(final_answer)

        # 1. 验证是否包含有效信息且不为空
        has_valid_info = isinstance(data.get("有效信息"), list) and len(data["有效信息"]) > 0
        if has_valid_info:
            # 每条有效信息必须有可信度标签
            all_tagged = all(
                item.get("可信度") in ("高", "中", "低")
                for item in data["有效信息"]
            )
            has_valid_info = has_valid_info and all_tagged

        # 2. 验证是否指出了可疑点
        has_suspicious = isinstance(data.get("可疑点"), list) and len(data["可疑点"]) > 0

        # 3. 验证是否给出了事件可信度
        has_credibility = isinstance(data.get("事件可信度"), str) and data["事件可信度"] in ("高", "中", "低")

        # 4. 验证是否有事件真相还原
        has_truth = isinstance(data.get("事件真相还原"), str) and len(data["事件真相还原"]) > 0

        return has_valid_info and has_suspicious and has_credibility and has_truth

    except (json.JSONDecodeError, AttributeError):
        # 如果 Agent 没有输出合法的 JSON，尝试从文本中搜索 JSON 块
        import re
        json_blocks = re.findall(r'\{[^{}]*\}', final_answer, re.DOTALL)
        for block in json_blocks:
            try:
                data = json.loads(block)
                if ("有效信息" in data and "可疑点" in data and
                        "事件可信度" in data and "事件真相还原" in data):
                    return True
            except json.JSONDecodeError:
                continue
        return False

SAMPLE_TASKS: list[Task] = [
    Task("read-config", "读取 config.json，告诉我 timeout 是多少", _check_read_config),
    Task("list-dir", "列出当前目录下的文件", _check_list_dir),
    Task("domain-task",
         "请核查以下事件及评论：\n"
         "事件：网传某知名科技公司将于下个月全面取消居家办公。\n"
         "评论A：我表哥在那家公司，他说已经收到内部邮件了，绝对是真的！\n"
         "评论B：别信，上周还有员工晒出居家办公的打卡截图呢。\n"
         "评论C：我是该公司HR，目前政策未变，网传消息为不实信息。\n"
         "请按照信息核查流程，剔除无效信息，保留有效信息并打上可信度，"
         "最后指出可疑点、给出事件可信度、还原事件真相。"
         "请严格以 JSON 格式输出最终结果，"
         "必须包含 有效信息、剔除信息、可疑点、事件可信度、事件真相还原 五个字段。",
         _check_domain),
    # 可再加 1 条
]
