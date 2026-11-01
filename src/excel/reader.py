import logging
import re
from typing import Dict, List, Optional, Any
from pathlib import Path
from openpyxl import load_workbook

from src.config import RESOURCE_DIR, COLS

logger = logging.getLogger(__name__)


def find_parent_file() -> Path:
    """
    Ищет в папке resources файл, начинающийся с '-'.
    Выбрасывает FileNotFoundError, если не найден.
    """
    if not RESOURCE_DIR.exists():
        raise FileNotFoundError(f"Directory {RESOURCE_DIR} does not exist")

    for fname in RESOURCE_DIR.glob('-*'):
        if fname.suffix.lower() in ('.xlsx', '.xls'):
            return fname

    raise FileNotFoundError(f"Parent file starting with '-' not found in {RESOURCE_DIR}")


def get_parent_data() -> Dict[int, Dict[str, Any]]:
    """
    Читает родительский файл и возвращает данные по лотам.

    Returns:
        Словарь { lot_number: { key: value, ... } }
    """
    parent_path = find_parent_file()
    wb = load_workbook(parent_path, data_only=True)
    ws = wb.active

    headers = [cell.value for cell in ws[1]]

    # Находим индексы нужных колонок по маппингу COLS
    col_indices = {}
    for key, possible_names in COLS.items():
        idx = _find_col_index(headers, possible_names)
        if idx:
            col_indices[key] = idx

    # Если не нашли номер лота, работать не можем
    if 'nr_lot' not in col_indices:
        raise ValueError("Column 'Nr. Lot' not found in parent file headers")

    return collect_lots(ws.iter_rows(min_row=2, values_only=True), col_indices)


POSITION_KEYS = ('specificatie', 'unitate_masura', 'cantitate_total')


def collect_lots(rows, col_indices: Dict[str, int]) -> Dict[int, Dict[str, Any]]:
    """
    Строки исходного файла -> данные по лотам.

    Если номер лота повторяется в нескольких строках - это один лот из нескольких позиций
    (в 21641880: лоты 184-201, 279 - тест-индикатор + журнал REGISTRU, лот 69 - 9 позиций),
    а не дубль. Название, количество, единица и сумма лота (suma alocată указана один раз) - по
    первой позиции; спецификация лота (B11) - все позиции по порядку, чтобы требования ко второй
    и следующим позициям не терялись при проверке оферт.
    """
    lot_data: Dict[int, Dict[str, Any]] = {}
    positions: Dict[int, List[Dict[str, Any]]] = {}

    for row in rows:
        nr_lot_val = row[col_indices['nr_lot'] - 1]

        # Пропускаем пустые строки
        if nr_lot_val is None or (isinstance(nr_lot_val, str) and not nr_lot_val.strip()):
            continue

        try:
            # Номер лота может быть float (1.0) или string ("1")
            nr_lot = int(float(nr_lot_val))
        except (ValueError, TypeError):
            continue

        # Собираем данные
        data = {}
        for key in ['denumire', 'specificatie', 'unitate_masura', 'cantitate_total', 'suma_alocata']:
            if key in col_indices:
                data[key] = row[col_indices[key] - 1]
            else:
                data[key] = None

        positions.setdefault(nr_lot, []).append({k: data[k] for k in POSITION_KEYS})
        if nr_lot in lot_data:
            # пустые значения первой позиции дополняются из следующих, непустые не затираются
            first = lot_data[nr_lot]
            for k, v in data.items():
                if first.get(k) in (None, "") and v not in (None, ""):
                    first[k] = v
            continue
        lot_data[nr_lot] = data

    multi = {lot: pos for lot, pos in positions.items() if len(pos) > 1}
    for lot, pos in multi.items():
        lot_data[lot]['specificatie'] = _positions_spec(pos)
        lot_data[lot]['positions'] = pos
    if multi:
        logger.info(f"Лоты из нескольких позиций (данные лота - по первой позиции, спецификация - все позиции): "
                    f"{', '.join(f'{lot} ({len(pos)})' for lot, pos in sorted(multi.items()))}")

    return lot_data


def _positions_spec(positions: List[Dict[str, Any]]) -> str:
    """Спецификация лота из нескольких позиций: 'Poziția N (кол-во ед.): спецификация'."""
    parts = []
    for i, pos in enumerate(positions, 1):
        qty = pos.get('cantitate_total')
        if isinstance(qty, float) and qty.is_integer():
            qty = int(qty)
        amount = " ".join(str(x).strip() for x in (qty, pos.get('unitate_masura')) if x not in (None, ""))
        head = f"Poziția {i}" + (f" ({amount})" if amount else "")
        parts.append(f"{head}: {str(pos.get('specificatie') or '').strip()}")
    return "\n\n".join(parts)


def get_tender_url_from_parent() -> Optional[str]:
    """
    Извлекает Tender URL из родительского файла.
    Ищет колонку с заголовком, содержащим 'site'/'url'/'link'.
    """
    try:
        parent_path = find_parent_file()
    except FileNotFoundError:
        return None

    wb = load_workbook(parent_path, data_only=True)
    ws = wb.active
    headers = [cell.value for cell in ws[1]]

    # Ищем колонку с URL
    url_col_idx = None
    for i, h in enumerate(headers):
        if not h:
            continue
        h_lower = str(h).lower()
        if h_lower in ("site", "url", "link") or any(k in h_lower for k in ("site", "url", "link")):
            url_col_idx = i + 1
            break

    if url_col_idx:
        # Ищем первое непустое значение
        for row in ws.iter_rows(min_row=2, values_only=True):
            val = row[url_col_idx - 1]
            if val and isinstance(val, str) and val.strip().lower().startswith("http"):
                return val.strip()

    return None


def _find_col_index(headers: List[Optional[str]], possible_names: List[str]) -> Optional[int]:
    """Вспомогательная функция: ищет индекс колонки по списку возможных имен."""
    for name in possible_names:
        # Чувствительность к регистру не важна, но сохраняем порядок имен в списке
        if name is None:
            continue
        for i, h in enumerate(headers):
            if h is None:
                continue
            if str(h).strip().lower() == str(name).strip().lower():
                return i + 1
    return None