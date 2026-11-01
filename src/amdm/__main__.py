"""
Проверка номеров DM из Excel оценки по реестру AMDM.

  # проверить книгу; номера, которых нет в локальной копии, искать на сайте (нужен playwright)
  python -m src.amdm check resources/21641880_CDM27_B.xlsx

  # только по локальной копии, без интернета
  python -m src.amdm check resources/21641880_CDM27_B.xlsx --offline

  # загрузить в локальную копию CSV, выгруженный с сайта реестра (до 5000 записей)
  python -m src.amdm import-csv Downloads/registru.csv

  # сохранить HTML/скриншот страницы реестра, если поиск на сайте не работает
  python -m src.amdm probe

Результат: копия книги с листом «Verificare AMDM» рядом с исходным файлом (*_AMDM.xlsx).
Исходный файл не изменяется.
"""
from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from pathlib import Path

from src.config import RESOURCE_DIR
from src.amdm.cache import RegistryCache
from src.amdm.matcher import check_offer
from src.amdm.report import write_report
from src.amdm.workbook import load_workbook_tolerant, read_offers

DEFAULT_DB = RESOURCE_DIR / "amdm_registry.sqlite"
logger = logging.getLogger("amdm")


def collect_records(cache: RegistryCache, numbers, offline: bool, headless: bool):
    """Возвращает (records, site_checked): dm -> запись | None, и номера, подтверждённые сайтом."""
    records, site_checked, to_fetch = {}, set(), []
    for dm in sorted(numbers):
        rec = cache.get(dm)
        if rec:
            records[dm] = rec
        elif cache.not_found_fresh(dm):
            records[dm] = None
            site_checked.add(dm)
        else:
            to_fetch.append(dm)

    logger.info(f"Номеров DM: {len(numbers)}; в локальной копии: {len(numbers) - len(to_fetch)}; "
                f"искать на сайте: {len(to_fetch)}")
    if to_fetch and not offline:
        from src.amdm.fetch import PlaywrightFetcher
        try:
            with PlaywrightFetcher(headless=headless) as fetcher:
                for i, dm in enumerate(to_fetch, start=1):
                    rec = fetcher.lookup(dm)
                    site_checked.add(dm)
                    if rec:
                        cache.put(rec, source="site")
                    else:
                        cache.mark_not_found(dm)
                    records[dm] = rec
                    if i % 20 == 0 or i == len(to_fetch):
                        logger.info(f"  сайт: {i}/{len(to_fetch)}")
        except ImportError:
            logger.error("Не установлен playwright: pip install playwright && playwright install chromium. "
                         "Продолжаю только по локальной копии.")
        except Exception as e:
            logger.error(f"Поиск на сайте прерван: {e}. Найденное сохранено, остальное — «neverificat».")
    return records, site_checked


def cmd_check(args) -> int:
    src = Path(args.workbook)
    wb = load_workbook_tolerant(src)
    offers = read_offers(wb)
    numbers = {dm for o in offers for dm in o.dm_numbers}
    cache = RegistryCache(Path(args.db))
    try:
        records, site_checked = collect_records(cache, numbers, args.offline, not args.show_browser)
    finally:
        cache.close()

    results = [r for o in offers for r in check_offer(o, records, site_checked)]
    write_report(wb, results)
    out = Path(args.out) if args.out else src.with_name(src.stem + "_AMDM.xlsx")
    wb.save(out)

    counts = Counter(r.status for r in results)
    logger.info("Итог: " + ", ".join(f"{k}: {v}" for k, v in counts.most_common()))
    logger.info(f"=== Готово: {out} (лист «Verificare AMDM») ===")
    return 0


def cmd_import_csv(args) -> int:
    from src.amdm.fetch import CsvImporter
    records = CsvImporter().read(Path(args.csv))
    cache = RegistryCache(Path(args.db))
    try:
        n = cache.put_many(records, source=f"csv:{Path(args.csv).name}")
        logger.info(f"Загружено записей: {n}. Всего в копии: {cache.count()}. "
                    f"Самая свежая дата приказа: {cache.latest_order_date()}")
    finally:
        cache.close()
    return 0


def cmd_probe(args) -> int:
    from src.amdm.fetch import probe
    out = probe(RESOURCE_DIR / "amdm_probe", headless=not args.show_browser)
    logger.info(f"Сохранено в {out}: page.html, page.png, requests.txt")
    return 0


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format='[%(asctime)s] [%(levelname)s] %(message)s', force=True)
    ap = argparse.ArgumentParser(prog="python -m src.amdm", description="Проверка регистрации AMDM")
    ap.add_argument("--db", default=str(DEFAULT_DB), help="файл локальной копии реестра (SQLite)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="проверить номера DM в книге оценки")
    c.add_argument("workbook")
    c.add_argument("--offline", action="store_true", help="не ходить на сайт, только локальная копия")
    c.add_argument("--out", help="путь итогового файла (по умолчанию *_AMDM.xlsx рядом с исходным)")
    c.add_argument("--show-browser", action="store_true", help="показать окно браузера при поиске")
    c.set_defaults(func=cmd_check)

    i = sub.add_parser("import-csv", help="загрузить CSV, выгруженный с сайта реестра")
    i.add_argument("csv")
    i.set_defaults(func=cmd_import_csv)

    p = sub.add_parser("probe", help="сохранить HTML/скриншот страницы реестра")
    p.add_argument("--show-browser", action="store_true")
    p.set_defaults(func=cmd_probe)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
