"""Tests for fetch._decoded_body — ensures UTF-8 fallback when the server
omits charset, so we never silently write mojibake into the shard."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from wechat_scraper.fetch import _decoded_body


class _FakeResponse:
    """Minimal stand-in for requests.Response used by _decoded_body.

    We bypass the network entirely; `_decoded_body` only touches `headers`,
    `encoding`, and `text`. To exercise the real decode path we mimic
    requests' behaviour: `text` decodes `_content` using `encoding`.
    """

    def __init__(self, body: bytes, content_type: str, server_encoding: str | None):
        self._content = body
        self.headers = {"content-type": content_type}
        # Mirror what requests would set after inspecting Content-Type.
        self.encoding = server_encoding

    @property
    def text(self) -> str:
        enc = self.encoding or "utf-8"
        return self._content.decode(enc, errors="replace")


class TestDecodedBody(unittest.TestCase):
    def test_utf8_with_explicit_charset(self):
        body = "合同纠纷".encode("utf-8")
        resp = _FakeResponse(body, "text/html; charset=UTF-8", "UTF-8")
        self.assertEqual(_decoded_body(resp), "合同纠纷")

    def test_forces_utf8_when_server_omits_charset(self):
        # Regression: without this fix, requests defaults to ISO-8859-1 for
        # text/* responses lacking charset (RFC 7231). Decoding UTF-8 Chinese
        # as Latin-1 produces mojibake but the ASCII verify/content markers
        # would still match, so we'd write garbled text as `success`.
        body = "合同纠纷".encode("utf-8")
        resp = _FakeResponse(body, "text/html", "ISO-8859-1")
        decoded = _decoded_body(resp)
        self.assertEqual(decoded, "合同纠纷")
        # And confirm the fixture demonstrates what the bug would have produced
        # if we did NOT override the encoding.
        mojibake = body.decode("ISO-8859-1")
        self.assertNotEqual(decoded, mojibake)

    def test_honours_explicit_non_utf8_charset(self):
        # If a (hypothetical) server explicitly declares GBK we trust it
        # rather than blindly forcing UTF-8.
        body = "合同".encode("gbk")
        resp = _FakeResponse(body, "text/html; charset=gbk", "gbk")
        self.assertEqual(_decoded_body(resp), "合同")


if __name__ == "__main__":
    unittest.main()
