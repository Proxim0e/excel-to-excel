"""
Уровень 1 проверки - «юридическое совпадение»: предложение участника (Anexa 22) против запроса лота.
Каждое условие запроса должно быть в предложении явно и выполнено (решение Ivan 04.11.2026); хоть одно
не найдено или нарушено - оферта «Alternativa» (синий).

Числовые условия запроса:
  «≥ 15 cm», «min. 3m», «Lungimea minima 150cm»          -> нижняя граница
  «≤ 60 ml», «max 5 microni», «nu mai mult de 10»         -> верхняя граница
  «70 mm (+-5mm)», «200cm (±10cm)», «240-310 cm»         -> диапазон
  «500 ml», «Ø 49mm»                                       -> точное значение
  «30x30 mm»                                               -> размер целиком: 50x50 - другой размер
Единицы приводятся к одной шкале внутри семейства (мм/см/м, мл/л, г/кг, мин/ч).
Не размеры: «2,04 cm H2O» (давление), «15M (masculin)», «22F-15M» (разъёмы), «18 G» (калибр иглы).
Значение засчитывается, только если подпись про тот же параметр («diametru ≥ 5 cm» не закрывается
«lungime 150 cm»); значение без подписи - только если в запросе одно условие этого семейства.

Совместимость: аппарат из запроса («compatibil cu Dräger 120», «pentru Electrocoagulator MBC-200») и коды
аппарата из названия лота («Cablu ECG Carescape B450, GE») должны быть названы в предложении.
Качественные признаки (steril, unică folosință / reutilizabil, adult / pediatric / nou-născut, fără latex):
названы в запросе - должны быть в предложении, противоположные - расхождение.
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
# «18 G», «22G» - калибр иглы/канюли, а не граммы (ищется в исходном тексте: заглавная G)
GAUGE_RE = re.compile(r"(?<![\w.,])\d{1,2}\s?G\b")
EPS = 1e-6
COPY_SHARE = 0.95   # спецификация - копия требования, если в ней есть 95% его слов


@dataclass
class Constraint:
    family: str
    lo: Optional[float]      # в базовых единицах семейства; None - без нижней границы
    hi: Optional[float]
    kind: str                # tol / range / min / max / exact
    text: str                # исходный фрагмент: «Lungime: ≥ 15 cm»
    dims: Optional[Tuple[float, ...]] = None  # размер AxB целиком (по возрастанию) - у обеих сторон

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
    if len(folded) == len(text or ""):
        connectors += [(m.start(), m.end()) for m in GAUGE_RE.finditer(text)]
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
        a, b = _num(g[0]) * u_a[1], _num(g[2]) * u_b[1]
        dims = tuple(sorted((a, b))) if u_a[0] == u_b[0] else None
        return [Constraint(u_a[0], a, a, "exact", "", dims), Constraint(u_b[0], b, b, "exact", "", dims)]
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


OK, ALTERNATIVA = "ok", "alternativa"


@dataclass
class DeclarativeCheck:
    conflicts: List[str]       # «Lungime: ≥ 15 cm (A22: 10 cm)», «steril (A22: nesteril)»
    missing: List[str]         # условия запроса, которых нет в Anexa 22 (в т.ч. совместимость)
    copy: bool                 # спецификация - копия требования
    matched: List[Tuple[Constraint, Constraint]]  # (условие требования, значение участника)
    # Незакрытые числовые условия с текстом пометки - их могут закрыть документы уточнений
    open_conditions: List[Tuple[Constraint, str]] = None

    @property
    def status(self) -> str:
        """ok - всё из запроса названо и выполнено; alternativa - нет данных или противоречие."""
        return ALTERNATIVA if (self.conflicts or self.missing) else OK


# Слова подписи, которые ничего не говорят о параметре: «minim 150 cm» - подписи нет
GENERIC_WORDS = {"minim", "minima", "minimum", "maxim", "maxima", "maximum", "pana", "mult", "putin",
                 "este", "sunt", "fara", "pentru", "conform", "valoare", "marime", "marimea", "dimensiune",
                 "dimensiuni", "microni", "micron", "cicluri", "bari"}


def _label_words(text: str) -> set:
    return {w for w in re.findall(r"[a-z]{4,}", normalize(text))} - GENERIC_WORDS


# Слова, которые делают параметры разными при общем названии: «diametru exterior» и «diametru interior»
DISTINCT_PAIRS = [("interior", "exterior"), ("intern", "extern"), ("inspir", "expir"),
                  ("adult", "pediatric"), ("lungime", "latime")]


def _related(a: str, b: str) -> bool:
    """Подписи про один и тот же параметр: есть общие слова и нет различающих пар. Слова сравниваются по
    основе (первые 6 букв): «Manșete» и «Manseta», «Lungime» и «Lungimea»."""
    wa, wb = _label_words(a), _label_words(b)
    if not {w[:6] for w in wa} & {w[:6] for w in wb}:
        return False
    for x, y in DISTINCT_PAIRS:
        has_a = (any(x in w for w in wa), any(y in w for w in wa))
        has_b = (any(x in w for w in wb), any(y in w for w in wb))
        if (has_a[0] and has_b[1] and not has_a[1]) or (has_a[1] and has_b[0] and not has_a[0]):
            return False
    return True


def _fmt_dims(values, family: str) -> str:
    unit = {"len": "mm", "vol": "ml", "mass": "g", "time": "min"}.get(family, "")
    return "x".join(f"{v:g}" for v in values) + (f" {unit}" if unit else "")


def _check_numbers(req: List[Constraint], off: List[Constraint]):
    """Числовые условия: значение засчитывается только при родственной подписи (или без подписи, если
    в запросе одно условие этого семейства); размер AxB - целиком."""
    conflicts, missing, matched, open_conditions = [], [], [], []
    # Сколько разных условий каждого семейства в запросе (одно и то же «5mm» дважды - одно условие)
    per_family = {}
    for c in req:
        per_family.setdefault(c.family, set()).add(c.dims or (c.lo, c.hi))
    per_family = {k: len(v) for k, v in per_family.items()}
    done_dims = set()
    for c in req:
        if c.dims:
            key = (c.family, c.dims)
            if key in done_dims:
                continue
            done_dims.add(key)
            offered_dims = sorted({o.dims for o in off if o.dims and o.family == c.family})
            if c.dims in offered_dims:
                matched.append((c, next(o for o in off if o.dims == c.dims)))
                continue
            if offered_dims:
                # «30x30 mm» против «50x50 mm» - другой размер
                note = f"{c.text} (A22: {_fmt_dims(offered_dims[0], c.family)})"
                conflicts.append(note)
                open_conditions.append((c, note))
                continue
            # в предложении размер не парой: стороны ищем как обычные значения
        same = [o for o in off if o.family == c.family]
        fits = [o for o in same if c.satisfied_by(o) or c.overlaps(o)]
        ok = [o for o in fits if _related(o.text, c.text)]
        # Условие без подписи («…, 5mm», «10 microni») сравнить по подписи нельзя - годится любое подходящее
        # значение; значение без подписи - если в запросе одно условие этого семейства
        req_bare = not _label_words(c.text)
        if not ok and req_bare:
            ok = fits
        elif not ok and per_family[c.family] <= 1:
            ok = [o for o in fits if not _label_words(o.text)]
        if ok:
            # при нескольких подходящих - с наибольшим совпадением слов названия параметра
            best = max(ok, key=lambda o: len(_label_words(o.text) & _label_words(c.text)))
            matched.append((c, best))
            continue
        # Расхождение - только если значение явно про тот же параметр (общие слова в названии);
        # иначе про этот параметр в Anexa 22 ничего нет («diametru» при «lungime», «exterior» при «interior»)
        related = [o for o in same if _related(o.text, c.text)]
        if not related and (req_bare or per_family[c.family] <= 1):
            # «1,21m» против «2.4m»: обе без подписи - про один параметр, если иначе сравнить не с чем
            related = [o for o in same if not _label_words(o.text)]
        note = f"{c.text} (A22: {related[0].text})" if related else c.text
        (conflicts if related else missing).append(note)
        open_conditions.append((c, note))
    return conflicts, missing, matched, open_conditions


# --- совместимость ------------------------------------------------------------------------------------
COMPAT_CUE = re.compile(r"(?:compatibil\w*|utilizare cu|utilizat\w* cu|se utilizeaza cu|pentru)\s+(?:cu\s+)?"
                        r"(?P<target>[^.;\n]{3,70})", re.IGNORECASE)
# Число с единицей или количество - не код аппарата: «30W», «90cm», «14x17inch», «3ani»
QUANTITY_TOKEN = re.compile(r"\d+(?:[.,]\d+)?(?:[x×х]\d+(?:[.,]\d+)?)*(?:" + UNIT_RX +
                            r"|inch|ani|an|luni|buc|mkm|mcr|mcm|kg|w)?", re.IGNORECASE)
# Слова с заглавной буквы в цели совместимости, которые не марка аппарата
NOT_BRANDS = {"lot", "lotul", "tip", "set", "kit", "adult", "adulti", "copii", "pacient", "pacienti", "monitor",
              "monitorul", "aparat", "aparatul", "ventilator", "ventilatorul", "sistem", "sistemul", "dispozitiv",
              "electrocoagulator", "autoclav", "autoclavul", "sterilizator", "pompa", "cablu", "conform", "ecg",
              "spo2", "nibp", "eeg", "masina", "generator", "imprimanta", "filtru", "seria"}


def _code_tokens(text: str) -> List[str]:
    """Коды аппарата: слова с буквами и цифрами («B450», «MX550», «SV650», «S1008-E2», «MBC-200»)."""
    codes = []
    for tok in re.findall(r"[A-Za-z0-9][\w\-/]*", text or ""):
        tok = tok.strip("-/_")
        if len(tok) < 2 or not re.search(r"[A-Za-z]", tok) or not re.search(r"\d", tok):
            continue
        if QUANTITY_TOKEN.fullmatch(tok):
            continue
        codes.append(tok)
    return list(dict.fromkeys(codes))


def _compact(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", normalize(text or ""))


def compatibility_targets(required: str, title: str = "") -> List[Tuple[str, List[str], List[str]]]:
    """[(фрагмент, коды, марки)] - аппарат, с которым изделие должно быть совместимо: из «compatibil cu …»,
    «pentru …» запроса и коды аппарата из названия лота."""
    targets = []
    for m in COMPAT_CUE.finditer(required or ""):
        phrase = m.group("target").strip(" ,:")
        codes = _code_tokens(phrase)
        # «Drager 120»: число сразу после марки - тоже модель
        for n in re.findall(r"(?<=[A-Za-zĂÎȘȚÂăîșțâ]\s)\d{2,4}\b", phrase):
            if n not in codes:
                codes.append(n)
        brands = [w for w in re.findall(r"\b[A-ZĂÎȘȚÂ][\wăîșțâ\-]{2,}", phrase)
                  if normalize(w) not in NOT_BRANDS and not re.search(r"\d", w)]
        if codes:
            targets.append((phrase, codes, brands))
    title = re.sub(r"^\s*lot(?:ul)?\s*(?:nr\.?)?\s*\d+\s*", "", title or "", flags=re.IGNORECASE)
    known = {_compact(c) for t in targets for c in t[1]}
    title_codes = [c for c in _code_tokens(title) if _compact(c) not in known]
    if title_codes:
        targets.append((title.strip(), title_codes, []))
    return targets


def _check_compatibility(offered: str, required: str, title: str) -> List[str]:
    have = _compact(offered)
    missing = []
    for phrase, codes, brands in compatibility_targets(required, title):
        lost = [c for c in codes if _compact(c) not in have]
        if brands and not any(_compact(b) in have for b in brands):
            lost.append(brands[0])
        if lost:
            missing.append(f"compatibilitate neindicată: {phrase[:60]} ({', '.join(lost)})")
    return missing


# --- качественные признаки ----------------------------------------------------------------------------
# Группа: [(признак, шаблон по normalize-тексту)]; признаки одной группы взаимоисключающие
QUALITY_GROUPS = [
    [("steril", r"(?<!ne)(?<!non)(?<!non-)\bsteril[ae]?\b"), ("nesteril", r"\bne-?steril|\bnon-?steril")],
    [("unică folosință", r"unic[ae]? (?:de )?folosint|de unica utilizare|single[- ]use|disposable|"
                         r"o singura (?:data|utilizare)"),
     ("reutilizabil", r"reutiliz|reusable|refolosibil")],
    [("adult", r"\badult"), ("pediatric", r"pediatric|\bcopii\b"), ("nou-născut", r"neonat|nou[- ]?nascu")],
    [("fără latex", r"(?:fara|free of|nu contine)\s+latex|latex[- ]?free")],
]


def _check_quality(offered: str, required: str):
    req, off = normalize(required or ""), normalize(offered or "")
    conflicts, missing = [], []
    for group in QUALITY_GROUPS:
        asked = [name for name, rx in group if re.search(rx, req)]
        if not asked:
            continue
        given = [name for name, rx in group if re.search(rx, off)]
        for name in asked:
            if name in given:
                continue
            others = [g for g in given if g not in asked]
            if others:
                conflicts.append(f"{name} (A22: {', '.join(others)})")
            else:
                missing.append(name)
    return conflicts, missing


def check_declarative(offered: str, required: str, title: str = "", offered_extra: str = "") -> DeclarativeCheck:
    """
    Юридическое совпадение: каждое условие запроса (B11 и коды аппарата из названия лота) явно названо и
    выполнено в предложении. offered - предложенная спецификация (из неё числа); offered_extra - модель и
    название позиции из Anexa 22 (совместимость и признаки часто указаны там).
    """
    req = parse_constraints(required)
    off = parse_constraints(offered)
    conflicts, missing, matched, open_conditions = _check_numbers(req, off)
    whole = f"{offered or ''} {offered_extra or ''}"
    q_conflicts, q_missing = _check_quality(whole, required)
    conflicts += q_conflicts
    missing += q_missing + _check_compatibility(whole, required, title)
    return DeclarativeCheck(list(dict.fromkeys(conflicts)), list(dict.fromkeys(missing)),
                            is_copy(offered, required), matched, open_conditions)
