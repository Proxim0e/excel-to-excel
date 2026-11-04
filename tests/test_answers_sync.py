"""Ответы человека между двумя ПК через git: каждый ПК пишет свой knowledge/answers/<ПК>.jsonl, другой подмешивает."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.evaluate import confidence as cf
from src.evaluate import engine
from src.evaluate.answers import answer_card, answer_medical, answer_offer, ask_medical_type, open_questions
from src.history import sync
from src.history.db import connect


def evaluate_one(kb, participant="Electeh SRL"):
    lot = engine.Lot("T1", 29, "lot 29", "Lot nr. 29 Electrod", "Electrod 5 mm")
    ev = engine.Evaluation("T1", 29, participant, "P:fiab|f4141", cf.CLARIFICARE, cf.PENDING, 0.3, ["x"],
                           findings={"card_prefill": {"model_key": "P:fiab|f4141", "producer": "Fiab", "dm": "DM1"}},
                           ask_card=True)
    engine.save(kb, ev, lot)
    kb.commit()


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.folder = root / "answers"
        self.work = connect(root / "work.sqlite")
        self.home = connect(root / "home.sqlite")
        for kb in (self.work, self.home):
            evaluate_one(kb)
            ask_medical_type(kb, "Caciuliță", {"word": "Caciuliță"})

    def tearDown(self):
        self.work.close()
        self.home.close()
        self.tmp.cleanup()

    def answer_all(self, kb):
        qs = {q["kind"]: q for q in open_questions(kb)}
        answer_card(kb, qs["product_card"]["id"], {"model_key": qs["product_card"]["model_key"], "producer": "FIAB SpA", "model": "F4141",
                                                   "dm": "DM1", "params": {"Diametru": "5 mm"}})
        answer_offer(kb, qs["offer_status"]["id"], "corespunde", comment="проверено по каталогу")
        answer_medical(kb, qs["medical_type"]["id"], False, "шапочки")

    def test_answers_from_work_pc_reach_home_pc(self):
        self.answer_all(self.work)
        with mock.patch.object(sync, "pc_name", return_value="work"):
            self.assertEqual(sync.export(self.work, sync.own_file(self.folder)), 3)
        lines = (self.folder / "work.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(sorted(json.loads(x)["t"] for x in lines), ["offer", "product", "type"])

        with mock.patch.object(sync, "pc_name", return_value="home"):
            st = sync.sync(self.home, self.folder)
        self.assertEqual((st["product"], st["offer"], st["type"], st["closed"]), (1, 1, 1, 3))
        self.assertEqual(open_questions(self.home), [])
        self.assertEqual(self.home.execute("SELECT producer, source FROM products").fetchone(), ("FIAB SpA", "human"))
        self.assertEqual(self.home.execute("SELECT status, source FROM evaluations").fetchone(),
                         ("corespunde", "human"))
        self.assertEqual(self.home.execute("SELECT medical FROM product_types").fetchone(), (0,))
        self.assertTrue((self.folder / "home.jsonl").is_file())
        # повторная загрузка ничего не меняет
        with mock.patch.object(sync, "pc_name", return_value="home"):
            st = sync.sync(self.home, self.folder)
        self.assertEqual(st["product"] + st["offer"] + st["type"], 0)

    def test_newer_answer_wins(self):
        self.answer_all(self.work)
        with mock.patch.object(sync, "pc_name", return_value="work"):
            sync.export(self.work, sync.own_file(self.folder))
        # дома тот же оферт решили позже и иначе
        q = [q for q in open_questions(self.home) if q["kind"] == "offer_status"][0]
        answer_offer(self.home, q["id"], "nu_corespunde")
        self.home.execute("UPDATE evaluations SET decided_at = '2999-01-01T00:00:00'")
        with mock.patch.object(sync, "pc_name", return_value="home"):
            sync.sync(self.home, self.folder)
        self.assertEqual(self.home.execute("SELECT status FROM evaluations").fetchone(), ("nu_corespunde",))
        with mock.patch.object(sync, "pc_name", return_value="work"):
            sync.sync(self.work, self.folder)
        self.assertEqual(self.work.execute("SELECT status FROM evaluations").fetchone(), ("nu_corespunde",))

    def test_decision_for_tender_not_evaluated_here_survives_evaluation(self):
        self.answer_all(self.work)
        with mock.patch.object(sync, "pc_name", return_value="work"):
            sync.export(self.work, sync.own_file(self.folder))
        fresh = connect(Path(self.tmp.name) / "fresh.sqlite")
        try:
            sync.apply(fresh, sync.read_all(self.folder))
            evaluate_one(fresh)                                   # оценка потом не перетирает решение человека
            self.assertEqual(fresh.execute("SELECT status, source FROM evaluations").fetchone(),
                             ("corespunde", "human"))
            self.assertEqual([q["kind"] for q in open_questions(fresh)], [])
        finally:
            fresh.close()

    def test_deleted_card_is_deleted_on_other_pc(self):
        self.answer_all(self.work)
        with mock.patch.object(sync, "pc_name", return_value="work"):
            sync.export(self.work, sync.own_file(self.folder))
        with mock.patch.object(sync, "pc_name", return_value="home"):
            sync.sync(self.home, self.folder)
        self.assertEqual(self.home.execute("SELECT source FROM products").fetchone(), ("human",))
        # на работе карточку удалили позже
        self.work.execute("UPDATE products SET source = 'deleted', confirmed_at = '2999-01-01T00:00:00'")
        self.work.commit()
        with mock.patch.object(sync, "pc_name", return_value="work"):
            sync.export(self.work, sync.own_file(self.folder))
        with mock.patch.object(sync, "pc_name", return_value="home"):
            sync.sync(self.home, self.folder)
        self.assertEqual(self.home.execute("SELECT source FROM products").fetchone(), ("deleted",))

    def test_broken_line_is_skipped(self):
        self.folder.mkdir(parents=True)
        (self.folder / "x.jsonl").write_text('{"t": "type", "word": "DVD-R", "medical": false, "at": "2026"}\n'
                                             'не json\n', encoding="utf-8")
        self.assertEqual(len(sync.read_all(self.folder)), 1)


if __name__ == "__main__":
    unittest.main()
