"""Turn cached API-Football responses of one season into the public season JSON (schema v2).

No API calls happen here. Missing source data stays null and every rate carries the number of
matches it was computed from (*_n / coverage fields). Nothing is filled in by guesswork.
"""
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone

SCHEMA_VERSION = 2
FINISHED = {'FT', 'AET', 'PEN', 'AWD', 'WO'}
PLAYED = {'FT', 'AET', 'PEN'}            # real matches with events; AWD/WO are awarded results
UPCOMING = {'NS', 'TBD'}
BIG_FOUR = ['Galatasaray', 'Fenerbahçe', 'Beşiktaş', 'Trabzonspor']
GOAL_LINES = [0.5, 1.5, 2.5, 3.5, 4.5]
CORNER_LINES = [7.5, 8.5, 9.5, 10.5, 11.5]
CARD_LINES = [2.5, 3.5, 4.5, 5.5, 6.5]
TEAM_CORNER_LINES = [3.5, 4.5, 5.5, 6.5]
TEAM_CARD_LINES = [0.5, 1.5, 2.5, 3.5]
MIN_SAMPLE = 5  # the site greys out rates computed from fewer matches
BUCKETS = ['1-15', '16-30', '31-45+', '46-60', '61-75', '76-90+']
# Count statistics where API-Football writes null for zero inside an otherwise filled statistics block.
COUNT_STATS = {'Shots on Goal', 'Shots off Goal', 'Total Shots', 'Blocked Shots', 'Shots insidebox',
               'Shots outsidebox', 'Fouls', 'Corner Kicks', 'Offsides', 'Yellow Cards', 'Red Cards',
               'Goalkeeper Saves', 'Total passes', 'Passes accurate'}
TR_MAP = str.maketrans({'ı': 'i', 'İ': 'i', 'ş': 's', 'Ş': 's', 'ğ': 'g', 'Ğ': 'g', 'ü': 'u', 'Ü': 'u',
                        'ö': 'o', 'Ö': 'o', 'ç': 'c', 'Ç': 'c', 'â': 'a', 'î': 'i', 'û': 'u'})


def fold(text):
    """Case/diacritic/Turkish-letter folding used for grouping keys and slugs."""
    s = str(text or '').translate(TR_MAP)
    s = unicodedata.normalize('NFKD', s)
    s = ''.join(c for c in s if not unicodedata.combining(c)).casefold()
    return re.sub(r'[^a-z0-9]+', ' ', s).strip()


def slugify(text):
    return fold(text).replace(' ', '-') or 'x'


def rate(n, d):
    return round(n / d, 4) if d else None


def avg(values):
    vals = [v for v in values if v is not None]
    return (round(sum(vals) / len(vals), 3) if vals else None), len(vals)


def season_label(season):
    return f'{season}–{str(int(season) + 1)[-2:]}'


# ---------------------------------------------------------------- referees

def clean_referee(raw):
    """API-Football often appends the country: 'Halil Umut Meler, Turkey'. Keep the person name only."""
    if not raw or not str(raw).strip():
        return None
    name = str(raw).strip()
    if ',' in name:
        head, tail = name.rsplit(',', 1)
        if fold(tail) in {'turkey', 'turkiye', 'tur'} or len(tail.strip()) <= 3:
            name = head.strip()
    name = re.sub(r'\s+', ' ', name)
    return name or None


def referee_key(name):
    key = fold(name)
    return key or None


# ---------------------------------------------------------------- events

def _minute(e):
    t = e.get('time') or {}
    el = t.get('elapsed')
    return (el if isinstance(el, int) else None), (t.get('extra') if isinstance(t.get('extra'), int) else 0)


def _is_shootout(e):
    el, _ = _minute(e)
    return 'shootout' in str(e.get('comments') or '').casefold() or (el is not None and el > 120)


def _bucket(elapsed):
    if elapsed is None:
        return None
    if elapsed <= 15:
        return 0
    if elapsed <= 30:
        return 1
    if elapsed <= 45:
        return 2
    if elapsed <= 60:
        return 3
    if elapsed <= 75:
        return 4
    return 5


