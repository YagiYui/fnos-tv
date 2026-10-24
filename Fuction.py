import hashlib
import json
import math
import os
import random
import re
import subprocess
import sys
import time
import zipfile
from itertools import combinations
from typing import Dict, List
from urllib import parse
from urllib.parse import parse_qsl, urlencode, unquote
from urllib.parse import urlparse

import requests as req
from curl_cffi import requests
from retrying import retry
from apps.danmu.sources import guard_source_request, SourceDisabled


@retry(stop_max_attempt_number=5, wait_random_min=1000, wait_random_max=2000,
       retry_on_exception=lambda error: not isinstance(error, SourceDisabled))
def request_data(method, url, status_code=None, **kwargs):
    """
    发送请求
    :param method: 请求方式
    :param url: 请求URL
    :param status_code: 成功的状态码
    :param kwargs:
    :return:
    """
    guard_source_request(url)
    if 'timeout' not in kwargs.keys():
        kwargs['timeout'] = 15
    if 'impersonate' in kwargs.keys():
        res = requests.request(method, url, **kwargs)
    else:
        res = req.request(method, url, **kwargs)
    # res.encoding = res.apparent_encoding
    if status_code:
        if res.status_code == status_code:
            return res
        else:
            return
    return res


def get_platform_link(douban_id):
    res = request_data("GET", f'https://movie.douban.com/subject/{douban_id}/', headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    })
    urls = re.findall(r'https://www\.douban\.com/link2/\?url=(.*?)",.+ep:.+"(.*?)"', res.text)
    url_dict = {}
    for url in urls:
        if url[1] in url_dict.keys():
            url_dict[str(url[1])].append(parse.unquote(url[0]))
        else:
            url_dict[str(url[1])] = [parse.unquote(url[0])]

    return url_dict


def get_md5(str):
    md5 = hashlib.md5()
    md5.update(str.encode('utf-8'))
    return md5.hexdigest()


def fetch_latest_dist_zip(repo, asset_name='dist.zip'):
    """
    拉取指定 GitHub 仓库最新发布版中命名为 dist.zip 的下载链接，并下载该文件。

    参数:
      repo: 字符串，仓库地址，格式为 "用户名/仓库名"
      asset_name: 要查找的资产名称，默认为 "dist.zip"

    返回:
      下载的文件路径，如果下载失败则退出程序。
    """
    # GitHub API URL，用于获取最新发布信息
    api_url = f"https://api.github.com/repos/{repo}/releases/latest"
    response = requests.get(api_url, headers={"User-Agent": "Awesome-Octocat-App"})
    if response.status_code != 200:
        print(f"获取发布信息失败，状态码：{response.status_code}", file=sys.stderr)
        sys.exit(1)

    release_info = response.json()
    assets = release_info.get("assets", [])
    download_url = None

    # 遍历发布中的所有资产，查找名称为 asset_name 的文件
    for asset in assets:
        if asset.get("name") == asset_name:
            download_url = asset.get("browser_download_url")
            break

    if not download_url:
        print(f"未找到名为 {asset_name} 的资产", file=sys.stderr)
        return

    print(f"找到 {asset_name} 的下载链接：{download_url}")

    # 下载该文件
    download_response = requests.get(download_url, stream=True, headers={"User-Agent": "Awesome-Octocat-App"})
    if download_response.status_code != 200:
        print(f"下载文件失败，状态码：{download_response.status_code}", file=sys.stderr)
        return

    file_path = asset_name
    with open(file_path, "wb") as f:
        for chunk in download_response.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)

    print(f"成功下载到 {file_path}")
    return file_path


def unzip_file(zip_path, extract_dir=None):
    """
    解压指定的 zip 文件到目标目录

    参数:
      zip_path: zip 文件路径
      extract_dir: 目标目录，若为 None 则解压到 zip 文件所在目录

    返回:
      解压后的文件列表
    """
    if extract_dir is None:
        extract_dir = os.path.dirname(zip_path)

    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        # 解压所有文件
        zip_ref.extractall(extract_dir)
        # 返回所有解压出来的文件名列表
        return zip_ref.namelist()


