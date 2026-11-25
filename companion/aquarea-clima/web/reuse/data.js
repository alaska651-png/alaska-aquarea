export const hour = 3_600_000;
const minute = 60_000;
const day = 24 * hour;
const rome = new Intl.DateTimeFormat('sv-SE', {
  timeZone: 'Europe/Rome', year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', hourCycle: 'h23',
});
const fieldNames = {
  mandata_c: 'flow', ritorno_c: 'return', esterna_c: 'out', buffer_c: 'buffer', acs_c: 'tank',
  calore_risc_kwh: 'heatQ', elettrico_risc_kwh: 'heatE', calore_acs_kwh: 'dhwQ', elettrico_acs_kwh: 'dhwE',
  fv_kwh: 'pv', prelievo_kwh: 'gridIn', immissione_kwh: 'gridOut', soc_pct: 'soc',
};
const temperatureKeys = ['flow', 'return', 'out', 'buffer', 'tank'];
const gridKeys = ['pv', 'gridIn', 'gridOut'];
export const modes = {
  riscaldamento: { q: 'heatQ', e: 'heatE', storage: 'buffer' },
  acs: { q: 'dhwQ', e: 'dhwE', storage: 'tank' },
};

export const romeDay = time => rome.format(time).slice(0, 10);
const romeHour = time => Number(rome.formatToParts(time).find(part => part.type === 'hour').value);
const dateStep = (date, count) => new Date(Date.parse(date + 'T00:00:00Z') + count * day).toISOString().slice(0, 10);
export function localMidnight(date) {
  const utcMidnight = Date.parse(date + 'T00:00:00Z');
  const probe = utcMidnight - 3 * hour;
  return probe + (24 - romeHour(probe)) * hour;
}
const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
const round = value => Math.round(value * 1000) / 1000;

export function makeDemo() {
  const start = Date.parse('2026-10-11T00:00:00+02:00');
  const end = Date.parse('2026-11-10T00:00:00+01:00');
  const rows = [];
  let battery = 4.4;
  for (let time = start; time < end; time += hour) {
    const n = Math.floor((Date.parse(romeDay(time) + 'T00:00:00Z') - Date.parse('2026-10-11T00:00:00Z')) / day);
    const h = romeHour(time);
    const out = 10.8 - n * .085 + 2.7 * Math.sin(n * .7) + 3.1 * Math.sin((h - 9) * Math.PI / 12);
    const dhw = h === 12 || (h === 19 && n % 3 !== 0);
    const heat = !dhw && (h < 10 || h > 16 || out < 10 || (h + n) % 4 === 0);
    const demand = clamp((17 - out) * .38 + Math.sin(h * 1.6 + n * .7) * .5, 1.3, 6.6);
    const flowHeat = 31.6 + (10 - out) * .48 + 1.25 * Math.sin(h * .41 + n * .74);
    const flow = dhw ? 47 + 4 * (h === 12) + 1.5 * Math.sin(n * .8) : heat ? flowHeat : flowHeat - 2.1;
    const tank = dhw ? 46.8 + 1.6 * Math.sin(n * .77 + h) : 49.2 - ((h + 5) % 24) * .27 + Math.cos(n) * .6;
    const buffer = flowHeat - 1.8 + Math.sin(h * .31 + n) * .9;
    const heatCop = clamp(3.95 - .067 * (flow - out - 23) + .21 * Math.sin(h * 1.23 + n), 2.2, 5.2);
    const dhwCop = clamp(2.5 - .047 * (flow - out - 36) + .14 * Math.cos(n * .91 + h), 1.65, 3.3);
    const heatQ = heat ? demand : 0;
    const dhwQ = dhw ? 2.9 + .7 * Math.cos(n * .72 + h) : 0;
    const heatE = heatQ / heatCop;
    const dhwE = dhwQ / dhwCop;
    const cloud = clamp(.75 + .26 * Math.sin(n * 1.17) + .12 * Math.cos(n * .38), .22, 1);
    const pv = h >= 7 && h < 18 ? 3 * Math.sin((h - 7) * Math.PI / 11) ** 1.65 * cloud : 0;
    const load = .28 + .24 * (h > 17 || h < 8) + .1 * (1 + Math.sin(h * 1.2)) + heatE + dhwE;
    const excess = pv - load;
    const charge = clamp(excess, 0, (9.6 - battery) / .94);
    const discharge = clamp(-excess, 0, (battery - 1.92) * .94);
    battery += charge * .94 - discharge / .94;
    rows.push({
      time, minutes: 60, flow: round(flow), return: round(flow - (dhw ? 4.1 : heat ? 2.6 : .6)),
      out: round(out), buffer: round(buffer), tank: round(tank), heatQ: round(heatQ), heatE: round(heatE),
      dhwQ: round(dhwQ), dhwE: round(dhwE), pv: round(pv), gridIn: round(Math.max(0, -excess - discharge)),
      gridOut: round(Math.max(0, excess - charge)), soc: round(battery / 9.6 * 100),
    });
  }
  return rows;
}

