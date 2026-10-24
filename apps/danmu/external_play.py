"""Short-lived, media-session-scoped playback URLs for players without browser cookies."""
import json
import os
from pathlib import Path
import re
import secrets
import time
from urllib.parse import quote, unquote, urljoin, urlsplit

import requests
from flask import Response, request, stream_with_context
from Config import fnos_url

STORE = Path('/run/fnos-tv-private/external-play')
LIFETIME = 4 * 3600


def media_path(value, root=None):
    parsed = urlsplit(value)
    path = unquote(parsed.path)
    if parsed.scheme or parsed.netloc or parsed.fragment or '\\' in path:
        raise ValueError('无效的播放路径')
    if not re.fullmatch(r'/v/media/[a-fA-F0-9]{32}/[A-Za-z0-9_./-]+', path):
        raise ValueError('仅支持当前影片的媒体链接')
    if any(part in ('.', '..', '') for part in path.split('/')[1:]):
        raise ValueError('无效的媒体路径')
    if root and not path.startswith(root):
        raise ValueError('播放资源不属于当前影片')
    return path + ('?' + parsed.query if parsed.query else '')


def raw_media_path(guid):
    if not isinstance(guid, str) or not re.fullmatch(r'[a-fA-F0-9]{32}', guid):
        raise ValueError('无效的媒体标识')
    return '/v/api/v1/media/range/' + guid


def upstream(path, token, method='GET', headers=None):
    # Never accept a destination host from the caller, or forward redirects with credentials.
    session = requests.Session()
    session.trust_env = False
    try:
        response = session.request(method, fnos_url.rstrip('/') + path, headers={
            'Authorization': token, 'Cookie': 'authorization=' + token,
            'Accept-Encoding': 'identity', **(headers or {})
        }, stream=True, timeout=(8, 45), allow_redirects=False)
    except Exception:
        session.close()
        raise
    original_close = response.close
    def close():
        try: original_close()
        finally: session.close()
    response.close = close
    return response


def playlist_text(response):
    data = bytearray()
    for chunk in response.iter_content(65536):
        data.extend(chunk)
        if len(data) > 2 * 1024 * 1024:
            raise ValueError('播放列表过大')
    text = data.decode('utf-8-sig')
    if not text.lstrip().startswith('#EXTM3U'):
        raise PermissionError('媒体登录已失效，请刷新网页重新打开影片')
    return text


def rewrite_playlist(text, source, root, ticket):
    def convert(uri):
        parsed = urlsplit(urljoin(fnos_url.rstrip('/') + source, uri))
        origin = urlsplit(fnos_url)
        if (parsed.scheme, parsed.netloc) != (origin.scheme, origin.netloc):
            raise ValueError('播放列表包含其他服务器的资源')
        path = media_path(parsed.path + ('?' + parsed.query if parsed.query else ''), root)
        return '/danmu/external-play/' + ticket + '/' + path[len(root):]
    lines = []
    for line in text.splitlines():
        if line and not line.startswith('#'):
            line = convert(line.strip())
        elif line.startswith('#'):
            line = re.sub(r'URI="([^"]+)"', lambda match: 'URI="' + convert(match[1]) + '"', line)
        lines.append(line)
    return '\n'.join(lines) + '\n'


