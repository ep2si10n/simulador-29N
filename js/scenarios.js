// Pure helpers for saved scenarios: no DOM. Input comes from localStorage, so everything is validated here.
import { validateAlliance, overridesFit, blocSeats, mcRuns, summarize, MAJORITY } from './engine.js';
const isObj = (x) => x !== null && typeof x === 'object' && !Array.isArray(x);
const clampPct = (x) => (typeof x === 'number' && Number.isFinite(x) ? Math.min(100, Math.max(0, x)) : 0);
// Pact entries are contender ids: a party, or a coalition "host+member" whose parties all exist.
const knownId = (id, ids) => typeof id === 'string' && id.split('+').every((p) => ids.includes(p));

function cleanAlliance(a, ids) {
  if (!isObj(a) || !ids.includes(a.host) || !Array.isArray(a.members)) return null;
  const members = a.members.filter((m) => isObj(m) && ids.includes(m.party)).map((m) => ({
    party: m.party,
    toCoalition: clampPct(m.toCoalition),
    transfers: (Array.isArray(m.transfers) ? m.transfers : []).filter((t) => isObj(t) && ids.includes(t.party))
      .map((t) => ({ party: t.party, pct: clampPct(t.pct) })),
  }));
  const clean = { host: a.host, members, chamber: ['congreso', 'senado'].includes(a.chamber) ? a.chamber : 'ambos' };
  const except = Array.isArray(a.except) ? a.except.filter((p) => typeof p === 'string' && /^\d{2}$/.test(p)) : [];
  if (except.length) clean.except = except; // provinces where a national coalition does not run
  return validateAlliance(clean) === null ? clean : null; // malformed or now-invalid coalitions are dropped
}

// Provincial overrides {prov: {party: pct}}: known parties, values 0-100; a province whose overrides exceed 100 is dropped.
function cleanOverrides(raw, ids) {
  const out = {};
  if (!isObj(raw)) return out;
  for (const [prov, row] of Object.entries(raw)) {
    if (!isObj(row)) continue;
    const kept = Object.fromEntries(Object.entries(row).filter(([id, v]) => ids.includes(id) && typeof v === 'number' && v >= 0 && v <= 100));
    if (Object.keys(kept).length && overridesFit(kept)) out[prov] = kept;
  }
  return out;
}

/** Working state {pct, alliances, pact, overrides} -> clean copy, or null when pct is missing. ids: every party id (otros included). */
export function cleanState(raw, ids) {
  if (!isObj(raw) || !isObj(raw.pct)) return null;
  const pct = Object.fromEntries(ids.map((id) => [id, clampPct(raw.pct[id])]));
  const al = isObj(raw.alliances) ? raw.alliances : {};
  const national = (Array.isArray(al.national) ? al.national : []).map((a) => cleanAlliance(a, ids)).filter(Boolean);
  const provincial = {};
  if (isObj(al.provincial)) {
    for (const [prov, list] of Object.entries(al.provincial)) {
      const kept = (Array.isArray(list) ? list : []).map((a) => cleanAlliance(a, ids)).filter(Boolean);
      if (kept.length) provincial[prov] = kept;
    }
  }
  const pact = (Array.isArray(raw.pact) ? raw.pact : []).filter((id) => knownId(id, ids));
  return { pct, alliances: { national, provincial }, pact, overrides: cleanOverrides(raw.overrides, ids) };
}

/** Stored scenario -> clean scenario, or null when id or pct is unusable. */
export function cleanScenario(raw, ids) {
  const state = cleanState(raw, ids);
  if (!state || typeof raw.id !== 'string' || !raw.id) return null;
  const name = typeof raw.name === 'string' ? raw.name.trim().slice(0, 60) : '';
  const created = Number.isFinite(raw.created) ? raw.created : 0;
  return { id: raw.id, name: name || 'Escenario', created, ...state };
}

/** Stored list -> clean list: non-arrays give [], malformed entries and duplicate ids are dropped. */
export function cleanScenarios(list, ids) {
  if (!Array.isArray(list)) return [];
  const seen = new Set();
  return list.map((s) => cleanScenario(s, ids)).filter((s) => {
    if (!s || seen.has(s.id)) return false;
    seen.add(s.id);
    return true;
  });
}

export const newId = (now = Date.now()) => now.toString(36) + Math.random().toString(36).slice(2, 8);

/** Working state + name -> new scenario object (deep copy, so later edits do not leak into it). */
export const newScenario = ({ pct, alliances, pact, overrides }, name, now = Date.now()) =>
  structuredClone({ id: newId(now), name, created: now, pct, alliances, pact, overrides });

