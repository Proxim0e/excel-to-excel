"""
Проверка AMDM одной оферты по запросу человека (кнопка «AMDM ⟳» у номера DM во вкладке «Проверка лотов»).

Номер DM ищется на сайте реестра заново, если сегодня его с сайта ещё не получали (например, проверкой всего
списка); сайт недоступен - ответ по локальной копии и причина сбоя. Оферта без номера DM - поиск по коду модели.
Нажатия идут в одну очередь (`Queue`): один браузер на все проверки, без одновременных запусков.
"""
from __future__ import annotations

import logging
import queue
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional

from src.amdm.cache import RegistryCache
from src.amdm.matcher import check_offer, search_codes
from src.amdm.models import IN_PROGRESS, CheckResult, OfferRegistration
from src.amdm.parse import parse_row3, parse_row4

logger = logging.getLogger("amdm")


def offer_registration(supplier: str, row3: str, row4: str, lot_title: str = "", sheet: str = "",
                       column: int = 0) -> OfferRegistration:
    p3, p4 = parse_row3(row3), parse_row4(row4)
    return OfferRegistration(sheet=sheet, column=column, supplier=supplier, producer=p3["producer"],
                             country=p3["country"], model=p3["model"], dm_numbers=p4["dm_numbers"],
                             order=p4["order"], order_date=p4["order_date"], quality_docs=p4["quality_docs"],
                             note=p4["note"], raw_row3=row3 or "", raw_row4=row4 or "", lot_title=lot_title or "")


def _short(e: Exception) -> str:
    lines = [l.strip() for l in str(e).splitlines() if l.strip() and not set(l.strip()) <= set("═║╔╗╚╝ ")]
    return " ".join(lines[:2])[:300] or type(e).__name__


class Site:
    """Браузер реестра, открытый один раз на очередь проверок. get() - открыть при первом обращении."""

    def __init__(self, factory: Optional[Callable] = None, headless: bool = True):
        if factory is None:
            from src.amdm.fetch import PlaywrightFetcher

            def factory():
                return PlaywrightFetcher(headless=headless)
        self.factory = factory
        self.fetcher = None

    def get(self):
        if self.fetcher is None:
            f = self.factory()
            self.fetcher = f.__enter__()
        return self.fetcher

    def broken(self):
        """После сбоя на странице - следующий запрос откроет браузер заново."""
        self.close()

    def close(self):
        if self.fetcher is not None:
            try:
                self.fetcher.__exit__(None, None, None)
            except Exception:
                pass
            self.fetcher = None


def check_single(offer: OfferRegistration, db: Path, offline: bool = False, headless: bool = True,
                 fetcher_factory=None, site: Optional[Site] = None) -> List[CheckResult]:
    """Результат по каждому номеру DM оферты (или один - по коду модели / сертификатам)."""
    own = site is None and not offline
    if own:
        site = Site(fetcher_factory, headless)
    cache = RegistryCache(db)
    error = None
    try:
        records, site_checked, by_name = {}, set(), None
        today = datetime.now().date().isoformat()
        if offer.dm_numbers:
            for dm in offer.dm_numbers:
                when = cache.fetched_from_site(dm)
                if when and when[:10] == today:            # сегодня уже с сайта (проверка всего списка и т.п.)
                    records[dm] = cache.get(dm)
                    site_checked.add(dm)
                    continue
                rec, asked = None, False
                if not offline:
                    try:
                        rec = site.get().lookup(dm)
                        asked = True
                    except Exception as e:
                        error = _short(e)
                        site.broken()
                        logger.warning(f"[AMDM] Сайт реестра не ответил ({error}) - ответ по локальной копии")
                if asked:
                    site_checked.add(dm)
                    records[dm] = rec
                    if rec:
                        cache.put(rec, source="site")
                    else:
                        cache.mark_not_found(dm)
                    logger.info(f"[AMDM] {offer.supplier}: {dm} на сайте → "
                                + (f"найден: {rec.producer or '—'}; {rec.model or rec.catalog or rec.name or '—'}"
                                   if rec else "нет в реестре"))
                else:
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
                            f = site.get()
                            found = f.search("catalog", code)
                            cache.put_search("catalog", code, found)
                            if not found:
                                found = f.search("model", code)
                                cache.put_search("model", code, found)
                        except Exception as e:
                            error = _short(e)
                            site.broken()
                            logger.warning(f"[AMDM] Поиск кода «{code}» на сайте не удался: {error}")
                            found = None
                    if found is not None:
                        by_name[code] = found
        results = check_offer(offer, records, site_checked, by_name)
    finally:
        cache.close()
        if own:
            site.close()
    for r in results:
        if r.status == IN_PROGRESS and error:
            r.reasons = [f"Сайт реестра не ответил ({error}); в локальной копии номера нет - нажмите «AMDM ⟳» ещё раз"]
        logger.info(f"[AMDM] {offer.sheet}, {offer.supplier}: {r.dm or 'без DM'} - {r.status}: {'; '.join(r.reasons)}")
    return results


class Checker:
    """Очередь проверок по кнопке: один фоновый поток и один браузер; браузер закрывается, когда очередь
    пуста дольше idle секунд. submit(offer, done) - done(results | Exception) вызывается в фоновом потоке."""

    def __init__(self, db: Path, offline: bool = False, headless: bool = True, factory=None, idle: float = 60):
        self.db, self.offline, self.headless, self.factory, self.idle = db, offline, headless, factory, idle
        self.q: "queue.Queue" = queue.Queue()
        self.thread: Optional[threading.Thread] = None
        self.lock = threading.Lock()

    def submit(self, offer: OfferRegistration, done: Callable):
        self.q.put((offer, done))
        with self.lock:
            if self.thread is None or not self.thread.is_alive():
                self.thread = threading.Thread(target=self._run, daemon=True, name="amdm-button")
                self.thread.start()

    def pending(self) -> int:
        return self.q.qsize()

    def _run(self):
        site = None if self.offline else Site(self.factory, self.headless)
        try:
            while True:
                try:
                    offer, done = self.q.get(timeout=self.idle)
                except queue.Empty:
                    with self.lock:
                        if self.q.empty():
                            self.thread = None
                            return
                    continue
                try:
                    done(check_single(offer, self.db, self.offline, site=site))
                except Exception as e:      # ответ окну в любом случае
                    done(e)
        finally:
            if site:
                site.close()

    def join(self, timeout: Optional[float] = None):
        """Для тестов: дождаться конца очереди."""
        t = self.thread
        if t is not None:
            t.join(timeout)
