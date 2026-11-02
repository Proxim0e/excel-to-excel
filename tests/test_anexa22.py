import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.anexa22 import parse as p
from src.anexa22.evaluate import participant_key, same_text, spec_recall
from src.anexa22.gold import parse_row3


def cell(x0, x1, text, y=0):
    return ((x0, y, x1, y + 10), text)


# Шапка таблицы как у Electeh/DITA: заголовки, продолжение заголовка ниже, строка номеров колонок
HEADER = [cell(0, 50, "Cod CPV"), cell(50, 80, "Nr. Lot"), cell(80, 200, "Denumirea modelului bunurilor"),
          cell(200, 260, "Ţara de origine"), cell(260, 340, "Produ- cătorul"),
          cell(340, 500, "Specificarea tehnică"), cell(500, 660, "Specificația tehnică"),
          cell(660, 760, "Standarde de")]
HEADER_TAIL = [(None, ""), (None, ""), (None, ""), (None, ""), (None, ""),
               cell(340, 500, "deplină solicitată"), cell(500, 660, "propusă de ofertant"),
               cell(660, 760, "referinţă")]
NUMBERING = [cell(0, 50, "1"), cell(50, 80, "2"), cell(80, 200, "3"), cell(200, 260, "4"),
             cell(260, 340, "5"), cell(340, 500, "6"), cell(500, 660, "7"), cell(660, 760, "8")]


def data_row(cpv, lot, model, country, producer, req, offered, std):
    # Значения слегка сдвинуты относительно колонок заголовка - как в реальных таблицах
    return [cell(2, 48, cpv), cell(52, 78, lot), cell(85, 195, model), cell(205, 255, country),
            cell(265, 335, producer), cell(345, 495, req), cell(505, 655, offered), cell(665, 755, std)]


def continuation(offered):
    return [(None, ""), (None, ""), (None, ""), (None, ""), (None, ""), (None, ""),
            cell(505, 655, offered), (None, "")]


class HeaderTests(unittest.TestCase):
    def test_header_fields(self):
        cases = {"Produ- cătorul": "producer", "Producăto rul": "producer", "Ţara de origine": "country",
                 "Ţara de origine Producătorul": p.COUNTRY_PRODUCER, "Denumirea modelului bunurilor": "model",
                 "Modelul articolului": "model", "Specificarea tehnică deplină solicitată": "spec_required",
                 "Specificaţia tehnică propusă": "spec_offered", "Standarde de referinţă": "standards",
                 "Cod CPV": "cpv", "Co d CP V": "cpv", "Nr. Lot": "lot", "Denumire Lot": "lot_name",
                 "Denumirea poziției": "item_name", "Nr. d/o": "item_no"}
        for text, expected in cases.items():
            self.assertEqual(p.header_field(text), expected, text)

    def test_header_with_tail_and_numbering(self):
        rows = [[cell(0, 760, "Numărul licitaţiei: 21641880")], HEADER, HEADER_TAIL, NUMBERING,
                data_row("33100000-1", "1", "312300", "Turcia", "R-VENT", "cerinta", "oferta", "DM000558963")]
        self.assertEqual(p.find_header(rows), (1, 4))
        columns = p.build_columns(rows, 1, 4)
        fields = {c.field: c.header for c in columns}
        self.assertIn("propusă", fields["spec_offered"])
        self.assertIn("solicitată", fields["spec_required"])


