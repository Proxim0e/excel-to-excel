"""
Классификация уникальных документов тендера (этап 1, docs/ROADMAP.md).

Каждый уникальный по содержимому файл (documents.sha256 в манифесте) читается один раз:
текст всех страниц сохраняется в document_pages, тип определяется правилами по ключевым словам.

Порядок проверки:
  1. заголовок первой страницы (первые TITLE_CHARS символов) - самый надёжный сигнал;
  2. текст первых двух страниц;
  3. имя файла;
иначе - "other". Правила внутри каждого прохода идут от более специфичных к общим.

Проверка человеком («спросить один раз»): --export выгружает classification_review.xlsx,
в колонке «Правильный тип» исправляются ошибки, --apply переносит их в манифест
(type_source = 'human'); такие решения правила больше не перезаписывают.

Запуск:  python -m src.classify downloads/21641880 [--force] [--export] [--apply [review.xlsx]]
"""
import argparse
import logging
import re
import sqlite3
import sys
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from src.manifest import MANIFEST_NAME, migrate

logger = logging.getLogger(__name__)

TITLE_CHARS = 600   # «шапка» первой страницы
HEAD_PAGES = 2      # сколько страниц смотрим для классификации

# Тип -> (описание для человека, шаблоны по нормализованному тексту)
# Нормализация: нижний регистр, без диакритики, пробелы схлопнуты.
DOC_TYPES = {
    # Заголовки колонок таблиц Anexa - в STRUCTURAL ниже
    "anexa22": ("Anexa 22 - specificatii tehnice", [
        r"specificatii? tehnice", r"specificatia tehnica", r"anexa (nr\.? ?)?22\b"]),
    "anexa23": ("Anexa 23 - specificatii de pret", [
        r"specificatii? de pret", r"specificatia de pret", r"anexa (nr\.? ?)?23\b"]),
    "duae": ("DUAE - documentul unic de achizitii european", [
        r"documentul unic de achizitii", r"\bduae\b", r"european single procurement"]),
    "cerere_participare": ("Cerere de participare (anexa 7)", [
        r"cerere(a)? de participare"]),
    "declaratie_valabilitate": ("Declaratie privind valabilitatea ofertei (anexa 8)", [
        r"valabilitatea ofertei"]),
    "garantie_oferta": ("Garantia pentru oferta / scrisoare bancara / ordin de plata", [
        r"garantia? (pentru|de) oferta", r"garantie bancara", r"scrisoare de garantie",
        r"ordin de plata", r"payment order", r"garantia bancara"]),
    "certificat_restante": ("Certificat privind lipsa restantelor (SFS)", [
        r"lipsa (sau (prezenta|existenta) )?restantelor", r"certificat(ul)? fiscal"]),
    "extras_registru": ("Extras din Registrul de stat al persoanelor juridice", [
        r"extras din registrul de stat", r"registrul de stat al persoanelor juridice"]),
    "certificat_inregistrare_firma": ("Certificat de inregistrare a intreprinderii", [
        r"certificat(ul)? de inregistrare a (intreprinderii|intreprinderi|persoanei juridice)",
        r"certificat(ul)? de inregistrare de stat"]),
    "cont_bancar": ("Certificat de atribuire a contului bancar", [
        r"atribuire(a)? (a )?contului", r"contul(ui)? bancar", r"conturi(lor)? bancare",
        r"existenta conturilor", r"conturi(lor)? curente", r"cont curent", r"date bancare"]),
    "mediu_eee": ("Confirmare inscriere in lista producatorilor EEE (Agentia de Mediu)", [
        r"lista producatorilor", r"agentia de mediu", r"\beee\b"]),
    "lista_fondatori": ("Lista fondatorilor / beneficiari efectivi", [
        r"lista fondatorilor", r"fondatorii"]),
    "raport_financiar": ("Situatii financiare / raport financiar", [
        r"situatii(le)? financiare", r"raport(ul)? financiar", r"\bbilant(ul)?\b"]),
    "ce_doc": ("Declaratie de conformitate / certificat CE", [
        r"declaration of conformity", r"declaratie de conformitate", r"declaratia de conformitate",
        r"eu declaration", r"ec certificate", r"ce certificate", r"certificat ce\b",
        r"regulation \(eu\) 2017/745", r"93/42/eec"]),
    "amdm": ("Inregistrare AMDM a dispozitivului medical", [
        r"agentia medicamentului", r"registrul de stat al dispozitivelor medicale",
        r"certificat de inregistrare a dispozitivului", r"\bdm0\d{6,}"]),
    "autorizatie_producator": ("Autorizatie / scrisoare a producatorului", [
        r"letter of authori[sz]ation", r"authori[sz]ation letter", r"manufacturer'?s? authori[sz]ation",
        r"scrisoare de autorizare", r"autorizatie(i)? (de la|a) producator", r"authorized distributor"]),
    "procura": ("Procura / imputernicire", [
        r"\bprocura\b", r"imputernicire", r"power of attorney"]),
    "declaratie": ("Alte declaratii ale ofertantului", [
        r"\bdeclaratie\b", r"\bdeclaratia\b", r"\bdeclar pe propria raspundere", r"declaratii"]),
    # После деклараций и доверенностей. «ISO 9001:2015» часто стоит в бланке письма компании,
    # поэтому номер стандарта засчитывается только рядом со словом «certificat(e)».
    "iso": ("Certificat ISO / sistem de management al calitatii (13485, 9001, 14001)", [
        r"certific\w*.{0,300}\biso ?(13485|9001|14001)", r"\biso ?(13485|9001|14001).{0,300}certific",
        r"certification body", r"quality management system", r"certifi(es|ca) that",
        r"sistem\w* de management al calitatii"]),
    "datasheet": ("Fisa tehnica / catalog / brosura / instructiuni", [
        r"data ?sheet", r"technical data", r"\bcatalog", r"brochure", r"brosura", r"instructions for use",
        r"user manual", r"instructiuni de utilizare", r"\bprospect", r"fisa tehnica", r"specifications?\b",
        r"product information"]),
}
OTHER = "other"
NO_TEXT = "no_text"

