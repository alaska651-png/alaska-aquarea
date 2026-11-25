// All durations use UTC epoch milliseconds. Local calendar days live in data.js.
export const HOUR = 3600000;
export const bindings = {
  flow: ['mandata', 'temperature'], return: ['ritorno', 'temperature'], out: ['esterna', 'temperature'],
  buffer: ['accumulo_tecnico', 'temperature'], tank: ['serbatoio_acs', 'temperature'],
  heatQ: ['termica_riscaldamento', 'energy'], heatE: ['elettrica_riscaldamento', 'energy'],
  dhwQ: ['termica_acs', 'energy'], dhwE: ['elettrica_acs', 'energy'],
  pv: ['potenza_fv', 'energy'], gridIn: ['prelievo', 'energy'], gridOut: ['immissione', 'energy'], soc: ['soc', 'percent'],
};
const finite = x => x !== null && x !== undefined && x !== '' && Number.isFinite(Number(x));
export function normalize(value, unit, kind) {
  if (!finite(value)) return null;
  const n = Number(value);
  if (kind === 'temperature') {
    const v = unit === '°F' ? (n - 32) * 5 / 9 : unit === '°C' ? n : null;
    return v !== null && v >= -80 && v <= 150 ? v : null;
  }
  if (kind === 'percent') return unit === '%' && n >= 0 && n <= 100 ? n : null;
  if (kind === 'signedPower') return unit === 'W' ? n / 1000 : unit === 'kW' ? n : null;
  if (n < 0) return null;
  return ({ W: n / 1000, kW: n, Wh: n / 1000, kWh: n })[unit] ?? null;
}

function mergeCoverage(segments) {
  const result = [];
  for (const s of segments) {
    const last = result.at(-1);
    if (last && s.start <= last.end) last.end = Math.max(last.end, s.end);
    else result.push({ ...s });
  }
  return result;
}

function eventsFor(history, id) {
  return (history[id] ?? []).map(s => ({ ...s, time: Date.parse(s.last_updated ?? s.last_changed) }))
    .filter(s => Number.isFinite(s.time)).sort((a,b) => a.time - b.time);
}

function heartbeat(history, id, start, end, maxGap) {
  if (!id) return []; // Missing liveness evidence is not continuous acquisition.
  const events = eventsFor(history,id);
  return mergeCoverage(events.flatMap((s,i) => {
    if (!finite(s.state)) return [];
    const a = Math.max(start,s.time), b = Math.min(end,events[i+1]?.time ?? end,s.time + maxGap);
    return b > a ? [{start:a,end:b,value:1}] : [];
  }));
}

function segmentsFor(events, state, kind, start, end, diagnostics) {
  const result = [];
  for (let i=0;i<events.length;i++) {
    const s = events[i], next = events[i+1];
    const unit = s.attributes?.unit_of_measurement ?? state?.attributes?.unit_of_measurement;
    const a = Math.max(start,s.time), b = Math.min(end,next?.time ?? end);
    if (b <= a) continue;
    let value = normalize(s.state,unit,kind);
    if (kind === 'energy' && ['Wh','kWh'].includes(unit)) {
      const cls = s.attributes?.state_class ?? state?.attributes?.state_class;
      const nextUnit = next?.attributes?.unit_of_measurement ?? unit;
      const after = normalize(next?.state,nextUnit,kind);
      if (!['total','total_increasing'].includes(cls) || !next || value === null || after === null || next.time <= s.time) continue;
      // A reset has unknown timing. Drop that interval instead of inventing energy.
      if (after < value || (next.attributes?.last_reset && next.attributes.last_reset !== s.attributes?.last_reset)) {
        diagnostics.resets++;
        continue;
      }
      value = (after - value) / ((next.time - s.time) / HOUR);
    }
    if (value !== null) result.push({start:a,end:b,value});
  }
  return result;
}

export function historyRows(history, entities, states, start, end, options = {}) {
  const diagnostics = {resets:0};
  const segments = {};
  for (const [key,[configKey,kind]] of Object.entries(bindings)) {
    const id = entities[configKey];
    segments[key] = segmentsFor(eventsFor(history,id),states[id],kind,start,end,diagnostics);
  }
  const maxGap = (options.max_gap_seconds ?? 120) * 1000;
  segments.pdcAlive = heartbeat(history,entities.pdc_heartbeat,start,end,maxGap);
  segments.solarAlive = heartbeat(history,entities.solar_heartbeat,start,end,maxGap);
  const boundaries = new Set([start,end]);
  for (let t = Math.ceil(start/HOUR)*HOUR;t<end;t+=HOUR) boundaries.add(t);
  for (const list of Object.values(segments)) for (const s of list) { boundaries.add(s.start); boundaries.add(s.end); }
  const points = [...boundaries].sort((a,b)=>a-b), cursors = {}, rows = [];
  for (let i=0;i<points.length-1;i++) {
    const a=points[i],b=points[i+1];
    if (b<=a) continue;
    const current = {};
    for (const [key,list] of Object.entries(segments)) {
      let j=cursors[key]??0;
      while (j<list.length && list[j].end<=a) j++;
      cursors[key]=j;
      if (list[j]?.start<=a && list[j].end>=b) current[key]=list[j].value;
    }
    const row={time:a,minutes:(b-a)/60000,source:'history'};
    for (const [key,[,kind]] of Object.entries(bindings)) {
      const alive = ['pv','gridIn','gridOut','soc'].includes(key) ? current.solarAlive : current.pdcAlive;
      if (alive && Number.isFinite(current[key])) row[key] = kind==='energy' ? current[key]*(b-a)/HOUR : current[key];
    }
    if (Object.keys(row).length>3) rows.push(row);
  }
  return {rows,diagnostics};
}

