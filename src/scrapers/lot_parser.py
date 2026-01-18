from urllib.parse import urljoin
from typing import List, Optional, Dict

import requests
from bs4 import BeautifulSoup

from ..config import HEADERS, BASE, LOT_LINK_RE
from ..models import LotData, Participant
from ..utils.text_helpers import clean_company_name, parse_price_to_number


class TenderScraper:
    """
    Класс для взаимодействия с веб-ресурсом (achizitii.md).
    Управляет сессией, парсингом ссылок и страниц лотов.
    """

    def __init__(self):
        # Инициализируем сессию для переиспользования TCP-соединений
        self.session = requests.Session()
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

    def parse_lot_page(self, lot_url: str) -> LotData:
        """
        Парсит страницу конкретного лота и возвращает объект LotData.
        """
        soup = self.get_soup(lot_url)
        if not soup:
            return LotData(number=0, url=lot_url, error="load_failed")

        # 1. Извлекаем заголовок
        title: Optional[str] = None
        for sel in ("h1", ".tender__page__title h1", ".tender__page__title"):
            el = soup.select_one(sel)
            if el and el.get_text(strip=True):
                title = el.get_text(strip=True)
                break
        if not title:
            mt = soup.find("title")
            title = mt.get_text(strip=True) if mt else "(untitled)"

        # 2. Извлекаем номер лота из URL (например, /tender/21463176/lot/1)
        # Простой парсинг через split, так как структура URL известна
        lot_number = 0
        parts = lot_url.rstrip('/').split('/')
        if len(parts) > 1 and parts[-1].isdigit():
            lot_number = int(parts[-1])
        # Fallback: пытаемся достать из title, если в URL не было
        if lot_number == 0:
            import re
            m = re.search(r'Lot(?:ul)?\s*nr\.?\s*(\d+)', title, flags=re.I)
            if m:
                lot_number = int(m.group(1))

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

                # Парсим цену в число
                price_val = parse_price_to_number(price_str)

                if name or price_str:
                    participants.append(Participant(name=name, price_str=price_str, price_val=price_val))
        else:
            # Fallback логика из legacy, если структура изменилась
            title_nodes = soup.select(".participant-container-body .participant-title, .participant-title")
            price_nodes = soup.select(".participant-container-body .participant-price, .participant-price")
            if title_nodes and price_nodes and len(title_nodes) == len(price_nodes):
                for t, p in zip(title_nodes, price_nodes):
                    name = clean_company_name(t.get_text(strip=True))
                    price_str = p.get_text(strip=True)
                    price_val = parse_price_to_number(price_str)
                    participants.append(Participant(name=name, price_str=price_str, price_val=price_val))

        return LotData(
            number=lot_number,
            url=lot_url,
            title=title,
            participants=participants
        )