def find_skipped_segments(records):
    """
    寻找跳过的片段
    :param records: 
    :return: 
    """
    skipped_segments = []

    # 先按 create_time 排序
    records.sort(key=lambda x: x['create_time'])

    base_data = [dict(**records[0])]
    base_data[0]['time'] = 0
    records = base_data + records

    for i in range(len(records) - 1):
        cur = records[i]
        nxt = records[i + 1]

        # 拆变量
        t1 = cur['time']
        t2 = nxt['time']
        ts1 = cur['create_time']
        ts2 = nxt['create_time']
        speed = float(cur.get('playback_speed', 1.0))

        # 时间间隔
        dt_real = ts2 - ts1
        dt_expected = dt_real * speed
        dt_video = t2 - t1
        skipped = max(0, dt_video - dt_expected)

        if skipped > 3:
            skipped_segments.append({
                'id': cur['id'],
                'guid': cur['guid'],
                'episode_guid': cur['episode_guid'],
                'create_time': cur['create_time'],
                'skipped_start': t1,
                'skipped_end': t2,
                'skipped': round(skipped, 2),
                'speed': speed,
                'real_interval': dt_real,
                'video_jump': dt_video
            })

    return skipped_segments


def calculate_repeat_rate(data, tolerance=15, threshold=0.6):
    """
    寻找多集跳过片段中重合率大于70%的，这样才能确定没有判断错误
    :param data: 
    :param tolerance: 
    :param threshold: 
    :return: 
    """
    results = []
    for key1, key2 in combinations(data.keys(), 2):
        segs1 = data[key1]
        segs2 = data[key2]
        repeat_count = 0

        matched2 = set()  # 避免 segs2 中一个段被重复匹配
        ret_data = []

        for seg1 in segs1:
            for idx2, seg2 in enumerate(segs2):
                if idx2 in matched2:
                    continue
                if abs(seg1['skipped_start'] - seg2['skipped_start']) <= tolerance and \
                        abs(seg1['skipped_end'] - seg2['skipped_end']) <= tolerance:
                    repeat_count += 1
                    matched2.add(idx2)
                    ret_data.append(seg2)
                    break

        total_segments = len(segs1) + len(segs2)
        repeat_rate = (2 * repeat_count) / total_segments if total_segments > 0 else 0
        if repeat_rate >= threshold:
            results.append({
                'episode_pair': (key1, key2),
                'repeat_count': repeat_count,
                'total_segments': total_segments,
                'repeat_rate': repeat_rate,
                'data': ret_data
            })
    return results


def merge_skipped_segments(segments):
    """
    去除重复的区间
    :param segments: 
    :return: 
    """
    # 先按 skipped_start 排序
    segments = sorted(segments, key=lambda x: x['skipped_start'])

    merged = []
    for seg in segments:
        if not merged:
            merged.append(seg)
        else:
            last = merged[-1]
            # 判断是否有重叠
            if seg['skipped_start'] <= last['skipped_end']:
                # 有重叠，保留更大的段（区间更大）
                len_last = last['skipped_end'] - last['skipped_start']
                len_cur = seg['skipped_end'] - seg['skipped_start']
                if len_cur < len_last:
                    merged[-1] = seg  # 替换为更短的那个
            else:
                merged.append(seg)
    return merged


def parse_url(o: str):
    """
    将 URL 拆分为路径和参数字典，过滤掉值为 'undefined' 或 'null' 的参数
    :param o: 原始 URL 字符串
    :return: (path, params_dict)
    """
    parts = o.split('?', 1)
    path = parts[0]
    params = {}
    if len(parts) > 1 and parts[1]:
        for k, v in parse_qsl(parts[1], keep_blank_values=True):
            if v not in ('undefined', 'null'):
                params[k] = v
    return path, params