# Только по имени файла (в тексте такие шаблоны дают ложные срабатывания).
# Имя нормализуется: знаки препинания -> пробелы, цифры остаются («iso 13485»).
FILENAME_ONLY = {
    "certificat_inregistrare_firma": [r"certificat de inreg"],
    "iso": [r"\biso\b"],
    "ce_doc": [r"certificate of conformity", r"\bce\b"],
    "amdm": [r"\blot(ul)?\b.*\breg\b", r"\bdm\b.*\binregistr", r"\binreg\b"],
    "datasheet": [r"brosur", r"brochure", r"catalog", r"prospect", r"\bimagin", r"datasheet"],
}

# Штамп электронной подписи MoldSign: в сканах это единственный текст на странице,
# а в остальных документах он занимает «шапку». Вырезается перед классификацией.
SIGNATURE_RE = re.compile(r"digitally signed by .{0,160}?location: ?[a-z]+( europeana| moldova)*")
MIN_TEXT_CHARS = 40  # меньше - считаем, что текстового слоя нет (скан, картинка)

# Признаки страницы каталога / даташита, когда явных слов «datasheet» нет
PRODUCT_MARKERS = [r"www\.", r"[®©™]", r"\bref\.?\b", r"\bp/n\b", r"\bcode\b", r"\bmodel\b",
                   r"\d+ ?mm\b", r"\d+ ?ml\b", r"\bdisposable\b", r"\breusable\b", r"\bsterile\b"]
_PRODUCT_RX = [re.compile(p) for p in PRODUCT_MARKERS]

