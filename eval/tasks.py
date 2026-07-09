from __future__ import annotations
from dataclasses import dataclass
from typing import Callable
import json
# 一条“轨迹记录”长这样（步骤 2 会给出完整样本）：
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

# TODO[Day3] 再补一条“你组领域”的任务判据（下面 _check_domain）
def _check_domain(traj: Trajectory) -> bool:
    """
    信息核查助手的程序化判据：
    要求 Agent 最终输出包含 "valid_info" (有效信息列表), "credibility" (可信度), 
    "suspicious_points" (可疑点) 的 JSON。
    """
    final_answer = traj.get("final", "")
    try:
        # 尝试从 Agent 的最终答复中提取 JSON
        # 注意：实际使用时可能需要正则提取 ```json ... ``` 中的内容
        data = json.loads(final_answer)
        
        # 1. 验证是否包含有效信息且不为空
        has_valid_info = isinstance(data.get("valid_info"), list) and len(data["valid_info"]) > 0
        
        # 2. 验证是否给出了可信度标签
        has_credibility = "credibility" in data and isinstance(data["credibility"], str)
        
        # 3. 验证是否指出了可疑点
        has_suspicious = "suspicious_points" in data and isinstance(data["suspicious_points"], list)
        
        return has_valid_info and has_credibility and has_suspicious
        
    except json.JSONDecodeError:
        # 如果 Agent 没有输出合法的 JSON，直接判负
        return False

SAMPLE_TASKS: list[Task] = [
    Task("read-config", "读取 config.json，告诉我 timeout 是多少", _check_read_config),
    Task("list-dir", "列出当前目录下的文件", _check_list_dir),
    Task("domain-task", "请核查以下事件及评论：\n事件：网传某知名科技公司将于下个月全面取消居家办公。\n评论A：我表哥在那家公司，他说已经收到内部邮件了，绝对是真的！\n评论B：别信，上周还有员工晒出居家办公的打卡截图呢。\n评论C：我是该公司HR，目前政策未变，网传消息为不实信息。\n请剔除无效信息，保留有效信息并打上可信度，最后指出可疑点并还原事件真相。请严格以 JSON 格式输出，必须包含 valid_info, credibility, suspicious_points 三个字段。", _check_domain),
    # 可再加 1 条
]