"""
Форма карточки товара в окне: заполнить быстро, а не печатать.

- название лота: наведение - подсказка с требованием лота, клик - отдельное окно с требованием;
- поля карточки в две колонки; экономические агенты и победители прошлых лет - выбор из списка
  (этот тендер / этот год / прошлый год / раньше) плюс ручной ввод;
- параметры - таблица «параметр - значение»: значение выбирается из вариантов (оферта, требование лота),
  пункты требования, которых нет в карточке, уже стоят строками;
- справа снизу - технические документы участника: текст PDF с поиском; выделили текст - кнопка
  «+ в параметры» добавляет строку в таблицу.
"""
import json
import logging
import os
import re
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Callable, Dict, List, Optional

from src.evaluate.cards import split_param
from src.history.cards import card_title
from src.ui import card_data as cd

logger = logging.getLogger("ui")

FIELDS_LEFT = ("producer", "model", "codes", "dm")
FIELDS_RIGHT = ("name", "compatible_with", "iso")
LABELS = {"producer": "Производитель", "model": "Модель", "codes": "Коды модели", "dm": "Номера DM",
          "name": "Изделие", "compatible_with": "Совместимо с", "iso": "ISO / CE"}


class Tooltip:
    """Подсказка у виджета при наведении."""

    def __init__(self, widget, text_fn: Callable[[], str]):
        self.widget, self.text_fn, self.tip = widget, text_fn, None
        widget.bind("<Enter>", self.show, add="+")
        widget.bind("<Leave>", self.hide, add="+")

    def show(self, _=None):
        text = self.text_fn()
        if self.tip or not text:
            return
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        x, y = self.widget.winfo_rootx(), self.widget.winfo_rooty() + self.widget.winfo_height() + 2
        self.tip.wm_geometry(f"+{x}+{y}")
        tk.Label(self.tip, text=text, justify="left", background="#ffffe0", relief="solid", borderwidth=1,
                 wraplength=700, font=("Segoe UI", 9), padx=6, pady=4).pack()

    def hide(self, _=None):
        if self.tip:
            self.tip.destroy()
            self.tip = None


