// UI state + rendering. The model lives in engine.js; d3/topojson come from global <script>s.
import { simulate, parsePct, rebalance, validateAlliance, allianceId, ambitoFactor, lastSeat, presetPct, mulberry32, mcRuns, summarize, pollSigma, MAJORITY, blocSeats, csvRows, solveTarget, forChamber, overridesFit, nationalShares, scenarioProb, withTurnout, turnoutPct } from './engine.js';
import { simulateSenate } from './senate.js';
import { createMap } from './map.js';
import { createHemicycle } from './hemicycle.js';
import { cleanState, cleanScenarios, newScenario, newId, consequences, decodeShare, encodeShare, cleanView, diffResults, createHistory } from './scenarios.js';

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
const nf = (d) => new Intl.NumberFormat('es-ES', { minimumFractionDigits: d, maximumFractionDigits: d, useGrouping: 'always' }); // es-ES skips the dot on 4 digits otherwise
const [f0, f1, f2, f3] = [0, 1, 2, 3].map(nf);
const round2 = (x) => Math.round(x * 100) / 100;
const fmtIn = (x) => String(round2(x)).replace('.', ','); // value shown in editable inputs
const sw = (color) => `<i class="sw" style="background:${esc(color)}"></i>`;
const clone = (x) => structuredClone(x);
// localStorage may be blocked (private mode, sandboxed frames): reads fall back to null, writes are dropped.
const store = {
  get(key) { try { return JSON.parse(localStorage.getItem(key)); } catch { return null; } },
  set(key, v) { try { localStorage.setItem(key, JSON.stringify(v)); } catch { /* not persisted */ } },
};

async function load(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
  return r.json();
}

let topo, provinces, elast, polls, senateData, senatePolls;
try {
  [topo, provinces, elast, polls, senateData, senatePolls] = await Promise.all(
    ['data/spain_provinces.topojson', 'data/provinces_data.json', 'data/party_elasticity.json', 'data/polls.json', 'data/senate.json', 'data/polls_senate.json'].map(load),
  );
} catch (e) {
  document.body.innerHTML = `<p class="error" style="padding:20px">No se pudieron cargar los datos: ${esc(e.message)}</p>`;
  throw e;
}

const parties = [...elast.parties].sort((a, b) => a.order - b.order);
const byId = Object.fromEntries(parties.map((p) => [p.party_id, p]));
const partyIds = parties.filter((p) => p.party_id !== 'otros').map((p) => p.party_id);
const allIds = parties.map((p) => p.party_id); // scenarios also store otros
const provById = Object.fromEntries(provinces.map((p) => [p.id, p]));
// Turnout: one national figure moves every province (same logit shift) and, through the slopes learned by the model
// lab, the parties' national %. The provinces array is updated in place so every chamber and simulation sees it.
const BASE_PROVS = provinces.map((p) => ({ ...p }));
const BASE_TURNOUT = (100 * BASE_PROVS.reduce((a, p) => a + p.expected_census, 0)) / BASE_PROVS.reduce((a, p) => a + p.census, 0);
const TURNOUT_SLOPES = polls.method?.turnout_effect ?? {};
let turnout = (() => { try { return Number(localStorage.getItem('e29n.turnout')) || BASE_TURNOUT; } catch { return BASE_TURNOUT; } })();
const applyTurnout = () => withTurnout(BASE_PROVS, turnout / 100).forEach((p, i) => (provinces[i].expected_census = p.expected_census));
if (Math.abs(turnout - BASE_TURNOUT) > 0.01) applyTurnout();
const placeholder = provinces.some((p) => p.placeholder) || elast.placeholder || polls.placeholder;
// Monte Carlo noise per party (pct points), proportional to its average: 'final' = error of the poll average in the last
// week of campaign, 'now' = at today's distance from the vote (both learned from 2018-2026 elections).
let sigmaMode = store.get('e29n.sigmaMode') === 'now' ? 'now' : 'final';
let SIGMA = pollSigma(polls, parties, sigmaMode);
// Provincial parties (input "ambito"): the field shows % of their own provinces = national share N * factor. State keeps N.
const factor = Object.fromEntries(partyIds.filter((id) => byId[id].input === 'ambito').map((id) => [id, ambitoFactor(provinces, byId[id].provinces)]));
const shown = (id, pct = S.pct) => pct[id] * (factor[id] ?? 1);
const toStore = (id, v) => v / (factor[id] ?? 1);

// ---------- theme: CSS does the work, the button only sets data-theme and remembers it ----------
const themeBtn = $('#theme-btn');
const isDark = () => (document.documentElement.dataset.theme || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')) === 'dark';
const paintTheme = () => (themeBtn.textContent = isDark() ? 'Modo claro' : 'Modo oscuro');
try { const saved = localStorage.getItem('theme'); if (saved) document.documentElement.dataset.theme = saved; } catch { /* private mode: system theme */ }
paintTheme();
themeBtn.addEventListener('click', () => {
  const t = isDark() ? 'light' : 'dark';
  document.documentElement.dataset.theme = t;
  try { localStorage.setItem('theme', t); } catch { /* not persisted */ }
  paintTheme();
});

// ---------- state ----------
function fixOtros(pct, scale) {
  let s = partyIds.reduce((a, id) => a + pct[id], 0);
  if (scale && s > 100) {
    partyIds.forEach((id) => (pct[id] *= 100 / s));
    s = 100;
  }
  pct.otros = Math.max(0, 100 - s);
  return pct;
}
const pollAverage = () => fixOtros(Object.fromEntries(partyIds.map((id) => [id, polls.average[id] ?? byId[id].default_pct ?? 0])), true);

// Chambers: same votes, different seat rules. Senate = limited vote per constituency + designated (fixed per CCAA).
const SEN_TOTAL = senateData.constituencies.reduce((a, c) => a + c.seats, 0) + senateData.designated.total;
const CHAMBER = {
  congreso: { title: 'Congreso de los Diputados', total: 350, majority: 176 },
  senado: { title: 'Senado', total: SEN_TOTAL, majority: Math.floor(SEN_TOTAL / 2) + 1 },
};

// Shareable scenario in the URL hash (#s=...). Bad or missing hashes are ignored.
const fromHash = decodeShare(location.hash);
const NO_SEAT = parties.filter((p) => p.seats_eligible === false).map((p) => p.party_id);
// Congreso 2023 por candidatura (base del Δ). Una coalición corre como su anfitrión, así que se compara con él.
const SEATS_2023 = { pp: 137, psoe: 121, vox: 33, fa: 31, erc: 7, junts: 7, bildu: 6, pnv: 5, bng: 1, cc: 1, upn: 1 };
const base2023 = (id) => SEATS_2023[id] ?? 0;
const round2Obj = (o) => Object.fromEntries(Object.entries(o).map(([k, v]) => [k, round2(v)]));

// Senado: si hay encuestas de voto del Senado, su media es el % nacional de la simulación del Senado (pctSenate); si no, el del Congreso.
const senateVoteOf = (sp) => (sp.average?.vote_pct ? fixOtros(Object.fromEntries(partyIds.map((id) => [id, sp.average.vote_pct[id] ?? 0])), true) : null);
let senateVoteAvg = senateVoteOf(senatePolls);
const senKey = () => (S.chamber === 'senado' && S.pctSenate ? 'pctSenate' : 'pct'); // el % que edita el panel nacional ahora
const natPct = () => S[senKey()];
// «Senado de Progreso» (pactado el 15-oct-2026): PSOE + Frente Amplio + Compromís en una sola lista al Senado, salvo en Cataluña.
const defaultAlliances = () => ({
  national: [{ host: 'psoe', chamber: 'senado', except: ['08', '17', '25', '43'], members: ['psoe', 'fa', 'fv'].map((party) => ({ party, toCoalition: 100, transfers: [] })) }],
  provincial: {},
});
const S = { pct: pollAverage(), pctSenate: senateVoteAvg, alliances: defaultAlliances(), overrides: {} /* {provId: {party: pct}} */, selected: null, balance: true, source: 'prop', chamber: 'congreso', pact: [], mapMode: 'ganador', touched: new Set() /* cards typed by the user */, view: 'hemi', loaded: null /* saved scenario id */, renaming: null };
// The hash wins over autosave when its pct is complete; cleanState drops malformed coalitions and unknown pact ids.
const hashState = cleanState({ pct: fromHash?.p, alliances: fromHash?.a, pact: fromHash?.g, overrides: fromHash?.o }, allIds);
const hashOk = !!hashState && partyIds.every((id) => Number.isFinite(fromHash.p[id]));
let scenarios = cleanScenarios(store.get('e29n.scenarios'), allIds); // saved scenarios (localStorage)
const curRaw = store.get('e29n.current'); // autosaved working state, restored unless the URL carries one
const cur = cleanState(curRaw, allIds);
if (fromHash ? fromHash.c === 'senado' : curRaw?.chamber === 'senado') S.chamber = 'senado';
if (hashOk) Object.assign(S, { pct: fixOtros(hashState.pct, true), alliances: hashState.alliances, pact: hashState.pact, overrides: hashState.overrides, ...cleanView(fromHash, provinces.map((p) => p.id), partyIds) });
else if (cur) Object.assign(S, { pct: cur.pct, alliances: cur.alliances, pact: cur.pact, overrides: cur.overrides, loaded: scenarios.some((s) => s.id === curRaw.loaded) ? curRaw.loaded : null });
// Una vez: el escenario de trabajo guardado antes del pacto del Senado lo recibe (luego se puede disolver como cualquier otro).
const freeOf = (ids) => !S.alliances.national.some((a) => a.members.some((m) => ids.includes(m.party)));
if (!hashOk && cur && !store.get('e29n.senadoProgreso') && freeOf(['psoe', 'fa', 'fv'])) S.alliances.national.push(...defaultAlliances().national);
store.set('e29n.senadoProgreso', true);
let R = null; // last simulate() result
let SN = null; // last simulateSenate() result
const sumOf = (m) => Object.values(m).reduce((a, b) => a + b, 0);
// Nombre + color del anfitrión para cualquier candidatura (también coaliciones de otro escenario, fuera de R.contenders).
const contenderLabel = (id) => {
  const c = R.contenders[id];
  return `${sw(c?.color ?? byId[id.split('+')[0]]?.color ?? '#999')}${esc(c?.name ?? id.split('+').map((p) => byId[p]?.name ?? p).join(' + '))}`;
};
// Leyenda clicable (pick): pulsar un partido lo añade/quita del pacto. delta: Δ escaños vs 2023 (solo Congreso).
const chips = (m, { cls = '', delta = false, pick = false } = {}) => Object.entries(m).filter(([id, n]) => n > 0 && R.contenders[id])
  .sort(([a], [b]) => R.contenders[a].order - R.contenders[b].order)
  .map(([id, n]) => {
    const d = delta ? n - base2023(id) : null;
    const tag = d == null ? '' : ` <small class="delta${d > 0 ? ' up' : d < 0 ? ' down' : ''}">${d > 0 ? '+' : ''}${d}</small>`;
    const el = pick ? 'button' : 'span';
    const attrs = pick ? ` type="button" data-pact="${id}" aria-pressed="${S.pact.includes(id)}"` : '';
    return `<${el} class="chip${cls}${pick && S.pact.includes(id) ? ' in-pact' : ''}"${attrs}>${contenderLabel(id)} <b>${n}</b>${tag}</${el}>`;
  }).join('');
// % de la candidatura que contiene al partido (si va en coalición, el % de la coalición).
const shareOf = (r, id) => r.pct[Object.keys(r.pct).find((k) => k === id || r.coal[k]?.includes(id))] ?? 0;
const shareHash = () => encodeShare({ p: round2Obj(S.pct), a: S.alliances, o: S.overrides, c: S.chamber, g: S.pact, v: S.view, s: S.selected, m: S.mapMode });
async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const t = Object.assign(document.createElement('textarea'), { value: text }); // no clipboard API (http, old browsers)
    document.body.append(t);
    t.select();
    const ok = document.execCommand('copy');
    t.remove();
    return ok;
  }
}
const seatsOf = (id) => (S.chamber === 'congreso' ? R.provinces[id].seats : SN.provinces[id].seats);
const winnerOf = (id) => (S.chamber === 'congreso' ? R.provinces[id].winner : SN.provinces[id].winner);
const senSeats = (id) => senateData.constituencies.filter((c) => c.province_id === id).reduce((a, c) => a + c.seats, 0);

