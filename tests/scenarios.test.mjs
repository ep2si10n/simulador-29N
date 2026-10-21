// Run: node tests/scenarios.test.mjs
import assert from 'node:assert/strict';
import { cleanState, cleanScenario, cleanScenarios, newScenario, consequences, scenarioOdds, decodeShare, encodeShare, cleanView, diffResults, createHistory } from '../js/scenarios.js';
import { mulberry32 } from '../js/engine.js';

const ids = ['pp', 'psoe', 'vox', 'otros'];
const ally = { host: 'pp', members: [{ party: 'pp', toCoalition: 100, transfers: [] }, { party: 'vox', toCoalition: 80, transfers: [{ party: 'psoe', pct: 10 }] }] };

// Unknown party ids dropped from pct, alliances and pact; pct clamped; missing ids default to 0.
const st = cleanState({ pct: { pp: 150, psoe: -4, vox: 'x', fake: 9 }, alliances: { national: [ally, { host: 'zz', members: [] }, 'junk'], provincial: { '28': [ally], '08': 'junk' } }, pact: ['pp+vox', 'pp+zz', 42, 'psoe'] }, ids);
assert.deepEqual(st.pct, { pp: 100, psoe: 0, vox: 0, otros: 0 });
assert.deepEqual(st.alliances.national, [{ ...ally, chamber: 'ambos' }]);
assert.deepEqual(Object.keys(st.alliances.provincial), ['28']);
assert.deepEqual(st.pact, ['pp+vox', 'psoe']);

// Unknown transfer target is removed; an alliance that is invalid afterwards (member without partner) is dropped.
const kept = cleanState({ pct: {}, alliances: { national: [{ host: 'pp', members: [{ party: 'pp', toCoalition: 60, transfers: [] }, { party: 'vox', toCoalition: 50, transfers: [{ party: 'nope', pct: 5 }] }] }] } }, ids).alliances.national;
assert.equal(kept.length, 1);
assert.deepEqual(kept[0].members[1].transfers, []);
const lone = cleanState({ pct: {}, alliances: { national: [{ host: 'pp', members: [{ party: 'pp', toCoalition: 60, transfers: [] }, { party: 'nope', toCoalition: 5, transfers: [] }] }] } }, ids).alliances.national;
assert.deepEqual(lone, [], 'a single valid member is not a coalition');

// Hash-shaped garbage (the bug that broke app.js): alliance without members, provincial as {}, non-numeric pct, unknown pact id.
const junk = cleanState({ pct: { pp: 'abc', psoe: null, vox: NaN }, alliances: { national: [{ host: 'pp' }], provincial: {} }, pact: ['zz', 'pp+zz', 'vox'] }, ids);
assert.deepEqual(junk.alliances, { national: [], provincial: {} }, 'alliance without members dropped');
assert.deepEqual(junk.pct, { pp: 0, psoe: 0, vox: 0, otros: 0 }, 'non-numeric pct becomes 0');
assert.deepEqual(junk.pact, ['vox'], 'unknown pact ids dropped');
assert.deepEqual(cleanState({ pct: {}, alliances: { national: [], provincial: { '28': [{ host: 'pp', members: 'x' }] } } }, ids).alliances.provincial, {}, 'provincial alliance without members dropped');
assert.deepEqual(cleanState({ pct: {}, alliances: 'junk', pact: 'junk' }, ids).pact, [], 'non-array pact gives []');

assert.equal(cleanState(null, ids), null);
assert.equal(cleanState({ pct: 'x' }, ids), null);

// Scenario: needs a string id and a pct; name defaults; created defaults to 0.
assert.equal(cleanScenario({ name: 'x', pct: {} }, ids), null, 'no id');
assert.equal(cleanScenario({ id: 'a', pct: null }, ids), null, 'no pct');
const sc = cleanScenario({ id: 'a', name: '  ', created: 'hoy', pct: { pp: 30 } }, ids);
assert.equal(sc.name, 'Escenario');
assert.equal(sc.created, 0);
assert.equal(cleanScenario({ id: 'a', name: 'n'.repeat(80), created: 5, pct: {} }, ids).name.length, 60);

// List: non-arrays give [], malformed entries and duplicate ids are dropped.
assert.deepEqual(cleanScenarios(undefined, ids), []);
assert.deepEqual(cleanScenarios({ id: 'a' }, ids), []);
const list = cleanScenarios([{ id: 'a', pct: {} }, 'junk', { id: 'a', pct: { pp: 1 } }, { id: 'b', pct: {} }], ids);
assert.deepEqual(list.map((s) => s.id), ['a', 'b']);

