import requests
from urllib.parse import urlsplit
from flask import Blueprint, request, current_app, g

import Config
from Config import fnos_username, fnos_password
from Fuction import merge_skipped_segments, calculate_repeat_rate, find_skipped_segments, generate_signature
from core.Fnos import FnOs
from core.db.base import CRUDBase
from core.db.model.recordDb import RecordDb
from core.db.model.videoConfigDb import videoConfigList, videoConfigUrl
from core.validation import media_identifier, video_config
from core.media_scope import verify_media_scope, verify_media_file, MediaUnavailable

play = Blueprint('play', __name__, url_prefix='/play')
if fnos_username is not None and fnos_password is not None:
    FnOs.fn_os_ws.start()
    FnOs.login(fnos_username, fnos_password)

api_app = Blueprint('api', __name__, url_prefix='/api')


@api_app.after_request
def private_headers(response):
    if request.endpoint != 'api.get_fn_url':
        response.headers['Cache-Control'] = 'no-store'
        response.vary.add('Authorization')
    return response


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
                user = result.get('data')
                if not isinstance(user, dict):
                    return {'code': -2, 'msg': '未取得有效账户权限'}, 401
                g.can_edit_shared = user.get('is_admin') in (True, 1)
            if request.endpoint in ('api.skip_list','api.get_video_config','api.update_video_config'):
                try:
                    guid = media_identifier(request.args.get('guid'))
                    episode = media_identifier(request.args.get('episode_guid')) if request.endpoint != 'api.skip_list' else guid
                except ValueError:
                    return {'code': -1, 'msg': '缺少有效媒体范围'}, 400
                verify_media_scope(Config.fnos_url, request.headers.get('Authorization',''), guid, episode)
                if request.endpoint == 'api.update_video_config' and not g.can_edit_shared:
                    return {'code': -2, 'msg': '只有管理员可修改家庭共享配置；个人弹幕来源请使用来源偏好'}, 403
            if request.endpoint == 'api._play':
                try:
                    item_guid = media_identifier(request.args.get('item_guid'))
                    media_guid = media_identifier(request.args.get('media_guid'))
                except ValueError:
                    return {'code': -1, 'msg': '请刷新播放页，直链请求需要当前媒体与文件标识'}, 400
                verify_media_file(Config.fnos_url,request.headers.get('Authorization',''),item_guid,media_guid,request.args.get('path'))
        except PermissionError:
            return {'code': -2, 'msg': '无权访问当前媒体或媒体范围不匹配'}, 403
        except MediaUnavailable:
            return {'code': -1, 'msg': '媒体授权核查暂时不可用'}, 502
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
        "url": [],
        "permissions": {'edit_shared': bool(getattr(g,'can_edit_shared',False))}
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
    try:
        play_path = request.args.get('path')
        candidates = []
        for row in FnOs.mountmgr_list().get('rsp', []):
            mount = row.get('mountPoint')
            if isinstance(mount,str) and mount and (play_path == mount or play_path.startswith(mount.rstrip('/') + '/')):
                candidates.append(row)
        if not candidates:
            return {'code': -1, 'msg': '未获取到对应远程挂载信息'}
        config = max(candidates,key=lambda row:len(row['mountPoint']))
        root = config.get('path','')
        if root != '/dav' and not root.startswith('/dav/'):
            return {'code': -1, 'msg': '此远程挂载不支持当前直链方式'}
        address = str(config.get('address',''))
        if ':' in address and not address.startswith('['): address = '[' + address + ']'
        host = f'{config.get("proto")}://{address}:{config.get("port")}'
        target = urlsplit(host)
        if target.scheme not in ('http','https') or not target.hostname or target.username or target.password or target.path:
            raise ValueError('无效挂载服务')
        path = root[len('/dav'):] + play_path[len(config['mountPoint']):]
        # A session belongs to this request; concurrent mounts never share global tokens/hosts.
        with requests.Session() as session:
            with session.post(host+'/api/auth/login',json={'username':config.get('userName'),'password':config.get('password'),'otp_code':''},timeout=(5,20),allow_redirects=False) as response:
                response.raise_for_status(); login = response.json()
                if response.status_code != 200 or login.get('code') != 200: raise ValueError('登录失败')
                token = login.get('data',{}).get('token')
                if not isinstance(token,str) or not token: raise ValueError('鉴权不可用')
            with session.post(host+'/api/fs/get',json={'path':path},headers={'Authorization':token,'Cache-Control':'no-cache'},timeout=(5,20),allow_redirects=False) as response:
                response.raise_for_status(); result = response.json()
                if response.status_code != 200 or result.get('code') != 200: raise ValueError('文件不可用')
                raw = result.get('data',{}).get('raw_url')
                if not isinstance(raw,str) or urlsplit(raw).scheme not in ('http','https'): raise ValueError('地址无效')
        return {'code':0,'data':raw,'msg':'获取成功'}
    except Exception:
        return {'code':-1,'msg':'远程挂载播放地址暂时不可用'},502
