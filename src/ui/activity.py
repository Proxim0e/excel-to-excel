"""
Журнал действий окна - как список задач у Claude: что сделано (✓), что идёт сейчас (⟳ и текущий шаг под ним),
что сорвалось (✗). Одна модель на окно (`Activity`), вид (`ActivityView`) - в левом нижнем углу каждой вкладки.

Источники: строки лога окна (`feed_log`: «=== Задача: старт/готово ===», «[REVIEW] …» - решения, [ERROR]) и
явные вызовы (`start` / `finish` / `note`) - проверка AMDM по кнопке, запись в Excel.
"""
from __future__ import annotations

import re
import time
import tkinter as tk
from dataclasses import dataclass, field
from tkinter import ttk
from typing import Dict, Hashable, List, Optional

RUNNING, DONE, FAILED, INFO = "run", "ok", "fail", "info"
ICON = {RUNNING: "⟳", DONE: "✓", FAILED: "✗", INFO: "•"}
COLOR = {RUNNING: "#1565c0", DONE: "#2e7d32", FAILED: "#c62828", INFO: "#555555"}
MAX_ENTRIES = 300

LINE = re.compile(r"^\[(\d\d:\d\d:\d\d)\]\s*(.*)$")
TASK = re.compile(r"^=== (.+?): (старт|готово|остановлено|процесс завершился с ошибкой.*|ошибка.*) ===")


@dataclass
class Entry:
    text: str
    state: str
    started: float = field(default_factory=time.time)
    ended: Optional[float] = None
    detail: str = ""          # текущий шаг идущей задачи
    steps: int = 0            # строк лога за время задачи
    key: Optional[Hashable] = None


def _duration(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 60}:{sec % 60:02d}" if sec >= 60 else f"{sec} с"


class Activity:
    def __init__(self, root: Optional[tk.Misc] = None):
        self.root = root
        self.entries: List[Entry] = []
        self.by_key: Dict[Hashable, Entry] = {}
        self.views: List["ActivityView"] = []
        self._scheduled = False

    # --- изменения -------------------------------------------------------------------------------------------
    def start(self, key: Hashable, text: str) -> Entry:
        e = self.by_key.get(key)
        if e is not None and e.state == RUNNING:
            e.text = text
        else:
            e = Entry(text, RUNNING, key=key)
            self.by_key[key] = e
            self._add(e)
        self.changed()
        return e

    def step(self, key: Hashable, detail: str):
        e = self.by_key.get(key)
        if e is not None and e.state == RUNNING:
            e.detail, e.steps = detail, e.steps + 1
            self.changed()

    def finish(self, key: Hashable, text: Optional[str] = None, ok: bool = True):
        e = self.by_key.get(key)
        if e is None or e.state != RUNNING:
            if text:
                self.note(text, ok)
            return
        e.state, e.ended, e.detail = (DONE if ok else FAILED), time.time(), ""
        if text:
            e.text = text
        self.changed()

    def note(self, text: str, ok: Optional[bool] = True):
        e = Entry(text, INFO if ok is None else (DONE if ok else FAILED))
        e.ended = e.started
        self._add(e)
        self.changed()

    def running(self) -> List[Entry]:
        return [e for e in self.entries if e.state == RUNNING]

    def _add(self, e: Entry):
        self.entries.append(e)
        if len(self.entries) > MAX_ENTRIES:
            for old in self.entries[:-MAX_ENTRIES]:
                if old.key is not None and self.by_key.get(old.key) is old:
                    del self.by_key[old.key]
            self.entries = self.entries[-MAX_ENTRIES:]

    # --- лог окна --------------------------------------------------------------------------------------------
    def feed_log(self, line: str):
        m = LINE.match(line)
        msg = (m.group(2) if m else line).strip()
        if not msg:
            return
        t = TASK.match(msg)
        if t:
            title, what = t.groups()
            key = ("task", title)
            if what == "старт":
                self.start(key, title)
            else:
                self.finish(key, ok=(what == "готово"))
                if what != "готово":
                    e = self.by_key.get(key)
                    if e is not None:
                        e.text = f"{title}: {what}"
            return
        tag = re.match(r"^\[(REVIEW|UI|CARDS)\]\s*", msg)      # действия человека в окне
        if tag:
            self.note(msg[tag.end():])
            return
        if msg.startswith("[ERROR]") or msg.startswith("[CRITICAL]"):
            self.note(re.sub(r"^\[\w+\]\s*", "", msg), ok=False)
        for e in reversed(self.entries):          # текущий шаг - под последней идущей задачей
            if e.state == RUNNING and isinstance(e.key, tuple) and e.key[0] == "task":
                self.step(e.key, re.sub(r"^\[\w+\]\s*", "", msg))
                break

    # --- показ ----------------------------------------------------------------------------------------------
    def changed(self):
        if self.root is None:
            return
        if not self._scheduled:
            self._scheduled = True
            self.root.after(150, self._render)

    def _render(self):
        self._scheduled = False
        for v in list(self.views):
            v.render(self.entries)

    def tick(self):
        """Раз в секунду - время у идущих задач."""
        if self.running():
            self.changed()
        if self.root is not None:
            self.root.after(1000, self.tick)


class ActivityView:
    """Журнал действий в одной вкладке: старые сверху, новые снизу; идущие - синим с текущим шагом."""

    def __init__(self, parent: tk.Misc, activity: Activity, height: int = 10):
        self.activity = activity
        self.frame = ttk.Labelframe(parent, text="Действия")
        self.text = tk.Text(self.frame, height=height, wrap="word", font=("Segoe UI", 9), state="disabled",
                            relief="flat", background="#fafafa", padx=4, pady=2, cursor="arrow")
        sy = ttk.Scrollbar(self.frame, command=self.text.yview)
        self.text.configure(yscrollcommand=sy.set)
        sy.pack(side="right", fill="y")
        self.text.pack(fill="both", expand=True)
        for state, color in COLOR.items():
            self.text.tag_configure(state, foreground=color)
        self.text.tag_configure("time", foreground="#999999")
        self.text.tag_configure("detail", foreground="#666666", lmargin1=18, lmargin2=18,
                                font=("Segoe UI", 8))
        activity.views.append(self)
        self.render(activity.entries)

    def render(self, entries: List[Entry]):
        if not self.text.winfo_exists():
            if self in self.activity.views:
                self.activity.views.remove(self)
            return
        at_end = self.text.yview()[1] > 0.98
        now = time.time()
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        for e in entries:
            self.text.insert("end", time.strftime("%H:%M ", time.localtime(e.started)), "time")
            self.text.insert("end", f"{ICON[e.state]} {e.text}", e.state)
            if e.state == RUNNING:
                self.text.insert("end", f"  ({_duration(now - e.started)})", "time")
                if e.detail:
                    self.text.insert("end", "\n" + e.detail[:220], "detail")
            elif e.ended and e.ended - e.started >= 1:
                self.text.insert("end", f"  ({_duration(e.ended - e.started)})", "time")
            self.text.insert("end", "\n")
        self.text.configure(state="disabled")
        if at_end or any(e.state == RUNNING for e in entries[-3:]):
            self.text.see("end")
