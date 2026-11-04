"""
Импорт рабочего Excel тендера в базу знаний как снимка (docs/CODEBOOK.md).

Лист лота:  A1 количество, B1 название, C9 единица, D7/D8 базовая цена за единицу / база,
            B11 требование, колонка «Note» - заметки по лоту, вкладка - цвет и суффикс (A/Ad/M).
Участник (колонка от E, «Ofertant: <имя>» в строке 2): строка 1 вердикт (текст + цвет),
            3 производитель/страна/модель, 4 DM, 7 цена за единицу, 8 сумма, 9 сумма/база,
            10 «Cistigator 2026», 11 спецификация (после «//» - пометки).
Колонки «clarificări» справа от участника (без «Ofertant») - уточнения этого участника.

Повторный импорт того же файла без изменений пропускается; изменённый - новый снимок и события.
"""
import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from src.amdm.parse import parse_row3, parse_row4
from src.amdm.workbook import load_workbook_tolerant
from src.history.decode import decode_offer, lot_outcome, norm_color, rejection_reason, sheet_suffix

FIRST_PARTICIPANT_COL = 5
EVENT_FIELDS = ("decision", "reason", "row1_text", "row1_color", "row11_color", "unit_price", "model", "dm")


@dataclass
class Offer:
    lot_no: int
    sheet: str
    col: int
    participant: str
    values: Dict[str, object] = field(default_factory=dict)
    clarifications: List[Tuple[int, str, str, Optional[str]]] = field(default_factory=list)


def _clean(v) -> Optional[str]:
    if v is None:
        return None
    s = re.sub(r"[ \t]+", " ", str(v)).strip()
    return s or None


def _num(v) -> Optional[float]:
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", ".").replace(" ", "")) if v not in (None, "") else None
    except ValueError:
        return None


def _rgb(cell) -> Optional[str]:
    if cell.fill is None or cell.fill.fill_type != "solid":
        return None
    c = cell.fill.fgColor
    return norm_color(c.rgb) if c.type == "rgb" else None


def _tab(ws) -> Optional[str]:
    tab = ws.sheet_properties.tabColor
    return norm_color(tab.rgb) if tab is not None and tab.type == "rgb" else None


def read_workbook(path: Path):
    """(лоты, оферты) рабочего Excel."""
    wb = load_workbook_tolerant(path, data_only=True)
    lots, offers = [], []
    try:
        for ws in wb.worksheets:
            lot_no, suffix = sheet_suffix(ws.title)
            if lot_no is None:
                continue
            if is_legacy(ws):
                lot, legacy = read_legacy_sheet(ws, lot_no, suffix)
                lots.append(lot)
                offers.extend(legacy)
                continue
            quantity, base_value = _num(ws["A1"].value), _num(ws["D8"].value)
            note_col = next((c for c in range(FIRST_PARTICIPANT_COL, ws.max_column + 1)
                             if _clean(ws.cell(row=1, column=c).value) and
                             _clean(ws.cell(row=1, column=c).value).lower() == "note"), None)
            note = None
            if note_col:
                note = "\n".join(x for x in (_clean(ws.cell(row=r, column=note_col).value)
                                             for r in range(2, 12)) if x and x.upper() != "NOTE") or None
            tab = _tab(ws)
            lots.append(dict(lot_no=lot_no, sheet=ws.title, title=_clean(ws["B1"].value), quantity=quantity,
                             unit=_clean(ws["C9"].value), base_price=_num(ws["D7"].value), base_value=base_value,
                             requirement=_clean(ws["B11"].value), suffix=suffix, tab_color=tab,
                             outcome=lot_outcome(tab, suffix), note=note))

            current: Optional[Offer] = None
            for col in range(FIRST_PARTICIPANT_COL, (note_col or ws.max_column + 1)):
                name = _clean(ws.cell(row=2, column=col).value) or ""
                row3 = _clean(ws.cell(row=3, column=col).value) or ""
                if name.lower().startswith("ofertant"):
                    participant = name.split(":", 1)[1].strip()
                    current = None
                    if participant:
                        current = Offer(lot_no, ws.title, col, participant, _offer_values(ws, col, quantity, base_value, suffix))
                        offers.append(current)
                elif current and not name and any(_clean(ws.cell(row=r, column=col).value) for r in (3, 4, 11)):
                    # Колонка уточнений: справа от участника, без «Ofertant», с содержимым. Слова
                    # «clarificări» может не быть - в строке 3 сразу итог («nu este inregistrat»)
                    current.clarifications.append((col, row3 or _clean(ws.cell(row=4, column=col).value),
                                                   _clean(ws.cell(row=11, column=col).value),
                                                   _rgb(ws.cell(row=11, column=col))))
    finally:
        wb.close()
    for o in offers:
        _reason_from_clarifications(o)
    return lots, offers


