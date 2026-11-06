"""Нестрогое сравнение оферты с записью реестра AMDM - на случаях из тендера 21641880."""
import unittest

from src.amdm.matcher import model_matches, producer_matches
from src.amdm.models import RegistryRecord as R


class ProducerTests(unittest.TestCase):
    def test_matches(self):
        for offered, registered in (("JHMS Co.", "JIANGXI HAWK MEDICAL SUPPLIES CO., LTD"),
                                    ("MIR", "MEDICAL INTERNATIONAL RESEARCH S.R.L."),
                                    ("ICMN", "INTERNATIONAL COMPANY FOR MEDICAL NECESSITIES"),
                                    ("Aqumedical", "AQUA MEDIKAL TIBBI ARAC VE GERECLERI"),
                                    ("Fiab", "FIAB SPA"),
                                    ("R-VENT MEDIKAL", "R-VENT MEDIKAL URETRIM A.S."),
                                    ("SUN O SUB", "SAN-O-SUB MBB S.R.L.")):  # 21643233, лот 451
            self.assertTrue(producer_matches(offered, registered)[0], offered)

    def test_mismatches(self):
        for offered, registered in (("Lumed S.R.L.", "MEDICAL INTERNATIONAL RESEARCH"),
                                    ("Agfa NV", "BERNHARD FORSTER GMBH FORESTADENT"),
                                    ("Fiab", "BIONEN S.A.S."),
                                    ("Shenzhen UpnMed Equipment Co., Ltd", "CHIRANA T. INJECTA, S.R.O.")):
            self.assertFalse(producer_matches(offered, registered)[0], offered)


class ModelTests(unittest.TestCase):
    def test_matches(self):
        cases = [("Ansa ovala 10mm (Codul produsului:S D-T 1024230)",
                  R(dm="x", model="ELECTROSURGICAL SNARES", catalog="SD-T, SD-L, SD-Y")),   # семейство
                 ("DI-HL 25X30 CM", R(dm="x", model="DI-HL")),                              # код без цифр
                 ("GS", R(dm="x", model="50 ML 3 PARTS", catalog="GS")),                     # короткий код целиком
                 ("80 MM X 20 M, RULOU", R(dm="x", model="80 MM X 20 M, RULOU")),            # модель-размер
                 ("Hîrtie ECG 110mm x 140mm x 200P", R(dm="x", model="110X140X200, TOP", catalog="T5Z110")),
                 ("Ortho CP GU-M; G 150; EOS.Dev.", R(dm="x", model="E.O.S. DEV.", catalog="EF19G")),  # буквенный код
                 ("Electrod (Codul produsului:F 4141)", R(dm="x", model="REUSABLE PENCIL", catalog="F4141"))]
        for offered, record in cases:
            self.assertIs(model_matches(offered, record)[0], True, offered)

    def test_code_written_with_space_is_glued(self):
        """21643233, лот 354 (MED-M): «M82750 63/5 M82750 63/10» - две модели, в реестре каталог M8275063/5
        (DM000267272) и M8275063/10 (DM000267273). Раньше совпадал обрывок «M82750» - /5 не отличался от /10."""
        from src.amdm.parse import split_models
        self.assertEqual(split_models("M82750 63/5 M82750 63/10"), ["M8275063/5", "M8275063/10"])
        self.assertEqual(split_models("0013.F02-UNI 0013.F03-UNI"), ["0013.F02-UNI", "0013.F03-UNI"])
        self.assertEqual(split_models("CUET44S 10 ml"), ["CUET44S 10 ml"])
        r5 = R(dm="DM000267272", model="500ml Canisters with gel, Carton of 5", catalog="M8275063/5")
        r10 = R(dm="DM000267273", model="500ml Canisters with gel, Carton of 10", catalog="M8275063/10")
        self.assertEqual(model_matches("M82750 63/5 M82750 63/10", r5), (True, "M8275063/5"))
        self.assertEqual(model_matches("M82750 63/5 M82750 63/10", r10), (True, "M8275063/10"))
        self.assertIs(model_matches("M82750 63/5", r10)[0], False)
        self.assertIs(model_matches("M82750 63/10", r5)[0], False)

    def test_mismatches(self):
        self.assertIs(model_matches("56 MM X 18 M, RULOU", R(dm="x", model="80 MM X 30 M, RULOU"))[0], False)
        ok, what = model_matches("PFS01-01523230", R(dm="x", model="STERILE HOT SNARE", catalog="PFS01-15223230"))
        self.assertIs(ok, False)
        self.assertEqual(what, "PFS01-01523230")

    def test_nothing_to_compare(self):
        self.assertIsNone(model_matches("489521", R(dm="x", name="ACCESORII RESPIRATORII"))[0])
        self.assertIsNone(model_matches("SD-S 35 x 43 cm", R(dm="x", model="LASER IMAGER DRYPRO SIGMA"))[0])


if __name__ == "__main__":
    unittest.main()
