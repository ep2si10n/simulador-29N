// Run: node tests/engine.test.mjs
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { estimate, dhondt, applyAlliances, activeAlliances, simulate, parsePct, rebalance, validateAlliance, ambitoFactor, lastSeat, presetPct, mulberry32, mcRuns, summarize, monteCarlo, pollSigma, blocOf, blocSeats, csvRows, solveTarget, forChamber, applyOverrides, nationalShares, scenarioProb } from '../js/engine.js';
import { simulateSenate } from '../js/senate.js';
import { cleanState } from '../js/scenarios.js';

const near = (a, b, eps, msg) => assert.ok(Math.abs(a - b) < eps, `${msg}: ${a} vs ${b}`);
const sum = (o) => Object.values(o).reduce((a, b) => a + b, 0);
const ones = (ids) => Object.fromEntries(ids.map((id) => [id, 1]));

// D'Hondt textbook case (7 seats).
assert.deepEqual(dhondt({ A: 100000, B: 80000, C: 30000, D: 20000 }, 7, 230000), { A: 3, B: 3, C: 1, D: 0 });

// 3% barrier: C (2.9%) would win a seat out of 50 without it.
const bv = { A: 500, B: 471, C: 29 };
assert.equal(dhondt(bv, 50, 100).C, 1, 'C wins a seat without an effective barrier');
assert.equal(dhondt(bv, 50, 1000).C, undefined, 'C excluded under 3%');
assert.equal(sum(dhondt(bv, 50, 1000)), 50);

// otros never seated, but counted in the barrier denominator (via simulate, single province, E=1).
const P = [
  { party_id: 'A', name: 'A', color: '#000', order: 1, elasticity_vector: { '01': 1 } },
  { party_id: 'C', name: 'C', color: '#111', order: 2, elasticity_vector: { '01': 1 } },
  { party_id: 'otros', name: 'Otros', color: '#999', order: 99, seats_eligible: false, elasticity_vector: { '01': 1 } },
];
const one = [{ id: '01', seats: 50, expected_census: 100000 }];
const r1 = simulate({ nationalPct: { A: 87.1, C: 2.9, otros: 10 }, parties: P, provinces: one, alliances: {} }).provinces['01'];
assert.equal(r1.seats.otros, undefined, 'otros never seated');
assert.equal(r1.seats.C, undefined, 'C is 2.9% of valid incl. otros -> excluded');
assert.equal(r1.seats.A, 50);
assert.ok(dhondt({ A: r1.votes.A, C: r1.votes.C }, 50, r1.votes.A + r1.votes.C).C > 0, 'without otros in the denominator C would pass');
assert.deepEqual(dhondt({ otros: 600, A: 400 }, 3, 1000, ['otros']), { A: 3 });

// IPF: rows sum 100 and national weighted shares match the input.
const provs = [
  { id: '01', expected_census: 100000 }, { id: '02', expected_census: 400000 },
  { id: '03', expected_census: 250000 }, { id: '04', expected_census: 50000 },
];
const parties = [
  { party_id: 'pp', elasticity_vector: { '01': 1.6, '02': 0.9, '03': 1.1, '04': 1.3 } },
  { party_id: 'psoe', elasticity_vector: { '01': 0.9, '02': 1.1, '03': 0.95, '04': 1.0 } },
  { party_id: 'reg', elasticity_vector: { '01': 0, '02': 0, '03': 2.5, '04': 0 } },
  { party_id: 'otros', elasticity_vector: ones(['01', '02', '03', '04']) },
];
const nat = { pp: 35, psoe: 40, reg: 8, otros: 17 };
const est = estimate(nat, parties, provs);
const W = sum(Object.fromEntries(provs.map((p) => [p.id, p.expected_census])));
for (const p of provs) near(sum(est[p.id]), 100, 1e-9, `row ${p.id}`);
for (const k of Object.keys(nat)) near(provs.reduce((a, p) => a + est[p.id][k] * p.expected_census, 0) / W, nat[k], 1e-4, `share ${k}`);
assert.equal(est['01'].reg, 0, 'E=0 stays 0');

