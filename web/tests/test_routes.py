"""Tests des routes HTTP principales (profils, journal, objectifs, aliments perso).

Un vrai serveur démarre sur 127.0.0.1 (port libre choisi par le système), avec
des données dans un dossier jetable.

    python -m unittest discover -s tests      (depuis le dossier web/)
"""

import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server  # noqa: E402

FOOD = {"id": "pomme", "name": "Pomme", "calories": 52, "proteins": 0.3, "carbs": 14, "fat": 0.2}


class RoutesTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        for name, value in [
            ("BASE_DIR", self.dir),
            ("PROFILES_FILE", os.path.join(self.dir, "profiles.json")),
            ("BACKUP_DIR", os.path.join(self.dir, "backups")),
        ]:
            patcher = mock.patch.object(server, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.alice = self.call("POST", "/api/users", {"name": "Alice"})[1]["created"]["id"]

    def call(self, method, path, body=None, user="alice"):
        """Renvoie (statut, JSON) ; `user=None` n'envoie pas d'en-tête X-User."""
        headers = {"Content-Type": "application/json"}
        if user:
            headers["X-User"] = user
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def add_entry(self, day="2026-10-01", grams=150, meal="dejeuner"):
        status, state = self.call("POST", "/api/journal", {"date": day, "meal": meal, "grams": grams, "food": FOOD})
        self.assertEqual(status, 200)
        return state["journal"][day][-1]

    # ------------------------------------------------------------ profils

    def test_profiles_are_created_and_listed(self):
        status, profiles = self.call("GET", "/api/users", user=None)
        self.assertEqual((status, profiles), (200, [{"id": "alice", "name": "Alice"}]))

    def test_duplicate_or_empty_profile_name_is_refused(self):
        self.assertEqual(self.call("POST", "/api/users", {"name": "alice"})[0], 400)
        self.assertEqual(self.call("POST", "/api/users", {"name": "   "})[0], 400)

    def test_unknown_profile_is_refused_everywhere(self):
        self.assertEqual(self.call("GET", "/api/state", user="mallory")[0], 400)
        self.assertEqual(self.call("GET", "/api/state", user=None)[0], 400)
        self.assertEqual(self.call("POST", "/api/journal", {"date": "2026-10-01"}, user="mallory")[0], 400)
        self.assertFalse(os.path.exists(server.data_file("mallory")))

    def test_profiles_do_not_share_their_journal(self):
        self.call("POST", "/api/users", {"name": "Bob"})
        self.add_entry()
        _, bob = self.call("GET", "/api/state", user="bob")
        self.assertEqual(bob["journal"], {})

    # ------------------------------------------------------------ journal

    def test_new_profile_starts_with_default_state(self):
        status, state = self.call("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(state, server.DEFAULT_DATA)

    def test_add_entry_copies_food_and_cleans_values(self):
        entry = self.add_entry(grams="150")
        self.assertEqual(entry["grams"], 150.0)
        self.assertEqual(entry["food"]["calories"], 52.0)
        self.assertEqual(entry["meal"], "dejeuner")
        # le journal est enregistré sur disque
        with open(server.data_file("alice"), encoding="utf-8") as f:
            self.assertEqual(len(json.load(f)["journal"]["2026-10-01"]), 1)

    def test_unknown_meal_falls_back_to_snack(self):
        self.assertEqual(self.add_entry(meal="minuit")["meal"], "collation")

    def test_add_entry_refuses_bad_date_quantity_and_nameless_food(self):
        for body in (
            {"date": "hier", "grams": 100, "food": FOOD},
            {"date": "2026-10-01", "grams": 0, "food": FOOD},
            {"date": "2026-10-01", "grams": "beaucoup", "food": FOOD},
            {"date": "2026-10-01", "grams": 100, "food": {"calories": 10}},
        ):
            with self.subTest(body=body):
                status, answer = self.call("POST", "/api/journal", body)
                self.assertEqual(status, 400)
                self.assertIn("error", answer)
        self.assertEqual(self.call("GET", "/api/state")[1]["journal"], {})

    def test_update_entry_changes_grams_and_meal(self):
        entry = self.add_entry()
        status, state = self.call("PUT", f"/api/journal/{entry['id']}", {"grams": 200, "meal": "diner"})
        self.assertEqual(status, 200)
        changed = state["journal"]["2026-10-01"][0]
        self.assertEqual((changed["grams"], changed["meal"]), (200.0, "diner"))

    def test_update_entry_refuses_invalid_grams_and_unknown_id(self):
        entry = self.add_entry()
        self.assertEqual(self.call("PUT", f"/api/journal/{entry['id']}", {"grams": -5})[0], 400)
        self.assertEqual(self.call("PUT", "/api/journal/inconnu", {"grams": 10})[0], 400)
        _, state = self.call("GET", "/api/state")
        self.assertEqual(state["journal"]["2026-10-01"][0]["grams"], 150.0)

    def test_deleting_the_last_entry_removes_the_day(self):
        first, second = self.add_entry(), self.add_entry()
        _, state = self.call("DELETE", f"/api/journal/{first['id']}")
        self.assertEqual([e["id"] for e in state["journal"]["2026-10-01"]], [second["id"]])
        _, state = self.call("DELETE", f"/api/journal/{second['id']}")
        self.assertNotIn("2026-10-01", state["journal"])
        self.assertEqual(self.call("DELETE", f"/api/journal/{second['id']}")[0], 400)

    def test_changing_a_custom_food_does_not_touch_the_journal(self):
        _, state = self.call("POST", "/api/foods", {**FOOD, "id": "perso1", "source": "manual"})
        self.call("POST", "/api/journal", {"date": "2026-10-01", "grams": 100, "meal": "dejeuner",
                                           "food": state["customFoods"][0]})
        self.call("PUT", "/api/foods/perso1", {**FOOD, "calories": 999})
        _, state = self.call("GET", "/api/state")
        self.assertEqual(state["customFoods"][0]["calories"], 999.0)
        self.assertEqual(state["journal"]["2026-10-01"][0]["food"]["calories"], 52.0)

    # ------------------------------------------------------------ objectifs

    def test_goals_are_rounded_and_at_least_one(self):
        status, state = self.call("PUT", "/api/goals", {"calories": 2150.6, "proteins": 0, "carbs": "abc"})
        self.assertEqual(status, 200)
        self.assertEqual(state["goals"], {"calories": 2151, "proteins": 1,
                                          "carbs": server.DEFAULT_DATA["goals"]["carbs"], "fat": 70})

    # ------------------------------------------------------------ aliments perso

    def test_custom_food_create_update_delete(self):
        _, state = self.call("POST", "/api/foods", {"name": "Houmous", "calories": 300, "category": "autre"})
        food = state["customFoods"][0]
        self.assertTrue(food["id"].startswith("manual_"))
        self.assertEqual(food["source"], "manual")

        _, state = self.call("PUT", f"/api/foods/{food['id']}", {"name": "Houmous maison", "calories": 250})
        self.assertEqual([(f["name"], f["calories"]) for f in state["customFoods"]], [("Houmous maison", 250.0)])

        _, state = self.call("DELETE", f"/api/foods/{food['id']}")
        self.assertEqual(state["customFoods"], [])

    def test_same_food_id_is_not_added_twice(self):
        self.call("POST", "/api/foods", {**FOOD, "source": "online"})
        _, state = self.call("POST", "/api/foods", {**FOOD, "source": "online"})
        self.assertEqual(len(state["customFoods"]), 1)

    def test_custom_food_needs_a_name_and_nutrients_are_not_negative(self):
        self.assertEqual(self.call("POST", "/api/foods", {"name": " ", "calories": 10})[0], 400)
        _, state = self.call("POST", "/api/foods", {"name": "Test", "calories": -40})
        self.assertEqual(state["customFoods"][0]["calories"], 0.0)

    # ------------------------------------------------------------ divers

    def test_unknown_routes_and_methods(self):
        self.assertEqual(self.call("POST", "/api/inconnu", {})[0], 400)
        self.assertEqual(self.call("PUT", "/api/foods", {"name": "x"})[0], 400)
        self.assertEqual(self.call("DELETE", "/api/goals")[0], 400)

    def test_backups_route_lists_copies_and_refuses_forged_dates(self):
        # profil créé aujourd'hui : on fabrique une copie à la main pour tester la route
        os.makedirs(server.BACKUP_DIR)
        with open(server.backup_file("alice", "2026-09-30"), "w", encoding="utf-8") as f:
            json.dump(server.DEFAULT_DATA, f)
        _, listing = self.call("GET", "/api/backups")
        self.assertEqual(listing, {"backups": ["2026-09-30"], "keep": server.BACKUP_KEEP, "undo": False})
        self.assertEqual(self.call("GET", "/api/backups?date=../../profiles")[0], 404)
        req = urllib.request.Request(f"{self.base}/api/backups?date=2026-09-30", headers={"X-User": "alice"})
        with urllib.request.urlopen(req) as resp:
            self.assertIn("attachment", resp.headers["Content-Disposition"])
            self.assertIn("journal", json.loads(resp.read()))

    # ------------------------------------------------------------ restauration

    def write_backup(self, day, content):
        os.makedirs(server.BACKUP_DIR, exist_ok=True)
        with open(server.backup_file("alice", day), "w", encoding="utf-8") as f:
            f.write(content if isinstance(content, str) else json.dumps(content))

    def read_data(self, path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def test_restore_replaces_data_and_keeps_the_current_state(self):
        self.write_backup("2026-09-30", {"goals": {"calories": 1500}, "journal": {}})
        entry = self.add_entry()
        status, state = self.call("POST", "/api/backups/restore", {"date": "2026-09-30"})
        self.assertEqual(status, 200)
        self.assertEqual(state["goals"], {"calories": 1500})
        self.assertEqual(state["journal"], {})
        self.assertEqual(state["customFoods"], [])  # clés manquantes complétées
        # l'état d'avant la restauration est gardé, et annoncé par la liste des copies
        undo = self.read_data(server.backup_file("alice", server.UNDO))
        self.assertEqual(undo["journal"]["2026-10-01"][0]["id"], entry["id"])
        self.assertTrue(self.call("GET", "/api/backups")[1]["undo"])
        self.assertEqual(self.call("GET", "/api/state")[1]["goals"], {"calories": 1500})

    def test_restoring_the_undo_copy_cancels_the_last_restore(self):
        self.write_backup("2026-09-30", {"goals": {"calories": 1500}, "journal": {}})
        entry = self.add_entry()
        self.call("POST", "/api/backups/restore", {"date": "2026-09-30"})
        status, state = self.call("POST", "/api/backups/restore", {"date": server.UNDO})
        self.assertEqual(status, 200)
        self.assertEqual(state["journal"]["2026-10-01"][0]["id"], entry["id"])
        # et l'annulation peut elle-même être annulée
        self.assertEqual(self.read_data(server.backup_file("alice", server.UNDO))["goals"], {"calories": 1500})

    def test_restore_refuses_unknown_forged_or_unreadable_copies(self):
        self.add_entry()
        before = self.read_data(server.data_file("alice"))
        self.write_backup("2026-09-29", "{pas du json")
        self.write_backup("2026-09-28", '["une liste"]')
        for day in ["2026-01-01", "../../profiles", "", server.UNDO, "2026-09-29", "2026-09-28"]:
            status, body = self.call("POST", "/api/backups/restore", {"date": day})
            self.assertEqual(status, 400, day)
            self.assertIn("error", body)
        self.assertEqual(self.call("POST", "/api/backups/restore", {"date": "2026-09-29"}, user="mallory")[0], 400)
        self.assertEqual(self.read_data(server.data_file("alice")), before)
        self.assertFalse(os.path.exists(server.backup_file("alice", server.UNDO)))

    def test_export_route_returns_csv_and_refuses_unknown_kind(self):
        self.add_entry()
        req = urllib.request.Request(f"{self.base}/api/export?type=jours", headers={"X-User": "alice"})
        with urllib.request.urlopen(req) as resp:
            body = resp.read()
            self.assertTrue(resp.headers["Content-Type"].startswith("text/csv"))
        self.assertTrue(body.startswith(b"\xef\xbb\xbf"))
        self.assertIn(b"2026-10-01", body)
        self.assertEqual(self.call("GET", "/api/export?type=pdf")[0], 400)


if __name__ == "__main__":
    unittest.main()
