"""Publish/rollback rehearsal against temporary static roots and SQLite databases."""
from contextlib import closing
import hashlib
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.deploy_static import publish, backup_database


def manifest(root):
    return {'assets': {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                       for p in root.rglob('*') if p.is_file()}}


class ReleaseTest(unittest.TestCase):
    def test_publish_rollback_and_corruption(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old, new, live = [root / name for name in ('old', 'new', 'live')]
            for folder, content in ((old, 'old'), (new, 'new')):
                (folder / 'assets').mkdir(parents=True)
                (folder / 'index.html').write_text(content, encoding='utf-8')
                (folder / 'assets' / (content + '.js')).write_text(content, encoding='utf-8')
            publish(old, live, root / 'empty-backup', manifest(old))
            publish(new, live, root / 'old-backup', manifest(new))
            self.assertEqual((live / 'index.html').read_text(), 'new')
            self.assertTrue((live / 'assets/old.js').exists())
            publish(root / 'old-backup', live, root / 'new-backup', manifest(root / 'old-backup'))
            self.assertEqual((live / 'index.html').read_text(), 'old')
            broken = manifest(new)
            (new / 'assets/new.js').write_text('corrupt', encoding='utf-8')
            with self.assertRaises(ValueError):
                publish(new, live, root / 'failed-backup', broken)
            self.assertEqual((live / 'index.html').read_text(), 'old')
            self.assertFalse((root / 'failed-backup').exists())

    def test_consistent_database_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            source, backup = Path(directory) / 'live.db', Path(directory) / 'snapshot.db'
            with closing(sqlite3.connect(source)) as connection:
                connection.execute('create table sample(value integer)')
                connection.execute('insert into sample values(42)')
                connection.commit()
                backup_database(source, backup)
                connection.execute('delete from sample')
                connection.commit()
            with closing(sqlite3.connect(backup)) as restored:
                self.assertEqual(restored.execute('select * from sample').fetchall(), [(42,)])
            with self.assertRaises(FileExistsError):
                backup_database(source, backup)


if __name__ == '__main__':
    unittest.main()
