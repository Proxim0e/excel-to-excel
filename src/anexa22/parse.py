"""
Разбор таблиц Anexa 22 (технической спецификации участника).

Все Anexa 22 - таблицы с линиями, их находит PyMuPDF (page.find_tables). Но раскладка у каждого
участника своя: шапка формы внутри таблицы, заголовки в несколько строк, объединённые ячейки
дают «пустые» колонки, значение может стоять не под своим заголовком по номеру колонки.
Поэтому:
  1. строка заголовков ищется по ключевым словам, её продолжение вниз склеивается;
  2. колонка = x-диапазон ячейки заголовка, поле определяется по тексту заголовка;
  3. ячейка данных попадает в поле по x-центру, а не по номеру колонки;
  4. строка без номера лота / CPV / названия - продолжение предыдущей (перенос длинного текста);
  5. таблица без заголовка на следующей странице берёт колонки предыдущей. Разбор идёт дальше
     страниц с типом anexa22: продолжения таблицы часто классифицированы иначе (в шапке номера DM),
     поэтому таблица продолжается, пока на следующей странице есть таблица на том же месте;
  6. строка во всю ширину «Lotul nr. 89 - ...» - разделитель: задаёт лот для строк под ней.
"""
import logging
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from src.classify import normalize
from src.manifest import MANIFEST_NAME

logger = logging.getLogger(__name__)

# Поле -> признаки в нормализованном заголовке (без диакритики, только буквы).
# Порядок важен: первое совпадение.
HEADER_FIELDS = [
    ("cpv", ("codcpv", "cpv")),
    ("item_no", ("nrdo",)),
    ("lot", ("nrlot", "nrlotului", "lotnr")),
    ("lot_name", ("denumirelot", "denumirealotului")),
    ("model", ("model",)),
    ("country", ("tara", "origine")),
    ("producer", ("producator", "producato", "fabricant")),
    ("spec_required", ("solicit", "cerut")),
    ("spec_offered", ("propus", "oferit")),
    ("standards", ("standard", "inregistrare", "referint")),
    ("item_name", ("denumirea", "denumire")),
]
# Объединённая колонка «Ţara de origine Producătorul»: значение «Italia Fiab» делится по стране
COUNTRY_PRODUCER = "country_producer"
COUNTRIES = (
    "marea britanie", "coreea de sud", "republica moldova", "emiratele arabe unite", "statele unite",
    "italia", "germania", "china", "turcia", "sua", "usa", "franta", "spania", "polonia", "ucraina", "india",
    "coreea", "japonia", "israel", "elvetia", "belgia", "olanda", "danemarca", "suedia", "austria", "cehia",
    "ungaria", "romania", "moldova", "taiwan", "malaezia", "pakistan", "vietnam", "thailanda", "irlanda",
    "finlanda", "norvegia", "portugalia", "grecia", "bulgaria", "belarus", "rusia", "lituania", "letonia",
    "estonia", "slovenia", "slovacia", "croatia", "serbia", "brazilia", "mexic", "canada", "australia",
    "egipt", "iran", "indonezia", "singapore", "uk",
)
HEADER_MIN_FIELDS = 3       # строка заголовков должна узнаваться минимум по трём полям
KEY_FIELDS = ("cpv", "lot", "item_no", "lot_name", "item_name")  # новая строка таблицы = новая оферта
DM_RE = re.compile(r"DM\s*0(?:\s*\d){8}", re.IGNORECASE)
LOT_CODE_RE = re.compile(r"\(\s*(\d{6})\s*\)")
LOT_TEXT_RE = re.compile(r"\blot(?:ul)?\s*(?:nr\.?|№)?\s*(\d{1,3})\b", re.IGNORECASE)
SECTION_ROW_SHARE = 0.6     # ячейка шире этой доли таблицы - строка-разделитель
TABLE_ALIGN_TOLERANCE = 25  # pt: таблица на следующей странице продолжает текущую


@dataclass
class Column:
    field: str
    x0: float
    x1: float
    header: str


