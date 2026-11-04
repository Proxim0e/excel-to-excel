"""
Данные для формы карточки товара в окне (без Tk - проверяется тестами).

- пункты требования лота / спецификации оферты -> строки «параметр - значение» (быстрое заполнение);
- экономические агенты, предлагавшие эту модель: этот тендер, этот год, прошлый год, раньше (оценки новых
  тендеров и история рабочих Excel);
- победители прошлых лет по этой модели (принятые оферты);
- технические документы участника из манифеста тендера и их текст (просмотр с поиском).
"""
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from src.amdm.parse import compact, fold, model_tokens
from src.evaluate.cards import spec_items, split_param

# Технические документы - первыми: в них параметры изделия
DOC_ORDER = ("datasheet", "anexa22", "anexa22+23", "anexa23", "ce_doc", "iso", "amdm", "autorizatie_producator",
             "other", "no_text")
DOC_LABELS = {"datasheet": "Fișa tehnică", "anexa22": "Anexa 22", "anexa22+23": "Anexa 22+23", "anexa23": "Anexa 23",
              "ce_doc": "CE", "iso": "ISO", "amdm": "AMDM", "autorizatie_producator": "Autorizație", "other": "Другое",
              "no_text": "Скан", "declaratie": "Declarație", "duae": "DUAE", "garantie_oferta": "Garanție",
              "cerere_participare": "Cerere", "raport_financiar": "Raport financiar", "procura": "Procură",
              "extras_registru": "Extras", "mediu_eee": "Mediu EEE"}

# --- пункты требования -> параметры --------------------------------------------------------------------------
def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", compact(name).lower())[:12]


def param_rows(params: Dict[str, str], requirement: str, offered: str = "") -> List[Dict]:
    """Строки таблицы параметров: значение карточки + варианты на выбор (из оферты и из требования лота).
    Пункты требования, которых нет в карточке, - отдельными строками с пустым значением (их легко заполнить)."""
    rows: List[Dict] = []
    seen = {}
    for name, value in (params or {}).items():
        value = str(value)
        # старое предзаполнение: ключ = начало текста, значение = весь текст («diametru 38 - 47mm»)
        name2, value2 = split_param(value) if value.startswith(str(name)) and value != "da" else (name, value)
        k = _key(name2)
        if k in seen:
            if value2 not in rows[seen[k]]["choices"]:
                rows[seen[k]]["choices"].append(value2)
            continue
        seen[k] = len(rows)
        rows.append({"name": name2, "value": value2, "choices": [value2] if value2 else []})
    for source, text in (("оферта", offered), ("требование", requirement)):
        for item in spec_items(text):
            name, value = split_param(item)
            k = _key(name)
            if k in seen:
                row = rows[seen[k]]
                if value not in row["choices"]:
                    row["choices"].append(value)
            elif source == "требование":
                seen[k] = len(rows)
                rows.append({"name": name, "value": "", "choices": [value], "required": item})
    for row in rows:   # «10 Hz» и «10 Hz ≤ 150 ohm» - берём полное
        longer = [c for c in row["choices"] if row["value"] and c != row["value"] and c.startswith(row["value"])]
        if longer:
            row["value"] = max(longer, key=len)
    return rows


# --- экономические агенты и победители ----------------------------------------------------------------------
def _codes(card: Dict) -> List[str]:
    codes = [compact(c) for c in re.split(r"[,;]", card.get("codes") or "") if len(compact(c)) >= 3]
    return codes or [compact(t) for t in model_tokens(card.get("model") or "") if len(compact(t)) >= 3]


def _dms(card: Dict) -> List[str]:
    """Номера DM карточки; поле пустое - из ключа модели «DM:DM000152480+DM…»."""
    from src.history.cards import split_dms
    text = card.get("dm") or ((card.get("model_key") or "")[3:] if (card.get("model_key") or "").startswith("DM:") else "")
    return split_dms(text)


def _period(year: Optional[int], tender_id: str, current_tender: str, current_year: int) -> str:
    """«этот тендер», «этот год (2026)», «прошлый год (2025)» или просто год - по году публикации тендера."""
    if tender_id == current_tender:
        return "этот тендер"
    if not year:
        return "год неизвестен"
    if year == current_year:
        return f"этот год ({year})"
    if year == current_year - 1:
        return f"прошлый год ({year})"
    return str(year)


COMPANY_NOISE = {"im", "îm", "m", "sc", "s", "c", "r", "l", "srl", "sa", "s.a", "s.r.l", "ii", "îi", "ici", "ltd", "llc",
                 "co", "com", "of", "de"}


