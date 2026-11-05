"""Проверка AMDM одной оферты по кнопке у номера DM: сайт заново, при сбое сайта - локальная копия."""
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from src.amdm.cache import RegistryCache
from src.amdm.models import CHECK, IN_PROGRESS, OK, RegistryRecord
from src.amdm.single import Checker, check_single, offer_registration

RECORD = RegistryRecord(dm="DM000681926", name="Cablu ECG", model="U304-22P3E", catalog="U304-22P3E",
                        producer="Shenzhen UpnMed Equipment Co., Ltd")


class FakeFetcher:
    def __init__(self, records=None, fail=False):
        self.records, self.fail, self.asked = records or {}, fail, []

    def __enter__(self):
        if self.fail:
            raise RuntimeError("сайт не открылся")
        return self

    def __exit__(self, *exc):
        return False

    def lookup(self, dm):
        self.asked.append(dm)
        return self.records.get(dm)


class SingleCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "amdm.sqlite"
        self.offer = offer_registration(
            "Labromed Laborator SRL",
            "Producator: Shenzhen UpnMed Equipment Co., Ltd\nȚara: China\nModel: U304-22P3E", "DM000681926",
            "Lot nr. 276 Cablu de interconexiune ECG", "lot 276", 7)

    def tearDown(self):
        self.tmp.cleanup()

    def test_site_lookup_even_if_cached_as_not_found(self):
        cache = RegistryCache(self.db)
        cache.mark_not_found("DM000681926")
        cache.close()
        fetcher = FakeFetcher({"DM000681926": RECORD})
        [r] = check_single(self.offer, self.db, fetcher_factory=lambda: fetcher)
        self.assertEqual(fetcher.asked, ["DM000681926"])
        self.assertEqual(r.status, OK)
        cache = RegistryCache(self.db)
        self.assertEqual(cache.get("DM000681926").producer, RECORD.producer)   # копия реестра обновлена
        cache.close()

    def test_site_down_falls_back_to_local_copy(self):
        cache = RegistryCache(self.db)
        cache.put(RECORD, source="csv")              # не с сайта сегодня - идём на сайт, он не отвечает
        cache.close()
        [r] = check_single(self.offer, self.db, fetcher_factory=lambda: FakeFetcher(fail=True))
        self.assertEqual(r.status, OK)

    def test_not_found_is_yellow(self):
        [r] = check_single(self.offer, self.db, fetcher_factory=lambda: FakeFetcher())
        self.assertEqual(r.status, CHECK)
        self.assertIn("не найден", r.reasons[0])

    def test_fetched_today_from_site_skips_browser(self):
        cache = RegistryCache(self.db)
        cache.put(RECORD, source="site")              # проверка всего списка сегодня
        cache.close()
        opened = []
        [r] = check_single(self.offer, self.db, fetcher_factory=lambda: opened.append(1) or FakeFetcher())
        self.assertEqual((opened, r.status), ([], OK))

    def test_site_error_is_shown_instead_of_not_asked(self):
        [r] = check_single(self.offer, self.db, fetcher_factory=lambda: FakeFetcher(fail=True))
        self.assertEqual(r.status, IN_PROGRESS)
        self.assertIn("Сайт реестра не ответил (сайт не открылся)", r.reasons[0])

    def test_checker_queue_uses_one_browser(self):
        opened, got, done = [], [], threading.Event()
        fetcher = FakeFetcher({"DM000681926": RECORD})
        checker = Checker(self.db, factory=lambda: opened.append(1) or fetcher, idle=0.2)

        def cb(result):
            got.append(result)
            if len(got) == 3:
                done.set()
        for _ in range(3):
            checker.submit(self.offer, cb)
        self.assertTrue(done.wait(10))
        self.assertEqual(len(opened), 1)                 # один браузер на всю очередь
        self.assertEqual([r[0].status for r in got], [OK, OK, OK])
        checker.join(5)

    def test_launch_retries_after_driver_crash(self):
        from src.amdm import fetch
        calls = []

        class FakePw:
            def __init__(self):
                self.chromium = self

            def start(self):
                return self

            def launch(self, headless=True):
                calls.append(1)
                if len(calls) == 1:
                    raise RuntimeError("BrowserType.launch: Connection closed while reading from the driver")
                return "browser"

            def stop(self):
                pass
        f = fetch.PlaywrightFetcher()
        with mock.patch("playwright.sync_api.sync_playwright", lambda: FakePw()), \
                mock.patch.object(fetch.time, "sleep", lambda s: None):
            f._launch()
        self.assertEqual((len(calls), f._browser), (2, "browser"))


if __name__ == "__main__":
    unittest.main()
