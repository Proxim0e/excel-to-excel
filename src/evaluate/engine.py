"""
Движок оценки: result-файл тендера + манифест -> решения (evaluations) и вопросы (questions) в базе знаний.

Оцениваются все участники лота, обход по цене (самый дешёвый - первым). Решение человека, если уже есть
(source = human), не пересчитывается.
"""
import json
import logging
import re
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

from src.amdm.matcher import check_offer as amdm_check, overall_status
from src.amdm.models import OfferRegistration
from src.amdm.workbook import load_workbook_tolerant, read_offers
from src.anexa22.evaluate import participant_key
from src.anexa22.evidence import check_offer
from src.config import PERCENT_THRESHOLD
from src.evaluate import confidence as cf
from src.evaluate.cards import prefill, save_card
from src.evaluate.history import HistoryInfo, lookup
from src.excel.fill_offers import FIRST_PARTICIPANT_COL, ROW_NAME, ROW_SPEC, ROW_VALUE, load_offers
from src.history.cards import model_key as make_model_key
from src.manifest import MANIFEST_NAME

logger = logging.getLogger(__name__)

SHEET_RE = re.compile(r"lot\s*(\d+)", re.IGNORECASE)


@dataclass
class Participant:
    column: int
    name: str
    ratio: Optional[float]               # сумма оферты / база (строка 8 / D8)
    offer: Optional[dict]                # строки Anexa 22 (fill_offers.load_offers)
    registration: Optional[OfferRegistration] = None   # строки 3-4 для AMDM


@dataclass
class Lot:
    tender_id: str
    lot_no: int
    sheet: str
    title: str
    requirement: str
    participants: List[Participant] = field(default_factory=list)


@dataclass
class Evaluation:
    tender_id: str
    lot_no: int
    participant: str
    model_key: Optional[str]
    status: str
    source: str
    confidence: float
    reasons: List[str] = field(default_factory=list)
    reason_code: Optional[str] = None
    findings: Dict = field(default_factory=dict)
    ask_card: bool = False


def tender_id_of(tender_dir: Path) -> str:
    conn = sqlite3.connect(Path(tender_dir) / MANIFEST_NAME)
    try:
        row = conn.execute("SELECT tender_id FROM document_refs WHERE tender_id IS NOT NULL LIMIT 1").fetchone()
    finally:
        conn.close()
    return str(row[0]) if row else Path(tender_dir).name


def read_lots(result_path: Path, tender_dir: Path) -> List[Lot]:
    """Листы лотов result-файла с участниками, их Anexa 22 и строками 3-4."""
    tender_id = tender_id_of(tender_dir)
    conn = sqlite3.connect(Path(tender_dir) / MANIFEST_NAME)
    try:
        offers = load_offers(conn)
    finally:
        conn.close()
    wb = load_workbook_tolerant(result_path, data_only=True)
    registrations = {(o.sheet, o.column): o for o in read_offers(wb)}
    lots = []
    for ws in wb.worksheets:
        m = SHEET_RE.fullmatch(ws.title.strip())
        if not m:
            continue
        lot = Lot(tender_id, int(m.group(1)), ws.title, str(ws.cell(row=1, column=2).value or ""),
                  str(ws.cell(row=ROW_SPEC, column=2).value or ""))
        base = ws.cell(row=ROW_VALUE, column=4).value
        for col in range(FIRST_PARTICIPANT_COL, ws.max_column + 1):
            head = ws.cell(row=ROW_NAME, column=col).value
            if not head or not str(head).lower().startswith("ofertant"):
                continue
            name = str(head).split(":", 1)[1].strip()
            if not name:
                continue
            value = ws.cell(row=ROW_VALUE, column=col).value
            ratio = value / base if isinstance(value, (int, float)) and isinstance(base, (int, float)) and base \
                else None
            lot.participants.append(Participant(col, name, ratio, offers.get((lot.lot_no, participant_key(name))),
                                                registrations.get((ws.title, col))))
        lots.append(lot)
    return lots


def offer_model_key(offer: Optional[dict]) -> Optional[str]:
    if not offer:
        return None
    dms = ",".join(d for value in offer["dm"] for d in value.split(",") if d)
    return make_model_key("; ".join(offer["producer"]), "; ".join(offer["model"]), dms)


