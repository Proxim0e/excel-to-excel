"""
Ответы человека между компьютерами - через git.

База знаний `data/knowledge.sqlite` в git не входит (большая, у каждого ПК своя). Ответы человека - карточки
товара (products, source = human), решения по офертам (evaluations, source = human) и «медицинское ли изделие»
(product_types) - выгружаются в текстовый файл `knowledge/answers/<имя ПК>.jsonl`, который коммитится вместе
с кодом. Каждый ПК пишет только свой файл - конфликтов слияния в git нет. Загрузка читает файлы всех ПК и
подмешивает ответы в свою базу: из двух ответов на одно и то же побеждает более поздний; открытые вопросы,
на которые ответили на другом ПК, закрываются.

Окно делает это само: загрузка - при открытии, выгрузка - после каждого ответа. Вручную:
    python -m src.history sync
"""
import json
import logging
import re
import socket
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from src.config import BASE_DIR

logger = logging.getLogger(__name__)

ANSWERS_DIR = Path(BASE_DIR) / "knowledge" / "answers"
PRODUCT_COLS = ("model_key", "producer", "model", "codes", "dm", "name", "params", "compatible_with", "iso",
                "agents", "winners")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def pc_name() -> str:
    return re.sub(r"[^\w\-]+", "_", socket.gethostname()).strip("_").lower() or "pc"


def own_file(folder: Path = ANSWERS_DIR) -> Path:
    return Path(folder) / f"{pc_name()}.jsonl"


# --- выгрузка ----------------------------------------------------------------------------------------------------
def collect(kb: sqlite3.Connection) -> List[Dict]:
    """Все ответы человека из базы: записи с ключом t (product / offer / type) и временем ответа at."""
    out: List[Dict] = []
    cols = ", ".join(PRODUCT_COLS)
    for row in kb.execute(f"SELECT {cols}, source, COALESCE(confirmed_at, updated_at) FROM products "
                          f"WHERE source IN ('human', 'deleted')"):
        if row[-2] == "deleted":      # удалена во вкладке «База карточек» - метка, чтобы другой ПК тоже удалил
            out.append({"t": "product", "model_key": row[0], "deleted": True, "at": row[-1]})
            continue
        rec = {"t": "product", **dict(zip(PRODUCT_COLS, row[:-2])), "at": row[-1]}
        rec["params"] = json.loads(rec["params"] or "{}")
        out.append(rec)
    comments = {(r[0], r[1], r[2]): json.loads(r[3] or "{}").get("comment", "")
                for r in kb.execute("SELECT tender_id, lot_no, participant, answer FROM questions "
                                    "WHERE kind = 'offer_status' AND state = 'answered'")}
    for t, lot, part, mk, status, reason, at in kb.execute(
            "SELECT tender_id, lot_no, participant, model_key, status, reason, decided_at FROM evaluations "
            "WHERE source = 'human'"):
        out.append({"t": "offer", "tender_id": t, "lot_no": lot, "participant": part, "model_key": mk,
                    "status": status, "reason": reason, "comment": comments.get((t, lot, part), ""), "at": at})
    for word, medical, comment, at in kb.execute("SELECT word, medical, comment, decided_at FROM product_types"):
        out.append({"t": "type", "word": word, "medical": bool(medical), "comment": comment or "", "at": at})
    return out


def _key(rec: Dict):
    if rec["t"] == "product":
        return ("product", rec["model_key"])
    if rec["t"] == "offer":
        return ("offer", rec["tender_id"], int(rec["lot_no"]), rec["participant"])
    return ("type", rec["word"])


def export(kb: sqlite3.Connection, path: Optional[Path] = None) -> int:
    """Пишет ответы этого ПК в knowledge/answers/<ПК>.jsonl: строка на ответ, по порядку ключей (стабильный diff)."""
    path = Path(path or own_file())
    records = sorted(collect(kb), key=lambda r: [str(k) for k in _key(r)])
    text = "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in records)
    if path.is_file() and path.read_text(encoding="utf-8") == text:
        return len(records)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    return len(records)


# --- загрузка ----------------------------------------------------------------------------------------------------
def read_all(folder: Path = ANSWERS_DIR) -> List[Dict]:
    """Ответы из файлов всех ПК; на один ключ - самый поздний."""
    best: Dict = {}
    for f in sorted(Path(folder).glob("*.jsonl")):
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
                key = _key(rec)
            except (ValueError, KeyError) as e:
                logger.warning(f"[SYNC] {f.name}:{n} пропущена строка: {e}")
                continue
            if key not in best or (rec.get("at") or "") > (best[key].get("at") or ""):
                best[key] = rec
    return list(best.values())


