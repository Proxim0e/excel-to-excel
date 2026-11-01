"""
Карточка модели (паспорт модели из концепта) по базе знаний.

Ключ модели: номер DM, если он есть (однозначен и связан с реестром AMDM); иначе производитель +
коды модели (без регистра, пробелов и дефисов), как при сверке с реестром. Набор из нескольких DM -
все номера через «+».

Карточка: где и кем модель предлагалась, цены за единицу (мин / медиана / макс), итоги и причины,
пометки человека «//» из строки 11, уточнения, запись реестра AMDM из локальной копии.
Учитывается текущее состояние (последний снимок + ответы человека); «excluded» в сводку не входит.
"""
import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from src.amdm.matcher import norm_company
from src.amdm.parse import compact, model_tokens

REGISTRY_DB = Path(__file__).resolve().parents[2] / "resources" / "amdm_registry.sqlite"
OFFER_COLUMNS = ("tender_id", "lot_no", "sheet", "participant", "producer", "country", "model", "dm",
                 "unit_price", "price_ratio", "final_decision", "final_reason", "decision_source",
                 "spec_notes", "row10", "answer_comment")


def _glue(text: str) -> str:
    """«F 4141» -> «F4141»: текст PDF разрывает коды пробелом после буквы-префикса."""
    return re.sub(r"\b([A-Za-z]{1,2})\s+(?=\d)", r"\1", text or "")


def model_key(producer: Optional[str], model: Optional[str], dm: Optional[str]) -> Optional[str]:
    dms = sorted({d for d in (dm or "").split(",") if d})
    if dms:
        return "DM:" + "+".join(dms)
    if not (producer or model):
        return None
    codes = [compact(t) for t in model_tokens(_glue(model)) if len(compact(t)) >= 3]
    body = "+".join(dict.fromkeys(codes)) or compact(model or "")[:40]
    return f"M:{norm_company(producer) or '?'}|{body}"


@dataclass
class Card:
    key: str
    offers: List[dict] = field(default_factory=list)
    registry: List[dict] = field(default_factory=list)

    @property
    def counted(self) -> List[dict]:
        return [o for o in self.offers if o["final_decision"] != "excluded"]

    def names(self, field_name: str) -> List[str]:
        return list(dict.fromkeys(o[field_name] for o in self.offers if o[field_name]))

    def prices(self) -> List[float]:
        return [o["unit_price"] for o in self.counted if isinstance(o["unit_price"], (int, float)) and o["unit_price"] > 0]

    def summary(self) -> dict:
        prices = self.prices()
        decisions = Counter(o["final_decision"] for o in self.counted)
        reasons = Counter(o["final_reason"] for o in self.counted if o["final_reason"])
        return {
            "key": self.key,
            "producer": "; ".join(self.names("producer")[:3]),
            "model": "; ".join(self.names("model")[:3]),
            "dm": "; ".join(self.names("dm")[:3]),
            "tenders": len({o["tender_id"] for o in self.offers}),
            "offers": len(self.offers),
            "participants": "; ".join(f"{p} ({n})" for p, n in Counter(o["participant"] for o in self.offers).most_common()),
            "price_min": min(prices) if prices else None,
            "price_median": statistics.median(prices) if prices else None,
            "price_max": max(prices) if prices else None,
            "accepted": decisions.get("accepted", 0),
            "rejected": decisions.get("rejected", 0),
            "not_evaluated": decisions.get("not_evaluated", 0),
            "other": sum(n for d, n in decisions.items() if d not in ("accepted", "rejected", "not_evaluated")),
            "reasons": "; ".join(f"{r} ({n})" for r, n in reasons.most_common()),
            "last_tender": max((o["tender_id"] for o in self.offers), default=None),
            "registry": "; ".join(f"{r['dm']}: {r['producer']} / {r['model'] or r['catalog'] or r['name']} / "
                                  f"{r['order']} {r['order_date']}" for r in self.registry),
        }


