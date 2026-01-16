# Excel Lot MVP

Краткое описание
- Скрипт переносит строки (лоты) из "родительского" Excel-файла в копии шаблонного `sample_model.xlsx` и автоматически формирует листы "lot N", заполняя заголовки, спецификации и данные по участникам (скрапит участников с сайта тендера).
- **НОВИНКА**: Автоматическая обработка PDF - поиск, загрузка и извлечение технических характеристик из документов (Anexa 22, спецификации) для каждого лота и оператора.
- Файл-скрипт по умолчанию: `updated_processor_with_scrape.py`.

Что в репозитории
- `updated_processor_with_scrape.py` — основной рабочий скрипт.
- `pdf_downloader.py`, `pdf_extractor.py`, `pdf_processor.py`, `excel_pdf_updater.py` — модули для обработки PDF.
- `requirements.txt` — зависимости Python.
- `install_deps.bat` / `install_deps.sh` — helper-скрипты для создания venv и установки зависимостей (Windows / Unix).
- `resources/sample_model.xlsx` — шаблон дочернего файла (должен быть в папке `resources`).
- `.gitignore` — исключает `.venv`, `__pycache__`, выходные файлы, PDF.
- `PDF_PROCESSING.md` — подробная документация по обработке PDF.

Требования
- Python 3.10+ (или совместимая версия).
- Tesseract OCR (для PDF-изображений) - см. `PDF_PROCESSING.md`.
- Установите зависимости:
  ````bash
  python -m pip install -r requirements.txt