// Alliance conservation: coalition + transfers + abstained = original member votes.
const votes = { pp: 1000, vox: 500, psoe: 800, otros: 100 };
const al = { id: 'pp+vox', host: 'pp', members: [
  { party: 'pp', toCoalition: 95, transfers: [{ party: 'psoe', pct: 2 }] },
  { party: 'vox', toCoalition: 80, transfers: [{ party: 'otros', pct: 5 }] },
] };
assert.equal(validateAlliance(al), null);
const ap = applyAlliances(votes, [al]);
near(ap.votes.pp, 1350, 1e-9, 'coalition');
near(ap.votes.psoe - 800 + ap.votes.otros - 100, 45, 1e-9, 'transfers');
near(ap.abstained, 105, 1e-9, 'abstained');
near(ap.votes.pp + (ap.votes.psoe - 800) + (ap.votes.otros - 100) + ap.abstained, 1500, 1e-9, 'conservation');
near(sum(ap.votes) + ap.abstained, sum(votes), 1e-9, 'total conservation');
assert.ok(validateAlliance({ ...al, members: [al.members[0], { party: 'vox', toCoalition: 90, transfers: [{ party: 'psoe', pct: 20 }] }] }));
// A transfer to a party that does not run in the province abstains instead of creating votes.
const absent = applyAlliances(votes, [{ ...al, members: [al.members[0], { party: 'vox', toCoalition: 80, transfers: [{ party: 'erc', pct: 10 }] }] }]);
assert.equal(absent.votes.erc, undefined);
near(sum(absent.votes) + absent.abstained, sum(votes), 1e-9, 'conservation with absent target');

// Provincial alliance overrides a national alliance sharing a party.
const alliances = {
  national: [{ host: 'pp', members: [{ party: 'pp', toCoalition: 100 }, { party: 'vox', toCoalition: 100 }] }],
  provincial: { '02': [{ host: 'pp', members: [{ party: 'pp', toCoalition: 100 }, { party: 'psoe', toCoalition: 100 }] }] },
};
assert.deepEqual(activeAlliances(alliances, '02').map((a) => a.members.length), [2]);
assert.equal(activeAlliances(alliances, '02')[0].members[1].party, 'psoe');
const sim = simulate({
  nationalPct: { pp: 33, psoe: 30, vox: 15, otros: 22 },
  parties: ['pp', 'psoe', 'vox', 'otros'].map((id, k) => ({ party_id: id, name: id, color: '#000', order: k, seats_eligible: id !== 'otros', elasticity_vector: ones(['01', '02']) })),
  provinces: [{ id: '01', seats: 10, expected_census: 1e5 }, { id: '02', seats: 10, expected_census: 1e5 }],
  alliances,
});
// A coalition runs under its host's id: donors disappear from the ballot, coal lists who is inside.
assert.ok(sim.provinces['01'].votes.pp > 0 && !sim.provinces['01'].votes.vox);
assert.deepEqual(sim.provinces['01'].coal, { pp: ['pp', 'vox'] });
assert.ok(sim.provinces['02'].votes.pp > 0 && sim.provinces['02'].votes.vox > 0 && !sim.provinces['02'].votes.psoe);
assert.ok(!sim.national.seats['pp+vox'] && !sim.contenders['pp+vox'], 'no "pp+vox" pseudo-party');
assert.equal(sum(sim.national.seats), 20);

// Input parsing and auto-balancer.
assert.equal(parsePct('32,45'), 32.45);
assert.equal(parsePct('32.45'), 32.45);
for (const bad of ['', '32.456', '101', '-1', 'abc', '1e2', '100.01']) assert.equal(parsePct(bad), null, bad);
const rb = rebalance({ a: 50, b: 30, otros: 20 }, 'a', 60, 'prop');
near(sum(rb), 100, 1e-9, 'prop keeps 100');
near(rb.b, 24, 1e-9, 'prop b');
const rs = rebalance({ a: 50, b: 30, c: 15, otros: 5 }, 'a', 60, 'otros');
near(rs.otros, 0, 1e-9, 'source clamps at 0');
near(sum(rs), 100, 1e-9, 'spill keeps 100');
near(rs.b, 30 - 5 * 30 / 45, 1e-9, 'spill proportional');

