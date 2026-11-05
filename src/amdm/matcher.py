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
from src.amdm.medical import HUMAN_NO, not_medical, type_word
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


# Связки не входят в аббревиатуру: ICMN = International Company for Medical Necessities.
# Формы собственности в конце не мешают: сравнивается начало («jhmscl».startswith(«jhms»))
_CONNECTORS = {"for", "of", "and", "the", "ve", "de", "la", "si", "und", "e", "y"}


def _initials(name: str) -> str:
    words = [w for w in re.findall(r"[a-z]+", fold(name or "")) if w not in _CONNECTORS]
    return "".join(w[0] for w in words)


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
    # аббревиатура: «JHMS» = Jiangxi Hawk Medical Supplies, «MIR» = Medical International Research
    short = re.sub(r"[^a-z]", "", fold(offered or ""))
    acronym = re.sub(r"[^a-z]", "", a)
    if 3 <= len(acronym) <= 6 and _initials(registered).startswith(acronym):
        return True, 0.85
    # опечатка или транслитерация в начале названия: «Aqumedical» - «AQUA MEDIKAL TIBBI ...»,
    # «SUN O SUB» - «SAN-O-SUB MBB»: начало реестрового названия той же длины или чуть длиннее
    reg = re.sub(r"[^a-z]", "", fold(registered or ""))
    if len(short) >= 6:
        head = max(difflib.SequenceMatcher(None, short, reg[:len(short) + extra]).ratio() for extra in (0, 1, 2))
        if head >= 0.8:
            return True, head
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
    offered_compact = compact(offered)
    # Значения полей реестра по отдельности: «SD-T, SD-L, SD-Y» - три кода семейства
    items = [(i.strip(), f) for f in (record.model, record.catalog) if f for i in re.split(r"[,;]", f) if i.strip()]
    sizes = _numbers(offered)
    for item, field in items:
        c = compact(item)
        # тот же код целиком, в том числе без цифр: «GS» - «GS»
        if c and c == offered_compact:
            return True, item
        # регистрация на семейство или код без цифр внутри оферты: «SD-T» в «SD-T 1024230», «DI-HL» в
        # «DI-HL 25X30 CM». Код из одних букв - от 5 символов и из двух частей и более
        # («E.O.S. DEV.», «Ortho CP GU M»): одно слово («RULOU») - не код.
        letters_code = len(c) >= 5 and len(re.findall(r"[A-Za-z0-9]+", item)) >= 2
        if (letters_code or (len(c) >= 3 and re.search(r"[\d\-]", item))) and c in offered_compact:
            # размеры, если указаны с обеих сторон (в значении или в поле реестра), должны совпасть
            field_sizes = _numbers(item) or _numbers(field)
            if not (len(field_sizes) >= 2 and len(sizes) >= 2 and field_sizes != sizes):
                return True, item
    tokens = model_tokens(offered)
    for tok in tokens:
        c = compact(tok)
        if len(c) >= 3 and c in hay:
            return True, tok
    if tokens and not items and not record.trade_name:
        # в записи реестра только общее название («ACCESORII RESPIRATORII») - код сравнивать не с чем
        return None, ""
    # и наоборот: код из реестра внутри текста оферты ('8250731SP' в 'Ref.N8250731SP')
    for code in model_tokens(" ".join(x for x in (record.model, record.catalog) if x)):
        c = compact(code)
        if len(c) >= 4 and c in offered_compact:
            return True, code
    # Модель описана размерами («80 MM X 20 M, RULOU»): совпали числа с полем реестра - та же модель,
    # не совпали - другая («56 X 18» против «80 X 30»)
    # артикул - буквы с цифрами («PFS01-01523230»), но не число с единицей («110mm», «200P»)
    is_article = any(re.search(r"[a-z]", t, re.I) and re.search(r"\d", t)
                     and not re.fullmatch(r"\d+(?:[.,]\d+)?[a-z]{1,3}", t, re.I) for t in tokens)
    if len(sizes) >= 2 and not is_article:
        for field in (record.model, record.trade_name, record.catalog):
            field_sizes = _numbers(field)
            if len(field_sizes) >= 2:
                return (True, field) if field_sizes == sizes else (False, f"dimensiuni {offered}")
    if tokens:
        return False, ", ".join(tokens[:4])
    # кодов нет, только слова: мягкое сравнение текста - с каждым полем отдельно и со всеми вместе
    texts = [x for x in (record.model, record.trade_name, record.catalog, record.name) if x] + [haystack_raw]
    ratio = max(difflib.SequenceMatcher(None, fold(offered), fold(t)).ratio() for t in texts)
    return (True, offered) if ratio >= 0.6 else (None, "")