def company_key(name: str) -> str:
    """«IM Vivamed International SRL», «M Vivamed International SRL», «Vivamed International S.R.L.» - одна
    фирма: без формы собственности, кавычек и знаков."""
    words = re.findall(r"[^\W_]+", fold(name or "").lower())
    words = [w for w in words if w not in COMPANY_NOISE]
    return " ".join(words) or (name or "").strip().lower()


def history_card(kb: sqlite3.Connection, card: Dict) -> Dict:
    """Последняя оферта этой модели в истории (по номеру DM): производитель, модель, DM, название лота -
    чтобы дозаполнить пустую карточку."""
    dms = _dms(card)
    if not dms:
        return {}
    size = _card_size(card)
    for d in dms:
        rows = kb.execute("SELECT o.producer, o.model, o.dm, l.title FROM current_offers o "
                          "LEFT JOIN lots l ON l.snapshot_id = o.snapshot_id AND l.sheet = o.sheet "
                          "WHERE o.dm LIKE ? ORDER BY o.snapshot_id DESC", (f"%{d}%",)).fetchall()
        row = next((r for r in rows if not _other_size(size, r[3])), None)
        if row:
            return {"producer": row[0], "model": row[1], "dm": row[2],
                    "name": re.sub(r"^\s*lot\s*nr\.?\s*\d+\s*", "", row[3] or "", flags=re.IGNORECASE)}
    return {}


def _card_size(card: Dict) -> str:
    from src.history.cards import key_size, size_of
    return key_size(card.get("model_key")) or size_of(card.get("name"))


def _other_size(card_size: str, lot_title: Optional[str]) -> bool:
    """Оферта по лоту другого типоразмера (код модели и DM у плёнок 20×25 и 35×43 общие) - не эта карточка."""
    from src.history.cards import size_of
    size = size_of(lot_title)
    return bool(card_size and size and size != card_size)


def _offer_matches(dm: str, model: str, dms: List[str], codes: List[str]) -> bool:
    if dms and any(d in (dm or "") for d in dms):
        return True
    m = compact(model or "")
    return bool(codes) and any(c in m for c in codes)


def agent_candidates(kb: sqlite3.Connection, card: Dict, tender_id: str, participant: str = "") -> List[Dict]:
    """Кто предлагал эту модель: {name, period, tender_id, lot_no, decision, won}. Текущий участник - первым."""
    from src.history.tender_year import fallback_year, known_years
    dms, codes = _dms(card), _codes(card)
    out: Dict[Tuple[str, str], Dict] = {}
    years = known_years(kb)                       # год публикации с achizitii.md (кеш в базе)
    current_year = years.get(tender_id) or datetime.now().year

    def add(name, tid, lot, year, decision, won):
        year = years.get(tid) or year
        period = _period(year, tid, tender_id, current_year)
        key = (company_key(name), tid)
        if not name.strip() or (key in out and not won):
            return
        out[key] = {"name": name.strip(), "period": period, "tender_id": tid, "lot_no": lot, "decision": decision,
                    "won": won, "year": year}

    if participant:
        add(participant, tender_id, None, current_year, "", False)
    for name, tid, lot, status, at in kb.execute(
            "SELECT participant, tender_id, lot_no, status, decided_at FROM evaluations WHERE model_key = ?",
            (card.get("model_key"),)):
        add(name, tid, lot, int(at[:4]) if at and at[:4].isdigit() else None, status, status == "corespunde")
    size = _card_size(card)
    rows = kb.execute("SELECT o.participant, o.tender_id, o.lot_no, o.dm, o.model, o.final_decision, "
                      "s.file_mtime, s.source_file, l.title FROM current_offers o JOIN snapshots s ON s.id = o.snapshot_id "
                      "LEFT JOIN lots l ON l.snapshot_id = o.snapshot_id AND l.sheet = o.sheet").fetchall()
    for name, tid, lot, dm, model, decision, mtime, source, title in rows:
        if _offer_matches(dm, model, dms, codes) and not _other_size(size, title):
            add(name, tid, lot, fallback_year(source, mtime), decision, decision == "accepted")
    # одна фирма в разных написаниях - имя как у текущего участника / самое частое
    names: Dict[str, Dict[str, int]] = {}
    for a in out.values():
        names.setdefault(company_key(a["name"]), {}).setdefault(a["name"], 0)
        names[company_key(a["name"])][a["name"]] += 1
    for a in out.values():
        variants = names[company_key(a["name"])]
        a["name"] = participant if participant and company_key(participant) == company_key(a["name"]) else \
            max(variants, key=variants.get)
    return sorted(out.values(), key=lambda a: (a["name"] != participant, a["period"] != "этот тендер",
                                               -(a["year"] or 0), a["name"]))


def agent_label(a: Dict) -> str:
    where = a["period"] + (f" ({a['tender_id']}" + (f", лот {a['lot_no']}" if a.get("lot_no") else "") + ")"
                           if a["period"] != "этот тендер" else (f", лот {a['lot_no']}" if a.get("lot_no") else ""))
    return f"{a['name']} - {where}" + (" - победитель" if a.get("won") else "")