class RowTests(unittest.TestCase):
    def setUp(self):
        rows = [HEADER, HEADER_TAIL, NUMBERING]
        self.columns = p.build_columns(rows, 0, 3)

    def test_values_by_x_and_continuation(self):
        rows = [data_row("33100000-1", "1", "312300", "Turcia", "R-VENT MEDIKAL", "Conector", "Conector Mount",
                         "CE/DM0005589 63"),
                continuation("(Extensie respiratorie)"),
                data_row("33100000-1", "9", "31305020", "Turcia", "R-VENT MEDIKAL", "Tub", "Tub gofrat", "")]
        records = p.parse_rows(rows, self.columns, 760)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["model"], "312300")
        self.assertEqual(records[0]["spec_offered"], "Conector Mount (Extensie respiratorie)")
        offers = p._to_offers(records, [], "Electeh", "sha", 1, {})
        self.assertEqual([o.lot for o in offers], [1, 9])
        self.assertEqual(offers[0].dm, ["DM000558963"])  # номер разорван переносом

    def test_section_row_sets_lot(self):
        columns = p.build_columns([[cell(0, 200, "Denumirea bunurilor"), cell(200, 300, "Denumirea modelului"),
                                    cell(300, 400, "Ţara de origine"), cell(400, 500, "Produ- cătorul")]], 0, 1)
        rows = [[cell(0, 500, "Lotul nr. 89 - Lot nr. 89 Hârtie milimetrică")],
                [cell(0, 200, "Hârtie milimetrică 80mm"), cell(200, 300, "ECG 80x20"),
                 cell(300, 400, "Ucraina"), cell(400, 500, "OOO SOLAR")]]
        offers = p._to_offers(p.parse_rows(rows, columns, 500), [], "AELO", "sha", 1, {})
        self.assertEqual([(o.lot, o.get("producer")) for o in offers], [(89, "OOO SOLAR")])

    def test_page_break_continuation_joins_previous_offer(self):
        first = p._to_offers(p.parse_rows([data_row("33100000-1", "1", "M1", "Turcia", "X", "a", "b", "")],
                                          self.columns, 760), [], "P", "sha", 1, {})
        rest = p._to_offers(p.parse_rows([continuation("sfarsit de text")], self.columns, 760),
                            first, "P", "sha", 2, {})
        self.assertEqual(rest, [])
        self.assertEqual(first[0].get("spec_offered"), "b sfarsit de text")

    def test_merged_country_producer_column(self):
        columns = p.build_columns([[cell(0, 100, "Nr. Lot"), cell(100, 200, "Ţara de origine Producătorul"),
                                    cell(200, 300, "Modelul")]], 0, 1)
        rows = [[cell(0, 100, "29"), cell(100, 200, "Italia Fiab"), cell(200, 300, "F4141")]]
        offer = p._to_offers(p.parse_rows(rows, columns, 300), [], "ICS", "sha", 1, {})[0]
        self.assertEqual((offer.get("country"), offer.get("producer")), ("Italia", "Fiab"))


class LotTests(unittest.TestCase):
    def test_lot_sources(self):
        self.assertEqual(p.lot_number({"lot": "56"}, {}), 56)
        self.assertEqual(p.lot_number({"item_name": "Electrod ECG ( 006951 )"}, {"006951": 32}), 32)
        self.assertEqual(p.lot_number({"item_name": "Lotul nr. 94 - Hartie"}, {}), 94)
        self.assertIsNone(p.lot_number({"item_name": "Hartie"}, {}))

    def test_split_country_producer(self):
        self.assertEqual(p.split_country_producer("Italia Fiab"), ("Italia", "Fiab"))
        self.assertEqual(p.split_country_producer("CARESTREAM HEALTH SUA"), ("SUA", "CARESTREAM HEALTH"))
        self.assertEqual(p.split_country_producer("Fiab"), ("", "Fiab"))


