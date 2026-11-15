"""Dixon–Coles model: fit on the last five available seasons, walk-forward backtest on the previous season.

Writes data/public/dixon_coles.json. Makes no API requests.

Leakage rule: every backtest prediction is made by a model trained only on matches that kicked off
before the first match of that prediction week. Forecasts are withheld for teams with too little history.
"""
import argparse
import json
import math
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np
from scipy.optimize import minimize

from apifootball import DATA, write_json

PUBLIC = DATA / 'public'
PLAYED = {'FT', 'AET', 'PEN'}
MAX_SEASONS = 5
HALF_LIFE_DAYS = 730
MIN_TRAIN_MATCHES = 80
MIN_TEAM_MATCHES = 6      # a team needs this many training matches before we publish a forecast for it
L2 = 0.05                 # ridge penalty on attack/defence, keeps promoted teams near average
MAX_GOALS = 10


def load_seasons():
    out = {}
    for path in sorted(PUBLIC.glob('season_*.json')):
        try:
            obj = json.loads(path.read_text(encoding='utf-8'))
            if obj.get('schema_version') == 2:
                out[int(obj['season'])] = obj
        except (ValueError, KeyError, json.JSONDecodeError):
            continue
    return out


def played_matches(season_obj):
    return [m for m in season_obj.get('matches', [])
            if m.get('status') in PLAYED and isinstance(m.get('home_goals'), int) and isinstance(m.get('away_goals'), int)
            and m.get('home_id') is not None and m.get('away_id') is not None and m.get('timestamp')]


def dc_tau(x, y, lh, la, rho):
    if x == 0 and y == 0:
        return 1 - lh * la * rho
    if x == 0 and y == 1:
        return 1 + lh * rho
    if x == 1 and y == 0:
        return 1 + la * rho
    if x == 1 and y == 1:
        return 1 - rho
    return 1.0


def fit(matches, half_life=HALF_LIFE_DAYS):
    if len(matches) < MIN_TRAIN_MATCHES:
        return None
    teams = sorted({m['home_id'] for m in matches} | {m['away_id'] for m in matches})
    if len(teams) < 4:
        return None
    ix = {t: i for i, t in enumerate(teams)}
    n = len(teams)
    h = np.array([ix[m['home_id']] for m in matches])
    a = np.array([ix[m['away_id']] for m in matches])
    x = np.array([m['home_goals'] for m in matches], dtype=float)
    y = np.array([m['away_goals'] for m in matches], dtype=float)
    ts = np.array([m['timestamp'] for m in matches], dtype=float)
    w = np.exp(-math.log(2) * (ts.max() - ts) / 86400 / half_life)
    lgx = np.array([math.lgamma(v + 1) for v in x])
    lgy = np.array([math.lgamma(v + 1) for v in y])
    low = (x <= 1) & (y <= 1)

    def unpack(p):
        return p[0], p[1], p[2:2 + n], p[2 + n:2 + 2 * n], p[-1]

    def negll(p):
        mu, home, att, dfn, rho = unpack(p)
        llh = mu + home + att[h] - dfn[a]
        lla = mu + att[a] - dfn[h]
        lh, la = np.exp(llh), np.exp(lla)
        ll = x * llh - lh - lgx + y * lla - la - lgy
        tau = np.ones_like(lh)
        tau = np.where((x == 0) & (y == 0), 1 - lh * la * rho, tau)
        tau = np.where((x == 0) & (y == 1), 1 + lh * rho, tau)
        tau = np.where((x == 1) & (y == 0), 1 + la * rho, tau)
        tau = np.where((x == 1) & (y == 1), 1 - rho, tau)
        tau = np.where(low, np.maximum(tau, 1e-9), 1.0)
        ll = ll + np.log(tau)
        return -(w * ll).sum() / w.sum() + L2 * ((att ** 2).sum() + (dfn ** 2).sum()) / n

    p0 = np.zeros(2 * n + 3)
    p0[0] = math.log(max(0.2, (x.mean() + y.mean()) / 2))
    p0[1] = 0.2
    bounds = [(-2, 2), (-1, 1)] + [(-2.5, 2.5)] * (2 * n) + [(-0.2, 0.2)]
    res = minimize(negll, p0, method='L-BFGS-B', bounds=bounds, options={'maxiter': 500})
    mu, home, att, dfn, rho = unpack(res.x)
    counts = defaultdict(int)
    for m in matches:
        counts[m['home_id']] += 1
        counts[m['away_id']] += 1
    return {'teams': teams, 'mu': float(mu), 'home': float(home), 'rho': float(rho),
            'attack': {t: float(att[ix[t]]) for t in teams}, 'defence': {t: float(dfn[ix[t]]) for t in teams},
            'team_matches': dict(counts), 'training_matches': len(matches), 'converged': bool(res.success)}


