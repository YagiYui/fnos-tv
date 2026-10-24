"""Persistent source registry and bounded Dandanplay-v2 adapter. No runtime scripts."""
from contextlib import contextmanager
import copy
import hashlib
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import ssl
import threading
import time
from urllib.parse import quote, urlencode, urlsplit

BUILTINS = (
    ('bilibili', 'B站', 'bilibili.com'), ('iqiyi', '爱奇艺', 'iqiyi.com'),
    ('tencent', '腾讯视频', 'v.qq.com'), ('youku', '优酷', 'youku.com'),
    ('mgtv', '芒果TV', 'mgtv.com'), ('sohu', '搜狐', 'tv.sohu.com'),
)
SEARCHES = (('360', '360影视', 'SearchBy360'), ('douban', '豆瓣', 'DoubanSearch'))
DATA = Path(__file__).resolve().parents[2] / 'data'
_lock = threading.RLock()


class SourceDisabled(ValueError):
    pass


def guard_source_request(url):
    host = (urlsplit(url).hostname or '').lower()
    domains = {'bilibili': ('bilibili.com', 'hdslb.com'), 'iqiyi': ('iqiyi.com', 'qiyi.com'),
        'tencent': ('qq.com',), 'youku': ('youku.com',), 'mgtv': ('mgtv.com', 'hunantv.com'),
        'sohu': ('sohu.com',), '360': ('360kan.com',), 'douban': ('douban.com',)}
    identifier = next((key for key, values in domains.items() if any(host == domain or host.endswith('.' + domain) for domain in values)), None)
    if identifier and identifier not in registry.enabled('providers' if identifier in [row[0] for row in BUILTINS] else 'searches'):
        raise SourceDisabled('此弹幕来源或搜索入口已被管理员禁用')


def defaults():
    return {'schema': 1, 'revision': 0, 'fallback': True, 'default_source': 'auto',
            'providers': [{'id': key, 'enabled': True} for key, _, _ in BUILTINS],
            'searches': [{'id': key, 'enabled': True} for key, _, _ in SEARCHES], 'custom': []}


def short_text(value, maximum=80):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or re.search(r'[\x00-\x1f\x7f]', value):
        raise ValueError('名称或标识格式无效')
    return value.strip()


def service_url(value):
    value = short_text(value, 500).rstrip('/')
    parsed = urlsplit(value)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('服务地址仅支持不含账号、查询和片段的 HTTP／HTTPS 地址')
    if any(part in ('.', '..') for part in parsed.path.split('/')) or '\\' in value or '%' in parsed.path:
        raise ValueError('服务地址路径无效')
    if not 1 <= (parsed.port or (443 if parsed.scheme == 'https' else 80)) <= 65535:
        raise ValueError('服务端口无效')
    return value


