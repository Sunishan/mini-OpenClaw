"""URL extraction and safety checks for network tools."""
from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urlparse, urlunparse


_URL_RE = re.compile(r"https?://[^\s<>'\"，。；！？）】》]+", re.IGNORECASE)
_TRAILING_PUNCT = ".,;:!?)\\]}，。；：！？）】》"
_ALLOWED_SCHEMES = {"http", "https"}
_ALLOWED_PORTS = {80, 443}
_LOCAL_HOSTS = {
    "localhost",
    "localhost.localdomain",
    "ip6-localhost",
    "ip6-loopback",
}


def extract_urls(text: str) -> list[str]:
    """Extract http/https URLs from user-authored text."""
    if not text:
        return []
    urls: list[str] = []
    for match in _URL_RE.finditer(text):
        url = match.group(0).rstrip(_TRAILING_PUNCT)
        if url:
            urls.append(url)
    return urls


def _hostname_ascii(hostname: str) -> str:
    hostname = hostname.strip().rstrip(".").lower()
    try:
        return hostname.encode("idna").decode("ascii")
    except UnicodeError:
        return hostname


def normalize_url(url: str) -> str:
    """Normalize a URL for equality checks against user-provided URLs."""
    parsed = urlparse(url.strip())
    scheme = parsed.scheme.lower()
    hostname = _hostname_ascii(parsed.hostname or "")
    port = parsed.port

    if ":" in hostname and not hostname.startswith("["):
        netloc = f"[{hostname}]"
    else:
        netloc = hostname
    if port is not None:
        netloc = f"{netloc}:{port}"

    path = parsed.path or "/"
    return urlunparse((scheme, netloc, path, "", parsed.query, ""))


def _is_unsafe_ip(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(
        (
            ip.is_loopback,
            ip.is_private,
            ip.is_link_local,
            ip.is_multicast,
            ip.is_reserved,
            ip.is_unspecified,
        )
    )


def _resolve_host(hostname: str, port: int) -> tuple[bool, str]:
    try:
        infos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, OSError):
        # DNS 解析失败只是网络问题，不等于 URL 不安全
        # 让实际 HTTP 请求自然处理超时/错误
        return True, ""

    addresses = {info[4][0] for info in infos if info and info[4]}
    if not addresses:
        return True, ""
    for address in addresses:
        if _is_unsafe_ip(address):
            return False, f"解析到不安全地址：{address}"
    return True, ""


def is_safe_public_url(url: str) -> tuple[bool, str]:
    """Return whether a URL is safe enough for automatic public web access."""
    if not isinstance(url, str) or not url.strip():
        return False, "URL 为空"

    try:
        parsed = urlparse(url.strip())
        port = parsed.port
    except ValueError as exc:
        return False, f"URL 端口无效：{exc}"

    scheme = parsed.scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        return False, f"不允许的 URL scheme：{parsed.scheme or '(empty)'}"

    if parsed.username or parsed.password:
        return False, "URL 中包含 userinfo"

    if port is not None and port not in _ALLOWED_PORTS:
        return False, f"不允许的端口：{port}"

    hostname = parsed.hostname
    if not hostname:
        return False, "URL 缺少主机名"

    host = _hostname_ascii(hostname)
    if host in _LOCAL_HOSTS or host.endswith(".localhost"):
        return False, "指向本地主机"

    if _is_unsafe_ip(host):
        return False, "指向内网、本机或保留地址"

    # Single-label hostnames are normally intranet names. Keep them out of the
    # auto-allow path even if the local resolver knows how to resolve them.
    if "." not in host and not re.fullmatch(r"\[[0-9a-f:.]+\]", host, re.IGNORECASE):
        return False, "单标签主机名不自动放行"

    return _resolve_host(host, port or (443 if scheme == "https" else 80))
