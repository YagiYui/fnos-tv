import importlib
import pkgutil
import re
import unicodedata
import hashlib
import time
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor

import tldextract
from flask import Blueprint
from flask import request
import requests
from loguru import logger

import core.danmu as danmu_base
import core.videoSearch as videoSearch
from core.danmu.base import GetDanmuBase
from core.danmu.danmuType import RetDanMuType
from core.db.base import CRUDBase
from core.db.model.videoConfigDb import videoConfigUrl
from core.videoSearch.Base import VideoSearchBase
from Config import fnos_url
from apps.danmu.ass_support import register_ass_routes

danmu = Blueprint('alist', __name__, url_prefix='/danmu')
register_ass_routes(danmu)
from apps.danmu.external_play import register_external_routes
register_external_routes(danmu)
# 遍历 danmu_base 中所有子模块
for finder, mod_name, ispkg in pkgutil.walk_packages(danmu_base.__path__, danmu_base.__name__ + "."):
    importlib.import_module(mod_name)

# 遍历 videoSearch 中所有子模块
for finder, mod_name, ispkg in pkgutil.walk_packages(videoSearch.__path__, videoSearch.__name__ + "."):
    importlib.import_module(mod_name)


def download_barrage(_url):
    # while True:
    for c in GetDanmuBase.__subclasses__():
        if c.domain in _url:
            d = c().get(_url)
            if d:
                return d


def manual_provider(url):
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https') or parsed.username or parsed.password:
        raise ValueError('请输入支持平台的 http/https 播放链接')
    if parsed.port not in (None, 80, 443):
        raise ValueError('不支持此链接端口')
    host = (parsed.hostname or '').lower()
    for provider in GetDanmuBase.__subclasses__():
        domain = provider.domain.lower()
        if host == domain or host.endswith('.' + domain):
            return provider
    raise ValueError('支持 B站、爱奇艺、腾讯视频、优酷、芒果TV、搜狐播放链接')


def manual_search(keyword, source='360'):
    keyword = keyword.strip()
    if not keyword or len(keyword) > 100:
        raise ValueError('请输入 1–100 字的片名或平台播放链接')
    if keyword.startswith(('http://', 'https://')):
        provider = manual_provider(keyword)
        return [{'title': '手动播放链接', 'platform': provider.name, 'url': keyword, 'season': None}]
    provider_name = {'360': 'SearchBy360', 'douban': 'DoubanSearch'}.get(source)
    provider = next((c for c in VideoSearchBase.__subclasses__() if c.__name__ == provider_name), None)
    if not provider:
        raise ValueError('不支持的搜索来源')
    results = []
    seen = set()
    for video in provider().get(keyword) or []:
        platforms = set()
        for url in video.url or []:
            try:
                platform = manual_provider(url)
            except (TypeError, ValueError):
                continue
            # 同一候选的每个平台保留一个入口，进入后列出所有分集。
            key = (video.title, video.season_number, platform.domain)
            if key in seen or platform.domain in platforms:
                continue
            seen.add(key)
            platforms.add(platform.domain)
            results.append({'title': video.title, 'season': video.season_number,
                            'platform': platform.name, 'url': url})
    return results[:50]


@danmu.get('/search')
def search_manual_danmu():
    try:
        return {'code': 0, 'results': manual_search(request.args.get('keyword', ''), request.args.get('source', '360'))}
    except ValueError as error:
        return {'code': -1, 'msg': str(error)}, 400
    except Exception as error:
        logger.warning(f'手动弹幕搜索失败: {type(error).__name__}')
        return {'code': -1, 'msg': '弹幕搜索源暂时不可用，请切换搜索来源或直接粘贴播放链接'}, 502


@danmu.get('/episodes')
def manual_danmu_episodes():
    try:
        url = request.args.get('url', '')
        provider = manual_provider(url)
        episodes = []
        for episode in provider().get_episode_url(url) or []:
            manual_provider(episode.url)
            episodes.append({'title': episode.episodeTitle or str(episode.episodeNumber),
                             'number': str(episode.episodeNumber), 'url': episode.url})
        # 单集视频、电影链接可能没有分集列表，仍允许用户明确选择当前链接。
        return {'code': 0, 'episodes': episodes[:500] or [{'title': '使用此播放链接', 'number': '', 'url': url}]}
    except ValueError as error:
        return {'code': -1, 'msg': str(error)}, 400
    except Exception as error:
        logger.warning(f'手动分集获取失败: {type(error).__name__}')
        return {'code': -1, 'msg': '平台分集获取失败，请直接粘贴具体集数的播放链接'}, 502


def check_manual_login(token):
    if not token:
        return False
    path = '/v/api/v1/user/info'
    nonce, timestamp = '123456', str(int(time.time() * 1000))
    digest = hashlib.md5(b'').hexdigest()
    sign = hashlib.md5('_'.join(['NDzZTVxnRKP8Z0jXg1VAMonaG8akvh', path, nonce, timestamp,
                                digest, '16CCEB3D-AB42-077D-36A1-F355324E4237']).encode()).hexdigest()
    response = requests.get(f'{fnos_url}{path}', timeout=10, headers={
        'Authorization': token, 'authx': f'nonce={nonce}&timestamp={timestamp}&sign={sign}'})
    return response.status_code == 200 and response.json().get('code') == 0


