"""
Документы заказчика тендера с MTender вместо ручного «родительского» файла.

Страница тендера на achizitii.md ссылается на MTender (ocds-b3wdp1-MD-…); публичный API MTender отдаёт
тендер целиком, в том числе документы заказчика:
  anunț de participare (PDF)    - объявление: сроки, условия;
  formularele.xlsx              - «Specificaţii tehnice» (лот, позиция, требуемая спецификация) и
                                  «Specificaţii de preț» (единица, количество, valoarea estimativă);
  lista de distribuție.xlsx     - количества по бенефициарам (больницам).
Из formularele.xlsx собираются лоты в том же виде, что из родительского файла (src/excel/reader.collect_lots),
поэтому main.py может работать без ручной подготовки Excel.

    python -m src.tender_docs <URL тендера или номер>        - скачать документы и показать лоты
"""
import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from openpyxl import load_workbook

from src.config import DOWNLOAD_DIR
from src.excel.reader import collect_lots

logger = logging.getLogger(__name__)

ACHIZITII_URL = "https://achizitii.md/ro/public/tender/{}/"
MTENDER_API = "https://public.mtender.gov.md/tenders/{}"
OCID_RE = re.compile(r"ocds-[a-z0-9]+-MD-\d+")
DOCS_DIR = "_tender"
# Документ заказчика -> признак в названии
WANTED = {"formulare": r"formular", "distributie": r"lista de distribu", "anunt": r"anun[tț] de participare"}
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36"}


def tender_id(tender: str) -> str:
    m = re.search(r"tender/(\d+)", tender) or re.fullmatch(r"\s*(\d+)\s*", tender)
    if not m:
        raise ValueError(f"Не понял номер тендера: {tender}")
    return m.group(1)


def find_ocid(tid: str) -> str:
    """MTender ID тендера со страницы achizitii.md."""
    html = requests.get(ACHIZITII_URL.format(tid), headers=UA, timeout=60).text
    m = OCID_RE.search(html)
    if not m:
        raise RuntimeError(f"На странице тендера {tid} нет ссылки на MTender")
    return m.group(0)


def fetch_tender_docs(tender: str, out_dir: Optional[Path] = None) -> Dict[str, Path]:
    """Скачивает JSON тендера с MTender и документы заказчика; уже скачанные не качает заново.
    Возвращает {'ocds': путь, 'formulare': путь, ...}."""
    tid = tender_id(tender)
    out = Path(out_dir or DOWNLOAD_DIR / tid / DOCS_DIR)
    out.mkdir(parents=True, exist_ok=True)
    ocds_path = out / "ocds.json"
    if not ocds_path.exists():
        ocid = find_ocid(tid)
        r = requests.get(MTENDER_API.format(ocid), headers=UA, timeout=120)
        r.raise_for_status()
        ocds_path.write_bytes(r.content)
        logger.info(f"[TENDER] MTender {ocid}: {len(r.content) // 1024} КБ")
    found = {"ocds": ocds_path}
    data = json.loads(ocds_path.read_text(encoding="utf-8"))
    for doc in _tender_documents(data):
        title = doc.get("title") or ""
        for key, rx in WANTED.items():
            if key in found or not re.search(rx, title, re.IGNORECASE):
                continue
            path = out / title
            if not path.exists():
                url = (doc.get("url") or "").replace("http://", "https://")
                b = requests.get(url, headers=UA, timeout=180)
                b.raise_for_status()
                path.write_bytes(b.content)
                logger.info(f"[TENDER] Скачан {title} ({len(b.content) // 1024} КБ)")
            found[key] = path
    missing = [k for k in WANTED if k not in found]
    if missing:
        logger.warning(f"[TENDER] Среди документов заказчика не найдены: {', '.join(missing)}")
    return found


def _tender_documents(data: dict) -> List[dict]:
    docs = []
    for rec in data.get("records", []):
        docs += rec.get("compiledRelease", {}).get("tender", {}).get("documents", [])
    return docs


