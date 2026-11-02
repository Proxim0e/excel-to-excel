"""
Проверка среды: установлены ли библиотеки ровно тех версий, что в requirements.txt, и браузер для AMDM.

    python -m src.envcheck

main.py вызывает её в начале и пишет предупреждения в лог, но не останавливается.
Зачем: на рабочем ПК PyMuPDF поставили после первого запуска и другой версии - классификация
молча прошла «по именам файлов», и Anexa 22 не нашлась ни в одном документе.
Починить: setup_env.ps1 (или pip install -r requirements-dev.txt и playwright install chromium).
"""
import logging
import re
import sys
from importlib import metadata
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS = ROOT / "requirements.txt"
PYTHON_VERSION = ROOT / ".python-version"


def _pinned(path: Path):
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*([A-Za-z0-9_.\-]+)==([^\s#;]+)", line)
        if m:
            yield m.group(1), m.group(2)


def _chromium_installed() -> bool:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    try:
        with sync_playwright() as pw:
            return Path(pw.chromium.executable_path).exists()
    except Exception:
        return False


def problems() -> List[str]:
    """Расхождения среды с requirements.txt и .python-version; пустой список - всё совпадает."""
    found = []
    if PYTHON_VERSION.exists():
        want = PYTHON_VERSION.read_text(encoding="utf-8").strip()
        have = "%d.%d" % sys.version_info[:2]
        if have != want:
            found.append(f"Python {have}, нужен {want}")
    for name, version in _pinned(REQUIREMENTS):
        try:
            installed = metadata.version(name)
        except metadata.PackageNotFoundError:
            found.append(f"{name}: не установлен (нужен {version})")
            continue
        if installed != version:
            found.append(f"{name}: {installed}, нужен {version}")
    if not any(p.startswith("playwright") for p in found) and not _chromium_installed():
        found.append("Chromium для playwright не установлен (проверка AMDM): playwright install chromium")
    return found


def log_problems() -> bool:
    """Пишет расхождения в лог. True - среда совпадает."""
    found = problems()
    for p in found:
        logger.warning(f"[ENV] {p}")
    if found:
        logger.warning("[ENV] Среда отличается от requirements.txt - запустите setup_env.ps1")
    return not found


def main() -> int:
    found = problems()
    if not found:
        print("Среда совпадает с requirements.txt")
        return 0
    print("Расхождения со средой проекта:")
    for p in found:
        print(f"  - {p}")
    print("Починить: powershell -ExecutionPolicy Bypass -File setup_env.ps1")
    return 1


if __name__ == "__main__":
    sys.exit(main())
