"""Retention works only on temporary SQLite fixtures; production data is never used."""
from contextlib import closing
import importlib.util
from pathlib import Path
import sqlite3
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('retention', Path(__file__).resolve().parents[1] / 'scripts/record_retention.py')
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)


class RetentionTest(unittest.TestCase):
    def test_dry_run_backup_and_apply_preserve_other_state(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'data.db'
            now = 1800000000
            with closing(sqlite3.connect(path)) as db:
                db.execute('CREATE TABLE record(id INTEGER PRIMARY KEY,guid TEXT,episode_guid TEXT,time INTEGER,create_time INTEGER,playback_speed REAL)')
                db.execute('CREATE TABLE shared(value TEXT)'); db.execute("INSERT INTO shared VALUES ('keep')")
                for stamp in (now-200*86400, now-10*86400, 0, now*1000, None):
                    db.execute("INSERT INTO record(guid,episode_guid,time,create_time,playback_speed) VALUES ('season','episode',20,?,1)", (stamp,))
                db.commit()
            before = path.read_bytes()
            self.assertEqual(module.maintain(path, now=now)['eligible'], 1)
            self.assertEqual(path.read_bytes(), before)
            with self.assertRaises(ValueError): module.maintain(path, apply=True, now=now)
            with self.assertRaises(FileExistsError): module.maintain(path, apply=True, backup_dir=path, now=now)
            self.assertEqual(path.read_bytes(), before, 'A failed backup must not delete rows')
            result = module.maintain(path, apply=True, backup_dir=Path(root)/'backup', now=now)
            self.assertEqual(result['deleted'],1)
            with closing(sqlite3.connect(result['backup'])) as db: self.assertEqual(db.execute('SELECT count(*) FROM record').fetchone()[0],5)
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(db.execute('SELECT count(*) FROM record').fetchone()[0],4)
                self.assertEqual(db.execute('SELECT value FROM shared').fetchone()[0],'keep')
            self.assertEqual(module.maintain(path, apply=True, backup_dir=Path(root)/'backup', now=now)['deleted'],0)


if __name__ == '__main__': unittest.main()
