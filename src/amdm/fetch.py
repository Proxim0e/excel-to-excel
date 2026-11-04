"""
Получение записей реестра AMDM.

Два источника:
  1. CsvImporter — файл, выгруженный кнопкой «Export CSV» на сайте реестра (до 5000 записей
     за раз; например, фильтр по дате приказа за последнюю неделю). Работает офлайн.
  2. PlaywrightFetcher — поиск по номеру DM прямо на сайте через браузер (таблица сайта
     работает на JavaScript, обычным HTTP-запросом её не прочитать). Запускается на твоём
     компьютере: `pip install playwright` и `playwright install chromium`.

ВАЖНО: разметка сайта ещё не сверена вживую (из среды разработки сайт недоступен).
Селекторы написаны под стандартную таблицу DevExpress с несколькими запасными вариантами.
Если поиск не находит строки — запусти `python -m src.amdm probe`, он сохранит HTML и скриншот
страницы в resources/amdm_probe/, по ним селекторы поправляются за пару минут.
"""
from __future__ import annotations

import csv
import io
import logging
import random
import re
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from src.amdm.models import RegistryRecord
from src.amdm.parse import fold, normalize_dm

logger = logging.getLogger(__name__)

REGISTRY_URL = "https://registru.dispozitive.amdm.gov.md/"

# заголовок колонки на сайте/в CSV -> поле RegistryRecord (по тексту без диакритики)
HEADER_MAP = [
    ("den.comerc", "trade_name"), ("den. comerc", "trade_name"), ("denumire comerciala", "trade_name"),
    ("denumire", "name"),
    ("nr. catalog", "catalog"), ("nr.catalog", "catalog"), ("catalog", "catalog"),
    ("model", "model"),
    ("tara", "country"),
    ("producator", "producer"),
    ("reprezentant", "representative"),
    ("ordin", "order"),
    ("data", "order_date"),
    ("cod vamal", "customs_code"),
    ("nr", "dm"),
]


def header_field(header: str) -> Optional[str]:
    h = re.sub(r'\s+', ' ', fold(header)).strip()
    for key, field in HEADER_MAP:
        if h == key or h.startswith(key):
            return field
    return None


def iso_date(value: Optional[str]) -> Optional[str]:
    m = re.search(r'(\d{1,2})[-./](\d{1,2})[-./](\d{4})', value or "")
    if not m:
        m2 = re.search(r'(\d{4})-(\d{2})-(\d{2})', value or "")
        return m2.group(0) if m2 else None
    d, mth, y = (int(x) for x in m.groups())
    return f"{y:04d}-{mth:02d}-{d:02d}"


def record_from_row(values: Dict[str, str]) -> Optional[RegistryRecord]:
    dm = normalize_dm(values.get("dm", ""))
    if not dm:
        return None
    clean = {k: (re.sub(r'\s+', ' ', v).strip() or None) if isinstance(v, str) else v
             for k, v in values.items() if k != "dm"}
    clean["order_date"] = iso_date(clean.get("order_date"))
    return RegistryRecord(dm=dm, **{k: v for k, v in clean.items() if k in RegistryRecord.__dataclass_fields__})


