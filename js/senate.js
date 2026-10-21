// Senado: voto limitado por circunscripción + designados fijos por CCAA (senate.json). Pure, sin DOM.

// Voto útil en el Senado: parte del voto de Congreso de la derecha minoritaria marca a los candidatos del PP.
// Ajustado sobre 2016, abr-2019, nov-2019 y 2023 (votos reales de Congreso -> senadores reales): con 12 % de fuga y ruido
// provincial total 0,12 (0,08 compartido con el Congreso en el Montecarlo + SENATE_NOISE) el error cuadrático de PP+PSOE
// cae de 1365 a 192. La fuga de la izquierda minoritaria al PSOE salió 0.
export const SENATE_LEAK = 0.12;
const LEAK_FROM = ['vox', 'salf'];
const LEAK_TO = 'pp';
export const SENATE_NOISE = 0.09;
const normal = (rng) => Math.sqrt(-2 * Math.log(1 - rng())) * Math.cos(2 * Math.PI * rng());

/**
 * congressResult: simulate() output (votes already after alliances). senateData: data/senate.json. provinces: [{id}].
 * Senate votes = Congreso votes with SENATE_LEAK of LEAK_FROM moved to LEAK_TO (when both run separately there), times
 * exp(Normal(0, SENATE_NOISE)) per contender when `rng` is given (Monte Carlo).
 * Limited vote: each contender fields min(marks, seats) candidates, each with the contender's votes; the top `seats`
 * candidates win (ties: more votes). "otros" (contenders with eligible=false) never gets seats.
 * Returns {seats: {id: n} (elected + designated), elected, designated: {id: n}, byConstituency: {cid: {id: n}},
 *          provinces: {id: {seats, winner}}, total}.
 * ponytail: islands share their province's votes (no per-island vote data); add island-level votes when they exist.
 */
export function simulateSenate(congressResult, senateData, provinces, rng = null) {
  const add = (m, id, n) => (m[id] = (m[id] || 0) + n);
  const out = { seats: {}, elected: {}, designated: {}, byConstituency: {}, provinces: {}, total: 0 };
  const eligible = (id) => congressResult.contenders[id]?.eligible !== false;
  for (const pr of provinces) {
    const votes = { ...congressResult.provinces[pr.id].votes };
    if (votes[LEAK_TO]) for (const id of LEAK_FROM) if (votes[id]) { votes[LEAK_TO] += SENATE_LEAK * votes[id]; votes[id] *= 1 - SENATE_LEAK; }
    if (rng) for (const id of Object.keys(votes)) votes[id] *= Math.exp(SENATE_NOISE * normal(rng));
    const contenders = Object.keys(votes).filter(eligible);
    const provSeats = {};
    for (const c of senateData.constituencies.filter((c) => c.province_id === pr.id)) {
      const cands = contenders
        .flatMap((id) => Array(Math.min(c.marks, c.seats)).fill(id))
        .sort((a, b) => votes[b] - votes[a]);
      const won = {};
      for (const id of cands.slice(0, c.seats)) add(won, id, 1);
      out.byConstituency[c.id] = won;
      for (const [id, n] of Object.entries(won)) {
        add(provSeats, id, n);
        add(out.elected, id, n);
      }
    }
    const winner = Object.keys(provSeats).sort((a, b) => provSeats[b] - provSeats[a] || votes[b] - votes[a])[0] ?? null;
    out.provinces[pr.id] = { seats: provSeats, winner };
  }
  for (const c of senateData.designated.by_community) {
    for (const [id, n] of Object.entries(c.by_party)) add(out.designated, id, n);
  }
  for (const map of [out.elected, out.designated]) for (const [id, n] of Object.entries(map)) add(out.seats, id, n);
  out.total = Object.values(out.seats).reduce((a, b) => a + b, 0);
  return out;
}
