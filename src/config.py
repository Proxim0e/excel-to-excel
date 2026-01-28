import os
import re
from pathlib import Path
from typing import Dict, Tuple, List, Optional

# ---------------------------------------------------------------------------
# Пути
# ---------------------------------------------------------------------------
# Используем Path для работы с файловой системой
BASE_DIR = Path(__file__).resolve().parent.parent
RESOURCE_DIR = BASE_DIR / "resources"
TEMPLATE_FILE = RESOURCE_DIR / "sample_model.xlsx"

# ---------------------------------------------------------------------------
# Web / Scraper Settings
# ---------------------------------------------------------------------------
DEFAULT_TENDER_URL: str = "https://achizitii.md/ro/public/tender/21463176"
BASE: str = "https://achizitii.md"

# Шаблон регулярного выражения для распознавания ссылок на лоты
LOT_LINK_RE = re.compile(r'/ro/public/tender/\d+/lot/\d+/?$')

HEADERS: Dict[str, str] = {
    "User-Agent": "Mozilla/5.0 (compatible; scraper/1.0; +https://example.com/bot)"
}



# ---------------------------------------------------------------------------
# Логика и Пороги

DOWNLOAD_RETRIES = 25 # попытки парсить и грузить
MAX_WORKERS: int = 50 # потоки

PDF_FUZZY_THRESHOLD = 95
PDF_MAX_PAGES_FOR_CONTENT_CHECK = 100
PDF_MAX_WORKERS_FOR_CONTENT_CHECK = 2
PDF_SPEC_ROW = 11
PDF_DATA_ROW = 3      # строка для Producator/Țara/Model

# Включить/Выключить скачивание файлов
# Если False - скрипт создаст только Excel, файлы качаться не будут
ENABLE_DOWNLOADS: bool = False
# ---------------------------------------------------------------------------
# Поведение при существующих листах
APPEND_TO_EXISTING: bool = False

# Оформление: цвета и шрифты (будут использоваться в excel модуле)
NOT_HELD_FILL_DARK = "001f4d"  # тёмно-синий
NOT_HELD_FONT_WHITE = "FFFFFF"
HIGH_PERCENT_FILL = "FF0000"   # красный
PERCENT_THRESHOLD: float = 1.3  # 130%

# ---------------------------------------------------------------------------
# Маппинг колонок (Data Mapping)
# ---------------------------------------------------------------------------
COLS: Dict[str, List[str]] = {
    'nr_lot': ['Nr. Lot', 'Nr Lot', 'Nr lot', 'Nr. lot'],
    'denumire': ['Denumirea Lotului\n04.09.2025', 'Denumirea Lotului', 'Denumire Lot NEW2'],
    'specificatie': ['Specificația Tehnică\n04.09.2025', 'Specificația Тehnică', 'Specificarea техническая NEW2'],
    'unitate_masura': ['Unitatea de măsură'],
    'cantitate_total': ['Cantitatea Totală'],
    'suma_alocata': ['Suma alocată'],
}

# Куда писать определённые поля в шаблоне
# key -> кортеж координат ячеек
TARGET_MAP: Dict[str, Tuple[str, ...]] = {
    "denumire": ("B1",),
    "specificatie": ("B11",),
    "unitate_masura": ("C9",),
    "cantitate_total": ("A1",),
    "suma_alocata": ("D8",)
}

# ---------------------------------------------------------------------------
# Настройки скачивания файлов (Downloads)
# ---------------------------------------------------------------------------
# Папка для сохранения документов
DOWNLOAD_DIR: Path = BASE_DIR / "downloads"



# Разрешенные расширения для скачивания.
# Если оставить список пустым [] - будут качаться ВСЕ файлы (как мы и планировали).
# Если добавить [".pdf"] - будут качаться только PDF.
ALLOWED_EXTENSIONS = [".pdf"]

# PDF processing settings
PDF_KEYWORDS_FILENAME = [
    "Anexa 22", "a22", "anexa22", "st",
    "specificatii tehnice", "specificatiitehnice",
    "specificații tehnice", "specificațiitehnice",
    "formulare teh", "specificatii tehnice 22",
    "specificatia tehnica", "anexa 22 specificatii tehnice",
    "anexanr22"
]

PDF_KEYWORDS_CONTENT = [
    "Specificarea tehnică deplină propusă de către ofertant", "Anexa nr. 22", "Specificaţii tehnice",
    " Specificatie tehnică propus  de ofertant", "Specificația tehnică propusă de \noperatorul economic", "прил. 22",
    "модель", "производитель", "страна происхождения", "изготовитель"
]


# ----------------------------------------------------------------------------
# Настройки обработки PDF (независимо от скачивания)
# ----------------------------------------------------------------------------
ENABLE_PDF_PROCESSING: bool = True          # Включи/выключи обработку PDF
USE_LATEST_DOWNLOAD_FOLDER: bool = True     # Использовать самую свежую папку в downloads, если нет новой