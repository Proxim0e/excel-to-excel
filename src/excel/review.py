"""
Ручная проверка лотов (вкладка окна «Проверка лотов»): человек проходит лоты, видит, что выдала система, и
ставит решения по кодбуку (docs/CODEBOOK.md); решения пишутся в копию result-файла <имя>_verificat.xlsx -
такой же рабочий Excel, какой Ivan делает вручную, только заполненный системой заранее.

Что пишется по решению участника:
  строка 1  - текст и заливка (зелёный «Tehnic corespunde», голубой «Alternativa» / «… (mostre)»,
              жёлтый «Clarificare», красный - причина отказа: «Tehnic nu corespunde», «Garantia Bancara
              insuficienta», «0.3» …);
  строка 11 - заливка того же цвета и комментарий после «//» (что проверено / что запросить / причина);
  вкладка   - итог лота (жёлтый > зелёный > голубой > красный), суффикс «(M)», «(A)», «(Ad)» в имени листа.
«Не оценивался» - строка 1 не трогается (голубой шаблон).
Решения человека пишутся и в базу знаний (evaluations, source = human) - через git они попадают на другой ПК.
"""
import json
import re
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from openpyxl import load_workbook
from openpyxl.styles import PatternFill

from src.anexa22.evaluate import participant_key
from src.evaluate import confidence as cf
from src.excel.fill_offers import (FIRST_PARTICIPANT_COL, ROW_DM, ROW_NAME, ROW_PRODUCER, ROW_SPEC, ROW_STATUS,
                                   ROW_VALUE)
from src.excel.verdicts import BLUE, GREEN, RED, YELLOW, effective, lot_tab

ROW_NOTES = 10
NAVY = "FF001F4D"
SUFFIX = " _verificat"
SHEET_RE = re.compile(r"\s*lot\s*(\d+)\s*(?:\(?\s*(M|A|Ad)\s*\)?)?\s*$", re.IGNORECASE)
TAB_SUFFIXES = ("", "M", "A", "Ad")     # образцы / альтернатива с уточнением / альтернатива по умолчанию


@dataclass
class Choice:
    """Решение по кодбуку: статус базы знаний, причина, текст строки 1, цвет."""
    key: str
    label: str            # в окне
    status: str
    reason: Optional[str]
    text: Optional[str]   # строка 1; None - не трогать
    color: Optional[str]


CHOICES = [
    Choice("corespunde", "Tehnic corespunde", cf.CORESPUNDE, None, "Tehnic corespunde", GREEN),
    Choice("alternativa", "Alternativa", cf.ALTERNATIVA, None, "Alternativa", BLUE),
    Choice("mostre", "Mostre (образцы)", cf.ALTERNATIVA, "samples", "Tehnic corespunde (mostre)", BLUE),
    Choice("clarificare", "Clarificare (уточнения)", cf.CLARIFICARE, None, "Clarificare", YELLOW),
    Choice("technical", "Tehnic nu corespunde", cf.NU_CORESPUNDE, "technical", "Tehnic nu corespunde", RED),
    Choice("amdm", "Nu este inregistrat (AMDM)", cf.NU_CORESPUNDE, "amdm", "Nu este inregistrat", RED),
    Choice("guarantee_insufficient", "Garantia Bancara insuficienta", cf.NU_CORESPUNDE, "guarantee_insufficient",
           "Garantia Bancara insuficienta", RED),
    Choice("guarantee_wrong", "Garantia Bancara gresita", cf.NU_CORESPUNDE, "guarantee_wrong",
           "Garantia Bancara gresita", RED),
    Choice("price_130", "0.3 (цена ≥ 130%)", cf.NU_CORESPUNDE, "price_130", "0.3", RED),
    Choice("withdrawn", "Retras ofertat", cf.NU_CORESPUNDE, "withdrawn", "Retras ofertat", RED),
    Choice("annulled_g", "G - аннулирование", cf.NU_CORESPUNDE, "annulled_g", "G", RED),
    Choice("alternative_rejected", "Alternativa nu este acceptata", cf.NU_CORESPUNDE, "alternative_rejected",
           "Alternativa nu este acceptata", RED),
    Choice("neevaluat", "Не оценивался", cf.NEEVALUAT, None, None, None),
]
BY_KEY = {c.key: c for c in CHOICES}


