"""
Вкладка «Проверка лотов»: человек проходит лоты по номеру, видит, что выдала система (строка 1 Excel, решение
базы знаний, уточнения), открывает документы участника и ставит решение по кодбуку. Решения пишутся в копию
<result>_verificat.xlsx (src/excel/review.py) и в базу знаний (source = human).
"""
import logging
import re
import threading
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
from src.ui.offer_strip import OfferStrip

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
        self._saver: Optional[threading.Thread] = None   # сохранение книги в фоне (~5 с на 283 листа)
        self._save_error: Optional[Exception] = None
        self._save_again = False
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

        right = self.layout.pane("review_right2", ttk.Panedwindow(panes, orient="vertical"), [0.75])
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
        self.req = tk.Text(top, height=3, wrap="word", font=("Segoe UI", 9), background="#f7f7f7")
        self.req.pack(fill="x", pady=(2, 4))

        # участники колонками, как в Excel (клик - выбор участника)
        self.strip = OfferStrip(top, lambda i: self._offer_selected(i))
        tbody = self.strip.frame
        tbody.pack(fill="both", expand=True)     # перепаковывается ниже: решение и сообщение - всегда видны
        self.detail_var = tk.StringVar()      # полностью: причины системы и уточнения выбранного участника
        self.detail_box = tk.Label(top, textvariable=self.detail_var, font=("Segoe UI", 11), fg="#222",
                                   bg="#fff8dc", wraplength=1100, justify="left", anchor="w", padx=6, pady=4,
                                   relief="solid", borderwidth=1)
        self.detail_box.pack(fill="x", pady=(4, 0))
        # ширина переноса - по ширине панели (сообщение не уходит за край)
        self.detail_box.bind("<Configure>", lambda e: self.detail_box.configure(wraplength=max(300, e.width - 16)))

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
        ttk.Button(btns, text="Принять предложения системы по лоту →", command=self.accept_system).pack(
            side="left", padx=6)
        self.state_var = tk.StringVar()
        ttk.Label(btns, textvariable=self.state_var, foreground="#555").pack(side="left", padx=6)
        self.save_var = tk.StringVar()        # состояние записи в Excel
        ttk.Label(btns, textvariable=self.save_var, foreground="#1565c0").pack(side="right", padx=6)

        # решение и сообщение системы - прижаты к низу и всегда видны; колонки участников - что осталось
        for w in (tbody, self.detail_box, dec):
            w.pack_forget()
        dec.pack(side="bottom", fill="x", pady=(4, 0))
        self.detail_box.pack(side="bottom", fill="x", pady=(4, 0))
        tbody.pack(fill="both", expand=True)
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
        if self.book is not None:                 # дождаться фоновой записи, дописать остаток
            self.save(quiet=True)
        self.book, self.lot, self.offer = None, None, None
        self.lots.delete(*self.lots.get_children())
        self.strip.set_lot(None)
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
        if lot.offers:
            pending = [i for i, o in enumerate(lot.offers) if (o.system or {}).get("source") != cf.HUMAN]
            self._offer_selected((pending or [0])[0])
        else:
            self.viewer.set_docs([])
            self.state_var.set("оферт нет - вкладка тёмно-синяя")

    def fill_offers(self):
        keep = self.strip.selected
        self.strip.set_lot(self.lot, reviewed=lambda o: (o.system or {}).get("source") == cf.HUMAN,
                           system=system_text)
        if keep is not None:
            self.strip.select(keep)

    def _offer_selected(self, index: int):
        if not self.lot or not (0 <= index < len(self.lot.offers)):
            return
        self.strip.select(index)
        o = self.lot.offers[index]
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
        self.detail_var.set("\n".join(line[:240] + ("…" if len(line) > 240 else "") for line in lines))
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
        self._wait_save()                     # книгу не меняем, пока она пишется на диск
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
        """Решение записано: есть непроверенные участники лота - к следующему из них; лот проверен целиком -
        сохранить в Excel (в фоне) и перейти к следующему лоту."""
        self.book.set_tab(self.lot)
        self._export()
        pending = [str(i) for i, o in enumerate(self.lot.offers)
                   if (o.system or {}).get("source") != cf.HUMAN]
        if not pending:
            self.next_lot()
            return
        self.fill_offers()
        self.fill_lots()
        self._offer_selected(int(pending[0]))
        self.state_var.set(f"записано; осталось участников в лоте: {len(pending)}")

    def _export(self):
        try:
            from src.history import sync as answers_sync
            answers_sync.export(self.app.kb, answers_sync.own_file(self.app.answers_dir))
        except Exception as e:
            logger.warning(f"[SYNC] Решение сохранено в базе, но не выгружено в файл: {e}")

    def next_lot(self):
        """Сохранить (в фоне) и открыть следующий по номеру лот из видимых в списке (с учётом фильтра)."""
        if not (self.book and self.lot):
            return
        cur = self.lot.lot_no
        if self.book.dirty:
            self.save_async()
        self.fill_lots()
        following = [i for i in self.lots.get_children() if int(i) > cur]
        if not following:
            self.fill_offers()
            self.state_var.set("это последний лот в списке")
            return
        nxt = following[0]
        self.lots.selection_set(nxt)
        self.lots.see(nxt)
        self.show_lot(self.book.lot(int(nxt)))

    # --- сохранение --------------------------------------------------------------------------------------
    def save_async(self):
        """Записать _verificat в фоне: окно не ждёт; решения следующего лота подождут конца записи."""
        if not self.book or not self.book.dirty:
            return
        if self._saver is not None and self._saver.is_alive():
            self._save_again = True
            return
        self._save_error, self._save_again = None, False
        self.book.dirty = False

        def work(book=self.book):
            try:
                book.save()
            except Exception as e:      # открыт в Excel / файл появился - сообщить из главного потока
                self._save_error = e
                book.dirty = True
        self._saver = threading.Thread(target=work, daemon=True)
        self._saver.start()
        self.save_var.set("сохраняю в Excel…")
        self.lots.after(200, self._poll_save)

    def _poll_save(self):
        if self._saver is not None and self._saver.is_alive():
            self.lots.after(200, self._poll_save)
            return
        self._saver = None
        if self._save_error is not None:
            self.save_var.set("НЕ сохранено в Excel")
            self._report(self._save_error)
            return
        self.save_var.set(f"сохранено: {self.book.target.name}")
        if self._save_again:
            self.save_async()

    def _wait_save(self):
        if self._saver is not None and self._saver.is_alive():
            self._saver.join()

    def _report(self, e: Exception):
        if isinstance(e, PermissionError):
            messagebox.showwarning("Excel", f"{self.book.target.name} открыт в Excel - закройте его и нажмите "
                                            f"«Сохранить в Excel». Решения пока в памяти и в базе.")
        elif isinstance(e, FileExistsError):
            messagebox.showwarning("Excel", f"{self.book.target.name} появился после открытия вкладки - "
                                            f"переоткройте вкладку, чтобы не затереть его.")
        else:
            messagebox.showwarning("Excel", f"Не сохранено: {e}")

    def save(self, quiet: bool = False) -> Optional[Path]:
        """Сохранить сейчас (кнопка, закрытие окна): дождаться фоновой записи и записать оставшееся.
        None - не сохранено (Excel открыт и т.п.)."""
        self._wait_save()
        if not self.book:
            return None
        if not self.book.dirty:
            return self.book.target
        try:
            path = self.book.save()
        except (PermissionError, FileExistsError) as e:
            self._report(e)
            return None
        self._save_error = None
        self.book.dirty = False
        self.save_var.set(f"сохранено: {path.name}")
        if not quiet:
            logger.info(f"[REVIEW] Сохранено: {path}")
        return path
