"""
Перевод карточек со старых ключей (номер DM «DM:…», «M:производитель|модель») на новый: производитель + коды
модели + типоразмер («P:agfa|dt2b|35x43»). Номер DM меняется со временем - он остаётся в списке номеров
карточки (поле dm) и по любому из них карточка находится (src/evaluate/history.py:resolve_key).

Запускается сам при синхронизации ответов (окно, оценка): повторный запуск ничего не меняет. Карточки без
модели (известен только DM) остаются на ключе «DM:…».
"""
import json
import logging
import sqlite3
from typing import Dict

from src.evaluate.cards import rename_key
from src.history.cards import product_key, size_of

logger = logging.getLogger(__name__)


def _lot_title(kb: sqlite3.Connection, key: str) -> str:
    row = kb.execute("SELECT payload FROM questions WHERE kind = 'product_card' AND model_key = ? ORDER BY id DESC "
                     "LIMIT 1", (key,)).fetchone()
    try:
        return (json.loads(row[0]) or {}).get("lot_title") or "" if row else ""
    except (TypeError, ValueError):
        return ""


def migrate(kb: sqlite3.Connection) -> Dict[str, int]:
    stats = {"renamed": 0, "kept": 0}
    rows = kb.execute("SELECT model_key, producer, model, codes, name FROM products WHERE source != 'deleted' "
                      "AND model_key NOT LIKE 'P:%'").fetchall()
    for old, producer, model, codes, name in rows:
        title = _lot_title(kb, old)
        new = product_key(producer, codes or model, size_of(title) or size_of(name))
        if not new:
            stats["kept"] += 1
            continue
        rename_key(kb, old, new)
        for qid, payload in kb.execute("SELECT id, payload FROM questions WHERE kind = 'product_card' AND model_key = ?",
                                       (new,)).fetchall():
            data = json.loads(payload or "{}")
            if isinstance(data.get("card"), dict):
                data["card"]["model_key"] = new
                kb.execute("UPDATE questions SET payload = ? WHERE id = ?", (json.dumps(data, ensure_ascii=False), qid))
        stats["renamed"] += 1
    kb.commit()
    if stats["renamed"]:
        logger.info(f"[SYNC] Карточки на новом ключе (производитель + модель + размер): {stats['renamed']}; "
                    f"только с номером DM: {stats['kept']}")
    return stats
