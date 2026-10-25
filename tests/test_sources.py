"""Source management uses temporary files and a loopback fixture, never production data."""
import copy
import ast
import importlib.util
import json
from pathlib import Path
import socket
import tempfile
import threading
import types
from urllib.parse import urlparse
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('source_fixture', ROOT / 'apps/danmu/sources.py')
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)


class SourcesTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.registry = module.SourceRegistry(self.temporary.name)
        self.state = module.UserState(self.temporary.name)
        self.patch = patch.object(module, 'registry', self.registry); self.patch.start(); self.addCleanup(self.patch.stop)
        self.state_patch = patch.object(module, 'user_state', self.state); self.state_patch.start(); self.addCleanup(self.state_patch.stop)

    def test_automatic_cache_account_revision_expiry_and_preferences_survive(self):
        try:
            from flask import Flask, g
        except ImportError:
            self.skipTest('Flask unavailable; verify in disposable image')
        calls = []
        def load(): calls.append(1); return {'1':['https://bilibili.com/fixture']}
        self.state.put('alice','danmaku','user',{'source_id':'auto'})
        with Flask(__name__).test_request_context('/'):
            g.source_account={'owner':'alice'}
            g.source_item={'guid':'episode'}
            self.assertEqual(module.automatic_urls(['episode'],load),module.automatic_urls(['episode'],load))
            self.assertEqual(len(calls),1)
            g.source_account={'owner':'bob'}; module.automatic_urls(['episode'],load); self.assertEqual(len(calls),2)
            g.source_account={'owner':'alice'}
            config=self.registry.read();config['providers'].reverse();self.registry.save(config)
            module.automatic_urls(['episode'],load);self.assertEqual(len(calls),3)
            with patch.object(module.time,'time',return_value=module.time.time()+601):
                module.automatic_urls(['episode'],load)
            self.assertEqual(len(calls),4)
            self.assertEqual(self.state.get('alice','danmaku','user'),{'source_id':'auto'})

    def test_persistence_priority_disable_secret_and_conflict(self):
        config = self.registry.read(); config['providers'].reverse(); config['providers'][0]['enabled'] = False
        config['searches'][0]['enabled'] = False
        config['custom'] = [{'id': 'custom-test', 'name': 'Fixture', 'protocol': 'dandanplay-v2', 'url': 'https://example.invalid', 'enabled': True, 'token': 'fixture-secret', 'timeout': 3}]
        saved = self.registry.save(config)
        self.assertEqual(saved['revision'], 1)
        self.assertEqual(module.SourceRegistry(self.temporary.name).read(), saved)
        self.assertFalse(self.registry.allowed('https://tv.sohu.com/example'))
        self.assertFalse(self.registry.allowed('https://bilibili.com.evil.invalid/test'))
        self.assertEqual(self.registry.enabled('searches'), ['douban'])
        self.assertNotIn('fixture-secret', json.dumps(self.registry.public(True)))
        self.assertNotIn('https://example.invalid', json.dumps(self.registry.public(False)))
        with self.assertRaisesRegex(ValueError, '其他页面'):
            self.registry.save(config)
        public = self.registry.public(True); public['custom'][0]['token'] = ''
        self.registry.save(public)
        self.assertEqual(self.registry.read()['custom'][0]['token'], 'fixture-secret')
        public = self.registry.public(True); public['custom'][0]['clear_token'] = True; self.registry.save(public)
        self.assertFalse(self.registry.read()['custom'][0]['token'])
        ordered = self.registry.ordered_urls(['https://bilibili.com/a', 'https://youku.com/a', 'https://tv.sohu.com/a'])
        self.assertEqual(ordered, ['https://youku.com/a', 'https://bilibili.com/a'])

    def test_invalid_configuration_does_not_enable_defaults(self):
        config = self.registry.read(); config['providers'].append(config['providers'][0])
        with self.assertRaises(ValueError): self.registry.save(config)
        for value in ['file:///tmp/a', 'https://user:pass@example.invalid', 'https://example.invalid?a=1', 'http://example.invalid/a/../b']:
            with self.assertRaises(ValueError): module.service_url(value)
        self.registry.path.write_text('{bad', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '恢复备份'): self.registry.enabled('providers')

    def test_account_ownership_precedence_and_delete(self):
        self.state.put('alice', 'danmaku', 'user', {'source_id': 'bilibili'})
        self.state.put('alice', 'danmaku', 'season:season', {'source_id': 'youku'})
        self.state.put('alice', 'danmaku', 'episode:one', {'source_id': 'iqiyi'})
        self.assertEqual(module.selection_for('alice', 'one', 'season')['source_id'], 'iqiyi')
        self.assertEqual(module.selection_for('alice', 'two', 'season')['source_id'], 'youku')
        self.assertEqual(module.selection_for('alice', 'two', 'other')['source_id'], 'bilibili')
        self.assertEqual(module.selection_for('bob', 'one', 'season')['source_id'], 'auto')
        self.state.put('alice', 'danmaku', 'episode:one', None)
        self.assertEqual(module.selection_for('alice', 'one', 'season')['source_id'], 'youku')

    def test_actual_source_handlers_honor_disable_priority_and_fallback(self):
        calls = []
        class Base: pass
        class Row:
            time = 1
            def __dict__(self): return {'text': 'fixture', 'time': 1}
        class Bili(Base):
            domain = 'bilibili.com'; name = 'B站'
            def get(self, url): calls.append('bilibili'); return types.SimpleNamespace(list=[])
            def getImg(self, url): calls.append('emoji'); return []
        class Youku(Base):
            domain = 'youku.com'; name = '优酷'
            def get(self, url): calls.append('youku'); return types.SimpleNamespace(list=[Row()])
        tree = ast.parse((ROOT / 'apps/danmu/app.py').read_text(encoding='utf-8'))
        names = ['download_barrage', 'manual_provider', 'fetch_danmu', 'fetch_emoji', 'get_danmu']
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
        for node in functions: node.decorator_list = []
        request = types.SimpleNamespace(args={})
        namespace = {'registry': self.registry, 'GetDanmuBase': Base, 'urlparse': urlparse, 'request': request,
            'logger': types.SimpleNamespace(info=lambda *a: None, error=lambda *a: None), 'g': types.SimpleNamespace(),
            'record_source_result': lambda *a: None, 'RetDanMuType': object,
            'get_url_dict': lambda *a: {'1': ['https://bilibili.com/one', 'https://youku.com/one']},
            'personalized_urls': lambda loader, *a: (loader(), self.registry.read()['fallback'])}
        exec(compile(ast.Module(body=functions, type_ignores=[]), '<real-source-handlers>', 'exec'), namespace)
        self.assertTrue(namespace['get_danmu']()['1']); self.assertEqual(calls, ['bilibili', 'youku'])
        config = self.registry.read(); config['fallback'] = False; self.registry.save(config); calls.clear()
        self.assertEqual(namespace['get_danmu'](), {}); self.assertEqual(calls, ['bilibili'])
        config = self.registry.read(); config['providers'][0]['enabled'] = False; self.registry.save(config); calls.clear()
        self.assertTrue(namespace['get_danmu']()['1']); self.assertEqual(calls, ['youku'])
        with self.assertRaisesRegex(ValueError, '禁用'): namespace['download_barrage']('https://bilibili.com/one')
        namespace['fetch_emoji']('https://bilibili.com/one'); self.assertEqual(calls, ['youku'])

    def test_address_policy(self):
        for address in ['127.0.0.1', '192.168.1.1', '169.254.169.254', '::1', '::ffff:127.0.0.1']:
            with patch.object(socket, 'getaddrinfo', return_value=[(0, 0, 0, '', (address, 80))]):
                with self.assertRaises(ValueError): module.resolved_addresses('fixture', 80)
        with patch.object(socket, 'getaddrinfo', return_value=[(0, 0, 0, '', ('169.254.169.254', 80))]):
            with self.assertRaises(ValueError): module.resolved_addresses('fixture', 80, True)
        with patch.object(socket, 'getaddrinfo', return_value=[(0, 0, 0, '', ('127.0.0.1', 80))]):
            self.assertEqual(module.resolved_addresses('fixture', 80, True), ['127.0.0.1'])

    def test_platform_requests_recheck_latest_configuration(self):
        module.guard_source_request('https://api.bilibili.com/x/test')
        config = self.registry.read(); config['providers'][0]['enabled'] = False; config['searches'][0]['enabled'] = False
        self.registry.save(config)
        for address in ['https://api.bilibili.com/x/test', 'https://api.so.360kan.com/index']:
            with self.assertRaises(module.SourceDisabled): module.guard_source_request(address)
        module.guard_source_request('http://official.invalid/v/api/v1/user/info')
        module.guard_source_request('https://api.bilibili.com.evil.invalid/test')
        # Exercise the real shared request wrapper, with only its transport replaced.
        tree = ast.parse((ROOT / 'Fuction.py').read_text(encoding='utf-8'))
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'request_data')
        function.decorator_list = []
        calls = []
        transport = types.SimpleNamespace(request=lambda *args, **kwargs: calls.append(args))
        namespace = {'guard_source_request': module.guard_source_request, 'requests': transport, 'req': transport}
        exec(compile(ast.Module(body=[function], type_ignores=[]), '<request-wrapper>', 'exec'), namespace)
        with self.assertRaises(module.SourceDisabled): namespace['request_data']('GET', 'https://api.bilibili.com/test')
        self.assertEqual(calls, [])

    def test_custom_protocol_and_no_redirect(self):
        calls = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                calls.append(self.path)
                if '/redirect/' in self.path:
                    self.send_response(302); self.send_header('Location', 'http://169.254.169.254/'); self.end_headers(); return
                if '/search/anime' in self.path:
                    value = {'success': True, 'animes': [{'animeId': 'item', 'animeTitle': 'Fixture'}]}
                elif '/bangumi/' in self.path:
                    value = {'success': True, 'bangumi': {'episodes': [{'episodeId': 'episode', 'episodeTitle': 'First', 'episodeNumber': '1'}]}}
                else:
                    value = {'comments': [{'p': '1.5,1,16777215,user', 'm': 'Fixture'}, {'p': 'nan,1,1', 'm': 'bad'}, {'p': '0,9,1', 'm': 'bad'}]}
                body = json.dumps(value).encode(); self.send_response(200); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
            def log_message(self, *args): pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            config = self.registry.read(); config['custom'] = [{'id': 'custom-test', 'name': 'Fixture', 'url': 'http://127.0.0.1:' + str(server.server_port), 'protocol': 'dandanplay-v2', 'enabled': True, 'allow_private': True, 'timeout': 3}]
            self.registry.save(config)
            result = module.custom_search('custom-test', 'Fixture')
            self.assertEqual(result[0]['url'], 'danmaku://custom-test/anime/item')
            episodes = module.custom_episodes(result[0]['url'])
            comments = module.custom_comments(episodes[0]['url'])
            self.assertEqual(comments[0]['time'], 1.5); self.assertEqual(comments[0]['mode'], 0)
            self.assertEqual(len(comments), 1)
            path_auth = self.registry.custom('custom-test'); path_auth['auth_mode'] = 'path'; path_auth['token'] = 'fixture-path'
            module.custom_json(path_auth, 'search/anime', {'keyword': 'Fixture'})
            self.assertIn('/fixture-path/api/v2/search/anime', calls[-1])
            remote = self.registry.custom('custom-test'); remote['url'] += '/redirect'
            with self.assertRaisesRegex(ValueError, '重定向'): module.custom_json(remote, 'search/anime')
            self.assertEqual(len(calls), 5)
            config = self.registry.public(True); config['custom'][0]['enabled'] = False; self.registry.save(config)
            with self.assertRaisesRegex(ValueError, '禁用'): module.custom_search('custom-test', 'Fixture')
            self.assertEqual(len(calls), 5, 'Disabled sources never open a new network request')
        finally:
            server.shutdown(); server.server_close(); thread.join()

    def test_route_roles_scope_and_authentication(self):
        try:
            from flask import Flask, Blueprint
        except ImportError:
            self.skipTest('Flask unavailable; run in disposable image for required route checks')
        app = Flask(__name__); blueprint = Blueprint('fixture', __name__, url_prefix='/danmu')
        blueprint.add_url_rule('/search', 'fixture_search', lambda: {'code': 0})
        module.register_source_routes(blueprint); app.register_blueprint(blueprint)
        client = app.test_client()
        def authenticate(token):
            if token not in ('admin', 'alice', 'bob'): raise PermissionError()
            return {'owner': token, 'admin': token == 'admin'}
        with patch.object(module, 'authenticated_user', side_effect=authenticate), patch.object(module, 'media_access', return_value={'guid': 'one'}):
            self.assertEqual(client.get('/danmu/sources').status_code, 403)
            config = client.get('/danmu/sources', headers={'Authorization': 'alice'}).json
            self.assertFalse(config['can_manage'])
            self.assertEqual(client.put('/danmu/sources', headers={'Authorization': 'alice'}, json=config['data']).status_code, 403)
            self.assertEqual(client.put('/danmu/sources', headers={'Authorization': 'admin'}, json=config['data']).status_code, 200)
            data = {'scope': 'episode', 'guid': 'one', 'parent_guid': 'season', 'source_id': 'youku', 'fallback': False}
            self.assertEqual(client.put('/danmu/sources/preferences', headers={'Authorization': 'alice'}, json=data).json['data']['source_id'], 'youku')
            self.assertEqual(client.get('/danmu/sources/preferences?guid=one&parent_guid=season', headers={'Authorization': 'bob'}).json['data']['source_id'], 'auto')
            with patch.object(module, 'media_access', side_effect=PermissionError()):
                self.assertEqual(client.put('/danmu/sources/preferences', headers={'Authorization': 'alice'}, json=data).status_code, 403)
            self.assertEqual(client.get('/danmu/search?source=custom-test').status_code, 401)

    def test_personal_selection_does_not_guess_or_silently_fallback(self):
        try:
            from flask import Flask
        except ImportError:
            self.skipTest('Flask unavailable; verify in disposable image')
        app = Flask(__name__)
        self.state.put('alice', 'danmaku', 'episode:one', {'source_id': 'youku', 'fallback': False, 'url': 'https://youku.com/one'})
        base = lambda: self.fail('An explicit binding must not start automatic search')
        with app.test_request_context('/danmu/get', headers={'Authorization': 'fixture'}), patch.object(module, 'authenticated_user', return_value={'owner': 'alice'}):
            urls, fallback = module.personalized_urls(base, 'one', 'season', 1, 'Fixture')
            self.assertEqual(urls, {'1': ['https://youku.com/one']}); self.assertFalse(fallback)
            self.state.put('alice', 'danmaku', 'episode:one', None)
            self.state.put('alice', 'danmaku', 'season:season', {'source_id':'youku','fallback':False,'episodes':{'2':'https://youku.com/two'}})
            with self.assertRaisesRegex(ValueError, '映射'): module.personalized_urls(base, 'one', 'season', 1, 'Fixture')
            self.state.put('alice', 'danmaku', 'season:season', None)
            config = self.registry.read(); config['custom'] = [{'id':'custom-test','name':'Fixture','url':'https://example.invalid','protocol':'dandanplay-v2','enabled':True}]; self.registry.save(config)
            self.state.put('alice', 'danmaku', 'user', {'source_id':'custom-test','fallback':True})
            with patch.object(module, 'custom_search', side_effect=ValueError('incompatible fixture')):
                self.assertEqual(module.personalized_urls({'1':['https://youku.com/one']}, 'one', 'season', 1, 'Fixture')[0], {'1':['https://youku.com/one']})
            self.state.put('alice', 'danmaku', 'user', {'source_id':'custom-test','fallback':False})
            with patch.object(module, 'custom_search', side_effect=ValueError('incompatible fixture')):
                with self.assertRaises(ValueError): module.personalized_urls({}, 'one', 'season', 1, 'Fixture')


if __name__ == '__main__':
    unittest.main(verbosity=2)
