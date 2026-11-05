"""
Тесты проверки AMDM. Примеры строк взяты из реальных книг оценки (CDM27_B, CPAS27-P1).
Запуск: python -m unittest discover -s tests -t .   (или python -m pytest)
"""
import io
import tempfile
import unittest
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

from openpyxl import Workbook

from src.amdm.cache import RegistryCache
from src.amdm.fetch import CsvImporter, header_field
from src.amdm.matcher import check_offer, model_matches, overall_status, producer_matches
from src.amdm.models import OK, CHECK, FAIL, NO_DM, IN_PROGRESS, OfferRegistration, RegistryRecord
from src.amdm.parse import normalize_dm, parse_row3, parse_row4
from src.amdm.report import SHEET, write_report
from src.amdm.workbook import load_workbook_tolerant, read_offers


class ParseRow4Test(unittest.TestCase):

    def test_dm_order_date(self):
        p = parse_row4("DM000365870 Rg04-000183 05-08-2022/ pentru DM")
        self.assertEqual(p["dm_numbers"], ["DM000365870"])
        self.assertEqual(p["order"], "Rg04-000183")
        self.assertEqual(p["order_date"], "2022-08-05")
        self.assertIn("pentru DM", p["note"])

    def test_line_break_and_several_numbers(self):
        p = parse_row4("DM000377731\nRg04-000243 17-10-2022")
        self.assertEqual((p["dm_numbers"], p["order_date"]), (["DM000377731"], "2022-10-17"))
        self.assertEqual(parse_row4("DM000123456 DM000654321")["dm_numbers"], ["DM000123456", "DM000654321"])

    def test_quality_documents_only(self):
        self.assertEqual(parse_row4("CE, ISO 13485")["quality_docs"], ["CE", "ISO 13485"])
        p = parse_row4("CE/ISO ")
        self.assertEqual((p["dm_numbers"], p["quality_docs"]), ([], ["CE", "ISO"]))

    def test_empty(self):
        for v in (None, "None", ""):
            self.assertEqual(parse_row4(v)["dm_numbers"], [])

    def test_normalize_dm(self):
        self.assertEqual(normalize_dm("dm 655298"), "DM000655298")
        self.assertEqual(normalize_dm("CE/DM000232508"), "DM000232508")
        self.assertIsNone(normalize_dm("ISO 9001"))


class ParseRow3Test(unittest.TestCase):

    def test_wrapped_values_are_joined(self):
        p = parse_row3("Producator: BSS MEDICAL\nSUPPLY CO.,\nLIMITED\nȚara: China\n"
                       "Model:  Electrod\nECG 8 ore\n(Codul\nprodusului:B3644)")
        self.assertEqual(p["producer"], "BSS MEDICAL SUPPLY CO., LIMITED")
        self.assertEqual(p["country"], "China")
        self.assertEqual(p["model"], "Electrod ECG 8 ore (Codul produsului:B3644)")

    def test_template_and_empty(self):
        self.assertEqual(parse_row3("Producator: \nȚara: \nModel:"), {"producer": None, "country": None, "model": None})
        self.assertEqual(parse_row3("None")["producer"], None)

    def test_old_template_labels(self):
        p = parse_row3("Producator:  Natus\nȚara de Origine: Irlanda \nModel art: 25V26")
        self.assertEqual((p["producer"], p["country"], p["model"]), ("Natus", "Irlanda", "25V26"))


class MatchingTest(unittest.TestCase):

    def test_producer(self):
        self.assertTrue(producer_matches("Mindray", "Shenzhen Mindray Bio-Medical Electronics Co., Ltd")[0])
        self.assertTrue(producer_matches("B.Braun", "B. Braun Melsungen AG")[0])
        self.assertTrue(producer_matches("Intco Medical", "Shandong Intco Medical Products Co., Ltd.")[0])
        self.assertTrue(producer_matches("P.J. Dahlhausen & Co. GmbH", "P.J. DAHLHAUSEN & CO. GMBH")[0])
        self.assertFalse(producer_matches("Fiab", "Intersurgical Ltd")[0])
        self.assertFalse(producer_matches(None, "Intersurgical")[0])

    def test_model_both_directions(self):
        rec = RegistryRecord(dm="DM000078224", catalog="8250731SP", model="Infusomat Space Line")
        self.assertTrue(model_matches("Infusomat Space Line Neutrapur Ref.N8250731SP", rec)[0])
        rec2 = RegistryRecord(dm="DM1", catalog="630-240-00B, 630-240-05B")
        self.assertTrue(model_matches("630 240 05B", rec2)[0])
        self.assertFalse(model_matches("T20163", rec2)[0])


