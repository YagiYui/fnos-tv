import requests
from flask import Blueprint, request, current_app

import Config
from Config import fnos_username, fnos_password
from Fuction import merge_skipped_segments, calculate_repeat_rate, find_skipped_segments, generate_signature
from core import alist_api
from core.Fnos import FnOs
from core.db.base import CRUDBase
from core.db.model.recordDb import RecordDb
from core.db.model.videoConfigDb import videoConfigList, videoConfigUrl
from core.validation import media_identifier, video_config

play = Blueprint('play', __name__, url_prefix='/play')
if fnos_username is not None and fnos_password is not None:
    FnOs.fn_os_ws.start()
    FnOs.login(fnos_username, fnos_password)

api_app = Blueprint('api', __name__, url_prefix='/api')


@api_app.before_request
def before_request():
    if request.endpoint != 'api.get_fn_url':
        if not request.headers.get('Authorization'):
            return {'code': -2, 'msg': '未登录'}, 401
        url = Config.fnos_url + '/v/api/v1/user/info'
        sign = generate_signature({
            'method': "get",
            'url': '/v/api/v1/user/info'
        }, "16CCEB3D-AB42-077D-36A1-F355324E4237")
        try:
            with requests.get(url, timeout=(5, 15), allow_redirects=False, headers={
                'authx': sign,
                'authorization': request.headers.get('authorization')
            }) as response:
                if response.status_code >= 500:
                    return {'code': -1, 'msg': '账户服务暂时不可用'}, 502
                result = response.json()
                if response.status_code != 200 or not isinstance(result, dict) or result.get('code') != 0:
                    return {'code': -2, 'msg': '未登录或登录已失效'}, 401
        except requests.Timeout:
            return {'code': -1, 'msg': '账户服务响应超时'}, 504
        except (requests.RequestException, ValueError):
            return {'code': -1, 'msg': '账户服务暂时不可用'}, 502


@api_app.get('/skipList')
def skip_list():
    data = request.args
    guid = data.get("guid")
    record_db = CRUDBase(RecordDb)
    # record_data_list = record_db.session.query(record_db.model).filter(RecordDb.guid==guid).group_by(RecordDb.episode_guid).all()
    try:
        record_data_dict = record_db.group_by('episode_guid', guid=guid)
    finally:
        record_db.close()
    skipped_segments = {}
    for key, value in record_data_dict.items():
        skipped_segments[key] = find_skipped_segments(value)
    ret_data = []
    if len(skipped_segments) >= 3:
        success_data = calculate_repeat_rate(skipped_segments)
        for item in success_data:
            ret_data += item['data']

    return {
        'code': 0,
        'data': merge_skipped_segments(ret_data)
    }


@api_app.get('/getFnUrl')
def get_fn_url():
    _fnos_url = Config.fnos_url
    if _fnos_url.endswith("/"):
        _fnos_url = _fnos_url[:-1]
    return _fnos_url


@api_app.get('/videoConfig')
def get_video_config():
    episode_guid = request.args.get('episode_guid')
    guid = request.args.get('guid')
    if guid is None or episode_guid is None:
        return {
            'code': 404,
            'msg': '未获取到数据'
        }
    ret_data = {
        "list": [],
        "url": []
    }
    for data in [['list', videoConfigList], ['url', videoConfigUrl]]:
        db = CRUDBase(data[1])
        if data[0] == "list":
            _guid = guid
        else:
            _guid = episode_guid
        try:
            for item in db.filter(guid=_guid):
                ret_data[data[0]].append(item.get_data())
        finally:
            db.close()
    return ret_data


@api_app.post('/videoConfig')
def update_video_config():
    try:
        guid = media_identifier(request.args.get('guid'))
        episode_guid = media_identifier(request.args.get('episode_guid'))
        data = video_config(request.get_json(silent=True))
    except ValueError as error:
        return {'code': -1, 'msg': str(error)}, 400
    db = CRUDBase(videoConfigList)
    try:
        for key, rows in data.items():
            model = videoConfigList if key == 'list' else videoConfigUrl
            scope = guid if key == 'list' else episode_guid
            db.session.query(model).filter_by(guid=scope).delete(synchronize_session=False)
            for row in rows:
                values = {**row, 'guid': scope}
                if key == 'url':
                    values['parent_guid'] = guid
                db.session.add(model(**values))
        db.session.commit()
        return {'code': 0, 'msg': 'ok'}
    except Exception:
        db.session.rollback()
        current_app.logger.warning('Video configuration transaction failed')
        return {'code': -1, 'msg': '保存失败，请稍后重试'}, 500
    finally:
        db.close()


@api_app.get('/play')
def _play():
    if fnos_username is None or fnos_password is None:
        return {
            'code': -1,
            "msg": '未获取到飞牛系统账号密码，不做处理'
        }
    play_path = request.args.get('path')
    mountmgr_list = FnOs.mountmgr_list()
    rsps = mountmgr_list.get('rsp', [])
    cloud_config = [rsp for rsp in rsps if rsp.get('mountPoint') in play_path]
    if not cloud_config:
        return {
            "code": -1,
            'msg': '未获取到对应远程挂载信息'
        }
    cloud_config = cloud_config[0]
    if '/dav' not in cloud_config.get('path'):
        return {
            "code": -1,
            'msg': '此远程疑似非alist'
        }
    alist_api.alist_host = f'{cloud_config.get("proto")}://{cloud_config.get("address")}:{cloud_config.get("port")}'
    login_res = alist_api.login(cloud_config.get('userName'), cloud_config.get('password'))
    alist_api.alist_token = login_res.json().get('data', {}).get('token')
    alist_path = cloud_config.get("path").replace('/dav', '') + play_path.replace(cloud_config.get('mountPoint'), '')
    fs_res = alist_api.fs_get(alist_path)
    return {
        'code': 0,
        'data': fs_res.json().get('data', {}).get('raw_url'),
        'msg': '获取成功'
    }
