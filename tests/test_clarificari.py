import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from openpyxl import Workbook, load_workbook
from openpyxl.styles import PatternFill

from src import classify as cl
from src import clarificari as clar
from src.excel import fill_offers as fo
from src.excel.columns import insert_column, shift_formula
from src.manifest import MANIFEST_NAME, migrate

YELLOW = PatternFill("solid", fgColor=fo.COLORS["yellow"])


class ColumnTests(unittest.TestCase):
    def test_shift_formula(self):
        self.assertEqual(shift_formula("=F8/A1", 6), "=G8/A1")
        self.assertEqual(shift_formula("=E8/D8", 6), "=E8/D8")
        self.assertEqual(shift_formula('=IF(F8>0,"F8",0)', 6), '=IF(G8>0,"F8",0)')
        self.assertEqual(shift_formula("=Sheet2!F8+$F$8", 6), "=Sheet2!F8+$G$8")

    def test_insert_column_moves_formulas_merges_widths(self):
        wb = Workbook()
        ws = wb.active
        ws["E7"], ws["F7"], ws["F8"] = "=E8/A1", "=F8/A1", 10
        ws.merge_cells("B1:D4")
        ws.merge_cells("F12:G12")
        ws.column_dimensions["F"].width = 50
        ws.column_dimensions["G"].width = 9
        insert_column(ws, 6, style_from=5)
        self.assertEqual((ws["E7"].value, ws["G7"].value, ws["G8"].value), ("=E8/A1", "=G8/A1", 10))
        self.assertIsNone(ws["F7"].value)
        self.assertEqual(sorted(str(r) for r in ws.merged_cells.ranges), ["B1:D4", "G12:H12"])
        self.assertEqual((ws.column_dimensions["G"].width, ws.column_dimensions["H"].width), (50, 9))


class MatchTests(unittest.TestCase):
    PARTICIPANTS = ["Electeh SRL", '"ALMICOM-TECH" S.R.L.', "ÎM „Dutchmed-M” SRL", "DITA ESTFARM S.R.L"]

    def test_folder_names(self):
        self.assertEqual(clar.match_participant("Electeh", self.PARTICIPANTS), "Electeh SRL")
        self.assertEqual(clar.match_participant("ALMICOM TECH", self.PARTICIPANTS), '"ALMICOM-TECH" S.R.L.')
        self.assertEqual(clar.match_participant("Dutchmed-M", self.PARTICIPANTS), "ÎM „Dutchmed-M” SRL")
        self.assertEqual(clar.match_participant("Dita Estfarm", self.PARTICIPANTS), "DITA ESTFARM S.R.L")
        self.assertIsNone(clar.match_participant("Firma Necunoscuta", self.PARTICIPANTS))

    def test_mentioned_lots(self):
        self.assertEqual(clar.mentioned_lots("202607301735_Lot 29,35,215,216 FIAB"), {29, 35, 215, 216})
        self.assertEqual(clar.mentioned_lots("Loturile 3 și 4"), {3, 4})
        self.assertEqual(clar.mentioned_lots("slot 5, Pilot 7"), set())

    def test_relevant_pages(self):
        pages = [("a", "raspuns lot 2.pdf", 1, "text"), ("b", "catalog.pdf", 1, "model F4141 ..."),
                 ("c", "alt.pdf", 1, "nimic")]
        self.assertEqual({p[0] for p in clar.relevant_pages(pages, 2, "Electrod F4141")}, {"a", "b"})
        self.assertEqual({p[0] for p in clar.relevant_pages(pages, 9, "Masca")}, set())


class RoundTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.tender = self.root / "tender"
        self.tender.mkdir()
        conn = sqlite3.connect(self.tender / MANIFEST_NAME)
        migrate(conn)
        conn.execute("INSERT INTO documents (sha256, size, first_seen, doc_type) VALUES "
                     "('a22', 1, 'now', 'anexa22'), ('cat', 1, 'now', 'datasheet')")
        for sha, name, who in (("a22", "anexa22.pdf", "SOGNO"), ("cat", "catalog.pdf", "SOGNO"),
                               ("a22", "anexa22-l.pdf", "Labromed Laborator SRL")):
            conn.execute("INSERT INTO document_refs (path, tender_id, lot_no, lot_title, participant, original_name,"
                         " url, sha256, status, recorded_at) VALUES (?, 'T', 2, 'Lot 2', ?, ?, 'u', ?, 'ok', 'now')",
                         (f"lot2/{who}/{name}", who, name, sha))
        conn.execute("INSERT INTO document_pages (sha256, page_no, text, page_type) VALUES "
                     "('a22', 1, 'Anexa 22', 'anexa22'), ('cat', 1, 'Riester Metpak\nNo. 5270\n500 ml', 'datasheet')")
        conn.execute("INSERT INTO anexa22_offers (sha256, page_no, participant, lot_no, model, country, producer, "
                     "spec_offered, standards, dm, parsed_at) VALUES ('a22', 1, 'SOGNO', 2, 'Metpak (cod 5270)', "
                     "'Germania', 'RIESTER', 'Manseta 500 ml, Lungime furtun: 120 cm', '', '', 'now')")
        conn.commit()
        conn.close()

        wb = Workbook()
        ws = wb.active
        ws.title = "lot 2"
        ws["B11"] = "Manșete 500 ml, Lungime furtun ≥ 100 cm"
        ws["E1"], ws["E2"], ws["E7"], ws["E11"] = "Tehnic    corespunde ", "Ofertant: SOGNO", "=E8/A1", "spec"
        ws["F1"], ws["F2"], ws["F7"] = "Tehnic    corespunde ", "Ofertant: Labromed Laborator SRL", "=F8/A1"
        for a in ("E1", "E11", "F1"):
            ws[a].fill = YELLOW
        ws.sheet_properties.tabColor = fo.COLORS["yellow"]
        self.xlsx = self.root / "result.xlsx"
        wb.save(self.xlsx)

        self.clar = self.root / "clarificari"
        (self.clar / "Sogno").mkdir(parents=True)
        (self.clar / "Necunoscut").mkdir()
        (self.clar / "Sogno" / "raspuns.pdf").write_bytes(b"%PDF-raspuns")
        texts = {"raspuns.pdf": ["Raspuns la clarificari pentru lotul 2: lungime furtun 120 cm"]}
        patcher = mock.patch.object(cl, "extract_pages", side_effect=lambda path: texts.get(Path(path).name, [""]))
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def test_round_adds_column_and_recolors(self):
        stats = clar.recheck_result(self.xlsx, self.tender, self.clar)
        self.assertEqual((stats["round"], stats["rechecked"], stats.get("green")), (1, 1, 1))
        self.assertEqual(stats["unmatched"], ["Necunoscut"])
        ws = load_workbook(self.xlsx)["lot 2"]
        self.assertEqual(ws["F3"].value, "clarificări (runda 1)")
        self.assertEqual(ws["F4"].value, "raspuns.pdf")
        self.assertIn('"Raspuns la clarificari pentru lotul 2: lungime furtun 120 cm", p[1], raspuns.pdf', ws["F11"].value)
        self.assertEqual(ws["F11"].fill.fgColor.rgb, fo.COLORS["green"])
        self.assertEqual(ws["E1"].fill.fgColor.rgb, fo.COLORS["green"])      # итог участника
        self.assertEqual(ws.sheet_properties.tabColor.rgb, fo.COLORS["yellow"])  # Labromed всё ещё жёлтый
        self.assertEqual((ws["G2"].value, ws["G7"].value), ("Ofertant: Labromed Laborator SRL", "=G8/A1"))

    def test_same_round_twice_does_not_duplicate(self):
        clar.recheck_result(self.xlsx, self.tender, self.clar)
        stats = clar.recheck_result(self.xlsx, self.tender, self.clar)
        self.assertEqual((stats["round"], stats.get("rechecked", 0), stats["already"]), (1, 0, 1))
        self.assertEqual(load_workbook(self.xlsx)["lot 2"]["G2"].value, "Ofertant: Labromed Laborator SRL")

    def test_irrelevant_document_does_not_confirm(self):
        (self.clar / "Sogno" / "raspuns.pdf").unlink()
        (self.clar / "Sogno" / "altceva.pdf").write_bytes(b"%PDF-alt")  # ни лота, ни модели
        stats = clar.recheck_result(self.xlsx, self.tender, self.clar)
        ws = load_workbook(self.xlsx)["lot 2"]
        self.assertEqual(stats.get("yellow"), 1)
        self.assertTrue(ws["F11"].value.startswith(clar.NOTHING_FOUND))
        self.assertEqual(ws["F11"].fill.fgColor.rgb, fo.COLORS["yellow"])

    def test_clarifications_invisible_to_regular_check(self):
        clar.recheck_result(self.xlsx, self.tender, self.clar)
        from src.anexa22.evidence import tech_pages
        conn = sqlite3.connect(self.tender / MANIFEST_NAME)
        names = {p[1] for p in tech_pages(conn, "SOGNO", None)}
        conn.close()
        self.assertNotIn("raspuns.pdf", names)


if __name__ == "__main__":
    unittest.main()
