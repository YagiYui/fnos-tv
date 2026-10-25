"""Exercise the real Alembic chain against disposable databases only."""
from pathlib import Path
from contextlib import closing
import sqlite3
import sys
import tempfile
import unittest

from alembic import command
from alembic.config import Config

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class MigrationTest(unittest.TestCase):
    def upgrade(self, path):
        config = Config(str(ROOT / 'alembic.ini'))
        config.set_main_option('script_location', str(ROOT / 'alembic'))
        config.set_main_option('sqlalchemy.url', 'sqlite:///' + path.as_posix())
        command.upgrade(config, 'head')

    def test_fresh_and_repeated_upgrade(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fresh.db'
            self.upgrade(path)
            self.upgrade(path)
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(connection.execute('select version_num from alembic_version').fetchone()[0], 'create_initial_tables')
                self.assertIn('parent_guid', [row[1] for row in connection.execute('pragma table_info(video_config_url)')])

    def test_legacy_rows_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'legacy.db'
            with closing(sqlite3.connect(path)) as connection:
                connection.executescript('''
                    create table record(id integer primary key, guid text, episode_guid text, time integer,
                                        create_time integer, playback_speed integer);
                    insert into record values(1,'season','episode',33,100,1);
                    create table video_config_list(id integer primary key, guid text, startTime integer, endTime integer);
                    insert into video_config_list values(1,'season',10,20);
                    create table video_config_url(id integer primary key, guid text, url text);
                    insert into video_config_url values(1,'episode','https://v.qq.com/keep');
                ''')
            self.upgrade(path)
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(connection.execute('select time from record').fetchall(), [(33,)])
                self.assertEqual(connection.execute('select startTime,endTime from video_config_list').fetchall(), [(10, 20)])
                self.assertEqual(connection.execute('select url,parent_guid from video_config_url').fetchall(), [('https://v.qq.com/keep', None)])


if __name__ == '__main__':
    unittest.main()
