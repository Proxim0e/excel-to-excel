import subprocess
import sys
import time
import tkinter as tk
import unittest

from src.ui import processes as pr

try:
    from src.ui.app import ensure_tcl
    ensure_tcl()
    tk.Tk().destroy()
    HAS_TK = True
except Exception:  # нет дисплея - тест окна пропускается
    HAS_TK = False

SLEEP = "import time; time.sleep(60)"


@unittest.skipIf(pr.psutil is None, "psutil не установлен")
class ProcessesTests(unittest.TestCase):
    def _find(self, pid, timeout=10):
        end = time.time() + timeout
        while time.time() < end:
            for p in pr.our_processes():
                if p.pid == pid:
                    return p
            time.sleep(0.2)
        return None

    def test_memory(self):
        m = pr.memory()
        self.assertGreater(m.commit_total, 0)
        self.assertLessEqual(m.commit_free, m.commit_total)
        self.assertEqual(pr.gb(512), "512 МБ")
        self.assertEqual(pr.gb(1536), "1,5 ГБ")

    def test_child_is_task_orphan_is_extra_and_killed(self):
        # python проекта (.venv) - процесс проекта; запущен этим процессом - «задача этого окна»
        child = subprocess.Popen([sys.executable, "-c", SLEEP])
        try:
            p = self._find(child.pid)
            self.assertIsNotNone(p)
            self.assertEqual((p.kind, p.extra), ("задача этого окна", False))
        finally:
            child.kill()
        # промежуточный процесс запускает «внука» и завершается - внук остался без родителя: лишний
        starter = subprocess.run(
            [sys.executable, "-c", f"import subprocess, sys; D = subprocess.DEVNULL; "
                                   f"p = subprocess.Popen([sys.executable, '-c', {SLEEP!r}], stdin=D, stdout=D, "
                                   f"stderr=D); print(p.pid)"], capture_output=True, text=True, timeout=30)
        orphan = int(starter.stdout.strip())
        p = self._find(orphan)
        self.assertIsNotNone(p)
        self.assertTrue(p.extra)
        self.assertIn("лишний", p.kind)
        self.assertEqual(pr.kill(p), 1)
        self.assertFalse(pr.psutil.pid_exists(orphan) and pr.psutil.Process(orphan).status() != "zombie")

    def test_top_consumers(self):
        top = pr.top_consumers(3)
        self.assertEqual(len(top), 3)
        self.assertGreaterEqual(top[0][1], top[1][1])


@unittest.skipUnless(HAS_TK, "нет Tk")
class ProcessWindowTests(unittest.TestCase):
    def setUp(self):
        from src.ui.app import new_root
        self.root = new_root()          # с повтором: Tcl в .venv изредка не дочитывает свои файлы
        self.root.withdraw()

    def tearDown(self):
        self.root.destroy()

    def test_kill_extra_and_selected(self):
        listed = [pr.Proc(11, "лишний: осталось от закрытого окна", "python -m src.amdm", 300, time.time(), True),
                  pr.Proc(22, "задача этого окна", "браузер проверки AMDM", 200, time.time(), False)]
        killed, asked, logs = [], [], []
        w = pr.ProcessWindow(self.root, on_log=logs.append, lister=lambda: [p for p in listed if p.pid not in killed],
                             killer=lambda p: killed.append(p.pid) or 1, ask=lambda t: asked.append(t) or True)
        self.assertEqual(str(w.extra_btn.cget("text")), "Завершить лишние (1)")
        self.assertEqual(w.kill_extra(), 1)
        self.assertEqual(killed, [11])
        self.assertIn("завершён процесс 11", logs[0])
        w.tree.selection_set("22")
        w.kill_selected()
        self.assertIn("работа прервётся", asked[-1])           # не лишний - предупреждение
        self.assertEqual(killed, [11, 22])
        self.assertEqual(str(w.extra_btn.cget("state")), "disabled")


if __name__ == "__main__":
    unittest.main()