function csvRecords(csv, separator) {
  const records = [];
  let row = [], cell = '', quoted = false, closed = false;
  function pushCell() {
    row.push(cell.trim());
    cell = '';
    closed = false;
  }
  for (let i = 0; i <= csv.length; i++) {
    const char = csv[i];
    if (quoted) {
      if (char === undefined) throw new Error('Una cella tra virgolette non è chiusa.');
      if (char === '"' && csv[i + 1] === '"') { cell += '"'; i++; }
      else if (char === '"') { quoted = false; closed = true; }
      else cell += char;
    } else if (char === separator) {
      pushCell();
    } else if (char === '\n' || char === '\r' || char === undefined) {
      if (char === '\r' && csv[i + 1] === '\n') i++;
      pushCell();
      if (row.some(Boolean)) records.push(row);
      row = [];
    } else if (closed && !/\s/.test(char)) {
      throw new Error('Carattere inatteso dopo una cella tra virgolette.');
    } else if (char === '"') {
      if (cell.trim()) throw new Error('Virgolette inattese in una cella.');
      quoted = true;
    } else {
      cell += char;
    }
  }
  return records;
}

function parseTime(value, index) {
  const match = /^(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d)(?::00(?:\.0+)?)?(?:Z|[+-]\d\d:\d\d)$/.exec(value);
  const time = Date.parse(value);
  if (!match || !Number.isFinite(time) || time % minute !== 0 || +match[4] > 23 || +match[5] > 59
    || new Date(Date.UTC(+match[1], +match[2] - 1, +match[3])).toISOString().slice(0, 10) !== match.slice(1, 4).join('-')) {
    throw new Error('Riga ' + index + '. Timestamp ISO 8601 con fuso e minuto intero richiesto.');
  }
  return time;
}

export function parseCSV(input) {
  const csv = input.trim();
  const separator = csv.split(/\r?\n/, 1)[0].includes(';') ? ';' : ',';
  const [headers = [], ...records] = csvRecords(csv, separator);
  const valueColumns = headers.map((header, position) => ({ key: fieldNames[header], header, position }))
    .filter(item => Object.hasOwn(fieldNames, item.header));
  const timeColumn = headers.indexOf('timestamp');
  const minutesColumn = headers.indexOf('minuti');
  if (timeColumn < 0 || minutesColumn < 0 || !valueColumns.length || new Set(headers).size !== headers.length) {
    throw new Error('Controlla le intestazioni. Servono timestamp, minuti e almeno una colonna di misura del modello, senza duplicati.');
  }
  if (!records.length) throw new Error('Il CSV contiene solo le intestazioni.');
  const rows = records.map((cells, offset) => {
    const index = offset + 2;
    if (cells.length !== headers.length) throw new Error('Riga ' + index + '. Il numero di colonne non coincide con le intestazioni.');
    const time = parseTime(cells[timeColumn], index);
    const minutes = Number(cells[minutesColumn]);
    if (!/^\d{1,2}$/.test(cells[minutesColumn]) || minutes < 1 || minutes > 60 || new Date(time).getUTCMinutes() + minutes > 60) {
      throw new Error('Riga ' + index + '. La durata deve essere da 1 a 60 minuti e restare nella stessa ora.');
    }
    const row = { time, minutes, index };
    for (const { key, header, position } of valueColumns) {
      if (!cells[position]) continue;
      const literal = separator === ';' ? cells[position].replace(',', '.') : cells[position];
      const value = Number(literal);
      const isTemp = temperatureKeys.includes(key);
      const permitsNegative = isTemp || key === 'heatQ' || key === 'dhwQ';
      if (!/^[+-]?(?:\d+(?:\.\d{0,9})?|\.\d{1,9})$/.test(literal) || !Number.isFinite(value)
        || Math.abs(value) > 1e9 || (isTemp && (value < -80 || value > 150)) || (!permitsNegative && value < 0)
        || (key === 'soc' && value > 100)) {
        throw new Error('Riga ' + index + '. Valore fuori formato o intervallo in ' + header + '.');
      }
      row[key] = value;
    }
    return row;
  }).sort((a, b) => a.time - b.time);
  for (let i = 1; i < rows.length; i++) {
    if (rows[i - 1].time + rows[i - 1].minutes * minute > rows[i].time) {
      throw new Error('Intervalli sovrapposti alle righe ' + rows[i - 1].index + ' e ' + rows[i].index + '.');
    }
  }
  if (!rows.some(row => valueColumns.some(({ key }) => Number.isFinite(row[key])))) {
    throw new Error('Il file non contiene valori di misura.');
  }
  return rows;
}