def analyse_events(events, home_id, away_id, home_goals, away_goals):
    """Derive goals, cards and penalties from the event feed.

    The team written on own-goal events is not documented reliably, so the convention is tested
    against the final score for each match. If neither convention reproduces the score, goal
    timing fields are withheld for that match (events_consistent = False).
    """
    out = {'events_available': bool(events)}
    if not events:
        return out
    goals, cards, pens = [], Counter(), Counter()
    for e in events:
        if _is_shootout(e):
            continue
        typ = (e.get('type') or '').casefold()
        detail = (e.get('detail') or '').casefold()
        tid = (e.get('team') or {}).get('id')
        side = 'home' if tid == home_id else 'away' if tid == away_id else None
        if typ == 'goal':
            if 'missed penalty' in detail:
                if side:
                    pens[side + '_missed'] += 1
                continue
            el, ex = _minute(e)
            goals.append({'side': side, 'own': 'own goal' in detail, 'elapsed': el, 'extra': ex, 'penalty': detail == 'penalty'})
            if detail == 'penalty' and side:
                pens[side + '_scored'] += 1
        elif typ == 'card' and side:
            if 'second yellow' in detail or 'red' in detail:
                cards[side + '_red'] += 1
            elif 'yellow' in detail:
                cards[side + '_yellow'] += 1
    for side in ('home', 'away'):
        out[f'{side}_yellow'] = cards[side + '_yellow']
        out[f'{side}_red'] = cards[side + '_red']
        out[f'{side}_penalties'] = pens[side + '_scored'] + pens[side + '_missed']
    out['penalties'] = out['home_penalties'] + out['away_penalties']

    def credited(g, own_as_beneficiary):
        if g['side'] is None:
            return None
        if g['own'] and not own_as_beneficiary:
            return 'away' if g['side'] == 'home' else 'home'
        return g['side']

    convention = None
    for own_as_beneficiary in (True, False):
        c = Counter(credited(g, own_as_beneficiary) for g in goals)
        if None not in c and c['home'] == home_goals and c['away'] == away_goals:
            convention = own_as_beneficiary
            break
    out['events_consistent'] = convention is not None
    if convention is None:
        out['first_goal'] = None
        out['goal_minutes'] = None
        return out
    timed = [g for g in goals if g['elapsed'] is not None]
    if len(timed) != len(goals):
        out['first_goal'] = None
        out['goal_minutes'] = None
        return out
    timed.sort(key=lambda g: (g['elapsed'], g['extra']))
    out['first_goal'] = credited(timed[0], convention) if timed else 'none'
    buckets = {'home': [0] * 6, 'away': [0] * 6}
    for g in timed:
        buckets[credited(g, convention)][_bucket(g['elapsed'])] += 1
    out['goal_minutes'] = buckets
    return out


# ---------------------------------------------------------------- statistics

def _num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        v = v.strip().replace('%', '').replace(',', '.')
        try:
            return float(v)
        except ValueError:
            return None
    return None


def team_statistics(statistics, team_id):
    """Return {type: value} for one team, or None when the API has no statistics block for it."""
    for row in statistics or []:
        if (row.get('team') or {}).get('id') == team_id:
            block = row.get('statistics') or []
            if not block:
                return None
            out = {}
            for x in block:
                typ, val = x.get('type'), x.get('value')
                num = _num(val)
                if num is None and val is None and typ in COUNT_STATS:
                    num = 0.0  # documented API behaviour: zero counts are returned as null
                out[typ] = num
            return out
    return None


def pick(stats, key):
    return None if stats is None else stats.get(key)


# ---------------------------------------------------------------- matches

def round_number(text):
    m = re.search(r'(\d+)\s*$', str(text or ''))
    return int(m.group(1)) if m else None


def normalise_match(item, detail_entry):
    full = (detail_entry or {}).get('fixture')
    src = full or item
    meta = src.get('fixture') or {}
    teams = src.get('teams') or {}
    goals = src.get('goals') or {}
    score = src.get('score') or {}
    league = src.get('league') or {}
    home, away = teams.get('home') or {}, teams.get('away') or {}
    if home.get('id') is None or away.get('id') is None:
        return None
    status = (meta.get('status') or {}).get('short')
    hg, ag = goals.get('home'), goals.get('away')
    ht = score.get('halftime') or {}
    referee_raw = (meta.get('referee') or '').strip() or None
    referee = clean_referee(referee_raw)
    m = {
        'id': meta.get('id'), 'date': meta.get('date'), 'timestamp': meta.get('timestamp'),
        'round': league.get('round'), 'round_no': round_number(league.get('round')),
        'status': status, 'status_long': (meta.get('status') or {}).get('long'),
        'home_id': home.get('id'), 'home': home.get('name'), 'away_id': away.get('id'), 'away': away.get('name'),
        'home_goals': hg if isinstance(hg, int) else None, 'away_goals': ag if isinstance(ag, int) else None,
        'ht_home': ht.get('home') if isinstance(ht.get('home'), int) else None,
        'ht_away': ht.get('away') if isinstance(ht.get('away'), int) else None,
        'referee': referee, 'referee_raw': referee_raw, 'referee_key': referee_key(referee),
        'venue': (meta.get('venue') or {}).get('name'),
        'details_available': bool(full),
    }
    m['slug'] = f"{m['id']}-{slugify(m['home'])}-{slugify(m['away'])}"
    finished = status in FINISHED and m['home_goals'] is not None and m['away_goals'] is not None
    m['finished'] = finished
    ev = {'events_available': False}
    if full and finished and status in PLAYED:
        ev = analyse_events(full.get('events'), m['home_id'], m['away_id'], m['home_goals'], m['away_goals'])
    for k in ('home_yellow', 'away_yellow', 'home_red', 'away_red', 'home_penalties', 'away_penalties', 'penalties', 'first_goal', 'goal_minutes', 'events_consistent'):
        m[k] = ev.get(k)
    m['events_available'] = ev.get('events_available', False)
    hs = team_statistics(full.get('statistics'), m['home_id']) if full else None
    as_ = team_statistics(full.get('statistics'), m['away_id']) if full else None
    m['statistics_available'] = hs is not None and as_ is not None
    for key, field in (('Corner Kicks', 'corners'), ('Total Shots', 'shots'), ('Shots on Goal', 'shots_on_target'),
                       ('Ball Possession', 'possession'), ('Fouls', 'fouls'), ('expected_goals', 'xg')):
        m[f'home_{field}'] = pick(hs, key)
        m[f'away_{field}'] = pick(as_, key)
    return m