def evaluate_participant(lot: Lot, p: Participant, manifest: sqlite3.Connection, kb: sqlite3.Connection,
                         amdm_records: dict, site_checked: set) -> Evaluation:
    key = offer_model_key(p.offer)

    def result(status, source, conf, reasons, code=None, findings=None, ask_card=False):
        return Evaluation(lot.tender_id, lot.lot_no, p.name, key, status, source, conf, reasons, code,
                          findings or {}, ask_card)

    if p.ratio is not None and p.ratio >= PERCENT_THRESHOLD:
        return result(cf.NU_CORESPUNDE, cf.AUTO, 1.0, [f"цена {p.ratio:.0%} базы (≥ 130%)"], "price_130")
    if not p.offer:
        return result(cf.CLARIFICARE, cf.PENDING, 0.1, ["нет Anexa 22 по этому лоту"])

    spec = "\n".join(p.offer["spec_offered"])
    model = "; ".join(p.offer["model"])
    check = check_offer(manifest, p.offer["participant"], lot.lot_no, model, spec, lot.requirement,
                        title=lot.title, offered_extra=" ".join(p.offer.get("item_name", [])))
    findings = {"level1": {"status": check.declarative, "conflicts": check.conflicts,
                           "missing": check.missing_in_offer, "copy": check.copy},
                "level2": {"status": check.status, "has_docs": check.has_docs, "missing": check.missing,
                           "evidence": [asdict(e) for e in check.evidence[:6]]}}
    if check.declarative == "alternativa":
        reasons = [f"≠ cerință: {c}" for c in check.conflicts] + [f"lipsește în A22: {m}" for m in check.missing_in_offer]
        return result(cf.ALTERNATIVA, cf.AUTO, 0.9, reasons, "technical", findings)

    amdm_status, amdm_reasons = None, []
    if p.registration:
        checks = amdm_check(p.registration, amdm_records, site_checked)
        if checks:
            amdm_status = overall_status(checks)
            amdm_reasons = [r for c in checks for r in c.reasons]
    findings["amdm"] = {"status": amdm_status, "reasons": amdm_reasons}

    history = lookup(kb, key, lot.tender_id, lot.requirement, lot.title)
    findings["history"] = {"card": history.card, "outcomes": history.outcomes[:10],
                           "similar_lots": history.similar_lots}
    if not history.card and key:
        dms = p.registration.dm_numbers if p.registration else []
        record = next((amdm_records.get(dm) for dm in dms if amdm_records.get(dm)), None)
        findings["card_prefill"] = prefill(key, p.offer, p.registration, record, lot.title)
    d = cf.decide(check, amdm_status, amdm_reasons, history)
    return result(d.status, d.source, d.confidence, d.reasons, d.reason_code, findings, d.ask_card)


def evaluate_lot(lot: Lot, manifest, kb, amdm_records, site_checked) -> List[Evaluation]:
    """Все участники лота, по цене (без цены - в конце)."""
    order = sorted(lot.participants, key=lambda p: (p.ratio is None, p.ratio or 0))
    return [evaluate_participant(lot, p, manifest, kb, amdm_records, site_checked) for p in order]


def amdm_lookup(lots: List[Lot], offline: bool = True):
    """Записи реестра AMDM по всем номерам DM тендера (локальная копия; offline=False - и сайт)."""
    from src.amdm.__main__ import DEFAULT_DB, collect_records
    from src.amdm.cache import RegistryCache
    numbers = {dm for lot in lots for p in lot.participants if p.registration for dm in p.registration.dm_numbers}
    cache = RegistryCache(Path(DEFAULT_DB))
    try:
        return collect_records(cache, numbers, offline, True)
    finally:
        cache.close()


# --- хранение --------------------------------------------------------------------------------------------

def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def human_decision(kb: sqlite3.Connection, ev: Evaluation) -> Optional[tuple]:
    return kb.execute("SELECT status, reason FROM evaluations WHERE tender_id = ? AND lot_no = ? AND participant = ? "
                      "AND source = 'human'", (ev.tender_id, ev.lot_no, ev.participant)).fetchone()