# --- старые форматы (2024-2025): участник с колонки B/C, решение - цветом ячеек спецификации ---------------------
RED, GREEN, BLUE, YELLOW = "FFFF0000", "FF00B050", "FF00B0F0", "FFFFFF00"
LEGACY_LAST_ROW = 40


def _ofertant_cols(ws) -> List[int]:
    return [c for c in range(1, min(ws.max_column, 60) + 1)
            if (_clean(ws.cell(row=2, column=c).value) or "").lower().startswith("ofertant")]


def is_legacy(ws) -> bool:
    """Нынешний формат: «Ofertant» в строке 2 с колонки E. Иначе (B/C/D) - старый."""
    cols = _ofertant_cols(ws)
    return bool(cols) and min(cols) < FIRST_PARTICIPANT_COL


def _legacy_decision(colors: List[str], tab: Optional[str]) -> Tuple[str, Optional[str]]:
    """Решение по цветам ячеек оферты (DM и спецификация): красный - отказ, голубой - альтернатива,
    жёлтый - уточнение, только зелёный - принят. Без цветов - не оценено."""
    if RED in colors:
        return "rejected", "technical"
    if BLUE in colors:
        return "alternative", None
    if YELLOW in colors:
        return "clarification", None
    if GREEN in colors:
        return "accepted", None
    return "not_evaluated", None


def read_legacy_sheet(ws, lot_no: int, suffix: str):
    """Лист старого формата -> (лот, оферты). Цены - по подписям в колонке A («Suma estimata», «pret per pcs»)."""
    tab = _tab(ws)
    title = next((_clean(ws.cell(row=1, column=c).value) for c in range(1, 4)
                  if (_clean(ws.cell(row=1, column=c).value) or "").lower().startswith("lot")), None)
    cols = _ofertant_cols(ws)
    first = min(cols)
    labels = {r: (_clean(ws.cell(row=r, column=1).value) or "").lower() for r in range(4, 9)}
    row_of = lambda word: next((r for r, t in labels.items() if word in t), None)   # noqa: E731
    r_sum, r_price = row_of("suma"), row_of("pret")
    # требование: колонка перед первым участником, строки спецификации
    spec_start = 8 if r_sum else 6
    req_col = first - 1 if first > 1 else 1
    requirement = "\n".join(x for x in (_clean(ws.cell(row=r, column=req_col).value)
                                        for r in range(spec_start, LEGACY_LAST_ROW)) if x) or None
    base_value = _num(ws.cell(row=r_sum, column=req_col).value) if r_sum else None
    lot = dict(lot_no=lot_no, sheet=ws.title, title=title, quantity=None, unit=None,
               base_price=_num(ws.cell(row=r_price, column=req_col).value) if r_price else None,
               base_value=base_value, requirement=requirement, suffix=suffix, tab_color=tab,
               outcome=lot_outcome(tab, suffix), note=None)
    offers = []
    for col in cols:
        participant = (_clean(ws.cell(row=2, column=col).value) or "").split(":", 1)[-1].strip(" :")
        if not participant:
            continue
        p3 = parse_row3(ws.cell(row=3, column=col).value)
        dm_text = " ".join(x for x in (_clean(ws.cell(row=r, column=col).value) for r in (4, 5)) if x)
        dms = parse_row4(dm_text)["dm_numbers"]
        spec_cells = [ws.cell(row=r, column=col) for r in range(spec_start, LEGACY_LAST_ROW)]
        spec = "\n".join(x for x in (_clean(c.value) for c in spec_cells) if x)
        colors = [c for c in (_rgb(ws.cell(row=r, column=col)) for r in (4, 5)) if c] + \
                 [c for c in (_rgb(cell) for cell in spec_cells) if c]
        decision, reason = _legacy_decision(colors, tab)
        value = _num(ws.cell(row=r_sum, column=col).value) if r_sum else None
        spec_main, _, notes = spec.partition("//")
        offers.append(Offer(lot_no, ws.title, col, participant, dict(
            producer=p3.get("producer"), country=p3.get("country"), model=p3.get("model"),
            dm=",".join(dms) or None, row4=_clean(ws.cell(row=4, column=col).value),
            unit_price=_num(ws.cell(row=r_price, column=col).value) if r_price else None, offer_value=value,
            price_ratio=(value / base_value) if value and base_value else None, row10=None, previous_winner=0,
            spec=spec_main.strip() or None, spec_notes=("//" + notes).strip() if notes else None,
            row1_text=None, row1_color=None, row11_color=colors[-1] if colors else None,
            decision=decision, reason=reason, needs_review=None)))
    return lot, offers