def predict(model, home_id, away_id):
    if home_id not in model['attack'] or away_id not in model['attack']:
        return None
    if model['team_matches'].get(home_id, 0) < MIN_TEAM_MATCHES or model['team_matches'].get(away_id, 0) < MIN_TEAM_MATCHES:
        return None
    lh = math.exp(model['mu'] + model['home'] + model['attack'][home_id] - model['defence'][away_id])
    la = math.exp(model['mu'] + model['attack'][away_id] - model['defence'][home_id])
    rho = model['rho']
    cells = {}
    for i in range(MAX_GOALS + 1):
        pi = math.exp(-lh + i * math.log(lh) - math.lgamma(i + 1))
        for j in range(MAX_GOALS + 1):
            pj = math.exp(-la + j * math.log(la) - math.lgamma(j + 1))
            cells[(i, j)] = max(0.0, pi * pj * dc_tau(i, j, lh, la, rho))
    tot = sum(cells.values())
    cells = {k: v / tot for k, v in cells.items()}
    r = lambda v: round(v, 4)
    return {'home_win': r(sum(p for (i, j), p in cells.items() if i > j)),
            'draw': r(sum(p for (i, j), p in cells.items() if i == j)),
            'away_win': r(sum(p for (i, j), p in cells.items() if i < j)),
            'over_2_5': r(sum(p for (i, j), p in cells.items() if i + j > 2)),
            'btts_yes': r(sum(p for (i, j), p in cells.items() if i > 0 and j > 0)),
            'expected_home_goals': r(lh), 'expected_away_goals': r(la),
            'top_scores': [{'score': f'{i}-{j}', 'probability': r(p)} for (i, j), p in sorted(cells.items(), key=lambda z: -z[1])[:5]]}


def outcome(m):
    return 0 if m['home_goals'] > m['away_goals'] else 1 if m['home_goals'] == m['away_goals'] else 2


def score_probs(preds):
    n = len(preds)
    brier = sum(sum((p[i] - (i == k)) ** 2 for i in range(3)) for p, k in preds) / n
    logloss = -sum(math.log(max(1e-12, p[k])) for p, k in preds) / n
    acc = sum(max(range(3), key=lambda i: p[i]) == k for p, k in preds) / n
    return round(acc, 4), round(brier, 4), round(logloss, 4)


def week_key(ts):
    d = datetime.fromtimestamp(ts, timezone.utc).isocalendar()
    return (d[0], d[1])


def binary_scores(pairs):
    """pairs: (p_yes, happened). Brier and accuracy for a yes/no market."""
    n = len(pairs)
    return {'brier_score': round(sum((p - y) ** 2 for p, y in pairs) / n, 4),
            'accuracy': round(sum((p >= 0.5) == bool(y) for p, y in pairs) / n, 4)}


def calibration(pairs, bins=10):
    """Reliability table: predicted vs observed frequency per 10% probability bin."""
    out = []
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        sel = [(p, y) for p, y in pairs if lo <= p < hi or (b == bins - 1 and p == 1.0)]
        if sel:
            out.append({'bin': f'{int(lo * 100)}-{int(hi * 100)}%', 'n': len(sel),
                        'predicted': round(sum(p for p, _ in sel) / len(sel), 4), 'observed': round(sum(y for _, y in sel) / len(sel), 4)})
    return out


SMALL_SAMPLE = 100


def backtest(rows, season):
    target = sorted((m for m in rows if m['_season'] == season), key=lambda m: m['timestamp'])
    weeks = defaultdict(list)
    for m in target:
        weeks[week_key(m['timestamp'])].append(m)
    preds, base, out, skipped = [], [], [], 0
    ou, ou_base, bt, bt_base, cal = [], [], [], [], []
    leak_free = True
    for key in sorted(weeks):
        fixtures = weeks[key]
        cutoff = min(m['timestamp'] for m in fixtures)
        train = [m for m in rows if m['timestamp'] < cutoff]
        if train and max(m['timestamp'] for m in train) >= cutoff:
            leak_free = False
        model = fit(train)
        if model is None:
            skipped += len(fixtures)
            continue
        freq = [sum(outcome(m) == k for m in train) / len(train) for k in range(3)]
        over_rate = sum(m['home_goals'] + m['away_goals'] > 2 for m in train) / len(train)
        btts_rate = sum(m['home_goals'] > 0 and m['away_goals'] > 0 for m in train) / len(train)
        for m in fixtures:
            p = predict(model, m['home_id'], m['away_id'])
            if p is None:
                skipped += 1
                continue
            probs = [p['home_win'], p['draw'], p['away_win']]
            k = outcome(m)
            over = int(m['home_goals'] + m['away_goals'] > 2)
            both = int(m['home_goals'] > 0 and m['away_goals'] > 0)
            preds.append((probs, k))
            base.append((freq, k))
            ou.append((p['over_2_5'], over))
            ou_base.append((over_rate, over))
            bt.append((p['btts_yes'], both))
            bt_base.append((btts_rate, both))
            cal.append((p['home_win'], int(k == 0)))
            cal.append((p['away_win'], int(k == 2)))
            out.append({'id': m['id'], 'slug': m.get('slug'), 'date': m.get('date'), 'home': m['home'], 'away': m['away'],
                        'score': f"{m['home_goals']}-{m['away_goals']}", 'actual': '1X2'[k],
                        'predicted': '1X2'[max(range(3), key=lambda i: probs[i])], 'probabilities': probs,
                        'over_2_5': p['over_2_5'], 'btts_yes': p['btts_yes'], 'trained_on': len(train)})
    if not preds:
        return {'matches': 0, 'skipped': skipped, 'note': 'Yeterli geçmiş veriyle test edilebilen maç bulunamadı.'}, []
    acc, brier, ll = score_probs(preds)
    bacc, bbrier, bll = score_probs(base)
    return {'matches': len(preds), 'skipped': skipped, 'accuracy': acc, 'brier_score': brier, 'log_loss': ll,
            'baseline': {'method': 'training-window 1/X/2 frequencies', 'accuracy': bacc, 'brier_score': bbrier, 'log_loss': bll},
            'beats_baseline': brier < bbrier and ll < bll,
            'over_2_5': {**binary_scores(ou), 'baseline': binary_scores(ou_base)},
            'btts': {**binary_scores(bt), 'baseline': binary_scores(bt_base)},
            'calibration': calibration(cal), 'small_sample': len(preds) < SMALL_SAMPLE,
            'refits': len(weeks), 'no_leakage_check': leak_free}, out


