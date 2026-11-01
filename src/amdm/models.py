"""
Модели данных проверки регистрации AMDM.

RegistryRecord — одна запись реестра registru.dispozitive.amdm.gov.md.
Номер DM — уникальный ключ: одна запись на номер.

OfferRegistration — то, что заявил участник в своём Excel-листе лота:
строка 3 (Producator / Țara / Model) и строка 4 (DM..., Rg..., дата приказа или ISO/CE).
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import List, Optional


@dataclass
class RegistryRecord:
    dm: str                              # Nr, например DM000655298
    name: Optional[str] = None           # Denumire
    trade_name: Optional[str] = None     # Den. comerc.
    model: Optional[str] = None          # Model
    catalog: Optional[str] = None        # Nr. catalog (может быть несколько значений)
    country: Optional[str] = None        # Țara
    producer: Optional[str] = None       # Producătorul
    representative: Optional[str] = None # Reprezentant
    order: Optional[str] = None          # Ordin, например Rg04-305
    order_date: Optional[str] = None     # Data приказа, ISO YYYY-MM-DD
    customs_code: Optional[str] = None   # Cod vamal

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class OfferRegistration:
    sheet: str                           # имя листа лота, например "lot 23" или "lot 45(A)"
    column: int                          # колонка участника
    supplier: str                        # Ofertant
    producer: Optional[str] = None
    country: Optional[str] = None
    model: Optional[str] = None          # всё, что после "Model:" (включая REF / Codul produsului)
    dm_numbers: List[str] = field(default_factory=list)
    order: Optional[str] = None          # Rg04-000183 из строки 4
    order_date: Optional[str] = None     # дата из строки 4, ISO YYYY-MM-DD
    quality_docs: List[str] = field(default_factory=list)  # ISO 13485, ISO 9001, CE, DoC
    raw_row3: str = ""
    raw_row4: str = ""
    note: Optional[str] = None           # "pentru DM", "piese" и т.п. — пояснение участника


# Статусы проверки (цвета соответствуют твоему кодбуку)
OK, CHECK, FAIL, NO_DM, IN_PROGRESS = "verde", "galben", "rosu", "fara DM", "neverificat"


@dataclass
class CheckResult:
    offer: OfferRegistration
    dm: Optional[str]
    status: str
    reasons: List[str] = field(default_factory=list)
    record: Optional[RegistryRecord] = None
