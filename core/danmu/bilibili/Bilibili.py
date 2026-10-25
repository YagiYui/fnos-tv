import concurrent.futures
import re
import time
import urllib.parse
from functools import partial
from functools import reduce
from hashlib import md5
from typing import List

from curl_cffi import requests
from loguru import logger
from tqdm import tqdm

import Config
import core.danmu.bilibili.bilibilidm_pb2 as Danmaku
from Fuction import request_data
from core.danmu.danmuType import EpisodeDataDto
from core.danmu.base import GetDanmuBase


class GetDanmuBilibili(GetDanmuBase):
    name = "B站"
    domain = "bilibili.com"

    mixinKeyEncTab = [
        46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
        33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
        61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
        36, 20, 34, 44, 52
    ]

    def __init__(self):
        self.api_video_info = "https://api.bilibili.com/x/web-interface/view"
        self.api_epid_cid = "https://api.bilibili.com/pgc/view/web/ep/list"
        self.img_key, self.sub_key = self.getWbiKeys()
        self.config = Config.config

    def get_link(self, url) -> List[str]:
        if url.find("bangumi/") != -1 and url.find("ep") != -1:
            epid = re.findall(r"ep(\d+)", url)[0]
            params = {
                "ep_id": epid
            }
            res = request_data("GET", url=self.api_epid_cid, params=params, impersonate="chrome110")
            res_json = res.json()
            if res_json.get("code") != 0:
                logger.error("获取番剧信息失败")
                return []

            target_episode = None
            for episode in res_json.get("result", {}).get("episodes", []):
                if episode.get("id", 0) == int(epid):
                    target_episode = episode
                    break
            if target_episode:
                ret_data = []
                # for i in range(1, int(target_episode.get('duration', 0) / 360) + 1):
                for i in range(1, 20):
                    params = {
                        'type': 1,  # 弹幕类型
                        'oid': target_episode.get("cid"),  # cid
                        'segment_index': i  # 弹幕分段
                    }
                    signed_params = self.encWbi(
                        params=params,
                        img_key=self.img_key,
                        sub_key=self.sub_key
                    )
                    ret_data.append(
                        'https://api.bilibili.com/x/v2/dm/wbi/web/seg.so?' + urllib.parse.urlencode(signed_params))

                return ret_data
        return []

    def parse(self, item):
        data_list = []
        data = item.content
        danmaku_seg = Danmaku.DmSegMobileReply()
        danmaku_seg.ParseFromString(data)
        for elem in danmaku_seg.elems:
            _d = self.get_data_dict()
            _d.text = elem.content
            _mode = elem.mode
            mode = 0
            match _mode:
                case 1 | 2 | 3:
                    mode = 0
                case 4:
                    mode = 2
                case 5:
                    mode = 1
            _d.time = int(elem.progress / 1000)
            _d.mode = mode
            _d.style['size'] = elem.fontsize
            _d.color = elem.color
            data_list.append(_d)

        return data_list

    def main(self, links: List[str]):
        self.data_list = []
        cookie = ""
        if 'BILIBILI' in self.config.keys():
            cookie = self.config["BILIBILI"].get('cookie', '')
        headers = {
            'cookie': cookie
        }
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(10, len(links))) as executor:
            fun = partial(self.request_data, requests, "GET", impersonate="chrome110", headers=headers)
            results = list(tqdm(executor.map(fun, links),
                                total=len(links),
                                desc="哔哩哔哩弹幕获取"))
            self.data_list.extend([i for i in results if i.status_code == 200])

        return self.data_list

    def get_episode_url(self, url) -> List[EpisodeDataDto]:
        data_list = []
        if url.find("bangumi/") != -1 and url.find("ep") != -1:
            epid = re.findall(r"ep(\d+)", url)[0]
            params = {
                "ep_id": epid
            }
            res = request_data("GET", url=self.api_epid_cid, params=params, impersonate="chrome110")
            res_json = res.json()
            for item in res_json.get('result', {}).get('episodes', []):
                if item.get('section_type') == 0:
                    data = EpisodeDataDto(
                        url=item.get('share_url'),
                        episodeTitle=item.get('show_title'),
                        episodeNumber=item.get('title')
                    )
                    data_list.append(data)
        return data_list

    def getMixinKey(self, orig: str):
        '对 imgKey 和 subKey 进行字符顺序打乱编码'
        return reduce(lambda s, i: s + orig[i], self.mixinKeyEncTab, '')[:32]

    def encWbi(self, params: dict, img_key: str, sub_key: str):
        '为请求参数进行 wbi 签名'
        mixin_key = self.getMixinKey(img_key + sub_key)
        curr_time = round(time.time())
        params['wts'] = curr_time  # 添加 wts 字段
        params = dict(sorted(params.items()))  # 按照 key 重排参数
        # 过滤 value 中的 "!'()*" 字符
        params = {
            k: ''.join(filter(lambda chr: chr not in "!'()*", str(v)))
            for k, v
            in params.items()
        }
        query = urllib.parse.urlencode(params)  # 序列化参数
        wbi_sign = md5((query + mixin_key).encode()).hexdigest()  # 计算 w_rid
        params['w_rid'] = wbi_sign
        return params

    def getWbiKeys(self) -> tuple[str, str]:
        '获取最新的 img_key 和 sub_key'
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3',
            'Referer': 'https://www.bilibili.com/'
        }
        resp = request_data('GET', 'https://api.bilibili.com/x/web-interface/nav', headers=headers)
        resp.raise_for_status()
        json_content = resp.json()
        img_url: str = json_content['data']['wbi_img']['img_url']
        sub_url: str = json_content['data']['wbi_img']['sub_url']
        img_key = img_url.rsplit('/', 1)[1].split('.')[0]
        sub_key = sub_url.rsplit('/', 1)[1].split('.')[0]
        return img_key, sub_key


if __name__ == '__main__':
    a = GetDanmuBilibili().get_episode_url(
        "https://www.bilibili.com/bangumi/play/ep1231553?spm_id_from=333.337.0.0&from_spmid=666.25.episode.0")
    print()
