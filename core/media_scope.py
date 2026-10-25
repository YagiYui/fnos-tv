"""Verify the caller's actual media and file scope before using shared NAS state."""
import requests
from core.validation import media_identifier
from Fuction import generate_signature


class MediaUnavailable(Exception):
    pass


def official_read(base, token, path, params=None):
    if not token:
        raise PermissionError('请重新登录')
    route = '/v' + path
    signature = generate_signature({'method':'GET','url':route,'params':params or {}}, '16CCEB3D-AB42-077D-36A1-F355324E4237')
    try:
        with requests.get(base.rstrip('/') + route, params=params, headers={'Authorization':token,'authx':signature}, timeout=(5,15), allow_redirects=False) as response:
            if response.status_code >= 500:
                raise MediaUnavailable('媒体服务暂时不可用')
            value = response.json()
            if response.status_code != 200 or not isinstance(value,dict) or value.get('code') != 0:
                raise PermissionError('无权访问此媒体')
            return value.get('data')
    except (requests.RequestException, ValueError):
        raise MediaUnavailable('媒体授权核查暂时不可用') from None


def read_media_item(base, token, guid):
    guid = media_identifier(guid)
    value = official_read(base, token, '/api/v1/item/' + guid)
    if not isinstance(value,dict) or value.get('guid') != guid:
        raise PermissionError('返回的媒体不属于当前请求')
    return value


def verify_media_scope(base, token, parent_guid, item_guid=None):
    parent_guid = media_identifier(parent_guid)
    item_guid = media_identifier(item_guid or parent_guid)
    item = read_media_item(base, token, item_guid)
    if item_guid != parent_guid:
        if item.get('parent_guid') != parent_guid:
            raise PermissionError('当前分集不属于所选季度')
        read_media_item(base, token, parent_guid)
    return item


def verify_media_file(base, token, item_guid, media_guid, path):
    read_media_item(base, token, item_guid)
    media_identifier(media_guid)
    streams = official_read(base, token, '/api/v1/stream/list/' + item_guid, {'before_play':1})
    if not isinstance(streams,dict) or not isinstance(streams.get('files'),list):
        raise PermissionError('未能核实媒体文件')
    selected = next((row for row in streams['files'] if row.get('guid') == media_guid),None)
    if not selected or not isinstance(path,str) or selected.get('path') != path:
        raise PermissionError('远程文件不属于当前媒体')
    return selected
