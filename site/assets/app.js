'use strict';
// Reads only generated JSON (data/index.json, data/public/season_<year>.json, data/public/dixon_coles.json).
// Missing values are shown as "—", never as 0.
const $ = (s) => document.querySelector(s);
let data = null;
let model = null;

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const dash = (v) => (v === null || v === undefined || v === '' ? '—' : esc(v));
const pct = (v) => (v === null || v === undefined ? '—' : `${Math.round(v * 100)}%`);
const num = (v, d = 2) => (v === null || v === undefined ? '—' : Number(v).toFixed(d));
const dateTR = (iso) => (iso ? new Date(iso).toLocaleDateString('tr-TR') : '—');
const teamHref = (t) => `takim/${encodeURIComponent(t.slug)}/`;
const matchHref = (m) => `mac/${encodeURIComponent(m.slug)}/`;

function notice(msg) {
  const n = $('#notice');
  n.hidden = !msg;
  n.textContent = msg || '';
}

function emptyRow(tbody, msg, cols = 12) {
  tbody.innerHTML = `<tr><td colspan="${cols}" class="empty">${esc(msg)}</td></tr>`;
}

function teamsList() {
  return Object.values(data.teams || {});
}

function showTable() {
  const split = $('#table-split').value;
  const query = $('#team-search').value.toLocaleLowerCase('tr');
  const body = $('#standings tbody');
  let rows;
  if (split === 'table') {
    rows = (data.standings?.rows || []).map((r) => ({ ...r, t: data.teams[String(r.team_id)] }));
  } else {
    rows = teamsList()
      .map((t) => ({ ...t[split], team_id: t.id, team: t.name, t }))
      .sort((a, b) => b.points - a.points || b.gd - a.gd || b.gf - a.gf)
      .map((r, i) => ({ ...r, rank: i + 1 }));
  }
  rows = rows.filter((r) => String(r.team || '').toLocaleLowerCase('tr').includes(query));
  if (!rows.length) return emptyRow(body, 'Bu filtre için veri bulunamadı.');
  body.innerHTML = rows.map((r) => {
    const form = (r.t?.form5 || []).map((f) => `<i class="${f.r}">${{ W: 'G', D: 'B', L: 'M' }[f.r]}</i>`).join('');
    const name = r.t ? `<a class="team-link" href="${teamHref(r.t)}">${esc(r.team)}</a>` : esc(r.team);
    return `<tr><td class="rank">${dash(r.rank)}</td><td>${name}</td><td>${dash(r.played)}</td><td>${dash(r.wins)}</td><td>${dash(r.draws)}</td><td>${dash(r.losses)}</td><td>${dash(r.gf)}</td><td>${dash(r.ga)}</td><td>${dash(r.gd)}</td><td class="pts">${dash(r.points)}</td><td><span class="form">${form}</span></td></tr>`;
  }).join('');
  $('#standings-note').textContent = split === 'table' && data.standings?.source === 'computed'
    ? 'Resmî puan durumu API\'de yok; sıralama puan, averaj ve atılan gole göre hesaplandı (ikili averaj uygulanmadı).' : '';
}

