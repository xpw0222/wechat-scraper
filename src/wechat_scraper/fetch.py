# src/wechat_scraper/fetch.py
from __future__ import annotations

import time
from dataclasses import dataclass
from itertools import cycle

import requests
from requests.adapters import HTTPAdapter

from wechat_scraper.config import HTTPConfig, NetworkConfig


class SourceAddressAdapter(HTTPAdapter):
    """Bind outgoing sockets to a specific local source IP."""

    def __init__(self, source_address: str, *args, **kwargs):
        self._source_address = (source_address, 0)
        super().__init__(*args, **kwargs)

    def init_poolmanager(self, *args, **kwargs):
        kwargs["source_address"] = self._source_address
        return super().init_poolmanager(*args, **kwargs)


class FetchTimeout(Exception):
    pass


class FetchError(Exception):
    pass


@dataclass(frozen=True)
class FetchResult:
    http_status: int
    body: str
    elapsed_ms: int
    egress_ip: str | None = None  # 记录本次请求由哪个本地 IP 发出，方便 debug 打印日志


def build_session(http: HTTPConfig, bind_ip: str | None = None) -> requests.Session:
    s = requests.Session()
    if bind_ip:
        adapter = SourceAddressAdapter(bind_ip)
        s.mount("https://", adapter)
        s.mount("http://", adapter)
    s.headers.update(
        {
            "User-Agent": http.user_agent,
            "Accept": "*/*",
            "Accept-Language": http.accept_language,
            "Accept-Encoding": "gzip, deflate, br",
        }
    )
    return s


class RotatingFetchBridge:
    """管理多 IP 对应的 Sessions，提供自动轮换与负载能力"""
    
    def __init__(self, http_cfg: HTTPConfig, net_cfg: NetworkConfig, override_bind_ip: str | None = None):
        self.http_cfg = http_cfg
        
        # 确定最终使用的 IP 池
        if override_bind_ip:
            # 如果 CLI 显式指定了 --bind-ip，则降级为单 IP 固定绑定模式
            self.ips = [override_bind_ip]
        else:
            self.ips = net_cfg.ip_pool if net_cfg.ip_pool else [None]  # None 代表使用默认单出口网卡
            
        # 为每一个 IP 预建一个持久的 Session 对象，复用连接池
        self.sessions = {ip: build_session(http_cfg, bind_ip=ip) for ip in self.ips}
        # 构建一个无限循环的迭代器
        self.ip_pool_cycle = cycle(self.ips)
        self.current_ip = next(self.ip_pool_cycle)

    def rotate(self) -> str | None:
        """主动切换到下一个 IP"""
        self.current_ip = next(self.ip_pool_cycle)
        return self.current_ip

    def fetch(self, url: str) -> FetchResult:
        """调用当前选定的 IP Session 执行请求"""
        session = self.sessions[self.current_ip]
        res = fetch_one(session, url, timeout=self.http_cfg.timeout_seconds)
        # 把当前的绑定 IP 塞进结果，方便 pipeline 模块的 stdout/log 记录统计
        return FetchResult(
            http_status=res.http_status, 
            body=res.body, 
            elapsed_ms=res.elapsed_ms, 
            egress_ip=self.current_ip
        )


def _decoded_body(resp: requests.Response) -> str:
    ctype = resp.headers.get("content-type", "")
    if "charset=" not in ctype.lower():
        resp.encoding = "utf-8"
    return resp.text


def fetch_one(session: requests.Session, url: str, timeout: int = 30) -> FetchResult:
    t0 = time.monotonic()
    try:
        resp = session.get(url, timeout=timeout, allow_redirects=True)
    except requests.exceptions.Timeout as e:
        raise FetchTimeout(str(e)) from e
    except requests.exceptions.RequestException as e:
        raise FetchError(f"{type(e).__name__}: {e}") from e
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    return FetchResult(
        http_status=resp.status_code, body=_decoded_body(resp), elapsed_ms=elapsed_ms
    )
