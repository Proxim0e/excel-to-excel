import unittest
from unittest import mock

from src.utils import http_client


class PacerTests(unittest.TestCase):
    def test_zero_delay_never_sleeps(self):
        pacer = http_client.Pacer(0.0)
        with mock.patch.object(http_client.time, "sleep") as sleep:
            for _ in range(10):
                pacer.wait()
        sleep.assert_not_called()

    def test_delay_spaces_requests(self):
        pacer = http_client.Pacer(1.5)
        with mock.patch.object(http_client.time, "monotonic", return_value=100.0), \
                mock.patch.object(http_client.time, "sleep") as sleep:
            pacer.wait()
            pacer.wait()
            pacer.wait()
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [1.5, 3.0])

    def test_pacers_are_independent(self):
        scrape, download = http_client.Pacer(0.0), http_client.Pacer(1.5)
        with mock.patch.object(http_client.time, "monotonic", return_value=100.0), \
                mock.patch.object(http_client.time, "sleep") as sleep:
            download.wait()
            for _ in range(5):
                scrape.wait()
        sleep.assert_not_called()


class SmartRequestPacerTests(unittest.TestCase):
    def ok(self, *a, **k):
        return mock.Mock(status_code=200)

    def test_uses_given_pacer(self):
        pacer = mock.Mock()
        http_client.smart_request(self.ok, "https://x.md/f", pacer=pacer)
        pacer.wait.assert_called_once()

    def test_defaults_to_download_pacer(self):
        with mock.patch.object(http_client.DOWNLOAD_PACER, "wait") as wait:
            http_client.smart_request(self.ok, "https://x.md/f")
        wait.assert_called_once()


if __name__ == "__main__":
    unittest.main()