def _close(kb, kind: str, where: str, params, answer: Dict, at: str) -> int:
    cur = kb.execute(f"UPDATE questions SET state = 'answered', answer = ?, answered_at = ? "
                     f"WHERE kind = ? AND state = 'open' AND {where}",
                     (json.dumps({**answer, "from": "sync"}, ensure_ascii=False), at, kind, *params))
    return cur.rowcount


def apply(kb: sqlite3.Connection, records: Iterable[Dict]) -> Dict[str, int]:
    """Подмешивает ответы в базу. Свой ответ новее - остаётся свой. Возвращает {тип: сколько изменено}."""
    stats = {"product": 0, "offer": 0, "type": 0, "closed": 0}
    for rec in records:
        at = rec.get("at") or _now()
        if rec["t"] == "product":
            row = kb.execute("SELECT source, COALESCE(confirmed_at, updated_at) FROM products WHERE model_key = ?",
                             (rec["model_key"],)).fetchone()
            if rec.get("deleted"):
                if not row or row[0] == "deleted" or (row[1] or "") >= at:
                    continue
                kb.execute("UPDATE products SET source = 'deleted', confirmed_at = ?, updated_at = ? "
                           "WHERE model_key = ?", (at, at, rec["model_key"]))
                stats["product"] += 1
                continue
            if row and row[0] in ("human", "deleted") and (row[1] or "") >= at:
                continue
            vals = [rec.get(c) for c in PRODUCT_COLS]
            vals[PRODUCT_COLS.index("params")] = json.dumps(rec.get("params") or {}, ensure_ascii=False)
            kb.execute(f"INSERT OR REPLACE INTO products ({', '.join(PRODUCT_COLS)}, source, confirmed_at, "
                       f"updated_at) VALUES ({', '.join('?' * len(PRODUCT_COLS))}, 'human', ?, ?)", (*vals, at, at))
            stats["product"] += 1
            stats["closed"] += _close(kb, "product_card", "model_key = ?", (rec["model_key"],),
                                      {"card": {c: rec.get(c) for c in PRODUCT_COLS}}, at)
        elif rec["t"] == "offer":
            key = (rec["tender_id"], int(rec["lot_no"]), rec["participant"])
            row = kb.execute("SELECT source, decided_at FROM evaluations WHERE tender_id = ? AND lot_no = ? "
                             "AND participant = ?", key).fetchone()
            if row and row[0] == "human" and (row[1] or "") >= at:
                continue
            if row:
                kb.execute("UPDATE evaluations SET status = ?, reason = ?, source = 'human', confidence = 1, "
                           "decided_at = ? WHERE tender_id = ? AND lot_no = ? AND participant = ?",
                           (rec["status"], rec.get("reason"), at, *key))
            else:   # этот тендер здесь ещё не оценивали - решение человека ждёт оценку (движок его не тронет)
                kb.execute("INSERT INTO evaluations (tender_id, lot_no, participant, model_key, status, reason, "
                           "confidence, source, findings, decided_at) VALUES (?, ?, ?, ?, ?, ?, 1, 'human', NULL, ?)",
                           (*key, rec.get("model_key"), rec["status"], rec.get("reason"), at))
            stats["offer"] += 1
            stats["closed"] += _close(kb, "offer_status", "tender_id = ? AND lot_no = ? AND participant = ?", key,
                                      {"status": rec["status"], "reason": rec.get("reason"),
                                       "comment": rec.get("comment", "")}, at)
        elif rec["t"] == "type":
            row = kb.execute("SELECT decided_at FROM product_types WHERE word = ?", (rec["word"],)).fetchone()
            if row and (row[0] or "") >= at:
                continue
            kb.execute("INSERT OR REPLACE INTO product_types (word, medical, comment, decided_at) VALUES (?, ?, ?, ?)",
                       (rec["word"], int(bool(rec["medical"])), rec.get("comment", ""), at))
            stats["type"] += 1
            stats["closed"] += _close(kb, "medical_type", "model_key = ?", (rec["word"],),
                                      {"medical": bool(rec["medical"]), "comment": rec.get("comment", "")}, at)
    kb.commit()
    return stats


def sync(kb: sqlite3.Connection, folder: Path = ANSWERS_DIR) -> Dict[str, int]:
    """Загрузить ответы всех ПК, затем выгрузить свои (с учётом загруженных)."""
    stats = apply(kb, read_all(folder))
    stats["exported"] = export(kb, own_file(folder))
    changed = stats["product"] + stats["offer"] + stats["type"]
    if changed:
        logger.info(f"[SYNC] С других ПК: карточек {stats['product']}, решений по офертам {stats['offer']}, "
                    f"типов изделий {stats['type']}; закрыто вопросов {stats['closed']}")
    logger.info(f"[SYNC] Ответов человека: {stats['exported']} -> {own_file(folder).name} (закоммитить вместе с кодом)")
    return stats
