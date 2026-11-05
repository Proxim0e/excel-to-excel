"""
Просмотр документов участника: выбор документа (оферта + уточнения), «Открыть PDF», поиск с подсветкой.
Используется во вкладке «Проверка лотов»; при открытии документа сразу ищется код модели / номер DM.
"""
import os
import tkinter as tk
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
        self.on_selection = on_selection
        self.frame = ttk.Frame(parent)
        bar = ttk.Frame(self.frame)
        bar.pack(fill="x")
        self.doc_var = tk.StringVar()
        self.combo = ttk.Combobox(bar, textvariable=self.doc_var, state="readonly", width=60)
        self.combo.pack(side="left")
        self.combo.bind("<<ComboboxSelected>>", lambda e: self.load(self.combo.current()))
        ttk.Button(bar, text="Открыть PDF", command=self.open_pdf).pack(side="left", padx=4)
        ttk.Label(bar, text="Поиск:").pack(side="left", padx=(10, 2))
        self.search_var = tk.StringVar()
        se = ttk.Entry(bar, textvariable=self.search_var, width=24)
        se.pack(side="left")
        se.bind("<Return>", lambda e: self.find_next())
        ttk.Button(bar, text="Найти", command=self.find_next).pack(side="left", padx=2)
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
        self.combo.configure(values=[d["label"] for d in docs])
        self.search_var.set("")
        if docs:
            self.combo.current(0)
            self.load(0)
        else:
            self.doc_var.set("")
            self._set("Документов участника нет (нет манифеста тендера или папки уточнений).")

    def _set(self, text: str):
        self.text.delete("1.0", "end")
        self.text.insert("1.0", text)
        self.hits_var.set("")

    def load(self, index: int):
        if not (0 <= index < len(self.docs)):
            return
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

    def open_pdf(self):
        index = self.combo.current()
        if 0 <= index < len(self.docs):
            path = self.docs[index]["path"]
            if Path(path).is_file():
                os.startfile(str(path))       # noqa: программа просмотра PDF по умолчанию
            else:
                messagebox.showwarning("PDF", f"Файл не найден: {path}")
