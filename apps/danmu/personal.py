"""Account-owned watchlist and versioned preferences, separate from Feiniu favorites."""
import copy
import json
import math
import re
import time

from apps.danmu.sources import authenticated_user, media_access, user_state

PLAYER_DEFAULTS = {'audioLanguage': '', 'autoPlayNext': True, 'assSubtitles': True, 'subtitleFontSize': 20,
    'playbackRate': 1, 'use302': False, 'gesturesEnabled': True, 'gestureHoldRate': 2,
    'subtitleLanguage': '', 'qualityPreference': 'highest', 'externalPlayer': ''}
HOME_DEFAULTS = {'sections': ['continue', 'watchlist', 'libraries'], 'hiddenSections': [],
    'libraryOrder': [], 'hiddenLibraries': [], 'density': 'comfortable', 'showHistory': True}


class Conflict(ValueError):
    pass


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', value):
        raise ValueError('媒体标识无效')
    return value


def preference_values(kind, values):
    defaults = PLAYER_DEFAULTS if kind == 'player' else HOME_DEFAULTS
    if not isinstance(values, dict) or set(values) - set(defaults):
        raise ValueError('偏好字段无效')
    for key, value in values.items():
        valid = False
        if type(defaults[key]) is bool:
            valid = type(value) is bool
        elif key == 'audioLanguage':
            valid = isinstance(value, str) and len(value) <= 64 and not re.search(r'[\x00-\x1f\x7f]', value)
        elif key == 'subtitleLanguage':
            valid = isinstance(value, str) and (value == '' or re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,31}', value))
        elif key == 'subtitleFontSize':
            valid = type(value) is int and 12 <= value <= 60
        elif key == 'playbackRate':
            valid = type(value) in (int, float) and math.isfinite(value) and .5 <= value <= 3
        elif key == 'gestureHoldRate':
            valid = type(value) in (int, float) and value in (2, 2.5, 3)
        elif key == 'qualityPreference':
            valid = value in ('highest', '4k', '1080', '720', '480', '360')
        elif key == 'externalPlayer':
            valid = value in ('', 'potplayer', 'vlc', 'iina', 'infuse', 'nplayer', 'mxplayer', 'mxplayer-pro')
        elif key == 'density':
            valid = value in ('comfortable', 'compact')
        elif key in ('sections', 'hiddenSections'):
            valid = isinstance(value, list) and all(isinstance(item, str) and item in HOME_DEFAULTS['sections'] for item in value) and len(set(value)) == len(value)
            if key == 'sections':
                valid = valid and set(value) == set(HOME_DEFAULTS['sections'])
        elif key in ('libraryOrder', 'hiddenLibraries'):
            valid = isinstance(value, list) and len(value) <= 1000 and all(isinstance(item, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,100}', item) for item in value) and len(set(value)) == len(value)
        if not valid:
            raise ValueError('偏好值超出允许范围：' + key)
    return copy.deepcopy(values)


def settings(owner, kind, update=None):
    if kind not in ('player', 'home'):
        raise ValueError('偏好类别无效')
    with user_state.connect() as connection:
        if update is not None:
            connection.execute('BEGIN IMMEDIATE')
        row = connection.execute('SELECT value FROM user_state WHERE owner=? AND kind=? AND scope=?', (owner, 'preferences', kind)).fetchone()
        current = json.loads(row[0]) if row else {'revision': 0, 'values': {}}
        if update is not None:
            if not isinstance(update, dict) or set(update) - {'revision', 'values', 'reset'}:
                raise ValueError('偏好请求字段无效')
            if type(update.get('revision')) is not int or update['revision'] != current['revision']:
                raise Conflict('其他设备已修改偏好，请重新读取后保存')
            values = preference_values(kind, update.get('values', {}))
            if update.get('reset') is True:
                values = copy.deepcopy(PLAYER_DEFAULTS if kind == 'player' else HOME_DEFAULTS)
            current = {'revision': current['revision'] + 1, 'values': {**current['values'], **values}}
            connection.execute('INSERT INTO user_state VALUES (?,?,?,?,?) ON CONFLICT(owner,kind,scope) DO UPDATE SET value=excluded.value,updated=excluded.updated',
                (owner, 'preferences', kind, json.dumps(current, ensure_ascii=False), int(time.time())))
        return {'schema': 1, **current}


def watchlist_page(owner, page=1, size=21):
    if type(page) is not int or type(size) is not int or not 1 <= page <= 1000 or not 1 <= size <= 50:
        raise ValueError('分页参数无效')
    with user_state.connect() as connection:
        total = connection.execute('SELECT count(*) FROM user_state WHERE owner=? AND kind=?', (owner, 'watchlist')).fetchone()[0]
        rows = connection.execute('SELECT scope,value,updated FROM user_state WHERE owner=? AND kind=? ORDER BY updated DESC,scope ASC LIMIT ? OFFSET ?',
            (owner, 'watchlist', size, (page - 1) * size)).fetchall()
    return {'page': page, 'page_size': size, 'total': total,
        'items': [{'guid': row[0], 'type': json.loads(row[1])['type'], 'added_at': row[2]} for row in rows]}


def register_personal_routes(blueprint):
    from flask import request

    @blueprint.after_request
    def private_personal_response(response):
        if request.path.startswith('/danmu/personal/'):
            response.headers['Cache-Control'] = 'no-store'
            response.headers['Vary'] = 'Authorization'
        return response

    def account():
        return authenticated_user(request.headers.get('Authorization', ''))['owner']

    def error_response(error):
        if isinstance(error, Conflict):
            return {'code': -3, 'msg': str(error)}, 409
        if isinstance(error, PermissionError):
            return {'code': -2, 'msg': '请重新登录并确认媒体权限'}, 403
        if isinstance(error, ValueError):
            return {'code': -1, 'msg': str(error)}, 400
        return {'code': -1, 'msg': '个人数据服务暂时不可用'}, 502

    @blueprint.get('/personal/watchlist')
    def list_watchlist():
        try:
            return {'code': 0, 'data': watchlist_page(account(), int(request.args.get('page', 1)), int(request.args.get('page_size', 21)))}
        except Exception as error:
            return error_response(error)

    @blueprint.route('/personal/watchlist/<guid>', methods=['GET', 'PUT', 'DELETE'])
    def watchlist_item(guid):
        try:
            owner = account(); identifier(guid)
            value = user_state.get(owner, 'watchlist', guid)
            if request.method == 'PUT':
                media = media_access(request.headers.get('Authorization', ''), guid)
                if media.get('type') not in ('Movie', 'TV'):
                    raise ValueError('待看清单目前支持电影和整部剧集')
                if value is None:
                    value = {'type': media['type']}; user_state.put(owner, 'watchlist', guid, value)
            elif request.method == 'DELETE':
                # Owners can clean up a saved id even after losing access to the media.
                user_state.put(owner, 'watchlist', guid, None); value = None
                user_state.put(owner, 'updates', guid, None)
            return {'code': 0, 'data': {'guid': guid, 'contains': value is not None}}
        except Exception as error:
            return error_response(error)

    @blueprint.route('/personal/preferences/<kind>', methods=['GET', 'PUT'])
    def personal_settings(kind):
        try:
            owner = account()
            update = request.get_json(silent=True) if request.method == 'PUT' else None
            if request.method == 'PUT' and not isinstance(update, dict):
                raise ValueError('偏好请求必须为 JSON 对象')
            return {'code': 0, 'data': settings(owner, kind, update)}
        except Exception as error:
            return error_response(error)

    @blueprint.route('/personal/updates/<guid>', methods=['PUT', 'DELETE'])
    def observe_series(guid):
        try:
            owner = account(); identifier(guid)
            if user_state.get(owner, 'watchlist', guid) is None:
                raise ValueError('仅记录本账号待看剧集的变化')
            if request.method == 'DELETE':
                value = user_state.get(owner, 'updates', guid)
                if value is not None:
                    value['delta'] = 0; user_state.put(owner, 'updates', guid, value)
                return {'code': 0, 'data': value}
            media = media_access(request.headers.get('Authorization', ''), guid)
            count = media.get('local_number_of_episodes')
            if media.get('type') != 'TV' or type(count) is not int or not 0 <= count <= 100000:
                return {'code': 0, 'data': {'guid': guid, 'available': False}}
            with user_state.connect() as connection:
                connection.execute('BEGIN IMMEDIATE')
                row = connection.execute('SELECT value FROM user_state WHERE owner=? AND kind=? AND scope=?', (owner, 'updates', guid)).fetchone()
                previous = json.loads(row[0]) if row else None
                value = {'guid': guid, 'available': True, 'count': count, 'previous_count': count, 'delta': 0, 'checked_at': int(time.time()), 'changed_at': None}
                if previous is not None:
                    if count != previous['count']:
                        value.update(previous_count=previous['count'], delta=count-previous['count'], changed_at=int(time.time()))
                    else:
                        value.update(previous_count=previous['previous_count'], delta=previous['delta'], changed_at=previous.get('changed_at'))
                connection.execute('INSERT INTO user_state VALUES (?,?,?,?,?) ON CONFLICT(owner,kind,scope) DO UPDATE SET value=excluded.value,updated=excluded.updated',
                    (owner, 'updates', guid, json.dumps(value), int(time.time())))
            return {'code': 0, 'data': value}
        except Exception as error:
            return error_response(error)