# Заголовки колонок таблиц Anexa. Одна колонка встречается и в декларациях
# (например, об образцах - «modelul articolului»), поэтому таблица засчитывается по двум и более.
STRUCTURAL = {
    "anexa22": [r"modelul a\w{0,3}icolului", r"specif\w*\.? tehnic\w* deplin", r"specificatia tehnica propusa",
                r"tara de origine.{0,40}producator"],
    "anexa23": [r"pret unitar \(fara tva\)", r"suma \(fara tva\)", r"pret unitar.{0,30}suma"],
}
MIN_STRUCTURAL_HITS = 2
# Вне «шапки» общие слова («specificatii tehnice», «ISO 13485», «regulation (EU) 2017/745»)
# встречаются в декларациях и брошюрах, поэтому эти типы в тексте не ищутся
# (Anexa 22/23 в тексте узнаются только по STRUCTURAL).
TITLE_ONLY = ("anexa22", "anexa23", "iso", "ce_doc")
# Таблицы Anexa продолжаются на следующих страницах без заголовка - страница наследует тип
CONTINUED_TYPES = ("anexa22", "anexa23")

# Порядок типов в DOC_TYPES важен: Anexa 22/23 и DUAE проверяются раньше «declaratie»,
# потому что в их шапках встречается слово «declaratie».
ORDER = list(DOC_TYPES)
_COMPILED = {t: [re.compile(p) for p in pats] for t, (_, pats) in DOC_TYPES.items()}
_STRUCTURAL = {t: [re.compile(p) for p in pats] for t, pats in STRUCTURAL.items()}


def _relaxed(pattern: str) -> str:
    """Шаблон для имён файлов без пробелов: «certificatprivindlipsarestantelor»."""
    return pattern.replace(r"\b", "").replace(" ", " ?")


_FILENAME_COMPILED = {t: [re.compile(_relaxed(p)) for p in pats] + [re.compile(p) for p in FILENAME_ONLY.get(t, [])]
                      for t, (_, pats) in DOC_TYPES.items()}

# Группы, которыми размечает человек (эталон 21641880). Тип системы -> группа.
GROUPS = {
    "Documente generale": ["duae", "cerere_participare", "declaratie_valabilitate", "garantie_oferta",
                           "certificat_restante", "extras_registru", "certificat_inregistrare_firma",
                           "cont_bancar", "mediu_eee", "lista_fondatori", "raport_financiar", "procura",
                           "declaratie"],
    "Document tehnic": ["datasheet", "other"],
    "ISO/CE/DM": ["iso", "ce_doc", "amdm", "autorizatie_producator"],
    "anexa22": ["anexa22"],
    "anexa23": ["anexa23"],
    "anexa22/anexa23": ["anexa22+23"],
}
GROUP_OF = {t: g for g, types in GROUPS.items() for t in types}
# Синонимы в разметке человека
GROUP_ALIASES = {"ISO/CE": "ISO/CE/DM", "Certificat de inregistrare": "Documente generale"}


@dataclass
class Classification:
    doc_type: str
    confidence: float
    rule: str
    page_types: List[Optional[str]] = field(default_factory=list)  # None - на странице нет текста
    has_text: bool = True

    @property
    def contains(self) -> List[str]:
        """Типы разделов в порядке появления (для PDF-«пачек»)."""
        seen = []
        for t in self.page_types:
            if t and t not in seen:
                seen.append(t)
        return seen


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text.lower()).strip()


def clean_page(text: str) -> str:
    """Нормализованный текст страницы без штампов подписи."""
    return SIGNATURE_RE.sub(" ", normalize(text)).strip()


def normalize_filename(name: str) -> str:
    name = normalize(name)
    name = re.sub(r"\.(pdf|signed|semnat)\b", " ", name)
    return re.sub(r"[\W_]+", " ", name).strip()


def _structural_hit(doc_type: str, text: str) -> Optional[str]:
    hits = [rx.pattern for rx in _STRUCTURAL.get(doc_type, []) if rx.search(text)]
    return " & ".join(hits) if len(hits) >= MIN_STRUCTURAL_HITS else None


def _first_match(text: str, region: str = "title") -> Optional[Tuple[str, str]]:
    """region: "title" - шапка страницы, "text" - весь текст начала, "filename" - имя файла."""
    for doc_type in ORDER:
        if region == "filename":
            patterns = _FILENAME_COMPILED[doc_type]
        elif region == "text" and doc_type in TITLE_ONLY:
            patterns = []
        else:
            patterns = _COMPILED[doc_type]
        for rx in patterns:
            if rx.search(text):
                return doc_type, rx.pattern
        if region != "filename":
            structural = _structural_hit(doc_type, text)
            if structural:
                return doc_type, structural
    return None


