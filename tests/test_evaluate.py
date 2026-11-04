import sqlite3
import unittest
from unittest import mock

from src.amdm.models import CHECK, FAIL, OK as AMDM_OK, OfferRegistration
from src.anexa22.evidence import GREEN, YELLOW, OfferCheck
from src.evaluate import confidence as cf
from src.evaluate import engine
from src.evaluate.history import HistoryInfo, lookup
from src.history.db import SCHEMA

CARD = {"model_key": "DM:DM1", "source": "human"}
ACCEPTED = [{"status": "corespunde", "source": "human", "similarity": 0.9}]


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
        conn.execute("INSERT INTO lot_profiles VALUES ('T0', 5, 'Lot nr. 5 Electrod neutru', "
                     "'Electrod neutru pentru electrochirurgie, adult, unică folosință', NULL)")
        conn.execute("INSERT INTO evaluations VALUES ('T0', 5, 'A', 'DM:DM1', 'corespunde', NULL, 1, 'human', '{}', 'now')")
        conn.execute("INSERT INTO evaluations VALUES ('T1', 7, 'A', 'DM:DM1', 'corespunde', NULL, 1, 'human', '{}', 'now')")
        info = lookup(conn, "DM:DM1", "T1", "Electrod neutru pentru electrochirurgie adult de unică folosință")
        self.assertTrue(info.card_confirmed and info.accepted_before)
        self.assertEqual([o["tender_id"] for o in info.outcomes], ["T0"])  # текущий тендер не считается
        self.assertEqual(info.similar_lots[0]["accepted"], ["A: DM:DM1"])
        # та же модель, но на совсем другом запросе - не «принималась раньше»
        self.assertFalse(lookup(conn, "DM:DM1", "T1", "Hîrtie termică pentru ECG 112 mm").accepted_before)

    def test_human_answers_enable_auto_next_tender(self):
        from src.evaluate.answers import answer_card, answer_offer, open_questions
        conn = kb()
        self.lot.participants = [participant("A", 0.5)]
        self.lot.participants[0].offer["dm"] = ["DM1"]
        green = OfferCheck(GREEN, declarative="ok")
        with mock.patch.object(engine, "check_offer", return_value=green),                 mock.patch.object(engine, "amdm_check", return_value=[mock.Mock(reasons=[])]),                 mock.patch.object(engine, "overall_status", return_value=AMDM_OK):
            self.lot.participants[0].registration = OfferRegistration("lot", 5, "A", dm_numbers=["DM1"])
            [ev] = engine.evaluate_lot(self.lot, None, conn, {}, set())
            engine.save(conn, ev, self.lot)
            questions = open_questions(conn, "T1")
            self.assertEqual([q["kind"] for q in questions], ["product_card", "offer_status"])
            self.assertEqual(questions[0]["payload"]["card"]["dm"], "DM1")   # карточка предзаполнена
            answer_card(conn, questions[0]["id"], questions[0]["payload"]["card"])
            answer_offer(conn, questions[1]["id"], "corespunde")
            self.assertEqual(open_questions(conn), [])
            # следующий тендер, похожий запрос, та же модель - решение без вопроса
            lot2 = engine.Lot("T2", 3, "lot 3", "Lot nr. 3 Electrod", "Electrod 5 mm")
            lot2.participants = [participant("B", 0.7)]
            lot2.participants[0].offer["dm"] = ["DM1"]
            lot2.participants[0].registration = OfferRegistration("lot", 5, "A", dm_numbers=["DM1"])
            [ev2] = engine.evaluate_lot(lot2, None, conn, {}, set())
        self.assertEqual((ev2.status, ev2.source), (cf.CORESPUNDE, cf.AUTO))


if __name__ == "__main__":
    unittest.main()
