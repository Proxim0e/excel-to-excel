"""
Запись решений оценки (data/knowledge.sqlite, таблица evaluations) в листы лотов result-файла - одно место,
где по итогам оценки ставятся цвета (кодбук):
  строка 1 участника - текст и заливка, строка 11 - заливка, вкладка листа - итог лота.

  corespunde     - зелёный 00B050, «Tehnic corespunde»
  alternativa    - голубой 00B0F0, «Alternativa» (запрос и Anexa 22 юридически не совпадают)
  nu_corespunde  - красный FF0000, «Tehnic nu corespunde» (цена ≥ 130% - «0.3»)
  clarificare и ещё не решённые (вопрос открыт) - жёлтый FFFF00
  neevaluat - не трогается (голубой шаблон).
Перекрашиваются только шаблонные и наши цвета: цвет, поставленный человеком в Excel, остаётся.
Вкладка: есть жёлтый - жёлтая; есть зелёный - зелёная; иначе голубая (только альтернативы) или красная.

  python -m src.excel.verdicts <result.xlsx> downloads/<тендер> [--db data/knowledge.sqlite] [--out файл]
По умолчанию пишет копию <result>_evaluare.xlsx (исходный файл может быть открыт в Excel).
"""
import argparse
import logging
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, Optional, Tuple

from openpyxl import load_workbook
from openpyxl.styles import PatternFill

from src.anexa22.evaluate import participant_key
from src.evaluate import confidence as cf
from src.evaluate.engine import tender_id_of
from src.excel.fill_offers import FIRST_PARTICIPANT_COL, KEEP_TAB_COLORS, ROW_NAME, ROW_SPEC, ROW_STATUS
from src.history.db import DEFAULT_DB, connect

logger = logging.getLogger(__name__)

GREEN, BLUE, RED, YELLOW = "FF00B050", "FF00B0F0", "FFFF0000", "FFFFFF00"
COLORS = {cf.CORESPUNDE: GREEN, cf.ALTERNATIVA: BLUE, cf.NU_CORESPUNDE: RED, cf.CLARIFICARE: YELLOW}
TEXTS = {cf.CORESPUNDE: "Tehnic    corespunde ", cf.ALTERNATIVA: "Alternativa",
         cf.NU_CORESPUNDE: "Tehnic nu corespunde", cf.CLARIFICARE: "Clarificare"}
PRICE_TEXT = "0.3"
# Наши и шаблонные заливки - их можно перекрашивать; остальное поставил человек
REPAINTABLE = {None, "00000000", "FF00B0F0", "00B0F0", "FF00B050", "00B050", "FFFFFF00", "FFFF00",
               "FFFF0000", "FF0000", "FF92D050"}
SHEET_RE = re.compile(r"lot\s*(\d+)", re.IGNORECASE)


def _rgb(cell) -> Optional[str]:
    if cell.fill is None or cell.fill.fill_type != "solid":
        return None
    return str(cell.fill.fgColor.rgb).upper()


def _paint(cell, color: str) -> bool:
    if _rgb(cell) not in REPAINTABLE:
        return False
    cell.fill = PatternFill("solid", fgColor=color)
    return True


def decisions(kb: sqlite3.Connection, tender_id: str) -> Dict[Tuple[int, str], Tuple[str, str, Optional[str]]]:
    """(лот, ключ участника) -> (статус, source, причина)."""
    rows = kb.execute("SELECT lot_no, participant, status, source, reason FROM evaluations WHERE tender_id = ?",
                      (tender_id,)).fetchall()
    return {(lot, participant_key(p)): (status, source, reason) for lot, p, status, source, reason in rows}


def effective(status: str, source: str) -> str:
    """Что показать: открытый вопрос (pending) - жёлтый, какое бы предложение ни было."""
    return cf.CLARIFICARE if source == cf.PENDING else status


def lot_tab(statuses) -> Optional[str]:
    statuses = set(statuses)
    if not statuses:
        return None
    if cf.CLARIFICARE in statuses:
        return YELLOW
    if cf.CORESPUNDE in statuses:
        return GREEN
    return BLUE if cf.ALTERNATIVA in statuses else RED


def write_verdicts(result_path: Path, tender_dir: Path, kb: sqlite3.Connection,
                   out_path: Optional[Path] = None) -> Dict:
    tender_id = tender_id_of(tender_dir)
    by = decisions(kb, tender_id)
    wb = load_workbook(result_path)
    stats = Counter()
    for ws in wb.worksheets:
        m = SHEET_RE.fullmatch(ws.title.strip())
        if not m:
            continue
        lot = int(m.group(1))
        shown = []
        for col in range(FIRST_PARTICIPANT_COL, ws.max_column + 1):
            head = ws.cell(row=ROW_NAME, column=col).value
            if not head or not str(head).lower().startswith("ofertant"):
                continue
            name = str(head).split(":", 1)[1].strip()
            found = by.get((lot, participant_key(name))) if name else None
            if not found:
                continue
            status, source, reason = found
            status = effective(status, source)
            if status == cf.NEEVALUAT or status not in COLORS:
                continue
            shown.append(status)
            stats[status] += 1
            row1 = ws.cell(row=ROW_STATUS, column=col)
            if _paint(row1, COLORS[status]):
                row1.value = PRICE_TEXT if reason == "price_130" else TEXTS[status]
            _paint(ws.cell(row=ROW_SPEC, column=col), COLORS[status])
        tab = lot_tab(shown)
        current = ws.sheet_properties.tabColor
        if tab and not (current is not None and str(current.rgb).upper() in KEEP_TAB_COLORS):
            ws.sheet_properties.tabColor = tab
            stats["tabs"] += 1
            logger.info(f"[VERDICTS] {ws.title}: вкладка {tab}, участников с решением {len(shown)}")
    out = Path(out_path) if out_path else Path(result_path).with_name(Path(result_path).stem + "_evaluare.xlsx")
    wb.save(out)
    stats["file"] = str(out)
    return dict(stats)


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format='[%(asctime)s] [%(levelname)s] %(message)s', force=True)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("result", type=Path)
    ap.add_argument("tender_dir", type=Path)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    kb = connect(args.db)
    try:
        stats = write_verdicts(args.result, args.tender_dir, kb, args.out)
    finally:
        kb.close()
    logger.info(f"[VERDICTS] {stats}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
