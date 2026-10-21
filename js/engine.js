// Pure simulation engine: no DOM, importable from node.
// Indices: p = party/contender, i = province. Percentages are of VALID votes (blank included in "otros").

import { simulateSenate } from './senate.js';

const sum = (xs) => xs.reduce((a, b) => a + b, 0);

/**
 * National poll % -> % per province via elasticities + iterative proportional fitting.
 * nationalPct: {party_id: pct} (normalized to 100 here). parties: [{party_id, elasticity_vector}].
 * provinces: [{id, expected_census}]. Returns {provinceId: {party_id: pct}} with rows summing 100.
 */
export function estimate(nationalPct, parties, provinces, maxIter = 200, tol = 1e-7) {
  const ids = parties.map((p) => p.party_id);
  const total = sum(ids.map((id) => Math.max(0, nationalPct[id] || 0)));
  const N = ids.map((id) => (total > 0 ? (100 * Math.max(0, nationalPct[id] || 0)) / total : 0));
  const W = provinces.map((pr) => pr.expected_census);
  const Wsum = sum(W);
  const V = parties.map((p, k) => provinces.map((pr) => N[k] * (p.elasticity_vector[pr.id] || 0)));
  const P = provinces.length, K = V.length;
  const scaleRows = () => { // returns max |row sum - 100| before scaling
    let err = 0;
    for (let i = 0; i < P; i++) {
      let r = 0;
      for (let k = 0; k < K; k++) r += V[k][i];
      if (r > 0) { err = Math.max(err, Math.abs(r - 100)); for (let k = 0; k < K; k++) V[k][i] *= 100 / r; }
    }
    return err;
  };
  for (let it = 0; it < maxIter; it++) {
    if (scaleRows() < tol && it > 0) break;
    for (let k = 0; k < K; k++) {
      const row = V[k];
      let share = 0;
      for (let i = 0; i < P; i++) share += row[i] * W[i];
      share /= Wsum;
      if (share > 0) for (let i = 0; i < P; i++) row[i] *= N[k] / share;
    }
  }
  scaleRows();
  const out = {};
  provinces.forEach((pr, i) => (out[pr.id] = Object.fromEntries(ids.map((id, k) => [id, V[k][i]]))));
  return out;
}

/** Provincial overrides {party_id: pct} fit when they sum to at most 100. */
export const overridesFit = (ov) => sum(Object.values(ov)) <= 100 + 1e-9;

/**
 * Province row {party_id: pct} (sums 100) with overrides applied: overridden parties keep their value, the rest share the
 * remainder in proportion to their own values (all at 0: the remainder goes to otros). Throws when the overrides exceed 100.
 */
export function applyOverrides(row, ov) {
  if (!overridesFit(ov)) throw new Error('Los porcentajes fijados suman más de 100 %.');
  const free = Object.keys(row).filter((id) => !(id in ov));
  const rest = Math.max(0, 100 - sum(Object.values(ov)));
  const pool = sum(free.map((id) => row[id]));
  const out = { ...row, ...ov };
  for (const id of free) out[id] = pool > 0 ? (rest * row[id]) / pool : 0;
  if (pool <= 0 && free.length) out[free.includes('otros') ? 'otros' : free[0]] = rest;
  // ponytail: every party fixed with a total under 100 leaves the remainder unassigned; the UI does not reach that.
  return out;
}

/** Result of simulate() -> national % per party (census-weighted over provinces, pre-alliance): what the national input becomes. */
export function nationalShares(result, provinces) {
  const total = sum(provinces.map((p) => p.expected_census));
  const out = {};
  for (const pr of provinces) for (const [id, v] of Object.entries(result.provinces[pr.id].raw)) out[id] = (out[id] || 0) + (100 * v) / total;
  return out;
}

/** "Partir de" preset: the poll's % for the parties it reports, `base` (weighted average) for the rest. */
export const presetPct = (values, base, ids) => Object.fromEntries(ids.map((id) => [id, values[id] ?? base[id] ?? 0]));

