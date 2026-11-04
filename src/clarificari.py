"""
Раунды уточнений (clarificări): перепроверка жёлтых оферт по документам, которые участники досылают.

Папка уточнений задаётся при запуске, внутри - по папке на компанию:
    <папка>/<Компания>/*.pdf
Название папки сопоставляется с участником тендера («Electeh» -> «Electeh SRL»).

Что делается:
  1. Документы попадают в манифест тендера (status = 'clarificare', url = 'clarificari/runda N/<папка>/<файл>'),
     классифицируются и читаются как обычные; обычная проверка (этап 3) их не видит.
  2. Для каждого лота, где у этой компании жёлтая строка 11, проверка повторяется с новыми
     документами: все их страницы (ответ часто приходит письмом, а не даташитом) участвуют в поиске
     кода модели и параметров и могут закрыть условия «≠ cerință» / «lipsește în A22».
  3. Справа от участника вставляется колонка «clarificări (runda N)»: строка 4 - присланные документы,
     строка 11 - что нашлось в них и что всё ещё не найдено, цвет - итог перепроверки.
     Строка 1 участника и вкладка листа перекрашиваются в новый итог.
Повторный запуск того же раунда колонку не дублирует.

Номер раунда: файлы этой папки уже были - их раунд; иначе следующий после последнего.

Запуск:  python -m src.clarificari <result.xlsx> <папка тендера> --dir <папка уточнений> [--round N] [--out файл]
"""
import argparse
import difflib
import logging
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional

from openpyxl import load_workbook
from openpyxl.styles import Alignment, PatternFill

from src.anexa22.evaluate import participant_key
from src.anexa22.evidence import GREEN, YELLOW, check_offer
from src.classify import classify_tender
from src.excel.columns import insert_column
from src.excel.fill_offers import (COLORS, FIRST_PARTICIPANT_COL, KEEP_TAB_COLORS, ROW_DM, ROW_NAME,
                                   ROW_PRODUCER, ROW_SPEC, ROW_STATUS, SHEET_RE, SPEC_ALIGNMENT,
                                   TAB_PRIORITY, TEMPLATE_STATUS_FILLS, load_offers)
from src.manifest import MANIFEST_NAME, Manifest

logger = logging.getLogger(__name__)

STATUS = "clarificare"
ROUND_URL = "clarificari/runda {}"
COLUMN_LABEL = "clarificări (runda {})"
NAME_MATCH_RATIO = 0.8
NOTHING_FOUND = "// clarificări: confirmare negăsită"
OUR_FILLS = {c.upper() for c in COLORS.values()} | {str(c).upper() for c in TEMPLATE_STATUS_FILLS}


def match_participant(folder: str, participants: List[str]) -> Optional[str]:
    """Участник по названию папки: без форм собственности и кавычек, вхождение или похожесть."""
    key = participant_key(folder)
    if not key:
        return None
    keys = {p: participant_key(p) for p in participants}
    exact = [p for p, k in keys.items() if k == key]
    if exact:
        return exact[0]
    contains = [p for p, k in keys.items() if k and (key in k or k in key)]
    if len(contains) == 1:
        return contains[0]
    best = max(participants, key=lambda p: difflib.SequenceMatcher(None, key, keys[p]).ratio(), default=None)
    if best and difflib.SequenceMatcher(None, key, keys[best]).ratio() >= NAME_MATCH_RATIO:
        return best
    return None


def _round_of(conn: sqlite3.Connection, clar_dir: Path) -> int:
    """Раунд этой папки, если её файлы уже загружались; иначе следующий."""
    prefix = str(Path(clar_dir).resolve())
    rows = conn.execute("SELECT DISTINCT url, path FROM document_refs WHERE status = ?", (STATUS,)).fetchall()
    rounds = {}
    for url, path in rows:
        m = re.search(r"runda (\d+)", url or "")
        if m:
            rounds.setdefault(int(m.group(1)), []).append(path)
    for n, paths in rounds.items():
        if any(str(p).startswith(prefix) for p in paths):
            return n
    return max(rounds, default=0) + 1