# ---------------------------------------------------------------- teams

def result_for(m, tid):
    own, opp = (m['home_goals'], m['away_goals']) if m['home_id'] == tid else (m['away_goals'], m['home_goals'])
    return own, opp, ('W' if own > opp else 'D' if own == opp else 'L')


def table_row(ms, tid):
    w = d = l = gf = ga = 0
    for m in ms:
        own, opp, r = result_for(m, tid)
        gf += own
        ga += opp
        w += r == 'W'
        d += r == 'D'
        l += r == 'L'
    return {'played': len(ms), 'wins': w, 'draws': d, 'losses': l, 'gf': gf, 'ga': ga, 'gd': gf - ga, 'points': 3 * w + d}


def total(m, field):
    a, b = m.get(f'home_{field}'), m.get(f'away_{field}')
    return a + b if a is not None and b is not None else None


def side_value(m, tid, field, own=True):
    is_home = m['home_id'] == tid
    side = ('home' if is_home else 'away') if own else ('away' if is_home else 'home')
    return m.get(f'{side}_{field}')


def htft_match(ms):
    """İY/MS from the match perspective: 1 = home leads/wins, X = draw, 2 = away."""
    c = Counter()
    for m in ms:
        a = '1' if m['ht_home'] > m['ht_away'] else 'X' if m['ht_home'] == m['ht_away'] else '2'
        f = '1' if m['home_goals'] > m['away_goals'] else 'X' if m['home_goals'] == m['away_goals'] else '2'
        c[f'{a}/{f}'] += 1
    return {k: rate(c[k], len(ms)) for k in [f'{a}/{b}' for a in '1X2' for b in '1X2']} if ms else {}


def cards_total(m):
    return m['home_yellow'] + m['away_yellow'] + m['home_red'] + m['away_red']


def league_block(played):
    """League-wide frequencies over all played matches (match perspective)."""
    n = len(played)
    b = {'matches': n,
         'home_win': rate(sum(m['home_goals'] > m['away_goals'] for m in played), n),
         'draw': rate(sum(m['home_goals'] == m['away_goals'] for m in played), n),
         'away_win': rate(sum(m['home_goals'] < m['away_goals'] for m in played), n),
         'btts_yes': rate(sum(m['home_goals'] > 0 and m['away_goals'] > 0 for m in played), n)}
    b['goals_avg'], _ = avg(m['home_goals'] + m['away_goals'] for m in played)
    for line in GOAL_LINES:
        b[f'over_{line}'] = rate(sum(m['home_goals'] + m['away_goals'] > line for m in played), n)
    ht = [m for m in played if m['ht_home'] is not None and m['ht_away'] is not None]
    b['htft_n'] = len(ht)
    b['htft_match'] = htft_match(ht)
    cm = [m for m in played if total(m, 'corners') is not None]
    b['corners_n'] = len(cm)
    b['corners_total_avg'], _ = avg(total(m, 'corners') for m in cm)
    for line in CORNER_LINES:
        b[f'corners_over_{line}'] = rate(sum(total(m, 'corners') > line for m in cm), len(cm))
    km = [m for m in played if m['events_available']]
    b['cards_n'] = len(km)
    b['cards_total_avg'], _ = avg(cards_total(m) for m in km)
    for line in CARD_LINES:
        b[f'cards_over_{line}'] = rate(sum(cards_total(m) > line for m in km), len(km))
    fg = [m for m in played if m.get('first_goal') is not None]
    b['first_goal_n'] = len(fg)
    b['first_goal_home'] = rate(sum(m['first_goal'] == 'home' for m in fg), len(fg))
    b['first_goal_away'] = rate(sum(m['first_goal'] == 'away' for m in fg), len(fg))
    gm = [m for m in played if m.get('goal_minutes')]
    b['goal_minutes_n'] = len(gm)
    b['goals_by_15'] = [sum(m['goal_minutes']['home'][i] + m['goal_minutes']['away'][i] for m in gm) for i in range(6)] if gm else None
    return b


