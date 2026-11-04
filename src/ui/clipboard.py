"""
Ctrl+C / Ctrl+V / Ctrl+X / Ctrl+A в окне при любой раскладке и меню правой кнопки «Копировать / Вставить».

Tk привязывает копирование к символу «c» латинской раскладки: при русской раскладке клавиша даёт «с»
(Cyrillic_es), и стандартная привязка не срабатывает. Здесь сочетания ловятся по коду клавиши (Windows:
C = 67, V = 86, X = 88, A = 65). Поле только для чтения (лог, просмотр документа) по клику не получает фокус -
без фокуса Ctrl+C некуда отправить, поэтому фокус ставится явно.
"""
import tkinter as tk

KEYCODES = {67: "<<Copy>>", 86: "<<Paste>>", 88: "<<Cut>>", 65: "<<SelectAll>>"}
LATIN = {"c", "v", "x", "a"}
EDITABLE = ("Entry", "TEntry", "TCombobox", "Text", "Spinbox", "TSpinbox")


def _on_key(event):
    if event.keysym.lower() in LATIN:          # латинская раскладка - сработали стандартные привязки Tk
        return None
    virtual = KEYCODES.get(event.keycode)
    if virtual and event.widget.winfo_class() in EDITABLE:
        event.widget.event_generate(virtual)
        return "break"
    return None


def _menu(event):
    w = event.widget
    try:
        readonly = str(w.cget("state")) in ("disabled", "readonly")
    except tk.TclError:
        readonly = False
    w.focus_set()
    menu = tk.Menu(w, tearoff=0)
    if not readonly:
        menu.add_command(label="Вырезать", command=lambda: w.event_generate("<<Cut>>"))
    menu.add_command(label="Копировать", command=lambda: w.event_generate("<<Copy>>"))
    if not readonly:
        menu.add_command(label="Вставить", command=lambda: w.event_generate("<<Paste>>"))
    menu.add_separator()
    menu.add_command(label="Выделить всё", command=lambda: _select_all(w))
    menu.tk_popup(event.x_root, event.y_root)


def _select_all(w):
    if w.winfo_class() == "Text":
        w.tag_add("sel", "1.0", "end-1c")
    else:
        w.selection_range(0, "end")


def install(root: tk.Misc) -> None:
    root.bind_all("<Control-KeyPress>", _on_key, add="+")
    root.bind_class("Text", "<Button-1>", lambda e: e.widget.focus_set(), add="+")
    for cls in EDITABLE:
        root.bind_class(cls, "<Button-3>", _menu, add="+")
