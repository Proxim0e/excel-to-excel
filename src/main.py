import re
import concurrent.futures
import logging
import time
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import shutil
import threading
import requests

from src.utils.file_helpers import sanitize_filename
from src.utils.http_client import (smart_request, RateLimitStop, is_blocked, stop_reason,
                                   make_download_session)
from src.utils.text_helpers import normalize_spaces, prepare_lot_title_for_b1

from config import (
    RESOURCE_DIR, MAX_WORKERS, DEFAULT_TENDER_URL, ENABLE_DOWNLOADS, DOWNLOAD_DIR, ALLOWED_EXTENSIONS, HEADERS, ONLY_DOWNLOAD
)
from scrapers.lot_parser import TenderScraper
from excel.writer import ExcelManager
from excel.reader import get_parent_data, get_tender_url_from_parent

# Отключаем лишние логи от библиотек requests и urllib3
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("requests").setLevel(logging.WARNING)

# Настройка логирования (вместо простых print)
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(levelname)s] %(message)s',
    force=True
)
#logging.basicConfig(level=logging.DEBUG, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)


def main():
    # --- ЗАПУСК ОБЩЕГО ТАЙМЕРА СКРИПТА ---
    script_start_time = time.time()

    logger.info("=== Запуск процесса синхронизации лотов ===")

    # -----------------------------------------------------
    # 1. Инициализация и чтение родительского файла
    # -----------------------------------------------------
    parent_data = {}
    tender_url = DEFAULT_TENDER_URL

    if not ONLY_DOWNLOAD:
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
        logger.info("Подготовка структуры Excel...")
        created_count = 0
        updated_count = 0
        skipped_count = 0

        for lot_num, lot_info in parent_data.items():
            ws = excel_manager.get_or_create_sheet(lot_num)
            if not ws:
                skipped_count += 1
                continue

            is_new_lot = (excel_manager.max_existing < lot_num)
            if is_new_lot:
                created_count += 1
            else:
                updated_count += 1

            lot_title = lot_info.get('denumire')
            if lot_title:
                ws["B1"] = f"Lot nr. {lot_num} {lot_title}".strip()

            lot_specification = lot_info.get('specificatie')
            if lot_specification:
                current_b11 = ws["B11"].value
                if current_b11 and lot_specification not in str(current_b11):
                    ws["B11"] = f"{current_b11}\n{lot_specification}"
                elif not current_b11:
                    ws["B11"] = str(lot_specification)

            unit_of_measure = lot_info.get('unitate_masura')
            total_quantity = lot_info.get('cantitate_total')
            allocated_sum = lot_info.get('suma_alocata')

            if unit_of_measure:
                ws["C9"] = unit_of_measure
            if total_quantity:
                ws["A1"] = total_quantity
            if allocated_sum:
                ws["D8"] = allocated_sum

        logger.info(f"Excel структура готова. Создано: {created_count}, Обновлено: {updated_count}, Пропущено: {skipped_count}")
    else:
        logger.info("=== РЕЖИМ ONLY_DOWNLOAD: Работа с Excel полностью пропущена. ===")
        # В этом режиме используем дефолтный URL
        logger.info(f"Использую дефолтный Tender URL: {tender_url}")

    # -----------------------------------------------------
    # 4. Скрапинг ссылок и данных лотов
    # -----------------------------------------------------
    scraper = TenderScraper()

    logger.info("Сбор ссылок на лоты...")
    lot_links = scraper.extract_lot_links(tender_url)
    if not lot_links:
        logger.error(f"Ссылки на лоты не найдены. Причина остановки: {stop_reason() or 'не определена'}")
        return
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
                    # При блокировке не засоряем лог сотнями одинаковых строк
                    if not is_blocked():
                        logger.warning(f"Ошибка обработки {url}: {lot_data.error}")
                else:
                    raw_title = lot_data.title if lot_data.title else ""
                    display_title = normalize_spaces(prepare_lot_title_for_b1(raw_title))

                    logger.info(f"Обработан   ===   {display_title}   === ---> [{len(lot_data.participants)}] участн.")
            except Exception as e:
                logger.error(f"Критическая ошибка при обработке {url}: {e}")

    # -----------------------------------------------------
    # Проверка: не ограничил ли сайт доступ во время парсинга
    # -----------------------------------------------------
    if is_blocked():
        logger.error(f"Парсинг остановлен: {stop_reason()}. Подождите несколько часов и запустите снова.")
        return

    failed_lots = [r for r in results if r.error]
    if failed_lots:
        logger.warning(
            f"Лотов с ошибками: {len(failed_lots)} из {len(results)}. "
            f"Их документы не будут скачаны, повторите запуск позже.")

        # -----------------------------------------------------
        # 5. Запись участников в Excel (Пропускаем, если ONLY_DOWNLOAD)
        # -----------------------------------------------------
    if not ONLY_DOWNLOAD:
        logger.info("Запись участников в Excel...")
        processed_count = 0
        skipped_errors = 0
        skipped_no_number = 0
        failed_writes = 0
        restored_count = 0

        for lot_data in results:
            if not lot_data:
                skipped_errors += 1
                continue

            if lot_data.error:
                skipped_errors += 1
                continue

            if not lot_data.number and lot_data.title:
                scraped_clean = " ".join(lot_data.title.split()).strip().lower()

                for parent_num, parent_info in parent_data.items():
                    parent_title = parent_info.get('denumire', '')
                    if not parent_title:
                        continue

                    parent_clean = " ".join(parent_title.split()).strip().lower()

                    if parent_clean in scraped_clean or scraped_clean in parent_clean:
                        lot_data.number = parent_num
                        restored_count += 1
                        logger.info(f"✅ Номер лота восстановлен из родительского файла: {parent_num}")
                        break

            if not lot_data.number:
                title = lot_data.title or "Без названия"
                logger.warning(f"Лот пропущен: отсутствует номер (заголовок: '{title[:60]}...').")
                skipped_no_number += 1
                continue

            try:
                excel_manager.write_participants(lot_data)
                processed_count += 1
            except Exception as e:
                logger.error(f"КРИТИЧЕСКАЯ ОШИБКА записи лота {lot_data.number} в Excel: {e}")
                failed_writes += 1

        logger.info(
            f"Запись участников завершена. Успешно: {processed_count} | "
            f"Восстановлено: {restored_count} | "
            f"Пропущено: {skipped_errors + skipped_no_number} | "
            f"Ошибки записи: {failed_writes}"
        )
    # -----------------------------------------------------
    # 6. Сохранение файла (ДОЛЖНО БЫТЬ ПЕРЕД ЗАГРУЗКАМИ!)
    # -----------------------------------------------------
    if not ONLY_DOWNLOAD:
        timestamp = datetime.now().strftime("%d.%m.%Y_%H-%M-%S")
        output_filename = RESOURCE_DIR / f"result_{timestamp}.xlsx"

        try:
            excel_manager.save(str(output_filename))
            logger.info(f"=== ГОТОВО! Файл сохранен: {output_filename} ===")
        except Exception as e:
            logger.error(f"Ошибка при сохранении файла: {e}")
    else:
        logger.info("=== РЕЖИМ ONLY_DOWNLOAD: Пропуск сохранения Excel ===")
    # -----------------------------------------------------
    # 7. Скачивание всех документов (PDF) - Параллельно
    # -----------------------------------------------------
    if ENABLE_DOWNLOADS:  # <-- Проверяем конфиг
        # Определяем ID тендера...
        tender_id = "unknown"
        if results:
            import re
            m = re.search(r'/tender/(\d+)/', results[0].url)
            if m:
                tender_id = m.group(1)
    else:
        print("Загрузки отключены в конфиге. Программа завершает работу.")
        # Принудительный выход из скрипта
        exit()

        # Определяем ID тендера
    tender_id = "unknown"
    if results:
        import re
        m = re.search(r'/tender/(\d+)/', results[0].url)
        if m:
            tender_id = m.group(1)

    # Папка БЕЗ таймстампа: при повторном запуске уже скачанные файлы
    # находятся на месте и пропускаются (докачка работает).
    tender_folder = DOWNLOAD_DIR / tender_id

    logger.info(f"Подготовка к скачиванию документов в папку: {tender_folder}")

    # Создаем базовую папку тендера
    tender_folder.mkdir(parents=True, exist_ok=True)

    # --- Словарь для локального копирования повторяющихся файлов ---
    url_cache = {}
    cache_lock = threading.Lock()

    # Общая сессия для скачивания: соединения переиспользуются, нагрузка на сервер меньше
    download_session = make_download_session()

    # Определяем функцию-воркера для скачивания одного файла
    def download_worker(task_data):
        """
        Воркер для скачивания. Использует smart_request + локальный кэш.
        """
        url, file_path, lot_name, part_name, file_name = task_data
        from pathlib import Path
        file_path = Path(file_path)

        # 1. Проверяем, есть ли файл УЖЕ в нужной папке (докачка с прошлого запуска)
        if file_path.exists():
            logger.info(f"[SKIP] Уже на месте: {file_name}")
            return "skipped"

        # 2. Проверяем, скачивали ли мы этот URL ранее в ЭТОТ запуск скрипта?
        with cache_lock:
            cached_path = url_cache.get(url)

        # Если файл уже скачивался в другую папку, просто копируем его локально!
        if cached_path and Path(cached_path).exists():
            try:
                file_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(cached_path, file_path)
                logger.info(f"[COPIED] Скопирован из кэша: {file_name} (Лот: {lot_name})")
                return "copied"
            except Exception as e:
                logger.warning(f"Не удалось скопировать {file_name}: {e}. Будем качать заново.")

        # Если сайт уже ограничил доступ - в сеть не ходим
        if is_blocked():
            return "blocked"

        # 3. Если файла нигде нет, скачиваем его с сервера
        try:
            logger.info(f"[START] Скачивание: {file_name} (Лот: {lot_name})")
            response = smart_request(
                download_session.get,
                url,
                timeout=60,
                action_name=f"Download {file_name}"
            )

            file_path.parent.mkdir(parents=True, exist_ok=True)
            # Пишем во временный файл и переименовываем: оборванная запись
            # не будет принята за готовый файл при следующем запуске.
            tmp_path = file_path.with_name(file_path.name + ".part")
            with open(tmp_path, 'wb') as f:
                f.write(response.content)
            tmp_path.replace(file_path)

            # 4. СОХРАНЯЕМ ПУТЬ В КЭШ, чтобы для других лотов скопировать его
            with cache_lock:
                url_cache[url] = str(file_path)

            file_size_kb = len(response.content) // 1024
            logger.info(
                f"[DOWNLOADED] OK ({file_size_kb} KB) === [{lot_name}] === Part: [{part_name}] === Doc: [{file_name}]")
            return "success"

        except RateLimitStop:
            return "blocked"
        except requests.exceptions.HTTPError as e:
            logger.error(f"[FAIL-HTTP] {file_name}: {e}")
            return "error"
        except OSError as e:
            logger.critical(f"[FAIL-DISK] Ошибка записи файла {file_name} (Диск/Путь): {e}")
            return "error"
        except Exception as e:
            logger.critical(f"[FAIL-UNKNOWN] {file_name}: {type(e).__name__} - {e}")
            return "error"
    # -----------------------------------------------------
    # Сбор списка задач (планирование путей)
    # -----------------------------------------------------
    tasks = []

    for lot_data in results:
        if not lot_data.title or not lot_data.participants:
            continue

        # 1. Создаем папку ЛОТА
        # sanitize_filename уже убирает двоеточия и слэши, но добавим .strip() для надежности
        lot_folder_name = sanitize_filename(lot_data.title).strip()
        # Используем оператор / (pathlib)
        lot_folder = tender_folder / lot_folder_name

        # Если имя пустое после очистки
        if not lot_folder_name:
            lot_folder_name = f"Lot_{lot_data.number}"

        if not lot_folder.exists():
            try:
                lot_folder.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                logger.error(f"Не удалось создать папку лота {lot_folder_name}: {e}")
                continue

        # 2. Цикл по УЧАСТНИКАМ
        for part in lot_data.participants:
            if not part.docs:
                continue

            part_folder_name = sanitize_filename(part.name).strip()
            # Если имя пустое
            if not part_folder_name:
                part_folder_name = "Participant"

            # Оператор /
            part_folder = lot_folder / part_folder_name

            if not part_folder.exists():
                try:
                    # parents=True обязательно!
                    part_folder.mkdir(parents=True, exist_ok=True)
                except Exception as e:
                    logger.error(f"Не удалось создать папку участника {part_folder_name}: {e}")
                    continue

            # 3. Цикл по ФАЙЛАМ (самый глубокий)
            for filename, url in part.docs:

                # !!! ТУТ ДОЛЖНА БЫТЬ ПРОВЕРКА РАСШИРЕНИЯ !!!
                # Проверяем расширение (если список не пустой в конфиге)
                if ALLOWED_EXTENSIONS:
                    # Приводим к нижнему регистру и проверяем
                    is_allowed = any(filename.lower().endswith(ext) for ext in ALLOWED_EXTENSIONS)
                    if not is_allowed:
                        logger.debug(f"   [SKIP] Тип файла не разрешен: {filename}")
                        continue  # Переходим к следующему файлу

                clean_filename = sanitize_filename(filename).strip()
                if not clean_filename:
                    continue
                # Оператор /
                save_path = part_folder / clean_filename

                # Добавляем задачу в очередь
                # Передаем lot_name и part_name только для логов
                tasks.append((url, save_path, lot_data.title, part.name, filename))

    # Для пробного запуска на небольшом числе файлов раскомментируйте следующую строку:
    #tasks = tasks[:20]

    # Считаем уникальные ссылки, чтобы понимать, сколько файлов реально качать из сети
    unique_urls = len(set(task[0] for task in tasks))

    logger.info(
        f"Найдено файлов для скачивания: {len(tasks)} (из них {unique_urls} уникальных ссылок). Запуск потоков (Workers: {MAX_WORKERS})...")
    # Запускаем таймер и сохраняем общее количество файлов
    start_download_time = time.time()
    total_tasks = len(tasks)
    # Запуск параллельной скачки
    success_count = 0
    copied_count = 0
    fail_count = 0
    skipped_count = 0
    blocked_count = 0
    processed_count = 0  # <--- ДОБАВИЛИ СЧЕТЧИК

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_task = {executor.submit(download_worker, task): task for task in tasks}

        # Обрабатываем результаты по мере готовности
        for future in as_completed(future_to_task):
            result = future.result()
            processed_count += 1  # <--- УВЕЛИЧИВАЕМ
            if result == "success":
                success_count += 1
            elif result == "copied":
                copied_count += 1
            elif result == "skipped":  # <--- ДОБАВИЛИ
                skipped_count += 1  # <--- Файл уже был на диске, пропустили
            elif result == "blocked":
                blocked_count += 1  # <--- Сайт ограничил доступ, файл не обрабатывался
            else:
                fail_count += 1
                # <--- ЛОГ ПРОГРЕССА КАЖДЫЕ 10 ФАЙЛОВ --->
            if processed_count % 10 == 0 or processed_count == total_tasks:
                elapsed_time = time.time() - start_download_time

                # Вычисляем проценты
                percent = (processed_count / total_tasks) * 100

                # Вычисляем оставшееся время (ETA)
                speed = 0.0
                if elapsed_time > 0:
                    speed = processed_count / elapsed_time  # Файлов в секунду
                    remaining_files = total_tasks - processed_count
                    eta_seconds = remaining_files / speed
                    eta_formatted = str(timedelta(seconds=int(eta_seconds)))
                else:
                    eta_formatted = "расчет..."

                # --- НОВЫЕ ТАЙМЕРЫ ---
                # Время с момента старта всего скрипта
                total_script_time = time.time() - script_start_time
                total_script_fmt = str(timedelta(seconds=int(total_script_time)))

                # Время с момента старта скачивания (можно оставить elapsed_time)
                download_time_fmt = str(timedelta(seconds=int(elapsed_time)))

                logger.info(
                    f"--- ПРОГРЕСС: {processed_count}/{total_tasks} ({percent:.1f}%) | "
                    f"Осталось: ~{eta_formatted} | "
                    f"Скорость: ~{speed:.2f} файл/сек | "
                    f"Время скачки: {download_time_fmt} | "
                    f"Общее время: {total_script_fmt} | "
                    f"Успешно: {success_count} | Ошибок: {fail_count} | Не тронуто из-за остановки: {blocked_count} ---"
                )
    # --- ПРОВЕРКА ФАКТИЧЕСКИХ ФАЙЛОВ НА ДИСКЕ ---
    saved_on_disk = 0
    try:
        saved_on_disk = sum(1 for x in tender_folder.rglob('*') if x.is_file())
    except Exception as e:
        logger.warning(f"Не удалось посчитать файлы на диске: {e}")

    logger.info(f"=== ИТОГОВАЯ ПРОВЕРКА ===")
    logger.info(f"Из {total_tasks} задач обработано: {success_count + copied_count + skipped_count}")
    logger.info(
        f"Детализация: Скачано из сети: {success_count} | Скопировано локально: {copied_count} | Пропущено (уже было): {skipped_count} | Ошибок: {fail_count}")
    logger.info(f"Физически на диске сохранено {saved_on_disk} файлов (Скачано + Скопировано).")
    if blocked_count:
        logger.error(
            f"Скачивание остановлено: {blocked_count} из {total_tasks} файлов НЕ обрабатывались "
            f"(причина: {stop_reason() or 'не указана'}). Это не «обработано», а пропущено."
        )

    if saved_on_disk < (success_count + copied_count):
        logger.warning(
            f"Внимание! Часть файлов ({(success_count + copied_count) - saved_on_disk} шт.) была перезаписана дублями.")
    else:
        logger.info("✅ Все уникальные файлы успешно сохранены на диск!")

    logger.info(
        f"Скачивание завершено. Скачано: {success_count} | Скопировано: {copied_count} | Пропущено: {skipped_count} | Ошибок: {fail_count}")

    if is_blocked():
        logger.error(
            f"ОСТАНОВЛЕНО: {stop_reason()}. Не обработано: {blocked_count}. "
            f"Запустите позже, скачанное пропустится.")


if __name__ == "__main__":
    main()