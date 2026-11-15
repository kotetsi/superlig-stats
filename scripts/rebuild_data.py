"""Recalculate every cached season into data/public/season_<year>.json. Makes no API requests."""
import argparse

from apifootball import DATA, RAW, load_details, load_index, load_meta, write_json
from build_data import (aggregate_referees, build_public, legionnaire_rows, results_index, season_players,
                        season_timelines, weekly_delta)
from apifootball import read_json

OUT = DATA / 'public'


def build_legionnaires():
    leg = DATA / 'raw' / 'legionnaires'
    national = read_json(leg / 'national.json')
    dirs = sorted((d for d in leg.glob('*') if d.is_dir() and d.name.isdigit()), key=lambda d: int(d.name)) if leg.exists() else []
    if not national or not dirs:
        return
    season_dir = dirs[-1]
    files = [read_json(f) for f in sorted(season_dir.glob('player_*.json'))]
    rows = legionnaire_rows(files, national['id'])
    snaps = sorted(season_dir.glob('snapshots/*.json'))
    latest = snaps[-1].stem if snaps else None
    prev = None
    if latest:
        from datetime import date
        d0 = date.fromisoformat(latest)
        older = [p for p in snaps if (d0 - date.fromisoformat(p.stem)).days >= 6]
        prev = (older[-1].stem, read_json(older[-1])) if older else None
    for pid, r in rows.items():
        r['week'] = weekly_delta(r['totals'], prev[1].get(pid) if prev else None) if prev else None
    fetched = [((f or {}).get('fetched_at') or '') for f in files]
    out = {'schema_version': 2, 'season': int(season_dir.name), 'national_team_id': national['id'], 'players': rows,
           'checked_players': len(files), 'data_as_of': max(fetched) if fetched else None,
           'week': {'from': prev[0], 'to': latest} if prev else None}
    write_json(OUT / 'legionnaires.json', out, compact=True)
    print(f'Legionnaires: {len(rows)} Turkish players abroad out of {len(files)} checked.')


def main(argv=None):
    p = argparse.ArgumentParser(description='Recalculate all cached seasons without API calls')
    p.add_argument('--seasons', help='Optional comma-separated season years')
    p.add_argument('--allow-empty', action='store_true', help='Exit successfully when nothing has been fetched yet')
    a = p.parse_args(argv)
    wanted = {int(x) for x in a.seasons.split(',') if x.strip()} if a.seasons else None
    made = []
    built = {}
    for path in sorted(RAW.glob('*')) if RAW.exists() else []:
        if not path.is_dir() or not path.name.isdigit():
            continue
        year = int(path.name)
        if wanted and year not in wanted:
            continue
        index = load_index(year)
        if not index or not index.get('response'):
            continue
        standings = load_meta(year, 'standings')
        as_of = max(x for x in (index.get('fetched_at'), (standings or {}).get('fetched_at')) if x)
        obj = build_public(year, index['response'], load_details(year), standings, load_meta(year, 'coverage'), as_of=as_of)
        write_json(OUT / f'season_{year}.json', obj, compact=True)
        # used only to build match pages; not loaded by the browser
        details = load_details(year)
        write_json(DATA / 'build' / f'timeline_{year}.json', season_timelines(index['response'], details), compact=True)
        pl = season_players(index['response'], details)
        pl['season'] = year
        write_json(OUT / f'players_{year}.json', pl, compact=True)
        q = obj['data_quality']
        print(f"Season {year}: {q['matches']} fixtures, {q['finished']} finished, {q['with_details']} with details, "
              f"{q['with_events']} with events, {q['with_statistics']} with statistics, {q['with_referee']} with referee.")
        made.append(year)
        built[year] = obj
    if not made:
        msg = 'No cached season data found under data/raw/. Run the backfill first.'
        if a.allow_empty:
            print(msg)
            return 0
        raise SystemExit(msg)
    # Referee profiles across every cached season (also seasons not rebuilt in this run).
    for f in sorted(OUT.glob('season_*.json')):
        y = int(f.stem.split('_')[1])
        if y not in built:
            import json
            obj = json.loads(f.read_text(encoding='utf-8'))
            if obj.get('schema_version') == 2:
                built[y] = obj
    write_json(OUT / 'results.json', results_index(built), compact=True)
    build_legionnaires()
    refs = aggregate_referees(built)
    write_json(OUT / 'referees.json', refs, compact=True)
    print(f"Referees: {len(refs['referees'])} profiles across seasons {refs['seasons']}.")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
