"""
Проверка AMDM одной оферты по запросу человека (кнопка «AMDM» у номера DM во вкладке «Проверка лотов»).

Номер DM ищется на сайте реестра заново (человек проверяет, потому что сомневается в копии); если сайт
недоступен - ответ по локальной копии. Оферта без номера DM - поиск по коду модели (как делает человек).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

from src.amdm.cache import RegistryCache
from src.amdm.matcher import check_offer, search_codes
from src.amdm.models import CheckResult, OfferRegistration
from src.amdm.parse import parse_row3, parse_row4

logger = logging.getLogger("amdm")


def offer_registration(supplier: str, row3: str, row4: str, lot_title: str = "", sheet: str = "",
                       column: int = 0) -> OfferRegistration:
    p3, p4 = parse_row3(row3), parse_row4(row4)
    return OfferRegistration(sheet=sheet, column=column, supplier=supplier, producer=p3["producer"],
                             country=p3["country"], model=p3["model"], dm_numbers=p4["dm_numbers"],
                             order=p4["order"], order_date=p4["order_date"], quality_docs=p4["quality_docs"],
                             note=p4["note"], raw_row3=row3 or "", raw_row4=row4 or "", lot_title=lot_title or "")


def check_single(offer: OfferRegistration, db: Path, offline: bool = False, headless: bool = True,
                 fetcher_factory=None) -> List[CheckResult]:
    """Результат по каждому номеру DM оферты (или один - по коду модели / сертификатам)."""
    if fetcher_factory is None and not offline:
        from src.amdm.fetch import PlaywrightFetcher

        def fetcher_factory():
            return PlaywrightFetcher(headless=headless)
    cache = RegistryCache(db)
    try:
        records, site_checked, by_name = {}, set(), None
        if offer.dm_numbers:
            fetched = False
            if not offline:
                try:
                    with fetcher_factory() as fetcher:
                        for dm in offer.dm_numbers:
                            rec = fetcher.lookup(dm)
                            site_checked.add(dm)
                            records[dm] = rec
                            if rec:
                                cache.put(rec, source="site")
                            else:
                                cache.mark_not_found(dm)
                            logger.info(f"[AMDM] {offer.supplier}: {dm} на сайте → "
                                        + (f"найден: {rec.producer or '—'}; {rec.model or rec.catalog or rec.name or '—'}"
                                           if rec else "нет в реестре"))
                    fetched = True
                except Exception as e:
                    logger.warning(f"[AMDM] Сайт реестра не ответил ({str(e).splitlines()[0][:120] if str(e) else e}) "
                                   f"- ответ по локальной копии")
            if not fetched:
                for dm in offer.dm_numbers:
                    rec = cache.get(dm)
                    if rec or cache.not_found_fresh(dm):
                        records[dm] = rec
        else:
            codes = search_codes(offer)
            if codes:
                by_name = {}
                for code in codes:
                    found = cache.get_search("catalog", code)
                    if found is None and not offline:
                        try:
                            with fetcher_factory() as fetcher:
                                found = fetcher.search("catalog", code)
                                cache.put_search("catalog", code, found)
                                if not found:
                                    found = fetcher.search("model", code)
                                    cache.put_search("model", code, found)
                        except Exception as e:
                            logger.warning(f"[AMDM] Поиск кода «{code}» на сайте не удался: {e}")
                            found = None
                    if found is not None:
                        by_name[code] = found
        results = check_offer(offer, records, site_checked, by_name)
    finally:
        cache.close()
    for r in results:
        logger.info(f"[AMDM] {offer.sheet}, {offer.supplier}: {r.dm or 'без DM'} - {r.status}: {'; '.join(r.reasons)}")
    return results
