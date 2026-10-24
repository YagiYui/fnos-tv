import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from flask import Flask, Blueprint

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

spec = importlib.util.spec_from_file_location('external_test_module', sys.argv[1])
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
prefix = '/v/media/' + 'a' * 32 + '/'
token = 'test-private-cookie'
raw_guid = 'c' * 32
raw_path = '/v/api/v1/media/range/' + raw_guid
class Server(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_HEAD(self):
        if self.path == raw_path:
            self.send_response(501); self.end_headers()
        else: self.do_GET()
    def do_GET(self):
        if self.headers.get('Authorization') != token or self.headers.get('Cookie') != 'authorization=' + token:
            body=b'{"code":-2,"msg":"Auth Failed"}'; kind='application/json'; status=200
        elif self.path == raw_path:
            body=b'fixture-media-bytes';kind='application/octet-stream';status=200
            if self.headers.get('Range'):
                status=206;body=body[:1] if self.headers['Range']=='bytes=0-0' else body[:7]
        elif self.path.startswith(prefix + 'preset.m3u8'):
            assert not self.headers.get('Range'), 'Playlist range must be stripped before rewriting'
            body=b'#EXTM3U\n#EXT-X-MEDIA:TYPE=AUDIO,URI="audio.m3u8?x=1&y=2"\nmain.m3u8\n';kind='application/vnd.apple.mpegurl';status=200
        elif self.path.startswith(prefix + 'main.m3u8'):
            body=b'#EXTM3U\n#EXTINF:5,\n00001.ts\n';kind='application/vnd.apple.mpegurl';status=200
        else:
            body=b'fixture-media-bytes'; kind='video/mp2t'; status=206 if self.headers.get('Range') else 200
            if status==206:body=body[0:7]
        self.send_response(status);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(body)))
        if status==206:self.send_header('Content-Range',f'bytes 0-{len(body)-1}/19')
        self.end_headers()
        if self.command!='HEAD':self.wfile.write(body)
server=ThreadingHTTPServer(('127.0.0.1',0),Server)
threading.Thread(target=server.serve_forever,daemon=True).start()
module.fnos_url=f'http://127.0.0.1:{server.server_port}'
with tempfile.TemporaryDirectory() as directory:
    module.STORE=Path(directory)/'tickets'
    app=Flask(__name__);bp=Blueprint('external_test',__name__,url_prefix='/danmu');module.register_external_routes(bp);app.register_blueprint(bp)
    client=app.test_client();payload={'play_link':prefix+'preset.m3u8'}
    assert client.post('/danmu/external-play',json=payload).status_code==401
    assert client.post('/danmu/external-play',json=payload,headers={'Authorization':'invalid'}).status_code==401
    for path in ['http://attacker.test/a.m3u8',prefix+'../secret.m3u8',prefix+'%2e%2e/secret.m3u8']:
        assert client.post('/danmu/external-play',json={'play_link':path},headers={'Authorization':token}).status_code==422
    response=client.post('/danmu/external-play',json=payload,headers={'Authorization':token})
    assert response.status_code==200,response.data
    url=response.json['data']['url'];assert token not in url
    ticket=url.split('/')[3];state=module.STORE/(ticket+'.json')
    assert os.stat(state).st_mode & 0o777 == 0o600
    body=client.get(url).get_data(as_text=True);assert body.startswith('#EXTM3U');assert token not in body
    assert client.get(url,headers={'Range':'bytes=0-10'}).get_data(as_text=True).startswith('#EXTM3U')
    assert f'/danmu/external-play/{ticket}/audio.m3u8?x=1&y=2' in body
    nested=body.splitlines()[-1];media=client.get(nested).get_data(as_text=True).splitlines()[-1]
    response=client.get(media,headers={'Range':'bytes=0-6'});assert response.status_code==206;assert response.data==b'fixture';assert response.headers['Content-Range']=='bytes 0-6/19'
    assert client.head(media).status_code==200
    assert client.get('/danmu/external-play/'+'b'*43+'/main.m3u8').status_code==403
    assert client.get('/danmu/external-play/'+ticket+'/%2e%2e/secret.m3u8').status_code==403
    for bad in ['../secret','https://other','z'*32]:
        assert client.post('/danmu/external-play',json={'media_guid':bad},headers={'Authorization':token}).status_code==422
    assert client.post('/danmu/external-play',json={'media_guid':raw_guid,'play_link':prefix+'preset.m3u8'},headers={'Authorization':token}).status_code==422
    assert client.post('/danmu/external-play',json={'media_guid':raw_guid},headers={'Authorization':'invalid'}).status_code==401
    raw=client.post('/danmu/external-play',json={'media_guid':raw_guid},headers={'Authorization':token})
    assert raw.status_code==200,raw.data
    raw_url=raw.json['data']['url'];assert raw_url.endswith('/media') and token not in raw_url
    response=client.get(raw_url,headers={'Range':'bytes=0-6'});assert response.status_code==206 and response.data==b'fixture'
    response=client.head(raw_url);assert response.status_code==200 and response.headers['Content-Length']=='19'
    assert client.get(raw_url+'/other').status_code==403
    assert client.get(raw_url+'?path=other').status_code==403
    try:module.rewrite_playlist('#EXTM3U\nhttps://attacker.test/file.ts\n',prefix+'preset.m3u8',prefix,ticket);raise AssertionError('foreign host accepted')
    except ValueError:pass
    value=json.loads(state.read_text());value['expires']=0;state.write_text(json.dumps(value))
    assert client.get(url).status_code==403
server.shutdown()
print('PASS: cookie/auth forwarding, unauthenticated external HLS and segments, URI query preservation, Range and HEAD, no token in URLs, scoped paths, expiry and private tickets')
