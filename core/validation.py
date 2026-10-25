"""Validation shared by the legacy playback and configuration endpoints."""
import math
import re
from urllib.parse import urlsplit


def media_identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', value):
        raise ValueError('缺少有效的影片标识')
    return value


def finite_number(value, minimum=0, maximum=10**12):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError('时间或倍速格式无效')
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError('时间或倍速超出范围')
    return value


def playback_record(data):
    if not isinstance(data, dict):
        raise ValueError('播放记录格式无效')
    return {
        'guid': media_identifier(data.get('guid')),
        'episode_guid': media_identifier(data.get('item_guid')),
        'time': int(finite_number(data.get('ts'))),
        'create_time': int(finite_number(data.get('create_time'))),
        'playback_speed': finite_number(data.get('playback_speed'), 0.1, 16),
    }


def video_config(data):
    if not isinstance(data, dict) or not data or set(data) - {'list', 'url'}:
        raise ValueError('配置字段无效')
    result = {}
    for key, rows in data.items():
        if not isinstance(rows, list) or len(rows) > 100:
            raise ValueError('配置条目过多或格式无效')
        clean = []
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError('配置条目格式无效')
            if key == 'list':
                if set(row) != {'startTime', 'endTime'}:
                    raise ValueError('跳过片段字段无效')
                start = finite_number(row['startTime'])
                end = finite_number(row['endTime'])
                if start >= end:
                    raise ValueError('结束时间必须大于开始时间')
                clean.append({'startTime': start, 'endTime': end})
            else:
                if set(row) != {'url'} or not isinstance(row['url'], str) or len(row['url']) > 4096:
                    raise ValueError('弹幕链接无效')
                parsed = urlsplit(row['url'])
                host = (parsed.hostname or '').lower()
                domains = ('bilibili.com', 'iqiyi.com', 'v.qq.com', 'youku.com', 'mgtv.com', 'tv.sohu.com')
                if (parsed.scheme not in ('http', 'https') or parsed.username or parsed.password
                        or parsed.port not in (None, 80, 443)
                        or not any(host == domain or host.endswith('.' + domain) for domain in domains)):
                    raise ValueError('请输入支持平台的播放链接')
                clean.append({'url': row['url']})
        result[key] = clean
    return result
