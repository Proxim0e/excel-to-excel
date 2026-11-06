"""
Память Windows и процессы программы.

- memory() - свободная виртуальная (commit) и физическая память; при нехватке commit-памяти Windows отказывает
  даже в мелочах (MemoryError в sqlite, «Tk_GetPixmap: Error from CreateDIBSection»), хотя RAM ещё свободна.
- our_processes() - процессы, связанные с проектом: задачи этого окна, другие окна программы, консоли PyCharm и
  «лишние» - оставшиеся от закрытого окна (их родитель уже завершился): python -m src.*, драйвер Playwright,
  браузер проверки AMDM. Браузер со своими дочерними процессами - одной строкой, память - суммой.
- kill() - завершить процесс со всеми дочерними.
- ProcessWindow - окно «Процессы и память»: список, «Завершить выбранные», «Завершить лишние».
"""
import ctypes
import os
import re
import sys
import time
import tkinter as tk
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Callable, Dict, List, Optional, Tuple

try:
    import psutil
except ImportError:          # не установлен - видна только память; починить: setup_env.ps1
    psutil = None

BASE_DIR = Path(__file__).resolve().parents[2]
LOW_MB = 1024                # меньше - предупреждение и предложение завершить лишние процессы
OK_MB = 1536                 # больше - предупреждение снова возможно
PLAYWRIGHT = re.compile(r"ms-playwright|playwright[\\/]driver|playwright_chromiumdev_profile", re.IGNORECASE)
WINDOW = re.compile(r"src[\\/]main\.py|-m\s+src\.ui\b", re.IGNORECASE)
TASK = re.compile(r"-m\s+(src\.[\w.]+)", re.IGNORECASE)


@dataclass
class Memory:
    commit_free: int        # МБ: свободно виртуальной памяти (RAM + файл подкачки - занятое)
    commit_total: int
    ram_free: int
    ram_total: int

    @property
    def low(self) -> bool:
        return self.commit_free < LOW_MB


