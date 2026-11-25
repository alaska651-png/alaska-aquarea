export const format = (value, digits = 1) => Number.isFinite(value)
  ? value.toLocaleString('it-IT', { minimumFractionDigits: digits, maximumFractionDigits: digits })
  : '—';

const timeFormat = new Intl.DateTimeFormat('it-IT', { timeZone: 'Europe/Rome', hour: '2-digit', minute: '2-digit' });
const dayFormat = new Intl.DateTimeFormat('it-IT', { timeZone: 'Europe/Rome', day: 'numeric', month: 'short' });
const fullTime = new Intl.DateTimeFormat('it-IT', {
  timeZone: 'Europe/Rome', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', timeZoneName: 'short',
});
export const pointDate = (time, hourly) => (hourly ? fullTime : dayFormat).format(time);
const H = 158;
const PAD = { l: 36, r: 12, t: 13, b: 24 };
const empty = '<p class="empty">Nessun valore disponibile per questa vista e questo periodo.</p>';

function yDomain(values, zero) {
  let min = Math.min(...values);
  let max = Math.max(...values);
  if (zero) { min = Math.min(0, min); max = Math.max(0, max); }
  if (min === max) {
    if (zero && min === 0) max = 1;
    else { min -= 1; max += 1; }
  }
  const rough = (max - min) / 4;
  const power = 10 ** Math.floor(Math.log10(rough));
  const step = [1, 2, 2.5, 5, 10].map(n => n * power).find(n => n >= rough);
  const low = Math.floor(min / step) * step;
  const high = Math.ceil(max / step) * step;
  const ticks = Array.from({ length: Math.round((high - low) / step) + 1 }, (_, i) => low + step * i);
  return { low, high, ticks, digits: step >= 1 && Number.isInteger(step) ? 0 : step >= .1 ? 1 : 2 };
}

function stageMarkup(title) {
  return '<div class="chart-stage" tabindex="0" role="group" aria-label="' + title
    + '. Usa le frecce per esplorare i dati." aria-keyshortcuts="ArrowLeft ArrowRight Home End Escape">'
    + '<div class="chart-tooltip" aria-live="polite" hidden></div></div>';
}

function interact(stage, dimensions, points, describe, nearest) {
  const tooltip = stage.querySelector('.chart-tooltip');
  const overlay = stage.querySelector('.hover-mark');
  let index = -1;
  function hide() {
    tooltip.hidden = true;
    overlay.setAttribute('visibility', 'hidden');
    index = -1;
  }
  function show(nextIndex) {
    if (index === nextIndex || nextIndex < 0) return;
    index = nextIndex;
    const point = points[index];
    tooltip.innerHTML = describe(point);
    tooltip.hidden = false;
    const maxLeft = Math.max(0, dimensions.width - tooltip.offsetWidth);
    tooltip.style.left = Math.max(0, Math.min(maxLeft, point.x + 12)) + 'px';
    tooltip.style.top = Math.max(0, Math.min(point.y - 10, dimensions.height - tooltip.offsetHeight)) + 'px';
    const line = overlay.querySelector('line');
    if (line) {
      line.setAttribute('x1', point.x);
      line.setAttribute('x2', point.x);
    }
    const circle = overlay.querySelector('circle');
    circle.setAttribute('cx', point.x);
    circle.setAttribute('cy', point.y);
    overlay.setAttribute('visibility', 'visible');
  }
  function pointer(event) {
    const box = stage.getBoundingClientRect();
    show(nearest(event.clientX - box.left, event.clientY - box.top));
  }
  stage.onpointermove = event => { if (event.pointerType === 'mouse' || event.pointerType === 'pen') pointer(event); };
  stage.onpointerdown = pointer;
  stage.onpointerleave = hide;
  stage.onblur = hide;
  stage.onkeydown = event => {
    const last = points.length - 1;
    let next;
    switch (event.key) {
      case 'ArrowRight': next = Math.min(last, index + 1); break;
      case 'ArrowLeft': next = index < 0 ? last : Math.max(0, index - 1); break;
      case 'Home': next = 0; break;
      case 'End': next = last; break;
      case 'Escape': hide(); return;
      default: return;
    }
    event.preventDefault();
    show(next);
  };
}

