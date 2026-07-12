"""Tool permission policy.

The policy is intentionally small and conservative: tool execution is split
into allow / confirm / deny before the AgentLoop runs a tool.
"""
from __future__ import annotations

from pathlib import Path


READONLY = {
    "read",
    "grep",
    "glob",
    "claim_extractor",
    "cross_validator",
    "credibility_scorer",
    "report_generator",
    "load_skill",
}
WRITE = {"write", "edit"}
EXEC = {"bash"}
NETWORK = {"webpage_reader", "web_search", "kb_retriever"}

# 工具结果属于"外部不可信内容"的来源
# 这些结果在注入 prompt 时会被标记隔离，防止提示注入
EXTERNAL_SOURCES = {
    "webpage_reader",
    "web_search",
    "kb_retriever",
}


def is_external_source(name: str) -> bool:
    """判断工具是否返回外部不可信数据。

    覆盖已知外部来源工具 + 所有 mcp__* 前缀工具。
    """
    return name in EXTERNAL_SOURCES or name.startswith("mcp__")

SENSITIVE_PARTS = {".ssh", ".env", ".git-credentials"}
SENSITIVE_PATHS = {"/etc/shadow", "/etc/sudoers"}


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _path_arg(args: dict) -> Path | None:
    value = args.get("path")
    if not isinstance(value, str) or not value:
        return None
    return Path(value).expanduser().resolve()


def _is_sensitive(path: Path) -> bool:
    as_posix = path.as_posix()
    if as_posix in SENSITIVE_PATHS:
        return True
    return any(part in SENSITIVE_PARTS for part in path.parts)


# ── 域信任缓存（供交互式确认使用）──────────────────────────
# 键为 "{tool}:{domain}"，值为 True 表示已信任
_NETWORK_DOMAIN_TRUST: dict[str, bool] = {}


def trust_domain(tool: str, domain: str) -> None:
    """标记某个工具的某个域名为已信任（本次会话有效）。"""
    _NETWORK_DOMAIN_TRUST[f"{tool}:{domain.lower()}"] = True


def is_domain_trusted(tool: str, domain: str) -> bool:
    """检查某个工具的某个域名是否已信任。"""
    return _NETWORK_DOMAIN_TRUST.get(f"{tool}:{domain.lower()}", False)


def clear_trust() -> None:
    """清空所有域信任记录（用于 reset）。"""
    _NETWORK_DOMAIN_TRUST.clear()


def check(tool: str, args: dict, workdir: Path) -> str:
    """Return one of: 'allow', 'confirm', 'deny'."""
    args = args or {}
    root = workdir.resolve()

    if tool.startswith("mcp__"):
        return "confirm"

    if tool in READONLY:
        path = _path_arg(args)
        if path is None:
            return "allow"
        if _is_sensitive(path):
            return "deny"
        return "allow" if _inside(path, root) else "confirm"

    if tool in WRITE:
        path = _path_arg(args)
        if path is None or _is_sensitive(path):
            return "deny"
        return "confirm" if _inside(path, root) else "deny"

    if tool in EXEC:
        return "confirm"

    if tool in NETWORK:
        return "confirm"

    # Unknown tools are treated conservatively.
    return "confirm"
