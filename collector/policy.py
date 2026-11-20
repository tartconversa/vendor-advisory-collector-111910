"""来源访问策略：允许路径、跳转主机、凭据作用域与限速。

策略来自来源 /policy 与本地输入，采集端只做防御性约束：
- 只请求允许前缀内的来源路径；
- 仅在凭据作用域路径内附带受限凭据；
- 跳转到未允许的外部主机时停止，不携带凭据继续跟随；
- 遵守来源声明的最小请求间隔与 429 Retry-After。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlsplit, urljoin


@dataclass
class Policy:
    base_url: str
    allowed_paths: tuple[str, ...]
    redirect_allowed_hosts: tuple[str, ...] = ()
    credential_scope: str = ""
    request_interval_ms: int = 0
    external_redirect_path: str | None = None
    credentials: str | None = None  # 受限凭据（如令牌），仅在作用域内发送
    # 协议约定的运维端点（策略发现与资产清单），不属于公告内容抓取前缀
    operational_paths: tuple[str, ...] = ("/policy", "/assets", "/health")
    # 仅在运行期从已授权公告页解析出的机器可读文件链接才动态放行（精确路径）
    referenced_paths: set = field(default_factory=set)

    def allow_referenced(self, url_or_path: str) -> str:
        """登记一条从已授权页面上发现的引用链接，返回解析后的绝对 URL。"""
        absolute = self.resolve(url_or_path)
        parts = urlsplit(absolute)
        base = urlsplit(self.base_url)
        if parts.hostname != base.hostname or parts.port != base.port:
            raise PermissionError("引用链接指向外站，拒绝登记: " + absolute)
        self.referenced_paths.add(parts.path)
        return absolute

    @classmethod
    def from_source(cls, base_url: str, policy_doc: dict, local_allowed_paths,
                    credentials: str | None = None) -> "Policy":
        host = urlsplit(base_url).hostname or ""
        allowed = tuple(policy_doc.get("allowed_paths") or local_allowed_paths or ())
        hosts = tuple(policy_doc.get("redirect_allowed_hosts") or (host,))
        return cls(
            base_url=base_url.rstrip("/"),
            allowed_paths=allowed,
            redirect_allowed_hosts=hosts,
            credential_scope=policy_doc.get("credential_scope", ""),
            request_interval_ms=int(policy_doc.get("request_interval_ms", 0) or 0),
            external_redirect_path=policy_doc.get("external_redirect"),
            credentials=credentials,
        )

    def resolve(self, url_or_path: str) -> str:
        """把来源页里的相对路径解析为绝对 URL。"""
        if url_or_path.startswith(("http://", "https://")):
            return url_or_path
        return self.base_url + (url_or_path if url_or_path.startswith("/") else "/" + url_or_path)

    def is_allowed_path(self, url: str) -> bool:
        parts = urlsplit(url)
        base = urlsplit(self.base_url)
        if parts.scheme not in ("http", "https"):
            return False
        if parts.hostname != base.hostname or parts.port != base.port:
            return False
        if parts.path in self.operational_paths:
            return True
        if parts.path in self.referenced_paths:
            return True
        return any(parts.path.startswith(prefix) for prefix in self.allowed_paths)

    def is_redirect_host_allowed(self, url: str) -> bool:
        host = urlsplit(url).hostname or ""
        return host in self.redirect_allowed_hosts

    def within_credential_scope(self, url: str) -> bool:
        if not self.credential_scope:
            return False
        return urlsplit(url).path.startswith(self.credential_scope)

    def auth_header(self, url: str) -> dict[str, str]:
        """仅在凭据作用域内附带凭据；其他路径（含镜像）一律不携带。"""
        if self.credentials and self.within_credential_scope(url):
            return {"Authorization": "Bearer " + self.credentials}
        return {}