let raf = 0;
const update = () => (raf ||= requestAnimationFrame(() => { raf = 0; render(); }));

let flashTimer;
function flash(msg) {
  const el = $('#flash');
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(flashTimer);
  flashTimer = setTimeout(() => el.classList.remove('show'), 4000);
}

const select = (id) => { S.selected = id || null; update(); };
const listOf = (scope) => (scope.kind === 'national' ? S.alliances.national : (S.alliances.provincial[scope.id] ||= []));
const coalitionName = (a) => `${byId[a.host].name} (con ${a.members.map((m) => m.party).filter((p) => p !== a.host).map((p) => byId[p].name).join(', ')})`;
const CHAMBER_TAG = { ambos: 'C+S', congreso: 'C', senado: 'S' };
const coalitionCards = (list, pct) => list.map((a) => {
  const id = allianceId(a);
  const share = pct?.[id] != null ? ` <small>${f1.format(pct[id])} %</small>` : '';
  return `<button type="button" class="coal" data-coal="${esc(id)}" style="border-left-color:${esc(byId[a.host].color)}" title="Editar o disolver${a.except?.length ? ` · no se presenta en ${esc(a.except.map((id) => provById[id]?.name ?? id).join(', '))}` : ''}">${esc(coalitionName(a))}<small class="tag">${CHAMBER_TAG[a.chamber ?? 'ambos']}</small>${share}</button>`;
}).join('');

// ---------- static setup ----------
$('#placeholder-badge').hidden = !placeholder;
const provSelect = $('#prov-select');
provSelect.innerHTML += [...provinces].sort((a, b) => a.name.localeCompare(b.name, 'es'))
  .map((p) => `<option value="${p.id}">${esc(p.name)}</option>`).join('');
provSelect.addEventListener('change', () => select(provSelect.value));

const map = createMap($('#map'), $('#tooltip'), topo, {
  onClick: (id) => select(S.selected === id ? null : id),
  tooltip: (id) => {
    const pr = provById[id];
    const r = R.provinces[id];
    if (!r) return `<span class="title">${esc(pr?.name ?? id)}</span>`;
    const top3 = Object.entries(r.pct).sort((a, b) => b[1] - a[1]).slice(0, 3)
      .map(([cid, p]) => `${contenderLabel(cid)} <b>${f1.format(p)} %</b>`).join('<br>');
    const n = seatsOf(id) ? Object.values(seatsOf(id)).reduce((a, b) => a + b, 0) : 0;
    return `<span class="title">${esc(pr.name)}</span><span class="muted">${n} ${n === 1 ? 'escaño' : 'escaños'} en el ${S.chamber === 'congreso' ? 'Congreso' : 'Senado'}</span><br>${top3}<br><span class="muted">Gana: ${R.contenders[winnerOf(id)] ? contenderLabel(winnerOf(id)) : '—'}</span>`;
  },
});
const hemi = createHemicycle($('#hemicycle'));

// National cards: grouped sections (built once; render() only refreshes values).
const cardsEl = $('#cards');
const card = (id) => {
  const p = byId[id];
  const provs = (p.provinces || []).map((c) => provById[c].name).join(', ');
  return `<div class="card" data-party="${id}"${provs ? ` data-provs="${esc(provs)}"` : ''}>
    <span class="handle" draggable="true" title="${esc(p.full_name || p.name)} (arrastra para pactar)">${sw(p.color)}<span>${esc(p.name)}</span></span>
    <input inputmode="decimal" autocomplete="off" aria-label="% ${esc(p.name)}" data-party="${id}"><span class="unit">%</span></div>`;
};
const groups = [
  ['Nacionales', (p) => p.scope === 'national'],
  ['Autonómicos', (p) => p.scope !== 'national' && p.input !== 'ambito'],
  ['Provinciales', (p) => p.input === 'ambito'],
];
cardsEl.innerHTML = `<div class="cards"><div class="card readonly"><span class="handle">${sw(byId.otros.color)}<span>Otros/Blanco</span></span><output id="otros-out"></output><span class="unit">%</span></div></div>`
  + groups.map(([title, test]) => {
    const ids = partyIds.filter((id) => test(byId[id]));
    return `<details class="group" open><summary>${title} <span class="muted">${ids.length}</span></summary><div class="cards">${ids.map(card).join('')}</div></details>`;
  }).join('');

cardsEl.addEventListener('input', (e) => e.target.classList.toggle('invalid', parsePct(e.target.value) === null));
cardsEl.addEventListener('keydown', (e) => e.key === 'Enter' && e.target.matches('input') && e.target.blur());
// Hover tag: provinces where a provincial/regional party runs (fixed tooltip, so the scroll box cannot clip it).
const tipEl = $('#tooltip');
cardsEl.addEventListener('mousemove', (e) => {
  const c = e.target.closest('[data-provs]');
  tipEl.hidden = !c;
  if (!c) return;
  tipEl.innerHTML = `<span class="muted">Provincias</span><br>${esc(c.dataset.provs)}`;
  tipEl.style.left = `${Math.min(e.clientX + 14, innerWidth - tipEl.offsetWidth - 8)}px`;
  tipEl.style.top = `${e.clientY + 14}px`;
});
cardsEl.addEventListener('mouseleave', () => (tipEl.hidden = true));
cardsEl.addEventListener('change', (e) => {
  const input = e.target;
  const id = input.dataset.party;
  const v = parsePct(input.value);
  input.classList.toggle('invalid', v === null);
  if (v === null) return;
  S.touched.add(id);
  if (v !== round2(shown(id, natPct()))) {
    const n = toStore(id, v);
    if (S.balance) S[senKey()] = rebalance(natPct(), id, n, S.source);
    else fixOtros(Object.assign(natPct(), { [id]: n }), false);
  }
  update();
});

const sourceSel = $('#source');
sourceSel.innerHTML += `<option value="otros">Otros/Blanco</option>` + partyIds.map((id) => `<option value="${id}">${esc(byId[id].name)}</option>`).join('');
sourceSel.addEventListener('change', () => (S.source = sourceSel.value));
$('#balance').addEventListener('change', (e) => {
  S.balance = e.target.checked;
  sourceSel.disabled = !S.balance;
  if (S.balance) fixOtros(natPct(), true);
  update();
});
const presetSel = $('#preset');
let pollsterOf = {};
function buildPresets() {
  pollsterOf = Object.fromEntries(polls.pollsters.map((p) => [p.key, p]));
  presetSel.innerHTML = '<option value="media">Media ponderada</option>' + polls.polls.map((p, i) => {
    const w = pollsterOf[p.pollster_key]?.weight;
    return `<option value="${i}">${esc(p.pollster)} · ${p.fieldwork_end}${w == null ? '' : ` · peso ${f1.format(100 * w)} %`}</option>`;
  }).join('');
}
buildPresets();
const presetPctOf = (poll) => fixOtros(presetPct(poll.values, pollAverage(), partyIds), true);
// Selector shows the preset whose pct matches S.pct (rounded), or blank when none does.
const syncPreset = () => {
  const key = partyIds.map((id) => round2(S.pct[id])).join();
  const hit = [['media', pollAverage()], ...polls.polls.map((p, i) => [String(i), presetPctOf(p)])]
    .find(([, pct]) => partyIds.map((id) => round2(pct[id])).join() === key);
  presetSel.value = hit ? hit[0] : '';
};
presetSel.addEventListener('change', () => {
  const poll = polls.polls[presetSel.value];
  S.pct = poll ? presetPctOf(poll) : pollAverage();
  S.touched.clear();
  update();
});
syncPreset();
function setTurnout(t, movePct = true) {
  t = Math.min(90, Math.max(40, t));
  if (movePct) {
    S.pct = turnoutPct(S.pct, t - turnout, TURNOUT_SLOPES);
    if (S.pctSenate) S.pctSenate = turnoutPct(S.pctSenate, t - turnout, TURNOUT_SLOPES);
  }
  turnout = t;
  applyTurnout();
  try { localStorage.setItem('e29n.turnout', String(t)); } catch { /* private mode: not remembered */ }
  $('#turnout').value = t.toFixed(1).replace('.', ',');
  mcKey = '';
}
$('#turnout').value = turnout.toFixed(1).replace('.', ',');
$('#turnout').title = `Participación prevista ${BASE_TURNOUT.toFixed(1).replace('.', ',')} % (media de las últimas generales por provincia). Al cambiarla se mueve la de cada provincia y, según lo aprendido de elecciones pasadas, los partidos: por cada punto más, ` +
  Object.entries(TURNOUT_SLOPES).map(([id, v]) => `${byId[id]?.short ?? byId[id]?.name ?? id} ${v > 0 ? '+' : ''}${v.toFixed(2).replace('.', ',')}`).join(', ') + ' puntos.';
$('#turnout').addEventListener('change', (e) => { const v = parsePct(e.target.value); if (v != null) setTurnout(v); else e.target.value = turnout.toFixed(1).replace('.', ','); update(); });
$('#turnout-reset').addEventListener('click', () => { setTurnout(BASE_TURNOUT); update(); });
$('#reset').addEventListener('click', () => { presetSel.value = 'media'; S.pct = pollAverage(); S.pctSenate = senateVoteAvg; S.touched.clear(); update(); });

