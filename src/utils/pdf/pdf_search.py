from rapidfuzz import fuzz
import pdfplumber
from pathlib import Path
from src.config import PDF_KEYWORDS_FILENAME, PDF_KEYWORDS_CONTENT, PDF_FUZZY_THRESHOLD, PDF_MAX_PAGES_FOR_CONTENT_CHECK
from loguru import logger


def find_spec_pdf(participant_dir: Path) -> Path | None:
    candidates = []

    for file_path in participant_dir.iterdir():
        if not file_path.is_file() or not file_path.name.lower().endswith('.pdf'):
            continue

        filename_lower = file_path.name.lower()

        # 1. Вычисляем максимальный балл за имя
        name_score = max(fuzz.partial_ratio(filename_lower, kw.lower()) for kw in PDF_KEYWORDS_FILENAME)

        # 2. (Опционально) Умножаем на вес, если хотим отдать приоритет определенным словам
        # Например, если в имени есть "anexa", повышаем приоритет

        # 3. Содержимое (только если имя подозрительное или нужно уточнение)
        # Можно запускать только если name_score слишком низкий, например < 80
        content_score = 0
        try:
            with pdfplumber.open(file_path) as pdf:
                text = ""
                # Читаем меньше страниц для скорости, если проверка нужна
                for page in pdf.pages[:2]:
                    text += (page.extract_text() or "") + " "
                content_score = sum(kw.lower() in text.lower() for kw in PDF_KEYWORDS_CONTENT)
        except Exception as e:
            # logger.warning(...) - уберите логирование чтобы не спамить
            continue

        # Общий балл: 60% имя + 40% содержание (можно настроить веса)
        total_score = (name_score * 0.6) + (content_score * 20 if content_score > 0 else 0)

        candidates.append((total_score, file_path))

    # ВАЖНО: Не выходим сразу, а смотрим на всех кандидатов
    if candidates:
        # Сортируем по убыванию очков и берем лучший файл
        candidates.sort(key=lambda x: x[0], reverse=True)

        # Если лучший кандидат имеет очень низкий балл, возвращаем None
        best_score, best_file = candidates[0]

        # Порог качества, если < 50, считаем что файла нет
        if best_score > PDF_FUZZY_THRESHOLD:
            logger.debug(f"Лучший кандидат найден (score {best_score}): {best_file.name}")
            return best_file
        else:
            return None

    return None