// ---------- sortable tables
const sortState = {};
function renderSortable(tableId, cols, rows, opts = {}) {
  const table = $(`#${tableId}`);
  const st = sortState[tableId] || (sortState[tableId] = { k: opts.defaultSort ?? null, asc: !!opts.defaultAsc });
  if (st.k !== null && !cols.some((c) => c.k === st.k)) { st.k = opts.defaultSort ?? null; st.asc = !!opts.defaultAsc; }
  table.querySelector('thead').innerHTML = '<tr>' + cols.map((c) => `<th data-k="${esc(c.k)}" class="${st.k === c.k ? 'sorted' + (st.asc ? ' asc' : '') : ''}"${c.title ? ` title="${esc(c.title)}"` : ''}>${esc(c.label)}</th>`).join('') + '</tr>';
  table.querySelectorAll('th[data-k]').forEach((th) => th.addEventListener('click', () => {
    const k = th.dataset.k;
    if (st.k === k) st.asc = !st.asc; else { st.k = k; st.asc = cols.find((c) => c.k === k)?.text === true; }
    renderSortable(tableId, cols, rows, opts);
  }));
  const col = cols.find((c) => c.k === st.k);
  const sorted = col ? [...rows].sort((a, b) => {
    const va = col.v(a), vb = col.v(b);
    if (va === null || va === undefined) return 1;
    if (vb === null || vb === undefined) return -1;
    const d = typeof va === 'string' ? va.localeCompare(vb, 'tr') : va - vb;
    return st.asc ? d : -d;
  }) : rows;
  const cell = (c, r, extra = '') => { const v = c.v(r); const low = c.n && (c.n(r) ?? 0) < MIN_SAMPLE ? ' low' : ''; return `<td class="${c.text ? '' : 'num'}${low}${extra}"${c.n ? ` title="${c.n(r) ?? 0} maç"` : ''}>${c.html ? c.html(r) : c.f ? c.f(v) : dash(v)}</td>`; };
  const tbody = table.querySelector('tbody');
  const top = (opts.pinned || []).map((r) => `<tr class="league-row">${cols.map((c) => cell(c, r)).join('')}</tr>`).join('');
  tbody.innerHTML = top + (sorted.map((r) => `<tr>${cols.map((c) => cell(c, r)).join('')}</tr>`).join('') || `<tr><td colspan="${cols.length}" class="empty">${esc(opts.empty || 'Veri yok.')}</td></tr>`);
}

let MIN_SAMPLE = 5;
const f1 = (v) => num(v, 1), f2 = (v) => num(v, 2);
const inv = (v) => (v === null || v === undefined ? null : 1 - v);
const per = (sum, n) => (sum === null || sum === undefined || !n ? null : sum / n);
const BUCKETS = ['1-15', '16-30', '31-45+', '46-60', '61-75', '76-90+'];
const HTFT = ['1/1', '1/X', '1/2', 'X/1', 'X/X', 'X/2', '2/1', '2/X', '2/2'];

function lineOptions(sel, lines, preferred) {
  const prev = sel.value;
  sel.innerHTML = lines.map((l) => `<option value="${l}">${l}</option>`).join('');
  sel.value = lines.map(String).includes(prev) ? prev : String(preferred ?? lines[Math.floor(lines.length / 2)]);
}

