"""
Уровень 1 проверки: предложенная спецификация (Anexa 22) против требования лота.

Требование разбирается на числовые условия:
  «≥ 15 cm», «min. 3m», «Lungimea minima 150cm»          -> нижняя граница
  «≤ 60 ml», «max 5 microni», «nu mai mult de 10»         -> верхняя граница
  «70 mm (+-5mm)», «200cm (±10cm)», «240-310 cm»         -> диапазон
  «500 ml», «Ø 49mm»                                       -> точное значение
Единицы приводятся к одной шкале внутри семейства (мм/см/м, мл/л, г/кг, мин/ч).
Не размеры: «2,04 cm H2O» (давление), «15M (masculin)», «22F-15M» (разъёмы).

Для каждого условия ищется значение того же семейства единиц в предложенной спецификации:
  в границах - ок; вне границ - расхождение («≠ cerință»); нет такого значения - «lipsește în A22».
Расхождение по концепту - «проверить», а не отказ: разбор свободного текста эвристический.
"""
import re
import unicodedata
from dataclasses import dataclass
from typing import List, Optional, Tuple

from src.classify import normalize

# Единица -> (семейство, множитель к базовой)
UNITS = {
    "mm": ("len", 1), "cm": ("len", 10), "m": ("len", 1000),
    "ml": ("vol", 1), "l": ("vol", 1000),
    "g": ("mass", 1), "kg": ("mass", 1000),
    "min": ("time", 1), "h": ("time", 60), "ore": ("time", 60),
    "%": ("pct", 1), "°c": ("temp", 1), "c": ("temp", 1),
    "mmhg": ("press_mmhg", 1), "cmh2o": ("press_cmh2o", 1), "kpa": ("press_kpa", 1), "bar": ("press_bar", 1),
    "bari": ("press_bar", 1), "fr": ("fr", 1), "ch": ("fr", 1), "micron": ("um", 1), "microni": ("um", 1),
    "µm": ("um", 1), "um": ("um", 1), "cicluri": ("cycles", 1), "pin": ("pin", 1), "buc": ("pcs", 1),
    "w": ("w", 1), "v": ("v", 1), "hz": ("hz", 1), "ohm": ("ohm", 1), "ω": ("ohm", 1), "mah": ("mah", 1),
}
# Длинные единицы раньше коротких: «cm h2o» раньше «cm», «mmhg» раньше «mm»
UNIT_RX = (r"cm\s?h2o|mm\s?hg|microni|micron|cicluri|bari|bar|kpa|mah|ohm|ω|hz|ml|mm|cm|kg|fr|ch|°c|µm|um|"
           r"pin|buc|ore|min|m|l|g|h|w|v|%")
NUM = r"\d+(?:[.,]\d+)?"
MIN_WORDS = r"(?:≥|>=|>|min\.?|minim(?:a|um|ă)?|nu mai (?:mic|putin|puțin) de|cel putin|de cel putin)"
MAX_WORDS = r"(?:≤|<=|<|max\.?|maxim(?:a|um|ă)?|nu mai mult de|pana la|până la)"
TOL = r"(?:\+\s*/?\s*-|±|\+-)"

END = r"(?![a-z0-9/])"  # «30l/min» - расход, а не объём
PATTERNS = [
    # 25- 35 cm ±3 cm - диапазон с допуском
    ("range_tol", re.compile(rf"(?<![\d.,])({NUM})\s?({UNIT_RX})?\s*(?:-|–)\s*({NUM})\s?({UNIT_RX})\s*\(?\s*{TOL}\s*({NUM})\s?({UNIT_RX})?\s*\)?")),
    # 70 mm (+-5mm), 200cm (±10cm)
    ("tol", re.compile(rf"({NUM})\s?({UNIT_RX})?\s*\(?\s*{TOL}\s*({NUM})\s?({UNIT_RX})?\s*\)?")),
    # 100mmx200m, 110mm х 140mm (бывает кириллическая «х») - каждое число отдельно
    ("dims", re.compile(rf"({NUM})\s?({UNIT_RX})?\s*[x×х]\s*({NUM})\s?({UNIT_RX}){END}")),
    # 240-310 cm, 104–106 mm, 0~300mm hg
    ("range", re.compile(rf"(?<![\d.,])({NUM})\s?({UNIT_RX})?\s*(?:-|–|÷|~|pana la|până la)\s*({NUM})\s?({UNIT_RX}){END}")),
    ("min", re.compile(rf"{MIN_WORDS}\s*:?\s*({NUM})\s?({UNIT_RX}){END}")),
    ("max", re.compile(rf"{MAX_WORDS}\s*:?\s*({NUM})\s?({UNIT_RX}){END}")),
    ("exact", re.compile(rf"(?<![\w.,])({NUM})\s?({UNIT_RX}){END}")),
]
# «15M (masculin)», «22F-15M», «22M-15F» - размеры разъёмов, а не метры
CONNECTOR_RE = re.compile(r"\d+\s?[mf]\s*\((?:masculin|feminin|male|female)|\d+[mf]\s*[-x/]\s*\d+[mf]\b")
EPS = 1e-6
COPY_SHARE = 0.95   # спецификация - копия требования, если в ней есть 95% его слов


