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
NETWORK = {"web_fetch", "webpage_reader", "web_search", "kb_retriever"}

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