def group_of(doc_type: Optional[str], contains: str = "") -> Optional[str]:
    """Группа человека для типа системы; PDF-«пачка» с обеими анексами - это anexa22/anexa23."""
    parts = set((contains or "").split(","))
    if {"anexa22", "anexa23"} <= parts:
        return "anexa22/anexa23"
    return GROUP_OF.get(doc_type)


def classify_pages(cleaned: List[str]) -> List[Optional[str]]:
    """Тип каждой страницы по её «шапке»; продолжение таблицы Anexa наследует тип."""
    result, prev = [], None
    for text in cleaned:
        if len(text) < MIN_TEXT_CHARS:
            result.append(None)
            continue
        hit = _first_match(text[:TITLE_CHARS])
        page_type = hit[0] if hit else (prev if prev in CONTINUED_TYPES else OTHER)
        result.append(page_type)
        prev = page_type
    return result


def classify(pages: List[str], filename: str = "") -> Classification:
    """Тип документа (по первой странице с текстом) и типы всех страниц."""
    cleaned = [clean_page(p) for p in pages]
    texted = [p for p in cleaned if len(p) >= MIN_TEXT_CHARS]
    fname = normalize_filename(filename)

    if not texted:
        hit = _first_match(fname, "filename")
        page_types = [None] * len(pages)
        if hit:
            return Classification(hit[0], 0.3, f"filename(no text):{hit[1]}", page_types, False)
        return Classification(NO_TEXT, 0.0, "no text layer", page_types, False)

    page_types = classify_pages(cleaned)
    title = texted[0][:TITLE_CHARS]
    head = " ".join(texted[:HEAD_PAGES])
    for text, conf, label in ((title, 0.9, "title"), (head, 0.6, "text"), (fname, 0.4, "filename")):
        hit = _first_match(text, label)
        if hit:
            doc_type, rule = hit
            # Один PDF с обеими анексами: техническая и ценовая части вместе
            if doc_type in ("anexa22", "anexa23") and {"anexa22", "anexa23"} <= set(page_types):
                return Classification("anexa22+23", conf, f"{label}:{rule} + pages", page_types)
            return Classification(doc_type, conf, f"{label}:{rule}", page_types)

    markers = sum(1 for rx in _PRODUCT_RX if rx.search(head))
    if markers >= 2:
        return Classification("datasheet", 0.4, f"product markers x{markers}", page_types)
    return Classification(OTHER, 0.0, "no rule", page_types)


def extract_pages(path: Path) -> List[str]:
    import fitz  # PyMuPDF: импорт здесь, чтобы манифест и тесты правил работали без него
    with fitz.open(path) as doc:
        return [page.get_text() for page in doc]


def classify_tender(tender_dir: Path, force: bool = False) -> dict:
    """Классифицирует уникальные документы манифеста, у которых ещё нет типа (или все при force)."""
    db = Path(tender_dir) / MANIFEST_NAME
    conn = sqlite3.connect(db)
    migrate(conn)
    where = "" if force else "WHERE d.doc_type IS NULL"
    rows = conn.execute(
        "SELECT d.sha256, MIN(r.path), MIN(r.original_name) FROM documents d "
        f"JOIN document_refs r USING (sha256) {where} GROUP BY d.sha256").fetchall()

    counts = {}
    now = datetime.now().isoformat(timespec="seconds")
    for sha, path, original_name in rows:
        # Решение человека правилами не перезаписываем
        if conn.execute("SELECT type_source FROM documents WHERE sha256 = ?", (sha,)).fetchone()[0] == "human":
            continue
        try:
            pages = extract_pages(Path(path))
        except Exception as e:
            logger.warning(f"[CLASSIFY] Не удалось прочитать {path}: {type(e).__name__} - {e}")
            pages = []
        c = classify(pages, original_name or Path(path).name)
        conn.execute("DELETE FROM document_pages WHERE sha256 = ?", (sha,))
        conn.executemany("INSERT INTO document_pages (sha256, page_no, text, page_type) VALUES (?, ?, ?, ?)",
                         [(sha, i + 1, t, pt) for i, (t, pt) in enumerate(zip(pages, c.page_types))])
        conn.execute(
            "UPDATE documents SET doc_type = ?, type_confidence = ?, type_source = 'rule', type_rule = ?, "
            "pages = ?, has_text = ?, contains = ?, classified_at = ? WHERE sha256 = ?",
            (c.doc_type, c.confidence, c.rule, len(pages), int(c.has_text), ",".join(c.contains), now, sha))
        counts[c.doc_type] = counts.get(c.doc_type, 0) + 1
    conn.commit()
    conn.close()
    return counts


