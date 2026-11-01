import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import PatternFill

from src.history import decode as d
from src.history.db import connect
from src.history.importer import import_workbook, tender_id_from


class DecodeTests(unittest.TestCase):
    def test_codebook(self):
        cases = [
            (("Tehnic corespunde", d.GREEN), ("accepted", None, None)),
            (("Tehnic corespunde (accceptat)", d.GREEN), ("accepted", None, None)),
            (("Alternativa acceptata", d.GREEN), ("alternative", None, None)),
            (("", d.BLUE), ("not_evaluated", None, None)),
            (("Tehnic    corespunde ", d.BLUE), ("not_evaluated", None, None)),
            (("Tehnic corespunde (Alternativa)", d.BLUE), ("alternative", None, None)),
            (("Alternativa ( testat mostre)", d.BLUE), ("samples", None, None)),
            (("Garantia Bancara insuficienta", d.RED), ("rejected", "guarantee_insufficient", None)),
            (("Garantia Bancara gresita", d.RED), ("rejected", "guarantee_wrong", None)),
            (("0.3", d.RED), ("rejected", "price_130", None)),
            (("G - canitati de corelat", d.RED), ("rejected", "annulled_g", None)),
            (("Tehnic corespunde ( nu este inregistrat)", d.RED), ("rejected", "amdm", None)),
            (("Retras ofertat", d.RED), ("rejected", "withdrawn", None)),
            (("Tehnic nu corespunde", d.RED), ("rejected", "technical", None)),
            (("Tehnic corespunde", d.ORANGE), ("improved_offer", None, None)),
        ]
        for (text, color), expected in cases:
            self.assertEqual(d.decode_offer(text, color), expected, text)

    def test_contradictions_are_questions(self):
        decision, reason, why = d.decode_offer("Tehnic corespunde", d.RED, ratio=1.05)
        self.assertEqual((decision, reason), ("rejected", None))
        self.assertIn("красный без причины", why)
        # но цена ≥ 130% объясняет красный: вопроса нет
        self.assertEqual(d.decode_offer("Tehnic corespunde", d.RED, ratio=1.31), ("rejected", "price_130", None))

    def test_tabs_and_suffixes(self):
        self.assertEqual(d.sheet_suffix("lot 45(A)"), (45, "A"))
        self.assertEqual(d.sheet_suffix("lot 9M"), (9, "M"))
        self.assertEqual(d.sheet_suffix("lot 208(Ad)"), (208, "AD"))
        self.assertEqual(d.sheet_suffix("Lot x"), (None, ""))
        self.assertEqual(d.lot_outcome("FF00B050"), "awarded")
        self.assertEqual(d.lot_outcome("00001f4d"), "not_held")
        self.assertEqual(d.lot_outcome("FF00B0F0", "M"), "samples")
        self.assertEqual(d.lot_outcome("FF00B0F0", ""), "working_group")

    def test_tender_id(self):
        self.assertEqual(tender_id_from(Path("21641880 CDM27 B — копия.xlsx")), "21641880")
        self.assertIsNone(tender_id_from(Path("result.xlsx")))


def fill(color):
    return PatternFill("solid", fgColor=color)


