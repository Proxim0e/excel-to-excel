"""Старые рабочие Excel (2024-2025): участник с колонки B/C, решение цветом ячеек; год тендера с achizitii.md."""
import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import PatternFill

from src.history.db import connect
from src.history.importer import import_workbook
from src.history.tender_year import fallback_year, parse_published, tender_year
from src.ui.card_data import agent_candidates, agent_label, winner_candidates


def fill(rgb):
    return PatternFill("solid", fgColor=rgb)


class LegacyImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        wb = Workbook()
        ws = wb.active
        ws.title = "lot 1"
        ws["B1"] = "Lot nr. 1 Electrod ECG"
        ws["C2"], ws["D2"] = "Ofertant: Electeh SRL", "Ofertant: Labromed"
        ws["C3"], ws["D3"] = "Producator: Intco\nȚara de Origine: China\nModel: SF01", "Producator: 3M"
        ws["C4"], ws["D4"] = "DM000569596", "DM000111"
        ws["A5"], ws["B5"], ws["C5"], ws["D5"] = "Suma estimata", 1000, 900, 1100
        ws["A6"], ws["B6"], ws["C6"], ws["D6"] = "pret per pcs", 1, 0.9, 1.1
        ws["B8"], ws["C8"], ws["D8"] = "1.diametru 38-47 mm", "diametru 40 mm", "diametru 50 mm"
        ws["C8"].fill = fill("FF00B050")
        ws["D8"].fill = fill("FFFF0000")
        self.path = Path(self.tmp.name) / "21305159 CDM25S.xlsx"
        wb.save(self.path)
        self.kb = connect(Path(self.tmp.name) / "kb.sqlite")

    def tearDown(self):
        self.kb.close()
        self.tmp.cleanup()

    def test_offers_and_decisions_by_color(self):
        r = import_workbook(self.kb, self.path)
        self.assertEqual(r["offers"], 2)
        rows = self.kb.execute("SELECT participant, producer, model, dm, decision, offer_value FROM offers "
                               "ORDER BY col").fetchall()
        self.assertEqual(rows[0], ("Electeh SRL", "Intco", "SF01", "DM000569596", "accepted", 900.0))
        self.assertEqual(rows[1][4], "rejected")
        self.assertIn("diametru", self.kb.execute("SELECT requirement FROM lots").fetchone()[0])

    def test_agents_and_winners_with_year_from_site_cache(self):
        import_workbook(self.kb, self.path)
        self.kb.execute("CREATE TABLE IF NOT EXISTS tenders (tender_id TEXT PRIMARY KEY, published TEXT, year INT, "
                        "source TEXT, checked_at TEXT NOT NULL)")
        self.kb.executemany("INSERT INTO tenders VALUES (?, ?, ?, 'site', 'x')",
                            [("21305159", "2025-03-01", 2025), ("21641880", "2026-08-01", 2026)])
        card = {"model_key": "DM:DM000569596", "dm": "DM000569596", "model": "SF01"}
        agents = agent_candidates(self.kb, card, "21641880", "Electeh SRL")
        self.assertEqual([agent_label(a) for a in agents],
                         ["Electeh SRL - этот тендер", "Electeh SRL - прошлый год (2025) (21305159, лот 1) - победитель"])
        self.assertEqual(winner_candidates(agents), ["Electeh SRL (2025, 21305159)"])
        self.assertEqual(tender_year(self.kb, "21305159", online=False), 2025)

    def test_prices_by_year(self):
        from src.ui.card_data import fmt_price, price_history, price_summary
        self.path.unlink()
        wb = Workbook()
        ws = wb.active
        ws.title = "lot 2"
        ws["A1"] = "Lot nr. 2 Electrod"
        ws["B2"] = "Ofertant: Electeh SRL"
        ws["B4"] = "DM000569596"
        ws["A5"], ws["A6"], ws["A7"] = 54000, 135, 400          # без подписей: сумма, цена, количество
        ws["B5"], ws["B6"] = 48000, 0.89
        ws["B8"] = "ok"
        ws["B8"].fill = fill("FF00B050")
        wb.save(self.path)
        import_workbook(self.kb, self.path)
        self.assertEqual(self.kb.execute("SELECT unit_price, offer_value FROM offers").fetchone(), (120.0, 48000.0))
        rows = price_history(self.kb, {"model_key": "DM:DM000569596"}, "21641880", "Electeh SRL",
                             {"unit_price": 110.0, "value": 231000.0, "quantity": 2100.0, "base_price": None,
                              "ratio": 0.95})
        self.assertEqual([(r["period"], r["unit_price"], r["won"]) for r in rows][0], ("этот тендер", 110.0, False))
        self.assertEqual(rows[1]["unit_price"], 120.0)
        self.assertTrue(rows[1]["won"])
        self.assertIn("победитель 120", price_summary(rows)[0])
        self.assertEqual(fmt_price(1234.5), "1 234.50")
        from src.ui.card_data import fmt_ratio
        self.assertEqual(fmt_ratio(117855 / (22.4 * 4050)), "129.91%")   # 21641880 лот 59: ниже порога 130%
        self.assertEqual(fmt_ratio(1.29999), "129.99%")
        self.assertEqual(fmt_ratio(1.3), "130.00%")

    def test_year_parsing(self):
        html = "<span>Perioada de actualizare de la 04.08.2025 16:05 până la 08.09.2025</span>"
        self.assertEqual(parse_published(html), "2025-08-04")
        self.assertEqual(fallback_year(r"\\srv\+LP ADM 2026\x.xlsx", None), 2025)


if __name__ == "__main__":
    unittest.main()
