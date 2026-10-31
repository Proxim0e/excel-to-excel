"""
Уровень 2 проверки: подтверждение заявленного в Anexa 22 техническими документами участника.

Для оферты (участник, лот) берутся технические документы из папки этого лота (даташиты, каталоги,
сертификаты - группы «Document tehnic» и «ISO/CE/DM»), в их тексте ищутся:
  * код модели (токены модели с цифрами: F4141, T20163, 3011-00010-01);
  * числовые параметры с единицами из предложенной спецификации (70 mm, 500 ml, 18 G).
Если код модели найден на каких-то страницах, параметры ищутся только там: в общем каталоге
то же «70 mm» может относиться к соседнему товару.

Результат в формате человека: // "цитата" "цитата", p[3], имя документа
Итог для ячейки: нет технических документов - «//docs» и жёлтый; не найден код модели или параметр -
«// <параметр>; ...» и жёлтый; всё найдено - зелёный; проверять нечего - светло-зелёный.
"""
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from src.anexa22.requirements import _fold, check_declarative, label_before, parse_constraints
from src.classify import GROUPS, normalize

TECH_TYPES = tuple(GROUPS["Document tehnic"] + GROUPS["ISO/CE/DM"])
# Внутри «пачки» техническими считаются только явно технические страницы: «other» внутри DUAE
# или финотчёта - не даташит
PAGE_TECH_TYPES = tuple(t for t in TECH_TYPES if t != "other")
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
    return list(dict.fromkeys(
        t for t in tokens
        if re.search(r"\d", t) and len(re.sub(r"\W", "", t)) >= 4
        and not VALUE_RE.fullmatch(normalize(t))))  # «500ml» в названии - объём, а не код


TOLERANCE_RE = re.compile(r"(\+\s*/?\s*-|±|\+-)\s*$")


def spec_values(spec: str) -> List[Tuple[str, str]]:
    """Числа с единицами из спецификации: [("70", "mm"), ("500", "ml")]. Допуски («±5mm», «+-5mm»)
    - не параметры, а часть предыдущего значения, их не ищем."""
    text = normalize(spec)
    values = []
    for m in VALUE_RE.finditer(text):
        if TOLERANCE_RE.search(text[max(0, m.start() - 4):m.start()]):
            continue
        v = (m.group(1).replace(",", "."), m.group(2))
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


# Итог уровня 2 для ячейки строки 11 (цвета - как в Excel Ivan, кодбук концепта)
GREEN, LIGHT_GREEN, YELLOW = "green", "light_green", "yellow"
NO_DOCS_MARK = "//docs"
MAX_MISSING = 6
MISSING_CHARS = 45


@dataclass
class OfferCheck:
    status: str                                   # GREEN / LIGHT_GREEN / YELLOW
    evidence: List[Evidence] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)  # уровень 2, не найдено: «model F4141», «Lungime: ≥ 15 cm»
    has_docs: bool = True
    conflicts: List[str] = field(default_factory=list)         # уровень 1: значение A22 вне требования
    missing_in_offer: List[str] = field(default_factory=list)  # уровень 1: условие требования не упомянуто
    copy: bool = False                                         # спецификация - копия требования

    def note(self) -> str:
        """Что дописать в строку 11 после спецификации."""
        parts = []
        if not self.has_docs:
            parts.append(NO_DOCS_MARK)
        else:
            if self.evidence:
                parts.append(format_evidence(self.evidence))
            if self.missing:
                parts.append("// " + "; ".join(self.missing[:MAX_MISSING]))
        if self.conflicts:
            parts.append("// ≠ cerință: " + "; ".join(self.conflicts[:MAX_MISSING]))
        if self.missing_in_offer:
            parts.append("// lipsește în A22: " + "; ".join(self.missing_in_offer[:MAX_MISSING]))
        return "\n".join(parts)


def tech_pages(conn: sqlite3.Connection, participant: str, lot: Optional[int]):
    """Технические страницы участника по лоту (lot=None - по всем лотам): документ технического типа
    или страница технического типа внутри «пачки» (даташиты после Anexa 22 в одном PDF). Страницы,
    из которых разобрана сама таблица Anexa 22, исключаются - иначе Anexa 22 «подтверждала» бы сама себя."""
    doc_ph = ",".join("?" * len(TECH_TYPES))
    page_ph = ",".join("?" * len(PAGE_TECH_TYPES))
    lot_filter = "AND r.lot_no = ?" if lot is not None else ""
    params = (participant, *([lot] if lot is not None else []), *TECH_TYPES, *PAGE_TECH_TYPES)
    # Страницы, через которые прошла таблица Anexa 22 (anexa22_pages), тоже исключаются:
    # продолжения таблицы с номерами DM в шапке классифицированы как amdm/datasheet
    return conn.execute(f"""
        SELECT d.sha256, MIN(r.original_name), p.page_no, p.text
        FROM document_refs r JOIN documents d USING (sha256) JOIN document_pages p USING (sha256)
        WHERE r.participant = ? {lot_filter}
          AND (d.doc_type IN ({doc_ph}) OR p.page_type IN ({page_ph}))
          AND COALESCE(p.page_type, '') NOT IN ('anexa22', 'anexa23')
          AND NOT EXISTS (SELECT 1 FROM anexa22_pages a WHERE a.sha256 = p.sha256 AND a.page_no = p.page_no)
        GROUP BY d.sha256, p.page_no ORDER BY MIN(r.original_name), p.page_no""", params).fetchall()