def market_block(ms, tid):
    n = len(ms)
    b = {'matches': n}
    totals = [m['home_goals'] + m['away_goals'] for m in ms]
    for line in GOAL_LINES:
        b[f'over_{line}'] = rate(sum(t > line for t in totals), n)
    b['btts_yes'] = rate(sum(m['home_goals'] > 0 and m['away_goals'] > 0 for m in ms), n)
    b['btts_no'] = rate(sum(not (m['home_goals'] > 0 and m['away_goals'] > 0) for m in ms), n)
    b['scored'] = rate(sum(result_for(m, tid)[0] > 0 for m in ms), n)
    b['clean_sheet'] = rate(sum(result_for(m, tid)[1] == 0 for m in ms), n)
    b['goals_for_avg'], _ = avg(result_for(m, tid)[0] for m in ms)
    b['goals_against_avg'], _ = avg(result_for(m, tid)[1] for m in ms)
    # İY/MS (half-time / full-time) from the team's perspective: 1 = this team leads.
    ht_ms = [m for m in ms if m['ht_home'] is not None and m['ht_away'] is not None]
    htft = Counter()
    for m in ht_ms:
        hown, hopp = (m['ht_home'], m['ht_away']) if m['home_id'] == tid else (m['ht_away'], m['ht_home'])
        own, opp, _ = result_for(m, tid)
        a = 'G' if hown > hopp else 'B' if hown == hopp else 'M'
        c = 'G' if own > opp else 'B' if own == opp else 'M'
        htft[f'{a}/{c}'] += 1
    b['htft_n'] = len(ht_ms)
    b['htft'] = {k: rate(v, len(ht_ms)) for k, v in sorted(htft.items())}
    b['htft_match'] = htft_match(ht_ms)
    # first goal (only matches whose event feed reproduces the score)
    fg = [m for m in ms if m.get('first_goal') is not None]
    own_side = lambda m: 'home' if m['home_id'] == tid else 'away'
    b['first_goal_n'] = len(fg)
    b['first_goal_for'] = rate(sum(m['first_goal'] == own_side(m) for m in fg), len(fg))
    b['first_goal_against'] = rate(sum(m['first_goal'] not in (own_side(m), 'none') for m in fg), len(fg))
    b['no_goal'] = rate(sum(m['first_goal'] == 'none' for m in fg), len(fg))
    gm = [m for m in ms if m.get('goal_minutes')]
    b['goal_minutes_n'] = len(gm)
    b['goals_for_by_15'] = [sum(m['goal_minutes'][own_side(m)][i] for m in gm) for i in range(6)] if gm else None
    b['goals_against_by_15'] = [sum(m['goal_minutes']['away' if own_side(m) == 'home' else 'home'][i] for m in gm) for i in range(6)] if gm else None
    # corners: statistics feed
    corner_ms = [m for m in ms if total(m, 'corners') is not None]
    b['corners_n'] = len(corner_ms)
    b['corners_for_avg'], _ = avg(side_value(m, tid, 'corners') for m in corner_ms)
    b['corners_against_avg'], _ = avg(side_value(m, tid, 'corners', own=False) for m in corner_ms)
    b['corners_total_avg'], _ = avg(total(m, 'corners') for m in corner_ms)
    for line in TEAM_CORNER_LINES:
        b[f'team_corners_over_{line}'] = rate(sum(side_value(m, tid, 'corners') > line for m in corner_ms), len(corner_ms))
    for line in CORNER_LINES:
        b[f'corners_over_{line}'] = rate(sum(total(m, 'corners') > line for m in corner_ms), len(corner_ms))
    # cards: event feed (yellow + red; a second yellow counts as one red)
    card_ms = [m for m in ms if m['events_available']]
    b['cards_n'] = len(card_ms)
    b['cards_for_avg'], _ = avg(side_value(m, tid, 'yellow') + side_value(m, tid, 'red') for m in card_ms)
    b['cards_total_avg'], _ = avg(cards_total(m) for m in card_ms)
    for line in CARD_LINES:
        b[f'cards_over_{line}'] = rate(sum(cards_total(m) > line for m in card_ms), len(card_ms))
    team_cards = lambda m: side_value(m, tid, 'yellow') + side_value(m, tid, 'red')
    for line in TEAM_CARD_LINES:
        b[f'team_cards_over_{line}'] = rate(sum(team_cards(m) > line for m in card_ms), len(card_ms))
    stat_ms = [m for m in ms if m['statistics_available']]
    b['statistics_n'] = len(stat_ms)
    for field in ('shots', 'shots_on_target', 'possession', 'fouls'):
        b[f'{field}_avg'], _ = avg(side_value(m, tid, field) for m in stat_ms)
    return b


# ---------------------------------------------------------------- standings

def official_standings(standings_meta):
    rows = []
    for league in (standings_meta or {}).get('response', []) or []:
        groups = ((league.get('league') or {}).get('standings') or [])
        if not groups:
            continue
        for r in groups[0]:
            team = r.get('team') or {}
            al = r.get('all') or {}
            g = al.get('goals') or {}
            rows.append({'rank': r.get('rank'), 'team_id': team.get('id'), 'team': team.get('name'),
                         'points': r.get('points'), 'gd': r.get('goalsDiff'), 'played': al.get('played'),
                         'wins': al.get('win'), 'draws': al.get('draw'), 'losses': al.get('lose'),
                         'gf': g.get('for'), 'ga': g.get('against'), 'description': r.get('description'),
                         'form': r.get('form')})
    return rows


def coverage_flags(coverage_meta, season):
    for row in (coverage_meta or {}).get('response', []) or []:
        for s in row.get('seasons') or []:
            if int(s.get('year', -1)) == int(season):
                return {'current': s.get('current'), 'start': s.get('start'), 'end': s.get('end'), 'coverage': s.get('coverage')}
    return None


# ---------------------------------------------------------------- build

