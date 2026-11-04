"""Ключ карточки: производитель + модель + типоразмер; DM - список номеров для поиска (DM меняется со временем)."""
import json
import tempfile
import unittest
from pathlib import Path

from src.evaluate.cards import save_card
from src.evaluate.engine import offer_model_key
from src.evaluate.history import resolve_key
from src.history.cards import card_key, card_title, size_of
from src.history.db import connect
from src.history.rekey import migrate


class CardKeyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.kb = connect(Path(self.tmp.name) / "kb.sqlite")

    def tearDown(self):
        self.kb.close()
        self.tmp.cleanup()

    def test_size_and_key(self):
        self.assertEqual(size_of("Lot nr. 59 Film radiologic 35 x 43 cm"), "35x43")
        self.assertEqual(size_of("Ac 18G x 90 mm"), "18g 90mm")
        self.assertEqual(size_of("Electrod ECG"), "")
        self.assertEqual(card_key("Agfa NV", "Drystar DT2B", "DM1", "Film radiologic 20x25"), "P:agfa|dt2b|20x25")
        self.assertNotEqual(card_key("Agfa NV", "DT2B", "DM1", "Film 20x25"), card_key("Agfa NV", "DT2B", "DM1", "Film 35x43"))
        self.assertEqual(card_key("", "", "DM2,DM1"), "DM:DM1+DM2")          # без модели - по DM
        self.assertEqual(card_title({"model_key": "P:agfa|dt2b|35x43", "name": "Film radiologic", "model": "Drystar DT2B",
                                     "codes": "DT2B", "producer": "Agfa NV"}),
                         "Film radiologic 35x43 — Drystar DT2B — Agfa NV")

    def test_new_dm_finds_same_card(self):
        save_card(self.kb, {"model_key": "P:agfa|dt2b|35x43", "producer": "Agfa", "model": "DT2B",
                            "dm": "DM000111, DM000222", "name": "Film 35x43"})
        offer = {"producer": ["Agfa NV"], "model": ["Drystar DT2B"], "dm": ["DM000222"], "item_name": []}
        # другой производитель записан иначе / размер тот же - по DM находится та же карточка
        self.assertEqual(offer_model_key(offer, "Film radiologic 35x43 cm", self.kb), "P:agfa|dt2b|35x43")
        odd = {"producer": ["Agfa HealthCare"], "model": ["DT 2 B"], "dm": ["DM000111"], "item_name": []}
        self.assertEqual(offer_model_key(odd, "Film 35x43", self.kb), "P:agfa|dt2b|35x43")
        # тот же DM, другой размер - другая карточка
        self.assertEqual(resolve_key(self.kb, "P:agfa|dt2b|20x25", ["DM000111"], "20x25"), "P:agfa|dt2b|20x25")

    def test_migrate_old_keys(self):
        now = "2026-11-04T10:00:00"
        self.kb.execute("INSERT INTO products (model_key, producer, model, dm, name, source, confirmed_at, updated_at) "
                        "VALUES ('DM:DM1', 'Fiab', 'F4141', 'DM1', 'Electrod', 'human', ?, ?)", (now, now))
        self.kb.execute("INSERT INTO products (model_key, producer, model, dm, source, updated_at) "
                        "VALUES ('DM:DM9', NULL, NULL, 'DM9', 'prefill', ?)", (now,))
        self.kb.execute("INSERT INTO products (model_key, producer, model, source, updated_at) "
                        "VALUES ('M:agfa|dt2b', 'Agfa', 'DT2B', 'prefill', ?)", (now,))
        self.kb.execute("INSERT INTO questions (kind, tender_id, lot_no, participant, model_key, payload, state, created_at)"
                        " VALUES ('product_card', 'T1', 0, '', 'M:agfa|dt2b', ?, 'open', ?)",
                        (json.dumps({"lot_title": "Lot nr. 5 Film 35x43", "card": {"model_key": "M:agfa|dt2b"}}), now))
        self.kb.execute("INSERT INTO evaluations (tender_id, lot_no, participant, model_key, status, source, decided_at) "
                        "VALUES ('T1', 5, 'X', 'M:agfa|dt2b', 'clarificare', 'pending', ?)", (now,))
        self.assertEqual(migrate(self.kb), {"renamed": 2, "kept": 1})
        keys = dict(self.kb.execute("SELECT model_key, source FROM products"))
        self.assertEqual(keys, {"P:fiab|f4141": "human", "DM:DM1": "deleted", "DM:DM9": "prefill",
                                "P:agfa|dt2b|35x43": "prefill"})
        self.assertEqual(self.kb.execute("SELECT model_key FROM evaluations").fetchone(), ("P:agfa|dt2b|35x43",))
        q = self.kb.execute("SELECT model_key, payload FROM questions").fetchone()
        self.assertEqual(q[0], "P:agfa|dt2b|35x43")
        self.assertEqual(json.loads(q[1])["card"]["model_key"], "P:agfa|dt2b|35x43")
        self.assertEqual(migrate(self.kb)["renamed"], 0)                   # повторно - ничего


if __name__ == "__main__":
    unittest.main()
