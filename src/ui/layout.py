"""
Положение разделителей окна (перетаскиваемые границы панелей) - запоминается в data/ui_layout.json:
доля от размера панели, чтобы работало и при другом размере окна. Только удобство: файл можно удалить.
"""
import json
from pathlib import Path
from tkinter import ttk
from typing import Dict, List, Optional


class Layout:
    def __init__(self, path: Path):
        self.path = Path(path)
        try:
            self.data: Dict[str, List[float]] = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}
        self.panes: Dict[str, ttk.Panedwindow] = {}

    def pane(self, name: str, paned: ttk.Panedwindow, default: List[float]) -> ttk.Panedwindow:
        """Запомнить панель: после первого показа - доли из файла (или default), после перетаскивания - запись."""
        self.panes[name] = paned
        fractions = self.data.get(name) or default

        def restore(_=None):
            size = self._size(paned)
            if size < 50:
                return
            paned.unbind("<Map>")
            for i, f in enumerate(fractions[:len(paned.panes()) - 1]):
                try:
                    paned.sashpos(i, int(size * f))
                except Exception:  # панель ещё не разложена
                    pass
        paned.bind("<Map>", lambda e: paned.after(50, restore), add="+")
        paned.bind("<ButtonRelease-1>", lambda e: self.save(name), add="+")
        return paned

    @staticmethod
    def _size(paned: ttk.Panedwindow) -> int:
        return paned.winfo_width() if str(paned.cget("orient")) == "horizontal" else paned.winfo_height()

    def fractions(self, name: str) -> Optional[List[float]]:
        paned = self.panes.get(name)
        if not paned:
            return None
        size = self._size(paned)
        if size < 50:
            return None
        return [round(paned.sashpos(i) / size, 4) for i in range(len(paned.panes()) - 1)]

    def save(self, name: str) -> None:
        f = self.fractions(name)
        if not f:
            return
        self.data[name] = f
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass
