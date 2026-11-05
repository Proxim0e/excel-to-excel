"""
Карточка товара: предзаполнение из Anexa 22, строк 3-4 и реестра AMDM; сохранение после подтверждения.

Предзаполненная карточка (source = prefill) - это предложение системы; в оценке «высокой уверенности»
участвует только подтверждённая человеком (source = human).
"""
import json
import re
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from src.amdm.models import OfferRegistration, RegistryRecord
from src.amdm.parse import compact, model_tokens

CARD_FIELDS = ("producer", "model", "codes", "dm", "name", "agents", "winners", "params", "compatible_with",
               "iso")


NUMBERED = re.compile(r"(?:^|(?<=[\s,;.]))(\d{1,2})\s*[.)]\s*(?=\D)")
# «≤ 5 microni -kit complet»: тире с пробелом перед словом - новый пункт («Mască-nebulizator», «3 ml-10min» - нет)
DASH_ITEM = re.compile(r"\s+[-–—]\s*(?=[a-zăâîșțşţ])")      # «DI – HL» (модель с большой буквы) - не делим
HAS_VALUE = re.compile(r"[\d≥≤<>±]")
SENTENCE = re.compile(r"(?<=\w\w)\.\s+(?=[A-ZĂÂÎȘȚ][a-zăâîșț])")         # «model 5392. Lungime …»; «(K. Storz)» - нет


def _split_commas(text: str) -> List[str]:
    """Запятая делит пункты, если слева уже есть значение (число) или справа его нет:
    «3 ml-10min, dimensiunea … 5 microni» - два пункта; «format din Mască-nebulizator, cameră … tub de 200cm» -
    один (перечисление относится к числу справа); «electrod Ag/AgCl, gel solid» - два; «1,5 mm» не режется."""
    parts = re.split(r",\s+(?=[^\W_])", text)
    out = [parts[0]]
    for part in parts[1:]:
        if part[0].isupper():                 # «DryPix SMART, LITE sau EDGE» - перечисление названий
            out[-1] = f"{out[-1]}, {part}"
        elif part[0].isdigit() or HAS_VALUE.search(out[-1]) or not HAS_VALUE.search(part):   # «bipolar, 2 tije»
            out.append(part)
        else:
            out[-1] = f"{out[-1]}, {part}"
    return out


def spec_items(text: str) -> List[str]:
    """Пункты текста требования/спецификации: «1.diametru 38 - 47mm, 2. electrod Ag/AgCl» -> два пункта.
    Без нумерации - по строкам, «;», тире-маркерам и запятым (см. _split_commas)."""
    text = (text or "").split("//")[0].strip()
    if not text:
        return []
    marks = list(NUMBERED.finditer(text))
    numbers = [int(m.group(1)) for m in marks]
    if len(marks) >= 2 and numbers[:2] == [numbers[0], numbers[0] + 1]:
        parts = [text[a.end():(b.start() if b else len(text))] for a, b in zip(marks, marks[1:] + [None])]
        head = text[:marks[0].start()]
        parts = ([head] if len(head.strip()) > 3 else []) + parts
        parts = [line for part in parts for line in re.split(r"[\n;]+", part)]   # «2. Dimensiuni: / Diametru …»
    else:
        parts = [c for line in re.split(r"[\n;]+", text) for sentence in SENTENCE.split(line)
                 for d in DASH_ITEM.split(sentence) for c in _split_commas(d)]
    parts = [p.strip(" \t\r\n,;.-–") for p in parts]
    return [p for p in parts if len(p) > 1 and not p.endswith(":")]        # «Dimensiuni:» - заголовок подпунктов


QUALIFIER = re.compile(r"\s(aproximativ|aprox\.?|circa|min(?:im)?\.?|max(?:im)?\.?|de|cel puțin|cel putin|pana la|până la|"
                       r"mai mare(?: de)?|mai mic(?: de)?|peste|sub|nu mai mare|nu mai mic|"
                       r"nu mai (?:mult|puțin|putin) de|nu mai mic de|nu mai mare de|nu mai mic decât|cca\.?)$",
                       re.IGNORECASE)
