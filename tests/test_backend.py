import ast
from pathlib import Path
import re
import types
import unicodedata
import urllib.parse

source = Path(__file__).resolve().parents[1] / 'apps/danmu/app.py'
tree = ast.parse(source.read_text(encoding='utf-8'))
functions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in
             ['normalized_title', 'searchVideoData', 'get_episode_url', 'get_url_dict']]
code = compile(ast.Module(body=functions, type_ignores=[]), str(source), 'exec')
closed = []
class DB:
    saved = []
    def __init__(self, model): pass
    def filter(self, **kwargs): return self.saved if 'guid' in kwargs else []
    def close(self): closed.append(True)

class SearchBase: pass
class DanmuBase: pass
ns = {'g':types.SimpleNamespace(),'automatic_urls':lambda key,loader:loader(),'registry': types.SimpleNamespace(allowed=lambda url:True,ordered_urls=lambda urls:list(dict.fromkeys(urls)),enabled=lambda kind:['360']), 'SEARCHES':[('360','360','Search')], 're': re, 'unicodedata': unicodedata, 'VideoSearchBase': SearchBase,
      'GetDanmuBase': DanmuBase, 'CRUDBase': DB, 'videoConfigUrl': object,
      'logger': types.SimpleNamespace(warning=lambda *args: None),
      'tldextract': types.SimpleNamespace(extract=lambda u: types.SimpleNamespace(domain=urllib.parse.urlparse(u).netloc))}
exec(code, ns)
DB.saved = [types.SimpleNamespace(url='https://video.example/episode1')]
assert ns['get_url_dict'](None, 'unknown', '1', '1', True, 'existing-guid') == {'1': ['https://video.example/episode1']}
assert closed
DB.saved = []
queries = []
class Search(SearchBase):
    def get(self, title):
        queries.append(title)
        return [types.SimpleNamespace(title='我推的孩子', season_number=1, url=['https://video.example/season'])]
assert ns['searchVideoData']('【我推的孩子】', '1', True) == ['https://video.example/season']
assert ns['searchVideoData']('【我推的孩子】', '2', True) == [], 'Do not match another season'
class Provider(DanmuBase):
    domain = 'video.example'
    def get_episode_url(self, url):
        return [types.SimpleNamespace(episodeNumber=n, url='https://video.example/episode1') for n in [1, '1']]
assert ns['get_episode_url'](['https://video.example/season']) == {'1': ['https://video.example/episode1']}
print('PASS: saved episode URLs survive automatic search, DB sessions close, title normalization, season isolation, string episode keys')

assert ns['get_url_dict'](None, '【我推的孩子】', '1', '1', True, 'new-guid') == {'1':['https://video.example/episode1']}
assert not hasattr(DB,'add'), 'Automatic matching must not require a shared database write'