/** Provincial party: its input is % of its own provinces = national share N * this factor. */
export const ambitoFactor = (provinces, ids) =>
  sum(provinces.map((p) => p.expected_census)) / sum(provinces.filter((p) => ids.includes(p.id)).map((p) => p.expected_census));

/** Coalition contender id: the host (the party the others were dragged onto) runs under its own name and keeps every seat. */
export const allianceId = (a) => a.host;

/** Returns an error message (Spanish, for the UI) or null. */
export function validateAlliance(a) {
  const inside = new Set(a.members.map((m) => m.party));
  if (inside.size < 2) return 'Una coalición necesita al menos dos partidos.';
  if (!inside.has(a.host)) return 'El partido anfitrión debe ser miembro.';
  for (const m of a.members) {
    const all = [m.toCoalition, ...(m.transfers || []).map((t) => t.pct)];
    if (all.some((x) => !(x >= 0 && x <= 100))) return 'Los porcentajes deben estar entre 0 y 100.';
    if (sum(all) > 100 + 1e-9) return `Los repartos de ${m.party} suman más de 100%.`;
    if ((m.transfers || []).some((t) => inside.has(t.party))) return 'Un trasvase no puede ir a un miembro de la coalición.';
  }
  return null;
}

/**
 * Alliances in force in a province: provincial ones first; national ones sharing a party with them are dropped, and so
 * are national ones listing the province in `except` (e.g. a joint list that does not run in Catalonia).
 */
export function activeAlliances(alliances, provinceId) {
  const prov = alliances?.provincial?.[provinceId] || [];
  const taken = new Set(prov.flatMap((a) => a.members.map((m) => m.party)));
  return [...prov, ...(alliances?.national || []).filter((a) => !a.except?.includes(provinceId) && !a.members.some((m) => taken.has(m.party)))];
}

/** Alliances that apply to a chamber ('congreso' | 'senado'): chamber 'ambos' (or missing) always, the others only to their own. */
export const forChamber = (alliances, chamber) => {
  const keep = (a) => (a.chamber ?? 'ambos') === 'ambos' || a.chamber === chamber;
  return { national: alliances.national.filter(keep), provincial: Object.fromEntries(Object.entries(alliances.provincial).map(([p, l]) => [p, l.filter(keep)])) };
};

/**
 * votes: {party_id: absolute votes}. For each member: toCoalition% -> coalition, transfers -> target
 * (routed to the target's own coalition if it has one), the rest abstains.
 * Returns {votes: {contender_id: v}, abstained}.
 */
export function applyAlliances(votes, alliances) {
  const owner = {};
  for (const a of alliances) for (const m of a.members) owner[m.party] = allianceId(a);
  const out = {};
  const add = (party, v) => {
    const c = owner[party] ?? party;
    out[c] = (out[c] || 0) + v;
  };
  let abstained = 0;
  for (const [p, v] of Object.entries(votes)) if (!owner[p]) add(p, v);
  for (const a of alliances) {
    const id = allianceId(a);
    out[id] = out[id] || 0;
    for (const m of a.members) {
      const v = votes[m.party] || 0;
      out[id] += (v * m.toCoalition) / 100;
      let left = (v * (100 - m.toCoalition)) / 100;
      for (const t of m.transfers || []) {
        if (!(votes[t.party] > 0)) continue; // target does not run here (e.g. ERC outside Catalonia): those voters abstain
        add(t.party, (v * t.pct) / 100);
        left -= (v * t.pct) / 100;
      }
      abstained += left;
    }
  }
  return { votes: out, abstained };
}

/**
 * D'Hondt with the 3% barrier on the province's valid votes. `exclude` ids (otros) never get seats.
 * Ties on the quotient go to the contender with more votes. Returns {id: seats}.
 */
export function dhondt(votes, seats, validTotal, exclude = []) {
  const ids = Object.keys(votes).filter((id) => !exclude.includes(id) && votes[id] > 0 && votes[id] >= 0.03 * validTotal);
  const won = Object.fromEntries(ids.map((id) => [id, 0]));
  for (let k = 0; k < seats && ids.length; k++) {
    let best = ids[0];
    for (const id of ids) {
      const q = votes[id] / (won[id] + 1);
      const qb = votes[best] / (won[best] + 1);
      if (q > qb || (q === qb && votes[id] > votes[best])) best = id;
    }
    won[best]++;
  }
  return won;
}