# ---------------------------------------------------------------------------
class CsvImporter:
    """Читает CSV, выгруженный с сайта реестра (разделитель и кодировка определяются сами)."""

    def read(self, path: Path) -> List[RegistryRecord]:
        raw = Path(path).read_bytes()
        for enc in ("utf-8-sig", "cp1250", "cp1252", "latin-1"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        try:
            dialect = csv.Sniffer().sniff(text[:5000], delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        rows = list(csv.reader(io.StringIO(text), dialect))
        if not rows:
            return []
        fields = [header_field(h) for h in rows[0]]
        if "dm" not in fields:
            raise ValueError(f"В CSV не найдена колонка с номером DM (заголовки: {rows[0]})")
        records = []
        for row in rows[1:]:
            values = {f: v for f, v in zip(fields, row) if f}
            rec = record_from_row(values)
            if rec:
                records.append(rec)
        return records


# ---------------------------------------------------------------------------
class PlaywrightFetcher:
    """
    Ищет номер DM на сайте реестра через фильтр колонки «Nr».
    Вежливо: одна вкладка, пауза между запросами, никакой параллельности.
    """

    def __init__(self, headless: bool = True, delay: float = 1.5):
        self.headless = headless
        self.delay = delay
        self._pw = self._browser = self._page = None
        self._columns: List[Optional[str]] = []

    def __enter__(self):
        from playwright.sync_api import sync_playwright
        started = time.time()
        logger.info("[AMDM] Запуск браузера…")
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=self.headless)
        self._page = self._browser.new_page()
        logger.info(f"[AMDM] Открываю реестр {REGISTRY_URL} (до 60 с)…")
        self._page.goto(REGISTRY_URL, wait_until="networkidle", timeout=60000)
        self._columns = self._read_headers()
        logger.info(f"[AMDM] Реестр открыт за {time.time() - started:.0f} с")
        if "dm" not in self._columns:
            raise RuntimeError(f"Не нашёл колонку 'Nr' в заголовках таблицы: {self._columns}. "
                               f"Запусти `python -m src.amdm probe` и пришли файлы из resources/amdm_probe/.")
        return self

    def __exit__(self, *exc):
        for obj in (self._browser, self._pw):
            try:
                obj and (obj.close() if obj is self._browser else obj.stop())
            except Exception:
                pass

    # --- разметка таблицы -------------------------------------------------------
    # На странице две таблицы DevExpress: слева документы (Tip/Denumire), справа сам реестр.
    # Проверено вживую 28.10.2026: реестр — ...MainContent_ASPxGridView4.
    GRID = "[id$='MainContent_ASPxGridView4']"
    HEADER_SELECTORS = [GRID + " tr[id*='DXHeadersRow'] > td", GRID + " tr.dxgvHeaderRow > td"]
    FILTER_INPUT_SELECTORS = [GRID + " tr[id*='DXFilterRow'] > td"]
    DATA_ROW_SELECTORS = [GRID + " tr[id*='DXDataRow']"]

    def _first(self, selectors):
        for sel in selectors:
            loc = self._page.locator(sel)
            if loc.count():
                return loc
        return None

    def _read_headers(self) -> List[Optional[str]]:
        loc = self._first(self.HEADER_SELECTORS)
        if loc is None:
            return []
        return [header_field(t) for t in loc.all_inner_texts()]

    def _rows(self) -> List[Dict[str, str]]:
        loc = self._first(self.DATA_ROW_SELECTORS)
        if loc is None:
            return []
        out = []
        for i in range(loc.count()):
            cells = loc.nth(i).locator(":scope > td").all_inner_texts()
            # в строке данных может быть служебная первая ячейка — выравниваем по правому краю
            shift = max(0, len(cells) - len(self._columns))
            values = {}
            for idx, field in enumerate(self._columns):
                j = idx + shift
                if field and j < len(cells):
                    values[field] = cells[j]
            out.append(values)
        return out

    # --- поиск ------------------------------------------------------------------
    EMPTY_ROW_SELECTORS = [GRID + " tr[id*='DXEmptyRow']", GRID + " tr.dxgvEmptyDataRow"]
    FILTER_WAIT = 20.0   # сек: сколько ждать перерисовки таблицы после фильтра

    def _first_dm(self) -> Optional[str]:
        rows = self._first(self.DATA_ROW_SELECTORS)
        if rows is None or not rows.count():
            return None
        return normalize_dm(rows.nth(0).locator(":scope > td").first.inner_text())

    def _empty_shown(self) -> bool:
        loc = self._first(self.EMPTY_ROW_SELECTORS)
        return bool(loc is not None and loc.count() and loc.first.is_visible())

    def lookup(self, dm: str) -> Optional[RegistryRecord]:
        time.sleep(self.delay + random.uniform(0, 0.6))
        cells = self._first(self.FILTER_INPUT_SELECTORS)
        if cells is None:
            raise RuntimeError("Не нашёл строку фильтров таблицы. Запусти `python -m src.amdm probe`.")
        idx = self._columns.index("dm")
        box = cells.nth(idx).locator("input").first
        # Таблица DevExpress фильтрует по событиям клавиатуры: fill() вставляет значение без них,
        # и фильтр не срабатывает (проверено 01.11.2026). Поэтому - ввод по символам и Enter.
        box.click()
        box.press("Control+A")
        box.press("Backspace")
        box.press_sequentially(dm, delay=40)
        box.press("Enter")
        # Ждём, пока таблица перерисуется: первой строкой станет искомый номер или «нет данных»
        deadline = time.time() + self.FILTER_WAIT
        while time.time() < deadline:
            time.sleep(0.5)
            if self._first_dm() == dm or self._empty_shown():
                break
        for values in self._rows():
            rec = record_from_row(values)
            if rec and rec.dm == dm:
                return rec
        return None

    def lookup_many(self, numbers: Iterable[str]) -> Dict[str, Optional[RegistryRecord]]:
        return {dm: self.lookup(dm) for dm in numbers}

    # --- поиск по колонке (оферта без номера DM) ---------------------------------
    PAGER_SELECTORS = [GRID + " .dxp-summary", GRID + " [class*='dxp-summary']"]
    SEARCH_FIELDS = ("dm", "catalog", "model", "producer", "name", "trade_name")

    def _signature(self) -> tuple:
        """Состояние таблицы: счётчик страниц, первая строка, «нет данных» - по смене видно, что фильтр сработал."""
        # all_inner_texts не ждёт элемент: во время перерисовки счётчик и строки на миг исчезают,
        # а inner_text() ждал бы их 30 с
        try:
            pager = next((t for sel in self.PAGER_SELECTORS for t in self._page.locator(sel).all_inner_texts()), "")
            first = self._page.locator(self.DATA_ROW_SELECTORS[0] + " > td").all_inner_texts()[:1]
            return (pager, first[0] if first else "", self._empty_shown())
        except Exception:
            return ("", "", None)   # таблица перерисовывается

    def _set_filter(self, field: str, text: str) -> None:
        """Одна колонка фильтра; ждём перерисовки таблицы (или FILTER_WAIT, если результат совпал с прежним)."""
        cells = self._first(self.FILTER_INPUT_SELECTORS)
        if cells is None:
            raise RuntimeError("Не нашёл строку фильтров таблицы. Запусти `python -m src.amdm probe`.")
        box = cells.nth(self._columns.index(field)).locator("input").first
        if (box.input_value() or "") == text:
            return
        before = self._signature()
        box.click()
        box.press("Control+A")
        box.press("Backspace")
        if text:
            box.press_sequentially(text, delay=40)
        box.press("Enter")
        deadline = time.time() + self.FILTER_WAIT
        while time.time() < deadline:
            time.sleep(0.5)
            if self._signature() != before:
                time.sleep(0.5)   # строки дорисовываются после счётчика
                return

    def search(self, field: str, text: str) -> List[RegistryRecord]:
        """Записи первой страницы реестра (до 10) по вхождению text в колонку field («catalog»: «F4141»).
        Остальные фильтры сбрасываются: в таблице всегда один фильтр."""
        time.sleep(self.delay + random.uniform(0, 0.6))
        for other in self.SEARCH_FIELDS:
            if other != field and other in self._columns:
                self._set_filter(other, "")
        self._set_filter(field, text)
        if self._empty_shown():
            return []
        return [r for r in (record_from_row(v) for v in self._rows()) if r]

    def search_total(self, field: str, text: str):
        """(всего записей, записи первой страницы) по вхождению text в колонку field."""
        records = self.search(field, text)
        pager = self._signature()[0]
        m = re.search(r"\((\d+)\s+items?\)", pager or "")
        return (int(m.group(1)) if m else len(records)), records

    def reload(self) -> None:
        """Страница реестра заново (после нескольких сбоев подряд), фильтры пустые."""
        self._page.goto(REGISTRY_URL, wait_until="networkidle", timeout=60000)
        self._columns = self._read_headers()


def probe(out_dir: Path, headless: bool = True) -> Path:
    """Сохраняет HTML и скриншот страницы реестра, чтобы поправить селекторы."""
    from playwright.sync_api import sync_playwright
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        page = browser.new_page()
        requests_log = []
        page.on("request", lambda r: requests_log.append(f"{r.method} {r.url}"))
        page.goto(REGISTRY_URL, wait_until="networkidle", timeout=60000)
        (out_dir / "page.html").write_text(page.content(), encoding="utf-8")
        page.screenshot(path=str(out_dir / "page.png"), full_page=True)
        (out_dir / "requests.txt").write_text("\n".join(requests_log), encoding="utf-8")
        browser.close()
    return out_dir
