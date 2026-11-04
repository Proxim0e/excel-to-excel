"""
Оценка тендера по схеме Ivan.

  python -m src.evaluate <result.xlsx> downloads/<тендер> [--dry-run] [--online] [--db data/knowledge.sqlite]

--dry-run - ничего не сохраняет в базу знаний (база в памяти): показать, что стало бы автоматически и какие
вопросы появились бы. --online - номера DM, которых нет в локальной копии реестра, искать на сайте AMDM.
"""
import argparse
import logging
import sqlite3
import sys
from collections import Counter
from pathlib import Path

from src.evaluate import confidence as cf
from src.evaluate.engine import evaluate_tender
from src.history.db import DEFAULT_DB, SCHEMA, connect


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format='[%(asctime)s] [%(levelname)s] %(message)s', force=True)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("result", type=Path)
    ap.add_argument("tender_dir", type=Path)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--online", action="store_true")
    args = ap.parse_args(argv)

    if args.dry_run:
        kb = sqlite3.connect(":memory:")
        kb.executescript(SCHEMA)
    else:
        kb = connect(args.db)
    try:
        evs = evaluate_tender(args.result, args.tender_dir, kb, offline=not args.online)
        by = Counter((e.status, e.source) for e in evs)
        print(f"Оферт: {len(evs)}")
        for (status, source), n in sorted(by.items()):
            print(f"  {status:14} {source:8} {n}")
        questions = kb.execute("SELECT kind, COUNT(*) FROM questions WHERE state = 'open' GROUP BY kind").fetchall()
        print("Вопросов к человеку: " + (", ".join(f"{k}: {n}" for k, n in questions) or "0"))
        reasons = Counter(r.split(":")[0] for e in evs if e.source == cf.PENDING for r in e.reasons)
        print("Почему вопрос: " + ", ".join(f"{r} - {n}" for r, n in reasons.most_common(8)))
        blue = [e for e in evs if e.status == cf.ALTERNATIVA][:8]
        for e in blue:
            print(f"  синий: лот {e.lot_no}, {e.participant}: {'; '.join(e.reasons)[:120]}")
    finally:
        kb.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