def type_summary(tender_dir: Path) -> List[Tuple[str, int, int]]:
    """(тип, уникальных документов, мест в лотах) по всему манифесту."""
    conn = sqlite3.connect(Path(tender_dir) / MANIFEST_NAME)
    try:
        return conn.execute(
            "SELECT COALESCE(d.doc_type, '?'), COUNT(DISTINCT d.sha256), COUNT(r.path) "
            "FROM documents d JOIN document_refs r USING (sha256) "
            "GROUP BY 1 ORDER BY 2 DESC").fetchall()
    finally:
        conn.close()


REVIEW_NAME = "classification_review.xlsx"
GOLD_NAME = "classification_gold.xlsx"  # эталонная разметка человека для оценки (--evaluate)
REVIEW_SHEET = "Документы"
REVIEW_COLUMN = "Правильный тип"
EXTRA_TYPES = {
    "anexa22+23": "Anexa 22 si 23 intr-un singur fisier",
    OTHER: "Altele - nu se proceseaza",
    NO_TEXT: "Fara text (scanare) - tipul nu poate fi determinat",
}


def all_types() -> dict:
    types = {t: desc for t, (desc, _) in DOC_TYPES.items()}
    types.update(EXTRA_TYPES)
    return types


def export_review(tender_dir: Path, xlsx_path: Optional[Path] = None) -> Path:
    """Таблица для проверки человеком: один уникальный документ - одна строка, сначала неуверенные."""
    from openpyxl import Workbook
    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation

    def safe(value):
        return ILLEGAL_CHARACTERS_RE.sub("", value) if isinstance(value, str) else value

    xlsx_path = Path(xlsx_path or Path(tender_dir) / REVIEW_NAME)
    conn = sqlite3.connect(Path(tender_dir) / MANIFEST_NAME)
    rows = conn.execute(
        "SELECT d.sha256, d.doc_type, d.type_confidence, d.type_source, d.type_rule, d.contains, d.pages, "
        "d.has_text, GROUP_CONCAT(DISTINCT r.participant), COUNT(DISTINCT r.lot_no), MIN(r.original_name), "
        "(SELECT text FROM document_pages p WHERE p.sha256 = d.sha256 AND p.page_no = 1), MIN(r.path) "
        "FROM documents d JOIN document_refs r USING (sha256) GROUP BY d.sha256 "
        "ORDER BY d.type_source = 'human', d.type_confidence, d.doc_type").fetchall()
    conn.close()

    wb = Workbook()
    ws = wb.active
    ws.title = REVIEW_SHEET
    header = ["sha256", "Тип (система)", "Группа (система)", "Уверенность", "Источник", "Правило", "Внутри файла",
              "Страниц", "Есть текст", "Участники", "Лотов", "Имя файла", "Начало первой страницы", "Путь",
              REVIEW_COLUMN]
    ws.append(header)
    for (sha, doc_type, conf, source, rule, contains, pages, has_text, parts, lots, name,
         first_page, path) in rows:
        ws.append([safe(v) for v in (sha, doc_type, group_of(doc_type, contains), conf, source, rule, contains,
                                     pages, "да" if has_text else "нет", parts, lots, name,
                                     clean_page(first_page or "")[:300], path,
                                     doc_type if source == "human" else None)])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    review_col = get_column_letter(len(header))
    ws[f"{review_col}1"].fill = PatternFill("solid", fgColor="FFFF00")
    for i, width in enumerate((14, 22, 18, 11, 9, 30, 30, 8, 9, 30, 7, 40, 80, 20, 22), start=1):
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.freeze_panes = "B2"
    ws.auto_filter.ref = ws.dimensions

    # Типы, а ниже группы: группу можно выбрать, чтобы подтвердить, что система не ошиблась с группой
    types_ws = wb.create_sheet("Типы")
    types_ws.append(["Тип", "Описание"])
    for t, desc in all_types().items():
        types_ws.append([t, desc])
    for g, types in GROUPS.items():
        if g not in all_types():
            types_ws.append([g, "группа: " + ", ".join(types)])
    types_ws.column_dimensions["A"].width = 30
    types_ws.column_dimensions["B"].width = 70

    dv = DataValidation(type="list", formula1=f"=Типы!$A$2:$A${types_ws.max_row}", allow_blank=True)
    ws.add_data_validation(dv)
    dv.add(f"{review_col}2:{review_col}{len(rows) + 1}")

    wb.save(xlsx_path)
    return xlsx_path


