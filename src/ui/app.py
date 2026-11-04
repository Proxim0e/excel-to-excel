"""
Окно приложения (tkinter): всё, что делает система по тендеру, видно в одном месте.

Слева сверху - текущий этап и прогресс, ниже лог (тот же logging, что в консоли). Справа - очередь вопросов
к человеку и форма ответа: статус оферты (Corespunde / Alternativa / Nu corespunde / Clarificare) или
карточка товара с предзаполненными полями. Работа идёт в фоновом потоке; когда появляется вопрос, окно
выходит на передний план.
"""
import inspect
import json
import logging
import os
import queue
import re
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Dict, List, Optional

from src.config import BASE_DIR, RESOURCE_DIR
from src.evaluate import confidence as cf
from src.evaluate.answers import answer_card, answer_offer, open_questions
from src.evaluate.cards import CARD_FIELDS
from src.history.db import DEFAULT_DB, connect
from src.history.review import REASONS

logger = logging.getLogger("ui")


def ensure_tcl() -> None:
    """В .venv на Windows Tk не находит свои библиотеки («Can't find a usable init.tcl»): они лежат в
    базовом Python - подставляем пути до создания окна, если их не задали."""
    base = Path(sys.base_prefix) / "tcl"
    for var, sub in (("TCL_LIBRARY", "tcl8.6"), ("TK_LIBRARY", "tk8.6")):
        if not os.environ.get(var) and (base / sub).is_dir():
            os.environ[var] = str(base / sub)


ensure_tcl()

STAGES = ["Сканирование лотов", "Excel", "Скачивание", "Классификация", "Anexa 22", "Заполнение", "Оценка",
          "AMDM", "Уточнения"]
# Строка лога -> этап (по меткам, которые модули уже пишут)
STAGE_MARKS = [
    (re.compile(r"Сбор ссылок|Обработан\s+===|параллельный парсинг"), "Сканирование лотов"),
    (re.compile(r"Подготовка структуры Excel|Запись участников"), "Excel"),
    (re.compile(r"скачиванию|ПРОГРЕСС|\[SKIP\]|Скачивание"), "Скачивание"),
    (re.compile(r"\[CLASSIFY\]"), "Классификация"),
    (re.compile(r"\[ANEXA22\]"), "Anexa 22"),
    (re.compile(r"\[FILL\]"), "Заполнение"),
    (re.compile(r"\[EVAL\]"), "Оценка"),
    (re.compile(r"Номеров DM|сайт: \d|Verificare AMDM"), "AMDM"),
    (re.compile(r"Раунд уточнений|clarific", re.IGNORECASE), "Уточнения"),
]
PROGRESS_RE = re.compile(r"\((\d+(?:\.\d+)?)%\)")
STATUS_LABELS = {cf.CORESPUNDE: "Corespunde", cf.ALTERNATIVA: "Alternativa", cf.NU_CORESPUNDE: "Nu corespunde",
                 cf.CLARIFICARE: "Clarificare"}
STATUS_COLORS = {cf.CORESPUNDE: "#00B050", cf.ALTERNATIVA: "#00B0F0", cf.NU_CORESPUNDE: "#FF0000",
                 cf.CLARIFICARE: "#FFFF00"}
CARD_LABELS = {"producer": "Производитель", "model": "Модель", "codes": "Коды модели", "dm": "Номера DM",
               "name": "Изделие", "params": "Параметры (JSON)", "compatible_with": "Совместимо с",
               "iso": "ISO / CE"}


class QueueHandler(logging.Handler):
    """Записи лога из фоновых потоков -> очередь -> текстовое поле окна."""

    def __init__(self, q: "queue.Queue"):
        super().__init__()
        self.q = q
        self.setFormatter(logging.Formatter("[%(asctime)s] %(message)s", "%H:%M:%S"))

    def emit(self, record):
        try:
            self.q.put(self.format(record))
        except Exception:
            self.handleError(record)


def stage_of(line: str) -> Optional[str]:
    for rx, stage in STAGE_MARKS:
        if rx.search(line):
            return stage
    return None


