"""Read-only live probes plus a static rollback rehearsal in a separate backup directory.

Run on the NAS host, never inside the production container. No account secrets are read.
"""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import time
from urllib.error import HTTPError
from urllib.parse import quote, unquote, urlencode
from urllib.request import Request, urlopen

from deploy_static import publish, verify


def command(*args):
    return subprocess.check_output(args, text=True).strip()


def containers():
    ids = command('docker', 'ps', '-aq').split()
    return sorted(command('docker', 'inspect', '--format',
                          '{{.Id}} {{.Name}} {{.State.StartedAt}}', *ids).splitlines())


def hashes(root):
    result = {}
    for file in sorted(root.rglob('*')):
        if file.is_symlink():
            raise ValueError('Refusing a snapshot containing symlinks')
        if file.is_file():
            result[file.relative_to(root).as_posix()] = hashlib.sha256(file.read_bytes()).hexdigest()
    return result


def fetch(url, headers=None):
    try:
        response = urlopen(Request(url, headers=headers or {}), timeout=20)
    except HTTPError as error:
        response = error
    with response:
        return response.status, response.read()


def signed_search_headers(source, params):
    # Read only public signing constants from the deployed client source.
    # The generated signature stays in memory and is never printed or saved.
    key = re.search(r'let api_key\s*=\s*["\']([^"\']+)', source).group(1)
    prefix = re.search(r'lt\s*=\s*\[\s*["\']([^"\']+)', source).group(1)
    nonce = str(100000 + secrets.randbelow(900000))
    timestamp = str(int(time.time() * 1000))
    canonical = urlencode(sorted(params.items()), quote_via=quote)
    payload_hash = hashlib.md5(unquote(canonical).encode()).hexdigest()
    signature = hashlib.md5('_'.join([prefix, '/v/api/v1/search/list', nonce,
                                      timestamp, payload_hash, key]).encode()).hexdigest()
    return {'authx': f'nonce={nonce}&timestamp={timestamp}&sign={signature}'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--service-root', type=Path, required=True)
    parser.add_argument('--previous-backup', type=Path, required=True)
    parser.add_argument('--release-manifest', type=Path, required=True)
    args = parser.parse_args()
    root = args.service_root.resolve()
    previous = args.previous_backup.resolve()
    info = json.loads(command('docker', 'inspect', 'fnos-tv'))[0]
    mounts = {entry['Destination']: entry['Source'] for entry in info['Mounts']}
    assert mounts['/app/dist'] == str(root / 'web')
    assert info['State']['Health']['Status'] == 'healthy'
    assert info['NetworkSettings']['Ports']['80/tcp'][0]['HostPort'] == '18005'
    assert previous.is_relative_to(root.parent / '.backups')
    assert (previous / 'web-before/index.html').is_file()
    assert (previous / 'frontend-before/src/views/Home.vue').is_file()
    fnos_url = next(value.split('=', 1)[1] for value in info['Config']['Env'] if value.startswith('FNOS_URL='))
    before = containers()
    manifest = json.loads(args.release_manifest.read_text())
    verify(root / 'web', manifest)
    stamp = datetime.now(timezone(timedelta(hours=8))).strftime('%Y%m%d-%H%M%S')
    backup = root.parent / '.backups' / ('fnos-tv-verify-search-' + stamp)
    backup.mkdir(mode=0o700, exist_ok=False)
    web_hashes = hashes(root / 'web')
    source_hashes = hashes(root / 'patches/frontend')
    shutil.copytree(root / 'web', backup / 'web-current')
    shutil.copytree(root / 'patches/frontend', backup / 'frontend-current')
    assert hashes(backup / 'web-current') == web_hashes
    assert hashes(backup / 'frontend-current') == source_hashes
    for file in (root.parent / 'docker-compose.yml', root / 'compose.service.yaml', root / 'patches/nginx.conf.template'):
        shutil.copy2(file, backup / file.name)
    (backup / 'web-manifest.json').write_text(json.dumps({'assets': web_hashes}, indent=2))
    (backup / 'source-manifest.json').write_text(json.dumps(source_hashes, indent=2))
    (backup / 'containers-before.json').write_text(json.dumps(before, indent=2))
    summary = {'backup': str(backup), 'previous_backup': str(previous), 'authenticated_browser_automation': False}
    (backup / 'verification.json').write_text(json.dumps({**summary, 'status': 'started'}, indent=2))

    base = 'http://127.0.0.1:18005'
    for name, expected in manifest['assets'].items():
        status, body = fetch(base + '/' + name)
        assert status == 200 and hashlib.sha256(body).hexdigest() == expected, 'Live asset differs: ' + name
    summary['http_assets_verified'] = len(manifest['assets'])
    status, body = fetch(base + '/api/videoConfig')
    assert status == 401 and json.loads(body)['code'] == -2
    summary['private_config_without_login'] = 401

    source = (root / 'patches/frontend/src/components/common.vue').read_text()
    probes = []
    for query, signed in [('fnos-live-verify-no-result', False), ('fnos-live-verify-no-result', True), ('中文 &?+# /', True)]:
        params = {'q': query}
        headers = signed_search_headers(source, params) if signed else {}
        suffix = '/v/api/v1/search/list?' + urlencode(params)
        status, body = fetch(base + '/fnos' + suffix, headers)
        direct_status, direct_body = fetch(fnos_url.rstrip('/') + suffix, headers)
        data, direct = json.loads(body), json.loads(direct_body)
        assert status == direct_status == 200 and data == direct, 'Proxy response differs from official service'
        expected_code = -2 if signed else 5000
        assert data.get('code') == expected_code, 'Unexpected search business status: ' + str(data.get('code'))
        probes.append({'signed': signed, 'special_characters': query.startswith('中文'),
                       'http_status': status, 'code': data['code'], 'matches_direct': True})
    summary['search_probes'] = probes

    # Exercise the same publication tool without touching the live target.
    rehearsal = backup / 'rehearsal'
    rehearsal.mkdir()
    target = rehearsal / 'web'
    current_manifest = {'assets': web_hashes}
    previous_manifest = {'assets': hashes(previous / 'web-before')}
    (backup / 'previous-web-manifest.json').write_text(json.dumps(previous_manifest, indent=2))
    verify(previous / 'web-before', previous_manifest)
    publish(backup / 'web-current', target, rehearsal / 'initial-empty', current_manifest)
    publish(previous / 'web-before', target, rehearsal / 'before-rollback', previous_manifest)
    verify(target, previous_manifest)
    assert (target / 'index.html').read_bytes() == (previous / 'web-before/index.html').read_bytes()
    publish(backup / 'web-current', target, rehearsal / 'before-restore', current_manifest)
    verify(target, current_manifest)
    assert (target / 'index.html').read_bytes() == (root / 'web/index.html').read_bytes()
    summary['rollback_rehearsal'] = 'current -> previous -> current, separate target'
    assert hashes(root / 'web') == web_hashes and hashes(root / 'patches/frontend') == source_hashes
    assert containers() == before
    command('docker', 'exec', 'fnos-tv', 'nginx', '-t')
    final = json.loads(command('docker', 'inspect', 'fnos-tv'))[0]
    assert final['State']['Health']['Status'] == 'healthy'
    summary.update(status='passed', production_files_unchanged=True, containers_unchanged=True, health='healthy')
    (backup / 'verification.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