// newScenario: deep copy (later edits do not leak) and a round trip through the cleaner keeps the data.
const work = { pct: { pp: 30, psoe: 25, vox: 10, otros: 35 }, alliances: { national: [structuredClone(ally)], provincial: {} }, pact: ['pp+vox'] };
const made = newScenario(work, 'Mi escenario', 1000);
work.pct.pp = 99;
work.alliances.national[0].host = 'x';
assert.equal(made.pct.pp, 30);
assert.equal(made.name, 'Mi escenario');
assert.equal(made.created, 1000);
assert.ok(typeof made.id === 'string' && made.id.length > 0);
const back = cleanScenario(JSON.parse(JSON.stringify(made)), ids);
assert.deepEqual(back.pct, made.pct);
assert.deepEqual(back.alliances, { national: [{ ...ally, chamber: 'ambos' }], provincial: {} });
assert.equal(work.alliances.national[0].host, 'x');
assert.deepEqual(back.pact, ['pp+vox']);
assert.notEqual(newScenario(work, 'a', 1000).id, newScenario(work, 'a', 1000).id, 'ids differ within one millisecond');

// consequences: pact 170 < 176 (faltan 6), PP falls 17 vs baseline, PSOE rises; no bloc majority.
const names = { pp: 'PP', psoe: 'PSOE', vox: 'Vox', 'pp+vox': 'PP + Vox' };
const fakeR = { national: { seats: { pp: 120, vox: 50, psoe: 150 } }, contenders: Object.fromEntries(Object.entries(names).map(([id, name]) => [id, { name }])) };
const base = { pp: 137, vox: 33, psoe: 121 };
const c1 = consequences(fakeR, base, ['pp', 'vox']);
assert.equal(c1.line, 'PP + Vox suman 170: no llegan (faltan 6); PP cae 17 escaños');
assert.equal(c1.pactSeats, 170);
assert.deepEqual(c1.largest, { id: 'psoe', name: 'PSOE', seats: 150 });
assert.deepEqual(c1.delta.map((x) => [x.name, x.d]), [['PSOE', 29], ['PP', -17], ['Vox', 17]], 'sorted by size');
assert.deepEqual(c1.blocs, { izquierda: 150, derecha: 170, otros: 0 }, 'bloc totals from blocSeats');
// No pact: a bloc with majority is announced; without drops only that line; otherwise the line says so.
const fakeR2 = { national: { seats: { pp: 100, vox: 80, psoe: 170 } }, contenders: fakeR.contenders };
assert.equal(consequences(fakeR2, { pp: 100, vox: 80, psoe: 170 }).line, 'Derecha suma 180: mayoría absoluta', 'no changes, no drops');
assert.equal(consequences(fakeR2, base).line, 'Derecha suma 180: mayoría absoluta; PP cae 37 escaños', 'drops listed after the bloc');
assert.equal(consequences(fakeR, { pp: 120, vox: 50, psoe: 150 }).line, 'Ningún bloque llega a 176', 'capitalized, no drops, no pact');

// scenarioOdds: same seed, same result; seat intervals ordered; pact probability in [0, 1]; no bloc odds.
const SIG = { pp: 2, psoe: 2, vox: 1, otros: 0.3 };
const mParties = [
  { party_id: 'pp', elasticity_vector: { '01': 1.2, '02': 0.9 } },
  { party_id: 'psoe', elasticity_vector: { '01': 0.9, '02': 1.1 } },
  { party_id: 'vox', elasticity_vector: { '01': 1, '02': 1 } },
  { party_id: 'otros', seats_eligible: false, elasticity_vector: { '01': 1, '02': 1 } },
];
const mProvs = [{ id: '01', seats: 20, expected_census: 300000 }, { id: '02', seats: 20, expected_census: 200000 }];
const mst = { nationalPct: { pp: 36, psoe: 31, vox: 14, otros: 19 }, parties: mParties, provinces: mProvs, alliances: {}, pact: ['pp', 'vox'] };
const so = scenarioOdds(mst, 40, mulberry32(5), SIG);
assert.deepEqual(scenarioOdds(mst, 40, mulberry32(5), SIG), so, 'same seed, same odds');
assert.deepEqual(Object.keys(so).sort(), ['pact', 'seats']);
assert.ok(so.pact >= 0 && so.pact <= 1);
assert.ok(so.seats.pp.lo <= so.seats.pp.p50 && so.seats.pp.p50 <= so.seats.pp.hi);

console.log('scenario tests: OK');

// Share hash: round trip, old hashes (p,a,c,g) still decode, bad input gives null.
const pl = { p: { pp: 30, psoe: 27 }, a: { national: [], provincial: {} }, c: 'senado', g: ['pp+vox'], v: 'escenarios', s: '28', m: 'vox' };
assert.deepEqual(decodeShare(encodeShare(pl)), pl);
assert.deepEqual(decodeShare('#s=' + btoa(encodeURIComponent(JSON.stringify({ p: { pp: 1 }, c: 'congreso' })))), { p: { pp: 1 }, c: 'congreso' });
assert.equal(decodeShare(''), null);
assert.equal(decodeShare('#s=%%%'), null);
assert.equal(decodeShare('#other'), null);

