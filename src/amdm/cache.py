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
-- Поиск по колонке реестра (каталожный номер / модель), когда в оферте нет номера DM:
-- запрос -> номера DM первой страницы результата (пусто - не найдено)
-- Тип изделия (первое слово названия лота): сколько записей реестра с таким названием - медицинский ли тип
CREATE TABLE IF NOT EXISTS amdm_type (
    word TEXT PRIMARY KEY,
    medical INTEGER NOT NULL,
    total INTEGER NOT NULL,
    checked_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS amdm_search (
    field TEXT NOT NULL,
    query TEXT NOT NULL,
    dms TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    PRIMARY KEY (field, query)
);
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

    def fetched_from_site(self, dm: str) -> Optional[str]:
        """Когда запись пришла с сайта (ISO), None - не с сайта или нет записи."""
        row = self.db.execute("SELECT fetched_at FROM amdm_record WHERE dm = ? AND source = 'site'", (dm,)).fetchone()
        return row[0] if row else None

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

    # --- поиск по названию / коду ---------------------------------------------
    def get_search(self, field: str, query: str, now: Optional[datetime] = None) -> Optional[list]:
        """Записи, найденные этим запросом недавно (None - запроса не было или он устарел)."""
        row = self.db.execute("SELECT dms, checked_at FROM amdm_search WHERE field = ? AND query = ?",
                              (field, query)).fetchone()
        if not row or (now or datetime.now()) - datetime.fromisoformat(row[1]) >= timedelta(days=NOT_FOUND_TTL_DAYS):
            return None
        return [r for r in (self.get(dm) for dm in row[0].split(",") if dm) if r]

    def put_search(self, field: str, query: str, records: Iterable[RegistryRecord]) -> None:
        records = list(records)
        for r in records:
            self.put(r, source="site-search")
        self.db.execute("INSERT OR REPLACE INTO amdm_search (field, query, dms, checked_at) VALUES (?, ?, ?, ?)",
                        (field, query, ",".join(r.dm for r in records), datetime.now().isoformat(timespec="seconds")))
        self.db.commit()

    # --- медицинский ли тип изделия ------------------------------------------
    TYPE_TTL_DAYS = 30

    def _type_row(self, word: str, now: Optional[datetime] = None):
        row = self.db.execute("SELECT medical, checked_at FROM amdm_type WHERE word = ?", (word,)).fetchone()
        if not row or (now or datetime.now()) - datetime.fromisoformat(row[1]) >= timedelta(days=self.TYPE_TTL_DAYS):
            return None
        return row

    def type_checked(self, word: str) -> bool:
        return self._type_row(word) is not None

    def get_type(self, word: str) -> Optional[bool]:
        """True - медицинский тип, False - в реестре таких нет, None - не проверяли или нельзя сказать."""
        row = self._type_row(word)
        return None if row is None or row[0] < 0 else bool(row[0])

    def put_type(self, word: str, medical: Optional[bool], total: int) -> None:
        value = -1 if medical is None else int(medical)
        self.db.execute("INSERT OR REPLACE INTO amdm_type (word, medical, total, checked_at) VALUES (?, ?, ?, ?)",
                        (word, value, total, datetime.now().isoformat(timespec="seconds")))
        self.db.commit()
