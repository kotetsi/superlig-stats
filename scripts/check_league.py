"""One request: list Turkish leagues with their API-Football ids and available seasons.

Use it once to find the value for the APIFOOTBALL_LEAGUE_ID repository variable.
"""
from apifootball import Api, QuotaReached

if __name__ == '__main__':
    api = Api(require_league=False)
    try:
        body = api.get('leagues', {'country': 'Turkey', 'type': 'league'})
    except QuotaReached as exc:
        raise SystemExit(str(exc))
    for row in body.get('response', []):
        league = row.get('league') or {}
        seasons = [s.get('year') for s in row.get('seasons') or []]
        current = [s.get('year') for s in row.get('seasons') or [] if s.get('current')]
        print(f"id={league.get('id')}\t{league.get('name')}\tseasons={min(seasons) if seasons else '-'}..{max(seasons) if seasons else '-'}\tcurrent={current}")
    print(f'Requests used today: {api.used_today()}/{api.limit}')
