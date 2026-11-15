"""Turkish players abroad (lejyonerler), weekly.

Who is checked: the current Turkey national-team squad from the API (players/squads), plus optional extra player ids
in config/legionnaire_candidates.json. Every candidate's nationality is confirmed by the API itself (players?id=)
before he is listed; the extra file only nominates, it never adds anyone by itself.

Cost per weekly run: national team id (once, cached), squad (1), one request per candidate whose data is older than
6 days. --max-requests (default 40) caps a run; the shared 100/day ledger still applies and the run resumes later.
"""
import argparse
import json
import sys
from datetime import datetime, timedelta, timezone

import apifootball
from apifootball import DATA, ROOT, Api, ApiError, PlanRestricted, QuotaReached, read_json, utc_now, write_json
from build_data import legionnaire_rows

LEG = DATA / 'raw' / 'legionnaires'
STALE = timedelta(days=6)


def _age(meta):
    try:
        return datetime.now(timezone.utc) - datetime.fromisoformat(meta['fetched_at'])
    except (KeyError, TypeError, ValueError):
        return timedelta(days=9999)


def candidates(squad, extra_path):
    ids = set()
    for team in (squad or {}).get('response', []) or []:
        for p in team.get('players') or []:
            if p.get('id') is not None:
                ids.add(int(p['id']))
    extra = read_json(extra_path, []) or []
    ids |= {int(x) for x in extra if str(x).isdigit()}
    return sorted(ids)


def snapshot(season, national_id):
    files = [read_json(f) for f in sorted((LEG / str(season)).glob('player_*.json'))]
    rows = legionnaire_rows(files, national_id)
    snap = {pid: r['totals'] for pid, r in rows.items()}
    write_json(LEG / str(season) / 'snapshots' / f'{apifootball.utc_today()}.json', snap)
    return len(rows)


def main(argv=None, api=None):
    ap = argparse.ArgumentParser(description='Weekly refresh of Turkish players abroad')
    ap.add_argument('--season', type=int, required=True, help='Season start year, e.g. 2026')
    ap.add_argument('--max-requests', type=int, default=40)
    a = ap.parse_args(argv)
    api = api or Api(require_league=False)
    made_at_start = api.requests_made
    budget = lambda: a.max_requests - (api.requests_made - made_at_start)
    code = 0
    try:
        national = read_json(LEG / 'national.json')
        if not national:
            body = api.get('teams', {'name': 'Turkey'})
            nat = [r['team'] for r in body.get('response', []) if (r.get('team') or {}).get('national')]
            if not nat:
                raise SystemExit('The API returned no national team named Turkey; nothing to do.')
            national = {'id': nat[0]['id'], 'name': nat[0]['name'], 'fetched_at': utc_now()}
            write_json(LEG / 'national.json', national)
        squad = read_json(LEG / 'squad.json')
        if (squad is None or _age(squad) > STALE) and budget() > 0:
            body = api.get('players/squads', {'team': national['id']})
            squad = {'fetched_at': utc_now(), 'response': body.get('response', [])}
            write_json(LEG / 'squad.json', squad)
        ids = candidates(squad, ROOT / 'config' / 'legionnaire_candidates.json')
        stale = []
        for pid in ids:
            meta = read_json(LEG / str(a.season) / f'player_{pid}.json')
            if meta is None or _age(meta) > STALE:
                stale.append((_age(meta) if meta else timedelta(days=99999), pid))
        stale.sort(reverse=True)  # oldest first
        fetched = 0
        for _, pid in stale:
            if budget() <= 0:
                print('Run budget used; remaining players are fetched in the next run.')
                break
            body = api.get('players', {'id': pid, 'season': a.season})
            write_json(LEG / str(a.season) / f'player_{pid}.json', {'fetched_at': utc_now(), 'response': body.get('response', [])})
            fetched += 1
        n = snapshot(a.season, national['id'])
        print(f'Candidates: {len(ids)}, refreshed: {fetched}, still stale: {len(stale) - fetched}, listed abroad: {n}.')
    except QuotaReached as exc:
        print(f'{exc} Saved progress; the next run continues.', file=sys.stderr)
    except PlanRestricted as exc:
        print(f'Not available on the current API plan: {exc}', file=sys.stderr)
        code = 3
    except ApiError as exc:
        print(f'API error: {exc}', file=sys.stderr)
        code = 2
    print(f'API requests made in this run: {api.requests_made - made_at_start}; used today: {api.used_today()}/{api.limit}.')
    return code


if __name__ == '__main__':
    raise SystemExit(main())
