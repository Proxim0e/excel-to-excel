import re
import logging

logger = logging.getLogger(__name__)


def sanitize_filename(name: str) -> str:
    """
    Очищает строку, чтобы использовать её как имя файла или папки.
    Заменяет недопустимые символы на пробел.
    """
    if not name:
        return "unknown"

    # Удаляем/заменяем символы, недопустимые в Windows и Unix
    # : \ / * ? " < > |
    s = re.sub(r'[:\\/*?"<>|]', '', str(name))

    # Заменяем несколько пробелов на один
    s = re.sub(r'\s+', ' ', s).strip()

    # Ограничиваем длину (опционально), чтобы не было проблем с путями
    if len(s) > 100:
        s = s[:100]

    return s if s else "unnamed"