/**
 * Last D'Hondt seat: its winner (smallest last quotient) and the best non-winner, plus the votes that runner-up
 * needs to beat that quotient. votesNeeded is null when nobody else is above the barrier. Null when there are no seats.
 */
export function lastSeat(votes, seats, validTotal, exclude = []) {
  const won = dhondt(votes, seats, validTotal, exclude);
  const winners = Object.keys(won).filter((id) => won[id] > 0);
  if (!winners.length) return null;
  const q = (id) => votes[id] / won[id];
  const winner = winners.sort((a, b) => q(a) - q(b) || votes[a] - votes[b])[0];
  const next = (id) => votes[id] / ((won[id] || 0) + 1);
  const runnerUp = Object.keys(votes)
    .filter((id) => id !== winner && !exclude.includes(id) && votes[id] > 0 && votes[id] >= 0.03 * validTotal)
    .sort((a, b) => next(b) - next(a))[0] ?? null;
  const votesNeeded = runnerUp ? Math.max(1, Math.round(Math.floor(q(winner) * ((won[runnerUp] || 0) + 1)) + 1 - votes[runnerUp])) : null;
  return { winner, runnerUp, votesNeeded };
}

/**
 * state: {nationalPct, parties, provinces, alliances:{national:[], provincial:{id:[]}}}.
 * Returns {provinces:{id:{votes, pct, seats, winner, valid, abstained, raw, coal}}, national:{seats, votes}, contenders:{id: meta}}.
 * coal: {host: [host, ...donors]} for the coalitions in force in that province.
 * ponytail: full recompute on every change (52x17); per-province incremental recompute if this ever feels slow.
 */
export function simulate({ nationalPct, parties, provinces, alliances, overrides }, est = estimate(nationalPct, parties, provinces)) {
  const contenders = Object.fromEntries(
    parties.map((p) => [p.party_id, { id: p.party_id, name: p.name, color: p.color, order: p.order, eligible: p.seats_eligible !== false }]),
  );
  const exclude = parties.filter((p) => p.seats_eligible === false).map((p) => p.party_id);
  const res = { provinces: {}, national: { seats: {}, votes: {} }, contenders };
  for (const pr of provinces) {
    const ov = overrides?.[pr.id];
    const raw = {};
    for (const [id, pct] of Object.entries(ov ? applyOverrides(est[pr.id], ov) : est[pr.id])) if (pct > 0) raw[id] = (pct / 100) * pr.expected_census;
    const active = activeAlliances(alliances, pr.id).filter((a) => a.members.some((m) => raw[m.party] > 0));
    const { votes: all, abstained } = applyAlliances(raw, active);
    const coal = Object.fromEntries(active.map((a) => [a.host, [a.host, ...a.members.map((m) => m.party).filter((p) => p !== a.host)]]));
    const votes = Object.fromEntries(Object.entries(all).filter(([, v]) => v > 0));
    const valid = sum(Object.values(votes));
    const seats = dhondt(votes, pr.seats, valid, exclude);
    const pct = Object.fromEntries(Object.entries(votes).map(([id, v]) => [id, (100 * v) / valid]));
    const winner = Object.keys(votes).filter((id) => !exclude.includes(id))
      .sort((a, b) => (seats[b] || 0) - (seats[a] || 0) || votes[b] - votes[a])[0] ?? null;
    res.provinces[pr.id] = { votes, pct, seats, winner, valid, abstained, raw, coal }; // raw = party votes before alliances
    for (const [id, v] of Object.entries(votes)) res.national.votes[id] = (res.national.votes[id] || 0) + v;
    for (const [id, s] of Object.entries(seats)) if (s) res.national.seats[id] = (res.national.seats[id] || 0) + s;
  }
  return res;
}

/** Strict "32.45" / "32,45" parser: 0-100, max 2 decimals. Returns a number or null. */
export function parsePct(text) {
  const s = String(text).trim();
  if (!/^\d{1,3}([.,]\d{1,2})?$/.test(s)) return null;
  const v = Number(s.replace(',', '.'));
  return v <= 100 ? v : null;
}

