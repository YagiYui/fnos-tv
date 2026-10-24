from typing import List
from urllib.parse import urlparse

from flask import Blueprint, request
from flask_caching import Cache

from Config import cache
from apps.dandan.dandanType import AnimeRetDto
from apps.danmu.app import download_barrage
from apps.danmu.sources import registry, SEARCHES
from core.danmu.base import GetDanmuBase
from core.danmu.danmuType import EpisodeDataDto, RetDanMuType
from core.videoSearch.Base import VideoSearchBase
from core.videoSearch.videoSearchType import VideoDataDto

dandan = Blueprint('dandan', __name__, url_prefix='/dandan/api/v2')


def convert_to_ascii_sum(sid: str) -> int:
    hash_value = 5381
    for c in sid:
        hash_value = ((hash_value * 33) ^ ord(c)) & 0xFFFFFFFF
    hash_value = hash_value % 9999999
    # 确保至少 5 位
    if hash_value < 10000:
        hash_value += 10000
    return hash_value

@cache.cached(query_string=True)
@dandan.get("/search/anime")
def search_anime():
    keyword = request.args.get('keyword')
    if keyword is None:
        return {
            "errorCode": 400,
            "success": False,
            "errorMessage": "未传递keyword",
            "animes": [
            ]
        }
    video_list: List[VideoDataDto] = []
    search_names = {key: name for key, _, name in SEARCHES}
    for source in registry.enabled('searches'):
        c = next((candidate for candidate in VideoSearchBase.__subclasses__() if candidate.__name__ == search_names.get(source)), None)
        if c is None:
            continue
        try:
            d = c().get(keyword)
            if d:
                for video in d:
                    video.url = registry.ordered_urls(video.url)
                d = [video for video in d if video.url]
                video_list += d
        except Exception as e:
            pass
    dandan_anime_data_list = []
    for video in video_list:
        dandan_anime_data = AnimeRetDto.VideoDataDto2AnimeRetDto(video)
        video.url = list(
            (lambda s: [u for u in video.url if not (urlparse(u).netloc in s or s.add(urlparse(u).netloc))])(set()))
        dandan_anime_data.animeId = convert_to_ascii_sum(dandan_anime_data.animeTitle + str(video.season_number) + '/'.join(video.url))
        dandan_anime_data.bangumiId = str(dandan_anime_data.animeId)
        cache.set('anime' + dandan_anime_data.bangumiId, video, timeout=0)

        dandan_anime_data_list.append(dandan_anime_data)
    return {
        "errorCode": 0,
        "success": True,
        "errorMessage": "",
        "animes": dandan_anime_data_list
    }


def get_episode_url(platform_url_list):
    data_list = []
    for platform_url in platform_url_list:
        if not registry.allowed(platform_url):
            continue
        for c in GetDanmuBase.__subclasses__():
            if c.domain in platform_url:
                d = c().get_episode_url(platform_url)
                data_list += d
    return data_list

@cache.cached(query_string=True)
@dandan.get("/bangumi/<bangumiId>")
def bangumi(bangumiId):
    video_data: VideoDataDto = cache.get('anime' + bangumiId)
    if video_data is None:
        return {
            "errorCode": 404,
            "success": False,
            "errorMessage": f"未获取到{bangumiId}对应缓存数据，请重新搜索",
        }
    episode_data_list:List[EpisodeDataDto] = get_episode_url(video_data.url)
    for episode_data in episode_data_list:
        episode_data.episodeId = convert_to_ascii_sum(episode_data.url)
        cache.set('bangumi' + str(episode_data.episodeId), episode_data.url, timeout=0)
    return {
        "errorCode": 0,
        "success": True,
        "errorMessage": "",
        "bangumi": {
            "type": "web",
            "typeDescription": "网络放送",
            "titles": [
                {
                    "language": "主标题",
                    "title": video_data.title
                },
            ],
            "seasons": [],
            "episodes": episode_data_list
        }
    }

@cache.cached(query_string=True)
@dandan.get("/comment/<episodeId>")
def comment(episodeId):
    url = cache.get('bangumi' + episodeId)
    if url is None:
        return {
            "errorCode": 404,
            "success": False,
            "errorMessage": f"未获取到{episodeId}对应缓存数据，请重新搜索",
        }
    danmu_data:RetDanMuType = download_barrage(url)
    return {
        'count': len(danmu_data.list),
        'comments': danmu_data.dandan
    }