function showBetting() {
  const L = data.lines || {};
  MIN_SAMPLE = L.min_sample || 5;
  const market = $('#mk-market').value, split = $('#mk-split').value;
  const lineSel = $('#mk-line'), tSel = $('#mk-tline');
  const showLine = ['goals', 'corners', 'cards'].includes(market), showT = ['corners', 'cards'].includes(market);
  $('#mk-line-wrap').hidden = !showLine; $('#mk-tline-wrap').hidden = !showT;
  if (market === 'goals') lineOptions(lineSel, L.goals || [], 2.5);
  if (market === 'corners') { lineOptions(lineSel, L.corners || [], 9.5); lineOptions(tSel, L.team_corners || [], 4.5); }
  if (market === 'cards') { lineOptions(lineSel, L.cards || [], 4.5); lineOptions(tSel, L.team_cards || [], 1.5); }
  const line = lineSel.value, tline = tSel.value;
  const lg = data.league || {};
  const rows = teamsList().map((t) => ({ t, b: t.markets?.[split] || {} }));
  const team = { k: 'team', label: 'Takım', text: true, v: (r) => r.t?.name ?? 'Lig ortalaması', html: (r) => r.t ? `<a class="team-link" href="${teamHref(r.t)}">${esc(r.t.name)}</a>` : 'Lig ortalaması' };
  const league = { lgRow: true, t: null };
  let cols = [], pinned = [], note = '';
  if (market === 'goals') {
    const g = (r) => (r.t ? r.b[`over_${line}`] : lg[`over_${line}`]);
    const n = (r) => (r.t ? r.b.matches : lg.matches);
    cols = [team, { k: 'n', label: 'Maç', v: n },
      { k: 'over', label: `Üst ${line}`, v: g, f: pct, n }, { k: 'under', label: `Alt ${line}`, v: (r) => inv(g(r)), f: pct, n },
      { k: 'gf', label: 'Attığı gol ort.', v: (r) => r.b?.goals_for_avg, f: f2, n }, { k: 'ga', label: 'Yediği gol ort.', v: (r) => r.b?.goals_against_avg, f: f2, n },
      { k: 'tot', label: 'Maç gol ort.', v: (r) => (r.t ? (r.b.goals_for_avg ?? null) !== null ? r.b.goals_for_avg + r.b.goals_against_avg : null : lg.goals_avg), f: f2, n },
      { k: 'scored', label: 'Gol attı', v: (r) => r.b?.scored, f: pct, n }, { k: 'cs', label: 'Gol yemedi', v: (r) => r.b?.clean_sheet, f: pct, n }];
    pinned = [{ ...league, b: {} }];
  } else if (market === 'btts') {
    const n = (r) => (r.t ? r.b.matches : lg.matches);
    cols = [team, { k: 'n', label: 'Maç', v: n },
      { k: 'yes', label: 'KG Var', v: (r) => (r.t ? r.b.btts_yes : lg.btts_yes), f: pct, n }, { k: 'no', label: 'KG Yok', v: (r) => (r.t ? r.b.btts_no : inv(lg.btts_yes)), f: pct, n },
      { k: 'scored', label: 'Gol attı', v: (r) => r.b?.scored, f: pct, n }, { k: 'cs', label: 'Gol yemedi', v: (r) => r.b?.clean_sheet, f: pct, n }];
    pinned = [{ ...league, b: {} }];
  } else if (market === 'htft') {
    const n = (r) => (r.t ? r.b.htft_n : lg.htft_n);
    cols = [team, { k: 'n', label: 'Maç', v: n }, ...HTFT.map((h) => ({ k: h, label: h, v: (r) => (r.t ? r.b.htft_match?.[h] : lg.htft_match?.[h]) ?? null, f: pct, n }))];
    pinned = [{ ...league, b: {} }];
    note = 'İY/MS maçın kendisine göre: 1 = ev sahibi, X = beraberlik, 2 = deplasman. Takımın kendi maçlarında, ilk yarı ve maç sonu sonucunun dağılımı.';
  } else if (market === 'corners') {
    const n = (r) => (r.t ? r.b.corners_n : lg.corners_n);
    const o = (r) => (r.t ? r.b[`corners_over_${line}`] : lg[`corners_over_${line}`]);
    cols = [team, { k: 'n', label: 'Maç', v: n }, { k: 'over', label: `Maç Üst ${line}`, v: o, f: pct, n }, { k: 'under', label: `Maç Alt ${line}`, v: (r) => inv(o(r)), f: pct, n },
      { k: 'tot', label: 'Maç ort.', v: (r) => (r.t ? r.b.corners_total_avg : lg.corners_total_avg), f: f1, n },
      { k: 'for', label: 'Takım ort.', v: (r) => r.b?.corners_for_avg, f: f1, n }, { k: 'ag', label: 'Rakip ort.', v: (r) => r.b?.corners_against_avg, f: f1, n },
      { k: 'tover', label: `Takım Üst ${tline}`, v: (r) => r.b?.[`team_corners_over_${tline}`], f: pct, n }];
    pinned = [{ ...league, b: {} }];
    note = 'Korner verisi API maç istatistiklerinden gelir; istatistiği olmayan maçlar hesaba katılmaz.';
  } else if (market === 'cards') {
    const n = (r) => (r.t ? r.b.cards_n : lg.cards_n);
    const o = (r) => (r.t ? r.b[`cards_over_${line}`] : lg[`cards_over_${line}`]);
    cols = [team, { k: 'n', label: 'Maç', v: n }, { k: 'over', label: `Maç Üst ${line}`, v: o, f: pct, n }, { k: 'under', label: `Maç Alt ${line}`, v: (r) => inv(o(r)), f: pct, n },
      { k: 'tot', label: 'Maç ort.', v: (r) => (r.t ? r.b.cards_total_avg : lg.cards_total_avg), f: f1, n },
      { k: 'for', label: 'Takım ort.', v: (r) => r.b?.cards_for_avg, f: f1, n }, { k: 'tover', label: `Takım Üst ${tline}`, v: (r) => r.b?.[`team_cards_over_${tline}`], f: pct, n }];
    pinned = [{ ...league, b: {} }];
    note = 'Sarı ve kırmızı kartlar sayılır (ikinci sarı = bir kırmızı). Kaynak: API maç olayları.';
  } else if (market === 'first') {
    const n = (r) => (r.t ? r.b.first_goal_n : lg.first_goal_n);
    cols = [team, { k: 'n', label: 'Maç', v: n }, { k: 'for', label: 'İlk golü attı', v: (r) => r.b?.first_goal_for, f: pct, n },
      { k: 'ag', label: 'İlk golü yedi', v: (r) => r.b?.first_goal_against, f: pct, n }, { k: 'none', label: 'Golsüz', v: (r) => r.b?.no_goal, f: pct, n }];
    note = 'Yalnızca olay verisi skorla tutarlı olan maçlar. Lig genelinde ilk golü ev sahibi ' + pct(lg.first_goal_home) + ', deplasman ' + pct(lg.first_goal_away) + ' atmış (' + dash(lg.first_goal_n) + ' maç).';
  } else if (market === 'minutes') {
    const n = (r) => (r.t ? r.b.goal_minutes_n : lg.goal_minutes_n);
    cols = [team, { k: 'n', label: 'Maç', v: n }, ...BUCKETS.map((b, i) => ({ k: 'b' + i, label: b + "'", title: 'Maç başına atılan – yenilen gol',
      v: (r) => (r.t ? per(r.b.goals_for_by_15?.[i], r.b.goal_minutes_n) : per(lg.goals_by_15?.[i], lg.goal_minutes_n)),
      html: (r) => (r.t ? `${f2(per(r.b.goals_for_by_15?.[i], r.b.goal_minutes_n))} – ${f2(per(r.b.goals_against_by_15?.[i], r.b.goal_minutes_n))}` : f2(per(lg.goals_by_15?.[i], lg.goal_minutes_n))), n }))];
    pinned = [{ ...league, b: {} }];
    note = 'Hücre: maç başına atılan – yenilen gol. Lig satırı: maç başına toplam gol. 45+ ve 90+ uzatma dakikaları ilgili dilime dahildir.';
  }
  if (split !== 'all') pinned = [];
  $('#mk-note').textContent = note;
  renderSortable('betting', cols, rows, { pinned, defaultSort: cols[2]?.k, empty: 'Gerçek API verileri geldikten sonra istatistikler burada görünecek.' });
}

