"""
Вежливый HTTP-клиент. Заменяет прежний src/utils/http_client.py.

Имя и сигнатура smart_request сохранены, поэтому остальной код
(скрапер, download_worker) продолжает работать без переписывания вызовов.

Что делает:
  * общий для ВСЕХ потоков лимит частоты: не чаще одного запроса раз в
    MIN_DELAY секунд (плюс случайная добавка JITTER);
  * на 429 останавливает все потоки сразу;
  * на серии сбоев подряд (403 / 503 / ошибки сети) останавливает все потоки;
  * 404 не повторяет; 5xx повторяет только несколько раз с нарастающей паузой;
  * жёсткий потолок попыток, даже если в конфиге стоит DOWNLOAD_RETRIES = 300.
"""
import time
import random
import logging
import threading

import requests

from src.config import DOWNLOAD_RETRIES

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Настройки "вежливого" режима
# ---------------------------------------------------------------------------
MIN_DELAY = 1.5                # минимум секунд между ЛЮБЫМИ двумя запросами (общий для потоков)
JITTER = 0.6                   # случайная добавка к паузе, сек
MAX_RETRIES_CAP = 5            # потолок попыток на один запрос
BACKOFF_BASE = 15              # пауза при сбоях: 15, 30, 45... сек
MAX_CONSECUTIVE_TROUBLE = 4    # столько сбоев подряд (403/503/сеть) = останавливаем всё

# ---------------------------------------------------------------------------
# Внутреннее состояние
# ---------------------------------------------------------------------------
_rate_lock = threading.Lock()
_next_slot = 0.0

_state_lock = threading.Lock()
_stop_event = threading.Event()
_stop_reason = ""
_trouble = 0


class RateLimitStop(Exception):
    """Сайт ограничил доступ (или идут сплошные сбои): все потоки должны остановиться."""


def is_blocked() -> bool:
    return _stop_event.is_set()


def stop_reason() -> str:
    return _stop_reason


def _trigger_stop(reason: str) -> None:
    global _stop_reason
    with _state_lock:
        if _stop_event.is_set():
            return
        _stop_reason = reason
        _stop_event.set()
    logger.error(
        "=== ОСТАНОВКА: %s. Все потоки прекращают запросы. "
        "Подождите несколько часов и запустите снова. ===", reason
    )


def _raise_if_stopped() -> None:
    if _stop_event.is_set():
        raise RateLimitStop(_stop_reason)


def _note_ok() -> None:
    global _trouble
    with _state_lock:
        _trouble = 0


def _note_trouble(reason: str) -> None:
    """Фиксирует сбой. Слишком много подряд - останавливает все потоки."""
    global _trouble
    with _state_lock:
        _trouble += 1
        count = _trouble
    if count >= MAX_CONSECUTIVE_TROUBLE:
        _trigger_stop(f"{count} сбоев подряд, последний: {reason}")
        raise RateLimitStop(_stop_reason)


def _wait_turn() -> None:
    """
    Резервирует следующий временной слот. Потоки выстраиваются в очередь,
    так что два потока НЕ бьют по серверу одновременно.
    """
    global _next_slot
    with _rate_lock:
        now = time.monotonic()
        slot = max(now, _next_slot)
        _next_slot = slot + MIN_DELAY + random.uniform(0, JITTER)
    delay = slot - now
    if delay > 0:
        time.sleep(delay)


def smart_request(request_func, url, retries=DOWNLOAD_RETRIES, timeout=30,
                  action_name="Request", **kwargs):
    """
    Универсальная обёртка для запросов (сигнатура прежняя).

    Бросает:
      RateLimitStop                      - сайт ограничил доступ, надо остановиться;
      requests.exceptions.HTTPError      - проблема с конкретным файлом (404, 403, исчерпаны попытки).
    """
    retries = max(1, min(retries, MAX_RETRIES_CAP))

    for attempt in range(1, retries + 1):
        _raise_if_stopped()
        _wait_turn()
        _raise_if_stopped()

        logger.debug(f"[{action_name}] Attempt {attempt}: {url}")
        try:
            resp = request_func(url, timeout=timeout, **kwargs)
        except requests.exceptions.RequestException as e:
            logger.warning(f"[{action_name}] Сбой сети: {type(e).__name__}. Попытка {attempt}/{retries}")
            _note_trouble(type(e).__name__)
            if attempt == retries:
                raise requests.exceptions.HTTPError(f"Retries exhausted: {url}") from e
            time.sleep(BACKOFF_BASE * attempt)
            continue

        code = resp.status_code

        if code == 200:
            _note_ok()
            return resp

        if code == 429:
            retry_after = resp.headers.get("Retry-After", "не указан")
            _trigger_stop(f"HTTP 429 (Retry-After: {retry_after}), URL: {url}")
            raise RateLimitStop(_stop_reason)

        if code == 404:
            _note_ok()  # сервер отвечает нормально, файла просто нет
            logger.warning(f"[{action_name}] 404, файл недоступен: {url}")
            raise requests.exceptions.HTTPError(f"Client Error 404: {url}")

        if code == 403:
            logger.warning(f"[{action_name}] 403: {url}")
            _note_trouble("HTTP 403")
            raise requests.exceptions.HTTPError(f"Client Error 403: {url}")

        if code in (500, 502, 503, 504):
            logger.warning(f"[{action_name}] Ошибка сервера {code}. Попытка {attempt}/{retries}")
            if code == 503:
                _note_trouble("HTTP 503")
            if attempt == retries:
                raise requests.exceptions.HTTPError(f"Server Error {code}, retries exhausted: {url}")
            time.sleep(BACKOFF_BASE * attempt)
            continue

        logger.warning(f"[{action_name}] Неожиданный статус {code}: {url}")
        raise requests.exceptions.HTTPError(f"HTTP {code}: {url}")

    raise requests.exceptions.HTTPError(f"Retries exhausted: {url}")