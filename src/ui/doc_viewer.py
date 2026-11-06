"""
Просмотр документов участника: выбор документа (оферта + уточнения) с поиском по названию, «Открыть PDF»,
поиск по тексту с подсветкой.
Используется во вкладке «Проверка лотов»; при открытии документа сразу ищется код модели / номер DM.
"""
import os
import subprocess
import tkinter as tk
import unicodedata
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Callable, Dict, List, Optional, Sequence

from src.ui import card_data as cd


class DocViewer:
    def __init__(self, parent, on_selection: Optional[Callable[[str], None]] = None):
        self.docs: List[Dict] = []
        self.probes: Sequence[str] = ()
        self.hits: List[str] = []
        self.hit_pos = -1
        self.shown: List[int] = []        # документы в списке (после поиска по названию) -> индексы self.docs
        self.cur = -1                     # открытый документ (индекс self.docs)
        self.on_selection = on_selection
        self.frame = ttk.Frame(parent)
        bar = ttk.Frame(self.frame)
        bar.pack(fill="x")
        ttk.Label(bar, text="Документ:").pack(side="left", padx=(0, 2))
        self.name_var = tk.StringVar()          # поиск по названию документа
        ne = ttk.Entry(bar, textvariable=self.name_var, width=16)
        ne.pack(side="left")
        self.name_var.trace_add("write", lambda *_: self.filter_docs())
        ne.bind("<Return>", lambda e: self.filter_docs(open_first=True))
        ttk.Button(bar, text="×", width=2, command=lambda: self.name_var.set("")).pack(side="left")
        self.doc_var = tk.StringVar()
        self.combo = ttk.Combobox(bar, textvariable=self.doc_var, state="readonly", width=56)
        self.combo.pack(side="left", padx=(2, 0))
        self.combo.bind("<<ComboboxSelected>>", lambda e: self._combo_picked())
        self.count_var = tk.StringVar()
        ttk.Label(bar, textvariable=self.count_var, foreground="#555").pack(side="left", padx=2)
        ttk.Button(bar, text="Открыть PDF", command=self.open_pdf).pack(side="left", padx=(4, 0))
        ttk.Button(bar, text="Папка", command=self.open_folder).pack(side="left", padx=(2, 4))
        ttk.Label(bar, text="Поиск:").pack(side="left", padx=(10, 2))
        self.search_var = tk.StringVar()
        se = ttk.Entry(bar, textvariable=self.search_var, width=24)
        se.pack(side="left")
        se.bind("<Return>", lambda e: self.find_next())
        ttk.Button(bar, text="Найти", command=self.find_next).pack(side="left", padx=2)
        ttk.Button(bar, text="Копировать", command=self.copy_search).pack(side="left", padx=2)
        self.hits_var = tk.StringVar()
        ttk.Label(bar, textvariable=self.hits_var, foreground="#555").pack(side="left", padx=4)

        body = ttk.Frame(self.frame)
        body.pack(fill="both", expand=True)
        self.text = tk.Text(body, wrap="word", font=("Consolas", 9), height=10)
        sy = ttk.Scrollbar(body, command=self.text.yview)
        self.text.configure(yscrollcommand=sy.set)
        sy.pack(side="right", fill="y")
        self.text.pack(fill="both", expand=True)
        self.text.tag_configure("hit", background="#fff59d")
        self.text.tag_configure("cur", background="#ffb74d")
        # только чтение, но с выделением и Ctrl+C / Ctrl+A (и при русской раскладке - по коду клавиши)
        self.text.bind("<Key>", lambda e: None if (e.state & 4 and (e.keysym.lower() in ("c", "a")
                                                                    or e.keycode in (67, 65))) else "break")

    def set_docs(self, docs: List[Dict], probes: Sequence[str] = ()):
        """Документы участника; probes - что искать сразу (код модели, номер DM)."""
        self.docs, self.probes = docs, [p for p in probes if p and len(p) >= 3]
        self.search_var.set("")
        self.cur = -1
        self.filter_docs(open_first=True)       # поиск по названию остаётся между участниками
        if docs and self.cur < 0:               # по названию ничего - весь список, первый документ
            self.name_var.set("")
        if not docs:
            self.doc_var.set("")
            self._set("Документов участника нет (нет манифеста тендера или папки уточнений).")

    @staticmethod
    def _plain(text: str) -> str:
        """Без регистра и диакритики: «Fișa tehnică» ищется как «fisa tehnica»."""
        decomposed = unicodedata.normalize("NFKD", text.lower())
        return "".join(c for c in decomposed if not unicodedata.combining(c))

    @classmethod
    def _haystack(cls, doc: Dict) -> str:
        return cls._plain(f"{doc.get('label', '')} {Path(str(doc.get('path', ''))).name}")

    def filter_docs(self, open_first: bool = False):
        """Список документов - только с названием, где есть все слова поиска; открытый остаётся, если подходит."""
        words = self._plain(self.name_var.get()).split()
        self.shown = [i for i, d in enumerate(self.docs) if all(w in self._haystack(d) for w in words)]
        self.combo.configure(values=[self.docs[i]["label"] for i in self.shown])
        self.count_var.set(f"{len(self.shown)} из {len(self.docs)}" if words else "")
        if not self.shown:
            self.doc_var.set("")
            return
        if self.cur in self.shown and not open_first:
            self.combo.current(self.shown.index(self.cur))
            return
        if open_first or self.cur not in self.shown:
            self.combo.current(0)
            self.load(self.shown[0])

    def _combo_picked(self):
        i = self.combo.current()
        if 0 <= i < len(self.shown):
            self.load(self.shown[i])

    def _set(self, text: str):
        self.text.delete("1.0", "end")
        self.text.insert("1.0", text)
        self.hits_var.set("")

    def load(self, index: int):
        if not (0 <= index < len(self.docs)):
            return
        self.cur = index
        self._set(cd.document_text(self.docs[index]["path"]))
        if not self.search_var.get().strip():
            for probe in self.probes:
                if self.text.search(probe, "1.0", nocase=True):
                    self.search_var.set(probe)
                    break
        self.hit_pos = -1
        self.find_next()

    def find_next(self):
        t, query = self.text, self.search_var.get().strip()
        t.tag_remove("hit", "1.0", "end")
        t.tag_remove("cur", "1.0", "end")
        self.hits, start = [], "1.0"
        while query:
            pos = t.search(query, start, stopindex="end", nocase=True)
            if not pos:
                break
            end = f"{pos}+{len(query)}c"
            t.tag_add("hit", pos, end)
            self.hits.append(pos)
            start = end
        if not query:
            self.hits_var.set("")
            return
        if not self.hits:
            self.hits_var.set("не найдено")
            return
        self.hit_pos = (self.hit_pos + 1) % len(self.hits)
        pos = self.hits[self.hit_pos]
        t.tag_add("cur", pos, f"{pos}+{len(query)}c")
        t.see(pos)
        self.hits_var.set(f"{self.hit_pos + 1}/{len(self.hits)}")

    def copy_search(self) -> str:
        """Текст поиска (код модели, номер DM) - в буфер обмена; поле пустое - выделенный текст документа."""
        text = self.search_var.get().strip()
        if not text:
            try:
                text = self.text.get("sel.first", "sel.last").strip()
            except tk.TclError:
                text = ""
        if text:
            self.frame.clipboard_clear()
            self.frame.clipboard_append(text)
            self.hits_var.set("скопировано")
        return text

    def open_folder(self):
        """Расположение файла: Проводник с выделенным документом (файла нет - его папка, если есть)."""
        if not (0 <= self.cur < len(self.docs)):
            return
        path = Path(self.docs[self.cur]["path"]).resolve()
        # строкой, а не списком: Popen взял бы в кавычки весь «/select,C:\Users\Capcs 1\...» (путь с пробелом),
        # Проводник такого не понимает и открывает «Документы»; нужно /select,"путь"
        if path.is_file():
            self.launch(f'explorer /select,"{path}"')
        elif path.parent.is_dir():
            self.launch(f'explorer "{path.parent}"')
        else:
            messagebox.showwarning("Папка", f"Папка не найдена: {path.parent}")

    @staticmethod
    def launch(command: str):
        subprocess.Popen(command)     # noqa: Проводник Windows (командная строка как есть)

    def open_pdf(self):
        index = self.cur
        if 0 <= index < len(self.docs):
            path = self.docs[index]["path"]
            if Path(path).is_file():
                os.startfile(str(path))       # noqa: программа просмотра PDF по умолчанию
            else:
                messagebox.showwarning("PDF", f"Файл не найден: {path}")
