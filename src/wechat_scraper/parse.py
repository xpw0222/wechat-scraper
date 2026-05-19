from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from bs4 import BeautifulSoup

Status = Literal[
    "success",
    "empty",
    "deleted",
    "verify",
    "parse_error",
]

VERIFY_MARKERS = (
    "安全验证",
    "滑块验证",
    "环境异常",
    "完成验证",
    "secitptpage",
    "cap_appid",
    "poc_token",
    "cap_sid",
)
DELETED_MARKERS = (
    "内容已被发布者删除",
    "此内容因违规无法查看",
)

# WeChat pages have two known forms for the encoded article body:
#   (A) content_noencode: JsDecode('\x3c...')      ← older
#   (B) content_noencode: '\x3c...'                 ← newer (no JsDecode wrapper)
# The body is a JS string literal: it can be single- or double-quoted, and the
# matching quote may appear escaped inside (`\'` / `\"`). Capture across escapes
# by allowing either `\<any char>` or any char that isn't the opening quote.
CONTENT_RE = re.compile(
    r"""content_noencode:\s*(?:JsDecode\()?(['"])((?:\\.|(?!\1).)*)\1\)?""",
    re.DOTALL,
)

_JS_DECODE_MAP = {
    "\\x5c": "\\",
    "\\x0d": "\r",
    "\\x22": '"',
    "\\x26": "&",
    "\\x27": "'",
    "\\x3c": "<",
    "\\x3e": ">",
    "\\x0a": "\n",
}

MIN_CONTENT_LEN = 50


def js_decode(s: str) -> str:
    for k, v in _JS_DECODE_MAP.items():
        s = s.replace(k, v)
    return s


@dataclass(frozen=True)
class ParseResult:
    status: Status
    content_text: str
    error: str | None = None


def detect_status_from_html(html: str) -> Status | None:
    """Return 'verify' / 'deleted' if those markers are present, else None."""
    if any(m in html for m in VERIFY_MARKERS):
        return "verify"
    if any(m in html for m in DELETED_MARKERS):
        return "deleted"
    return None


def extract_content_text(html: str) -> str:
    """Find content_noencode JS, decode escapes, parse HTML, return clean text.
    Empty string if no match."""
    m = CONTENT_RE.search(html)
    if not m:
        return ""
    raw = js_decode(m.group(2))
    soup = BeautifulSoup(raw, "lxml")
    for tag in soup(["script", "style"]):
        tag.decompose()
    parts: list[str] = []
    for el in soup.descendants:
        name = getattr(el, "name", None)
        if name == "img":
            src = el.get("data-src") or el.get("src") or ""
            if src:
                parts.append(f"[IMG:{src}]")
        elif name is None:
            text = str(el).strip()
            if text:
                parts.append(text)
    return "\n".join(parts).strip()


def parse(html: str) -> ParseResult:
    # Marker detection is pure string-in; let any exception there propagate
    # rather than be miscategorised as parse_error and written as terminal.
    marker_status = detect_status_from_html(html)
    if marker_status is not None:
        return ParseResult(status=marker_status, content_text="")
    try:
        text = extract_content_text(html)
    except Exception as e:
        return ParseResult(status="parse_error", content_text="", error=f"{type(e).__name__}: {e}")
    if len(text) < MIN_CONTENT_LEN:
        return ParseResult(status="empty", content_text=text)
    return ParseResult(status="success", content_text=text)