function svgRoot(width, height, content) {
  return '<svg aria-hidden="true" viewBox="0 0 ' + width + ' ' + height + '">' + content + '</svg>';
}

function yGrid(domain, y, width) {
  return domain.ticks.map(t => '<line class="gridline" x1="' + PAD.l + '" x2="' + (width - PAD.r)
    + '" y1="' + y(t) + '" y2="' + y(t) + '"/><text x="' + (PAD.l - 7) + '" y="' + (y(t) + 3)
    + '" text-anchor="end">' + format(t, domain.digits) + '</text>').join('');
}

function drawLines(bins, series, x, y, baseline) {
  return series.map(item => {
    const segments = [];
    let segment = [];
    for (const [i, bin] of bins.entries()) {
      if (Number.isFinite(bin[item.key])) segment.push({ x: x(i), y: y(bin[item.key]) });
      else if (segment.length) { segments.push(segment); segment = []; }
    }
    if (segment.length) segments.push(segment);
    return segments.map(points => {
      const path = points.map(({ x: px, y: py }, index) => (index ? 'L' : 'M') + px + ',' + py).join(' ');
      const fill = item.area && points.length > 1
        ? '<path d="' + path + ' L' + points.at(-1).x + ',' + baseline + ' L' + points[0].x + ',' + baseline + ' Z" fill="' + item.color + '" opacity=".11"/>'
        : '';
      const marks = points.length < 10 ? points.map(p => '<circle cx="' + p.x + '" cy="' + p.y + '" r="2.8" fill="' + item.color + '"/>').join('') : '';
      return fill + '<path d="' + path + '" fill="none" stroke="' + item.color + '" stroke-width="2.1" stroke-linejoin="round" stroke-linecap="round"/>' + marks;
    }).join('');
  }).join('');
}

function drawBars(bins, series, x, y, step) {
  const width = Math.min(20, step * .78 / series.length);
  return bins.map((bin, i) => series.map((item, j) => {
    const value = bin[item.key];
    if (!Number.isFinite(value) || value === 0) return '';
    const left = x(i) + (j - (series.length - 1) / 2) * width - width * .47;
    return '<rect x="' + left + '" y="' + Math.min(y(value), y(0)) + '" width="' + (width * .91)
      + '" height="' + Math.abs(y(value) - y(0)) + '" rx="' + Math.min(width / 4, 2) + '" fill="' + item.color + '"/>';
  }).join('')).join('');
}

