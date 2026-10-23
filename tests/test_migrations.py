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
    def config(self, path):
        config = Config(str(ROOT / 'alembic.ini'))
        config.set_main_option('script_location', str(ROOT / 'alembic'))
        config.set_main_option('sqlalchemy.url', 'sqlite:///' + path.as_posix())
        return config

    def upgrade(self, path):
        config = self.config(path)
        command.upgrade(config, 'head')

    def assert_lookup_indexes(self, connection):
        for table, column in [('record', 'guid'), ('video_config_list', 'guid'),
                              ('video_config_url', 'guid'), ('video_config_url', 'parent_guid')]:
            indexes = {row[1]: row for row in connection.execute('pragma index_list(' + table + ')')}
            name = 'ix_' + table + '_' + column
            self.assertIn(name, indexes)
            self.assertEqual(indexes[name][2], 0, 'Indexes must not impose new uniqueness constraints')
            plan = connection.execute('explain query plan select * from ' + table + ' where ' + column + '=?', ('fixture',)).fetchall()
            self.assertTrue(any(name in row[3] and 'SEARCH' in row[3] for row in plan), plan)

    def test_fresh_and_repeated_upgrade(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fresh.db'
            self.upgrade(path)
            self.upgrade(path)
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(connection.execute('select version_num from alembic_version').fetchone()[0], 'add_lookup_indexes')
                self.assertIn('parent_guid', [row[1] for row in connection.execute('pragma table_info(video_config_url)')])
                self.assert_lookup_indexes(connection)

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
                self.assert_lookup_indexes(connection)

    def test_existing_head_and_index_only_rollback(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'existing-head.db'
            self.upgrade(path)
            command.downgrade(self.config(path), 'create_initial_tables')
            with closing(sqlite3.connect(path)) as connection:
                connection.execute("insert into record values(1,'season','episode',33,100,1)")
                connection.execute("insert into record values(2,'season','episode',33,100,1)")
                connection.commit()
                self.assertEqual(connection.execute('pragma index_list(record)').fetchall(), [])
            self.upgrade(path)
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(connection.execute('select count(*) from record').fetchone()[0], 2)
                self.assert_lookup_indexes(connection)
            command.downgrade(self.config(path), 'create_initial_tables')
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(connection.execute('select count(*) from record').fetchone()[0], 2)
                self.assertEqual(connection.execute('select version_num from alembic_version').fetchone()[0], 'create_initial_tables')
            self.upgrade(path)


if __name__ == '__main__':
    unittest.main()
