"""Ручная проверка лотов: решение -> строки 1 и 11 листа, цвет и суффикс вкладки, копия _verificat, база знаний."""
import json
import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook, load_workbook

from src.evaluate import confidence as cf
from src.excel import review as rv
from src.excel.verdicts import BLUE, GREEN, RED, YELLOW
from src.history.db import connect


def make_result(path: Path) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "lot 3"
    ws["A1"], ws["B1"] = 10, "Lot nr. 3 Set perfuzie"
    ws["B11"], ws["D8"] = "Set perfuzie, filtru 15 microni", 100
    for col, (name, value) in ((5, ("Alfa SRL", 90)), (7, ("Beta SA", 140))):
        ws.cell(row=2, column=col, value=f"Ofertant: {name}")
        ws.cell(row=3, column=col, value="Producer X, model AB-123")
        ws.cell(row=4, column=col, value="DM000123456")
        ws.cell(row=8, column=col, value=value)
        ws.cell(row=11, column=col, value="Set perfuzie AB-123")
    ws["F3"], ws["F11"] = "clarificări (runda 1)", "TDS: filtru 15 µm - confirmat"   # уточнения Alfa
    wb.create_sheet("lot 4")
    wb["lot 4"]["B1"] = "Lot nr. 4 fără oferte"
    wb.save(path)
    return path


class ReviewBookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.result = make_result(Path(self.tmp.name) / "result_x.xlsx")
        self.kb = connect(Path(self.tmp.name) / "kb.sqlite")

    def tearDown(self):
        self.kb.close()
        self.tmp.cleanup()

    def test_reads_lots_offers_and_clarifications(self):
        book = rv.ReviewBook(self.result)
        self.assertEqual([lot.lot_no for lot in book.lots], [3, 4])
        lot = book.lot(3)
        self.assertEqual([o.name for o in lot.offers], ["Alfa SRL", "Beta SA"])
        self.assertAlmostEqual(lot.offers[1].ratio, 1.4)
        self.assertEqual(lot.offers[0].clar[0]["note"], "TDS: filtru 15 µm - confirmat")
        self.assertEqual(rv.target_path(self.result).name, "result_x_verificat.xlsx")

    def test_decide_writes_copy_and_keeps_original(self):
        book = rv.ReviewBook(self.result)
        lot = book.lot(3)
        alfa, beta = lot.offers
        book.decide(lot, alfa, rv.BY_KEY["corespunde"], "filtru 15 µm confirmat in TDS")
        book.decide(lot, beta, rv.BY_KEY["price_130"])
        alfa.system = {"status": cf.CORESPUNDE, "source": cf.HUMAN}
        beta.system = {"status": cf.NU_CORESPUNDE, "source": cf.HUMAN}
        self.assertEqual(book.set_tab(lot, suffix="M"), GREEN)
        book.set_tab(book.lot(4))
        path = book.save()
        self.assertEqual(path.name, "result_x_verificat.xlsx")

        ws = load_workbook(path)["lot 3 (M)"]
        self.assertEqual(ws["E1"].value, "Tehnic corespunde")
        self.assertEqual(ws["E1"].fill.fgColor.rgb, GREEN)
        self.assertEqual(ws["E11"].value, "Set perfuzie AB-123\n// filtru 15 µm confirmat in TDS")
        self.assertEqual(ws["G1"].value, "0.3")
        self.assertEqual(ws["G11"].fill.fgColor.rgb, RED)
        self.assertEqual(ws.sheet_properties.tabColor.rgb, GREEN)
        self.assertEqual(load_workbook(path)["lot 4"].sheet_properties.tabColor.rgb, rv.NAVY)
        self.assertIsNone(load_workbook(self.result)["lot 3"]["E1"].value)     # оригинал не тронут

        again = rv.ReviewBook(self.result)                                     # продолжение - из копии
        self.assertEqual(again.source, again.target)
        self.assertEqual(again.lot(3).suffix, "M")
        again.decide(again.lot(3), again.lot(3).offers[0], rv.BY_KEY["corespunde"], "verificat din nou")
        self.assertEqual(again.lot(3).offers[0].spec, "Set perfuzie AB-123\n// verificat din nou")
        again.save()
        self.assertTrue(path.with_suffix(".bak.xlsx").is_file())

    def test_record_human_decision_closes_question(self):
        book = rv.ReviewBook(self.result)
        lot = book.lot(3)
        self.kb.execute("INSERT INTO evaluations (tender_id, lot_no, participant, status, source, findings, decided_at) "
                        "VALUES ('T1', 3, 'ALFA SRL', 'clarificare', 'pending', ?, '2026-11-05')",
                        (json.dumps({"reasons": ["≠ filtru"]}),))
        self.kb.execute("INSERT INTO questions (kind, state, tender_id, lot_no, participant, payload, created_at) "
                        "VALUES ('offer_status', 'open', 'T1', 3, 'ALFA SRL', '{}', '2026-11-05')")
        self.kb.commit()
        rv.attach_system(book.lots, self.kb, "T1")
        alfa = lot.offers[0]
        self.assertEqual(alfa.system["participant"], "ALFA SRL")
        self.assertFalse(lot.reviewed)
        rv.record(self.kb, "T1", lot, alfa, rv.BY_KEY["alternativa"], "dimensiuni diferite")
        status, source, findings = self.kb.execute(
            "SELECT status, source, findings FROM evaluations WHERE lot_no = 3 AND participant = 'ALFA SRL'").fetchone()
        self.assertEqual((status, source), (cf.ALTERNATIVA, cf.HUMAN))
        self.assertEqual(json.loads(findings)["comment"], "dimensiuni diferite")
        self.assertEqual(self.kb.execute("SELECT state FROM questions").fetchone()[0], "answered")

    def test_choices_and_tab_color(self):
        self.assertEqual(rv.choice_for(cf.NU_CORESPUNDE, "amdm").key, "amdm")
        self.assertEqual(rv.choice_for(cf.NU_CORESPUNDE, "неизвестная").key, "technical")
        self.assertEqual(rv.choice_for(cf.ALTERNATIVA, "samples").key, "mostre")
        book = rv.ReviewBook(self.result)
        lot = book.lot(3)
        lot.offers[0].system = {"status": cf.CORESPUNDE, "source": cf.PENDING}
        lot.offers[1].system = {"status": cf.ALTERNATIVA, "source": cf.AUTO}
        self.assertEqual(book.set_tab(lot), YELLOW)          # открытый вопрос - жёлтый
        lot.offers[0].system["source"] = cf.HUMAN
        self.assertEqual(book.set_tab(lot), GREEN)
        lot.offers[0].system = {"status": cf.NU_CORESPUNDE, "source": cf.HUMAN}
        self.assertEqual(book.set_tab(lot), BLUE)


if __name__ == "__main__":
    unittest.main()
