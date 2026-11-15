"""Shared API-Football v3 client and raw-data storage helpers.

Rules enforced here:
- The key is read only from the APIFOOTBALL_KEY environment variable and sent as a header.
  It is never written to disk, URLs or logs.
- A hard cap of at most 100 requests per UTC day, tracked in data/request_ledger.json and
  synchronised with the API's own x-ratelimit-requests-remaining header.
- A minimum interval between requests respects the per-minute limit of the plan.
- Error bodies are never cached.
"""
import gzip
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(os.environ.get('SUPERLIG_ROOT') or Path(__file__).resolve().parents[1])
DATA = ROOT / 'data'
RAW = DATA / 'raw'
BASE_URL = 'https://v3.football.api-sports.io'
HARD_DAILY_CAP = 100
FINISHED = {'FT', 'AET', 'PEN', 'AWD', 'WO'}
BATCH_SIZE = 20  # fixtures?ids= accepts at most 20 ids and returns events, lineups, statistics, players


class QuotaReached(RuntimeError):
    """Daily request budget used up (local ledger or API answer)."""


class PlanRestricted(RuntimeError):
    """The API plan does not include the requested season or endpoint."""


class ApiError(RuntimeError):
    """Any other API error (bad key, bad parameters)."""


def utc_today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def read_json(path: Path, default=None):
    try:
        if path.suffix == '.gz':
            with gzip.open(path, 'rt', encoding='utf-8') as fh:
                return json.load(fh)
        return json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return default
    except (json.JSONDecodeError, OSError, EOFError) as exc:
        raise SystemExit(f'Corrupt data file {path}: {exc}. Restore it from git before continuing.')


def write_json(path: Path, obj, *, compact=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    if path.suffix == '.gz':
        # mtime=0 keeps the gzip bytes stable, so unchanged data does not create git diffs.
        with open(tmp, 'wb') as raw_fh, gzip.GzipFile(fileobj=raw_fh, mode='wb', mtime=0) as gz:
            gz.write(json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8'))
    else:
        text = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':')) if compact else json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True)
        tmp.write_text(text + '\n', encoding='utf-8')
    tmp.replace(path)


class Api:
    def __init__(self, session=None, sleep=time.sleep, clock=time.monotonic, require_league=True):
        self.key = os.environ.get('APIFOOTBALL_KEY', '').strip()
        if not self.key:
            raise SystemExit('APIFOOTBALL_KEY environment variable is required (GitHub Secret APIFOOTBALL_KEY).')
        self.league = os.environ.get('APIFOOTBALL_LEAGUE_ID', '').strip()
        if require_league and not self.league.isdigit():
            raise SystemExit('APIFOOTBALL_LEAGUE_ID must be set to the numeric league id returned by /leagues '
                             '(run scripts/check_league.py). It is never guessed.')
        try:
            requested = int(os.environ.get('API_DAILY_LIMIT', str(HARD_DAILY_CAP)))
        except ValueError:
            requested = HARD_DAILY_CAP
        self.limit = max(1, min(HARD_DAILY_CAP, requested))
        self.min_interval = max(0.0, float(os.environ.get('API_MIN_INTERVAL', '6.5')))
        if session is None:
            import requests
            session = requests.Session()
        self.session = session
        self.session.headers.update({'x-apisports-key': self.key})
        self._sleep = sleep
        self._clock = clock
        self._last_call = None
        self.ledger_path = DATA / 'request_ledger.json'
        self.requests_made = 0

    # ---- ledger -------------------------------------------------------
    def _ledger(self):
        return read_json(self.ledger_path, {}) or {}

    def used_today(self) -> int:
        return int(self._ledger().get(utc_today(), 0))

    def remaining_today(self) -> int:
        return max(0, self.limit - self.used_today())

    def _record(self, count=None):
        ledger = self._ledger()
        today = utc_today()
        ledger[today] = int(ledger.get(today, 0)) + 1 if count is None else int(count)
        # keep the ledger small: last 60 days only
        for day in sorted(ledger)[:-60]:
            ledger.pop(day, None)
        write_json(self.ledger_path, ledger)

    # ---- requests -----------------------------------------------------
    def get(self, endpoint: str, params: dict[str, Any], *, _retry=True) -> dict:
        if self.used_today() >= self.limit:
            raise QuotaReached(f'Daily API request cap reached ({self.used_today()}/{self.limit}, UTC day {utc_today()}).')
        if self._last_call is not None:
            wait = self.min_interval - (self._clock() - self._last_call)
            if wait > 0:
                self._sleep(wait)
        # Count the request before sending it: a request that fails in transit may still be billed.
        self._record()
        self.requests_made += 1
        self._last_call = self._clock()
        response = self.session.get(f'{BASE_URL}/{endpoint}', params=params, timeout=45)
        remaining = response.headers.get('x-ratelimit-requests-remaining') if hasattr(response, 'headers') else None
        if remaining is not None and str(remaining).isdigit() and int(remaining) <= 0:
            self._record(self.limit)  # the API says the day is used up, whatever our ledger says
        if response.status_code == 429:
            if _retry:
                self._sleep(61)
                return self.get(endpoint, params, _retry=False)
            raise QuotaReached('API answered 429 Too Many Requests twice; stopping for today.')
        if response.status_code >= 400:
            raise ApiError(f'HTTP {response.status_code} from {endpoint}')
        body = response.json()
        errors = body.get('errors')
        if errors:
            items = errors if isinstance(errors, dict) else {str(i): e for i, e in enumerate(errors)}
            text = '; '.join(f'{k}: {v}' for k, v in items.items())
            keys = {str(k).lower() for k in items}
            if 'requests' in keys:
                self._record(self.limit)
                raise QuotaReached(f'API daily limit reached: {text}')
            if 'ratelimit' in keys and _retry:
                self._sleep(61)
                return self.get(endpoint, params, _retry=False)
            if 'ratelimit' in keys:
                raise QuotaReached(f'API per-minute limit hit twice: {text}')
            if 'plan' in keys:
                raise PlanRestricted(text)
            raise ApiError(text)
        return body