// View fields: valid kept, unknown province/map mode/tab dropped to defaults.
const provIds = ['28', '08'];
assert.deepEqual(cleanView(pl, provIds, ids), { view: 'escenarios', selected: '28', mapMode: 'vox' });
assert.deepEqual(cleanView({ v: 'x', s: '99', m: 'fake' }, provIds, ids), { view: 'hemi', selected: null, mapMode: 'ganador' });
assert.deepEqual(cleanView(null, provIds, ids), { view: 'hemi', selected: null, mapMode: 'ganador' });
console.log('share tests: OK');

// diffResults: seats sides, ids on one side count 0, flips only where the province winner changes.
const res = (seats, winners) => ({
  national: { seats },
  contenders: { pp: { name: 'PP' }, psoe: { name: 'PSOE' }, vox: { name: 'Vox' } },
  provinces: Object.fromEntries(Object.entries(winners).map(([p, w]) => [p, { winner: w }])),
});
const baseRes = res({ pp: 150, psoe: 120, vox: 30 }, { '28': 'pp', '08': 'psoe' });
const same = diffResults(baseRes, baseRes);
assert.deepEqual(same.flips, [], 'no changes: no flips');
assert.ok(same.seats.every((x) => x.d === 0), 'no changes: zero deltas');
const moved = diffResults(baseRes, res({ pp: 150, psoe: 119, vox: 30, otros: 1 }, { '28': 'pp', '08': 'vox' }));
assert.deepEqual(moved.flips, [{ prov: '08', a: 'psoe', b: 'vox' }]);
assert.deepEqual(moved.seats.find((x) => x.id === 'psoe'), { id: 'psoe', name: 'PSOE', a: 120, b: 119, d: -1 });
assert.deepEqual(moved.seats.find((x) => x.id === 'vox'), { id: 'vox', name: 'Vox', a: 30, b: 30, d: 0 });
const solo = diffResults(baseRes, res({ pp: 150, psoe: 120, vox: 30, pp2: 0 }, { '28': 'pp', '08': 'psoe' }));
assert.equal(solo.seats.some((x) => x.id === 'pp2'), false, 'ids with no seats on either side are not rows');
const gone = diffResults(res({ pp: 170, xx: 4 }, { '28': 'pp' }), res({ pp: 170 }, { '28': 'pp' }));
assert.deepEqual(gone.seats.find((x) => x.id === 'xx'), { id: 'xx', name: 'xx', a: 4, b: 0, d: -4 }, 'id only in A counts 0 in B');
console.log('diffResults tests: OK');
// createHistory: baseline, duplicate pushes ignored, undo/redo, redo dropped by a new push, limit 50.
const h = createHistory(50);
h.clear({ pct: { pp: 1 } });
assert.equal(h.canUndo, false);
assert.equal(h.undo(), null, 'nothing to undo at baseline');
h.push({ pct: { pp: 1 } });
assert.equal(h.canUndo, false, 'identical push ignored');
h.push({ pct: { pp: 2 } });
h.push({ pct: { pp: 3 } });
assert.deepEqual(h.undo(), { pct: { pp: 2 } });
assert.deepEqual(h.undo(), { pct: { pp: 1 } });
assert.equal(h.canUndo, false);
assert.deepEqual(h.redo(), { pct: { pp: 2 } });
assert.equal(h.canRedo, true);
h.push({ pct: { pp: 9 } });
assert.equal(h.canRedo, false, 'push after undo clears redo');
assert.deepEqual(h.undo(), { pct: { pp: 2 } });
const copy = h.redo(); copy.pct.pp = 77;
assert.deepEqual(h.undo(), { pct: { pp: 2 } }, 'returned states are copies');
const lim = createHistory(50);
lim.clear({ n: 0 });
for (let i = 1; i <= 60; i++) lim.push({ n: i });
let undone = 0;
while (lim.undo()) undone++;
assert.equal(undone, 50, 'at most 50 undo steps');
const h2 = createHistory(50); h2.clear({ n: 0 }); h2.push({ n: 1 }); h2.undo(); h2.redo(); assert.equal(h2.redo(), null);
console.log('createHistory tests: OK');

// A national coalition keeps its province exceptions (two-digit ids only).
const exc = cleanState({ pct: {}, alliances: { national: [{ ...ally, except: ['08', 'x', 17] }] } }, ids).alliances.national[0];
assert.deepEqual(exc.except, ['08']);
console.log('except tests: OK');