# после значения снова слова и число: «… (10 x 14 inch) ambalaj până la 150» - со слов начинается новый параметр
VALUE_OVERRUN = re.compile(r"(?<=[\w)])\s+(?=[a-zăâîșțşţ]{3,}(?:\s+[^\d\s]+)+\s+[≥≤<>]?[A-Za-z]?\d)")
DIMS = re.compile(r"\d+(?:[.,]\d+)?\s*(?:mm|cm|m)?\s*[x×хХX*]\s*\d")
STRONG_CUT = re.compile(r",\s+|\s+(?:și|si|cu)\s+", re.IGNORECASE)           # число относится к последнему
WEAK_CUT = re.compile(r"\s+(?:pentru|din|la|în|in)\s+", re.IGNORECASE)               # только у длинных названий
LEAD_WORDS = re.compile(r"^(?:cu|și|si|de|a|al|la|din|pentru|iar)\s+", re.IGNORECASE)
LONG_NAME = 40


def _short_name(name: str) -> Tuple[str, str, str]:
    """Название у числа -> (короткое название, остаток слева, разделитель): «Cu cameră speciala conceputa cu rata
    de nebulizare» -> («rata de nebulizare», «Cu cameră speciala conceputa», « cu »); «cameră nebulizator și tub»
    -> («tub», «cameră nebulizator», « și »)."""
    cuts = [m for m in STRONG_CUT.finditer(name) if len(name) - m.end() >= 3 and m.start() >= 3]
    if not cuts and len(name) > LONG_NAME:
        cuts = [m for m in WEAK_CUT.finditer(name) if len(name) - m.end() >= 3]
    if not cuts:
        return name, "", ""
    m = cuts[-1]
    return name[m.end():].strip(" ,-"), name[:m.start()].strip(" ,-"), m.group(0)


def _plain(item: str) -> Tuple[str, str]:
    """Пункт без числа: «kit complet format din Mască-nebulizator, cameră» -> (kit complet format, Mască-…);
    «material: PVC» -> (material, PVC); иначе весь пункт параметром со значением «da»."""
    m = re.search(r"\s(?:format(?:ă|a)? din|din|compus din|alcătuit din)\s", item, re.IGNORECASE)
    if m and 3 <= m.start() <= LONG_NAME:
        return item[:m.end()].strip(), item[m.end():].strip()
    return item, "da"