def offer(**kw):
    base = dict(sheet="lot 1", column=5, supplier="Labromed", producer="P.J. Dahlhausen & Co. GmbH",
                model="43.018.00.013", dm_numbers=["DM000654009"], order="Rg04-000259", order_date="2023-11-09")
    base.update(kw)
    return OfferRegistration(**base)


RECORD = RegistryRecord(dm="DM000654009", name="Cateter", producer="P.J. Dahlhausen & Co. GmbH",
                        catalog="43.018.00.013, 43.018.00.016", order="Rg04-000259", order_date="2023-11-09")


class CheckOfferTest(unittest.TestCase):

    def test_green(self):
        [r] = check_offer(offer(), {"DM000654009": RECORD}, {"DM000654009"})
        self.assertEqual(r.status, OK)

    def test_not_found_is_yellow(self):
        """«Не найден» - жёлтый с ручной проверкой (21641880: все 3 такие номера Ivan принял)."""
        [r] = check_offer(offer(), {"DM000654009": None}, {"DM000654009"})
        self.assertEqual(r.status, CHECK)
        self.assertIn("проверить вручную", r.reasons[0])
        [r] = check_offer(offer(), {"DM000654009": None}, set())
        self.assertIn("по локальной отметке", r.reasons[0])

    def test_search_codes(self):
        from src.amdm.matcher import search_codes
        o = offer(model="Masca nebulizator adult (Codul produsului:T 20163) ISO 13485:2016", dm_numbers=[])
        self.assertEqual(search_codes(o), ["20163"])
        self.assertEqual(search_codes(offer(model="F4141; DM000466393", dm_numbers=[])), ["F4141"])
        # объём и описание - не код (21641880, лоты 2 и 219)
        self.assertEqual(search_codes(offer(model="Metpak 500ml (cod 5270)", dm_numbers=[])), ["5270"])
        self.assertEqual(search_codes(offer(model="ECG 5-Lead", dm_numbers=[])), [])
        # 121C - температура стерилизации, а не код (лот 187)
        self.assertEqual(search_codes(offer(model="Indicator strip Steam 121C- 15 (104.220.0500)", dm_numbers=[])),
                         ["104.220.0500"])

    def test_no_dm_found_by_model_code(self):
        # 21641880: участник указал только CE/ISO, человек нашёл регистрацию поиском по названию
        pencil = RegistryRecord(dm="DM000466393", name="Electrod activ", producer="FIAB S.P.A.",
                                model="REUSABLE PENCIL", catalog="F4141")
        o = offer(producer="Fiab", model="F4141", dm_numbers=[], order=None, order_date=None, quality_docs=["CE"])
        [r] = check_offer(o, {}, set(), {"F4141": [pencil]})
        self.assertEqual((r.status, r.dm), (OK, "DM000466393"))
        self.assertIn("найден в реестре по коду F4141", r.reasons[0])
        # тот же код у другого производителя - проверить
        [r] = check_offer(offer(producer="Bowa", model="F4141", dm_numbers=[], order=None, order_date=None),
                          {}, set(), {"F4141": [pencil]})
        self.assertEqual(r.status, CHECK)
        self.assertIn("другого производителя", r.reasons[0])
        # по коду ничего - возможно, nu este înregistrat
        [r] = check_offer(o, {}, set(), {"F4141": []})
        self.assertEqual(r.status, CHECK)
        self.assertIn("nu este înregistrat", r.reasons[0])
        # поиск не делали (offline, новый код) - прежняя пометка про ISO/CE
        [r] = check_offer(o, {}, set(), {})
        self.assertIn("Номера DM нет, указаны: CE", r.reasons[0])

    def test_medical_type_by_registry(self):
        from src.amdm.medical import is_medical_type, registry_query, type_variants, type_word
        self.assertEqual(type_word("Lot nr. 234 DVD-R+plicuri  ( 007260 )"), "DVD-R")
        self.assertEqual(type_word("Hârtie termosensibilă, 50mm"), "Hârtie")
        self.assertEqual(registry_query("Hârtie"), "rtie")            # в реестре «HÂRTIE»
        self.assertEqual(type_variants("Test-indicator"), ["Test-indicator", "indicator"])
        # «CD/DVD» только у считывателей дисков - сами DVD-R в реестре не регистрируются
        self.assertFalse(is_medical_type("DVD-R", 3, ["CITITOR DE DISCURI MEDICALE CD/DVD"]))
        self.assertTrue(is_medical_type("Hârtie", 1272, []))
        self.assertTrue(is_medical_type("Gel", 4, ["GEL ECG"]))

    def test_non_medical_offer_needs_no_dm(self):
        dvd = offer(producer="Verbatim", model="DVD-R 4.7 GB", dm_numbers=[], order=None, order_date=None,
                    quality_docs=["ISO", "CE"], lot_title="Lot nr. 234 DVD-R+plicuri ( 007260 )")
        [r] = check_offer(dvd, {}, set(), {}, {"DVD-R": False})
        self.assertEqual(r.status, OK)
        self.assertIn("Не медицинское изделие", r.reasons[0])
        self.assertIn("ISO, CE", r.reasons[0])
        # тип медицинский или неизвестен - как раньше
        [r] = check_offer(dvd, {}, set(), {}, {"DVD-R": None})
        self.assertEqual(r.status, CHECK)

    def test_search_cache(self):
        import tempfile
        from pathlib import Path
        from src.amdm.cache import RegistryCache
        with tempfile.TemporaryDirectory() as tmp:
            cache = RegistryCache(Path(tmp) / "r.sqlite")
            self.assertIsNone(cache.get_search("catalog", "F4141"))
            cache.put_search("catalog", "F4141", [RegistryRecord(dm="DM000466393", producer="FIAB")])
            cache.put_search("catalog", "X999", [])
            self.assertEqual([r.dm for r in cache.get_search("catalog", "F4141")], ["DM000466393"])
            self.assertEqual(cache.get_search("catalog", "X999"), [])
            cache.close()

    def test_device_dm_for_spare_part_needs_iso(self):
        # 21643233, лот 509: комплект обслуживания SV650, а DM - самого вентилятора SV650
        device = RegistryRecord(dm="DM000153158", name="Ventilator pulmonar", model="SV650",
                                producer="SHENZHEN MINDRAY BIO-MEDICAL ELECTRONICS CO., LTD.")
        part = offer(producer="Mindray", model="115-110150-00", dm_numbers=["DM000153158"], order=None,
                     order_date=None, quality_docs=["ISO 13485"],
                     lot_title="Lot nr. 509 Kit de mentenanță pentru 1 an compatibil cu ventilatorul pulmonar SV650")
        [r] = check_offer(part, {"DM000153158": device}, {"DM000153158"})
        self.assertEqual(r.status, CHECK)
        self.assertIn("DM аппарата «SV650»", r.reasons[0])
        self.assertIn("нужен ISO", r.reasons[0])
        self.assertIn("ISO 13485", r.reasons[0])
        # аппарат в названии лота не назван - обычное «модель не найдена» с подсказкой про ISO
        [r] = check_offer(offer(model="X-1", lot_title="Lot nr. 1 Cateter"), {"DM000654009": RECORD}, set())
        self.assertIn("не найдены в записи реестра", r.reasons[0])

    def test_mismatch_is_yellow(self):
        [r] = check_offer(offer(producer="Fiab"), {"DM000654009": RECORD}, set())
        self.assertEqual(r.status, CHECK)
        self.assertIn("Производитель", r.reasons[0])
        [r] = check_offer(offer(model="X-99999"), {"DM000654009": RECORD}, set())
        self.assertEqual(r.status, CHECK)
        [r] = check_offer(offer(order_date="2023-11-10"), {"DM000654009": RECORD}, set())
        self.assertEqual(r.status, CHECK)

    def test_not_fetched_and_no_dm(self):
        [r] = check_offer(offer(), {}, set())
        self.assertEqual(r.status, IN_PROGRESS)
        [r] = check_offer(offer(dm_numbers=[], quality_docs=["ISO 13485"]), {}, set())
        self.assertEqual(r.status, CHECK)
        [r] = check_offer(offer(dm_numbers=[]), {}, set())
        self.assertEqual(r.status, NO_DM)
        self.assertEqual(check_offer(offer(dm_numbers=[], producer=None, model=None), {}, set()), [])

    def test_kit_overall_is_worst(self):
        rs = check_offer(offer(dm_numbers=["DM000654009", "DM000000001"]),
                         {"DM000654009": RECORD, "DM000000001": None}, {"DM000000001"})
        self.assertEqual(overall_status(rs), CHECK)         # набор: худший - «не найден» (жёлтый)
        self.assertEqual(overall_status([rs[0], rs[1].__class__(rs[1].offer, "x", FAIL, [])]), FAIL)


