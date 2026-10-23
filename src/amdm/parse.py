"""
Разбор ячеек Excel оценки: строка 3 (производитель / страна / модель) и строка 4 (регистрация или сертификаты).

Реальные примеры строки 4 (CDM27_B, CPAS27-P1):
    'DM000365870 Rg04-000183 05-08-2022/ pentru DM'
    'DM000377731\\nRg04-000243 17-10-2022'
    'DM000850027 (DM pentru dispozitiv)'
    'DM000... ( piese)/ISO'
    'CE/ISO', 'CE, ISO 13485', 'ISO 11140-1 2014'
"""
from __future__ import annotations

import re
import unicodedata
from typing import Dict, List, Optional, Tuple

DM_RE = re.compile(r'\bDM\s?0*(\d{6,})\b', re.I)
ORDER_RE = re.compile(r'\b(Rg\s?\d{2}\s?-\s?\d{2,6})\b', re.I)
DATE_RE = re.compile(r'\b(\d{1,2})[-./](\d{1,2})[-./](\d{4})\b')
QUALITY_RE = re.compile(r'\b(ISO\s?13485|ISO\s?9001|ISO(?:\s?[\d\-:]+)?|CE|DoC|declara\w* de conformitate)\b', re.I)
NOTE_RE = re.compile(r'\(([^)]*)\)|/\s*(pentru\s+DM)', re.I)

ROW3_LABELS = {
    "producer": re.compile(r'^\s*produc[aă]tor(?:ul)?\s*:\s*', re.I),
    "country": re.compile(r'^\s*[țţt]ara(?:\s+de\s+origine)?\s*:\s*', re.I),
    "model": re.compile(r'^\s*model(?:ul)?(?:\s+art(?:icolului)?)?\s*:\s*', re.I),
}


def fold(s: str) -> str:
    """Нижний регистр без диакритики."""
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(ch for ch in s if not unicodedata.combining(ch)).lower()


def normalize_dm(raw: str) -> Optional[str]:
    """'DM 655298', 'dm000655298' -> 'DM000655298' (9 цифр, как в реестре)."""
    m = DM_RE.search(raw or "")
    return f"DM{int(m.group(1)):09d}" if m else None


def parse_row4(text: Optional[str]) -> Dict:
    t = str(text or "").strip()
    if t.lower() == "none":
        t = ""
    dms: List[str] = []
    for m in DM_RE.finditer(t):
        dm = f"DM{int(m.group(1)):09d}"
        if dm not in dms:
            dms.append(dm)
    order = None
    om = ORDER_RE.search(t)
    if om:
        order = re.sub(r'\s', '', om.group(1))
        order = order[0].upper() + order[1:]
    date = None
    dm_ = DATE_RE.search(t)
    if dm_:
        d, mth, y = (int(x) for x in dm_.groups())
        if 1 <= d <= 31 and 1 <= mth <= 12:
            date = f"{y:04d}-{mth:02d}-{d:02d}"
    rest = DM_RE.sub(" ", t)
    quality = []
    for m in QUALITY_RE.finditer(rest):
        q = re.sub(r'\s+', ' ', m.group(1)).upper().replace("DOC", "DoC")
        if q not in quality:
            quality.append(q)
    notes = [n for g in NOTE_RE.findall(t) for n in g if n.strip()]
    return {"dm_numbers": dms, "order": order, "order_date": date,
            "quality_docs": quality, "note": "; ".join(n.strip() for n in notes) or None}


def parse_row3(text: Optional[str]) -> Dict[str, Optional[str]]:
    """
    'Producator: BSS MEDICAL\\nSUPPLY CO.,\\nLIMITED\\nȚara: China\\nModel:  Electrod\\nECG 8 ore'
    -> producer='BSS MEDICAL SUPPLY CO., LIMITED', country='China', model='Electrod ECG 8 ore'
    Строки без метки — продолжение предыдущего поля (перенос в ячейке).
    """
    result: Dict[str, Optional[str]] = {"producer": None, "country": None, "model": None}
    t = str(text or "")
    if t.strip().lower() in ("", "none"):
        return result
    current = None
    parts: Dict[str, List[str]] = {k: [] for k in result}
    for line in t.split("\n"):
        matched = False
        for key, rx in ROW3_LABELS.items():
            if rx.match(line):
                current = key
                rest = rx.sub("", line, count=1)
                if rest.strip():
                    parts[key].append(rest.strip())
                matched = True
                break
        if not matched and current and line.strip():
            parts[current].append(line.strip())
    for key, chunks in parts.items():
        value = re.sub(r'\s+', ' ', " ".join(chunks)).strip()
        result[key] = value or None
    return result


def model_tokens(text: Optional[str]) -> List[str]:
    """
    Кандидаты на модель / каталожный номер: коды с цифрами ('630-240-00B', 'N8250731SP', 'JD17O01', '41902.8').
    Слова без цифр ('Electrod', 'Line') не годятся как идентификатор.
    """
    if not text:
        return []
    tokens = re.findall(r'[A-Za-z0-9][A-Za-z0-9.\-/_]*[A-Za-z0-9]|\d', text)
    out = []
    for tok in tokens:
        tok = tok.strip(".-/_")
        if re.search(r'\d', tok) and len(tok) >= 3 and tok not in out:
            out.append(tok)
    return out


def compact(s: Optional[str]) -> str:
    """Для сравнения кодов: без регистра, пробелов, точек, дефисов, слэшей."""
    return re.sub(r'[\s.\-/_]', '', fold(s or ""))
