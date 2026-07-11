"""受控 shell 执行（Day5：bash；Day10：加沙箱与权限）。"""
from __future__ import annotations
from pathlib import Path
import shutil
import subprocess
from .base import Tool


DENY = (
    "rm -rf /",
    "rm -rf ~",
    ":(){",
    "mkfs",
    "dd if=",
    "> /dev/sd",
    "curl",
    "wget",
)


def _bash(command: str, timeout: int = 30) -> str:
    lowered = command.lower()
    if any(bad in lowered for bad in DENY):
        return f"[沙箱] 拒绝执行高危命令：{command}"

    workdir = Path.cwd().resolve()
    using_bwrap = bool(shutil.which("bwrap"))
    if using_bwrap:
        cmd = [
            "bwrap",
            "--ro-bind", "/", "/",
            "--bind", str(workdir), str(workdir),
            "--chdir", str(workdir),
            "--unshare-net",
            "--dev", "/dev",
            "--proc", "/proc",
            "bash", "-c", command,
        ]
    else:
        cmd = ["bash", "-c", command]

    try:
        p = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=workdir,
        )
    except subprocess.TimeoutExpired:
        return f"[超时] 命令超过 {timeout}s 未结束：{command}"
    except Exception as e:  # noqa: BLE001
        return f"[沙箱] 执行失败：{e}"

    prefix = ""
    if using_bwrap and p.returncode != 0 and "bwrap:" in (p.stderr or ""):
        prefix = "[沙箱] bwrap 不可用，已降级为黑名单兜底执行。\n"
        try:
            p = subprocess.run(
                ["bash", "-c", command],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=workdir,
            )
        except subprocess.TimeoutExpired:
            return f"[超时] 命令超过 {timeout}s 未结束：{command}"
        except Exception as e:  # noqa: BLE001
            return f"[沙箱] 降级执行失败：{e}"

    out = p.stdout or ""
    if p.stderr:
        out += f"\n[stderr]\n{p.stderr}"
    if p.returncode != 0:
        out += f"\n[returncode={p.returncode}]"
    return prefix + (out.strip() or "[无输出]")


bash_tool = Tool(
    name="bash",
    description="在工作目录中执行一条 shell 命令并返回输出。",
    parameters={"type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"]},
    run=_bash,
)
