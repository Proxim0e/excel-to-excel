"""Вопрос «медицинское ли изделие»: реестр не решил -> вопрос в окне -> решение человека в проверке AMDM."""
import tempfile
import unittest
from pathlib import Path

from src.amdm.__main__ import collect_types
from src.amdm.cache import RegistryCache
from src.amdm.matcher import check_offer
from src.amdm.medical import HUMAN_NO, HUMAN_YES
from src.amdm.models import CHECK, OK, OfferRegistration
from src.evaluate.answers import answer_medical, human_types, open_questions
from src.history.db import connect


def offer(title, supplier="Ecochimie", model="Caciulita chirurgicala 26203"):
    return OfferRegistration(sheet="lot 7", column=5, supplier=supplier, producer="X", model=model, country=None,
                             dm_numbers=[], order=None, order_date=None, quality_docs=["CE", "ISO"],
                             lot_title=title)


class MedicalQuestionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = RegistryCache(Path(self.tmp.name) / "r.sqlite")
        self.kb = connect(Path(self.tmp.name) / "kb.sqlite")
        # реестр уже спрашивали: «Caciuli» - 0 записей, по буквам без диакритики - неясно
        self.cache.put_type("Caciuliță", None, 0)

    def tearDown(self):
        self.cache.close()
        self.kb.close()
        self.tmp.cleanup()

    def test_unclear_type_asks_once_then_uses_answer(self):
        offers = [offer("Lot nr. 7 Caciuliță chirurgicală"), offer("Lot nr. 8 Caciuliță chirurgicală", "Labromed")]
        self.assertEqual(collect_types(self.cache, offers, False, True, self.kb), {"Caciuliță": None})
        [q] = open_questions(self.kb)
        self.assertEqual((q["kind"], q["model_key"], q["payload"]["offers_total"]), ("medical_type", "Caciuliță", 2))
        collect_types(self.cache, offers, False, True, self.kb)          # повторно - тот же вопрос, не новый
        self.assertEqual(len(open_questions(self.kb)), 1)

        answer_medical(self.kb, q["id"], False, "шапочки - не мед. изделие")
        self.assertEqual(human_types(self.kb), {"Caciuliță": False})
        self.assertEqual(open_questions(self.kb), [])
        medical = collect_types(self.cache, offers, False, True, self.kb)
        self.assertEqual(medical, {"Caciuliță": HUMAN_NO})
        [r] = check_offer(offers[0], {}, set(), {}, medical)
        self.assertEqual(r.status, OK)
        self.assertIn("решение человека", r.reasons[0])

    def test_human_says_medical(self):
        offers = [offer("Lot nr. 7 Caciuliță chirurgicală")]
        collect_types(self.cache, offers, False, True, self.kb)
        [q] = open_questions(self.kb)
        answer_medical(self.kb, q["id"], True)
        medical = collect_types(self.cache, offers, False, True, self.kb)
        self.assertEqual(medical, {"Caciuliță": HUMAN_YES})
        [r] = check_offer(offers[0], {}, set(), {}, medical)
        self.assertEqual(r.status, CHECK)                                # медицинское без DM - жёлтое

    def test_offline_does_not_ask(self):
        collect_types(self.cache, [offer("Lot nr. 7 Caciuliță chirurgicală")], True, True, self.kb)
        self.assertEqual(open_questions(self.kb), [])


if __name__ == "__main__":
    unittest.main()
