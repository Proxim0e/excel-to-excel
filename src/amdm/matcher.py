"""
Сверка заявленной регистрации с записью реестра.

Итог (как в твоём кодбуке):
  verde        — номер найден, производитель и модель/каталожный номер совпали;
  galben       — найден, но производитель или модель не совпали; либо DM нет, есть только ISO/CE;
  rosu         — номер не найден в реестре (после проверки на сайте);
  fara DM      — нет ни номера DM, ни сертификатов;
  neverificat  — номера нет в локальной копии, а сайт в этом запуске не опрашивался.
"""
from __future__ import annotations

import difflib
import re
from typing import List, Optional, Tuple

from src.amdm.models import (OfferRegistration, RegistryRecord, CheckResult,
                             OK, CHECK, FAIL, NO_DM, IN_PROGRESS)
from src.amdm.parse import fold, compact, model_tokens

LEGAL_FORMS = re.compile(
    r'\b(s\.?r\.?l|s\.?a|s\.?p\.?a|gmbh(?:\s*&\s*co\.?\s*kg(?:aa)?)?|kg(?:aa)?|ag|ltd|limited|inc|corp(?:oration)?|'
    r'co|company|llc|ooo|tov|plc|bv|nv|oy|ab|as|sirketi|ticaret|sanayi|import|export|'
    r'equipment|medical|technology|group|international|industries|industry|manufacturing)\b\.?')


def norm_company(name: Optional[str]) -> str:
    s = fold(name or "")
    s = re.sub(r'[„“"\'«»()]', ' ', s)
    s = s.replace("&", " ")
    s = LEGAL_FORMS.sub(' ', s)
    s = re.sub(r'[^a-z0-9]+', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def producer_matches(offered: Optional[str], registered: Optional[str]) -> Tuple[bool, float]:
    a, b = norm_company(offered), norm_company(registered)
    if not a or not b:
        return False, 0.0
    if a == b or a in b or b in a:
        return True, 1.0
    ta, tb = set(a.split()), set(b.split())
    # совпало «значимое» слово: 'mindray' в 'shenzhen mindray bio medical electronics'
    common = {t for t in ta & tb if len(t) >= 4}
    if common:
        return True, 0.9
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    return ratio >= 0.75, ratio


def model_matches(offered: Optional[str], record: RegistryRecord) -> Tuple[Optional[bool], str]:
    """
    True/False — нашёлся ли код модели из оферты в записи; None — сравнивать нечего.
    Сравниваются коды с цифрами ('630-240-00B', 'T20163'), без учёта пробелов, точек и дефисов.
    """
    haystack_raw = " ".join(x for x in (record.model, record.catalog, record.trade_name, record.name) if x)
    if not offered or not haystack_raw:
        return None, ""
    hay = compact(haystack_raw)
    tokens = model_tokens(offered)
    for tok in tokens:
        c = compact(tok)
        if len(c) >= 3 and c in hay:
            return True, tok
    # и наоборот: код из реестра внутри текста оферты ('8250731SP' в 'Ref.N8250731SP')
    offered_compact = compact(offered)
    for code in model_tokens(" ".join(x for x in (record.model, record.catalog) if x)):
        c = compact(code)
        if len(c) >= 4 and c in offered_compact:
            return True, code
    if tokens:
        return False, ", ".join(tokens[:4])
    # кодов нет, только слова: мягкое сравнение текста
    ratio = difflib.SequenceMatcher(None, fold(offered), fold(haystack_raw)).ratio()
    return (True, offered) if ratio >= 0.6 else (None, "")


def check_offer(offer: OfferRegistration, records: dict, site_checked: set) -> List[CheckResult]:
    """
    records: dm -> RegistryRecord | None (None = искали и не нашли).
    site_checked: номера, по которым в этом запуске был ответ сайта или свежая отметка «не найден».
    Возвращает по одному результату на каждый номер DM (или один, если номеров нет).
    """
    if not offer.dm_numbers:
        if offer.quality_docs:
            return [CheckResult(offer, None, CHECK, [
                f"Номера DM нет, указаны: {', '.join(offer.quality_docs)} — проверить, что изделие "
                f"не медицинское, и срок сертификата на дату «evaluare»"])]
        if not (offer.producer or offer.model):
            return []                         # колонка участника не заполнена — проверять нечего
        return [CheckResult(offer, None, NO_DM, ["Нет ни номера DM, ни сертификатов качества"])]

    results = []
    for dm in offer.dm_numbers:
        if dm not in records:
            results.append(CheckResult(offer, dm, IN_PROGRESS,
                                       ["Номера нет в локальной копии, сайт в этом запуске не опрашивался"]))
            continue
        record = records[dm]
        if record is None:
            reason = "Номер не найден в реестре AMDM"
            if dm not in site_checked:
                reason += " (по локальной отметке; перепроверить на сайте)"
            results.append(CheckResult(offer, dm, FAIL, [reason]))
            continue

        reasons: List[str] = []
        p_ok, p_score = producer_matches(offer.producer, record.producer)
        if not offer.producer:
            reasons.append("В оферте не указан производитель")
        elif not p_ok:
            reasons.append(f"Производитель не совпал: в оферте «{offer.producer}», в реестре «{record.producer}»")

        m_ok, m_what = model_matches(offer.model, record)
        if m_ok is False:
            reasons.append(f"Модель/каталожный номер ({m_what}) не найдены в записи реестра "
                           f"(модель: «{record.model or '—'}», каталог: «{record.catalog or '—'}»)")
        elif m_ok is None and offer.model:
            reasons.append("Модель не с чем сравнить — проверить вручную")

        if offer.order and record.order and compact(offer.order) != compact(record.order):
            reasons.append(f"Приказ не совпал: в оферте {offer.order}, в реестре {record.order}")
        if offer.order_date and record.order_date and offer.order_date != record.order_date:
            reasons.append(f"Дата приказа не совпала: в оферте {offer.order_date}, в реестре {record.order_date}")
        if offer.note and re.search(r'pentru\s+(dm|dispozitiv)|piese|pentr', fold(offer.note)):
            reasons.append(f"Пояснение участника: «{offer.note}» — регистрация может относиться к аппарату, а не к изделию")

        status = OK if (p_ok and m_ok is True and not reasons) else CHECK
        if status == OK:
            reasons = [f"Найден: {record.producer}; {record.model or record.catalog or record.name}"]
        results.append(CheckResult(offer, dm, status, reasons, record))
    return results


def overall_status(results: List[CheckResult]) -> str:
    """Итог по участнику при нескольких номерах DM (наборы): худший из результатов."""
    order = [FAIL, CHECK, NO_DM, IN_PROGRESS, OK]
    for s in order:
        if any(r.status == s for r in results):
            return s
    return IN_PROGRESS