/**
 * Auto-balancer: set pct[changed] = value and take the difference from `source`
 * ('prop' = all others incl. otros in proportion; or a party id). A depleted source spills over proportionally.
 */
export function rebalance(pct, changed, value, source = 'prop') {
  const out = { ...pct, [changed]: value };
  let rest = value - (pct[changed] || 0);
  if (source !== 'prop' && source !== changed && source in out) {
    const take = Math.min(rest, out[source]);
    out[source] -= take;
    rest -= take;
  }
  if (Math.abs(rest) > 1e-12) {
    const others = Object.keys(out).filter((id) => id !== changed);
    const pool = sum(others.map((id) => out[id]));
    if (pool > 0) for (const id of others) out[id] = Math.max(0, out[id] - (rest * out[id]) / pool);
    else if ('otros' in out) out.otros -= rest; // everyone else is at 0: only a decrease lands here
  }
  return out;
}

/**
 * Smallest % of `partyId` (bisection, rebalance keeps 100 %) for which targetFn(national seats) holds; assumes it holds
 * from some % upwards. Returns {pct, seats} with pct within `step` of the minimum (pct itself always satisfies), or null
 * when even `hi` fails. state: {pct, source, parties, provinces, alliances}; it is not mutated.
 */
export function solveTarget(state, targetFn, partyId, { lo = 0, hi = 60, step = 0.05 } = {}) {
  const at = (v) => simulate({ ...state, nationalPct: rebalance(state.pct, partyId, v, state.source) }).national.seats;
  if (!targetFn(at(hi))) return null;
  if (targetFn(at(lo))) return { pct: lo, seats: at(lo) };
  let a = lo, b = hi;
  while (b - a > step) {
    const m = (a + b) / 2;
    if (targetFn(at(m))) b = m;
    else a = m;
  }
  return { pct: b, seats: at(b) };
}

