import re
import logging

logger = logging.getLogger(__name__)

_FORBIDDEN_CHARS = re.compile(r'[:\\/*?"<>|]')
_MAX_EXT_LEN = 5  # ".docx", ".jpeg"; длиннее - считаем частью имени, а не расширением


def sanitize_filename(name: str, max_stem: int = 40) -> str:
    """
    Очищает строку для использования как имя файла или папки.

    Имя (без расширения) обрезается до max_stem символов, чтобы не упереться
    в лимит длины пути Windows. Расширение сохраняется целиком, с точкой.

    Обрезка может дать одинаковые имена для разных файлов - это нормально:
    коллизии разрешает downloads.plan_downloads (добавляет хеш URL).
    """
    if not name:
        return "unknown"

    s = _FORBIDDEN_CHARS.sub('', str(name))
    s = re.sub(r'\s+', ' ', s)
    if not s.strip():
        return "unnamed"

    stem, dot, ext = s.rpartition('.')
    if dot and stem and 0 < len(ext) <= _MAX_EXT_LEN and ' ' not in ext:
        ext = '.' + ext
    else:
        # Расширения нет (например, название лота или фирмы).
        # Хвост из последних 4 символов сохранён ради совместимости с папками,
        # уже созданными прежней версией функции: иначе докачка начнётся с нуля.
        stem, ext = s, (s[-4:] if len(s) > max_stem else '')

    if len(stem) > max_stem:
        stem = stem[:max_stem] + '..'
    return stem + ext
