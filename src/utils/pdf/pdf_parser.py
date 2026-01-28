import pdfplumber
from pathlib import Path
from loguru import logger
import re
from typing import Optional, Dict

# Список СТРОГО для заголовков (без слов из названий компаний!)
HEADER_STOP_WORDS = {
    "cod", "cpv", "nr.", "d/o", "denumirea", "bunurilor", "serviciilor",
    "modelul", "articolului", "poziției", "tara", "de", "origine",
    "producătorul", "specificarea", "tehnica", "deplină", "solicitată",
    "de", "catre", "autoritatea", "contractanta", "propusa", "ofertant",
    "standarde", "referinţă", "înregistrare", "amdm", "valabilitate"
}

COUNTRIES = ["China", "Romania", "Turcia", "Germania", "Italia", "Moldova", "Japonia", "Coreea",
             "India", "Franta", "Spania", "Marea Britanie", "SUA", "Russia", "Ungaria", "Polonia",
             "Cehia", "Elvetia", "Ucraina"]

# Суффиксы производителей
MFG_SUFFIXES = ["SRL", "GmbH", "Co.", "Ltd", "Inc", "AG", "SA", "S.A.", "Limited", "Corp", "Sp.z o.o."]


def parse_spec_pdf(pdf_path: Path, lot_number: Optional[str] = None, participant_name: Optional[str] = None) -> Dict[
    str, Optional[str]]:
    """
    Парсер v4: Умный поиск Mfg (даже без суффиксов) и чистая модель.
    """
    result = {
        "model": None,
        "country": None,
        "manufacturer": None,
        "specification_text": None,
        "dm_code": None,
        "error": None
    }

    if participant_name:
        logger.info(f"[{participant_name}] Анализируем файл: {pdf_path.name} (Лот: {lot_number})")
    else:
        logger.info(f"Анализируем файл: {pdf_path.name} (Лот: {lot_number})")

    try:
        with pdfplumber.open(pdf_path) as pdf:
            all_tables = []
            for page in pdf.pages:
                page_tables = page.extract_tables()
                if page_tables:
                    all_tables.extend(page_tables)

            if not all_tables:
                result["error"] = "Таблиц не найдено"
                return result

            # 1. Создаем поток токенов
            tokens = []
            for table in all_tables:
                for row in table:
                    for cell in row:
                        if cell:
                            clean_text = str(cell).replace('\n', ' ').strip()
                            if clean_text:
                                tokens.append(clean_text)

            logger.debug(f"[{participant_name}] Собран поток из {len(tokens)} токенов.")

            # 2. Поиск номера лота
            lot_num = ""
            if lot_number:
                parts = lot_number.split()
                # Ищем самую длинную строку цифр, похожую на номер
                lot_num = max([s for s in parts if s.isdigit()], default="")

            lot_idx = -1
            if lot_num:
                for i, token in enumerate(tokens):
                    if lot_num in token:
                        lot_idx = i
                        break

            if lot_idx == -1:
                logger.warning(f"[{participant_name}] Номер лота '{lot_num}' не найден.")
                return {"error": "Лот не найден"}

            # 3. Окно данных
            data_window = tokens[lot_idx + 1: lot_idx + 80]  # Увеличил окно до 80

            if not data_window:
                return {"error": "Нет данных после лота"}

            # 4. ИЗВЛЕЧЕНИЕ С ЭВРИСТИКОЙ

            # А. Поиск Страны
            found_country = None
            for token in data_window:
                if token in COUNTRIES:
                    found_country = token
                    result["country"] = token
                    break

            # Б. Поиск DM-кода
            dm_code = None
            for token in data_window:
                if token.startswith("CE/") or token.startswith("DM"):
                    dm_code = token
                    result["dm_code"] = token
            # Ищем последний, если нашлось несколько
            if dm_code:
                result["dm_code"] = dm_code

            # В. Умный поиск Производителя

            # 1. По суффиксам (SRL, GmbH)
            found_mfg = None
            for token in data_window:
                if any(token.endswith(s) for s in MFG_SUFFIXES):
                    found_mfg = token
                    break

            # 2. Если не нашли (например SCW Medicath), ищем по признакам:
            # - Длина > 3
            # - Не Страна
            # - Не начинается с цифры (CPV)
            # - Буквы верхнего регистра (названия компаний часто таковы)
            if not found_mfg:
                for token in data_window:
                    if len(token) < 4: continue  # Слишком короткие (иначе)
                    if token in COUNTRIES: continue  # Страна
                    if token.replace('.', '').replace('-', '').isdigit(): continue  # Число/CPV

                    # Проверяем на "Business-like" токен
                    if token[0].isupper():
                        found_mfg = token
                        break

            if found_mfg:
                result["manufacturer"] = found_mfg

            # Г. Определение Границ Модели
            # Модель - это то, что идет сразу после лота, но ДО производителя (или спецификации).
            # Важно пропустить CPV код (обычно идет сразу после лота).

            model_tokens = []
            model_end_idx = len(data_window)

            if found_mfg:
                try:
                    mfg_idx = data_window.index(found_mfg)
                    model_end_idx = mfg_idx
                except ValueError:
                    pass

            # Собираем токены для модели
            for i, token in enumerate(data_window[:model_end_idx]):
                # Пропускаем короткие и CPV коды
                if len(token) < 2: continue
                # Пропускаем числа (CPV коды типа 33100000-1)
                if token.replace('.', '').isdigit(): continue
                # Если это страна (хотя модель редко бывает только страной), пропускаем
                if token in COUNTRIES: continue

                model_tokens.append(token)

            # Если нашлось много токенов, вероятно, мы попали в спецификацию (описание).
            # Берем только первые 2-3 токена.
            if len(model_tokens) > 3:
                model_tokens = model_tokens[:2]

            model_name = " ".join(model_tokens).strip()

            # Д. Определение Спецификации
            # Это всё, что идет после производителя (или если производителя нет, то остаток).
            spec_tokens = []
            start_spec_idx = model_end_idx + 1

            for i in range(start_spec_idx, len(data_window)):
                token = data_window[i]
                # Пропускаем страну (если она была после модели)
                if token in COUNTRIES: continue
                # Пропускаем DM код
                if token.startswith("CE/DM"): continue

                spec_tokens.append(token)

            spec_text = " ".join(spec_tokens).strip()
            if spec_text:
                result["specification_text"] = spec_text[:1500]

            # Е. Финальная проверка модели
            if not model_name:
                # Если модель пуста, но есть производитель, берем токен до него
                if found_mfg:
                    try:
                        m_idx = data_window.index(found_mfg)
                        # Ищем последний токен до производителя, который не CPV и не цифры
                        for i in range(m_idx - 1, 0, -1):
                            t = data_window[i]
                            if not t.replace('.', '').isdigit():
                                model_name = t
                                break
                    except:
                        pass

            if model_name:
                result["model"] = model_name

            if any(result.values()):
                logger.info(f"  -> ИЗВЛЕЧЕНО (Эвристика v4):")
                logger.info(f"     Model: {model_name}")
                logger.info(f"     Mfg: {found_mfg}")
                logger.info(f"     Country: {found_country}")
                return result
            else:
                logger.warning(f"  -> Не удалось распознать данные.")
                return {"error": "Эвристика не сработала"}

    except Exception as e:
        logger.error(f"[{participant_name}] Критическая ошибка: {e}")
        return {"error": str(e)}

    return result