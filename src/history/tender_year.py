"""
Год тендера - по дате публикации на achizitii.md («Perioada de actualizare de la 04.08.2025 …»).

Номер тендера есть в имени рабочего Excel; год нужен карточке товара («этот год / прошлый год»). Ответ сайта
хранится в базе знаний (таблица tenders) - сайт спрашивается один раз на тендер. Без сайта - год из папки
«+LP ADM 2026» минус 1 (план закупок на 2026 разыгрывается в 2025), иначе год файла.
"""
import logging
import re
import sqlite3
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

ACHIZITII_URL = "https://achizitii.md/ro/public/tender/{}/"
DATE_RE = re.compile(r"(\d\d)\.(\d\d)\.(20\d\d)")

SCHEMA = """CREATE TABLE IF NOT EXISTS tenders (
    tender_id  TEXT PRIMARY KEY,
    published  TEXT,              -- дата публикации ГГГГ-ММ-ДД (achizitii.md)
    year       INTEGER,
    source     TEXT,              -- site / folder / file
    checked_at TEXT NOT NULL
)"""


def parse_published(html: str) -> Optional[str]:
    """Первая дата периода публикации на странице тендера (ГГГГ-ММ-ДД)."""
    text = re.sub(r"<[^>]+>", " ", html or "")
    m = re.search(r"Perioada de actualizare de la\s+(\d\d)\.(\d\d)\.(20\d\d)", text) or DATE_RE.search(text)
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else None


def fetch_published(tender_id: str, timeout: int = 30) -> Optional[str]:
    import requests
    from src.tender_docs import UA
    r = requests.get(ACHIZITII_URL.format(tender_id), headers=UA, timeout=timeout)
    r.raise_for_status()
    return parse_published(r.text)


def fallback_year(source_file: Optional[str], mtime: Optional[str]) -> Optional[int]:
    m = re.search(r"ADM\s*(20\d\d)", source_file or "", re.IGNORECASE)
    if m:
        return int(m.group(1)) - 1
    return int(mtime[:4]) if mtime and mtime[:4].isdigit() else None


def tender_year(kb: sqlite3.Connection, tender_id: str, source_file: Optional[str] = None,
                mtime: Optional[str] = None, online: bool = True) -> Optional[int]:
    """Год тендера: из базы, иначе с сайта (запоминается), иначе по папке/файлу (не запоминается -
    при следующем запуске сайт спросят снова)."""
    kb.execute(SCHEMA)
    row = kb.execute("SELECT year FROM tenders WHERE tender_id = ?", (tender_id,)).fetchone()
    if row and row[0]:
        return int(row[0])
    if online and tender_id:
        try:
            published = fetch_published(tender_id)
        except Exception as e:  # сайт недоступен - не мешать работе
            logger.warning(f"[YEAR] {tender_id}: achizitii.md не ответил ({e})")
            published = None
        if published:
            kb.execute("INSERT OR REPLACE INTO tenders (tender_id, published, year, source, checked_at) "
                       "VALUES (?, ?, ?, 'site', ?)", (tender_id, published, int(published[:4]),
                                                       datetime.now().isoformat(timespec="seconds")))
            kb.commit()
            logger.info(f"[YEAR] {tender_id}: опубликован {published}")
            return int(published[:4])
    return fallback_year(source_file, mtime)


def known_years(kb: sqlite3.Connection) -> dict:
    kb.execute(SCHEMA)
    return {t: y for t, y in kb.execute("SELECT tender_id, year FROM tenders WHERE year IS NOT NULL")}
