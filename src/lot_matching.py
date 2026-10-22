"""Сопоставление спарсенного лота с лотом из родительского файла."""
from __future__ import annotations

import logging
from typing import Any, Optional

from src.utils.text_helpers import normalize_spaces

logger = logging.getLogger(__name__)


def match_lot_by_title(title: Optional[str], parent_data: dict[int, dict[str, Any]]) -> Optional[int]:
    """
    Ищет номер лота по названию. Возвращает номер ТОЛЬКО при единственном совпадении.

    Неоднозначное совпадение (короткое название вроде "Electrod" подходит
    к нескольким лотам) возвращает None: пропущенный лот виден в отчёте,
    а участники, записанные в чужой лист, - нет.
    """
    if not title:
        return None
    scraped = normalize_spaces(title).casefold()

    hits = []
    for number, info in parent_data.items():
        name = info.get("denumire")
        if not name:
            continue
        parent = normalize_spaces(str(name)).casefold()
        if parent in scraped or scraped in parent:
            hits.append(number)

    if len(hits) == 1:
        return hits[0]
    if hits:
        logger.warning("Лот не сопоставлен: название %r подходит к нескольким лотам %s",
                       title[:60], sorted(hits))
    return None