def split_params(item: str) -> List[Tuple[str, str]]:
    """Пункт -> параметры (название, значение): «Lungime: ≥ 15 cm» -> (Lungime, ≥ 15 cm); «Cu cameră speciala
    conceputa cu rata de nebulizare aproximativ 3 ml-10min» -> (rata de nebulizare, aproximativ 3 ml-10min) и
    (Cu cameră speciala conceputa, da). Слова «aproximativ / min. / max. / de / până la» уходят в значение."""
    item = re.sub(r"\s+", " ", item).strip()
    # текст Anexa 22 бывает обрезан: «…ceputa cu rata …» - обрывок слова не название
    item = re.sub(r"^(?:…|\.{2,})(?:[^\W\d_]+\s+)?", "", item).strip()
    item = LEAD_WORDS.sub("", item)
    if ":" in item:
        name, value = item.split(":", 1)
        head = re.match(r"(.*\d\S*(?:\s+(?:mm|cm|m|ml|l|g|kg|buc|bucăți|pcs|fr|ch)\b)?)\s+"
                        r"([^\W\d_][\w.]*(?:\s+[^\W\d_][\w.]*)?)\s*$", name, re.IGNORECASE)
        if head and value.strip():            # «… 110mmх25 m tip: rola» - «tip» отдельный параметр после размера
            return split_params(head.group(1)) + [(head.group(2), value.strip())]
        if name.strip() and value.strip() and len(name) <= LONG_NAME * 2:
            return [(name.strip(), value.strip())]
    m = HAS_VALUE.search(item)
    if m and m.start() <= 2:                 # пункт начинается с числа: «200x330 mm», «2 tije»
        if DIMS.match(item):
            return [("dimensiuni", item)]
        lead = re.match(r"([≥≤<>±]?\s*\d[\d.,/\-–x×]*\s*(?:mm|cm|m|ml|l|g|kg|%|buc\.?|pcs)?)\s+(\D.*)$", item, re.IGNORECASE)
        return [(lead.group(2).strip(), lead.group(1).strip())] if lead else [(item, "da")]
    if not m:
        return [_plain(item)]
    start = m.start()
    while start > 0 and m.start() - start < 2 and item[start - 1].isalpha():
        start -= 1                           # «N100», «F4141» - буквы перед числом - часть значения
    if start != m.start() and start > 0 and not item[start - 1].isspace():
        start = m.start()
    name, value = item[:start].strip(" -–—,"), item[start:].strip()
    more = []
    over = VALUE_OVERRUN.search(value)       # «26 x 36 cm (10 x 14 inch) ambalaj până la 150 bucăți» - два параметра
    if over:
        value, more = value[:over.start()].strip(" ,"), split_params(value[over.start():])
        value = re.sub(r"\s+(?:cu|și|si|de|la|în|in)$", "", value)
    q = QUALIFIER.search(" " + name)
    while q and q.start() > 2:              # «termen de utilizare min.» + «24 ore» -> «min. 24 ore»
        name, value = name[:q.start() - 1].strip(" -,"), f"{q.group(1)} {value}"
        q = QUALIFIER.search(" " + name)
    if DIMS.match(value) and not re.search(r"dimensi|mărime|marime|format|size", name, re.IGNORECASE):
        name, rest, cut = "dimensiuni", name, ""    # «Film radiologic cu baza albastra 26 x 36 cm»: размер + тип
    else:
        name, rest, cut = _short_name(name)
    out = [(name, value)]
    if rest:
        rest = re.sub(r"^(?:cu|și|si)\s+", "", rest, flags=re.IGNORECASE)
        head, listed = _plain(rest)
        if listed != "da" and cut.strip(" ,").lower() in ("", "și", "si"):   # «format din A, B și tub de 200cm»
            listed = f"{listed}{cut.rstrip()} {name}".replace(" ,", ",")
        out.append((head, listed))
    return out + more


def split_param(item: str) -> Tuple[str, str]:
    """Главный параметр пункта (у числа) - см. split_params."""
    return split_params(item)[0]


def prefill(model_key: str, offer: Optional[dict], registration: Optional[OfferRegistration] = None,
            record: Optional[RegistryRecord] = None, lot_title: str = "") -> Dict:
    """Поля карточки из того, что уже известно об оферте."""
    offer = offer or {}
    model = "; ".join(offer.get("model", [])) or (registration.model if registration else "") or ""
    producer = "; ".join(offer.get("producer", [])) or (registration.producer if registration else "") or ""
    dms = [d for value in offer.get("dm", []) for d in value.split(",") if d]
    if registration:
        dms += [d for d in registration.dm_numbers if d not in dms]
    spec = "\n".join(offer.get("spec_offered", []))
    params = {}
    for item in spec_items(spec):          # пункты спецификации оферты: «параметр - значение»
        for name, value in split_params(item):
            params.setdefault(name[:80], value)
    name = "; ".join(offer.get("item_name", [])) or (record.name if record else "") or lot_title
    codes = [t for t in model_tokens(model) if len(compact(t)) >= 3]
    return {"model_key": model_key, "producer": producer or (record.producer if record else ""), "model": model,
            "codes": ", ".join(dict.fromkeys(codes)), "dm": ", ".join(dict.fromkeys(dms)), "name": name[:200],
            "params": params, "compatible_with": "", "iso": ", ".join(registration.quality_docs) if registration else ""}