/** Seedable PRNG (mulberry32): returns a function giving floats in [0, 1). */
export function mulberry32(seed) {
  let a = seed | 0;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export const MAJORITY = 176;
const normal = (rng, sd) => sd * Math.sqrt(-2 * Math.log(1 - rng())) * Math.cos(2 * Math.PI * rng());
const absentSigma = (pct) => Math.max(0.05, 0.25 * pct); // party without poll values: 25 % of its share, at least 0.05 pts

/**
 * National sigma (pct points) per party for the Monte Carlo, proportional to the party's poll average:
 * sigma = r x average %, so seat intervals scale with expected seats. r = relative error of the poll average learned
 * from 2018-2026 elections (pipeline/model/poll_accuracy.py): polls.method.sigma_rel_final (last week of campaign, default)
 * or sigma_rel_now (today's horizon), 0.12 when absent. Parties without an average (provincial ones): absentSigma
 * of default_pct. polls = data/polls.json, parties = data/party_elasticity.json parties, mode = 'final' | 'now'.
 */
export function pollSigma(polls, parties, mode = 'final') {
  const r = (mode === 'now' ? polls.method?.sigma_rel_now : polls.method?.sigma_rel_final) ?? 0.12;
  const dflt = Object.fromEntries(parties.map((p) => [p.party_id, p.default_pct ?? 0]));
  const ids = new Set([...Object.keys(dflt), ...Object.keys(polls.average)]);
  return Object.fromEntries([...ids].map((id) => [id, polls.average[id] != null ? r * polls.average[id] : absentSigma(dflt[id] ?? 0)]));
}

/**
 * One noisy simulation per yield: {c: Congreso seats, pc: {provId: Congreso seats}, s, ps: same for the Senate (null
 * without `senate`)}. Noise: national % += Normal(0, sigma[id]) around the scenario value (clamped at 0; estimate()
 * renormalizes to 100), then each province x party *= exp(Normal(0, 0.08)) before D'Hondt. Both chambers share the draw.
 * sigma: pollSigma(polls, parties, provinces), computed once by the UI. senate: {data: senate.json, alliances} or null.
 * ponytail: the Senate reuses the Congreso vote draw (no separate Senate vote %); split them if Senate vote polls appear.
 */
const seatsByProv = (res) => Object.fromEntries(Object.entries(res.provinces).map(([id, r]) => [id, r.seats]));
export function* mcRuns(state, runs, rng, sigma = {}, senate = null) {
  const ids = state.parties.map((p) => p.party_id);
  for (let r = 0; r < runs; r++) {
    const nationalPct = Object.fromEntries(ids.map((id) => {
      const n = Math.max(0, state.nationalPct[id] || 0);
      return [id, Math.max(0, n + normal(rng, sigma[id] ?? absentSigma(n)))];
    }));
    const est = estimate(nationalPct, state.parties, state.provinces, 40, 1e-3); // looser IPF: the noise dwarfs a 1e-3 error
    for (const pr of state.provinces) { // overrides fixed first; provincial noise hits only the rest, simulate() renormalizes
      const ov = state.overrides?.[pr.id];
      if (ov) est[pr.id] = applyOverrides(est[pr.id], ov);
      for (const id of ids) if (!ov || !(id in ov)) est[pr.id][id] *= Math.exp(normal(rng, 0.08));
    }
    const rc = simulate({ ...state, nationalPct }, est);
    const sn = senate && simulateSenate(simulate({ ...state, nationalPct, alliances: senate.alliances }, est), senate.data, state.provinces, rng);
    yield { c: rc.national.seats, pc: seatsByProv(rc), s: sn?.seats ?? null, ps: sn && Object.fromEntries(Object.entries(sn.provinces).map(([id, x]) => [id, x.seats])) };
  }
}

/**
 * P(conditions) over mcRuns records. cond: {scope: 'nacional' | provId, chamber: 'congreso' | 'senado', parties: [ids],
 * op: '>=' | '=' | '<=', n}; a coalition's seats sit under its host, so listing every member counts them once.
 * Returns {all: P(every condition), each: [P(cond)]}; Senate conditions need runs made with `senate`.
 */
export function scenarioProb(records, conds) {
  const ok = (rec, c) => {
    const m = c.chamber === 'senado' ? (c.scope === 'nacional' ? rec.s : rec.ps?.[c.scope]) : (c.scope === 'nacional' ? rec.c : rec.pc[c.scope]);
    const v = sum(c.parties.map((id) => m?.[id] || 0));
    return c.op === '=' ? v === c.n : c.op === '<=' ? v <= c.n : v >= c.n;
  };
  const n = records.length || 1;
  return { all: records.filter((r) => conds.every((c) => ok(r, c))).length / n, each: conds.map((c) => records.filter((r) => ok(r, c)).length / n) };
}

/**
 * Per-run seat maps -> {seats: {id: {lo, p50, hi, mean}}, pact: P(pact >= majority) or null}.
 * lo-hi is the 97.5 % interval of seats (quantiles 1.25 % and 98.75 %). ids missing in a run count as 0 seats.
 */
export function summarize(seatMaps, pact, majority = MAJORITY) {
  const n = seatMaps.length;
  const hist = {};
  seatMaps.forEach((seats, r) => {
    for (const [id, s] of Object.entries(seats)) (hist[id] ??= new Array(n).fill(0))[r] = s;
  });
  const seats = {};
  for (const [id, xs] of Object.entries(hist)) {
    const sorted = [...xs].sort((a, b) => a - b);
    const at = (p) => sorted[Math.floor(p * (n - 1))];
    seats[id] = { lo: at(0.0125), p5: at(0.05), p50: at(0.5), p95: at(0.95), hi: at(0.9875), mean: sum(xs) / n };
  }
  const pactP = pact?.length ? seatMaps.filter((s) => sum(pact.map((id) => s[id] || 0)) >= majority).length / n : null;
  return { seats, pact: pactP };
}

/**
 * CSV rows (header first). Provincial rows, then one TOTAL row per candidate (national votes, seats Congreso and Senado
 * elected+designated), then a PACTO row. mc: summarize() output or null (MC columns left empty).
 */
export function csvRows({ R, SN, provinces, pact, mc, overrides = {} }) {
  const name = (id) => R.contenders[id]?.name ?? id;
  const rows = [['codigo', 'provincia', 'candidatura', 'pct', 'votos', 'escanos_congreso', 'escanos_senado', 'congreso_p5', 'congreso_p50', 'congreso_p95', 'pct_fijado']];
  for (const pr of provinces) {
    const r = R.provinces[pr.id];
    for (const id of Object.keys(r.votes)) {
      rows.push([pr.id, pr.name, name(id), r.pct[id].toFixed(2), Math.round(r.votes[id]), r.seats[id] || 0, SN.provinces[pr.id].seats[id] || 0, '', '', '', overrides[pr.id]?.[id] ?? '']);
    }
  }
  const valid = sum(Object.values(R.national.votes));
  const ids = [...new Set([...Object.keys(R.national.votes), ...Object.keys(SN.seats)])].sort((a, b) => (R.national.votes[b] || 0) - (R.national.votes[a] || 0));
  for (const id of ids) {
    const v = R.national.votes[id] || 0;
    const s = mc?.seats[id];
    rows.push(['', 'TOTAL', name(id), ((100 * v) / valid).toFixed(2), Math.round(v), R.national.seats[id] || 0, SN.seats[id] || 0, s?.p5 ?? '', s?.p50 ?? '', s?.p95 ?? '', '']);
  }
  if (pact?.length) {
    const of = (m) => sum(pact.map((id) => m[id] || 0));
    rows.push(['', 'PACTO', pact.map(name).join(' + '), '', '', of(R.national.seats), of(SN.seats), '', '', '', '']);
  }
  return rows;
}

/** state: simulate() inputs plus `pact` (ids). Blocking version of mcRuns + summarize (the UI chunks mcRuns itself). */
export const monteCarlo = (state, runs, rng, sigma) => summarize([...mcRuns(state, runs, rng, sigma)].map((r) => r.c), state.pact);

// Blocs for the hemicycle bar. A coalition runs as its host, so the host decides its bloc.
const LEFT = ['podemos', 'fa', 'fv', 'aa', 'cup', 'bildu', 'bng', 'erc', 'psoe', 'gbai', 'nc'];
const RIGHT = ['pp', 'vox', 'salf', 'upn', 'ac'];
export const blocOf = (id) => (LEFT.includes(id.split('+')[0]) ? 'izquierda' : RIGHT.includes(id.split('+')[0]) ? 'derecha' : 'otros');

/** seats: {id: n} -> {izquierda, derecha, otros} seat totals. */
export function blocSeats(seats) {
  const out = { izquierda: 0, derecha: 0, otros: 0 };
  for (const [id, n] of Object.entries(seats)) out[blocOf(id)] += n;
  return out;
}

/** Provinces with turnout moved so the census-weighted national turnout is `target` (0-1). Same logit shift in every
 * province: low-turnout provinces move more in points, and none leaves (0, 1). Base turnout = expected_census / census. */
export function withTurnout(provinces, target) {
  const lg = provinces.map((p) => Math.log(p.expected_census / (p.census - p.expected_census)));
  const at = (d) => provinces.map((p, i) => ({ ...p, expected_census: Math.round(p.census / (1 + Math.exp(-(lg[i] + d)))) }));
  const census = sum(provinces.map((p) => p.census));
  let lo = -4, hi = 4;
  for (let i = 0; i < 50; i++) {
    const m = (lo + hi) / 2;
    if (sum(at(m).map((p) => p.expected_census)) / census < target) lo = m; else hi = m;
  }
  return at((lo + hi) / 2);
}

/** National % moved by a change of `dT` turnout points: party += slope x dT (slopes learned by pipeline/model/model_lab.py:
 * how far each party beat its polls per extra point of turnout), total kept. */
export function turnoutPct(pct, dT, slopes) {
  const moved = Object.fromEntries(Object.entries(pct).map(([id, v]) => [id, Math.max(0, v + (slopes[id] ?? 0) * dT)]));
  const k = sum(Object.values(pct)) / (sum(Object.values(moved)) || 1);
  return Object.fromEntries(Object.entries(moved).map(([id, v]) => [id, v * k]));
}
