"""Resumable historical import of several Süper Lig seasons from API-Football v3.

Per season it needs about 19 requests: league coverage (1), fixture index (1),
fixture details in batches of 20 (~16 for 306 matches) and final standings (1).
Progress is saved after every request; rerun the same command after the UTC day resets.
"""
import argparse
import sys

from apifootball import (DATA, FINISHED, Api, ApiError, PlanRestricted, QuotaReached, fetch_details,
                         fixture_id, fixture_status, load_details, load_index, load_meta, read_json,
                         save_index, save_meta, utc_now, write_json)

STATE = DATA / 'backfill_state.json'
MAX_MISSING_ATTEMPTS = 3


def pending_detail_ids(index_items, details):
    ids = []
    for item in index_items:
        fid = fixture_id(item)
        if not fid or fixture_status(item) not in FINISHED:
            continue
        entry = details.get(fid) or {}
        if entry.get('fixture'):
            continue
        if int(entry.get('missing_attempts', 0)) >= MAX_MISSING_ATTEMPTS:
            continue
        ids.append(fid)
    return sorted(ids, key=int)


def backfill_season(api, season, state):
    st = state['seasons'].setdefault(str(season), {})
    if load_meta(season, 'coverage') is None:
        save_meta(season, 'coverage', api.get('leagues', {'id': api.league, 'season': season}))
    index = load_index(season)
    if index is None:
        body = api.get('fixtures', {'league': api.league, 'season': season})
        save_index(season, body)
        index = load_index(season)
    items = index.get('response', [])
    if not items:
        st.update({'complete': False, 'note': 'API returned no fixtures for this league/season.', 'checked_at': utc_now()})
        print(f'Season {season}: API returned 0 fixtures. Check APIFOOTBALL_LEAGUE_ID and the season year.', file=sys.stderr)
        return
    details = load_details(season)
    todo = pending_detail_ids(items, details)
    if todo:
        print(f'Season {season}: fetching details for {len(todo)} finished fixtures in batches of 20.')
        fetch_details(api, season, todo, details)
    finished = [i for i in items if fixture_status(i) in FINISHED]
    season_over = bool(items) and all(fixture_status(i) in FINISHED | {'CANC', 'ABD', 'PST'} for i in items)
    if load_meta(season, 'standings') is None or (season_over and not st.get('final_standings')):
        save_meta(season, 'standings', api.get('standings', {'league': api.league, 'season': season}))
        st['final_standings'] = season_over
    with_details = sum(1 for i in finished if (details.get(fixture_id(i)) or {}).get('fixture'))
    st.update({'fixtures': len(items), 'finished': len(finished), 'finished_with_details': with_details,
               'complete': with_details + sum(1 for i in finished if int((details.get(fixture_id(i)) or {}).get('missing_attempts', 0)) >= MAX_MISSING_ATTEMPTS) >= len(finished),
               'saved_at': utc_now()})
    st.pop('note', None)
    print(f'Season {season}: {len(items)} fixtures, {with_details}/{len(finished)} finished fixtures with full details.')


def main(argv=None, api=None):
    parser = argparse.ArgumentParser(description='Resumable API-Football historical backfill')
    parser.add_argument('--seasons', required=True, help='Comma-separated season start years, e.g. 2022,2023,2024,2025,2026')
    args = parser.parse_args(argv)
    seasons = [int(s.strip()) for s in args.seasons.split(',') if s.strip()]
    api = api or Api()
    state = read_json(STATE, None) or {'seasons': {}}
    state.setdefault('seasons', {})
    exit_code = 0
    try:
        for season in seasons:
            try:
                backfill_season(api, season, state)
            except PlanRestricted as exc:
                state['seasons'].setdefault(str(season), {}).update({'complete': False, 'plan_restricted': str(exc), 'checked_at': utc_now()})
                print(f'Season {season}: not available on the current API plan ({exc}).', file=sys.stderr)
            finally:
                write_json(STATE, state)
    except QuotaReached as exc:
        print(f'{exc} Progress saved; rerun the same command after 00:00 UTC.', file=sys.stderr)
    except ApiError as exc:
        print(f'API error: {exc}', file=sys.stderr)
        exit_code = 2
    finally:
        write_json(STATE, state)
        print(f'API requests made in this run: {api.requests_made}; used today: {api.used_today()}/{api.limit}.')
    done = [s for s in seasons if state['seasons'].get(str(s), {}).get('complete')]
    print(f'Complete seasons: {done or "none"}; pending: {[s for s in seasons if s not in done] or "none"}.')
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