def ingest(tender_dir: Path, clar_dir: Path, round_no: Optional[int] = None) -> dict:
    """Документы уточнений -> манифест. Возвращает {участник: [имена файлов]}, раунд и несопоставленные папки."""
    tender_dir, clar_dir = Path(tender_dir), Path(clar_dir).resolve()
    conn = sqlite3.connect(tender_dir / MANIFEST_NAME)
    try:
        participants = [r[0] for r in conn.execute(
            "SELECT DISTINCT participant FROM document_refs WHERE status <> ? AND participant IS NOT NULL", (STATUS,))]
        tender_id = (conn.execute("SELECT tender_id FROM document_refs LIMIT 1").fetchone() or [tender_dir.name])[0]
        round_no = round_no or _round_of(conn, clar_dir)
    finally:
        conn.close()

    files: Dict[str, List[str]] = defaultdict(list)
    unmatched = []
    manifest = Manifest(tender_dir / MANIFEST_NAME)
    try:
        for folder in sorted(p for p in clar_dir.iterdir() if p.is_dir()):
            participant = match_participant(folder.name, participants)
            if not participant:
                unmatched.append(folder.name)
                continue
            for path in sorted(folder.rglob("*.pdf")):
                # url у каждого файла свой: манифест кэширует sha256 по url, с общим url раунда все файлы
                # получили бы хеш первого и читался бы только он
                url = f"{ROUND_URL.format(round_no)}/{path.relative_to(clar_dir).as_posix()}"
                manifest.record(tender_id=tender_id, lot_no=None, lot_title=None, participant=participant,
                                original_name=path.name, url=url, path=path, status=STATUS)
                files[participant].append(path.name)
    finally:
        manifest.close()
    classify_tender(tender_dir)  # новые документы: текст страниц и тип
    return {"round": round_no, "files": dict(files), "unmatched": unmatched}


# «(?<![a-z])», а не «\b»: в именах файлов перед «Lot» стоит «_» («202607301735_Lot 29,35»)
LOT_LIST_RE = re.compile(r"(?<![a-z])lot(?:ul|urile|urilor|uri)?\s*(?:nr\.?|№)?\s*((?:\d{1,3}\s*(?:,|;|/|si|și|şi|and|-)?\s*)+)",
                         re.IGNORECASE)


def mentioned_lots(text: str) -> set:
    """Номера лотов из текста или имени файла: «Lot 29,35,215,216», «lotul 75», «loturile 3 și 4»."""
    lots = set()
    for m in LOT_LIST_RE.finditer(text or ""):
        lots.update(int(n) for n in re.findall(r"\d{1,3}", m.group(1)))
    return lots


def relevant_pages(pages, lot: int, model: str):
    """Страницы документов уточнений, относящихся к лоту: в документе (или имени файла) есть код модели
    оферты или номер лота. Иначе случайное число из чужого каталога «подтвердило» бы параметр."""
    from src.anexa22.evidence import _alnum, model_tokens

    codes = [_alnum(c) for c in model_tokens(model)]
    by_doc: Dict[str, list] = defaultdict(list)
    for page in pages:
        by_doc[page[0]].append(page)
    result = []
    for doc_pages in by_doc.values():
        name = doc_pages[0][1]
        text = " ".join(p[3] for p in doc_pages)
        if lot in mentioned_lots(name) or lot in mentioned_lots(text) or \
                any(c and c in _alnum(text) for c in codes):
            result.extend(doc_pages)
    return result


def clarification_pages(conn: sqlite3.Connection, participant: str, round_no: int):
    """Все страницы документов уточнений участника в раунде: [(sha, имя, страница, текст)]."""
    return conn.execute("""
        SELECT p.sha256, MIN(r.original_name), p.page_no, p.text
        FROM document_refs r JOIN document_pages p USING (sha256)
        WHERE r.participant = ? AND r.status = ? AND (r.url = ? OR r.url LIKE ?)
        GROUP BY p.sha256, p.page_no ORDER BY MIN(r.original_name), p.page_no""",
                        (participant, STATUS, ROUND_URL.format(round_no), ROUND_URL.format(round_no) + "/%")).fetchall()