@dataclass
class Constraint:
    family: str
    lo: Optional[float]      # в базовых единицах семейства; None - без нижней границы
    hi: Optional[float]
    kind: str                # tol / range / min / max / exact
    text: str                # исходный фрагмент: «Lungime: ≥ 15 cm»

    def satisfied_by(self, other: "Constraint") -> bool:
        """Значение/диапазон other укладывается в условие."""
        if other.family != self.family:
            return False
        if self.lo is not None and (other.lo is None or other.lo < self.lo - EPS):
            return False
        if self.hi is not None and (other.hi is None or other.hi > self.hi + EPS):
            return False
        return True

    def overlaps(self, other: "Constraint") -> bool:
        """Диапазоны пересекаются: «150-180 cm» и «≥ 160 cm» совместимы, хоть и не вложены."""
        if other.family != self.family:
            return False
        lo = max(x for x in (self.lo, other.lo, float("-inf")) if x is not None)
        hi = min(x for x in (self.hi, other.hi, float("inf")) if x is not None)
        return lo <= hi + EPS


def _num(text: str) -> float:
    return float(text.replace(",", "."))


def _unit(unit: Optional[str]) -> Optional[Tuple[str, float]]:
    if not unit:
        return None
    return UNITS.get(re.sub(r"\s", "", unit))


def _fold(text: str) -> str:
    """normalize() без схлопывания пробелов: позиции совпадают с исходным текстом."""
    folded = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)).lower()
    return folded if len(folded) == len(text) else text.lower()


def label_before(text: str, start: int, end: int, max_chars: int = 45, begin: int = 0) -> str:
    """Параметр, к которому относится фрагмент: от последнего разделителя (не раньше begin -
    конца предыдущего условия) до конца значения; при нескольких параметрах без разделителей -
    последняя пара «название: значение». Запятая между цифрами («99,99») - не разделитель."""
    separators = list(re.finditer(r"[\n;]|,(?!\d)|(?<!\d)\d{1,2}\.\s*(?=[^\d\s])", text[begin:start]))
    if separators:
        begin += separators[-1].end()
    label = re.sub(r"\s+", " ", text[begin:end]).strip(" -:")
    pair = re.search(r"((?:[^\W\d_]{2,}\s+){0,2}[^\W\d_]{2,}\s*:[^:]*)$", label)
    if pair and pair.start() > 0:
        label = pair.group(1)
    return label if len(label) <= max_chars else "…" + label[-max_chars:]


def parse_constraints(text: str) -> List[Constraint]:
    """Числовые условия текста; более специфичные шаблоны (допуск, диапазон, min/max) раньше точных."""
    folded = _fold(text or "")
    taken: List[Tuple[int, int]] = []
    hits: List[Tuple[int, int, List[Constraint]]] = []

    def free(a, b):
        return all(b <= s or a >= e for s, e in taken)

    connectors = [(m.start(), m.end()) for m in CONNECTOR_RE.finditer(folded)]
    for kind, rx in PATTERNS:
        for m in rx.finditer(folded):
            if not free(m.start(), m.end()) or not all(m.end() <= s or m.start() >= e for s, e in connectors):
                continue
            built = _build(kind, m)
            if not built:
                continue
            taken.append((m.start(), m.end()))
            hits.append((m.start(), m.end(), built))

    result, ends = [], [0]
    for start, end, built in sorted(hits, key=lambda h: h[0]):
        label = label_before(text, start, end, begin=ends[-1])
        if not re.search(r"[^\W\d_]{3,}", re.sub(UNIT_RX, "", _fold(label))) and len(ends) > 1:
            # «Impedanță 10 Hz ≤ 150 ohm»: подпись «≤ 150 ohm» без названия - берём с предыдущим значением
            label = label_before(text, start, end, begin=ends[-2])
        for c in built:
            c.text = label
            result.append(c)
        ends.append(end)
    return result