// Provincial party factor: census of all provinces / census of the party's provinces.
const fac = [{ id: '01', expected_census: 100 }, { id: '02', expected_census: 300 }];
near(ambitoFactor(fac, ['02']), 400 / 300, 1e-12, 'ambito factor');
near(ambitoFactor(fac, ['01', '02']), 1, 1e-12, 'ambito factor, all provinces');

// Last seat: textbook 7-seat case. B takes the 7th seat (quotient 26667); A needs 6667 more votes to take it (106667/4 > 26667).
assert.deepEqual(lastSeat({ A: 100000, B: 80000, C: 30000, D: 20000 }, 7, 230000), { winner: 'B', runnerUp: 'A', votesNeeded: 6667 });
assert.equal(lastSeat({ A: 100 }, 0, 100), null, 'no seats, no last seat');
assert.deepEqual(lastSeat({ A: 900, B: 90, C: 10 }, 1, 1000, []), { winner: 'A', runnerUp: 'B', votesNeeded: 811 }, 'C below 3% never runs; B needs 811 more');
assert.equal(lastSeat({ A: 60, otros: 40 }, 1, 100, ['otros']).runnerUp, null, 'nobody else above barrier');

console.log('engine tests: OK');


// Senado: limited vote per constituency (min(marks, seats) candidates each) + fixed designated seats.
const sR = {
  provinces: { '01': { votes: { A: 600, B: 300, C: 100 } }, '02': { votes: { A: 100, B: 900, otros: 5000 } } },
  contenders: { A: { eligible: true }, B: { eligible: true }, C: { eligible: true }, otros: { eligible: false } },
};
const sData = {
  constituencies: [{ id: '01', province_id: '01', seats: 4, marks: 3 }, { id: '01b', province_id: '01', seats: 1, marks: 1 }, { id: '02', province_id: '02', seats: 2, marks: 2 }],
  designated: { by_community: [{ community: 'X', seats: 2, by_party: { A: 1, B: 1 } }] },
};
const sen = simulateSenate(sR, sData, [{ id: '01' }, { id: '02' }]);
assert.deepEqual(sen.byConstituency['01'], { A: 3, B: 1 }, 'limited vote 3 marks, 4 seats');
assert.deepEqual(sen.byConstituency['01b'], { A: 1 }, 'single seat');
assert.deepEqual(sen.byConstituency['02'], { B: 2 }, 'otros never seated, B takes both');
assert.deepEqual(sen.provinces['01'], { seats: { A: 4, B: 1 }, winner: 'A' });
assert.deepEqual(sen.provinces['02'], { seats: { B: 2 }, winner: 'B' });
assert.deepEqual(sen.designated, { A: 1, B: 1 });
assert.deepEqual(sen.seats, { A: 5, B: 4 });
assert.equal(sen.total, 9);
console.log('senate tests: OK');

// "Partir de" preset: poll values win, parties the poll lacks keep the average.
assert.deepEqual(presetPct({ A: 30, C: 5 }, { A: 20, B: 10, C: 7 }, ['A', 'B', 'C']), { A: 30, B: 10, C: 5 });
assert.deepEqual(presetPct({ A: 30, X: 1 }, { A: 20 }, ['A', 'B']), { A: 30, B: 0 }, 'unknown keys ignored, missing base is 0');
console.log('preset tests: OK');

// pollSigma: proportional to the average. Final-week r by default, today's r with mode 'now', 0.12 without a model.
const fixPolls = { average: { A: 30, C: 0.1 }, method: { sigma_rel_final: 0.1, sigma_rel_now: 0.15 } };
const fs = pollSigma(fixPolls, [{ party_id: 'A' }, { party_id: 'D', default_pct: 8 }]);
near(fs.A, 3, 1e-9, '10 % of 30');
near(fs.C, 0.01, 1e-9, 'tiny party: tiny sigma (proportional)');
near(pollSigma(fixPolls, [], 'now').A, 4.5, 1e-9, "mode 'now'");
near(pollSigma({ average: { A: 30 } }, []).A, 3.6, 1e-9, 'default r 0.12');
assert.equal(fs.D, 2, 'provincial party without an average: 25 % of default_pct');
const realPolls = JSON.parse(readFileSync(new URL('../data/polls.json', import.meta.url), 'utf8'));
const realParties = JSON.parse(readFileSync(new URL('../data/party_elasticity.json', import.meta.url), 'utf8')).parties;
const realSigma = pollSigma(realPolls, realParties);
for (const p of realParties) assert.ok(Number.isFinite(realSigma[p.party_id]) && realSigma[p.party_id] > 0, `real sigma ${p.party_id}`);
console.log('poll sigma tests: OK');