// Re-reads the poll files after server.py rewrote them. Alliances and overrides stay; the average replaces the
// working numbers only when the user was on "Media ponderada" (typed or preset numbers are theirs).
function applyPolls(newPolls, newSenate) {
  const wasMedia = presetSel.value === 'media';
  polls = newPolls;
  senatePolls = newSenate;
  SIGMA = pollSigma(polls, parties, sigmaMode);
  senateVoteAvg = senateVoteOf(senatePolls);
  buildPresets();
  if (wasMedia) S.pct = pollAverage();
  syncPreset();
  mcKey = ''; // a Monte Carlo run from the old polls is stale
  update();
}
const updBtn = $('#update-polls');
updBtn.hidden = !['localhost', '127.0.0.1'].includes(location.hostname); // needs server.py: hidden on the public site
const updStatus = $('#poll-status');
updBtn.addEventListener('click', async () => {
  updBtn.disabled = true;
  updStatus.textContent = 'Actualizando encuestas (Wikipedia, puede tardar un minuto)…';
  try {
    const res = await fetch('api/update-polls', { method: 'POST' });
    const out = await res.json().catch(() => null); // python -m http.server answers 501 with HTML
    if (!res.ok || !out?.ok) throw new Error(out?.error ?? (res.status === 404 || res.status === 501 ? 'Arranca la app con: python server.py' : `Error ${res.status}`));
    const [p, sp] = await Promise.all(['data/polls.json', 'data/polls_senate.json'].map((u) => fetch(u, { cache: 'reload' }).then((x) => x.json())));
    applyPolls(p, sp);
    updStatus.textContent = `Encuestas actualizadas: ${p.polls.length} encuestas, última ${p.polls[0]?.fieldwork_end ?? '—'}`;
  } catch (e) {
    updStatus.textContent = e.message;
  } finally {
    updBtn.disabled = false;
  }
});

// ---------- chamber tabs, share link, CSV ----------
const tabs = document.querySelectorAll('.tabs [role="tab"]');
const paintTabs = () => tabs.forEach((t) => t.setAttribute('aria-selected', String(t.dataset.view ? S.view === 'escenarios' : S.view === 'hemi' && t.dataset.chamber === S.chamber)));
tabs.forEach((t) => t.addEventListener('click', () => { S.view = t.dataset.view ?? 'hemi'; if (t.dataset.chamber) S.chamber = t.dataset.chamber; paintTabs(); update(); }));
paintTabs();

$('#share-btn').addEventListener('click', async () => {
  flash((await copyText(location.href)) ? 'Enlace del escenario copiado.' : 'Copia el enlace de la barra de direcciones.');
});
function resetScenario() {
  setTurnout(BASE_TURNOUT, false);
  Object.assign(S, { pct: pollAverage(), pctSenate: senateVoteAvg, alliances: defaultAlliances(), pact: [], overrides: {}, selected: null });
  S.touched.clear();
  syncPreset();
}
$('#reset-all').addEventListener('click', () => {
  if (!confirm('¿Restablecer todo el escenario (encuestas, coaliciones y pacto)?')) return;
  resetScenario();
  update();
});

// ---------- escenarios: saved copies of the working state (the working state itself is autosaved in render()) ----------
const current = () => ({ pct: S.pct, alliances: S.alliances, pact: S.pact, overrides: S.overrides });
// Deshacer/rehacer: render() records every change of current() (duplicates are ignored), so restore() does not push by itself.
const hist = createHistory(50);
hist.clear(current());
const restore = (st) => {
  if (!st) return;
  Object.assign(S, st);
  syncPreset();
  update();
};
const undo = () => { hist.push(current()); restore(hist.undo()); }; // push first: a change whose render is still pending must be on the stack
const redo = () => { hist.push(current()); restore(hist.redo()); };
$('#undo-btn').addEventListener('click', undo);
$('#redo-btn').addEventListener('click', redo);
const snap = (st) => JSON.stringify(cleanState(st, allIds)); // compare as stored, so cleaning does not look like a change
const runFor = (st) => simulate({ nationalPct: st.pct, parties, provinces, alliances: forChamber(st.alliances, 'congreso'), overrides: st.overrides });
const BASE_SEATS = runFor({ pct: pollAverage(), alliances: { national: [], provincial: {} } }).national.seats; // encuestas sin coaliciones: base del Δ
const consInfo = (st) => { const r = runFor(st); return { r, cons: consequences(r, BASE_SEATS, st.pact) }; };
const consHtml = (c) => `<div class="scen-cons">Izquierda <b>${c.blocs.izquierda}</b> · Derecha <b>${c.blocs.derecha}</b>${c.largest ? ` · Mayor: ${esc(c.largest.name)} (${c.largest.seats})` : ''}
  <br>Δ vs encuestas sin coaliciones: ${c.delta.slice(0, 4).map((x) => `${esc(x.name)} <b class="delta ${x.d > 0 ? 'up' : 'down'}">${x.d > 0 ? '+' : ''}${x.d}</b>`).join(' · ') || '—'}
  <div class="con">${esc(c.line)}</div></div>`;
const loadScenario = (sc) => {
  Object.assign(S, { pct: clone(sc.pct), alliances: clone(sc.alliances), pact: clone(sc.pact), overrides: clone(sc.overrides), loaded: sc.id });
  S.touched.clear();
  syncPreset();
};
const loadedSc = () => scenarios.find((s) => s.id === S.loaded) ?? null;
const saveScenarios = () => store.set('e29n.scenarios', scenarios);
const defaultName = () => `Escenario ${scenarios.length + 1}`;

// Comparar: A y B son 'actual' o un id de escenario. shown: la tabla está visible.
const cmp = { a: 'actual', b: null, shown: false };
const pickOf = (key) => `<span class="pick"><label>A <input type="radio" name="cmp-a" value="${key}"${cmp.a === key ? ' checked' : ''}></label> <label>B <input type="radio" name="cmp-b" value="${key}"${cmp.b === key ? ' checked' : ''}></label></span>`;
const wname = (r, id) => (id ? r.contenders[id]?.name ?? id : '—');
function renderCompare() {
  const box = $('#scen-cmp');
  if (!cmp.shown) return (box.innerHTML = '');
  const stA = stateOfKey(cmp.a), stB = cmp.b && stateOfKey(cmp.b);
  if (!stA || !stB) return (box.innerHTML = '<p class="hint">Elige A y B para comparar.</p>');
  const rA = runFor(stA), rB = runFor(stB);
  const bA = consequences(rA, BASE_SEATS, stA.pact), bB = consequences(rB, BASE_SEATS, stB.pact);
  const { seats, flips } = diffResults(rA, rB);
  const share = (r, id) => `${f1.format((100 * (r.national.votes[id] || 0)) / sumOf(r.national.votes))} %`;
  const color = (id) => rA.contenders[id]?.color ?? rB.contenders[id]?.color ?? '#999';
  const blocs = (c) => `${c.blocs.izquierda} / ${c.blocs.derecha} / ${c.blocs.otros}`;
  const dBlocs = ['izquierda', 'derecha', 'otros'].map((k) => bB.blocs[k] - bA.blocs[k]).map((d) => `${d > 0 ? '+' : ''}${d}`).join(' / ');
  box.innerHTML = `<table class="mc-table"><thead><tr><th>Candidatura</th><th>% A</th><th>% B</th><th>Escaños A</th><th>Escaños B</th><th>Δ</th></tr></thead><tbody>
    ${seats.map((x) => `<tr><td>${sw(color(x.id))}${esc(x.name)}</td><td>${x.a ? share(rA, x.id) : '—'}</td><td>${x.b ? share(rB, x.id) : '—'}</td><td>${x.a}</td><td>${x.b}</td><td><b class="delta ${x.d > 0 ? 'up' : x.d < 0 ? 'down' : ''}">${x.d > 0 ? '+' : ''}${x.d}</b></td></tr>`).join('')}
    <tr><td>Bloques (Izq. / Der. / Otros)</td><td></td><td></td><td>${blocs(bA)}</td><td>${blocs(bB)}</td><td>${dBlocs}</td></tr>
  </tbody></table>
  <div class="scen-cons"><b>A:</b> ${esc(bA.line)}<br><b>B:</b> ${esc(bB.line)}</div>
  <h3 class="cmp-h">Provincias que cambian de ganador (${flips.length})</h3>
  ${flips.length ? `<ul class="flips">${flips.map((f) => `<li><button type="button" data-flip="${esc(f.prov)}">${esc(provById[f.prov]?.name ?? f.prov)}: ${esc(wname(rA, f.a))} → ${esc(wname(rB, f.b))}</button></li>`).join('')}</ul>` : '<p class="hint">Ninguna provincia cambia de ganador.</p>'}`;
}
$('#cmp-btn').addEventListener('click', () => { cmp.shown = true; renderCompare(); });
$('#scen-panel').addEventListener('change', (e) => {
  if (e.target.name !== 'cmp-a' && e.target.name !== 'cmp-b') return;
  cmp[e.target.name === 'cmp-a' ? 'a' : 'b'] = e.target.value;
  renderScenarios();
});
$('#scen-cmp').addEventListener('click', (e) => { // clic en provincia: carga B (si es escenario guardado) y la selecciona en Congreso
  const prov = e.target.closest('[data-flip]')?.dataset.flip;
  if (!prov) return;
  const sc = scenarios.find((s) => s.id === cmp.b);
  if (sc) loadScenario(sc);
  Object.assign(S, { view: 'hemi', chamber: 'congreso' });
  select(prov);
});

function renderScenarios() {
  const sc = loadedSc();
  $('#scen-now').innerHTML = `<b>Estado actual</b>${pickOf('actual')}${consHtml(consInfo(current()).cons)}`;
  const modified = !!sc && snap(sc) !== snap(current());
  $('#scen-loaded').innerHTML = sc ? `Cargado: <b>${esc(sc.name)}</b>${modified ? ' <span class="badge">modificado</span>' : ''}`
    : '<span class="muted">Ningún escenario guardado cargado</span>';
  $('#scen-update').hidden = !modified;
  $('#scen-list').innerHTML = scenarios.length ? scenarios.map((s) => {
    const { r, cons } = consInfo(s);
    const top = Object.entries(r.national.seats).filter(([, n]) => n > 0).sort(([, a], [, b]) => b - a).slice(0, 4)
      .map(([id, n]) => `${sw(r.contenders[id].color)}${esc(r.contenders[id].name)} <b>${n}</b>`).join(' · ');
    const name = S.renaming === s.id ? `<input data-rename="${s.id}" value="${esc(s.name)}" aria-label="Nuevo nombre">` : `<b>${esc(s.name)}</b>`;
    return `<div class="scen${s.id === S.loaded ? ' loaded' : ''}" data-id="${s.id}">
      <div class="scen-main">${name} ${pickOf(s.id)} <span class="muted">${new Date(s.created).toLocaleDateString('es-ES')}</span>
        <div class="scen-sum">${top}</div>${consHtml(cons)}</div>
      <div class="scen-btns">
        <button type="button" data-act="load">Cargar</button>
        <button type="button" data-act="dup">Duplicar</button>
        <button type="button" data-act="ren">Renombrar</button>
        <button type="button" data-act="del" class="danger">Borrar</button>
      </div></div>`;
  }).join('') : '<p class="hint">Aún no hay escenarios guardados.</p>';
  const ren = $('[data-rename]');
  ren?.focus();
  ren?.select();
  renderCompare();
  renderOdds();
}

