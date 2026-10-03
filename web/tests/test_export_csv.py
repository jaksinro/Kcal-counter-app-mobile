"""Tests de l'export CSV de l'historique (GET /api/export?type=jours|aliments).

    python -m unittest discover -s tests      (depuis le dossier web/)
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server  # noqa: E402


def entry(meal, grams, added, name="Riz", kcal=130.0, proteins=2.7, detail=""):
    food = {"id": "f_" + name, "name": name, "detail": detail, "calories": kcal, "proteins": proteins}
    return {"id": name + str(added), "food": food, "grams": grams, "meal": meal, "addedAt": added}


def sample():
    return {"goals": {}, "customFoods": [], "journal": {
        "2026-10-02": [entry("diner", 100, 5, "Soupe", kcal=40.0, proteins=1.0)],
        "2026-10-01": [
            entry("dejeuner", 150, 3),
            entry("petit-dejeuner", 200, 4, "Lait", kcal=46.0, proteins=3.2),
        ],
        "2026-09-30": [],  # jour vidé : pas de ligne
    }}


def rows(text):
    return [line.split(";") for line in text.lstrip("﻿").split("\r\n") if line]


class ExportCsvTest(unittest.TestCase):
    def test_daily_totals_one_line_per_day_in_date_order(self):
        text = server.export_csv(sample(), "jours")
        self.assertTrue(text.startswith("﻿"))
        lines = rows(text)
        self.assertEqual(lines[0][:3], ["date", "kcal", "proteines_g"])
        self.assertEqual(lines[0][-1], "nb_aliments")
        self.assertEqual([r[0] for r in lines[1:]], ["2026-10-01", "2026-10-02"])
        # 150 g de riz à 130 kcal + 200 g de lait à 46 kcal = 195 + 92
        self.assertEqual(lines[1][1], "287,0")
        self.assertEqual(lines[1][2], "10,4")  # 4,05 + 6,4
        self.assertEqual(lines[1][-1], "2")

    def test_detail_one_line_per_food_sorted_by_meal(self):
        lines = rows(server.export_csv(sample(), "aliments"))
        self.assertEqual(lines[0][:5], ["date", "repas", "aliment", "detail", "quantite_g"])
        self.assertEqual([(r[1], r[2]) for r in lines[1:3]], [("Petit-déjeuner", "Lait"), ("Déjeuner", "Riz")])
        self.assertEqual(lines[1][4:6], ["200,0", "92,0"])
        self.assertEqual(len(lines), 4)

    def test_cells_are_escaped_for_spreadsheets(self):
        data = {"journal": {"2026-10-01": [
            entry("diner", 50, 1, '=HYPERLINK("x")'),
            entry("diner", 50, 2, "Pâtes; sauce", detail="Marque \"Bio\""),
        ]}}
        lines = server.export_csv(data, "aliments").split("\r\n")
        self.assertIn(";\"'=HYPERLINK(\"\"x\"\")\";", lines[1])
        self.assertIn(";\"Pâtes; sauce\";\"Marque \"\"Bio\"\"\";", lines[2])

    def test_empty_journal_gives_header_only(self):
        self.assertEqual(len(rows(server.export_csv({"journal": {}}, "jours"))), 1)

    def test_unknown_kind_is_refused(self):
        with self.assertRaises(ValueError):
            server.export_csv(sample(), "tout")


if __name__ == "__main__":
    unittest.main()