// Monte Carlo: same seed -> same result; every run seats all 40 seats; intervals ordered; pact probability in [0, 1].
const mcParties = parties.map((p) => (p.party_id === 'otros' ? { ...p, seats_eligible: false } : p));
const mcState = { nationalPct: nat, parties: mcParties, provinces: provs.map((p) => ({ ...p, seats: 10 })), alliances: {}, pact: ['pp', 'reg'] };
const SIG = { pp: 1.5, psoe: 1.5, reg: 0.5, otros: 0.3 };
const mcA = monteCarlo(mcState, 60, mulberry32(7), SIG);
assert.deepEqual(monteCarlo(mcState, 60, mulberry32(7), SIG), mcA, 'same seed, same result');
assert.notDeepEqual(monteCarlo(mcState, 60, mulberry32(8), SIG), mcA, 'other seed, other result');
for (const { c } of mcRuns(mcState, 20, mulberry32(3), SIG)) assert.equal(sum(c), 40, 'every run seats all 40');
for (const { lo, p50, hi } of Object.values(mcA.seats)) assert.ok(lo <= p50 && p50 <= hi, 'interval ordered');
assert.ok(mcA.pact >= 0 && mcA.pact <= 1, 'pact probability in [0,1]');
near(sum(Object.values(mcA.seats).map((s) => s.p50)), 40, 3, 'medians near 40 seats');
// Noise follows sigma: a wider sigma for pp gives a wider 97.5 % interval.
const wide = monteCarlo(mcState, 200, mulberry32(7), { ...SIG, pp: 6 });
const narrow = monteCarlo(mcState, 200, mulberry32(7), { ...SIG, pp: 0.2 });
assert.ok(wide.seats.pp.hi - wide.seats.pp.lo > narrow.seats.pp.hi - narrow.seats.pp.lo, 'sigma widens the interval');
// summarize: 3 runs, quantiles picked by index floor(p*(n-1)); pact sums 350, 280, 176 -> all >= 176.
const sm = summarize([{ a: 200, b: 150 }, { a: 100, b: 180 }, { a: 176 }], ['a', 'b']);
assert.deepEqual(sm.seats.a, { lo: 100, p5: 100, p50: 176, p95: 176, hi: 176, mean: (200 + 100 + 176) / 3 });
assert.deepEqual(sm.seats.b, { lo: 0, p5: 0, p50: 150, p95: 150, hi: 150, mean: 110 });
assert.equal(sm.pact, 1);
assert.equal(summarize([{ a: 1 }], []).pact, null, 'no pact, no pact probability');
console.log('monte carlo tests: OK');

// Blocs: coalitions count with their host; every seat lands in exactly one bloc.
assert.equal(blocOf('psoe'), 'izquierda');
assert.equal(blocOf('pp'), 'derecha');
assert.equal(blocOf('pnv'), 'otros');
assert.equal(blocOf('pp+junts'), 'derecha', 'coalition takes its host bloc');
assert.equal(blocOf('junts+psoe'), 'otros', 'host junts, not psoe, decides');
assert.deepEqual(blocSeats({ psoe: 120, 'pp+vox': 150, pnv: 6, 'junts+psoe': 7, otros: 3 }), { izquierda: 120, derecha: 150, otros: 16 });
assert.deepEqual(blocSeats({}), { izquierda: 0, derecha: 0, otros: 0 });
console.log('bloc tests: OK');

