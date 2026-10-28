import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from src import manifest as manifest_mod
from src.manifest import Manifest


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.m = Manifest(self.root / "manifest.sqlite")

    def tearDown(self):
        self.m.close()
        self.tmp.cleanup()

    def put(self, rel, data):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return p

    def rec(self, path, url, lot_no=1, status="success"):
        return self.m.record(tender_id="T1", lot_no=lot_no, lot_title=f"Lot {lot_no}", participant="ACME",
                             original_name=Path(path).name, url=url, path=path, status=status)

    def rows(self, sql):
        self.m.close()
        c = sqlite3.connect(self.root / "manifest.sqlite")
        try:
            return c.execute(sql).fetchall()
        finally:
            c.close()

    def test_same_content_is_one_document(self):
        a = self.put("lot1/acme/anexa22.pdf", b"%PDF-same")
        b = self.put("lot2/acme/anexa22.pdf", b"%PDF-same")
        c = self.put("lot2/beta/anexa22.pdf", b"%PDF-other")
        self.rec(a, "u1", 1)
        self.rec(b, "u1", 2, status="copied")
        self.rec(c, "u2", 2)
        self.assertEqual(self.m.stats(), {"refs": 3, "documents": 2, "missing": 0})

    def test_different_urls_same_content_dedupe_by_hash(self):
        a = self.put("lot1/a.pdf", b"%PDF-x")
        b = self.put("lot2/b.pdf", b"%PDF-x")
        self.assertEqual(self.rec(a, "u1"), self.rec(b, "u2"))
        self.assertEqual(self.m.stats()["documents"], 1)

    def test_rerun_replaces_ref_not_duplicates(self):
        a = self.put("lot1/a.pdf", b"%PDF-x")
        self.rec(a, "u1", status="success")
        self.rec(a, "u1", status="skipped")
        self.assertEqual(self.rows("SELECT status FROM document_refs"), [("skipped",)])

    def test_failed_download_is_recorded_without_hash(self):
        self.assertIsNone(self.rec(self.root / "lot1/missing.pdf", "u9", status="error"))
        self.assertEqual(self.m.stats(), {"refs": 1, "documents": 0, "missing": 1})

    def test_copies_of_same_url_hashed_once(self):
        a = self.put("lot1/a.pdf", b"%PDF-x")
        b = self.put("lot2/a.pdf", b"%PDF-x")
        with mock.patch.object(manifest_mod, "file_sha256", wraps=manifest_mod.file_sha256) as sha:
            self.rec(a, "u1", 1)
            self.rec(b, "u1", 2)
        self.assertEqual(sha.call_count, 1)

    def test_parallel_writes(self):
        paths = [self.put(f"lot{i}/f.pdf", f"%PDF-{i % 5}".encode()) for i in range(60)]
        threads = [threading.Thread(target=self.rec, args=(p, f"u{i}", i)) for i, p in enumerate(paths)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(self.m.stats(), {"refs": 60, "documents": 5, "missing": 0})


if __name__ == "__main__":
    unittest.main()
