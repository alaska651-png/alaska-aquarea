export const ZONE='Europe/Rome';
export const fmt=(n,d=1)=>Number.isFinite(n)?n.toLocaleString('it-IT',{minimumFractionDigits:d,maximumFractionDigits:d}):'N/D';
export const clock=t=>new Intl.DateTimeFormat('it-IT',{timeZone:ZONE,hour:'2-digit',minute:'2-digit'}).format(new Date(t));
export const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export function numeric(state,units){
  if(!state||!units.includes(state.attributes?.unit_of_measurement)||['unknown','unavailable',''].includes(state.state))return null;
  const n=Number(state.state);return Number.isFinite(n)?n:null;
}
export function kw(state){const n=numeric(state,['W','kW']);return n===null?null:n/(state.attributes.unit_of_measurement==='W'?1000:1);}
export function telemetryFresh(heartbeat,limit,now=Date.now()){
  // HA and the browser can differ slightly: a just-arrived report is not stale.
  // Bound the tolerance; never retain data indefinitely or treat missing data as OFF.
  const age=now-Date.parse(heartbeat?.last_changed);
  return Number.isFinite(age)&&age>=-5000&&age<limit;
}
export function pumpStatus(state,heartbeat,online,connected=true,now=Date.now()){
  if(!connected||online?.state!=='on'||!telemetryFresh(heartbeat,40000,now)||!['on','off'].includes(state?.state))return 'N/D';
  return state.state.toUpperCase();
}
export function temperatureReading(state,heartbeat,online,connected,min,max,now=Date.now()){
  const value=numeric(state,['°C']);
  return connected&&online?.state==='on'&&telemetryFresh(heartbeat,120000,now)&&value!==null&&value>=min&&value<=max?value:null;
}
export function weatherHumidity(state,connected=true,now=Date.now()){
  const value=state?.attributes?.humidity,age=now-Date.parse(state?.last_updated);
  return connected&&!['unknown','unavailable'].includes(state?.state)&&Number.isFinite(value)&&value>=0&&value<=100&&Number.isFinite(age)&&age>=0&&age<10800000?value:null;
}
export function ratio(thermal,power){return Number.isFinite(thermal)&&Number.isFinite(power)&&thermal>=0&&power>0?thermal/power:null;}
export function midnight(now=Date.now(),offsetDays=0){
  const parts=Object.fromEntries(new Intl.DateTimeFormat('en-GB',{timeZone:ZONE,year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(now).map(p=>[p.type,p.value]));
  const day=new Date(Date.UTC(+parts.year,+parts.month-1,+parts.day+offsetDays));
  const target=day.getTime();let guess=target;
  for(let i=0;i<3;i++){
    const p=Object.fromEntries(new Intl.DateTimeFormat('en-GB',{timeZone:ZONE,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}).formatToParts(guess).map(x=>[x.type,x.value]));
    const wall=Date.UTC(+p.year,+p.month-1,+p.day,+p.hour,+p.minute,+p.second);guess+=target-wall;
  }
  return guess;
}
export function statisticRows(stats,id,unit){
  if(!id||!['W','kW'].includes(unit))return [];
  return (stats[id]||[]).map(r=>({t:r.start,end:r.end,value:Number.isFinite(r.mean)?r.mean/(unit==='W'?1000:1):null}));
}
export function alignedRatio(a,b){const map=new Map(b.map(r=>[r.t,r]));return a.map(r=>({t:r.t,end:r.end,value:map.get(r.t)?.end===r.end?ratio(r.value,map.get(r.t)?.value):null}));}
export function energyDelta(rows,start,end,unit){
  if(!['kWh','Wh'].includes(unit)||!rows?.length)return {value:null,until:null};
  const sorted=rows.filter(r=>Number.isFinite(r.sum)).sort((a,b)=>a.start-b.start);
  const base=sorted.find(r=>Math.abs(r.end-start)<1000);
  const range=sorted.filter(r=>r.start>=start&&r.end<=end);
  if(!base||!range.length)return {value:null,until:null};
  let previous=base;
  for(const r of range){if(r.start-previous.end>1000||r.sum<previous.sum-0.001)return {value:null,until:null};previous=r;}
  return {value:(previous.sum-base.sum)/(unit==='Wh'?1000:1),until:previous.end};
}
export function svgPath(rows,start,end,max,width=600,height=170){
  let d='',prev=null;
  for(const r of rows){
    if(!Number.isFinite(r.value)||r.t<start||r.t>end){prev=null;continue;}
    const x=(r.t-start)/(end-start)*width,y=height-Math.max(0,Math.min(max,r.value))/max*height;
    d+=`${prev!==null&&r.t-prev<=600000?'L':'M'}${x.toFixed(2)},${y.toFixed(2)} `;prev=r.t;
  }
  return d;
}