def register_external_routes(blueprint):
    @blueprint.post('/external-play')
    def create_external_play():
        token = request.headers.get('Authorization', '')
        if not token or any(char in token for char in '\r\n;'):
            return {'code': -2, 'msg': '请重新登录影视账号'}, 401
        try:
            data = request.get_json(silent=True) or {}
            if not isinstance(data, dict):
                raise ValueError('无效的请求')
            if data.get('media_guid'):
                if data.get('play_link'):
                    raise ValueError('仅支持一种播放来源')
                path = raw_media_path(data['media_guid'])
                with upstream(path, token, headers={'Range': 'bytes=0-0'}) as response:
                    if 'json' in response.headers.get('Content-Type', ''):
                        raise PermissionError('媒体登录已失效或没有访问权限')
                    if response.status_code not in (200, 206) or 'text/html' in response.headers.get('Content-Type', ''):
                        raise ValueError('原文件不可用')
                state = {'mode': 'file', 'path': path}
                suffix = 'media'
            else:
                path = media_path(data.get('play_link', ''))
                if not urlsplit(path).path.endswith('.m3u8'):
                    raise ValueError('请重新打开影片获取播放列表')
                root = '/'.join(urlsplit(path).path.split('/')[:4]) + '/'
                with upstream(path, token) as response:
                    if response.status_code != 200:
                        raise ValueError(f'媒体接口返回 HTTP {response.status_code}，请重新打开影片')
                    text = playlist_text(response)
                state = {'root': root}
                suffix = path[len(root):]
            ticket = secrets.token_urlsafe(32)
            # Validate every child URI before issuing a link; credentials stay on the NAS.
            if state.get('mode') != 'file':
                rewrite_playlist(text, path, root, ticket)
            STORE.mkdir(parents=True, exist_ok=True, mode=0o700)
            STORE.chmod(0o700)
            for old in STORE.glob('*.json'):
                if time.time() - old.stat().st_mtime > LIFETIME:
                    old.unlink(missing_ok=True)
            if sum(1 for _ in STORE.glob('*.json')) >= 512:
                return {'code': -1, 'msg': '临时播放链接过多，请稍后重试'}, 429
            state.update(token=token, expires=time.time() + LIFETIME)
            with os.fdopen(os.open(STORE / (ticket + '.json'), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as file:
                json.dump(state, file)
            return {'code': 0, 'data': {'url': '/danmu/external-play/' + ticket + '/' + suffix, 'expires_in': LIFETIME}}
        except PermissionError as error:
            return {'code': -2, 'msg': str(error)}, 401
        except (ValueError, TypeError):
            return {'code': -1, 'msg': '无法创建外部播放链接，请重新打开影片后重试'}, 422
        except (requests.RequestException, OSError):
            return {'code': -1, 'msg': '媒体服务暂时不可用，请重试'}, 502

    @blueprint.route('/external-play/<ticket>/<path:resource>', methods=['GET', 'HEAD'])
    def external_media(ticket, resource):
        if not re.fullmatch(r'[A-Za-z0-9_-]{43}', ticket):
            return '播放链接无效', 403
        try:
            state = json.loads((STORE / (ticket + '.json')).read_text())
            if state['expires'] < time.time():
                return '播放链接已过期，请从网页重新打开', 403
            raw_file = state.get('mode') == 'file'
            if raw_file:
                if resource != 'media' or request.query_string:
                    return '播放资源不属于当前影片', 403
                path = raw_media_path(state['path'].rsplit('/', 1)[-1])
                if path != state['path']:
                    return '播放链接无效', 403
            else:
                path = media_path(state['root'] + resource + ('?' + request.query_string.decode('ascii') if request.query_string else ''), state['root'])
            headers = {key: request.headers[key] for key in ['Range', 'If-Range'] if key in request.headers}
            # A rewritten playlist must be complete; byte ranges apply only to media files.
            if urlsplit(path).path.endswith('.m3u8'):
                headers = {}
            # Official raw-file routes do not implement HEAD. Probe one byte using GET.
            raw_head = raw_file and request.method == 'HEAD'
            if raw_head and 'Range' not in headers:
                headers['Range'] = 'bytes=0-0'
            response = upstream(path, state['token'], 'GET' if raw_head else request.method, headers)
            if response.status_code not in (200, 206):
                status = response.status_code if response.status_code in (401, 403, 404, 410, 416) else 502
                response.close()
                return '媒体资源不可用，请重新打开影片', status
            if '.json' in response.headers.get('Content-Type', '') or 'application/json' in response.headers.get('Content-Type', ''):
                response.close()
                return '影视登录已失效，请重新打开影片', 401
            if urlsplit(path).path.endswith('.m3u8') and request.method != 'HEAD':
                try:
                    text = rewrite_playlist(playlist_text(response), path, state['root'], ticket)
                finally:
                    response.close()
                return Response(text, mimetype='application/vnd.apple.mpegurl', headers={'Cache-Control': 'no-store'})
            outgoing = {key: response.headers[key] for key in ['Content-Type', 'Content-Length', 'Content-Range', 'Accept-Ranges', 'ETag', 'Last-Modified'] if key in response.headers}
            outgoing['Cache-Control'] = 'private, no-store'
            if request.method == 'HEAD':
                status = response.status_code
                if raw_head and 'Range' not in request.headers:
                    match = re.fullmatch(r'bytes \d+-\d+/(\d+)', response.headers.get('Content-Range', ''))
                    if match:
                        outgoing['Content-Length'] = match[1]
                        outgoing.pop('Content-Range', None)
                        status = 200
                response.close()
                return Response(status=status, headers=outgoing)
            def chunks():
                try:
                    yield from response.iter_content(128 * 1024)
                finally:
                    response.close()
            result = Response(stream_with_context(chunks()), status=response.status_code, headers=outgoing)
            result.call_on_close(response.close)
            return result
        except PermissionError:
            return '影视登录已失效，请重新打开影片', 401
        except (FileNotFoundError, KeyError, ValueError, UnicodeError):
            return '播放链接无效或已过期', 403
        except (requests.RequestException, OSError):
            return '媒体服务暂时不可用', 502
