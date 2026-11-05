"""
Участники лота колонками, как в рабочем Excel: строка 1 (вердикт) цветом, «Ofertant», производитель / страна /
модель, DM, цена за единицу, сумма, % базы, спецификация (строка 11) цветом ячейки; справа от участника - его
колонки уточнений («clarificări (runda N)»: что запрошено, документы, итог цветом). Клик по колонке - выбор.
"""
import re
import tkinter as tk
from tkinter import ttk
from typing import Callable, List, Optional, Tuple

from src.excel import review as rv
from src.excel.verdicts import BLUE, GREEN, RED, YELLOW

COLUMN_WIDTH = 270
CELL_BG = {GREEN: "#00B050", YELLOW: "#FFFF00", RED: "#FF0000", BLUE: "#00B0F0", rv.NAVY: "#001F4D",
           "FFFFC000": "#FFC000", "FF92D050": "#92D050"}
DARK = {GREEN, RED, rv.NAVY}          # на тёмной заливке - белый текст
SELECTED = "#1565c0"
FIELD = re.compile(r"^[^\W\d][\w .()/-]{0,30}?:\s*(.+)$")   # «Producator: X», «Țara: Y», «Model: Z»
MODEL_KEY = re.compile(r"^\s*(model|cod|catalog|ref|art)", re.IGNORECASE)
MODEL_SPLIT = re.compile(r"\s*[+;,|&]\s*|\s+/\s+|\s+(?:și|si|şi|and|и)\s+", re.IGNORECASE)
AMDM_BG = {"verde": "#00B050", "galben": "#FFFF00", "rosu": "#FF0000", "fara DM": "#FFC000", "neverificat": "#dddddd"}


def _bg(color: Optional[str], default: str = "#ffffff") -> str:
    return CELL_BG.get((color or "").upper(), default)


def _fg(color: Optional[str]) -> str:
    return "#ffffff" if (color or "").upper() in DARK else "#000000"


def money(v: Optional[float]) -> str:
    return f"{v:,.2f} L".replace(",", " ").replace(".", ",") if v is not None else ""


