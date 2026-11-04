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


def spec_items(text: str) -> List[str]:
    """Пункты текста требования/спецификации: «1.diametru 38 - 47mm, 2. electrod Ag/AgCl» -> два пункта.
    Без нумерации - по строкам и «;»; запятая делит только перед словом («1,5 mm» не режется)."""
    text = (text or "").split("//")[0].strip()
    if not text:
        return []
    marks = list(NUMBERED.finditer(text))
    numbers = [int(m.group(1)) for m in marks]
    if len(marks) >= 2 and numbers[:2] == [numbers[0], numbers[0] + 1]:
        parts = [text[a.end():(b.start() if b else len(text))] for a, b in zip(marks, marks[1:] + [None])]
        head = text[:marks[0].start()]
        parts = ([head] if len(head.strip()) > 3 else []) + parts
    else:
        parts = re.split(r"[\n;]+|,\s+(?=[^\W\d_])", text)
    return [p.strip(" \t\r\n,;.-") for p in parts if len(p.strip(" \t\r\n,;.-")) > 1]


def split_param(item: str) -> Tuple[str, str]:
    """Пункт -> (параметр, значение): «Lungime: ≥ 15 cm» -> (Lungime, ≥ 15 cm); «diametru 38 - 47mm» ->
    (diametru, 38 - 47mm); без чисел - весь пункт параметром со значением «да» («electrod Ag/AgCl»)."""
    item = re.sub(r"\s+", " ", item).strip()
    if ":" in item:
        name, value = item.split(":", 1)
        if name.strip() and value.strip():
            return name.strip(), value.strip()
    m = re.search(r"[\d≥≤<>±]", item)
    if m and m.start() > 2:
        name, value = item[:m.start()].strip(" -,"), item[m.start():].strip()
        q = re.search(r"\s(min(?:im)?\.?|max(?:im)?\.?|de|cel puțin|pana la|până la)$", name, re.IGNORECASE)
        if q and q.start() > 2:                     # «termen de utilizare min.» + «24 ore» -> «min. 24 ore»
            name, value = name[:q.start()].strip(), f"{q.group(1)} {value}"
        return name, value
    return item, "da"


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
        name, value = split_param(item)
        params.setdefault(name[:80], value)
    name = "; ".join(offer.get("item_name", [])) or (record.name if record else "") or lot_title
    codes = [t for t in model_tokens(model) if len(compact(t)) >= 3]
    return {"model_key": model_key, "producer": producer or (record.producer if record else ""), "model": model,
            "codes": ", ".join(dict.fromkeys(codes)), "dm": ", ".join(dict.fromkeys(dms)), "name": name[:200],
            "params": params, "compatible_with": "", "iso": ", ".join(registration.quality_docs) if registration else ""}


def save_card(kb: sqlite3.Connection, card: Dict, source: str = "human") -> None:
    """Сохраняет карточку (подтверждённую человеком - source = human). Подтверждённую prefill не затирает."""
    now = datetime.now().isoformat(timespec="seconds")
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