def main(argv=None):
    parser = argparse.ArgumentParser(description='Train and backtest Dixon–Coles from season JSON')
    parser.add_argument('--allow-not-ready', action='store_true', help='Exit successfully when there is not enough data yet')
    args = parser.parse_args(argv)
    data = load_seasons()

    def not_ready(msg):
        out = PUBLIC / 'dixon_coles.json'
        if out.exists():
            out.unlink()  # never leave an old model next to newer data
        if args.allow_not_ready:
            print('Model not ready: ' + msg)
            return 0
        raise SystemExit(msg)

    if len(data) < 2:
        return not_ready('at least two season JSON files with real matches are required.')
    with_matches = [y for y in sorted(data) if played_matches(data[y])]
    if len(with_matches) < 2:
        return not_ready('at least two seasons with completed matches are required.')
    current = max(data)                    # forecasts are for the newest season (it may not have started yet)
    years = with_matches[-MAX_SEASONS:]     # training: the last five seasons that have completed matches
    earlier = [y for y in years if y < current]
    if not earlier:
        return not_ready('no completed season before the current one.')
    previous = earlier[-1]                  # backtest season
    rows = []
    for y in years:
        for m in played_matches(data[y]):
            rows.append({**m, '_season': y})
    rows.sort(key=lambda m: m['timestamp'])
    final = fit(rows)
    if final is not None and not final['converged']:
        return not_ready('the optimiser did not converge; no forecasts are published.')
    if final is None:
        return not_ready(f'at least {MIN_TRAIN_MATCHES} completed matches are required (found {len(rows)}).')
    forecasts, withheld = {}, 0
    for m in data[current].get('matches', []):
        if m.get('status') in ('NS', 'TBD') and m.get('home_id') is not None:
            p = predict(final, m['home_id'], m['away_id'])
            if p:
                forecasts[str(m['id'])] = p
            else:
                withheld += 1
    test, test_rows = backtest(rows, previous)
    names = {}
    for y in years:
        for t in data[y].get('teams', {}).values():
            names[t['id']] = t['name']
    model = {
        'schema_version': 2, 'method': 'dixon_coles', 'season': current, 'training_seasons': years, 'max_seasons': MAX_SEASONS,
        'training_matches': len(rows), 'half_life_days': HALF_LIFE_DAYS, 'min_team_matches': MIN_TEAM_MATCHES,
        'rho': round(final['rho'], 4), 'home_advantage': round(math.exp(final['home']), 4),
        'converged': final['converged'],
        'ratings': sorted(({'team_id': t, 'team': names.get(t), 'attack': round(final['attack'][t], 4),
                            'defence': round(final['defence'][t], 4), 'matches': final['team_matches'].get(t, 0)}
                           for t in final['teams']), key=lambda r: -(r['attack'] + r['defence'])),
        'fixtures': forecasts, 'withheld_fixtures': withheld,
        'backtest_season': previous, 'backtest': test, 'backtest_predictions': test_rows,
        'data_as_of': max((data[y].get('generated_at') or '') for y in years),
    }
    write_json(PUBLIC / 'dixon_coles.json', model, compact=True)
    print(f"Dixon–Coles: {len(rows)} matches from seasons {years}; {len(forecasts)} forecasts ({withheld} withheld); "
          f"backtest {test.get('matches', 0)} matches on {previous} (skipped {test.get('skipped', 0)}).")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
