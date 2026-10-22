"""Publish verified static assets before atomically switching the SPA entrypoint."""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import re
from urllib.parse import urlsplit, unquote


def verify(source, manifest):
    source = Path(source).resolve()
    if not (source / 'index.html').is_file():
        raise ValueError('Missing index.html')
    for name, digest in manifest['assets'].items():
        file = source / name
        if file.is_symlink() or not file.resolve().is_relative_to(source) or not file.is_file():
            raise ValueError('Invalid asset path')
        if hashlib.sha256(file.read_bytes()).hexdigest() != digest:
            raise ValueError('Asset hash mismatch: ' + name)
    if 'index.html' not in manifest['assets']:
        raise ValueError('Manifest does not include index.html')
    for reference in re.findall(r'(?:src|href)=["\']([^"\']+)["\']', (source / 'index.html').read_text(encoding='utf-8')):
        parsed = urlsplit(reference)
        if parsed.scheme or parsed.netloc or not parsed.path:
            continue
        name = unquote(parsed.path).lstrip('/')
        if name not in manifest['assets']:
            raise ValueError('Entrypoint references an unverified asset')
    return source


def atomic_copy(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix='.fnos-publish-', dir=destination.parent)
    try:
        with os.fdopen(descriptor, 'wb') as output, source.open('rb') as incoming:
            shutil.copyfileobj(incoming, output)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(name, 0o644)
        os.replace(name, destination)
    finally:
        Path(name).unlink(missing_ok=True)


def publish(source, target, backup, manifest):
    source = verify(source, manifest)
    target, backup = Path(target).resolve(), Path(backup).resolve()
    if target.is_relative_to(source) or source.is_relative_to(target):
        raise ValueError('Source and target must be separate directories')
    if backup.is_relative_to(target) or target.is_relative_to(backup) or backup.exists():
        raise ValueError('Backup must be a new directory outside the target')
    # Reject symlinks before backups or writes so a mounted target cannot escape its root.
    if target.exists() and any(path.is_symlink() for path in target.rglob('*')):
        raise ValueError('Target contains symlinks')
    if target.exists():
        shutil.copytree(target, backup)
    else:
        backup.mkdir(parents=True)
    target.mkdir(parents=True, exist_ok=True)
    for name in manifest['assets']:
        if name != 'index.html':
            atomic_copy(source / name, target / name)
    atomic_copy(source / 'index.html', target / 'index.html')
    # Old hashed resources are intentionally retained for already-open pages.


def backup_database(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents accidentally replacing an earlier backup.
    descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    try:
        with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as incoming, \
                closing(sqlite3.connect(destination)) as outgoing:
            incoming.backup(outgoing)
    except Exception:
        destination.unlink(missing_ok=True)
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--target', type=Path, required=True)
    parser.add_argument('--backup', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    args = parser.parse_args()
    publish(args.source, args.target, args.backup, json.loads(args.manifest.read_text(encoding='utf-8')))
