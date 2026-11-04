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
from src.amdm.mark import mark_workbook
from src.amdm.matcher import check_offer, search_codes
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

    logger.info(f"[AMDM] Номеров DM: {len(numbers)}; в локальной копии: {len(numbers) - len(to_fetch)}; "
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
                    found = (f"найден: {rec.producer or '—'}; {rec.model or rec.catalog or rec.name or '—'}"
                             if rec else "нет в реестре")
                    logger.info(f"[AMDM] сайт: {i}/{len(to_fetch)} ({100 * i / len(to_fetch):.0f}%) {dm} → {found}")
        except ImportError:
            logger.error("Не установлен playwright: pip install playwright && playwright install chromium. "
                         "Продолжаю только по локальной копии.")
        except Exception as e:
            logger.error(f"Поиск на сайте прерван: {e}. Найденное сохранено, остальное — «neverificat».")
    return records, site_checked


def collect_by_name(cache: RegistryCache, codes, offline: bool, headless: bool) -> dict:
    """Оферты без номера DM: код модели -> записи реестра, где он есть в «Nr. catalog» (или в «Model»).
    Так ищет человек, когда участник указал только CE/ISO. Запрос хранится в копии реестра неделю;
    offline - только сохранённые запросы."""
    found, to_search = {}, []
    for code in sorted(set(codes)):
        cached = cache.get_search("catalog", code)
        if cached is not None:
            found[code] = cached or (cache.get_search("model", code) or [])
        else:
            to_search.append(code)
    logger.info(f"[AMDM] Оферт без номера DM - поиск по коду модели: кодов {len(found) + len(to_search)}; "
                f"уже искали: {len(found)}; искать на сайте: {0 if offline else len(to_search)}")
    if to_search and not offline:
        from src.amdm.fetch import PlaywrightFetcher
        try:
            with PlaywrightFetcher(headless=headless) as fetcher:
                failures = 0
                for i, code in enumerate(to_search, start=1):
                    try:
                        recs = fetcher.search("catalog", code)
                        cache.put_search("catalog", code, recs)
                        where = "Nr. catalog"
                        if not recs:
                            recs = fetcher.search("model", code)
                            cache.put_search("model", code, recs)
                            where = "Model"
                        failures = 0
                    except Exception as e:  # сбой на одном коде не обрывает поиск остальных
                        failures += 1
                        logger.warning(f"[AMDM] поиск: {i}/{len(to_search)} ({100 * i / len(to_search):.0f}%) "
                                       f"код «{code}» - ошибка: {str(e).splitlines()[0][:120]}; пропускаю")
                        if failures >= 3:
                            logger.info("[AMDM] Несколько сбоев подряд - перезагружаю страницу реестра")
                            fetcher.reload()
                            failures = 0
                        continue
                    found[code] = recs
                    what = ("; ".join(f"{r.dm} {r.producer or '—'} {r.catalog or r.model or ''}".strip()
                                      for r in recs[:3]) if recs else "ничего")
                    logger.info(f"[AMDM] поиск: {i}/{len(to_search)} ({100 * i / len(to_search):.0f}%) "
                                f"код «{code}» в {where} → {what}")
        except ImportError:
            logger.error("Не установлен playwright: поиск по коду модели пропущен.")
        except Exception as e:
            logger.error(f"Поиск по коду модели прерван: {e}. Найденное сохранено.")
    return found


def cmd_check(args) -> int:
    src = Path(args.workbook)
    wb = load_workbook_tolerant(src)
    offers = read_offers(wb)
    numbers = {dm for o in offers for dm in o.dm_numbers}
    cache = RegistryCache(Path(args.db))
    try:
        records, site_checked = collect_records(cache, numbers, args.offline, not args.show_browser)
        codes = [c for o in offers if not o.dm_numbers for c in search_codes(o)]
        by_name = collect_by_name(cache, codes, args.offline, not args.show_browser)
    finally:
        cache.close()

    results = [r for o in offers for r in check_offer(o, records, site_checked, by_name)]
    for r in results:
        why = f" — {'; '.join(r.reasons)[:160]}" if r.status != "verde" and r.reasons else ""
        logger.info(f"[AMDM] {r.offer.sheet}, {r.offer.supplier}: {r.dm or 'без DM'} {r.status}{why}")
    write_report(wb, results)
    marked = mark_workbook(wb, results)
    logger.info("Листы лотов: строка 4 и цвета - " + ", ".join(f"{k}: {v}" for k, v in sorted(marked.items())))
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
