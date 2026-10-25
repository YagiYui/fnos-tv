"""Real Flask/SQLite regression tests; no NAS or official service is contacted."""
import importlib.util
from pathlib import Path
import secrets
import sys
import types
import unittest
from unittest.mock import Mock, patch

from flask import Flask
import requests
from sqlalchemy import create_engine
from sqlalchemy.orm import scoped_session, sessionmaker
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
engine = create_engine('sqlite://', poolclass=StaticPool)
sessions = scoped_session(sessionmaker(bind=engine))


def load_routes(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


config = types.ModuleType('Config')
config.fnos_url = 'http://official.invalid'
config.fnos_username = config.fnos_password = None
functions = types.ModuleType('Fuction')
for name in ('merge_skipped_segments', 'calculate_repeat_rate', 'find_skipped_segments', 'generate_signature'):
    setattr(functions, name, lambda *args: '')
database = types.ModuleType('core.db.db')
database.SessionLocal = sessions
fnos = types.ModuleType('core.Fnos')
fnos.FnOs = types.SimpleNamespace()
with patch.dict(sys.modules, {'Config': config, 'Fuction': functions, 'core.db.db': database,
                              'core.Fnos': fnos, 'core.alist_api': types.ModuleType('core.alist_api')}):
    record_routes = load_routes('test_record_routes', 'apps/fnos/app.py')
    api_routes = load_routes('test_api_routes', 'apps/api.py')

from core.db.model import Base
from core.db.model.recordDb import RecordDb
from core.db.model.videoConfigDb import videoConfigList, videoConfigUrl


def response(body=None, status=200):
    value = Mock(status_code=status)
    value.json.return_value = {'code': 0} if body is None else body
    value.__enter__ = Mock(return_value=value)
    value.__exit__ = Mock(return_value=False)
    return value


class RoutesTest(unittest.TestCase):
    def setUp(self):
        sessions.remove()
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        app = Flask(__name__)
        app.config['TESTING'] = True
        app.register_blueprint(record_routes.fnos_app)
        app.register_blueprint(api_routes.api_app)
        app.teardown_appcontext(lambda error: sessions.remove())
        self.client = app.test_client()
        self.headers = {'Authorization': secrets.token_hex(16)}
        self.record = {'guid': 'season-a', 'item_guid': 'episode-a', 'ts': 20,
                       'create_time': 100, 'playback_speed': 1.25}
        self.url = '/api/videoConfig?guid=season-a&episode_guid=episode-a'
        self.auth = patch.object(requests, 'get', return_value=response())
        self.auth.start()
        self.addCleanup(self.auth.stop)

    def tearDown(self):
        sessions.remove()

    def seed(self):
        db = sessions()
        db.add_all([videoConfigList(guid='season-a', startTime=10, endTime=20),
                    videoConfigList(guid='season-b', startTime=30, endTime=40),
                    videoConfigUrl(guid='episode-a', parent_guid='season-a', url='https://v.qq.com/old'),
                    videoConfigUrl(guid='episode-b', parent_guid='season-b', url='https://v.qq.com/keep')])
        db.commit()

    def test_success_and_rejected_record(self):
        with patch.object(requests, 'post', return_value=response()):
            result = self.client.post('/fnos/v/api/v1/play/record', json=self.record)
        self.assertEqual(result.json['code'], 0)
        self.assertEqual(sessions().query(RecordDb).count(), 1)
        for status in (200, 403):
            with patch.object(requests, 'post', return_value=response({'code': -2}, status)):
                result = self.client.post('/fnos/v/api/v1/play/record', json=self.record)
                self.assertEqual(result.status_code, status)
                self.assertEqual(result.json['code'], -2)
        self.assertEqual(sessions().query(RecordDb).count(), 1)

    def test_invalid_and_unavailable_record(self):
        with patch.object(requests, 'post') as upstream:
            for data in ({}, [], {**self.record, 'ts': -1}, {**self.record, 'ts': True}):
                self.assertEqual(self.client.post('/fnos/v/api/v1/play/record', json=data).status_code, 400)
            upstream.assert_not_called()
        for failure, status in ((requests.Timeout(), 504), (ValueError(), 502), (requests.ConnectionError(), 502)):
            with patch.object(requests, 'post', side_effect=failure):
                self.assertEqual(self.client.post('/fnos/v/api/v1/play/record', json=self.record).status_code, status)
        self.assertEqual(sessions().query(RecordDb).count(), 0)

    def test_auxiliary_failure_preserves_official_success(self):
        with patch.object(requests, 'post', return_value=response()), \
                patch.object(record_routes.CRUDBase, 'add', side_effect=RuntimeError('fixture failure')):
            result = self.client.post('/fnos/v/api/v1/play/record', json=self.record)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json['code'], 0)
        self.assertEqual(sessions().query(RecordDb).count(), 0)

    def test_clear_is_persistent_and_scoped(self):
        self.seed()
        result = self.client.post(self.url, json={'list': [], 'url': []}, headers=self.headers)
        self.assertEqual(result.json['code'], 0)
        self.assertEqual(self.client.get(self.url, headers=self.headers).json, {'list': [], 'url': []})
        self.assertEqual(sessions().query(videoConfigList).filter_by(guid='season-b').count(), 1)
        self.assertEqual(sessions().query(videoConfigUrl).filter_by(guid='episode-b').count(), 1)

    def test_replace_and_partial_update(self):
        self.seed()
        data = {'list': [{'startTime': 5, 'endTime': 8}]}
        self.assertEqual(self.client.post(self.url, json=data, headers=self.headers).json['code'], 0)
        result = self.client.get(self.url, headers=self.headers).json
        self.assertEqual(result['list'], data['list'])
        self.assertEqual(result['url'], [{'url': 'https://v.qq.com/old'}])

    def test_invalid_update_leaves_data_intact(self):
        self.seed()
        for data in ({'list': [{'startTime': 20, 'endTime': 10}]}, {'invalid': []},
                     {'url': [{'url': 'http://127.0.0.1/private'}]}, {'url': [{'url': 'https://v.qq.com.evil.invalid/'}]}):
            self.assertEqual(self.client.post(self.url, json=data, headers=self.headers).status_code, 400)
        self.assertEqual(sessions().query(videoConfigList).count(), 2)
        self.assertEqual(sessions().query(videoConfigUrl).count(), 2)

    def test_transaction_failure_rolls_back_both_models(self):
        self.seed()
        with patch.object(sessions(), 'commit', side_effect=RuntimeError('fixture failure')):
            result = self.client.post(self.url, json={'list': [], 'url': []}, headers=self.headers)
        self.assertEqual(result.status_code, 500)
        self.assertEqual(sessions().query(videoConfigList).count(), 2)
        self.assertEqual(sessions().query(videoConfigUrl).count(), 2)
        self.assertEqual(self.client.post(self.url, json={'list': []}, headers=self.headers).json['code'], 0)

    def test_login_failure_and_timeout(self):
        self.assertEqual(self.client.post(self.url, json={'list': []}).status_code, 401)
        with patch.object(requests, 'get', side_effect=requests.Timeout()):
            self.assertEqual(self.client.post(self.url, json={'list': []}, headers=self.headers).status_code, 504)
        self.assertEqual(sessions().query(videoConfigList).count(), 0)


if __name__ == '__main__':
    unittest.main()
