# 90+ İstatistik — Trendyol Süper Lig

Static Turkish statistics site for the Trendyol Süper Lig. Single data source: **API-Football v3** (api-sports.io).
No scraping, no other sites. Hosting: GitHub Pages, updated by GitHub Actions. Python does every calculation;
the browser reads only generated JSON.

## Flow

```
backfill.py / update.py / legionnaires.py  →  data/raw/         API responses; the only steps that use requests (committed)
rebuild_data.py                            →  data/public/      season, players, referees, results JSON (not committed)
build_model.py                             →  data/public/dixon_coles.json                                   (not committed)
build_site.py                              →  public/           the site, uploaded to GitHub Pages            (not committed)
```

Only `data/raw/`, `data/request_ledger.json` and `data/backfill_state.json` are committed. Everything else is
rebuilt on every run, so the git history grows only with new API data (one gzip file per match, unchanged files
produce identical bytes and no diff).

## One-time setup

1. **Repository.** Create a GitHub repository, upload this project to the default branch.
   GitHub Pages is free for **public** repositories; a private repository needs a paid GitHub plan for Pages.
2. **API key.** Create an API-Football account (dashboard.api-football.com) and copy the key.
3. **Secret** (Settings → Secrets and variables → Actions → *Secrets*): `APIFOOTBALL_KEY` = the key.
   Never put the key in a file, URL, variable or log.
4. **League id.** On your own computer, once (1 request):
   ```bash
   pip install -r requirements.txt
   APIFOOTBALL_KEY=... python scripts/check_league.py
   ```
   It prints every Turkish league with its id and seasons. Use the id of the Süper Lig.
5. **Variables** (same page, *Variables* tab):
   | name | value |
   |---|---|
   | `APIFOOTBALL_LEAGUE_ID` | the id from step 4 |
   | `CURRENT_SEASON` | start year of the running season, e.g. `2026` for 2026–27 (change it every summer) |
   | `SITE_URL` | the Pages address, e.g. `https://OWNER.github.io/REPOSITORY` |
6. **Pages:** Settings → Pages → Source: **GitHub Actions**.
7. **Actions permissions:** Settings → Actions → General → Workflow permissions: **Read and write**.
8. **Historical data:** Actions → *Initial five-season data fill* → Run workflow (default `2022,2023,2024,2025,2026`).
   About 19 requests per season, ~95 in total. If it stops at the daily cap, run it again after 00:00 UTC; it
   continues where it stopped. If a season is not in your API plan, the log says so (`plan_restricted` in
   `data/backfill_state.json`).
9. **First publish:** Actions → *Refresh data and publish* → Run workflow. The site is live at `SITE_URL`.
10. **Turkish players abroad:** Actions → *Turkish players abroad (weekly)* → Run workflow once; it then runs weekly.

## Automatic runs and request budget

| workflow | when | requests |
|---|---|---|
| Refresh data and publish | daily 08:17 and 22:47 UTC, or manually | ~3 per run (fixture list, new results in batches of 20, standings) |
| Turkish players abroad | Tuesdays 06:41 UTC, or manually | squad 1 + 1 per player, max 40 per run |
| Initial data fill | manually | ~19 per season |

* Hard cap: **100 requests per UTC day** across all workflows. Each request is counted in `data/request_ledger.json`
  before it is sent; the run stops when the ledger or the API's `x-ratelimit-requests-remaining` header says the day
  is used up. `API_DAILY_LIMIT` can lower the cap, never raise it. A 6.5 s pause between requests respects the
  per-minute limit; a rate-limit answer is retried once after 61 s.
* All three workflows share one concurrency group: they never run at the same time, so they share the ledger
  safely and never race on `git push`.
* Every workflow runs the offline tests first; a failing test stops the run before any request.
* Notes: GitHub may delay scheduled runs, and disables schedules in public repositories after 60 days without
  repository activity (the data commits count as activity). Change `CURRENT_SEASON` when a new season starts, then
  run the refresh manually once.

