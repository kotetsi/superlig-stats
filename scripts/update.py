"""Daily refresh of one season (normally the current one).

Typical cost: 1 request for the fixture index, 1 per 20 newly finished fixtures, 1 for standings.
Only fixtures that are finished and lack details, changed status, or got their details too soon
after kickoff (statistics/events still empty) are fetched again.
"""
import argparse
import sys
from datetime import datetime, timedelta, timezone

from apifootball import (FINISHED, Api, ApiError, PlanRestricted, QuotaReached, fetch_details, fixture_id,
                         fixture_status, load_details, load_index, load_meta, save_index, save_meta)

REFETCH_WINDOW = timedelta(days=4)   # keep checking incomplete details for a few days after kickoff
REFETCH_EVERY = timedelta(hours=20)  # but not more than about once per daily run


def _parse(ts):
    try:
        return datetime.fromisoformat(str(ts).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None


def needs_details(item, entry, now):
    if fixture_status(item) not in FINISHED:
        return False
    if not entry or not entry.get('fixture'):
        return int((entry or {}).get('missing_attempts', 0)) < 3
    full = entry['fixture']
    if fixture_status(full) != fixture_status(item):
        return True
    kickoff = _parse(((item.get('fixture') or {}).get('date')))
    fetched = _parse(entry.get('fetched_at'))
    incomplete = not full.get('events') or not full.get('statistics')
    if incomplete and kickoff and fetched and now - kickoff <= REFETCH_WINDOW and now - fetched >= REFETCH_EVERY:
        return True
    return False


def main(argv=None, api=None, now=None):
    parser = argparse.ArgumentParser(description='Refresh changed fixtures of one season')
    parser.add_argument('--season', type=int, required=True)
    args = parser.parse_args(argv)
    season = args.season
    api = api or Api()
    now = now or datetime.now(timezone.utc)
    code = 0
    try:
        if load_meta(season, 'coverage') is None:
            save_meta(season, 'coverage', api.get('leagues', {'id': api.league, 'season': season}))
        body = api.get('fixtures', {'league': api.league, 'season': season})
        save_index(season, body)
        items = load_index(season)['response']
        details = load_details(season)
        todo = sorted((fixture_id(i) for i in items if fixture_id(i) and needs_details(i, details.get(fixture_id(i)), now)), key=int)
        stored = fetch_details(api, season, todo, details) if todo else 0
        standings = load_meta(season, 'standings')
        stale = standings is None or (_parse(standings.get('fetched_at')) or now) < now - timedelta(hours=20)
        if stored or stale:
            save_meta(season, 'standings', api.get('standings', {'league': api.league, 'season': season}))
        print(f'Season {season}: {len(items)} fixtures in index, {len(todo)} needed details, {stored} stored.')
    except QuotaReached as exc:
        print(f'{exc} Refresh stopped; everything fetched so far is saved.', file=sys.stderr)
    except PlanRestricted as exc:
        print(f'Season {season} is not available on the current API plan: {exc}', file=sys.stderr)
        code = 3
    except ApiError as exc:
        print(f'API error: {exc}', file=sys.stderr)
        code = 2
    print(f'API requests made in this run: {api.requests_made}; used today: {api.used_today()}/{api.limit}.')
    return code


if __name__ == '__main__':
    raise SystemExit(main())
