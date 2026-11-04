"""
История для оценки: подтверждённая карточка товара, прошлые решения по модели и похожие прошлые запросы.

Высокая уверенность требует, чтобы человек раньше принимал эту модель на похожем запросе: сходство слов
запроса (B11) и названия лота не ниже SIMILAR.
"""
import json
import re
import sqlite3
from dataclasses import dataclass, field
from typing import List, Optional

from src.classify import normalize

SIMILAR = 0.5          # доля общих слов запроса (Жаккар), с которой запросы считаются похожими


def _words(text: str) -> set:
    return {w[:6] for w in re.findall(r"[a-z]{4,}", normalize(text or ""))}


def similarity(req_a: str, title_a: str, req_b: str, title_b: str) -> float:
    """Похожесть двух запросов: по требованию, если оно есть у обоих, иначе по названию лота."""
    a, b = _words(req_a), _words(req_b)
    if not (a and b):
        a, b = _words(title_a), _words(title_b)
    if not (a and b):
        return 0.0
    return len(a & b) / len(a | b)


@dataclass
class HistoryInfo:
    card: Optional[dict] = None                         # карточка товара (products), если есть
    outcomes: List[dict] = field(default_factory=list)  # решения по модели в других тендерах (+ similarity)
    similar_lots: List[dict] = field(default_factory=list)  # похожие прошлые запросы и что на них принимали

    @property
    def card_confirmed(self) -> bool:
        return bool(self.card and self.card.get("source") == "human")

    @property
    def accepted_before(self) -> bool:
        """Человек раньше принимал эту модель на похожем запросе."""
        return any(o["status"] == "corespunde" and o["source"] == "human" and o.get("similarity", 0) >= SIMILAR
                   for o in self.outcomes)


def _rows(conn: sqlite3.Connection, sql: str, params) -> List[dict]:
    cur = conn.execute(sql, params)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


def lookup(conn: sqlite3.Connection, model_key: Optional[str], tender_id: str, requirement: str = "",
           title: str = "") -> HistoryInfo:
    """Карточка модели, её решения в других тендерах и похожие прошлые запросы."""
    card = None
    outcomes: List[dict] = []
    if model_key:
        cards = _rows(conn, "SELECT * FROM products WHERE model_key = ?", (model_key,))
        card = cards[0] if cards else None
        if card and isinstance(card.get("params"), str) and card["params"]:
            card["params"] = json.loads(card["params"])
        outcomes = _rows(conn, """
            SELECT e.tender_id, e.lot_no, e.participant, e.status, e.reason, e.source, p.title, p.requirement
            FROM evaluations e LEFT JOIN lot_profiles p USING (tender_id, lot_no)
            WHERE e.model_key = ? AND e.tender_id <> ? ORDER BY e.decided_at DESC""", (model_key, tender_id))
        for o in outcomes:
            o["similarity"] = round(similarity(requirement, title, o.pop("requirement") or "", o["title"] or ""), 2)
    similar = []
    if requirement or title:
        for p in _rows(conn, "SELECT tender_id, lot_no, title, requirement FROM lot_profiles WHERE tender_id <> ?",
                       (tender_id,)):
            s = similarity(requirement, title, p["requirement"] or "", p["title"] or "")
            if s >= SIMILAR:
                p["similarity"] = round(s, 2)
                p["accepted"] = [r["participant"] + ": " + (r["model_key"] or "?") for r in _rows(
                    conn, "SELECT participant, model_key FROM evaluations WHERE tender_id = ? AND lot_no = ? "
                          "AND status = 'corespunde' AND source = 'human'", (p["tender_id"], p["lot_no"]))]
                p.pop("requirement", None)
                similar.append(p)
        similar.sort(key=lambda p: -p["similarity"])
    return HistoryInfo(card, outcomes, similar[:10])
