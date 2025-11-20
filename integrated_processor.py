import os
import re
from datetime import datetime
from openpyxl import load_workbook
import requests
from bs4 import BeautifulSoup

# Папка для ресурсов
RESOURCE_DIR = './resources'

# Фиксированное имя шаблонного файла
TEMPLATE_FILE = os.path.join(RESOURCE_DIR, 'sample_model.xlsx')

def find_file(prefix, ext='.xlsx'):
    """
    Поиск файла по префиксу и расширению в папке RESOURCE_DIR.
    """
    for fname in os.listdir(RESOURCE_DIR):
        if fname.startswith(prefix) and fname.endswith(ext):
            return os.path.join(RESOURCE_DIR, fname)
    raise FileNotFoundError(f"Файл с префиксом '{prefix}' не найден в директории {RESOURCE_DIR}!")

def safe_col_idx(names, headers):
    """
    Поиск индекса столбца по возможным названиям.
    """
    if isinstance(names, str):
        names = [names]
    for name in names:
        try:
            return headers.index(name) + 1
        except ValueError:
            continue
    # Не бросаем ошибку — возвращаем None
    print(f"Внимание! Столбец из {names} не найден.")
    return None

def safe_row_get(row, keys, headers):
    """
    Безопасное получение значения из строки по названию столбца.
    """
    idx = safe_col_idx(keys, headers)
    if idx is not None:
        return row[idx - 1]
    return None

def scrape_lots(url):
    """
    Сбор ссылок/описаний лотов со страницы (используется отдельно от обработки Excel).
    """
    print(f"Скрапинг данных с URL: {url}")
    try:
        response = requests.get(url, timeout=15)
    except Exception as e:
        print(f"Ошибка при запросе: {e}")
        return None

    if response.status_code != 200:
        print(f"Ошибка загрузки страницы: {response.status_code}")
        return None

    soup = BeautifulSoup(response.text, 'html.parser')
    lots = []
    for element in soup.find_all('a', href=True):
        link = element['href']
        text = element.get_text(strip=True)
        lots.append({'link': link, 'description': text})
    return lots

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

# -----------------------------------------------------------------------------
# Основная логика
# -----------------------------------------------------------------------------
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

    # Регекс для поиска существующих листов вида "lot N"
    lot_regex = re.compile(r'^lot (\d+)$')

    # Создаём mapping номер_лота (int) -> Worksheet (объект)
    parent_lot_sheets = {}
    for sname in wb_child.sheetnames:
        m = lot_regex.match(sname)
        if m:
            try:
                num = int(m.group(1))
                parent_lot_sheets[num] = wb_child[sname]
            except Exception:
                # пропускаем некорректные имена
                continue

    print(f"Найдено в шаблоне/книге {len(parent_lot_sheets)} существующих лотов: {sorted(parent_lot_sheets.keys())}")

    # Получение заголовков из родительского файла (первая строка)
    headers = [cell.value for cell in ws_parent[1]]
    print("Заголовки в родительском файле:", headers)

    # Обработка строк с лотами: сохраняем лишь одно создание листа на номер лота,
    # повторные строки дополняют только B11 (specificatie)
    for row in ws_parent.iter_rows(min_row=2, values_only=True):
        nr_lot_raw = safe_row_get(row, COLS['nr_lot'], headers)
        if nr_lot_raw is None or (isinstance(nr_lot_raw, str) and not nr_lot_raw.strip()):
            continue

        # Приводим номер лота к целому: если "244.0" -> 244, если "244" -> 244.
        try:
            nr_lot_int = int(float(nr_lot_raw))
        except Exception:
            print(f"Некорректный номер лота (пропуск): {nr_lot_raw!r}")
            continue

        denumire = safe_row_get(row, COLS['denumire'], headers)
        specificatie_data = safe_row_get(row, COLS['specificatie'], headers)
        unitate = safe_row_get(row, COLS['unitate_masura'], headers)
        cantitate = safe_row_get(row, COLS['cantitate_total'], headers)
        suma = safe_row_get(row, COLS['suma_alocata'], headers)

        # Если лист уже создан (в шаблоне или нами ранее) — дополняем ТОЛЬКО B11
        if nr_lot_int in parent_lot_sheets:
            ws_target = parent_lot_sheets[nr_lot_int]
            if specificatie_data:
                existing = ws_target["B11"].value or ""
                # Избегаем точного дублирования блока (опционально)
                specific_to_add = str(specificatie_data).strip()
                if specific_to_add:
                    if specific_to_add not in existing:
                        new_val = (existing + "\n" + specific_to_add).strip() if existing else specific_to_add
                        ws_target["B11"] = new_val
                        print(f"Дополнена спецификация для lot {nr_lot_int}")
                    else:
                        print(f"Спецификация уже содержит этот текст для lot {nr_lot_int}, пропускаем добавление.")
            else:
                print(f"Для lot {nr_lot_int} нет данных specificatie для добавления.")
            # НЕ переписываем другие поля
            continue

        # Иначе — создаём новый лист из шаблона и заполняем все целевые поля
        new_sheet = wb_child.copy_worksheet(template_sheet)
        new_title = f"lot {nr_lot_int}"
        # Название может автоматически измениться при коллизии, но мы заранее проверяем mapping,
        # поэтому здесь задаём ожидаемое название.
        new_sheet.title = new_title
        parent_lot_sheets[nr_lot_int] = new_sheet
        print(f"Создаем лист: {new_sheet.title}")

        # B1: "Lot nr. X {denumirea_lotului}"
        denumire_text = denumire if denumire is not None else ""
        new_sheet["B1"] = f"Lot nr. {nr_lot_int} {denumire_text}".strip()

        # Заполняем остальные ячейки по TARGET_MAP, кроме specificatie (обработаем отдельно)
        for key, target_cells in TARGET_MAP.items():
            if key == "specificatie":
                continue
            value = None
            if key == "unitate_masura":
                value = unitate
            elif key == "cantitate_total":
                value = cantitate
            elif key == "suma_alocata":
                value = suma
            elif key == "denumire":
                # denumire уже записан в B1, но на всякий случай можно записать в указанные ячейки
                value = denumire_text

            if value is not None:
                for cell in target_cells:
                    new_sheet[cell] = value

        # Устанавливаем initial specificatie для B11 (если есть)
        if specificatie_data:
            new_sheet["B11"] = str(specificatie_data).strip()

    # Формирование имени выходного файла
    parent_filename = os.path.basename(parent_file).replace('-', '')
    timestamp = datetime.now().strftime("%d.%m.%Y_%H-%M-%S")
    output_name = f"{RESOURCE_DIR}/{os.path.splitext(parent_filename)[0]}_{timestamp}_upd.xlsx"

    # Сохранение обновлённого дочернего файла
    wb_child.save(output_name)
    print(f"Готово! Файл сохранён как {output_name}")

    # -----------------------------------------------------------------------------
    # Этап 2: Скрапинг данных (опционально) — оставил как вспомогательный шаг (не влияет на Excel)
    # -----------------------------------------------------------------------------
    scrape_url = "https://achizitii.md/ro/public/tender/21463195/"
    lots_data = scrape_lots(scrape_url)
    if lots_data:
        print("\n[INFO] Данные о лотах с веб-страницы (пример вывода):")
        for lot in lots_data[:20]:  # ограниченный вывод
            print(f" - Описание: {lot['description']}, Ссылка: {lot['link']}")

except FileNotFoundError as e:
    print(e)
except Exception as e:
    print(f"Неизвестная ошибка произошла: {e}")