def _fill_rgb(cell) -> Optional[str]:
    if cell.fill is None or cell.fill.fill_type != "solid":
        return None
    return str(cell.fill.fgColor.rgb).upper()


def _clar_columns(ws, col: int) -> List[int]:
    """Колонки уточнений справа от участника: без «Ofertant» в строке 2, «clarificări» в строке 3."""
    cols, c = [], col + 1
    while c <= ws.max_column and not ws.cell(row=ROW_NAME, column=c).value \
            and str(ws.cell(row=ROW_PRODUCER, column=c).value or "").lower().startswith("clarific"):
        cols.append(c)
        c += 1
    return cols


def _recolor_tab(ws) -> None:
    tab = ws.sheet_properties.tabColor
    if tab is not None and str(tab.rgb).upper() in KEEP_TAB_COLORS:
        return
    statuses = []
    for col in range(FIRST_PARTICIPANT_COL, ws.max_column + 1):
        if str(ws.cell(row=ROW_NAME, column=col).value or "").lower().startswith("ofertant"):
            rgb = _fill_rgb(ws.cell(row=ROW_STATUS, column=col))
            statuses += [s for s, c in COLORS.items() if c.upper() == rgb]
    if statuses:
        ws.sheet_properties.tabColor = COLORS[next(s for s in TAB_PRIORITY if s in statuses)]