def latest_result() -> Optional[Path]:
    files = sorted(Path(RESOURCE_DIR).glob("result_*.xlsx"), key=lambda p: p.stat().st_mtime, reverse=True)
    return next((f for f in files if not f.name.startswith("~$")), None)


class App:
    def __init__(self, root: tk.Tk, result: Optional[Path] = None, tender_dir: Optional[Path] = None,
                 db: Path = DEFAULT_DB):
        self.root, self.db = root, Path(db)
        self.kb = connect(self.db)            # соединение окна; у фоновых задач - свои
        self.log_queue: "queue.Queue" = queue.Queue()
        self.worker: Optional[threading.Thread] = None
        self.stop_flag = threading.Event()
        self.known_questions: set = set()
        self.current: Optional[Dict] = None
        self.questions: List[Dict] = []

        root.title("Оценка тендеров - мед. изделия")
        root.geometry("1400x850")
        self.result_var = tk.StringVar(value=str(result or latest_result() or ""))
        self.tender_var = tk.StringVar(value=str(tender_dir or ""))
        self.url_var = tk.StringVar()
        self.stage_var = tk.StringVar(value="Готово к работе")
        self.counts_var = tk.StringVar()
        self._build()

        handler = QueueHandler(self.log_queue)
        logging.getLogger().addHandler(handler)
        logging.getLogger().setLevel(logging.INFO)
        self.root.after(200, self._poll_log)
        self.root.after(1000, self._poll_questions)
        self.refresh_questions()

    # --- разметка ------------------------------------------------------------------------------------------
    def _build(self):
        top = ttk.Frame(self.root, padding=6)
        top.pack(fill="x")
        ttk.Label(top, text="Тендер (ссылка или номер):").grid(row=0, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.url_var, width=50).grid(row=0, column=1, sticky="we")
        ttk.Button(top, text="Запустить тендер", command=self.run_tender).grid(row=0, column=2, padx=3)
        ttk.Label(top, text="Result:").grid(row=1, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.result_var, width=90).grid(row=1, column=1, sticky="we")
        ttk.Button(top, text="…", width=3, command=self.pick_result).grid(row=1, column=2, sticky="w")
        ttk.Label(top, text="Папка тендера:").grid(row=2, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.tender_var, width=90).grid(row=2, column=1, sticky="we")
        ttk.Button(top, text="…", width=3, command=self.pick_tender).grid(row=2, column=2, sticky="w")
        buttons = ttk.Frame(top)
        buttons.grid(row=0, column=3, rowspan=3, padx=10)
        ttk.Button(buttons, text="Оценка", command=self.run_evaluation).pack(fill="x")
        ttk.Button(buttons, text="Уточнения", command=self.run_clarifications).pack(fill="x")
        ttk.Button(buttons, text="В Excel", command=self.write_excel).pack(fill="x")
        ttk.Button(buttons, text="Стоп", command=self.stop).pack(fill="x")
        top.columnconfigure(1, weight=1)

        status = ttk.Frame(self.root, padding=(6, 0))
        status.pack(fill="x")
        ttk.Label(status, textvariable=self.stage_var, font=("Segoe UI", 11, "bold")).pack(side="left")
        self.progress = ttk.Progressbar(status, length=300, mode="determinate")
        self.progress.pack(side="left", padx=10)
        ttk.Label(status, textvariable=self.counts_var).pack(side="left")
        self.stages = ttk.Label(status, text=" → ".join(STAGES), foreground="#777")
        self.stages.pack(side="right")

        panes = ttk.Panedwindow(self.root, orient="horizontal")
        panes.pack(fill="both", expand=True, padx=6, pady=6)
        logf = ttk.Labelframe(panes, text="Лог работы")
        self.log_text = tk.Text(logf, wrap="none", font=("Consolas", 9), state="disabled")
        sy = ttk.Scrollbar(logf, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=sy.set)
        sy.pack(side="right", fill="y")
        self.log_text.pack(fill="both", expand=True)
        panes.add(logf, weight=1)

        qf = ttk.Labelframe(panes, text="Вопросы к человеку")
        panes.add(qf, weight=2)
        self.q_list = tk.Listbox(qf, width=42, exportselection=False)
        self.q_list.pack(side="left", fill="y")
        self.q_list.bind("<<ListboxSelect>>", lambda e: self.show_selected())
        self.form = ttk.Frame(qf, padding=6)
        self.form.pack(side="left", fill="both", expand=True)

    # --- выбор файлов ---------------------------------------------------------------------------------------
    def pick_result(self):
        path = filedialog.askopenfilename(initialdir=RESOURCE_DIR, filetypes=[("Excel", "*.xlsx")])
        if path:
            self.result_var.set(path)

    def pick_tender(self):
        path = filedialog.askdirectory(initialdir=Path(BASE_DIR) / "downloads")
        if path:
            self.tender_var.set(path)

    def _paths(self):
        result, tender = Path(self.result_var.get()), Path(self.tender_var.get())
        if not result.is_file() or not (tender / "manifest.sqlite").is_file():
            messagebox.showwarning("Нет файлов", "Укажите result-файл и папку тендера (с manifest.sqlite).")
            return None
        return result, tender

    # --- фоновые задачи -------------------------------------------------------------------------------------
    def _start(self, title: str, target):
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Занято", "Предыдущая задача ещё идёт.")
            return
        self.stop_flag.clear()
        self.stage_var.set(title)

        def run():
            try:
                target()
                logger.info(f"=== {title}: готово ===")
            except Exception as e:  # ошибка задачи - в лог, окно продолжает работать
                logger.exception(f"{title}: ошибка {type(e).__name__}: {e}")

        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()

    def run_tender(self):
        def task():
            src_dir = str(Path(BASE_DIR) / "src")
            if src_dir not in sys.path:
                sys.path.insert(0, src_dir)   # main.py импортирует модули как из папки src
            from src import main as main_module
            tender = self.url_var.get().strip() or None
            if tender and "tender" in inspect.signature(main_module.main).parameters:
                main_module.main(tender=tender)
            else:
                main_module.main()
            result = latest_result()
            if result:
                self.root.after(0, lambda: self.result_var.set(str(result)))
        self._start("Тендер", task)

    def run_evaluation(self):
        paths = self._paths()
        if not paths:
            return
        result, tender = paths

        def task():
            from src.evaluate.engine import evaluate_tender
            kb = connect(self.db)
            try:
                done = {"n": 0}

                def on_lot(lot, evs):
                    done["n"] += 1
                    if self.stop_flag.is_set():
                        raise KeyboardInterrupt("остановлено")
                    self.root.after(0, lambda n=done["n"]: self._progress(n))

                evaluate_tender(result, tender, kb, offline=True, on_lot=on_lot)
                self._write_excel(result, tender, kb)
            finally:
                kb.close()
        self._start("Оценка", task)

    @staticmethod
    def _write_excel(result: Path, tender: Path, kb):
        from src.excel.verdicts import write_verdicts
        stats = write_verdicts(result, tender, kb)
        logger.info(f"[VERDICTS] Цвета решений записаны: {stats.pop('file')} - {stats}")

    def write_excel(self):
        """Решения (с ответами человека) - в копию result-файла."""
        paths = self._paths()
        if not paths:
            return
        result, tender = paths

        def task():
            kb = connect(self.db)
            try:
                self._write_excel(result, tender, kb)
            finally:
                kb.close()
        self._start("Запись в Excel", task)

    def run_clarifications(self):
        paths = self._paths()
        if not paths:
            return
        result, tender = paths
        clar = tender / "Clarificari"
        if not clar.is_dir():
            picked = filedialog.askdirectory(initialdir=tender, title="Папка уточнений (<Компания>/*.pdf)")
            if not picked:
                return
            clar = Path(picked)

        def task():
            from src.clarificari import recheck_result
            out = result.with_name(result.stem + "_clarificari.xlsx")
            stats = recheck_result(result, tender, clar, out_path=out)
            logger.info(f"Уточнения: {stats}")
        self._start("Уточнения", task)

    def stop(self):
        self.stop_flag.set()
        logger.info("Остановка: текущий лот доделывается, дальше - стоп")

    def _progress(self, lots_done: int):
        self.progress.configure(mode="indeterminate" if not lots_done else "determinate")
        self.counts_var.set(f"лотов оценено: {lots_done}")

    # --- лог и этап -----------------------------------------------------------------------------------------
    def _poll_log(self):
        lines = []
        while True:
            try:
                lines.append(self.log_queue.get_nowait())
            except queue.Empty:
                break
        if lines:
            self.log_text.configure(state="normal")
            for line in lines:
                self.log_text.insert("end", line + "\n")
                stage = stage_of(line)
                if stage:
                    self.stage_var.set(f"Этап: {stage}")
                    self.stages.configure(text=" → ".join(f"[{s}]" if s == stage else s for s in STAGES))
                m = PROGRESS_RE.search(line)
                if m:
                    self.progress["value"] = float(m.group(1))
            self.log_text.configure(state="disabled")
            self.log_text.see("end")
        self.root.after(200, self._poll_log)

    # --- вопросы --------------------------------------------------------------------------------------------
    def _poll_questions(self):
        new = self.refresh_questions()
        if new:
            self.popup()
        self.root.after(1500, self._poll_questions)

    def refresh_questions(self) -> int:
        """Обновляет список; возвращает число новых вопросов."""
        self.questions = open_questions(self.kb)
        ids = {q["id"] for q in self.questions}
        new = len(ids - self.known_questions)
        self.known_questions |= ids
        selected = self.current["id"] if self.current else None
        self.q_list.delete(0, "end")
        for i, q in enumerate(self.questions):
            if q["kind"] == "product_card":
                text = f"Карточка: {q['model_key']}"
            else:
                text = f"Лот {q['lot_no']}: {q['participant']}"
            self.q_list.insert("end", text)
            if q["kind"] == "offer_status":
                self.q_list.itemconfig(i, background=STATUS_COLORS.get(q["payload"].get("suggested"), "white"))
            if q["id"] == selected:
                self.q_list.selection_set(i)
        cards = sum(q["kind"] == "product_card" for q in self.questions)
        self.counts_var.set(f"открыто вопросов: {len(self.questions)} (карточек: {cards})")
        if self.questions and not self.current:
            self.q_list.selection_set(0)
            self.show_selected()
        return new

    def popup(self):
        """Новый вопрос - окно на передний план."""
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(500, lambda: self.root.attributes("-topmost", False))
        self.root.bell()

    def show_selected(self):
        sel = self.q_list.curselection()
        if not sel:
            return
        self.current = self.questions[sel[0]]
        for w in self.form.winfo_children():
            w.destroy()
        if self.current["kind"] == "product_card":
            self._card_form(self.current)
        else:
            self._offer_form(self.current)

    def _text(self, parent, title: str, body: str, height: int = 4):
        ttk.Label(parent, text=title, font=("Segoe UI", 9, "bold")).pack(anchor="w")
        t = tk.Text(parent, height=height, wrap="word", font=("Segoe UI", 9))
        t.insert("1.0", body or "—")
        t.configure(state="disabled")
        t.pack(fill="x", pady=(0, 4))

    def _offer_form(self, q: Dict):
        p, f = q["payload"], q["payload"].get("findings", {})
        ttk.Label(self.form, text=f"Лот {q['lot_no']} - {q['participant']}", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        self._text(self.form, "Название лота", p.get("lot_title", ""), 2)
        self._text(self.form, "Запрос (B11)", p.get("requirement", ""), 4)
        l1, l2 = f.get("level1", {}), f.get("level2", {})
        self._text(self.form, "Уровень 1 - совпадение запроса и Anexa 22",
                   f"{l1.get('status')}; копия запроса: {l1.get('copy')}\n"
                   + "\n".join(["≠ " + c for c in l1.get("conflicts", [])] + ["нет: " + m for m in l1.get("missing", [])]), 3)
        quotes = "\n".join(f"«{'» «'.join(e.get('quotes', [])[:3])}», p{e.get('page_no')}, {e.get('document')}"
                           for e in l2.get("evidence", []))
        self._text(self.form, "Уровень 2 - документы", (f"не найдено: {', '.join(l2.get('missing', []))}\n" if l2.get("missing") else "")
                   + (quotes or ("нет технических документов" if not l2.get("has_docs", True) else "")), 4)
        amdm = f.get("amdm", {})
        self._text(self.form, "AMDM", f"{amdm.get('status')}: " + "; ".join(amdm.get("reasons", [])), 2)
        hist = f.get("history", {})
        hist_lines = [f"{o['tender_id']} лот {o['lot_no']}: {o['participant']} - {o['status']} ({o['source']}), "
                      f"сходство запроса {o.get('similarity')}" for o in hist.get("outcomes", [])]
        hist_lines += [f"похожий запрос {s['tender_id']} лот {s['lot_no']} ({s['similarity']}): принято "
                       f"{', '.join(s.get('accepted', [])) or '—'}" for s in hist.get("similar_lots", [])]
        self._text(self.form, "История", "\n".join(hist_lines) or "модель и похожие запросы в базе не встречались", 3)
        self._text(self.form, "Почему вопрос", "\n".join(p.get("reasons", [])), 3)

        choice = tk.StringVar(value=p.get("suggested", cf.CLARIFICARE))
        row = ttk.Frame(self.form)
        row.pack(anchor="w", pady=4)
        for status, label in STATUS_LABELS.items():
            tk.Radiobutton(row, text=label, variable=choice, value=status, indicatoron=False, width=14,
                           selectcolor=STATUS_COLORS[status]).pack(side="left", padx=2)
        reason = tk.StringVar()
        ttk.Label(self.form, text="Причина:").pack(anchor="w")
        ttk.Combobox(self.form, textvariable=reason, values=[""] + list(REASONS), width=30).pack(anchor="w")
        comment = tk.StringVar()
        ttk.Label(self.form, text="Комментарий:").pack(anchor="w")
        ttk.Entry(self.form, textvariable=comment, width=80).pack(anchor="w")

        def save():
            answer_offer(self.kb, q["id"], choice.get(), reason.get() or None, comment.get())
            logger.info(f"[UI] Лот {q['lot_no']}, {q['participant']}: {STATUS_LABELS[choice.get()]}")
            self.current = None
            self.refresh_questions()
        ttk.Button(self.form, text="Сохранить решение", command=save).pack(anchor="e", pady=6)

    def _card_form(self, q: Dict):
        card = dict(q["payload"].get("card") or {"model_key": q["model_key"]})
        ttk.Label(self.form, text=f"Карточка товара: {q['model_key']}", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        ttk.Label(self.form, text=f"Из лота {q['payload'].get('lot_no')}, {q['payload'].get('participant')}: "
                                  f"{q['payload'].get('lot_title', '')}").pack(anchor="w")
        grid = ttk.Frame(self.form)
        grid.pack(fill="x", pady=6)
        entries = {}
        for i, name in enumerate(CARD_FIELDS):
            ttk.Label(grid, text=CARD_LABELS[name]).grid(row=i, column=0, sticky="nw", padx=(0, 6))
            value = card.get(name) or ""
            if name == "params":
                value = json.dumps(value, ensure_ascii=False, indent=1) if not isinstance(value, str) else value
                w = tk.Text(grid, height=6, width=90, font=("Consolas", 9))
                w.insert("1.0", value)
            else:
                w = ttk.Entry(grid, width=90)
                w.insert(0, str(value))
            w.grid(row=i, column=1, sticky="we", pady=1)
            entries[name] = w

        def save():
            new = {"model_key": card["model_key"]}
            for name, w in entries.items():
                new[name] = w.get("1.0", "end").strip() if isinstance(w, tk.Text) else w.get().strip()
            try:
                new["params"] = json.loads(new["params"] or "{}")
            except json.JSONDecodeError:
                messagebox.showerror("Параметры", "Параметры - JSON: {\"Lungime\": \"12 cm\"}")
                return
            answer_card(self.kb, q["id"], new)
            logger.info(f"[UI] Карточка подтверждена: {card['model_key']}")
            self.current = None
            self.refresh_questions()
        ttk.Button(self.form, text="Подтвердить карточку", command=save).pack(anchor="e", pady=6)


def main(argv=None) -> int:
    argv = list(argv or [])
    result = Path(argv[0]) if len(argv) > 0 else None
    tender = Path(argv[1]) if len(argv) > 1 else None
    root = tk.Tk()
    App(root, result, tender)
    root.mainloop()
    return 0
