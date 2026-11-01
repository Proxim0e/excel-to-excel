"""
Лист «Проверить» для истории: вопросы импорта (противоречие текста и цвета строки 1 и т.п.)
и ответы человека («спросить один раз»).

export: data/review_<тендер>.xlsx - по строке на открытый вопрос, колонки «Решение», «Причина»
        (выпадающие списки) и «Комментарий». Уже отвеченные вопросы не попадают.
apply:  ответы -> таблица answers базы знаний; текущее решение (представление current_offers)
        берёт ответ человека вместо расшифровки по кодбуку.
"""
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

DECISIONS = {
    "accepted": "принят (соответствует)",
    "rejected": "отклонён",
    "not_evaluated": "не оценивался",
    "alternative": "альтернатива",
    "samples": "образцы",
    "working_group": "рабочая группа",
    "clarification": "уточнения",
    "not_held": "закупка не состоялась",
    "improved_offer": "улучшенное предложение",
    "excluded": "не учитывать (ошибка разметки)",
}
REASONS = {
    "price_130": "цена ≥ 130% базы",
    "guarantee_insufficient": "гарантия: недостаточная сумма",
    "guarantee_wrong": "гарантия: неверная",
    "amdm": "нет регистрации AMDM",
    "technical": "технически не соответствует",
    "annulled_g": "G - аннулирование по техническим причинам",
    "withdrawn": "отозвал оферту",
    "alternative_rejected": "альтернатива не принята",
    "other": "другое (в комментарии)",
}
SHEET = "Проверить"
HEADER = ["Тендер", "Лот", "Лист", "Участник", "Вопрос", "Строка 1 (текст)", "Строка 1 (цвет)",
          "Цена / база", "Предложение системы", "Решение", "Причина", "Комментарий"]


def review_path(tender_id: str, base: Path) -> Path:
    return Path(base) / f"review_{tender_id}.xlsx"


def open_questions(conn, tender_id: str) -> List[tuple]:
    return conn.execute(
        "SELECT tender_id, lot_no, sheet, participant, open_question, row1_text, row1_color, price_ratio, "
        "decision, reason FROM current_offers WHERE tender_id = ? AND open_question IS NOT NULL "
        "ORDER BY lot_no, col", (tender_id,)).fetchall()


def export_review(conn, tender_id: str, path: Path) -> int:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation

    rows = open_questions(conn, tender_id)
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET
    ws.append(HEADER)
    for t, lot, sheet, who, why, text, color, ratio, decision, reason in rows:
        suggestion = DECISIONS.get(decision, decision) + (f"; {REASONS.get(reason, reason)}" if reason else "")
        ws.append([t, lot, sheet, who, why, text, color, round(ratio, 4) if ratio is not None else None,
                   suggestion, None, None, None])
        if color:
            ws.cell(row=ws.max_row, column=7).fill = PatternFill("solid", fgColor=color)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for col in (10, 11, 12):
        ws.cell(row=1, column=col).fill = PatternFill("solid", fgColor="FFFFFF00")
    for i, width in enumerate((10, 6, 10, 28, 45, 30, 12, 10, 30, 22, 30, 40), start=1):
        ws.column_dimensions[get_column_letter(i)].width = width
    for row in ws.iter_rows(min_row=2):
        row[4].alignment = Alignment(wrap_text=True, vertical="top")
    ws.freeze_panes = "A2"

    ref = wb.create_sheet("Справка")
    ref.append(["Решение", "Значение", "", "Причина", "Значение"])
    keys_d, keys_r = list(DECISIONS.items()), list(REASONS.items())
    for i in range(max(len(keys_d), len(keys_r))):
        d = keys_d[i] if i < len(keys_d) else ("", "")
        r = keys_r[i] if i < len(keys_r) else ("", "")
        ref.append([d[0], d[1], "", r[0], r[1]])
    for col, width in zip("ABCDE", (16, 34, 2, 24, 40)):
        ref.column_dimensions[col].width = width
    if rows:
        last = len(rows) + 1
        dv_d = DataValidation(type="list", formula1=f"=Справка!$A$2:$A${len(keys_d) + 1}", allow_blank=True)
        dv_r = DataValidation(type="list", formula1=f"=Справка!$D$2:$D${len(keys_r) + 1}", allow_blank=True)
        ws.add_data_validation(dv_d)
        ws.add_data_validation(dv_r)
        dv_d.add(f"J2:J{last}")
        dv_r.add(f"K2:K{last}")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return len(rows)


def apply_review(conn, path: Path) -> Tuple[int, List[str]]:
    """Ответы из файла «Проверить» -> answers. Возвращает (сколько сохранено, ошибки)."""
    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True)
    try:
        rows = list(wb[SHEET].iter_rows(values_only=True))
    finally:
        wb.close()
    header = list(rows[0])
    ix = {name: header.index(name) for name in HEADER}
    saved, errors = 0, []
    for n, row in enumerate(rows[1:], start=2):
        decision = (row[ix["Решение"]] or "").strip() if row[ix["Решение"]] else ""
        if not decision:
            continue
        reason = (row[ix["Причина"]] or "").strip() or None if row[ix["Причина"]] else None
        if decision not in DECISIONS:
            errors.append(f"строка {n}: неизвестное решение {decision!r}")
            continue
        if reason and reason not in REASONS:
            errors.append(f"строка {n}: неизвестная причина {reason!r}")
            continue
        save_answer(conn, str(row[ix["Тендер"]]), int(row[ix["Лот"]]), row[ix["Участник"]],
                    row[ix["Строка 1 (текст)"]], row[ix["Строка 1 (цвет)"]], decision, reason,
                    row[ix["Комментарий"]])
        saved += 1
    conn.commit()
    return saved, errors


def save_answer(conn, tender_id: str, lot_no: int, participant: str, row1_text: Optional[str],
                row1_color: Optional[str], decision: str, reason: Optional[str] = None,
                comment: Optional[str] = None) -> None:
    conn.execute("INSERT OR REPLACE INTO answers (tender_id, lot_no, participant, row1_text, row1_color, decision, "
                 "reason, comment, answered_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (tender_id, lot_no, participant, row1_text, row1_color, decision, reason, comment,
                  datetime.now().isoformat(timespec="seconds")))
