"""Build the static GitHub Pages site into public/ from data/public/*.json. Makes no API requests.

Stable URLs (Turkish, ASCII slugs):
  /takim/<team>/            team, latest season + history
  /hakem/<referee>/         referee, all seasons
  /mac/<id>-<home>-<away>/  match, every season
  /h2h/<team-a>-vs-<team-b>/  head to head, all seasons (teams in alphabetical slug order)
Slug collisions get the API id appended. Nothing on a page is invented: missing values print as "—".
"""
import html
import json
import os
import re
import shutil
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from apifootball import DATA, ROOT, write_json

SITE = ROOT / 'site'
PUB = Path(os.environ.get('SITE_OUT') or ROOT / 'public')
SRC = DATA / 'public'
BUILD = DATA / 'build'
BASE = os.environ.get('SITE_URL', '').strip().rstrip('/')
SITE_NAME = '90+ İstatistik'
STATUS_TR = {'FT': 'Maç sonu', 'AET': 'Uzatmalar sonu', 'PEN': 'Penaltılar sonu', 'NS': 'Başlamadı', 'TBD': 'Saat belli değil',
             'PST': 'Ertelendi', 'CANC': 'İptal', 'ABD': 'Yarıda kaldı', 'AWD': 'Hükmen', 'WO': 'Hükmen', 'SUSP': 'Askıda'}
EVENT_STATUS = {'PST': 'https://schema.org/EventPostponed', 'CANC': 'https://schema.org/EventCancelled', 'ABD': 'https://schema.org/EventCancelled'}
POS = {'G': 'Kaleci', 'D': 'Defans', 'M': 'Orta saha', 'F': 'Forvet'}
BUCKETS = ['1-15', '16-30', '31-45+', '46-60', '61-75', '76-90+']


def esc(v):
    return html.escape('—' if v is None or v == '' else str(v))


def r1(v):
    return None if v is None else f'{v:.1f}'


def r2(v):
    return None if v is None else f'{v:.2f}'


def pct(v):
    return '—' if v is None else f'{round(v * 100)}%'


def url(path):
    return f'{BASE}/{path}' if BASE else None


def page(title, desc, path, body, schemas, crumbs=(), depth=2, h1=None, root=None):
    root = root if root is not None else '../' * depth
    full = url(path)
    head = [f'<link rel="canonical" href="{esc(full)}">', f'<meta property="og:url" content="{esc(full)}">'] if BASE else []
    robots = 'index,follow' if BASE else 'noindex'
    graph = list(schemas)
    if crumbs and BASE:
        items = [{'@type': 'ListItem', 'position': 1, 'name': 'Ana sayfa', 'item': BASE + '/'}]
        items += [{'@type': 'ListItem', 'position': i + 2, 'name': n, **({'item': url(p)} if p else {})} for i, (n, p) in enumerate(crumbs)]
        graph.append({'@type': 'BreadcrumbList', 'itemListElement': items})
    schema_tag = json.dumps({'@context': 'https://schema.org', '@graph': graph}, ensure_ascii=False).replace('</', '<\\/')
    nav = ''.join(f' <span>›</span> <a href="{root}{p}">{esc(n)}</a>' if p else f' <span>›</span> <span>{esc(n)}</span>' for n, p in crumbs)
    return (f'<!doctype html><html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{esc(title)} | {SITE_NAME}</title><meta name="description" content="{esc(desc)}"><meta name="robots" content="{robots}">'
            f'<meta name="theme-color" content="#071b2d"><meta property="og:type" content="website"><meta property="og:locale" content="tr_TR">'
            f'<meta property="og:site_name" content="{SITE_NAME}"><meta property="og:title" content="{esc(title)}"><meta property="og:description" content="{esc(desc)}">'
            f'{"".join(head)}<link rel="stylesheet" href="{root}assets/style.css"><script type="application/ld+json">{schema_tag}</script></head><body>'
            f'<header class="top"><a class="brand" href="{root}"><span class="brand-mark">90+</span><span>İSTATİSTİK<small>TRENDYOL SÜPER LİG</small></span></a>'
            f'<nav class="crumbs" aria-label="Sayfa yolu"><a href="{root}">Ana sayfa</a>{nav}</nav></header><main>'
            f'<section class="hero hero-sm"><div class="eyebrow">TRENDYOL SÜPER LİG · İSTATİSTİK</div><h1>{esc(h1 or title)}</h1><p>{esc(desc)}</p></section>'
            f'{body}</main><footer><span>{SITE_NAME}</span><span>Veri kaynağı: API-Football v3 · Canlı skor değildir</span></footer></body></html>')


def box(title, rows, note=None):
    head = f'<div class="section-head"><h2>{esc(title)}</h2></div>' if title else ''
    n = f'<p class="muted">{esc(note)}</p>' if note else ''
    return (f'<section class="panel">{head}{n}<div class="table-wrap"><table class="kv"><tbody>'
            + ''.join(f'<tr><th>{esc(k)}</th><td>{v if isinstance(v, Raw) else esc(v)}</td></tr>' for k, v in rows) + '</tbody></table></div></section>')


class Raw(str):
    """Already-escaped HTML for box() values."""


def grid(title, headers, rows, note=None, cls=''):
    n = f'<p class="muted">{esc(note)}</p>' if note else ''
    body = ''.join('<tr>' + ''.join(f'<td>{c}</td>' for c in r) + '</tr>' for r in rows) or f'<tr><td colspan="{len(headers)}" class="empty">Veri yok.</td></tr>'
    return (f'<section class="panel {cls}"><div class="section-head"><h2>{esc(title)}</h2></div>{n}<div class="table-wrap"><table><thead><tr>'
            + ''.join(f'<th>{html.escape(str(h))}</th>' for h in headers) + f'</tr></thead><tbody>{body}</tbody></table></div></section>')


def form_html(items):
    lab = {'W': 'G', 'D': 'B', 'L': 'M'}
    return Raw('<span class="form">' + ''.join(f'<i class="{x["r"]}">{lab[x["r"]]}</i>' for x in items) + '</span>') if items else None


