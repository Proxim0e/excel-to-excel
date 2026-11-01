"""
python -m src.history import "<рабочий Excel>" [--tender 21641880] [--db data/knowledge.sqlite]
python -m src.history events 21641880

Импорт рабочего Excel тендера в базу знаний (снимок + события) и сводка по решениям.
"""
import argparse
import sys
from collections import Counter
from pathlib import Path

from src.history.db import DEFAULT_DB, connect
from src.history.importer import import_workbook


def cmd_import(args) -> int:
    conn = connect(args.db)
    try:
        r = import_workbook(conn, Path(args.workbook), args.tender)
        if r["skipped"]:
            print(f"Тендер {r['tender_id']}: файл не менялся с последнего импорта (снимок {r['snapshot_id']})")
            return 0
        sid = r["snapshot_id"]
        print(f"Тендер {r['tender_id']}: снимок {sid}, лотов {r['lots']}, оферт {r['offers']}")
        rows = conn.execute("SELECT decision, reason, needs_review, sheet, participant, row1_text "
                            "FROM offers WHERE snapshot_id = ?", (sid,)).fetchall()
        print("Решения:", dict(Counter(r[0] for r in rows).most_common()))
        print("Причины отказа:", dict(Counter(r[1] for r in rows if r[1]).most_common()))
        print("Итоги лотов:", dict(Counter(o for (o,) in conn.execute(
            "SELECT outcome FROM lots WHERE snapshot_id = ?", (sid,))).most_common()))
        review = [r for r in rows if r[2]]
        print(f"Спросить человека: {len(review)}")
        for decision, _, why, sheet, who, text in review[:args.show]:
            print(f"  {sheet:10} {who[:28]:28} {why} | «{text}»")
        if r["events"]:
            print(f"События относительно прошлого снимка: {len(r['events'])}")
            for lot, who, f, old, new in r["events"][:args.show]:
                print(f"  лот {lot} {who or '':24} {f}: {old} -> {new}")
    finally:
        conn.close()
    return 0


def cmd_events(args) -> int:
    conn = connect(args.db)
    try:
        rows = conn.execute("SELECT detected_at, lot_no, participant, field, old_value, new_value FROM events "
                            "WHERE tender_id = ? ORDER BY id", (args.tender,)).fetchall()
        print(f"Событий: {len(rows)}")
        for at, lot, who, f, old, new in rows:
            print(f"  {at} лот {lot} {who or '':24} {f}: {old} -> {new}")
    finally:
        conn.close()
    return 0


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="python -m src.history", description="База знаний: история тендеров")
    ap.add_argument("--db", default=str(DEFAULT_DB), help="файл базы знаний (SQLite)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("import", help="импортировать рабочий Excel тендера (новый снимок)")
    i.add_argument("workbook")
    i.add_argument("--tender", help="номер тендера, если его нет в имени файла")
    i.add_argument("--show", type=int, default=25, help="сколько строк «спросить человека» и событий показать")
    i.set_defaults(func=cmd_import)
    e = sub.add_parser("events", help="события тендера (изменения между снимками)")
    e.add_argument("tender")
    e.set_defaults(func=cmd_events)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