// CSV: TOTAL rows sum seats, PACTO sums the pact, MC columns empty unless a summary is given.
{
  const R = { contenders: { a: { name: 'A' }, b: { name: 'B' } }, national: { votes: { a: 600, b: 400 }, seats: { a: 2, b: 1 } },
    provinces: { p: { votes: { a: 600, b: 400 }, pct: { a: 60, b: 40 }, seats: { a: 2, b: 1 } } } };
  const SN = { seats: { a: 3, b: 1 }, provinces: { p: { seats: { a: 1 } } } };
  const provs = [{ id: 'p', name: 'Prov' }];
  const plain = csvRows({ R, SN, provinces: provs, pact: ['a', 'b'], mc: null });
  assert.equal(plain[0].length, 11);
  assert.deepEqual(plain[1], ['p', 'Prov', 'A', '60.00', 600, 2, 1, '', '', '', '']);
  assert.deepEqual(plain[3], ['', 'TOTAL', 'A', '60.00', 600, 2, 3, '', '', '', '']);
  assert.deepEqual(plain[4], ['', 'TOTAL', 'B', '40.00', 400, 1, 1, '', '', '', '']);
  assert.deepEqual(plain[5], ['', 'PACTO', 'A + B', '', '', 3, 4, '', '', '', '']);
  assert.equal(csvRows({ R, SN, provinces: provs, pact: [], mc: null, overrides: { p: { a: 55 } } })[1][10], 55, 'fixed pct in its own column');
  const mc = { seats: { a: { p5: 1, p50: 2, p95: 3 } } };
  const withMc = csvRows({ R, SN, provinces: provs, pact: [], mc });
  assert.equal(withMc.length, 5, 'no PACTO row without pact');
  assert.deepEqual(withMc[3].slice(7), [1, 2, 3, '']);
  assert.deepEqual(withMc[4].slice(7), ['', '', '', '']);
}
console.log('csv tests: OK');

// solveTarget: bisection on one party's % (rebalance keeps 100 %). Monotone in the party's %, minimal within 0.05, null when impossible.
{
  const tp = [
    { party_id: 'A', name: 'A', color: '#000', order: 1, elasticity_vector: { '01': 1, '02': 1 } },
    { party_id: 'B', name: 'B', color: '#111', order: 2, elasticity_vector: { '01': 1, '02': 1 } },
    { party_id: 'otros', name: 'Otros', color: '#999', order: 99, seats_eligible: false, elasticity_vector: { '01': 1, '02': 1 } },
  ];
  const tprov = [{ id: '01', seats: 5, expected_census: 100000 }, { id: '02', seats: 7, expected_census: 200000 }];
  const st = { pct: { A: 30, B: 50, otros: 20 }, source: 'prop', parties: tp, provinces: tprov, alliances: {} };
  const seatsAt = (id, v) => simulate({ ...st, nationalPct: rebalance(st.pct, id, v, st.source) }).national.seats;
  const before = structuredClone(st);

  const grid = Array.from({ length: 31 }, (_, k) => 20 + k);
  const sA = grid.map((v) => seatsAt('A', v).A || 0);
  sA.slice(1).forEach((x, k) => assert.ok(x >= sA[k], `A seats monotone at ${grid[k + 1]}`));

  const fn = (s) => (s.A || 0) >= 6;
  const r = solveTarget(st, fn, 'A');
  assert.ok(r && r.pct > 0.05 && r.pct <= 60, 'reachable target gives a pct');
  assert.ok(fn(r.seats), 'result reaches the target');
  assert.deepEqual(r.seats, seatsAt('A', r.pct), 'seats are those of the returned pct');
  assert.ok(!fn(seatsAt('A', r.pct - 0.05)), '0.05 pts less does not reach it');

  assert.equal(solveTarget(st, (s) => (s.A || 0) >= 100, 'A'), null, 'impossible target gives null');
  assert.equal(solveTarget(st, () => true, 'A').pct, 0, 'already met at lo gives lo');
  assert.deepEqual(st, before, 'input state is not mutated');
}
console.log('solveTarget tests: OK');

