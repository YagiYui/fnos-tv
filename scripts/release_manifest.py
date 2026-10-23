"""Write a paired source/asset manifest without reading runtime data or credentials."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import subprocess


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()


def manifest(backend, frontend, dist):
    return {
        'backend_sha': git(backend, 'rev-parse', 'HEAD'),
        'frontend_sha': git(frontend, 'rev-parse', 'HEAD'),
        'backend_dirty': bool(git(backend, 'status', '--porcelain')),
        'frontend_dirty': bool(git(frontend, 'status', '--porcelain')),
        'python': platform.python_version(),
        'node': subprocess.check_output(['node', '--version'], text=True).strip(),
        'pnpm': json.loads((frontend / 'package.json').read_text(encoding='utf-8')).get('packageManager'),
        'migration_head': 'add_lookup_indexes',
        'assets': {str(file.relative_to(dist)).replace('\\', '/'): hashlib.sha256(file.read_bytes()).hexdigest()
                   for file in sorted(dist.rglob('*')) if file.is_file()},
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    for name in ('backend', 'frontend', 'dist'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    result = manifest(args.backend, args.frontend, args.dist)
    (args.backend / 'release-manifest.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