def choice_for(status: str, reason: Optional[str]) -> Optional[Choice]:
    """Решение системы / человека из базы -> пункт кодбука."""
    for c in CHOICES:
        if c.status == status and c.reason == reason:
            return c
    return next((c for c in CHOICES if c.status == status and (c.reason is None or status == cf.NU_CORESPUNDE)),
                None)


def target_path(result: Path) -> Path:
    """<result>_verificat.xlsx (если выбран уже он - он сам)."""
    result = Path(result)
    if result.stem.endswith("_verificat"):
        return result
    return result.with_name(result.stem + "_verificat.xlsx")


def _rgb(cell) -> Optional[str]:
    if cell.fill is None or cell.fill.fill_type != "solid":
        return None
    rgb = str(cell.fill.fgColor.rgb).upper()
    return rgb if len(rgb) == 8 else "FF" + rgb


def _num(v) -> Optional[float]:
    try:
        return float(str(v).replace(",", ".")) if v not in (None, "") else None
    except ValueError:
        return None


@dataclass
class Offer:
    col: int
    name: str
    producer: str = ""
    dm: str = ""
    value: Optional[float] = None
    ratio: Optional[float] = None
    row1: str = ""
    row1_color: Optional[str] = None
    spec: str = ""
    spec_color: Optional[str] = None
    notes: str = ""
    clar: List[Dict] = field(default_factory=list)   # колонки уточнений справа: label, docs, note, color
    system: Optional[Dict] = None     # решение системы / человека из базы: status, source, reason, reasons, comment
    dm_color: Optional[str] = None    # заливка строки 4 (итог AMDM)


@dataclass
class Lot:
    lot_no: int
    sheet: str
    title: str = ""
    requirement: str = ""
    quantity: Optional[float] = None
    base: Optional[float] = None
    suffix: str = ""
    tab_color: Optional[str] = None
    offers: List[Offer] = field(default_factory=list)

    @property
    def reviewed(self) -> bool:
        return bool(self.offers) and all(o.system and o.system.get("source") == cf.HUMAN for o in self.offers)


def read_lot(ws) -> Optional[Lot]:
    m = SHEET_RE.match(ws.title)
    if not m:
        return None
    tab = ws.sheet_properties.tabColor
    lot = Lot(int(m.group(1)), ws.title, str(ws.cell(row=1, column=2).value or "").strip(),
              str(ws.cell(row=ROW_SPEC, column=2).value or "").strip(), _num(ws.cell(row=1, column=1).value),
              _num(ws.cell(row=ROW_VALUE, column=4).value), m.group(2) or "",
              str(tab.rgb).upper() if tab is not None and tab.rgb else None)
    for col in range(FIRST_PARTICIPANT_COL, ws.max_column + 1):
        head = ws.cell(row=ROW_NAME, column=col).value
        if not head or not str(head).lower().startswith("ofertant"):
            continue
        name = str(head).split(":", 1)[1].strip() if ":" in str(head) else ""
        if not name:
            continue
        value = _num(ws.cell(row=ROW_VALUE, column=col).value)
        lot.offers.append(Offer(
            col, name, str(ws.cell(row=ROW_PRODUCER, column=col).value or "").strip(),
            str(ws.cell(row=ROW_DM, column=col).value or "").strip(), value,
            value / lot.base if value and lot.base else None,
            str(ws.cell(row=ROW_STATUS, column=col).value or "").strip(), _rgb(ws.cell(row=ROW_STATUS, column=col)),
            str(ws.cell(row=ROW_SPEC, column=col).value or "").strip(), _rgb(ws.cell(row=ROW_SPEC, column=col)),
            str(ws.cell(row=ROW_NOTES, column=col).value or "").strip(),
            dm_color=_rgb(ws.cell(row=ROW_DM, column=col))))
        c = col + 1          # уточнения: колонки без «Ofertant» в строке 2 и с «clarific…» в строке 3
        while c <= ws.max_column and not ws.cell(row=ROW_NAME, column=c).value and                 str(ws.cell(row=ROW_PRODUCER, column=c).value or "").lower().startswith("clarific"):
            lot.offers[-1].clar.append({
                "label": str(ws.cell(row=ROW_PRODUCER, column=c).value or "").strip(),
                "docs": str(ws.cell(row=ROW_DM, column=c).value or "").strip(),
                "request": str(ws.cell(row=ROW_VALUE, column=c).value or "").strip(),   # «Sa solicitat …»
                "note": str(ws.cell(row=ROW_SPEC, column=c).value or "").strip(),
                "color": _rgb(ws.cell(row=ROW_SPEC, column=c))})
            c += 1
    return lot