# ---- raw storage -------------------------------------------------------

def season_dir(season: int) -> Path:
    return RAW / str(season)


def load_index(season):
    d = season_dir(season)
    return read_json(d / 'index.json.gz', None) or read_json(d / 'index.json', None)


def save_index(season, body):
    write_json(season_dir(season) / 'index.json.gz', {'fetched_at': utc_now(), 'response': body.get('response', [])})
    legacy = season_dir(season) / 'index.json'
    if legacy.exists():
        legacy.unlink()


def load_details(season) -> dict:
    """One gzip file per fixture, so a daily refresh only rewrites the fixtures that changed (small git history)."""
    d = season_dir(season)
    out = read_json(d / 'details.json.gz', {}) or {}  # legacy single-file format
    for f in sorted((d / 'details').glob('*.json.gz')) if (d / 'details').exists() else []:
        out[f.name.split('.')[0]] = read_json(f)
    return out


def save_details(season, details: dict, fids=None):
    d = season_dir(season)
    legacy = d / 'details.json.gz'
    if legacy.exists():
        fids = None  # migrate everything once
    for fid in (details if fids is None else fids):
        if fid in details:
            write_json(d / 'details' / f'{fid}.json.gz', details[fid])
    if legacy.exists():
        legacy.unlink()


def load_meta(season, name):
    return read_json(season_dir(season) / f'{name}.json', None)


def save_meta(season, name, body):
    write_json(season_dir(season) / f'{name}.json', {'fetched_at': utc_now(), 'response': body.get('response', [])})


def fixture_id(item) -> str | None:
    fid = ((item or {}).get('fixture') or {}).get('id')
    return str(fid) if fid is not None else None


def fixture_status(item) -> str | None:
    return (((item or {}).get('fixture') or {}).get('status') or {}).get('short')


def fetch_details(api: Api, season: int, ids: list[str], details: dict, on_batch=None) -> int:
    """Fetch full fixture objects (with events/statistics/lineups/players) in batches of 20.

    Saves after every batch so an interrupted run loses nothing. Returns number of fixtures stored.
    """
    stored = 0
    for start in range(0, len(ids), BATCH_SIZE):
        chunk = ids[start:start + BATCH_SIZE]
        body = api.get('fixtures', {'ids': '-'.join(chunk)})
        got = {}
        for item in body.get('response', []):
            fid = fixture_id(item)
            if fid:
                got[fid] = item
        now = utc_now()
        for fid in chunk:
            if fid in got:
                details[fid] = {'fetched_at': now, 'fixture': got[fid]}
                stored += 1
            else:
                # Remember the miss so the same id is not requested forever.
                prev = details.get(fid) or {}
                details[fid] = {**prev, 'missing_attempts': int(prev.get('missing_attempts', 0)) + 1, 'last_attempt': now}
        save_details(season, details, chunk)
        if on_batch:
            on_batch(len(chunk))
    return stored
