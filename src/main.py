import concurrent.futures
from datetime import datetime
import logging

# Настройка логирования (вместо простых print)
logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')
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
    for lot_num, lot_info in parent_data.items():
        # Пытаемся получить или создать лист
        ws = excel_manager.get_or_create_sheet(lot_num)
        # ВАЖНО: В текущей реализации get_or_create_sheet заполняет только B1 из LotData,
        # который создается "на лету". Но реальная спецификация и точное название лежат в lot_info (из родителя).

        # Если мы только что создали лист или режим APPEND_TO_EXISTING=True, нужно дополнить данные из родителя.
        if ws and (excel_manager.max_existing < lot_num or APPEND_TO_EXISTING):
            # Заполняем ячейки данными из родительского файла
            # Для упрощения сделаем это прямо здесь, хотя в идеале это должно быть в Writer

            # Локальные переменные на английском!
            # Denumire -> lot_title
            lot_title = lot_info.get('denumire')
            if lot_title:
                ws["B1"] = f"Lot nr. {lot_num} {lot_title}".strip()

            # Specificatie -> lot_specification
            lot_specification = lot_info.get('specificatie')
            if lot_specification:
                current_b11 = ws["B11"].value
                if current_b11 and lot_specification not in str(current_b11):
                    ws["B11"] = f"{current_b11}\n{lot_specification}"
                elif not current_b11:
                    ws["B11"] = str(lot_specification)

            # Остальные поля (для порядка переведем и их)
            unit_of_measure = lot_info.get('unitate_masura')
            total_quantity = lot_info.get('cantitate_total')
            allocated_sum = lot_info.get('suma_alocata')

            if unit_of_measure: ws["C9"] = unit_of_measure
            if total_quantity: ws["A1"] = total_quantity
            if allocated_sum: ws["D8"] = allocated_sum

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
                    logger.warning(f"Ошибка парсинга {url}: {lot_data.error}")
                else:
                    logger.info(f"Спарсен лот {lot_data.number}: {len(lot_data.participants)} участн.")
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