@dataclass
class Offer:
    participant: str
    sha256: str
    page_no: int
    lot: Optional[int] = None
    fields: Dict[str, str] = field(default_factory=dict)
    pages: set = field(default_factory=set)  # все страницы строки, включая перенос на следующие

    def get(self, name: str) -> str:
        return self.fields.get(name, "")

    @property
    def dm(self) -> List[str]:
        text = " ".join(self.get(f) for f in ("standards", "spec_offered", "model"))
        return list(dict.fromkeys(re.sub(r"\s", "", m).upper() for m in DM_RE.findall(text)))


def _letters(text: str) -> str:
    return re.sub(r"[^a-z]", "", normalize(text))


def header_field(text: str) -> Optional[str]:
    letters = _letters(text)
    if not letters:
        return None
    if "tara" in letters and "producator" in letters:
        return COUNTRY_PRODUCER
    for name, keys in HEADER_FIELDS:
        if any(k in letters for k in keys):
            return name
    return None


def _clean(text) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _table_rows(table) -> List[List[Tuple[Optional[tuple], str]]]:
    """Строки таблицы: [(bbox ячейки или None, текст)]."""
    texts = table.extract()
    return [list(zip(row.cells, texts[i])) for i, row in enumerate(table.rows)]


def _is_numbering_row(row) -> bool:
    """Строка с номерами колонок «1 2 3 ... 8» под заголовком."""
    vals = [_clean(t) for _, t in row if _clean(t)]
    return len(vals) >= 3 and all(re.fullmatch(r"\d{1,2}", v) for v in vals)


def find_header(rows) -> Optional[Tuple[int, int]]:
    """(индекс строки заголовков, индекс первой строки данных) или None."""
    for i, row in enumerate(rows):
        fields = {header_field(t) for _, t in row if _clean(t)}
        fields.discard(None)
        if len(fields) < HEADER_MIN_FIELDS:
            continue
        j = i + 1
        # Продолжение заголовка: переносы слов, вертикальные буквы «Co / d / CP / V»
        while j < len(rows) and not _is_numbering_row(rows[j]) and _looks_like_header_tail(rows[j]):
            j += 1
        if j < len(rows) and _is_numbering_row(rows[j]):
            j += 1
        return i, j
    return None


def _looks_like_header_tail(row) -> bool:
    vals = [_clean(t) for _, t in row if _clean(t)]
    if not vals:
        return True
    if any(re.search(r"\d{5}\s?\d{3}-\d", v) for v in vals):  # код CPV - это уже данные
        return False
    return all(len(v) <= 4 or header_field(v) or re.search(r"[a-z]", v) and len(v) < 40 and not re.search(r"\d", v)
               for v in vals) and len(vals) <= 6


def build_columns(rows, header_idx: int, data_idx: int) -> List[Column]:
    """Колонки по ячейкам строки заголовков; текст заголовка собирается со всех строк шапки."""
    columns = []
    for bbox, text in rows[header_idx]:
        if bbox is None:
            continue
        x0, x1 = bbox[0], bbox[2]
        parts = [_clean(text)]
        for r in rows[header_idx + 1:data_idx]:
            for b, t in r:
                if b is not None and _clean(t) and x0 - 1 <= (b[0] + b[2]) / 2 <= x1 + 1:
                    parts.append(_clean(t))
        header = " ".join(p for p in parts if p)
        name = header_field(header)
        if name:
            columns.append(Column(name, x0, x1, header))
    # Одно поле - одна колонка: при повторе (две колонки «denumire») берём первую
    seen, unique = set(), []
    for c in columns:
        if c.field not in seen:
            seen.add(c.field)
            unique.append(c)
    return unique


def _field_at(columns: List[Column], bbox) -> Optional[str]:
    cx = (bbox[0] + bbox[2]) / 2
    for c in columns:
        if c.x0 - 1 <= cx <= c.x1 + 1:
            return c.field
    # Ячейка шире колонок (объединённая) - по наибольшему перекрытию
    best, overlap = None, 0.0
    for c in columns:
        o = min(c.x1, bbox[2]) - max(c.x0, bbox[0])
        if o > overlap:
            best, overlap = c.field, o
    return best


def _section_lot(row, table_width: float) -> Optional[int]:
    """Номер лота из строки-разделителя во всю ширину таблицы («Lotul nr. 89 - ...»)."""
    cells = [(b, _clean(t)) for b, t in row if b is not None and _clean(t)]
    if len(cells) != 1:
        return None
    bbox, text = cells[0]
    if (bbox[2] - bbox[0]) < SECTION_ROW_SHARE * table_width:
        return None
    m = LOT_TEXT_RE.search(normalize(text))
    return int(m.group(1)) if m else None


