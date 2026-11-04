import sqlite3
import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import PatternFill

from src.evaluate import confidence as cf
from src.excel import verdicts as v
from src.history.db import SCHEMA
from src.manifest import MANIFEST_NAME, migrate


class VerdictTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.tender = root / "tender"
        self.tender.mkdir()
        conn = sqlite3.connect(self.tender / MANIFEST_NAME)
        migrate(conn)
        conn.execute("INSERT INTO document_refs (path, tender_id, lot_no, lot_title, participant, original_name, url, "
                     "sha256, status, recorded_at) VALUES ('p', 'T1', 2, 'Lot 2', 'A', 'a.pdf', 'u', NULL, 'ok', 'now')")
        conn.commit()
        conn.close()
        wb = Workbook()
        ws = wb.active
        ws.title = "lot 2"
        names = ["Alpha SRL", "Beta SRL", "Gamma SRL", "Delta SRL", "Epsilon SRL"]
        for i, name in enumerate(names):
            ws.cell(row=2, column=5 + i, value=f"Ofertant: {name}")
            ws.cell(row=1, column=5 + i, value="Tehnic    corespunde ").fill = PatternFill("solid", fgColor="FF00B0F0")
        ws.cell(row=1, column=9).fill = PatternFill("solid", fgColor="FF7030A0")   # цвет поставил человек
        ws.sheet_properties.tabColor = "FF00B050"
        self.xlsx = root / "result.xlsx"
        wb.save(self.xlsx)
        self.kb = sqlite3.connect(":memory:")
        self.kb.executescript(SCHEMA)
        for name, status, source, reason in (("Alpha SRL", cf.ALTERNATIVA, cf.AUTO, "technical"),
                                             ("Beta SRL", cf.CORESPUNDE, cf.HUMAN, None),
                                             ("Gamma SRL", cf.CORESPUNDE, cf.PENDING, None),   # вопрос открыт
                                             ("Delta SRL", cf.NU_CORESPUNDE, cf.AUTO, "price_130"),
                                             ("Epsilon SRL", cf.CORESPUNDE, cf.HUMAN, None)):
            self.kb.execute("INSERT INTO evaluations VALUES ('T1', 2, ?, NULL, ?, ?, 1, ?, '{}', 'now')",
                            (name, status, reason, source))

    def tearDown(self):
        self.kb.close()
        self.tmp.cleanup()

    def test_colors_texts_and_tab(self):
        stats = v.write_verdicts(self.xlsx, self.tender, self.kb)
        ws = load_workbook(stats["file"])["lot 2"]
        row1 = [(ws.cell(row=1, column=c).value, ws.cell(row=1, column=c).fill.fgColor.rgb) for c in range(5, 10)]
        self.assertEqual(row1[0], ("Alternativa", v.BLUE))
        self.assertEqual(row1[1], ("Tehnic    corespunde ", v.GREEN))
        self.assertEqual(row1[2], ("Clarificare", v.YELLOW))          # открытый вопрос - жёлтый
        self.assertEqual(row1[3], ("0.3", v.RED))                     # цена ≥ 130%
        self.assertEqual(row1[4][1], "FF7030A0")                      # цвет человека не тронут
        self.assertEqual(ws.cell(row=11, column=5).fill.fgColor.rgb, v.BLUE)
        self.assertEqual(ws.sheet_properties.tabColor.rgb, v.YELLOW)  # есть открытый вопрос
        self.assertTrue(stats["file"].endswith("result_evaluare.xlsx"))

    def test_tab_rules(self):
        self.assertEqual(v.lot_tab([cf.CORESPUNDE, cf.ALTERNATIVA]), v.GREEN)
        self.assertEqual(v.lot_tab([cf.ALTERNATIVA, cf.NU_CORESPUNDE]), v.BLUE)
        self.assertEqual(v.lot_tab([cf.NU_CORESPUNDE]), v.RED)
        self.assertIsNone(v.lot_tab([]))


if __name__ == "__main__":
    unittest.main()
