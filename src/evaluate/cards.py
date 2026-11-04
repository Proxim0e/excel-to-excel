"""
Карточка товара: предзаполнение из Anexa 22, строк 3-4 и реестра AMDM; сохранение после подтверждения.

Предзаполненная карточка (source = prefill) - это предложение системы; в оценке «высокой уверенности»
участвует только подтверждённая человеком (source = human).
"""
import json
import sqlite3
from datetime import datetime
from typing import Dict, Optional

from src.amdm.models import OfferRegistration, RegistryRecord
from src.amdm.parse import compact, model_tokens
from src.anexa22.requirements import parse_constraints

CARD_FIELDS = ("producer", "model", "codes", "dm", "name", "params", "compatible_with", "iso")


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
    for c in parse_constraints(spec):
        label = c.text.split(":")[0].strip(" -")[:40] or c.family
        params.setdefault(label, c.text)
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
               "iso, source, confirmed_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
               (card["model_key"], card.get("producer"), card.get("model"), card.get("codes"), card.get("dm"),
                card.get("name"), json.dumps(params, ensure_ascii=False) if not isinstance(params, str) else params,
                card.get("compatible_with"), card.get("iso"), source, now if source == "human" else None, now))
    kb.commit()
