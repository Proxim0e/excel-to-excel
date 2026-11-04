"""
База знаний: история тендеров (одна на все тендеры, в отличие от манифеста тендера).

Рабочий Excel правится и после тендера (апелляции, отказы), поэтому каждый импорт - новый снимок
(snapshots); текущее состояние тендера - его последний снимок. Отличия от предыдущего снимка
записываются как события (events): так видна история пересмотров решений.
"""
import sqlite3
from pathlib import Path

from src.config import BASE_DIR

DEFAULT_DB = BASE_DIR / "data" / "knowledge.sqlite"

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id          INTEGER PRIMARY KEY,
    tender_id   TEXT NOT NULL,
    source_file TEXT NOT NULL,
    file_sha256 TEXT NOT NULL,
    file_mtime  TEXT,
    imported_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snapshots_tender ON snapshots(tender_id, id);

CREATE TABLE IF NOT EXISTS lots (
    snapshot_id   INTEGER NOT NULL REFERENCES snapshots(id),
    lot_no        INTEGER NOT NULL,
    sheet         TEXT NOT NULL,
    title         TEXT,
    quantity      REAL,
    unit          TEXT,
    base_price    REAL,        -- D7: базовая цена за единицу
    base_value    REAL,        -- D8: база (Valoare estimata)
    requirement   TEXT,        -- B11
    suffix        TEXT,        -- A / Ad / M из имени листа
    tab_color     TEXT,
    outcome       TEXT,        -- awarded / not_awarded / not_held / working_group / samples / alternative / clarification_open
    note          TEXT,        -- колонка «Note»
    PRIMARY KEY (snapshot_id, lot_no, sheet)
);

CREATE TABLE IF NOT EXISTS offers (
    snapshot_id     INTEGER NOT NULL REFERENCES snapshots(id),
    lot_no          INTEGER NOT NULL,
    sheet           TEXT NOT NULL,
    col             INTEGER NOT NULL,
    participant     TEXT NOT NULL,
    producer        TEXT,
    country         TEXT,
    model           TEXT,
    dm              TEXT,       -- номера DM через запятую
    row4            TEXT,
    unit_price      REAL,
    offer_value     REAL,
    price_ratio     REAL,       -- сумма / база
    row10           TEXT,       -- «Cistigator 2026 ...»
    previous_winner INTEGER,    -- победитель прошлого года по базовому списку
    spec            TEXT,       -- строка 11 до первого «//»
    spec_notes      TEXT,       -- строка 11 после «//»
    row1_text       TEXT,
    row1_color      TEXT,
    row11_color     TEXT,
    decision        TEXT,       -- accepted / rejected / not_evaluated / alternative / samples / working_group /
                                -- clarification / not_held / improved_offer / unknown
    reason          TEXT,       -- price_130 / guarantee_insufficient / guarantee_wrong / amdm / technical /
                                -- annulled_g / withdrawn / alternative_rejected
    needs_review    TEXT,       -- почему спросить человека (текст и цвет противоречат и т.п.)
    PRIMARY KEY (snapshot_id, sheet, col)
);
CREATE INDEX IF NOT EXISTS idx_offers_lot ON offers(snapshot_id, lot_no, participant);

CREATE TABLE IF NOT EXISTS clarifications (
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
    lot_no      INTEGER NOT NULL,
    participant TEXT NOT NULL,
    col         INTEGER NOT NULL,
    label       TEXT,          -- строка 3: «clarificări (runda 1)»
    text        TEXT,          -- строка 11
    color       TEXT
);

-- Ответы человека на вопросы («спросить один раз»). Привязаны к самому вопросу (текст и цвет строки 1):
-- если человек потом поменял ячейку в Excel, ответ к новому состоянию не применяется
CREATE TABLE IF NOT EXISTS answers (
    tender_id   TEXT NOT NULL,
    lot_no      INTEGER NOT NULL,
    participant TEXT NOT NULL,
    row1_text   TEXT,
    row1_color  TEXT,
    decision    TEXT NOT NULL,
    reason      TEXT,
    comment     TEXT,
    answered_at TEXT NOT NULL,
    PRIMARY KEY (tender_id, lot_no, participant)
);

-- Текущее состояние: последний снимок каждого тендера + ответы человека поверх расшифровки
CREATE VIEW IF NOT EXISTS current_offers AS
SELECT s.tender_id, o.*,
       CASE WHEN a.decision IS NOT NULL THEN a.decision ELSE o.decision END AS final_decision,
       CASE WHEN a.decision IS NOT NULL THEN a.reason ELSE o.reason END     AS final_reason,
       CASE WHEN a.decision IS NOT NULL THEN NULL ELSE o.needs_review END  AS open_question,
       CASE WHEN a.decision IS NOT NULL THEN 'human' ELSE 'codebook' END   AS decision_source,
       a.comment AS answer_comment