def _anexa22_models(downloads: Path, tender_id: str) -> Dict[tuple, dict]:
    """(лот, ключ участника) -> производитель / страна / модель / DM из разбора Anexa 22 (манифест тендера)."""
    import sqlite3

    from src.anexa22.evaluate import participant_key

    manifest = Path(downloads) / str(tender_id) / "manifest.sqlite"
    if not manifest.is_file() or manifest.stat().st_size == 0:
        return {}
    conn = sqlite3.connect(f"file:{manifest}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT lot_no, participant, producer, country, model, dm FROM anexa22_offers "
                            "WHERE lot_no IS NOT NULL ORDER BY id").fetchall()
    except sqlite3.OperationalError:
        return {}
    finally:
        conn.close()
    result: Dict[tuple, dict] = {}
    for lot, who, producer, country, model, dm in rows:
        result.setdefault((lot, participant_key(who)),
                          {"producer": producer, "country": country, "model": model, "dm": dm or None})
    return result


def build_cards(conn, registry_db: Path = REGISTRY_DB, downloads: Optional[Path] = None) -> Dict[str, Card]:
    """Карточки по текущему состоянию базы знаний. Человек заполняет строки 3-4 только у тех, кого
    оценивает: у остальных участников модель и DM берутся из разбора Anexa 22 (манифест тендера),
    если он есть, - тогда в карточке видны цены всех конкурентов (model_source = anexa22)."""
    from src.anexa22.evaluate import participant_key
    from src.config import DOWNLOAD_DIR

    downloads = Path(downloads or DOWNLOAD_DIR)
    rows = conn.execute(f"SELECT {', '.join(OFFER_COLUMNS)} FROM current_offers").fetchall()
    cards: Dict[str, Card] = {}
    parsed: Dict[str, Dict[tuple, dict]] = {}
    for row in rows:
        offer = dict(zip(OFFER_COLUMNS, row))
        offer["model_source"] = "excel"
        if not (offer["model"] or offer["dm"]):
            tender = offer["tender_id"]
            if tender not in parsed:
                parsed[tender] = _anexa22_models(downloads, tender)
            found = parsed[tender].get((offer["lot_no"], participant_key(offer["participant"])))
            if found:
                offer.update({k: v for k, v in found.items() if v and not offer.get(k)})
                offer["model_source"] = "anexa22"
        key = model_key(offer["producer"], offer["model"], offer["dm"])
        if key:
            cards.setdefault(key, Card(key)).offers.append(offer)
    _attach_registry(cards, registry_db)
    _merge_into_dm_cards(cards)
    return cards


def _codes(card: Card) -> set:
    texts = card.names("model") + [r["model"] or "" for r in card.registry] + [r["catalog"] or "" for r in card.registry]
    return {compact(t) for text in texts for t in model_tokens(_glue(text)) if len(compact(t)) >= 4}


def _merge_into_dm_cards(cards: Dict[str, Card]) -> None:
    """Оферты без DM той же модели (общий код модели и тот же производитель) - в карточку с DM:
    в лоте 29 четыре участника предлагали FIAB F4141, но номер DM указали не все."""
    from src.amdm.matcher import producer_matches

    dm_cards = [c for c in cards.values() if c.key.startswith("DM:")]
    index = defaultdict(list)
    for c in dm_cards:
        for code in _codes(c):
            index[code].append(c)
    for key in [k for k in cards if k.startswith("M:")]:
        card = cards[key]
        producers = card.names("producer")
        for code in _codes(card):
            target = next((t for t in index.get(code, [])
                           if any(producer_matches(p, q)[0] for p in producers
                                  for q in t.names("producer") + [r["producer"] for r in t.registry])), None)
            if target:
                target.offers.extend(card.offers)
                del cards[key]
                break


def _attach_registry(cards: Dict[str, Card], registry_db: Path) -> None:
    if not Path(registry_db).is_file():
        return
    import sqlite3
    reg = sqlite3.connect(f"file:{registry_db}?mode=ro", uri=True)
    try:
        cols = ["dm", "producer", "model", "catalog", "name", "order", "order_date"]
        select = ", ".join('"' + c + '"' for c in cols)  # «order» - служебное слово SQL
        for card in cards.values():
            if not card.key.startswith("DM:"):
                continue
            for dm in card.key[3:].split("+"):
                r = reg.execute(f"SELECT {select} FROM amdm_record WHERE dm = ?", (dm,)).fetchone()
                if r:
                    card.registry.append(dict(zip(cols, r)))
    finally:
        reg.close()


