"""受限 HTTP 抓取。

约束：
- 不自动跟随重定向：目标主机不在允许列表时停止并返回明确原因；
- 凭据只在策略作用域内的初始请求发送，任何重定向后的请求都不携带受限凭据；
- 遵守来源 request_interval_ms 限速与 429 Retry-After；
- 503 暂时不可访问、429 限速、404/410 与其他错误区分上报，由编排层决定后果；
- 只做 GET 采集，不发送任何攻击性载荷。
"""
from __future__ import annotations

import hashlib
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit

from .policy import Policy


class RedirectNotAllowed(Exception):
    """跳转到未经允许的外部主机；采集必须停止并向值班员说明。"""

    def __init__(self, target: str):
        super().__init__("重定向指向未经允许的外部主机，已停止: " + target)
        self.target = target


class SameSiteRedirect(Exception):
    """同站且在允许路径内的跳转；编排层确认后无凭据重新请求。"""

    def __init__(self, target: str):
        super().__init__("同站允许跳转: " + target)
        self.target = target


class SourceUnavailable(Exception):
    """来源暂时不可访问（5xx），与正式撤销区分。"""

    def __init__(self, status: int, retry_after: float | None = None):
        super().__init__("来源暂时不可访问 status=%s" % status)
        self.status = status
        self.retry_after = retry_after


class RateLimited(Exception):
    """429 限速，携带来源要求的等待秒数。"""

    def __init__(self, retry_after: float):
        super().__init__("来源限速，需等待 %.1f 秒" % retry_after)
        self.retry_after = retry_after


@dataclass
class FetchResult:
    url: str
    status: int
    body: bytes
    content_type: str
    observed_at: str
    sha256: str
    final_url: str


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # 返回 None 让 urllib 抛出 HTTPError，跳转由调用方显式裁决
        return None


class Fetcher:
    def __init__(self, policy: Policy, user_agent: str = "defensive-advisory-collector/1.0",
                 clock=time.monotonic, sleeper=time.sleep, max_retries: int = 2):
        self.policy = policy
        self.clock = clock
        self.sleeper = sleeper
        self.max_retries = max_retries
        self._last_request_at = -1.0
        self._opener = urllib.request.build_opener(_NoRedirectHandler())
        self.user_agent = user_agent

    def _respect_interval(self) -> None:
        min_gap = self.policy.request_interval_ms / 1000.0
        if min_gap <= 0 or self._last_request_at < 0:
            return
        elapsed = self.clock() - self._last_request_at
        if elapsed < min_gap:
            self.sleeper(min_gap - elapsed)

    def get(self, url_or_path: str) -> FetchResult:
        url = self.policy.resolve(url_or_path)
        if not self.policy.is_allowed_path(url):
            raise PermissionError("路径不在来源允许范围内: " + url)
        return self._get_with_retries(url, send_credentials=True)

    def get_without_credentials(self, url: str) -> FetchResult:
        """重定向后的再请求：显式剥离受限凭据。"""
        return self._get_with_retries(url, send_credentials=False)

    def _get_with_retries(self, url: str, send_credentials: bool) -> FetchResult:
        attempts = 0
        while True:
            self._respect_interval()
            headers = {"User-Agent": self.user_agent, "Accept": "*/*"}
            if send_credentials:
                headers.update(self.policy.auth_header(url))
            request = urllib.request.Request(url, headers=headers, method="GET")
            self._last_request_at = self.clock()
            try:
                response = self._opener.open(request, timeout=15)
                status, body, resp_headers = response.status, response.read(), response.headers
            except urllib.error.HTTPError as exc:
                status, body, resp_headers = exc.code, exc.read(), exc.headers
                if status in (301, 302, 303, 307, 308):
                    raise self._classify_redirect(url, resp_headers)
                if status == 429:
                    retry_after = float(resp_headers.get("Retry-After", "0") or 0)
                    if attempts < self.max_retries and retry_after > 0:
                        attempts += 1
                        self.sleeper(retry_after)
                        continue
                    raise RateLimited(retry_after)
                if 500 <= status < 600:
                    retry_after = resp_headers.get("Retry-After")
                    raise SourceUnavailable(status, float(retry_after) if retry_after else None)
                # 其余状态（含 404/410）原样返回，由编排层区分撤销/不存在/待核对
            return FetchResult(
                url=url,
                status=status,
                body=body,
                content_type=resp_headers.get("Content-Type", ""),
                observed_at=datetime.now(timezone.utc).isoformat(),
                sha256=hashlib.sha256(body).hexdigest(),
                final_url=url,
            )

    def _classify_redirect(self, from_url: str, headers) -> Exception:
        target = urljoin(from_url, headers.get("Location", ""))
        target_host = urlsplit(target).hostname or ""
        if target_host not in self.policy.redirect_allowed_hosts:
            # 未经允许的外部域：停止，不携带凭据继续
            return RedirectNotAllowed(target)
        if not self.policy.is_allowed_path(target):
            return PermissionError("重定向目标不在允许路径内: " + target)
        return SameSiteRedirect(target)