def position_svg(series, n_teams, label):
    if not series:
        return '<p class="muted">Hafta bazlı sıralama verisi yok.</p>'
    W, H, pad = max(540, len(series) * 26), 220, 28
    def xy(i, p):
        return pad + i * (W - 2 * pad) / max(1, len(series) - 1), pad + (p - 1) * (H - 2 * pad) / max(1, n_teams - 1)
    pts = ' '.join('%.1f,%.1f' % xy(i, p['position']) for i, p in enumerate(series))
    dots = ''.join('<circle cx="%.1f" cy="%.1f" r="3"><title>%s. hafta: %s. sıra, %s puan</title></circle>' % (*xy(i, p['position']), p['round'], p['position'], p['points']) for i, p in enumerate(series))
    return (f'<div class="chart-wrap"><svg viewBox="0 0 {W} {H}" role="img" aria-label="{esc(label)}"><line x1="{pad}" y1="{pad}" x2="{W - pad}" y2="{pad}"/>'
            f'<line x1="{pad}" y1="{H - pad}" x2="{W - pad}" y2="{H - pad}"/><polyline points="{pts}"/>{dots}</svg></div>'
            '<p class="muted">1. sıra üstte. Hafta numarasına göre, o haftaya kadar oynanmış maçlardan hesaplanır.</p>')


def unique_slugs(items):
    count = defaultdict(int)
    for _, s in items:
        count[s] += 1
    return {k: (s if count[s] == 1 else f'{s}-{re.sub(r"[^a-z0-9]+", "-", str(k).lower())}') for k, s in items}


def load_json(path, default=None):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


def load_seasons():
    out = {}
    for f in sorted(SRC.glob('season_*.json')):
        obj = json.loads(f.read_text(encoding='utf-8'))
        if obj.get('schema_version') == 2:
            out[int(obj['season'])] = obj
    return out


def write_page(path, text):
    target = PUB / path / 'index.html'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding='utf-8')


def date_tr(iso):
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace('Z', '+00:00')).astimezone(timezone.utc).strftime('%d.%m.%Y')
    except ValueError:
        return iso[:10]


def score(m):
    return f"{m['home_goals']} – {m['away_goals']}" if m.get('finished') else None


def h2h_path(slug_a, slug_b):
    a, b = sorted([slug_a, slug_b])
    return f'h2h/{a}-vs-{b}/'


def outcome_for(m, tid):
    own, opp = (m['home_goals'], m['away_goals']) if str(m['home_id']) == str(tid) else (m['away_goals'], m['home_goals'])
    return 'W' if own > opp else 'D' if own == opp else 'L'


def record(rows, tid):
    """rows: compact results [id, slug, season, ts, home_id, away_id, hg, ag, hth, hta]. Record from tid's perspective."""
    w = d = l = gf = ga = 0
    for r in rows:
        own, opp = (r[6], r[7]) if r[4] == tid else (r[7], r[6])
        gf += own
        ga += opp
        w += own > opp
        d += own == opp
        l += own < opp
    return {'played': len(rows), 'wins': w, 'draws': d, 'losses': l, 'gf': gf, 'ga': ga}