let allRefs = null;
function showRefs() {
  const scope = $('#ref-scope').value, min = Number($('#ref-min').value), q = $('#ref-search').value.toLocaleLowerCase('tr');
  let refs;
  if (scope === 'all' && allRefs) refs = Object.values(allRefs).map((r) => ({ slug: r.slug, name: r.name, ...r.totals }));
  else refs = Object.values(data.referees || {});
  refs = refs.filter((r) => r.matches >= min && r.name.toLocaleLowerCase('tr').includes(q));
  const n = (r) => r.events_n;
  const cols = [
    { k: 'name', label: 'Hakem', text: true, v: (r) => r.name, html: (r) => `<a class="ref-link" href="hakem/${encodeURIComponent(r.slug)}/">${esc(r.name)}</a>` },
    { k: 'm', label: 'Maç', v: (r) => r.matches },
    { k: 'y', label: 'Sarı / maç', v: (r) => r.yellow_per_match, f: f2, n },
    { k: 'r', label: 'Kırmızı / maç', v: (r) => r.red_per_match, f: f2, n },
    { k: 'c', label: 'Kart / maç', v: (r) => r.cards_per_match, f: f2, n },
    { k: 'c45', label: 'Üst 4.5 kart', v: (r) => r['cards_over_4.5'], f: pct, n },
    { k: 'p', label: 'Penaltı / maç', v: (r) => r.penalties_per_match, f: f2, n },
    { k: 'h', label: 'Ev sahibi gal.', v: (r) => r.home_win_rate, f: pct, n: (r) => r.matches },
  ];
  renderSortable('referees', cols, refs, { defaultSort: 'm', empty: 'Bu filtreyle hakem bulunamadı.' });
}

