"""
История для оценки: подтверждённая карточка товара и прошлые решения по модели.

Высокая уверенность требует, чтобы модель уже была принята человеком раньше - на похожем запросе.
Похожесть запросов (lot_profiles) - этап C; пока прошлым решением считается любое принятие этой модели.
"""
import json
import sqlite3
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class HistoryInfo:
    card: Optional[dict] = None                     # карточка товара (products), если есть
    outcomes: List[dict] = field(default_factory=list)  # прошлые решения по модели в других тендерах

    @property
    def card_confirmed(self) -> bool:
        return bool(self.card and self.card.get("source") == "human")

    @property
    def accepted_before(self) -> bool:
        """Человек раньше принимал эту модель (не автоматически)."""
        return any(o["status"] == "corespunde" and o["source"] == "human" for o in self.outcomes)


def _rows(conn: sqlite3.Connection, sql: str, params) -> List[dict]:
    cur = conn.execute(sql, params)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


def lookup(conn: sqlite3.Connection, model_key: Optional[str], tender_id: str) -> HistoryInfo:
    """Карточка модели и её решения в других тендерах."""
    if not model_key:
        return HistoryInfo()
    cards = _rows(conn, "SELECT * FROM products WHERE model_key = ?", (model_key,))
    card = cards[0] if cards else None
    if card and card.get("params"):
        card["params"] = json.loads(card["params"])
    outcomes = _rows(conn, "SELECT tender_id, lot_no, participant, status, reason, source FROM evaluations "
                           "WHERE model_key = ? AND tender_id <> ? ORDER BY decided_at DESC",
                     (model_key, tender_id))
    return HistoryInfo(card, outcomes)
