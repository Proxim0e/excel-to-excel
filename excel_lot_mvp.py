"""
Скрипт переноса данных между Excel-файлами (MVP)

Данный скрипт выполняет перенос данных из родительского файла в дочерний файл.
Шаблонный файл для дочернего файла имеет фиксированное имя `sample_model.xlsx`.
Выходные файлы формируются с уникальным именем, включающим название родительского файла,
текущую дату и время.

Особенности:
- Фиксированное имя для шаблонного файла: `sample_model.xlsx`.
- Уникальность выходного файла за счёт добавления даты и таймштампа.
- Формат имени выходного файла: `<родительский файл>_<дата>_<время>_upd.xlsx`.

Автор: Proxim0e
Дата: 19 ноября 2025 года
"""

import os
import re
from datetime import datetime
from openpyxl import load_workbook

# Папка для ресурсов
RESOURCE_DIR = './resources'

# Фиксированное имя шаблонного файла
TEMPLATE_FILE = os.path.join(RESOURCE_DIR, 'sample_model.xlsx')

def find_file(prefix, ext='.xlsx'):
    """
    Функция поиска файла по префиксу и расширению в папке `RESOURCE_DIR`.

    Аргументы:
        prefix (str): Префикс имени файла.
        ext (str): Расширение файла (по умолчанию `.xlsx`).

    Возвращает:
        str: Полный путь к найденному файлу.

    Исключения:
        FileNotFoundError: Если файл с заданным префиксом не найден.
    """
    for fname in os.listdir(RESOURCE_DIR):
        if fname.startswith(prefix) and fname.endswith(ext):
            return os.path.join(RESOURCE_DIR, fname)
    raise FileNotFoundError(f"Файл с префиксом '{prefix}' не найден в директории {RESOURCE_DIR}!")

def safe_col_idx(names, headers):
    """
    Функция поиска индекса столбца по возможным названиям.

    Аргументы:
        names (list или str): Список возможных названий столбца или одно название.
        headers (list): Заголовки столбцов в родительском файле.

    Возвращает:
        int: Индекс найденного столбца (начиная с 1).

    Печатает:
        Предупреждение: Если ни одно название не совпало с заголовками.
    """
    if isinstance(names, str):
        names = [names]  # Поддержка строки вместо списка
    for name in names:
        try:
            return headers.index(name) + 1
        except ValueError:
            continue
    print(f"Внимание! Столбец из {names} не найден.")
    return None

def safe_row_get(row, keys, headers):
    """
    Метод безопасного получения значения из строки по названию столбца.

    Аргументы:
        row (list): Строка из родительского файла.
        keys (list или str): Список возможных названий столбца или одно название.
        headers (list): Заголовки столбцов в родительском файле.

    Возвращает:
        Any: Значение найденной ячейки, либо None.
    """
    idx = safe_col_idx(keys, headers)
    if idx is not None:
        return row[idx - 1]
    return None

# Предопределённые названия столбцов (гибкие варианты)
COLS = {
    'nr_lot':            ['Nr. Lot', 'Nr Lot'],
    'denumire':          ['Denumirea Lotului\n04.09.2025', 'Denumirea Lotului', 'Denumire Lot NEW2'],
    'specificatie':      ['Specificația Tehnică\n04.09.2025', 'Specificația Тehnică', 'Specificarea tehnică deplină NEW2'],
    'unitate_masura':    ['Unitatea de măsură'],
    'cantitate_total':   ['Cantitatea Totală'],
    'suma_alocata':      ['Suma alocată'],
}

# Соответствие данных целевым ячейкам
TARGET_MAP = {
    "denumire": ("B1",),         # Lot nr. X {denumirea_lotului}
    "specificatie": ("B11",),
    "unitate_masura": ("C9",),
    "cantitate_total": ("A1",),
    "suma_alocata": ("D8",)
}

# Основная логика
try:
    # Поиск родительского файла
    parent_file = find_file('-')
    print(f"Родительский файл: {parent_file}")
    print(f"Шаблонный файл: {TEMPLATE_FILE}")

    # Загрузка файлов
    wb_parent = load_workbook(parent_file, data_only=True)
    ws_parent = wb_parent.active
    wb_child = load_workbook(TEMPLATE_FILE)
    sheet_names = wb_child.sheetnames

    # Определение шаблона для листов
    template_sheet_name = sheet_names[0]
    template_sheet = wb_child[template_sheet_name]

    # Анализ существующих листов в дочернем файле
    lot_regex = re.compile(r'^lot (\d+)$')
    existing_lots = {int(lot_regex.match(s).group(1)): s for s in sheet_names if lot_regex.match(s)}
    max_lot = max(existing_lots.keys(), default=0)
    print(f"Обнаружено {len(existing_lots)} лотов. Максимальный: {max_lot}")

    # Получение заголовков
    headers = [cell.value for cell in ws_parent[1]]
    print("Заголовки в родительском файле:", headers)

    # Обработка строк с лотами
    for row in ws_parent.iter_rows(min_row=2, values_only=True):
        nr_lot = safe_row_get(row, COLS['nr_lot'], headers)
        if nr_lot is None or (isinstance(nr_lot, str) and not nr_lot.strip()):
            continue
        try:
            nr_lot_int = int(nr_lot)
        except ValueError:
            print(f"Некорректный номер лота: {nr_lot}")
            continue

        if nr_lot_int in existing_lots:
            print(f"Лист для лота {nr_lot_int} уже существует, пропускаем.")
            continue

        # Создание нового листа
        new_sheet = wb_child.copy_worksheet(template_sheet)
        new_sheet.title = f"lot {nr_lot_int}"
        print(f"Создаем лист: {new_sheet.title}")

        for key, target_cells in TARGET_MAP.items():
            value = safe_row_get(row, COLS[key], headers)
            if value is not None:
                for cell in target_cells:
                    new_sheet[cell] = value

    # Формирование имени выходного файла
    #parent_filename = os.path.basename(parent_file)
    parent_filename = os.path.basename(parent_file).replace('-', '')
    timestamp = datetime.now().strftime("%d.%m.%Y_%H-%M-%S")
    output_name = f"{RESOURCE_DIR}/{os.path.splitext(parent_filename)[0]}_{timestamp}_upd.xlsx"

    # Сохранение обновлённого дочернего файла
    wb_child.save(output_name)
    print(f"Готово! Файл сохранён как {output_name}")

except FileNotFoundError as e:
    print(e)
except Exception as e:
    print(f"Неизвестная ошибка произошла: {e}")