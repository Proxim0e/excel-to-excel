"""
Окно приложения (tkinter): всё, что делает система по тендеру, видно в одном месте.

Слева сверху - текущий этап и прогресс, ниже лог (тот же logging, что в консоли). Справа - очередь вопросов
к человеку и форма ответа: статус оферты (Corespunde / Alternativa / Nu corespunde / Clarificare) или
карточка товара с предзаполненными полями. Работа идёт в фоновом потоке; когда появляется вопрос, окно
выходит на передний план.
"""
import json
import logging
import os
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Dict, List, Optional

from src import config as app_config
from src.config import BASE_DIR, RESOURCE_DIR
from src.evaluate import confidence as cf
from src.evaluate.answers import answer_card, answer_medical, answer_offer, open_questions
from src.history.cards import card_title
from src.history.db import DEFAULT_DB, connect
from src.history import sync as answers_sync
from src.history.review import REASONS
from src.ui import clipboard
from src.ui.card_form import CardForm
from src.ui.cards_tab import CardsTab

logger = logging.getLogger("ui")


def ensure_tcl() -> None:
    """В .venv на Windows Tk не находит свои библиотеки («Can't find a usable init.tcl»): они лежат в
    базовом Python - подставляем пути до создания окна, если их не задали."""
    base = Path(sys.base_prefix) / "tcl"
    for var, sub in (("TCL_LIBRARY", "tcl8.6"), ("TK_LIBRARY", "tk8.6")):
        if not os.environ.get(var) and (base / sub).is_dir():
            os.environ[var] = str(base / sub)


ensure_tcl()


def new_root(attempts: int = 3) -> tk.Tk:
    """tk.Tk() с повтором: в .venv на Windows Tcl изредка не дочитывает свои файлы («Can't find a usable
    tk.tcl») - со второй попытки окно создаётся."""
    for i in range(attempts):
        try:
            return tk.Tk()
        except tk.TclError:
            if i == attempts - 1:
                raise
            time.sleep(0.3)

STAGES = ["Документы тендера", "Листы Excel", "Сканирование лотов", "Участники в Excel", "Скачивание", "Классификация", "Anexa 22", "Заполнение", "Оценка",
          "AMDM", "Уточнения"]
# Строка лога -> этап (по меткам, которые модули уже пишут)
STAGE_MARKS = [
    (re.compile(r"\[TENDER\]|Загружено \d+ лотов"), "Документы тендера"),
    (re.compile(r"Сбор ссылок|Обработан\s+(?:\d|===)|параллельный парсинг"), "Сканирование лотов"),
    (re.compile(r"Подготовка структуры Excel|Excel структура готова"), "Листы Excel"),
    (re.compile(r"Запись участников|ГОТОВО! Файл сохранен|\[EXCEL\]"), "Участники в Excel"),
    (re.compile(r"скачиванию|ПРОГРЕСС|\[SKIP\]|Скачивание"), "Скачивание"),
    (re.compile(r"\[CLASSIFY\]"), "Классификация"),
    (re.compile(r"\[ANEXA22\]"), "Anexa 22"),
    (re.compile(r"\[FILL\]"), "Заполнение"),
    (re.compile(r"\[EVAL\]"), "Оценка"),
    (re.compile(r"\[AMDM\]|Номеров DM|сайт: \d|Verificare AMDM"), "AMDM"),
    (re.compile(r"\[CLAR\]|Раунд уточнений|clarific", re.IGNORECASE), "Уточнения"),
]
# «(46.0%)», «(12%)», «(12/283, 4%)» - процент перед закрывающей скобкой
PROGRESS_RE = re.compile(r"(\d+(?:\.\d+)?)%\)")
SILENCE_SECONDS = 30
MAX_LOG_LINES = 4000   # старые строки уходят: окно не тормозит на тысячах [SKIP] при скачивании
# Не показывать в окне: подсказки библиотек на каждый документ и строки по отдельным файлам при скачивании
# (тысячи «[SKIP] Уже на месте»; ход скачивания виден по строкам «ПРОГРЕСС», полный лог - в консоли)
NOISE = re.compile(r"pymupdf_layout|Consider using the pymupdf|\[(?:SKIP|COPIED)\]")
# Строка процесса (main.py, amdm, clarificari): «[2026-11-04 09:43:29,561] [INFO] текст» -> «[09:43:29] текст»
PROCESS_LINE = re.compile(r"^\[\d{4}-\d\d-\d\d (\d\d:\d\d:\d\d)[,.]\d+\]\s*\[(\w+)\]\s?(.*)$")   # столько без новых строк - показываем, что ждём (сайт, большой PDF)
# Подсветка строк лога: тег -> (шаблон, цвет текста)
LOG_TAGS = [
    ("error", re.compile(r"ERROR|CRITICAL|ошибк|rosu|nu_corespunde|нет в реестре", re.IGNORECASE), "#C00000"),
    ("alt", re.compile(r"alternativa", re.IGNORECASE), "#0070C0"),
    ("warn", re.compile(r"WARNING|galben|вопрос человеку|не подтверждено|clarificare|fara DM", re.IGNORECASE),
     "#9C6500"),
    ("ok", re.compile(r"verde|corespunde|подтверждено|найден:|готово", re.IGNORECASE), "#00703C"),
]


