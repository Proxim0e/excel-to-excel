import concurrent.futures
import logging
from src.utils.pdf.pdf_processor import process_all_participants
from loguru import logger as pdf_logger  # отдельный логгер, если хочешь
from src.utils.text_helpers import normalize_spaces, prepare_lot_title_for_b1
from src.utils.file_helpers import sanitize_filename
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from src.utils.http_client import smart_request
import requests

from .config import (
    RESOURCE_DIR, TEMPLATE_FILE,
    MAX_WORKERS, DEFAULT_TENDER_URL, APPEND_TO_EXISTING, ENABLE_DOWNLOADS, DOWNLOAD_DIR, ALLOWED_EXTENSIONS, HEADERS,
    USE_LATEST_DOWNLOAD_FOLDER, ENABLE_PDF_PROCESSING
)
from .scrapers.lot_parser import TenderScraper
from .excel.writer import ExcelManager
from .excel.reader import get_parent_data, get_tender_url_from_parent

# Отключаем лишние логи от библиотек requests и urllib3
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("requests").setLevel(logging.WARNING)

# Настройка логирования (вместо простых print)
logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s', force=True)
#logging.basicConfig(level=logging.DEBUG, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)


def main():
    logger.info("=== Запуск процесса синхронизации лотов ===")

    # -----------------------------------------------------
    # 1. Инициализация и чтение родительского файла
    # -----------------------------------------------------
    try:
        parent_data = get_parent_data()
        logger.info(f"Загружено {len(parent_data)} лотов из родительского файла.")
    except FileNotFoundError as e:
        logger.error(e)
        return
    except Exception as e:
        logger.error(f"Ошибка при чтении родительского файла: {e}")
        return

    # -----------------------------------------------------
    # 2. Получение URL тендера
    # -----------------------------------------------------
    tender_url = get_tender_url_from_parent()
    if not tender_url:
        tender_url = DEFAULT_TENDER_URL
        logger.warning(f"URL тендера не найден в родительском файле. Использую дефолтный: {tender_url}")
    else:
        logger.info(f"Найден Tender URL: {tender_url}")

    # -----------------------------------------------------
    # 3. Подготовка Excel Manager
    # -----------------------------------------------------
    excel_manager = ExcelManager()

    # Фаза А: Создание листов на основе родительского файла
    # Мы проходим по данным из родительского файла, чтобы создать листы (или убедиться, что они есть)
    # и заполнить их базовой мета-информацией.

    logger.info("Подготовка структуры Excel...")
    created_count = 0
    updated_count = 0
    skipped_count = 0

    for lot_num, lot_info in parent_data.items():
        # Пытаемся получить или создать лист
        ws = excel_manager.get_or_create_sheet(lot_num)

        # Если метод вернул None -> мы в режиме APPEND_TO_EXISTING=False и лист старый -> пропускаем
        if not ws:
            logger.debug(f"Лот {lot_num}: Пропущен (лист уже существовал, APPEND_TO_EXISTING=False)")
            skipped_count += 1
            continue

        # Определяем статус обработки
        is_new_lot = (excel_manager.max_existing < lot_num)
        if is_new_lot:
            created_count += 1
            logger.info(f" Создан лист {lot_num}.")
        else:
            updated_count += 1
            logger.info(f"Лот {lot_num}: ИСПОЛЬЗУЕТСЯ существующий лист.")

        # --- Заполнение данных ---

        lot_title = lot_info.get('denumire')
        if lot_title:
            ws["B1"] = f"Lot nr. {lot_num} {lot_title}".strip()
            logger.debug(f"   -> Заголовок (B1): обновлен")
        else:
            logger.debug(f"   -> Заголовок (B1): данных нет")

        lot_specification = lot_info.get('specificatie')
        if lot_specification:
            current_b11 = ws["B11"].value
            if current_b11 and lot_specification not in str(current_b11):
                ws["B11"] = f"{current_b11}\n{lot_specification}"
                logger.debug(f"   -> Спецификация (B11): добавлена")
            elif not current_b11:
                ws["B11"] = str(lot_specification)
                logger.debug(f"   -> Спецификация (B11): записана")
            else:
                logger.debug(f"   -> Спецификация (B11): уже существует")
        else:
            logger.debug(f"   -> Спецификация (B11): данных нет")

        # Остальные поля
        unit_of_measure = lot_info.get('unitate_masura')
        total_quantity = lot_info.get('cantitate_total')
        allocated_sum = lot_info.get('suma_alocata')

        if unit_of_measure:
            ws["C9"] = unit_of_measure
            logger.debug(f"   -> Ед. изм (C9): {unit_of_measure}")

        if total_quantity:
            ws["A1"] = total_quantity
            logger.debug(f"   -> Кол-во (A1): {total_quantity}")

        if allocated_sum:
            ws["D8"] = allocated_sum
            logger.debug(f"   -> Сумма (D8): {allocated_sum}")

    logger.info(
        f"Excel структура готова. Создано: {created_count}, Обновлено: {updated_count}, Пропущено: {skipped_count}")

    logger.info("Excel структура готова.")

    # -----------------------------------------------------
    # 4. Скрапинг ссылок и данных лотов
    # -----------------------------------------------------
    scraper = TenderScraper()

    logger.info("Сбор ссылок на лоты...")
    lot_links = scraper.extract_lot_links(tender_url)
    logger.info(f"Найдено ссылок: {len(lot_links)}. Начинаю параллельный парсинг...")

    results = []
    # Используем ThreadPoolExecutor для параллелизма
    workers = min(MAX_WORKERS, max(2, len(lot_links)))

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_url = {executor.submit(scraper.parse_lot_page, url): url for url in lot_links}

        for future in concurrent.futures.as_completed(future_to_url):
            url = future_to_url[future]
            try:
                lot_data = future.result()
                results.append(lot_data)
                if lot_data.error:
                    logger.warning(f"Ошибка обработки {url}: {lot_data.error}")
                else:
                    raw_title = lot_data.title if lot_data.title else ""
                    display_title = normalize_spaces(prepare_lot_title_for_b1(raw_title))

                    logger.info(f"Обработан   ===   {display_title}   === ---> [{len(lot_data.participants)}] участн.")
            except Exception as e:
                logger.error(f"Критическая ошибка при обработке {url}: {e}")

    # -----------------------------------------------------
    # 5. Запись участников в Excel
    # -----------------------------------------------------
    logger.info("Запись участников в Excel...")
    processed_count = 0
    for lot_data in results:
        if lot_data.number:
            logger.info(f"Обработан лот {lot_data.number}: {len(lot_data.participants)} участн.")
            excel_manager.write_participants(lot_data)
            processed_count += 1

    # -----------------------------------------------------
    # 6. Сохранение файла
    # -----------------------------------------------------
    timestamp = datetime.now().strftime("%d.%m.%Y_%H-%M-%S")
    output_filename = RESOURCE_DIR / f"result_{timestamp}.xlsx"

    try:
        excel_manager.save(str(output_filename))
        logger.info(f"=== ГОТОВО! Файл сохранен: {output_filename} ===")
    except Exception as e:
        logger.error(f"Ошибка при сохранении файла: {e}")
    # -----------------------------------------------------
    # 7. Скачивание всех документов (PDF) - Параллельно
    # -----------------------------------------------------
    tender_folder = None

    if ENABLE_DOWNLOADS:
        logger.info("=== Запуск скачивания документов ===")

        # Определяем ID тендера
        tender_id = "unknown"
        if results:
            import re
            m = re.search(r'/tender/(\d+)/', results[0].url)
            if m:
                tender_id = m.group(1)

        # Добавляем таймстамп
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        tender_folder_name = f"{tender_id}_{timestamp}"
        tender_folder = DOWNLOAD_DIR / tender_folder_name
        logger.info(f"Папка для скачивания: {tender_folder}")

        # Создаём папку тендера
        tender_folder.mkdir(parents=True, exist_ok=True)

        # Определяем функцию-воркера для скачивания одного файла
        def download_worker(task_data):
            """Воркер для скачивания. Использует smart_request + requests.get (стабильно)."""
            url, file_path, lot_name, part_name, file_name = task_data
            from pathlib import Path
            import traceback

            file_path = Path(file_path)
            if file_path.exists():
                logger.debug(f"[SKIP] File exists: {file_name}")
                return "skipped"

            try:
                # ВЫЗОВ УМНОГО ЗАПРОСА
                response = smart_request(
                    requests.get,
                    url,
                    headers=HEADERS,
                    timeout=60,
                    action_name=f"Download {file_name}"
                )
                # Записываем файл
                with open(file_path, 'wb') as f:
                    f.write(response.content)
                # Лог успеха
                logger.info(f"[DOWNLOADED] OK === [{lot_name}] === Part: [{part_name}] === Doc: [{file_name}]")
                return "success"
            except (requests.exceptions.HTTPError, OSError) as e:
                logger.error(f"[FAIL] {file_name}: {e}")
                return "error"

        # Сбор списка задач (планирование путей)
        tasks = []
        for lot_data in results:
            if not lot_data.title or not lot_data.participants:
                continue

            # 1. Папка лота
            lot_folder_name = sanitize_filename(lot_data.title).strip()
            if not lot_folder_name:
                lot_folder_name = f"Lot_{lot_data.number}"
            lot_folder = tender_folder / lot_folder_name
            if not lot_folder.exists():
                try:
                    lot_folder.mkdir(parents=True, exist_ok=True)
                except Exception as e:
                    logger.error(f"Не удалось создать папку лота {lot_folder_name}: {e}")
                    continue

            # 2. Цикл по участникам
            for part in lot_data.participants:
                if not part.docs:
                    continue

                part_folder_name = sanitize_filename(part.name).strip()
                if not part_folder_name:
                    part_folder_name = "Participant"
                part_folder = lot_folder / part_folder_name
                if not part_folder.exists():
                    try:
                        part_folder.mkdir(parents=True, exist_ok=True)
                    except Exception as e:
                        logger.error(f"Не удалось создать папку участника {part_folder_name}: {e}")
                        continue

                # 3. Цикл по файлам
                for filename, url in part.docs:
                    # Проверка расширения
                    if ALLOWED_EXTENSIONS:
                        is_allowed = any(filename.lower().endswith(ext) for ext in ALLOWED_EXTENSIONS)
                        if not is_allowed:
                            logger.debug(f"[SKIP] Тип файла не разрешен: {filename}")
                            continue

                    clean_filename = sanitize_filename(filename).strip()
                    if not clean_filename:
                        continue

                    save_path = part_folder / clean_filename
                    tasks.append((url, save_path, lot_data.title, part.name, filename))

        # Если есть что скачивать
        if tasks:
            logger.info(f"Найдено файлов для скачивания: {len(tasks)}. Запуск потоков (Workers: {MAX_WORKERS})...")

            success_count = 0
            skip_count = 0
            fail_count = 0

            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                future_to_task = {executor.submit(download_worker, task): task for task in tasks}
                for future in as_completed(future_to_task):
                    result = future.result()
                    if result == "success":
                        success_count += 1
                    elif result == "skipped":
                        skip_count += 1
                    else:
                        fail_count += 1

            logger.info(
                f"Скачивание завершено. Успешно: {success_count}, Пропущено: {skip_count}, Ошибок: {fail_count}")
        else:
            logger.info("Нет файлов для скачивания в этом тендере")
    else:
        logger.info("Скачивание отключено в конфиге — пропускаем загрузку новых файлов")
    # -----------------------------------------------------
    # 8. Обработка PDF (независимо от скачивания)
    # -----------------------------------------------------
    if ENABLE_PDF_PROCESSING:
        logger.info("=== Запуск обработки технических спецификаций из PDF ===")

        # Определяем папку для обработки
        pdf_folder = None
        if ENABLE_DOWNLOADS and tender_folder is not None and tender_folder.exists():
            pdf_folder = tender_folder
            logger.info(f"Используем свежескачанную папку: {pdf_folder}")
        elif USE_LATEST_DOWNLOAD_FOLDER:
            try:
                # Находим самую свежую папку в DOWNLOAD_DIR (по времени создания)
                pdf_folder = max(
                    (p for p in DOWNLOAD_DIR.iterdir() if p.is_dir()),
                    key=lambda p: p.stat().st_ctime,
                    default=None
                )
                if pdf_folder:
                    logger.info(f"Используем самую свежую существующую папку: {pdf_folder}")
                else:
                    logger.warning("Нет ни одной папки в downloads — пропускаем PDF-обработку")
            except Exception as e:
                logger.error(f"Ошибка поиска последней папки: {e}")
        else:
            logger.info("USE_LATEST_DOWNLOAD_FOLDER выключено — пропускаем PDF без новой загрузки")

        if pdf_folder and pdf_folder.exists():
            try:
                from src.utils.pdf.pdf_processor import process_all_participants

                pdf_results = process_all_participants(pdf_folder)

                if pdf_results:
                    logger.info(f"Обработано участников из PDF: {len(pdf_results)}")

                    updated_count = 0
                    for res in pdf_results:
                        if res.error:
                            logger.warning(f"PDF ошибка {res.participant_name} (лот {res.lot_name}): {res.error}")
                            continue

                        try:
                            lot_nr = int(res.lot_name) if res.lot_name.isdigit() else int(res.lot_name.split()[-1])
                            excel_manager.write_pdf_data_to_participant(
                                lot_number=lot_nr,
                                participant_name=res.participant_name,
                                model=res.model,
                                country=res.country,
                                manufacturer=res.manufacturer,
                                specification_text=res.specification_text
                            )
                            updated_count += 1
                        except Exception as e:
                            logger.error(f"Ошибка записи PDF-данных для {res.participant_name}: {e}")

                    logger.info(f"Успешно обновлено из PDF: {updated_count} участников")
                else:
                    logger.info("PDF-результаты пустые — ничего не обновлено")

            except ImportError as ie:
                logger.error(f"Ошибка импорта PDF-модулей: {ie}")
            except Exception as e:
                logger.error(f"Критическая ошибка обработки PDF: {e}")
        else:
            logger.info("Папка с PDF-файлами не найдена — пропускаем обработку")
    else:
        logger.info("Обработка PDF отключена в конфиге (ENABLE_PDF_PROCESSING=False)")
if __name__ == "__main__":
    main()