export function getPeriod(rows, days, lastDate = romeDay(rows.at(-1)?.time ?? Date.now())) {
  const firstDate = dateStep(lastDate, 1 - days);
  const start = localMidnight(firstDate);
  const end = localMidnight(dateStep(lastDate, 1));
  const available = rows.filter(row => row.time >= start && row.time < end);
  const hours = new Map();
  const dates = new Map();
  for (let time = start; time < end; time += hour) {
    hours.set(time, { time, rows: [] });
    const date = romeDay(time);
    if (!dates.has(date)) dates.set(date, { time, rows: [] });
  }
  for (const row of available) {
    const startHour = Math.floor(row.time / hour) * hour;
    hours.get(startHour).rows.push(row);
    dates.get(romeDay(row.time)).rows.push(row);
  }
  return { start, end, rows: available, hours: [...hours.values()], days: [...dates.values()] };
}

function sum(rows, read) {
  let total = null;
  for (const row of rows) {
    const value = read(row);
    if (Number.isFinite(value)) total = (total ?? 0) + value;
  }
  return total;
}

function mean(rows, read) {
  let total = 0, minutes = 0;
  for (const row of rows) {
    const value = read(row);
    if (Number.isFinite(value)) {
      total += value * row.minutes;
      minutes += row.minutes;
    }
  }
  return minutes ? total / minutes : null;
}

export function summarize(rows, mode) {
  if (!mode) return {
    ...Object.fromEntries(gridKeys.map(key => [key, sum(rows, row => row[key])])),
    pdc: sum(rows, row => Number.isFinite(row.heatE) && Number.isFinite(row.dhwE) ? row.heatE + row.dhwE : null),
    soc: mean(rows, row => row.soc), out: mean(rows, row => row.out),
  };
  const paired = rows.filter(row => Number.isFinite(row[mode.q]) && Number.isFinite(row[mode.e]));
  const active = paired.filter(row => row[mode.e] > 0);
  const thermal = sum(paired, row => row[mode.q]);
  const electric = sum(paired, row => row[mode.e]);
  const estimated = paired.some(row => row.copEligible === false);
  return {
    thermal, electric, cop: !estimated && electric > 0 ? thermal / electric : null, estimated,
    coverage: paired.filter(row => row.copEligible !== false).reduce((minutes, row) => minutes + row.minutes, 0),
    activity: active.reduce((minutes, row) => minutes + row.minutes, 0),
    flow: mean(active, row => row.flow), return: mean(active, row => row.return),
    delta: mean(active, row => Number.isFinite(row.flow) && Number.isFinite(row.out) ? row.flow - row.out : null),
    activeOut: mean(active, row => row.out), activeStorage: mean(active, row => row[mode.storage]),
    activeStorageCoverage: active.filter(row => Number.isFinite(row[mode.storage])).reduce((minutes, row) => minutes + row.minutes, 0),
    out: mean(rows, row => row.out), storage: mean(rows, row => row[mode.storage]),
  };
}

export function correlation(points) {
  if (points.length < 4) return null;
  const mx = points.reduce((value, point) => value + point.x, 0) / points.length;
  const my = points.reduce((value, point) => value + point.y, 0) / points.length;
  let covariance = 0, vx = 0, vy = 0;
  for (const { x, y } of points) {
    covariance += (x - mx) * (y - my);
    vx += (x - mx) ** 2;
    vy += (y - my) ** 2;
  }
  return vx && vy ? covariance / Math.sqrt(vx * vy) : null;
}