// Horquillas por escenario. Cache por escenario: vale mientras su pct/coaliciones/pacto no cambien.
const ODDS_RUNS = 1000;
const ODDS_SLICE = 300; // runs per setTimeout turn, so the tab stays responsive
const odds = new Map(); // 'actual' or scenario id -> {key, res}
let oddsToken = 0;
const keyOf = (st) => JSON.stringify([st.pct, st.alliances, st.pact, st.overrides]);
const modelOf = (st) => ({ nationalPct: st.pct, parties, provinces, alliances: forChamber(st.alliances, 'congreso'), overrides: st.overrides, pact: st.pact });
const stateOfKey = (key) => (key === 'actual' ? current() : scenarios.find((s) => s.id === key) ?? null);
const NOTE_IC = 'Intervalo del 97,5 %: ruido proporcional al voto de cada partido según el error histórico de la media de encuestas (elecciones 2018-2026), centrado en los valores del escenario.';
const MAIN_IDS = ['pp', 'psoe', 'vox', 'fa'];
const horq = (s) => `${s.lo}–${s.hi} (${s.p50})`;

function renderOdds() {
  const rows = [{ key: 'actual', name: 'Actual', st: current() }, ...scenarios.map((s) => ({ key: s.id, name: s.name, st: s }))]
    .map((x) => ({ ...x, o: odds.get(x.key)?.key === keyOf(x.st) ? odds.get(x.key).res : null }));
  const extra = rows.flatMap((x) => (x.o ? Object.keys(x.o.seats).filter((id) => x.o.seats[id].hi >= 5) : []));
  const cols = [...new Set([...MAIN_IDS, ...extra])];
  $('#scen-odds').innerHTML = `<table class="mc-table odds"><thead><tr><th>Escenario</th>${cols.map((id) => `<th>${contenderLabel(id)}</th>`).join('')}<th>P(pacto ≥ ${MAJORITY})</th></tr></thead><tbody>
    ${rows.map(({ key, name, o }) => `<tr${key === 'actual' ? '' : ` data-id="${key}"`}><td>${esc(name)}</td>${o
      ? `${cols.map((id) => `<td>${horq(o.seats[id] ?? { lo: 0, p50: 0, hi: 0 })}</td>`).join('')}<td>${o.pact == null ? '—' : pc(o.pact)}</td>`
      : `<td colspan="${cols.length + 1}" class="muted">Sin calcular</td>`}</tr>`).join('')}
  </tbody></table><p class="hint">${NOTE_IC}</p>`;
}

// Runs every scenario whose cache is missing or stale, sequentially. Changing one scenario drops only its own run.
function calcOdds() {
  const token = ++oddsToken;
  const todo = [{ key: 'actual', st: current() }, ...scenarios.map((s) => ({ key: s.id, st: s }))]
    .map((x) => ({ ...x, sig: keyOf(x.st) }))
    .filter((x) => odds.get(x.key)?.key !== x.sig);
  let i = 0, gen = null, maps = [];
  const step = () => {
    if (token !== oddsToken) return;
    if (i >= todo.length) { $('#odds-prog').textContent = todo.length ? 'Listo.' : 'Ya estaba todo calculado.'; return; }
    const job = todo[i];
    const now = stateOfKey(job.key);
    if (!now || keyOf(now) !== job.sig) { i++; gen = null; maps = []; return setTimeout(step, 0); }
    gen ??= mcRuns(modelOf(job.st), ODDS_RUNS, mulberry32(29), SIGMA);
    for (let k = 0; k < ODDS_SLICE && maps.length < ODDS_RUNS; k++) maps.push(gen.next().value.c);
    $('#odds-prog').textContent = `Escenario ${i + 1} de ${todo.length}: ${maps.length} de ${ODDS_RUNS} simulaciones`;
    if (maps.length < ODDS_RUNS) return setTimeout(step, 0);
    odds.set(job.key, { key: job.sig, res: summarize(maps, job.st.pact) });
    i++; gen = null; maps = [];
    renderOdds();
    setTimeout(step, 0);
  };
  step();
}
$('#odds-btn').addEventListener('click', calcOdds);
$('#scen-odds').addEventListener('click', (e) => {
  const sc = scenarios.find((s) => s.id === e.target.closest('tr[data-id]')?.dataset.id);
  if (!sc) return;
  loadScenario(sc);
  update();
});

$('#scen-new').addEventListener('click', () => { resetScenario(); S.loaded = null; update(); });
$('#scen-save').addEventListener('click', () => {
  const name = prompt('Nombre del escenario:', defaultName());
  if (name === null) return;
  const sc = newScenario(current(), name.trim() || defaultName());
  scenarios.push(sc);
  S.loaded = sc.id;
  saveScenarios();
  update();
});
$('#scen-update').addEventListener('click', () => {
  const sc = loadedSc();
  if (!sc) return;
  Object.assign(sc, clone(current()));
  saveScenarios();
  update();
});
$('#scen-clear').addEventListener('click', () => {
  if (!confirm(`¿Borrar los ${scenarios.length} escenarios guardados?`)) return;
  scenarios = [];
  S.loaded = null;
  saveScenarios();
  update();
});
$('#scen-list').addEventListener('click', (e) => {
  const act = e.target.dataset.act;
  const id = e.target.closest('.scen')?.dataset.id;
  const sc = scenarios.find((s) => s.id === id);
  if (!act || !sc) return;
  if (act === 'load') loadScenario(sc);
  else if (act === 'dup') {
    scenarios.splice(scenarios.indexOf(sc) + 1, 0, { ...clone(sc), id: newId(), name: `${sc.name} (copia)`, created: Date.now() });
  } else if (act === 'ren') {
    S.renaming = id;
  } else if (act === 'del') {
    if (!confirm(`¿Borrar el escenario «${sc.name}»?`)) return;
    scenarios = scenarios.filter((s) => s !== sc);
    if (S.loaded === id) S.loaded = null;
  }
  saveScenarios();
  update();
});
const scenList = $('#scen-list');
scenList.addEventListener('keydown', (e) => e.key === 'Enter' && e.target.matches('[data-rename]') && e.target.blur());
scenList.addEventListener('focusout', (e) => { // rename input done: blur saves it
  const sc = scenarios.find((s) => s.id === e.target.dataset.rename);
  if (!sc) return;
  const name = e.target.value.trim();
  if (name) sc.name = name.slice(0, 60);
  S.renaming = null;
  saveScenarios();
  update();
});
$('#legend').addEventListener('click', (e) => {
  const id = e.target.closest('[data-pact]')?.dataset.pact;
  if (!id) return;
  S.pact = S.pact.includes(id) ? S.pact.filter((x) => x !== id) : [...S.pact, id];
  update();
});
$('#pact').addEventListener('click', (e) => {
  if (e.target.id !== 'pact-clear') return;
  S.pact = [];
  update();
});

// Solver "¿Qué hace falta para…?" (Congreso). solveRes = {key, html, id, pct} for the state it was computed on.
$('#solve-party').innerHTML = partyIds.map((id) => `<option value="${id}">${esc(byId[id].name)}</option>`).join('');
const solveKey = () => JSON.stringify([S.pct, S.alliances, S.overrides, S.pact, S.source, S.balance]);
let solveRes = null;
function solve() {
  const id = $('#solve-party').value;
  const name = esc(byId[id].name);
  const pactMode = $('#solve-goal').value === 'pacto';
  const n = Math.round(Number($('#solve-n').value));
  if (!pactMode && !(n > 0)) { solveRes = { key: solveKey(), html: '<p class="hint">Indica un número de escaños.</p>' }; return update(); }
  const goal = pactMode
    ? `el pacto ${S.pact.map((x) => esc(R.contenders[x]?.name ?? x)).join(' + ')} llegue a ${MAJORITY}`
    : `${name} llegue a ${n} escaños`;
  const reached = pactMode ? (s) => S.pact.reduce((a, x) => a + (s[x] || 0), 0) >= MAJORITY : (s) => (s[id] || 0) >= n;
  const r = solveTarget({ pct: S.pct, source: S.source, parties, provinces, alliances: forChamber(S.alliances, 'congreso'), overrides: S.overrides }, reached, id, { hi: toStore(id, 60), step: toStore(id, 0.05) });
  const f = factor[id] ?? 1;
  const cur = shown(id);
  let html = '', pct = null;
  if (!r) html = `<p>No se consigue que ${goal} ni con 60 % de ${name}.</p>`;
  else if (r.pct * f - cur < 0.005) html = `<p>Ya se cumple con el % actual (${f2.format(cur)} %): ${goal}.</p>`;
  else {
    pct = r.pct;
    html = `<p>Hace falta que <b>${name}</b> suba de ${f2.format(cur)} % a ${f2.format(r.pct * f)} % (+${f2.format(r.pct * f - cur)} pts) para que ${goal}.</p>
      <button id="solve-apply" type="button" class="primary">Aplicar</button>`;
  }
  solveRes = { key: solveKey(), html, id, pct };
  update();
}
$('#solve-goal').addEventListener('change', () => { solveRes = null; update(); });
$('#solver').addEventListener('click', (e) => {
  if (e.target.id === 'solve-btn') solve();
  if (e.target.id !== 'solve-apply' || solveRes?.pct == null) return;
  S.pct = rebalance(S.pct, solveRes.id, solveRes.pct, S.source);
  S.touched.add(solveRes.id);
  solveRes = null;
  update();
});
function renderSolver() {
  const pactMode = $('#solve-goal').value === 'pacto';
  $('#solver').hidden = S.chamber !== 'congreso';
  $('#solve-n').hidden = pactMode;
  const hint = pactMode && !S.pact.length ? 'Elige un pacto en la leyenda.' : !S.balance ? 'Activa el autoequilibrado para calcular.' : '';
  $('#solve-hint').textContent = hint;
  $('#solve-btn').disabled = !!hint;
  if (solveRes?.key !== solveKey()) solveRes = null; // stale: the scenario changed since the calculation
  $('#solve-out').innerHTML = solveRes?.html ?? '';
}
$('#filo-list').addEventListener('click', (e) => {
  const id = e.target.closest('[data-prov]')?.dataset.prov;
  if (id) select(id);
});
const mapMode = $('#map-mode');
mapMode.innerHTML += partyIds.map((id) => `<option value="${id}">% ${esc(byId[id].name)}</option>`).join('');
mapMode.value = S.mapMode;
mapMode.addEventListener('change', () => { S.mapMode = mapMode.value; update(); });

