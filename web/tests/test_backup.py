"""Tests de la sauvegarde automatique quotidienne (dossier backups/).

    python -m unittest discover -s tests      (depuis le dossier web/)
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server  # noqa: E402


class BackupTest(unittest.TestCase):
    def setUp(self):
        # Les fichiers de données et de sauvegarde vont dans un dossier jetable
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        for name, value in [("BASE_DIR", self.dir), ("BACKUP_DIR", os.path.join(self.dir, "backups"))]:
            patcher = mock.patch.object(server, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def write(self, user, calories):
        with open(server.data_file(user), "w", encoding="utf-8") as f:
            json.dump({"goals": {"calories": calories}}, f)

    def backup_content(self, user, day):
        with open(server.backup_file(user, day), encoding="utf-8") as f:
            return json.load(f)["goals"]["calories"]

    def test_first_save_of_the_day_keeps_the_previous_state(self):
        self.write("alice", 1800)
        with mock.patch.object(server.time, "strftime", return_value="2026-10-02"):
            server.save("alice", {"goals": {"calories": 2000}})
            server.save("alice", {"goals": {"calories": 2200}})  # même jour : pas de nouvelle copie
        self.assertEqual(server.list_backups("alice"), ["2026-10-02"])
        self.assertEqual(self.backup_content("alice", "2026-10-02"), 1800)

    def test_nothing_to_back_up_for_a_new_profile(self):
        server.save("bob", server.DEFAULT_DATA)
        self.assertEqual(server.list_backups("bob"), [])

    def test_keeps_only_the_most_recent_backups(self):
        self.write("alice", 1)
        days = [f"2026-09-{d:02d}" for d in range(1, 21)]
        for day in days:
            server.backup_daily("alice", today=day)
        self.assertEqual(server.list_backups("alice"), sorted(days, reverse=True)[:server.BACKUP_KEEP])

    def test_profiles_with_similar_ids_are_kept_apart(self):
        self.write("jean", 1)
        self.write("jean-2", 2)
        for d in range(1, 21):
            server.backup_daily("jean-2", today=f"2026-09-{d:02d}")
        server.backup_daily("jean", today="2026-08-01")
        self.assertEqual(server.list_backups("jean"), ["2026-08-01"])  # pas effacée par la rotation de jean-2
        self.assertEqual(len(server.list_backups("jean-2")), server.BACKUP_KEEP)

    def test_failed_backup_does_not_block_saving(self):
        self.write("alice", 1800)
        with mock.patch.object(server.os, "makedirs", side_effect=OSError("disque plein")), \
                mock.patch("sys.stderr"):
            server.save("alice", {"goals": {"calories": 2000}})
        self.assertEqual(server.load("alice")["goals"]["calories"], 2000)


if __name__ == "__main__":
    unittest.main()
