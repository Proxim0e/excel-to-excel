"""
Диагностика скачивания: почему файлы не качаются на этом компьютере.

    python -m src.diag [URL тендера] [--lots N]

Что делает:
  1. Показывает окружение: прокси из переменных среды, версии Python/requests.
  2. Берёт несколько лотов тендера, собирает адреса документов участников.
  3. Для каждого уникального хоста проверяет по шагам: DNS -> TCP -> HTTPS-запрос,
     и пишет, на каком шаге ломается и что это обычно значит.
Ничего не скачивает и не сохраняет: читает только первые байты ответа.
Результат (вывод в консоль) можно целиком прислать для разбора.
"""
from __future__ import annotations

import argparse
import os
import platform
import socket
import sys
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional
from urllib.parse import urlparse

import requests

from src.config import DEFAULT_TENDER_URL, HEADERS
from src.utils.http_client import describe_network_error

PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
              "http_proxy", "https_proxy", "all_proxy", "no_proxy")


@dataclass
class HostReport:
    host: str
    port: int
    urls: List[str] = field(default_factory=list)
    dns: str = ""
    tcp: str = ""
    http: str = ""
    ok: bool = False

    def verdict(self) -> str:
        if self.ok:
            return "OK"
        if self.dns.startswith("FAIL"):
            return "ломается на DNS: имя хоста не разрешается (DNS провайдера, VPN, hosts-файл)"
        if self.tcp.startswith("FAIL"):
            return "ломается на TCP: до хоста нет соединения (блокировка по IP, файрвол, IPv6, нет маршрута)"
        return "ломается на HTTPS-запросе: сеть есть, но запрос обрывается (антивирус с проверкой HTTPS, прокси, фильтрация)"


def environment_info() -> Dict[str, str]:
    info = {"python": sys.version.split()[0], "platform": platform.platform(),
            "requests": requests.__version__}
    for name in PROXY_VARS:
        if os.environ.get(name):
            info[name] = os.environ[name]
    return info


def group_by_host(urls: List[str]) -> Dict[str, HostReport]:
    out: Dict[str, HostReport] = {}
    for url in urls:
        p = urlparse(url)
        if not p.hostname:
            continue
        port = p.port or (443 if p.scheme == "https" else 80)
        key = f"{p.hostname}:{port}"
        rep = out.setdefault(key, HostReport(host=p.hostname, port=port))
        if url not in rep.urls:
            rep.urls.append(url)
    return out


def probe_host(rep: HostReport, *, resolver=socket.getaddrinfo, connector=socket.create_connection,
               getter: Optional[Callable] = None, timeout: float = 10.0) -> HostReport:
    """Три шага: DNS -> TCP -> HTTPS. Каждый шаг пишет результат или причину сбоя."""
    try:
        infos = resolver(rep.host, rep.port, proto=socket.IPPROTO_TCP)
        addrs = sorted({i[4][0] for i in infos})
        rep.dns = "ok: " + ", ".join(addrs[:4])
    except OSError as e:
        rep.dns = f"FAIL: {e}"
        return rep

    started = time.monotonic()
    try:
        conn = connector((rep.host, rep.port), timeout=timeout)
        conn.close()
        rep.tcp = f"ok: {time.monotonic() - started:.2f} c"
    except OSError as e:
        rep.tcp = f"FAIL: {e}"
        return rep

    get = getter or requests.get
    url = rep.urls[0]
    try:
        resp = get(url, headers=HEADERS, timeout=timeout, stream=True)
        try:
            first = next(resp.iter_content(1024), b"")
        finally:
            resp.close()
        rep.http = f"HTTP {resp.status_code}, {resp.headers.get('Content-Type', '?')}, первых байт: {len(first)}"
        rep.ok = resp.status_code == 200
    except requests.exceptions.RequestException as e:
        rep.http = "FAIL: " + describe_network_error(e, url)
    return rep


def collect_doc_urls(tender_url: str, lots: int = 3) -> List[str]:
    from src.scrapers.lot_parser import TenderScraper
    scraper = TenderScraper()
    links = scraper.extract_lot_links(tender_url)
    urls: List[str] = []
    for link in links[:lots]:
        data = scraper.parse_lot_page(link)
        for part in data.participants:
            urls.extend(u for _, u in part.docs)
    return urls


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Диагностика скачивания документов")
    ap.add_argument("tender_url", nargs="?", default=DEFAULT_TENDER_URL)
    ap.add_argument("--lots", type=int, default=3, help="сколько лотов просмотреть")
    args = ap.parse_args(argv)

    print("== Окружение ==")
    for k, v in environment_info().items():
        print(f"  {k}: {v}")

    print(f"\n== Сайт: {args.tender_url} ==")
    try:
        urls = collect_doc_urls(args.tender_url, args.lots)
    except Exception as e:  # диагностика не должна падать с трейсбеком
        print("  Не удалось получить страницы тендера:", describe_network_error(e, args.tender_url))
        return 1
    hosts = group_by_host(urls)
    print(f"  Ссылок на документы: {len(urls)}, уникальных хостов: {len(hosts)}")
    if not hosts:
        print("  Ссылок на документы не найдено: проблема не в сети, а в разборе страницы.")
        return 1

    bad = 0
    for rep in hosts.values():
        probe_host(rep)
        print(f"\n  Хост {rep.host}:{rep.port}  (пример: {rep.urls[0]})")
        print(f"    DNS:   {rep.dns}")
        print(f"    TCP:   {rep.tcp or '-'}")
        print(f"    HTTPS: {rep.http or '-'}")
        print(f"    Итог:  {rep.verdict()}")
        bad += 0 if rep.ok else 1
    print("\nГотово. Пришлите этот вывод целиком." if bad else "\nВсе хосты отвечают.")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
