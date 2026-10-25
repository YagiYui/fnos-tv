import ast, types, re, hashlib, time
from pathlib import Path
from urllib.parse import urlparse
source=Path(__file__).resolve().parents[1]/'apps/danmu/app.py'
tree=ast.parse(source.read_text(encoding='utf-8'))
names=['manual_provider','manual_search','search_manual_danmu','manual_danmu_episodes','check_manual_login','select_manual_danmu']
functions=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names]
for n in functions:n.decorator_list=[]
class ProviderBase:pass
class SearchBase:pass
class Platform(ProviderBase):
 domain='video.example';name='Fixture platform'
 def get_episode_url(self,url):return [types.SimpleNamespace(episodeTitle='First',episodeNumber='1',url='https://video.example/ep1')]
class SearchBy360(SearchBase):
 def get(self,query):return [types.SimpleNamespace(title='Fixture title',season_number=1,url=['https://video.example/ep1','https://video.example/ep2','https://badvideo.example/ep'])]
request=types.SimpleNamespace(args={},headers={},get_json=lambda **k:{})
ns={'registry':types.SimpleNamespace(provider_id=lambda url:'fixture',allowed=lambda url:True,enabled=lambda kind:['360','douban']),'urlparse':urlparse,'GetDanmuBase':ProviderBase,'VideoSearchBase':SearchBase,'request':request,'re':re,
 'logger':types.SimpleNamespace(warning=lambda *a:None),'hashlib':hashlib,'time':time,'fnos_url':'http://official.fixture',
 'requests':types.SimpleNamespace(get=lambda *a,**k:types.SimpleNamespace(status_code=200,json=lambda:{'code':0}))}
exec(compile(ast.Module(body=functions,type_ignores=[]),str(source),'exec'),ns)
for bad in ['http://127.0.0.1/','http://video.example.evil/','http://video.example@evil/','file:///video.example','http://video.example:8000/']:
 try:ns['manual_provider'](bad);raise AssertionError(bad)
 except ValueError:pass
assert ns['manual_provider']('https://www.video.example/watch') is Platform
rows=ns['manual_search']('Fixture');assert len(rows)==1 and rows[0]['platform']=='Fixture platform'
assert ns['manual_search']('https://video.example/ep1')[0]['url']=='https://video.example/ep1'
request.args={'url':'https://video.example/ep1'}
assert ns['manual_danmu_episodes']()['episodes'][0]['number']=='1'
assert not ns['check_manual_login']('')
assert ns['check_manual_login']('fixture-token')
assert ns['select_manual_danmu']()[1]==401
saved=[];actions=[]
class Model:
 def __init__(self,**kwargs):self.__dict__.update(kwargs)
class Session:
 def query(self,model):return self
 def filter_by(self,**kwargs):actions.append(('filter',kwargs));return self
 def delete(self):actions.append(('delete',None))
 def add(self,item):saved.append(item)
 def commit(self):actions.append(('commit',None))
 def rollback(self):actions.append(('rollback',None))
class DB:
 def __init__(self,model):self.session=Session()
 def close(self):actions.append(('close',None))
ns.update(CRUDBase=DB,videoConfigUrl=Model)
request.headers={'Authorization':'fixture-token'}
request.get_json=lambda **k:{'guid':'episode-1','parent_guid':'season-1','url':'https://video.example/ep1'}
assert ns['select_manual_danmu']()['code']==0
assert saved[0].guid=='episode-1' and saved[0].parent_guid=='season-1'
assert actions==[('filter',{'guid':'episode-1'}),('delete',None),('commit',None),('close',None)]
print('PASS: URL host/scheme/port validation, per-platform search results, manual URLs, episode parsing, login check, scoped transactional selection and DB cleanup')