let players = null;
const POS = { G: 'Kaleci', D: 'Defans', M: 'Orta saha', F: 'Forvet' };
function showPlayers() {
  const tbody = $('#players tbody');
  if (!players?.players) { $('#players thead').innerHTML = ''; tbody.innerHTML = '<tr><td class="empty">Bu sezon için oyuncu istatistiği yok.</td></tr>'; $('#pl-note').textContent = ''; return; }
  const sel = $('#pl-team'), prev = sel.value;
  sel.innerHTML = '<option value="">Tümü</option>' + teamsList().sort((a, b) => a.name.localeCompare(b.name, 'tr')).map((t) => `<option value="${t.id}">${esc(t.name)}</option>`).join('');
  sel.value = [...sel.options].some((o) => o.value === prev) ? prev : '';
  const team = sel.value, min = Number($('#pl-min').value), q = $('#pl-search').value.toLocaleLowerCase('tr');
  const rows = Object.values(players.players).map((p) => (team ? { ...p, ...p.by_team?.[team], team_id: Number(team), _in: !!p.by_team?.[team] } : { ...p, _in: true }))
    .filter((p) => p._in && p.minutes >= min && String(p.name).toLocaleLowerCase('tr').includes(q));
  const t = (p) => data.teams[String(p.team_id)];
  const cols = [
    { k: 'name', label: 'Oyuncu', text: true, v: (p) => p.name, html: (p) => `<a href="oyuncu/${encodeURIComponent(p.slug)}/">${esc(p.name)}</a>` },
    { k: 'team', label: 'Takım', text: true, v: (p) => t(p)?.name ?? null, html: (p) => (t(p) ? `<a class="team-link" href="${teamHref(t(p))}">${esc(t(p).name)}</a>` : '—') },
    { k: 'pos', label: 'Mevki', text: true, v: (p) => POS[p.position] ?? p.position ?? null },
    { k: 'apps', label: 'Maç', v: (p) => p.apps }, { k: 'min', label: 'Dakika', v: (p) => p.minutes },
    { k: 'g', label: 'Gol', v: (p) => p.goals }, { k: 'a', label: 'Asist', v: (p) => p.assists },
    { k: 'ga90', label: 'G+A / 90', v: (p) => (p.minutes ? ((p.goals + p.assists) * 90) / p.minutes : null), f: f2 },
    { k: 'y', label: 'Sarı', v: (p) => p.yellow }, { k: 'r', label: 'Kırmızı', v: (p) => p.red },
    { k: 'rt', label: 'Ort. puan', v: (p) => (team ? null : p.rating), f: f2 },
  ];
  sortState.players = sortState.players || { k: 'g', asc: false };
  renderSortable('players', cols, rows, { defaultSort: 'g', empty: 'Bu filtreyle oyuncu bulunamadı.' });
  $('#pl-note').textContent = 'Kaynak: API maç kadroları ve oyuncu istatistikleri; yalnızca forma giyilen maçlar. Takım seçildiğinde o takımdaki maçlar sayılır.';
}

function matchCard(m) {
  const score = m.finished ? `${m.home_goals} : ${m.away_goals}` : '– : –';
  return `<a class="match-card" href="${matchHref(m)}"><span class="match-teams"><span>${esc(m.home)}</span><span>${esc(m.away)}</span></span><span class="score">${score}</span><span class="match-date">${dateTR(m.date)}<br>${esc(m.status || '')}</span></a>`;
}

