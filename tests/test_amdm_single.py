"""Проверка AMDM одной оферты по кнопке у номера DM: сайт заново, при сбое сайта - локальная копия."""
import tempfile
import unittest
from pathlib import Path

from src.amdm.cache import RegistryCache
from src.amdm.models import CHECK, OK, RegistryRecord
from src.amdm.single import check_single, offer_registration

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
        cache.put(RECORD, source="site")
        cache.close()
        [r] = check_single(self.offer, self.db, fetcher_factory=lambda: FakeFetcher(fail=True))
        self.assertEqual(r.status, OK)

    def test_not_found_is_yellow(self):
        [r] = check_single(self.offer, self.db, fetcher_factory=lambda: FakeFetcher())
        self.assertEqual(r.status, CHECK)
        self.assertIn("не найден", r.reasons[0])


if __name__ == "__main__":
    unittest.main()
