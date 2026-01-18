import concurrent.futures
from datetime import datetime
import logging
from urllib3 import disable_warnings
from urllib3.exceptions import InsecureRequestWarning # Иногда бывает полезно
from src.utils.text_helpers import normalize_spaces, prepare_lot_title_for_b1

# Отключаем лишние логи от библиотек requests и urllib3
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("requests").setLevel(logging.WARNING)

# Настройка логирования (вместо простых print)
logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')
#logging.basicConfig(level=logging.DEBUG,format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

from config import (
    RESOURCE_DIR, TEMPLATE_FILE,
    MAX_WORKERS, DEFAULT_TENDER_URL, APPEND_TO_EXISTING
)
from scrapers.lot_parser import TenderScraper
from excel.writer import ExcelManager
from excel.reader import get_parent_data, get_tender_url_from_parent


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

                    logger.info(f"Обработан лот {display_title}: {len(lot_data.participants)} участн.")
            except Exception as e:
                logger.error(f"Критическая ошибка при обработке {url}: {e}")

    # -----------------------------------------------------
    # 5. Запись участников в Excel
    # -----------------------------------------------------
    logger.info("Запись участников в Excel...")
    processed_count = 0
    for lot_data in results:
        if lot_data.number:
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


if __name__ == "__main__":
    main()