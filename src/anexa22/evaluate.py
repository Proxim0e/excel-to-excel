"""
Точность разбора Anexa 22 против эталона - листов лотов, заполненных человеком.

Сравнение (gold - эталон, got - распознано):
  model / producer / country - буквы и цифры без регистра и диакритики, равны или одно содержит другое;
  dm   - все номера DM эталона найдены;
  spec - доля слов эталона (от 3 букв), найденных в распознанной спецификации, >= SPEC_RECALL.
         Человек дописывает к спецификации свои пометки, поэтому меряется полнота, а не равенство.
"""
import re
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from src.anexa22.gold import GoldOffer
from src.anexa22.parse import Offer
from src.classify import normalize

SPEC_RECALL = 0.8
FIELDS = ("model", "producer", "country", "dm", "spec")


def _alnum(text) -> str:
    return re.sub(r"[\W_]+", "", normalize(str(text or "")))


def same_text(gold: Optional[str], got: Optional[str]) -> bool:
    g, o = _alnum(gold), _alnum(got)
    return bool(g) and bool(o) and (g == o or g in o or o in g)


def _words(text) -> List[str]:
    return [w for w in re.findall(r"[a-z0-9]{3,}", normalize(str(text or "")))]


def spec_recall(gold: Optional[str], got: Optional[str]) -> float:
    """Доля слов эталона, найденных в распознанном тексте (склеенном: PyMuPDF теряет «_» и
    вставляет пробелы внутри слов при переносах)."""
    words = _words(gold)
    if not words:
        return 0.0
    have = _alnum(got)
    return sum(w in have for w in words) / len(words)


def participant_key(name: str) -> str:
    """Название участника без кавычек, форм собственности и регистра."""
    text = normalize(name)
    text = re.sub(r"\b(s\.?r\.?l|srl|s\.?a|im|ics|ltd|sc|co)\b\.?", " ", text)
    return re.sub(r"[\W_]+", "", text)


def compare(gold: List[GoldOffer], offers: List[Offer]) -> dict:
    by_key: Dict[Tuple[str, int], List[Offer]] = defaultdict(list)
    for o in offers:
        if o.lot is not None:
            by_key[(participant_key(o.participant), o.lot)].append(o)

    result = {"gold": len(gold), "found": 0, "fields": {f: [0, 0] for f in FIELDS},
              "per_participant": defaultdict(lambda: {"gold": 0, "found": 0, "ok": 0, "checked": 0}),
              "misses": [], "errors": []}
    for g in gold:
        p = result["per_participant"][g.participant]
        p["gold"] += 1
        candidates = by_key.get((participant_key(g.participant), g.lot))
        if not candidates:
            result["misses"].append(g)
            continue
        result["found"] += 1
        p["found"] += 1
        # Несколько строк на лот (позиции) - берём лучшую по модели
        o = max(candidates, key=lambda c: (same_text(g.model, c.get("model")),
                                           spec_recall(g.spec, c.get("spec_offered"))))
        checks = {
            "model": (g.model, same_text(g.model, o.get("model")), o.get("model")),
            "producer": (g.producer, same_text(g.producer, o.get("producer")), o.get("producer")),
            "country": (g.country, same_text(g.country, o.get("country")), o.get("country")),
            "dm": (g.dm or None, set(g.dm) <= set(o.dm), o.dm),
            "spec": (g.spec, spec_recall(g.spec, o.get("spec_offered")) >= SPEC_RECALL, o.get("spec_offered")),
        }
        for name, (expected, ok, got) in checks.items():
            if not expected:
                continue
            result["fields"][name][1] += 1
            result["fields"][name][0] += ok
            p["checked"] += 1
            p["ok"] += ok
            if not ok:
                result["errors"].append((g.participant, g.lot, name, expected, got, o))
    return result
