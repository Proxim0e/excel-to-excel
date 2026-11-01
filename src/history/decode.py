"""
Расшифровка разметки рабочего Excel по кодбуку (docs/CODEBOOK.md, ответы Ivan 01.11.2026).

Строка 1 участника: текст - техническое мнение, цвет - итоговое решение. Когда они противоречат
друг другу (красный «Tehnic corespunde» без другой причины, зелёный «nu corespunde»), решение берётся
по цвету, но запись помечается needs_review - спросить человека (п. 11 кодбука).
"""
import re
from typing import Optional, Tuple

GREEN, YELLOW, RED, BLUE, NAVY, ORANGE = "FF00B050", "FFFFFF00", "FFFF0000", "FF00B0F0", "FF001F4D", "FFFFC000"
PRICE_LIMIT = 1.3   # цена ≥ 130% базы - автоматическое отклонение по закону
TEMPLATE_TEXT = "tehnic corespunde"  # текст шаблона строки 1


def norm_color(rgb: Optional[str]) -> Optional[str]:
    if not rgb:
        return None
    rgb = str(rgb).upper()
    if len(rgb) == 6:
        rgb = "FF" + rgb
    if rgb == "00000000":
        return None
    return "FF" + rgb[2:] if rgb.startswith("00") and rgb != "00000000" else rgb


def _t(text: Optional[str]) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def rejection_reason(text: str, ratio: Optional[float]) -> Optional[str]:
    t = _t(text)
    if "garant" in t:
        return "guarantee_wrong" if "gresit" in t or "greșit" in t else "guarantee_insufficient"
    if "inregistrat" in t or "înregistrat" in t:
        return "amdm"
    if "retras" in t:
        return "withdrawn"
    if re.match(r"g\s*[-–]", t) or t == "g":
        return "annulled_g"
    if "alternativ" in t:
        return "alternative_rejected"
    if t in ("0.3", "0,3") or (ratio is not None and ratio >= PRICE_LIMIT):
        return "price_130"
    if "nu corespunde" in t:
        return "technical"
    return None


def decode_offer(row1_text: Optional[str], row1_color: Optional[str], suffix: str = "",
                 ratio: Optional[float] = None) -> Tuple[str, Optional[str], Optional[str]]:
    """(решение, причина, почему спросить человека) по строке 1 участника."""
    t, color = _t(row1_text), norm_color(row1_color)
    suffix = (suffix or "").upper()
    if color == GREEN:
        if "nu corespunde" in t and "alternativ" not in t:
            return "accepted", None, "зелёный, но текст «nu corespunde»"
        return ("alternative" if "alternativ" in t else "accepted"), None, None
    if color == RED:
        reason = rejection_reason(t, ratio)
        if reason:
            return "rejected", reason, None
        if t.startswith(TEMPLATE_TEXT) or not t:
            return "rejected", None, "красный без причины (текст «Tehnic corespunde» или пусто)"
        return "rejected", None, f"красный, причина не распознана: «{row1_text}»"
    if color == BLUE:
        if not t or t == TEMPLATE_TEXT:
            return "not_evaluated", None, None   # шаблон не менялся: раньше по цене была зелёная оферта
        if "mostre" in t or (suffix == "M" and "alternativ" not in t):
            return "samples", None, None
        if "alternativ" in t or suffix in ("A", "AD"):
            return "alternative", None, None
        return "working_group", None, None
    if color == YELLOW:
        return "clarification", None, None
    if color == NAVY:
        return "not_held", None, None
    if color == ORANGE:
        return "improved_offer", None, None
    if not t:
        return "not_evaluated", None, None
    return "unknown", None, f"цвет строки 1 не из кодбука: {row1_color}"


def lot_outcome(tab_color: Optional[str], suffix: str = "") -> Optional[str]:
    color, suffix = norm_color(tab_color), (suffix or "").upper()
    if color == GREEN:
        return "awarded"
    if color == RED:
        return "not_awarded"
    if color == NAVY:
        return "not_held"
    if color == YELLOW:
        return "clarification_open"
    if color == BLUE:
        return {"M": "samples", "A": "alternative", "AD": "alternative"}.get(suffix, "working_group")
    return None


def sheet_suffix(title: str) -> Tuple[Optional[int], str]:
    """«lot 45(A)» -> (45, «A»); «lot 9M» -> (9, «M»); «lot 260 (M)» -> (260, «M»)."""
    m = re.match(r"\s*lot\s*(\d+)\s*\(?\s*([A-Za-z]{0,2})\s*\)?\s*$", title or "", re.IGNORECASE)
    if not m:
        return None, ""
    return int(m.group(1)), m.group(2).upper()
