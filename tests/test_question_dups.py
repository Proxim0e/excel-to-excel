"""Вопросы к человеку без дублей: один вопрос на оферту, одна карточка на модель для всех тендеров."""
import tempfile
import unittest
from pathlib import Path

from src.evaluate import confidence as cf
from src.evaluate import engine
from src.evaluate.answers import answer_card, open_questions, tidy_questions
from src.history.db import connect


def evaluation(tender, participant, key, lot_no=29):
    lot = engine.Lot(tender, lot_no, f"lot {lot_no}", "Lot nr. 29 Electrod", "Electrod 5 mm")
    ev = engine.Evaluation(tender, lot_no, participant, key, cf.CLARIFICARE, cf.PENDING, 0.3, ["x"],
                           findings={"card_prefill": {"model_key": key, "producer": "Fiab"}}, ask_card=True)
    return ev, lot


class QuestionDupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.kb = connect(Path(self.tmp.name) / "kb.sqlite")

    def tearDown(self):
        self.kb.close()
        self.tmp.cleanup()

    def kinds(self):
        return sorted((q["kind"], q["tender_id"], q["model_key"]) for q in open_questions(self.kb))

    def test_engine_one_question_per_offer_and_card_per_model(self):
        engine.save(self.kb, *evaluation("T1", "Electeh", "DM:DM1"))
        engine.save(self.kb, *evaluation("T1", "Electeh", "P:fiab|f4141"))    # ключ модели сменился
        engine.save(self.kb, *evaluation("T2", "Electeh", "P:fiab|f4141"))    # та же модель в другом тендере
        self.assertEqual(self.kinds(), [("offer_status", "T1", "P:fiab|f4141"), ("offer_status", "T2", "P:fiab|f4141"),
                                        ("product_card", "T1", "DM:DM1"), ("product_card", "T1", "P:fiab|f4141")])

    def test_tidy_old_duplicates_and_answer_closes_other_tenders(self):
        now = "2026-11-05T10:00:00"
        rows = [("offer_status", "T1", 29, "Electeh", "DM:DM1"), ("offer_status", "T1", 29, "Electeh", "P:a|1"),
                ("product_card", "T1", 0, "", "P:a|1"), ("product_card", "T2", 0, "", "P:a|1"),
                ("product_card", "T2", 0, "", "P:b|2")]
        for r in rows:
            self.kb.execute("INSERT INTO questions (kind, tender_id, lot_no, participant, model_key, payload, state, "
                            "created_at) VALUES (?, ?, ?, ?, ?, '{}', 'open', ?)", (*r, now))
        self.kb.commit()
        self.assertEqual(tidy_questions(self.kb), 2)
        self.assertEqual(self.kinds(), [("offer_status", "T1", "P:a|1"), ("product_card", "T2", "P:a|1"),
                                        ("product_card", "T2", "P:b|2")])
        q = [q for q in open_questions(self.kb) if q["model_key"] == "P:b|2"][0]
        answer_card(self.kb, q["id"], {"model_key": "P:b|2", "producer": "B"})
        self.assertEqual(self.kinds(), [("offer_status", "T1", "P:a|1"), ("product_card", "T2", "P:a|1")])


if __name__ == "__main__":
    unittest.main()
