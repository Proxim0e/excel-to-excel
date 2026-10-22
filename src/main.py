"""
Точка входа. Запуск из корня репозитория:  python -m src.main
(в PyCharm работает и прямой запуск src/main.py: корень проекта уже в PYTHONPATH).

Коды выхода: 0 - всё успешно; 1 - фатальная ошибка; 2 - результат неполный
(сайт ограничил доступ, часть лотов не спарсилась или не записалась).
"""
import concurrent.futures
import logging
import time
from datetime import datetime

from src.config import DEFAULT_TENDER_URL, ENABLE_DOWNLOADS, MAX_WORKERS, ONLY_DOWNLOAD, RESOURCE_DIR
from src.downloads import run_downloads
from src.excel.reader import get_parent_data, get_tender_url_from_parent
from src.excel.writer import ExcelManager
from src.lot_matching import match_lot_by_title
from src.models import LotData
from src.scrapers.lot_parser import TenderScraper
from src.utils.http_client import is_blocked, stop_reason
from src.utils.text_helpers import normalize_spaces, prepare_lot_title_for_b1

logger = logging.getLogger(__name__)

EXIT_OK, EXIT_FATAL, EXIT_PARTIAL = 0, 1, 2


def _configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format='[%(asctime)s] [%(levelname)s] %(message)s', force=True)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Этапы
# ---------------------------------------------------------------------------
def _prepare_excel(parent_data: dict) -> ExcelManager:
    excel_manager = ExcelManager()
    created = updated = skipped = 0

    for lot_num, lot_info in parent_data.items():
        ws = excel_manager.get_or_create_sheet(lot_num)
        if not ws:
            skipped += 1
            continue
        if excel_manager.max_existing < lot_num:
            created += 1
        else:
            updated += 1

        if lot_title := lot_info.get('denumire'):
            ws["B1"] = f"Lot nr. {lot_num} {lot_title}".strip()

        if lot_specification := lot_info.get('specificatie'):
            current_b11 = ws["B11"].value
            if current_b11 and lot_specification not in str(current_b11):
                ws["B11"] = f"{current_b11}\n{lot_specification}"
            elif not current_b11:
                ws["B11"] = str(lot_specification)

        if unit_of_measure := lot_info.get('unitate_masura'):
            ws["C9"] = unit_of_measure
        if total_quantity := lot_info.get('cantitate_total'):
            ws["A1"] = total_quantity
        if allocated_sum := lot_info.get('suma_alocata'):
            ws["D8"] = allocated_sum

    logger.info(f"Excel структура готова. Создано: {created}, Обновлено: {updated}, Пропущено: {skipped}")
    return excel_manager