class CardForm:
    def __init__(self, parent: ttk.Frame, kb, q: Dict, tender_dir: Optional[Path], on_save: Callable[[Dict], None],
                 save_text: str = "Подтвердить карточку", result: Optional[Path] = None, layout=None):
        self.layout = layout          # src.ui.layout.Layout - запоминание разделителей (окно); в тестах - None
        self.result = result          # result-файл текущего тендера - цена оферты участника
        self.parent, self.kb, self.q, self.on_save, self.save_text = parent, kb, q, on_save, save_text
        payload = q.get("payload") or {}
        self.card = dict(payload.get("card") or {})
        self.card["model_key"] = q.get("model_key") or self.card.get("model_key")   # ключ мог смениться (rekey)
        # пустые поля - из последней оферты этой модели в истории тендеров (по номеру DM)
        self.from_history = []
        for k, v in cd.history_card(kb, self.card).items():
            if v and not self.card.get(k):
                self.card[k] = v
                self.from_history.append(k)
        self.lot_no, self.participant = payload.get("lot_no"), payload.get("participant") or ""
        self.lot_title = payload.get("lot_title", "")
        row = kb.execute("SELECT requirement FROM lot_profiles WHERE tender_id = ? AND lot_no = ?",
                         (q.get("tender_id"), self.lot_no)).fetchone()
        self.requirement = (row[0] if row else "") or ""
        self.tender_dir = tender_dir
        self.entries: Dict[str, ttk.Entry] = {}
        self.param_widgets: List[Dict] = []
        self.docs: Optional[List[Dict]] = None    # документы участника (оферта + уточнения), один раз
        self.hits: List[str] = []
        self.hit_pos = -1
        self._build()

    # --- разметка ----------------------------------------------------------------------------------------------
    def _build(self):
        f = self.parent
        head = ttk.Frame(f)
        head.pack(fill="x")
        self.title_var = tk.StringVar(value=card_title(self.card))
        ttk.Label(head, textvariable=self.title_var, font=("Segoe UI", 11, "bold")).pack(side="left")
        ttk.Label(head, text=f"  ключ: {self.card['model_key']}", foreground="gray").pack(side="left")
        self.save_button = ttk.Button(head, text=self.save_text, command=self.save)
        self.save_button.pack(side="right")

        lot = ttk.Frame(f)
        if self.lot_no or self.lot_title:
            lot.pack(fill="x")
        ttk.Label(lot, text=f"Из лота {self.lot_no}, {self.participant}: " if self.lot_no else
                  f"{self.q.get('tender_id') or ''} {self.participant}: ").pack(side="left")
        self.lot_label = tk.Label(lot, text=self.lot_title or "(название лота)", fg="#0645ad", cursor="hand2",
                                  font=("Segoe UI", 9, "underline"))
        self.lot_label.pack(side="left")
        self.lot_tip = Tooltip(self.lot_label, lambda: "Требование лота:\n" + (self.requirement or "нет в базе"))
        self.lot_label.bind("<Button-1>", lambda e: self.show_requirement())

        top = ttk.Frame(f)
        top.pack(fill="x", pady=(4, 0))
        for col, names in ((0, FIELDS_LEFT), (2, FIELDS_RIGHT)):
            for i, name in enumerate(names):
                ttk.Label(top, text=LABELS[name]).grid(row=i, column=col, sticky="w", padx=(0 if col == 0 else 10, 4))
                e = ttk.Entry(top, width=40)
                e.insert(0, str(self.card.get(name) or ""))
                e.grid(row=i, column=col + 1, sticky="we", pady=1)
                self.entries[name] = e
        top.columnconfigure(1, weight=1)
        top.columnconfigure(3, weight=2)

        # агенты и цены | параметры | документы - границы перетаскиваются (положение запоминается)
        split = ttk.Panedwindow(f, orient="vertical")
        split.pack(fill="both", expand=True, pady=(4, 0))
        if self.layout is not None:
            self.layout.pane("card", split, [0.36, 0.62])
        upper, params, viewer = ttk.Frame(split), ttk.Frame(split), ttk.Frame(split)
        split.add(upper, weight=2)
        split.add(params, weight=2)
        split.add(viewer, weight=3)
        self._build_agents(upper)
        self._build_prices(upper)
        self._build_params(params)
        self._build_viewer(viewer)

    def _build_agents(self, f):
        box = ttk.Frame(f)
        box.pack(fill="x", pady=(4, 0))
        self.agents = cd.agent_candidates(self.kb, self.card, self.q.get("tender_id") or "", self.participant)
        saved_agents = [a.strip() for a in (self.card.get("agents") or "").split(";") if a.strip()]
        winners = cd.winner_candidates(self.agents)
        saved_winners = [w.strip() for w in (self.card.get("winners") or "").split(";") if w.strip()]

        self.agent_list = self._choice_list(box, 0, "Экономические агенты (выбор, Ctrl - несколько)",
                                            [cd.agent_label(a) for a in self.agents])
        names = [a["name"] for a in self.agents]
        for i, a in enumerate(self.agents):
            if a["name"] in saved_agents or (not saved_agents and a["name"] == self.participant):
                self.agent_list.selection_set(i)
        for extra in saved_agents:
            if extra not in names:
                self.agent_list.insert("end", extra)
                self.agent_list.selection_set("end")
        self.winner_list = self._choice_list(box, 1, "Победители прошлых лет (по этой модели)", winners)
        for extra in saved_winners:
            if extra not in winners:
                self.winner_list.insert("end", extra)
        for i in range(self.winner_list.size()):
            if self.winner_list.get(i) in saved_winners:
                self.winner_list.selection_set(i)
        box.columnconfigure(0, weight=3)
        box.columnconfigure(1, weight=2)

    def _build_prices(self, f):
        """Цены этой модели по годам: текущий тендер и прошлые (принятые - зелёным, отклонённые - красным)."""
        current = cd.current_offer_price(self.result, self.lot_no, self.participant)
        self.prices = cd.price_history(self.kb, self.card, self.q.get("tender_id") or "", self.participant, current,
                                       re.sub(r"^\s*lot\s*nr\.?\s*\d+\s*", "", self.lot_title or "", flags=re.IGNORECASE))
        summary = cd.price_summary(self.prices)
        frame = ttk.Labelframe(f, text="Цены по годам (за единицу)" + (": " + " | ".join(summary[:3]) if summary else ""))
        frame.pack(fill="x", pady=(4, 0))
        cols = (("year", "Год", 140), ("where", "Тендер, лот", 115), ("title", "Название лота", 260),
                ("participant", "Участник", 170), ("unit", "Цена за ед.", 85), ("base", "База за ед.", 85),
                ("qty", "Кол-во", 65), ("ratio", "% базы", 55), ("decision", "Решение", 90))
        tree = self.price_tree = ttk.Treeview(frame, columns=[c for c, _, _ in cols], show="headings",
                                              height=min(4, max(1, len(self.prices))))
        for c, title, width in cols:
            tree.heading(c, text=title)
            tree.column(c, width=width, stretch=c == "title",
                        anchor="w" if c in ("year", "where", "title", "participant", "decision") else "e")
        tree.tag_configure("won", foreground="#1b5e20")
        tree.tag_configure("rejected", foreground="#b71c1c")
        tree.tag_configure("now", background="#e3f2fd")
        for r in self.prices:
            where = "этот" if r["period"] == "этот тендер" else f"{r['tender_id']}" + \
                (f", лот {r['lot_no']}" if r.get("lot_no") else "")
            tag = "now" if r["period"] == "этот тендер" else ("won" if r["won"] else
                                                               ("rejected" if r["decision"] == "rejected" else ""))
            tree.insert("", "end", values=(
                r["period"] if r["period"] == "этот тендер" or r["period"].isdigit() else r["period"],
                where, r.get("lot_title") or "", r["participant"], cd.fmt_price(r.get("unit_price")),
                cd.fmt_price(r.get("base_price")),
                cd.fmt_price(r.get("quantity")), cd.fmt_ratio(r.get("ratio")),
                cd.DECISION_LABELS.get(r["decision"], r["decision"] or "")), tags=(tag,))
        if not self.prices:
            tree.insert("", "end", values=("", "", "в истории нет оферт этой модели", "", "", "", "", "", ""))
        sy = ttk.Scrollbar(frame, command=tree.yview)
        tree.configure(yscrollcommand=sy.set)
        sy.pack(side="right", fill="y")
        tree.pack(fill="x")

    def _choice_list(self, parent, col: int, title: str, items: List[str]) -> tk.Listbox:
        frame = ttk.Labelframe(parent, text=title)
        frame.grid(row=0, column=col, sticky="nsew", padx=(0, 6) if col == 0 else 0)
        lb = tk.Listbox(frame, selectmode="extended", height=3, exportselection=False)
        for item in items:
            lb.insert("end", item)
        if not items:
            lb.insert("end", "(в истории нет - впишите ниже)")
            lb.itemconfig(0, foreground="#888")
        lb.pack(fill="x")
        add = ttk.Frame(frame)
        add.pack(fill="x")
        entry = ttk.Entry(add)
        entry.pack(side="left", fill="x", expand=True)

        def push(_=None):
            text = entry.get().strip()
            if text:
                if lb.size() == 1 and lb.get(0).startswith("(в истории нет"):
                    lb.delete(0)
                lb.insert("end", text)
                lb.selection_set("end")
                entry.delete(0, "end")
        entry.bind("<Return>", push)
        ttk.Button(add, text="+", width=3, command=push).pack(side="left")
        lb.add_entry = entry      # для тестов
        lb.add_push = push
        return lb

    @staticmethod
    def selected(lb: tk.Listbox) -> List[str]:
        return [lb.get(i) for i in lb.curselection() if not lb.get(i).startswith("(в истории нет")]

    # --- параметры -----------------------------------------------------------------------------------------------
    def _build_params(self, f):
        frame = ttk.Labelframe(f, text="Параметры изделия (значение - выбор из оферты и требования лота или свой)")
        frame.pack(fill="both", expand=True, pady=(4, 0))
        bar = ttk.Frame(frame)
        bar.pack(fill="x")
        ttk.Button(bar, text="+ параметр", command=lambda: self.add_param("", "")).pack(side="left")
        ttk.Button(bar, text="Убрать пустые", command=self.drop_empty).pack(side="left", padx=4)
        ttk.Label(bar, text="пустые строки - пункты требования лота, которых нет в оферте",
                  foreground="#777").pack(side="left", padx=6)
        self.doc_filled_var = tk.StringVar()
        ttk.Label(bar, textvariable=self.doc_filled_var, foreground="#1565c0").pack(side="left", padx=6)
        canvas = tk.Canvas(frame, height=140, highlightthickness=0)
        sb = ttk.Scrollbar(frame, orient="vertical", command=canvas.yview)
        self.params_frame = ttk.Frame(canvas)
        self.params_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        win = canvas.create_window((0, 0), window=self.params_frame, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        canvas.pack(fill="both", expand=True)
        self.params_canvas = canvas
        self.params_frame.columnconfigure(0, weight=2)
        self.params_frame.columnconfigure(1, weight=3)
        params = self.card.get("params") or {}
        if isinstance(params, str):
            try:
                params = json.loads(params or "{}")
            except json.JSONDecodeError:
                params = {}
        rows = cd.param_rows(params, self.requirement)
        # значения из технических документов участника (TDS из уточнений, даташиты) - по величине и словам параметра
        if self.docs is None:
            self.docs = cd.participant_documents(self.tender_dir, self.participant, self.lot_no) if self.tender_dir else []
        hint = " ".join(self.entries[k].get() for k in ("model", "name")) + " " + (self.lot_title or "")
        found = cd.doc_values(rows, cd.value_documents(self.docs, cd._codes(self.values())), hint,
                              replace_requirement=self.card.get("source") != "human")   # подтверждённую не меняем
        self.doc_filled_var.set(f"из документов заполнено: {found}" if found else "")
        for row in rows:
            self.add_param(row["name"], row["value"], row["choices"], row.get("required"), scroll=False,
                           doc=row.get("doc"))

    def add_param(self, name: str, value: str, choices: Optional[List[str]] = None, required: Optional[str] = None,
                  scroll: bool = True, doc: Optional[Dict] = None):
        r = len(self.param_widgets)
        n = ttk.Entry(self.params_frame)
        n.insert(0, name)
        v = ttk.Combobox(self.params_frame, values=[c for c in (choices or []) if c])
        v.set(value)
        x = ttk.Button(self.params_frame, text="✕", width=3)
        n.grid(row=r, column=0, sticky="we", padx=(0, 4), pady=1)
        v.grid(row=r, column=1, sticky="we", pady=1)
        x.grid(row=r, column=2, padx=2)
        item = {"name": n, "value": v, "del": x, "required": required}
        x.configure(command=lambda: self.remove_param(item))
        if required:
            Tooltip(n, lambda: "Требование лота: " + required)
        if doc:                                   # откуда значение из документа
            Tooltip(v, lambda: f"Из документа «{doc['name']}»: {doc['value']}\n«{doc['quote']}»")
        self.param_widgets.append(item)
        if scroll:
            self.params_canvas.update_idletasks()
            self.params_canvas.yview_moveto(1.0)
        return item

    def remove_param(self, item: Dict):
        for w in ("name", "value", "del"):
            item[w].destroy()
        self.param_widgets.remove(item)

    def drop_empty(self):
        for item in [i for i in self.param_widgets if not i["value"].get().strip()]:
            self.remove_param(item)

    def params(self) -> Dict[str, str]:
        out = {}
        for item in self.param_widgets:
            name, value = item["name"].get().strip(), item["value"].get().strip()
            if name and value:
                out[name] = value
        return out

    # --- просмотр документов ----------------------------------------------------------------------------------
    def _build_viewer(self, f):
        frame = ttk.Labelframe(f, text=f"Документы участника: {self.participant or '?'}")
        frame.pack(fill="both", expand=True, pady=(6, 0))
        bar = ttk.Frame(frame)
        bar.pack(fill="x")
        if self.docs is None:
            self.docs = cd.participant_documents(self.tender_dir, self.participant, self.lot_no) if self.tender_dir else []
        self.doc_var = tk.StringVar()
        self.doc_combo = ttk.Combobox(bar, textvariable=self.doc_var, state="readonly", width=55,
                                      values=[d["label"] for d in self.docs])
        self.doc_combo.pack(side="left")
        self.doc_combo.bind("<<ComboboxSelected>>", lambda e: self.load_doc(self.doc_combo.current()))
        ttk.Button(bar, text="Открыть PDF", command=self.open_pdf).pack(side="left", padx=4)
        ttk.Label(bar, text="Поиск:").pack(side="left", padx=(10, 2))
        self.search_var = tk.StringVar()
        se = ttk.Entry(bar, textvariable=self.search_var, width=24)
        se.pack(side="left")
        se.bind("<Return>", lambda e: self.find_next())
        ttk.Button(bar, text="Найти", command=self.find_next).pack(side="left", padx=2)
        self.hits_var = tk.StringVar()
        ttk.Label(bar, textvariable=self.hits_var, foreground="#555").pack(side="left", padx=4)

        body = ttk.Frame(frame)
        body.pack(fill="both", expand=True)
        self.doc_text = tk.Text(body, wrap="word", font=("Consolas", 9), height=12)
        sy = ttk.Scrollbar(body, command=self.doc_text.yview)
        self.doc_text.configure(yscrollcommand=sy.set)
        sy.pack(side="right", fill="y")
        self.doc_text.pack(fill="both", expand=True)
        self.doc_text.tag_configure("hit", background="#fff59d")
        self.doc_text.tag_configure("cur", background="#ffb74d")
        # только чтение, но с выделением и Ctrl+C
        self.doc_text.bind("<Key>", lambda e: None if (e.state & 4 and (e.keysym.lower() in ("c", "a")
                                                                         or e.keycode in (67, 65))) else "break")
        self.doc_text.bind("<ButtonRelease-1>", self._on_select)
        self.add_button = tk.Button(self.doc_text, text="+ в параметры", bg="#e3f2fd", relief="raised",
                                    command=self.add_selection)
        if self.docs:
            self.doc_combo.current(0)
            self.load_doc(0)
        else:
            self._set_text("Документов участника нет: укажите «Папку тендера» (с manifest.sqlite) сверху окна.")

    def _set_text(self, text: str):
        self.doc_text.delete("1.0", "end")
        self.doc_text.insert("1.0", text)

    def load_doc(self, index: int):
        if not (0 <= index < len(self.docs)):
            return
        self.add_button.place_forget()
        self._set_text(cd.document_text(self.docs[index]["path"]))
        if not self.search_var.get().strip():     # сразу к модели: код модели или номер DM
            codes = [c.strip() for c in re.split(r"[,;]", self.entries["codes"].get()) if len(c.strip()) >= 3]
            dms = [d.strip() for d in self.entries["dm"].get().split(",") if d.strip()]
            for probe in codes + dms:
                if self.doc_text.search(probe, "1.0", nocase=True):
                    self.search_var.set(probe)
                    break
        self.hit_pos = -1
        self.find_next()

    def _mark_hits(self) -> List[str]:
        t = self.doc_text
        t.tag_remove("hit", "1.0", "end")
        t.tag_remove("cur", "1.0", "end")
        query = self.search_var.get().strip()
        hits, start = [], "1.0"
        while query:
            pos = t.search(query, start, stopindex="end", nocase=True)
            if not pos:
                break
            end = f"{pos}+{len(query)}c"
            t.tag_add("hit", pos, end)
            hits.append(pos)
            start = end
        return hits

    def find_next(self):
        query = self.search_var.get().strip()
        self.hits = self._mark_hits()
        if not query:
            self.hits_var.set("")
            return
        if not self.hits:
            self.hits_var.set("не найдено")
            return
        self.hit_pos = (self.hit_pos + 1) % len(self.hits)
        pos = self.hits[self.hit_pos]
        self.doc_text.tag_add("cur", pos, f"{pos}+{len(query)}c")
        self.doc_text.see(pos)
        self.hits_var.set(f"{self.hit_pos + 1}/{len(self.hits)}")

    def open_pdf(self):
        index = self.doc_combo.current()
        if 0 <= index < len(self.docs):
            path = self.docs[index]["path"]
            if Path(path).is_file():
                os.startfile(str(path))       # noqa: в Windows - программа просмотра PDF по умолчанию
            else:
                messagebox.showwarning("PDF", f"Файл не найден: {path}")

    def _on_select(self, event):
        try:
            text = self.doc_text.get("sel.first", "sel.last").strip()
        except tk.TclError:
            text = ""
        if text:
            self.add_button.place(x=event.x + 8, y=event.y + 8)
        else:
            self.add_button.place_forget()

    def add_selection(self, text: Optional[str] = None):
        if text is None:
            try:
                text = self.doc_text.get("sel.first", "sel.last")
            except tk.TclError:
                text = ""
        text = re.sub(r"\s+", " ", text or "").strip()
        self.add_button.place_forget()
        if not text:
            return None
        name, value = split_param(text)
        if value == "da" and name == text:    # без числа и «:» - текст значением, название параметра впишет человек
            name, value = "", text
        for item in self.param_widgets:       # тот же параметр уже есть, но пустой - заполнить его
            if name and cd._key(item["name"].get()) == cd._key(name) and not item["value"].get().strip():
                item["value"].set(value)
                item["value"].focus_set()
                return item
        item = self.add_param(name, value, [value])
        (item["name"] if not name else item["value"]).focus_set()
        return item

    # --- требование и сохранение ------------------------------------------------------------------------------
    def show_requirement(self):
        self.lot_tip.hide()
        win = tk.Toplevel(self.parent)
        win.title(f"Лот {self.lot_no}: требование")
        win.geometry("700x320")
        t = tk.Text(win, wrap="word", font=("Segoe UI", 10))
        t.insert("1.0", f"{self.lot_title}\n\n{self.requirement or 'Требование лота не найдено в базе.'}")
        t.pack(fill="both", expand=True)
        ttk.Button(win, text="Закрыть", command=win.destroy).pack(pady=4)
        return win

    def values(self) -> Dict:
        new = {"model_key": self.card["model_key"]}
        for name, e in self.entries.items():
            new[name] = e.get().strip()
        new["params"] = self.params()
        agents = []
        for label in self.selected(self.agent_list):
            name = label.split(" - ")[0].strip()
            if name not in agents:
                agents.append(name)
        new["agents"] = "; ".join(agents)
        new["winners"] = "; ".join(self.selected(self.winner_list))
        return new

    def save(self):
        self.on_save(self.values())