class SourceRegistry:
    def __init__(self, directory=DATA):
        self.directory = Path(directory)
        self.path = self.directory / 'danmaku-sources.json'

    def read(self):
        if not self.path.exists():
            return defaults()
        try:
            result = json.loads(self.path.read_text(encoding='utf-8'))
            if result.get('schema') != 1 or not isinstance(result.get('revision'), int):
                raise ValueError()
            return result
        except (ValueError, OSError):
            # Corrupt configuration must not silently re-enable disabled providers.
            raise ValueError('弹幕来源配置不可读，请管理员恢复备份') from None

    @contextmanager
    def locked(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        with _lock, (self.directory / '.danmaku-sources.lock').open('a+b') as handle:
            if os.name != 'nt':
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                if os.name != 'nt':
                    fcntl.flock(handle, fcntl.LOCK_UN)

    def save(self, value):
        if not isinstance(value, dict):
            raise ValueError('来源配置必须为对象')
        with self.locked():
            previous = self.read()
            if value.get('revision') != previous['revision']:
                raise ValueError('来源配置已被其他页面修改，请重新加载')
            result = defaults()
            result['revision'] = previous['revision'] + 1
            if not isinstance(value.get('fallback'), bool):
                raise ValueError('自动回退必须为开关')
            result['fallback'] = value['fallback']
            for key, known in [('providers', BUILTINS), ('searches', SEARCHES)]:
                rows = value.get(key)
                ids = [row.get('id') for row in rows] if isinstance(rows, list) and all(isinstance(row, dict) for row in rows) else []
                if sorted(ids) != sorted(row[0] for row in known) or any(type(row.get('enabled')) is not bool for row in rows):
                    raise ValueError('内置来源必须完整且不可重复')
                result[key] = [{'id': row['id'], 'enabled': row['enabled']} for row in rows]
            custom = value.get('custom', [])
            if not isinstance(custom, list) or len(custom) > 8:
                raise ValueError('自定义来源最多八个')
            seen = set()
            for item in custom:
                if not isinstance(item, dict) or not re.fullmatch(r'custom-[a-zA-Z0-9_-]{1,40}', str(item.get('id', ''))):
                    raise ValueError('自定义来源标识无效')
                if item['id'] in seen:
                    raise ValueError('来源标识重复')
                seen.add(item['id'])
                if item.get('protocol') != 'dandanplay-v2':
                    raise ValueError('当前仅支持 Dandanplay v2 兼容协议')
                if type(item.get('enabled')) is not bool or type(item.get('allow_private', False)) is not bool:
                    raise ValueError('来源开关无效')
                timeout = item.get('timeout', 10)
                if type(timeout) is not int or not 2 <= timeout <= 30:
                    raise ValueError('超时范围为 2–30 秒')
                old = next((row for row in previous['custom'] if row['id'] == item['id']), {})
                secret = item.get('token', '')
                if secret == '':
                    secret = old.get('token', '')
                if item.get('clear_token') is True:
                    secret = ''
                if not isinstance(secret, str) or len(secret) > 1000 or re.search(r'[\x00-\x20\x7f]', secret):
                    raise ValueError('鉴权字段无效')
                mode = item.get('auth_mode', 'bearer')
                if mode not in ('bearer', 'path'):
                    raise ValueError('仅支持 Bearer 或独立保存的路径令牌')
                result['custom'].append({'id': item['id'], 'name': short_text(item.get('name')),
                    'url': service_url(item.get('url')), 'protocol': 'dandanplay-v2', 'enabled': item['enabled'],
                    'allow_private': item.get('allow_private', False), 'timeout': timeout, 'token': secret, 'auth_mode': mode})
            preferred = value.get('default_source', 'auto')
            if preferred not in ['auto'] + [row['id'] for row in result['providers'] + result['custom'] if row['enabled']]:
                raise ValueError('服务默认来源已禁用或不存在，请重新选择')
            result['default_source'] = preferred
            temporary = self.path.with_suffix('.tmp')
            with temporary.open('w', encoding='utf-8') as handle:
                os.chmod(temporary, 0o600)
                json.dump(result, handle, ensure_ascii=False, separators=(',', ':'))
                handle.flush(); os.fsync(handle.fileno())
            temporary.replace(self.path)
            return result

    def public(self, admin=False):
        value = copy.deepcopy(self.read())
        names = {key: name for key, name, _ in BUILTINS + SEARCHES}
        for key in ('providers', 'searches'):
            for row in value[key]:
                row['name'] = names[row['id']]
        for row in value['custom']:
            row['has_token'] = bool(row.pop('token', ''))
            if not admin:
                row.pop('url', None); row.pop('allow_private', None)
        return value

    def enabled(self, kind):
        return [row['id'] for row in self.read()[kind] if row['enabled']]

    def provider_id(self, url):
        parsed = urlsplit(url)
        if parsed.scheme == 'danmaku':
            return parsed.netloc
        host = (parsed.hostname or '').lower()
        return next((key for key, _, domain in BUILTINS if host == domain or host.endswith('.' + domain)), None)

    def allowed(self, url):
        key = self.provider_id(url)
        return key in self.enabled('providers') or key in self.enabled('custom')

    def ordered_urls(self, urls):
        order = self.enabled('providers') + self.enabled('custom')
        return sorted(dict.fromkeys(url for url in urls if self.allowed(url)), key=lambda url: order.index(self.provider_id(url)))

    def custom(self, key):
        row = next((row for row in self.read()['custom'] if row['id'] == key and row['enabled']), None)
        if not row:
            raise ValueError('自定义来源已删除或禁用')
        return row


registry = SourceRegistry()


class UserState:
    """A separate account-owned store; legacy shared media configuration is not reassigned."""
    def __init__(self, directory=DATA):
        self.directory = Path(directory)

    @contextmanager
    def connect(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / 'user-state.sqlite3'
        connection = sqlite3.connect(path, timeout=5)
        os.chmod(path, 0o600)
        try:
            connection.execute('CREATE TABLE IF NOT EXISTS user_state (owner TEXT NOT NULL, kind TEXT NOT NULL, scope TEXT NOT NULL, value TEXT NOT NULL, updated INTEGER NOT NULL, PRIMARY KEY(owner,kind,scope))')
            yield connection
            connection.commit()
        except Exception:
            connection.rollback(); raise
        finally:
            connection.close()

    def get(self, owner, kind, scope):
        with self.connect() as connection:
            row = connection.execute('SELECT value FROM user_state WHERE owner=? AND kind=? AND scope=?', (owner, kind, scope)).fetchone()
            return json.loads(row[0]) if row else None

    def put(self, owner, kind, scope, value):
        with self.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            if value is None:
                connection.execute('DELETE FROM user_state WHERE owner=? AND kind=? AND scope=?', (owner, kind, scope))
                return
            if connection.execute('SELECT count(*) FROM user_state WHERE owner=?', (owner,)).fetchone()[0] >= 5000 and connection.execute('SELECT 1 FROM user_state WHERE owner=? AND kind=? AND scope=?', (owner, kind, scope)).fetchone() is None:
                raise ValueError('个人配置超过上限，请先清理不再使用的条目')
            payload = json.dumps(value, ensure_ascii=False)
            size = len(payload.encode('utf-8'))
            if size > 128 * 1024:
                raise ValueError('单项个人配置超过 128 KB，请减少映射条目')
            used = connection.execute('SELECT COALESCE(sum(length(CAST(value AS BLOB))),0) FROM user_state WHERE owner=? AND NOT (kind=? AND scope=?)', (owner, kind, scope)).fetchone()[0]
            if used + size > 16 * 1024 * 1024:
                raise ValueError('账号个人配置超过 16 MB，请先清理不再使用的条目')
            connection.execute('INSERT INTO user_state VALUES (?,?,?,?,?) ON CONFLICT(owner,kind,scope) DO UPDATE SET value=excluded.value,updated=excluded.updated',
                (owner, kind, scope, payload, int(time.time())))


user_state = UserState()


def selection_for(owner, episode, season):
    for scope in ('episode:' + (episode or ''), 'season:' + (season or ''), 'user'):
        value = user_state.get(owner, 'danmaku', scope)
        if value is not None:
            return {**value, 'scope': scope}
    config = registry.read()
    return {'source_id': config.get('default_source', 'auto'), 'fallback': config['fallback'], 'scope': 'service'}


def media_access(token, guid):
    from apps.danmu.ass_support import signed_headers
    from Config import fnos_url
    import requests
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', str(guid)):
        raise ValueError('缺少有效媒体标识')
    path = '/api/v1/item/' + guid
    with requests.get(fnos_url.rstrip('/') + '/v' + path, headers=signed_headers(token, 'GET', path), timeout=(5, 15), allow_redirects=False) as response:
        result = response.json()
        if response.status_code != 200 or result.get('code') != 0 or not isinstance(result.get('data'), dict) or result['data'].get('guid') != guid:
            raise PermissionError('无权访问此媒体')
        return result['data']


def source_url(value):
    if not isinstance(value, str) or len(value) > 4096:
        raise ValueError('来源链接无效')
    if value.startswith('danmaku://'):
        parse_reference(value, 'episode')
    else:
        parsed = urlsplit(value)
        if parsed.scheme not in ('http', 'https') or parsed.username or parsed.password or parsed.port not in (None, 80, 443) or not registry.allowed(value):
            raise ValueError('来源链接不属于已启用的平台')
    return value


def personalized_urls(urls, episode, season, episode_number, title):
    from flask import request
    def base():
        value = urls() if callable(urls) else urls
        return {key: registry.ordered_urls(rows) for key, rows in value.items()}
    token = request.headers.get('Authorization', '')
    if not token:
        config = registry.read()
        choice = {'source_id': config.get('default_source', 'auto'), 'fallback': config['fallback']}
        if choice['source_id'].startswith('custom-'):
            raise PermissionError('自定义来源需要登录')
    else:
        account = authenticated_user(token)
        choice = selection_for(account['owner'], episode, season)
    explicit = choice.get('url') or choice.get('episodes', {}).get(str(episode_number))
    if choice.get('episodes') and not explicit:
        raise ValueError('季度来源没有当前集映射')
    if explicit:
        source_url(explicit)
        return {str(episode_number or 1): [explicit]}, False
    source = choice.get('source_id', 'auto')
    if source == 'auto':
        source = registry.read().get('default_source', 'auto')
    if source == 'auto':
        return base(), choice.get('fallback', True)
    if source not in registry.enabled('providers') + registry.enabled('custom'):
        raise ValueError('个人首选来源已禁用')
    if source.startswith('custom-'):
        # Require a unique exact title and an explicit episode number, never select the first fuzzy match.
        preferred = []
        try:
            candidates = [row for row in custom_search(source, title or '') if row['title'].strip() == (title or '').strip()]
            if len(candidates) == 1:
                matches = [row for row in custom_episodes(candidates[0]['url']) if row['number'] == str(episode_number or 1)]
                if len(matches) == 1:
                    preferred = [matches[0]['url']]
            record_source_result(source, 'ok' if preferred else 'empty')
        except ValueError:
            record_source_result(source, 'failed')
            if not choice.get('fallback', True):
                raise
        if preferred:
            key = str(episode_number or 1)
            return {key: preferred + (base().get(key, []) if choice.get('fallback', True) else [])}, choice.get('fallback', True)
        return (base() if choice.get('fallback', True) else {}), choice.get('fallback', True)
    result = {}
    for key, addresses in base().items():
        addresses = registry.ordered_urls(addresses)
        preferred = [address for address in addresses if registry.provider_id(address) == source]
        result[key] = preferred + ([address for address in addresses if address not in preferred] if choice.get('fallback', True) else [])
    return result, choice.get('fallback', True)


def record_source_result(identifier, outcome):
    if identifier and outcome in ('ok', 'empty', 'failed'):
        try:
            user_state.put('service', 'source-status', identifier, {'outcome': outcome, 'at': int(time.time())})
        except (OSError, sqlite3.Error):
            pass  # Diagnostics may never interrupt playback.


def resolved_addresses(host, port, allow_private=False):
    addresses = list(dict.fromkeys(row[4][0] for row in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)))
    if not addresses:
        raise ValueError('服务地址无法解析')
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if ip.version == 6 and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved or (not ip.is_global and not allow_private):
            raise ValueError('服务地址不是允许的公网地址；内网地址需管理员显式允许')
    return addresses


def custom_json(config, path, params=None):
    parsed = urlsplit(config['url'])
    port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    addresses = resolved_addresses(parsed.hostname, port, config.get('allow_private', False))
    # Pin the verified IP for this request. Preserve original Host/SNI, ignore environment proxies.
    connection = http.client.HTTPConnection(parsed.hostname, port, timeout=config['timeout'])
    prefix = parsed.path.rstrip('/')
    if config.get('auth_mode') == 'path' and config.get('token'):
        prefix += '/' + quote(config['token'], safe='')
    target = prefix + '/api/v2/' + path
    if params:
        target += '?' + urlencode(params)
    headers = {'Accept': 'application/json', 'User-Agent': 'FNOS-TV-Danmaku/1'}
    if config.get('token') and config.get('auth_mode', 'bearer') == 'bearer':
        headers['Authorization'] = 'Bearer ' + config['token']
    try:
        started = time.monotonic()
        registry.custom(config['id'])  # Recheck enablement after DNS resolution, before opening a connection.
        connection.sock = socket.create_connection((addresses[0], port), timeout=config['timeout'])
        if parsed.scheme == 'https':
            connection.sock = ssl.create_default_context().wrap_socket(connection.sock, server_hostname=parsed.hostname)
        connection.request('GET', target, headers=headers)
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError('自定义服务返回非成功状态（不跟随重定向）')
        chunks, total = [], 0
        while total <= 16 * 1024 * 1024:
            if response.fp is None:
                break
            remaining = config['timeout'] - (time.monotonic() - started)
            if remaining <= 0:
                raise ValueError('自定义来源请求超时')
            response.fp.raw._sock.settimeout(remaining)
            chunk = response.read1(min(65536, 16 * 1024 * 1024 + 1 - total))
            if not chunk:
                break
            chunks.append(chunk); total += len(chunk)
        body = b''.join(chunks)
        if len(body) > 16 * 1024 * 1024:
            raise ValueError('自定义来源响应超过 16 MB')
        value = json.loads(body)
        if not isinstance(value, dict) or value.get('success') is False or value.get('errorCode', 0) != 0:
            raise ValueError('自定义来源响应协议或业务状态无效')
        return value
    except (OSError, UnicodeError, json.JSONDecodeError, http.client.HTTPException):
        raise ValueError('自定义弹幕服务连接失败或响应格式无效') from None
    finally:
        connection.close()


def reference(key, kind, identifier):
    identifier = str(identifier)
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,160}', identifier):
        raise ValueError('自定义服务条目标识无效')
    return f'danmaku://{key}/{kind}/{identifier}'