class ResolveLotsTests(unittest.TestCase):
    """Лот для строк Anexa 22 без номера лота и CPV (тендер 21643233: MED-M, Triumf-Motiv, Romedcom)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("CREATE TABLE document_refs (lot_no INTEGER, lot_title TEXT, participant TEXT)")
        self.conn.executemany("INSERT INTO document_refs VALUES (?, ?, ?)", [
            (390, "Lot nr. 390 Electrod pasiv", "MED-M"),
            (391, "Lot nr. 391 Electrod pasiv din silicon reutilizabil, adult ARC 400 Bowa", "Electeh"),
            (342, "Lot nr. 342 Centre disc Dialog+ (V5), B.Braun", "ACME"),
            (343, "Lot nr. 343 Centre disc Dialog+ (V5), B.Braun", "ACME"),
            (398, "Lot nr. 398 Element filtrare IMT-POC 8600", "Romedcom"),
        ])
        # Папка лота участника пустая (документы загружены в другой лот), название обрезано
        (self.root / "Lot nr. 523 Lama pentru dermatom sterile..tor)" / "MED-M").mkdir(parents=True)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def offer(self, participant, name, lot=None):
        return p.Offer(participant, "sha", 1, lot, {"item_name": name, "model": "X"})

    def resolve(self, *offers):
        p.resolve_lots(list(offers), self.conn, self.root)
        return [o.lot for o in offers]

    def test_tender_lot_wins_over_participant_numbering(self):
        o = self.offer("Romedcom", "Lotul nr. 130 - Lot nr. 398 Element filtrare IMT-POC 8600", lot=130)
        self.assertEqual(self.resolve(o), [398])

    def test_exact_name_in_tender_beats_prefix_in_participant_lots(self):
        # у MED-M в манифесте только лот 390, но название точно совпадает с лотом 391
        o = self.offer("MED-M", "Electrod pasiv din silicon reutilizabil, adult ARC 400 Bowa")
        plain = self.offer("MED-M", "Electrod pasiv")
        self.assertEqual(self.resolve(o, plain), [391, 390])

    def test_same_titles_are_taken_in_order(self):
        a = self.offer("ACME", "Centre disc Dialog+ (V5), B.Braun")
        b = self.offer("ACME", "Centre disc Dialog+ (V5), B.Braun")
        self.assertEqual(self.resolve(a, b), [342, 343])

    def test_truncated_folder_title(self):
        o = self.offer("MED-M", "Lama pentru dermatom sterile ( Watson- autor)")
        self.assertEqual(self.resolve(o), [523])

    def test_unknown_name_stays_without_lot(self):
        self.assertEqual(self.resolve(self.offer("MED-M", "Hartie termica 57 mm")), [None])

    def test_junk_rows(self):
        junk = ["Bunuri/servicii", "TOTAL", "Lotul 269", "Total lot 269", "Total lot 392 Lotul 394"]
        for name in junk:
            self.assertTrue(p._is_junk(p.Offer("L", "sha", 1, None, {"item_name": name})), name)
        # хвост производителя предыдущей строки в итоге - всё равно мусор
        self.assertTrue(p._is_junk(p.Offer("L", "sha", 1, None, {"item_name": "Total lot 353", "producer": "Ltd"})))
        # оферта, чьё название похоже на заголовок, но есть модель - не мусор
        self.assertFalse(p._is_junk(p.Offer("L", "sha", 1, None, {"item_name": "Lot 5", "model": "F4141"})))

    def test_name_only_row_on_next_page_is_continuation(self):
        columns = p.build_columns([[cell(0, 200, "Denumirea bunurilor"), cell(200, 300, "Modelul"),
                                    cell(300, 400, "Producătorul")]], 0, 1)
        first = p._to_offers(p.parse_rows([[cell(0, 200, "Element filtrare IMT-POC 8600 med"),
                                            cell(200, 300, "IPE160SMA"), cell(300, 400, "INMATEC")]],
                                          columns, 400), [], "Romedcom", "sha", 1, {})
        tail = p._to_offers(p.parse_rows([[cell(0, 200, "INMATEC GaseTechnologie GmbH")]], columns, 400),
                            first, "Romedcom", "sha", 2, {})
        self.assertEqual(tail, [])
        self.assertTrue(first[0].get("item_name").endswith("INMATEC GaseTechnologie GmbH"))
        self.assertEqual(first[0].pages, {1, 2})


class EvaluateTests(unittest.TestCase):
    def test_participant_key(self):
        self.assertEqual(participant_key('"ALMICOM-TECH" S.R.L.'), participant_key("ALMICOM-TECH SRL"))
        self.assertEqual(participant_key("ÎM „Dutchmed-M” SRL"), participant_key("IM Dutchmed-M SRL"))

    def test_same_text_and_recall(self):
        self.assertTrue(same_text("Masca (Codul produsului:T20163)", "Masca (Codul produsului: T 20163)"))
        self.assertFalse(same_text("10mm", "15mm"))
        self.assertEqual(spec_recall('"QinPot_Eng_2020.pdf" Recipient', '"QinPot Eng 2020.pdf" Recipient'), 1.0)

    def test_gold_row3(self):
        self.assertEqual(parse_row3("Producator: FIAB SPA\nȚara: Italia\nModel:  F4141"),
                         ("FIAB SPA", "Italia", "F4141"))


if __name__ == "__main__":
    unittest.main()
