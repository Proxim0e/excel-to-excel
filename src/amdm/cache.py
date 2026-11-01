"""
Локальная копия реестра AMDM в SQLite.

Растёт по мере проверок: каждая найденная на сайте запись сохраняется и в следующий раз
не запрашивается. Отдельно хранятся номера, которые на сайте НЕ нашлись, с датой проверки:
копия отстаёт до недели, поэтому «не найден» перепроверяется, если с проверки прошло больше
NOT_FOUND_TTL_DAYS дней.

Это первый кирпич будущей общей базы проекта (см. концепцию: SQLite -> Postgres).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional

from src.amdm.models import RegistryRecord

NOT_FOUND_TTL_DAYS = 7
FIELDS = ["dm", "name", "trade_name", "model", "catalog", "country", "producer",
          "representative", "order", "order_date", "customs_code"]

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS amdm_record (
    {", ".join(f'"{f}" TEXT' + (' PRIMARY KEY' if f == 'dm' else '') for f in FIELDS)},
    fetched_at TEXT NOT NULL,
    source TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS amdm_not_found (
    dm TEXT PRIMARY KEY,
    checked_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS amdm_record_order_date ON amdm_record(order_date);
"""


class RegistryCache:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path))
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    # --- записи ---------------------------------------------------------------
    def get(self, dm: str) -> Optional[RegistryRecord]:
        cols = ", ".join(f'"{f}"' for f in FIELDS)
        row = self.db.execute(f"SELECT {cols} FROM amdm_record WHERE dm = ?", (dm,)).fetchone()
        return RegistryRecord(**dict(zip(FIELDS, row))) if row else None

    def put(self, record: RegistryRecord, source: str) -> None:
        values = [getattr(record, f) for f in FIELDS]
        cols = ", ".join(f'"{f}"' for f in FIELDS)
        marks = ", ".join("?" for _ in FIELDS)
        self.db.execute(
            f"INSERT OR REPLACE INTO amdm_record ({cols}, fetched_at, source) VALUES ({marks}, ?, ?)",
            values + [datetime.now().isoformat(timespec="seconds"), source])
        self.db.execute("DELETE FROM amdm_not_found WHERE dm = ?", (record.dm,))
        self.db.commit()

    def put_many(self, records: Iterable[RegistryRecord], source: str) -> int:
        n = 0
        for r in records:
            self.put(r, source)
            n += 1
        return n

    def count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM amdm_record").fetchone()[0]

    def latest_order_date(self) -> Optional[str]:
        """Самая свежая дата приказа в копии — с неё начинается еженедельная догрузка."""
        return self.db.execute("SELECT MAX(order_date) FROM amdm_record").fetchone()[0]

    # --- «не найден» ----------------------------------------------------------
    def mark_not_found(self, dm: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO amdm_not_found (dm, checked_at) VALUES (?, ?)",
                        (dm, datetime.now().isoformat(timespec="seconds")))
        self.db.commit()

    def not_found_fresh(self, dm: str, now: Optional[datetime] = None) -> bool:
        """True, если номер недавно искали и не нашли — повторно на сайт не идём."""
        row = self.db.execute("SELECT checked_at FROM amdm_not_found WHERE dm = ?", (dm,)).fetchone()
        if not row:
            return False
        checked = datetime.fromisoformat(row[0])
        return (now or datetime.now()) - checked < timedelta(days=NOT_FOUND_TTL_DAYS)