function showMatches() {
  const q = $('#match-search').value.toLocaleLowerCase('tr');
  const root = $('#matches');
  const ms = (data.matches || []).filter((m) => `${m.home} ${m.away}`.toLocaleLowerCase('tr').includes(q)).sort((a, b) => (b.timestamp || 0) - (a.timestamp || 0));
  root.innerHTML = ms.length ? ms.slice(0, 60).map(matchCard).join('') : '<div class="empty">Karşılaşma verisi bulunamadı.</div>';
}

function fillSelect(sel, teams) {
  const prev = sel.value;
  sel.innerHTML = teams.map((t) => `<option value="${esc(t.id)}">${esc(t.name)}</option>`).join('');
  if (teams.some((t) => String(t.id) === prev)) sel.value = prev;
}

function showTrend() {
  const sel = $('#trend-team');
  const teams = teamsList().sort((a, b) => a.name.localeCompare(b.name, 'tr'));
  if (!sel.options.length || !teams.some((t) => String(t.id) === sel.value)) fillSelect(sel, teams);
  const series = data.table_by_round?.[sel.value] || [];
  const root = $('#position-chart');
  if (!series.length) { root.innerHTML = '<p class="muted">Bu takım için hafta bazlı sıralama verisi yok.</p>'; return; }
  const n = teams.length;
  const W = Math.max(540, series.length * 30), H = 220, pad = 28;
  const xy = (p, i) => [pad + (i * (W - pad * 2)) / Math.max(1, series.length - 1), pad + ((p.position - 1) * (H - pad * 2)) / Math.max(1, n - 1)];
  const pts = series.map((p, i) => xy(p, i).join(',')).join(' ');
  const dots = series.map((p, i) => { const [x, y] = xy(p, i); return `<circle cx="${x}" cy="${y}" r="3"><title>${p.round}. hafta: ${p.position}. sıra, ${p.points} puan</title></circle>`; }).join('');
  root.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(data.teams[sel.value]?.name)} hafta bazlı sıralama"><line x1="${pad}" y1="${pad}" x2="${W - pad}" y2="${pad}"/><line x1="${pad}" y1="${H - pad}" x2="${W - pad}" y2="${H - pad}"/><polyline points="${pts}"/>${dots}</svg><p class="muted">1. sıra üstte. Hafta numarasına göre, o haftaya kadar oynanmış maçlardan hesaplanır.</p>`;
}

let results = null;
function showH2H() {
  // All seasons when results.json is available, otherwise the selected season only.
  const teams = results
    ? Object.entries(results.teams).map(([id, t]) => ({ id: Number(id), name: t.name, slug: t.slug }))
    : teamsList();
  teams.sort((a, b) => a.name.localeCompare(b.name, 'tr'));
  for (const id of ['#h2h-home', '#h2h-away']) if (!$(id).options.length || !teams.some((t) => String(t.id) === $(id).value)) fillSelect($(id), teams);
  if ($('#h2h-home').value === $('#h2h-away').value && teams.length > 1) $('#h2h-away').value = String(teams.find((t) => String(t.id) !== $('#h2h-home').value).id);
  const a = Number($('#h2h-home').value), b = Number($('#h2h-away').value);
  const byId = Object.fromEntries(teams.map((t) => [t.id, t]));
  let ms;
  if (results) {
    ms = results.rows.filter((r) => (r[4] === a && r[5] === b) || (r[4] === b && r[5] === a))
      .map((r) => ({ id: r[0], slug: r[1], season: r[2], timestamp: r[3], date: r[3] ? new Date(r[3] * 1000).toISOString() : null, home_id: r[4], away_id: r[5], home_goals: r[6], away_goals: r[7], finished: true, home: byId[r[4]]?.name, away: byId[r[5]]?.name, status: '' }));
  } else {
    ms = (data.matches || []).filter((m) => m.finished && ((m.home_id === a && m.away_id === b) || (m.home_id === b && m.away_id === a)));
  }
  ms.sort((x, y) => (y.timestamp || 0) - (x.timestamp || 0));
  let w = 0, d = 0, l = 0, g = 0;
  for (const m of ms) { const ag = m.home_id === a ? m.home_goals : m.away_goals, bg = m.home_id === a ? m.away_goals : m.home_goals; g += ag + bg; if (ag > bg) w++; else if (ag === bg) d++; else l++; }
  const sa = byId[a]?.slug, sb = byId[b]?.slug;
  const link = ms.length && sa && sb ? ` · <a href="h2h/${[sa, sb].sort().map(encodeURIComponent).join('-vs-')}/">ayrıntılı sayfa →</a>` : '';
  const scope = results ? 'tüm sezonlar' : 'bu sezon';
  $('#h2h-result').innerHTML = `<div class="muted">${esc(byId[a]?.name)}: ${w} galibiyet · ${d} beraberlik · ${l} mağlubiyet · ${ms.length} maç (${scope})${ms.length ? ` · maç başı ${num(g / ms.length, 2)} gol` : ''}${link}</div>` +
    (ms.length ? ms.slice(0, 20).map(matchCard).join('') : '<p class="muted">Bu iki takım arasında kayıtlı maç yok.</p>');
}

