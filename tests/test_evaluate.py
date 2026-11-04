import sqlite3
import unittest
from unittest import mock

from src.amdm.models import CHECK, FAIL, OK as AMDM_OK
from src.anexa22.evidence import GREEN, YELLOW, OfferCheck
from src.evaluate import confidence as cf
from src.evaluate import engine
from src.evaluate.history import HistoryInfo, lookup
from src.history.db import SCHEMA

CARD = {"model_key": "DM:DM1", "source": "human"}
ACCEPTED = [{"status": "corespunde", "source": "human"}]


def kb():
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA)
    return conn


class DecideTests(unittest.TestCase):
    def test_auto_only_with_full_confidence(self):
        ok = OfferCheck(GREEN)
        d = cf.decide(ok, AMDM_OK, [], HistoryInfo(CARD, ACCEPTED))
        self.assertEqual((d.status, d.source), (cf.CORESPUNDE, cf.AUTO))
        # чего-то одного нет - вопрос человеку
        for check, amdm, hist in ((OfferCheck(YELLOW, missing=["model X"]), AMDM_OK, HistoryInfo(CARD, ACCEPTED)),
                                  (ok, CHECK, HistoryInfo(CARD, ACCEPTED)),
                                  (ok, AMDM_OK, HistoryInfo(None, ACCEPTED)),
                                  (ok, AMDM_OK, HistoryInfo(CARD, []))):
            self.assertEqual(cf.decide(check, amdm, ["x"], hist).source, cf.PENDING)

    def test_suggestions(self):
        self.assertEqual(cf.decide(OfferCheck(GREEN), FAIL, ["nu"], HistoryInfo()).status, cf.NU_CORESPUNDE)
        self.assertEqual(cf.decide(OfferCheck(GREEN), AMDM_OK, [], HistoryInfo()).status, cf.CORESPUNDE)
        d = cf.decide(OfferCheck(YELLOW, has_docs=False), None, [], HistoryInfo())
        self.assertEqual(d.status, cf.CLARIFICARE)
        self.assertTrue(d.ask_card)


def participant(name, ratio, offer=True):
    rec = {"participant": name, "model": ["F4141"], "producer": ["Fiab"], "dm": [], "spec_offered": ["spec"],
           "item_name": ["Electrod"]} if offer else None
    return engine.Participant(5, name, ratio, rec)


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.lot = engine.Lot("T1", 29, "lot 29", "Lot nr. 29 Electrod", "Electrod 5 mm")

    def test_all_participants_by_price(self):
        self.lot.participants = [participant("B", 0.9), participant("A", 0.5), participant("C", 1.4)]
        with mock.patch.object(engine, "check_offer", return_value=OfferCheck(GREEN, declarative="ok")):
            evs = engine.evaluate_lot(self.lot, None, kb(), {}, set())
        self.assertEqual([e.participant for e in evs], ["A", "B", "C"])   # все, по цене
        self.assertEqual(evs[2].status, cf.NU_CORESPUNDE)                # ≥ 130%
        self.assertEqual(evs[2].reason_code, "price_130")
        self.assertEqual(evs[0].source, cf.PENDING)                      # пустая база - вопрос

    def test_alternativa_is_blue_and_stops_participant(self):
        self.lot.participants = [participant("A", 0.5)]
        check = OfferCheck(GREEN, declarative="alternativa", conflicts=["30x30 mm (A22: 50x50 mm)"])
        with mock.patch.object(engine, "check_offer", return_value=check), \
                mock.patch.object(engine, "lookup") as history:
            [ev] = engine.evaluate_lot(self.lot, None, kb(), {}, set())
        self.assertEqual((ev.status, ev.source), (cf.ALTERNATIVA, cf.AUTO))
        self.assertIn("50x50", ev.reasons[0])
        history.assert_not_called()                                      # дальше не проверяли

    def test_save_keeps_human_and_dedupes_questions(self):
        conn = kb()
        ev = engine.Evaluation("T1", 29, "A", "DM:DM1", cf.CLARIFICARE, cf.PENDING, 0.3, ["x"], ask_card=True)
        self.assertEqual(engine.save(conn, ev, self.lot), "offer_status")
        self.assertIsNone(engine.save(conn, ev, self.lot))              # тот же вопрос второй раз не создаётся
        kinds = sorted(k for k, in conn.execute("SELECT kind FROM questions"))
        self.assertEqual(kinds, ["offer_status", "product_card"])
        conn.execute("UPDATE evaluations SET status = 'corespunde', source = 'human'")
        auto = engine.Evaluation("T1", 29, "A", "DM:DM1", cf.ALTERNATIVA, cf.AUTO, 0.9, [])
        engine.save(conn, auto, self.lot)
        self.assertEqual(conn.execute("SELECT status, source FROM evaluations").fetchone(), ("corespunde", "human"))

    def test_history_lookup(self):
        conn = kb()
        conn.execute("INSERT INTO products (model_key, source, updated_at) VALUES ('DM:DM1', 'human', 'now')")
        conn.execute("INSERT INTO evaluations VALUES ('T0', 5, 'A', 'DM:DM1', 'corespunde', NULL, 1, 'human', '{}', 'now')")
        conn.execute("INSERT INTO evaluations VALUES ('T1', 7, 'A', 'DM:DM1', 'corespunde', NULL, 1, 'human', '{}', 'now')")
        info = lookup(conn, "DM:DM1", "T1")
        self.assertTrue(info.card_confirmed and info.accepted_before)
        self.assertEqual([o["tender_id"] for o in info.outcomes], ["T0"])  # текущий тендер не считается


if __name__ == "__main__":
    unittest.main()
