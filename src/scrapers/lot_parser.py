from urllib.parse import urljoin
from typing import List, Optional, Dict
from src.utils.http_client import smart_request
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
        Парсит страницу лота. Использует smart_request для ретраев.
        """
        try:
            # --- ВЫЗОВ УМНОГО ЗАПРОСА ---
            response = smart_request(
                self.session.get,
                lot_url,
                timeout=30,
                action_name="Scrape"
            )

            # Если успех — парсим HTML
            soup = BeautifulSoup(response.text, "html.parser")

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

                    price_val = parse_price_to_number(price_str)
                    all_docs = self.extract_participant_docs(info)

                    if name or price_str:
                        participants.append(Participant(
                            name=name,
                            price_str=price_str,
                            price_val=price_val,
                            docs=all_docs
                        ))

            return LotData(
                number=lot_number,
                url=lot_url,
                title=title,
                participants=participants
            )

        except requests.exceptions.HTTPError as e:
            # smart_request выдал эту ошибку (404 или ретраи кончились)
            logger.warning(f"Ошибка запроса для {lot_url}: {e}")
            return LotData(number=0, url=lot_url, error=str(e))

        except Exception as e:
            # Любые другие ошибки парсинга
            logger.error(f"Критическая ошибка парсинга {lot_url}: {e}")
            return LotData(number=0, url=lot_url, error=str(e))