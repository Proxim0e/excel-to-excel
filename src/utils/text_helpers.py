import re
import logging
from typing import Optional
logger = logging.getLogger(__name__)


def strip_price_label(s: str) -> str:
    """
    Очищает префиксы вроде 'prețul ofertei: ' из текстовых представлений цены.
    """
    if not s:
        return s
    s = s.strip()
    # Разные варианты написания цены и prețul (с диакритиками или без)
    patterns = [
        r'(?i)prețul ofertei[:\s]*',
        r'(?i)preţul ofertei[:\s]*',
        r'(?i)preţul[:\s]*',
        r'(?i)preț[:\s]*',
    ]
    for pattern in patterns:
        s = re.sub(pattern, '', s)
    return s.strip()


def parse_price_to_number(price_str: Optional[str]) -> Optional[float]:
    """
    Парсер цен.
    Сначала очищает текстовые метки, затем выделяет число.
    """
    if not price_str:
        return None

    # 1. Сначала убираем мусор-префиксы ("Preţul ofertei:" и т.д.)
    # Это самый важный шаг, который мы пропустили ранее
    s = strip_price_label(price_str)
    logger.debug(f"Price parse: после очистки текста: '{s}'")

    # 2. Ищем в строке кусок, похожий на число (цифры, пробелы, точки, запятые)
    m = re.search(r'[\d\s\.,]+', s)
    if not m:
        logger.debug(f"Price parse: цифры не найдены в '{s}'")
        return None

    s = m.group(0).strip()
    logger.debug(f"Price parse: числовой кусок: '{s}'")

    # 3. Превращаем неразрывный пробел в обычный
    s = s.replace('\u00A0', ' ')

    # 4. Удаляем пробелы (разделитель тысяч)
    s = s.replace(' ', '')

    # 5. Определяем десятичный разделитель
    if ',' in s and '.' in s:
        # Если оба есть: "1.234,56" или "1,234.56"
        if s.rfind(',') > s.rfind('.'):
            s = s.replace('.', '').replace(',', '.')
        else:
            s = s.replace(',', '')
    elif ',' in s:
        # Если только запятая (европейский стиль): меняем на точку
        s = s.replace(',', '.')
    # Если только точка - оставляем как есть

    # Очищаем от оставшегося мусора
    s = re.sub(r'[^\d.]', '', s)

    if not s:
        return None

    try:
        val = float(s)
        logger.debug(f"Price parse: ФИНАЛ: {val}")
        return val
    except ValueError as e:
        logger.warning(f"Price parse: ошибка конвертации '{s}' -> {e}")
        return None


def clean_company_name(raw_name: Optional[str]) -> str:
    """
    Нормализация названия участника.
    Убирает префиксы вроде 'Denumirea participantului:' и обрывает строку перед 'Preţ'.
    """
    if not raw_name:
        return ""
    s = raw_name.strip()
    s = re.sub(r'(?i)denumirea participantului[:\s]*', '', s).strip()
    s = re.sub(r'^\s*Denumirea[:\s]*', '', s, flags=re.I).strip()
    # Обрываем, если идет слово "Preţ", чтобы не ловить цену в названии
    s = re.split(r'\s+Preţ|Prețul|Preț', s)[0].strip()
    return s


def prepare_lot_title_for_b1(title: Optional[str]) -> str:
    """
    Очищает текст 'title' перед помещением в B1.
    Убирает префиксы 'Lot nr N...'.
    """
    if not title:
        return ""
    s = str(title).strip()
    # ... логика ...
    return re.sub(r'\s+', ' ', s).strip()


def normalize_spaces(s: Optional[str]) -> str:
    """
    Приводит последовательности пробельных символов к одному пробелу.
    """
    if not s:
        return ""
    return re.sub(r'\s+', ' ', str(s)).strip()