def read_labels(xlsx_path: Path) -> List[Tuple[int, str, str]]:
    """(номер строки, sha256, метка человека) для заполненных строк колонки «Правильный тип»."""
    from openpyxl import load_workbook

    wb = load_workbook(xlsx_path, read_only=True)
    try:
        # read_only держит файл открытым до close(): читаем всё сразу и отпускаем файл
        rows = list(wb[REVIEW_SHEET].iter_rows(values_only=True))
    finally:
        wb.close()
    header = list(rows[0])
    sha_i, fix_i = header.index("sha256"), header.index(REVIEW_COLUMN)
    return [(n, row[sha_i], str(row[fix_i]).strip())
            for n, row in enumerate(rows[1:], start=2) if row[sha_i] and row[fix_i] and str(row[fix_i]).strip()]


def _label_groups(label: str) -> List[str]:
    """Группы, допустимые по метке человека: «Document tehnic/ISO/CE» - смешанный документ."""
    label = GROUP_ALIASES.get(label, label)
    if label in GROUPS or label in GROUP_OF:
        return [GROUP_OF.get(label, label)]
    # Ищем известные названия групп и синонимов внутри метки, от длинных к коротким
    groups, rest = [], label
    for name in sorted(list(GROUPS) + list(GROUP_ALIASES), key=len, reverse=True):
        if name in rest:
            group = GROUP_ALIASES.get(name, name)
            if group not in groups:
                groups.append(group)
            rest = rest.replace(name, "")
    return groups


def evaluate(tender_dir: Path, gold_path: Optional[Path] = None) -> dict:
    """Сравнивает группы системы с эталонной разметкой человека."""
    labels = read_labels(Path(gold_path or Path(tender_dir) / GOLD_NAME))
    conn = sqlite3.connect(Path(tender_dir) / MANIFEST_NAME)
    system = {sha: (t, c, has_text) for sha, t, c, has_text
              in conn.execute("SELECT sha256, doc_type, contains, has_text FROM documents")}
    names = dict(conn.execute("SELECT sha256, MIN(original_name) FROM document_refs GROUP BY sha256"))
    conn.close()

    result = {"total": 0, "correct": 0, "scans": 0, "scans_correct": 0, "per_group": {}, "errors": []}
    for _, sha, label in labels:
        if sha not in system:
            continue
        doc_type, contains, has_text = system[sha]
        expected = _label_groups(label)
        got = group_of(doc_type, contains)
        ok = got in expected
        result["total"] += 1
        result["correct"] += ok
        if not has_text:
            result["scans"] += 1
            result["scans_correct"] += ok
        stats = result["per_group"].setdefault(" / ".join(expected) or label, [0, 0])
        stats[0] += ok
        stats[1] += 1
        if not ok:
            result["errors"].append((label, doc_type, got, names.get(sha, "")))
    return result


