import sqlite3
import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import PatternFill

from src.history import cards as cd
from src.history import decode as d
from src.history.db import connect
from src.history.importer import import_workbook
from src.history.review import save_answer
from src.manifest import migrate


class KeyTests(unittest.TestCase):
    def test_model_key(self):
        self.assertEqual(cd.model_key("Fiab", "F4141", "DM000466393"), "DM:DM000466393")
        self.assertEqual(cd.model_key("X", "kit", "DM000000002,DM000000001"), "DM:DM000000001+DM000000002")
        self.assertEqual(cd.model_key("FIAB SPA", "Electrod (Codul produsului: F 4141)", None), "M:fiab|f4141")
        self.assertIsNone(cd.model_key(None, None, None))


class CardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        # рабочий Excel: Electeh заполнен (DM), DITA и ICS - нет (не оценивались), Labromed - ошибка разметки
        wb = Workbook()
        ws = wb.active
        ws.title = "lot 29"
        ws["A1"], ws["D8"] = 1300, 377000
        people = [("Electeh SRL", d.GREEN, "Tehnic corespunde", "Producator: Fiab\nȚara: Italia\nModel: F4141",
                   "DM000466393", 444600),
                  ("DITA ESTFARM S.R.L", d.BLUE, "", None, None, 445081),
                  ("ICS Eyecon Medical SRL", d.BLUE, "", None, None, 465400),
                  ("Labromed Laborator SRL", d.RED, "Tehnic corespunde", "Producator: Fiab\nȚara: Italia\nModel: F4141",
                   None, 400000)]
        for i, (who, color, text, row3, dm, value) in enumerate(people):
            col = 5 + i
            ws.cell(1, col, text).fill = PatternFill("solid", fgColor=color)
            ws.cell(2, col, f"Ofertant: {who}")
            ws.cell(3, col, row3)
            ws.cell(4, col, dm)
            ws.cell(8, col, value)
        xlsx = root / "21641880 B.xlsx"
        wb.save(xlsx)
        self.conn = connect(root / "kb.sqlite")
        import_workbook(self.conn, xlsx)
        save_answer(self.conn, "21641880", 29, "Labromed Laborator SRL", "Tehnic corespunde", d.RED, "excluded")
        self.conn.commit()

        # манифест тендера: модели DITA и ICS из разбора Anexa 22
        tender = root / "downloads" / "21641880"
        tender.mkdir(parents=True)
        m = sqlite3.connect(tender / "manifest.sqlite")
        migrate(m)
        m.executemany("INSERT INTO anexa22_offers (sha256, participant, lot_no, producer, model, dm, parsed_at) "
                      "VALUES ('x', ?, 29, ?, ?, ?, 'now')",
                      [("DITA ESTFARM S.R.L", "FIAB SPA", "Electrod (Codul produsului:F 4141)", "DM000466393"),
                       ("ICS Eyecon Medical SRL", "Fiab", "F4141", "")])
        m.commit()
        m.close()
        # локальная копия реестра AMDM
        reg = root / "registry.sqlite"
        r = sqlite3.connect(reg)
        r.execute('CREATE TABLE amdm_record (dm TEXT, producer TEXT, model TEXT, catalog TEXT, name TEXT, '
                  '"order" TEXT, order_date TEXT)')
        r.execute("INSERT INTO amdm_record VALUES ('DM000466393', 'FIAB SPA', 'REUSABLE PENCIL', 'F4141', "
                  "'INSTRUMENT', 'Rg04-000083', '2023-04-13')")
        r.commit()
        r.close()
        self.cards = cd.build_cards(self.conn, registry_db=reg, downloads=root / "downloads")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_one_card_for_all_participants(self):
        self.assertEqual(list(self.cards), ["DM:DM000466393"])
        card = self.cards["DM:DM000466393"]
        self.assertEqual(sorted(o["participant"] for o in card.offers),
                         ["DITA ESTFARM S.R.L", "Electeh SRL", "ICS Eyecon Medical SRL", "Labromed Laborator SRL"])
        self.assertEqual({o["participant"]: o["model_source"] for o in card.offers}["ICS Eyecon Medical SRL"], "anexa22")

    def test_summary_excludes_marking_errors(self):
        s = self.cards["DM:DM000466393"].summary()
        self.assertEqual((s["offers"], s["accepted"], s["rejected"], s["not_evaluated"]), (4, 1, 0, 2))
        self.assertEqual((s["price_min"], s["price_max"]), (444600 / 1300, 465400 / 1300))  # 400000 - excluded
        self.assertIn("Rg04-000083", s["registry"])

    def test_find_and_format(self):
        for query in ("F4141", "f 4141", "DM000466393", "fiab"):
            self.assertEqual([c.key for c in cd.find_cards(self.cards, query)], ["DM:DM000466393"], query)
        text = cd.format_card(self.cards["DM:DM000466393"])
        self.assertIn("ICS Eyecon Medical SRL", text)
        self.assertIn("[A22]", text)

    def test_catalog_export(self):
        path = Path(self.tmp.name) / "catalog.xlsx"
        self.assertEqual(cd.export_catalog(self.cards, path), 1)
        from openpyxl import load_workbook
        wb = load_workbook(path, read_only=True)
        try:
            self.assertEqual(len(list(wb["Оферты"].iter_rows(min_row=2))), 4)
        finally:
            wb.close()


if __name__ == "__main__":
    unittest.main()
