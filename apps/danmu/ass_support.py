"""Authenticated ASS subtitle and embedded font extraction."""
import fcntl
import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlencode
import requests
from flask import request, send_file
from Config import fnos_url

CACHE = Path('/app/data/subtitle-cache')
PRIVATE = Path('/run/fnos-tv-private')


class SubtitlePending(Exception):
    pass


def write_job(directory, **state):
    pending = directory / f'.job-{os.getpid()}.tmp'
    pending.write_text(json.dumps({**state, 'updated': time.time()}))
    pending.replace(directory / 'job.json')


def job_running(state):
    if state.get('state') != 'running' or time.time() - state.get('updated', 0) >= 660:
        return False
    try:
        command = Path(f'/proc/{int(state["pid"])}/cmdline').read_bytes()
        return b'extraction_job' in command
    except (OSError, KeyError, ValueError):
        return False


def extraction_job(source, mapping, destination, kind='ass'):
    if kind not in ('ass', 'sup'): raise ValueError('不支持的字幕格式')
    directory = Path(destination)
    with (directory / '.extract.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        target = directory / ('subtitle.' + kind)
        if target.exists():
            return
        write_job(directory, state='running', pid=os.getpid())
        temporary = directory / ('subtitle.pending.' + kind)
        try:
            result = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-analyzeduration', '1', '-probesize', '32768', '-i', source, '-map', mapping, '-c:s', 'copy', '-f', kind, str(temporary)], capture_output=True, timeout=600)
            if result.returncode or not temporary.exists() or temporary.stat().st_size > (256 if kind == 'sup' else 32) * 1024 * 1024:
                raise ValueError('无法提取这条特效字幕，请切换字幕轨道')
            if kind == 'sup':
                with temporary.open('rb') as file:
                    if file.read(2) != b'PG': raise ValueError('提取结果不是有效 PGS 字幕')
            else:
                with temporary.open(encoding='utf-8-sig') as file:
                    if '[Events]' not in file.read(65536): raise ValueError('提取结果不是有效 ASS 字幕')
                extract_fonts(Path(source), directory)
                font_catalog(directory, json.loads((directory / 'fonts-v2.json').read_text()))
            temporary.replace(target)
            write_job(directory, state='ready', pid=os.getpid())
        except (ValueError, OSError, subprocess.SubprocessError) as error:
            temporary.unlink(missing_ok=True)
            message = str(error) if isinstance(error, ValueError) else '特效字幕提取暂时失败，可继续使用普通字幕'
            write_job(directory, state='error', message=message, pid=os.getpid())


def ensure_extraction(source, mapping, directory, kind='ass'):
    if (directory / ('subtitle.' + kind)).exists():
        return
    CACHE.mkdir(parents=True, exist_ok=True)
    with (CACHE / '.scheduler.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state_file = directory / 'job.json'
        state = json.loads(state_file.read_text()) if state_file.exists() else {}
        age = time.time() - state.get('updated', 0)
        if state.get('state') == 'error' and age < 120:
            raise ValueError(state.get('message') or '字幕提取暂时失败')
        if job_running(state):
            raise SubtitlePending()
        active = 0
        for job in CACHE.glob('*/job.json'):
            data = json.loads(job.read_text())
            if job_running(data):
                active += 1
        if active >= 2:
            raise SubtitlePending()
        command = 'from apps.danmu.ass_support import extraction_job; import sys; extraction_job(*sys.argv[1:])'
        process = subprocess.Popen([sys.executable, '-c', command, str(source), mapping, str(directory), kind], env={**os.environ, 'PYTHONPATH': '/app'}, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        write_job(directory, state='running', pid=process.pid)
        threading.Thread(target=process.wait, daemon=True).start()
    raise SubtitlePending()


def signed_headers(token, method, path, params=None, data=None):
    nonce, timestamp = '123456', str(int(time.time() * 1000))
    payload = urlencode(sorted((params or {}).items())) if method == 'GET' else json.dumps(data or {}, ensure_ascii=False, separators=(',', ':'))
    digest = hashlib.md5(payload.encode()).hexdigest()
    signature = hashlib.md5('_'.join(['NDzZTVxnRKP8Z0jXg1VAMonaG8akvh', '/v' + path, nonce, timestamp, digest, '16CCEB3D-AB42-077D-36A1-F355324E4237']).encode()).hexdigest()
    return {'Authorization': token, 'authx': f'nonce={nonce}&timestamp={timestamp}&sign={signature}', 'X-Trim-Client': 'web', 'X-Trim-Client-Version': '631'}


def official(token, method, path, params=None, data=None, raw=False):
    if not token:
        raise PermissionError('请重新登录影视账号')
    kwargs = {'params': params, 'headers': signed_headers(token, method, path, params, data), 'timeout': 45}
    if method != 'GET':
        kwargs['json'] = data or {}
    response = requests.request(method, fnos_url + '/v' + path, **kwargs)
    if response.status_code != 200:
        raise ValueError(f'官方字幕接口返回 HTTP {response.status_code}')
    if raw and not response.text.lstrip().startswith('{'):
        return response.text
    result = response.json()
    if result.get('code') in (-2, -7):
        raise PermissionError('登录已失效或无权读取该影片')
    if result.get('code') != 0:
        raise ValueError(str(result.get('msg') or '官方字幕接口拒绝请求'))
    return result.get('data')


def identifier(value):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', str(value or '')):
        raise ValueError('缺少有效的影片或字幕标识')
    return str(value)


def permitted_path(value):
    path = Path(value).resolve()
    roots = json.loads(Path('/app/data/media-roots.json').read_text())
    if not any(path.is_relative_to(Path(root).resolve()) for root in roots):
        raise PermissionError('该媒体目录尚未配置只读访问权限')
    if not path.is_file():
        raise ValueError('媒体文件不存在或尚未挂载')
    return path


def media_stream(token, item_guid):
    return official(token, 'GET', '/api/v1/stream/list/' + identifier(item_guid), {'before_play': '1'})


def run_ffmpeg(args, timeout=90):
    result = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    if result.returncode:
        raise ValueError('字幕提取失败，请切换轨道或上传外挂 ASS 字幕')
    if len(result.stdout) > 32 * 1024 * 1024:
        raise ValueError('字幕文件过大')
    return result.stdout.decode('utf-8-sig')


def font_ticket(directory, filename, secret, expiry):
    return hmac.new(secret, f'{directory}/{filename}/{expiry}'.encode(), hashlib.sha256).hexdigest()


def secret_key():
    PRIVATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    PRIVATE.chmod(0o700)
    path = PRIVATE / 'subtitle-key'
    with (PRIVATE / 'subtitle-key.lock').open('a+b') as lock:
        os.fchmod(lock.fileno(), 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not path.exists():
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as file:
                file.write(os.urandom(32))
                file.flush()
                os.fsync(file.fileno())
        path.chmod(0o600)
        result = path.read_bytes()
        if len(result) != 32: raise ValueError('字幕签名密钥无效')
        return result


def extract_fonts(source, directory):
    marker = directory / 'fonts-v2.json'
    if marker.exists():
        return json.loads(marker.read_text())
    fonts = []
    try:
        probe = subprocess.run(['ffprobe', '-v', 'error', '-analyzeduration', '1', '-probesize', '32768', '-select_streams', 't', '-show_streams', '-of', 'json', str(source)], capture_output=True, timeout=15, check=True)
        args = []
        font_bytes = 0
        for track in json.loads(probe.stdout).get('streams', [])[:40]:
            suffix = Path(track.get('tags', {}).get('filename', '')).suffix.lower()
            size = int(track.get('extradata_size', 0))
            if suffix not in ('.ttf', '.otf', '.ttc') or size > 25 * 1024 * 1024 or font_bytes + size > 96 * 1024 * 1024:
                continue
            font_bytes += size
            name = f'font-{len(fonts)}{suffix}'
            args.extend([f'-dump_attachment:{int(track["index"])}', str(directory / name)])
            fonts.append(name)
        if args:
            run_ffmpeg(['-y', *args, '-analyzeduration', '1', '-probesize', '32768', '-i', str(source), '-t', '0', '-map', '0:v:0?', '-c', 'copy', '-f', 'null', '-'], timeout=20)
        fonts = [name for name in fonts if (directory / name).is_file()]
    except (subprocess.SubprocessError, ValueError, KeyError):
        fonts = []
    marker.write_text(json.dumps(fonts))
    return fonts


def font_catalog(directory, fonts):
    marker = directory / 'font-names-v1.json'
    if marker.exists():
        return json.loads(marker.read_text())
    catalog = {}
    for name in fonts:
        try:
            result = subprocess.run(['fc-scan', '--format', '%{family}\n%{postscriptname}\n%{fullname}\n', str(directory / name)], capture_output=True, timeout=5, check=True)
            for alias in re.split(r'[,\r\n]', result.stdout.decode('utf-8')):
                alias = alias.strip().lower().lstrip('@')
                if alias:
                    catalog.setdefault(alias, name)
        except (OSError, subprocess.SubprocessError, UnicodeError):
            # 无法确定名字时保留旧的完整加载行为，避免丢失特效字体。
            return None
    marker.write_text(json.dumps(catalog, ensure_ascii=False))
    return catalog


def ass_source(token, item_guid, subtitle_guid, stream=None, binary=False):
    stream = stream or media_stream(token, item_guid)
    track = next((s for s in stream.get('subtitle_streams', []) if s.get('guid') == subtitle_guid), None)
    if not track:
        raise PermissionError('当前影片中未找到这条字幕')
    codec = str(track.get('codec_name') or '').lower()
    if binary and codec not in ('hdmv_pgs_subtitle', 'pgssub', 'pgs'):
        raise ValueError('当前轨道不是 PGS 图片字幕')
    if track.get('extra_file') and not binary:
        try:
            text = official(token, 'GET', '/api/v1/subtitle/dl/' + identifier(subtitle_guid), {'fmt': 'ass'}, raw=True)
            if isinstance(text, str) and '[Events]' in text:
                return text, []
        except ValueError:
            pass
    media_guid = track.get('media_guid') or (stream.get('video_streams') or [{}])[0].get('media_guid')
    source = next((f for f in stream.get('files', []) if f.get('guid') == media_guid), None) or (stream.get('files') or [None])[0]
    if not source:
        raise ValueError('没有可读取的媒体文件')
    path = permitted_path(source['path'])
    if track.get('extra_file'):
        external = track.get('path') or track.get('file_path')
        if not external:
            raise ValueError('无法读取该外挂字幕，请使用官方搜索下载或上传 ASS')
        path = permitted_path(external)
        mapping = '0:s:0'
    else:
        mapping = '0:' + str(int(track['index']))
    stat = path.stat()
    key = hashlib.sha256(f'{path}:{stat.st_mtime_ns}:{stat.st_size}:{subtitle_guid}'.encode()).hexdigest()
    directory = CACHE / key
    directory.mkdir(parents=True, exist_ok=True)
    directory.touch()
    ensure_extraction(path, mapping, directory, 'sup' if binary else 'ass')
    if binary:
        expiry = (int(time.time()) // 86400 + 2) * 86400
        ticket = font_ticket(key, 'subtitle.sup', secret_key(), expiry)
        return f'/danmu/subtitle-pgs-file/{key}?expires={expiry}&ticket={ticket}', []
    target = directory / 'subtitle.ass'
    fonts = extract_fonts(path, directory)
    catalog = font_catalog(directory, fonts)
    text = target.read_text(encoding='utf-8')
    # 同一天返回相同的签名 URL，允许浏览器复用字体缓存；有效期为 24 至 48 小时。
    expiry = (int(time.time()) // 86400 + 2) * 86400
    secret = secret_key()
    urls = {name: f'/danmu/subtitle-font/{key}/{name}?expires={expiry}&ticket={font_ticket(key, name, secret, expiry)}' for name in fonts}
    return text, {alias: urls[name] for alias, name in catalog.items()} if catalog is not None else list(urls.values())


def register_ass_routes(blueprint):
    @blueprint.get('/subtitle-pgs')
    def subtitle_pgs():
        try:
            url, _ = ass_source(request.headers.get('Authorization', ''), identifier(request.args.get('item_guid')), identifier(request.args.get('subtitle_guid')), binary=True)
            return {'code': 0, 'url': url}
        except SubtitlePending:
            return {'code': 0, 'pending': True, 'retry_after': 3, 'msg': '正在后台准备 PGS 图片字幕'}, 202
        except PermissionError as error:
            return {'code': -2, 'msg': str(error)}, 401
        except (ValueError, KeyError, OSError, subprocess.SubprocessError, requests.RequestException) as error:
            return {'code': -1, 'msg': str(error) if isinstance(error, ValueError) else 'PGS 字幕提取失败，请稍后重试'}, 422

    @blueprint.get('/subtitle-pgs-file/<key>')
    def subtitle_pgs_file(key):
        expiry = request.args.get('expires', '0')
        ticket = request.args.get('ticket', '')
        if not re.fullmatch(r'[0-9a-f]{64}', key) or not expiry.isdigit(): return {'code': -1}, 404
        if int(expiry) < time.time() or not hmac.compare_digest(ticket, font_ticket(key, 'subtitle.sup', secret_key(), expiry)): return {'code': -2}, 403
        response = send_file(CACHE / key / 'subtitle.sup', mimetype='application/octet-stream', max_age=86400)
        response.headers['Cache-Control'] = 'private, max-age=86400'
        return response

    @blueprint.get('/subtitle-ass')
    def subtitle_ass():
        try:
            text, fonts = ass_source(request.headers.get('Authorization', ''), identifier(request.args.get('item_guid')), identifier(request.args.get('subtitle_guid')))
            return {'code': 0, 'ass': text, 'fonts': fonts}
        except SubtitlePending:
            return {'code': 0, 'pending': True, 'retry_after': 3, 'msg': '正在后台准备特效字幕，期间继续显示普通字幕'}, 202
        except PermissionError as error:
            return {'code': -2, 'msg': str(error)}, 401
        except (ValueError, KeyError, OSError, subprocess.SubprocessError, requests.RequestException) as error:
            message = str(error) if isinstance(error, ValueError) else '字幕提取暂时失败，请稍后重试或切换轨道'
            return {'code': -1, 'msg': message}, 422

    @blueprint.get('/subtitle-font/<key>/<filename>')
    def subtitle_font(key, filename):
        expiry = request.args.get('expires', '0')
        ticket = request.args.get('ticket', '')
        if not re.fullmatch(r'[0-9a-f]{64}', key) or not re.fullmatch(r'font-\d+\.(?:ttf|otf|ttc)', filename) or not expiry.isdigit():
            return {'code': -1}, 404
        if int(expiry) < time.time() or not hmac.compare_digest(ticket, font_ticket(key, filename, secret_key(), expiry)):
            return {'code': -2}, 403
        response = send_file(CACHE / key / filename, max_age=86400)
        response.headers['Cache-Control'] = 'private, max-age=86400'
        return response

