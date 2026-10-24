"""Opt-in retention of auxiliary skip-learning rows, never official playback history.

No scheduled cleanup is installed. Default invocation is read-only; --apply requires
an explicit backup directory. SQLite online backup runs while other writers are
blocked, before the same transaction deletes eligible rows.
"""
from contextlib import closing
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import time


def maintain(database, days=180, *, apply=False, backup_dir=None, now=None):
    if type(days) is not int or not 30 <= days <= 3650:
        raise ValueError('保留天数必须为 30–3650')
    path = Path(database).resolve(strict=True)
    if apply and not backup_dir:
        raise ValueError('执行清理必须指定备份目录')
    cutoff = int(time.time() if now is None else now) - days * 86400
    predicate = 'typeof(create_time) IN (\'integer\',\'real\') AND create_time>=946684800 AND create_time<?'
    with closing(sqlite3.connect(path.as_uri() + ('?mode=rw' if apply else '?mode=ro'), uri=True, timeout=5)) as connection:
        if not apply:
            connection.execute('PRAGMA query_only=ON')
        columns = {row[1] for row in connection.execute('PRAGMA table_info(record)')}
        if not {'id', 'guid', 'episode_guid', 'time', 'create_time', 'playback_speed'} <= columns:
            raise ValueError('目标不是受支持的辅助记录数据库')
        if connection.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise ValueError('数据库检查失败，拒绝清理')
        if apply:
            connection.execute('BEGIN IMMEDIATE')
        total = connection.execute('SELECT count(*) FROM record').fetchone()[0]
        eligible = connection.execute('SELECT count(*) FROM record WHERE ' + predicate, (cutoff,)).fetchone()[0]
        result = {'dry_run': not apply, 'keep_days': days, 'cutoff_utc': datetime.fromtimestamp(cutoff, timezone.utc).isoformat(), 'total': total, 'eligible': eligible, 'deleted': 0}
        if apply and eligible:
            folder = Path(backup_dir).resolve()
            folder.mkdir(parents=True, exist_ok=True, mode=0o700)
            backup = folder / ('aux-record-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.sqlite3')
            descriptor = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
            try:
                with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as source, closing(sqlite3.connect(backup)) as target:
                    source.backup(target)
                    if target.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                        raise ValueError('备份验证失败')
                result['deleted'] = connection.execute('DELETE FROM record WHERE ' + predicate, (cutoff,)).rowcount
                result['backup'] = str(backup)
            except Exception:
                connection.rollback()
                raise
        connection.commit()
        return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database')
    parser.add_argument('--days', type=int, default=180)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--backup-dir')
    args = parser.parse_args()
    print(json.dumps(maintain(args.database, args.days, apply=args.apply, backup_dir=args.backup_dir), ensure_ascii=False))