class OfferStrip:
    def __init__(self, parent, on_select: Callable[[int], None], on_amdm: Optional[Callable[[int], None]] = None,
                 on_confirm: Optional[Callable[[int], None]] = None):
        self.on_select = on_select
        self.on_amdm = on_amdm                       # кнопка «AMDM» у номера DM: проверить по реестру
        self.on_confirm = on_confirm                 # кнопка «✓ DM/ISO ок»: регистрацию проверил человек
        self.amdm: dict = {}                         # (лот, колонка) -> (статус | None, текст): результат проверки
        self.amdm_labels: dict = {}                  # индекс участника -> метка результата под DM
        self.lot: Optional[rv.Lot] = None
        self.copied = ""                             # последнее скопированное щелчком значение
        self.frame = ttk.Frame(parent)
        self.canvas = tk.Canvas(self.frame, highlightthickness=0, background="#e8e8e8")
        sx = ttk.Scrollbar(self.frame, orient="horizontal", command=self.canvas.xview)
        sy = ttk.Scrollbar(self.frame, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=sx.set, yscrollcommand=sy.set)
        sx.pack(side="bottom", fill="x")
        sy.pack(side="right", fill="y")
        self.canvas.pack(fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, background="#e8e8e8")
        self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.columns: List[List[tk.Frame]] = []     # по участнику: его колонка и колонки уточнений
        self.selected: Optional[int] = None
        self.canvas.bind("<Shift-MouseWheel>", self._wheel_x)
        self.canvas.bind("<MouseWheel>", self._wheel_y)

    def _wheel_x(self, e):
        self.canvas.xview_scroll(-1 if e.delta > 0 else 1, "units")
        return "break"

    def _wheel_y(self, e):
        self.canvas.yview_scroll(-1 if e.delta > 0 else 1, "units")
        return "break"

    # --- построение --------------------------------------------------------------------------------------
    def set_lot(self, lot: Optional[rv.Lot], reviewed: Callable[[rv.Offer], bool] = lambda o: False,
                system: Callable[[rv.Offer], str] = lambda o: ""):
        for child in self.inner.winfo_children():
            child.destroy()
        self.columns, self.selected, self.amdm_labels, self.lot = [], None, {}, lot
        if lot is None:
            return
        if not lot.offers:
            tk.Label(self.inner, text="Оферт нет - закупка не состоялась (вкладка тёмно-синяя)",
                     font=("Segoe UI", 11), background="#e8e8e8").pack(padx=10, pady=10)
            return
        col = 0
        for i, o in enumerate(lot.offers):
            frames = [self._offer_column(col, i, o, lot, reviewed(o), system(o))]
            col += 1
            for c in o.clar:
                frames.append(self._clar_column(col, i, c))
                col += 1
            self.columns.append(frames)
        self.canvas.xview_moveto(0)
        self.canvas.yview_moveto(0)

    def _box(self, col: int, index: int) -> tk.Frame:
        box = tk.Frame(self.inner, background="#ffffff", highlightthickness=3, highlightbackground="#e8e8e8",
                       width=COLUMN_WIDTH)
        box.grid(row=0, column=col, sticky="ns", padx=2, pady=2)
        box.index = index
        return box

    def _cell(self, box, text: str, color: Optional[str] = None, bold: bool = False, size: int = 10,
              height: Optional[int] = None, anchor: str = "center"):
        lab = tk.Label(box, text=text, background=_bg(color), foreground=_fg(color), wraplength=COLUMN_WIDTH - 12,
                       justify="center" if anchor == "center" else "left", anchor=anchor,
                       font=("Segoe UI", size, "bold" if bold else "normal"), relief="solid", borderwidth=1,
                       padx=4, pady=2)
        if height:
            lab.configure(height=height)
        lab.pack(fill="x")
        return lab

    @staticmethod
    def fields(text: str) -> List[Tuple[str, str]]:
        """«Producator: X / Țara: Y / Model: Z» -> [(строка, значение)]; строка без «Ключ:» продолжает предыдущую."""
        out: List[Tuple[str, str]] = []
        for line in (text or "").splitlines():
            line = line.strip()
            if not line:
                continue
            m = FIELD.match(line)
            if m or not out:
                out.append((line, (m.group(1) if m else line).strip()))
            else:
                prev_line, prev_value = out[-1]
                out[-1] = (f"{prev_line} {line}", f"{prev_value} {line}")
        return out

    @staticmethod
    def parts(line: str, value: str) -> List[str]:
        """Несколько моделей / кодов в одной строке («A + B», «A, B», «A; B», «A și B», «A / B») - по отдельности.
        Только для строк «Model / Cod / Catalog / Ref / Art»; «/» без пробелов - часть кода (AB-12/3)."""
        if not MODEL_KEY.match(line):
            return []
        items = [p.strip(" .") for p in MODEL_SPLIT.split(value)]
        items = [p for p in items if p]
        return items if len(items) > 1 else []

    def copy(self, widget, value: str):
        """Значение в буфер обмена, строка на миг зеленеет."""
        widget.clipboard_clear()
        widget.clipboard_append(value)
        self.copied = value
        bg = widget.cget("background")
        widget.configure(background="#c8e6c9")
        widget.after(600, lambda: widget.winfo_exists() and widget.configure(background=bg))

    def _producer(self, box, text: str):
        """Производитель / страна / модель: каждая строка отдельно, щелчок копирует её значение (без «Ключ:»)."""
        frame = tk.Frame(box, background="#ffffff", relief="solid", borderwidth=1)
        frame.pack(fill="x")
        for line, value in self.fields(text) or [("", "")]:
            lab = tk.Label(frame, text=line + ("   ⧉" if value else ""), background="#ffffff",
                           wraplength=COLUMN_WIDTH - 12, justify="center", font=("Segoe UI", 10), padx=4, pady=0)
            lab.pack(fill="x")
            if value:
                lab.configure(cursor="hand2")
                lab.bind("<Button-1>", lambda e, w=lab, v=value: self.copy(w, v), add="+")
                lab.copy_value = value
            items = self.parts(line, value)
            if items:                       # каждая модель - своя кнопка, по 3 в ряд
                chips = tk.Frame(frame, background="#ffffff")
                chips.pack(pady=(0, 2))
                for n, item in enumerate(items):
                    chip = tk.Label(chips, text=item + " ⧉", background="#eef3fb", relief="groove", borderwidth=1,
                                    font=("Segoe UI", 9), padx=3, pady=0, cursor="hand2")
                    chip.grid(row=n // 3, column=n % 3, padx=1, pady=1, sticky="ew")
                    chip.bind("<Button-1>", lambda e, w=chip, v=item: self.copy(w, v), add="+")
                    chip.copy_value = item
        return frame

    def _spec(self, box, text: str, color: Optional[str]):
        t = tk.Text(box, height=7, wrap="word", font=("Segoe UI", 9), background=_bg(color, "#ffffff"),
                    foreground=_fg(color), relief="solid", borderwidth=1, width=1)
        t.insert("1.0", text)
        t.configure(state="disabled")
        t.pack(fill="both", expand=True)
        return t

    def _offer_column(self, col, i, o: rv.Offer, lot: rv.Lot, done: bool, system: str) -> tk.Frame:
        box = self._box(col, i)
        self._cell(box, ("✓ " if done else "") + (o.row1 or "—"), o.row1_color, bold=True, size=12)
        self._cell(box, f"Ofertant: {o.name}", bold=True)
        self._producer(box, o.producer or "")
        self._dm_row(box, i, o)
        unit = o.value / lot.quantity if o.value and lot.quantity else None
        self._cell(box, f"{money(unit)} / buc.    total {money(o.value)}")
        pct = f"{o.ratio * 100:.2f}%" if o.ratio else ""
        self._cell(box, pct, RED if o.ratio and o.ratio >= 1.3 else None, bold=True)
        self._spec(box, o.spec, o.spec_color)
        if system:
            self._cell(box, "система: " + system, size=9, anchor="w")
        self._clickable(box)
        return box

    def _dm_row(self, box, i: int, o: rv.Offer):
        """DM (цвет строки 4) и кнопка «AMDM» - проверить номер на сайте реестра; результат - строкой ниже."""
        row = tk.Frame(box, background=_bg(o.dm_color))
        row.pack(fill="x")
        btns = tk.Frame(row, background=_bg(o.dm_color))
        btns.pack(side="right", padx=2, pady=1)
        if self.on_amdm:
            b = tk.Button(btns, text="AMDM ⟳", font=("Segoe UI", 8), cursor="hand2", padx=2, pady=0,
                          command=lambda n=i: self.on_amdm(n))
            b.pack(fill="x")
            b.no_select = True
        if self.on_confirm:     # регистрацию проверил человек: по DM или по ISO (ISO убирает номера DM)
            done = rv.ReviewBook.registration_confirmed(o)
            pair = tk.Frame(btns, background=_bg(o.dm_color))
            pair.pack(fill="x", pady=(1, 0))
            for kind, text in (("dm", "✓ DM ок"), ("iso", "✓ ISO ок")):
                c = tk.Button(pair, text=text, font=("Segoe UI", 8), cursor="hand2", padx=1, pady=0,
                              relief="sunken" if done == kind else "raised",
                              background="#c8e6c9" if done == kind else "SystemButtonFace",
                              command=lambda n=i, k=kind: self.on_confirm(n, k))
                c.pack(side="left", fill="x", expand=True)
                c.no_select = True
        lab = tk.Label(row, text=o.dm or "", background=_bg(o.dm_color), foreground=_fg(o.dm_color),
                       wraplength=COLUMN_WIDTH - 80, font=("Segoe UI", 10, "bold"), padx=4, pady=2)
        lab.pack(fill="x")
        if self.on_amdm:
            lab.configure(cursor="hand2")
            lab.bind("<Double-Button-1>", lambda e, n=i: self.on_amdm(n))
        res = tk.Label(box, text="", font=("Segoe UI", 9), wraplength=COLUMN_WIDTH - 12, justify="left",
                       anchor="w", padx=4)
        res.after_row = row
        self.amdm_labels[i] = res
        self._show_amdm(i, o)

    def _show_amdm(self, i: int, o: rv.Offer):
        res = self.amdm_labels.get(i)
        found = self.amdm.get((self.lot.lot_no if self.lot else None, o.col))
        if res is None or found is None:
            return
        status, text = found
        res.configure(text=text, background=AMDM_BG.get(status, "#ffffff"),
                      foreground="#ffffff" if status in ("verde", "rosu") else "#000000")
        if not res.winfo_ismapped():
            res.pack(fill="x", after=res.after_row)             # сразу под строкой DM

    def set_amdm(self, lot_no: int, col: int, status: Optional[str], text: str):
        """Результат проверки AMDM участника (статус None - идёт проверка)."""
        self.amdm[(lot_no, col)] = (status, text)
        if self.lot and self.lot.lot_no == lot_no:
            for i, o in enumerate(self.lot.offers):
                if o.col == col:
                    self._show_amdm(i, o)

    def _clar_column(self, col, i, c: dict) -> tk.Frame:
        box = self._box(col, i)
        self._cell(box, c.get("label") or "clarificări", bold=True, size=11)
        if c.get("request"):
            self._cell(box, c["request"], size=9)
        self._cell(box, c.get("docs") or "", size=9)
        self._spec(box, c.get("note") or "", c.get("color"))
        self._clickable(box)
        return box

    def _clickable(self, box):
        def bind(w):
            if getattr(w, "no_select", False):
                return
            w.bind("<Button-1>", lambda e, n=box.index: self.select(n, notify=True), add="+")
            w.bind("<Shift-MouseWheel>", self._wheel_x, add="+")
            for ch in w.winfo_children():
                bind(ch)
        bind(box)

    # --- выбор -------------------------------------------------------------------------------------------
    def select(self, index: int, notify: bool = False):
        if not (0 <= index < len(self.columns)):
            return
        for n, frames in enumerate(self.columns):
            for f in frames:
                f.configure(highlightbackground=SELECTED if n == index else "#e8e8e8")
        changed = index != self.selected
        self.selected = index
        self.see(index)
        if notify and changed:
            self.on_select(index)

    def see(self, index: int):
        """Прокрутить так, чтобы колонка участника была видна."""
        self.inner.update_idletasks()
        frames = self.columns[index]
        total = max(self.inner.winfo_width(), 1)
        x = frames[0].winfo_x()
        view = self.canvas.winfo_width()
        lo, hi = self.canvas.xview()
        if x < lo * total or x + COLUMN_WIDTH > hi * total:
            self.canvas.xview_moveto(max(0.0, (x - 10) / total) if total > view else 0)