$('#csv-btn').addEventListener('click', () => {
  const csv = csvRows({ R, SN, provinces, pact: S.pact, mc: mcRes?.c ?? null, overrides: S.overrides }).map((l) => l.map((v) => `"${String(v).replace(/"/g, '""')}"`).join(',')).join('\n');
  const url = URL.createObjectURL(new Blob([`﻿${csv}`], { type: 'text/csv;charset=utf-8' }));
  Object.assign(document.createElement('a'), { href: url, download: 'simulacion-29N.csv' }).click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});

// ---------- drag & drop (national panel and province sandbox) ----------
function onDrop(scope, src, targetParty, targetCoal) {
  if (!src || src === targetParty || !byId[src]) return;
  if (scope.kind === 'provincial' && !(R.provinces[scope.id].raw[src] > 0)) return flash('Ese partido no tiene votos en esta provincia.');
  const list = listOf(scope);
  const owner = (p) => list.find((a) => a.members.some((m) => m.party === p));
  // donors (non-host) start with one empty transfer row: their voters may also go to parties outside the pact
  const member = (party, donor = true) => ({ party, toCoalition: 100, transfers: donor ? [{ party: null, pct: 0 }] : [] });
  const srcA = owner(src);
  const tgtA = targetCoal ? list.find((a) => allianceId(a) === targetCoal) : owner(targetParty);
  if (srcA && tgtA) return srcA !== tgtA && flash('Ese partido ya está en otra coalición: quítalo o disuélvela antes.');
  const base = tgtA || srcA;
  if (base) return openAlliance(scope, base, { ...clone(base), members: [...clone(base.members), member(tgtA ? src : targetParty)] });
  openAlliance(scope, null, { host: targetParty, members: [member(targetParty, false), member(src)] });
}

function wireDnD(root, scopeOf) {
  const target = (e) => e.target.closest?.('.card[data-party], .coal[data-coal]');
  root.addEventListener('dragstart', (e) => {
    const handle = e.target.closest?.('[draggable="true"]'); // ignore text dragged out of inputs
    if (!handle) return;
    e.dataTransfer.setData('text/plain', handle.closest('[data-party]').dataset.party);
    e.dataTransfer.effectAllowed = 'move';
  });
  root.addEventListener('dragover', (e) => {
    const t = target(e);
    if (!t) return;
    e.preventDefault();
    t.classList.add('over');
  });
  root.addEventListener('dragleave', (e) => target(e)?.classList.remove('over'));
  root.addEventListener('drop', (e) => {
    const t = target(e);
    if (!t) return;
    e.preventDefault();
    t.classList.remove('over');
    onDrop(scopeOf(), e.dataTransfer.getData('text/plain'), t.dataset.party, t.dataset.coal);
  });
  root.addEventListener('click', (e) => {
    const c = e.target.closest('.coal');
    if (!c) return;
    const scope = scopeOf();
    const a = listOf(scope).find((x) => allianceId(x) === c.dataset.coal);
    if (a) openAlliance(scope, a, clone(a));
  });
}
wireDnD($('#national'), () => ({ kind: 'national' }));
wireDnD($('#province'), () => ({ kind: 'provincial', id: S.selected }));

// ---------- alliance modal ----------
const modal = $('#alliance-modal');
const membersEl = $('#am-members');
let draft = null; // {scope, original, a}

function openAlliance(scope, original, a) {
  draft = { scope, original, a };
  $('#am-scope').textContent = scope.kind === 'national'
    ? `Coalición nacional: se aplica en todas las provincias salvo donde haya una coalición provincial con alguno de sus partidos${a.except?.length ? ` y salvo en ${a.except.map((id) => provById[id]?.name ?? id).join(', ')}` : ''}.`
    : `Coalición provincial: solo en ${provById[scope.id].name}.`;
  $('#am-dissolve').hidden = !original;
  $('#am-chamber').value = a.chamber ?? 'ambos';
  modal.returnValue = '';
  renderMembers();
  modal.showModal();
}

function candidates() {
  const inside = new Set(draft.a.members.map((m) => m.party));
  const raw = draft.scope.kind === 'provincial' ? R.provinces[draft.scope.id].raw : null;
  const weight = (id) => (raw ? raw[id] || 0 : S.pct[id] || 0);
  return parties.map((p) => p.party_id).filter((id) => !inside.has(id) && (!raw || raw[id] > 0)).sort((a, b) => weight(b) - weight(a));
}

function renderMembers() {
  const cands = candidates();
  const opts = (sel) => cands.map((p) => `<option value="${p}"${p === sel ? ' selected' : ''}>${esc(byId[p].name)}</option>`).join('');
  $('#am-title').textContent = coalitionName(draft.a);
  membersEl.innerHTML = draft.a.members.map((m, i) => {
    m.transfers = (m.transfers || []).map((t) => ({ ...t, party: t.party ?? cands[0] })).filter((t) => cands.includes(t.party)); // a target that joined the pact is dropped
    const host = m.party === draft.a.host;
    return `<div class="member" data-i="${i}">
      <div class="member-head">${sw(byId[m.party].color)}<b>${esc(byId[m.party].name)}</b>${host ? ' <span class="muted">(encabeza)</span>' : '<button type="button" data-act="rm-member">Quitar</button>'}</div>
      <span class="abst"></span>
      <label>Votantes de ${esc(byId[m.party].name)} a la coalición (%) <input class="pct" data-f="to" inputmode="decimal" value="${fmtIn(m.toCoalition)}"></label>
      ${m.transfers.map((t, j) => `<div class="transfer" data-j="${j}">→ <select aria-label="Trasvase a">${opts(t.party)}</select>
        <input class="pct" data-f="tv" inputmode="decimal" value="${fmtIn(t.pct)}" aria-label="% trasvase"> %
        <button type="button" data-act="rm-transfer" aria-label="Quitar trasvase">×</button></div>`).join('')}
      <button type="button" data-act="add-transfer"${cands.length ? '' : ' disabled'}>+ votantes a otro partido</button>
    </div>`;
  }).join('');
  readDraft();
}

function readDraft() {
  let bad = false;
  let neg = false;
  const num = (input) => {
    const v = parsePct(input.value);
    input.classList.toggle('invalid', v === null);
    bad ||= v === null;
    return v ?? 0;
  };
  for (const el of membersEl.querySelectorAll('.member')) {
    const m = draft.a.members[+el.dataset.i];
    m.toCoalition = num(el.querySelector('[data-f=to]'));
    el.querySelectorAll('.transfer').forEach((t, j) => (m.transfers[j] = { party: t.querySelector('select').value, pct: num(t.querySelector('[data-f=tv]')) }));
    const ab = 100 - m.toCoalition - m.transfers.reduce((s, t) => s + t.pct, 0);
    const span = el.querySelector('.abst');
    span.textContent = `Abstención: ${fmtIn(ab)} %`;
    span.classList.toggle('neg', ab < -1e-9);
    neg ||= ab < -1e-9;
  }
  const err = bad ? 'Hay porcentajes no válidos (0-100, máximo 2 decimales).'
    : neg ? 'La abstención no puede ser negativa: reduce la coalición o los trasvases.'
    : validateAlliance(draft.a);
  $('#am-error').textContent = err || '';
  $('#am-ok').disabled = !!err;
}

membersEl.addEventListener('input', readDraft);
membersEl.addEventListener('change', readDraft);
membersEl.addEventListener('click', (e) => {
  const act = e.target.dataset.act;
  if (!act) return;
  const i = +e.target.closest('.member').dataset.i;
  const m = draft.a.members[i];
  if (act === 'add-transfer') m.transfers.push({ party: candidates()[0], pct: 0 });
  if (act === 'rm-transfer') m.transfers.splice(+e.target.closest('.transfer').dataset.j, 1);
  if (act === 'rm-member') draft.a.members.splice(i, 1);
  renderMembers();
});
modal.addEventListener('close', () => {
  if (!draft) return; // a late close event after an already-applied one
  const { scope, original, a } = draft;
  const list = listOf(scope);
  const idx = list.indexOf(original);
  if (modal.returnValue === 'ok') {
    a.id = allianceId(a);
    a.chamber = $('#am-chamber').value;
    if (idx >= 0) list[idx] = a;
    else list.push(a);
  }
  if (modal.returnValue === 'dissolve' && idx >= 0) list.splice(idx, 1);
  draft = null;
  update();
});

// ---------- methodology ----------
$('#method-btn').addEventListener('click', () => {
  const dl = (m) => `<dl>${Object.entries(m || {}).map(([k, v]) =>
    `<dt>${esc(k)}</dt><dd>${typeof v === 'object' ? `<pre>${esc(JSON.stringify(v, null, 1))}</pre>` : esc(v)}</dd>`).join('')}</dl>`;
  const ps = [...(polls.pollsters || [])].sort((a, b) => (b.weight ?? 0) - (a.weight ?? 0));
  const n = (f, x) => (x == null ? '—' : f.format(x));
  $('#method-body').innerHTML = `<h2>Metodología</h2>
    ${placeholder ? '<p class="badge">Hay ficheros de datos provisionales (placeholder): los resultados no son una estimación real.</p>' : ''}
    <h3>Motor del simulador</h3>
    <p>1) Cada partido parte de su % nacional × su elasticidad provincial; un ajuste proporcional iterativo hace que cada provincia sume 100 % y que la media nacional ponderada por voto válido esperado coincida con el % introducido.
    2) Las coaliciones reparten el voto de cada miembro entre la coalición, trasvases a otros partidos y abstención.
    3) Escaños por D'Hondt con barrera del 3 % del voto válido de la provincia (Otros/Blanco cuenta para la barrera pero nunca obtiene escaño).
    4) Senado: en cada circunscripción cada candidatura presenta min(marcas, escaños) candidatos con sus votos provinciales; ganan los escaños con más votos (voto limitado). Las islas comparten los votos de su provincia. Los designados por las CCAA (${senateData.designated.total}) son fijos por partido según senate.json.
    Voto útil: el 12 % del voto de Vox/SALF marca a los candidatos del PP (ajustado con los votos reales de Congreso y los senadores reales de 2016, abr-2019, nov-2019 y 2023: el error cuadrático de PP+PSOE baja de 1365 a 192). Por defecto, PSOE + Frente Amplio + Compromís van en una sola lista al Senado salvo en Cataluña («Senado de Progreso»).
    5) Incertidumbre: 1000 simulaciones con ruido nacional proporcional al voto de cada partido (σ = r × %, con r el error relativo histórico de la media de encuestas en 40 elecciones 2018-2026: recta final o a la distancia de hoy, a elegir) y ruido provincial; el Senado añade ruido propio. El «esperado» (media) es la mejor estimación puntual: el reparto determinista da al primero los escaños al filo. «Probabilidad de un escenario» cuenta en qué fracción de esas simulaciones se cumplen tus condiciones.</p>
    <h3>Elasticidades provinciales</h3><p class="muted">Generado: ${esc(elast.generated ?? '—')}</p>${dl(elast.method)}
    <h3>Media de encuestas</h3><p class="muted">Generado: ${esc(polls.generated ?? '—')}</p>${dl(polls.method)}
    <h3>Encuestadoras</h3>${ps.length ? `<table class="pollsters"><thead><tr><th>Encuestadora</th><th>Peso</th><th>MAE</th><th>Elecciones</th><th>Último campo</th></tr></thead><tbody>
      ${ps.map((p) => `<tr><td>${esc(p.name)}</td><td>${n(f3, p.weight)}</td><td>${n(f2, p.mae)}</td><td>${p.n_elections ?? '—'}</td><td>${esc(p.last_fieldwork ?? '—')}</td></tr>`).join('')}
    </tbody></table>` : '<p class="muted">Sin datos de encuestadoras todavía.</p>'}`;
  $('#method-modal').showModal();
});