def parse_reference(url, kind):
    parsed = urlsplit(url)
    if parsed.scheme != 'danmaku' or parsed.query or parsed.fragment or not parsed.path.startswith('/' + kind + '/'):
        raise ValueError('弹幕来源标识无效')
    identifier = parsed.path.split('/')[-1]
    if reference(parsed.netloc, kind, identifier) != url:
        raise ValueError('弹幕来源标识无效')
    return registry.custom(parsed.netloc), identifier


def custom_search(key, keyword):
    config = registry.custom(key)
    value = custom_json(config, 'search/anime', {'keyword': keyword})
    if not isinstance(value.get('animes'), list):
        raise ValueError('自定义来源不兼容：缺少 animes 数组')
    return [{'title': str(row.get('animeTitle', '未命名作品'))[:200], 'platform': config['name'], 'source_id': key,
             'url': reference(key, 'anime', row['animeId']), 'season': None}
            for row in value['animes'][:50] if isinstance(row, dict) and row.get('animeId') is not None]


def custom_episodes(url):
    config, identifier = parse_reference(url, 'anime')
    value = custom_json(config, 'bangumi/' + quote(identifier, safe=''))
    episodes = value.get('bangumi', {}).get('episodes')
    if not isinstance(episodes, list):
        raise ValueError('自定义来源不兼容：缺少 bangumi.episodes')
    return [{'title': str(row.get('episodeTitle', '未命名分集'))[:200],
             'number': str(row.get('episodeNumber', '')), 'url': reference(config['id'], 'episode', row['episodeId'])}
            for row in episodes[:500] if isinstance(row, dict) and row.get('episodeId') is not None]


