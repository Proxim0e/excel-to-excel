"""
Уровень 2 проверки: подтверждение заявленного в Anexa 22 техническими документами участника.

Для оферты (участник, лот) берутся технические документы из папки этого лота (даташиты, каталоги,
сертификаты - группы «Document tehnic» и «ISO/CE/DM»), в их тексте ищутся:
  * код модели (токены модели с цифрами: F4141, T20163, 3011-00010-01);
  * числовые параметры с единицами из предложенной спецификации (70 mm, 500 ml, 18 G).
Если код модели найден на каких-то страницах, параметры ищутся только там: в общем каталоге
то же «70 mm» может относиться к соседнему товару.

Результат в формате человека: // "цитата" "цитата", p[3], имя документа
"""
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from src.classify import GROUPS, normalize

TECH_TYPES = tuple(GROUPS["Document tehnic"] + GROUPS["ISO/CE/DM"])
UNITS = (r"mm2|cm2|mmhg|kpa|bar|mah|ohm|hz|ml|mm|cm|kg|fr|ch|°c|micron[i]?|µm|um|pin|buc|ore|min|"
         r"m|l|g|h|w|v|%")
VALUE_RE = re.compile(rf"(?<![\w.,])(\d+(?:[.,]\d+)?)\s?({UNITS})(?![a-z])")
MAX_VALUES = 12        # параметров из одной спецификации
MAX_QUOTES = 4         # цитат на страницу
MAX_PAGES = 3          # страниц в результате
QUOTE_CHARS = 110


@dataclass
class Evidence:
    document: str
    page_no: int
    quotes: List[str] = field(default_factory=list)
    found: List[str] = field(default_factory=list)  # что именно найдено: код модели, «70 mm»


def model_tokens(model: str) -> List[str]:
    """Коды из названия модели: токены с цифрами длиной от 4 символов (F4141, 3011-00010-01)."""
    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9.\-/]{2,}[A-Za-z0-9]", model or "")
    return list(dict.fromkeys(t for t in tokens if re.search(r"\d", t) and len(re.sub(r"\W", "", t)) >= 4))


def spec_values(spec: str) -> List[Tuple[str, str]]:
    """Числа с единицами из спецификации: [("70", "mm"), ("500", "ml")]."""
    values = []
    for num, unit in VALUE_RE.findall(normalize(spec)):
        v = (num.replace(",", "."), unit)
        if v not in values:
            values.append(v)
    return values[:MAX_VALUES]


def _alnum(text: str) -> str:
    return re.sub(r"[\W_]+", "", normalize(text))


def _quote(page_text: str, pos_test) -> Optional[str]:
    """Строка страницы, для которой pos_test(нормализованная строка) истинно."""
    for line in page_text.splitlines():
        if pos_test(normalize(line)):
            q = re.sub(r"\s+", " ", line).strip()
            return q[:QUOTE_CHARS] + ("…" if len(q) > QUOTE_CHARS else "")
    return None


def _value_rx(num: str, unit: str):
    variants = {num, num.replace(".", ",")}
    alt = "|".join(re.escape(v) for v in variants)
    return re.compile(rf"(?<![\d.,])(?:{alt})\s?{re.escape(unit)}(?![a-z])")


def find_evidence(conn: sqlite3.Connection, participant: str, lot: int, model: str, spec: str) -> List[Evidence]:
    # Технические страницы: документ технического типа или страница технического типа внутри
    # «пачки» (даташиты после Anexa 22 в одном PDF). Страницы, из которых разобрана сама таблица
    # Anexa 22, исключаются - иначе Anexa 22 «подтверждала» бы сама себя.
    placeholders = ",".join("?" * len(TECH_TYPES))
    pages = conn.execute(f"""
        SELECT d.sha256, MIN(r.original_name), p.page_no, p.text
        FROM document_refs r JOIN documents d USING (sha256) JOIN document_pages p USING (sha256)
        WHERE r.participant = ? AND r.lot_no = ?
          AND (d.doc_type IN ({placeholders}) OR p.page_type IN ({placeholders}))
          AND COALESCE(p.page_type, '') NOT IN ('anexa22', 'anexa23')
          AND NOT EXISTS (SELECT 1 FROM anexa22_offers o WHERE o.sha256 = p.sha256 AND o.page_no = p.page_no)
        GROUP BY d.sha256, p.page_no ORDER BY MIN(r.original_name), p.page_no""",
                         (participant, lot, *TECH_TYPES, *TECH_TYPES)).fetchall()
    if not pages:
        return []

    codes = model_tokens(model)
    with_model = [(sha, name, pno, text) for sha, name, pno, text in pages
                  if any(_alnum(c) in _alnum(text) for c in codes)]
    candidates = with_model or pages
    values = spec_values(spec)

    results: Dict[Tuple[str, int], Evidence] = {}
    for sha, name, pno, text in candidates:
        ev = Evidence(name, pno)
        for code in codes:
            key = _alnum(code)
            if key in _alnum(text):
                q = _quote(text, lambda line, k=key: k in _alnum(line))
                ev.found.append(code)
                if q and q not in ev.quotes:
                    ev.quotes.append(q)
        for num, unit in values:
            rx = _value_rx(num, unit)
            if rx.search(normalize(text)):
                q = _quote(text, rx.search)
                ev.found.append(f"{num} {unit}")
                if q and q not in ev.quotes:
                    ev.quotes.append(q)
        if ev.found:
            ev.quotes = ev.quotes[:MAX_QUOTES]
            results[(sha, pno)] = ev
    # Самые содержательные страницы - первыми
    return sorted(results.values(), key=lambda e: -len(e.found))[:MAX_PAGES]


def format_evidence(items: List[Evidence]) -> str:
    """// "цитата" "цитата", p[3], имя документа // ..."""
    parts = []
    for ev in items:
        quotes = " ".join(f'"{q}"' for q in ev.quotes)
        parts.append(f"// {quotes}, p[{ev.page_no}], {ev.document}")
    return " ".join(parts)
