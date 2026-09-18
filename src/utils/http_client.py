import time
import logging
import requests
from src.config import DOWNLOAD_RETRIES

logger = logging.getLogger(__name__)


def smart_request(request_func, url, retries=DOWNLOAD_RETRIES, timeout=30, action_name="Request", **kwargs):
    """
    Универсальная обертка для запросов.
    Обеспечивает повторные попытки (Retries) и проверку статусов (503 vs 404).
    """
    for attempt in range(retries):
        try:
            logger.debug(f"[{action_name}] Attempt {attempt + 1}: {url}")
            resp = request_func(url, timeout=timeout, **kwargs)

            # --- ПРОВЕРКА СТАТУСОВ ---
            if resp.status_code == 200:
                return resp

            elif resp.status_code in [404, 403]:
                # Фатальные ошибки. НЕ повторяем. Вылетаем сразу.
                logger.warning(f"[{action_name}] ФАТАЛЬНО: Статус {resp.status_code}. Файл недоступен. URL: {url}")
                raise requests.exceptions.HTTPError(f"Client Error {resp.status_code}")

            elif resp.status_code in [503, 502, 500, 504]:
                # Ошибки сервера. ПОВТОРЯЕМ.
                logger.warning(
                    f"[{action_name}] СЕРВЕР ЛЕЖИТ (Статус {resp.status_code}). Попытка {attempt + 1}/{retries}. Ждем...")
                raise Exception(f"Server Error {resp.status_code}")

            else:
                # Неизвестный статус. Вылетаем.
                logger.warning(f"[{action_name}] НЕИЗВЕСТНЫЙ статус {resp.status_code}")
                raise requests.exceptions.HTTPError(f"HTTP {resp.status_code}")

        except Exception as e:
            # Сюда попадают:
            # 1. Ошибки сети (ConnectionError, Timeout)
            # 2. Наши рукотворные Exception("Server Error...")

            # Проверяем, не является ли ошибкой фатальный отказ клиента (404)
            if isinstance(e, requests.exceptions.HTTPError):
                if "Client Error" in str(e) or "404" in str(e) or "403" in str(e):
                    raise e  # Пробрасываем наружу, чтобы воркер перестал повторять

            logger.warning(f"[{action_name}] Exception: {e} (Attempt {attempt + 1})")
            # ЛОГ СЕТЕВОЙ ОШИБКИ ИЛИ ТАЙМАУТА
            logger.warning(f"[{action_name}] Сбой сети/Таймаут: {type(e).__name__}. Попытка {attempt + 1}/{retries}")
            # Если попытки кончились — выходим с ошибкой
            if attempt == retries - 1:
                logger.error(f"[{action_name}] Retries exhausted for {url}")
                raise requests.exceptions.HTTPError(f"Retries exhausted: {url}")

            # Ждем 10 секунды и повторяем
            time.sleep(10)

    return None