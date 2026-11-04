"""
Когда решение ставится автоматически, а когда - вопрос человеку.

Автоматически без вопроса:
  - цена ≥ 130% базы - nu_corespunde (закон);
  - уровень 1 не совпал - alternativa (синий): запрос и Anexa 22 не совпадают юридически.
Автоматически «corespunde» - только при полной уверенности, всё сразу:
  уровень 1 ok, уровень 2 нашёл код модели и параметры в документах, AMDM verde,
  карточка товара подтверждена человеком и эта модель раньше принималась человеком.
Всё остальное - вопрос: system предлагает статус, человек выбирает. Нет подтверждённой карточки -
сначала вопрос «карточка товара».
"""
from dataclasses import dataclass, field
from typing import List, Optional

from src.amdm.models import CHECK, FAIL, NO_DM, OK as AMDM_OK
from src.anexa22.evidence import GREEN, OfferCheck
from src.evaluate.history import HistoryInfo

CORESPUNDE, ALTERNATIVA, NU_CORESPUNDE, CLARIFICARE, NEEVALUAT = (
    "corespunde", "alternativa", "nu_corespunde", "clarificare", "neevaluat")
STATUSES = (CORESPUNDE, ALTERNATIVA, NU_CORESPUNDE, CLARIFICARE, NEEVALUAT)
AUTO, HUMAN, PENDING = "auto", "human", "pending"


@dataclass
class Decision:
    status: str                 # итог или предложение системы (при source = pending)
    source: str                 # auto / pending
    confidence: float
    reasons: List[str] = field(default_factory=list)
    reason_code: Optional[str] = None
    ask_card: bool = False      # нужен вопрос «карточка товара»


def decide(check: Optional[OfferCheck], amdm_status: Optional[str], amdm_reasons: List[str],
           history: HistoryInfo) -> Decision:
    """Решение по оферте, прошедшей уровень 1 (alternativa отсекается раньше, в engine)."""
    reasons: List[str] = []
    level2 = bool(check and check.status == GREEN and check.has_docs)
    if not check:
        reasons.append("нет Anexa 22 по этому лоту")
    elif not check.has_docs:
        reasons.append("нет технических документов (//docs)")
    elif check.status != GREEN:
        reasons.append("не подтверждено документами: " + "; ".join(check.missing[:4]))
    if amdm_status == FAIL:
        reasons.append("AMDM: " + "; ".join(amdm_reasons[:2]))
    elif amdm_status in (CHECK, NO_DM):
        reasons.append("AMDM: " + "; ".join(amdm_reasons[:2]))
    elif amdm_status != AMDM_OK:
        reasons.append("AMDM не проверен")
    if not history.card_confirmed:
        reasons.append("карточка товара не подтверждена")
    if not history.accepted_before:
        reasons.append("модель раньше не принималась")

    if level2 and amdm_status == AMDM_OK and history.card_confirmed and history.accepted_before:
        return Decision(CORESPUNDE, AUTO, 0.95, ["полное соответствие: документы, AMDM, история"])

    # Предложение системы для вопроса
    if amdm_status == FAIL:
        suggested, code = NU_CORESPUNDE, "amdm"
    elif level2 and amdm_status == AMDM_OK:
        suggested, code = CORESPUNDE, None
    else:
        suggested, code = CLARIFICARE, None
    score = 0.2 + 0.3 * level2 + 0.2 * (amdm_status == AMDM_OK) + 0.15 * history.card_confirmed \
        + 0.15 * history.accepted_before
    return Decision(suggested, PENDING, round(score, 2), reasons, code, ask_card=not history.card_confirmed)
