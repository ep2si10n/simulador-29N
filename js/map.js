// Province map: d3.geoConicConformalSpain (Canarias inset). Uses the global d3 + topojson scripts.
const W = 800;
const H = 640;

/** opts: {tooltip(id) -> html, onClick(id)}. Returns {update(colorOf, selectedId)}. */
export function createMap(svgEl, tipEl, topo, opts) {
  const features = topojson.feature(topo, topo.objects.provinces).features.filter((f) => f.id !== '54');
  const projection = d3.geoConicConformalSpain().fitSize([W, H], { type: 'FeatureCollection', features });
  const svg = d3.select(svgEl).attr('viewBox', `0 0 ${W} ${H}`);
  const paths = svg.append('g').selectAll('path').data(features).join('path')
    .attr('class', 'prov')
    .attr('d', d3.geoPath(projection))
    .on('mousemove', (ev, f) => {
      tipEl.innerHTML = opts.tooltip(f.id);
      tipEl.hidden = false;
      const x = Math.min(ev.clientX + 14, window.innerWidth - tipEl.offsetWidth - 8);
      tipEl.style.left = `${x}px`;
      tipEl.style.top = `${ev.clientY + 14}px`;
    })
    .on('mouseleave', () => (tipEl.hidden = true))
    .on('click', (ev, f) => opts.onClick(f.id));
  svg.append('path').attr('class', 'composition').attr('d', projection.getCompositionBorders());

  return {
    count: features.length,
    update(colorOf, selected) {
      svg.classed('has-selection', !!selected);
      paths.attr('fill', (f) => colorOf(f.id)).classed('selected', (f) => f.id === selected);
      paths.filter((f) => f.id === selected).raise();
    },
  };
}