export function renderTimeChart(container, title, subtitle, bins, series, { kind = 'line', hourly = false, unit = '', digits = 1, bounds } = {}) {
  const values = bins.flatMap(bin => series.map(item => bin[item.key]).filter(Number.isFinite));
  const legend = '<div class="legend">' + series.map(item => '<span><i class="swatch" style="--swatch:' + item.color
    + '"></i>' + item.label + '</span>').join('') + '</div>';
  container.innerHTML = '<div class="card-head"><div><h2>' + title + '</h2><p>' + subtitle + '</p></div>'
    + '<span class="chip">' + (hourly ? 'Orario' : 'Giornaliero') + '</span></div>'
    + legend + (values.length ? stageMarkup(title) : empty);
  if (!values.length) return;
  const stage = container.querySelector('.chart-stage');
  const tooltip = stage.querySelector('.chart-tooltip');
  const width = stage.clientWidth;
  const domain = bounds ? { ...bounds, digits: 0 } : yDomain(values, kind === 'bar');
  const height = H;
  const step = (width - PAD.l - PAD.r) / bins.length;
  const x = index => PAD.l + step * (index + .5);
  const y = value => height - PAD.b - (value - domain.low) / (domain.high - domain.low) * (height - PAD.t - PAD.b);
  const labelCount = Math.min(bins.length, width < 400 ? 5 : 7);
  const axis = Array.from({ length: labelCount }, (_, i) => {
    const index = labelCount === 1 ? 0 : Math.round(i * (bins.length - 1) / (labelCount - 1));
    const date = (hourly ? timeFormat : dayFormat).format(bins[index].time);
    const labelX = Math.max(PAD.l, Math.min(width - 22, x(index)));
    return '<text x="' + labelX + '" y="' + (height - 5) + '" text-anchor="middle">' + date + '</text>';
  }).join('');
  const marks = kind === 'bar' ? drawBars(bins, series, x, y, step) : drawLines(bins, series, x, y, y(domain.low));
  const focusMark = '<g class="hover-mark" visibility="hidden"><line class="guide" y1="' + PAD.t + '" y2="' + (height - PAD.b)
    + '"/><circle r="4" fill="#214c3d" stroke="white" stroke-width="1.5"/></g>';
  tooltip.insertAdjacentHTML('beforebegin', svgRoot(width, height, yGrid(domain, y, width) + axis + marks + focusMark));
  const points = bins.map((bin, i) => {
    const value = series.map(item => bin[item.key]).find(Number.isFinite);
    return { x: x(i), y: Number.isFinite(value) ? y(value) : y(domain.low), bin };
  });
  interact(stage, { width, height }, points, ({ bin }) =>
    '<strong>' + pointDate(bin.time, hourly) + '</strong>' + series.map(item => '<div><span>' + item.label + '</span><b>'
      + format(bin[item.key], digits) + (Number.isFinite(bin[item.key]) && unit ? ' ' + unit : '') + '</b></div>').join(''),
    px => Math.max(0, Math.min(points.length - 1, Math.floor((px - PAD.l) / step))));
}

export function renderScatter(container, data, name) {
  container.innerHTML = data.length ? stageMarkup('Confronto tra ' + name + ' e COP') : empty;
  if (!data.length) return;
  const stage = container.querySelector('.chart-stage');
  const tooltip = stage.querySelector('.chart-tooltip');
  const width = stage.clientWidth;
  const height = 169;
  const dx = yDomain(data.map(p => p.x), false);
  const dy = yDomain(data.map(p => p.y), false);
  const x = value => PAD.l + (value - dx.low) / (dx.high - dx.low) * (width - PAD.l - PAD.r);
  const y = value => height - PAD.b - (value - dy.low) / (dy.high - dy.low) * (height - PAD.t - PAD.b);
  const points = data.map(point => ({ x: x(point.x), y: y(point.y), point }));
  const dots = points.map(p => '<circle cx="' + p.x + '" cy="' + p.y + '" r="3.1" fill="#398366" fill-opacity=".62"/>').join('');
  const axis = dx.ticks.map(t => '<text x="' + x(t) + '" y="' + (height - 5) + '" text-anchor="middle">' + format(t, dx.digits) + '°</text>').join('');
  const marker = '<g class="hover-mark" visibility="hidden"><circle r="5" fill="#214c3d" stroke="white" stroke-width="1.5"/></g>';
  tooltip.insertAdjacentHTML('beforebegin', svgRoot(width, height, yGrid(dy, y, width) + axis + dots + marker));
  interact(stage, { width, height }, points, ({ point }) => '<strong>' + pointDate(point.time, true) + '</strong><div><span>'
    + name + '</span><b>' + format(point.x) + ' °C</b></div><div><span>COP</span><b>' + format(point.y, 2) + '</b></div>',
    (px, py) => {
      let selected = 0, best = Infinity;
      for (const [i, p] of points.entries()) {
        const distance = (p.x - px) ** 2 + (p.y - py) ** 2;
        if (distance < best) { best = distance; selected = i; }
      }
      return selected;
    });
}