def winner_candidates(agents: List[Dict]) -> List[str]:
    """Победители прошлых лет по этой модели: «участник (год, тендер)»."""
    out = []
    for a in agents:
        if a.get("won") and a["period"] != "этот тендер":
            label = f"{a['name']} ({a.get('year') or '?'}, {a['tender_id']})"
            if label not in out:
                out.append(label)
    return out


# --- цены по годам -------------------------------------------------------------------------------------------
DECISION_LABELS = {"accepted": "принят", "rejected": "отклонён", "alternative": "альтернатива",
                   "clarification": "уточнение", "not_evaluated": "не оценён", "working_group": "раб. группа",
                   "samples": "образцы", "unknown": "?", "corespunde": "corespunde", "nu_corespunde": "nu corespunde",
                   "alternativa": "alternativa", "clarificare": "clarificare"}

_RESULT_CACHE: Dict[str, Dict] = {}


def current_offer_price(result: Optional[Path], lot_no: Optional[int], participant: str) -> Optional[Dict]:
    """Цена оферты в текущем тендере из result-файла: A1 количество, D8 база, строка 8 участника - сумма
    (строка 7 - формула, её значение openpyxl не считает). Книга читается один раз и запоминается."""
    if not result or not lot_no or not Path(result).is_file():
        return None
    key = str(result)
    if key not in _RESULT_CACHE:
        from openpyxl import load_workbook
        sheets = {}
        try:
            wb = load_workbook(result, read_only=True, data_only=True)
            for ws in wb.worksheets:
                m = re.match(r"\s*lot\s*(\d+)", ws.title, re.IGNORECASE)
                if m:
                    sheets[int(m.group(1))] = [list(r) for r in ws.iter_rows(min_row=1, max_row=8, values_only=True)]
            wb.close()
        except Exception:  # файл открыт / повреждён - без цены текущего тендера
            pass
        _RESULT_CACHE[key] = sheets
    rows = _RESULT_CACHE[key].get(int(lot_no))
    if not rows or len(rows) < 8:
        return None

    def num(v):
        try:
            return float(str(v).replace(",", ".")) if v not in (None, "") else None
        except ValueError:
            return None
    qty, base = num(rows[0][0]), num(rows[7][3]) if len(rows[7]) > 3 else None
    for c, v in enumerate(rows[1]):
        if isinstance(v, str) and v.lower().startswith("ofertant") and \
                company_key(v.split(":", 1)[-1]) == company_key(participant):
            value = num(rows[7][c]) if c < len(rows[7]) else None
            return {"value": value, "quantity": qty, "unit_price": value / qty if value and qty else None,
                    "base_price": base / qty if base and qty else None, "ratio": value / base if value and base else None}
    return None


def price_history(kb: sqlite3.Connection, card: Dict, tender_id: str = "", participant: str = "",
                  current: Optional[Dict] = None, lot_title: str = "") -> List[Dict]:
    """Все оферты этой модели с ценами: текущий тендер и прошлые, новые - сверху.
    {year, period, tender_id, lot_no, participant, unit_price, base_price, quantity, value, ratio, decision, won}."""
    from src.history.tender_year import fallback_year, known_years
    dms, codes = _dms(card), _codes(card)
    years = known_years(kb)
    current_year = years.get(tender_id) or datetime.now().year
    out: List[Dict] = []
    if current:
        out.append({"year": current_year, "period": "этот тендер", "tender_id": tender_id, "lot_no": None,
                    "participant": participant, "decision": "", "won": False, "lot_title": lot_title, **current})
    rows = kb.execute("SELECT o.participant, o.tender_id, o.lot_no, o.dm, o.model, o.final_decision, o.unit_price, "
                      "o.offer_value, o.price_ratio, l.quantity, l.base_price, s.file_mtime, s.source_file, l.title "
                      "FROM current_offers o JOIN snapshots s ON s.id = o.snapshot_id "
                      "LEFT JOIN lots l ON l.snapshot_id = o.snapshot_id AND l.sheet = o.sheet").fetchall()
    size = _card_size(card)
    for name, tid, lot, dm, model, decision, unit, value, ratio, qty, base_price, mtime, source, title in rows:
        if not _offer_matches(dm, model, dms, codes) or _other_size(size, title):
            continue
        year = years.get(tid) or fallback_year(source, mtime)
        out.append({"year": year, "period": _period(year, tid, tender_id, current_year), "tender_id": tid,
                    "lot_title": re.sub(r"^\s*lot\s*nr\.?\s*\d+\s*", "", title or "", flags=re.IGNORECASE),
                    "lot_no": lot, "participant": name, "unit_price": unit, "value": value, "ratio": ratio,
                    "quantity": qty, "base_price": base_price, "decision": decision, "won": decision == "accepted"})
    out.sort(key=lambda r: (r["period"] != "этот тендер", -(r["year"] or 0), not r["won"], r["unit_price"] or 0))
    return out


