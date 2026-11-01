import unittest

from openpyxl import Workbook
from openpyxl.styles import PatternFill

from src.amdm import mark
from src.amdm.models import CHECK, FAIL, IN_PROGRESS, NO_DM, OK, CheckResult, OfferRegistration, RegistryRecord

FIAB = RegistryRecord(dm="DM000466393", producer="FIAB SPA", model="REUSABLE PENCIL", catalog="F4141",
                      order="Rg04-000083", order_date="2023-04-13")


def result(status, dm="DM000466393", record=FIAB, reasons=None, col=5, sheet="lot 29"):
    offer = OfferRegistration(sheet=sheet, column=col, supplier="Electeh SRL", producer="Fiab", model="F4141",
                              dm_numbers=[dm] if dm else [])
    return CheckResult(offer, dm, status, reasons or [], record)


class Row4Tests(unittest.TestCase):
    def test_found_adds_order_like_human(self):
        self.assertEqual(mark.row4_text("DM000466393", [result(OK)]), "DM000466393\nRg04-000083 13-04-2023")

    def test_order_under_its_number_in_a_set(self):
        other = RegistryRecord(dm="DM000466394", order="Rg04-000090", order_date="2023-05-01")
        text = mark.row4_text("DM000466393\nDM000466394", [result(OK), result(OK, "DM000466394", other)])
        self.assertEqual(text, "DM000466393\nRg04-000083 13-04-2023\nDM000466394\nRg04-000090 01-05-2023")

    def test_shared_order_under_each_number(self):
        other = RegistryRecord(dm="DM000466394", order="Rg04-000083", order_date="2023-04-13")
        text = mark.row4_text("DM000466393\nDM000466394", [result(OK), result(OK, "DM000466394", other)])
        self.assertEqual(text, "DM000466393\nRg04-000083 13-04-2023\nDM000466394\nRg04-000083 13-04-2023")

    def test_empty_row4_note_without_blank_line(self):
        text = mark.row4_text(None, [result(NO_DM, None, None, ["Нет ни номера DM, ни сертификатов"])])
        self.assertEqual(text, "// Нет ни номера DM, ни сертификатов")

    def test_not_registered(self):
        text = mark.row4_text("DM000569605", [result(FAIL, "DM000569605", None)])
        self.assertEqual(text, "DM000569605\n// nu este înregistrat în AMDM: DM000569605")

    def test_mismatch_reason(self):
        text = mark.row4_text("DM000466393", [result(CHECK, reasons=["Производитель не совпал"])])
        self.assertEqual(text, "DM000466393\nRg04-000083 13-04-2023\n// Производитель не совпал")

    def test_rerun_does_not_duplicate(self):
        once = mark.row4_text("DM000466393", [result(CHECK, reasons=["x"])])
        self.assertEqual(mark.row4_text(once, [result(CHECK, reasons=["x"])]), once)


class MarkTests(unittest.TestCase):
    def sheet(self, wb, title="lot 29", tab="FF00B050"):
        ws = wb.create_sheet(title)
        ws["E2"], ws["F2"] = "Ofertant: Electeh SRL", "Ofertant: DITA"
        ws["E4"], ws["F4"] = "DM000466393", "DM000466393"
        for a in ("E1", "F1"):
            ws[a].fill = PatternFill("solid", fgColor="FF00B050")
        ws.sheet_properties.tabColor = tab
        return ws

    def test_colors_row4_row1_and_tab(self):
        wb = Workbook()
        ws = self.sheet(wb)
        stats = mark.mark_workbook(wb, [result(OK, col=5), result(FAIL, "DM000466393", None, col=6)])
        self.assertEqual(stats, {OK: 1, FAIL: 1})
        self.assertEqual(ws["E4"].fill.fgColor.rgb, mark.GREEN)
        self.assertEqual(ws["F4"].fill.fgColor.rgb, mark.RED)
        self.assertEqual(ws["E1"].fill.fgColor.rgb, mark.GREEN)   # зелёный не «ухудшается»
        self.assertEqual(ws["F1"].fill.fgColor.rgb, mark.RED)
        self.assertEqual(ws.sheet_properties.tabColor.rgb, mark.RED)

    def test_no_dm_is_yellow_and_not_checked_is_untouched(self):
        wb = Workbook()
        ws = self.sheet(wb)
        mark.mark_workbook(wb, [result(NO_DM, None, None, ["Номера DM нет, указаны: CE"], col=5),
                                result(IN_PROGRESS, col=6)])
        self.assertEqual(ws["E4"].fill.fgColor.rgb, mark.YELLOW)
        self.assertEqual(ws["E1"].fill.fgColor.rgb, mark.YELLOW)
        self.assertEqual(ws["F4"].value, "DM000466393")
        self.assertIsNone(ws["F4"].fill.fill_type)

    def test_human_color_and_not_held_tab_kept(self):
        wb = Workbook()
        ws = self.sheet(wb, tab="FF001F4D")
        ws["E1"].fill = PatternFill("solid", fgColor="FFFFC000")  # оранжевый поставил человек
        mark.mark_workbook(wb, [result(FAIL, "DM000466393", None, col=5)])
        self.assertEqual(ws["E1"].fill.fgColor.rgb, "FFFFC000")
        self.assertEqual(ws.sheet_properties.tabColor.rgb, "FF001F4D")


if __name__ == "__main__":
    unittest.main()
