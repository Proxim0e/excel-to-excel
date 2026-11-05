"""
Ответы человека на вопросы из окна приложения.

offer_status  - решение по оферте: evaluations.source = human (движок его больше не пересчитывает);
product_card  - карточка товара подтверждена (products.source = human): дальше эта модель может
                получать автоматическое решение при полной уверенности.
medical_type  - медицинское ли изделие этого типа (product_types): реестр AMDM не дал ответа; решение
                действует для всех тендеров, проверка AMDM и оценка берут его вместо реестра.
"""
import json
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional

from src.evaluate.cards import save_card
from src.evaluate.confidence import HUMAN, STATUSES


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


SUPERSEDED = "superseded"       # вопрос закрыт без ответа: есть более новый такой же или ответ уже есть


def tidy_questions(kb: sqlite3.Connection) -> int:
    """Убирает дубли и уже решённые вопросы (state = superseded):
    - по оферте (тендер, лот, участник) - открытым остаётся самый новый (старые ключи модели DM: / M: и т.п.);
    - по карточке модели - один на модель для всех тендеров (самый новый);
    - карточка уже подтверждена человеком / по оферте уже есть решение человека.
    Возвращает число закрытых вопросов."""
    sqls = [
        "UPDATE questions SET state = ?, answered_at = ? WHERE state = 'open' AND kind = 'offer_status' AND EXISTS ("
        "SELECT 1 FROM questions q WHERE q.state = 'open' AND q.kind = 'offer_status' AND q.id > questions.id "
        "AND q.tender_id = questions.tender_id AND q.lot_no = questions.lot_no "
        "AND q.participant = questions.participant)",
        "UPDATE questions SET state = ?, answered_at = ? WHERE state = 'open' AND kind = 'product_card' AND EXISTS ("
        "SELECT 1 FROM questions q WHERE q.state = 'open' AND q.kind = 'product_card' AND q.id > questions.id "
        "AND q.model_key = questions.model_key)",
        "UPDATE questions SET state = ?, answered_at = ? WHERE state = 'open' AND kind = 'product_card' AND EXISTS ("
        "SELECT 1 FROM products p WHERE p.model_key = questions.model_key AND p.source = 'human')",
        "UPDATE questions SET state = ?, answered_at = ? WHERE state = 'open' AND kind = 'offer_status' AND EXISTS ("
        "SELECT 1 FROM evaluations e WHERE e.tender_id = questions.tender_id AND e.lot_no = questions.lot_no "
        "AND e.participant = questions.participant AND e.source = 'human')",
    ]
    closed = 0
    now = _now()
    for sql in sqls:
        closed += kb.execute(sql, (SUPERSEDED, now)).rowcount
    kb.commit()
    return closed


def open_questions(kb: sqlite3.Connection, tender_id: Optional[str] = None) -> List[Dict]:
    sql ="SELECT id, kind, tender_id, lot_no, participant, model_key, payload FROM questions WHERE state = 'open'"
    params = ()
    if tender_id:
        sql += " AND tender_id = ?"
        params = (tender_id,)
    # карточки товара - первыми: от них зависит уверенность по офертам этой модели
    sql += " ORDER BY kind = 'offer_status', lot_no, id"
    rows = kb.execute(sql, params).fetchall()
    return [{"id": r[0], "kind": r[1], "tender_id": r[2], "lot_no": r[3], "participant": r[4], "model_key": r[5],
             "payload": json.loads(r[6] or "{}")} for r in rows]


def answer_offer(kb: sqlite3.Connection, question_id: int, status: str, reason: Optional[str] = None,
                 comment: str = "") -> None:
    if status not in STATUSES:
        raise ValueError(f"неизвестный статус: {status}")
    q = kb.execute("SELECT tender_id, lot_no, participant FROM questions WHERE id = ?", (question_id,)).fetchone()
    if not q:
        raise KeyError(question_id)
    kb.execute("UPDATE evaluations SET status = ?, reason = ?, source = ?, confidence = 1, decided_at = ? "
               "WHERE tender_id = ? AND lot_no = ? AND participant = ?", (status, reason, HUMAN, _now(), *q))
    _close(kb, question_id, {"status": status, "reason": reason, "comment": comment})
    tidy_questions(kb)


def answer_card(kb: sqlite3.Connection, question_id: int, card: Dict) -> None:
    save_card(kb, card, source="human")
    _close(kb, question_id, {"card": card})
    tidy_questions(kb)          # вопросы по этой модели из других тендеров больше не нужны


def human_types(kb: sqlite3.Connection) -> Dict[str, bool]:
    """Решения человека: тип изделия -> медицинский ли."""
    return {w: bool(m) for w, m in kb.execute("SELECT word, medical FROM product_types")}


def ask_medical_type(kb: sqlite3.Connection, word: str, payload: Dict) -> bool:
    """Вопрос «медицинское ли изделие «word»?» (один на тип, для всех тендеров). True - создан новый."""
    cur = kb.execute("INSERT OR IGNORE INTO questions (kind, tender_id, lot_no, participant, model_key, payload, "
                     "state, created_at) VALUES ('medical_type', '', 0, '', ?, ?, 'open', ?)",
                     (word, json.dumps(payload, ensure_ascii=False, default=str), _now()))
    kb.commit()
    return bool(cur.rowcount)


def answer_medical(kb: sqlite3.Connection, question_id: int, medical: bool, comment: str = "") -> None:
    q = kb.execute("SELECT model_key FROM questions WHERE id = ? AND kind = 'medical_type'", (question_id,)).fetchone()
    if not q:
        raise KeyError(question_id)
    kb.execute("INSERT OR REPLACE INTO product_types (word, medical, comment, decided_at) VALUES (?, ?, ?, ?)",
               (q[0], int(medical), comment, _now()))
    _close(kb, question_id, {"medical": medical, "comment": comment})


def _close(kb: sqlite3.Connection, question_id: int, answer: Dict) -> None:
    kb.execute("UPDATE questions SET state = 'answered', answer = ?, answered_at = ? WHERE id = ?",
               (json.dumps(answer, ensure_ascii=False), _now(), question_id))
    kb.commit()