def price_summary(rows: List[Dict]) -> List[str]:
    """По годам: цена победителя (минимальная из принятых) и разброс всех оферт."""
    by_year: Dict[int, List[Dict]] = {}
    for r in rows:
        if r["period"] != "этот тендер" and r.get("unit_price"):
            by_year.setdefault(r["year"] or 0, []).append(r)
    out = []
    for year in sorted(by_year, reverse=True):
        prices = [r["unit_price"] for r in by_year[year]]
        won = [r["unit_price"] for r in by_year[year] if r["won"]]
        text = f"{year or '?'}: "
        text += (f"победитель {fmt_price(min(won))}; " if won else "") + \
            (f"оферты {fmt_price(min(prices))}–{fmt_price(max(prices))}" if len(prices) > 1 else f"оферта {fmt_price(prices[0])}")
        out.append(text)
    return out


def fmt_ratio(ratio: Optional[float]) -> str:
    """Доля базы в процентах с двумя знаками: 1.2991071 -> «129.91%». Округление до целого показало бы «130%»
    у оферты ниже порога 130% (отказ по цене - только при ≥ 130%, без округления)."""
    if ratio is None:
        return ""
    return f"{int(ratio * 10000 + 1e-6) / 100:.2f}%"     # отбросить, а не округлить: 129.999 -> 129.99, не 130.00


def fmt_price(v: Optional[float]) -> str:
    if v is None:
        return ""
    return f"{v:,.2f}".replace(",", " ").replace(".00", "")


# --- технические документы ----------------------------------------------------------------------------------
def _resolve(path: str, tender_dir: Path) -> Path:
    """Путь из манифеста абсолютный (с другого ПК может не совпадать) - ищем ту же часть внутри папки тендера."""
    p = Path(path)
    if p.is_file():
        return p
    parts = p.parts
    if tender_dir.name in parts:
        candidate = tender_dir.joinpath(*parts[parts.index(tender_dir.name) + 1:])
        if candidate.is_file():
            return candidate
    return p


def tech_documents(tender_dir: Path, participant: str, lot_no: Optional[int] = None) -> List[Dict]:
    """Документы участника в тендере: технические - первыми, документы этого лота - раньше других.
    {path, name, doc_type, label, pages, has_text}."""
    manifest = Path(tender_dir) / "manifest.sqlite"
    if not manifest.is_file() or not participant:
        return []
    con = sqlite3.connect(manifest)
    try:
        rows = con.execute("SELECT r.path, r.lot_no, d.doc_type, d.pages, d.has_text FROM document_refs r "
                           "JOIN documents d USING (sha256) WHERE r.participant = ?", (participant,)).fetchall()
    finally:
        con.close()
    docs, seen = [], set()
    for path, lot, doc_type, pages, has_text in rows:
        p = _resolve(path, Path(tender_dir))
        if p.name in seen:
            continue
        seen.add(p.name)
        docs.append({"path": p, "name": p.name, "doc_type": doc_type or "other", "pages": pages,
                     "has_text": bool(has_text), "lot_no": lot,
                     "label": f"{DOC_LABELS.get(doc_type, doc_type or '?')}: {p.name}"})
    rank = {t: i for i, t in enumerate(DOC_ORDER)}
    docs.sort(key=lambda d: (d["doc_type"] not in rank, rank.get(d["doc_type"], 99), d["lot_no"] != lot_no, d["name"]))
    return docs


_TEXT_CACHE: Dict[str, str] = {}


def document_text(path: Path, max_pages: int = 60) -> str:
    """Текст PDF по страницам («--- стр. N ---»). Скан без текстового слоя - пояснение вместо текста."""
    key = str(path)
    if key in _TEXT_CACHE:
        return _TEXT_CACHE[key]
    if not Path(path).is_file():
        return f"Файл не найден: {path}"
    try:
        import fitz
        with fitz.open(path) as doc:
            pages = [f"--- стр. {i + 1} ---\n{page.get_text().strip()}" for i, page in enumerate(doc)
                     if i < max_pages]
    except Exception as e:  # повреждённый / не PDF
        return f"Не удалось прочитать {Path(path).name}: {e}"
    text = "\n\n".join(pages)
    if len(re.sub(r"---.*?---|\s", "", text)) < 20:
        text = "В документе нет текстового слоя (скан) - откройте PDF кнопкой «Открыть PDF».\n\n" + text
    _TEXT_CACHE[key] = text
    return text