def build_public(season, index_items, details, standings_meta=None, coverage_meta=None, as_of=None):
    matches = []
    for item in index_items or []:
        fid = str(((item.get('fixture') or {}).get('id')))
        m = normalise_match(item, (details or {}).get(fid))
        if m:
            matches.append(m)
    matches.sort(key=lambda m: (m['timestamp'] or 0, m['id'] or 0))
    finished = [m for m in matches if m['finished']]
    played = [m for m in finished if m['status'] in PLAYED]

    names = {}
    for m in matches:
        names[m['home_id']] = m['home']
        names[m['away_id']] = m['away']

    teams = {}
    for tid, name in names.items():
        ms = [m for m in finished if tid in (m['home_id'], m['away_id'])]
        home_ms = [m for m in ms if m['home_id'] == tid]
        away_ms = [m for m in ms if m['away_id'] == tid]
        form = [{'id': m['id'], 'r': result_for(m, tid)[2]} for m in ms]
        pl = [m for m in played if tid in (m['home_id'], m['away_id'])]
        teams[str(tid)] = {
            'id': tid, 'name': name, 'slug': slugify(name),
            'table': table_row(ms, tid), 'home': table_row(home_ms, tid), 'away': table_row(away_ms, tid),
            'form5': form[-5:], 'form10': form[-10:],
            'markets': {'all': market_block(pl, tid), 'home': market_block([m for m in pl if m['home_id'] == tid], tid),
                        'away': market_block([m for m in pl if m['away_id'] == tid], tid)},
        }

    off = official_standings(standings_meta)
    if off and all(r['team_id'] is not None for r in off):
        standings = {'source': 'api', 'rows': off}
    else:
        rows = sorted(({'team_id': int(t), **teams[t]['table']} for t in teams), key=lambda r: (-r['points'], -r['gd'], -r['gf'], names[r['team_id']]))
        for i, r in enumerate(rows, 1):
            r['rank'] = i
            r['team'] = names[r['team_id']]
        standings = {'source': 'computed', 'rows': rows,
                     'note': 'API standings unavailable; ordered by points, goal difference, goals. Official Süper Lig tiebreaks (head-to-head) are not applied.'}

    # position after each round number, from completed matches of rounds <= N
    by_round = defaultdict(list)
    for m in finished:
        if m['round_no'] is not None:
            by_round[m['round_no']].append(m)
    table_by_round = {str(t): [] for t in names}
    done = []
    for rnd in sorted(by_round):
        done.extend(by_round[rnd])
        rows = []
        for tid in names:
            r = table_row([m for m in done if tid in (m['home_id'], m['away_id'])], tid)
            rows.append((tid, r))
        rows.sort(key=lambda x: (-x[1]['points'], -x[1]['gd'], -x[1]['gf'], names[x[0]]))
        for pos, (tid, r) in enumerate(rows, 1):
            if r['played']:
                table_by_round[str(tid)].append({'round': rnd, 'position': pos, 'points': r['points']})

    # referees
    groups = defaultdict(list)
    for m in finished:
        if m['referee_key']:
            groups[m['referee_key']].append(m)
    big_ids = {}
    for tid, name in names.items():
        for big in BIG_FOUR:
            if fold(name) == fold(big) or fold(name).startswith(fold(big) + ' '):
                big_ids[big] = tid
    referees = {}
    for key, ms in groups.items():
        spellings = Counter(m['referee'] for m in ms)
        ev = [m for m in ms if m['events_available']]
        yellow = sum(m['home_yellow'] + m['away_yellow'] for m in ev)
        red = sum(m['home_red'] + m['away_red'] for m in ev)
        pens = sum(m['penalties'] for m in ev)
        big = {}
        for label, tid in big_ids.items():
            bm = [m for m in ms if tid in (m['home_id'], m['away_id'])]
            row = table_row(bm, tid)
            bev = [m for m in bm if m['events_available']]
            row['cards_for_avg'], _ = avg(side_value(m, tid, 'yellow') + side_value(m, tid, 'red') for m in bev)
            row['penalties_for'] = sum(side_value(m, tid, 'penalties') for m in bev) if bev else None
            row['penalties_against'] = sum(side_value(m, tid, 'penalties', own=False) for m in bev) if bev else None
            row['events_n'] = len(bev)
            big[label] = {'team_id': tid, **row}
        referees[key] = {
            'key': key, 'name': spellings.most_common(1)[0][0], 'slug': slugify(spellings.most_common(1)[0][0]),
            'aliases': [{'name': n, 'matches': c} for n, c in sorted(spellings.items())],
            'raw_aliases': sorted({m['referee_raw'] for m in ms if m['referee_raw']}),
            'matches': len(ms), 'events_n': len(ev),
            'yellow_per_match': rate(yellow, len(ev)), 'red_per_match': rate(red, len(ev)), 'penalties_per_match': rate(pens, len(ev)),
            'home_win_rate': rate(sum(m['home_goals'] > m['away_goals'] for m in ms), len(ms)),
            'draw_rate': rate(sum(m['home_goals'] == m['away_goals'] for m in ms), len(ms)),
            'away_win_rate': rate(sum(m['home_goals'] < m['away_goals'] for m in ms), len(ms)),
            'big_four': big, 'match_ids': [m['id'] for m in ms],
        }
        referees[key].update(_ref_stats(ms))
    # flag (never merge) keys that may be the same person: same surname and same first initial
    by_sig = defaultdict(list)
    for key in referees:
        parts = key.split()
        if len(parts) >= 2:
            by_sig[(parts[0][0], parts[-1])].append(key)
    for keys in by_sig.values():
        if len(keys) > 1:
            for k in keys:
                referees[k]['possible_duplicates'] = sorted(x for x in keys if x != k)

    quality = {
        'matches': len(matches), 'finished': len(finished), 'played': len(played),
        'with_details': sum(m['details_available'] for m in finished),
        'with_events': sum(m['events_available'] for m in played),
        'events_consistent': sum(bool(m['events_consistent']) for m in played),
        'with_statistics': sum(m['statistics_available'] for m in played),
        'with_referee': sum(bool(m['referee']) for m in finished),
    }
    return {
        'schema_version': SCHEMA_VERSION, 'season': int(season), 'season_label': season_label(season),
        'generated_at': as_of or datetime.now(timezone.utc).isoformat(timespec='seconds'), 'source': 'API-Football v3',
        'coverage': coverage_flags(coverage_meta, season), 'data_quality': quality,
        'match_count': len(matches), 'completed_match_count': len(finished),
        'standings': standings, 'teams': teams, 'matches': matches,
        'table_by_round': table_by_round, 'referees': referees,
        'league': league_block(played),
        'lines': {'goals': GOAL_LINES, 'corners': CORNER_LINES, 'cards': CARD_LINES, 'team_corners': TEAM_CORNER_LINES,
                  'team_cards': TEAM_CARD_LINES, 'minute_buckets': BUCKETS, 'min_sample': MIN_SAMPLE},
    }


