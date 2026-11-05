"""Требование лота -> параметры карточки: короткие названия, «aproximativ / min. / de» - в значении."""
import tempfile
import unittest
from pathlib import Path

from src.evaluate.cards import spec_items, split_params
from src.ui.card_data import clarification_documents, lots_in_name, param_rows, participant_documents


def params(text):
    return [p for item in spec_items(text) for p in split_params(item)]


NEBULIZER = ("Cu cameră speciala conceputa cu rata de nebulizare aproximativ 3 ml-10min, dimensiunea medie a "
             "particulelor ≤ 5 microni -kit complet format din Mască-nebulizator, cameră nebulizator și tub de 200cm "
             "(±10cm)")


class ParamTests(unittest.TestCase):
    def test_nebulizer_lot_3(self):
        self.assertEqual(params(NEBULIZER), [
            ("rata de nebulizare", "aproximativ 3 ml-10min"), ("cameră speciala conceputa", "da"),
            ("dimensiunea medie a particulelor", "≤ 5 microni"), ("tub", "de 200cm (±10cm)"),
            ("kit complet format din", "Mască-nebulizator, cameră nebulizator și tub")])

    def test_other_requirements(self):
        self.assertEqual(params("Film radiologic cu baza albastra  26 x 36 cm  (10 x 14 inch)  ambalaj până la 150 "
                                "bucăți în cutie\ncompatibil cu DryPix SMART, LITE sau EDGE\nEchivalent cu model DI – HL"),
                         [("dimensiuni", "26 x 36 cm (10 x 14 inch)"), ("Film radiologic cu baza albastra", "da"),
                          ("ambalaj", "până la 150 bucăți în cutie"), ("compatibil cu DryPix SMART, LITE sau EDGE", "da"),
                          ("Echivalent cu model DI – HL", "da")])
        self.assertEqual(params("Hîrtie milimetrică p/u ECG , termosensibilă 57х25 tip: rolă caroiaj rosu/albastru."),
                         [("dimensiuni", "57х25"), ("Hîrtie milimetrică p/u ECG , termosensibilă", "da"),
                          ("tip", "rolă caroiaj rosu/albastru")])
        self.assertEqual(params("1.Muștuc cu turbina\n2. Dimensiuni:\nDiametru interior – 30,5mm ±0,5mm\n"
                                "Lungime – 41,5 mm ±5mm\n3. De unică folosința"),
                         [("Muștuc cu turbina", "da"), ("Diametru interior", "30,5mm ±0,5mm"), ("Lungime", "41,5 mm ±5mm"),
                          ("unică folosința", "da")])
        self.assertEqual(params("Electrod taiere, bipolar, 2 tije, diametru 0,40 mm"),
                         [("Electrod taiere", "da"), ("bipolar", "da"), ("tije", "2"), ("diametru", "0,40 mm")])
        self.assertEqual(params("model 5392. Lungime mai mare 3 metri"), [("model", "5392"), ("Lungime", "mai mare 3 metri")])
        self.assertEqual(params("1.diametru 38 - 47mm, 2. electrod Ag/AgCl, 3. termen de utilizare min. 24 ore"),
                         [("diametru", "38 - 47mm"), ("electrod Ag/AgCl", "da"), ("termen de utilizare", "min. 24 ore")])

    def test_old_prefill_with_cut_text_merges_with_requirement(self):
        old = {"…ceputa cu rata de nebulizare aproximati": "…ceputa cu rata de nebulizare aproximativ 3 ml",
               "cameră nebulizator și tub de 200cm (±10c": "cameră nebulizator și tub de 200cm (±10cm)"}
        rows = {r["name"]: r for r in param_rows(old, NEBULIZER)}
        self.assertEqual(rows["rata de nebulizare"]["value"], "aproximativ 3 ml-10min")
        self.assertEqual(rows["tub"]["value"], "de 200cm (±10cm)")
        self.assertNotIn("…ceputa cu rata de nebulizare", rows)


class ClarificationDocsTests(unittest.TestCase):
    def test_lots_in_file_name(self):
        self.assertEqual(lots_in_name("3-4 TDS.semnat.pdf"), {3, 4})
        self.assertEqual(lots_in_name("146 180 205-206 TDS.signed.pdf"), {146, 180, 205, 206})
        self.assertEqual(lots_in_name("Lot 70_71_73.signed.pdf"), {70, 71, 73})
        self.assertEqual(lots_in_name("Rg01-6570 04.09.2026.pdf"), set())
        self.assertEqual(lots_in_name("04.09.2026 scrisoare.pdf"), set())

    def test_documents_from_participant_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            tender = Path(tmp) / "21641880"
            for folder, name in (("Dita Estfarm", "3-4 TDS.semnat.pdf"), ("Dita Estfarm", "Anexa23.pdf"),
                                 ("Dita Estfarm", "11 TDS.pdf"), ("Electeh", "Lot 3.pdf")):
                path = tender / "Clarificari" / folder / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"%PDF-1.4 " + name.encode())
            docs = clarification_documents(tender, "DITA ESTFARM S.R.L", 3)
            self.assertEqual([d["name"] for d in docs], ["3-4 TDS.semnat.pdf", "11 TDS.pdf", "Anexa23.pdf"])
            self.assertTrue(docs[0]["label"].startswith("Уточнение (этот лот): Dita Estfarm/"))
            self.assertEqual([d["name"] for d in participant_documents(tender, "DITA ESTFARM S.R.L", 3)][0],
                             "3-4 TDS.semnat.pdf")


if __name__ == "__main__":
    unittest.main()
