import json
from typing import List

import requests
from loguru import logger
from requests import Response
import concurrent.futures
from tqdm import tqdm

from core.danmu.danmuType import EpisodeDataDto
from core.danmu.danmuType import DanMuType, RetDanMuType
from apps.danmu.sources import guard_source_request


class GetDanmuBase(object):
    data_list = []
    name = ""
    domain = ""

    def get_data_dict(self) -> DanMuType:
        return DanMuType()

    def request_data(self, session, method, url, status_code=None, **kwargs):
        """
        统一的请求函数，用于网络请求
        :param session: 
        :param method: 
        :param url: 
        :param status_code: 
        :param kwargs: 
        :return: 
        """
        try:
            guard_source_request(url)
            kwargs.setdefault('timeout', 15)
            res = session.request(method, url, **kwargs)
            return res
        except Exception as e:
            logger.error(e)
            return

    def get_link(self, url) -> List:
        return []

    def main(self, links: List) -> List[requests.Response]:
        """
        获取弹幕的主逻辑
        """
        pass

    def parse(self, data: Response) -> list[DanMuType]:
        """
        解析返回的原始数据
        """
        return []

    def get(self, url, _type='json'):
        self.data_list = []
        try:
            links = self.get_link(url)
            self.main(links)
            parse_data = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(10, len(self.data_list))) as executor:
                results = list(tqdm(executor.map(self.parse, self.data_list),
                                    total=len(self.data_list),
                                    desc="弹幕数据解析"))
                for result in results:
                    parse_data.extend(result)
            return RetDanMuType(parse_data)
        except Exception as e:
            return RetDanMuType([])

    def getImg(self, url):
        """
        获取弹幕的表情链接
        """
        return []

    def time_to_second(self, _time: list):
        s = 0
        m = 1
        for d in _time[::-1]:
            s += m * int(d)
            m *= 60
        return s

    def get_episode_url(self, url: str) -> List[EpisodeDataDto]:
        """
        根据一个链接获取所有剧集链接，返回一个字典
        :param url: 
        :return: 
        """
        return []
