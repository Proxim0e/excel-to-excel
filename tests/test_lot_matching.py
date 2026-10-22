from src.lot_matching import match_lot_by_title

PARENT = {
    10: {"denumire": "Cateter WIT Mandrin"},
    16: {"denumire": "Electrod ECG, Adeziv"},
    17: {"denumire": "Electrod pasiv, Lamiday Noury"},
    20: {"denumire": None},
}


def test_unique_match_returns_number():
    assert match_lot_by_title("Lot: Cateter   WIT  mandrin", PARENT) == 10


def test_ambiguous_match_returns_none():
    # "Electrod" - подстрока обоих названий: раньше бралось первое попавшееся
    assert match_lot_by_title("Electrod", PARENT) is None


def test_no_match_or_empty_title():
    assert match_lot_by_title("Senzor de flux", PARENT) is None
    assert match_lot_by_title(None, PARENT) is None
