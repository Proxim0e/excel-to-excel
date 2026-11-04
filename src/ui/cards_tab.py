"""
Вкладка «База карточек»: все карточки товара (подтверждённые человеком и предложенные системой), поиск,
фильтр, редактирование той же формой, что и в вопросах, новая карточка, удаление.

Сохранение - карточка становится подтверждённой (source = human); если по ней был открытый вопрос, он
закрывается. Удаление - метка source = deleted (её переносит git-синхронизация, иначе карточку вернул бы
файл ответов другого ПК); оценка удалённую карточку не учитывает.
"""
import json
import logging
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk
from typing import Callable, Dict, List, Optional

from src.evaluate.answers import answer_card
from src.evaluate.cards import save_card
from src.evaluate.history import resolve_key
from src.history.cards import card_key, card_title, split_dms
from src.ui.card_form import CardForm

logger = logging.getLogger("ui")

COLUMNS = (("title", "Карточка (тип — модель — производитель)", 380), ("dm", "DM (все номера)", 150),
           ("agents", "Агенты", 160), ("source", "Статус", 90), ("updated", "Изменена", 120),
           ("model_key", "Ключ", 170))
SOURCE_LABELS = {"human": "подтверждена", "prefill": "предложена"}
FILTERS = ("все", "подтверждённые", "предложенные")
PRODUCT_COLS = ("model_key", "producer", "model", "codes", "dm", "name", "params", "compatible_with", "iso",
                "agents", "winners", "source", "COALESCE(confirmed_at, updated_at)")


