import unittest

from src.anexa22.requirements import check_declarative, is_copy, parse_constraints


def bounds(text):
    return [(c.kind, c.family, c.lo, c.hi) for c in parse_constraints(text)]


class ParseTests(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(bounds("Lungime: ≥ 15 cm"), [("min", "len", 150, None)])
        self.assertEqual(bounds("lungimea min. 3m"), [("min", "len", 3000, None)])
        self.assertEqual(bounds("Lungimea minima 150cm"), [("min", "len", 1500, None)])
        self.assertEqual(bounds("Volum compresiabil ≤ 60 ml"), [("max", "vol", None, 60)])
        self.assertEqual(bounds("cuțit lungime 70 mm (+-5mm)"), [("tol", "len", 65, 75)])
        self.assertEqual(bounds("Lungimea tubului 240-310 cm"), [("range", "len", 2400, 3100)])
        self.assertEqual(bounds("Marimea 25- 35 cm ±3 cm"), [("range_tol", "len", 220, 380)])
        self.assertEqual(bounds("Manșete infuzii: 500 ml"), [("exact", "vol", 500, 500)])

    def test_not_lengths(self):
        self.assertEqual(bounds("Configurație conectori: 15M (masculin) x 22F (feminin)"), [])
        self.assertEqual(bounds("Conexiuni 22F-15M, 22M-15F"), [])
        self.assertEqual(bounds("Rezistența la flux 30l/min ≤ 2,04 cm H2O"),
                         [("max", "press_cmh2o", None, 2.04)])
        self.assertEqual(bounds("Rata de filtrare ≥ 99,99%"), [("min", "pct", 99.99, None)])

    def test_dimensions(self):
        self.assertEqual(bounds("hîrtie 110mm х 140mm"), [("exact", "len", 110, 110), ("exact", "len", 140, 140)])
        self.assertEqual(bounds("Rolă 100mmx200m"), [("exact", "len", 100, 100), ("exact", "len", 200000, 200000)])

    def test_labels(self):
        labels = [c.text for c in parse_constraints(
            "Rata de filtrare Bacteriala ≥ 99,99% Volum compresiabil ≤ 60 ml, Lungimea tubului 240-310 cm "
            "Rezistent la presiune (2 bari)")]
        self.assertEqual(labels[0], "Rata de filtrare Bacteriala ≥ 99,99%")
        self.assertEqual(labels[1], "Volum compresiabil ≤ 60 ml")
        self.assertEqual(labels[3], "Rezistent la presiune (2 bari")


class DeclarativeTests(unittest.TestCase):
    def test_satisfied_and_overlapping(self):
        r = check_declarative("Lungime circuit ≥ 160 cm", "Lungime 150-180cm")
        self.assertEqual((r.conflicts, r.missing), ([], []))
        r = check_declarative("Marimea 33-47 cm", "Marimea 36-46 cm ±5 cm")
        self.assertEqual((r.conflicts, r.missing), ([], []))

    def test_conflict(self):
        r = check_declarative("cu cablu de interconectare lungimea 100 cm", "cu cablu de interconectare lungimea 60 cm")
        self.assertEqual(len(r.conflicts), 1)
        self.assertIn("(A22: cu cablu de interconectare lungimea 100 cm)", r.conflicts[0])

    def test_interior_is_not_exterior(self):
        r = check_declarative("Diametru interior – 25,3 ±0,2mm", "Diametru exterior – 28 mm ±0,5mm")
        self.assertEqual(r.conflicts, [])
        self.assertEqual(r.missing, ["Diametru exterior – 28 mm ±0,5mm"])

    def test_missing(self):
        r = check_declarative("1.diametru 50 mm, 2. electrod Ag/AgCl", "1.diametru 50 mm 9.Impedanță 10 Hz ≤ 150 ohm")
        self.assertEqual(r.conflicts, [])
        self.assertIn("Impedanță 10 Hz ≤ 150 ohm", r.missing[-1])

    def test_label_must_match(self):
        # «diametru ≥ 5 cm» не закрывается длиной
        r = check_declarative("Cablu, lungime 150 cm", "Diametru ≥ 5 cm")
        self.assertEqual(r.missing, ["Diametru ≥ 5 cm"])
        self.assertEqual(r.status, "alternativa")
        # значение без подписи - если в запросе одно условие этого семейства
        r = check_declarative("150 cm", "Lungime ≥ 100 cm")
        self.assertEqual(r.status, "ok")

    def test_unlabeled_requirement(self):
        # 21643233, лоты 323 и 364: копия запроса, значения без подписи
        req = "Electrod activ reutilizabil tip billă, 5mm Lungime12 cm diametru bilă 5mm"
        self.assertEqual(check_declarative(req, req).status, "ok")
        req = "Cartus din fir de polipropilena 10'', 10 microni Cartus din fir de polipropilena 10'', 10 microni"
        self.assertEqual(check_declarative(req, req).status, "ok")
        # лот 277: 1,21m против 2.4m - расхождение, а не «нет данных»
        r = check_declarative("Cablu SpO2 25 pini la LNCS, drept , 2.4m", "Cablu SpO2 25 pini la LNCS, drept , 1,21m")
        self.assertEqual(len(r.conflicts), 1)

    def test_requested_5_given_4(self):
        r = check_declarative("Lungime 4 cm", "Lungime 5 cm")
        self.assertEqual(len(r.conflicts), 1)
        self.assertEqual(r.status, "alternativa")

    def test_dimensions_whole(self):
        # пример Ivan: ЭКГ-электрод 30x30 против 50x50, остальное ок
        req = "Electrod ECG 30x30 mm, Ag/AgCl, adult"
        r = check_declarative("Electrod ECG 50x50 mm, Ag/AgCl, adult", req)
        self.assertIn("(A22: 50x50 mm)", r.conflicts[0])
        self.assertEqual(r.status, "alternativa")
        self.assertEqual(check_declarative("Electrod 30 x 30 mm Ag/AgCl adult", req).status, "ok")

    def test_gauge_is_not_grams(self):
        self.assertEqual(bounds("Ac 18 G, lungime 40 mm"), [("exact", "len", 40, 40)])

    def test_compatibility_must_be_named(self):
        # пример Ivan: просим кабель для Dräger 120, в оферте не сказано, с чем совместим
        req = "Cablu de interconectare compatibil cu Drager 120, lungime ≥ 2 m"
        r = check_declarative("Cablu de interconectare, lungime 3 m", req)
        self.assertEqual(r.status, "alternativa")
        self.assertIn("compatibilitate neindicată", r.missing[0])
        self.assertEqual(check_declarative("Cablu de interconectare pentru Dräger 120, lungime 3 m", req).status, "ok")
        # 21643233, лот 483: «pentru sterilizarea ... de 54 grade C» - не аппарат
        self.assertEqual(check_declarative("Rolă hîrtie", "Rolă pentru sterilizarea cu plasmă la temperatura de 54 "
                                                          "grade C").missing, [])

    def test_device_from_lot_title(self):
        title = "Lot nr. 282 Cablu ECG Carescape B450, GE"
        r = check_declarative("Cablu ECG 5 fire", "Cablu ECG 5 fire", title=title)
        self.assertEqual(r.status, "alternativa")
        r = check_declarative("Cablu ECG 5 fire", "Cablu ECG 5 fire", title=title, offered_extra="U302-15CE GE B450")
        self.assertEqual(r.status, "ok")
        # размеры и количества в названии - не коды аппарата
        r = check_declarative("Film radiologic", "Film", title="Lot nr. 406 Film radiologic 35x43(14x17inch), 30W")
        self.assertEqual(r.missing, [])

    def test_quality_attributes(self):
        r = check_declarative("Electrod reutilizabil, adult", "Electrod de unică folosință, adult")
        self.assertEqual(r.conflicts, ["unică folosință (A22: reutilizabil)"])
        r = check_declarative("Manusi chirurgicale", "Manusi chirurgicale sterile, fără latex")
        self.assertEqual(sorted(r.missing), ["fără latex", "steril"])
        self.assertEqual(check_declarative("Manusi sterile, latex free", "Manusi sterile, fără latex").status, "ok")
        # «sterilizabil» - не «steril»
        self.assertEqual(check_declarative("Recipient sterilizabil", "Recipient").status, "ok")

    def test_copy(self):
        req = "Electrod ECG, Adeziv, Ag/AgCl, Tip snap button, De unica folosinta, Tip pacient: Adult"
        self.assertTrue(is_copy(req + " *Conform broșură pagina 1", req))
        self.assertFalse(is_copy("Electrod ECG REF JD17O01", req))


if __name__ == "__main__":
    unittest.main()