// Atajos: Esc sale del panel ampliado o cierra la provincia; Ctrl+Z/Ctrl+Shift+Z/Ctrl+Y deshacer/rehacer; 1-3 cambian de pestaña. Fuera de campos y diálogos.
const tabEls = document.querySelectorAll('.tabs [role="tab"]');
// Ampliar: any panel can fill the window to work on it; ⤢ / Esc / "Volver" bring it back.
const maxPanel = (panel) => {
  const open = document.querySelector('.panel.max');
  if (open) { open.classList.remove('max'); open.querySelector('.max-btn').textContent = '⤢'; open.querySelector('.max-btn').title = 'Ampliar a pantalla completa (F)'; }
  if (panel && panel !== open) { panel.classList.add('max'); panel.querySelector('.max-btn').textContent = '✕ Volver'; panel.querySelector('.max-btn').title = 'Volver (Esc o F)'; }
  document.body.classList.toggle('has-max', !!document.querySelector('.panel.max'));
};
for (const panel of document.querySelectorAll('main .panel')) {
  panel.insertAdjacentHTML('afterbegin', '<button type="button" class="max-btn" title="Ampliar a pantalla completa (F)">⤢</button>');
  panel.querySelector('.max-btn').addEventListener('click', () => maxPanel(panel));
}
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && document.querySelector('.panel.max') && !document.querySelector('dialog[open]')) return maxPanel(null);
  if (e.key === 'Escape' && S.selected && !document.querySelector('dialog[open]')) select(null);
  if (e.target.closest?.('input, textarea, select') || document.querySelector('dialog[open]')) return;
  const mod = e.ctrlKey || e.metaKey;
  const k = e.key.toLowerCase();
  if (mod && k === 'z') { e.preventDefault(); e.shiftKey ? redo() : undo(); }
  else if (mod && k === 'y') { e.preventDefault(); redo(); }
  else if (!mod && !e.altKey && k === 'f') maxPanel(document.querySelector('.panel.max') ?? document.querySelector('main .panel:hover'));
  else if (!mod && !e.altKey && tabEls[+e.key - 1]) tabEls[+e.key - 1].click();
});
// Provincial % overrides: a typed party value fixes it (rest renormalized in the engine); × or "Restablecer provincia" undo it.
const setOv = (prov, ov) => (Object.keys(ov).length ? (S.overrides[prov] = ov) : delete S.overrides[prov]);
$('#province').addEventListener('click', (e) => {
  const row = e.target.closest('tr[data-map]');
  if (row?.dataset.map) { S.mapMode = S.mapMode === row.dataset.map ? 'ganador' : row.dataset.map; mapMode.value = S.mapMode; return update(); }
  const { act, ovReset } = e.target.dataset;
  if (act === 'close') return select(null);
  if (act === 'reset-prov') setOv(S.selected, {});
  else if (ovReset) { const ov = { ...S.overrides[S.selected] }; delete ov[ovReset]; setOv(S.selected, ov); }
  else return;
  update();
});
$('#province').addEventListener('keydown', (e) => e.key === 'Enter' && e.target.matches('[data-ov]') && e.target.blur());
$('#province').addEventListener('change', (e) => {
  const input = e.target;
  const id = input.dataset.ov;
  if (!id) return;
  const v = parsePct(input.value);
  input.classList.toggle('invalid', v === null);
  if (v === null) return;
  const ov = { ...S.overrides[S.selected], [id]: v };
  if (!overridesFit(ov)) { input.classList.add('invalid'); return flash('Los porcentajes fijados en esta provincia suman más de 100 %.'); }
  const est = (100 * (R.provinces[S.selected].raw[id] || 0)) / provById[S.selected].expected_census;
  if (v !== round2(est) || S.overrides[S.selected]?.[id] !== undefined) setOv(S.selected, ov);
  update();
});

// ---------- render ----------
function renderNational() {
  for (const input of cardsEl.querySelectorAll('input')) {
    const id = input.dataset.party;
    input.closest('.card').classList.toggle('changed', S.touched.has(id));
    if (input === document.activeElement) continue;
    input.value = fmtIn(shown(id, natPct()));
    input.classList.remove('invalid');
  }
  $('#otros-out').textContent = fmtIn(natPct().otros);
  const total = partyIds.reduce((a, id) => a + natPct()[id], natPct().otros);
  const over = total > 100.005;
  $('#total').textContent = `Total: ${f2.format(total)} %${over ? ' · supera 100, el motor normaliza' : ''}`;
  $('#total').classList.toggle('over', over);
  const ovNote = $('#ov-note');
  ovNote.hidden = !Object.keys(S.overrides).length;
  if (!ovNote.hidden) {
    const nat = nationalShares(R, provinces);
    const moved = [...partyIds, 'otros'].filter((id) => Math.abs((nat[id] ?? 0) - S.pct[id]) > 0.1);
    ovNote.textContent = `Con ajustes provinciales: % nacional resultante ${moved.map((id) => `${byId[id].name} ${f2.format(nat[id] ?? 0)} % (entrada ${f2.format(S.pct[id])} %)`).join(' · ') || '(ningún partido cambia más de 0,1 pts)'}`;
  }
  $('#nat-coalitions').innerHTML = coalitionCards(S.alliances.national)
    || '<span class="hint">Arrastra un partido sobre otro para formar una coalición nacional.</span>';
}

function renderHemi() {
  const c = CHAMBER[S.chamber];
  const seatsMap = S.chamber === 'congreso' ? R.national.seats : SN.seats;
  const list = Object.entries(seatsMap).filter(([id, s]) => s > 0 && R.contenders[id])
    .map(([id, seats]) => ({ ...R.contenders[id], seats }))
    .sort((a, b) => a.order - b.order || a.id.localeCompare(b.id));
  hemi.update(list, c.total, c.majority);
  const blocs = blocSeats(seatsMap);
  const at = (n) => `${(100 * n / c.total).toFixed(2)}%`;
  $('#blocs').innerHTML = `<div class="bloc-bar" role="img" aria-label="Izquierda ${blocs.izquierda}, Derecha ${blocs.derecha}, Otros ${blocs.otros} de ${c.total}">`
    + `<i class="bloc-izq" style="width:${at(blocs.izquierda)}"></i><i class="bloc-der" style="width:${at(blocs.derecha)}"></i><i class="bloc-otros" style="width:${at(blocs.otros)}"></i>`
    + `<em style="left:${at(c.majority)}" title="Mayoría absoluta ${c.majority}"></em></div>`
    + `<div class="bloc-sum">Izquierda <b>${blocs.izquierda}</b> · Derecha <b>${blocs.derecha}</b> · Otros <b>${blocs.otros}</b> · mayoría ${c.majority}</div>`;
  const top = list.reduce((a, b) => (b.seats > a.seats ? b : a), list[0]);
  const seated = list.reduce((a, x) => a + x.seats, 0);
  $('#hemi-title').textContent = c.title;
  $('#hemi-sub').textContent = `${c.total} escaños · mayoría absoluta ${c.majority}`;
  const parts = S.chamber === 'senado'
    ? `<span class="sum">Elegidos ${sumOf(SN.elected)}</span>${chips(SN.elected, { pick: true })}<span class="sum">Designados ${sumOf(SN.designated)}</span>${chips(SN.designated, { cls: ' desig', pick: true })}`
    : chips(R.national.seats, { delta: true, pick: true });
  $('#legend').innerHTML = `${parts}<span class="sum">Mayor: ${esc(top?.name ?? '—')} (${top?.seats ?? 0}) · ${seated} de ${c.total} escaños asignados</span>`;
}

// Encuestas del Senado (escaños) frente a nuestra proyección, con Δ. Solo en la pestaña Senado y si hay encuestas de escaños.
function renderSenPolls() {
  const el = $('#sen-polls');
  const { polls: list, average } = senatePolls;
  const avg = average?.seats ?? {};
  el.hidden = S.chamber !== 'senado' || !Object.keys(avg).length;
  if (el.hidden) return;
  const ours = SN.seats;
  const exp = mcRes?.s.seats;
  const ids = [...new Set([...Object.keys(avg), ...Object.keys(ours)])].filter((id) => (ours[id] ?? 0) > 0 || (avg[id] ?? 0) > 0)
    .sort((a, b) => (avg[b] ?? 0) - (avg[a] ?? 0) || (ours[b] ?? 0) - (ours[a] ?? 0));
  const signed = (d) => `${d > 0 ? '+' : ''}${f1.format(d)}`;
  el.innerHTML = `<h3>Encuestas del Senado (escaños)</h3>
    <div class="wide"><table class="pollsters"><thead><tr><th>Partido</th><th>Nuestro</th>${exp ? '<th>Esperado</th>' : ''}<th>Media</th><th>Δ</th>${list.map((p) => `<th>${esc(p.pollster)}<br><small>${p.fieldwork_end}</small></th>`).join('')}</tr></thead>
    <tbody>${ids.map((id) => `<tr><th>${contenderLabel(id)}</th><td>${f0.format(ours[id] ?? 0)}</td>${exp ? `<td><b>${f1.format(exp[id]?.mean ?? 0)}</b></td>` : ''}<td>${avg[id] == null ? '—' : f1.format(avg[id])}</td><td>${avg[id] == null ? '—' : signed((exp ? exp[id]?.mean ?? 0 : ours[id] ?? 0) - avg[id])}</td>${list.map((p) => `<td>${p.seats[id] == null ? '—' : f1.format(p.seats[id])}</td>`).join('')}</tr>`).join('')}</tbody></table></div>
    <p class="hint">Fuentes: ${list.map((p) => `<a href="${esc(p.source_url)}" target="_blank" rel="noopener">${esc(p.pollster)} · ${p.fieldwork_end}</a>`).join(' · ')}. Escaños = punto medio de las horquillas publicadas. Media ponderada por fiabilidad y recencia (semivida 14 días).
    «Nuestro» es el reparto determinista; ${exp ? '«Esperado» es la media de 1000 simulaciones (Δ se calcula sobre él)' : 'pulsa «Incertidumbre» para ver el esperado (media de 1000 simulaciones), más fiable en el Senado'}.
    Nuestro Senado incluye el voto útil (12 % del voto de Vox/SALF marca al PP, ajustado con 2016-2023) y el pacto «Senado de Progreso».</p>`;
}

