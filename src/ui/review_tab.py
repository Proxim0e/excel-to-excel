"""
Вкладка «Проверка лотов»: человек проходит лоты по номеру, видит, что выдала система (строка 1 Excel, решение
базы знаний, уточнения), открывает документы участника и ставит решение по кодбуку. Решения пишутся в копию
<result>_verificat.xlsx (src/excel/review.py) и в базу знаний (source = human).
"""
import logging
import re
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Dict, List, Optional

from src.evaluate import confidence as cf
from src.excel import review as rv
from src.excel.verdicts import BLUE, GREEN, RED, YELLOW, effective
from src.history.cards import split_dms
from src.ui import card_data as cd
from src.ui.doc_viewer import DocViewer

logger = logging.getLogger(__name__)

FILTERS = ("все", "жёлтые", "не проверенные мной")
COLOR_TAGS = {GREEN: ("green", "#d7f5d7"), YELLOW: ("yellow", "#fff7b0"), RED: ("red", "#f8d0d0"),
              BLUE: ("blue", "#d3eefc"), rv.NAVY: ("navy", "#d0d4e0")}
STATUS_RU = {cf.CORESPUNDE: "corespunde", cf.ALTERNATIVA: "alternativa", cf.NU_CORESPUNDE: "nu corespunde",
             cf.CLARIFICARE: "clarificare", cf.NEEVALUAT: "не оценивался"}
CODE_RE = re.compile(r"\w[\w.\-/]{2,}\w")


def system_text(offer: rv.Offer) -> str:
    s = offer.system
    if not s:
        return "нет решения"
    who = {cf.HUMAN: "я", cf.PENDING: "вопрос", cf.AUTO: "система"}.get(s.get("source"), s.get("source") or "")
    reasons = s.get("reasons") or ([s["reason"]] if s.get("reason") else [])
    text = f"{STATUS_RU.get(s['status'], s['status'])} ({who})"
    return text + (f": {', '.join(map(str, reasons[:2]))}" if reasons else "")


def probes(offer: rv.Offer) -> List[str]:
    """Что искать в документах сразу: коды модели из строки 3, номера DM."""
    codes = [c for c in CODE_RE.findall(offer.producer)
             if re.search(r"\d", c) and not re.fullmatch(r"[\d.,/\-]+", c)]
    return codes + split_dms(offer.dm)