def _scrape(tender_url: str) -> list[LotData]:
    scraper = TenderScraper()
    logger.info("Сбор ссылок на лоты...")
    lot_links = scraper.extract_lot_links(tender_url)
    if not lot_links:
        logger.error(f"Ссылки на лоты не найдены. Причина: {stop_reason() or 'не определена'}")
        return []
    logger.info(f"Найдено ссылок: {len(lot_links)}. Начинаю параллельный парсинг...")

    results: list[LotData] = []
    workers = min(MAX_WORKERS, max(2, len(lot_links)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_url = {executor.submit(scraper.parse_lot_page, url): url for url in lot_links}
        for future in concurrent.futures.as_completed(future_to_url):
            url = future_to_url[future]
            try:
                lot_data = future.result()
            except Exception:
                logger.exception(f"Критическая ошибка при обработке {url}")
                continue
            results.append(lot_data)
            if lot_data.error:
                if not is_blocked():  # при блокировке не засоряем лог одинаковыми строками
                    logger.warning(f"Ошибка обработки {url}: {lot_data.error}")
            else:
                display_title = normalize_spaces(prepare_lot_title_for_b1(lot_data.title or ""))
                logger.info(f"Обработан   ===   {display_title}   === ---> [{len(lot_data.participants)}] участн.")
    return results


def _write_results(excel_manager: ExcelManager, results: list[LotData], parent_data: dict) -> bool:
    """Пишет участников в Excel. Возвращает True, если записано всё, что ожидалось."""
    logger.info("Запись участников в Excel...")
    written: set[int] = set()
    restored = skipped_errors = skipped_no_number = failed_writes = 0

    for lot_data in results:
        if lot_data.error:
            skipped_errors += 1
            continue

        if not lot_data.number:
            if (number := match_lot_by_title(lot_data.title, parent_data)) is not None:
                lot_data.number = number
                restored += 1
                logger.info(f"Номер лота восстановлен из родительского файла: {number}")
            else:
                logger.warning(f"Лот пропущен: нет номера (заголовок: '{(lot_data.title or '')[:60]}...').")
                skipped_no_number += 1
                continue

        try:
            excel_manager.write_participants(lot_data)
            written.add(lot_data.number)
        except Exception:
            logger.exception(f"КРИТИЧЕСКАЯ ОШИБКА записи лота {lot_data.number} в Excel")
            failed_writes += 1

    # Лоты из родительского файла, по которым данных нет, помечаем явно,
    # чтобы они не выглядели как "участников не было".
    not_scraped = sorted(parent_data.keys() - written)
    for number in not_scraped:
        excel_manager.mark_not_scraped(number)

    logger.info(
        f"Запись завершена. Успешно: {len(written)} | Восстановлено: {restored} | "
        f"Ошибки парсинга: {skipped_errors} | Без номера: {skipped_no_number} | "
        f"Ошибки записи: {failed_writes} | Помечено как не спарсенные: {len(not_scraped)}"
    )
    return not (skipped_errors or skipped_no_number or failed_writes or not_scraped)


def _save(excel_manager: ExcelManager, complete: bool) -> bool:
    suffix = "" if complete else "_PARTIAL"
    output = RESOURCE_DIR / f"result_{datetime.now():%d.%m.%Y_%H-%M-%S}{suffix}.xlsx"
    try:
        excel_manager.save(output)
    except OSError:
        logger.exception(f"Ошибка при сохранении файла {output} (открыт в Excel?)")
        return False
    logger.info(f"=== ГОТОВО! Файл сохранен: {output} ===")
    return True


# ---------------------------------------------------------------------------
# Оркестрация
# ---------------------------------------------------------------------------
def main() -> int:
    script_start_time = time.time()
    logger.info("=== Запуск процесса синхронизации лотов ===")

    parent_data: dict = {}
    excel_manager = None
    tender_url = DEFAULT_TENDER_URL

    if ONLY_DOWNLOAD:
        logger.info(f"=== РЕЖИМ ONLY_DOWNLOAD: Excel пропущен. Tender URL: {tender_url} ===")
    else:
        try:
            parent_data = get_parent_data()
        except Exception:  # нет файла, битый xlsx, нет колонки 'Nr. Lot' - дальше идти нельзя
            logger.exception("Ошибка при чтении родительского файла")
            return EXIT_FATAL
        logger.info(f"Загружено {len(parent_data)} лотов из родительского файла.")

        tender_url = get_tender_url_from_parent() or DEFAULT_TENDER_URL
        logger.info(f"Tender URL: {tender_url}")
        excel_manager = _prepare_excel(parent_data)

    results = _scrape(tender_url)
    blocked = is_blocked()
    if blocked:
        logger.error(f"Парсинг остановлен: {stop_reason()}. Сохраняю то, что успели собрать.")
    if not results and not blocked:
        return EXIT_FATAL

    complete = not blocked
    if excel_manager is not None:
        complete &= _write_results(excel_manager, results, parent_data)
        if not _save(excel_manager, complete):
            return EXIT_FATAL

    if blocked:
        logger.error("Скачивание документов пропущено: сайт ограничил доступ. Запустите позже.")
        return EXIT_PARTIAL

    if ENABLE_DOWNLOADS:
        stats = run_downloads(results, script_start_time)
        complete &= not (stats["error"] or stats["blocked"])
    else:
        logger.info("Загрузки отключены в конфиге.")

    return EXIT_OK if complete else EXIT_PARTIAL


if __name__ == "__main__":
    _configure_logging()
    raise SystemExit(main())
