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

from src.amdm.parse import compact, model_tokens
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
    return [d.strip() for d in re.split(r"[,;+ ]", card.get("dm") or "") if d.strip().upper().startswith("DM")]


def _period(year: Optional[str], tender_id: str, current_tender: str) -> str:
    if tender_id == current_tender:
        return "этот тендер"
    now = datetime.now().year
    if not year:
        return "раньше"
    y = int(year)
    return "этот год" if y == now else ("прошлый год" if y == now - 1 else str(y))


PERIOD_ORDER = {"этот тендер": 0, "этот год": 1, "прошлый год": 2}


def _offer_matches(dm: str, model: str, dms: List[str], codes: List[str]) -> bool:
    if dms and any(d in (dm or "") for d in dms):
        return True
    m = compact(model or "")
    return bool(codes) and any(c in m for c in codes)


def agent_candidates(kb: sqlite3.Connection, card: Dict, tender_id: str, participant: str = "") -> List[Dict]:
    """Кто предлагал эту модель: {name, period, tender_id, lot_no, decision, won}. Текущий участник - первым."""
    dms, codes = _dms(card), _codes(card)
    out: Dict[Tuple[str, str], Dict] = {}

    def add(name, tid, lot, year, decision, won):
        period = _period(year, tid, tender_id)
        key = (name.strip(), tid)
        if not name.strip() or (key in out and not won):
            return
        out[key] = {"name": name.strip(), "period": period, "tender_id": tid, "lot_no": lot, "decision": decision,
                    "won": won, "year": year}

    if participant:
        add(participant, tender_id, None, str(datetime.now().year), "", False)
    for name, tid, lot, status, at in kb.execute(
            "SELECT participant, tender_id, lot_no, status, decided_at FROM evaluations WHERE model_key = ?",
            (card.get("model_key"),)):
        add(name, tid, lot, (at or "")[:4], status, status == "corespunde")
    rows = kb.execute("SELECT o.participant, o.tender_id, o.lot_no, o.dm, o.model, o.final_decision, "
                      "s.file_mtime FROM current_offers o JOIN snapshots s ON s.id = o.snapshot_id").fetchall()
    for name, tid, lot, dm, model, decision, mtime in rows:
        if _offer_matches(dm, model, dms, codes):
            add(name, tid, lot, (mtime or "")[:4], decision, decision == "accepted")
    return sorted(out.values(), key=lambda a: (a["name"] != participant, PERIOD_ORDER.get(a["period"], 3),
                                               -(int(a["year"]) if (a["year"] or "").isdigit() else 0), a["name"]))


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