// Only old, complete statistic buckets; never overlay raw history or extrapolate the current hour.
// Mean power does not expose its exact valid sample support. Energy is an estimate; COP is excluded.
export function statisticsRows(statistics, entities, states, metadata, start, end) {
  const byTime=new Map();
  for (const [key,[configKey,kind]] of Object.entries(bindings)) {
    const id=entities[configKey], meta=metadata[id]??{};
    const unit=meta.display_unit_of_measurement??meta.unit_of_measurement??states[id]?.attributes?.unit_of_measurement??meta.statistics_unit_of_measurement;
    const entries=statistics[id]??[];
    for (let i=0;i<entries.length;i++) {
      const s=entries[i], a=Number(s.start),b=Number(s.end);
      if (a<start || b>end || b<=a) continue;
      let value;
      if (kind==='energy' && ['Wh','kWh'].includes(unit)) {
        const previous=entries[i-1];
        // HA sum already compensates counter resets. Require consecutive boundaries.
        if (!previous || Number(previous.end)!==a || !finite(previous.sum) || !finite(s.sum) || s.sum<previous.sum) continue;
        value=normalize(s.sum-previous.sum,unit,kind);
      } else {
        value=normalize(s.mean,unit,kind);
        if (kind==='energy' && value!==null) value*=(b-a)/HOUR;
      }
      if (value===null || value===undefined) continue;
      if (!byTime.has(a)) byTime.set(a,{time:a,minutes:(b-a)/60000,source:'statistics',copEligible:false});
      byTime.get(a)[key]=value;
    }
  }
  return [...byTime.values()].sort((a,b)=>a.time-b.time);
}

export function coverage(rows, key) {
  const valid=rows.filter(r=>Number.isFinite(r[key]));
  return {minutes:valid.reduce((n,r)=>n+r.minutes,0),first:valid[0]?.time??null,last:valid.length?valid.at(-1).time+valid.at(-1).minutes*60000:null};
}

export async function loadHomeAssistant(hass, config, start, end, cache=new Map()) {
  const entities=config.entities??{}, ids=[...new Set(Object.values(entities).filter(id=>typeof id==='string'&&id.startsWith('sensor.')))];
  if (!ids.length) throw new Error('Nessuna entità configurata.');
  const now=Date.now();
  end=Math.min(end,now);
  if (end<=start) return {rows:[],loadedAt:now,errors:[],sources:[],coverage:{}};
  const cutoff=Math.max(start,Math.ceil((now-(config.detailed_history_days??10)*24*HOUR)/HOUR)*HOUR);
  let metadata={}, stats={};
  const errors=[], sources=[];
  try {
    const list=await hass.callWS({type:'recorder/get_statistics_metadata',statistic_ids:ids});
    metadata=Object.fromEntries(list.map(m=>[m.statistic_id,m]));
    const available=ids.filter(id=>metadata[id]);
    if (available.length && cutoff>start) {
      stats=await hass.callWS({type:'recorder/statistics_during_period',start_time:new Date(start-HOUR).toISOString(),end_time:new Date(Math.min(end,cutoff)).toISOString(),statistic_ids:available,period:'hour',types:['mean','sum','state']});
      sources.push('Statistiche orarie HA (stime; escluse dal COP)');
    }
  } catch { errors.push('Statistiche HA non disponibili.'); }
  const history={};
  if (end>cutoff) for (let a=cutoff;a<end;a+=24*HOUR) {
    const b=Math.min(end,a+24*HOUR);
    // Stable UTC chunks, memory-only cache; live chunk expires after 60 seconds.
    const key=ids.join(',')+'|'+a+'|'+Math.floor(b/(24*HOUR));
    let cached=cache.get(key);
    const ttl=b<now-60000?600000:55000;
    try {
      if (!cached || now-cached.at>ttl || cached.end<b-60000) {
        const path='history/period/'+encodeURIComponent(new Date(a).toISOString())+'?filter_entity_id='+encodeURIComponent(ids.join(','))+'&end_time='+encodeURIComponent(new Date(b).toISOString())+'&significant_changes_only=0';
        cached={at:now,end:b,data:await hass.callApi('GET',path)};
        cache.set(key,cached);
      }
      for (const list of cached.data) if (list.length) (history[list[0].entity_id]??=[]).push(...list);
    } catch { errors.push('Cronologia non disponibile: '+new Date(a).toISOString().slice(0,10)); }
  }
  if (cache.size>45) for (const key of [...cache.keys()].slice(0,cache.size-45)) cache.delete(key);
  const raw=historyRows(history,entities,hass.states,cutoff,end,config);
  if (raw.rows.length) sources.unshift('Cronologia HA: integrazione a gradini su intervalli comuni');
  const older=statisticsRows(stats,entities,hass.states,metadata,start,Math.min(end,cutoff));
  const rows=[...older,...raw.rows];
  return {rows,loadedAt:Date.now(),start,end,sources,errors,diagnostics:raw.diagnostics,coverage:Object.fromEntries(Object.keys(bindings).map(k=>[k,coverage(rows,k)]))};
}
