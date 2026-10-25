"""Temporary account-owned state; no real identity or media service is contacted."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module
sources = load('personal_source_fixture', 'apps/danmu/sources.py')
with patch.dict(sys.modules, {'apps.danmu.sources': sources}):
    personal = load('personal_fixture', 'apps/danmu/personal.py')


class PersonalTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.state = sources.UserState(self.temporary.name)
        target = patch.object(personal, 'user_state', self.state); target.start(); self.addCleanup(target.stop)

    def test_watchlist_is_independent_finite_and_owned(self):
        for number in range(24): self.state.put('alice', 'watchlist', 'film-' + str(number), {'type': 'Movie'})
        self.state.put('bob', 'watchlist', 'other', {'type': 'TV'})
        first = personal.watchlist_page('alice'); second = personal.watchlist_page('alice', 2)
        self.assertEqual(first['total'], 24); self.assertEqual(len(first['items']), 21); self.assertEqual(len(second['items']), 3)
        self.assertFalse(set(row['guid'] for row in first['items']) & set(row['guid'] for row in second['items']))
        self.assertEqual(personal.watchlist_page('bob')['total'], 1)
        self.assertEqual(personal.watchlist_page('missing')['items'], [])
        with self.assertRaises(ValueError): personal.watchlist_page('alice', 1, 10000)

    def test_preferences_conflicts_reset_and_allowlist(self):
        self.assertEqual(personal.settings('alice', 'player')['values'], {})
        first = personal.settings('alice', 'player', {'revision': 0, 'values': {'playbackRate': 1.5}})
        with self.assertRaises(personal.Conflict): personal.settings('alice', 'player', {'revision': 0, 'values': {'use302': True}})
        second = personal.settings('alice', 'player', {'revision': first['revision'], 'values': {'use302': True}})
        self.assertEqual(second['values'], {'playbackRate':1.5,'use302':True})
        reset = personal.settings('alice', 'player', {'revision': second['revision'], 'reset': True})
        self.assertEqual(reset['values'], personal.PLAYER_DEFAULTS)
        self.assertEqual(personal.settings('bob', 'player')['values'], {})
        self.assertEqual(personal.settings('alice', 'home')['values'], {})
        for values in [{'playbackRate': True}, {'playbackRate': float('nan')}, {'subtitleFontSize': 100}, {'audioLanguage': '\n'}, {'script':'bad'}]:
            with self.assertRaises(ValueError): personal.preference_values('player', values)
        for values in [{'sections':['continue','continue','libraries']},{'libraryOrder':['../path']},{'density':'huge'}]:
            with self.assertRaises(ValueError): personal.preference_values('home', values)
        valid = {'sections':['libraries','continue','watchlist'],'density':'compact','hiddenLibraries':['library-a']}
        self.assertEqual(personal.preference_values('home', valid), valid)

    def test_routes_permissions_idempotence_and_lost_access_cleanup(self):
        try:
            from flask import Flask, Blueprint
        except ImportError:
            self.skipTest('Flask unavailable; complete in disposable image')
        app = Flask(__name__); blueprint = Blueprint('personal', __name__, url_prefix='/danmu')
        personal.register_personal_routes(blueprint); app.register_blueprint(blueprint); client = app.test_client()
        def auth(token):
            if token not in ('alice','bob'): raise PermissionError()
            return {'owner': token}
        with patch.object(personal, 'authenticated_user', side_effect=auth), patch.object(personal, 'media_access', return_value={'guid':'film','type':'Movie'}) as access:
            self.assertEqual(client.get('/danmu/personal/watchlist').status_code,403)
            self.assertEqual(client.get('/danmu/personal/watchlist').headers['Cache-Control'],'no-store')
            headers={'Authorization':'alice'}
            self.assertTrue(client.put('/danmu/personal/watchlist/film',headers=headers).json['data']['contains'])
            client.put('/danmu/personal/watchlist/film',headers=headers)
            self.assertEqual(client.get('/danmu/personal/watchlist',headers=headers).json['data']['total'],1)
            self.assertFalse(client.get('/danmu/personal/watchlist/film',headers={'Authorization':'bob'}).json['data']['contains'])
            access.side_effect=PermissionError()
            self.assertEqual(client.put('/danmu/personal/watchlist/forbidden',headers=headers).status_code,403)
            self.assertFalse(client.delete('/danmu/personal/watchlist/film',headers=headers).json['data']['contains'])
            result=client.put('/danmu/personal/preferences/player',headers=headers,json={'revision':0,'values':{'playbackRate':1.5}})
            self.assertEqual(result.status_code,200)
            self.assertEqual(client.put('/danmu/personal/preferences/player',headers=headers,json={'revision':0,'reset':True}).status_code,409)
            self.assertEqual(client.put('/danmu/personal/preferences/player',headers=headers,json=[]).status_code,400)

    def test_series_observations_use_authorized_metadata_and_first_baseline(self):
        try:
            from flask import Flask, Blueprint
        except ImportError:
            self.skipTest('Flask unavailable; complete in disposable image')
        app=Flask(__name__);blueprint=Blueprint('updates',__name__,url_prefix='/danmu')
        personal.register_personal_routes(blueprint);app.register_blueprint(blueprint);client=app.test_client()
        media={'guid':'series','type':'TV','local_number_of_episodes':4}
        with patch.object(personal,'authenticated_user',side_effect=lambda token:{'owner':token}),patch.object(personal,'media_access',side_effect=lambda *args:dict(media)):
            headers={'Authorization':'alice'};client.put('/danmu/personal/watchlist/series',headers=headers)
            first=client.put('/danmu/personal/updates/series',headers=headers).json['data']
            self.assertEqual(first['delta'],0,'The first observation establishes a baseline, never a fabricated update')
            media['local_number_of_episodes']=5
            second=client.put('/danmu/personal/updates/series',headers=headers).json['data']
            self.assertEqual((second['previous_count'],second['count'],second['delta']),(4,5,1))
            self.assertEqual(client.put('/danmu/personal/updates/series',headers={'Authorization':'bob'}).status_code,400)
            again=client.put('/danmu/personal/updates/series',headers=headers,json={'count':99999}).json['data']
            self.assertEqual(again['count'],5,'Client-provided counts never replace authorized official metadata')
            client.delete('/danmu/personal/watchlist/series',headers=headers)
            self.assertIsNone(self.state.get('alice','updates','series'))


if __name__ == '__main__': unittest.main(verbosity=2)
