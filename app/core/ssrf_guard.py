"""SSRF 防护（P1-D）：出站 URL 目标校验，fail-closed。

- 仅允许 http/https；拒绝回环、私网、链路本地、未指定、组播、保留地址
  （IPv4 + IPv6）与 localhost 主机名。
- 主机名在调用前解析并校验**全部**解析结果：任一落入禁区即拒绝（覆盖
  DNS 重绑定中"解析到内网"的一类攻击；解析失败同样拒绝）。
- 默认开启（SSRF_GUARD_ENABLED=true）；显式关闭属降级，须见于
  docs/security-testing-attack-surface.md 与部署配置。
- 重定向目标需另行调用 ``assert_safe_url`` 逐跳复核（见文档）。

注意：这是调用时点的一次性校验；对"连接时解析变化"的完整防护需部署方
配合 Egress 网络策略（禁内网出口）与私有 DNS 加固（见部署方需确认）。
"""

from __future__ import annotations

import ipaddress
from typing import Any
from urllib.parse import urlsplit

from app.core.config import get_settings


class SSRFGuardError(ValueError):
    """SSRF 拦截（携带拒绝原因，消息不含可重定向凭据）。"""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"SSRF 防护拦截：{reason}")


def blocked_ip_reason(ip: str) -> str | None:
    """返回该 IP 的禁区类别；安全则返回 None。"""
    try:
        obj = ipaddress.ip_address(ip)
    except ValueError:
        return "invalid_ip"
    if obj.is_loopback:
        return "loopback"
    if obj.is_private:
        return "private"
    if obj.is_link_local:
        return "link_local"
    if obj.is_unspecified:
        return "unspecified"
    if obj.is_multicast:
        return "multicast"
    if obj.is_reserved:
        return "reserved"
    return None


def blocked_host_reason(host: str, *, resolve: bool = True) -> str | None:
    """校验主机名（含解析结果）；安全返回 None，否则返回拒绝原因。

    ``resolve=False`` 仅校验字面 IP 与已知危险主机名（localhost），不做 DNS 查询。
    """
    host = (host or "").strip().lower().rstrip(".")
    if not host:
        return "empty_host"
    if host == "localhost":
        return "loopback"
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        return blocked_ip_reason(str(literal))
    if not resolve:
        return None

    import socket

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return "unresolvable"
    except OSError:
        return "resolution_failed"
    if not infos:
        return "unresolvable"
    for info in infos:
        reason = blocked_ip_reason(info[4][0])
        if reason:
            return reason
    return None


def assert_safe_url(url: str, *, resolve: bool = True) -> str:
    """校验出站 URL；不安全抛 ``SSRFGuardError``。返回原 URL（供链式使用）。

    ``resolve=False`` 仅校验字面 IP/已知危险主机名（不含 DNS 查询），测试用。
    """
    if not get_settings().SSRF_GUARD_ENABLED:
        return url
    if not url:
        raise SSRFGuardError("empty_url")
    parsed = urlsplit(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in {"http", "https"}:
        raise SSRFGuardError(f"scheme 非 http/https（{scheme or 'none'}）")
    host = parsed.hostname
    if not host:
        raise SSRFGuardError("missing_host")
    reason = blocked_host_reason(host, resolve=resolve)
    if reason:
        raise SSRFGuardError(f"目标 {host} 命中 {reason} 地址段")
    return url


def validate_redirect_target(url: str, **kwargs: Any) -> str:
    """重定向逐跳复核入口：对每个待跟随的目标调用本函数（配合使用方禁用
    自动跟随或自行逐跳）。"""
    return assert_safe_url(url, **kwargs)