def _reason_from_clarifications(offer: Offer) -> None:
    """Отказ без причины в строке 1: причина из колонок уточнений («nu este inregistrat»)."""
    v = offer.values
    if v["decision"] != "rejected" or v["reason"] or not offer.clarifications:
        return
    text = " ".join(f"{label or ''} {body or ''}" for _, label, body, _ in offer.clarifications)
    reason = rejection_reason(text, None)
    if reason:
        v["reason"], v["needs_review"] = reason, None


def _offer_values(ws, col: int, quantity: Optional[float], base_value: Optional[float], suffix: str) -> dict:
    p3 = parse_row3(ws.cell(row=3, column=col).value)
    row4 = _clean(ws.cell(row=4, column=col).value)
    p4 = parse_row4(row4)
    value = _num(ws.cell(row=8, column=col).value)
    unit_price = _num(ws.cell(row=7, column=col).value)
    if unit_price is None and value is not None and quantity:
        unit_price = value / quantity
    ratio = _num(ws.cell(row=9, column=col).value)
    if ratio is None and value is not None and base_value:
        ratio = value / base_value
    row10 = _clean(ws.cell(row=10, column=col).value)
    spec_raw = _clean(ws.cell(row=11, column=col).value) or ""
    spec, _, notes = spec_raw.partition("//")
    row1_text = _clean(ws.cell(row=1, column=col).value)
    row1_color = _rgb(ws.cell(row=1, column=col))
    decision, reason, review = decode_offer(row1_text, row1_color, suffix, ratio)
    return dict(producer=p3.get("producer"), country=p3.get("country"), model=p3.get("model"),
                dm=",".join(p4["dm_numbers"]) or None, row4=row4, unit_price=unit_price, offer_value=value,
                price_ratio=ratio, row10=row10, previous_winner=int(bool(row10 and "cistig" in row10.lower())),
                spec=spec.strip() or None, spec_notes=("//" + notes).strip() if notes else None,
                row1_text=row1_text, row1_color=row1_color, row11_color=_rgb(ws.cell(row=11, column=col)),
                decision=decision, reason=reason, needs_review=review)


def tender_id_from(path: Path) -> Optional[str]:
    m = re.search(r"(?<!\d)(\d{8})(?!\d)", Path(path).name)
    return m.group(1) if m else None


