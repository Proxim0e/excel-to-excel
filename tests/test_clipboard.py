"""Ctrl+C/V при русской раскладке: сочетание узнаётся по коду клавиши."""
import tkinter as tk
import unittest
from types import SimpleNamespace

from src.ui import clipboard
from src.ui.app import new_root


class ClipboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.root = new_root()   # с повтором: Tcl в .venv изредка не дочитывает свои файлы
        except tk.TclError as e:
            raise unittest.SkipTest(f"нет дисплея: {e}")
        cls.root.geometry("+-3000+-3000")   # виртуальные события доходят только до показанного окна

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def test_copy_paste_with_cyrillic_layout(self):
        src, dst = tk.Entry(self.root), tk.Entry(self.root)
        src.pack()
        dst.pack()
        self.root.update()
        src.insert(0, "DT2B 35x43")
        src.selection_range(0, "end")
        ev = SimpleNamespace(keysym="Cyrillic_es", keycode=67, widget=src)
        self.assertEqual(clipboard._on_key(ev), "break")
        self.root.update()
        self.assertEqual(self.root.clipboard_get(), "DT2B 35x43")
        clipboard._on_key(SimpleNamespace(keysym="Cyrillic_em", keycode=86, widget=dst))
        self.root.update()
        self.assertEqual(dst.get(), "DT2B 35x43")

    def test_latin_layout_left_to_tk(self):
        e = tk.Entry(self.root)
        self.assertIsNone(clipboard._on_key(SimpleNamespace(keysym="c", keycode=67, widget=e)))


if __name__ == "__main__":
    unittest.main()
