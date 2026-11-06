"""Окно приложения без показа на экране: лог и этап, вопросы появляются в списке, ответы сохраняются."""
import logging
import tempfile
import unittest
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import ttk
    from src.ui.app import ensure_tcl
    ensure_tcl()
    tk.Tk().destroy()
    HAS_TK = True
except Exception:  # нет дисплея - тесты окна пропускаются
    HAS_TK = False

from src.evaluate import confidence as cf
from src.evaluate import engine
from src.evaluate.answers import answer_card
from src.history.db import connect


def rv_label(key):
    from src.excel.review import BY_KEY
    return BY_KEY[key].label


@unittest.skipUnless(HAS_TK, "нет tkinter / дисплея")
class AppTests(unittest.TestCase):
    def setUp(self):
        from src.ui.app import App
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "kb.sqlite"
        from src.ui.app import new_root
        self.root = new_root()
        self.root.withdraw()
        self.app = App(self.root, db=self.db)

    def tearDown(self):
        logging.getLogger().handlers = [h for h in logging.getLogger().handlers
                                        if type(h).__name__ != "QueueHandler"]
        self.app.kb.close()
        self.root.destroy()
        self.tmp.cleanup()

    def test_review_tab_decision_saved_to_copy(self):
        from openpyxl import load_workbook
        from tests.test_review import make_result
        result = make_result(Path(self.tmp.name) / "result_r.xlsx")
        self.app.result_var.set(str(result))
        self.assertEqual(self.app.result_short.get(), "result_r.xlsx")
        self.app.tabs.select(self.app.review_frame)
        tab = self.app._open_review_tab()
        self.assertEqual(tab.lots.get_children(), ("3", "4"))
        tab.lots.selection_set("3")
        tab._lot_selected()
        self.assertEqual(tab.offer.name, "Alfa SRL")
        self.assertIn("AB-123", tab.viewer.probes)
        tab.choice_var.set(rv_label("corespunde"))
        tab._choice_changed()
        tab.comment_var.set("verificat")
        tab.apply()
        self.assertEqual(tab.offer.name, "Beta SA")              # дальше - следующая непроверенная оферта
        tab.filter_var.set("не проверенные мной")
        tab.fill_lots()
        self.assertIn("3", tab.lots.get_children())
        tab.choice_var.set(rv_label("price_130"))
        tab._choice_changed()
        tab.apply()                                               # лот проверен: запись в фоне и следующий лот
        self.assertEqual(tab.lot.lot_no, 4)
        self.assertNotIn("3", tab.lots.get_children())            # фильтр «не проверенные мной»
        self.assertIsNotNone(tab.save())                          # дождаться фоновой записи
        ws = load_workbook(result.with_name("result_r_verificat.xlsx"))["lot 3"]
        self.assertEqual((ws["E1"].value, ws["E11"].value), ("Tehnic corespunde", "Set perfuzie AB-123\n// verificat"))

    def test_review_tab_copy_producer_fields(self):
        """Производитель / страна / модель - отдельные строки, щелчок копирует значение без «Ключ:»."""
        import openpyxl
        from tests.test_review import make_result
        path = Path(self.tmp.name) / "result_p.xlsx"
        make_result(path)
        wb = openpyxl.load_workbook(path)
        wb["lot 3"]["G3"] = "Producator: Shenzhen UpnMed Equipment\nCo., Ltd\nȚara: China\nModel: U603-1A"
        wb.save(path)
        self.app.result_var.set(str(path))
        self.app.tabs.select(self.app.review_frame)
        tab = self.app._open_review_tab()
        tab.lots.selection_set("3")
        tab._lot_selected()
        self.app.root.update()
        labels = []

        def walk(w):
            if getattr(w, "copy_value", None):
                labels.append(w)
            for ch in w.winfo_children():
                walk(ch)
        walk(tab.strip.inner)
        values = [w.copy_value for w in labels]
        self.assertIn("Shenzhen UpnMed Equipment Co., Ltd", values)
        self.assertIn("China", values)
        model = labels[values.index("U603-1A")]
        model.event_generate("<Button-1>")
        self.app.root.update()
        self.assertEqual((tab.strip.copied, self.app.root.clipboard_get()), ("U603-1A", "U603-1A"))
        from src.ui.offer_strip import OfferStrip
        parts = OfferStrip.parts
        self.assertEqual(parts("Model: x", "CUET44S + CUET48S + CUET52S +\nCUET56S".replace("\n", " ")),
                         ["CUET44S", "CUET48S", "CUET52S", "CUET56S"])
        self.assertEqual(parts("Model: x", "A1, B2; C3 și D4 / E5"), ["A1", "B2", "C3", "D4", "E5"])
        self.assertEqual(parts("Model: x", "AB-12/3"), [])                 # «/» без пробелов - часть кода
        self.assertEqual(parts("Model: x", "0013.F02-UNI-S-E-21 0013.F03-UNI-S-E-21 0013.F05-UNI-S-E-21"),
                         ["0013.F02-UNI-S-E-21", "0013.F03-UNI-S-E-21", "0013.F05-UNI-S-E-21"])   # через пробел
        self.assertEqual(parts("Model: x", "Vacutainer 200"), [])           # слово без цифры - одна модель
        self.assertEqual(parts("Model: x", "CUET44S 10 ml"), [])
        self.assertEqual(parts("Țara: x", "Italia, Franța"), [])            # не модель - не делим

    def test_review_tab_confirm_registration(self):
        """«✓ DM/ISO ок»: строка 4 без пометок AMDM, «✓ verificat», зелёная; второе нажатие снимает отметку."""
        import openpyxl
        from tests.test_review import make_result
        from src.amdm.mark import GREEN, REG_OK
        path = Path(self.tmp.name) / "result_a.xlsx"
        make_result(path)
        wb = openpyxl.load_workbook(path)
        wb["lot 3"]["G4"] = "ISO 13485\n// Номер DM в оферте не указан\n// cere certificat"
        wb.save(path)
        self.app.result_var.set(str(path))
        self.app.tabs.select(self.app.review_frame)
        tab = self.app._open_review_tab()
        tab.lots.selection_set("3")
        tab._lot_selected()
        tab.confirm_registration(1, "dm")                        # номера DM нет нигде - отметка не ставится
        self.assertIsNone(tab.book.registration_confirmed(tab.lot.offers[1]))
        self.assertIn("номера DM нет", tab.state_var.get())
        tab.confirm_registration(1, "iso")
        o = tab.lot.offers[1]
        self.assertEqual(o.dm, f"ISO 13485\n// cere certificat\n{REG_OK} ISO")
        self.assertEqual(o.dm_color, GREEN)
        tab.save()
        self.assertEqual(openpyxl.load_workbook(tab.book.target)["lot 3"]["G4"].value, o.dm)
        tab.confirm_registration(1, "iso")                       # второе нажатие - снять
        self.assertEqual(tab.lot.offers[1].dm, "ISO 13485\n// cere certificat")
        # Alfa: DM000123456 + приказ; «✓ ISO ок» убирает номер DM и приказ, «✓ DM ок» - оставляет
        alfa = tab.lot.offers[0]
        tab.book.sheet(tab.lot)["E4"] = "DM000123456\nRg04-000260 01-11-2022\n// Модель не найдена"
        alfa.dm = tab.book.sheet(tab.lot)["E4"].value
        tab.confirm_registration(0, "dm")
        self.assertEqual(tab.lot.offers[0].dm, f"DM000123456\nRg04-000260 01-11-2022\n{REG_OK} DM")
        self.assertEqual(tab.book.registration_confirmed(tab.lot.offers[0]), "dm")
        tab.confirm_registration(0, "iso")
        self.assertEqual(tab.lot.offers[0].dm, f"ISO 13485\n{REG_OK} ISO")
        self.assertEqual(tab.book.registration_confirmed(tab.lot.offers[0]), "iso")
        tab.confirm_registration(0, "dm")                        # обратно на DM - номер и приказ возвращаются
        self.assertEqual(tab.lot.offers[0].dm, f"DM000123456\nRg04-000260 01-11-2022\n{REG_OK} DM")
        tab.confirm_registration(0, "dm")                        # нажатие на выбранную - снять
        self.assertIsNone(tab.book.registration_confirmed(tab.lot.offers[0]))
        # после перезапуска (памяти нет) номер DM берётся из исходного result-файла
        tab.confirm_registration(0, "iso")
        tab.book._removed_dm.clear()
        tab.confirm_registration(0, "dm")
        self.assertEqual(tab.lot.offers[0].dm, f"DM000123456\n{REG_OK} DM")
        # «✓ Aviz»: Aviz Sanitar вместо ISO / CE - убираются номера DM и сертификаты; «✓ ISO» их возвращает
        tab.book.sheet(tab.lot)["E4"] = "DM000123456\nCE/ISO\n// Модель не найдена"
        tab.lot.offers[0].dm = tab.book.sheet(tab.lot)["E4"].value
        tab.confirm_registration(0, "aviz")
        self.assertEqual(tab.lot.offers[0].dm, f"Aviz Sanitar\n{REG_OK} AVIZ")
        self.assertEqual(tab.book.registration_confirmed(tab.lot.offers[0]), "aviz")
        tab.confirm_registration(0, "iso")
        self.assertEqual(tab.lot.offers[0].dm, f"CE/ISO\n{REG_OK} ISO")
        tab.confirm_registration(0, "dm")
        self.assertEqual(tab.lot.offers[0].dm, f"DM000123456\nCE/ISO\n{REG_OK} DM")

    def test_review_tab_choice_sets_tab_letter(self):
        from tests.test_review import make_result
        result = make_result(Path(self.tmp.name) / "result_s.xlsx")
        self.app.result_var.set(str(result))
        self.app.tabs.select(self.app.review_frame)
        tab = self.app._open_review_tab()
        tab.lots.selection_set("3")
        tab._lot_selected()
        tab.choice_var.set(rv_label("mostre"))
        tab._choice_changed()
        self.assertEqual((tab.suffix_var.get(), tab.lot.sheet), ("M", "lot 3 (M)"))
        asked = []
        tab.ask_alternative_suffix = lambda: asked.append(1) or "Ad"      # окно «A или Ad?»
        tab.choice_var.set(rv_label("alternativa"))
        tab._choice_changed()
        self.assertEqual((asked, tab.lot.sheet), ([1], "lot 3 (Ad)"))
        tab.ask_alternative_suffix = lambda: None                        # «Не менять вкладку»
        tab._choice_changed()
        self.assertEqual(tab.lot.sheet, "lot 3 (Ad)")
        tab.choice_var.set(rv_label("corespunde"))       # другой статус: буква снимается, её больше никто не держит
        tab._choice_changed()
        self.assertEqual((tab.suffix_var.get(), tab.lot.sheet), ("", "lot 3"))
        tab.choice_var.set(rv_label("mostre"))
        tab._choice_changed()
        self.assertEqual(tab.lot.sheet, "lot 3 (M)")
        tab.choice_var.set(rv_label("technical"))
        tab._choice_changed()
        self.assertEqual(tab.lot.sheet, "lot 3")
        if len(tab.lot.offers) > 1:                      # M держит другая оферта с образцами - буква остаётся
            tab.lot.offers[1].row1 = "Tehnic corespunde (mostre)"
            tab.choice_var.set(rv_label("mostre"))
            tab._choice_changed()
            tab.choice_var.set(rv_label("corespunde"))
            tab._choice_changed()
            self.assertEqual(tab.lot.sheet, "lot 3 (M)")

    def test_review_tab_amdm_button(self):
        from src.amdm.cache import RegistryCache
        from src.amdm.models import RegistryRecord
        from tests.test_review import make_result
        result = make_result(Path(self.tmp.name) / "result_a.xlsx")
        amdm_db = Path(self.tmp.name) / "amdm.sqlite"
        cache = RegistryCache(amdm_db)
        cache.put(RegistryRecord(dm="DM000123456", model="AB-123", catalog="AB-123", producer="Producer X"), "site")
        cache.close()
        self.app.result_var.set(str(result))
        self.app.tabs.select(self.app.review_frame)
        tab = self.app._open_review_tab()
        tab.amdm_db, tab.amdm_offline = amdm_db, True          # без сайта: по локальной копии
        tab.lots.selection_set("3")
        tab._lot_selected()
        tab.check_amdm(1)                                        # кнопка «AMDM ⟳» у Beta SA
        self.assertEqual(tab.offer.name, "Beta SA")
        label = tab.strip.amdm_labels[1]
        self.assertIn("проверяю", label.cget("text"))
        tab.amdm_checker.idle = 0.1
        for _ in range(100):                                     # дождаться очереди проверок и опроса окна
            self.root.update()
            if "проверяю" not in label.cget("text"):
                break
            self.root.after(100)
        self.assertIn("DM000123456 - ", label.cget("text"))
        self.assertTrue(label.winfo_manager())                   # показан под строкой DM
        tab.fill_offers()                                        # перерисовка колонок - результат остаётся
        self.assertIn("DM000123456", tab.strip.amdm_labels[1].cget("text"))

    def test_review_tab_amdm_all_into_current_file(self):
        """Общая кнопка «AMDM»: итог - в открытый файл проверки (строка 4, лист «Verificare AMDM») и под кнопками."""
        import openpyxl
        from unittest import mock
        from src.amdm.cache import RegistryCache
        from src.amdm.models import RegistryRecord
        from tests.test_review import make_result
        result = make_result(Path(self.tmp.name) / "result_a.xlsx")
        amdm_db = Path(self.tmp.name) / "amdm.sqlite"
        cache = RegistryCache(amdm_db)
        cache.put(RegistryRecord(dm="DM000123456", model="AB-123", catalog="AB-123", producer="Producer X",
                                 order="Rg04-000083", order_date="2023-04-13"), "site")
        cache.close()
        self.app.result_var.set(str(result))
        self.app.tabs.select(self.app.review_frame)
        tab = self.app._open_review_tab()
        tab.amdm_db = amdm_db
        tab.lots.selection_set("3")
        tab._lot_selected()
        with mock.patch("src.amdm.__main__.collect_types", lambda *a, **k: {}):
            self.app._amdm_into_review()
        target = tab.book.target
        self.assertTrue(target.is_file())
        wb = openpyxl.load_workbook(target)
        self.assertIn("Verificare AMDM", wb.sheetnames)
        self.assertEqual(tab.lot.lot_no, 3)                       # тот же лот на экране
        texts = [l.cget("text") for l in tab.strip.amdm_labels.values()]
        self.assertTrue(any("DM000123456 - " in t for t in texts), texts)
        self.assertIn("AMDM внесено", self.app.activity.entries[-1].text)

    def test_activity_log_in_tabs(self):
        from src.ui.activity import DONE, FAILED, RUNNING
        act = self.app.activity
        log = logging.getLogger("x")
        log.info("=== AMDM: старт ===")
        log.info("[AMDM] сайт: 1/3 (33%) DM1 → найден")
        self.app._poll_log()
        e = act.by_key[("task", "AMDM")]
        self.assertEqual((e.state, e.detail), (RUNNING, "сайт: 1/3 (33%) DM1 → найден"))
        log.info("=== AMDM: готово ===")
        log.info("[REVIEW] lot 3, Beta SA: Tehnic corespunde")
        log.error("Поиск на сайте прерван")
        self.app._poll_log()
        self.assertEqual(e.state, DONE)
        self.assertEqual([x.text for x in act.entries[-2:]], ["lot 3, Beta SA: Tehnic corespunde",
                                                               "Поиск на сайте прерван"])
        self.assertEqual(act.entries[-1].state, FAILED)
        self.app.tabs.select(self.app.review_frame)
        self.app._open_review_tab()
        self.root.after(200)
        self.root.update()
        self.assertGreaterEqual(len(act.views), 2)                 # «Работа» и «Проверка лотов»
        shown = act.views[-1].text.get("1.0", "end")
        self.assertIn("✓ AMDM", shown)
        self.assertIn("✗ Поиск на сайте прерван", shown)

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

    def test_start_dialog_asks_how_we_work(self):
        from unittest import mock
        from src.ui import app as ui
        parent = Path(self.tmp.name) / "-21643233.xlsx"
        with mock.patch.object(ui, "parent_info", return_value=(parent, "https://achizitii.md/ro/public/tender/21643233/")), \
                mock.patch.object(ui, "tender_dirs", return_value=[]), \
                mock.patch.object(ui.App, "run_tender") as run:
            win = self.app.ask_start()
            self.assertEqual(win.title(), "Как будем работать?")
            self.assertEqual(self.app.start_mode.get(), "parent")         # нет скачанных - родительский файл
            texts = [w.cget("text") for w in win.winfo_children()[0].winfo_children()
                     if isinstance(w, ttk.Radiobutton)]
            self.assertIn("-21643233.xlsx", texts[1])
            # новый тендер без номера - диалог не закрывается
            with mock.patch.object(ui.messagebox, "showwarning") as warn:
                self.assertFalse(self.app.apply_start("tender", run=True))
            warn.assert_called_once()
            self.app.start_number.set("21641880")
            self.assertTrue(self.app.apply_start("tender", run=True))
            run.assert_called_once()
            self.assertEqual((self.app.mode, self.app.url_var.get()), ("tender", "21641880"))
            self.assertIsNone(self.app.start_win)

    def test_continue_picks_working_file_of_this_tender(self):
        """«Скачанный тендер»: не последняя копия (_evaluare / _AMDM), а <база>_verificat.xlsx; потом - запомненный."""
        from unittest import mock
        from src.ui import app as ui
        base = Path(self.tmp.name)
        (base / "downloads" / "21641880").mkdir(parents=True)
        (base / "downloads" / "21641880" / "manifest.sqlite").write_bytes(b"")
        res = base / "res"
        res.mkdir()
        for name in ("result_05.11.2026_10-44-15.xlsx", "result_05.11.2026_10-44-15_verificat.xlsx",
                     "result_05.11.2026_10-44-15_verificat_evaluare.xlsx"):
            (res / name).write_bytes(b"x")
        latest = res / "result_05.11.2026_10-44-15_verificat_evaluare.xlsx"
        self.assertEqual(ui.base_result(res / "result_1_verificat_evaluare_AMDM.xlsx").name, "result_1.xlsx")
        self.assertEqual(ui.base_result(res / "result_1_verificat.bak.xlsx").name, "result_1.xlsx")
        with mock.patch.object(ui, "BASE_DIR", base), mock.patch.object(ui, "latest_result", return_value=latest):
            self.app.start_folder = tk.StringVar(value="21641880")
            self.assertTrue(self.app.apply_start("continue"))
            self.assertEqual(Path(self.app.result_var.get()).name, "result_05.11.2026_10-44-15_verificat.xlsx")
            # человек выбрал другой файл этого тендера - при следующем продолжении берётся он
            other = res / "result_06.11.2026_09-00-00.xlsx"
            other.write_bytes(b"x")
            self.app.result_var.set(str(other))
            self.app.result_var.set("")
            self.assertTrue(self.app.apply_start("continue"))
            self.assertEqual(Path(self.app.result_var.get()), other)

    def test_poll_questions_survives_memory_error(self):
        """MemoryError в опросе вопросов не останавливает опрос; одна и та же ошибка - в лог один раз."""
        scheduled = []
        self.app.root.after = lambda ms, fn=None, *a: scheduled.append(ms)

        def boom():
            raise MemoryError()
        self.app.refresh_questions = boom
        with self.assertLogs("ui", level="WARNING") as logs:
            self.app._poll_questions()
            self.app._poll_questions()
        self.assertEqual(scheduled, [5000, 5000])
        self.assertEqual(len(logs.records), 1)
        self.assertIn("памяти Windows", logs.output[0])
        self.app.refresh_questions = lambda: 0          # память освободилась - обычный опрос
        self.app._poll_questions()
        self.assertEqual(scheduled[-1], 1500)
        del self.app.root.after

    def test_close_stops_running_process_after_asking(self):
        from unittest import mock
        from src.ui import app as ui
        proc = mock.Mock()
        proc.poll.return_value = None
        self.app.proc, self.app.task_title = proc, "AMDM"
        with mock.patch.object(ui.messagebox, "askyesno", return_value=False) as ask:
            self.app.close()
        self.assertIn("AMDM", ask.call_args[0][1])
        proc.terminate.assert_not_called()                       # «Нет» - окно осталось, процесс идёт
        with mock.patch.object(ui.messagebox, "askyesno", return_value=True), \
                mock.patch.object(self.root, "destroy") as destroy:
            self.app.close()
        proc.terminate.assert_called_once()
        destroy.assert_called_once()

    def test_doc_viewer_search_by_name(self):
        from src.ui.doc_viewer import DocViewer
        tmp = Path(self.tmp.name)
        docs = []
        for label in ("Oferta tehnica", "Certificat ISO 13485", "Fișa tehnică lotul 147", "Declaratie CE"):
            path = tmp / (label.replace(" ", "_") + ".txt")
            path.write_text(f"текст: {label}", encoding="utf-8")
            docs.append({"label": label, "path": path})
        v = DocViewer(self.root)
        v.set_docs(docs)
        self.assertEqual(v.cur, 0)
        v.name_var.set("fisa tehnica")                           # без диакритики находит «Fișa tehnică»
        self.assertEqual([docs[i]["label"] for i in v.shown], ["Fișa tehnică lotul 147"])
        v.name_var.set("tehnic")
        self.assertEqual(len(v.shown), 2)
        v.name_var.set("iso")
        self.assertEqual((v.shown, v.cur, v.count_var.get()), ([1], 1, "1 из 4"))   # открыт найденный
        v.set_docs(docs)                                         # следующий участник - поиск остаётся
        self.assertEqual(v.cur, 1)
        v.name_var.set("нет такого")
        self.assertEqual(v.shown, [])
        v.name_var.set("")
        self.assertEqual(len(v.shown), 4)
        v.search_var.set("U223-11BE")                            # «Копировать» - текст поиска в буфер
        self.assertEqual(v.copy_search(), "U223-11BE")
        self.assertEqual(self.root.clipboard_get(), "U223-11BE")
        calls = []                                               # «Папка» - Проводник с выделенным файлом
        v.launch = calls.append
        f = Path(self.tmp.name) / "doc.pdf"
        f.write_bytes(b"x")
        v.docs[v.cur]["path"] = str(f)
        v.open_folder()
        self.assertEqual(calls, [f'explorer /select,"{f.resolve()}"'])     # кавычки - только вокруг пути
        v.docs[v.cur]["path"] = str(Path(self.tmp.name) / "нет.pdf")       # файла нет - его папка
        v.open_folder()
        self.assertEqual(calls[-1], f'explorer "{Path(self.tmp.name).resolve()}"')

    def test_empty_tender_field_asks_before_parent_file(self):
        from unittest import mock
        from src.ui import app as ui
        parent = Path(self.tmp.name) / "-21643233.xlsx"
        with mock.patch.object(ui, "parent_info", return_value=(parent, None)), \
                mock.patch.object(ui.messagebox, "askyesno", return_value=False) as ask, \
                mock.patch.object(ui.App, "_start_process") as start:
            self.app.run_tender()
            self.assertIn("-21643233.xlsx", ask.call_args[0][1])
            start.assert_not_called()                                      # «нет» - ничего не запущено
            ask.return_value = True
            self.app.run_tender()
            self.assertNotIn("--tender", start.call_args[0][1])            # «да» - по родительскому файлу
            self.assertEqual(self.app.mode, "parent")

    def test_card_form_quick_fill(self):
        import sqlite3
        import fitz
        from unittest import mock
        from src.ui import card_form
        # тендер с одним документом участника и требованием лота
        tdir = Path(self.tmp.name) / "T1"
        (tdir / "lot").mkdir(parents=True)
        pdf = tdir / "lot" / "fisa.pdf"
        doc = fitz.open()
        doc.new_page().insert_text((72, 72), "Electrod SF01\nDiametru: 40 mm\nImpedanta 10 Hz 120 ohm")
        doc.save(pdf)
        doc.close()
        m = sqlite3.connect(tdir / "manifest.sqlite")
        m.executescript("CREATE TABLE documents (sha256 TEXT, doc_type TEXT, pages INT, has_text INT);"
                        "CREATE TABLE document_refs (path TEXT, lot_no INT, participant TEXT, sha256 TEXT);")
        m.execute("INSERT INTO documents VALUES ('x', 'datasheet', 1, 1)")
        m.execute("INSERT INTO document_refs VALUES (?, 29, 'Electeh SRL', 'x')", (str(pdf),))
        m.commit()
        m.close()
        kb = connect(self.db)
        lot = engine.Lot("T1", 29, "lot 29", "Lot nr. 29 Electrod", "1.diametru 38 - 47mm, 2. electrod Ag/AgCl")
        ev = engine.Evaluation("T1", 29, "Electeh SRL", "DM:DM1", cf.CLARIFICARE, cf.PENDING, 0.3, ["x"],
                               findings={"card_prefill": {"model_key": "DM:DM1", "producer": "Intco", "model": "SF01",
                                                          "codes": "SF01", "dm": "DM1", "params": {}}},
                               ask_card=True)
        engine.save(kb, ev, lot)
        kb.commit()
        kb.close()
        self.app.tender_var.set(str(tdir))
        self.app.refresh_questions()
        self.app.q_list.selection_clear(0, "end")
        self.app.q_list.selection_set(0)
        self.app.show_selected()
        form = self.app.card_form
        # требование лота: подсказка и строки пунктов требования
        self.assertIn("diametru", form.requirement)
        self.assertEqual([i["name"].get() for i in form.param_widgets], ["diametru", "electrod Ag/AgCl"])
        # значение из технического документа участника: та же величина (мм) рядом со словом «diametru»
        self.assertEqual(list(form.param_widgets[0]["value"].cget("values")), ["38 - 47mm", "40 mm"])
        self.assertEqual(form.param_widgets[0]["value"].get(), "40 mm")
        self.assertEqual(form.doc_filled_var.get(), "из документов заполнено: 1")
        # документ участника открыт, найден код модели
        self.assertIn("Diametru: 40 mm", form.doc_text.get("1.0", "end"))
        self.assertEqual(form.search_var.get(), "SF01")
        self.assertEqual(form.hits_var.get(), "1/1")
        # выделенный текст -> в параметры
        form.add_selection("Impedanta 10 Hz 120 ohm")
        self.assertEqual(form.params(), {"diametru": "40 mm", "Impedanta": "10 Hz 120 ohm"})
        # экономические агенты: текущий участник выбран, можно дописать
        self.assertEqual(form.selected(form.agent_list), ["Electeh SRL - этот тендер"])
        form.winner_list.add_entry.insert(0, "Labromed (2025, 21600000)")
        form.winner_list.add_push()
        win = form.show_requirement()
        win.destroy()
        form.save_button.invoke()
        row = self.app.kb.execute("SELECT agents, winners, params, source FROM products WHERE source = 'human'").fetchone()
        self.assertEqual(row[:2], ("Electeh SRL", "Labromed (2025, 21600000)"))
        self.assertEqual(row[3], "human")

    def test_cards_tab_edit_new_delete(self):
        from src.evaluate.cards import save_card
        save_card(self.app.kb, {"model_key": "DM:DM1", "producer": "Fiab", "model": "F4141", "dm": "DM1",
                                "params": {"Diametru": "5 mm"}}, source="prefill")
        save_card(self.app.kb, {"model_key": "P:intco|sf01", "producer": "Intco", "model": "SF01"}, source="human")
        self.app.tabs.select(self.app.cards_frame)
        tab = self.app._open_cards_tab()
        self.assertEqual(len(tab.tree.get_children()), 2)
        tab.search_var.set("fiab")
        tab.fill()
        self.assertEqual(tab.tree.get_children(), ("DM:DM1",))
        tab.filter_var.set("подтверждённые")
        tab.search_var.set("")
        tab.fill()
        self.assertEqual(tab.tree.get_children(), ("P:intco|sf01",))
        tab.filter_var.set("все")
        tab.fill()
        # редактирование: предложенная карточка -> подтверждена, параметр изменён
        tab.tree.selection_set("DM:DM1")
        form = tab.edit_selected()
        self.assertEqual(form.save_button.cget("text"), "Подтвердить карточку")
        form.param_widgets[0]["value"].set("6 mm")
        form.save_button.invoke()
        # с моделью ключ - производитель + модель (DM меняется со временем), предложенная карточка переехала
        self.assertEqual(self.app.kb.execute("SELECT source, params, dm FROM products WHERE model_key = 'P:fiab|f4141'"
                                             ).fetchone(), ("human", '{"Diametru": "6 mm"}', "DM1"))
        self.assertIsNone(self.app.kb.execute("SELECT source FROM products WHERE model_key = 'DM:DM1'").fetchone())
        self.assertIn("P:fiab|f4141", tab.tree.get_children())
        # по старому номеру DM находится та же карточка
        tab.new_card("DM1")
        self.assertEqual(tab.tree.selection(), ("P:fiab|f4141",))
        self.assertTrue((self.app.answers_dir).glob("*.jsonl"))
        # новая карточка по номеру DM
        form = tab.new_card("DM000777")
        form.entries["producer"].insert(0, "Natus")
        form.save_button.invoke()
        self.assertEqual(self.app.kb.execute("SELECT producer FROM products WHERE model_key = 'DM:DM000777'").fetchone(),
                         ("Natus",))
        # удаление - метка, из списка пропадает
        tab.tree.selection_set("P:intco|sf01")
        tab.delete_selected(confirm=False)
        self.assertNotIn("P:intco|sf01", tab.tree.get_children())
        self.assertEqual(self.app.kb.execute("SELECT source FROM products WHERE model_key = 'P:intco|sf01'").fetchone(),
                         ("deleted",))

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
        self.assertEqual(self.app.q_list.get(0), "Карточка: Fiab")    # карточки - первыми
        self.app.show_selected()
        self.assertEqual(self.app.current["kind"], "product_card")
        # подтвердить карточку кнопкой формы
        self.app.card_form.save_button.invoke()
        self.assertEqual(self.app.kb.execute("SELECT source FROM products").fetchone()[0], "human")
        self.app.q_list.selection_set(0)
        self.app.show_selected()
        self.assertEqual(self.app.current["kind"], "offer_status")
        button = [w for w in self.app.form.winfo_children() if isinstance(w, tk.ttk.Button)][-1]
        button.invoke()                                                 # предложение системы - clarificare
        self.assertEqual(self.app.kb.execute("SELECT status, source FROM evaluations").fetchone(),
                         ("clarificare", "human"))
        self.assertEqual(self.app.questions, [])

    def test_question_search_and_card_closed_elsewhere(self):
        kb = connect(self.db)
        for n, (participant, key, producer) in enumerate((("Electeh SRL", "P:fiab|f1", "Fiab"),
                                                          ("Dita", "P:intco|s2", "Intco")), start=1):
            lot = engine.Lot("T1", n, f"lot {n}", f"Lot nr. {n}", "x")
            engine.save(kb, engine.Evaluation("T1", n, participant, key, cf.CLARIFICARE, cf.PENDING, 0.3, ["x"],
                                              findings={"card_prefill": {"model_key": key, "producer": producer}},
                                              ask_card=True), lot)
        kb.close()
        self.app.refresh_questions()
        self.assertEqual(len(self.app.questions), 4)
        self.app.q_search.set("intco")
        self.assertEqual([self.app.q_list.get(i) for i in range(self.app.q_list.size())],
                         ["Карточка: Intco", "Лот 2: Dita"])                # и карточка, и оферта этой модели
        self.assertIn("найдено: 2", self.app.qf.cget("text"))
        self.app.q_search.set("fiab карточка")                          # несколько слов - все должны быть
        self.assertEqual(self.app.q_list.get(0, "end"), ("Карточка: Fiab",))
        self.app.q_search.set("dita")                                   # по участнику - вопрос по оферте
        self.assertEqual(self.app.q_list.get(0), "Лот 2: Dita")
        self.app.q_search.set("")
        # карточку подтвердили на вкладке «База карточек» - вопрос пропадает при следующем опросе
        self.app.q_list.selection_clear(0, "end")
        self.app.q_list.selection_set(0)
        self.app.show_selected()
        qid = self.app.current["id"]
        answer_card(self.app.kb, qid, {"model_key": self.app.current["model_key"], "producer": "Fiab"})
        self.app.refresh_questions()
        self.assertNotIn(qid, [q["id"] for q in self.app.questions])
        self.assertNotEqual(self.app.current["id"], qid)


if __name__ == "__main__":
    unittest.main()