# ---------------------------------------------------------------- referees across seasons

def _ref_stats(ms):
    ev = [m for m in ms if m['events_available']]
    out = {'matches': len(ms), 'events_n': len(ev),
           'yellow_per_match': rate(sum(m['home_yellow'] + m['away_yellow'] for m in ev), len(ev)),
           'red_per_match': rate(sum(m['home_red'] + m['away_red'] for m in ev), len(ev)),
           'cards_per_match': rate(sum(cards_total(m) for m in ev), len(ev)),
           'penalties_per_match': rate(sum(m['penalties'] for m in ev), len(ev)),
           'home_win_rate': rate(sum(m['home_goals'] > m['away_goals'] for m in ms), len(ms)),
           'draw_rate': rate(sum(m['home_goals'] == m['away_goals'] for m in ms), len(ms)),
           'away_win_rate': rate(sum(m['home_goals'] < m['away_goals'] for m in ms), len(ms)),
           'goals_per_match': rate(sum(m['home_goals'] + m['away_goals'] for m in ms), len(ms))}
    for line in (3.5, 4.5, 5.5):
        out[f'cards_over_{line}'] = rate(sum(cards_total(m) > line for m in ev), len(ev))
    hy = [m['home_yellow'] + m['home_red'] for m in ev]
    ay = [m['away_yellow'] + m['away_red'] for m in ev]
    out['home_cards_per_match'] = rate(sum(hy), len(ev))
    out['away_cards_per_match'] = rate(sum(ay), len(ev))
    return out


def aggregate_referees(seasons):
    """seasons: {year: season_json}. One record per referee key across all seasons."""
    groups = defaultdict(list)
    names_by_id = {}
    for y, s in sorted(seasons.items()):
        for m in s.get('matches', []):
            names_by_id[m['home_id']] = m['home']
            names_by_id[m['away_id']] = m['away']
            if m.get('finished') and m.get('referee_key'):
                groups[m['referee_key']].append({**m, '_season': y})
    big_ids = {}
    for tid, name in names_by_id.items():
        for big in BIG_FOUR:
            if fold(name) == fold(big) or fold(name).startswith(fold(big) + ' '):
                big_ids[big] = tid
    out = {}
    for key, ms in groups.items():
        ms.sort(key=lambda m: m['timestamp'] or 0)
        spellings = Counter(m['referee'] for m in ms)
        name = spellings.most_common(1)[0][0]
        big = {}
        for label, tid in big_ids.items():
            bm = [m for m in ms if tid in (m['home_id'], m['away_id'])]
            if not bm:
                continue
            row = table_row(bm, tid)
            bev = [m for m in bm if m['events_available']]
            row['events_n'] = len(bev)
            row['cards_for_avg'], _ = avg(side_value(m, tid, 'yellow') + side_value(m, tid, 'red') for m in bev)
            row['cards_against_avg'], _ = avg(side_value(m, tid, 'yellow', own=False) + side_value(m, tid, 'red', own=False) for m in bev)
            row['penalties_for'] = sum(side_value(m, tid, 'penalties') for m in bev) if bev else None
            row['penalties_against'] = sum(side_value(m, tid, 'penalties', own=False) for m in bev) if bev else None
            row['win_rate'] = rate(row['wins'], row['played'])
            big[label] = {'team_id': tid, **row}
        out[key] = {
            'key': key, 'name': name, 'slug': slugify(name),
            'aliases': [{'name': n, 'matches': c} for n, c in sorted(spellings.items())],
            'raw_aliases': sorted({m['referee_raw'] for m in ms if m.get('referee_raw')}),
            'first_match': ms[0]['date'], 'last_match': ms[-1]['date'],
            'totals': _ref_stats(ms),
            'seasons': {str(y): _ref_stats([m for m in ms if m['_season'] == y]) for y in sorted({m['_season'] for m in ms})},
            'big_four': big,
            'matches': [{'id': m['id'], 'slug': m['slug'], 'season': m['_season'], 'date': m['date'], 'round_no': m['round_no'],
                         'home': m['home'], 'away': m['away'], 'home_goals': m['home_goals'], 'away_goals': m['away_goals'],
                         'yellow': (m['home_yellow'] + m['away_yellow']) if m['events_available'] else None,
                         'red': (m['home_red'] + m['away_red']) if m['events_available'] else None,
                         'penalties': m['penalties'] if m['events_available'] else None} for m in reversed(ms)],
        }
    by_sig = defaultdict(list)
    for key in out:
        parts = key.split()
        if len(parts) >= 2:
            by_sig[(parts[0][0], parts[-1])].append(key)
    for keys in by_sig.values():
        if len(keys) > 1:
            for k in keys:
                out[k]['possible_duplicates'] = [{'key': x, 'name': out[x]['name']} for x in sorted(keys) if x != k]
    return {'schema_version': SCHEMA_VERSION, 'seasons': sorted(seasons), 'big_four': list(big_ids), 'referees': out}