function showModel() {
  const summary = $('#model-summary');
  const grid = $('#predictions');
  if (!model || model.season !== data.season) {
    summary.textContent = model ? `Model ${model.season} sezonu için hesaplandı; bu sezon için tahmin yok.` : 'Model için en az iki gerçek sezon ve 80 tamamlanmış maç gerekir.';
    grid.innerHTML = '';
    return;
  }
  const bt = model.backtest || {};
  const warn = bt.small_sample ? ' · Test örneği küçük.' : '';
  summary.innerHTML = '';
  summary.textContent = `Dixon–Coles · ${model.training_matches} maç (${model.training_seasons.join(', ')}) · backtest ${model.backtest_season}: ${bt.matches ?? 0} maç, doğruluk ${pct(bt.accuracy)}, Brier ${dash(bt.brier_score)}, log loss ${dash(bt.log_loss)}` + (bt.baseline ? ` (basit referans: Brier ${bt.baseline.brier_score}, log loss ${bt.baseline.log_loss})` : '') + warn;
  summary.insertAdjacentHTML('beforeend', ' · <a href="model/">Yöntem ve test ayrıntıları →</a>');
  const byId = new Map((data.matches || []).map((m) => [String(m.id), m]));
  const items = Object.entries(model.fixtures || {}).map(([id, p]) => [byId.get(id), p]).filter(([m]) => m && !m.finished).sort((x, y) => x[0].timestamp - y[0].timestamp).slice(0, 9);
  grid.innerHTML = items.map(([m, p]) => `<a class="prediction" href="${matchHref(m)}"><strong>${esc(m.home)} – ${esc(m.away)}</strong><span>${dateTR(m.date)}</span><span>1 ${pct(p.home_win)} · X ${pct(p.draw)} · 2 ${pct(p.away_win)}</span><br>Üst 2.5 ${pct(p.over_2_5)} · KG Var ${pct(p.btts_yes)}<br>En olası skor: ${esc(p.top_scores?.[0]?.score || '—')}</a>`).join('') || '<div class="empty">Tahmin yayımlanacak fikstür yok.</div>';
}

