"""
Скачивание документов участников.

Модель потоков:
  * план (какой URL в какие файлы) строится в ГЛАВНОМ потоке, до запуска пула;
  * каждый URL - ровно одна задача пула, поэтому два потока никогда не пишут
    в один и тот же файл и не качают один и тот же URL;
  * у каждого потока своя requests.Session (Session не гарантированно thread-safe);
  * общий лимит частоты и остановка при блокировке - в utils.http_client.
"""
from __future__ import annotations

import hashlib
import logging
import re
import shutil
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta
from pathlib import Path

import requests

from src.config import ALLOWED_EXTENSIONS, DOWNLOAD_DIR, HEADERS, MAX_WORKERS
from src.models import LotData
from src.utils.file_helpers import sanitize_filename
from src.utils.http_client import RateLimitStop, is_blocked, smart_request, stop_reason

logger = logging.getLogger(__name__)

Task = tuple[str, Path]  # (url, путь сохранения)

_CHUNK_SIZE = 1 << 16  # 64 KB: файл не держится в памяти целиком
_TENDER_ID_RE = re.compile(r"/tender/(\d+)/")
_local = threading.local()


# ---------------------------------------------------------------------------
# Планирование (главный поток, без сети)
# ---------------------------------------------------------------------------
def tender_id_from_results(results: list[LotData]) -> str:
    for lot in results:
        if m := _TENDER_ID_RE.search(lot.url):
            return m.group(1)
    return "unknown"


def collect_tasks(results: list[LotData], tender_folder: Path) -> list[Task]:
    """Превращает результаты парсинга в список (url, путь) с учётом фильтра расширений."""
    allowed = tuple(ext.lower() for ext in ALLOWED_EXTENSIONS)
    tasks: list[Task] = []
    for lot in results:
        if lot.error or not lot.title or not lot.participants:
            continue
        lot_folder = tender_folder / (sanitize_filename(lot.title).strip() or f"Lot_{lot.number}")
        for part in lot.participants:
            part_folder = lot_folder / (sanitize_filename(part.name).strip() or "Participant")
            for filename, url in part.docs:
                if allowed and not filename.lower().endswith(allowed):
                    continue
                clean = sanitize_filename(filename).strip()
                if clean:
                    tasks.append((url, part_folder / clean))
    return tasks


def _short_hash(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:8]


def plan_downloads(tasks: list[Task]) -> dict[str, list[Path]]:
    """
    Группирует задачи: URL -> список путей, куда должен лечь этот файл.

    Если в один путь претендуют РАЗНЫЕ URL (обрезанные имена совпали),
    ВСЕ такие файлы получают суффикс с хешем URL. Решение не зависит от порядка
    задач, поэтому имена стабильны между запусками и докачка не путает файлы.
    """
    urls_by_path: dict[Path, set[str]] = defaultdict(set)
    for url, path in tasks:
        urls_by_path[path].add(url)

    plan: dict[str, list[Path]] = defaultdict(list)
    for url, path in tasks:
        if len(urls_by_path[path]) > 1:
            path = path.with_stem(f"{path.stem}_{_short_hash(url)}")
        if path not in plan[url]:
            plan[url].append(path)
    return dict(plan)


# ---------------------------------------------------------------------------
# Выполнение (потоки пула)
# ---------------------------------------------------------------------------
def _session() -> requests.Session:
    session = getattr(_local, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update(HEADERS)
        _local.session = session
    return session


def _fetch_to_file(url: str, path: Path) -> None:
    """Потоковая запись во временный .part и атомарное переименование."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    with smart_request(_session().get, url, timeout=60,
                       action_name=f"Download {path.name}", stream=True) as resp:
        with tmp.open("wb") as f:
            for chunk in resp.iter_content(chunk_size=_CHUNK_SIZE):
                f.write(chunk)
    tmp.replace(path)
    logger.info("[DOWNLOADED] %s (%d KB)", path, path.stat().st_size // 1024)


def download_url(url: str, paths: list[Path]) -> str:
    """
    Один URL -> все его пути. Качает из сети максимум один раз, остальное копирует.
    Возвращает: "skipped" | "copied" | "success" | "blocked" | "error".
    """
    missing = [p for p in paths if not p.exists()]
    if not missing:
        return "skipped"
    source = next((p for p in paths if p.exists()), None)

    try:
        if source is None:
            if is_blocked():
                return "blocked"
            source = missing.pop(0)
            _fetch_to_file(url, source)
            outcome = "success"
        else:
            outcome = "copied"

        for p in missing:
            p.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, p)
        return outcome

    except RateLimitStop:
        return "blocked"
    except requests.exceptions.RequestException as e:
        logger.error("[FAIL-HTTP] %s: %s", url, e)
        return "error"
    except OSError as e:
        logger.critical("[FAIL-DISK] %s: %s", source, e)
        return "error"


# ---------------------------------------------------------------------------
# Точка входа этапа
# ---------------------------------------------------------------------------
def run_downloads(results: list[LotData], script_start_time: float) -> Counter:
    tender_folder = DOWNLOAD_DIR / tender_id_from_results(results)
    plan = plan_downloads(collect_tasks(results, tender_folder))
    total_files = sum(len(paths) for paths in plan.values())
    total_jobs = len(plan)

    logger.info("Файлов к сохранению: %d, уникальных ссылок: %d. Папка: %s. Потоков: %d",
                total_files, total_jobs, tender_folder, MAX_WORKERS)

    stats: Counter = Counter()
    start = time.monotonic()
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(download_url, url, paths): url for url, paths in plan.items()}
        for done, future in enumerate(as_completed(futures), start=1):
            try:
                stats[future.result()] += 1
            except Exception:  # неожиданная ошибка в воркере не должна валить весь этап
                logger.exception("[FAIL-UNKNOWN] %s", futures[future])
                stats["error"] += 1
            if done % 10 == 0 or done == total_jobs:
                _log_progress(done, total_jobs, start, script_start_time, stats)

    not_saved = [p for paths in plan.values() for p in paths if not p.exists()]
    logger.info("=== ИТОГ СКАЧИВАНИЯ === Из сети: %d | Скопировано: %d | Уже были: %d | "
                "Ошибок: %d | Не обработано (блокировка): %d",
                stats["success"], stats["copied"], stats["skipped"], stats["error"], stats["blocked"])
    if not_saved:
        logger.warning("Не сохранено файлов: %d из %d. Примеры: %s",
                       len(not_saved), total_files, [str(p) for p in not_saved[:3]])
    else:
        logger.info("Все %d файлов на месте.", total_files)
    if is_blocked():
        logger.error("ОСТАНОВЛЕНО: %s. Запустите позже, скачанное пропустится.", stop_reason())
    return stats


def _log_progress(done: int, total: int, start: float, script_start: float, stats: Counter) -> None:
    elapsed = time.monotonic() - start
    speed = done / elapsed if elapsed > 0 else 0.0
    eta = timedelta(seconds=int((total - done) / speed)) if speed else "расчет..."
    logger.info("--- ПРОГРЕСС: %d/%d (%.1f%%) | Осталось: ~%s | %.2f ссылок/сек | "
                "Общее время: %s | Успешно: %d | Ошибок: %d ---",
                done, total, done / total * 100, eta, speed,
                timedelta(seconds=int(time.time() - script_start)),
                stats["success"] + stats["copied"], stats["error"])