def attach_system(lots: List[Lot], kb: sqlite3.Connection, tender_id: str) -> None:
    """Решения из базы знаний (система или человек) - к офертам."""
    rows = kb.execute("SELECT lot_no, participant, status, source, reason, findings FROM evaluations "
                      "WHERE tender_id = ?", (tender_id,)).fetchall()
    by = {}
    for lot_no, who, status, source, reason, findings in rows:
        try:
            f = json.loads(findings or "{}")
        except ValueError:
            f = {}
        by[(lot_no, participant_key(who))] = {"status": status, "source": source, "reason": reason,
                                             "reasons": f.get("reasons") or [], "comment": f.get("comment") or "",
                                             "findings": f, "participant": who}
    for lot in lots:
        for o in lot.offers:
            o.system = by.get((lot.lot_no, participant_key(o.name)))


class ReviewBook:
    """Рабочая книга проверки: загружается один раз, решения меняют её в памяти, save() пишет _verificat."""

    def __init__(self, result: Path):
        self.result = Path(result)
        self.target = target_path(self.result)
        self.source = self.target if self.target.is_file() else self.result
        self.wb = load_workbook(self.source)
        self.dirty = False
        self.reread()

    def reread(self) -> None:
        """Лоты заново из книги в памяти (после пометок AMDM в строках 1/4 и цветов вкладок)."""
        self.lots: List[Lot] = [lot for lot in (read_lot(ws) for ws in self.wb.worksheets) if lot]
        self.lots.sort(key=lambda lot: lot.lot_no)

    def sheet(self, lot: Lot):
        return self.wb[lot.sheet]

    def lot(self, lot_no: int) -> Optional[Lot]:
        return next((lot for lot in self.lots if lot.lot_no == lot_no), None)

    def decide(self, lot: Lot, offer: Offer, choice: Choice, comment: str = "", row1_text: Optional[str] = None):
        """Решение участника - в строки 1 и 11 листа (в памяти)."""
        ws = self.sheet(lot)
        if choice.color:
            text = row1_text if row1_text is not None else choice.text
            if choice.key == "annulled_g" and comment and not (row1_text or "").startswith("G"):
                text = f"G - {comment}"
            cell = ws.cell(row=ROW_STATUS, column=offer.col)
            cell.value = text
            cell.fill = PatternFill("solid", fgColor=choice.color)
            ws.cell(row=ROW_SPEC, column=offer.col).fill = PatternFill("solid", fgColor=choice.color)
            offer.row1, offer.row1_color, offer.spec_color = text, choice.color, choice.color
        if comment:
            spec = ws.cell(row=ROW_SPEC, column=offer.col)
            left = str(spec.value or "").split("//")[0].rstrip()
            spec.value = f"{left}\n// {comment}" if left else f"// {comment}"
            offer.spec = spec.value
        self.dirty = True

    def set_tab(self, lot: Lot, color: Optional[str] = None, suffix: Optional[str] = None) -> str:
        """Вкладка лота: цвет (по умолчанию - итог решений) и суффикс в имени листа «lot 9 (M)»."""
        ws = self.sheet(lot)
        if color is None:
            color = lot_tab(self.statuses(lot)) or (NAVY if not lot.offers else None)
        if color:
            ws.sheet_properties.tabColor = color
            lot.tab_color = color
        if suffix is not None and suffix != lot.suffix:
            title = f"lot {lot.lot_no}" + (f" ({suffix})" if suffix else "")
            ws.title = title
            lot.sheet, lot.suffix = title, suffix
        self.dirty = True
        return color or ""

    @staticmethod
    def statuses(lot: Lot) -> List[str]:
        """Итоги оферт для цвета вкладки: открытый вопрос - жёлтый, «не оценивался» не считается."""
        out = []
        for o in lot.offers:
            if o.system:
                status = effective(o.system["status"], o.system.get("source"))
                if status != cf.NEEVALUAT:
                    out.append(status)
        return out

    def save(self) -> Path:
        if self.target.is_file() and self.source != self.target:
            raise FileExistsError(self.target)
        if self.target.is_file() and not (self.target.with_suffix(".bak.xlsx")).is_file():
            shutil.copy2(self.target, self.target.with_suffix(".bak.xlsx"))   # одна резервная копия до правок
        self.wb.save(self.target)
        self.source = self.target
        self.dirty = False
        return self.target


