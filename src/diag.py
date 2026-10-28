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
import logging
import os
import platform
import shutil
import socket
import ssl
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional
from urllib.parse import urlparse

import requests

from src.config import DEFAULT_TENDER_URL, HEADERS
from src.utils.http_client import BROWSER_UA, DOWNLOAD_HEADERS, describe_network_error

PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
              "http_proxy", "https_proxy", "all_proxy", "no_proxy")


@dataclass
class HostReport:
    host: str
    port: int
    urls: List[str] = field(default_factory=list)
    dns: str = ""
    tcp: str = ""
    tls: str = ""
    http: str = ""            # запрос с User-Agent скрейпера
    http_browser: str = ""    # тот же запрос с User-Agent браузера
    curl: str = ""
    ok: bool = False
    ok_browser: bool = False
    ok_curl: Optional[bool] = None

    def verdict(self) -> str:
        if self.ok:
            return "OK"
        if self.dns.startswith("FAIL"):
            return "ломается на DNS: имя хоста не разрешается (DNS провайдера, VPN, hosts-файл)"
        if self.tcp.startswith("FAIL"):
            return "ломается на TCP: до хоста нет соединения (блокировка по IP, файрвол, IPv6, нет маршрута)"
        if self.tls.startswith("FAIL"):
            return ("сбрасывается на TLS-рукопожатии: фильтрация провайдера по имени хоста (SNI), "
                    "антивирус с проверкой HTTPS или блокировка по IP. Проверьте этот адрес в браузере "
                    "и с другого интернета (раздача с телефона)")
        if self.ok_browser:
            return "сервер отвергает User-Agent скрейпера, с браузерным запрос проходит: нужен браузерный User-Agent"
        if self.ok_curl:
            return "curl проходит, а Python нет: сервер отличает TLS-отпечаток Python (нужен другой HTTP-клиент)"
        return ("TLS устанавливается, но запрос обрывается и с браузерным User-Agent: "
                "блокировка на стороне провайдера или сервера для этого IP/сети")


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


def _tls_handshake(host: str, port: int, timeout: float) -> str:
    ctx = ssl.create_default_context()
    with socket.create_connection((host, port), timeout=timeout) as raw:
        with ctx.wrap_socket(raw, server_hostname=host) as tls:
            cert = tls.getpeercert() or {}
            subject = dict(x[0] for x in cert.get("subject", ()))
            return f"{tls.version()}, сертификат: {subject.get('commonName', '?')}"


def _run_curl(url: str, timeout: float = 15.0) -> tuple[bool, str]:
    exe = shutil.which("curl.exe") or shutil.which("curl")
    if not exe:
        return False, "curl не найден"
    try:
        r = subprocess.run([exe, "-sS", "-I", "-m", str(int(timeout)), url],
                           capture_output=True, text=True, timeout=timeout + 5)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"FAIL: {e}"
    first = (r.stdout.splitlines() or [""])[0].strip()
    if r.returncode == 0 and first:
        return True, first
    return False, f"FAIL (код {r.returncode}): {(r.stderr or first).strip()[:200]}"


def _try_get(get, url: str, headers: dict, timeout: float) -> tuple[bool, str]:
    try:
        resp = get(url, headers=headers, timeout=timeout, stream=True)
        try:
            first = next(resp.iter_content(1024), b"")
        finally:
            resp.close()
        info = f"HTTP {resp.status_code}, {resp.headers.get('Content-Type', '?')}, первых байт: {len(first)}"
        return resp.status_code == 200, info
    except requests.exceptions.RequestException as e:
        return False, "FAIL: " + describe_network_error(e, url)


def probe_host(rep: HostReport, *, resolver=socket.getaddrinfo, connector=socket.create_connection,
               getter: Optional[Callable] = None, tls_checker: Optional[Callable] = None,
               curl_runner: Optional[Callable] = None, timeout: float = 10.0) -> HostReport:
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

    tls_check = tls_checker or _tls_handshake
    try:
        rep.tls = "ok: " + tls_check(rep.host, rep.port, timeout)
    except (OSError, ssl.SSLError) as e:
        rep.tls = f"FAIL: {type(e).__name__}: {e}"
        return rep

    get = getter or requests.get
    url = rep.urls[0]
    rep.ok, rep.http = _try_get(get, url, HEADERS, timeout)
    if not rep.ok:
        rep.ok_browser, rep.http_browser = _try_get(get, url, DOWNLOAD_HEADERS, timeout)
        ok_curl, rep.curl = (curl_runner or _run_curl)(url)
        rep.ok_curl = ok_curl
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
    logging.disable(logging.DEBUG)  # без шума от requests и скрапера

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
        print(f"    TLS:   {rep.tls or '-'}")
        print(f"    HTTPS (User-Agent скрейпера): {rep.http or '-'}")
        if rep.http_browser:
            print(f"    HTTPS (User-Agent браузера):  {rep.http_browser}")
        if rep.curl:
            print(f"    curl:  {rep.curl}")
        print(f"    Итог:  {rep.verdict()}")
        bad += 0 if rep.ok else 1
    print("\nГотово. Пришлите этот вывод целиком." if bad else "\nВсе хосты отвечают.")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