// Pactómetro: sum of the seats of the parties picked in the legend vs the absolute majority.
function renderPact() {
  const c = CHAMBER[S.chamber];
  const seatsMap = S.chamber === 'congreso' ? R.national.seats : SN.seats;
  const sum = S.pact.reduce((a, id) => a + (seatsMap[id] || 0), 0);
  const ok = sum >= c.majority;
  $('#pact').innerHTML = S.pact.length
    ? `<div class="pact-head"><b>Pacto: ${S.pact.map((id) => contenderLabel(id)).join(' + ')}</b><button type="button" id="pact-clear">Limpiar pacto</button></div>
       <div class="pact-bar" role="img" aria-label="${sum} de ${c.majority} escaños"><i style="width:${(100 * sum / c.total).toFixed(2)}%"></i><em style="left:${(100 * c.majority / c.total).toFixed(2)}%"></em></div>
       <div class="pact-sum ${ok ? 'ok' : 'no'}">${sum} de ${c.majority} escaños · Mayoría absoluta ${ok ? '✔' : '✘'}</div>`
    : '<span class="hint">Pactómetro: pulsa los partidos de la leyenda para sumar un pacto.</span>';
}

function renderMap() {
  const mode = S.mapMode;
  if (mode === 'ganador') {
    map.update((id) => R.contenders[winnerOf(id)]?.color ?? '#ddd', S.selected);
    const won = {};
    for (const id of Object.keys(R.provinces)) { const w = winnerOf(id); if (w) won[w] = (won[w] || 0) + 1; }
    $('#map-legend').innerHTML = `<span class="muted">Provincias ganadas (${S.chamber === 'congreso' ? 'Congreso' : 'Senado'}):</span>` + Object.entries(won)
      .sort((a, b) => b[1] - a[1])
      .map(([id, n]) => `<span class="chip">${contenderLabel(id)} <b>${n}</b></span>`).join('');
    return;
  }
  const share = (id) => shareOf(R.provinces[id], mode);
  const mx = Math.max(1e-9, ...provinces.map((pr) => share(pr.id)));
  const ramp = d3.interpolateRgb('#f1f3f7', byId[mode].color);
  map.update((id) => ramp(share(id) / mx), S.selected);
  $('#map-legend').innerHTML = `<span class="ramp" style="background:linear-gradient(to right,#f1f3f7,${esc(byId[mode].color)})"></span>
    <span class="muted">${esc(byId[mode].name)}: 0 % → ${f1.format(mx)} % (máx. provincial)</span>`;
}

// Provincias en el filo: the 10 provinces where the last seat is decided by the fewest votes (Congreso only).
function renderFilo() {
  const rows = provinces.map((pr) => {
    const r = R.provinces[pr.id];
    const ls = lastSeat(r.votes, pr.seats, r.valid, NO_SEAT);
    return ls?.votesNeeded ? { pr, r, ...ls, pct: (100 * ls.votesNeeded) / r.valid } : null;
  }).filter(Boolean).sort((a, b) => a.pct - b.pct).slice(0, 10);
  $('#filo-panel').hidden = S.chamber !== 'congreso';
  $('#filo-list').innerHTML = rows.map((x) => `<li><button type="button" data-prov="${x.pr.id}">${esc(x.pr.name)}</button>
    <span class="muted">${contenderLabel(x.winner)} gana el último escaño; ${contenderLabel(x.runnerUp)} necesita +${f0.format(x.votesNeeded)} votos (${f2.format(x.pct)} % del válido)</span></li>`).join('');
}

// Sin provincia: resumen nacional por candidatura. Clic en una fila colorea el mapa por ese partido.
function renderSummary(el) {
  const valid = sumOf(R.national.votes);
  const cong = S.chamber === 'congreso';
  const seatsOn = cong ? R.national.seats : SN.seats;
  const ids = Object.keys(R.national.votes).filter((id) => R.national.votes[id] > 0)
    .sort((a, b) => (seatsOn[b] || 0) - (seatsOn[a] || 0) || R.national.votes[b] - R.national.votes[a]);
  const delta = (id) => { const d = (R.national.seats[id] || 0) - base2023(id); return `<small class="delta${d > 0 ? ' up' : d < 0 ? ' down' : ''}">${d > 0 ? '+' : ''}${d}</small>`; };
  el.innerHTML = `<div class="prov-head"><h2>Resumen nacional</h2><span class="muted">Elige una provincia en el mapa para ver su detalle · clic en una fila colorea el mapa</span></div>
    <table class="summary"><thead><tr><th></th><th>Candidatura</th><th>% voto</th><th>Votos</th><th>${cong ? '<b>Congreso</b>' : 'Congreso'}</th><th>Δ 2023</th><th>${cong ? 'Senado' : '<b>Senado</b>'}</th>${mcRes ? `<th>Esperado ${cong ? 'Congreso' : 'Senado'}</th><th>Horquilla 97,5 %</th>` : ''}</tr></thead><tbody>
    ${ids.map((id) => { const c = R.contenders[id]; const s = R.national.seats[id] || 0; return `<tr data-map="${byId[id] ? id : ''}" class="${S.mapMode === id ? 'in-pact' : ''}${c.eligible && !s ? ' below' : ''}"><td>${sw(c.color)}</td><td>${esc(c.name)}</td>
      <td>${f2.format((100 * R.national.votes[id]) / valid)} %</td><td>${f0.format(R.national.votes[id])}</td><td>${c.eligible ? `<b>${s}</b>` : '—'}</td><td>${c.eligible ? delta(id) : ''}</td><td>${c.eligible ? SN.seats[id] || 0 : '—'}</td>${mcRes ? (({ seats: ms } = mcRes[cong ? 'c' : 's']) => `<td>${ms[id] ? f1.format(ms[id].mean) : '—'}</td><td>${ms[id] ? horq(ms[id]) : '—'}</td>`)() : ''}</tr>`; }).join('')}
    </tbody></table>`;
}

function renderProvince() {
  const el = $('#province-body');
  provSelect.value = S.selected || '';
  if (!S.selected) return renderSummary(el);
  const pr = provById[S.selected];
  const r = R.provinces[S.selected];
  const seats = seatsOf(S.selected);
  const ls = S.chamber === 'congreso' ? lastSeat(r.votes, pr.seats, r.valid, NO_SEAT) : null;
  const cons =senateData.constituencies.filter((c) => c.province_id === S.selected);
  const senSeatsOf = SN.provinces[S.selected].seats;
  const cong = S.chamber === 'congreso';
  const ov = S.overrides[S.selected] ?? {};
  // % cells are the editable party shares (pre-alliance, % of the province); a coalition row lists one input per member.
  const pctInput = (p, multi) => {
    const set = p in ov;
    return `${multi ? `<small>${esc(byId[p].name)}</small> ` : ''}<span class="ov${set ? ' set' : ''}"><input inputmode="decimal" autocomplete="off" data-ov="${p}" aria-label="% ${esc(byId[p].name)} en ${esc(pr.name)}" value="${fmtIn((100 * (r.raw[p] || 0)) / pr.expected_census)}">%${set ? ` <button type="button" data-ov-reset="${p}" aria-label="Quitar ajuste de ${esc(byId[p].name)}">×</button>` : ''}</span>`;
  };
  const rows = Object.keys(r.votes).sort((a, b) => r.votes[b] - r.votes[a]).map((id) => {
    const c = R.contenders[id];
    const below = c.eligible && r.pct[id] < 3;
    const inPact = S.pact.includes(id);
    return `<tr${below ? ' class="below"' : ''}${inPact ? ' class="in-pact"' : ''}><td>${sw(c.color)}</td><td>${esc(c.name)}${below ? '<span class="tag">&lt;3%</span>' : ''}${inPact ? '<span class="tag pact-tag">Pacto</span>' : ''}</td>
      <td>${(r.coal[id] ?? [id]).map((p) => pctInput(p, !!r.coal[id])).join(' ')}${r.coal[id] ? ` <small>= ${f1.format(r.pct[id])} %</small>` : ''}</td><td>${f0.format(r.votes[id])}</td>
      <td>${cong ? `<b>${r.seats[id] || 0}</b>` : r.seats[id] || 0}</td><td>${cong ? senSeatsOf[id] || 0 : `<b>${senSeatsOf[id] || 0}</b>`}</td></tr>`;
  }).join('');
  const pactSeats = S.pact.reduce((a, id) => a + (seats[id] || 0), 0);
  const prov = S.alliances.provincial[S.selected] || [];
  const inProv = new Set(prov.flatMap((a) => a.members.map((m) => m.party)));
  const free = partyIds.filter((id) => r.raw[id] > 0 && !inProv.has(id));
  const extra = (100 * r.abstained) / pr.census;
  el.innerHTML = `
    <div class="prov-head"><h2>${esc(pr.name)}</h2>
      <span>${S.chamber === 'congreso' ? '<b>' : ''}${pr.seats} ${pr.seats === 1 ? 'diputado' : 'diputados'}${S.chamber === 'congreso' ? '</b>' : ''} · ${S.chamber === 'senado' ? '<b>' : ''}${senSeats(S.selected)} senadores${S.chamber === 'senado' ? '</b>' : ''}</span>
      <span>Abstención prevista: <b>${f1.format(100 * pr.abstention + extra)} %</b>${extra >= 0.05 ? ` <span class="muted">(+${f1.format(extra)} por coaliciones)</span>` : ''}</span>
      <span>Votos válidos: <b>${f0.format(r.valid)}</b></span>
      <button type="button" data-act="close" aria-label="Cerrar provincia">×</button></div>
    ${ls ? `<p class="last-seat">Último escaño: <b>${contenderLabel(ls.winner)}</b>${ls.runnerUp
      ? ` · ${contenderLabel(ls.runnerUp)} necesita <b>+${f0.format(ls.votesNeeded)}</b> votos (${f2.format((100 * ls.votesNeeded) / r.valid)} %)`
      : ' · sin rival cercano'}</p>` : ''}
    ${S.pact.length ? `<p class="last-seat">Pacto: <b>${pactSeats}</b> escaños en la provincia (${cong ? 'Congreso' : 'Senado'})</p>` : ''}
    ${Object.keys(ov).length ? '<p class="last-seat"><button type="button" data-act="reset-prov">Restablecer provincia</button> <span class="muted">Hay % fijados a mano en esta provincia</span></p>' : ''}
    <div class="prov-body">
      <table><thead><tr><th></th><th>Candidatura</th><th>%</th><th>Votos</th><th>Cong.</th><th>Sen.</th></tr></thead><tbody>${rows}</tbody></table>
      <div class="sandbox"><h3>Coaliciones provinciales</h3>
        <div class="coalitions">${coalitionCards(prov, r.pct)}</div>
        <p class="hint">Arrastra un partido sobre otro (o sobre una coalición) para pactar solo en ${esc(pr.name)}. Prevalece sobre las coaliciones nacionales.</p>
        <div class="cards">${free.map((id) => `<div class="card" data-party="${id}" draggable="true"><span class="handle">${sw(byId[id].color)}<span>${esc(byId[id].name)}</span></span>
          <small>${f1.format((100 * r.raw[id]) / pr.expected_census)}</small></div>`).join('')}</div>
      </div>
    </div>
    ${S.chamber === 'senado' ? `<div class="cons"><h3>Senado por circunscripción</h3>${cons.map((c) => `<div class="con">
      <b>${esc(c.name)}</b> <span class="muted">${c.seats} ${c.seats === 1 ? 'escaño' : 'escaños'} · voto limitado ${c.marks}</span>
      <div class="chips">${chips(SN.byConstituency[c.id]) || '<span class="muted">Sin escaños</span>'}</div></div>`).join('')}</div>` : ''}`;
}