@danmu.post('/select')
def select_manual_danmu():
    try:
        if not check_manual_login(request.headers.get('Authorization', '')):
            return {'code': -2, 'msg': '登录已失效，请重新登录'}, 401
        data = request.get_json(silent=True) or {}
        guid = str(data.get('guid', ''))
        parent_guid = str(data.get('parent_guid', ''))
        url = data.get('url', '')
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}', guid) or len(parent_guid) > 100:
            raise ValueError('缺少有效的当前集标识')
        manual_provider(url)
        db = CRUDBase(videoConfigUrl)
        try:
            db.session.query(videoConfigUrl).filter_by(guid=guid).delete()
            db.session.add(videoConfigUrl(guid=guid, parent_guid=parent_guid, url=url))
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
        finally:
            db.close()
        return {'code': 0, 'msg': '当前集弹幕来源已保存'}
    except ValueError as error:
        return {'code': -1, 'msg': str(error)}, 400
    except Exception as error:
        logger.warning(f'保存手动弹幕来源失败: {type(error).__name__}')
        return {'code': -1, 'msg': '保存弹幕来源失败，请稍后重试'}, 502


def normalized_title(name):
    return re.sub(r'[\s【】\[\]「」『』《》]', '', unicodedata.normalize('NFKC', name or '')).casefold()


def searchVideoData(name: str, tv_num: str, season: bool):
    if not name:
        return []
    url_list = []
    queries = list(dict.fromkeys([name, normalized_title(name)]))
    for query in queries:
        for c in VideoSearchBase.__subclasses__():
            try:
                for v in c().get(query) or []:
                    if normalized_title(v.title) == normalized_title(name) and v.season_number == int(tv_num or 1):
                        url_list += v.url
            except Exception as e:
                logger.warning(f'弹幕搜索源 {c.__name__} 失败: {type(e).__name__}')
        if url_list:
            break
    return list({tldextract.extract(u).domain: u for u in url_list}.values())


def get_episode_url(platform_url_list):
    url_dict = {}
    for platform_url in platform_url_list:
        for c in GetDanmuBase.__subclasses__():
            if c.domain in platform_url:
                try:
                    d = c().get_episode_url(platform_url)
                except Exception as e:
                    logger.warning(f'弹幕分集解析 {c.__name__} 失败: {type(e).__name__}')
                    continue
                for v in d:
                    key = str(v.episodeNumber)
                    if key not in url_dict:
                        url_dict[key] = []
                    if v.url not in url_dict[key]:
                        url_dict[key].append(v.url)
    return url_dict


def fetch_emoji(url):
    """获取单个URL的表情数据"""
    try:
        for c in GetDanmuBase.__subclasses__():
            if c.domain in url:
                return {"url": url, "data": c().getImg(url)}
        return {"url": url, "data": []}
    except Exception as e:
        logger.error(f"获取表情失败 {url}: {str(e)}")
        return {"url": url, "data": []}


def fetch_danmu(url, episode_key):
    """获取单个URL的弹幕数据"""
    try:
        logger.info(f"获取弹幕: {url} (集数: {episode_key})")
        danmu_data = download_barrage(url)
        return {"key": episode_key, "data": danmu_data}
    except Exception as e:
        logger.error(f"获取弹幕失败 {url}: {str(e)}")
        return {"key": episode_key, "data": None}


def get_url_dict(douban_id, title=None, season_number=None, episode_number=None, season=None, guid=None,
                 episode_title=None, parent_guid=None):
    if episode_number:
        episode_number = int(episode_number)
    url_dict = {}
    platform_url_list = []

    db = CRUDBase(videoConfigUrl)
    # 已保存的当前集链接应优先使用，不能被后续自动搜索覆盖。
    try:
        if guid and episode_number:
            saved = [item.url for item in db.filter(guid=guid) if item.url]
            if saved:
                return {str(episode_number): list(dict.fromkeys(saved))}
        if parent_guid:
            platform_url_list = list(dict.fromkeys(item.url for item in db.filter(parent_guid=parent_guid) if item.url))
    finally:
        db.close()

    # 三方数据搜索
    if len(platform_url_list) == 0:
        platform_url_list = searchVideoData(title, season_number, season)
    url_dict = get_episode_url(platform_url_list)

    if str(episode_number) in url_dict or episode_title in url_dict:
        url_list = url_dict.get(str(episode_number), url_dict.get(episode_title, []))
        if guid is not None:
            # 如果匹配到直接保存到数据库内
            db = CRUDBase(videoConfigUrl)
            try:
                for item in url_list:
                    db.add(guid=guid, url=item, parent_guid=parent_guid)
            finally:
                db.close()
        url_dict = {
            str(episode_number): url_list
        }
    return url_dict


