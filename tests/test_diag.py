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

    def test_http_failure_is_explained(self):
        res = lambda *a, **k: [(0, 0, 0, "", ("1.2.3.4", 443))]
        conn = mock.Mock(return_value=mock.Mock(close=lambda: None))
        def get(*a, **k):
            raise requests.exceptions.ConnectionError("Connection aborted, ConnectionResetError(10054)")
        r = diag.probe_host(self.rep(), resolver=res, connector=conn, getter=get)
        self.assertFalse(r.ok)
        self.assertIn("сброшено", r.http)
        self.assertIn("HTTPS", r.verdict())

    def test_success(self):
        res = lambda *a, **k: [(0, 0, 0, "", ("1.2.3.4", 443))]
        conn = mock.Mock(return_value=mock.Mock(close=lambda: None))
        resp = mock.Mock(status_code=200, headers={"Content-Type": "application/pdf"})
        resp.iter_content = lambda n: iter([b"%PDF"])
        r = diag.probe_host(self.rep(), resolver=res, connector=conn, getter=lambda *a, **k: resp)
        self.assertTrue(r.ok)
        self.assertEqual(r.verdict(), "OK")


if __name__ == "__main__":
    unittest.main()
