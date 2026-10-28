import sqlite3
import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook, load_workbook

from src.anexa22 import evidence as ev
from src.excel import fill_offers as fo
from src.manifest import MANIFEST_NAME, migrate

TEMPLATE_ROW3 = "Producator: \nȚara: \nModel:"


class EvidenceTests(unittest.TestCase):
    def test_model_tokens(self):
        self.assertEqual(ev.model_tokens("Electrod lama (Codul produsului:F4141)"), ["F4141"])
        self.assertEqual(ev.model_tokens("M3D, 3011-00010-01"), ["3011-00010-01"])
        self.assertEqual(ev.model_tokens("Masca adult"), [])

    def test_spec_values(self):
        values = ev.spec_values("lungime 70 mm (+-5mm), Ø 49mm, 0~300mm Hg, 500 ml, 3m, 1,5 ml, 10 cm ± 2 cm")
        # допуски «+-5mm», «± 2 cm» - не параметры
        self.assertEqual(values, [("70", "mm"), ("49", "mm"), ("300", "mm"), ("500", "ml"), ("3", "m"),
                                  ("1.5", "ml"), ("10", "cm")])

    def test_parameter_label(self):
        spec = "3. Tip tub: Smoothbore 4. Lungime: ≥ 15 cm 5. Funcționalitate: Swivel"
        self.assertEqual(ev.parameter_label(spec, "15", "cm"), "Lungime: ≥ 15 cm")
        spec = "Lavabilă la temperatura: 60° C Manometru: Ø 49mm, 0~300mm Hg"
        self.assertEqual(ev.parameter_label(spec, "49", "mm"), "Manometru: Ø 49mm")

    def test_format(self):
        items = [ev.Evidence("catalog.pdf", 3, ['49 mm diameter', 'No. 5270'])]
        self.assertEqual(ev.format_evidence(items), '// "49 mm diameter" "No. 5270", p[3], catalog.pdf')


class FillTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        conn = sqlite3.connect(self.root / MANIFEST_NAME)
        migrate(conn)
        conn.execute("INSERT INTO documents (sha256, size, first_seen, doc_type) VALUES "
                     "('a22', 1, 'now', 'anexa22'), ('cat', 1, 'now', 'datasheet')")
        for sha, name in (("a22", "anexa22.pdf"), ("cat", "catalog.pdf")):
            conn.execute("INSERT INTO document_refs (path, tender_id, lot_no, lot_title, participant, original_name,"
                         " url, sha256, status, recorded_at) VALUES (?, 'T', 2, 'Lot 2', 'SOGNO', ?, 'u', ?, 'ok', 'now')",
                         (f"lot2/{name}", name, sha))
        conn.execute("INSERT INTO document_pages (sha256, page_no, text, page_type) VALUES "
                     "('a22', 1, 'Anexa 22 Metpak 5270 500 ml', 'anexa22'), "
                     "('cat', 1, 'Riester Metpak\nNo. 5270\nfor bags from 500 ml\nmanometer 49 mm diameter', 'datasheet')")
        conn.execute("INSERT INTO anexa22_offers (sha256, page_no, participant, lot_no, model, country, producer, "
                     "spec_offered, standards, dm, parsed_at) VALUES ('a22', 1, 'SOGNO', 2, 'Metpak 500ml (cod 5270)', "
                     "'Germania', 'RIESTER', 'Manseta 500 ml, Manometru: Ø 49mm', 'CE, ISO', '', 'now')")
        conn.commit()
        conn.close()

        wb = Workbook()
        ws = wb.active
        ws.title = "lot 2"
        ws["E2"], ws["E3"] = "Ofertant: SOGNO", TEMPLATE_ROW3
        ws["F2"], ws["F4"] = "Ofertant: Labromed Laborator SRL", "уже заполнено человеком"
        ws["G2"] = "Ofertant: "
        self.xlsx = self.root / "result.xlsx"
        wb.save(self.xlsx)

    def tearDown(self):
        self.tmp.cleanup()

    def test_fill_rows_and_evidence(self):
        stats = fo.fill_result(self.xlsx, self.root)
        self.assertEqual((stats["participants"], stats["matched"], stats["missing"]), (2, 1, 1))
        ws = load_workbook(self.xlsx)["lot 2"]
        self.assertEqual(ws["E3"].value, "Producator: RIESTER\nȚara: Germania\nModel: Metpak 500ml (cod 5270)")
        self.assertEqual(ws["E4"].value, "CE, ISO")
        spec = ws["E11"].value
        self.assertTrue(spec.startswith("Manseta 500 ml, Manometru: Ø 49mm\n// "))
        self.assertIn('"No. 5270"', spec)
        self.assertIn('"manometer 49 mm diameter"', spec)
        self.assertIn("p[1], catalog.pdf", spec)
        self.assertNotIn("anexa22.pdf", spec)  # Anexa 22 не подтверждает сама себя
        self.assertEqual(ws["F4"].value, "уже заполнено человеком")

    def test_nothing_overwritten_on_second_run(self):
        fo.fill_result(self.xlsx, self.root)
        wb = load_workbook(self.xlsx)
        wb["lot 2"]["E11"] = "правка человека"
        wb.save(self.xlsx)
        stats = fo.fill_result(self.xlsx, self.root)
        self.assertEqual((stats["row3"], stats["row11"]), (0, 0))
        self.assertEqual(load_workbook(self.xlsx)["lot 2"]["E11"].value, "правка человека")

    def test_colors_and_missing_parameters(self):
        conn = sqlite3.connect(self.root / MANIFEST_NAME)
        conn.execute("UPDATE anexa22_offers SET spec_offered = 'Manseta 500 ml, Lungime furtun: 120 cm'")
        conn.commit()
        conn.close()
        fo.fill_result(self.xlsx, self.root)
        ws = load_workbook(self.xlsx)["lot 2"]
        self.assertIn("// Lungime furtun: 120 cm", ws["E11"].value)
        self.assertEqual(ws["E11"].fill.fgColor.rgb, fo.COLORS[ev.YELLOW])
        self.assertEqual(ws.sheet_properties.tabColor.rgb, fo.COLORS[ev.YELLOW])

    def test_all_confirmed_is_green(self):
        fo.fill_result(self.xlsx, self.root)
        ws = load_workbook(self.xlsx)["lot 2"]
        self.assertEqual(ws["E11"].fill.fgColor.rgb, fo.COLORS[ev.GREEN])
        self.assertEqual(ws.sheet_properties.tabColor.rgb, fo.COLORS[ev.GREEN])

    def test_no_technical_documents(self):
        conn = sqlite3.connect(self.root / MANIFEST_NAME)
        conn.execute("DELETE FROM document_refs WHERE sha256 = 'cat'")
        conn.commit()
        conn.close()
        fo.fill_result(self.xlsx, self.root)
        ws = load_workbook(self.xlsx)["lot 2"]
        self.assertTrue(ws["E11"].value.endswith("\n//docs"))
        self.assertEqual(ws["E11"].fill.fgColor.rgb, fo.COLORS[ev.YELLOW])

    def test_documents_without_confirmation_are_not_docs_missing(self):
        conn = sqlite3.connect(self.root / MANIFEST_NAME)
        conn.execute("UPDATE document_pages SET text = 'Riester general catalog' WHERE sha256 = 'cat'")
        conn.commit()
        conn.close()
        fo.fill_result(self.xlsx, self.root)
        spec = load_workbook(self.xlsx)["lot 2"]["E11"].value
        self.assertNotIn("//docs", spec)
        self.assertIn("// model 5270; Manseta 500 ml; Manometru: Ø 49mm", spec)  # «500ml» - не код модели

    def test_brochure_from_another_lot(self):
        conn = sqlite3.connect(self.root / MANIFEST_NAME)
        conn.execute("UPDATE document_refs SET lot_no = 7, path = 'lot7/catalog.pdf' WHERE sha256 = 'cat'")
        conn.commit()
        conn.close()
        fo.fill_result(self.xlsx, self.root)
        self.assertIn("p[1], catalog.pdf", load_workbook(self.xlsx)["lot 2"]["E11"].value)

    def test_missing_manifest_is_an_error(self):
        (self.root / MANIFEST_NAME).unlink()
        with self.assertRaises(FileNotFoundError):
            fo.fill_result(self.xlsx, self.root)
        self.assertFalse((self.root / MANIFEST_NAME).exists())

    def test_dm_cell_prefers_dm_numbers(self):
        rec = {"dm": ["DM000466393,DM000466394"], "standards": ["CE/DM000466393"]}
        self.assertEqual(fo.dm_cell(rec), "DM000466393\nDM000466394")


if __name__ == "__main__":
    unittest.main()
