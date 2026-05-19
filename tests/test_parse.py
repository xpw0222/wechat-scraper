import unittest
from pathlib import Path

from wechat_scraper.parse import (
    DELETED_MARKERS,
    VERIFY_MARKERS,
    detect_status_from_html,
    extract_content_text,
    js_decode,
    parse,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class TestJsDecode(unittest.TestCase):
    def test_known_escapes(self):
        src = "\\x3cp\\x3ehi\\x26bye\\x3c/p\\x3e"
        self.assertEqual(js_decode(src), "<p>hi&bye</p>")


class TestStatusDetection(unittest.TestCase):
    def test_verify_fixture(self):
        self.assertEqual(detect_status_from_html(_read("verify.html")), "verify")

    def test_deleted_fixture(self):
        self.assertEqual(detect_status_from_html(_read("deleted.html")), "deleted")

    def test_neither_returns_none(self):
        self.assertIsNone(detect_status_from_html("<html><body>hello</body></html>"))

    def test_each_verify_marker(self):
        for marker in VERIFY_MARKERS:
            with self.subTest(marker=marker):
                html = f"<html><body>{marker}</body></html>"
                self.assertEqual(detect_status_from_html(html), "verify")

    def test_each_deleted_marker(self):
        for marker in DELETED_MARKERS:
            with self.subTest(marker=marker):
                html = f"<html><body>{marker}</body></html>"
                self.assertEqual(detect_status_from_html(html), "deleted")


class TestParseFixtures(unittest.TestCase):
    def test_success_jsdecode_form(self):
        pr = parse(_read("success.html"))
        self.assertEqual(pr.status, "success", msg=str(pr))
        self.assertIn("合同纠纷", pr.content_text)
        self.assertIn("民法典", pr.content_text)
        self.assertIn("[IMG:https://example.com/img1.jpg]", pr.content_text)

    def test_success_bare_form(self):
        pr = parse(_read("success_bare.html"))
        self.assertEqual(pr.status, "success", msg=str(pr))
        self.assertIn("合同纠纷", pr.content_text)
        self.assertIn("民法典", pr.content_text)

    def test_success_with_escaped_quote_in_body(self):
        # Regression: the body contains a literal `\'` inside the JS string
        # literal. The old [^'"]* regex would chop the article at that point
        # and silently persist a truncated success. The full article must be
        # captured end-to-end so all three paragraphs land in content_text.
        pr = parse(_read("success_with_quote.html"))
        self.assertEqual(pr.status, "success", msg=str(pr))
        self.assertIn("本案事实清楚", pr.content_text)
        self.assertIn("民法典", pr.content_text)
        self.assertIn("维护当事人合法权益", pr.content_text)

    def test_empty(self):
        pr = parse(_read("empty.html"))
        self.assertEqual(pr.status, "empty")

    def test_verify(self):
        pr = parse(_read("verify.html"))
        self.assertEqual(pr.status, "verify")
        self.assertEqual(pr.content_text, "")

    def test_deleted(self):
        pr = parse(_read("deleted.html"))
        self.assertEqual(pr.status, "deleted")

    def test_extract_no_marker_returns_empty(self):
        self.assertEqual(extract_content_text("<html><body><p>no js</p></body></html>"), "")

    def test_first_content_noencode_wins(self):
        # Some pages have a second content_noencode in a footer/ad template.
        # The article body is always the first match in production HTML.
        pr = parse(_read("multi_match.html"))
        self.assertEqual(pr.status, "success", msg=str(pr))
        self.assertIn("真正的文章正文", pr.content_text)
        self.assertIn("民法典", pr.content_text)
        self.assertNotIn("广告内容", pr.content_text)

    def test_verify_marker_beats_content(self):
        # A verify interstitial sometimes ships with a stale content blob in
        # the same page. We must NOT extract it — those articles need to be
        # retried after the captcha is solved, not persisted with stale text.
        pr = parse(_read("verify_with_content.html"))
        self.assertEqual(pr.status, "verify")
        self.assertEqual(pr.content_text, "")


if __name__ == "__main__":
    unittest.main()
