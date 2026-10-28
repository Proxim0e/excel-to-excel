import socket
import unittest
from unittest import mock

import requests

from src import diag
from src.utils import http_client


class DescribeNetworkErrorTests(unittest.TestCase):
    def test_dns(self):
        e = requests.exceptions.ConnectionError("HTTPSConnectionPool: getaddrinfo failed")
        out = http_client.describe_network_error(e, "https://files.example.md/a.pdf")
        self.assertIn("DNS", out)
        self.assertIn("files.example.md", out)

    def test_reset_windows(self):
        e = requests.exceptions.ConnectionError("('Connection aborted.', ConnectionResetError(10054, 'forcibly closed'))")
        self.assertIn("сброшено", http_client.describe_network_error(e, "https://x.md/f"))

    def test_ssl(self):
        e = requests.exceptions.SSLError("certificate verify failed")
        self.assertIn("SSL", http_client.describe_network_error(e, "https://x.md/f"))

    def test_timeout(self):
        e = requests.exceptions.ConnectTimeout("timed out")
        self.assertIn("таймаут", http_client.describe_network_error(e, "https://x.md/f"))

    def test_unknown_still_names_class_and_host(self):
        out = http_client.describe_network_error(ValueError("strange"), "https://x.md/f")
        self.assertTrue(out.startswith("ValueError"))
        self.assertIn("x.md", out)


class DownloadSessionTests(unittest.TestCase):
    def test_session_has_browser_user_agent(self):
        s = http_client.make_download_session()
        ua = s.headers["User-Agent"]
        self.assertIn("Mozilla/5.0", ua)
        self.assertNotIn("scraper", ua)
        self.assertIn("pdf", s.headers["Accept"])

    def test_session_keeps_project_headers_not_overridden(self):
        from src.config import HEADERS
        s = http_client.make_download_session()
        for k in HEADERS:
            self.assertIn(k, s.headers)


class StopAdviceTests(unittest.TestCase):
    def test_network_vs_site_limits(self):
        self.assertTrue(http_client._is_network_reason("4 сбоев подряд, последний: ConnectionError: DNS"))
        self.assertFalse(http_client._is_network_reason("4 сбоев подряд, последний: HTTP 403"))
        self.assertFalse(http_client._is_network_reason("HTTP 429 (Retry-After: 60), URL: x"))


class GroupByHostTests(unittest.TestCase):
    def test_groups_and_ports(self):
        g = diag.group_by_host(["https://a.md/1.pdf", "https://a.md/2.pdf", "http://b.md/x", "bad"])
        self.assertEqual(set(g), {"a.md:443", "b.md:80"})
        self.assertEqual(len(g["a.md:443"].urls), 2)


class ProbeHostTests(unittest.TestCase):
    def rep(self):
        return diag.HostReport(host="a.md", port=443, urls=["https://a.md/f.pdf"])

    def test_dns_failure_stops_early(self):
        def bad(*a, **k):
            raise socket.gaierror("getaddrinfo failed")
        r = diag.probe_host(self.rep(), resolver=bad)
        self.assertTrue(r.dns.startswith("FAIL"))
        self.assertEqual(r.tcp, "")
        self.assertIn("DNS", r.verdict())

    def test_tcp_failure(self):
        res = lambda *a, **k: [(0, 0, 0, "", ("1.2.3.4", 443))]
        def conn(*a, **k):
            raise ConnectionRefusedError("refused")
        r = diag.probe_host(self.rep(), resolver=res, connector=conn)
        self.assertTrue(r.tcp.startswith("FAIL"))
        self.assertIn("TCP", r.verdict())

    RES = staticmethod(lambda *a, **k: [(0, 0, 0, "", ("1.2.3.4", 443))])

    def conn(self):
        return mock.Mock(return_value=mock.Mock(close=lambda: None))

    def test_tls_reset_is_reported_as_tls(self):
        def bad_tls(*a, **k):
            raise ConnectionResetError(10054, "reset")
        r = diag.probe_host(self.rep(), resolver=self.RES, connector=self.conn(), tls_checker=bad_tls)
        self.assertTrue(r.tls.startswith("FAIL"))
        self.assertIn("TLS", r.verdict())
        self.assertEqual(r.http, "")

    def _resp(self, code=200):
        resp = mock.Mock(status_code=code, headers={"Content-Type": "application/pdf"})
        resp.iter_content = lambda n: iter([b"%PDF"])
        return resp

    def test_scraper_user_agent_rejected_browser_passes(self):
        def get(url, headers=None, **k):
            if "scraper" in headers.get("User-Agent", ""):
                raise requests.exceptions.ConnectionError("ConnectionResetError(10054)")
            return self._resp()
        r = diag.probe_host(self.rep(), resolver=self.RES, connector=self.conn(), getter=get,
                            tls_checker=lambda *a: "TLSv1.3", curl_runner=lambda u: (False, "x"))
        self.assertFalse(r.ok)
        self.assertTrue(r.ok_browser)
        self.assertIn("User-Agent", r.verdict())

    def test_curl_ok_python_fails(self):
        def get(*a, **k):
            raise requests.exceptions.ConnectionError("ConnectionResetError(10054)")
        r = diag.probe_host(self.rep(), resolver=self.RES, connector=self.conn(), getter=get,
                            tls_checker=lambda *a: "TLSv1.3", curl_runner=lambda u: (True, "HTTP/2 200"))
        self.assertIn("curl", r.verdict())

    def test_everything_fails_after_tls(self):
        def get(*a, **k):
            raise requests.exceptions.ConnectionError("ConnectionResetError(10054)")
        r = diag.probe_host(self.rep(), resolver=self.RES, connector=self.conn(), getter=get,
                            tls_checker=lambda *a: "TLSv1.3", curl_runner=lambda u: (False, "FAIL"))
        self.assertIn("блокировка", r.verdict())
        self.assertIn("сброшено", r.http)

    def test_success(self):
        res = lambda *a, **k: [(0, 0, 0, "", ("1.2.3.4", 443))]
        conn = mock.Mock(return_value=mock.Mock(close=lambda: None))
        resp = mock.Mock(status_code=200, headers={"Content-Type": "application/pdf"})
        resp.iter_content = lambda n: iter([b"%PDF"])
        r = diag.probe_host(self.rep(), resolver=res, connector=conn, getter=lambda *a, **k: resp,
                            tls_checker=lambda *a: "TLSv1.3")
        self.assertTrue(r.ok)
        self.assertEqual(r.verdict(), "OK")


if __name__ == "__main__":
    unittest.main()
