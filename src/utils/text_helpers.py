import re
from typing import Optional


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
    Попытка извлечь число из строки цены.
    Поддерживает форматы: "30 833,35 MDL", "30.833,35", "30833.35".
    Возвращает float или None.
    """
    if not price_str:
        return None
    s = price_str.strip()

    # Ищем кусок, похожий на число
    m = re.search(r'[\d\s\.,]+', s)
    if not m:
        return None

    s = m.group(0).strip()
    s = s.replace('\u00A0', ' ')  # неразрывный пробел в обычный

    # Логика определения точки и запятой
    # Если есть и точка, и запятая: та, что правее — десятичный разделитель
    if ',' in s and '.' in s:
        if s.rfind(',') > s.rfind('.'):
            s = s.replace('.', '')  # убираем тысячи-разделитель (точки)
            s = s.replace(',', '.')  # меняем запятую на точку
        else:
            s = s.replace(',', '')  # убираем тысячи-разделитель (запятые)
    elif ',' in s:
        # Вероятно, европейский формат (30.000,00) -> запятая это десятичная, точка это тысячи
        # Но для простоты парсинга убираем пробелы и заменяем запятую
        s = s.replace(' ', '')
        s = s.replace(',', '.')
    else:
        s = s.replace(' ', '')

    # Удаляем всё, кроме цифр и точки
    s = re.sub(r'[^\d\.]', '', s)

    if not s:
        return None

    try:
        return float(s)
    except Exception:
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