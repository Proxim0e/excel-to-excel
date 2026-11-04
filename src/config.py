import json
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
# Логика и Пороги

DOWNLOAD_RETRIES = 5 # попытки парсить и грузить
# Сканирование страниц лотов (achizitii.md): лимита не замечено, можно много потоков
SCRAPE_WORKERS: int = 8 # потоки сканирования лотов
SCRAPE_MIN_DELAY: float = 0.0 # пауза между запросами сканирования, сек (0 = без паузы)
# Скачивание файлов (storage.mtender.gov.md): на частые запросы отвечает 429
DOWNLOAD_WORKERS: int = 3 # потоки скачивания
DOWNLOAD_MIN_DELAY: float = 1.5 # пауза между запросами скачивания, сек (общая для потоков)
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

# Включить/Выключить скачивание файлов
# Если False - скрипт создаст только Excel, файлы качаться не будут
ENABLE_DOWNLOADS: bool = True
# Режим "Только скачивание". Пропускает создание и запись Excel файлов.
ONLY_DOWNLOAD: bool = False
# Разрешенные расширения для скачивания.
# Если оставить список пустым [] - будут качаться ВСЕ файлы (как мы и планировали).
# Если добавить [".pdf"] - будут качаться только PDF.
ALLOWED_EXTENSIONS = [".pdf"]
# ---------------------------------------------------------------------------
# Web / Scraper Settings
# ---------------------------------------------------------------------------
DEFAULT_TENDER_URL: str = "https://achizitii.md/ro/public/tender/21643235"
BASE: str = "https://achizitii.md"

# Шаблон регулярного выражения для распознавания ссылок на лоты
LOT_LINK_RE = re.compile(r'/ro/public/tender/\d+/lot/\d+/?$')

HEADERS: Dict[str, str] = {
    "User-Agent": "Mozilla/5.0 (compatible; scraper/1.0; +https://example.com/bot)"
}


# ---------------------------------------------------------------------------
# Настройки из окна приложения (кнопка «Настройки»): data/settings.json перекрывает значения выше.
# Файл свой на каждом ПК (data/ не в git); значения по умолчанию - здесь. Запуск тендера, AMDM и уточнений
# из окна идёт отдельным процессом, поэтому сохранённое применяется со следующего запуска.
# ---------------------------------------------------------------------------
SETTINGS_FILE: Path = BASE_DIR / "data" / "settings.json"
# имя -> (подпись в окне, тип)
EDITABLE: Dict[str, Tuple[str, type]] = {
    "ENABLE_DOWNLOADS": ("Скачивать документы участников", bool),
    "ONLY_DOWNLOAD": ("Только скачивание (Excel не создавать)", bool),
    "ALLOWED_EXTENSIONS": ("Расширения файлов через запятую (пусто - все)", list),
    "SCRAPE_WORKERS": ("Потоков сканирования лотов", int),
    "SCRAPE_MIN_DELAY": ("Пауза между запросами сканирования, с", float),
    "DOWNLOAD_WORKERS": ("Потоков скачивания", int),
    "DOWNLOAD_MIN_DELAY": ("Пауза между скачиваниями, с (сайт отвечает 429)", float),
    "DOWNLOAD_RETRIES": ("Попыток на запрос", int),
    "APPEND_TO_EXISTING": ("Дописывать в существующие листы", bool),
    "PERCENT_THRESHOLD": ("Порог цены, доля базы (1.3 = 130%)", float),
    "DEFAULT_TENDER_URL": ("Тендер по умолчанию (запуск по родительскому файлу)", str),
}
DEFAULTS = {name: globals()[name] for name in EDITABLE}


def coerce(name: str, value):
    """Значение из окна / JSON -> тип настройки; ошибка - ValueError с понятным текстом."""
    label, kind = EDITABLE[name]
    if kind is bool:
        return value if isinstance(value, bool) else str(value).strip().lower() in ("1", "true", "да", "yes", "on")
    if kind is list:
        items = value if isinstance(value, list) else str(value).split(",")
        return [("." + s.strip().lstrip(".")).lower() for s in items if str(s).strip()]
    try:
        return kind(str(value).strip().replace(",", ".") if kind is float else str(value).strip())
    except ValueError:
        raise ValueError(f"«{label}»: нужно {'число' if kind in (int, float) else 'значение'}, а не «{value}»")


def current_settings() -> Dict[str, object]:
    return {name: globals()[name] for name in EDITABLE}


def save_settings(values: Dict[str, object]) -> Path:
    """Проверяет и сохраняет в SETTINGS_FILE только отличия от значений по умолчанию."""
    clean = {name: coerce(name, values[name]) for name in EDITABLE if name in values}
    globals().update(clean)
    diff = {k: v for k, v in current_settings().items() if v != DEFAULTS[k]}
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(diff, ensure_ascii=False, indent=2), encoding="utf-8")
    return SETTINGS_FILE


def _load_settings_file() -> None:
    if not SETTINGS_FILE.is_file():
        return
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        globals().update({k: coerce(k, v) for k, v in data.items() if k in EDITABLE})
    except Exception as e:  # битый файл настроек не должен ломать запуск
        print(f"[config] {SETTINGS_FILE} не прочитан ({e}) - значения по умолчанию")


_load_settings_file()