def _header_row(ws, must: str, max_rows: int = 15):
    """(номер строки, {заголовок в нижнем регистре: номер колонки}) - строка, где есть колонка `must`."""
    for i, row in enumerate(ws.iter_rows(min_row=1, max_row=max_rows, values_only=True), start=1):
        heads = {re.sub(r"\s+", " ", str(v)).strip().lower(): j for j, v in enumerate(row, start=1) if v}
        if any(must in h for h in heads):
            return i, heads
    raise ValueError(f"На листе «{ws.title}» не найдена шапка с «{must}»")


def _col(heads: Dict[str, int], *keys: str) -> Optional[int]:
    for key in keys:
        for h, j in heads.items():
            if key in h:
                return j
    return None


def _sheet(wb, key: str):
    for ws in wb.worksheets:
        if key in ws.title.lower():
            return ws
    raise ValueError(f"В formularele.xlsx нет листа «{key}»")


def lots_from_formulare(path: Path) -> Dict[int, Dict[str, Any]]:
    """Лоты из formularele.xlsx: название и спецификация - «Specificaţii tehnice», единица, количество и
    valoarea estimativă - «Specificaţii de preț». Позиции листов сопоставляются по порядку внутри лота.
    Сумма лота из нескольких позиций - сумма позиций."""
    wb = load_workbook(path, read_only=True, data_only=True)
    tech = _sheet(wb, "tehnic")
    price = _sheet(wb, "pre")
    t_row, t_heads = _header_row(tech, "nr. lot")
    p_row, p_heads = _header_row(price, "nr. lot")
    t_lot, t_name = _col(t_heads, "nr. lot"), _col(t_heads, "denumire lot")
    t_pos, t_spec = _col(t_heads, "denumirea pozi"), _col(t_heads, "specificarea tehnic")
    p_lot, p_um = _col(p_heads, "nr. lot"), _col(p_heads, "unitatea de m")
    p_qty, p_value = _col(p_heads, "cantitatea"), _col(p_heads, "valoarea estimativ")

    def lot_of(value):
        try:
            return int(float(str(value).strip()))
        except (TypeError, ValueError):
            return None

    prices: Dict[int, List[tuple]] = {}
    for row in price.iter_rows(min_row=p_row + 1, values_only=True):
        lot = lot_of(row[p_lot - 1])
        if lot is not None:
            prices.setdefault(lot, []).append((row[p_um - 1], row[p_qty - 1], row[p_value - 1] if p_value else None))

    rows, seen = [], {}
    for row in tech.iter_rows(min_row=t_row + 1, values_only=True):
        lot = lot_of(row[t_lot - 1])
        if lot is None:
            continue
        k = seen.get(lot, 0)
        seen[lot] = k + 1
        um, qty, value = (prices.get(lot) or [])[k] if k < len(prices.get(lot) or []) else (None, None, None)
        name = row[t_name - 1] or (row[t_pos - 1] if t_pos else None)
        rows.append((lot, name, row[t_spec - 1], um, qty, value))
    wb.close()

    cols = {"nr_lot": 1, "denumire": 2, "specificatie": 3, "unitate_masura": 4, "cantitate_total": 5,
            "suma_alocata": 6}
    lots = collect_lots(rows, cols)
    totals: Dict[int, float] = {}
    for lot, *_, value in rows:
        try:
            totals[lot] = totals.get(lot, 0.0) + float(value)
        except (TypeError, ValueError):
            pass
    for lot, data in lots.items():
        if data.get("positions") and lot in totals:
            data["suma_alocata"] = totals[lot]
    return lots


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] %(message)s", force=True)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tender", help="URL тендера на achizitii.md или его номер")
    args = ap.parse_args(argv)
    docs = fetch_tender_docs(args.tender)
    if "formulare" not in docs:
        return 1
    lots = lots_from_formulare(docs["formulare"])
    total = sum(float(d.get("suma_alocata") or 0) for d in lots.values())
    print(f"Лотов: {len(lots)}, сумма valoarea estimativă: {total:,.2f}")
    for lot in sorted(lots)[:5]:
        d = lots[lot]
        print(f"  {lot}: {str(d['denumire'])[:60]} | {d['cantitate_total']} {d['unitate_masura']} | {d['suma_alocata']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