def custom_comments(url):
    config, identifier = parse_reference(url, 'episode')
    value = custom_json(config, 'comment/' + quote(identifier, safe=''), {'format': 'json'})
    if not isinstance(value.get('comments'), list):
        raise ValueError('自定义来源不兼容：缺少 comments 数组；异步分片协议尚不支持')
    rows = []
    for row in value['comments'][:100000]:
        if not isinstance(row, dict) or not isinstance(row.get('m'), str):
            continue
        fields = str(row.get('p', '')).split(',')
        try:
            second = float(fields[0]); mode = int(fields[1]); color = int(fields[2])
            if not 0 <= second <= 7 * 86400 or not 0 <= color <= 0xffffff or mode not in (1, 4, 5):
                continue
        except (IndexError, ValueError, OverflowError):
            continue
        rows.append({'text': row['m'][:2000], 'time': second, 'mode': {1: 0, 4: 2, 5: 1}[mode], 'color': f'#{color:06X}', 'border': False, 'style': {}})
    return rows


def authenticated_user(token):
    if not token:
        raise PermissionError('请重新登录')
    import requests
    from Config import fnos_url
    from apps.danmu.ass_support import signed_headers
    with requests.get(fnos_url.rstrip('/') + '/v/api/v1/user/info',
        headers=signed_headers(token, 'GET', '/api/v1/user/info'), timeout=(5, 15), allow_redirects=False) as response:
        value = response.json()
        if response.status_code != 200 or value.get('code') != 0 or not isinstance(value.get('data'), dict):
            raise PermissionError('登录失效')
        user = value['data']
    identity = next((str(user[key]) for key in ('guid', 'uid', 'username') if user.get(key) is not None and str(user[key])), None)
    if not identity:
        raise PermissionError('账户缺少稳定标识，无法保存个人配置')
    return {'owner': hashlib.sha256(identity.encode()).hexdigest(), 'admin': user.get('is_admin') in (True, 1)}