FROM offers o
JOIN snapshots s ON s.id = o.snapshot_id
LEFT JOIN answers a ON a.tender_id = s.tender_id AND a.lot_no = o.lot_no AND a.participant = o.participant
     AND a.row1_text IS o.row1_text AND a.row1_color IS o.row1_color
WHERE s.id = (SELECT MAX(id) FROM snapshots s2 WHERE s2.tender_id = s.tender_id);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY,
    tender_id   TEXT NOT NULL,
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
    lot_no      INTEGER,
    participant TEXT,
    field       TEXT NOT NULL,
    old_value   TEXT,
    new_value   TEXT,
    detected_at TEXT NOT NULL
);

-- Оценка новых тендеров (src/evaluate, решение Ivan 04.11.2026)

-- Карточка товара: подтверждённая человеком (source = human) или предзаполненная (prefill)
CREATE TABLE IF NOT EXISTS products (
    model_key       TEXT PRIMARY KEY,   -- src/history/cards.py:model_key
    producer        TEXT,
    model           TEXT,
    codes           TEXT,               -- коды модели через запятую
    dm              TEXT,               -- номера DM через запятую
    name            TEXT,               -- что это за изделие
    params          TEXT,               -- JSON: параметр -> значение
    compatible_with TEXT,               -- с каким аппаратом совместимо
    iso             TEXT,               -- ISO / CE, если DM нет
    agents          TEXT,               -- экономические агенты, предлагавшие модель (через «; »)
    winners         TEXT,               -- победители прошлых лет по модели: «участник (год, тендер)» через «; »
    source          TEXT NOT NULL,      -- human / prefill
    confirmed_at    TEXT,
    updated_at      TEXT NOT NULL
);

-- Запрос лота: с ним сравниваются запросы новых тендеров
CREATE TABLE IF NOT EXISTS lot_profiles (
    tender_id   TEXT NOT NULL,
    lot_no      INTEGER NOT NULL,
    title       TEXT,
    requirement TEXT,
    conditions  TEXT,                   -- JSON: условия запроса (числа, признаки, совместимость)
    PRIMARY KEY (tender_id, lot_no)
);

-- Решение по оферте: corespunde / alternativa / nu_corespunde / clarificare / neevaluat
CREATE TABLE IF NOT EXISTS evaluations (
    tender_id   TEXT NOT NULL,
    lot_no      INTEGER NOT NULL,
    participant TEXT NOT NULL,
    model_key   TEXT,
    status      TEXT NOT NULL,
    reason      TEXT,                   -- код из src/history/review.py:REASONS
    confidence  REAL,
    source      TEXT NOT NULL,          -- auto / human / pending (ждёт ответа)
    findings    TEXT,                   -- JSON: уровни 1-3 и история
    decided_at  TEXT NOT NULL,
    PRIMARY KEY (tender_id, lot_no, participant)
);
CREATE INDEX IF NOT EXISTS idx_evaluations_model ON evaluations(model_key, status);

-- Очередь вопросов к человеку для окна приложения
CREATE TABLE IF NOT EXISTS questions (
    id          INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL,          -- offer_status / product_card / medical_type
    tender_id   TEXT NOT NULL,
    lot_no      INTEGER,
    participant TEXT,
    model_key   TEXT,
    payload     TEXT NOT NULL,          -- JSON: что показать
    state       TEXT NOT NULL,          -- open / answered
    answer      TEXT,                   -- JSON: ответ человека
    created_at  TEXT NOT NULL,
    answered_at TEXT,
    UNIQUE (kind, tender_id, lot_no, participant, model_key)
);

-- Медицинское ли изделие такого типа (первое слово названия лота) - ответ человека, когда реестр не решил.
-- Не медицинское: регистрация AMDM не требуется, достаточно ISO/CE. Действует для всех тендеров.
CREATE TABLE IF NOT EXISTS product_types (
    word        TEXT PRIMARY KEY,
    medical     INTEGER NOT NULL,
    comment     TEXT,
    decided_at  TEXT NOT NULL
);
"""


def connect(path=DEFAULT_DB) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


# Колонки, добавленные после создания таблиц (старые базы на обоих ПК)
ADDED_COLUMNS = {"products": (("agents", "TEXT"), ("winners", "TEXT"))}


def _migrate(conn: sqlite3.Connection) -> None:
    for table, cols in ADDED_COLUMNS.items():
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, kind in cols:
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")
    conn.commit()