class CacheTest(unittest.TestCase):

    def test_put_get_and_not_found_ttl(self):
        with tempfile.TemporaryDirectory() as d:
            c = RegistryCache(Path(d) / "r.sqlite")
            c.put(RECORD, source="test")
            self.assertEqual(c.get("DM000654009").catalog, RECORD.catalog)
            self.assertEqual(c.latest_order_date(), "2023-11-09")
            c.mark_not_found("DM000000001")
            self.assertTrue(c.not_found_fresh("DM000000001"))
            self.assertFalse(c.not_found_fresh("DM000000001", now=datetime.now() + timedelta(days=8)))
            c.put(RegistryRecord(dm="DM000000001"), source="test")       # нашёлся позже
            self.assertFalse(c.not_found_fresh("DM000000001"))
            c.close()


class CsvImportTest(unittest.TestCase):

    def test_semicolon_csv_with_site_headers(self):
        text = ("Nr;Denumire;Den.comerc.;Model;Nr. catalog;Tara;Producatorul;Reprezentant;Ordin;Data;Cod vamal\n"
                "DM000927883;Cateter;Cat-X;M1;123-45;Germania;B. Braun;Rep SRL;Rg04-305;06-10-2026;9018\n")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "r.csv"
            p.write_text(text, encoding="utf-8")
            [rec] = CsvImporter().read(p)
        self.assertEqual((rec.dm, rec.trade_name, rec.catalog, rec.order_date),
                         ("DM000927883", "Cat-X", "123-45", "2026-10-06"))

    def test_header_names(self):
        self.assertEqual(header_field("Producătorul"), "producer")
        self.assertEqual(header_field("Den.comerc."), "trade_name")
        self.assertEqual(header_field("Nr. catalog"), "catalog")
        self.assertEqual(header_field("Nr"), "dm")