def log_tag(line: str) -> Optional[str]:
    for tag, rx, _ in LOG_TAGS:
        if rx.search(line):
            return tag
    return None
STATUS_LABELS = {cf.CORESPUNDE: "Corespunde", cf.ALTERNATIVA: "Alternativa", cf.NU_CORESPUNDE: "Nu corespunde",
                 cf.CLARIFICARE: "Clarificare"}
STATUS_COLORS = {cf.CORESPUNDE: "#00B050", cf.ALTERNATIVA: "#00B0F0", cf.NU_CORESPUNDE: "#FF0000",
                 cf.CLARIFICARE: "#FFFF00"}


class QueueHandler(logging.Handler):
    """Записи лога из фоновых потоков -> очередь -> текстовое поле окна."""

    def __init__(self, q: "queue.Queue"):
        super().__init__()
        self.q = q
        self.setFormatter(logging.Formatter("[%(asctime)s] %(message)s", "%H:%M:%S"))

    def emit(self, record):
        try:
            line = self.format(record)
            if record.levelno >= logging.WARNING:
                line = line.replace("] ", f"] [{record.levelname}] ", 1)
            self.q.put(line)
        except Exception:
            self.handleError(record)


def normalize_line(line: str) -> str:
    """Строка вывода дочернего процесса - в формат лога окна; уровень пишется только для WARNING и выше."""
    m = PROCESS_LINE.match(line)
    if not m:
        return f"[{time.strftime('%H:%M:%S')}] {line}"
    t, level, msg = m.groups()
    return f"[{t}] " + ("" if level in ("INFO", "DEBUG") else f"[{level}] ") + msg


def stage_of(line: str) -> Optional[str]:
    for rx, stage in STAGE_MARKS:
        if rx.search(line):
            return stage
    return None


def latest_tender_dir() -> Optional[Path]:
    """Последняя папка тендера в downloads/ (с manifest.sqlite) - после запуска по родительскому файлу."""
    dirs = [d for d in (Path(BASE_DIR) / "downloads").glob("*") if (d / "manifest.sqlite").is_file()]
    return max(dirs, key=lambda d: (d / "manifest.sqlite").stat().st_mtime) if dirs else None


def latest_result() -> Optional[Path]:
    files = sorted(Path(RESOURCE_DIR).glob("result_*.xlsx"), key=lambda p: p.stat().st_mtime, reverse=True)
    return next((f for f in files if not f.name.startswith("~$")), None)


def tender_dirs() -> List[Path]:
    """Скачанные тендеры (downloads/<номер> с manifest.sqlite), последний - первым."""
    dirs = [d for d in (Path(BASE_DIR) / "downloads").glob("*") if (d / "manifest.sqlite").is_file()]
    return sorted(dirs, key=lambda d: (d / "manifest.sqlite").stat().st_mtime, reverse=True)


def parent_info():
    """Родительский файл resources/-*.xlsx и ссылка на тендер из него: (Path | None, str | None)."""
    from src.excel.reader import find_parent_file, get_tender_url_from_parent
    try:
        path = find_parent_file()
    except FileNotFoundError:
        return None, None
    try:
        url = get_tender_url_from_parent()
    except Exception:  # файл открыт в Excel / повреждён - имя файла всё равно покажем
        url = None
    return path, url