# ---------------------------------------------------------------- per-match timeline and cross-season results

def timeline(full, home_id, away_id):
    """Compact goal/card timeline for match pages. None when the API has no events."""
    events = (full or {}).get('events')
    if not events:
        return None
    out = []
    for e in events:
        if _is_shootout(e):
            continue
        typ = (e.get('type') or '').casefold()
        detail = (e.get('detail') or '').casefold()
        tid = (e.get('team') or {}).get('id')
        side = 'home' if tid == home_id else 'away' if tid == away_id else None
        if side is None:
            continue
        if typ == 'goal':
            kind = 'mpen' if 'missed' in detail else 'own' if 'own goal' in detail else 'pen' if detail == 'penalty' else 'goal'
        elif typ == 'card':
            kind = 'r' if ('red' in detail or 'second yellow' in detail) else 'y'
        elif typ == 'var':
            kind = 'var'
        else:
            continue
        el, ex = _minute(e)
        out.append({'m': el, 'x': ex or None, 'k': kind, 's': side, 'p': (e.get('player') or {}).get('name'),
                    'a': (e.get('assist') or {}).get('name') if kind == 'goal' else None,
                    'd': e.get('detail') if kind == 'var' else None})
    out.sort(key=lambda r: ((r['m'] or 0), (r['x'] or 0)))
    return out


def season_timelines(index_items, details):
    out = {}
    for item in index_items or []:
        fid = str(((item.get('fixture') or {}).get('id')))
        full = ((details or {}).get(fid) or {}).get('fixture')
        if not full:
            continue
        teams = full.get('teams') or {}
        tl = timeline(full, (teams.get('home') or {}).get('id'), (teams.get('away') or {}).get('id'))
        if tl is not None:
            out[fid] = tl
    return out


def results_index(seasons):
    """All finished matches of all seasons, compact, for cross-season H2H."""
    teams, rows = {}, []
    for y, s in sorted(seasons.items()):
        for t in s.get('teams', {}).values():
            teams[str(t['id'])] = {'name': t['name'], 'slug': t['slug']}
        for m in s.get('matches', []):
            if m.get('finished'):
                rows.append([m['id'], m['slug'], y, m['timestamp'], m['home_id'], m['away_id'], m['home_goals'], m['away_goals'], m['ht_home'], m['ht_away']])
    rows.sort(key=lambda r: r[3] or 0)
    return {'schema_version': SCHEMA_VERSION, 'columns': ['id', 'slug', 'season', 'timestamp', 'home_id', 'away_id', 'home_goals', 'away_goals', 'ht_home', 'ht_away'],
            'teams': teams, 'rows': rows}


# ---------------------------------------------------------------- players (from fixture player statistics, no extra requests)

def _int0(v):
    """Inside a player's statistics block the API writes null for zero counts."""
    try:
        return int(v) if v is not None else 0
    except (TypeError, ValueError):
        return 0


def _rating(v):
    try:
        r = float(v)
        return r if 0 < r <= 10 else None
    except (TypeError, ValueError):
        return None


def player_slug(name, pid):
    return f'{slugify(name)}-{pid}'


