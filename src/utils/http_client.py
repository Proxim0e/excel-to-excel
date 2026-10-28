"""
Вежливый HTTP-клиент. Заменяет прежний src/utils/http_client.py.

Имя и сигнатура smart_request сохранены, поэтому остальной код
(скрапер, download_worker) продолжает работать без переписывания вызовов.

Что делает:
  * лимит частоты, общий для потоков одного вида работы (Pacer): сканирование лотов
    и скачивание файлов ограничиваются раздельно (SCRAPE_PACER / DOWNLOAD_PACER);
  * на 429 останавливает все потоки сразу;
  * на серии сбоев подряд (403 / 503 / ошибки сети) останавливает все потоки;
  * 404 не повторяет; 5xx повторяет только несколько раз с нарастающей паузой;
  * жёсткий потолок попыток, даже если в конфиге стоит DOWNLOAD_RETRIES = 300.
"""
import time
import random
import logging
import threading

from urllib.parse import urlparse

import requests

from src.config import DOWNLOAD_RETRIES, SCRAPE_MIN_DELAY, DOWNLOAD_MIN_DELAY

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Настройки "вежливого" режима
# ---------------------------------------------------------------------------
# Хранилище файлов storage.mtender.gov.md сбрасывает соединение (10054) на запросы со
# «скриптовым» User-Agent (проверено 28.10.2026: curl и requests режутся, браузерный UA проходит).
# Поэтому для СКАЧИВАНИЯ файлов используется браузерный заголовок. Вежливые лимиты частоты остаются.
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
DOWNLOAD_HEADERS = {
    "User-Agent": BROWSER_UA,
    "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.8",
    "Accept-Language": "ro-RO,ro;q=0.9,en;q=0.8",
}

JITTER = 0.6                   # случайная добавка к паузе скачивания, сек
MAX_RETRIES_CAP = 5            # потолок попыток на один запрос
BACKOFF_BASE = 15              # пауза при сбоях: 15, 30, 45... сек
MAX_CONSECUTIVE_TROUBLE = 4    # столько сбоев подряд (403/503/сеть) = останавливаем всё

# ---------------------------------------------------------------------------
# Внутреннее состояние
# ---------------------------------------------------------------------------
_state_lock = threading.Lock()
_stop_event = threading.Event()
_stop_reason = ""
_trouble = 0


class RateLimitStop(Exception):
    """Сайт ограничил доступ (или идут сплошные сбои): все потоки должны остановиться."""


def make_download_session() -> requests.Session:
    """Сессия для скачивания файлов: общие HEADERS проекта + браузерные заголовки."""
    from src.config import HEADERS
    session = requests.Session()
    session.headers.update(HEADERS)
    session.headers.update(DOWNLOAD_HEADERS)
    return session


def describe_network_error(exc: BaseException, url: str = "") -> str:
    """
    Короткое объяснение сетевого сбоя: хост + причина по тексту исключения.
    Нужно, чтобы в логе было видно, ЧТО именно сломалось (DNS, отказ, SSL, таймаут, прокси),
    а не только имя класса исключения.
    """
    host = urlparse(url).netloc or "?"
    text = str(exc)
    low = text.lower()
    if isinstance(exc, requests.exceptions.SSLError) or "ssl" in low or "certificate" in low:
        cause = "ошибка SSL/сертификата (антивирус с проверкой HTTPS, неверные дата/время, прокси)"
    elif "proxy" in low:
        cause = "проблема с прокси"
    elif any(m in low for m in ("getaddrinfo", "name or service not known", "nodename nor servname",
                                "temporary failure in name resolution", "11001", "11004")):
        cause = "не разрешается имя хоста (DNS)"
    elif any(m in low for m in ("refused", "10061", "errno 111")):
        cause = "соединение отклонено хостом (порт закрыт или блокировка)"
    elif isinstance(exc, requests.exceptions.Timeout) or "timed out" in low or "10060" in low:
        cause = "таймаут соединения (блокировка по IP, файрвол, нет маршрута)"
    elif any(m in low for m in ("reset", "10054", "aborted", "10053", "remote end closed", "connection broken")):
        cause = "соединение сброшено при установке (фильтрация провайдером, антивирус, VPN)"
    else:
        cause = "сетевой сбой"
    tail = text.replace("\n", " ")[:200]
    return f"{type(exc).__name__}: {cause}; хост {host}; {tail}"


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
    advice = ("Проверьте сеть, DNS, VPN и антивирус; запустите `python -m src.diag`."
              if _is_network_reason(reason)
              else "Подождите несколько часов и запустите снова.")
    logger.error("=== ОСТАНОВКА: %s. Все потоки прекращают запросы. %s ===", reason, advice)


def _is_network_reason(reason: str) -> bool:
    """Остановка из-за сети (а не из-за ограничений сайта 403/429/503)."""
    return not any(m in reason for m in ("HTTP 403", "HTTP 429", "HTTP 503"))


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


class Pacer:
    """
    Лимит частоты для группы потоков: не чаще одного запроса раз в min_delay
    секунд (плюс случайная добавка до jitter). min_delay = 0 - без ограничения.
    """

    def __init__(self, min_delay: float, jitter: float = 0.0):
        self.min_delay = min_delay
        self.jitter = jitter
        self._lock = threading.Lock()
        self._next_slot = 0.0

    def wait(self) -> None:
        """
        Резервирует следующий временной слот. Потоки выстраиваются в очередь,
        так что два потока НЕ бьют по серверу одновременно.
        """
        if self.min_delay <= 0 and self.jitter <= 0:
            return
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next_slot)
            self._next_slot = slot + self.min_delay + random.uniform(0, self.jitter)
        delay = slot - now
        if delay > 0:
            time.sleep(delay)


SCRAPE_PACER = Pacer(SCRAPE_MIN_DELAY)
DOWNLOAD_PACER = Pacer(DOWNLOAD_MIN_DELAY, JITTER)


def smart_request(request_func, url, retries=DOWNLOAD_RETRIES, timeout=30,
                  action_name="Request", pacer=None, **kwargs):
    """
    Универсальная обёртка для запросов (сигнатура прежняя).
    pacer - чей лимит частоты соблюдать; по умолчанию осторожный DOWNLOAD_PACER.

    Бросает:
      RateLimitStop                      - сайт ограничил доступ, надо остановиться;
      requests.exceptions.HTTPError      - проблема с конкретным файлом (404, 403, исчерпаны попытки).
    """
    retries = max(1, min(retries, MAX_RETRIES_CAP))
    pacer = pacer or DOWNLOAD_PACER

    for attempt in range(1, retries + 1):
        _raise_if_stopped()
        pacer.wait()
        _raise_if_stopped()

        logger.debug(f"[{action_name}] Attempt {attempt}: {url}")
        try:
            resp = request_func(url, timeout=timeout, **kwargs)
        except requests.exceptions.RequestException as e:
            details = describe_network_error(e, url)
            logger.warning(f"[{action_name}] Сбой сети: {details}. Попытка {attempt}/{retries}")
            _note_trouble(details)
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