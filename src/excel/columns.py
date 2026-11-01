"""
Вставка колонки в уже заполненный лист.

openpyxl.insert_cols сдвигает только значения ячеек: ссылки в формулах («=F8/A1»), объединённые
ячейки и ширина колонок остаются на старых местах. Здесь сдвигается всё, что правее вставки.
"""
import re
from copy import copy

from openpyxl.utils import column_index_from_string, get_column_letter

# Ссылка на ячейку этого листа: F8, $F$8 (ссылки на другие листы «Лист!F8» не трогаем)
REF_RE = re.compile(r"(?<![A-Za-z0-9_!.])(\$?)([A-Z]{1,3})(\$?)(\d+)(?![A-Za-z0-9_(])")


def shift_formula(formula: str, at: int) -> str:
    """Колонки начиная с at сдвигаются на одну вправо; строковые литералы не трогаются."""
    parts = re.split(r'("[^"]*")', formula)
    for i in range(0, len(parts), 2):
        parts[i] = REF_RE.sub(lambda m: _shift_ref(m, at), parts[i])
    return "".join(parts)


def _shift_ref(m, at: int) -> str:
    col = column_index_from_string(m.group(2))
    if col >= at:
        col += 1
    return f"{m.group(1)}{get_column_letter(col)}{m.group(3)}{m.group(4)}"


def insert_column(ws, at: int, style_from: int = None) -> None:
    """Вставляет пустую колонку под номером at; style_from - колонка-образец стилей (номер до вставки)."""
    widths = {column_index_from_string(k): d.width for k, d in ws.column_dimensions.items()
              if d.width is not None}
    merged = [r for r in ws.merged_cells.ranges if r.max_col >= at]
    for r in merged:
        ws.unmerge_cells(str(r))

    ws.insert_cols(at)

    for row in ws.iter_rows():
        for cell in row:
            if isinstance(cell.value, str) and cell.value.startswith("="):
                cell.value = shift_formula(cell.value, at)
    for r in merged:
        min_col = r.min_col + 1 if r.min_col >= at else r.min_col
        ws.merge_cells(start_row=r.min_row, end_row=r.max_row, start_column=min_col, end_column=r.max_col + 1)
    for col in sorted(widths, reverse=True):
        if col >= at:
            ws.column_dimensions[get_column_letter(col + 1)].width = widths[col]
    if style_from is not None:
        src = style_from + 1 if style_from >= at else style_from
        if style_from in widths:
            ws.column_dimensions[get_column_letter(at)].width = widths[style_from]
        for row in range(1, ws.max_row + 1):
            s, d = ws.cell(row=row, column=src), ws.cell(row=row, column=at)
            if s.has_style:
                d.font, d.border, d.alignment = copy(s.font), copy(s.border), copy(s.alignment)
                d.number_format = s.number_format
