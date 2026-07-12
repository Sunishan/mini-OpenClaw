"""网站访问安全层。

为所有外发 HTTP 请求提供分层安全防护：
- Layer 1: URL 验证 / SSRF 防护（UrlValidator）
- Layer 2: 安全 HTTP 客户端（SafeHttpxClient）
- Layer 3: 速率限制（RateLimiter）—— 默认关闭
- Layer 4: 审计日志（AuditLogger）

用法：
    from tools.security import SafeHttpxClient, SecurityConfig
    client = SafeHttpxClient(SecurityConfig.from_env())
    resp = client.get("https://example.com", _tool_name="webpage_reader")
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import socket
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

import httpx


# ============================================================
# Config
# ============================================================


@dataclass
class SecurityConfig:
    """从环境变量读取安全配置，提供合理的默认值。

    默认值针对信息核查场景设计：只拦截真正危险的请求，
    不干扰对外部公开网站的访问。
    """

    # Layer 1: SSRF 防护
    block_private_ips: bool = True
    allowed_schemes: frozenset = field(
        default_factory=lambda: frozenset({"http", "https"})
    )
    denied_domains: frozenset = field(default_factory=frozenset)

    # Layer 2: 请求控制
    max_response_bytes: int = 5 * 1024 * 1024   # 5 MB
    max_redirects: int = 5
    request_timeout: float = 20.0
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
    allowed_content_types: frozenset = field(
        default_factory=lambda: frozenset({
            "text/html", "text/plain", "application/json",
            "application/xml", "text/xml", "application/xhtml+xml",
            "application/ld+json", "text/markdown",
        })
    )
    block_binary_downloads: bool = True

    # Layer 3: 速率限制（0 = 不限速）
    rate_limit_rpm: int = 0

    # Layer 4: 审计日志
    audit_log_path: str = ""
    audit_enabled: bool = True

    @classmethod
    def from_env(cls) -> SecurityConfig:
        """读取 OPENCLAW_* 环境变量，返回填充后的配置。"""
        cfg = cls()

        val = os.environ.get("OPENCLAW_BLOCK_PRIVATE_IPS")
        if val is not None:
            cfg.block_private_ips = val.lower() in ("1", "true", "yes")

        val = os.environ.get("OPENCLAW_DENIED_DOMAINS")
        if val:
            cfg.denied_domains = frozenset(
                d.strip() for d in val.split(",") if d.strip()
            )

        val = os.environ.get("OPENCLAW_MAX_RESPONSE_BYTES")
        if val is not None:
            try:
                cfg.max_response_bytes = int(val)
            except ValueError:
                pass

        val = os.environ.get("OPENCLAW_MAX_REDIRECTS")
        if val is not None:
            try:
                cfg.max_redirects = int(val)
            except ValueError:
                pass

        val = os.environ.get("OPENCLAW_REQUEST_TIMEOUT")
        if val is not None:
            try:
                cfg.request_timeout = float(val)
            except ValueError:
                pass

        val = os.environ.get("OPENCLAW_USER_AGENT")
        if val is not None:
            cfg.user_agent = val

        val = os.environ.get("OPENCLAW_RATE_LIMIT_RPM")
        if val is not None:
            try:
                cfg.rate_limit_rpm = int(val)
            except ValueError:
                pass

        val = os.environ.get("OPENCLAW_AUDIT_LOG_PATH")
        if val is not None:
            cfg.audit_log_path = val

        val = os.environ.get("OPENCLAW_AUDIT_ENABLED")
        if val is not None:
            cfg.audit_enabled = val.lower() in ("1", "true", "yes")

        return cfg


# ============================================================
# Layer 1: URL 验证 / SSRF 防护
# ============================================================

# 私有/保留 IP 地址段（IPv4 + IPv6）
_PRIVATE_NETS = [
    ipaddress.ip_network("127.0.0.0/8"),        # 回环
    ipaddress.ip_network("10.0.0.0/8"),          # 私有 A
    ipaddress.ip_network("172.16.0.0/12"),       # 私有 B
    ipaddress.ip_network("192.168.0.0/16"),      # 私有 C
    ipaddress.ip_network("169.254.0.0/16"),      # 链路本地
    ipaddress.ip_network("0.0.0.0/8"),           # "本"网络
    ipaddress.ip_network("100.64.0.0/10"),       # CGNAT
    ipaddress.ip_network("198.18.0.0/15"),       # 基准测试
    ipaddress.ip_network("::1/128"),             # IPv6 回环
    ipaddress.ip_network("fc00::/7"),            # IPv6 唯一本地
    ipaddress.ip_network("fe80::/10"),           # IPv6 链路本地
]


class UrlValidationError(ValueError, httpx.RequestError):
    """URL 安全验证失败时抛出。

    同时继承 ValueError 和 httpx.RequestError，使得现有的
    except httpx.RequestError 处理逻辑也能捕获验证错误。
    """

    def __init__(self, message: str) -> None:
        ValueError.__init__(self, message)
        httpx.RequestError.__init__(self, message, request=None)


class UrlValidator:
    """URL 验证器：在发起实际请求前检查 URL 是否安全。"""

    def __init__(self, config: SecurityConfig) -> None:
        self._config = config

    def validate(self, url: str) -> None:
        """验证 URL。失败时抛出 UrlValidationError。"""
        if not url or not isinstance(url, str):
            raise UrlValidationError("URL 为空或不是字符串")

        parsed = urlparse(url)
        scheme = parsed.scheme.lower()

        # 1. 检查 scheme
        if scheme not in self._config.allowed_schemes:
            raise UrlValidationError(f"不允许的 URL scheme：{scheme}（仅支持 http/https）")

        # 2. 必须有 hostname
        hostname = parsed.hostname
        if not hostname:
            raise UrlValidationError("URL 缺少主机名")

        # 3. 检查 localhost
        if hostname.lower() in ("localhost", "localhost.localdomain"):
            raise UrlValidationError("禁止访问 localhost（SSRF 防护）")

        # 4. 域名黑名单
        if self._config.denied_domains:
            if self._domain_matches(hostname, self._config.denied_domains):
                raise UrlValidationError(f"域名被黑名单拦截：{hostname}")

        # 5. SSRF：检查 IP 是否私有
        if self._config.block_private_ips:
            self._check_ssrf(hostname)

    def _check_ssrf(self, hostname: str) -> None:
        """解析 hostname 并检查 IP 是否为私有/保留地址。"""
        try:
            addrs = socket.getaddrinfo(hostname, None)
        except socket.gaierror as e:
            raise UrlValidationError(f"无法解析域名：{hostname}") from e

        for family, _, _, _, sockaddr in addrs:
            ip_str = sockaddr[0]
            try:
                ip = ipaddress.ip_address(ip_str)
            except ValueError:
                continue  # 无法解析为 IP，跳过（非 IP 的 hostname）
            for net in _PRIVATE_NETS:
                if ip in net:
                    raise UrlValidationError(
                        f"禁止访问私有/保留 IP：{ip_str}（主机名：{hostname}）"
                    )

    @staticmethod
    def _domain_matches(hostname: str, patterns: frozenset) -> bool:
        """检查 hostname 是否匹配某个模式（精确匹配或 *.后缀通配）。"""
        lowered = hostname.lower()
        for p in patterns:
            p = p.lower().strip()
            if p == lowered:
                return True
            if p.startswith("*.") and lowered.endswith(p[1:]):
                return True
        return False

    @staticmethod
    def extract_domain(url: str) -> str:
        """安全地从 URL 中提取域名。"""
        try:
            return urlparse(url).hostname or ""
        except Exception:
            return ""


# ============================================================
# Layer 2: 安全 HTTP 客户端
# ============================================================


class SafeHttpxClient:
    """httpx 安全封装，强制执行所有安全层。"""

    def __init__(self, config: Optional[SecurityConfig] = None) -> None:
        self._config = config or SecurityConfig.from_env()
        self._validator = UrlValidator(self._config)
        self._rate_limiter = RateLimiter.get_instance(self._config)
        self._audit = AuditLogger(self._config)
        self._client = httpx.Client(
            timeout=self._config.request_timeout,
            follow_redirects=False,  # 手动处理重定向，每跳验证
            headers={"User-Agent": self._config.user_agent},
        )

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        """发送 GET 请求，全程执行安全检查。

        接受一个特殊的 _tool_name 参数用于审计日志记录。
        """
        tool_name = kwargs.pop("_tool_name", "unknown")

        # Layer 1: URL 验证
        self._validator.validate(url)

        # Layer 3: 速率限制
        if self._config.rate_limit_rpm > 0:
            self._rate_limiter.check(url)

        # 手动处理重定向链，每跳验证
        current_url = url
        redirect_count = 0

        while True:
            resp = self._client.get(current_url, **kwargs)

            if resp.is_redirect:
                redirect_count += 1
                if redirect_count > self._config.max_redirects:
                    raise httpx.RequestError(
                        f"重定向链超过限制（{self._config.max_redirects} 跳）"
                    )
                location = resp.headers.get("location", "")
                if not location:
                    break
                current_url = urljoin(current_url, location)
                self._validator.validate(current_url)  # 每跳重新验证
                continue

            break

        # Layer 2: 内容类型检查
        if self._config.block_binary_downloads:
            raw_ct = (resp.headers.get("content-type", "") or "").split(";")[0].strip().lower()
            if raw_ct and raw_ct not in self._config.allowed_content_types:
                raise httpx.RequestError(
                    f"拦截了未允许的内容类型：{raw_ct}。"
                    f"允许的类型：{', '.join(self._config.allowed_content_types)}"
                )

        # Layer 2: 大小检查——先看 Content-Length 头
        content_length = resp.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > self._config.max_response_bytes:
                    raise httpx.RequestError(
                        f"Content-Length（{content_length}）超过最大限制（{self._config.max_response_bytes}）"
                    )
            except ValueError:
                pass

        # 读取响应体并检查大小
        body = resp.content
        if len(body) > self._config.max_response_bytes:
            raise httpx.RequestError(
                f"响应体（{len(body)} 字节）超过最大限制（{self._config.max_response_bytes} 字节）"
            )

        # Layer 4: 审计日志
        domain = self._validator.extract_domain(current_url)
        self._audit.log(
            url=url,
            domain=domain,
            final_url=str(resp.url),
            status_code=resp.status_code,
            bytes_received=len(body),
            content_type=resp.headers.get("content-type", ""),
            tool_name=tool_name,
        )

        return resp

    def __enter__(self) -> SafeHttpxClient:
        return self

    def __exit__(self, *args: Any) -> None:
        self._client.close()


# ============================================================
# Layer 3: 速率限制
# ============================================================


class RateLimiter:
    """每域名令牌桶速率限制器。

    使用 SHA-256 域名哈希存储桶，不存储原始域名。
    只有在 rate_limit_rpm > 0 时才实际限制。
    """

    _instance: Optional[RateLimiter] = None

    def __init__(self, config: SecurityConfig) -> None:
        self._rpm = config.rate_limit_rpm
        self._buckets: dict[str, list[float]] = {}

    @classmethod
    def get_instance(cls, config: SecurityConfig) -> RateLimiter:
        """获取模块级单例。"""
        if cls._instance is None:
            cls._instance = cls(config)
        return cls._instance

    def check(self, url: str) -> None:
        """检查是否超限，超限时抛出 httpx.RequestError。

        如果 rate_limit_rpm == 0，直接返回（不限速）。
        """
        if self._rpm <= 0:
            return

        domain = urlparse(url).hostname or "unknown"
        domain_hash = hashlib.sha256(domain.lower().encode()).hexdigest()[:16]
        now = time.time()
        window = 60.0  # 1 分钟窗口

        if domain_hash not in self._buckets:
            self._buckets[domain_hash] = []

        # 清理过期记录
        self._buckets[domain_hash] = [
            t for t in self._buckets[domain_hash] if now - t < window
        ]

        if len(self._buckets[domain_hash]) >= self._rpm:
            raise httpx.RequestError(
                f"域名 '{domain}' 请求频率超限：最大 {self._rpm} 次/分钟"
            )

        self._buckets[domain_hash].append(now)

    def reset(self) -> None:
        """清空所有速率限制桶（用于测试）。"""
        self._buckets.clear()


# ============================================================
# Layer 4: 审计日志
# ============================================================


class AuditLogger:
    """JSONL 格式的审计日志，记录所有外发网络请求。"""

    def __init__(self, config: SecurityConfig) -> None:
        self._enabled = config.audit_enabled
        log_path = config.audit_log_path or str(
            Path.home() / ".mini-openclaw" / "audit.jsonl"
        )
        self._path = Path(log_path)
        if self._enabled:
            self._path.parent.mkdir(parents=True, exist_ok=True)

    def log(
        self,
        *,
        url: str,
        domain: str,
        final_url: str,
        status_code: int,
        bytes_received: int,
        content_type: str,
        tool_name: str,
    ) -> None:
        """记录一条审计日志。写入失败不阻塞主流程。"""
        if not self._enabled:
            return

        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "tool": tool_name,
            "url": url,
            "domain": domain,
            "final_url": final_url,
            "status": status_code,
            "bytes": bytes_received,
            "content_type": content_type,
        }
        try:
            with self._path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            pass  # 审计失败不阻塞主流程


# ============================================================
# 模块级便捷访问器
# ============================================================

_CONFIG: Optional[SecurityConfig] = None


def get_config() -> SecurityConfig:
    """获取模块级单例配置。"""
    global _CONFIG
    if _CONFIG is None:
        _CONFIG = SecurityConfig.from_env()
    return _CONFIG


def get_client() -> SafeHttpxClient:
    """获取模块级单例 HTTP 客户端。"""
    return SafeHttpxClient(get_config())