def recheck_result(result_path: Path, tender_dir: Path, clar_dir: Path, round_no: Optional[int] = None,
                   out_path: Optional[Path] = None) -> dict:
    """Загружает уточнения и перепроверяет жёлтые оферты; результат - колонки «clarificări»."""
    info = ingest(tender_dir, clar_dir, round_no)
    round_no = info["round"]
    stats = defaultdict(int, {"round": round_no, "unmatched": info["unmatched"],
                              "companies": len(info["files"]), "files": sum(map(len, info["files"].values()))})
    by_key = {participant_key(p): p for p in info["files"]}
    logger.info(f"[CLAR] Раунд {round_no}: папка {clar_dir}, компаний {stats['companies']}, файлов {stats['files']}")
    for participant, names in sorted(info["files"].items()):
        logger.info(f"[CLAR]   {participant}: {len(names)} файл(ов)")
    if info["unmatched"]:
        logger.warning(f"[CLAR] Папки без участника тендера: {', '.join(info['unmatched'])}")

    conn = sqlite3.connect(Path(tender_dir) / MANIFEST_NAME)
    offers = load_offers(conn)
    pages_cache = {p: clarification_pages(conn, p, round_no) for p in info["files"]}
    wb = load_workbook(result_path)
    label = COLUMN_LABEL.format(round_no)
    try:
        total = len(wb.worksheets)
        for n, ws in enumerate(wb.worksheets, start=1):
            m = SHEET_RE.fullmatch(ws.title.strip())
            if not m:
                continue
            lot = int(m.group(1))
            requirement = str(ws.cell(row=ROW_SPEC, column=2).value or "")
            title = str(ws.cell(row=1, column=2).value or "")  # B1: коды аппарата для совместимости
            changed = False
            # справа налево: вставка колонки не сдвигает ещё не обработанных участников
            for col in range(ws.max_column, FIRST_PARTICIPANT_COL - 1, -1):
                name = str(ws.cell(row=ROW_NAME, column=col).value or "")
                if not name.lower().startswith("ofertant"):
                    continue
                participant = by_key.get(participant_key(name.split(":", 1)[1]))
                if not participant:
                    continue
                existing = _clar_columns(ws, col)
                if any(ws.cell(row=ROW_PRODUCER, column=c).value == label for c in existing):
                    stats["already"] += 1
                    continue
                if _fill_rgb(ws.cell(row=ROW_SPEC, column=col)) != COLORS[YELLOW].upper():
                    continue  # перепроверяются только жёлтые
                rec = offers.get((lot, participant_key(participant)))
                if not rec:
                    continue
                model = "; ".join(rec["model"])
                pages = relevant_pages(pages_cache[participant], lot, model)
                check = check_offer(conn, rec["participant"], lot, model,
                                    "\n".join(rec["spec_offered"]), requirement, extra_pages=pages,
                                    title=title, offered_extra=" ".join(rec.get("item_name", [])))
                docs = sorted({n for _, n, _, _ in pages})
                note = check.note(only_documents=set(docs))
                if not any(e.document in docs for e in check.evidence):
                    # ничего нового: итог прежний - жёлтый
                    note = f"{NOTHING_FOUND}\n{note}".strip()
                    check.status = YELLOW
                docs = docs or sorted({n for _, n, _, _ in pages_cache[participant]})

                at = (existing[-1] if existing else col) + 1
                insert_column(ws, at, style_from=col)
                ws.cell(row=ROW_PRODUCER, column=at, value=label).alignment = Alignment(
                    horizontal="center", vertical="center", wrap_text=True)
                ws.cell(row=ROW_DM, column=at, value="\n".join(docs)[:32767]).alignment = Alignment(
                    horizontal="center", vertical="center", wrap_text=True)
                spec_cell = ws.cell(row=ROW_SPEC, column=at, value=note[:32767])
                spec_cell.alignment = SPEC_ALIGNMENT
                spec_cell.fill = PatternFill("solid", fgColor=COLORS[check.status])
                status_cell = ws.cell(row=ROW_STATUS, column=col)
                if (_fill_rgb(status_cell) or "NONE") in OUR_FILLS | {"NONE"}:
                    status_cell.fill = PatternFill("solid", fgColor=COLORS[check.status])
                stats[check.status] += 1
                stats["rechecked"] += 1
                changed = True
                found = "подтверждено" if check.status == GREEN else "не подтверждено"
                logger.info(f"[CLAR] лот {lot} (лист {n}/{total}, {100 * n / total:.0f}%), {participant}: "
                            f"{found} ({len(docs)} документ(ов))")
            if changed:
                _recolor_tab(ws)
        target = out_path or result_path
        wb.save(target)
    finally:
        conn.close()
    logger.info(f"[CLAR] Перепроверено жёлтых: {stats['rechecked']} -> зелёных {stats.get(GREEN, 0)}, "
                f"жёлтых {stats.get(YELLOW, 0)}; уже были в этом раунде: {stats.get('already', 0)}. Файл: {target}")
    return dict(stats)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Перепроверка жёлтых оферт по документам уточнений")
    parser.add_argument("result", type=Path, help="result-файл с заполненными строками 3, 4, 11")
    parser.add_argument("tender_dir", type=Path, help="папка тендера с manifest.sqlite")
    parser.add_argument("--dir", type=Path, required=True, help="папка уточнений: <папка>/<Компания>/*.pdf")
    parser.add_argument("--round", type=int, help="номер раунда (по умолчанию определяется сам)")
    parser.add_argument("--out", type=Path, help="куда сохранить (по умолчанию - в тот же файл)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] %(message)s")
    sys.stdout.reconfigure(encoding="utf-8")

    s = recheck_result(args.result, args.tender_dir, args.dir, args.round, args.out)
    print(f"Раунд уточнений: {s['round']}, компаний: {s['companies']}, файлов: {s['files']}")
    if s["unmatched"]:
        print(f"  ! Папки не сопоставлены с участниками тендера: {', '.join(s['unmatched'])}")
    print(f"Перепроверено жёлтых оферт: {s.get('rechecked', 0)} -> зелёный {s.get(GREEN, 0)}, "
          f"жёлтый {s.get(YELLOW, 0)}" + (f"; уже были в этом раунде: {s['already']}" if s.get("already") else ""))
    print(f"Сохранено: {args.out or args.result}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