class CardsTab:
    def __init__(self, parent, kb, tender_dir_fn: Callable[[str], Optional[Path]], on_change: Callable[[], None],
                 result_fn: Optional[Callable[[str], Optional[Path]]] = None):
        self.parent, self.kb, self.tender_dir_fn, self.on_change = parent, kb, tender_dir_fn, on_change
        self.result_fn = result_fn or (lambda tender_id: None)
        self.rows: List[Dict] = []
        self.form: Optional[CardForm] = None
        self._build()
        self.reload()

    def _build(self):
        bar = ttk.Frame(self.parent, padding=(4, 4))
        bar.pack(fill="x")
        ttk.Label(bar, text="Поиск:").pack(side="left")
        self.search_var = tk.StringVar()
        e = ttk.Entry(bar, textvariable=self.search_var, width=30)
        e.pack(side="left", padx=4)
        e.bind("<KeyRelease>", lambda ev: self.fill())
        self.filter_var = tk.StringVar(value=FILTERS[0])
        f = ttk.Combobox(bar, textvariable=self.filter_var, values=FILTERS, state="readonly", width=15)
        f.pack(side="left")
        f.bind("<<ComboboxSelected>>", lambda ev: self.fill())
        ttk.Button(bar, text="Обновить", command=self.reload).pack(side="left", padx=4)
        ttk.Button(bar, text="Новая карточка", command=self.new_card).pack(side="left")
        ttk.Button(bar, text="Удалить", command=self.delete_selected).pack(side="left", padx=4)
        self.count_var = tk.StringVar()
        ttk.Label(bar, textvariable=self.count_var, foreground="#555").pack(side="left", padx=8)

        panes = ttk.Panedwindow(self.parent, orient="vertical")
        panes.pack(fill="both", expand=True)
        top = ttk.Frame(panes)
        self.tree = ttk.Treeview(top, columns=[c for c, _, _ in COLUMNS], show="headings", height=9)
        for col, title, width in COLUMNS:
            self.tree.heading(col, text=title, command=lambda c=col: self.sort(c))
            self.tree.column(col, width=width, stretch=col in ("name", "agents"))
        sy = ttk.Scrollbar(top, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sy.set)
        sy.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True)
        self.tree.tag_configure("human", foreground="#1b5e20")
        self.tree.tag_configure("prefill", foreground="#555")
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.edit_selected())
        panes.add(top, weight=1)
        self.form_frame = ttk.Frame(panes, padding=4)
        panes.add(self.form_frame, weight=3)
        self.sort_col, self.sort_desc = "updated", True

    # --- список ---------------------------------------------------------------------------------------------------
    def reload(self):
        rows = self.kb.execute(f"SELECT {', '.join(PRODUCT_COLS)} FROM products WHERE source != 'deleted'").fetchall()
        keys = [c if " " not in c else "updated" for c in PRODUCT_COLS]
        keys[-1] = "updated"
        self.rows = [dict(zip(keys, r)) for r in rows]
        for r in self.rows:
            r["title"] = card_title(r)
        self.fill()

    def visible(self) -> List[Dict]:
        text = self.search_var.get().strip().lower()
        flt = self.filter_var.get()
        out = []
        for r in self.rows:
            if flt == "подтверждённые" and r["source"] != "human" or flt == "предложенные" and r["source"] == "human":
                continue
            if text and text not in " ".join(str(r.get(k) or "") for k in
                                             ("title", "model_key", "producer", "model", "codes", "dm", "name", "agents")).lower():
                continue
            out.append(r)
        out.sort(key=lambda r: str(r.get(self.sort_col) or ""), reverse=self.sort_desc)
        return out

    def fill(self):
        selected = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        rows = self.visible()
        for r in rows:
            values = [SOURCE_LABELS.get(r["source"], r["source"]) if c == "source" else
                      (str(r.get(c) or "")[:16].replace("T", " ") if c == "updated" else str(r.get(c) or ""))
                      for c, _, _ in COLUMNS]
            self.tree.insert("", "end", iid=r["model_key"], values=values, tags=(r["source"],))
        human = sum(r["source"] == "human" for r in self.rows)
        self.count_var.set(f"показано {len(rows)} из {len(self.rows)}; подтверждено {human}")
        keep = [s for s in selected if self.tree.exists(s)]
        if keep:
            self.tree.selection_set(keep)

    def sort(self, col: str):
        self.sort_desc = not self.sort_desc if self.sort_col == col else False
        self.sort_col = col
        self.fill()

    # --- редактирование -------------------------------------------------------------------------------------------
    def _context(self, model_key: str) -> Dict:
        """Откуда карточка: вопрос по ней (тендер, лот, участник) - для требования лота и документов."""
        row = self.kb.execute("SELECT tender_id, payload FROM questions WHERE kind = 'product_card' AND model_key = ? "
                              "ORDER BY id DESC LIMIT 1", (model_key,)).fetchone()
        if not row:
            return {"tender_id": ""}
        payload = json.loads(row[1] or "{}")
        return {"tender_id": row[0], "lot_no": payload.get("lot_no"), "participant": payload.get("participant"),
                "lot_title": payload.get("lot_title", ""), "card": payload.get("card") or {}}

    def edit(self, card: Dict):
        for w in self.form_frame.winfo_children():
            w.destroy()
        ctx = self._context(card["model_key"])
        card = dict(card)
        for k, v in (ctx.get("card") or {}).items():      # пустые поля базы - из предзаполнения вопроса
            if v and not card.get(k):
                card[k] = v
        if isinstance(card.get("params"), str):
            try:
                card["params"] = json.loads(card["params"] or "{}")
            except json.JSONDecodeError:
                card["params"] = {}
        q = {"model_key": card["model_key"], "tender_id": ctx["tender_id"],
             "payload": {"card": card, "lot_no": ctx.get("lot_no"), "participant": ctx.get("participant") or "",
                         "lot_title": ctx.get("lot_title", "")}}
        tender_dir = self.tender_dir_fn(ctx["tender_id"]) if ctx["tender_id"] else None
        self.form = CardForm(self.form_frame, self.kb, q, tender_dir, self.save,
                             save_text="Сохранить карточку" if card.get("source") == "human" else "Подтвердить карточку",
                             result=self.result_fn(ctx["tender_id"]) if ctx["tender_id"] else None)
        return self.form

    def edit_selected(self):
        sel = self.tree.selection()
        if not sel:
            return None
        card = next((r for r in self.rows if r["model_key"] == sel[0]), None)
        return self.edit(card) if card else None

    def save(self, card: Dict):
        open_q = self.kb.execute("SELECT id FROM questions WHERE kind = 'product_card' AND state = 'open' "
                                 "AND model_key = ?", (card["model_key"],)).fetchall()
        if open_q:
            answer_card(self.kb, open_q[0][0], card)
            for (qid,) in open_q[1:]:
                self.kb.execute("UPDATE questions SET state = 'answered' WHERE id = ?", (qid,))
            self.kb.commit()
        else:
            save_card(self.kb, card, source="human")
        logger.info(f"[UI] Карточка сохранена в базе: {card['model_key']}")
        self.reload()
        if self.tree.exists(card["model_key"]):
            self.tree.selection_set(card["model_key"])
            self.tree.see(card["model_key"])
        self.on_change()

    def new_card(self, key: Optional[str] = None):
        """«Производитель | модель | название с размером» или номер DM. Карточка с этим ключом или с этим DM
        в списке номеров уже есть - выделяется она."""
        key = key or simpledialog.askstring(
            "Новая карточка", "Производитель | модель | название с размером\n"
            "(например: Agfa | Drystar DT2B | Film radiologic 35x43)\nили номер DM (DM000…):", parent=self.parent)
        if not key or not key.strip():
            return None
        key = key.strip()
        card = {"params": {}, "source": "new"}
        if key.upper().startswith("DM") and "|" not in key:
            dms = split_dms(key)
            model_key = resolve_key(self.kb, "DM:" + "+".join(dms), dms)
            card["dm"] = ", ".join(dms)
        elif ":" in key.split("|")[0]:                     # уже готовый ключ
            model_key = key
        else:
            parts = [x.strip() for x in key.split("|")] + ["", ""]
            producer, model, name = parts[:3]
            model_key = card_key(producer, model, "", name) or f"M:{key.lower()}"
            card.update({"producer": producer, "model": model, "name": name})
        card["model_key"] = model_key
        if self.kb.execute("SELECT 1 FROM products WHERE model_key = ? AND source != 'deleted'",
                           (model_key,)).fetchone():
            if self.tree.exists(model_key):
                self.tree.selection_set(model_key)
                self.tree.see(model_key)
            return self.form
        return self.edit(card)

    def delete_selected(self, confirm: bool = True):
        sel = self.tree.selection()
        if not sel:
            return
        titles = [next((r["title"] for r in self.rows if r["model_key"] == k), k) for k in sel]
        if confirm and not messagebox.askyesno("Удалить", "Удалить карточки:\n" + "\n".join(titles) + "?",
                                               parent=self.parent):
            return
        from datetime import datetime
        now = datetime.now().isoformat(timespec="seconds")
        for key in sel:
            self.kb.execute("UPDATE products SET source = 'deleted', confirmed_at = ?, updated_at = ? "
                            "WHERE model_key = ?", (now, now, key))
        self.kb.commit()
        logger.info(f"[UI] Карточки удалены: {', '.join(sel)}")
        for w in self.form_frame.winfo_children():
            w.destroy()
        self.form = None
        self.reload()
        self.on_change()