def save(kb: sqlite3.Connection, ev: Evaluation, lot: Lot) -> Optional[str]:
    """Пишет решение и, если нужно, вопросы. Решение человека не перезаписывается.
    Возвращает тип созданного вопроса (или None)."""
    if human_decision(kb, ev):
        return None
    kb.execute("INSERT OR REPLACE INTO lot_profiles (tender_id, lot_no, title, requirement, conditions) "
               "VALUES (?, ?, ?, ?, NULL)", (lot.tender_id, lot.lot_no, lot.title, lot.requirement))
    kb.execute("INSERT OR REPLACE INTO evaluations (tender_id, lot_no, participant, model_key, status, reason, "
               "confidence, source, findings, decided_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
               (ev.tender_id, ev.lot_no, ev.participant, ev.model_key, ev.status, ev.reason_code, ev.confidence,
                ev.source, json.dumps({"reasons": ev.reasons, **ev.findings}, ensure_ascii=False, default=str),
                _now()))
    created = None
    if ev.source == cf.PENDING:
        payload = {"lot_title": lot.title, "requirement": lot.requirement, "suggested": ev.status,
                   "confidence": ev.confidence, "reasons": ev.reasons, "findings": ev.findings}
        cur = kb.execute("INSERT OR IGNORE INTO questions (kind, tender_id, lot_no, participant, model_key, payload, "
                         "state, created_at) VALUES ('offer_status', ?, ?, ?, ?, ?, 'open', ?)",
                         (ev.tender_id, ev.lot_no, ev.participant, ev.model_key or "",
                          json.dumps(payload, ensure_ascii=False, default=str), _now()))
        created = "offer_status" if cur.rowcount else None
        if ev.ask_card and ev.model_key:
            card = ev.findings.get("card_prefill") or {"model_key": ev.model_key}
            save_card(kb, card, source="prefill")
            card_payload = {"lot_no": ev.lot_no, "participant": ev.participant, "lot_title": lot.title,
                            "card": card}
            cur = kb.execute("INSERT OR IGNORE INTO questions (kind, tender_id, lot_no, participant, model_key, "
                             "payload, state, created_at) VALUES ('product_card', ?, 0, '', ?, ?, 'open', ?)",
                             (ev.tender_id, ev.model_key, json.dumps(card_payload, ensure_ascii=False, default=str),
                              _now()))
            created = created or ("product_card" if cur.rowcount else None)
    kb.commit()
    return created


def describe(ev: Evaluation, asked: Optional[str] = None) -> str:
    """Строка лога по оферте: итог, кто решил и почему (первые причины)."""
    how = {cf.PENDING: "вопрос человеку", "auto": "авто", "human": "решение человека"}.get(ev.source, ev.source)
    if ev.source == cf.PENDING and ev.ask_card:
        how += " + карточка товара"
    why = "; ".join(ev.reasons[:2])
    return f"{ev.status} ({how}){' — ' + why[:180] if why else ''}"


def evaluate_tender(result_path: Path, tender_dir: Path, kb: sqlite3.Connection, offline: bool = True,
                    on_lot: Optional[Callable[[Lot, List[Evaluation]], None]] = None) -> List[Evaluation]:
    """Оценка всего тендера; on_lot(lot, решения) - после каждого лота (окно приложения, прогресс)."""
    lots = read_lots(result_path, tender_dir)
    logger.info(f"[EVAL] Лотов: {len(lots)}, оферт: {sum(len(l.participants) for l in lots)}")
    records, site_checked = amdm_lookup(lots, offline)
    manifest = sqlite3.connect(Path(tender_dir) / MANIFEST_NAME)
    evaluations = []
    try:
        for i, lot in enumerate(lots, start=1):
            evs = evaluate_lot(lot, manifest, kb, records, site_checked)
            for ev in evs:
                asked = save(kb, ev, lot)
                logger.info(f"[EVAL] лот {lot.lot_no} ({i}/{len(lots)}, {100 * i / len(lots):.0f}%), "
                            f"{ev.participant}: {describe(ev, asked)}")
            evaluations.extend(evs)
            if on_lot:
                on_lot(lot, evs)
    finally:
        manifest.close()
    return evaluations