class ReviewTab:
    def __init__(self, parent, app, layout):
        self.app, self.layout = app, layout
        self.book: Optional[rv.ReviewBook] = None
        self.tender_dir: Optional[Path] = None
        self.tender_id = ""
        self.loaded_from: Optional[Path] = None
        self.lot: Optional[rv.Lot] = None
        self.offer: Optional[rv.Offer] = None
        self._shown = None
        self._build(parent)
        self.reload()

    # --- окно --------------------------------------------------------------------------------------------
    def _build(self, parent):
        panes = self.layout.pane("review", ttk.Panedwindow(parent, orient="horizontal"), [0.24])
        panes.pack(fill="both", expand=True)

        left = ttk.Frame(panes, padding=4)
        panes.add(left, weight=1)
        bar = ttk.Frame(left)
        bar.pack(fill="x")
        self.filter_var = tk.StringVar(value=FILTERS[0])
        f = ttk.Combobox(bar, textvariable=self.filter_var, values=FILTERS, state="readonly", width=18)
        f.pack(side="left")
        f.bind("<<ComboboxSelected>>", lambda e: self.fill_lots())
        self.search_var = tk.StringVar()
        se = ttk.Entry(bar, textvariable=self.search_var, width=14)
        se.pack(side="left", padx=4, fill="x", expand=True)
        self.search_var.trace_add("write", lambda *_: self.fill_lots())
        self.file_var = tk.StringVar()
        ttk.Label(left, textvariable=self.file_var, foreground="#555", wraplength=300).pack(fill="x")
        body = ttk.Frame(left)
        body.pack(fill="both", expand=True)
        self.lots = ttk.Treeview(body, columns=("ok", "n", "title"), show="headings", selectmode="browse")
        for col, text, width in (("ok", "✓", 28), ("n", "Лот", 70), ("title", "Название", 220)):
            self.lots.heading(col, text=text)
            self.lots.column(col, width=width, stretch=col == "title", anchor="w" if col == "title" else "center")
        for tag, color in COLOR_TAGS.values():
            self.lots.tag_configure(tag, background=color)
        sy = ttk.Scrollbar(body, command=self.lots.yview)
        self.lots.configure(yscrollcommand=sy.set)
        sy.pack(side="right", fill="y")
        self.lots.pack(fill="both", expand=True)
        self.lots.bind("<<TreeviewSelect>>", lambda e: self._lot_selected())
        self.progress_var = tk.StringVar()
        ttk.Label(left, textvariable=self.progress_var).pack(fill="x")

        right = self.layout.pane("review_right", ttk.Panedwindow(panes, orient="vertical"), [0.55])
        panes.add(right, weight=4)
        top = ttk.Frame(right, padding=4)
        right.add(top, weight=3)

        head = ttk.Frame(top)
        head.pack(fill="x")
        self.lot_var = tk.StringVar()
        ttk.Label(head, textvariable=self.lot_var, font=("Segoe UI", 10, "bold")).pack(side="left")
        ttk.Button(head, text="Следующий лот →", command=self.next_lot).pack(side="right")
        ttk.Button(head, text="Сохранить в Excel", command=self.save).pack(side="right", padx=4)
        ttk.Label(head, text="Вкладка:").pack(side="right")
        self.suffix_var = tk.StringVar()
        sc = ttk.Combobox(head, textvariable=self.suffix_var, values=("", "M", "A", "Ad"), state="readonly", width=4)
        sc.pack(side="right", padx=(2, 10))
        sc.bind("<<ComboboxSelected>>", lambda e: self._suffix_changed())
        self.req = tk.Text(top, height=4, wrap="word", font=("Segoe UI", 9), background="#f7f7f7")
        self.req.pack(fill="x", pady=(2, 4))

        cols = (("name", "Участник", 170), ("pct", "% базы", 70), ("producer", "Производитель / модель", 220),
                ("dm", "DM", 110), ("row1", "Строка 1 (Excel)", 170), ("system", "Система", 220),
                ("clar", "Уточнения", 200))
        tbody = ttk.Frame(top)
        tbody.pack(fill="both", expand=True)
        self.offers = ttk.Treeview(tbody, columns=[c[0] for c in cols], show="headings", selectmode="browse",
                                   height=6)
        for col, text, width in cols:
            self.offers.heading(col, text=text)
            self.offers.column(col, width=width, anchor="w", stretch=True)
        for tag, color in COLOR_TAGS.values():
            self.offers.tag_configure(tag, background=color)
        self.offers.tag_configure("human", font=("Segoe UI", 9, "bold"))
        osy = ttk.Scrollbar(tbody, command=self.offers.yview)
        self.offers.configure(yscrollcommand=osy.set)
        osy.pack(side="right", fill="y")
        self.offers.pack(fill="both", expand=True)
        self.offers.bind("<<TreeviewSelect>>", lambda e: self._offer_selected())
        self.detail_var = tk.StringVar()      # полностью: причины системы и уточнения выбранного участника
        ttk.Label(top, textvariable=self.detail_var, foreground="#333", wraplength=1100,
                  justify="left").pack(fill="x", pady=(2, 0))

        dec = ttk.Labelframe(top, text="Решение (кодбук)", padding=4)
        dec.pack(fill="x", pady=(4, 0))
        dec.columnconfigure(1, weight=1)
        dec.columnconfigure(3, weight=1)
        ttk.Label(dec, text="Статус:").grid(row=0, column=0, sticky="w")
        self.choice_var = tk.StringVar()
        cc = ttk.Combobox(dec, textvariable=self.choice_var, values=[c.label for c in rv.CHOICES],
                          state="readonly", width=32)
        cc.grid(row=0, column=1, sticky="w")
        cc.bind("<<ComboboxSelected>>", lambda e: self._choice_changed())
        ttk.Label(dec, text="Строка 1:").grid(row=0, column=2, sticky="e", padx=(8, 2))
        self.row1_var = tk.StringVar()
        ttk.Entry(dec, textvariable=self.row1_var).grid(row=0, column=3, sticky="we")
        ttk.Label(dec, text="Комментарий (строка 11, после //):").grid(row=1, column=0, columnspan=2, sticky="w")
        self.comment_var = tk.StringVar()
        ce = ttk.Entry(dec, textvariable=self.comment_var)
        ce.grid(row=2, column=0, columnspan=4, sticky="we")
        ce.bind("<Return>", lambda e: self.apply())
        btns = ttk.Frame(dec)
        btns.grid(row=3, column=0, columnspan=4, sticky="we", pady=(4, 0))
        ttk.Button(btns, text="Записать решение (Enter)", command=self.apply).pack(side="left")
        ttk.Button(btns, text="Принять предложения системы по лоту", command=self.accept_system).pack(side="left",
                                                                                                    padx=6)
        self.state_var = tk.StringVar()
        ttk.Label(btns, textvariable=self.state_var, foreground="#555").pack(side="left", padx=6)

        self.viewer = DocViewer(right)
        right.add(self.viewer.frame, weight=2)

    # --- данные ------------------------------------------------------------------------------------------
    def _paths(self):
        result = Path(self.app.result_var.get() or "")
        tender = Path(self.app.tender_var.get() or "")
        return result, tender

    def reload_if_changed(self):
        result, _ = self._paths()
        if self.book is None or self.book.result != result:
            self.reload()

    def reload(self):
        result, tender = self._paths()
        if self.book is not None and self.book.dirty:
            self.save(quiet=True)
        self.book, self.lot, self.offer = None, None, None
        self.lots.delete(*self.lots.get_children())
        self.offers.delete(*self.offers.get_children())
        if not result.is_file():
            self.file_var.set("Выберите result-файл (поле «Result» сверху).")
            return
        try:
            self.book = rv.ReviewBook(result)
        except Exception as e:
            self.file_var.set(f"Не открылся {result.name}: {e}")
            return
        from src.evaluate.engine import tender_id_of
        self.tender_dir = tender if (tender / "manifest.sqlite").is_file() else None
        self.tender_id = tender_id_of(self.tender_dir) if self.tender_dir else ""
        if self.tender_id:
            rv.attach_system(self.book.lots, self.app.kb, self.tender_id)
        src = "продолжаю " if self.book.source == self.book.target else "новая копия → "
        self.file_var.set(f"{src}{self.book.target.name}" + ("" if self.tender_id else
                                                            "  (папка тендера не выбрана: нет решений системы)"))
        self.fill_lots()

    def _lot_tag(self, lot: rv.Lot) -> str:
        color = lot.tab_color or rv.lot_tab(self.book.statuses(lot)) if self.book else None
        return COLOR_TAGS.get(color, ("", ""))[0]

    def _visible(self, lot: rv.Lot) -> bool:
        mode, query = self.filter_var.get(), self.search_var.get().strip().lower()
        if mode == FILTERS[1] and self._lot_tag(lot) != "yellow":
            return False
        if mode == FILTERS[2] and lot.reviewed:
            return False
        if query and query not in f"{lot.lot_no} {lot.title} " \
                                   f"{' '.join(o.name for o in lot.offers)}".lower():
            return False
        return True

    def fill_lots(self):
        if not self.book:
            return
        keep = self.lot.lot_no if self.lot else None
        self.lots.delete(*self.lots.get_children())
        for lot in self.book.lots:
            if self._visible(lot):
                self.lots.insert("", "end", iid=str(lot.lot_no), tags=(self._lot_tag(lot),),
                                 values=("✓" if lot.reviewed else "", lot.sheet, lot.title))
        done = sum(lot.reviewed for lot in self.book.lots if lot.offers)
        total = sum(1 for lot in self.book.lots if lot.offers)
        self.progress_var.set(f"проверено мной: {done} из {total} лотов с офертами")
        if keep is not None and self.lots.exists(str(keep)):
            self.lots.selection_set(str(keep))
            self.lots.see(str(keep))

    def _lot_selected(self):
        sel = self.lots.selection()
        if not sel or not self.book:
            return
        lot = self.book.lot(int(sel[0]))
        if lot is self.lot:
            return
        self.show_lot(lot)

    def show_lot(self, lot: rv.Lot):
        self.lot, self.offer = lot, None
        base = f"{lot.base:,.2f}".replace(",", " ") if lot.base else "?"
        qty = f"{lot.quantity:g}" if lot.quantity else "?"
        self.lot_var.set(f"{lot.sheet}: {lot.title[:90]}   (кол-во {qty}, база {base})")
        self.suffix_var.set(lot.suffix)
        self.req.delete("1.0", "end")
        self.req.insert("1.0", lot.requirement)
        self.fill_offers()
        children = self.offers.get_children()
        if children:
            pending = [c for c in children
                       if not (lot.offers[int(c)].system or {}).get("source") == cf.HUMAN]
            first = (pending or children)[0]
            self.offers.selection_set(first)
            self.offers.focus(first)
            self._offer_selected()
        else:
            self.viewer.set_docs([])
            self.state_var.set("оферт нет - вкладка тёмно-синяя")

    def fill_offers(self):
        self.offers.delete(*self.offers.get_children())
        for i, o in enumerate(self.lot.offers):
            pct = f"{o.ratio * 100:.2f}%" if o.ratio else ""
            clar = "; ".join(f"{c['label']}: {c['note'][:60]}" for c in o.clar)
            tags = [COLOR_TAGS.get(o.row1_color, ("", ""))[0]]
            if o.system and o.system.get("source") == cf.HUMAN:
                tags.append("human")
            self.offers.insert("", "end", iid=str(i), tags=tags,
                               values=(("✓ " if "human" in tags else "") + o.name, pct, o.producer[:80],
                                       o.dm[:40], o.row1, system_text(o), clar))

    def _offer_selected(self):
        sel = self.offers.selection()
        if not sel or not self.lot:
            return
        o = self.lot.offers[int(sel[0])]
        if o is self.offer and self._shown == id(o.system):
            return
        self.offer, self._shown = o, id(o.system)
        s = o.system or {}
        choice = rv.choice_for(s["status"], s.get("reason")) if s else None
        if choice is None and o.ratio and o.ratio >= 1.3:
            choice = rv.BY_KEY["price_130"]
        choice = choice or rv.BY_KEY["clarificare"]
        self.choice_var.set(choice.label)
        human = s.get("source") == cf.HUMAN
        self.row1_var.set(o.row1 if human and o.row1 else (choice.text or ""))
        self.comment_var.set(rv.system_comment(o))
        self.state_var.set("уже проверено мной" if human else "предложение системы - проверьте и запишите")
        reasons = s.get("reasons") or []
        lines = [f"Система: {system_text(o).split(':')[0]}" + (": " + "; ".join(map(str, reasons)) if reasons else "")]
        lines += [f"{c['label']}: {c['note']}" + (f"  [{c['docs']}]" if c["docs"] else "") for c in o.clar]
        self.detail_var.set("\n".join(line[:400] for line in lines))
        docs = cd.participant_documents(self.tender_dir, o.name, self.lot.lot_no) if self.tender_dir else []
        self.viewer.set_docs(docs, probes(o))

    def _choice(self) -> Optional[rv.Choice]:
        return next((c for c in rv.CHOICES if c.label == self.choice_var.get()), None)

    def _choice_changed(self):
        c = self._choice()
        if c:
            self.row1_var.set(c.text or "")

    def _suffix_changed(self):
        if self.book and self.lot:
            self.book.set_tab(self.lot, suffix=self.suffix_var.get())
            self.fill_lots()
            self.state_var.set(f"лист: {self.lot.sheet} (не сохранено)")

    # --- решения -----------------------------------------------------------------------------------------
    def _decide(self, offer: rv.Offer, choice: rv.Choice, comment: str, row1: Optional[str]):
        self.book.decide(self.lot, offer, choice, comment, row1)
        if self.tender_id:
            rv.record(self.app.kb, self.tender_id, self.lot, offer, choice, comment)
        else:   # без папки тендера - только Excel; «проверено» видно по строке 1
            offer.system = {**(offer.system or {}), "status": choice.status, "source": cf.HUMAN,
                            "reason": choice.reason}

    def apply(self):
        if not (self.book and self.lot and self.offer):
            return
        choice = self._choice()
        if not choice:
            return
        self._decide(self.offer, choice, self.comment_var.get().strip(), self.row1_var.get().strip() or None)
        logger.info(f"[REVIEW] {self.lot.sheet}, {self.offer.name}: {choice.label}"
                    + (f" // {self.comment_var.get().strip()}" if self.comment_var.get().strip() else ""))
        self._after_decision()

    def accept_system(self):
        """Все оферты лота, ещё не проверенные мной: решение системы как есть (вопрос - «Clarificare»)."""
        if not (self.book and self.lot):
            return
        n = 0
        for o in self.lot.offers:
            s = o.system or {}
            if s.get("source") == cf.HUMAN:
                continue
            choice = rv.choice_for(effective(s["status"], s.get("source")), s.get("reason")) if s else None
            choice = choice or rv.BY_KEY["clarificare"]
            self._decide(o, choice, rv.system_comment(o), None)
            n += 1
        logger.info(f"[REVIEW] {self.lot.sheet}: приняты предложения системы ({n})")
        self._after_decision()

    def _after_decision(self):
        self.book.set_tab(self.lot)
        self._export()
        current = self.offers.selection()
        self.fill_offers()
        self.fill_lots()
        pending = [str(i) for i, o in enumerate(self.lot.offers)
                   if (o.system or {}).get("source") != cf.HUMAN]
        nxt = pending[0] if pending else (current[0] if current else None)
        if nxt is not None:
            self.offers.selection_set(nxt)
            self._offer_selected()
        self.state_var.set("лот проверен - «Следующий лот →»" if self.lot.reviewed else "записано (не сохранено)")

    def _export(self):
        try:
            from src.history import sync as answers_sync
            answers_sync.export(self.app.kb, answers_sync.own_file(self.app.answers_dir))
        except Exception as e:
            logger.warning(f"[SYNC] Решение сохранено в базе, но не выгружено в файл: {e}")

    def next_lot(self):
        if not (self.book and self.lot):
            return
        if self.book.dirty:
            self.save(quiet=True)
        items = self.lots.get_children()
        cur = str(self.lot.lot_no)
        if cur in items and items.index(cur) + 1 < len(items):
            nxt = items[items.index(cur) + 1]
            self.lots.selection_set(nxt)
            self.lots.see(nxt)

    def save(self, quiet: bool = False) -> Optional[Path]:
        if not self.book or not self.book.dirty:
            return None
        try:
            path = self.book.save()
        except PermissionError:
            messagebox.showwarning("Excel", f"{self.book.target.name} открыт в Excel - закройте его и нажмите "
                                            f"«Сохранить в Excel». Решения пока в памяти и в базе.")
            return None
        except FileExistsError:
            messagebox.showwarning("Excel", f"{self.book.target.name} появился после открытия вкладки - "
                                            f"переоткройте вкладку, чтобы не затереть его.")
            return None
        self.state_var.set(f"сохранено: {path.name}")
        if not quiet:
            logger.info(f"[REVIEW] Сохранено: {path}")
        return path