def main():
    if PUB.exists():
        shutil.rmtree(PUB)
    shutil.copytree(SITE, PUB)
    seasons = load_seasons()
    years = sorted(seasons)
    model = load_json(SRC / 'dixon_coles.json')
    refs = (load_json(SRC / 'referees.json', {}) or {}).get('referees') or {}
    results = load_json(SRC / 'results.json', {'rows': [], 'teams': {}})
    players = {y: load_json(SRC / f'players_{y}.json', {}) or {} for y in years}
    legion = load_json(SRC / 'legionnaires.json')

    # ---- slugs, globally unique and identical in JSON and pages
    team_latest = {}
    for y in years:
        for tid, t in seasons[y]['teams'].items():
            team_latest[tid] = (y, t)
    team_slug = unique_slugs([(tid, t['slug']) for tid, (_, t) in sorted(team_latest.items())])
    ref_slug = unique_slugs([(k, r['slug']) for k, r in sorted(refs.items())])
    for k, r in refs.items():
        r['slug'] = ref_slug[k]
    for y in years:
        for tid, t in seasons[y]['teams'].items():
            t['slug'] = team_slug[tid]
        for key, r in seasons[y]['referees'].items():
            r['slug'] = ref_slug.get(key, r['slug'])
    for tid, t in results.get('teams', {}).items():
        t['slug'] = team_slug.get(tid, t['slug'])
    names = {tid: t['name'] for tid, (_, t) in team_latest.items()}
    ref_of_match = {}
    for key, r in refs.items():
        for m in r['matches']:
            ref_of_match[m['id']] = r
    rows_all = results.get('rows', [])
    pair_rows = defaultdict(list)
    for r in rows_all:
        pair_rows[frozenset((r[4], r[5]))].append(r)

    # ---- index.html
    idx = (PUB / 'index.html').read_text(encoding='utf-8')
    if BASE:
        idx = idx.replace('<!--CANONICAL-->', f'<link rel="canonical" href="{esc(BASE + "/")}"><meta property="og:url" content="{esc(BASE + "/")}">').replace('"url":""', json.dumps({'url': BASE + '/'})[1:-1])
    else:
        idx = idx.replace('<!--CANONICAL-->', '').replace('content="index,follow"', 'content="noindex"')
    (PUB / 'index.html').write_text(idx, encoding='utf-8')

    # ---- data for the browser
    out_dir = PUB / 'data' / 'public'
    out_dir.mkdir(parents=True, exist_ok=True)
    for y in years:
        write_json(out_dir / f'season_{y}.json', seasons[y], compact=True)
    if model:
        write_json(out_dir / 'dixon_coles.json', model, compact=True)
    if refs:
        write_json(out_dir / 'referees.json', {'schema_version': 2, 'referees': refs}, compact=True)
    write_json(out_dir / 'results.json', results, compact=True)
    for y in years:
        if players[y].get('players'):
            write_json(out_dir / f'players_{y}.json', players[y], compact=True)
    if legion:
        write_json(out_dir / 'legionnaires.json', legion, compact=True)
    write_json(PUB / 'data' / 'index.json', {
        'schema_version': 2, 'latest_season': years[-1] if years else None, 'model': bool(model), 'referees': bool(refs), 'results': bool(rows_all),
        'players': [y for y in years if players[y].get('players')], 'legionnaires': bool(legion and legion.get('players')),
        'seasons': {str(y): {'label': seasons[y]['season_label'], 'matches': seasons[y]['match_count'], 'completed': seasons[y]['completed_match_count'],
                             'generated_at': seasons[y]['generated_at']} for y in years}})

    urls = []
    forecasts = (model or {}).get('fixtures') or {}
    model_season = (model or {}).get('season')
    tlabel = lambda tid: names.get(str(tid))
    tlink = lambda tid, up='../../': f'<a href="{up}takim/{esc(team_slug[str(tid)])}/">{esc(names[str(tid)])}</a>' if str(tid) in team_slug else esc(tlabel(tid))
    mlink = lambda slug, text, up='../../': f'<a href="{up}mac/{esc(slug)}/">{esc(text)}</a>'

    # ---- team pages
    for tid, (y, t) in sorted(team_latest.items()):
        s = seasons[y]
        path = f'takim/{t["slug"]}/'
        st = next((r for r in s['standings']['rows'] if str(r['team_id']) == tid), {})
        rank = st.get('rank')
        n_teams = len(s['teams'])
        body = box(f'{s["season_label"]} sezonu özeti', [
            ('Sıra', f'{rank} / {n_teams}' if rank else None), ('Puan', st.get('points', t['table']['points'])),
            ('Son 5 maç', form_html(t['form5'])), ('Son 10 maç', form_html(t['form10'])),
            ('Son 10 maçta puan', sum(3 if f['r'] == 'W' else 1 if f['r'] == 'D' else 0 for f in t['form10']) if t['form10'] else None),
            ('Durum', st.get('description'))])
        split_rows = []
        for label, k in (('Genel', 'table'), ('İç saha', 'home'), ('Deplasman', 'away')):
            r = t[k]
            split_rows.append([esc(label), esc(r['played']), esc(r['wins']), esc(r['draws']), esc(r['losses']), esc(r['gf']), esc(r['ga']), esc(r['gd']), esc(r['points']),
                               esc(r2(r['points'] / r['played']) if r['played'] else None)])
        body += grid('İç saha / deplasman', ['', 'O', 'G', 'B', 'M', 'AG', 'YG', 'AV', 'P', 'P / maç'], split_rows)
        body += f'<section class="panel"><div class="section-head"><h2>Haftalara göre sıralama</h2></div>{position_svg(s["table_by_round"].get(tid, []), n_teams, t["name"] + " sıralama grafiği")}</section>'
        mk = t['markets']
        lines = [('Maç', 'matches', None), ('Üst 1.5', 'over_1.5', pct), ('Üst 2.5', 'over_2.5', pct), ('Üst 3.5', 'over_3.5', pct), ('KG Var', 'btts_yes', pct),
                 ('Gol attı', 'scored', pct), ('Gol yemedi', 'clean_sheet', pct), ('Attığı gol ort.', 'goals_for_avg', r2), ('Yediği gol ort.', 'goals_against_avg', r2),
                 ('İlk golü attı', 'first_goal_for', pct), ('Maç korner ort.', 'corners_total_avg', r1), ('Takım korner ort.', 'corners_for_avg', r1),
                 ('Üst 9.5 korner', 'corners_over_9.5', pct), ('Maç kart ort.', 'cards_total_avg', r1), ('Takım kart ort.', 'cards_for_avg', r1), ('Üst 4.5 kart', 'cards_over_4.5', pct)]
        mrows = [[esc(lab)] + [esc(f(mk[sp].get(key)) if f else mk[sp].get(key)) for sp in ('all', 'home', 'away')] for lab, key, f in lines]
        body += grid('İddaa istatistikleri', ['', 'Tümü', 'İç saha', 'Deplasman'], mrows,
                     note=f"Korner {mk['all']['corners_n']} maçtan, kart {mk['all']['cards_n']} maçtan, ilk gol {mk['all']['first_goal_n']} maçtan hesaplandı.")
        b = mk['all']
        if b.get('goals_for_by_15'):
            n = b['goal_minutes_n']
            body += grid('Gol dakikaları (maç başına)', ['Dakika', 'Atılan', 'Yenilen'],
                         [[esc(BUCKETS[i] + "'"), esc(r2(b['goals_for_by_15'][i] / n)), esc(r2(b['goals_against_by_15'][i] / n))] for i in range(6)], note=f'{n} maçtan.')
        team_ms = [m for m in s['matches'] if tid in (str(m['home_id']), str(m['away_id']))]
        upcoming = [m for m in team_ms if not m['finished'] and m['status'] in ('NS', 'TBD')][:5]
        if upcoming:
            urows = []
            for m in upcoming:
                f = forecasts.get(str(m['id'])) if y == model_season else None
                urows.append([esc(date_tr(m['date'])), esc(m['round_no']), mlink(m['slug'], f"{m['home']} – {m['away']}"),
                              esc(f"{pct(f['home_win'])} / {pct(f['draw'])} / {pct(f['away_win'])}" if f else None)])
            body += grid('Sıradaki maçlar', ['Tarih', 'Hafta', 'Maç', 'Model 1 / X / 2'], urows)
        recent = [m for m in team_ms if m['finished']][-10:]
        body += grid('Son maçlar', ['Tarih', 'Maç', 'Skor', 'Sonuç'],
                     [[esc(date_tr(m['date'])), mlink(m['slug'], f"{m['home']} – {m['away']}"), esc(score(m)),
                       str(form_html([{'r': outcome_for(m, tid)}]))] for m in reversed(recent)])
        pls = [p for p in (players[y].get('players') or {}).values() if tid in (p.get('by_team') or {})]
        if pls:
            cov = (players[y].get('team_coverage') or {}).get(tid)
            pls.sort(key=lambda p: (-p['by_team'][tid]['minutes'], p['name']))
            prow = []
            for p_ in pls:
                bt = p_['by_team'][tid]
                prow.append([f'<a href="../../oyuncu/{esc(p_["slug"])}/">{esc(p_["name"])}</a>', esc(POS.get(p_.get('position'), p_.get('position'))), esc(bt['apps']),
                             esc(bt['minutes']), esc(bt['goals']), esc(bt['assists']), esc(bt['yellow']), esc(bt['red'])])
            body += grid(f'Kadro: forma giyen oyuncular ({s["season_label"]})', ['Oyuncu', 'Mevki', 'Maç', 'Dakika', 'Gol', 'Asist', 'Sarı', 'Kırmızı'], prow,
                         note=f'Oyuncu istatistiği olan {cov} maçtan. Forma giymemiş kadro oyuncuları listelenmez.' if cov else None)
        # opponents across all seasons
        opp = []
        for other in sorted({str(r[5] if str(r[4]) == tid else r[4]) for r in rows_all if tid in (str(r[4]), str(r[5]))}, key=lambda o: names.get(o, '')):
            rr = pair_rows[frozenset((int(tid), int(other)))]
            rec = record(rr, int(tid))
            link = f'<a href="../../{h2h_path(t["slug"], team_slug[other])}">{esc(names.get(other))}</a>' if other in team_slug else esc(names.get(other))
            opp.append([link, esc(rec['played']), esc(rec['wins']), esc(rec['draws']), esc(rec['losses']), esc(f"{rec['gf']}:{rec['ga']}")])
        body += grid('Rakiplere karşı (tüm sezonlar)', ['Rakip', 'Maç', 'G', 'B', 'M', 'Gol'], opp)
        ref_rows = defaultdict(list)
        for m in team_ms:
            if m['finished'] and m['referee_key']:
                ref_rows[m['referee_key']].append(m)
        rr_out = []
        for key, ms in sorted(ref_rows.items(), key=lambda kv: -len(kv[1])):
            res = {'W': 0, 'D': 0, 'L': 0}
            for m in ms:
                own, oth = (m['home_goals'], m['away_goals']) if str(m['home_id']) == tid else (m['away_goals'], m['home_goals'])
                res['W' if own > oth else 'D' if own == oth else 'L'] += 1
            r = refs.get(key)
            name = f'<a href="../../hakem/{esc(r["slug"])}/">{esc(r["name"])}</a>' if r else esc(ms[0]['referee'])
            rr_out.append([name, esc(len(ms)), esc(res['W']), esc(res['D']), esc(res['L'])])
        body += grid(f'Hakemler ({s["season_label"]})', ['Hakem', 'Maç', 'G', 'B', 'M'], rr_out)
        history = []
        for yy in years:
            tt = seasons[yy]['teams'].get(tid)
            if tt:
                hr = next((r for r in seasons[yy]['standings']['rows'] if str(r['team_id']) == tid), {})
                history.append([esc(seasons[yy]['season_label']), esc(hr.get('rank')), esc(tt['table']['played']), esc(tt['table']['wins']), esc(tt['table']['draws']),
                                esc(tt['table']['losses']), esc(f"{tt['table']['gf']}:{tt['table']['ga']}"), esc(hr.get('points', tt['table']['points']))])
        body += grid('Sezonlar', ['Sezon', 'Sıra', 'O', 'G', 'B', 'M', 'Gol', 'P'], history)
        schema = {'@type': 'SportsTeam', 'name': t['name'], 'sport': 'Futbol', 'memberOf': {'@type': 'SportsOrganization', 'name': 'Trendyol Süper Lig'}}
        if BASE:
            schema['url'] = url(path)
        desc = (f'{t["name"]} {s["season_label"]} Trendyol Süper Lig istatistikleri: ' + (f'{rank}. sıra, ' if rank else '') +
                f'{t["table"]["points"]} puan, iç saha/deplasman, form, Alt/Üst, KG Var, korner ve kart oranları.')
        write_page(path, page(f'{t["name"]} istatistikleri', desc, path, body, [schema], crumbs=[(t['name'], None)]))
        urls.append((path, s['generated_at']))

    # ---- referee pages (all seasons, from referees.json)
    lbl = lambda y: seasons[int(y)]['season_label'] if int(y) in seasons else str(y)
    for key, r in sorted(refs.items()):
        path = f'hakem/{r["slug"]}/'
        t = r['totals']
        body = box('Kariyer özeti (Süper Lig, mevcut sezonlar)', [
            ('Yönettiği maç', t['matches']), ('Olay verisi olan maç', t['events_n']),
            ('Sarı kart / maç', r2(t['yellow_per_match'])), ('Kırmızı kart / maç', r2(t['red_per_match'])),
            ('Toplam kart / maç', r2(t['cards_per_match'])), ('Ev sahibi / deplasman kartı', f"{r2(t['home_cards_per_match']) or '—'} / {r2(t['away_cards_per_match']) or '—'}"),
            ('Penaltı / maç', r2(t['penalties_per_match'])), ('Gol / maç', r2(t['goals_per_match'])),
            ('Üst 3.5 / 4.5 / 5.5 kart', f"{pct(t['cards_over_3.5'])} / {pct(t['cards_over_4.5'])} / {pct(t['cards_over_5.5'])}"),
            ('1 / X / 2', f"{pct(t['home_win_rate'])} / {pct(t['draw_rate'])} / {pct(t['away_win_rate'])}"),
            ('İlk / son maç', f"{date_tr(r['first_match']) or '—'} / {date_tr(r['last_match']) or '—'}")])
        body += grid('Sezonlara göre', ['Sezon', 'Maç', 'Sarı / maç', 'Kırmızı / maç', 'Kart / maç', 'Penaltı / maç', 'Ev sahibi galibiyeti'],
                     [[esc(lbl(y)), esc(v['matches']), esc(r2(v['yellow_per_match'])), esc(r2(v['red_per_match'])), esc(r2(v['cards_per_match'])),
                       esc(r2(v['penalties_per_match'])), esc(pct(v['home_win_rate']))] for y, v in sorted(r['seasons'].items(), reverse=True)],
                     note='Kart ve penaltı oranları yalnızca olay verisi olan maçlardan hesaplanır.')
        body += grid('Büyük dört takım bu hakemle', ['Takım', 'Maç', 'G', 'B', 'M', 'Galibiyet', 'Kart / maç (takım – rakip)', 'Penaltı (lehine – aleyhine)'],
                     [[tlink(b['team_id']) if str(b['team_id']) in team_slug else esc(n), esc(b['played']), esc(b['wins']), esc(b['draws']), esc(b['losses']), esc(pct(b['win_rate'])),
                       esc(f"{r1(b['cards_for_avg'])} – {r1(b['cards_against_avg'])}" if b['cards_for_avg'] is not None else None),
                       esc(f"{b['penalties_for']} – {b['penalties_against']}" if b['penalties_for'] is not None else None)] for n, b in r['big_four'].items()])
        body += grid('Yönettiği maçlar', ['Sezon', 'Tarih', 'Maç', 'Skor', 'Sarı', 'Kırmızı', 'Penaltı'],
                     [[esc(lbl(m['season'])), esc(date_tr(m['date'])), mlink(m['slug'], f"{m['home']} – {m['away']}"),
                       esc(f"{m['home_goals']}-{m['away_goals']}"), esc(m['yellow']), esc(m['red']), esc(m['penalties'])] for m in r['matches']])
        info = [('API yazımları', ', '.join(r['raw_aliases']) or None), ('Gruplanan yazımlar', ', '.join(a['name'] for a in r['aliases']))]
        if r.get('possible_duplicates'):
            info.append(('Aynı kişi olabilir (birleştirilmedi)', Raw(', '.join(f'<a href="../{esc(refs[d["key"]]["slug"])}/">{esc(d["name"])}</a>' for d in r['possible_duplicates'] if d['key'] in refs))))
        body += box('İsim kontrolü', info)
        last = seasons.get(max(int(y) for y in r['seasons']))
        schema = {'@type': 'Person', 'name': r['name'], 'jobTitle': 'Futbol hakemi'}
        if BASE:
            schema['url'] = url(path)
        write_page(path, page(f'{r["name"]} hakem istatistikleri', f'{r["name"]} Süper Lig maçları: {t["matches"]} maç, kart ve penaltı ortalamaları, ev sahibi galibiyet oranı ve büyük takımlarla sonuçlar.',
                              path, body, [schema], crumbs=[('Hakemler', None), (r['name'], None)]))
        urls.append((path, (last or {}).get('generated_at')))

    # ---- match pages (all seasons)
    for y in years:
        s = seasons[y]
        tl_all = load_json(BUILD / f'timeline_{y}.json', {}) or {}
        for m in s['matches']:
            path = f'mac/{m["slug"]}/'
            hid, aid = str(m['home_id']), str(m['away_id'])
            ref = ref_of_match.get(m['id'])
            fg = {'home': m['home'], 'away': m['away'], 'none': 'Gol yok'}.get(m['first_goal']) if m['first_goal'] else None
            rows = [('Sezon / hafta', f"{s['season_label']} · {m['round_no']}. hafta" if m['round_no'] else s['season_label']),
                    ('Tarih (UTC)', (m['date'] or '').replace('T', ' ')[:16]), ('Durum', STATUS_TR.get(m['status'], m['status'])),
                    ('Ev sahibi', Raw(tlink(hid))), ('Deplasman', Raw(tlink(aid))), ('Skor', score(m)),
                    ('İlk yarı', f"{m['ht_home']} – {m['ht_away']}" if m['ht_home'] is not None else None), ('İlk gol', fg),
                    ('Hakem', Raw(f'<a href="../../hakem/{esc(ref["slug"])}/">{esc(ref["name"])}</a>') if ref else m['referee']), ('Stat', m['venue'])]
            body = box(f"{m['home']} – {m['away']}", rows)
            tl = tl_all.get(str(m['id']))
            if tl:
                icon = {'goal': '⚽', 'pen': '⚽ (P)', 'own': '⚽ (KK)', 'mpen': '✗ Penaltı kaçtı', 'y': '🟨', 'r': '🟥', 'var': 'VAR'}
                trows = []
                for e in tl:
                    minute = f"{e['m']}+{e['x']}'" if e['x'] else f"{e['m']}'"
                    txt = f"{icon[e['k']]} {e['p'] or ''}" + (f" ({e['a']})" if e.get('a') else '') + (f" — {e['d']}" if e.get('d') else '')
                    trows.append([esc(txt.strip()) if e['s'] == 'home' else '', esc(minute), esc(txt.strip()) if e['s'] == 'away' else ''])
                body += grid('Maç olayları', [m['home'], 'Dk.', m['away']], trows, note='KK = kendi kalesine. Kaynak: API maç olayları.', cls='timeline')
            if m['statistics_available'] or m['events_available']:
                srows = []
                for label, field, fmt in (('Topa sahip olma', 'possession', lambda v: f'%{v:.0f}'), ('Şut', 'shots', lambda v: f'{v:.0f}'), ('İsabetli şut', 'shots_on_target', lambda v: f'{v:.0f}'),
                                          ('Korner', 'corners', lambda v: f'{v:.0f}'), ('Faul', 'fouls', lambda v: f'{v:.0f}'), ('Gol beklentisi (xG)', 'xg', lambda v: f'{v:.2f}'),
                                          ('Sarı kart', 'yellow', str), ('Kırmızı kart', 'red', str), ('Penaltı', 'penalties', str)):
                    a, b = m.get(f'home_{field}'), m.get(f'away_{field}')
                    if a is None and b is None:
                        continue
                    srows.append([esc(fmt(a) if a is not None else None), esc(label), esc(fmt(b) if b is not None else None)])
                body += grid('Maç istatistikleri', [m['home'], '', m['away']], srows, cls='compare')
            # context: both teams this season and previous meetings
            th, ta = s['teams'].get(hid), s['teams'].get(aid)
            if th and ta:
                mh, ma = th['markets']['home'], ta['markets']['away']
                crow = lambda lab, v1, v2: [esc(v1), esc(lab), esc(v2)]
                ctx = [crow('Sıra', next((r['rank'] for r in s['standings']['rows'] if str(r['team_id']) == hid), None), next((r['rank'] for r in s['standings']['rows'] if str(r['team_id']) == aid), None)),
                       [str(form_html(th['form5']) or '—'), esc('Son 5 maç'), str(form_html(ta['form5']) or '—')],
                       crow('İç saha / deplasman puanı', th['home']['points'], ta['away']['points']),
                       crow('Üst 2.5 (iç saha / deplasman)', pct(mh.get('over_2.5')), pct(ma.get('over_2.5'))),
                       crow('KG Var (iç saha / deplasman)', pct(mh.get('btts_yes')), pct(ma.get('btts_yes'))),
                       crow('Maç korner ort.', r1(mh.get('corners_total_avg')), r1(ma.get('corners_total_avg'))),
                       crow('Maç kart ort.', r1(mh.get('cards_total_avg')), r1(ma.get('cards_total_avg')))]
                body += grid(f'Takımların sezon profili ({s["season_label"]}, güncel)', [m['home'], '', m['away']], ctx, cls='compare',
                             note='Ev sahibinin iç saha, deplasman takımının deplasman maçlarından; sayfanın oluşturulduğu tarihteki veriler.')
            prev = [r for r in pair_rows[frozenset((m['home_id'], m['away_id']))] if r[0] != m['id'] and (r[3] or 0) < (m['timestamp'] or 0)][-6:]
            if prev:
                body += grid('Önceki karşılaşmalar', ['Sezon', 'Tarih', 'Maç', 'Skor'],
                             [[esc(seasons[r[2]]['season_label'] if r[2] in seasons else r[2]), esc(datetime.fromtimestamp(r[3], timezone.utc).strftime('%d.%m.%Y') if r[3] else None),
                               mlink(r[1], f"{tlabel(r[4])} – {tlabel(r[5])}"), esc(f'{r[6]} – {r[7]}')] for r in reversed(prev)])
                if hid in team_slug and aid in team_slug:
                    body += f'<p class="more"><a href="../../{h2h_path(team_slug[hid], team_slug[aid])}">Tüm {esc(m["home"])} – {esc(m["away"])} karşılaşmaları →</a></p>'
            f = forecasts.get(str(m['id'])) if y == model_season else None
            if f and not m['finished']:
                body += box('Model olasılıkları (Dixon–Coles)', [('1 / X / 2', f"{pct(f['home_win'])} / {pct(f['draw'])} / {pct(f['away_win'])}"),
                                                               ('Üst 2.5', pct(f['over_2_5'])), ('KG Var', pct(f['btts_yes'])),
                                                               ('Beklenen gol', f"{r2(f['expected_home_goals'])} – {r2(f['expected_away_goals'])}"),
                                                               ('En olası skorlar', ', '.join(f"{x['score']} ({pct(x['probability'])})" for x in f['top_scores'][:3]))],
                            note='Olasılık tahminidir, kesin sonuç değildir.')
            if m['finished'] and not m['details_available']:
                body += '<p class="notice">Bu maçın olay ve istatistik verisi API\'den henüz alınmadı.</p>'
            title = f"{m['home']} – {m['away']}"
            when = date_tr(m['date'])
            desc = (f"{title} ({when}), {s['season_label']} Trendyol Süper Lig " + (f"{m['round_no']}. hafta" if m['round_no'] else '') +
                    (f": {m['home_goals']}-{m['away_goals']}. Goller, kartlar, istatistikler ve önceki karşılaşmalar." if m['finished'] else ': maç öncesi istatistikler, form ve önceki karşılaşmalar.'))
            schema = {'@type': 'SportsEvent', 'name': title, 'sport': 'Futbol', 'startDate': m['date'],
                      'eventStatus': EVENT_STATUS.get(m['status'], 'https://schema.org/EventScheduled'),
                      'homeTeam': {'@type': 'SportsTeam', 'name': m['home']}, 'awayTeam': {'@type': 'SportsTeam', 'name': m['away']},
                      'superEvent': {'@type': 'SportsEvent', 'name': f"Trendyol Süper Lig {s['season_label']}"}}
            if m['venue']:
                schema['location'] = {'@type': 'Place', 'name': m['venue']}
            if BASE:
                schema['url'] = url(path)
            write_page(path, page(f"{title} {('maç sonucu ve istatistikleri' if m['finished'] else 'maç önü istatistikler')}", desc, path, body, [schema],
                                  crumbs=[(m['home'], f'takim/{team_slug[hid]}/' if hid in team_slug else None), (title, None)], h1=title))
            urls.append((path, s['generated_at']))

    # ---- head-to-head pages (every pair that has met)
    for pair, rr in sorted(pair_rows.items(), key=lambda kv: sorted(str(x) for x in kv[0])):
        ids = sorted((str(x) for x in pair), key=lambda x: team_slug.get(x, x))
        if len(ids) != 2 or any(i not in team_slug for i in ids):
            continue
        a, b = ids
        path = h2h_path(team_slug[a], team_slug[b])
        ra, rb = record(rr, int(a)), record(rr, int(b))
        n = len(rr)
        goals = [r[6] + r[7] for r in rr]
        at_a = [r for r in rr if str(r[4]) == a]
        at_b = [r for r in rr if str(r[4]) == b]
        body = box(f'{names[a]} – {names[b]}: tüm karşılaşmalar', [
            ('Maç', n), (f'{names[a]} galibiyeti', ra['wins']), ('Beraberlik', ra['draws']), (f'{names[b]} galibiyeti', rb['wins']),
            ('Goller', f"{ra['gf']} – {rb['gf']}"), ('Maç başı gol', r2(sum(goals) / n) if n else None),
            ('Üst 2.5', pct(sum(g > 2.5 for g in goals) / n) if n else None), ('KG Var', pct(sum(r[6] > 0 and r[7] > 0 for r in rr) / n) if n else None),
            (f'{names[a]} evinde (G-B-M)', '{wins}-{draws}-{losses}'.format(**record(at_a, int(a))) if at_a else None),
            (f'{names[b]} evinde (G-B-M)', '{wins}-{draws}-{losses}'.format(**record(at_b, int(b))) if at_b else None)],
            note='Yalnızca sitedeki sezonlar (API-Football verisi) dahildir.')
        body += grid('Karşılaşmalar', ['Sezon', 'Tarih', 'Maç', 'Skor', 'İlk yarı'],
                     [[esc(seasons[r[2]]['season_label'] if r[2] in seasons else r[2]), esc(datetime.fromtimestamp(r[3], timezone.utc).strftime('%d.%m.%Y') if r[3] else None),
                       mlink(r[1], f"{tlabel(r[4])} – {tlabel(r[5])}"), esc(f'{r[6]} – {r[7]}'), esc(f'{r[8]} – {r[9]}' if r[8] is not None else None)] for r in reversed(rr)])
        body += f'<p class="more"><a href="../../takim/{esc(team_slug[a])}/">{esc(names[a])} istatistikleri →</a> · <a href="../../takim/{esc(team_slug[b])}/">{esc(names[b])} istatistikleri →</a></p>'
        title = f'{names[a]} – {names[b]} rekabeti'
        desc = f'{names[a]} ile {names[b]} arasındaki {n} Süper Lig maçı: {ra["wins"]} – {ra["draws"]} – {rb["wins"]}, goller, Alt/Üst ve KG Var oranları.'
        schema = {'@type': 'WebPage', 'name': title, 'about': [{'@type': 'SportsTeam', 'name': names[a]}, {'@type': 'SportsTeam', 'name': names[b]}]}
        write_page(path, page(title, desc, path, body, [schema], crumbs=[('İkili rekabet', None), (title, None)]))
        urls.append((path, seasons[max(r[2] for r in rr)]['generated_at'] if rr and max(r[2] for r in rr) in seasons else None))

    # ---- player pages (every player with Süper Lig minutes in the cached seasons)
    by_player = defaultdict(dict)
    for y in years:
        for pid, p_ in (players[y].get('players') or {}).items():
            by_player[pid][y] = p_
    for pid, per in sorted(by_player.items()):
        last_y = max(per)
        p_ = per[last_y]
        path = f'oyuncu/{p_["slug"]}/'
        tot = {k: sum(v[k] for v in per.values()) for k in ('apps', 'starts', 'minutes', 'goals', 'assists', 'yellow', 'red', 'shots', 'shots_on', 'key_passes', 'pen_scored', 'pen_missed')}
        club = str(p_.get('team_id'))
        body = box('Süper Lig özeti (mevcut sezonlar)', [
            ('Son takım', Raw(tlink(club)) if club in team_slug else None), ('Mevki', POS.get(p_.get('position'), p_.get('position'))),
            ('Maç (ilk 11)', f"{tot['apps']} ({tot['starts']})"), ('Dakika', tot['minutes']), ('Gol / asist', f"{tot['goals']} / {tot['assists']}"),
            ('90 dakikada gol + asist', r2((tot['goals'] + tot['assists']) * 90 / tot['minutes']) if tot['minutes'] else None),
            ('Şut (isabetli)', f"{tot['shots']} ({tot['shots_on']})"), ('Kilit pas', tot['key_passes']), ('Penaltı gol / kaçan', f"{tot['pen_scored']} / {tot['pen_missed']}"),
            ('Sarı / kırmızı', f"{tot['yellow']} / {tot['red']}")],
            note='Yalnızca oyuncunun forma giydiği ve API\'nin oyuncu istatistiği verdiği Süper Lig maçları.')
        body += grid('Sezonlara göre', ['Sezon', 'Takım', 'Maç', 'İlk 11', 'Dakika', 'Gol', 'Asist', 'Sarı', 'Kırmızı', 'Ort. puan'],
                     [[esc(seasons[yy]['season_label']), ', '.join(tlink(t) for t in v['teams']), esc(v['apps']), esc(v['starts']), esc(v['minutes']), esc(v['goals']),
                       esc(v['assists']), esc(v['yellow']), esc(v['red']), esc(r2(v['rating']))] for yy, v in sorted(per.items(), reverse=True)],
                     note='Ortalama puan, API\'nin maç puanlarının ortalamasıdır (puanı olan maçlar).')
        schema = {'@type': 'Person', 'name': p_['name'], 'jobTitle': 'Futbolcu'}
        if club in names:
            schema['memberOf'] = {'@type': 'SportsTeam', 'name': names[club]}
        if BASE:
            schema['url'] = url(path)
        write_page(path, page(f'{p_["name"]} istatistikleri', f'{p_["name"]} Süper Lig istatistikleri: {tot["apps"]} maç, {tot["goals"]} gol, {tot["assists"]} asist, dakika ve kartlar.',
                              path, body, [schema], crumbs=[(names.get(club, 'Oyuncular'), f'takim/{team_slug[club]}/' if club in team_slug else None), (p_['name'], None)]))
        urls.append((path, seasons[last_y]['generated_at']))

    # ---- Turkish players abroad
    if legion and legion.get('players'):
        path = 'lejyonerler/'
        rows_l = sorted(legion['players'].values(), key=lambda r: (-r['totals']['minutes'], r['name']))
        wk = legion.get('week')
        body = ''
        if wk:
            active = [r for r in rows_l if r.get('week') and r['week']['apps'] > 0]
            active.sort(key=lambda r: (-(r['week']['goals'] + r['week']['assists']), -r['week']['minutes']))
            body += grid(f"Bu hafta ({wk['from']} → {wk['to']})", ['Oyuncu', 'Takım', 'Lig', 'Maç', 'Dakika', 'Gol', 'Asist', 'Kart'],
                         [[esc(r['name']), esc(r['club']), esc(r['league']), esc(r['week']['apps']), esc(r['week']['minutes']), esc(r['week']['goals']),
                           esc(r['week']['assists']), esc(r['week']['yellow'] + r['week']['red'])] for r in active],
                         note='İki haftalık API kaydı arasındaki fark (kulüp maçları, milli maçlar hariç).')
        else:
            body += '<p class="notice">Haftalık karşılaştırma için en az iki haftalık veri gerekir; ilk güncellemeden bir hafta sonra görünür.</p>'
        body += grid(f"Sezon toplamı ({legion['season']}–{str(legion['season'] + 1)[-2:]})", ['Oyuncu', 'Takım', 'Lig', 'Ülke', 'Maç', 'İlk 11', 'Dakika', 'Gol', 'Asist', 'Sarı', 'Kırmızı'],
                     [[esc(r['name']), esc(r['club']), esc(r['league']), esc(r['country']), esc(r['totals']['apps']), esc(r['totals']['starts']), esc(r['totals']['minutes']),
                       esc(r['totals']['goals']), esc(r['totals']['assists']), esc(r['totals']['yellow']), esc(r['totals']['red'])] for r in rows_l],
                     note=f"API'de uyruğu Türkiye olan ve Türkiye dışında kulüp maçı oynayan oyuncular. Kontrol edilen: {legion['checked_players']} oyuncu (milli takım kadrosu ve ek liste). Veri tarihi: {(legion.get('data_as_of') or '')[:10]}.")
        write_page(path, page('Yurt dışındaki Türk futbolcular', 'Avrupa liglerinde ve kupalarında oynayan Türk futbolcuların haftalık ve sezonluk istatistikleri: maç, dakika, gol, asist.',
                              path, body, [{'@type': 'WebPage', 'name': 'Yurt dışındaki Türk futbolcular'}], crumbs=[('Lejyonerler', None)], depth=1))
        urls.append((path, legion.get('data_as_of')))

    # ---- model page: method, backtest, calibration, ratings
    if model:
        path = 'model/'
        bt = model.get('backtest') or {}
        prev_lbl = seasons[model['backtest_season']]['season_label'] if model.get('backtest_season') in seasons else model.get('backtest_season')
        body = box('Yöntem', [('Model', 'Dixon–Coles (Poisson, düşük skor düzeltmesi)'),
                              ('Eğitim sezonları', ', '.join(seasons[y]['season_label'] for y in model['training_seasons'] if y in seasons)),
                              ('Eğitim maçı', model['training_matches']), ('Zaman ağırlığı', f"{model['half_life_days']} gün yarı ömür"),
                              ('Ev sahibi avantajı (gol çarpanı)', r2(model['home_advantage'])), ('ρ (düşük skor düzeltmesi)', r2(model['rho'])),
                              ('Tahmin koşulu', f"Her iki takımın da en az {model['min_team_matches']} eğitim maçı olmalı"),
                              ('Yayımlanmayan fikstür', model.get('withheld_fixtures'))],
                   note='Olasılık tahminidir; kesin sonuç ya da bahis tavsiyesi değildir.')
        if bt.get('matches'):
            warn = '<p class="notice">Test örneği küçük (100 maçtan az); sonuçlar dikkatle yorumlanmalı.</p>' if bt.get('small_sample') else ''
            verdict = 'Model, basit referansı Brier skoru ve log loss bakımından geçiyor.' if bt.get('beats_baseline') else 'Model bu testte basit referansı geçemedi.'
            b0 = bt['baseline']
            body += warn + grid(f'Geriye dönük test ({prev_lbl})', ['Ölçüt', 'Model', 'Basit referans'],
                                [[esc('Maç (atlanan)'), esc(f"{bt['matches']} ({bt['skipped']})"), esc('—')],
                                 [esc('1X2 doğruluk'), esc(pct(bt['accuracy'])), esc(pct(b0['accuracy']))],
                                 [esc('1X2 Brier skoru (düşük iyi)'), esc(bt['brier_score']), esc(b0['brier_score'])],
                                 [esc('1X2 log loss (düşük iyi)'), esc(bt['log_loss']), esc(b0['log_loss'])],
                                 [esc('Üst 2.5 Brier'), esc(bt['over_2_5']['brier_score']), esc(bt['over_2_5']['baseline']['brier_score'])],
                                 [esc('Üst 2.5 doğruluk'), esc(pct(bt['over_2_5']['accuracy'])), esc(pct(bt['over_2_5']['baseline']['accuracy']))],
                                 [esc('KG Var Brier'), esc(bt['btts']['brier_score']), esc(bt['btts']['baseline']['brier_score'])],
                                 [esc('KG Var doğruluk'), esc(pct(bt['btts']['accuracy'])), esc(pct(bt['btts']['baseline']['accuracy']))]],
                                note=f"Her hafta, yalnızca o haftadan önce oynanmış maçlarla yeniden eğitildi ({bt['refits']} kez). Basit referans: eğitim dönemindeki 1/X/2, Üst 2.5 ve KG Var sıklıkları. {verdict}")
            body += grid('Kalibrasyon (ev ve deplasman galibiyeti olasılıkları)', ['Olasılık aralığı', 'Tahmin', 'Ortalama tahmin', 'Gerçekleşen'],
                         [[esc(c['bin']), esc(c['n']), esc(pct(c['predicted'])), esc(pct(c['observed']))] for c in bt['calibration']],
                         note='İyi kalibre edilmiş modelde ortalama tahmin ile gerçekleşen oran birbirine yakındır.')
            preds = model.get('backtest_predictions') or []
            body += grid('Test tahminleri (son 40)', ['Tarih', 'Maç', 'Skor', '1 / X / 2', 'Tahmin', 'Sonuç'],
                         [[esc(date_tr(x['date'])), mlink(x['slug'], f"{x['home']} – {x['away']}", up='../') if x.get('slug') else esc(f"{x['home']} – {x['away']}"), esc(x['score']),
                           esc(' / '.join(pct(v) for v in x['probabilities'])), esc(x['predicted']), esc(x['actual'])] for x in reversed(preds[-40:])])
        else:
            body += f'<p class="notice">{esc(bt.get("note") or "Geriye dönük test yapılamadı.")}</p>'
        body += grid('Takım güçleri (hücum / savunma)', ['Takım', 'Hücum', 'Savunma', 'Eğitim maçı'],
                     [[tlink(r['team_id'], up='../') if str(r['team_id']) in team_slug else esc(r['team']), esc(r2(r['attack'])), esc(r2(r['defence'])), esc(r['matches'])] for r in model['ratings']],
                     note='Log ölçeğinde; 0 lig ortalaması. Hücum yüksek = çok gol atar, savunma yüksek = az gol yer.')
        write_page(path, page('Tahmin modeli ve doğruluk testi', 'Dixon–Coles modelinin yöntemi, geriye dönük test sonuçları (doğruluk, Brier skoru, log loss), kalibrasyon ve takım güçleri.',
                              path, body, [{'@type': 'WebPage', 'name': 'Tahmin modeli'}], crumbs=[('Tahmin modeli', None)], depth=1))
        urls.append((path, model.get('data_as_of')))

    # ---- 404, sitemap, robots
    root404 = (re.sub(r'^https?://[^/]+', '', BASE) + '/') if BASE else '/'
    (PUB / '404.html').write_text(page('Sayfa bulunamadı', 'Aradığınız sayfa bulunamadı. Ana sayfadan puan durumu, takım, hakem ve maç istatistiklerine ulaşabilirsiniz.',
                                       '404.html', '', [], root=root404).replace('content="index,follow"', 'content="noindex"').replace(f'<link rel="canonical" href="{esc(url("404.html"))}">', ''), encoding='utf-8')
    if BASE:
        entries = [f'<url><loc>{esc(BASE)}/</loc></url>'] + [f'<url><loc>{esc(url(p))}</loc>' + (f'<lastmod>{esc(d[:10])}</lastmod>' if d else '') + '</url>' for p, d in sorted(set(urls))]
        (PUB / 'sitemap.xml').write_text('<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + ''.join(entries) + '</urlset>\n', encoding='utf-8')
        (PUB / 'robots.txt').write_text(f'User-agent: *\nAllow: /\nSitemap: {BASE}/sitemap.xml\n', encoding='utf-8')
    else:
        (PUB / 'robots.txt').write_text('User-agent: *\nDisallow: /\n', encoding='utf-8')
    (PUB / '.nojekyll').write_text('', encoding='utf-8')
    kinds = defaultdict(int)
    for p, _ in urls:
        kinds[p.split('/')[0]] += 1
    print(f'Built {PUB} from {len(years)} season file(s): ' + ', '.join(f'{v} {k}' for k, v in sorted(kinds.items())) + ('' if BASE else ' (SITE_URL not set: noindex, no sitemap)'))


if __name__ == '__main__':
    main()
