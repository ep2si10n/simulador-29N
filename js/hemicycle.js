// Parliament half-ring: `total` dots in concentric rows, filled left to right by contender order.
const ROWS = 10;
const R = 100;
const R0 = 45;

function seatPositions(total) {
  const radii = d3.range(ROWS).map((k) => R0 + ((R - R0) * k) / (ROWS - 1));
  const rsum = d3.sum(radii);
  const counts = radii.map((r) => Math.round((total * r) / rsum));
  counts[ROWS - 1] += total - d3.sum(counts);
  const seats = [];
  radii.forEach((r, k) => {
    for (let j = 0; j < counts[k]; j++) {
      const a = Math.PI * (1 - j / (counts[k] - 1));
      seats.push({ a, r, x: r * Math.cos(a), y: -r * Math.sin(a) });
    }
  });
  return seats.sort((s, t) => t.a - s.a || s.r - t.r);
}

/** Returns {update(list, total, majority)} with list = [{color, seats}] already sorted by order. */
export function createHemicycle(svgEl) {
  const dot = (0.42 * (R - R0)) / (ROWS - 1);
  const svg = d3.select(svgEl).attr('viewBox', `${-R - dot} ${-R - dot - 14} ${2 * (R + dot)} ${R + 2 * dot + 14}`);
  const seatsG = svg.append('g');
  svg.append('line').attr('class', 'majority').attr('x1', 0).attr('x2', 0).attr('y1', -R - dot).attr('y2', dot);
  const label = svg.append('text').attr('class', 'majority-label').attr('x', 0).attr('y', -R - dot - 4);
  return {
    update(list, total, majority) {
      const colors = list.flatMap((c) => Array(c.seats).fill(c.color));
      const circles = seatsG.selectAll('circle').data(seatPositions(total)).join('circle')
        .attr('cx', (s) => s.x).attr('cy', (s) => s.y).attr('r', dot);
      circles.transition().duration(350).style('fill', (_, i) => colors[i] ?? 'var(--seat-empty)');
      label.text(majority);
    },
  };
}