class App:
    def __init__(self, root: tk.Tk, result: Optional[Path] = None, tender_dir: Optional[Path] = None,
                 db: Path = DEFAULT_DB, answers_dir: Optional[Path] = None):
        self.root, self.db = root, Path(db)
        clipboard.install(root)               # Ctrl+C/V при русской раскладке, меню правой кнопки
        # ответы человека для git; в тестах - временная папка (по умолчанию - knowledge/answers, только с боевой базой)
        self.answers_dir = Path(answers_dir) if answers_dir else (
            answers_sync.ANSWERS_DIR if Path(db) == Path(DEFAULT_DB) else Path(db).parent / "answers")
        self.kb = connect(self.db)            # соединение окна; у фоновых задач - свои
        self.log_queue: "queue.Queue" = queue.Queue()
        self.ui_calls: "queue.Queue" = queue.Queue()   # действия с окном из фоновых потоков (Tk - только из главного)
        self.worker: Optional[threading.Thread] = None
        self.stop_flag = threading.Event()
        self.known_questions: set = set()
        self.current: Optional[Dict] = None
        self.questions: List[Dict] = []
        self.proc: Optional[subprocess.Popen] = None     # тендер / AMDM / уточнения - отдельным процессом
        self.stage: Optional[str] = None
        self.done_stages: List[str] = []

        root.title("Оценка тендеров - мед. изделия")
        root.geometry("1400x850")
        self.result_var = tk.StringVar(value=str(result or latest_result() or ""))
        self.tender_var = tk.StringVar(value=str(tender_dir or ""))
        self.url_var = tk.StringVar()
        self.stage_var = tk.StringVar(value="Готово к работе")
        self.counts_var = tk.StringVar()
        self.now_var = tk.StringVar()       # последнее действие текущей задачи
        self.clock_var = tk.StringVar()     # «идёт 02:13» - задача жива
        self.task_started = self.last_line_at = 0.0
        self.mode: Optional[str] = None     # как работаем: tender / parent / continue (выбор при запуске)
        self.start_win: Optional[tk.Toplevel] = None
        self._build()
        self.tender_var.trace_add("write", lambda *_: self._sync_tender())
        self._sync_tender()

        self.handler = QueueHandler(self.log_queue)
        self._attach_log()
        self.root.after(200, self._poll_log)
        self.root.after(1000, self._poll_questions)
        self.sync_answers()
        self.refresh_questions()

    def sync_answers(self):
        """Ответы человека с других ПК (knowledge/answers/*.jsonl после git pull) - в базу, свои - в файл ПК."""
        try:
            answers_sync.sync(self.kb, self.answers_dir)
        except Exception as e:  # файл ответов битый / занят - окно работает и без него
            logger.warning(f"[SYNC] Ответы с других ПК не загружены: {e}")

    def _answered(self):
        """После ответа: выгрузить ответы этого ПК (для git) и обновить список вопросов."""
        try:
            answers_sync.export(self.kb, answers_sync.own_file(self.answers_dir))
        except Exception as e:
            logger.warning(f"[SYNC] Ответ сохранён в базе, но не выгружен в файл: {e}")
        self.current = None
        self.refresh_questions()

    # --- как будем работать: выбор при запуске окна ---------------------------------------------------------
    def ask_start(self) -> tk.Toplevel:
        """Диалог при открытии окна: новый тендер по номеру, родительский файл или продолжить скачанный.
        Крестик / Esc - просто окно, без запуска."""
        parent, parent_url = parent_info()
        dirs = tender_dirs()
        win = self.start_win = tk.Toplevel(self.root)
        win.title("Как будем работать?")
        win.transient(self.root)
        win.resizable(False, False)
        body = ttk.Frame(win, padding=12)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Как будем работать?", font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(0, 8))
        mode = self.start_mode = tk.StringVar(value="continue" if dirs else ("parent" if parent else "tender"))
        self.start_number = tk.StringVar(value=self.url_var.get())
        self.start_folder = tk.StringVar(value=dirs[0].name if dirs else "")

        ttk.Radiobutton(body, text="Новый тендер - по номеру или ссылке (документы с MTender)",
                        variable=mode, value="tender").pack(anchor="w")
        entry = ttk.Entry(body, textvariable=self.start_number, width=60)
        entry.pack(anchor="w", padx=(24, 0), pady=(2, 8))
        entry.bind("<FocusIn>", lambda e: mode.set("tender"))

        if parent:
            text = f"По родительскому файлу: {parent.name}" + (f"\n{parent_url}" if parent_url else "")
        else:
            text = "По родительскому файлу: в resources/ нет файла -*.xlsx"
        rb = ttk.Radiobutton(body, text=text, variable=mode, value="parent")
        rb.pack(anchor="w", pady=(0, 8))
        if not parent:
            rb.state(["disabled"])

        rb = ttk.Radiobutton(body, text="Продолжить скачанный тендер (AMDM, Оценка, Уточнения):",
                             variable=mode, value="continue")
        rb.pack(anchor="w")
        combo = ttk.Combobox(body, textvariable=self.start_folder, values=[d.name for d in dirs],
                             state="readonly", width=20)
        combo.pack(anchor="w", padx=(24, 0), pady=(2, 8))
        combo.bind("<<ComboboxSelected>>", lambda e: mode.set("continue"))
        if not dirs:
            rb.state(["disabled"])
            combo.state(["disabled"])

        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=(6, 0))
        ttk.Button(buttons, text="Начать", command=lambda: self.apply_start(mode.get(), run=True)).pack(side="right")
        ttk.Button(buttons, text="Только открыть окно",
                   command=lambda: self.apply_start(mode.get(), run=False)).pack(side="right", padx=6)
        win.protocol("WM_DELETE_WINDOW", lambda: self.apply_start("none"))
        win.bind("<Return>", lambda e: self.apply_start(mode.get(), run=True))
        win.bind("<Escape>", lambda e: self.apply_start("none"))
        win.update_idletasks()
        x = self.root.winfo_rootx() + max(0, (self.root.winfo_width() - win.winfo_reqwidth()) // 2)
        win.geometry(f"+{x}+{self.root.winfo_rooty() + 120}")
        try:
            win.grab_set()
        except tk.TclError:   # окно ещё не показано (тесты) - без модальности
            pass
        return win

    def apply_start(self, mode: str, run: bool = False) -> bool:
        """Выбор из ask_start: заполнить поля окна и (run) запустить тендер. False - выбор неполный,
        диалог остаётся открытым."""
        if mode == "tender":
            number = self.start_number.get().strip()
            if not number:
                messagebox.showwarning("Номер тендера", "Введите номер или ссылку тендера.", parent=self.start_win)
                return False
            self.url_var.set(number)
        elif mode == "parent":
            self.url_var.set("")
        elif mode == "continue":
            folder = Path(BASE_DIR) / "downloads" / self.start_folder.get()
            if not (folder / "manifest.sqlite").is_file():
                return False
            self.tender_var.set(str(folder))
            result = latest_result()
            if result:
                self.result_var.set(str(result))
        self.mode = mode if mode != "none" else None
        if self.start_win is not None:
            self.start_win.grab_release()
            self.start_win.destroy()
            self.start_win = None
        if mode == "tender":
            self.stage_var.set(f"Новый тендер: {self.url_var.get()}")
        elif mode == "parent":
            self.stage_var.set("Тендер по родительскому файлу")
        elif mode == "continue":
            self.stage_var.set(f"Тендер {Path(self.tender_var.get()).name}: дальше - AMDM, Оценка, Уточнения")
        if run and mode in ("tender", "parent"):
            self.run_tender()
        return True

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
        ttk.Button(buttons, text="AMDM", command=self.run_amdm).pack(fill="x")
        ttk.Button(buttons, text="Оценка", command=self.run_evaluation).pack(fill="x")
        ttk.Button(buttons, text="Уточнения", command=self.run_clarifications).pack(fill="x")
        ttk.Button(buttons, text="В Excel", command=self.write_excel).pack(fill="x")
        ttk.Button(buttons, text="Стоп", command=self.stop).pack(fill="x")
        ttk.Button(buttons, text="Настройки", command=self.open_settings).pack(fill="x", pady=(6, 0))
        top.columnconfigure(1, weight=1)

        status = ttk.Frame(self.root, padding=(6, 0))
        status.pack(fill="x")
        ttk.Label(status, textvariable=self.stage_var, font=("Segoe UI", 11, "bold")).pack(side="left")
        self.progress = ttk.Progressbar(status, length=300, mode="determinate")
        self.progress.pack(side="left", padx=10)
        ttk.Label(status, textvariable=self.counts_var).pack(side="left")
        ttk.Label(status, textvariable=self.clock_var, foreground="#555").pack(side="left", padx=8)
        self.stages = ttk.Label(self.root, text=" → ".join(STAGES), foreground="#777", padding=(8, 0))
        self.stages.pack(fill="x")
        ttk.Label(self.root, textvariable=self.now_var, foreground="#333", padding=(8, 0)).pack(fill="x")

        panes = ttk.Panedwindow(self.root, orient="horizontal")
        panes.pack(fill="both", expand=True, padx=6, pady=6)
        logf = ttk.Labelframe(panes, text="Лог работы")
        self.log_text = tk.Text(logf, wrap="none", font=("Consolas", 9), state="disabled")
        for tag, _, color in LOG_TAGS:
            self.log_text.tag_configure(tag, foreground=color)
        sy = ttk.Scrollbar(logf, command=self.log_text.yview)
        sx = ttk.Scrollbar(logf, orient="horizontal", command=self.log_text.xview)
        self.log_text.configure(yscrollcommand=sy.set, xscrollcommand=sx.set)
        sy.pack(side="right", fill="y")
        sx.pack(side="bottom", fill="x")
        self.log_text.pack(fill="both", expand=True)
        panes.add(logf, weight=3)

        self.tabs = ttk.Notebook(panes)
        panes.add(self.tabs, weight=2)
        qf = self.qf = ttk.Labelframe(self.tabs, text="Вопросы к человеку")
        self.tabs.add(qf, text="Вопросы")
        self.cards_frame = ttk.Frame(self.tabs)
        self.tabs.add(self.cards_frame, text="База карточек")
        self.cards_tab = None                     # строится при первом открытии вкладки
        self.tabs.bind("<<NotebookTabChanged>>", lambda e: self._open_cards_tab())
        self.root.after(100, lambda: panes.sashpos(0, int(self.root.winfo_width() * 0.45)))
        self.q_list = tk.Listbox(qf, width=30, exportselection=False)
        self.q_list.pack(side="left", fill="y")
        self.q_list.bind("<<ListboxSelect>>", lambda e: self.show_selected())
        self.form = ttk.Frame(qf, padding=6)
        self.form.pack(side="left", fill="both", expand=True)

    # --- выбор файлов ---------------------------------------------------------------------------------------
    def pick_result(self):
        path = filedialog.askopenfilename(initialdir=RESOURCE_DIR, filetypes=[("Excel", "*.xlsx")])
        if path:
            self.result_var.set(path)

    def _sync_tender(self):
        """Тендер в работе виден всегда: номер из папки тендера - в поле ссылки (если оно пустое или от
        другого тендера) и в заголовке окна."""
        m = re.search(r"(\d{6,})", Path(self.tender_var.get() or "").name)
        if not m:
            return
        tid = m.group(1)
        if tid not in self.url_var.get():
            self.url_var.set(f"https://achizitii.md/ro/public/tender/{tid}/")
        self.root.title(f"Оценка тендеров - мед. изделия - тендер {tid}")

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
    def _busy(self) -> bool:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Занято", "Предыдущая задача ещё идёт. Дождитесь конца или нажмите «Стоп».")
            return True
        return False

    def _begin(self, title: str):
        self.stop_flag.clear()
        self.stage, self.done_stages = None, []
        self.stage_var.set(f"{title}: запуск…")
        self.stages.configure(text=" → ".join(STAGES))
        self.counts_var.set("")
        self._animate(True)
        self.task_started = self.last_line_at = time.time()
        self.root.after(1000, self._tick)
        logger.info(f"=== {title}: старт ===")

    def _start(self, title: str, target):
        """Задача в фоновом потоке этого процесса (оценка, запись в Excel): лог - через logging."""
        if self._busy():
            return
        self._begin(title)

        def run():
            ok = False
            try:
                target()
                ok = True
                logger.info(f"=== {title}: готово ===")
            except Exception as e:  # ошибка задачи - в лог, окно продолжает работать
                logger.exception(f"{title}: ошибка {type(e).__name__}: {e}")
            self.ui_calls.put(lambda: self._finished(title, ok))

        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()

    def _start_process(self, title: str, args: List[str], on_done=None):
        """Задача отдельным процессом (python <args>): каждая строка его вывода сразу - в лог окна.
        Свежие настройки (data/settings.json) применяются при каждом запуске, «Стоп» завершает процесс."""
        if self._busy():
            return
        self._begin(title)
        cmd = [sys.executable, "-u"] + args
        env = dict(os.environ, PYTHONPATH=str(BASE_DIR), PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        def run():
            ok = False
            try:
                self.proc = subprocess.Popen(cmd, cwd=str(BASE_DIR), env=env, stdout=subprocess.PIPE,
                                             stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                             errors="replace", bufsize=1, creationflags=flags)
                for raw in self.proc.stdout:
                    if raw.strip() and not NOISE.search(raw):
                        self.log_queue.put(normalize_line(raw.rstrip()))
                code = self.proc.wait()
                if self.stop_flag.is_set():
                    logger.warning(f"=== {title}: остановлено ===")
                elif code == 0:
                    ok = True
                    if on_done:
                        on_done()
                    logger.info(f"=== {title}: готово ===")
                else:
                    logger.error(f"=== {title}: процесс завершился с ошибкой (код {code}) - см. строки выше ===")
            except Exception as e:
                logger.exception(f"{title}: ошибка запуска {type(e).__name__}: {e}")
            finally:
                self.proc = None
                self.ui_calls.put(lambda: self._finished(title, ok))

        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()

    def _finished(self, title: str, ok: bool):
        self._animate(False)
        if ok:
            self.progress["value"] = 100
            if self.stage and self.stage not in self.done_stages:
                self.done_stages.append(self.stage)
            self._show_stages()
        self.stage_var.set(f"{title}: {'готово' if ok else 'остановлено / ошибка'}")
        self.now_var.set("")

    def _animate(self, on: bool):
        """Этап без процентов в логе - бегущая полоса (задача жива); с процентами - заполнение."""
        self.progress.stop()
        if on:
            self.progress.configure(mode="indeterminate")
            self.progress.start(15)
        else:
            self.progress.configure(mode="determinate")

    def _show_stages(self):
        self.stages.configure(text=" → ".join(
            f"[{s}]" if s == self.stage and s not in self.done_stages else (f"✓{s}" if s in self.done_stages else s)
            for s in STAGES))

    def _tick(self):
        """Раз в секунду: сколько идёт задача и не молчит ли она - видно, что окно не зависло."""
        if not (self.worker and self.worker.is_alive()):
            self.clock_var.set("")
            return
        elapsed = int(time.time() - self.task_started)
        text = f"идёт {elapsed // 60:02d}:{elapsed % 60:02d}"
        silent = int(time.time() - self.last_line_at)
        if silent >= SILENCE_SECONDS:
            text += f" · новых строк нет {silent} с - ждём ответа (сайт, большой документ)"
        self.clock_var.set(text)
        self.root.after(1000, self._tick)

    def run_tender(self):
        """Скачивание и разбор тендера: src/main.py --console [--tender <номер|URL>] отдельным процессом."""
        tender = self.url_var.get().strip()
        if not tender and self.mode != "parent":         # пустое поле - не запускать молча по родительскому
            parent, url = parent_info()
            if not parent:
                messagebox.showwarning("Тендер", "Введите номер или ссылку тендера "
                                                 "(родительского файла resources/-*.xlsx нет).")
                return
            if not messagebox.askyesno("Тендер", f"Номер тендера не указан. Запустить по родительскому файлу "
                                                 f"{parent.name}?" + (f"\n{url}" if url else "")):
                return
            self.mode = "parent"
        args = [str(Path(BASE_DIR) / "src" / "main.py"), "--console"] + (["--tender", tender] if tender else [])
        m = re.search(r"tender/(\d+)", tender) or re.fullmatch(r"\s*(\d+)\s*", tender)

        def done():
            result = latest_result()
            folder = Path(BASE_DIR) / "downloads" / m.group(1) if m else latest_tender_dir()

            def show():
                if result:
                    self.result_var.set(str(result))
                if folder:
                    self.tender_var.set(str(folder))
            self.ui_calls.put(show)
        self._start_process("Тендер", args, done)

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
                    self.ui_calls.put(lambda n=done["n"]: self._progress(n))

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

    def run_amdm(self):
        """Номера DM из result - в реестр AMDM (новые - на сайте) до «Оценки»: оценка сверяет только с
        локальной копией реестра, и непроверенные номера ушли бы человеку вопросами."""
        paths = self._paths()
        if not paths:
            return
        result, _ = paths
        self._start_process("AMDM", ["-m", "src.amdm", "check", str(result)])

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

        out = result.with_name(result.stem + "_clarificari.xlsx")
        self._start_process("Уточнения", ["-m", "src.clarificari", str(result), str(tender), "--dir", str(clar),
                                          "--out", str(out)])

    def stop(self):
        self.stop_flag.set()
        proc = self.proc
        if proc and proc.poll() is None:
            logger.warning("Остановка: процесс завершается (скачанное сохранено, при повторе пропустится)")
            proc.terminate()
        elif self.worker and self.worker.is_alive():
            logger.info("Остановка: текущий лот доделывается, дальше - стоп")

    def _progress(self, lots_done: int):
        self.counts_var.set(f"лотов оценено: {lots_done}")

    # --- лог и этап -----------------------------------------------------------------------------------------
    def _attach_log(self):
        """Обработчик окна - на корневом логгере. Модуль, вызвавший logging.basicConfig(force=True), его
        снимает - тогда лог в окне пропадал; проверяется при каждом опросе и возвращается."""
        root = logging.getLogger()
        if self.handler not in root.handlers:
            root.addHandler(self.handler)
        if root.level > logging.INFO or root.level == logging.NOTSET:
            root.setLevel(logging.INFO)

    def _poll_log(self):
        self._attach_log()
        lines = []
        while True:
            try:
                lines.append(self.log_queue.get_nowait())
            except queue.Empty:
                break
        if lines:
            self.log_text.configure(state="normal")
            self.last_line_at = time.time()
            self.now_var.set("сейчас: " + re.sub(r"^\[[\d:]+\]\s*", "", lines[-1])[:180])
            for line in lines:
                self.log_text.insert("end", line + "\n", log_tag(line) or ())
                stage = stage_of(line)
                if stage and stage != self.stage:
                    if self.stage and self.stage not in self.done_stages:
                        self.done_stages.append(self.stage)
                    self.stage = stage
                    self.stage_var.set(f"Этап: {stage}")
                    self._show_stages()
                    if self.worker and self.worker.is_alive():
                        self._animate(True)
                m = PROGRESS_RE.search(line)
                if m:
                    if str(self.progress.cget("mode")) != "determinate":
                        self._animate(False)
                    self.progress["value"] = min(100.0, float(m.group(1)))
            extra = int(self.log_text.index("end-1c").split(".")[0]) - MAX_LOG_LINES
            if extra > 0:
                self.log_text.delete("1.0", f"{extra + 1}.0")
            self.log_text.configure(state="disabled")
            self.log_text.see("end")
        while True:
            try:
                self.ui_calls.get_nowait()()
            except queue.Empty:
                break
        self.root.after(200, self._poll_log)

    # --- настройки ------------------------------------------------------------------------------------------
    def open_settings(self):
        """config.py + data/settings.json: правка в окне; сохраняются только отличия от значений по умолчанию."""
        win = tk.Toplevel(self.root)
        win.title("Настройки")
        win.transient(self.root)
        frame = ttk.Frame(win, padding=10)
        frame.pack(fill="both", expand=True)
        current = app_config.current_settings()
        fields = {}

        def shown(value):
            return ", ".join(value) if isinstance(value, list) else str(value)

        for i, (name, (label, kind)) in enumerate(app_config.EDITABLE.items()):
            ttk.Label(frame, text=label).grid(row=i, column=0, sticky="w", padx=(0, 8), pady=2)
            if kind is bool:
                var = tk.BooleanVar(value=bool(current[name]))
                ttk.Checkbutton(frame, variable=var).grid(row=i, column=1, sticky="w")
            else:
                var = tk.StringVar(value=shown(current[name]))
                ttk.Entry(frame, textvariable=var, width=60 if kind is str else 20).grid(row=i, column=1, sticky="w")
            ttk.Label(frame, text=f"по умолчанию: {shown(app_config.DEFAULTS[name])}",
                      foreground="#777").grid(row=i, column=2, sticky="w", padx=8)
            fields[name] = var
        n = len(fields)
        ttk.Label(frame, text=f"Файл: {app_config.SETTINGS_FILE}\nТендер, AMDM и уточнения читают настройки при "
                              f"каждом запуске; порог цены для «Оценки» применяется сразу.",
                  foreground="#555").grid(row=n, column=0, columnspan=3, sticky="w", pady=(8, 4))

        def save():
            try:
                path = app_config.save_settings({k: v.get() for k, v in fields.items()})
            except ValueError as e:
                messagebox.showerror("Настройки", str(e), parent=win)
                return
            logger.info(f"[UI] Настройки сохранены: {path}")
            win.destroy()

        def reset():
            for name, var in fields.items():
                default = app_config.DEFAULTS[name]
                var.set(default if isinstance(var, tk.BooleanVar) else shown(default))

        row = ttk.Frame(frame)
        row.grid(row=n + 1, column=0, columnspan=3, sticky="e")
        ttk.Button(row, text="По умолчанию", command=reset).pack(side="left", padx=4)
        ttk.Button(row, text="Отмена", command=win.destroy).pack(side="left", padx=4)
        ttk.Button(row, text="Сохранить", command=save).pack(side="left", padx=4)

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
                text = "Карточка: " + card_title(q["payload"].get("card") or {"model_key": q["model_key"]})
            elif q["kind"] == "medical_type":
                text = f"Мед. изделие? «{q['model_key']}»"
            else:
                text = f"Лот {q['lot_no']}: {q['participant']}"
            self.q_list.insert("end", text)
            if q["kind"] == "offer_status":
                self.q_list.itemconfig(i, background=STATUS_COLORS.get(q["payload"].get("suggested"), "white"))
            if q["id"] == selected:
                self.q_list.selection_set(i)
        cards = sum(q["kind"] == "product_card" for q in self.questions)
        self.qf.configure(text=f"Вопросы к человеку - открыто {len(self.questions)} (карточек: {cards})")
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
        elif self.current["kind"] == "medical_type":
            self._medical_form(self.current)
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
            self._answered()
        ttk.Button(self.form, text="Сохранить решение", command=save).pack(anchor="e", pady=6)

    def _medical_form(self, q: Dict):
        """Реестр AMDM не ответил, регистрируются ли изделия этого типа: решает человек, один раз для всех тендеров."""
        p, word = q["payload"], q["model_key"]
        ttk.Label(self.form, text=f"Медицинское ли изделие «{word}»?", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        ttk.Label(self.form, wraplength=540, justify="left", text=(
            "У этих оферт нет номера DM. Тип изделия - первое слово названия лота. Если такие изделия не "
            "медицинские (бумага для принтера, DVD, конверты), регистрация AMDM не нужна - достаточно ISO/CE. "
            "Если медицинские - без номера DM оферта остаётся жёлтой (поиск в реестре по коду модели).")
                  ).pack(anchor="w", pady=(0, 6))
        registry = "; ".join(f"запрос «{r['query']}» - записей: {r['total'] if r['total'] is not None else '?'}"
                             for r in p.get("registry", []))
        self._text(self.form, "Реестр AMDM", (registry or "не проверялся") + "\nНоль записей при запросе без "
                   "диакритики ничего не доказывает: в реестре слово может быть написано с ней.", 3)
        offers = "\n".join(f"{o['sheet']}, {o['participant']}: {o['model'] or '—'}"
                            f"{' (' + ', '.join(o['quality_docs']) + ')' if o.get('quality_docs') else ''}"
                            f"\n    {o['lot_title']}" for o in p.get("offers", []))
        more = p.get("offers_total", 0) - len(p.get("offers", []))
        self._text(self.form, f"Оферты ({p.get('offers_total', 0)})", offers + (f"\n… и ещё {more}" if more > 0 else ""), 8)
        comment = tk.StringVar()
        ttk.Label(self.form, text="Комментарий:").pack(anchor="w")
        ttk.Entry(self.form, textvariable=comment, width=80).pack(anchor="w")

        def save(medical: bool):
            answer_medical(self.kb, q["id"], medical, comment.get())
            logger.info(f"[UI] Тип «{word}»: {'медицинское' if medical else 'не медицинское'} изделие - "
                        f"применится при следующей проверке AMDM / оценке")
            self._answered()
        row = ttk.Frame(self.form)
        row.pack(anchor="w", pady=6)
        ttk.Button(row, text="Медицинское - нужна регистрация AMDM", command=lambda: save(True)).pack(fill="x", pady=2)
        ttk.Button(row, text="Не медицинское - достаточно ISO/CE", command=lambda: save(False)).pack(fill="x", pady=2)

    def _open_cards_tab(self):
        if self.tabs.select() != str(self.cards_frame):
            return None
        if self.cards_tab is None:
            self.cards_tab = CardsTab(self.cards_frame, self.kb, self._card_dir, self._cards_changed,
                                      result_fn=self._card_result)
        else:
            self.cards_tab.reload()
        return self.cards_tab

    def _cards_changed(self):
        """Карточку сохранили / удалили во вкладке базы: выгрузить ответы для git, обновить вопросы."""
        try:
            answers_sync.export(self.kb, answers_sync.own_file(self.answers_dir))
        except Exception as e:
            logger.warning(f"[SYNC] Карточка сохранена в базе, но не выгружена в файл: {e}")
        self.refresh_questions()

    def _card_dir(self, tender_id: str) -> Optional[Path]:
        """Папка тендера вопроса: из поля «Папка тендера», если тот же тендер, иначе downloads/<номер>."""
        current = Path(self.tender_var.get() or "")
        if (current / "manifest.sqlite").is_file() and (not tender_id or tender_id in current.name):
            return current
        folder = Path(BASE_DIR) / "downloads" / str(tender_id or "")
        return folder if (folder / "manifest.sqlite").is_file() else None

    def _card_result(self, tender_id: str) -> Optional[Path]:
        """Result-файл, если он того же тендера, что в «Папке тендера» (цена оферты в текущем тендере)."""
        result = Path(self.result_var.get() or "")
        current = Path(self.tender_var.get() or "")
        if result.is_file() and tender_id and tender_id in current.name:
            return result
        return None

    def _card_form(self, q: Dict):
        def save(card: Dict):
            answer_card(self.kb, q["id"], card)
            logger.info(f"[UI] Карточка подтверждена: {card_title(card)} [{card['model_key']}]"
                        + (f"; агенты: {card['agents']}" if card.get("agents") else ""))
            self._answered()
        self.card_form = CardForm(self.form, self.kb, q, self._card_dir(q.get("tender_id")), save,
                                  result=self._card_result(q.get("tender_id")))


def main(argv=None) -> int:
    argv = list(argv or [])
    result = Path(argv[0]) if len(argv) > 0 else None
    tender = Path(argv[1]) if len(argv) > 1 else None
    root = new_root()
    app = App(root, result, tender)
    if not tender:                                   # окно открыто без тендера - спросить, как работаем
        root.after(300, app.ask_start)
    root.mainloop()
    return 0