def lot_workbook():
    wb = Workbook()
    ws = wb.active
    ws.title = "lot 23"
    ws["E2"] = "Ofertant: \"ALMICOM-TECH\" S.R.L."
    ws["E3"] = "Producator: Emed\nȚara: Polonia\nModel: 630-240-00B "
    ws["E4"] = "CE/ISO "
    ws["F2"] = None                                    # колонка уточнений, не участник
    ws["F3"] = "clarificări"
    ws["G2"] = "Ofertant: Labromed Laborator SRL"
    ws["G3"] = "Producator: P.J. Dahlhausen & Co. GmbH\nȚara: Germania\nModel: 43.018.00.013"
    ws["G4"] = "DM000654009 Rg04-000259 09-11-2023"
    ws["H2"] = "Ofertant: "                            # пустая колонка шаблона
    wb.create_sheet("Lot x")["E2"] = "Ofertant: шаблон"
    return wb


class WorkbookTest(unittest.TestCase):

    def test_reads_participants_only(self):
        offers = read_offers(lot_workbook())
        self.assertEqual([o.supplier for o in offers], ['"ALMICOM-TECH" S.R.L.', "Labromed Laborator SRL"])
        self.assertEqual(offers[1].dm_numbers, ["DM000654009"])
        self.assertEqual(offers[0].quality_docs, ["CE", "ISO"])

    def test_report_sheet_does_not_touch_lot_cells(self):
        wb = lot_workbook()
        before = wb["lot 23"]["G4"].value
        results = [r for o in read_offers(wb) for r in check_offer(o, {"DM000654009": RECORD}, set())]
        write_report(wb, results)
        self.assertEqual(wb.sheetnames[0], SHEET)
        self.assertEqual(wb["lot 23"]["G4"].value, before)
        statuses = [wb[SHEET].cell(row=r, column=6).value for r in (2, 3)]
        self.assertEqual(sorted(statuses), sorted([CHECK, OK]))

    def test_opens_workbook_with_removed_images(self):
        """Книга, из которой удалили xl/media ради размера, всё равно открывается."""
        from openpyxl.drawing.image import Image
        from PIL import Image as PILImage
        with tempfile.TemporaryDirectory() as d:
            src, dst = Path(d) / "a.xlsx", Path(d) / "b.xlsx"
            wb = lot_workbook()
            buf = io.BytesIO()
            PILImage.new("RGB", (4, 4)).save(buf, format="PNG")
            wb["lot 23"].add_image(Image(io.BytesIO(buf.getvalue())), "J10")
            wb.save(src)
            with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w") as zout:
                for item in zin.infolist():
                    if not item.filename.startswith("xl/media/"):
                        zout.writestr(item, zin.read(item.filename))
            self.assertEqual(len(read_offers(load_workbook_tolerant(dst))), 2)


if __name__ == "__main__":
    unittest.main()
