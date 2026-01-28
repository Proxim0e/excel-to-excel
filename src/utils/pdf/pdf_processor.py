from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from loguru import logger
from src.utils.pdf.pdf_search import find_spec_pdf
from src.utils.pdf.pdf_parser import parse_spec_pdf
from src.models import ParticipantProcessingResult
from src.config import PDF_MAX_WORKERS_FOR_CONTENT_CHECK

cache = {}  # str(participant_dir) -> result (для дублирующихся участников)

def process_single_participant(lot_name: str, participant_name: str, participant_dir: Path) -> ParticipantProcessingResult:
    dir_key = str(participant_dir.resolve())
    if dir_key in cache:
        logger.debug(f"Кэш хит: {participant_name} в лоте {lot_name}")
        cached = cache[dir_key]
        # Важно: lot_name и participant_name передаются явно, чтобы в объекте результата всегда были актуальные данные
        return ParticipantProcessingResult(lot_name=lot_name, participant_name=participant_name, **cached)

    spec_pdf = find_spec_pdf(participant_dir)
    if not spec_pdf:
        return ParticipantProcessingResult(
            lot_name=lot_name,
            participant_name=participant_name,
            error="Техническая спецификация не найдена"
        )

    # --- ИСПРАВЛЕНО: Передаем lot_name и participant_name ---
    parsed = parse_spec_pdf(
        spec_pdf,
        lot_number=lot_name,        # Теперь парсер будет фильтровать строки по этому номеру
        participant_name=participant_name  # Теперь логи будут информативными
    )
    # ---------------------------------------------

    result_dict = {
        "model": parsed.get("model"),
        "country": parsed.get("country"),
        "manufacturer": parsed.get("manufacturer"),
        "specification_text": parsed.get("specification_text"),
        "spec_file_path": str(spec_pdf),
        "error": parsed.get("error")
    }
    cache[dir_key] = result_dict  # кэшируем только извлечённые данные
    return ParticipantProcessingResult(lot_name=lot_name, participant_name=participant_name, **result_dict)

def process_all_participants(tender_folder: Path) -> list[ParticipantProcessingResult]:
    tasks = []
    for lot_dir in tender_folder.iterdir():
        if not lot_dir.is_dir():
            continue
        for part_dir in lot_dir.iterdir():
            if part_dir.is_dir():
                tasks.append((lot_dir.name, part_dir.name, part_dir))

    results = []
    with ProcessPoolExecutor(max_workers=PDF_MAX_WORKERS_FOR_CONTENT_CHECK) as executor:
        future_to_task = {
            executor.submit(process_single_participant, lot_name, part_name, part_dir): (lot_name, part_name)
            for lot_name, part_name, part_dir in tasks
        }
        for future in as_completed(future_to_task):
            results.append(future.result())

    logger.info(f"Обработка PDF завершена. Обработано участников: {len(results)}")
    return results