def parameter_label(spec: str, num: str, unit: str) -> str:
    """Параметр, к которому относится значение: «4. Lungime: ≥ 15 cm 5. ...» -> «Lungime: ≥ 15 cm»."""
    m = _value_rx(num, unit).search(_fold(spec))
    if not m:
        return f"{num} {unit}"
    return label_before(spec, m.start(), m.end(), MISSING_CHARS)


def check_offer(conn: sqlite3.Connection, participant: str, lot: int, model: str, spec: str,
                requirement: str = "") -> OfferCheck:
    """
    Уровень 1 (если дано требование лота): условия требования против значений Anexa 22.
    Уровень 2: код модели и параметры из Anexa 22 в технических документах участника по лоту.
    Брошюру часто прикладывают к одному лоту, а ссылаются на неё в нескольких: если в папке лота
    код модели не найден, ищем в технических документах участника из других лотов.
    Значение из документа, не совпадающее буквально, засчитывается, если укладывается в условие
    требования (требование «≥ 15 cm», в даташите «22 cm») - только на страницах с кодом модели.
    """
    declared = check_declarative(spec, requirement) if requirement else None
    codes = model_tokens(model)
    values = spec_values(spec)
    pages = tech_pages(conn, participant, lot)
    evidence = _search(pages, codes, values) if pages else []
    found = {f for ev in evidence for f in ev.found}
    search_pages = pages
    if codes and not any(c in found for c in codes):
        wider = [p for p in tech_pages(conn, participant, None)
                 if any(_alnum(c) in _alnum(p[3]) for c in codes)]
        if wider:
            evidence = _search(wider, codes, values)
            found = {f for ev in evidence for f in ev.found}
            search_pages = wider
    if not evidence and not pages:
        # технических документов по лоту нет, и код модели не нашёлся у участника в других лотах
        return _with_declared(OfferCheck(YELLOW, has_docs=False), declared)

    not_found = [(num, unit) for num, unit in values if f"{num} {unit}" not in found]
    if not_found and requirement and codes:
        model_pages = [p for p in search_pages if any(_alnum(c) in _alnum(p[3]) for c in codes)]
        for num, unit in list(not_found):
            ev = _satisfying_value(model_pages, num, unit, requirement)
            if ev:
                evidence.append(ev)
                not_found.remove((num, unit))

    missing = [f"model {c}" for c in codes if c not in found]
    missing += list(dict.fromkeys(parameter_label(spec, num, unit) for num, unit in not_found))
    if missing:
        status = YELLOW
    elif codes or values:
        status = GREEN
    else:
        status = LIGHT_GREEN  # проверять нечего - соответствие только декларативное
    return _with_declared(OfferCheck(status, evidence[:MAX_PAGES], missing), declared)


def _with_declared(check: OfferCheck, declared) -> OfferCheck:
    """Добавляет итог уровня 1: расхождения и пропуски в Anexa 22 делают ячейку жёлтой."""
    if declared is None:
        return check
    check.conflicts = declared.conflicts
    check.missing_in_offer = declared.missing
    check.copy = declared.copy
    if declared.conflicts or declared.missing:
        check.status = YELLOW
    return check


def _satisfying_value(pages, num: str, unit: str, requirement: str) -> Optional[Evidence]:
    """Значение в документе, которое укладывается в условие требования для заявленного значения."""
    offered = parse_constraints(f"{num} {unit}")
    if not offered:
        return None
    conditions = [c for c in parse_constraints(requirement) if c.satisfied_by(offered[0]) and c.kind != "exact"]
    if not conditions:
        return None
    for sha, name, pno, text in pages:
        for line in text.splitlines():
            for value in parse_constraints(line):
                if value.kind == "exact" and any(c.satisfied_by(value) for c in conditions):
                    quote = re.sub(r"\s+", " ", line).strip()[:QUOTE_CHARS]
                    return Evidence(name, pno, [quote], [f"{num} {unit}"])
    return None


def find_evidence(conn: sqlite3.Connection, participant: str, lot: int, model: str, spec: str) -> List[Evidence]:
    return check_offer(conn, participant, lot, model, spec).evidence


def _search(pages, codes: List[str], values: List[Tuple[str, str]]) -> List[Evidence]:
    with_model = [(sha, name, pno, text) for sha, name, pno, text in pages
                  if any(_alnum(c) in _alnum(text) for c in codes)]
    candidates = with_model or pages

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
    # Самые содержательные страницы - первыми (все - для подсчёта найденного, в ячейку - MAX_PAGES)
    return sorted(results.values(), key=lambda e: -len(e.found))


def format_evidence(items: List[Evidence]) -> str:
    """// "цитата" "цитата", p[3], имя документа // ..."""
    parts = []
    for ev in items:
        quotes = " ".join(f'"{q}"' for q in ev.quotes)
        parts.append(f"// {quotes}, p[{ev.page_no}], {ev.document}")
    return " ".join(parts)