def parse_rows(rows, columns: List[Column], table_width: float = 0.0) -> List[Dict[str, str]]:
    """Строки таблицы -> записи {поле: текст}; строки-продолжения дописываются к предыдущей записи."""
    has_key = {c.field for c in columns} & {"cpv", "lot"}
    keys = tuple(has_key) if has_key else KEY_FIELDS
    records: List[Dict[str, str]] = []
    section = None
    for row in rows:
        lot = _section_lot(row, table_width) if table_width else None
        if lot is not None:
            section = lot
            continue
        rec: Dict[str, List[str]] = {}
        for bbox, text in row:
            text = _clean(text)
            if bbox is None or not text:
                continue
            name = _field_at(columns, bbox)
            if name:
                rec.setdefault(name, []).append(text)
        if not rec or _is_numbering_row(row):
            continue
        joined = {k: " ".join(v) for k, v in rec.items()}
        if section is not None and not joined.get("lot"):
            joined["section_lot"] = str(section)
        if any(joined.get(k) for k in keys) or not records:
            records.append(joined)
        else:
            prev = records[-1]
            for k, v in joined.items():
                prev[k] = f"{prev[k]} {v}" if prev.get(k) else v
    return records


def split_country_producer(text: str) -> Tuple[str, str]:
    """«Italia Fiab» -> («Italia», «Fiab»); страна ищется в начале или в конце значения."""
    norm = normalize(text)
    for country in sorted(COUNTRIES, key=len, reverse=True):
        if norm.startswith(country + " ") or norm == country:
            return text[:len(country)].strip(), text[len(country):].strip(" ,;/-")
        if norm.endswith(" " + country):
            return text[-len(country):].strip(), text[:-len(country)].strip(" ,;/-")
    return "", text


def lot_number(rec: Dict[str, str], lot_by_code: Dict[str, int]) -> Optional[int]:
    """Номер лота: из колонки «Nr. Lot», иначе по коду «( 006951 )» в названии."""
    m = re.match(r"\s*(\d{1,3})\b", rec.get("lot", ""))
    if m:
        return int(m.group(1))
    if rec.get("section_lot"):
        return int(rec["section_lot"])
    for name in ("lot_name", "item_name", "spec_required"):
        code = LOT_CODE_RE.search(rec.get(name, ""))
        if code and code.group(1) in lot_by_code:
            return lot_by_code[code.group(1)]
    for name in ("lot_name", "item_name"):
        m = LOT_TEXT_RE.search(normalize(rec.get(name, "")))
        if m:
            return int(m.group(1))
    m = re.match(r"\s*(\d{1,3})\b", rec.get("item_no", ""))
    return int(m.group(1)) if m else None


def _aligned(a, b) -> bool:
    return abs(a[0] - b[0]) <= TABLE_ALIGN_TOLERANCE and abs(a[2] - b[2]) <= TABLE_ALIGN_TOLERANCE


def parse_document(path: Path, start_pages: List[int], participant: str, sha: str,
                   lot_by_code: Dict[str, int]) -> List[Offer]:
    """Разбор с каждой стартовой страницы Anexa 22, пока таблица продолжается на следующих страницах."""
    import fitz

    offers: List[Offer] = []
    done = set()
    with fitz.open(path) as doc:
        for start in start_pages:
            if start in done:
                continue
            columns: List[Column] = []
            table_box = None
            for pno in range(start, doc.page_count + 1):
                tables = doc[pno - 1].find_tables().tables
                continued = False
                for table in tables:
                    rows = _table_rows(table)
                    header = find_header(rows)
                    if header:
                        new_columns = build_columns(rows, *header)
                        # Не всякая таблица с «denumirea» - Anexa 22: нужна модель, производитель
                        # или предложенная спецификация (у некоторых участников модели нет)
                        if {c.field for c in new_columns} & {"model", "producer", "spec_offered"}:
                            columns, table_box = new_columns, table.bbox
                            data = rows[header[1]:]
                        else:
                            continue
                    elif columns and table_box and _aligned(table.bbox, table_box):
                        data = rows  # продолжение таблицы с прошлой страницы
                    else:
                        continue
                    continued = True
                    width = table.bbox[2] - table.bbox[0]
                    offers.extend(_to_offers(parse_rows(data, columns, width), offers,
                                             participant, sha, pno, lot_by_code))
                if not continued and pno > start:
                    break
                done.add(pno)
    return offers


