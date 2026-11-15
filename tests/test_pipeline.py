"""Offline tests: no network, no API key, temporary data folder.

Run: python -m unittest discover -s tests -v
"""
import importlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
TMP = Path(tempfile.mkdtemp(prefix='superlig-test-'))
shutil.copytree(REPO / 'site', TMP / 'site')
os.environ.update({'SUPERLIG_ROOT': str(TMP), 'APIFOOTBALL_KEY': 'test-key-not-real', 'APIFOOTBALL_LEAGUE_ID': '203',
                   'API_MIN_INTERVAL': '0', 'API_DAILY_LIMIT': '100'})
sys.path.insert(0, str(REPO / 'scripts'))
sys.path.insert(0, str(HERE))

import apifootball  # noqa: E402
import backfill  # noqa: E402
import build_data  # noqa: E402
import build_model  # noqa: E402
import rebuild_data  # noqa: E402
import update  # noqa: E402
from fakeapi import FakeSession  # noqa: E402

DAY = {'today': '2026-11-16'}
apifootball.utc_today = lambda: DAY['today']


def api(session, limit=100):
    os.environ['API_DAILY_LIMIT'] = str(limit)
    return apifootball.Api(session=session, sleep=lambda s: None)


class T1ApiClient(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(TMP / 'data', ignore_errors=True)

    def test_hard_cap_and_ledger(self):
        os.environ['API_DAILY_LIMIT'] = '500'
        a = apifootball.Api(session=FakeSession({2024: None}), sleep=lambda s: None)
        self.assertEqual(a.limit, 100, 'limit above 100 must be clamped')
        a = api(FakeSession({2024: None}), limit=2)
        a.get('standings', {'league': 203, 'season': 2024})
        a.get('standings', {'league': 203, 'season': 2024})
        with self.assertRaises(apifootball.QuotaReached):
            a.get('standings', {'league': 203, 'season': 2024})
        self.assertEqual(json.loads((TMP / 'data/request_ledger.json').read_text())[DAY['today']], 2)

    def test_error_classes(self):
        s = FakeSession({2024: None})
        a = api(s)
        s.queue.append(({'errors': {'plan': 'Free plans do not have access to this season'}, 'response': []},))
        with self.assertRaises(apifootball.PlanRestricted):
            a.get('fixtures', {'league': 203, 'season': 2019})
        s.queue.append(({'errors': {'requests': 'You have reached the request limit for the day'}, 'response': []},))
        with self.assertRaises(apifootball.QuotaReached):
            a.get('fixtures', {'league': 203, 'season': 2024})
        self.assertEqual(a.remaining_today(), 0)

    def test_rate_limit_retry_and_header_sync(self):
        s = FakeSession({2024: None})
        slept = []
        os.environ['API_DAILY_LIMIT'] = '100'
        a = apifootball.Api(session=s, sleep=slept.append)
        s.queue.append(({'errors': {'rateLimit': 'Too many requests'}, 'response': []},))
        a.get('standings', {'league': 203, 'season': 2024})
        self.assertIn(61, slept)
        s.queue.append(({'errors': [], 'response': []}, 200, {'x-ratelimit-requests-remaining': '0'}))
        a.get('standings', {'league': 203, 'season': 2024})
        self.assertEqual(a.remaining_today(), 0, 'API header says 0 remaining: local ledger must stop')

    def test_key_never_written(self):
        a = api(FakeSession({2024: None}))
        a.get('standings', {'league': 203, 'season': 2024})
        for p in (TMP / 'data').rglob('*'):
            if p.is_file() and p.suffix != '.gz':
                self.assertNotIn('test-key-not-real', p.read_text(encoding='utf-8'))


class T2BackfillAndUpdate(unittest.TestCase):
    def test_resume_across_days_without_repeats(self):
        shutil.rmtree(TMP / 'data', ignore_errors=True)
        s = FakeSession({2024: None, 2025: None, 2026: 6})
        DAY['today'] = '2026-11-16'
        backfill.main(['--seasons', '2024,2025,2026'], api=api(s, limit=7))
        state = json.loads((TMP / 'data/backfill_state.json').read_text())
        self.assertFalse(state['seasons']['2024'].get('complete', False))
        self.assertEqual(len(s.calls), 7)
        for day in ('2026-11-17', '2026-11-18'):
            DAY['today'] = day
            backfill.main(['--seasons', '2024,2025,2026'], api=api(s, limit=7))
        state = json.loads((TMP / 'data/backfill_state.json').read_text())
        for y in ('2024', '2025', '2026'):
            self.assertTrue(state['seasons'][y]['complete'], y)
        ids = [c[1]['ids'] for c in s.calls if 'ids' in c[1]]
        flat = [i for chunk in ids for i in chunk.split('-')]
        self.assertEqual(len(flat), len(set(flat)), 'no fixture fetched twice')
        # 90 + 90 + 30 finished fixtures -> 5 + 5 + 2 batches; plus 3 x (coverage, index, standings)
        self.assertEqual(len(s.calls), 12 + 9)
        n_before = len(s.calls)
        backfill.main(['--seasons', '2024,2025,2026'], api=api(s))
        self.assertEqual(len(s.calls), n_before, 'completed seasons cost no requests')

        # update: round 7 finishes -> index + one batch + standings
        s.finish_round(2026, 7)
        DAY['today'] = '2026-11-19'
        before = len(s.calls)
        update.main(['--season', '2026'], api=api(s))
        new = s.calls[before:]
        self.assertEqual([c[0] for c in new], ['fixtures', 'fixtures', 'standings'])
        self.assertEqual(len(new[1][1]['ids'].split('-')), 5)
        before = len(s.calls)
        update.main(['--season', '2026'], api=api(s))
        self.assertEqual(len(s.calls) - before, 1, 'nothing changed: only the index request')

        # Turkish players abroad: run twice a week apart
        import legionnaires
        DAY['today'] = '2026-11-10'
        legionnaires.main(['--season', '2026'], api=api(s))
        snap = TMP / 'data/raw/legionnaires/2026/snapshots'
        (snap / '2026-11-10.json').rename(snap / '2026-11-09.json') if (snap / '2026-11-10.json').exists() else None
        for f in (TMP / 'data/raw/legionnaires/2026').glob('player_*.json'):
            j = json.loads(f.read_text()); j['fetched_at'] = '2026-11-01T00:00:00+00:00'; f.write_text(json.dumps(j))
        s.player_goals = 2
        DAY['today'] = '2026-11-19'
        before = len(s.calls)
        legionnaires.main(['--season', '2026'], api=api(s))
        self.assertEqual([c[0] for c in s.calls[before:]].count('players'), 3, 'stale players refetched; national id and squad cached')

        # data -> season JSON -> model -> site
        rebuild_data.main([])
        leg = json.loads((TMP / 'data/public/legionnaires.json').read_text())
        self.assertEqual(sorted(leg['players']), ['1'], 'Süper Lig player and non-Turkish player excluded')
        p1 = leg['players']['1']
        self.assertEqual(p1['totals']['apps'], 10, 'national-team matches excluded')
        self.assertEqual(p1['week']['goals'], 2)
        pl = json.loads((TMP / 'data/public/players_2026.json').read_text())
        striker = pl['players'][str(645 * 100)]
        gs_goals = sum(m['home_goals'] if m['home_id'] == 645 else m['away_goals'] for m in json.loads((TMP / 'data/public/season_2026.json').read_text())['matches'] if m['finished'] and 645 in (m['home_id'], m['away_id']))
        self.assertEqual(striker['goals'], gs_goals, 'player goals add up to team goals in the fake data')
        self.assertNotIn(str(645 * 100 + 11), pl['players'], 'players with no minutes are not counted')
        season = json.loads((TMP / 'data/public/season_2026.json').read_text())
        self.assertEqual(season['schema_version'], 2)
        self.assertEqual(season['completed_match_count'], 35)
        self.assertEqual(season['data_quality']['with_events'], 35)
        lg = season['league']
        self.assertEqual(lg['matches'], 35)
        self.assertAlmostEqual(lg['home_win'] + lg['draw'] + lg['away_win'], 1, delta=0.001)
        self.assertAlmostEqual(sum(lg['htft_match'].values()), 1, delta=0.01)
        b = season['teams']['645']['markets']['all']
        self.assertAlmostEqual(b['btts_yes'] + b['btts_no'], 1, delta=0.001)
        self.assertIn('team_corners_over_4.5', b)
        self.assertIn('team_cards_over_1.5', b)
        refs = json.loads((TMP / 'data/public/referees.json').read_text())['referees']
        meler = refs['halil umut meler']
        self.assertEqual(sorted(meler['seasons']), ['2024', '2025', '2026'])
        self.assertEqual(meler['totals']['matches'], sum(v['matches'] for v in meler['seasons'].values()))
        self.assertEqual(len(meler['matches']), meler['totals']['matches'])
        self.assertIn('Galatasaray', meler['big_four'])
        self.assertTrue(any(d['key'] == 'a kardesler' for d in refs['arda kardesler'].get('possible_duplicates', [])))
        build_model.main([])
        model = json.loads((TMP / 'data/public/dixon_coles.json').read_text())
        self.assertGreater(model['backtest']['matches'], 30)
        self.assertTrue(model['backtest']['no_leakage_check'])
        self.assertLessEqual(len(model['training_seasons']), 5)
        for k in ('over_2_5', 'btts', 'calibration', 'beats_baseline', 'small_sample'):
            self.assertIn(k, model['backtest'])
        self.assertEqual(sum(c['n'] for c in model['backtest']['calibration']), 2 * model['backtest']['matches'])
        self.assertTrue(model['converged'])
        for p in model['backtest_predictions']:
            self.assertAlmostEqual(sum(p['probabilities']), 1, delta=0.01)
        self.assertGreater(len(model['fixtures']), 0)
        top = [r['team'] for r in model['ratings'][:3]]
        self.assertTrue({'Galatasaray', 'Fenerbahçe'} & set(top), top)

        os.environ['SITE_URL'] = 'https://example.github.io/super-lig'
        os.environ['SITE_OUT'] = str(TMP / 'public')
        import build_site
        importlib.reload(build_site)
        build_site.main()
        pub = TMP / 'public'
        self.assertTrue((pub / 'takim/kasimpasa/index.html').exists())
        self.assertTrue((pub / 'takim/caykur-rizespor/index.html').exists())
        self.assertTrue((pub / 'hakem/halil-umut-meler/index.html').exists(), 'country suffix stripped')
        sitemap = (pub / 'sitemap.xml').read_text()
        self.assertIn('https://example.github.io/super-lig/takim/galatasaray/', sitemap)
        self.assertIn('Sitemap: https://example.github.io/super-lig/sitemap.xml', (pub / 'robots.txt').read_text())
        for page in pub.rglob('index.html'):
            text = page.read_text(encoding='utf-8')
            self.assertNotIn('None', text, page)
            self.assertIsNone(re.search(r'\b(nan|NaN|undefined)\b', text), page)
        ref = (pub / 'hakem/arda-kardesler/index.html').read_text(encoding='utf-8')
        self.assertIn('A. Kardeşler', ref, 'abbreviated spelling flagged for review, not merged')
        self.assertIn('2024–25', ref, 'referee page covers all seasons')
        self.assertTrue((pub / 'data/public/referees.json').exists())
        # Phase C: H2H, match timeline, structured data on every page
        h2h = pub / 'h2h/fenerbahce-vs-galatasaray/index.html'
        self.assertTrue(h2h.exists())
        self.assertIn('h2h/fenerbahce-vs-galatasaray/', sitemap)
        meets = [r for r in json.loads((TMP / 'data/public/results.json').read_text())['rows'] if {r[4], r[5]} == {611, 645}]
        expected = 0
        for y in (2024, 2025, 2026):
            sj = json.loads((TMP / f'data/public/season_{y}.json').read_text())
            expected += sum(1 for m in sj['matches'] if m['finished'] and {m['home_id'], m['away_id']} == {611, 645})
        self.assertEqual(len(meets), expected)
        self.assertGreaterEqual(expected, 4)
        self.assertIn(f'>{expected}<', h2h.read_text(encoding='utf-8'))
        season = json.loads((TMP / 'data/public/season_2026.json').read_text())
        played = next(m for m in season['matches'] if m['finished'] and m['home_goals'] > 0)
        mp = (pub / f"mac/{played['slug']}/index.html").read_text(encoding='utf-8')
        self.assertIn('Maç olayları', mp)
        later = [m for m in season['matches'] if m['finished'] and {m['home_id'], m['away_id']} == {played['home_id'], played['away_id']} and m['timestamp'] > played['timestamp']]
        for m in later:
            self.assertNotIn(m['slug'], mp, 'previous meetings must not include later matches')
        upcoming = next(m for m in season['matches'] if not m['finished'])
        up = (pub / f"mac/{upcoming['slug']}/index.html").read_text(encoding='utf-8')
        self.assertIn('Model olasılıkları', up)
        gs = (pub / 'takim/galatasaray/index.html').read_text(encoding='utf-8')
        self.assertIn('Rakiplere karşı', gs)
        self.assertIn('Kadro: forma giyen oyuncular', gs)
        self.assertTrue((pub / f"oyuncu/{striker['slug']}/index.html").exists())
        lp = (pub / 'lejyonerler/index.html').read_text(encoding='utf-8')
        self.assertIn('Player 1', lp)
        self.assertNotIn('Player 3', lp)
        self.assertIn('Bu hafta', lp)
        self.assertTrue((pub / '404.html').exists())
        self.assertIn('Geriye dönük test', (pub / 'model/index.html').read_text(encoding='utf-8'))
        for page in pub.rglob('index.html'):
            text = page.read_text(encoding='utf-8')
            for block in re.findall(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', text, re.S):
                json.loads(block.replace('<\\/', '</'))
            if page.parent != pub:
                self.assertIn('<link rel="canonical" href="https://example.github.io/super-lig/', text)
        os.environ.pop('SITE_URL')
        importlib.reload(build_site)
        build_site.main()
        self.assertIn('Disallow: /', (pub / 'robots.txt').read_text())
        self.assertIn('noindex', (pub / 'index.html').read_text())


class T2bStorage(unittest.TestCase):
    def test_legacy_details_migrate_to_one_file_per_fixture(self):
        shutil.rmtree(TMP / 'data', ignore_errors=True)
        apifootball.write_json(apifootball.season_dir(2020) / 'details.json.gz', {'1': {'fixture': {'a': 1}}, '2': {'fixture': {'a': 2}}})
        d = apifootball.load_details(2020)
        self.assertEqual(sorted(d), ['1', '2'])
        d['3'] = {'fixture': {'a': 3}}
        apifootball.save_details(2020, d, ['3'])
        self.assertFalse((apifootball.season_dir(2020) / 'details.json.gz').exists())
        self.assertEqual(sorted(p.name for p in (apifootball.season_dir(2020) / 'details').iterdir()), ['1.json.gz', '2.json.gz', '3.json.gz'])
        b1 = (apifootball.season_dir(2020) / 'details' / '1.json.gz').read_bytes()
        apifootball.save_details(2020, apifootball.load_details(2020), ['1'])
        self.assertEqual(b1, (apifootball.season_dir(2020) / 'details' / '1.json.gz').read_bytes(), 'unchanged data gives identical bytes')

    def test_derived_data_not_committed(self):
        ignore = (REPO / '.gitignore').read_text()
        self.assertIn('data/public/', ignore)
        self.assertIn('data/build/', ignore)


class T3Calculations(unittest.TestCase):
    def match(self, events=None, statistics=None, hg=2, ag=1, referee='Ali Şansalan, Turkey'):
        item = {'fixture': {'id': 1, 'referee': referee, 'timestamp': 1, 'date': '2026-08-01T17:00:00+00:00', 'status': {'short': 'FT'}},
                'league': {'round': 'Regular Season - 1'}, 'teams': {'home': {'id': 10, 'name': 'Göztepe'}, 'away': {'id': 20, 'name': 'Kasımpaşa'}},
                'goals': {'home': hg, 'away': ag}, 'score': {'halftime': {'home': 1, 'away': 0}}}
        full = {**item, 'events': events, 'statistics': statistics}
        return build_data.normalise_match(item, {'fixture': full} if events is not None or statistics is not None else None)

    def test_zero_cards_are_zero_and_missing_events_are_null(self):
        ev = [{'type': 'Goal', 'detail': 'Normal Goal', 'team': {'id': 10}, 'time': {'elapsed': 10}},
              {'type': 'Goal', 'detail': 'Normal Goal', 'team': {'id': 10}, 'time': {'elapsed': 50}},
              {'type': 'Goal', 'detail': 'Normal Goal', 'team': {'id': 20}, 'time': {'elapsed': 80}}]
        m = self.match(ev)
        self.assertEqual((m['home_yellow'], m['away_red']), (0, 0))
        m = self.match(None)
        self.assertIsNone(m['home_yellow'])
        self.assertFalse(m['events_available'])

    def test_missed_penalty_own_goal_and_shootout(self):
        ev = [{'type': 'Goal', 'detail': 'Missed Penalty', 'team': {'id': 20}, 'time': {'elapsed': 3}},
              {'type': 'Goal', 'detail': 'Own Goal', 'team': {'id': 20}, 'time': {'elapsed': 20}},   # player of team 20, credited to 10
              {'type': 'Goal', 'detail': 'Penalty', 'team': {'id': 10}, 'time': {'elapsed': 45, 'extra': 2}},
              {'type': 'Goal', 'detail': 'Normal Goal', 'team': {'id': 20}, 'time': {'elapsed': 90, 'extra': 4}},
              {'type': 'Card', 'detail': 'Second Yellow card', 'team': {'id': 20}, 'time': {'elapsed': 70}},
              {'type': 'Goal', 'detail': 'Penalty', 'team': {'id': 10}, 'time': {'elapsed': 120}, 'comments': 'Penalty Shootout'}]
        m = self.match(ev)
        self.assertTrue(m['events_consistent'])
        self.assertEqual(m['first_goal'], 'home')
        self.assertEqual(m['penalties'], 2, 'awarded = scored + missed, shootout excluded')
        self.assertEqual(m['away_red'], 1)
        self.assertEqual(m['goal_minutes']['home'], [0, 1, 1, 0, 0, 0])
        self.assertEqual(m['goal_minutes']['away'], [0, 0, 0, 0, 0, 1])

    def test_inconsistent_events_withhold_timing(self):
        ev = [{'type': 'Goal', 'detail': 'Normal Goal', 'team': {'id': 10}, 'time': {'elapsed': 10}}]
        m = self.match(ev)  # score 2-1 but one goal event
        self.assertFalse(m['events_consistent'])
        self.assertIsNone(m['first_goal'])
        self.assertIsNone(m['goal_minutes'])

    def test_statistics_null_means_zero_only_inside_a_block(self):
        st = [{'team': {'id': 10}, 'statistics': [{'type': 'Corner Kicks', 'value': None}, {'type': 'Ball Possession', 'value': '61%'}]},
              {'team': {'id': 20}, 'statistics': [{'type': 'Corner Kicks', 'value': 7}]}]
        m = self.match([], st)
        self.assertEqual(m['home_corners'], 0.0)
        self.assertEqual(m['home_possession'], 61.0)
        self.assertIsNone(m['away_possession'])
        m = self.match([], [{'team': {'id': 10}, 'statistics': []}])
        self.assertIsNone(m['home_corners'])
        self.assertFalse(m['statistics_available'])

    def test_referee_cleaning_and_slugs(self):
        self.assertEqual(build_data.clean_referee('Halil Umut Meler, Turkey'), 'Halil Umut Meler')
        self.assertEqual(build_data.clean_referee('  '), None)
        self.assertEqual(build_data.slugify('Kasımpaşa'), 'kasimpasa')
        self.assertEqual(build_data.slugify('İstanbul Başakşehir'), 'istanbul-basaksehir')
        self.assertEqual(build_data.referee_key('ALİ ŞANSALAN'), build_data.referee_key('Ali Şansalan'))

    def test_corner_market_uses_match_total(self):
        st = [{'team': {'id': 10}, 'statistics': [{'type': 'Corner Kicks', 'value': 6}]}, {'team': {'id': 20}, 'statistics': [{'type': 'Corner Kicks', 'value': 5}]}]
        ev = [{'type': 'Goal', 'detail': 'Normal Goal', 'team': {'id': 10}, 'time': {'elapsed': 10}}] * 2 + [{'type': 'Goal', 'detail': 'Normal Goal', 'team': {'id': 20}, 'time': {'elapsed': 80}}]
        m = self.match(ev, st)
        b = build_data.market_block([m], 10)
        self.assertEqual(b['corners_over_9.5'], 1.0)
        self.assertEqual(b['corners_for_avg'], 6.0)


class T4Frontend(unittest.TestCase):
    def test_app_js_parses(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('node not installed')
        r = subprocess.run([node, '--check', str(REPO / 'site/assets/app.js')], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == '__main__':
    unittest.main()