// Coalition chamber: forChamber filters alliances per chamber (missing field = ambos); a senate-only coalition
// leaves the Congreso simulation alone and shows up in the senate pass only.
const chAl = {
  national: [
    { host: 'pp', chamber: 'congreso', members: [{ party: 'pp', toCoalition: 100 }, { party: 'psoe', toCoalition: 100 }] },
    { host: 'vox', members: [{ party: 'vox', toCoalition: 100 }, { party: 'otros', toCoalition: 100 }] },
  ],
  provincial: { '01': [{ host: 'pp', chamber: 'senado', members: [{ party: 'pp', toCoalition: 100 }, { party: 'vox', toCoalition: 100 }] }] },
};
assert.deepEqual(forChamber(chAl, 'congreso').national.map((a) => a.host), ['pp', 'vox']);
assert.deepEqual(forChamber(chAl, 'senado').national.map((a) => a.host), ['vox']);
assert.deepEqual(forChamber(chAl, 'congreso').provincial, { '01': [] });
assert.equal(forChamber(chAl, 'senado').provincial['01'].length, 1);
const chParties = ['pp', 'psoe', 'vox', 'otros'].map((id, k) => ({ party_id: id, name: id, color: '#000', order: k, seats_eligible: id !== 'otros', elasticity_vector: ones(['01', '02']) }));
const chProvs = [{ id: '01', seats: 10, expected_census: 1e5 }, { id: '02', seats: 10, expected_census: 1e5 }];
const chNat = { pp: 33, psoe: 30, vox: 15, otros: 22 };
const chRun = (al) => simulate({ nationalPct: chNat, parties: chParties, provinces: chProvs, alliances: al });
const chNone = chRun({ national: [], provincial: {} });
const chCong = chRun(forChamber({ national: [], provincial: { '01': chAl.provincial['01'] } }, 'congreso'));
const chSen = chRun(forChamber({ national: [], provincial: { '01': chAl.provincial['01'] } }, 'senado'));
assert.deepEqual(chCong.national.seats, chNone.national.seats, 'senate-only provincial coalition does not touch Congreso');
assert.ok(chCong.provinces['01'].votes.vox > 0);
assert.equal(chSen.provinces['01'].votes.vox, undefined, 'senate pass uses the coalition');
const chSenData = { constituencies: [{ id: 'c1', province_id: '01', seats: 4, marks: 4 }, { id: 'c2', province_id: '02', seats: 2, marks: 2 }], designated: { by_community: [] } };
assert.ok(!simulateSenate(chSen, chSenData, chProvs).byConstituency.c1.vox, 'senate-only coalition: vox runs inside pp in c1');

// cleanState: missing or unknown chamber -> ambos; congreso/senado kept.
const csAl = (chamber) => ({ host: 'a', members: [{ party: 'a', toCoalition: 50 }, { party: 'b', toCoalition: 50 }], ...(chamber === undefined ? {} : { chamber }) });
const csOut = cleanState({ pct: { a: 1, b: 1 }, alliances: { national: [csAl(), csAl('senado'), csAl('x')], provincial: {} }, pact: [] }, ['a', 'b']);
assert.deepEqual(csOut.alliances.national.map((a) => a.chamber), ['ambos', 'senado', 'ambos']);

console.log('chamber tests: OK');

// Provincial overrides: fixed values kept, the rest proportional, rows sum exactly 100.
{
  const o = applyOverrides({ a: 50, b: 30, c: 20, otros: 0 }, { a: 10 });
  assert.equal(o.a, 10);
  near(o.b, 54, 1e-9, 'b keeps its share of the rest');
  near(o.c, 36, 1e-9, 'c keeps its share of the rest');
  assert.equal(o.otros, 0);
  assert.throws(() => applyOverrides({ a: 50, b: 50 }, { a: 60, b: 50 }), /suman más de 100/);
  assert.deepEqual(applyOverrides({ a: 100, b: 0, otros: 0 }, { a: 40 }), { a: 40, b: 0, otros: 60 }, 'others at 0: remainder to otros');
  const rng = mulberry32(3);
  const ids = ['a', 'b', 'c', 'd', 'e', 'otros'];
  for (let t = 0; t < 500; t++) {
    const base = Object.fromEntries(ids.map((id) => [id, rng() * 30]));
    const row = Object.fromEntries(ids.map((id) => [id, (100 * base[id]) / sum(base)]));
    const ov = Object.fromEntries(ids.filter(() => rng() < 0.3).map((id) => [id, (rng() * 100) / ids.length]));
    const free = ids.filter((id) => !(id in ov));
    if (!free.length) continue; // every party fixed: nothing left to absorb the remainder
    const out = applyOverrides(row, ov);
    near(sum(out), 100, 1e-9, 'row sums 100');
    for (const id of Object.keys(ov)) assert.equal(out[id], ov[id], 'overridden value kept');
    if (free.length >= 2) near(out[free[0]] / out[free[1]], row[free[0]] / row[free[1]], 1e-9, 'rest proportional');
  }
  const ns = nationalShares({ provinces: { p1: { raw: { a: 10 } }, p2: { raw: { a: 150 } } } }, [{ id: 'p1', expected_census: 100 }, { id: 'p2', expected_census: 300 }]);
  near(ns.a, 40, 1e-9, 'national share is census-weighted');
  const ovSim = simulate({ nationalPct: nat, parties, provinces: provs, alliances: {}, overrides: { [provs[0].id]: { pp: 20 } } });
  near((100 * ovSim.provinces[provs[0].id].raw.pp) / provs[0].expected_census, 20, 1e-9, 'simulate applies the override');
}
console.log('override tests: OK');