def proper_key(card: Dict) -> Optional[str]:
    """Ключ по полям карточки: производитель + модель (коды) + типоразмер (из ключа или названия)."""
    from src.history.cards import key_size, product_key, size_of
    size = key_size(card.get("model_key")) or size_of(card.get("name"))
    return product_key(card.get("producer"), card.get("codes") or card.get("model"), size)


def rename_key(kb: sqlite3.Connection, old: str, new: str) -> None:
    """Карточка получила новый ключ: решения и вопросы переходят к нему. Уже есть карточка с новым ключом -
    остаётся подтверждённая человеком, номера DM обеих объединяются. У подтверждённой карточки на старом
    ключе - отметка «deleted» (через git старая карточка удалится и на другом ПК); предложенная системой
    просто переезжает - другой ПК переведёт свою сам (src/history/rekey.py)."""
    from src.history.cards import split_dms
    if not old or not new or old == new:
        return
    now = datetime.now().isoformat(timespec="seconds")
    row = kb.execute("SELECT dm, source FROM products WHERE model_key = ?", (old,)).fetchone()
    target = kb.execute("SELECT dm, source FROM products WHERE model_key = ? AND source != 'deleted'",
                        (new,)).fetchone()
    if row and row[1] != "deleted":
        dms = ", ".join(dict.fromkeys(split_dms(row[0]) + split_dms(target[0] if target else "")))
        if target and target[1] == "human" and row[1] != "human":
            kb.execute("DELETE FROM products WHERE model_key = ?", (old,))     # остаётся подтверждённая
        else:
            kb.execute("DELETE FROM products WHERE model_key = ?", (new,))
            kb.execute("UPDATE products SET model_key = ? WHERE model_key = ?", (new, old))
        kb.execute("UPDATE products SET dm = ? WHERE model_key = ?", (dms, new))
        if row[1] == "human":
            kb.execute("INSERT INTO products (model_key, source, updated_at) VALUES (?, 'deleted', ?)", (old, now))
    kb.execute("UPDATE evaluations SET model_key = ? WHERE model_key = ?", (new, old))
    kb.execute("UPDATE OR IGNORE questions SET model_key = ? WHERE model_key = ? AND kind != 'medical_type'", (new, old))
    kb.execute("UPDATE questions SET state = 'answered' WHERE model_key = ? AND kind = 'product_card' AND state = 'open'",
               (old,))


def save_card(kb: sqlite3.Connection, card: Dict, source: str = "human") -> None:
    """Сохраняет карточку (подтверждённую человеком - source = human). Подтверждённую prefill не затирает.
    Человек поправил производителя / модель / размер - карточка переходит на новый ключ (rename_key)."""
    now = datetime.now().isoformat(timespec="seconds")
    if source == "human":
        new = proper_key(card)
        if new and new != card["model_key"]:
            from src.history.cards import split_dms
            rename_key(kb, card["model_key"], new)
            card["model_key"] = new
            row = kb.execute("SELECT dm FROM products WHERE model_key = ?", (new,)).fetchone()
            card["dm"] = ", ".join(dict.fromkeys(split_dms(card.get("dm")) + split_dms(row[0] if row else "")))
    existing = kb.execute("SELECT source FROM products WHERE model_key = ?", (card["model_key"],)).fetchone()
    if existing and existing[0] == "human" and source != "human":
        return
    params = card.get("params") or {}
    kb.execute("INSERT OR REPLACE INTO products (model_key, producer, model, codes, dm, name, params, compatible_with, "
               "iso, agents, winners, source, confirmed_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
               (card["model_key"], card.get("producer"), card.get("model"), card.get("codes"), card.get("dm"),
                card.get("name"), json.dumps(params, ensure_ascii=False) if not isinstance(params, str) else params,
                card.get("compatible_with"), card.get("iso"), card.get("agents"), card.get("winners"), source,
                now if source == "human" else None, now))
    kb.commit()