def _numbers(text: Optional[str]) -> List[str]:
    """Числа текста как размеры: «80 MM X 20 M» -> ['20', '80'] (порядок не важен)."""
    return sorted(n.replace(",", ".").lstrip("0") or "0" for n in re.findall(r"\d+(?:[.,]\d+)?", text or ""))


def device_in_lot(record: RegistryRecord, lot_title: str) -> Optional[str]:
    """Модель из записи реестра названа в названии лота («Kit mentenanta 3 ani SV650» и DM аппарата SV650):
    участник указал регистрацию аппарата, к которому запчасть. Возвращает эту модель или None."""
    title = compact(lot_title or "")
    for value in (record.model, record.trade_name):
        c = compact(value or "")
        if len(c) >= 4 and re.search(r"\d", c) and c in title:
            return value
    return None


# Числа, которые не код изделия: стандарты и годы («ISO 13485:2016», «EN 1041»)
NOT_CODES = re.compile(r"^(?:13485|9001|14001|45001|14971|11135|11137|11140|10993|60601|1041|15223|"
                       r"(?:19|20)\d\d)$")


UNIT_LIKE = re.compile(r"(?i)^\d+(?:[.,]\d+)?(?:x\d+(?:[.,]\d+)?)*(?:ml|mm|cm|m|l|g|kg|mg|inch|buc|pcs|fr|ch|"
                       r"w|v|h|min|c|°c)$|^\d{1,2}[./]\d{1,2}[./]\d{2,4}$|^\d+(?:[.,]\d+)?(?:x\d+(?:[.,]\d+)?)+$|^\d+-(?:lead|fir|fire|canale|ch)$")


def search_codes(offer: OfferRegistration, limit: int = 2) -> List[str]:
    """Коды модели оферты для поиска в реестре по колонке «Nr. catalog» / «Model», когда номера DM нет:
    с цифрами, от 4 знаков, не стандарт и не год, не номер DM."""
    codes = []
    for tok in model_tokens(offer.model):
        c = re.sub(r"\s", "", tok)
        # «500ml», «35x43cm», «5-Lead» - размер, объём, описание, а не код изделия
        if UNIT_LIKE.match(c):
            continue
        if len(compact(c)) >= 4 and not NOT_CODES.match(c) and not re.match(r"(?i)^dm\d", c) and c not in codes:
            codes.append(c)
    return codes[:limit]


def check_by_name(offer: OfferRegistration, found: List[RegistryRecord], searched: List[str]) -> Optional[CheckResult]:
    """Оферта без номера DM: записи реестра, найденные по коду модели (как ищет человек - по названию).
    Совпали производитель и модель - изделие зарегистрировано (номер DM в оферте не указан);
    по кодам ничего - вероятно «nu este inregistrat» (или изделие не медицинское) - проверить."""
    if not searched:
        return None
    hits = [r for r in found if model_matches(offer.model, r)[0] is True]
    same = [r for r in hits if producer_matches(offer.producer, r.producer)[0]] if offer.producer else hits
    iso = f" В оферте: {', '.join(offer.quality_docs)}." if offer.quality_docs else ""
    if same:
        r = same[0]
        return CheckResult(offer, r.dm, OK, [
            f"Номер DM в оферте не указан; найден в реестре по коду {', '.join(searched)}: {r.dm} — "
            f"{r.producer}; {r.model or r.catalog or r.name}"], r)
    if hits:
        r = hits[0]
        return CheckResult(offer, r.dm, CHECK, [
            f"Номер DM в оферте не указан; по коду {', '.join(searched)} в реестре {r.dm} другого производителя: "
            f"«{r.producer}» (в оферте «{offer.producer}»).{iso}"], r)
    return CheckResult(offer, None, CHECK, [
        f"Номер DM в оферте не указан, по коду модели ({', '.join(searched)}) в реестре не найдено — возможно, "
        f"nu este înregistrat (или изделие не медицинское).{iso}"])