let mcKey = '';
let mcRes = null; // last finished Monte Carlo: {c, s} summaries (Congreso, Senado); cleared with mcKey so nothing shows stale numbers
let mcRecs = null; // its per-run records (mcRuns), for the scenario probabilities
function render() {
  const key = JSON.stringify([S.pct, S.alliances, S.pact, S.overrides]);
  if (key !== mcKey) { // a running or finished simulation is stale when the scenario or pact changes (not on resize/selection/chamber)
    mcKey = key;
    mcToken++;
    mcRes = mcRecs = null;
    mcOut.innerHTML = '';
  }
  R = simulate({ nationalPct: S.pct, parties, provinces, alliances: forChamber(S.alliances, 'congreso'), overrides: S.overrides });
  const RS = simulate({ nationalPct: S.pctSenate ?? S.pct, parties, provinces, alliances: forChamber(S.alliances, 'senado'), overrides: S.overrides });
  R.contenders = RS.contenders = { ...R.contenders, ...RS.contenders }; // senate-only coalitions need labels in both
  SN = simulateSenate(RS, senateData, provinces);
  store.set('e29n.current', { ...current(), loaded: S.loaded, chamber: S.chamber });
  hist.push(current());
  $('#undo-btn').disabled = !hist.canUndo;
  $('#redo-btn').disabled = !hist.canRedo;
  $('#hemi-panel').hidden = S.view === 'escenarios';
  $('#scen-panel').hidden = S.view !== 'escenarios';
  renderNational();
  renderHemi();
  renderSenPolls();
  renderPact();
  renderSolver();
  renderMap();
  renderFilo();
  renderProvince();
  if (S.view === 'escenarios') renderScenarios();
  if (mcRes) showMC();
  renderProb();
  try { history.replaceState(null, '', shareHash()); } catch { /* file:// or sandboxed: the URL just stays as it is */ }
}

// ---------- incertidumbre (Monte Carlo, Congreso + Senado con el mismo sorteo) ----------
// Runs simulations in ~40 ms slices; render() bumps mcToken, which stops a run that has gone stale.
let mcToken = 0;
const mcOut = $('#mc-out');
const MC_RUNS = 1000;
const pc = (p) => `${f1.format(100 * p)} %`;
// Horquilla + esperado (media) de la cámara activa. El esperado es la mejor estimación puntual: el reparto determinista
// regala al primero los escaños al filo (sobre todo en el Senado mayoritario).
function showMC() {
  const cong = S.chamber === 'congreso';
  const res = mcRes[cong ? 'c' : 's'];
  const name = (id) => R.contenders[id]?.name ?? id;
  const rows = Object.entries(res.seats).filter(([, s]) => s.hi > 0).sort((a, b) => b[1].mean - a[1].mean || (a[0] < b[0] ? -1 : 1));
  mcOut.innerHTML = `<h3>${cong ? 'Congreso' : 'Senado'}: ${MC_RUNS} simulaciones</h3><table class="mc-table"><thead><tr><th>Candidatura</th><th>Esperado</th><th>Horquilla 97,5 % (mediana)</th></tr></thead><tbody>
    ${rows.map(([id, s]) => `<tr><td>${contenderLabel(id)}</td><td>${f1.format(s.mean)}</td><td>${horq(s)}</td></tr>`).join('')}
    </tbody></table>${res.pact == null ? '' : `<p class="pact-sum">Pacto (${S.pact.map(name).join(' + ')}): P(≥${CHAMBER[S.chamber].majority}) = ${pc(res.pact)}</p>`}
    <p class="hint">${NOTE_IC}</p>`;
}
function runMC(done) {
  const token = ++mcToken;
  const gen = mcRuns({ nationalPct: S.pct, parties, provinces, alliances: forChamber(S.alliances, 'congreso'), overrides: S.overrides }, MC_RUNS, mulberry32(29), SIGMA,
    { data: senateData, alliances: forChamber(S.alliances, 'senado') });
  const recs = [];
  const step = () => {
    if (token !== mcToken) return;
    const t0 = performance.now(); // setTimeout keeps running when the tab is in the background (rAF would stall)
    while (recs.length < MC_RUNS && performance.now() - t0 < 40) recs.push(gen.next().value);
    if (recs.length < MC_RUNS) {
      mcOut.innerHTML = `<p class="hint">Simulando… ${recs.length} de ${MC_RUNS}</p>`;
      return setTimeout(step, 0);
    }
    mcRecs = recs;
    mcRes = { c: summarize(recs.map((r) => r.c), S.pact), s: summarize(recs.map((r) => r.s), S.pact, CHAMBER.senado.majority) };
    showMC();
    renderSenPolls();
    if (!S.selected) renderProvince(); // the national summary gains the horquilla column
    renderProb();
    done?.();
  };
  step();
}
$('#mc-btn').addEventListener('click', () => runMC());
const mcMode = $('#mc-mode');
const modeTitle = () => {
  const r = sigmaMode === 'now' ? polls.method?.sigma_rel_now : polls.method?.sigma_rel_final;
  mcMode.title = `σ de cada partido = ${f0.format(100 * (r ?? 0.12))} % de su voto (error relativo histórico de la media de encuestas, elecciones 2018-2026${sigmaMode === 'now' ? `, a ${polls.method?.horizon_days ?? '—'} días de la elección` : ', última semana'}).`;
};
mcMode.value = sigmaMode;
modeTitle();
mcMode.addEventListener('change', () => {
  sigmaMode = mcMode.value;
  store.set('e29n.sigmaMode', sigmaMode);
  SIGMA = pollSigma(polls, parties, sigmaMode);
  modeTitle();
  mcToken++; // a running or finished simulation used the other noise
  mcRes = mcRecs = null;
  mcOut.innerHTML = '';
  odds.clear();
  renderProb();
});

// ---------- probabilidad de un escenario: condiciones evaluadas sobre los registros del Montecarlo ----------
const OPS = { '>=': 'al menos', '=': 'exactamente', '<=': 'como mucho' };
const validCond = (c) => c && (c.scope === 'nacional' || provById[c.scope]) && ['congreso', 'senado'].includes(c.chamber) && OPS[c.op]
  && Number.isInteger(c.n) && c.n >= 0 && Array.isArray(c.parties) && c.parties.length && c.parties.every((id) => byId[id]);
let conds = (Array.isArray(store.get('e29n.conds')) ? store.get('e29n.conds') : []).filter(validCond);
const prSel = new Set();
const prScope = $('#pr-scope');
prScope.insertAdjacentHTML('beforeend', provinces.map((pr) => `<option value="${pr.id}">${esc(pr.name)}</option>`).join(''));
const condText = (c) => `${c.scope === 'nacional' ? 'Nacional' : esc(provById[c.scope].name)} · ${c.chamber === 'senado' ? 'Senado' : 'Congreso'}: ${c.parties.map((id) => esc(byId[id].name)).join(' + ')} ${OPS[c.op]} ${c.n}`;
// Chips of the parties that run in the chosen scope (votes > 0 before coalitions).
function renderPrParties() {
  const scope = prScope.value;
  const runs = (id) => (scope === 'nacional' ? provinces.some((pr) => R.provinces[pr.id].raw[id] > 0) : R.provinces[scope].raw[id] > 0);
  for (const id of [...prSel]) if (!runs(id)) prSel.delete(id);
  $('#pr-parties').innerHTML = partyIds.filter(runs).map((id) => `<button type="button" class="chip${prSel.has(id) ? ' in-pact' : ''}" data-pr="${id}" aria-pressed="${prSel.has(id)}">${sw(byId[id].color)}${esc(byId[id].name)}</button>`).join('');
}
function renderProb() {
  renderPrParties();
  const res = mcRecs && conds.length ? scenarioProb(mcRecs, conds) : null;
  $('#pr-out').innerHTML = conds.length
    ? `<ol class="conds">${conds.map((c, i) => `<li>${condText(c)}${res ? ` · <b>${pc(res.each[i])}</b>` : ''} <button type="button" class="ghost" data-rm-cond="${i}" aria-label="Quitar condición">×</button></li>`).join('')}</ol>
       ${res ? `<p class="pact-sum">Probabilidad de que se cumplan todas: ${pc(res.all)}</p><p class="hint">Sobre ${MC_RUNS} simulaciones del escenario actual (mismo ruido que la incertidumbre). Menos de 0,1 % es «prácticamente imposible», no «imposible».</p>`
         : '<p class="hint">Pulsa «Calcular probabilidad» (lanza 1000 simulaciones si no están hechas).</p>'}`
    : '<p class="hint">Sin condiciones. Ejemplo: Ávila · Congreso · PP + Por Ávila · exactamente 2.</p>';
}
const saveConds = () => { store.set('e29n.conds', conds); renderProb(); };
prScope.addEventListener('change', renderPrParties);
$('#pr-parties').addEventListener('click', (e) => {
  const id = e.target.closest('[data-pr]')?.dataset.pr;
  if (!id) return;
  prSel.has(id) ? prSel.delete(id) : prSel.add(id);
  renderPrParties();
});
$('#pr-add').addEventListener('click', () => {
  const c = { scope: prScope.value, chamber: $('#pr-chamber').value, parties: [...prSel], op: $('#pr-op').value, n: Number($('#pr-n').value) };
  if (!c.parties.length) return flash('Elige al menos un partido.');
  if (!validCond(c)) return flash('Número de escaños no válido.');
  conds.push(c);
  prSel.clear();
  saveConds();
});
$('#pr-out').addEventListener('click', (e) => {
  const i = e.target.dataset.rmCond;
  if (i == null) return;
  conds.splice(+i, 1);
  saveConds();
});
$('#pr-clear').addEventListener('click', () => { conds = []; saveConds(); });
$('#pr-calc').addEventListener('click', () => (conds.length ? (mcRecs ? renderProb() : runMC()) : flash('Añade alguna condición primero.')));

render();
window.__sim = { S, get R() { return R; }, map, update }; // debugging hook for the console
