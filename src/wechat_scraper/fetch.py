from __future__ import annotations

import time
from dataclasses import dataclass

import requests
from requests.adapters import HTTPAdapter

from wechat_scraper.config import HTTPConfig


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


def _decoded_body(resp: requests.Response) -> str:
    """Decode response bytes as UTF-8 unless the server sends an explicit charset.

    `resp.text` honours the Content-Type charset, but when WeChat (or any text/*
    server) omits charset, `requests` falls back to ISO-8859-1 per RFC 7231 —
    silently turning UTF-8 Chinese into mojibake. The verify/deleted marker
    checks and the `content_noencode` regex are ASCII, so they would still match
    on the garbled body and we would happily write garbage as `success`.
    Force UTF-8 when no explicit charset was sent.
    """
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