def _build(kind: str, m) -> List[Constraint]:
    g = m.groups()
    if kind == "range_tol":
        u_hi = _unit(g[3])
        u_lo = _unit(g[1]) or u_hi
        if not u_hi or u_lo[0] != u_hi[0]:
            return []
        tol = _num(g[4]) * (_unit(g[5]) or u_hi)[1]
        lo, hi = _num(g[0]) * u_lo[1], _num(g[2]) * u_hi[1]
        return [Constraint(u_hi[0], min(lo, hi) - tol, max(lo, hi) + tol, kind, "")]
    if kind == "tol":
        u = _unit(g[1]) or _unit(g[3])
        if not u:
            return []
        family, k = u
        tol_k = (_unit(g[3]) or u)[1]
        center, tol = _num(g[0]) * k, _num(g[2]) * tol_k
        return [Constraint(family, center - tol, center + tol, kind, "")]
    if kind == "dims":
        u_b = _unit(g[3])
        u_a = _unit(g[1]) or u_b
        if not u_b:
            return []
        return [Constraint(u_a[0], _num(g[0]) * u_a[1], _num(g[0]) * u_a[1], "exact", ""),
                Constraint(u_b[0], _num(g[2]) * u_b[1], _num(g[2]) * u_b[1], "exact", "")]
    if kind == "range":
        u_hi = _unit(g[3])
        u_lo = _unit(g[1]) or u_hi
        if not u_hi or u_lo[0] != u_hi[0]:
            return []
        lo, hi = _num(g[0]) * u_lo[1], _num(g[2]) * u_hi[1]
        return [Constraint(u_hi[0], min(lo, hi), max(lo, hi), kind, "")] if lo != hi else []
    u = _unit(g[1])
    if not u:
        return []
    value = _num(g[0]) * u[1]
    if kind == "min":
        return [Constraint(u[0], value, None, kind, "")]
    if kind == "max":
        return [Constraint(u[0], None, value, kind, "")]
    return [Constraint(u[0], value, value, kind, "")]


def _words(text: str) -> List[str]:
    return re.findall(r"[a-z0-9]{3,}", normalize(text))


def is_copy(offered: str, required: str) -> bool:
    """Предложенная спецификация повторяет требование почти слово в слово."""
    req = _words(required)
    if len(req) < 3:
        return False
    have = set(_words(offered))
    return sum(w in have for w in req) / len(req) >= COPY_SHARE


@dataclass
class DeclarativeCheck:
    conflicts: List[str]       # «Lungime: ≥ 15 cm (A22: 10 cm)»
    missing: List[str]         # условия требования без значения в Anexa 22
    copy: bool                 # спецификация - копия требования
    matched: List[Tuple[Constraint, Constraint]]  # (условие требования, значение участника)


def _label_words(text: str) -> set:
    return {w for w in re.findall(r"[a-z]{4,}", normalize(text))}


# Слова, которые делают параметры разными при общем названии: «diametru exterior» и «diametru interior»
DISTINCT_PAIRS = [("interior", "exterior"), ("intern", "extern"), ("inspir", "expir"),
                  ("adult", "pediatric"), ("lungime", "latime")]


def _related(a: str, b: str) -> bool:
    """Подписи про один и тот же параметр: есть общие слова и нет различающих пар."""
    wa, wb = _label_words(a), _label_words(b)
    if not wa & wb:
        return False
    for x, y in DISTINCT_PAIRS:
        has_a = (any(x in w for w in wa), any(y in w for w in wa))
        has_b = (any(x in w for w in wb), any(y in w for w in wb))
        if (has_a[0] and has_b[1] and not has_a[1]) or (has_a[1] and has_b[0] and not has_a[0]):
            return False
    return True


def check_declarative(offered: str, required: str) -> DeclarativeCheck:
    """
    Каждое условие требования против значений того же семейства единиц в Anexa 22:
      значение укладывается в условие или пересекается с ним («150-180 cm» и «≥ 160 cm») - ок;
      значение явно про тот же параметр и не пересекается - расхождение;
      значения того же семейства нет - условие не упомянуто в Anexa 22.
    """
    req = parse_constraints(required)
    off = parse_constraints(offered)
    conflicts, missing, matched = [], [], []
    for c in req:
        same = [o for o in off if o.family == c.family]
        ok = [o for o in same if c.satisfied_by(o) or c.overlaps(o)]
        if ok:
            # при нескольких подходящих - с наибольшим совпадением слов названия параметра
            best = max(ok, key=lambda o: len(_label_words(o.text) & _label_words(c.text)))
            matched.append((c, best))
            continue
        # Расхождение - только если значение явно про тот же параметр (общие слова в названии);
        # иначе это, скорее всего, другой размер того же семейства (диаметр против длины)
        related = [o for o in same if _related(o.text, c.text)]
        if related:
            conflicts.append(f"{c.text} (A22: {related[0].text})")
        else:
            missing.append(c.text)  # про этот параметр в Anexa 22 ничего нет («exterior» при «interior»)
    return DeclarativeCheck(list(dict.fromkeys(conflicts)), list(dict.fromkeys(missing)),
                            is_copy(offered, required), matched)
