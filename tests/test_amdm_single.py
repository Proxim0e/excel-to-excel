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

    def test_launch_falls_back_to_edge(self):
        """chrome-headless-shell не запускается («Executable doesn't exist») - Edge из Windows."""
        from src.amdm import fetch
        channels = []

        class FakePw:
            def __init__(self):
                self.chromium = self

            def start(self):
                return self

            def launch(self, headless=True, channel=None):
                channels.append(channel)
                if channel != "msedge":
                    raise RuntimeError("BrowserType.launch: Executable doesn't exist at C:\\...\\chrome-headless-shell.exe")
                return "edge"

            def stop(self):
                pass
        f = fetch.PlaywrightFetcher()
        with mock.patch("playwright.sync_api.sync_playwright", lambda: FakePw()), \
                mock.patch.object(fetch.time, "sleep", lambda s: None):
            f._launch()
        self.assertEqual((channels, f._browser), ([None, None, None, "msedge"], "edge"))


class CollectRecordsTests(unittest.TestCase):
    """Проверка всего списка: сбой сайта на одном номере не обрывает остальные."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = RegistryCache(Path(self.tmp.name) / "amdm.sqlite")

    def tearDown(self):
        self.cache.close()
        self.tmp.cleanup()

    def run_with(self, fetcher, numbers):
        from src.amdm.__main__ import collect_records
        with mock.patch("src.amdm.fetch.PlaywrightFetcher", lambda headless=True: fetcher):
            return collect_records(self.cache, numbers, offline=False, headless=True)

    def test_one_timeout_skips_number_and_retries_it(self):
        fetcher = FlakyFetcher({"DM1": RECORD, "DM2": RECORD, "DM3": None}, fail_once={"DM2"})
        records, checked = self.run_with(fetcher, {"DM1", "DM2", "DM3"})
        self.assertEqual(fetcher.asked, ["DM1", "DM2", "DM3", "DM2"])   # DM2 - ещё раз в конце
        self.assertEqual(fetcher.reloads, 1)
        self.assertEqual(checked, {"DM1", "DM2", "DM3"})
        self.assertIsNotNone(records["DM2"])

    def test_site_down_stops_after_failures_in_row(self):
        from src.amdm import __main__ as cli
        numbers = {f"DM{i}" for i in range(10)}
        fetcher = FlakyFetcher({}, fail_always=True)
        records, checked = self.run_with(fetcher, numbers)
        self.assertEqual(len(fetcher.asked), cli.MAX_FAILURES_IN_ROW)   # без повтора и без ожидания по всем
        self.assertEqual((records, checked), ({}, set()))


class FlakyFetcher(FakeFetcher):
    def __init__(self, records, fail_once=(), fail_always=False):
        super().__init__(records)
        self.fail_once, self.fail_always, self.reloads = set(fail_once), fail_always, 0

    def lookup(self, dm):
        self.asked.append(dm)
        if self.fail_always or dm in self.fail_once:
            self.fail_once.discard(dm)
            raise RuntimeError("Locator.click: Timeout 30000ms exceeded.")
        return self.records.get(dm)

    def reload(self):
        self.reloads += 1


if __name__ == "__main__":
    unittest.main()
