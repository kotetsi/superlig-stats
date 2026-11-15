"""Synthetic API-Football responses for offline tests only.

Nothing here is ever copied to data/ or the site: tests run in a temporary SUPERLIG_ROOT.
"""
import math
import random

TEAMS = [(611, 'Fenerbahçe'), (645, 'Galatasaray'), (549, 'Beşiktaş'), (998, 'Trabzonspor'), (3563, 'Kasımpaşa'),
         (1005, 'Göztepe'), (1007, 'Çaykur Rizespor'), (3573, 'İstanbul Başakşehir'), (607, 'Konyaspor'), (994, 'Alanyaspor')]
STRENGTH = {611: 0.45, 645: 0.5, 549: 0.3, 998: 0.25, 3563: -0.1, 1005: -0.05, 1007: -0.2, 3573: 0.05, 607: -0.3, 994: -0.25}
REFEREES = ['Halil Umut Meler, Turkey', 'Arda Kardeşler, Turkey', 'Ali Şansalan', 'Zorbay Küçük, Turkey', 'A. Kardeşler']


def poisson(rng, lam):
    L, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= L:
            return k
        k += 1


def make_season(season, finished_rounds=None, seed=0, league=203):
    """Double round robin of 10 teams = 18 rounds, 90 fixtures."""
    rng = random.Random(season * 7 + seed)
    ids = [t for t, _ in TEAMS]
    names = dict(TEAMS)
    rounds = []
    order = ids[:]
    for r in range(len(ids) - 1):
        pairs = [(order[i], order[-1 - i]) for i in range(len(ids) // 2)]
        rounds.append(pairs)
        order = [order[0]] + [order[-1]] + order[1:-1]
    rounds = rounds + [[(b, a) for a, b in rnd] for rnd in rounds]
    finished_rounds = len(rounds) if finished_rounds is None else finished_rounds
    start = 1_690_000_000 + (season - 2022) * 365 * 86400
    fixtures, full = [], {}
    fid = season * 10000
    for rn, pairs in enumerate(rounds, 1):
        for k, (h, a) in enumerate(pairs):
            fid += 1
            ts = start + (rn - 1) * 7 * 86400 + k * 3600
            done = rn <= finished_rounds
            lh = math.exp(0.25 + 0.2 + STRENGTH[h] - 0.5 * STRENGTH[a])
            la = math.exp(0.25 + STRENGTH[a] - 0.5 * STRENGTH[h])
            hg, ag = (poisson(rng, lh), poisson(rng, la)) if done else (None, None)
            base = {
                'fixture': {'id': fid, 'referee': REFEREES[fid % len(REFEREES)], 'timestamp': ts,
                            'date': f'2000-01-01T00:00:00+00:00', 'venue': {'name': 'Stadyum'},
                            'status': {'short': 'FT' if done else 'NS', 'long': 'Match Finished' if done else 'Not Started'}},
                'league': {'id': league, 'season': season, 'round': f'Regular Season - {rn}'},
                'teams': {'home': {'id': h, 'name': names[h]}, 'away': {'id': a, 'name': names[a]}},
                'goals': {'home': hg, 'away': ag},
                'score': {'halftime': {'home': None if not done else min(hg, poisson(rng, 0.5)), 'away': None if not done else min(ag, poisson(rng, 0.4))},
                          'fulltime': {'home': hg, 'away': ag}},
            }
            from datetime import datetime, timezone
            base['fixture']['date'] = datetime.fromtimestamp(ts, timezone.utc).isoformat()
            fixtures.append(base)
            if done:
                events = []
                for side, tid, n in (('home', h, hg), ('away', a, ag)):
                    for _ in range(n):
                        events.append({'time': {'elapsed': rng.randint(1, 90), 'extra': None}, 'team': {'id': tid}, 'type': 'Goal', 'detail': 'Normal Goal'})
                    for _ in range(poisson(rng, 2.0)):
                        events.append({'time': {'elapsed': rng.randint(1, 90), 'extra': None}, 'team': {'id': tid}, 'type': 'Card', 'detail': 'Yellow Card'})
                    events.append({'time': {'elapsed': 60, 'extra': None}, 'team': {'id': tid}, 'type': 'subst', 'detail': 'Substitution 1'})
                stats = [{'team': {'id': tid}, 'statistics': [{'type': 'Corner Kicks', 'value': poisson(rng, 5) or None},
                                                               {'type': 'Total Shots', 'value': poisson(rng, 12)},
                                                               {'type': 'Shots on Goal', 'value': poisson(rng, 4)},
                                                               {'type': 'Ball Possession', 'value': '50%'},
                                                               {'type': 'Red Cards', 'value': None}]} for tid in (h, a)]
                pblocks = []
                for side, tid, n in (('home', h, hg), ('away', a, ag)):
                    plist = []
                    for k2 in range(12):
                        pid = tid * 100 + k2
                        mins = 90 if k2 < 10 else (25 if k2 == 10 else None)
                        plist.append({'player': {'id': pid, 'name': f'{names[tid][:3]} Oyuncu {k2}'},
                                      'statistics': [{'games': {'minutes': mins, 'position': 'F' if k2 < 2 else 'M', 'rating': '7.1' if mins else None, 'substitute': k2 >= 10},
                                                      'goals': {'total': (n if k2 == 0 else None), 'assists': (1 if k2 == 1 and n else None)},
                                                      'cards': {'yellow': 0, 'red': 0}, 'shots': {'total': 2, 'on': 1}, 'passes': {'key': None}, 'penalty': {}}]})
                    pblocks.append({'team': {'id': tid, 'name': names[tid]}, 'players': plist})
                full[fid] = {**base, 'events': events, 'statistics': stats, 'lineups': [], 'players': pblocks}
    return fixtures, full


class FakeResponse:
    def __init__(self, body, status=200, headers=None):
        self._body, self.status_code, self.headers = body, status, headers or {}

    def json(self):
        return self._body


class FakeSession:
    """Serves fixtures, fixtures?ids=, standings and leagues for several seasons."""

    def __init__(self, seasons):
        self.headers = {}
        self.calls = []
        self.data = {}
        for s, rounds in seasons.items():
            self.data[s] = make_season(s, rounds)
        self.queue = []  # optional scripted responses (body, status, headers)

    def get(self, url, params=None, timeout=None):
        endpoint = url.split('v3.football.api-sports.io/')[-1]
        self.calls.append((endpoint, dict(params or {})))
        if self.queue:
            return FakeResponse(*self.queue.pop(0))
        if endpoint == 'leagues':
            s = int(params['season'])
            return FakeResponse({'errors': [], 'response': [{'league': {'id': 203}, 'seasons': [{'year': s, 'current': s == max(self.data), 'coverage': {'fixtures': {'events': True, 'statistics_fixtures': True}}}]}]})
        if endpoint == 'teams':
            return FakeResponse({'errors': [], 'response': [{'team': {'id': 777, 'name': 'Turkey', 'national': True}}, {'team': {'id': 9999, 'name': 'Turkey U21', 'national': True}}]})
        if endpoint == 'players/squads':
            return FakeResponse({'errors': [], 'response': [{'team': {'id': 777}, 'players': [{'id': i} for i in (1, 2, 3)]}]})
        if endpoint == 'players':
            pid = int(params['id'])
            self.player_goals = getattr(self, 'player_goals', 0)
            stats = {1: [({'id': 541, 'name': 'Real Madrid'}, {'id': 140, 'name': 'La Liga', 'country': 'Spain'}, 10, 3 + self.player_goals),
                         ({'id': 777, 'name': 'Turkey'}, {'id': 5, 'name': 'UEFA Nations League', 'country': 'World'}, 2, 1)],
                     2: [({'id': 645, 'name': 'Galatasaray'}, {'id': 203, 'name': 'Süper Lig', 'country': 'Turkey'}, 12, 5)],
                     3: [({'id': 496, 'name': 'Juventus'}, {'id': 135, 'name': 'Serie A', 'country': 'Italy'}, 8, 1)]}[pid]
            nat = 'Turkey' if pid != 3 else 'Germany'
            return FakeResponse({'errors': [], 'response': [{'player': {'id': pid, 'name': f'Player {pid}', 'nationality': nat},
                                                             'statistics': [{'team': t, 'league': l, 'games': {'appearences': ap, 'lineups': ap, 'minutes': ap * 80, 'position': 'Midfielder'},
                                                                             'goals': {'total': g, 'assists': None}, 'cards': {'yellow': None, 'red': None}} for t, l, ap, g in stats]}]})
        if endpoint == 'standings':
            return FakeResponse({'errors': [], 'response': []})
        if endpoint == 'fixtures' and 'ids' in params:
            out = []
            for fid in params['ids'].split('-'):
                fid = int(fid)
                full = self.data[fid // 10000][1].get(fid)
                if full:
                    out.append(full)
            assert len(params['ids'].split('-')) <= 20
            return FakeResponse({'errors': [], 'response': out})
        if endpoint == 'fixtures':
            return FakeResponse({'errors': [], 'response': self.data[int(params['season'])][0]})
        return FakeResponse({'errors': {'endpoint': 'unknown'}, 'response': []})

    def finish_round(self, season, rnd):
        fixtures, full = self.data[season]
        _, ref_full = make_season(season, None)
        for f in fixtures:
            if f['league']['round'].endswith(f' {rnd}'):
                fid = f['fixture']['id']
                done = ref_full[fid]
                f.update({k: done[k] for k in ('goals', 'score')})
                f['fixture']['status'] = {'short': 'FT', 'long': 'Match Finished'}
                full[fid] = done
