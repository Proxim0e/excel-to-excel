#!/usr/bin/env python3
"""
Скрипт для:
- извлечения ссылок на все лоты с главной страницы тендера;
- перехода по каждой ссылке лота, парсинга названия лота и списка участников;
- вывода в консоль для каждого лота: название + нумерованный список участников в формате:
    1. Имя_участника_1 сумма_участника_1
    2. Имя_участника_2 сумма_участника_2
и т.д.

Использование:
    python scrape_lots_and_participants.py

Автор: Proxim0e (адаптация)
Дата: 2025-11-19
"""
import re
import sys
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

BASE = "https://achizitii.md"
# Пример: https://achizitii.md/ro/public/tender/21463195/
TENDER_URL = "https://achizitii.md/ro/public/tender/21463195/"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; scraper/1.0; +https://example.com/bot)"
}

# Регекс для ссылок на лоты (относительных или абсолютных)
LOT_LINK_RE = re.compile(r'/ro/public/tender/\d+/lot/\d+/?$')


def get_soup(url):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"[ERROR] Ошибка загрузки {url}: {e}")
        return None
    return BeautifulSoup(resp.text, "html.parser")


def extract_lot_links(tender_url):
    """
    Возвращает список уникальных полных URL-ов лотов, найденных на странице тендера.
    """
    soup = get_soup(tender_url)
    if not soup:
        return []

    links = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        # Нормализуем относительные и абсолютные ссылки
        full = urljoin(BASE, href)
        # Проверяем на соответствие паттерну /ro/public/tender/<tender_id>/lot/<lot_id>/
        # Мы допускаем как абсолютные так и относительные пути
        if LOT_LINK_RE.search(href) or LOT_LINK_RE.search(full.replace(BASE, "")):
            # нормализуем в абсолютный
            links.add(full.split('#')[0].rstrip("/"))  # убираем якори и завершающий слэш
    links_list = sorted(links)
    print(f"[INFO] Найдено {len(links_list)} ссылок на лоты.")
    return links_list


def parse_lot_page(lot_url):
    """
    Возвращает (lot_title, participants_list)
    participants_list - список кортежей (name, price_str)
    """
    soup = get_soup(lot_url)
    if not soup:
        return None, []

    # 1) Получаем название лота: обычно в <h1> или внутри .tender__page__title h1
    title = None
    # Популярные селекторы в текущем HTML
    sel_candidates = [
        "h1",
        ".tender__page__title h1",
        ".tender__page__title",
        ".tender__item__list__item b",  # резервный
    ]
    for sel in sel_candidates:
        el = soup.select_one(sel)
        if el and el.get_text(strip=True):
            title = el.get_text(strip=True)
            break
    if not title:
        # fallback: meta title
        mt = soup.find("title")
        title = mt.get_text(strip=True) if mt else "(untitled)"

    # 2) Ищем участников: структура в примере:
    #    <div class="participant-container-body">
    #      <div class="participant-container-body-info">
    #        <div class="participant-container-body-item participant-number">...</div>
    #        <div class="participant-container-body-item participant-title"> <p>NAME</p> </div>
    #        <div class="participant-container-body-item participant-price"> <b> 900 000 MDL</b> </div>
    #      </div>
    #    </div>
    participants = []

    # Найдём все блоки с participant-container-body-info (по одному на участника)
    infos = soup.select(".participant-container-body-info")
    if not infos:
        # Альтернативно: ищем более общие контейнеры с participant-container-body и пробуем сгруппировать
        # Найдём .participant-container-body и внутри него все прямые дочерние .participant-container-body-item группы
        containers = soup.select(".participant-container-body")
        for cont in containers:
            # внутри cont могут быть несколько подряд идущих групп participant-container-body-item,
            # но чаще есть wrapper .participant-container-body-info, поэтому если его нет, попытаемся собрать группы по каждой группе из .participant-container-body-item-wrapper
            infos = cont.select(".participant-container-body-info")
            if infos:
                break
    # Если всё ещё пусто — попробуем найти участника по наличию классов participant-title внутри страницы
    if not infos:
        title_nodes = soup.select(".participant-container-body .participant-title, .participant-title")
        price_nodes = soup.select(".participant-container-body .participant-price, .participant-price")
        # Пытаемся параллельно сопоставить
        if title_nodes and price_nodes and len(title_nodes) == len(price_nodes):
            for t, p in zip(title_nodes, price_nodes):
                name = t.get_text(separator=" ", strip=True)
                price = p.get_text(separator=" ", strip=True)
                participants.append((name, price))
    else:
        for info in infos:
            # title
            name = ""
            price = ""
            tnode = info.select_one(".participant-container-body-item.participant-title, .participant-title, .participant-container-body-item.participant-title .participant-title-div, .participant-title-div")
            if tnode:
                # имя может быть внутри <p> или просто текст
                name = tnode.get_text(separator=" ", strip=True)
            else:
                # иногда внутри participant-title есть <p> или <div><p>...
                alt = info.find(class_=re.compile(r"participant.*title"))
                if alt:
                    name = alt.get_text(separator=" ", strip=True)

            pnode = info.select_one(".participant-container-body-item.participant-price, .participant-price")
            if pnode:
                price = pnode.get_text(separator=" ", strip=True)
            else:
                altp = info.find(class_=re.compile(r"participant.*price"))
                if altp:
                    price = altp.get_text(separator=" ", strip=True)

            # Если имя пустое, попытка получить из блока participant-title внутри общего контекста
            if not name:
                fallback = info.select_one(".participant-title")
                if fallback:
                    name = fallback.get_text(separator=" ", strip=True)

            # чистка
            name = name.strip()
            price = price.strip()

            if name or price:
                participants.append((name if name else "(без имени)", price if price else "(без суммы)"))

    return title, participants


def main():
    print(f"Скрапинг данных с URL: {TENDER_URL}")
    lot_links = extract_lot_links(TENDER_URL)
    if not lot_links:
        print("[WARN] Не найдено ссылок на лоты. Проверьте страницу или селекторы.")
        return

    for lot_url in lot_links:
        print("\n" + "=" * 80)
        print(f"Обрабатываю лот: {lot_url}")
        title, participants = parse_lot_page(lot_url)
        print(f"Название лота: {title}")
        if not participants:
            print("Участников не найдено.")
            continue

        # Выводим в формате: 1. Имя сумма
        for i, (name, price) in enumerate(participants, start=1):
            # Нормализация стоимости: убираем лишние пробелы и переносы строк
            price_norm = " ".join(price.split())
            name_norm = " ".join(name.split())
            print(f"{i}. {name_norm} {price_norm}")
        print(f"Всего участников: {len(participants)}")

    print("\nГотово.")


if __name__ == "__main__":
    main()