// except: a national coalition skips the listed provinces.
const exAl = { national: [{ host: 'pp', except: ['02'], members: [{ party: 'pp', toCoalition: 100 }, { party: 'vox', toCoalition: 100 }] }], provincial: {} };
assert.equal(activeAlliances(exAl, '01').length, 1);
assert.equal(activeAlliances(exAl, '02').length, 0, 'excepted province');

// scenarioProb over joint Congreso + Senado runs.
const spRecs = [...mcRuns({ nationalPct: chNat, parties: chParties, provinces: chProvs, alliances: { national: [], provincial: {} } }, 50, mulberry32(5), {}, { data: chSenData, alliances: { national: [], provincial: {} } })];
assert.ok(spRecs.every((r) => sum(r.c) === 20 && r.s && r.ps['01']), 'joint runs carry both chambers');
const sp = scenarioProb(spRecs, [{ scope: 'nacional', chamber: 'congreso', parties: ['pp', 'psoe', 'vox'], op: '=', n: 20 }, { scope: '01', chamber: 'congreso', parties: ['pp'], op: '>=', n: 99 }]);
assert.deepEqual(sp, { all: 0, each: [1, 0] });
assert.equal(scenarioProb(spRecs, [{ scope: '01', chamber: 'senado', parties: ['pp', 'psoe', 'vox'], op: '=', n: 4 }]).all, 1, 'senate seats per province');
console.log('scenarioProb tests: OK');

// Senate strategic vote: 12 % of Vox moves to PP before the limited vote (100/100 -> 112/88: PP takes 3 of 4).
const leakRes = { provinces: { '01': { votes: { pp: 100, vox: 100 } } }, contenders: { pp: {}, vox: {} } };
const leakSen = simulateSenate(leakRes, { constituencies: [{ id: 'c', province_id: '01', seats: 4, marks: 3 }], designated: { by_community: [] } }, [{ id: '01' }]);
assert.deepEqual(leakSen.elected, { pp: 3, vox: 1 });
console.log('senate leak tests: OK');

{
  const { withTurnout, turnoutPct } = await import('../js/engine.js');
  const provs = [{ id: 'a', census: 1000, expected_census: 500 }, { id: 'b', census: 3000, expected_census: 2400 }];
  const up = withTurnout(provs, 0.8);
  const tot = (ps) => ps.reduce((s, p) => s + p.expected_census, 0) / 4000;
  if (Math.abs(tot(up) - 0.8) > 0.002) throw new Error('turnout target');
  if (!(up[0].expected_census - 500 > up[1].expected_census - 2400 - 600)) throw new Error('low-turnout province moves');
  if (Math.abs(tot(withTurnout(provs, 0.725)) - 0.725) > 0.002) throw new Error('turnout identity');
  const m = turnoutPct({ x: 30, y: 20, z: 50 }, 5, { x: 0.2 });
  if (!(m.x > 30) || Math.abs(m.x + m.y + m.z - 100) > 1e-9) throw new Error('turnoutPct');
  console.log('turnout tests: OK');
}