class ImportFixture(unittest.TestCase):
    """Рабочий Excel с одним листом лота и база знаний во временной папке (без тестов)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.xlsx = self.root / "21641880 CDM27 B.xlsx"
        self.conn = connect(self.root / "kb.sqlite")
        self.save(electeh_color=d.GREEN, electeh_text="Tehnic corespunde")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def save(self, electeh_color, electeh_text):
        wb = Workbook()
        wb.active.title = "Lot x"
        ws = wb.create_sheet("lot 29")
        ws["A1"], ws["B1"], ws["C9"], ws["D7"], ws["D8"] = 1300, "Lot nr. 29 Electrod", "Bucată", 290, 377000
        ws["B11"] = "Electrod lungime 70 mm"
        ws["E1"], ws["E2"] = electeh_text, "Ofertant: Electeh SRL"
        ws["E3"], ws["E4"] = "Producator: Fiab\nȚara: Italia\nModel: F4141", "DM000466393\nRg04-000083 13-04-2023"
        ws["E8"], ws["E10"], ws["E11"] = 447678, "Cistigator 2026", "Electrod 70 mm // conform brosura"
        ws["E1"].fill = fill(electeh_color)
        ws["F2"], ws["F3"], ws["F11"] = None, "clarificări (runda 1)", "// \"F4141\", p[4], brosura.pdf"
        ws["F11"].fill = fill(d.GREEN)
        ws["G1"], ws["G2"], ws["G8"] = "Tehnic    corespunde ", "Ofertant: DITA ESTFARM", 500000
        ws["G1"].fill = fill(d.BLUE)
        ws["H1"], ws["H11"] = "Note", "Sa solicitat 70 mm"
        ws.sheet_properties.tabColor = d.GREEN
        wb.save(self.xlsx)


class ImportTests(ImportFixture):
    def test_import_reads_lot_and_offers(self):
        r = import_workbook(self.conn, self.xlsx)
        self.assertEqual((r["tender_id"], r["lots"], r["offers"]), ("21641880", 1, 2))
        lot = self.conn.execute("SELECT quantity, unit, base_value, outcome, note FROM lots").fetchone()
        self.assertEqual(lot, (1300.0, "Bucată", 377000.0, "awarded", "Sa solicitat 70 mm"))
        row = self.conn.execute(
            "SELECT producer, model, dm, unit_price, round(price_ratio, 4), previous_winner, spec, spec_notes, "
            "decision FROM offers WHERE participant = 'Electeh SRL'").fetchone()
        self.assertEqual(row, ("Fiab", "F4141", "DM000466393", 447678 / 1300, round(447678 / 377000, 4), 1,
                               "Electrod 70 mm", "// conform brosura", "accepted"))
        self.assertEqual(self.conn.execute("SELECT decision FROM offers WHERE participant LIKE 'DITA%'").fetchone(),
                         ("not_evaluated",))
        self.assertEqual(self.conn.execute("SELECT participant, label, color FROM clarifications").fetchone(),
                         ("Electeh SRL", "clarificări (runda 1)", d.GREEN))

    def test_unchanged_file_is_skipped(self):
        first = import_workbook(self.conn, self.xlsx)
        second = import_workbook(self.conn, self.xlsx)
        self.assertTrue(second["skipped"])
        self.assertEqual(second["snapshot_id"], first["snapshot_id"])

    def test_appeal_creates_events(self):
        import_workbook(self.conn, self.xlsx)
        self.save(electeh_color=d.RED, electeh_text="Garantia Bancara insuficienta")
        r = import_workbook(self.conn, self.xlsx)
        changes = {(e[1], e[2]): (e[3], e[4]) for e in r["events"]}
        self.assertEqual(changes[("Electeh SRL", "decision")], ("accepted", "rejected"))
        self.assertEqual(changes[("Electeh SRL", "reason")], (None, "guarantee_insufficient"))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0], 2)


class ReviewTests(ImportFixture):
    """Вопрос (красный «Tehnic corespunde») -> ответ человека -> повторный импорт."""

    def setUp(self):
        super().setUp()
        self.save(electeh_color=d.RED, electeh_text="Tehnic corespunde")
        import_workbook(self.conn, self.xlsx)
        from src.history import review
        self.review = review
        self.path = self.root / "review.xlsx"

    def answer_electeh(self, decision, comment=None):
        from openpyxl import load_workbook
        wb = load_workbook(self.path)
        ws = wb[self.review.SHEET]
        for row in ws.iter_rows(min_row=2):
            if row[3].value == "Electeh SRL":
                row[9].value, row[11].value = decision, comment
        wb.save(self.path)
        return self.review.apply_review(self.conn, self.path)

    def current(self):
        return self.conn.execute("SELECT final_decision, decision_source, open_question FROM current_offers "
                                 "WHERE participant = 'Electeh SRL'").fetchone()

    def test_question_answer_cycle(self):
        self.assertEqual(self.review.export_review(self.conn, "21641880", self.path), 1)
        self.assertEqual(self.answer_electeh("excluded", "ошибка разметки"), (1, []))
        self.assertEqual(self.current(), ("excluded", "human", None))
        self.assertEqual(self.review.export_review(self.conn, "21641880", self.path), 0)  # спрошено один раз

    def test_answer_survives_reimport_of_unchanged_cell(self):
        self.review.export_review(self.conn, "21641880", self.path)
        self.answer_electeh("accepted")
        # Excel правился, но не ячейка строки 1 Electeh - ответ остаётся в силе
        from openpyxl import load_workbook
        wb = load_workbook(self.xlsx)
        wb["lot 29"]["H11"] = "заметка после апелляции"
        wb.save(self.xlsx)
        self.assertFalse(import_workbook(self.conn, self.xlsx)["skipped"])
        self.assertEqual(self.current(), ("accepted", "human", None))

    def test_changed_cell_makes_answer_stale(self):
        self.review.export_review(self.conn, "21641880", self.path)
        self.answer_electeh("accepted")
        self.save(electeh_color=d.RED, electeh_text="Tehnic corespunde (revizuit)")  # человек поменял ячейку
        import_workbook(self.conn, self.xlsx)
        decision, source, question = self.current()
        self.assertEqual(source, "codebook")
        self.assertIsNotNone(question)

    def test_unknown_decision_reported(self):
        self.review.export_review(self.conn, "21641880", self.path)
        saved, errors = self.answer_electeh("da")
        self.assertEqual(saved, 0)
        self.assertIn("неизвестное решение", errors[0])


if __name__ == "__main__":
    unittest.main()