def memory() -> Memory:
    if sys.platform == "win32":
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong)] + [
                (n, ctypes.c_ulonglong) for n in ("ullTotalPhys", "ullAvailPhys", "ullTotalPageFile",
                                                  "ullAvailPageFile", "ullTotalVirtual", "ullAvailVirtual",
                                                  "ullAvailExtendedVirtual")]
        st = MEMORYSTATUSEX()
        st.dwLength = ctypes.sizeof(st)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
            mb = 1024 * 1024
            return Memory(st.ullAvailPageFile // mb, st.ullTotalPageFile // mb, st.ullAvailPhys // mb,
                          st.ullTotalPhys // mb)
    if psutil:
        vm, sw = psutil.virtual_memory(), psutil.swap_memory()
        mb = 1024 * 1024
        return Memory((vm.available + sw.free) // mb, (vm.total + sw.total) // mb, vm.available // mb, vm.total // mb)
    return Memory(LOW_MB * 100, LOW_MB * 100, 0, 0)


def gb(mb: int) -> str:
    return f"{mb / 1024:.1f} ГБ".replace(".", ",") if mb >= 1024 else f"{mb} МБ"


@dataclass
class Proc:
    pid: int
    kind: str                # что это: «задача этого окна», «лишний: ...», ...
    what: str                # коротко: python -m src.amdm, браузер AMDM, ...
    mb: int                  # память процесса и всех его дочерних
    started: float
    extra: bool = False      # лишний - можно завершить без вопросов о работе
    pids: List[int] = field(default_factory=list)


def _private(p) -> int:
    try:
        m = p.memory_info()
        return getattr(m, "private", 0) or m.rss
    except Exception:
        return 0


def _what(name: str, cmd: str) -> str:
    if PLAYWRIGHT.search(cmd):
        return "драйвер Playwright (AMDM)" if "driver" in cmd.lower() and "node" in name.lower() \
            else "браузер проверки AMDM"
    if WINDOW.search(cmd):
        return "окно программы"
    if "pydevconsole" in cmd:
        return "консоль PyCharm"
    m = TASK.search(cmd)
    if m:
        return f"python -m {m.group(1)}"
    return name


def our_processes(me: Optional[int] = None) -> List[Proc]:
    """Процессы проекта, кроме этого окна (и тех, кто его запустил)."""
    if psutil is None:
        return []
    me = me or os.getpid()
    project = str(BASE_DIR).lower()
    info: Dict[int, Tuple[int, str, str, float]] = {}       # pid -> (ppid, name, cmd, create_time)
    for p in psutil.process_iter(["pid", "ppid", "name", "cmdline", "create_time"]):
        try:
            cmd = " ".join(p.info["cmdline"] or [])
        except Exception:
            cmd = ""
        info[p.info["pid"]] = (p.info["ppid"] or 0, p.info["name"] or "", cmd, p.info["create_time"] or 0)

    def related(pid):
        _, _, cmd, _ = info[pid]
        return project in cmd.lower() or bool(PLAYWRIGHT.search(cmd))

    def parent(pid):
        ppid = info[pid][0]
        # родителя нет или его pid уже занят более новым процессом - родитель завершился
        if ppid in info and ppid != pid and info[ppid][3] <= info[pid][3]:
            return ppid
        return None

    ancestors, pid = {me}, me
    while pid in info and parent(pid):
        pid = parent(pid)
        ancestors.add(pid)
    children: Dict[int, List[int]] = {}
    for pid in info:
        pp = parent(pid)
        if pp:
            children.setdefault(pp, []).append(pid)

    def subtree(pid):
        out, stack = [], [pid]
        while stack:
            x = stack.pop()
            out.append(x)
            stack.extend(children.get(x, []))
        return out

    def under_me(pid):
        while pid:
            if pid == me:
                return True
            pid = parent(pid)
        return False

    tops = [pid for pid in info if pid not in ancestors and related(pid)
            and not (parent(pid) and related(parent(pid)) and parent(pid) not in ancestors)]
    out = []
    for pid in tops:
        _, name, cmd, started = info[pid]
        pids = subtree(pid)
        mb = sum(_private(psutil.Process(x)) for x in pids if x in info and _alive(x)) // (1024 * 1024)
        what = _what(name, cmd)
        if under_me(pid):
            kind, extra = "задача этого окна", False
        elif parent(pid) is None:
            kind, extra = ("лишний: PyCharm, открывший её, закрыт" if what == "консоль PyCharm"
                           else "лишний: осталось от закрытого окна"), True
        elif what == "окно программы":
            kind, extra = "другое окно программы", False
        elif what == "консоль PyCharm":
            kind, extra = "консоль PyCharm (не программа)", False
        else:
            kind, extra = "запущен не окном (PyCharm, тесты)", False
        out.append(Proc(pid, kind, what, mb, started, extra, pids))
    return sorted(out, key=lambda p: (not p.extra, -p.mb))


def _alive(pid: int) -> bool:
    try:
        return psutil.pid_exists(pid)
    except Exception:
        return False


def top_consumers(n: int = 5) -> List[Tuple[str, int]]:
    """Кто занимает память: имя программы -> МБ (все её процессы вместе)."""
    if psutil is None:
        return []
    by: Dict[str, int] = {}
    for p in psutil.process_iter(["name"]):
        name = re.sub(r"\.exe$", "", p.info["name"] or "?", flags=re.IGNORECASE)
        by[name] = by.get(name, 0) + _private(p)
    return [(k, v // (1024 * 1024)) for k, v in sorted(by.items(), key=lambda kv: -kv[1])[:n]]


def kill(proc: Proc, timeout: float = 3) -> int:
    """Завершить процесс и все его дочерние; вернуть число завершённых."""
    if psutil is None:
        return 0
    procs = []
    for pid in proc.pids or [proc.pid]:
        try:
            procs.append(psutil.Process(pid))
        except Exception:
            pass
    for p in procs:
        try:
            p.terminate()
        except Exception:
            pass
    _, alive = psutil.wait_procs(procs, timeout=timeout)
    for p in alive:
        try:
            p.kill()
        except Exception:
            pass
    return len(procs)


class ProcessWindow:
    """Окно «Процессы и память»."""

    def __init__(self, root, on_log: Callable[[str], None] = lambda s: None,
                 lister: Callable[[], List[Proc]] = our_processes, killer: Callable[[Proc], int] = kill,
                 ask: Callable[[str], bool] = None):
        self.lister, self.killer, self.on_log = lister, killer, on_log
        self.ask = ask or (lambda text: messagebox.askyesno("Процессы", text, parent=self.win))
        self.procs: List[Proc] = []
        self.win = tk.Toplevel(root)
        self.win.title("Процессы и память")
        self.win.geometry("860x380")
        self.mem_var, self.top_var = tk.StringVar(), tk.StringVar()
        ttk.Label(self.win, textvariable=self.mem_var, font=("Segoe UI", 10, "bold"), padding=(8, 6, 8, 0)).pack(
            anchor="w")
        ttk.Label(self.win, textvariable=self.top_var, foreground="#555", padding=(8, 0, 8, 4)).pack(anchor="w")
        cols = (("pid", "PID", 60), ("what", "Что", 200), ("kind", "Чей", 250), ("mb", "Память", 80),
                ("started", "Запущен", 110))
        self.tree = ttk.Treeview(self.win, columns=[c[0] for c in cols], show="headings", selectmode="extended")
        for key, title, width in cols:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, anchor="w" if key in ("what", "kind") else "center")
        self.tree.tag_configure("extra", background="#ffe0b2")
        self.tree.pack(fill="both", expand=True, padx=8)
        bar = ttk.Frame(self.win, padding=8)
        bar.pack(fill="x")
        ttk.Button(bar, text="Обновить", command=self.refresh).pack(side="left")
        ttk.Button(bar, text="Завершить выбранные", command=self.kill_selected).pack(side="left", padx=6)
        self.extra_btn = ttk.Button(bar, text="Завершить лишние", command=self.kill_extra)
        self.extra_btn.pack(side="left")
        ttk.Label(bar, text="оранжевые - лишние: то, что их запустило, уже закрыто", foreground="#555").pack(
            side="left", padx=10)
        self.refresh()

    def refresh(self):
        m = memory()
        self.mem_var.set(f"Память Windows: свободно {gb(m.commit_free)} из {gb(m.commit_total)} (виртуальная), "
                         f"RAM свободно {gb(m.ram_free)} из {gb(m.ram_total)}"
                         + ("  -  МАЛО" if m.low else ""))
        top = top_consumers()
        self.top_var.set("Больше всего занимают: " + ", ".join(f"{n} {gb(mb)}" for n, mb in top) if top else
                         ("psutil не установлен - список процессов недоступен (setup_env.ps1)" if psutil is None
                          else ""))
        self.procs = self.lister()
        self.tree.delete(*self.tree.get_children())
        for p in self.procs:
            self.tree.insert("", "end", iid=str(p.pid), tags=("extra",) if p.extra else (),
                             values=(p.pid, p.what, p.kind, gb(p.mb),
                                     time.strftime("%d.%m %H:%M", time.localtime(p.started)) if p.started else ""))
        n = sum(p.extra for p in self.procs)
        self.extra_btn.configure(text=f"Завершить лишние ({n})", state="normal" if n else "disabled")

    def _kill(self, procs: List[Proc], confirm: str) -> int:
        if not procs or not self.ask(confirm):
            return 0
        done = 0
        for p in procs:
            done += self.killer(p)
            self.on_log(f"[UI] завершён процесс {p.pid} ({p.what}, {p.kind}, {gb(p.mb)})")
        self.refresh()
        return done

    def kill_selected(self) -> int:
        chosen = {int(i) for i in self.tree.selection()}
        procs = [p for p in self.procs if p.pid in chosen]
        busy = [p for p in procs if not p.extra]
        text = "Завершить выбранные процессы (" + ", ".join(f"{p.what} {gb(p.mb)}" for p in procs) + ")?"
        if busy:
            text += "\n\nСреди них не лишние (" + ", ".join(p.kind for p in busy) + ") - их работа прервётся."
        return self._kill(procs, text)

    def kill_extra(self) -> int:
        procs = [p for p in self.procs if p.extra]
        return self._kill(procs, f"Завершить лишние процессы: {len(procs)} шт., {gb(sum(p.mb for p in procs))}?\n"
                                 f"То, что их запустило (окно программы, PyCharm), уже закрыто.")