@danmu.get('/get')
def get_danmu():
    try:
        douban_id = request.args.get('douban_id', None)
        douban_id = None if douban_id == "" or douban_id is None or douban_id == 'undefined' else douban_id
        episode_number = request.args.get('episode_number', None)
        episode_title = request.args.get('episode_title', None)
        title = request.args.get('title')
        season_number = request.args.get('season_number', None)
        season = True if request.args.get('season') == 'true' else False
        url = request.args.get('url', None)
        guid = request.args.get('guid', None)
        parent_guid = request.args.get('parent_guid', None)
        _type = request.args.get('type', 'json')
    except Exception as e:
        return {
            "code": -1,
            "msg": '解析参数失败'
        }

    if url is not None and url != "":
        try:
            manual_provider(url)
        except ValueError as error:
            return {'code': -1, 'msg': str(error)}, 400
        danmu_data: RetDanMuType = download_barrage(url)
        if danmu_data is None:
            return {'code': -1, 'msg': '此链接未返回弹幕，请尝试其他来源'}, 502
        danmu_data.list.sort(key=lambda x: x.time)
        if _type == 'json':
            return [item.__dict__() for item in danmu_data.list]
        else:
            return danmu_data.xml

    all_danmu_data = {}
    url_dict = get_url_dict(douban_id, title, season_number, episode_number, season, guid, episode_title, parent_guid)

    # 准备任务列表
    tasks = []
    for k, v in url_dict.items():
        for u in v:
            tasks.append((u, k))

    # 使用线程池并行获取所有弹幕
    if tasks:
        logger.info(f"开始并行获取 {len(tasks)} 个链接的弹幕")
        with ThreadPoolExecutor(max_workers=min(20, len(tasks))) as executor:
            results = list(executor.map(lambda args: fetch_danmu(*args), tasks))

            # 处理结果
            for result in results:
                if result["data"] is not None:
                    k = str(result["key"])
                    if k not in all_danmu_data.keys():
                        all_danmu_data[k] = []
                    all_danmu_data[k] += result["data"].list
    # 对数据进行排序
    for key, value in all_danmu_data.items():
        value.sort(key=lambda x: x.time)
        if _type == 'xml':
            all_danmu_data[key] = RetDanMuType(value).xml
        else:
            all_danmu_data[key] = [item.__dict__() for item in value]

    return all_danmu_data


@danmu.get('/getEmoji')
def get_emoji():
    try:
        douban_id = request.args.get('douban_id', None)
        douban_id = None if douban_id == "" or douban_id is None or douban_id == 'undefined' else douban_id
        episode_number = request.args.get('episode_number', None)
        episode_title = request.args.get('episode_title', None)
        title = request.args.get('title')
        season_number = request.args.get('season_number', None)
        season = True if request.args.get('season') == 'true' else False
        url = request.args.get('url', None)
        guid = request.args.get('guid', None)
        parent_guid = request.args.get('parent_guid', None)
        _type = request.args.get('type', 'json')
    except Exception as e:
        return {
            "code": -1,
            "msg": '解析参数失败'
        }

    if url is not None and url != "":
        url_dict = {
            "1": url
        }
    else:
        url_dict = get_url_dict(douban_id, title, season_number, episode_number, season, guid, episode_title,
                                parent_guid=parent_guid)
    emoji_data = {}
    if len(url_dict.keys()) == 0:
        return emoji_data

    # 获取所有需要处理的URL
    urls = url_dict.get(list(url_dict.keys())[0], [])

    # 使用线程池并行获取所有表情
    if urls:
        logger.info(f"开始并行获取 {len(urls)} 个链接的表情")
        with ThreadPoolExecutor(max_workers=min(10, len(urls))) as executor:
            all_emoji_data = list(executor.map(fetch_emoji, urls))

            # 处理结果
            for result in all_emoji_data:
                if result["data"]:
                    for d in result["data"]:
                        emoji_data[d['emoji_code']] = d['emoji_url']

    return emoji_data

@danmu.get('/download')
def download_danmu():
    try:
        url = request.args.get('url')
        if not url:
            return {
                "code": -1,
                "msg": '缺少url参数'
            }
    except Exception as e:
        return {
            "code": -1,
            "msg": '解析参数失败'
        }

    # 默认文件名
    filename = 'danmu.xml'
    
    # 获取弹幕数据并下载
    danmu_data: RetDanMuType = download_barrage(url)
    if danmu_data:
        danmu_data.list.sort(key=lambda x: x.time)
        xml_content = danmu_data.xml
        response = Response(xml_content, mimetype='application/xml')
        response.headers.set('Content-Disposition', f'attachment; filename={filename}')
        return response
    
    # 如果没有找到任何弹幕数据，返回空的XML
    empty_xml = '''<?xml version="1.0" encoding="utf-8"?>
<i>
</i>'''
    response = Response(empty_xml, mimetype='application/xml')
    response.headers.set('Content-Disposition', f'attachment; filename={filename}')
    return response

