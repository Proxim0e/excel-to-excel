import unittest

from src.excel.reader import collect_lots

COLS = {'nr_lot': 1, 'denumire': 2, 'specificatie': 3, 'unitate_masura': 4, 'cantitate_total': 5,
        'suma_alocata': 6}


class CollectLotsTests(unittest.TestCase):
    def test_single_position_untouched(self):
        lots = collect_lots([("185", "Seringă", "Seringă 5 ml", "Bucată", 100, 50.0)], COLS)
        self.assertEqual(lots[185]['specificatie'], "Seringă 5 ml")
        self.assertNotIn('positions', lots[185])

    def test_repeated_lot_is_one_lot_with_positions(self):
        # лот 186 из 21641880: тесты + журнал REGISTRU, сумма лота - только в первой строке
        rows = [("186", "Test-indicator și Registru", "Indicator 121/15", "Test", "10000", "2700"),
                ("186", "Test-indicator și Registru", "Registru Ord nr 828", "Bucată", "11", None),
                (None, None, None, None, None, None)]
        lot = collect_lots(rows, COLS)[186]
        self.assertEqual((lot['cantitate_total'], lot['unitate_masura'], lot['suma_alocata']),
                         ("10000", "Test", "2700"))
        self.assertEqual(lot['specificatie'], "Poziția 1 (10000 Test): Indicator 121/15\n\n"
                                              "Poziția 2 (11 Bucată): Registru Ord nr 828")
        self.assertEqual(len(lot['positions']), 2)

    def test_empty_first_values_filled_from_next_positions(self):
        rows = [(69.0, "Set radiologie", "Film 13x18", "Bucată", 2000.0, None),
                (69.0, "Set radiologie", "Developator", "litru", 600.0, 508626.43)]
        lot = collect_lots(rows, COLS)[69]
        self.assertEqual(lot['suma_alocata'], 508626.43)
        self.assertEqual(lot['cantitate_total'], 2000.0)
        self.assertTrue(lot['specificatie'].startswith("Poziția 1 (2000 Bucată): Film 13x18"))


if __name__ == "__main__":
    unittest.main()
