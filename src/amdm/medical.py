"""
Медицинское изделие или нет - по самому реестру AMDM.

CPV не помогает: в централизованных тендерах у всех лотов 33100000 («медицинское оборудование»), включая
бумагу и DVD-R. Словарь названий тоже: термобумага почти всегда зарегистрирована (21641880: у 76 оферт по
бумаге есть номер DM), а DVD-R - нет. Поэтому спрашиваем реестр: есть ли в нём вообще изделия такого типа.

Тип - первое слово названия лота («DVD-R+plicuri» -> «DVD-R», «Hârtie termosensibilă» -> «Hârtie»).
В реестре ищется вхождение (диакритика в реестре - «HÂRTIE», поэтому в запрос идёт самый длинный кусок слова
без диакритики), а затем проверяется, что название записи НАЧИНАЕТСЯ с этого слова («CITITOR DE DISCURI CD/DVD» -
не DVD-R). Ни одной такой записи - тип не регистрируется как медицинское изделие: регистрация AMDM не требуется,
нужен ISO/CE. Записей много (больше страницы) - тип медицинский.
"""
import re
from typing import Iterable, List, Optional

from src.amdm.parse import fold

PAGE = 10   # строк на странице реестра: больше - видим не все, считаем тип медицинским
# Первые слова, по которым тип не определить
GENERIC = {"set", "kit", "lot", "lotul", "piese", "accesorii", "consumabile", "material", "materiale", "dispozitiv",
           "test", "tip", "mini", "auto"}


def type_word(title: str) -> Optional[str]:
    """Первое содержательное слово названия лота (с диакритикой, как в названии)."""
    title = re.sub(r"^\s*lot(?:ul)?\s*(?:nr\.?)?\s*\d+\s*", "", title or "", flags=re.IGNORECASE)
    for word in re.findall(r"[^\W_][\w\-]*", title):
        word = word.split("+")[0].strip("-")
        if len(fold(word)) >= 3 and fold(word).lower() not in GENERIC and not word.isdigit():
            return word
    return None


STEM = 5   # основа слова: «Cabluri» и «Cablu» - один тип


def stem(word: str) -> str:
    return fold(word).lower()[:STEM]


def registry_query(word: str) -> str:
    """Запрос в фильтр реестра. Слово с диакритикой - самый длинный кусок без неё («Hârtie» -> «rtie»),
    без диакритики - основа («Cabluri» -> «Cablu»)."""
    parts = re.findall(r"[A-Za-z0-9\-]+", word)
    if len(parts) > 1 or (parts and parts[0] != word):
        return max(parts, key=len)
    return word[:STEM]


def is_medical_type(word: str, total: int, names: Iterable[str]) -> Optional[bool]:
    """По ответу реестра на registry_query(word): total записей, names - названия первой страницы.
    True - медицинский; False - таких изделий в реестре нет; None - нельзя сказать: запрос без диакритики
    с буквами, которые в реестре бывают с ней («Caciulita» против «CĂCIULIȚĂ»), ноль записей ничего не доказывает."""
    if total > PAGE:
        return True
    key = stem(word)
    if any(fold(n or "").lower().startswith(key) for n in names):
        return True
    if re.search(r"[aist]", registry_query(word), re.IGNORECASE):
        return None   # в реестре эта буква может стоять с диакритикой («Caciuli» против «CĂCIULIȚĂ»)
    return False


def type_variants(word: str) -> List[str]:
    """Слово и, для составного, его части от 4 букв: «Test-indicator» -> [«Test-indicator», «indicator»].
    Тип медицинский, если медицинский хоть один вариант."""
    parts = [p for p in word.split("-") if len(fold(p)) >= 4 and fold(p).lower() not in GENERIC]
    return list(dict.fromkeys([word] + (parts if len(parts) > 1 or (parts and parts[0] != word) else [])))
