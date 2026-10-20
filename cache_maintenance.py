"""Prune only inactive subtitle cache entries; never media files or account data."""
import fcntl
import json
from pathlib import Path
import re
import shutil
import time
from apps.danmu.ass_support import job_running


def prune(root=Path('/app/data/subtitle-cache'), now=None, max_bytes=2 * 1024**3, ttl=30 * 86400, grace=3 * 86400):
    now = time.time() if now is None else now
    root.mkdir(parents=True, exist_ok=True)
    with (root / '.maintenance.lock').open('a') as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: return {'busy': True}
        candidates = []
        total = 0
        for entry in root.iterdir():
            if entry.is_symlink() or not entry.is_dir() or not re.fullmatch('[a-f0-9]{64}', entry.name): continue
            size = sum(file.stat().st_size for file in entry.rglob('*') if not file.is_symlink() and file.is_file())
            total += size
            if now - entry.stat().st_mtime < grace: continue
            job = entry / 'job.json'
            try:
                if job.exists() and job_running(json.loads(job.read_text())): continue
            except (OSError, ValueError): continue
            candidates.append((entry.stat().st_mtime, entry, size))
        removed = 0
        for modified, entry, size in sorted(candidates):
            if now - modified < ttl and total <= max_bytes: continue
            if not entry.resolve().is_relative_to(root.resolve()): raise ValueError('Cache path escaped root')
            shutil.rmtree(entry)
            total -= size
            removed += 1
        return {'removed': removed, 'remaining_bytes': total, 'soft_quota_exceeded': total > max_bytes}


if __name__ == '__main__':
    while True:
        try: print(json.dumps(prune()), flush=True)
        except Exception as error: print('Subtitle cache maintenance: ' + type(error).__name__, flush=True)
        time.sleep(3600)