def check_offer(offer: OfferRegistration, records: dict, site_checked: set,
                by_name: Optional[dict] = None, medical: Optional[dict] = None) -> List[CheckResult]:
    """
    records: dm -> RegistryRecord | None (None = искали и не нашли).
    site_checked: номера, по которым в этом запуске был ответ сайта или свежая отметка «не найден».
    by_name: код модели -> записи реестра, найденные поиском по нему (для оферт без номера DM).
    Возвращает по одному результату на каждый номер DM (или один, если номеров нет).
    """
    if not offer.dm_numbers:
        word = type_word(offer.lot_title)
        kind = medical.get(word) if medical and word else None
        if not_medical(kind):
            # Тип изделия в реестре не регистрируется (DVD-R, конверты): номер DM не нужен, нужен ISO/CE
            iso = (f"указано: {', '.join(offer.quality_docs)} — проверить срок на дату «evaluare»"
                   if offer.quality_docs else "ISO/CE в оферте не указаны — запросить")
            return [CheckResult(offer, None, OK, [
                f"Не медицинское изделие: "
                + (f"«{word}» - решение человека" if kind == HUMAN_NO else f"в реестре AMDM нет изделий «{word}»")
                + f" — регистрация не требуется; {iso}"])]
        if by_name is not None:
            codes = [c for c in search_codes(offer) if c in by_name]
            found = [r for c in codes for r in by_name[c]]
            result = check_by_name(offer, found, codes)
            if result:
                return [result]
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
            # Не красный: в 21641880 все 3 «не найден» Ivan принял - поиск сайта мог не ответить / номер
            # переоформлен. Отклонение «Nu este inregistrat» - только после ручной проверки на сайте.
            reason = "Номер не найден в реестре AMDM — проверить вручную на сайте (поиск мог не ответить)"
            if dm not in site_checked:
                reason += "; по локальной отметке"
            results.append(CheckResult(offer, dm, CHECK, [reason]))
            continue

        reasons: List[str] = []
        p_ok, p_score = producer_matches(offer.producer, record.producer)
        if not offer.producer:
            reasons.append("В оферте не указан производитель")
        elif not p_ok:
            reasons.append(f"Производитель не совпал: в оферте «{offer.producer}», в реестре «{record.producer}»")

        m_ok, m_what = model_matches(offer.model, record)
        device = device_in_lot(record, offer.lot_title) if m_ok is False else None
        if device:
            # Решение Ivan 02.11.2026: регистрация аппарата не покрывает его запчасть / аксессуар / комплект -
            # на само изделие нужен ISO (или его собственный DM)
            iso = f" В оферте указано: {', '.join(offer.quality_docs)} — проверить его." if offer.quality_docs else ""
            reasons.append(f"DM аппарата «{device}» ({record.name or '—'}), а лот — запчасть/аксессуар к нему: "
                           f"регистрация аппарата не подходит, нужен ISO на изделие ({m_what}).{iso}")
        elif m_ok is False:
            reasons.append(f"Модель/каталожный номер ({m_what}) не найдены в записи реестра "
                           f"(модель: «{record.model or '—'}», каталог: «{record.catalog or '—'}»); "
                           f"если это DM аппарата, а предлагается его запчасть — нужен ISO")
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
