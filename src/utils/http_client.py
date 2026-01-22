import time
import logging
import requests

# Берем настройки из конфига
from src.config import DOWNLOAD_RETRIES

logger = logging.getLogger(__name__)


def smart_request(request_func, url, retries=DOWNLOAD_RETRIES, timeout=90, action_name="Request", **kwargs):
    """
    Универсальная обертка с поддержкой **kwargs и исправленной обработкой ошибок.
    """
    for attempt in range(retries):
        try:
            logger.debug(f"[{action_name}] Attempt {attempt + 1}: {url}")
            # Передаем все параметры (headers, stream и т.д.) через **kwargs
            resp = request_func(url, timeout=timeout, **kwargs)

            # --- Обработка статусов ---
            if resp.status_code == 200:
                return resp

            elif resp.status_code in [404, 403]:
                # Фатальные ошибки клиента.
                logger.error(f"[{action_name}] FATAL: {resp.status_code} for {url}")
                raise requests.exceptions.HTTPError(f"Client Error {resp.status_code}: {url}")

            elif resp.status_code in [503, 502, 500, 504]:
                # Ошибки сервера. Повторяем.
                logger.warning(f"[{action_name}] Server Error {resp.status_code} === (Attempt {attempt + 1} ===)")
                raise Exception(f"Server Error: {resp.status_code}")

            else:
                # Неизвестный статус
                logger.warning(f"[{action_name}] Unknown Status {resp.status_code} for {url}")
                raise requests.exceptions.HTTPError(f"HTTP {resp.status_code}")


        except requests.exceptions.RequestException as e:
            # Ловим все сетевые ошибки requests (Connection, Timeout, HTTPError)
            # Но так как внутри smart_request мы сами создаем Exception('Server Error'),
            # нам нужно ловить и их.
            logger.warning(f"[{action_name}] Request Exception: {e} (Attempt {attempt + 1})")

            if attempt == retries - 1:
                raise requests.exceptions.HTTPError(f"Retries exhausted: {url}")

            # Проверяем, не ошибка ли это клиента (404/403), которые были прокинуты снаружи?
            # В нашей логике мы выкидываем HTTPError сразу для 404, так что сюда они не попадут.
            time.sleep(1)

        except Exception as e:
            # Сюда попадают наши рукотворные Exception('Server Error: 503')
            logger.warning(f"[{action_name}] Exception: {e} (Attempt {attempt + 1})")
            if attempt == retries - 1:
                raise requests.exceptions.HTTPError(f"Retries exhausted: {url}")

            time.sleep(1)

    return None