def register_source_routes(blueprint):
    from flask import request

    def user():
        return authenticated_user(request.headers.get('Authorization', ''))

    @blueprint.after_request
    def source_headers(response):
        from flask import g
        if request.path.startswith('/danmu/sources'):
            response.headers['Cache-Control'] = 'no-store'
            response.headers['Vary'] = 'Authorization'
        if request.path in ('/danmu/get', '/danmu/getEmoji'):
            response.headers['Cache-Control'] = 'no-store'
            if getattr(g, 'danmaku_source', None):
                response.headers['X-Danmaku-Source'] = g.danmaku_source
        return response

    @blueprint.before_request
    def custom_source_login():
        if str(request.args.get('source', '')).startswith('custom-') or str(request.args.get('url', '')).startswith('danmaku://'):
            try:
                user()
            except Exception:
                return {'code': -2, 'msg': '自定义来源需要登录'}, 401

    @blueprint.route('/sources/preferences', methods=['GET', 'PUT', 'DELETE'])
    def source_preferences():
        try:
            account = user()
            data = (request.get_json(silent=True) or {}) if request.method != 'GET' else request.args
            episode, season = data.get('guid', ''), data.get('parent_guid', '')
            if request.method != 'GET':
                level = data.get('scope')
                if level not in ('user', 'season', 'episode'):
                    raise ValueError('配置范围无效')
                target = episode if level == 'episode' else season
                if level != 'user':
                    media_access(request.headers.get('Authorization', ''), target)
                scope = 'user' if level == 'user' else level + ':' + target
                if request.method == 'DELETE':
                    user_state.put(account['owner'], 'danmaku', scope, None)
                else:
                    source = data.get('source_id', 'auto')
                    if source not in ['auto'] + registry.enabled('providers') + registry.enabled('custom') or type(data.get('fallback')) is not bool:
                        raise ValueError('来源已禁用或偏好字段无效')
                    value = {'source_id': source, 'fallback': data['fallback']}
                    if level == 'episode' and data.get('url'):
                        value['url'] = source_url(data['url'])
                    if level == 'season' and data.get('episodes') is not None:
                        entries = data['episodes']
                        if not isinstance(entries, dict) or len(entries) > 500 or any(not re.fullmatch(r'\d{1,5}', str(key)) for key in entries):
                            raise ValueError('季度分集映射无效')
                        value['episodes'] = {str(key): source_url(url) for key, url in entries.items()}
                    user_state.put(account['owner'], 'danmaku', scope, value)
            return {'code': 0, 'data': selection_for(account['owner'], episode, season)}
        except PermissionError:
            return {'code': -2, 'msg': '请重新登录并确认媒体访问权限'}, 403
        except ValueError as error:
            return {'code': -1, 'msg': str(error)}, 400
        except Exception:
            return {'code': -1, 'msg': '个人弹幕偏好读取或保存失败'}, 502

    @blueprint.route('/sources', methods=['GET', 'PUT'])
    def sources_config():
        try:
            account = user()
            if request.method == 'PUT':
                if not account['admin']:
                    raise PermissionError('仅管理员可修改服务来源')
                registry.save(request.get_json(silent=True))
            value = registry.public(account['admin'])
            for kind in ('providers', 'searches', 'custom'):
                for row in value[kind]:
                    row['last_result'] = user_state.get('service', 'source-status', row['id'])
            return {'code': 0, 'data': value, 'can_manage': account['admin']}
        except PermissionError:
            return {'code': -2, 'msg': '请使用有效的管理员账号修改，普通账号仅可读取'}, 403
        except ValueError as error:
            return {'code': -1, 'msg': str(error)}, 400
        except Exception:
            return {'code': -1, 'msg': '来源配置暂时不可用'}, 502

    @blueprint.post('/sources/check')
    def sources_check():
        try:
            if not user()['admin']:
                raise PermissionError()
            data = request.get_json(silent=True) or {}
            config = registry.custom(str(data.get('id', '')))
            results = custom_search(config['id'], short_text(data.get('keyword', '测试'), 100))
            stages = {'connection': True, 'search': True, 'episodes': None, 'comments': None}
            if results:
                episodes = custom_episodes(results[0]['url']); stages['episodes'] = True
                if episodes:
                    comments = custom_comments(episodes[0]['url']); stages['comments'] = True; stages['comment_count'] = len(comments)
            return {'code': 0, 'data': stages}
        except PermissionError:
            return {'code': -2, 'msg': '仅管理员可检查自定义来源'}, 403
        except Exception:
            return {'code': -1, 'msg': '连接或协议检查失败，请检查服务地址、网络、鉴权和协议；空搜索不证明分集／弹幕兼容'}, 502