def find_cards(cards: Dict[str, Card], query: str) -> List[Card]:
    """По номеру DM, коду модели или производителю (без регистра, пробелов и дефисов)."""
    q = compact(query)
    found = []
    for card in cards.values():
        haystack = compact(" ".join([card.key] + card.names("model") + card.names("producer") + card.names("dm")
                                    + [r["catalog"] or "" for r in card.registry]))
        if q and q in haystack:
            found.append(card)
    return sorted(found, key=lambda c: -len(c.offers))


def format_card(card: Card) -> str:
    s = card.summary()
    money = lambda v: f"{v:,.2f}".replace(",", " ") if isinstance(v, (int, float)) else "-"
    lines = [f"== {s['key']}",
             f"Производитель: {s['producer'] or '-'}   Модель: {s['model'] or '-'}",
             f"Реестр AMDM: {s['registry'] or '-'}",
             f"Тендеров: {s['tenders']}, оферт: {s['offers']}; участники: {s['participants']}",
             f"Цена за единицу: мин {money(s['price_min'])}, медиана {money(s['price_median'])}, "
             f"макс {money(s['price_max'])}",
             f"Итоги: принят {s['accepted']}, отклонён {s['rejected']}, не оценивался {s['not_evaluated']}, "
             f"другое {s['other']}" + (f"; причины: {s['reasons']}" if s["reasons"] else ""),
             "Оферты:"]
    for o in sorted(card.offers, key=lambda o: (o["tender_id"], o["lot_no"])):
        note = " ".join(x for x in (o["spec_notes"], o["row10"], o["answer_comment"]) if x)
        source = " [A22]" if o.get("model_source") == "anexa22" else ""
        lines.append(f"  {o['tender_id']} лот {o['lot_no']:<4} {o['participant'][:26]:26} "
                     f"{money(o['unit_price']):>12}  {o['final_decision']}{source}"
                     + (f" ({o['final_reason']})" if o["final_reason"] else "")
                     + (f"  {note[:90]}" if note else ""))
    return "\n".join(lines)


SUMMARY_HEADER = [("key", "Ключ модели"), ("producer", "Производитель"), ("model", "Модель"), ("dm", "DM"),
                  ("registry", "Реестр AMDM"), ("tenders", "Тендеров"), ("offers", "Оферт"),
                  ("participants", "Участники"), ("price_min", "Цена мин"), ("price_median", "Цена медиана"),
                  ("price_max", "Цена макс"), ("accepted", "Принят"), ("rejected", "Отклонён"),
                  ("not_evaluated", "Не оценивался"), ("other", "Другое"), ("reasons", "Причины отказа"),
                  ("last_tender", "Последний тендер")]
OFFER_HEADER = ["Ключ модели"] + ["Тендер", "Лот", "Лист", "Участник", "Производитель", "Страна", "Модель", "DM",
                                  "Цена за ед.", "Цена / база", "Решение", "Причина", "Источник решения",
                                  "Пометки //", "Строка 10", "Комментарий к ответу", "Модель из"]


def export_catalog(cards: Dict[str, Card], path: Path) -> int:
    from openpyxl import Workbook
    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
    from openpyxl.styles import Font

    def safe(v):
        return ILLEGAL_CHARACTERS_RE.sub("", v) if isinstance(v, str) else v

    wb = Workbook()
    ws = wb.active
    ws.title = "Модели"
    ws.append([title for _, title in SUMMARY_HEADER])
    for card in sorted(cards.values(), key=lambda c: (-len(c.offers), c.key)):
        s = card.summary()
        ws.append([safe(s[k]) for k, _ in SUMMARY_HEADER])
    offers_ws = wb.create_sheet("Оферты")
    offers_ws.append(OFFER_HEADER)
    for card in cards.values():
        for o in sorted(card.offers, key=lambda o: (o["tender_id"], o["lot_no"])):
            offers_ws.append([card.key] + [safe(o[c]) for c in OFFER_COLUMNS] + [o["model_source"]])
    for sheet in (ws, offers_ws):
        for cell in sheet[1]:
            cell.font = Font(bold=True)
        sheet.freeze_panes = "B2"
        sheet.auto_filter.ref = sheet.dimensions
        for col in sheet.columns:
            sheet.column_dimensions[col[0].column_letter].width = min(
                45, max(10, max(len(str(c.value or "")) for c in col[:200]) * 0.9))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return len(cards)
