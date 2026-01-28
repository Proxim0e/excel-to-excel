import re
import logging

logger = logging.getLogger(__name__)

import re


def sanitize_filename(name: str) -> str:
    """
    Очищает строку, чтобы использовать её как имя файла.
    Увеличиваем лимит и улучшаем логику расширения.
    """
    if not name:
        return "unknown"

    s = str(name)

    # 1. Убираем запрещенные символы
    s = re.sub(r'[:\\/*?"<>|]', '', s)

    # 2. Убираем лишние пробелы
    s = re.sub(r'\s+', ' ', s)

    # 3. Обрезаем только если имя файла слишком длинное
    # Windows лимит на имя файла ~ 255 символов.
    # Умные люди делают отступ 200-220, чтобы влезало в полный путь (260 символов).
    if len(s) > 220:
        # Пытаемся сохранить расширение (.pdf, .xlsx)
        if '.' in s:
            parts = s.rsplit('.', 1)
            name_part = parts[0]
            ext_part = parts[1]

            # Обрезаем имя, оставляя место для ".." и расширения
            s = name_part[:215] + ".." + "." + ext_part
        else:
            s = s[:220] + ".."

    return s.strip() if s else "unnamed"