def _to_offers(records, offers: List[Offer], participant: str, sha: str, pno: int,
               lot_by_code: Dict[str, int]) -> List[Offer]:
    """Записи таблицы -> оферты; запись без ключевых полей - перенос строки через границу страницы."""
    new: List[Offer] = []
    for rec in records:
        previous = new[-1] if new else (offers[-1] if offers else None)
        if previous and not any(rec.get(k) for k in KEY_FIELDS) and not rec.get("section_lot"):
            for k, v in rec.items():
                previous.fields[k] = f"{previous.fields[k]} {v}" if previous.fields.get(k) else v
            previous.pages.add(pno)
            continue
        new.append(Offer(participant, sha, pno, lot_number(rec, lot_by_code), rec, {pno}))
    for offer in new:
        both = offer.fields.pop(COUNTRY_PRODUCER, None)
        if both:
            country, producer = split_country_producer(both)
            offer.fields.setdefault("country", country)
            offer.fields.setdefault("producer", producer)
    return new


def tender_lot_codes(conn: sqlite3.Connection) -> Dict[str, int]:
    """Код позиции «( 006951 )» из названия лота -> номер лота."""
    codes = {}
    for lot_no, title in conn.execute("SELECT DISTINCT lot_no, lot_title FROM document_refs"):
        m = LOT_CODE_RE.search(title or "")
        if m and lot_no is not None:
            codes[m.group(1)] = lot_no
    return codes


def save_offers(tender_dir: Path, offers: List[Offer]) -> int:
    """Перезаписывает таблицу anexa22_offers манифеста результатом разбора."""
    from datetime import datetime

    from src.manifest import migrate

    conn = sqlite3.connect(Path(tender_dir) / MANIFEST_NAME)
    try:
        migrate(conn)
        now = datetime.now().isoformat(timespec="seconds")
        conn.execute("DELETE FROM anexa22_offers")
        conn.execute("DELETE FROM anexa22_pages")
        conn.executemany("INSERT OR IGNORE INTO anexa22_pages (sha256, page_no) VALUES (?, ?)",
                         [(o.sha256, p) for o in offers for p in (o.pages or {o.page_no})])
        conn.executemany(
            "INSERT INTO anexa22_offers (sha256, page_no, participant, lot_no, cpv, item_name, model, country, "
            "producer, spec_required, spec_offered, standards, dm, parsed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(o.sha256, o.page_no, o.participant, o.lot, o.get("cpv"), o.get("item_name") or o.get("lot_name"),
              o.get("model"), o.get("country"), o.get("producer"), o.get("spec_required"), o.get("spec_offered"),
              o.get("standards"), ",".join(o.dm), now) for o in offers])
        conn.commit()
    finally:
        conn.close()
    return len(offers)


def parse_tender(tender_dir: Path) -> List[Offer]:
    """Все оферты из страниц Anexa 22 манифеста тендера."""
    conn = sqlite3.connect(Path(tender_dir) / MANIFEST_NAME)
    try:
        lot_by_code = tender_lot_codes(conn)
        docs = conn.execute("""
            SELECT p.sha256, GROUP_CONCAT(p.page_no),
                   (SELECT MIN(path) FROM document_refs r WHERE r.sha256 = p.sha256),
                   (SELECT MIN(participant) FROM document_refs r WHERE r.sha256 = p.sha256)
            FROM document_pages p WHERE p.page_type = 'anexa22' GROUP BY p.sha256""").fetchall()
    finally:
        conn.close()
    offers = []
    for sha, pages, path, participant in docs:
        pages = sorted(int(p) for p in pages.split(","))
        try:
            offers.extend(parse_document(Path(path), pages, participant, sha, lot_by_code))
        except Exception as e:  # один битый PDF не должен останавливать разбор тендера
            logger.warning(f"[ANEXA22] Не удалось разобрать {path}: {type(e).__name__} - {e}")
    return offers
