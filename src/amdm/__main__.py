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
                failed, aborted = _lookup_all(fetcher, cache, to_fetch, records, site_checked)
                if failed and not aborted:
                    logger.info(f"[AMDM] Повторяю номера со сбоем: {len(failed)}")
                    failed, aborted = _lookup_all(fetcher, cache, failed, records, site_checked, "повтор ")
                if failed:
                    logger.error(f"[AMDM] Сайт так и не ответил по {len(failed)} номерам: {', '.join(failed)} - "
                                 f"они «neverificat», запустите проверку ещё раз")
        except ImportError:
            logger.error("Не установлен playwright: pip install playwright && playwright install chromium. "
                         "Продолжаю только по локальной копии.")
        except Exception as e:
            logger.error(f"Поиск на сайте прерван: {e}. Найденное сохранено, остальное — «neverificat».")
    return records, site_checked


MAX_FAILURES_IN_ROW = 5     # столько сбоев подряд - сайт лежит, дальше не ждём по 30 с на номер


def _lookup_all(fetcher, cache: RegistryCache, dms, records, site_checked, label: str = ""):
    """Номера по одному; сбой на номере (таймаут клика и т.п.) не обрывает остальные: номер пропускается,
    страница реестра перезагружается. Возвращает (номера со сбоем, остановлено ли из-за сбоев подряд)."""
    failed, in_row = [], 0
    for i, dm in enumerate(dms, start=1):
        progress = f"[AMDM] сайт: {label}{i}/{len(dms)} ({100 * i / len(dms):.0f}%) {dm}"
        try:
            rec = fetcher.lookup(dm)
        except Exception as e:
            failed.append(dm)
            in_row += 1
            logger.warning(f"{progress} - ошибка: {(str(e).splitlines() or [type(e).__name__])[0][:120]}; пропускаю")
            if in_row >= MAX_FAILURES_IN_ROW:
                rest = list(dms[i:])
                failed.extend(rest)
                logger.error(f"[AMDM] {in_row} сбоев подряд - сайт реестра не отвечает, "
                             f"остальные {len(rest)} номеров не проверяю")
                return failed, True
            try:
                fetcher.reload()
            except Exception as e2:
                logger.warning(f"[AMDM] Страница реестра не перезагрузилась: {(str(e2).splitlines() or [type(e2).__name__])[0][:120]}")
            continue
        in_row = 0
        site_checked.add(dm)
        if rec:
            cache.put(rec, source="site")
        else:
            cache.mark_not_found(dm)
        records[dm] = rec
        found = (f"найден: {rec.producer or '—'}; {rec.model or rec.catalog or rec.name or '—'}"
                 if rec else "нет в реестре")
        logger.info(f"{progress} → {found}")
    return failed, False


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


def collect_types(cache: RegistryCache, offers, offline: bool, headless: bool, kb=None) -> dict:
    """Оферты без номера DM (OfferRegistration): тип изделия (первое слово названия лота) -> медицинский ли он
    (True / False по реестру; HUMAN_YES / HUMAN_NO - решение человека; None - неизвестно). Ответ реестра хранится
    в его копии 30 дней. kb - база знаний: решения человека берутся из неё, а на неясный тип ставится вопрос
    в окне («медицинское ли изделие?»)."""
    from src.amdm.medical import HUMAN_NO, HUMAN_YES, is_medical_type, registry_query, type_variants, type_word
    offers = list(offers)
    by_word = {}
    for o in offers:
        w = type_word(o.lot_title)
        if w:
            by_word.setdefault(w, []).append(o)
    words = sorted(by_word)
    human = {}
    if kb is not None:
        from src.evaluate.answers import human_types
        human = human_types(kb)
    variants = {w: type_variants(w) for w in words}
    known = {v: cache.get_type(v) for vs in variants.values() for v in vs}
    to_check = [v for v in known if not cache.type_checked(v)]
    logger.info(f"[AMDM] Типов изделий у оферт без номера DM: {len(words)}; проверить в реестре: "
                f"{0 if offline else len(to_check)}")
    if to_check and not offline:
        from src.amdm.fetch import PlaywrightFetcher
        try:
            with PlaywrightFetcher(headless=headless) as fetcher:
                for i, v in enumerate(to_check, start=1):
                    try:
                        total, recs = fetcher.search_total("name", registry_query(v))
                    except Exception as e:
                        logger.warning(f"[AMDM] тип «{v}» - ошибка: {str(e).splitlines()[0][:120]}; пропускаю")
                        continue
                    medical = is_medical_type(v, total, [r.name for r in recs])
                    cache.put_type(v, medical, total)
                    known[v] = medical
                    logger.info(f"[AMDM] тип: {i}/{len(to_check)} ({100 * i / len(to_check):.0f}%) «{v}» → "
                                f"{ {True: 'медицинский', False: 'в реестре таких изделий нет - не медицинский', None: 'неясно (буквы без диакритики)'}[medical] } "
                                f"(записей по «{registry_query(v)}»: {total})")
        except ImportError:
            logger.error("Не установлен playwright: проверка типа изделия пропущена.")
        except Exception as e:
            logger.error(f"Проверка типа изделия прервана: {e}. Найденное сохранено.")
    result, asked = {}, 0
    for w, vs in variants.items():
        if w in human:
            result[w] = HUMAN_YES if human[w] else HUMAN_NO
            continue
        flags = [known.get(v) for v in vs]
        result[w] = True if any(f is True for f in flags) else (False if all(f is False for f in flags) else None)
        if result[w] is None and kb is not None and not offline:
            from src.evaluate.answers import ask_medical_type
            registry = [{"query": registry_query(v), "total": (cache.db.execute(
                "SELECT total FROM amdm_type WHERE word = ?", (v,)).fetchone() or [None])[0]} for v in vs]
            examples = [{"sheet": o.sheet, "participant": o.supplier, "lot_title": o.lot_title, "model": o.model,
                         "quality_docs": o.quality_docs} for o in by_word[w][:8]]
            if ask_medical_type(kb, w, {"word": w, "registry": registry, "offers": examples,
                                        "offers_total": len(by_word[w])}):
                asked += 1
                logger.info(f"[AMDM] тип «{w}»: реестр не дал ответа - вопрос в окне «медицинское ли изделие?»")
    if human:
        used = [w for w in words if w in human]
        if used:
            logger.info(f"[AMDM] Типы по решению человека: " + ", ".join(
                f"{w} - {'медицинский' if human[w] else 'не медицинский'}" for w in used))
    return result


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
        from src.history.db import connect as connect_kb
        kb = connect_kb()
        try:
            medical = collect_types(cache, [o for o in offers if not o.dm_numbers], args.offline,
                                    not args.show_browser, kb)
        finally:
            kb.close()
    finally:
        cache.close()

    results = [r for o in offers for r in check_offer(o, records, site_checked, by_name, medical)]
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
