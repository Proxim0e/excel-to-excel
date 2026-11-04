"""Окно приложения без показа на экране: лог и этап, вопросы появляются в списке, ответы сохраняются."""
import logging
import tempfile
import unittest
from pathlib import Path

try:
    import tkinter as tk
    from src.ui.app import ensure_tcl
    ensure_tcl()
    tk.Tk().destroy()
    HAS_TK = True
except Exception:  # нет дисплея - тесты окна пропускаются
    HAS_TK = False

from src.evaluate import confidence as cf
from src.evaluate import engine
from src.history.db import connect


@unittest.skipUnless(HAS_TK, "нет tkinter / дисплея")
class AppTests(unittest.TestCase):
    def setUp(self):
        from src.ui.app import App
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "kb.sqlite"
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = App(self.root, db=self.db)

    def tearDown(self):
        logging.getLogger().handlers = [h for h in logging.getLogger().handlers
                                        if type(h).__name__ != "QueueHandler"]
        self.app.kb.close()
        self.root.destroy()
        self.tmp.cleanup()

    def test_log_sets_stage(self):
        logging.getLogger("x").info("[ANEXA22] Строк спецификаций: 260")
        self.app._poll_log()
        self.assertEqual(self.app.stage_var.get(), "Этап: Anexa 22")
        self.assertIn("Строк спецификаций", self.app.log_text.get("1.0", "end"))

    def test_live_progress_current_action_and_colors(self):
        from src.ui.app import log_tag
        log = logging.getLogger("amdm")
        log.info("[AMDM] сайт: 23/192 (12%) DM000689873 → найден: PLASTI-MED; ACCESORII")
        self.app._poll_log()
        self.assertEqual(self.app.stage_var.get(), "Этап: AMDM")
        self.assertEqual(self.app.progress["value"], 12.0)
        self.assertIn("DM000689873 → найден", self.app.now_var.get())
        logging.getLogger("x").info("[EVAL] лот 5 (12/283, 4%), Electeh SRL: alternativa (авто)")
        self.app._poll_log()
        self.assertEqual(self.app.progress["value"], 4.0)
        self.assertEqual(log_tag("[EVAL] лот 5, X: alternativa (авто)"), "alt")
        self.assertEqual(log_tag("[AMDM] lot 3, X: DM1 galben — модель не найдена"), "warn")
        self.assertEqual(log_tag("[AMDM] сайт: 1/2 (50%) DM1 → нет в реестре"), "error")
        self.assertEqual(log_tag("[AMDM] lot 3, X: DM1 verde"), "ok")

    def test_import_main_keeps_window_log(self):
        # src/main.py при импорте вызывал basicConfig(force=True) и снимал обработчик окна - лог пропадал
        import sys
        src_dir = str(Path(__file__).resolve().parent.parent / "src")
        if src_dir not in sys.path:
            sys.path.insert(0, src_dir)
        import src.main  # noqa: F401
        self.assertIn(self.app.handler, logging.getLogger().handlers)

    def test_process_task_streams_log_stage_and_progress(self):
        code = ("import logging; logging.basicConfig(level=20, format='[%(asctime)s] [%(levelname)s] %(message)s');"
                "logging.info('[AMDM] сайт: 1/2 (50%) DM1 → найден: X'); logging.warning('медленно')")
        self.app._start_process("AMDM", ["-c", code])
        self.app.worker.join(30)
        self.app._poll_log()
        log = self.app.log_text.get("1.0", "end")
        self.assertIn("DM1 → найден", log)
        self.assertIn("[WARNING] медленно", log)
        self.assertEqual(self.app.stage, "AMDM")
        self.app._poll_log()                     # _finished из потока задачи - через очередь окна
        self.assertEqual(self.app.progress["value"], 100)
        self.assertIn("✓AMDM", self.app.stages.cget("text"))
        self.assertIn("готово", self.app.stage_var.get())

    def test_medical_type_question_in_window(self):
        from src.evaluate.answers import ask_medical_type, human_types
        kb = connect(self.db)
        ask_medical_type(kb, "DVD-R", {"word": "DVD-R", "registry": [{"query": "DVD-R", "total": 0}],
                                       "offers": [{"sheet": "lot 234", "participant": "Ecochimie", "model": "DVD-R 4.7",
                                                   "lot_title": "Lot nr. 234 DVD-R+plicuri", "quality_docs": ["ISO"]}],
                                       "offers_total": 1})
        kb.close()
        self.assertEqual(self.app.refresh_questions(), 1)
        self.assertEqual(self.app.q_list.get(0), "Мед. изделие? «DVD-R»")
        self.app.show_selected()
        rows = [w for w in self.app.form.winfo_children() if isinstance(w, tk.ttk.Frame)]
        no = [b for b in rows[-1].winfo_children() if "Не медицинское" in b.cget("text")][0]
        no.invoke()
        self.assertEqual(human_types(self.app.kb), {"DVD-R": False})
        self.assertEqual(self.app.questions, [])

    def test_question_appears_and_is_answered(self):
        kb = connect(self.db)
        lot = engine.Lot("T1", 29, "lot 29", "Lot nr. 29 Electrod", "Electrod 5 mm")
        ev = engine.Evaluation("T1", 29, "Electeh SRL", "DM:DM1", cf.CLARIFICARE, cf.PENDING, 0.3, ["x"],
                               findings={"card_prefill": {"model_key": "DM:DM1", "producer": "Fiab", "dm": "DM1"}},
                               ask_card=True)
        engine.save(kb, ev, lot)
        kb.close()
        self.assertEqual(self.app.refresh_questions(), 2)               # карточка и статус оферты
        self.assertEqual(self.app.q_list.get(0), "Карточка: DM:DM1")    # карточки - первыми
        self.app.show_selected()
        self.assertEqual(self.app.current["kind"], "product_card")
        # подтвердить карточку кнопкой формы
        button = [w for w in self.app.form.winfo_children() if isinstance(w, tk.ttk.Button)][-1]
        button.invoke()
        self.assertEqual(self.app.kb.execute("SELECT source FROM products").fetchone()[0], "human")
        self.app.q_list.selection_set(0)
        self.app.show_selected()
        self.assertEqual(self.app.current["kind"], "offer_status")
        button = [w for w in self.app.form.winfo_children() if isinstance(w, tk.ttk.Button)][-1]
        button.invoke()                                                 # предложение системы - clarificare
        self.assertEqual(self.app.kb.execute("SELECT status, source FROM evaluations").fetchone(),
                         ("clarificare", "human"))
        self.assertEqual(self.app.questions, [])


if __name__ == "__main__":
    unittest.main()