/** simulate() result + baseline seats ({id: n}, the poll average without alliances) + pact ids -> consequences. */
export function consequences(r, baseSeats, pact = []) {
  const seats = r.national.seats;
  const name = (id) => r.contenders[id]?.name ?? id;
  const ids = new Set([...Object.keys(seats), ...Object.keys(baseSeats)]);
  const delta = [...ids].map((id) => ({ id, name: name(id), d: (seats[id] || 0) - (baseSeats[id] || 0) }))
    .filter((x) => x.d).sort((a, b) => Math.abs(b.d) - Math.abs(a.d));
  const top = Object.entries(seats).sort(([, a], [, b]) => b - a)[0];
  const blocs = blocSeats(seats);
  const pactSeats = pact.length ? pact.reduce((a, id) => a + (seats[id] || 0), 0) : null;
  const parts = [];
  if (pactSeats !== null) parts.push(`${pact.map(name).join(' + ')} suman ${pactSeats}: ${pactSeats >= MAJORITY ? 'mayoría absoluta' : `no llegan (faltan ${MAJORITY - pactSeats})`}`);
  else for (const [bloc, label] of [['derecha', 'Derecha'], ['izquierda', 'Izquierda']]) if (blocs[bloc] >= MAJORITY) parts.push(`${label} suma ${blocs[bloc]}: mayoría absoluta`);
  if (!parts.length) parts.push('ningún bloque llega a 176');
  const drop = delta.filter((x) => x.d < 0).sort((a, b) => a.d - b.d)[0];
  if (drop) parts.push(`${drop.name} cae ${-drop.d} escaños`);
  const line = parts.join('; ');
  return {
    delta,
    largest: top ? { id: top[0], name: name(top[0]), seats: top[1] } : null,
    blocs,
    pactSeats,
    line: line[0].toUpperCase() + line.slice(1),
  };
}

/** Two simulate() results -> {seats: candidaturas with seats in A or B, sorted by size; flips: provinces whose winner differs}. Ids on one side only count 0. */
export function diffResults(rA, rB) {
  const sa = rA.national.seats, sb = rB.national.seats;
  const seats = [...new Set([...Object.keys(sa), ...Object.keys(sb)])]
    .map((id) => ({ id, name: (rA.contenders[id] ?? rB.contenders[id])?.name ?? id, a: sa[id] || 0, b: sb[id] || 0 }))
    .filter((x) => x.a || x.b)
    .map((x) => ({ ...x, d: x.b - x.a }))
    .sort((x, y) => y.a + y.b - (x.a + x.b));
  const flips = Object.keys(rA.provinces)
    .map((prov) => ({ prov, a: rA.provinces[prov].winner ?? null, b: rB.provinces[prov]?.winner ?? null }))
    .filter((x) => x.a !== x.b);
  return { seats, flips };
}

/** state: mcRuns inputs ({nationalPct, parties, provinces, alliances}) plus `pact`. Blocking: the UI slices mcRuns itself. */
export function scenarioOdds(state, runs, rng, sigma) {
  return summarize([...mcRuns(state, runs, rng, sigma)].map((r) => r.c), state.pact);
}

/** Share hash (#s=base64 JSON) -> payload, or null when missing or malformed. Old hashes (p, a, c, g) stay valid. */
export function decodeShare(hash) {
  try { const m = hash.match(/^#s=(.+)$/); return m ? JSON.parse(decodeURIComponent(atob(m[1]))) : null; } catch { return null; }
}
/** Payload -> share hash. */
export const encodeShare = (payload) => `#s=${btoa(encodeURIComponent(JSON.stringify(payload)))}`;

/** Hash view fields (v: tab, s: province, m: map mode) -> valid values; anything else falls back to the defaults. */
export function cleanView(raw, provIds, partyIds) {
  const o = isObj(raw) ? raw : {};
  return {
    view: o.v === 'escenarios' ? 'escenarios' : 'hemi',
    selected: provIds.includes(o.s) ? o.s : null,
    mapMode: o.m === 'ganador' || partyIds.includes(o.m) ? o.m : 'ganador',
  };
}

/** Undo/redo over working states. clear(s) sets the baseline; push(s) records s unless it equals the current state (a new push drops the redo); undo/redo return a copy of the state to show, or null. */
export function createHistory(limit = 50) {
  let cur, past = [], future = [];
  const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
  return {
    clear(state) { cur = structuredClone(state); past = []; future = []; },
    push(state) {
      if (same(state, cur)) return;
      past.push(cur);
      if (past.length > limit) past.shift();
      cur = structuredClone(state);
      future = [];
    },
    undo() {
      if (!past.length) return null;
      future.push(cur);
      cur = past.pop();
      return structuredClone(cur);
    },
    redo() {
      if (!future.length) return null;
      past.push(cur);
      cur = future.pop();
      return structuredClone(cur);
    },
    get canUndo() { return past.length > 0; },
    get canRedo() { return future.length > 0; },
  };
}