## Local use

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -v          # offline, no key, no requests
export APIFOOTBALL_KEY=...  APIFOOTBALL_LEAGUE_ID=...
python scripts/backfill.py --seasons 2022,2023,2024,2025,2026
python scripts/update.py --season 2026
python scripts/legionnaires.py --season 2026
python scripts/rebuild_data.py
python scripts/build_model.py --allow-not-ready
SITE_URL=https://OWNER.github.io/REPO python scripts/build_site.py   # without SITE_URL: noindex, no sitemap
python -m http.server -d public 8000                                  # preview at http://localhost:8000
```

## Data rules

* Missing source data is `null`, never 0. Every rate carries its sample size (`*_n`, `matches`); the site greys out
  rates from fewer than 5 matches.
* Inside a statistics block that the API did return, `null` for count types (corners, cards, shots…) means 0 —
  that is how API-Football writes zero. A missing block means "no data".
* Cards, penalties, first goal and goal minutes come from the event feed; corners, shots, possession from the
  statistics feed. Penalties = awarded (scored + missed); shoot-outs excluded; a second yellow counts as a red.
* For each match the own-goal convention is checked against the final score. If the events do not reproduce the
  score, first goal and goal minutes are withheld for that match (`events_consistent: false`).
* Referees: the country suffix (`, Turkey`) is removed; names are grouped only when they differ in case, Turkish
  letters or punctuation. Possible duplicates (same first initial and surname, e.g. "A. Kardeşler") are shown for
  review, never merged. API spellings are kept in `raw_aliases`.
* Standings: the official API table (Süper Lig tiebreaks, point deductions) when present; otherwise a computed
  table with a visible note.

## Pages

`/` home (standings, position chart, İddaa tables, referees, players, matches, all-season H2H, model) ·
`/takim/<team>/` · `/mac/<id>-<home>-<away>/` · `/h2h/<a>-vs-<b>/` · `/hakem/<referee>/` · `/oyuncu/<name>-<id>/` ·
`/lejyonerler/` · `/model/`. Every page has its own title, description, canonical URL, Open Graph tags and
Schema.org data; `sitemap.xml`, `robots.txt` and `404.html` are generated.

## İddaa statistics

Per team and split (all / home / away) plus a league row: goals Alt/Üst 0.5–4.5, KG Var/Yok, İY/MS (9 combinations),
match corners 7.5–11.5 and team corners 3.5–6.5, match cards 2.5–6.5 and team cards 0.5–3.5, first goal,
goals per 15 minutes. Corner lines use only matches with API statistics, card lines only matches with events.

## Players and Turkish players abroad

* Süper Lig player statistics come from the per-player block of the match responses already fetched: **no extra
  requests**. Squad tables list players who appeared (unused squad members would cost one request per club).
* Turkish players abroad: candidates are the current Turkey national-team squad from the API plus optional ids in
  `config/legionnaire_candidates.json`; a player is listed only if the API gives nationality Turkey and club
  matches outside Turkey (national-team matches excluded). "This week" = difference between two weekly snapshots,
  shown from the second weekly run. The API cannot list all players of a nationality, so players outside the
  national squad must be added to the extra list by id.

## Model

Dixon–Coles (Poisson goals per team with attack/defence strengths, home advantage and the low-score correction ρ),
fitted by maximum likelihood (L-BFGS) on the **last five seasons that have completed matches**, teams identified by
API id, exponential time decay (730-day half-life), ridge penalty pulling teams with little data towards average.

* Forecasts for the newest season; withheld when either team has fewer than 6 training matches, when there are
  fewer than 80 training matches, or when the optimiser does not converge.
* Backtest on the previous season: every week the model is refitted on matches that kicked off **before** that
  week's first match (checked: `no_leakage_check`). Reported: sample size and skipped matches, 1X2 accuracy, Brier
  score, log loss, Üst 2.5 and KG Var Brier/accuracy, a calibration table, and the same scores for a naive baseline
  (training-window frequencies). `beats_baseline` says honestly whether the model is better. Fewer than 100 tested
  matches is flagged as a small sample. Everything is on `/model/`.

## Tests

`python -m unittest discover -s tests -v` — 14 offline tests with a fake API (`tests/fakeapi.py`; its synthetic
data never reaches `data/` or the site): request cap and error handling, resumable backfill across days without
repeated requests, update fetching only changes, storage migration, calculation rules, player and referee
aggregation, Turkish players abroad, model and backtest leakage, site build (pages, slugs, sitemap, robots,
Schema.org, no "None"/"NaN" on any page), JavaScript syntax.

## Not verifiable without a real key

Whether your API plan includes the current season; the API's coverage of events, statistics, referees, player
statistics and xG for the Süper Lig; the exact own-goal convention (handled automatically, see data rules); the real
model accuracy. After the first runs, check `data_quality` in `data/public/season_<y>.json` and the `/model/` page.

## Status

Phases A–E done. Tipsters: waiting for the full description.