def import_workbook(conn, path: Path, tender_id: Optional[str] = None, force: bool = False,
                    online: bool = False) -> dict:
    """Новый снимок тендера и события относительно предыдущего. Без изменений файла - пропуск
    (force - перечитать тот же файл, например после улучшения импорта)."""
    path = Path(path)
    tender_id = tender_id or tender_id_from(path)
    if not tender_id:
        raise ValueError(f"Не удалось определить номер тендера из имени файла {path.name}: укажите --tender")
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    prev = conn.execute("SELECT id, file_sha256 FROM snapshots WHERE tender_id = ? ORDER BY id DESC LIMIT 1",
                        (tender_id,)).fetchone()
    if prev and prev[1] == sha and not force:
        return {"tender_id": tender_id, "snapshot_id": prev[0], "skipped": True}

    lots, offers = read_workbook(path)
    now = datetime.now().isoformat(timespec="seconds")
    cur = conn.execute("INSERT INTO snapshots (tender_id, source_file, file_sha256, file_mtime, imported_at) "
                       "VALUES (?, ?, ?, ?, ?)",
                       (tender_id, str(path.resolve()), sha,
                        datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"), now))
    sid = cur.lastrowid
    for lot in lots:
        conn.execute("INSERT OR REPLACE INTO lots (snapshot_id, lot_no, sheet, title, quantity, unit, base_price, "
                     "base_value, requirement, suffix, tab_color, outcome, note) VALUES "
                     "(:sid, :lot_no, :sheet, :title, :quantity, :unit, :base_price, :base_value, :requirement, "
                     ":suffix, :tab_color, :outcome, :note)", {"sid": sid, **lot})
    for o in offers:
        cols = ", ".join(o.values)
        marks = ", ".join(f":{k}" for k in o.values)
        conn.execute(f"INSERT OR REPLACE INTO offers (snapshot_id, lot_no, sheet, col, participant, {cols}) "
                     f"VALUES (:sid, :lot_no, :sheet, :col, :participant, {marks})",
                     {"sid": sid, "lot_no": o.lot_no, "sheet": o.sheet, "col": o.col,
                      "participant": o.participant, **o.values})
        for col, label, text, color in o.clarifications:
            conn.execute("INSERT INTO clarifications (snapshot_id, lot_no, participant, col, label, text, color) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?)", (sid, o.lot_no, o.participant, col, label, text, color))
    events = _diff(conn, tender_id, prev[0], sid, now) if prev else []
    conn.commit()
    from src.history.tender_year import tender_year
    tender_year(conn, tender_id, str(path), None, online=online)
    return {"tender_id": tender_id, "snapshot_id": sid, "skipped": False, "lots": len(lots),
            "offers": len(offers), "events": events}


def _diff(conn, tender_id: str, old_sid: int, new_sid: int, now: str) -> List[tuple]:
    """События: изменения решений и полей оферт и итогов лотов между снимками."""
    def offers_of(sid):
        rows = conn.execute(f"SELECT lot_no, participant, {', '.join(EVENT_FIELDS)} FROM offers WHERE snapshot_id = ?",
                            (sid,)).fetchall()
        return {(r[0], r[1]): dict(zip(EVENT_FIELDS, r[2:])) for r in rows}

    def lots_of(sid):
        return {r[0]: r[1] for r in conn.execute("SELECT lot_no, outcome FROM lots WHERE snapshot_id = ?", (sid,))}

    events = []
    old, new = offers_of(old_sid), offers_of(new_sid)
    for key in sorted(set(old) | set(new), key=lambda k: (k[0], k[1])):
        lot, who = key
        if key not in old:
            events.append((lot, who, "offer", None, "added"))
        elif key not in new:
            events.append((lot, who, "offer", "removed", None))
        else:
            for f in EVENT_FIELDS:
                a, b = old[key][f], new[key][f]
                if isinstance(a, float) or isinstance(b, float):
                    if a is not None and b is not None and abs(a - b) < 1e-6:
                        continue
                if a != b:
                    events.append((lot, who, f, None if a is None else str(a), None if b is None else str(b)))
    lo, ln = lots_of(old_sid), lots_of(new_sid)
    for lot in sorted(set(lo) | set(ln)):
        if lo.get(lot) != ln.get(lot):
            events.append((lot, None, "lot_outcome", lo.get(lot), ln.get(lot)))
    conn.executemany("INSERT INTO events (tender_id, snapshot_id, lot_no, participant, field, old_value, new_value, "
                     "detected_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                     [(tender_id, new_sid, *e, now) for e in events])
    return events
