from urllib.parse import urljoin
from typing import List, Optional, Dict

import requests
from bs4 import BeautifulSoup
from src.utils.text_helpers import clean_company_name, parse_price_to_number, normalize_spaces
import logging
logging.basicConfig(level=logging.DEBUG,
                    format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

from src.config import HEADERS, BASE, LOT_LINK_RE, MAX_WORKERS
from src.models import LotData, Participant
from src.utils.text_helpers import clean_company_name, parse_price_to_number

class TenderScraper:
    """
    Класс для взаимодействия с веб-ресурсом (achizitii.md).
    Управляет сессией, парсингом ссылок и страниц лотов.
    """

    def __init__(self):
        # Инициализируем сессию для переиспользования TCP-соединений
        self.session = requests.Session()
        # Увеличиваем пул соединений, чтобы 16 воркеров не ждали
        adapter = requests.adapters.HTTPAdapter(pool_connections=MAX_WORKERS, pool_maxsize=MAX_WORKERS)
        self.session.mount('https://', adapter)
        self.session.mount('http://', adapter)

        self.session.headers.update(HEADERS)

    def get_soup(self, url: str, timeout: int = 20) -> Optional[BeautifulSoup]:
        """Загружает страницу и возвращает объект BeautifulSoup."""
        try:
            resp = self.session.get(url, timeout=timeout)
            resp.raise_for_status()
            return BeautifulSoup(resp.text, "html.parser")
        except Exception as e:
            print(f"[ERROR] Ошибка загрузки {url}: {e}")
            return None

    def extract_lot_links(self, tender_url: str) -> List[str]:
        """
        Собирает список ссылок на лоты со страницы тендера.
        Возвращает отсортированный список уникальных абсолютных URL.
        """
        soup = self.get_soup(tender_url)
        if not soup:
            return []

        links = set()
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            full = urljoin(BASE, href)
            # Проверяем по регулярке из конфига
            if LOT_LINK_RE.search(href) or LOT_LINK_RE.search(full.replace(BASE, "")):
                links.add(full.split('#')[0].rstrip("/"))

        return sorted(links)


    @staticmethod
    def extract_participant_docs(participant_node):
        """
        Извлекает ВСЕ ссылки на документы из блока участника.
        Возвращает список кортежей: [('имя файла.pdf', 'http://...'), ...]
        """
        docs = []
        # Ищем все строки с документами
        rows = participant_node.select(".popup__tender__docs")

        for row in rows:
            # Берем ссылку (приоритет is-mobile, так как там прямой URL)
            link_node = row.select_one("a.is-mobile") or row.select_one("a")
            if not link_node:
                continue

            # Имя файла из текста ссылки
            filename = link_node.get_text(strip=True)
            # Сама ссылка
            href = link_node.get("href")

            if not href:
                continue

            # Если ссылка относительная (начинается с /), делаем полной
            if href.startswith("/"):
                from urllib.parse import urljoin
                href = urljoin("https://achizitii.md", href)

            docs.append((filename, href))

        return docs

    def parse_lot_page(self, lot_url: str) -> LotData:
        """
        Парсит страницу конкретного лота и возвращает объект LotData.
        """
        logger.debug(f"Начинаю парсинг страницы: {lot_url}")

        soup = self.get_soup(lot_url)
        if not soup:
            logger.error(f"Не удалось загрузить страницу: {lot_url}")
            return LotData(number=0, url=lot_url, error="load_failed")

        # 1. Извлекаем заголовок
        title: Optional[str] = None
        for sel in ("h1", ".tender__page__title h1", ".tender__page__title"):
            el = soup.select_one(sel)
            if el and el.get_text(strip=True):
                title = normalize_spaces(el.get_text(strip=True))
                break

        if not title:
            mt = soup.find("title")
            title = normalize_spaces(mt.get_text(strip=True)) if mt else "(untitled)"

        logger.debug(f"Найден заголовок: {title}")

        # 2. Извлекаем номер лота из title
        lot_number = 0

        if lot_number == 0:
            import re
            m = re.search(r'Lot(?:ul)?\s*nr\.?\s*(\d+)', title, flags=re.I)
            if m:
                lot_number = int(m.group(1))
        logger.debug(f"Определен номер лота: {lot_number}")

        # 3. Извлекаем участников
        participants: List[Participant] = []
        infos = soup.select(".participant-container-body-info")

        if infos:
            logger.debug("Найден основной блок участников (.participant-container-body-info)")
            for info in infos:
                name = ""
                nnode = info.select_one(
                    ".participant-container-body-item.participant-title, .participant-title, .participant-title-div")
                if nnode:
                    name = clean_company_name(nnode.get_text(separator=" ", strip=True))

                price_str = ""
                pnode = info.select_one(".participant-container-body .participant-price, .participant-price")
                if pnode:
                    price_str = pnode.get_text(separator=" ", strip=True)

                # Парсим цену в число
                price_val = parse_price_to_number(price_str)

                # Извлекаем все документы участника
                all_docs = self.extract_participant_docs(info)

                # Дебаг для каждого участника
                logger.debug(
                    f"   Участник: {name} | Цена (стр): {price_str} | Цена (число): {price_val} | Документов: {len(all_docs)}")

                if name or price_str:
                    participants.append(Participant(
                        name=name,
                        price_str=price_str,
                        price_val=price_val,
                        docs=all_docs  # <-- ПЕРЕДАЕМ ВСЕ ССЫЛКИ
                    ))
        else:
            # Fallback логика
            logger.warning("Основной блок участников не найден, включаю Fallback режим...")
            title_nodes = soup.select(".participant-container-body .participant-title, .participant-title")
            price_nodes = soup.select(".participant-container-body .participant-price, .participant-price")
            if title_nodes and price_nodes and len(title_nodes) == len(price_nodes):
                for t, p in zip(title_nodes, price_nodes):
                    name = clean_company_name(t.get_text(strip=True))
                    price_str = p.get_text(strip=True)
                    price_val = parse_price_to_number(price_str)

                    # В Fallback режиме документов может и не быть, но попробуем найти
                    # (Здесь info нет, так что ищем по DOM выше, если нужно, но для простоты оставим docs пустым)
                    participants.append(Participant(name=name, price_str=price_str, price_val=price_val, docs=[]))
            else:
                logger.warning("Не удалось найти участников ни в одном из режимов.")

        logger.debug(f"Всего извлечено участников: {len(participants)}")

        return LotData(
            number=lot_number,
            url=lot_url,
            title=title,
            participants=participants
        )