"""Tool permission policy.

The policy is intentionally small and conservative: tool execution is split
into allow / confirm / deny before the AgentLoop runs a tool.
"""
from __future__ import annotations

from pathlib import Path

from agent.url_safety import is_safe_public_url, normalize_url


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
LOW_RISK_NETWORK = {"web_search", "kb_retriever"}
EXPLICIT_URL_NETWORK = {
    "webpage_reader",
    "mcp__firecrawl_scrape",
    "mcp__browser_navigate",
}
MCP_READONLY = {
    "mcp__browser_snapshot",
    "mcp__browser_find",
    "mcp__browser_tabs",
}
MEMORY_WRITE = {"remember"}

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


def _url_arg(args: dict) -> str | None:
    value = args.get("url")
    return value if isinstance(value, str) and value else None


def _check_explicit_url_tool(
    args: dict,
    user_allowed_urls: set[str],
) -> tuple[str, str]:
    url = _url_arg(args)
    if url is None:
        return "confirm", "工具未提供 URL 参数"

    safe, reason = is_safe_public_url(url)
    if not safe:
        return "deny", f"URL 不安全（{reason}）"

    normalized = normalize_url(url)
    if normalized in user_allowed_urls:
        return "allow", "用户显式提供的安全 URL"

    return "confirm", "URL 安全，但不是用户显式提供的目标"


def check_with_reason(
    tool: str,
    args: dict,
    workdir: Path,
    user_allowed_urls: set[str] | None = None,
) -> tuple[str, str]:
    """Return (verdict, reason), where verdict is allow / confirm / deny."""
    args = args or {}
    root = workdir.resolve()
    user_allowed_urls = user_allowed_urls or set()

    if tool in MEMORY_WRITE:
        return "confirm", "长期记忆写入需要确认"

    if tool in MCP_READONLY:
        return "allow", "MCP 只读页面状态工具"

    if tool in EXPLICIT_URL_NETWORK:
        return _check_explicit_url_tool(args, user_allowed_urls)

    if tool.startswith("mcp__"):
        return "confirm", "MCP 工具需要确认"

    if tool in READONLY:
        path = _path_arg(args)
        if path is None:
            return "allow", "只读工具"
        if _is_sensitive(path):
            return "deny", "读取敏感路径"
        if _inside(path, root):
            return "allow", "读取工作目录内文件"
        return "confirm", "读取工作目录外文件需要确认"

    if tool in WRITE:
        path = _path_arg(args)
        if path is None or _is_sensitive(path):
            return "deny", "写入路径为空或敏感"
        if _inside(path, root):
            return "confirm", "写入工作目录内文件需要确认"
        return "deny", "拒绝写入工作目录外文件"

    if tool in EXEC:
        return "confirm", "执行命令需要确认"

    if tool in LOW_RISK_NETWORK:
        return "allow", "低风险检索工具"

    # Unknown tools are treated conservatively.
    return "confirm", "未知工具需要确认"


def check(
    tool: str,
    args: dict,
    workdir: Path,
    user_allowed_urls: set[str] | None = None,
) -> str:
    """Return one of: 'allow', 'confirm', 'deny'."""
    verdict, _reason = check_with_reason(
        tool,
        args,
        workdir,
        user_allowed_urls,
    )
    return verdict
