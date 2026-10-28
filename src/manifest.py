"""
Манифест документов тендера (ADR-001): SQLite рядом со скачанными файлами.

documents      - один уникальный файл по содержимому (sha256);
document_refs  - каждое место, где файл лежит в тендере: лот, участник, исходное имя, ссылка, путь.

Пишется из download_worker при любом исходе задачи (скачан, скопирован, уже был на диске),
поэтому повторный запуск по уже скачанному тендеру просто заполняет манифест.
Тип документа (Anexa 22/23, ISO...) заполняет следующий шаг - классификатор.
"""
import hashlib
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

MANIFEST_NAME = "manifest.sqlite"
COMMIT_EVERY = 200  # коммит пачками: тысячи отдельных коммитов заметно медленнее

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    sha256          TEXT PRIMARY KEY,
    size            INTEGER NOT NULL,
    first_seen      TEXT NOT NULL,
    doc_type        TEXT,
    type_confidence REAL,
    type_source     TEXT,
    pages           INTEGER,
    has_text        INTEGER
);
CREATE TABLE IF NOT EXISTS document_refs (
    path          TEXT PRIMARY KEY,
    tender_id     TEXT NOT NULL,
    lot_no        INTEGER,
    lot_title     TEXT,
    participant   TEXT,
    original_name TEXT,
    url           TEXT NOT NULL,
    sha256        TEXT REFERENCES documents(sha256),
    status        TEXT NOT NULL,
    recorded_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_refs_sha ON document_refs(sha256);
CREATE INDEX IF NOT EXISTS idx_refs_lot ON document_refs(tender_id, lot_no);
"""


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Manifest:
    """Потокобезопасная запись манифеста: одно соединение под блокировкой."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        self._pending = 0
        # Ссылка -> хеш: копии одного файла не хешируем повторно
        self._sha_by_url = {}

    def record(self, *, tender_id: str, lot_no: Optional[int], lot_title: str, participant: str,
               original_name: str, url: str, path: Path, status: str) -> Optional[str]:
        """Записывает место файла в тендере. Возвращает sha256 или None, если файла на диске нет."""
        path = Path(path)
        sha = None
        size = None
        if path.is_file():
            with self._lock:
                sha = self._sha_by_url.get(url)
            if sha is None:
                sha = file_sha256(path)
                with self._lock:
                    self._sha_by_url[url] = sha
            size = path.stat().st_size

        now = datetime.now().isoformat(timespec="seconds")
        with self._lock:
            if sha:
                self._conn.execute(
                    "INSERT OR IGNORE INTO documents (sha256, size, first_seen) VALUES (?, ?, ?)",
                    (sha, size, now))
            self._conn.execute(
                "INSERT OR REPLACE INTO document_refs "
                "(path, tender_id, lot_no, lot_title, participant, original_name, url, sha256, status, recorded_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (str(path), tender_id, lot_no, lot_title, participant, original_name, url, sha, status, now))
            self._pending += 1
            if self._pending >= COMMIT_EVERY:
                self._conn.commit()
                self._pending = 0
        return sha

    def stats(self) -> dict:
        with self._lock:
            refs = self._conn.execute("SELECT COUNT(*) FROM document_refs").fetchone()[0]
            docs = self._conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            missing = self._conn.execute(
                "SELECT COUNT(*) FROM document_refs WHERE sha256 IS NULL").fetchone()[0]
        return {"refs": refs, "documents": docs, "missing": missing}

    def close(self) -> None:
        with self._lock:
            if self._conn is None:
                return
            self._conn.commit()
            self._conn.close()
            self._conn = None