def season_players(index_items, details):
    """Aggregate per-player season stats from the 'players' block of every fetched fixture.

    Only matches whose fixture object carries player statistics count; coverage is reported per team.
    """
    players, team_cov, team_names = {}, Counter(), {}
    for item in index_items or []:
        fid = str(((item.get('fixture') or {}).get('id')))
        full = ((details or {}).get(fid) or {}).get('fixture')
        if not full or (((full.get('fixture') or {}).get('status') or {}).get('short')) not in PLAYED:
            continue
        ts = (full.get('fixture') or {}).get('timestamp') or 0
        for block in full.get('players') or []:
            team = block.get('team') or {}
            tid = team.get('id')
            if tid is None or not block.get('players'):
                continue
            team_cov[tid] += 1
            team_names[tid] = team.get('name')
            for row in block['players']:
                pl = row.get('player') or {}
                pid = pl.get('id')
                st = (row.get('statistics') or [{}])[0] or {}
                g = st.get('games') or {}
                minutes = g.get('minutes')
                if pid is None or not isinstance(minutes, int) or minutes <= 0:
                    continue
                p = players.setdefault(pid, {'id': pid, 'name': pl.get('name'), 'slug': player_slug(pl.get('name') or str(pid), pid),
                                             'teams': {}, 'position': None, 'apps': 0, 'starts': 0, 'minutes': 0, 'goals': 0, 'assists': 0,
                                             'yellow': 0, 'red': 0, 'shots': 0, 'shots_on': 0, 'key_passes': 0, 'pen_scored': 0, 'pen_missed': 0,
                                             '_ratings': [], '_last': 0})
                if ts >= p['_last']:
                    p['_last'] = ts
                    p['team_id'] = tid
                    p['position'] = g.get('position') or p['position']
                    p['number'] = g.get('number')
                p['teams'][str(tid)] = team.get('name')
                bt = p.setdefault('by_team', {}).setdefault(str(tid), {'apps': 0, 'minutes': 0, 'goals': 0, 'assists': 0, 'yellow': 0, 'red': 0})
                bt['apps'] += 1
                bt['minutes'] += minutes
                g_ = st.get('goals') or {}
                c_ = st.get('cards') or {}
                bt['goals'] += _int0(g_.get('total'))
                bt['assists'] += _int0(g_.get('assists'))
                bt['yellow'] += _int0(c_.get('yellow'))
                bt['red'] += _int0(c_.get('red'))
                p['apps'] += 1
                p['starts'] += 0 if g.get('substitute') else 1
                p['minutes'] += minutes
                goals = st.get('goals') or {}
                p['goals'] += _int0(goals.get('total'))
                p['assists'] += _int0(goals.get('assists'))
                cards = st.get('cards') or {}
                p['yellow'] += _int0(cards.get('yellow'))
                p['red'] += _int0(cards.get('red'))
                shots = st.get('shots') or {}
                p['shots'] += _int0(shots.get('total'))
                p['shots_on'] += _int0(shots.get('on'))
                p['key_passes'] += _int0((st.get('passes') or {}).get('key'))
                pen = st.get('penalty') or {}
                p['pen_scored'] += _int0(pen.get('scored'))
                p['pen_missed'] += _int0(pen.get('missed'))
                r = _rating(g.get('rating'))
                if r is not None:
                    p['_ratings'].append(r)
    for p in players.values():
        rs = p.pop('_ratings')
        p.pop('_last', None)
        p['rating'] = round(sum(rs) / len(rs), 2) if rs else None
        p['rating_n'] = len(rs)
        p['goals_per_90'] = round(p['goals'] * 90 / p['minutes'], 2) if p['minutes'] else None
        p['ga_per_90'] = round((p['goals'] + p['assists']) * 90 / p['minutes'], 2) if p['minutes'] else None
    return {'schema_version': SCHEMA_VERSION, 'team_coverage': {str(k): v for k, v in team_cov.items()},
            'players': {str(k): v for k, v in players.items()}}


# ---------------------------------------------------------------- Turkish players abroad

def legionnaire_rows(player_files, national_team_id):
    """player_files: list of players?id=&season= responses. Keeps players the API lists with nationality Turkey and
    only their club competitions outside Turkey (national-team matches excluded)."""
    out = {}
    for body in player_files:
        for row in (body or {}).get('response', []) or []:
            pl = row.get('player') or {}
            if fold(pl.get('nationality')) not in ('turkey', 'turkiye'):
                continue
            comps = []
            for st in row.get('statistics') or []:
                team, league = st.get('team') or {}, st.get('league') or {}
                if team.get('id') == national_team_id or league.get('country') in ('Turkey', 'Türkiye'):
                    continue
                g = st.get('games') or {}
                apps = g.get('appearences') if g.get('appearences') is not None else g.get('appearances')
                if not apps:
                    continue
                goals = st.get('goals') or {}
                cards = st.get('cards') or {}
                comps.append({'team_id': team.get('id'), 'team': team.get('name'), 'league_id': league.get('id'), 'league': league.get('name'),
                              'country': league.get('country'), 'apps': _int0(apps), 'starts': _int0(g.get('lineups')), 'minutes': _int0(g.get('minutes')),
                              'goals': _int0(goals.get('total')), 'assists': _int0(goals.get('assists')),
                              'yellow': _int0(cards.get('yellow')), 'red': _int0(cards.get('red')) + _int0(cards.get('yellowred')),
                              'rating': _rating(g.get('rating')), 'position': g.get('position')})
            if not comps:
                continue
            main = max(comps, key=lambda c: c['minutes'])
            tot = {k: sum(c[k] for c in comps) for k in ('apps', 'starts', 'minutes', 'goals', 'assists', 'yellow', 'red')}
            out[str(pl.get('id'))] = {'id': pl.get('id'), 'name': pl.get('name'), 'firstname': pl.get('firstname'), 'lastname': pl.get('lastname'),
                                      'age': pl.get('age'), 'position': main['position'], 'club': main['team'], 'league': main['league'],
                                      'country': main['country'], 'competitions': comps, 'totals': tot}
    return out


def weekly_delta(current, previous):
    """Difference of season totals between two snapshots: what changed during the week."""
    if previous is None:
        return None
    keys = ('apps', 'starts', 'minutes', 'goals', 'assists', 'yellow', 'red')
    return {k: current[k] - previous.get(k, 0) for k in keys}
