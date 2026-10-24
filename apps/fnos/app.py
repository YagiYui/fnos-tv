import requests
from flask import Blueprint, current_app, request

from Config import fnos_url
from core.db.base import CRUDBase
from core.db.model.recordDb import RecordDb
from core.validation import playback_record
from core.media_scope import verify_media_scope

fnos_app = Blueprint('fnos', __name__, url_prefix='/fnos')


@fnos_app.post('/v/api/v1/play/record')
def record():
    """
    记录播放记录，推算跳过片段
    :return: 
    """
    data = request.get_json(silent=True)
    try:
        local_record = playback_record(data)
    except ValueError as error:
        return {'code': -1, 'msg': str(error)}, 400
    try:
        with requests.post(f"{fnos_url}/v/api/v1/play/record", json=data,
                           timeout=(5, 20), allow_redirects=False, headers={
                               key: request.headers[key]
                               for key in ('Cookie', 'Authorization', 'authx') if key in request.headers
                           }) as response:
            status = response.status_code
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError('Unexpected response')
    except requests.Timeout:
        return {'code': -1, 'msg': '记录进度超时，请稍后重试'}, 504
    except (requests.RequestException, ValueError):
        return {'code': -1, 'msg': '记录进度服务暂时不可用'}, 502
    # Auxiliary recording must neither accept a rejected request nor hide upstream success.
    if 200 <= status < 300 and result.get('code') == 0:
        record_db = None
        try:
            verify_media_scope(fnos_url,request.headers.get('Authorization',''),local_record['guid'],local_record['episode_guid'])
            record_db = CRUDBase(RecordDb)
            record_db.add(**local_record)
        except Exception:
            if record_db is not None:
                record_db.session.rollback()
            current_app.logger.warning('Auxiliary playback recording failed')
        finally:
            if record_db is not None:
                record_db.close()
    return result, status
