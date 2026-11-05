"""Уточнения (21641880): повтор требования в письме-ответе не подтверждение; значение в документе,
нарушающее требование рядом с кодом модели, - противоречие (не зелёный)."""
import unittest

from src.anexa22.evidence import _strip_echo, _value_for_condition, document_conflicts, is_echo, _words
from src.anexa22.requirements import parse_constraints

REQ = ("Filtru traheostomic tip HME\nRezistența la flux 30l/min ≤ 0,3 cm H2O\n"
       "8.termen de utilizare min. 48 ore sau de lungă durată, 9.Impedanță 10 Hz ≤ 150 ohm;")


class EchoTests(unittest.TestCase):
    def test_echo_line_is_not_evidence(self):
        echo = set(_words(REQ))
        self.assertTrue(is_echo("8.termen de utilizare min. 48 ore sau de lungă durată, 9.Impedanță 10 Hz", echo))
        self.assertFalse(is_echo("Electrod ECG 48 ore (Codul produsului:F9067)", echo))
        page = ("s", "Raspuns.pdf", 3, "min. 48 ore sau de lungă durată, 9.Impedanță 10 Hz\nHz ≤ 150 ohm;\n"
                                       "Electrod ECG 48 ore (Codul produsului:F9067)")
        kept = _strip_echo(page, REQ, echo)[3]
        self.assertEqual(kept, "Electrod ECG 48 ore (Codul produsului:F9067)")

    def test_bound_does_not_close_stricter_bound(self):
        [cond] = [c for c in parse_constraints(REQ) if c.family == "press_cmh2o"]
        page = ("s", "TDS.pdf", 1, "Rezistența la flux 30l/min ≤ 1,63 cm H2O")
        self.assertIsNone(_value_for_condition([page], cond))
        self.assertIsNotNone(_value_for_condition([("s", "TDS.pdf", 1, "Rezistența la flux 0,25 cm H2O")], cond))

    def test_conflict_near_model_code_only(self):
        conds = parse_constraints("Rezistența la flux 30l/min ≤ 0,3 cm H2O")
        near = ("s", "Raspuns.pdf", 5, "Filtru AL-08025.V001\nRezistența la flux 30l/min ≤ 1,63 cm H2O")
        self.assertEqual(len(document_conflicts([near], conds, ["08025.V001"])), 1)
        far = ("s", "Raspuns.pdf", 5, "Filtru AL-08022.V001\nRezistența la flux 30l/min ≤ 1,63 cm H2O")
        self.assertEqual(document_conflicts([far], conds, ["08025.V001"]), [])
        # каталог с вариантами: есть и подходящее значение - не противоречие
        both = ("s", "Cat.pdf", 1, "08025.V001\nRezistența la flux ≤ 1,63 cm H2O\nRezistența la flux ≤ 0,25 cm H2O")
        self.assertEqual(document_conflicts([both], conds, ["08025.V001"]), [])


if __name__ == "__main__":
    unittest.main()