def record(kb: sqlite3.Connection, tender_id: str, lot: Lot, offer: Offer, choice: Choice, comment: str = "") -> None:
    """Решение человека - в базу знаний (как ответ на вопрос): движок его больше не пересчитывает, через
    knowledge/answers оно уходит на другой ПК. Открытый вопрос по этой оферте закрывается."""
    now = datetime.now().isoformat(timespec="seconds")
    who = offer.system.get("participant") if offer.system else offer.name
    findings = dict(offer.system.get("findings") or {}) if offer.system else {}
    findings["comment"] = comment
    findings["reviewed"] = True
    kb.execute("INSERT INTO evaluations (tender_id, lot_no, participant, model_key, status, reason, confidence, "
               "source, findings, decided_at) VALUES (?, ?, ?, NULL, ?, ?, 1, ?, ?, ?) "
               "ON CONFLICT (tender_id, lot_no, participant) DO UPDATE SET status = excluded.status, "
               "reason = excluded.reason, confidence = 1, source = excluded.source, findings = excluded.findings, "
               "decided_at = excluded.decided_at",
               (tender_id, lot.lot_no, who, choice.status, choice.reason, cf.HUMAN,
                json.dumps(findings, ensure_ascii=False, default=str), now))
    kb.execute("UPDATE questions SET state = 'answered', answer = ?, answered_at = ? WHERE kind = 'offer_status' "
               "AND state = 'open' AND tender_id = ? AND lot_no = ? AND participant = ?",
               (json.dumps({"status": choice.status, "reason": choice.reason, "comment": comment},
                           ensure_ascii=False), now, tender_id, lot.lot_no, who))
    kb.commit()
    offer.system = {**(offer.system or {}), "status": choice.status, "source": cf.HUMAN, "reason": choice.reason,
                    "comment": comment, "participant": who, "findings": findings}


def system_comment(offer: Offer) -> str:
    """Комментарий для строки 11 по находкам системы: чего нет / что найдено."""
    s = offer.system or {}
    if s.get("comment"):
        return s["comment"]
    f = s.get("findings") or {}
    missing = (f.get("level2") or {}).get("missing") or []
    level1 = f.get("level1") or {}
    parts = [f"≠ {c}" for c in (level1.get("conflicts") or [])[:2]] + list(missing[:3])
    amdm = (f.get("amdm") or {}).get("status")
    if amdm == "rosu":
        parts.append("nu este inregistrat")
    return "; ".join(parts)
