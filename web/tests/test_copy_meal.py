"""Tests de la copie d'un repas d'un jour à l'autre (POST /api/journal/copy).

    python -m unittest discover -s tests      (depuis le dossier web/)
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server  # noqa: E402


def entry(eid, meal, grams, added, name="Riz"):
    food = {"id": "f_" + name, "name": name, "calories": 130.0, "proteins": 2.7}
    return {"id": eid, "food": food, "grams": grams, "meal": meal, "addedAt": added}


def sample():
    return {"goals": {}, "customFoods": [], "journal": {
        "2026-09-30": [
            entry("a", "petit-dejeuner", 40, 2, "Flocons"),
            entry("b", "dejeuner", 150, 3),
            entry("c", "petit-dejeuner", 200, 1, "Lait"),
        ],
    }}


def route(data, body):
    return server.Handler.route(None, "POST", ["journal", "copy"], body, data)


class CopyMealTest(unittest.TestCase):
    def test_copies_only_the_chosen_meal_in_original_order(self):
        data = sample()
        self.assertIsNone(route(data, {"from": "2026-09-30", "to": "2026-10-01", "meal": "petit-dejeuner"}))
        copied = data["journal"]["2026-10-01"]
        self.assertEqual([e["food"]["name"] for e in copied], ["Lait", "Flocons"])
        self.assertEqual([e["grams"] for e in copied], [200, 40])
        self.assertTrue(all(e["meal"] == "petit-dejeuner" for e in copied))
        self.assertLess(copied[0]["addedAt"], copied[1]["addedAt"])

    def test_copies_are_independent_entries(self):
        data = sample()
        route(data, {"from": "2026-09-30", "to": "2026-10-01", "meal": "dejeuner"})
        src, dst = data["journal"]["2026-09-30"][1], data["journal"]["2026-10-01"][0]
        self.assertNotEqual(src["id"], dst["id"])
        dst["food"]["name"] = "Modifié"
        self.assertEqual(src["food"]["name"], "Riz")
        self.assertEqual(len(data["journal"]["2026-09-30"]), 3)  # source intacte

    def test_appends_to_existing_day_and_can_change_meal(self):
        data = sample()
        data["journal"]["2026-10-01"] = [entry("z", "diner", 100, 9)]
        route(data, {"from": "2026-09-30", "to": "2026-10-01", "meal": "dejeuner", "toMeal": "diner"})
        self.assertEqual([e["meal"] for e in data["journal"]["2026-10-01"]], ["diner", "diner"])

    def test_rejects_invalid_requests(self):
        bad = [
            {"from": "2026-09-30", "to": "2026-09-30", "meal": "dejeuner"},   # même jour
            {"from": "2026-09-31", "to": "2026-10-01", "meal": "dejeuner"},   # date invalide
            {"from": "2026-09-30", "to": "2026-10-01", "meal": "brunch"},     # repas inconnu
            {"from": "2026-09-30", "to": "2026-10-01", "meal": "diner"},      # rien à copier
            {"from": "2026-09-29", "to": "2026-10-01", "meal": "dejeuner"},   # jour vide
            None,
        ]
        for body in bad:
            data = sample()
            with self.subTest(body=body), self.assertRaises(ValueError):
                route(data, body)
            self.assertNotIn("2026-10-01", data["journal"])


if __name__ == "__main__":
    unittest.main()