function render() {
  for (const id of results ? ['#trend-team'] : ['#trend-team', '#h2h-home', '#h2h-away']) {
    const sel = $(id), prev = sel.value;
    fillSelect(sel, teamsList().sort((a, b) => a.name.localeCompare(b.name, 'tr')));
    if (prev && [...sel.options].some((o) => o.value === prev)) sel.value = prev;
  }
  showTable(); showTrend(); showPlayers(); showBetting(); showRefs(); showMatches(); showH2H(); showModel();
  $('#updated-at').textContent = `Veri tarihi: ${data.generated_at ? new Date(data.generated_at).toLocaleString('tr-TR') : '—'}`;
  const q = data.data_quality || {};
  $('#coverage').textContent = `${q.finished ?? 0} tamamlanmış / ${q.matches ?? 0} kayıtlı maç. Ayrıntılı veri: ${q.with_details ?? 0}, olay verisi: ${q.with_events ?? 0}, maç istatistiği: ${q.with_statistics ?? 0}, hakem bilgisi: ${q.with_referee ?? 0}. Eksik alanlar sıfır kabul edilmez.`;
}

async function loadSeason(year) {
  try {
    const res = await fetch(`data/public/season_${encodeURIComponent(year)}.json`);
    if (!res.ok) throw new Error('Bu sezon için veri dosyası bulunamadı.');
    const obj = await res.json();
    if (obj.schema_version !== 2) throw new Error('Veri dosyası eski biçimde; site yeniden oluşturulmalı.');
    data = obj;
    players = null;
    if (seasonIndex?.players?.includes(Number(year))) { try { const r = await fetch(`data/public/players_${encodeURIComponent(year)}.json`); if (r.ok) players = await r.json(); } catch (_) { players = null; } }
    notice('');
    render();
  } catch (e) {
    notice(e.message);
  }
}

let seasonIndex = null;
async function init() {
  $('#menu-toggle').addEventListener('click', () => { const open = $('#main-nav').classList.toggle('open'); $('#menu-toggle').setAttribute('aria-expanded', String(open)); });
  let index = null;
  try { const r = await fetch('data/index.json'); if (r.ok) index = await r.json(); } catch (_) { /* offline or not built */ }
  seasonIndex = index;
  if (index?.legionnaires) $('#nav-legion').hidden = false;
  const years = Object.keys(index?.seasons || {}).sort((a, b) => b - a);
  if (!years.length) {
    notice('Henüz API-Football verisi yayımlanmadı. Sayfalar gerçek veri alındıktan sonra dolar.');
    emptyRow($('#standings tbody'), 'Gerçek veri bekleniyor.');
    for (const id of ['betting', 'referees']) $(`#${id} tbody`).innerHTML = '<tr><td class="empty">Gerçek veri bekleniyor.</td></tr>';
    return;
  }
  $('#season-select').innerHTML = years.map((y) => `<option value="${esc(y)}">${esc(index.seasons[y].label || y)}</option>`).join('');
  if (index.results) { try { const r = await fetch('data/public/results.json'); if (r.ok) results = await r.json(); } catch (_) { results = null; } }
  if (index.referees) { try { const r = await fetch('data/public/referees.json'); if (r.ok) allRefs = (await r.json()).referees; } catch (_) { allRefs = null; } }
  if (!allRefs) $('#ref-scope').querySelector('option[value="all"]').disabled = true;
  if (index.model) { try { const r = await fetch('data/public/dixon_coles.json'); if (r.ok) model = await r.json(); } catch (_) { model = null; } }
  await loadSeason(years[0]);
  $('#season-select').addEventListener('change', (e) => loadSeason(e.target.value));
  $('#table-split').addEventListener('change', showTable);
  $('#team-search').addEventListener('input', showTable);
  $('#ref-search').addEventListener('input', showRefs);
  $('#ref-scope').addEventListener('change', showRefs);
  for (const id of ['#pl-team', '#pl-min']) $(id).addEventListener('change', showPlayers);
  $('#pl-search').addEventListener('input', showPlayers);
  $('#ref-min').addEventListener('change', showRefs);
  for (const id of ['#mk-market', '#mk-split', '#mk-line', '#mk-tline']) $(id).addEventListener('change', showBetting);
  $('#match-search').addEventListener('input', showMatches);
  $('#trend-team').addEventListener('change', showTrend);
  $('#h2h-home').addEventListener('change', showH2H);
  $('#h2h-away').addEventListener('change', showH2H);
}

init();
