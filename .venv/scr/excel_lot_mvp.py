import os
import re
from openpyxl import load_workbook

# Автоматический поиск файлов по префиксу (родительский: -, дочерний: ++)
def find_file(prefix, ext='.xlsx'):
    for fname in os.listdir('.'):
        if fname.startswith(prefix) and fname.endswith(ext):
            return fname
    raise FileNotFoundError(f"Файл с префиксом '{prefix}' не найден!")

# Файл-родитель начинается с '-', файл-дочерний с '++'
parent_file = find_file('-')
child_file = find_file('++')

print(f"Родительский файл: {parent_file}")
print(f"Дочерний файл: {child_file}")

# Загружаем книги
wb_parent = load_workbook(parent_file, data_only=True)
ws_parent = wb_parent.active

wb_child = load_workbook(child_file)
sheet_names = wb_child.sheetnames

# Находим лист-образец
template_sheet_name = sheet_names[0]  # первый лист — шаблон (или поменяйте название, если у вас другой)
template_sheet = wb_child[template_sheet_name]

# Функция для получения всех lot-листов и их номеров
lot_regex = re.compile(r'^lot (\d+)$')
existing_lots = {int(lot_regex.match(s).group(1)): s for s in sheet_names if lot_regex.match(s)}
max_lot = max(existing_lots.keys(), default=0)

print(f"Обнаружено {len(existing_lots)} лотов. Максимальный: {max_lot}")

# Получаем заголовки родителя (печатем для дебага)
headers = [cell.value for cell in ws_parent[1]]
print("Заголовки в родительском файле:", headers)

# Универсальный поиск индекса по списку вариантов названия столбца
def safe_col_idx(names):
    if isinstance(names, str):  # Поддержка обратной совместимости со старой функцией
        names = [names]
    for name in names:
        try:
            return headers.index(name) + 1
        except ValueError:
            continue
    print(f"Внимание! Столбец из {names} не найден.")
    return None

# Функция безопасного доступа к строке по названию столбца
def safe_row_get(row, keys):
    idx = safe_col_idx(keys)
    if idx is not None:
        return row[idx - 1]
    return None

# Список вариантов названий для каждого столбца
COLS = {
    'nr_lot':            ['Nr. Lot'],
    'denumire':          ['Denumirea Lotului\n04.09.2025', 'Denumirea Lotului'],
    'specificatie':      ['Specificația Tehnică\n04.09.2025', 'Specificația Tehnică'],
    'unitate_masura':    ['Unitatea de măsură'],
    'cantitate_total':   ['Cantitatea Totală'],
    'suma_alocata':      ['Suma alocată'],
}

# Маппинг куда и что писать в дочернем листе
TARGET_MAP = {
    "denumire": ("B1",),         # Lot nr. X {denumirea_lotului}
    "specificatie": ("B11",),
    "unitate_masura": ("C9",),
    "cantitate_total": ("A1",),
    "suma_alocata": ("D9",)
}

# Проходим по строкам с лотами (начиная со второй строки, кроме шапки)
for row in ws_parent.iter_rows(min_row=2, values_only=True):
    # Получаем номер лота
    nr_lot = safe_row_get(row, COLS['nr_lot'])
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

    # Создаем новый лист из шаблона
    new_sheet = wb_child.copy_worksheet(template_sheet)
    new_sheet.title = f"lot {nr_lot_int}"
    print(f"Создаем лист: {new_sheet.title}")

    # Данные для заполнения
    denumire = safe_row_get(row, COLS['denumire'])
    specificatie = safe_row_get(row, COLS['specificatie'])
    unitate_masura = safe_row_get(row, COLS['unitate_masura'])
    cantitate_total = safe_row_get(row, COLS['cantitate_total'])
    suma_alocata = safe_row_get(row, COLS['suma_alocata'])

    # Заполняем нужные ячейки
    if denumire is not None:
        new_sheet["B1"] = f"Lot nr. {nr_lot_int} {denumire}"
    if specificatie is not None:
        new_sheet["B11"] = specificatie
    if unitate_masura is not None:
        new_sheet["C9"] = unitate_masura
    if cantitate_total is not None:
        new_sheet["A1"] = cantitate_total
    if suma_alocata is not None:
        new_sheet["D9"] = suma_alocata

# Сохраняем новый дочерний файл
output_name = "++lots_result.xlsx"
wb_child.save(output_name)
print(f"Готово! Файл сохранён как {output_name}")