def apply_review(tender_dir: Path, xlsx_path: Optional[Path] = None) -> Tuple[int, List[str]]:
    """Переносит исправления человека в манифест. Возвращает (сколько применено, ошибки)."""
    labels = read_labels(Path(xlsx_path or Path(tender_dir) / REVIEW_NAME))
    valid = all_types()

    conn = sqlite3.connect(Path(tender_dir) / MANIFEST_NAME)
    applied, errors = 0, []
    now = datetime.now().isoformat(timespec="seconds")
    for n, sha, fix in labels:
        if fix not in valid:
            group = GROUP_ALIASES.get(fix, fix)
            if group in GROUPS:
                # Группа вместо типа: совпала с системой - это подтверждение, иначе нужен конкретный тип
                row = conn.execute("SELECT doc_type, contains FROM documents WHERE sha256 = ?", (sha,)).fetchone()
                system_group = group_of(*row) if row else None
                if system_group != group:
                    errors.append(f"строка {n}: группа {fix!r} не совпадает с системой ({system_group}) - "
                                  f"выберите конкретный тип")
            else:
                errors.append(f"строка {n}: неизвестный тип {fix!r}")
            continue
        cur = conn.execute(
            "UPDATE documents SET doc_type = ?, type_confidence = 1.0, type_source = 'human', "
            "type_rule = 'human', classified_at = ? WHERE sha256 = ?", (fix, now, sha))
        if cur.rowcount:
            applied += 1
        else:
            errors.append(f"строка {n}: документ {str(sha)[:12]} не найден в манифесте")
    conn.commit()
    conn.close()
    return applied, errors


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Классификация документов тендера по манифесту")
    parser.add_argument("tender_dir", type=Path)
    parser.add_argument("--force", action="store_true", help="переклассифицировать всё, кроме решений человека")
    parser.add_argument("--export", action="store_true", help=f"выгрузить {REVIEW_NAME} для проверки")
    parser.add_argument("--apply", nargs="?", const="", metavar="XLSX",
                        help=f"применить колонку «{REVIEW_COLUMN}» (по умолчанию {REVIEW_NAME} в папке тендера)")
    parser.add_argument("--evaluate", nargs="?", const="", metavar="XLSX",
                        help=f"сравнить группы с эталоном (по умолчанию {GOLD_NAME} в папке тендера)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] %(message)s")
    sys.stdout.reconfigure(encoding="utf-8")

    if args.apply is not None:
        applied, errors = apply_review(args.tender_dir, Path(args.apply) if args.apply else None)
        print(f"Исправлений применено: {applied}")
        for e in errors:
            print(f"  ! {e}")

    counts = classify_tender(args.tender_dir, force=args.force)
    print(f"Классифицировано сейчас: {sum(counts.values())}")
    for doc_type, docs, refs in type_summary(args.tender_dir):
        print(f"  {doc_type:30} {docs:5} уникальных  {refs:6} в лотах")

    if args.evaluate is not None:
        r = evaluate(args.tender_dir, Path(args.evaluate) if args.evaluate else None)
        texted = r["total"] - r["scans"]
        print(f"\nСовпадение групп с эталоном: {r['correct']}/{r['total']} = {r['correct'] / r['total']:.1%}")
        print(f"  с текстом: {r['correct'] - r['scans_correct']}/{texted}, сканы: {r['scans_correct']}/{r['scans']}")
        for g, (ok, n) in sorted(r["per_group"].items(), key=lambda kv: -kv[1][1]):
            print(f"  {g:32} {ok:4}/{n}")
        print("Расхождения (эталон <- тип системы, группа системы, файл):")
        for label, doc_type, got, name in r["errors"]:
            print(f"  {label:24} <- {str(doc_type):24} {str(got):20} {name[:50]}")

    if args.export:
        try:
            print(f"Таблица для проверки: {export_review(args.tender_dir)}")
        except PermissionError:
            print(f"Не удалось записать {REVIEW_NAME}: файл открыт в Excel? Закройте его и повторите.")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