def hash_signature_data(o: str = '') -> str:
    """
    对字符串进行解码并计算 MD5，若解码失败则直接对原始串计算
    :param o: 原始字符串（可能包含百分号编码）
    :return: MD5 十六进制摘要
    """
    try:
        # 将无效百分号转义后再解码
        safe = o.replace('%(?![0-9A-Fa-f]{2})', '%25')
        decoded = unquote(safe)
        return hashlib.md5(decoded.encode('utf-8')).hexdigest()
    except Exception:
        return hashlib.md5(o.encode('utf-8')).hexdigest()


def rc(t) -> str:
    """
    返回对象类型名称
    """
    return type(t).__name__


def is_undefined(t) -> bool:
    """
    判断是否为 None（映射 JS 中的 undefined）
    """
    return t is None


def is_null(t) -> bool:
    """
    判断是否为 None（映射 JS 中的 null）
    """
    return t is None


def stringify_params(o: dict = None) -> str:
    """
    将字典按键排序并编码为查询字符串，过滤掉 None 值，空格编码为 %20
    :param o: 参数字典
    :return: 排序后的查询字符串
    """
    if o is None:
        o = {}
    filtered = {k: v for k, v in sorted(o.items()) if not is_undefined(v) and not is_null(v)}
    qs = urlencode(filtered, doseq=True)
    return qs.replace('+', '%20')


def get_random_number(o: float = 0, s: float = 100, a: str = 'round') -> int:
    """
    获取范围内随机数，可指定取整方式：round、floor、ceil
    :param o: 最小值
    :param s: 最大值
    :param a: 取整方式
    :return: 随机整数
    """
    val = random.random() * (s - o) + o
    if a == 'floor':
        return math.floor(val)
    if a == 'ceil':
        return math.ceil(val)
    # 默认 round
    return round(val)


def generate_signature(o: dict, s: str = '') -> str:
    """
    根据请求信息生成签名参数串：nonce、timestamp、sign
    :param o: 请求信息，包含 method、url、params、data
    :param s: 签名中附加的密钥字符串
    :return: 格式化后的签名参数串，如 nonce=...&timestamp=...&sign=...
    """
    try:
        method = o.get('method', '').upper()
        is_get = method == 'GET'
        url = o.get('url', '')
        path, query_params = parse_url(url)

        if is_get:
            combined = {**o.get('params', {}), **query_params}
            rt = stringify_params(combined)
        else:
            rt = json.dumps(o.get('data', {}), separators=(',', ':'), ensure_ascii=False) if o.get(
                'data') is not None else ''

        st = hash_signature_data(rt)
        nonce = str(get_random_number(1e5, 1e6, 'round')).zfill(6)
        timestamp = str(int(time.time() * 1000))
        raw = '_'.join(["NDzZTVxnRKP8Z0jXg1VAMonaG8akvh", path, nonce, timestamp, st, s])
        sign = hashlib.md5(raw.encode('utf-8')).hexdigest()

        return f"nonce={nonce}&timestamp={timestamp}&sign={sign}"
    except Exception as e:
        print(e)
        return ''


def resolve_url_query(url: str) -> Dict[str, List[str]]:
    _url = urlparse(url)
    parad = parse.parse_qs(_url.query)
    return parad


def run_alembic_upgrade():
    """
    执行 Alembic 数据库迁移
    """
    try:
        # 确保 alembic.ini在当前目录
        alembic_ini = os.path.join(os.path.dirname(__file__), 'alembic.ini')

        # 确保数据目录存在
        data_dir = os.path.join(os.path.dirname(__file__), 'data')
        if not os.path.exists(data_dir):
            os.makedirs(data_dir)
            print(f"Created data directory: {data_dir}")

        # 执行迁移
        result = subprocess.run(
            ["alembic", "-c", alembic_ini, "upgrade", "head"],
            check=True,
            capture_output=True,
            text=True
        )
        print(f"Alembic upgrade output: {result.stdout}")

    except subprocess.CalledProcessError as e:
        print(f"Alembic upgrade failed: {e}")
        if e.stderr:
            print(f"Error output: {e.stderr}")
        raise
    except FileNotFoundError:
        print("Alembic command not found")
        raise
    except Exception as e:
        print(f"Unexpected error during alembic upgrade: {e}")
        raise
