"""
Результат проверки AMDM в листах лотов (этап 4).

Строка 4 участника (номер DM), как в Excel Ivan:
  найден, производитель и модель совпали   -> под номером «Rg04-000083 13-04-2023», зелёный;
  найден, но что-то расходится            -> приказ и дата + «// причина», жёлтый;
  не найден в реестре                     -> «// nu este înregistrat în AMDM», красный;
  номера DM нет (только CE/ISO) или нет ничего -> «// ...», жёлтый;
  не проверялся (нет в копии, сайт не опрашивался) -> без изменений.
Строка 1 участника и вкладка листа принимают худший цвет: красный > жёлтый > зелёный.
Повторный запуск ничего не дублирует: приказ и пометки дописываются, только если их ещё нет.
"""
import re
from collections import defaultdict
from datetime import date
from typing import Dict, List, Optional, Tuple

from openpyxl.styles import Alignment, PatternFill

from src.amdm.models import CHECK, FAIL, IN_PROGRESS, NO_DM, OK, CheckResult
from src.amdm.matcher import overall_status

GREEN, YELLOW, RED = "FF00B050", "FFFFFF00", "FFFF0000"
STATUS_COLOR = {OK: GREEN, CHECK: YELLOW, NO_DM: YELLOW, FAIL: RED}
SEVERITY = {GREEN: 0, YELLOW: 1, RED: 2}
# Строку 1 и вкладку можно перекрашивать только из «наших» цветов и шаблонного голубого
REPAINTABLE = {None, "00000000", "FF00B0F0", GREEN, YELLOW, RED}
KEEP_TAB = {"FF001F4D", "001F4D"}  # «не состоялась»
NOT_REGISTERED = "// nu este înregistrat în AMDM"
ROW_STATUS, ROW_DM = 1, 4


def _rgb(cell) -> Optional[str]:
    if cell.fill is None or cell.fill.fill_type != "solid":
        return None
    return str(cell.fill.fgColor.rgb).upper()


def _order_line(order: Optional[str], order_date: Optional[str]) -> str:
    """«Rg04-000083 13-04-2023» - как пишет человек."""
    d = ""
    if order_date:
        try:
            d = date.fromisoformat(order_date).strftime("%d-%m-%Y")
        except ValueError:
            d = order_date
    return " ".join(x for x in (order, d) if x)


def row4_text(original: str, results: List[CheckResult]) -> str:
    """Текст строки 4: исходные строки + приказ/дата под каждым найденным номером + пометки."""
    text = str(original or "").strip()
    lines = text.split("\n") if text else []
    for r in results:
        if r.record and r.dm:
            info = _order_line(r.record.order, r.record.order_date)
            if not info:
                continue
            idx = next((i for i, line in enumerate(lines) if r.dm in line.replace(" ", "")), None)
            if idx is None:
                lines += [r.dm, info]
            elif idx + 1 >= len(lines) or lines[idx + 1].strip() != info:
                # приказ - под своим номером (у нескольких номеров набора он может быть общим)
                lines.insert(idx + 1, info)
    text = "\n".join(lines)
    notes = []
    for r in results:
        if r.status == FAIL:
            note = f"{NOT_REGISTERED}: {r.dm}"
        elif r.status in (CHECK, NO_DM) and r.reasons:
            note = "// " + "; ".join(r.reasons)
        else:
            continue
        if note not in text and note not in notes:
            notes.append(note)
    return "\n".join(x for x in [text] + notes if x)


def mark_workbook(wb, results: List[CheckResult]) -> Dict[str, int]:
    """Пишет результат в строку 4, красит строку 4, строку 1 участника и вкладку."""
    by_cell: Dict[Tuple[str, int], List[CheckResult]] = defaultdict(list)
    for r in results:
        by_cell[(r.offer.sheet, r.offer.column)].append(r)

    stats = defaultdict(int)
    touched_sheets = set()
    for (sheet, col), rs in by_cell.items():
        status = overall_status(rs)
        stats[status] += 1
        if status == IN_PROGRESS:
            continue
        ws = wb[sheet]
        color = STATUS_COLOR[status]
        cell = ws.cell(row=ROW_DM, column=col)
        cell.value = row4_text(cell.value, rs)
        old = cell.alignment
        cell.alignment = Alignment(horizontal=old.horizontal, vertical=old.vertical, wrap_text=True)
        cell.fill = PatternFill("solid", fgColor=color)

        head = ws.cell(row=ROW_STATUS, column=col)
        current = _rgb(head)
        if current in REPAINTABLE and SEVERITY[color] > SEVERITY.get(current, -1):
            head.fill = PatternFill("solid", fgColor=color)
        touched_sheets.add(sheet)

    for sheet in touched_sheets:
        _recolor_tab(wb[sheet])
    return dict(stats)


def _recolor_tab(ws) -> None:
    tab = ws.sheet_properties.tabColor
    if tab is not None and str(tab.rgb).upper() in KEEP_TAB:
        return
    worst = None
    for col in range(5, ws.max_column + 1):
        if not str(ws.cell(row=2, column=col).value or "").lower().startswith("ofertant"):
            continue
        rgb = _rgb(ws.cell(row=ROW_STATUS, column=col))
        if rgb in SEVERITY and (worst is None or SEVERITY[rgb] > SEVERITY[worst]):
            worst = rgb
    if worst:
        ws.sheet_properties.tabColor = worst
