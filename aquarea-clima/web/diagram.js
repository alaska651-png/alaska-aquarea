export function zoneStatus(d,n){
 const get=k=>d.metrics[k]?.quality==='fresh'?Number(d.metrics[k].value):null;const power=get('Heatpump_State'),mode=get('Operating_Mode_State'),zones=get('Zones_State'),season=d.zones.season_quality==='fresh'?d.zones.season:null;
 if(power===0)return {label:'Z'+n+' — OFF · nessuna richiesta ammessa',active:false,excluded:true};
 if(season==='winter'&&[0,4].includes(mode))return n===1?{label:zones===1?'Z1 — DISABILITATA / INVERNO':'Z1 — esclusione invernale non confermata',active:false,excluded:zones===1}:{label:zones===1?'Z2 — ABILITATA / INVERNO':'Z2 — consenso invernale da verificare',active:zones===1,excluded:false};
 if(season==='summer'&&[1,5].includes(mode))return n===2?{label:zones===0?'Z2 — DISABILITATA / ESTATE':'Z2 — esclusione estiva non confermata',active:false,excluded:zones===0}:{label:zones===0?'Z1 — ABILITATA / ESTATE':'Z1 — consenso estivo da verificare',active:zones===0,excluded:false};
 return {label:mode===3?'Z'+n+' — sola ACS · climatizzazione esclusa':'Z'+n+' — stagione / consensi da verificare',active:false,excluded:mode===3};
}
export function regulation(d,n){const r=d.metrics[n===1?'Cooling_Mode':'Heating_Mode'];return r?.quality==='fresh'&&Number(r.value)===0?'curve':r?.quality==='fresh'&&Number(r.value)===1?'fixed':'unknown';}
const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export function plantDiagram(d,read,badge,view='all',control=()=> ''){
 const m=k=>k==='Low_Pressure'&&d.metrics[k]?.value===0?'N/D':read(d.metrics[k]);
 const fresh=k=>d.metrics[k]&&d.metrics[k].quality==='fresh';
 const running=fresh('Compressor_Freq')&&d.metrics.Compressor_Freq.value>0;
 const pumpOn=n=>d.pumps['zone'+n]?.quality==='fresh'?d.pumps['zone'+n].value==='on':null;
 const mainFlow=fresh('Pump_Flow')&&d.metrics.Pump_Flow.value>1&&fresh('Pump_Speed')&&d.metrics.Pump_Speed.value>0;
 const dhwSelected=fresh('ThreeWay_Valve_State')&&Number(d.metrics.ThreeWay_Valve_State.value)===1;
 const txt=(x,y,text,cls='label',extra='')=>`<text x="${x}" y="${y}" class="${cls}" ${extra}>${esc(text)}</text>`;
 const frame=(x,y,w,h,active=false)=>`<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="15" class="frame ${active?'active-frame':''}"/>`;
 const pump=(x,y,on)=>`<g class="${on===null?'pump-unknown':on?'pump-active':'pump-inactive'}"><circle cx="${x}" cy="${y}" r="18" fill="#00120b" stroke-width="3"/><path d="M${x-7} ${y-11}l19 11-19 11Z" fill="${on===null?'#e9ad40':on?'#59fa0a':'#78939b'}" stroke="none"/></g>`;
 const labelbox=(x,y,w,label,key)=>`<g data-metric="${key}" role="button" tabindex="0">${frame(x,y,w,48)}${txt(x+10,y+17,label,'tiny')}${txt(x+w-10,y+37,m(key),'value','text-anchor="end"')}</g>`;
 const fanSvg=(cx,cy,n)=>`<g><circle cx="${cx}" cy="${cy}" r="47" fill="#101c20" stroke="#6e7c80"/><g class="${fresh('Fan'+n+'_Motor_Speed')&&d.metrics['Fan'+n+'_Motor_Speed'].value>0?'rotating':''}" fill="#68767a" stroke="#b4c3c8" stroke-width=".6">${[0,60,120,180,240,300].map(a=>`<path transform="rotate(${a} ${cx} ${cy})" d="M${cx} ${cy}c-24-33 22-47 31-21S${cx+7} ${cy+4} ${cx} ${cy}"/>`).join('')}</g><circle cx="${cx}" cy="${cy}" r="9" fill="#1c3036"/>${[36,42,48].map(r=>`<circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="#7e9196" stroke-width=".5"/>`).join('')}</g>`;

 const ctl=(x,y,key)=>'<foreignObject x="'+x+'" y="'+y+'" width="66" height="37"><div xmlns="http://www.w3.org/1999/xhtml" class="svg-control">'+control(key)+'</div></foreignObject>';
 const box=(x,y,w,label,key)=>frame(x,y,w,49)+txt(x+w/2,y+17,label,'tiny','text-anchor="middle"')+txt(x+w/2,y+39,m(key),'label','text-anchor="middle"');
 return `<svg class="plant" viewBox="0 0 1320 440" role="img" aria-label="Sinottico Aquarea con ACS 180 litri integrato, buffer esterno e due zone"><defs><linearGradient id="metal" x2="1" y2="0"><stop stop-color="#809398"/><stop offset=".3" stop-color="#eff1ed"/><stop offset=".6" stop-color="#a3b5b8"/><stop offset=".85" stop-color="#e9edeb"/><stop offset="1" stop-color="#73888d"/></linearGradient></defs>
 <path d="M130 194H225V145H405V216H454" class="tube gas"/><path d="M130 260H161V327H398V260H454" class="tube cold"/>
 <path d="M601 181H625V195H646 M708 195H718V104H760" class="tube hot"/><path d="M646 305H632V211H601 M708 305H737V174H760" class="tube cold"/>
 <path d="M718 195V257H760" class="tube hot"/><path d="M760 305H737" class="tube cold"/>
 ${txt(78,23,'UNITÀ ESTERNA','caption','text-anchor="middle"')}${txt(78,43,'WH-UX12HE8 · 12 kW','tiny','text-anchor="middle"')}
 <rect x="18" y="75" width="120" height="260" rx="8" class="device"/>${fanSvg(77,147,1)}${fanSvg(77,263,2)}${txt(77,91,'Panasonic','tiny','text-anchor="middle" style="fill:#14252b"')}
 ${txt(298,23,'CIRCUITO FRIGORIFERO','caption','text-anchor="middle"')}${txt(298,43,'R410A · inversione caldo / freddo','tiny','text-anchor="middle"')}
 ${box(205,60,105,'Scarico','Discharge_Temp')}${box(330,72,110,'P. alta','High_Pressure')}${box(148,121,88,'Sbrin.','Defrost_Temp')}
 <path d="M245 107V145M386 121V145M190 170L170 194M191 327V354M363 327V305" stroke="#c7dde2" fill="none"/>
 <rect x="264" y="157" width="45" height="83" rx="12" class="device"/>${box(246,227,92,'Comp.','Compressor_Freq')}
 ${box(155,351,103,'Evaporatore','Eva_Outlet_Temp')}${box(338,280,103,'P. bassa','Low_Pressure')}
 <circle cx="286" cy="327" r="14" fill="#f9d500"/><path d="M275 317l22 20m-22 0 22-20" stroke="#273a3e" stroke-width="2"/>${txt(286,369,'EEV · N/D','tiny','text-anchor="middle"')}
 ${txt(526,23,'UNITÀ INTERNA · ALL-IN-ONE','caption','text-anchor="middle"')}${txt(526,43,'WH-ADC0916H9E8','tiny','text-anchor="middle"')}
 <rect x="453" y="65" width="150" height="348" rx="20" class="device"/>
 <path d="M482 103h87q12 7 0 14h-80q-12 7 0 14h80q12 7 0 14h-80" fill="none" stroke="#ff3048" stroke-width="4"/>
 ${box(528,68,84,'Tubo int.','Inside_Pipe_Temp')}
 <rect x="461" y="155" width="134" height="65" rx="8" fill="#00171d" stroke="#00b8cb"/>${txt(468,177,'Mandata','tiny')}${txt(586,177,m('Main_Outlet_Temp'),'label','text-anchor="end"')}${txt(468,205,'Ritorno','tiny')}${txt(586,205,m('Main_Inlet_Temp'),'label','text-anchor="end"')}
 <rect x="466" y="231" width="72" height="165" rx="16" fill="#e8ad61" stroke="#c18332"/><ellipse cx="502" cy="239" rx="34" ry="10" fill="#ffe2a4"/>${txt(502,257,'ACS integrato','tiny','text-anchor="middle" style="fill:#10222a;font-weight:700"')}${txt(502,275,'180 L','label','text-anchor="middle" style="fill:#10222a"')}
 <rect x="470" y="286" width="64" height="37" rx="6" fill="#270e19"/>${txt(502,312,m('DHW_Temp'),'value','text-anchor="middle"')}
 <path d="M476 343h48q7 5 0 10h-43q-7 5 0 10h43" fill="none" stroke="#ff174e" stroke-width="4"/><path d="M481 375h43" stroke="#00aaff" stroke-width="4"/>
 <rect x="540" y="232" width="58" height="167" rx="8" fill="#001820" stroke="#4d8491"/>${txt(568,248,'Set ACS','tiny','text-anchor="middle"')}${txt(568,269,m('DHW_Target_Temp'),'label','text-anchor="middle"')}${ctl(535,277,'DHW_Target_Temp')}
 ${txt(569,327,'Δ ACS','tiny','text-anchor="middle"')}${txt(569,348,m('DHW_Heat_Delta'),'label','text-anchor="middle"')}${txt(568,373,dhwSelected&&running?'Produzione ON':fresh('ThreeWay_Valve_State')&&fresh('Compressor_Freq')?'Produzione OFF':'Produzione N/D','tiny','text-anchor="middle"')}
 ${pump(465,393,fresh('Pump_Speed')?d.metrics.Pump_Speed.value>0:null)}
 ${txt(676,100,'BUFFER','caption','text-anchor="middle"')}${txt(676,120,'100 L','tiny','text-anchor="middle"')}
 <rect x="646" y="147" width="62" height="188" rx="18" class="device"/><ellipse cx="677" cy="153" rx="31" ry="12" fill="#d8e1df"/>
 <rect x="650" y="199" width="54" height="49" rx="6" fill="#00141b"/>${txt(677,230,m('Buffer_Temp'),'label','text-anchor="middle"')}<rect x="650" y="259" width="54" height="59" rx="6" fill="#00141b"/>${txt(677,277,'Δ buffer','tiny','text-anchor="middle"')}${txt(677,304,m('Buffer_Tank_Delta'),'label','text-anchor="middle"')}
 ${frame(760,55,543,132)}<path d="M760 104H940V127H942M978 127H990V158H929V135" class="tube hot"/><path d="M777 135H770V174H760" class="tube cold"/>${txt(773,80,'ZONA 1 — FAN-COIL · circuito diretto · Estate','caption')}${txt(773,95,zoneStatus(d,1).label,'tiny')}
 <rect x="777" y="100" width="152" height="53" rx="8" class="device"/><path d="M788 144h128m-128 4h128" stroke="#677b80"/>${pump(960,127,pumpOn(1))}
 ${txt(1000,112,'Mandata Z1','tiny')}${txt(1155,112,m('Z1_Water_Temp'),'label','text-anchor="end"')}${txt(1000,140,'Ritorno Z1','tiny')}${txt(1155,140,'N/D','label','text-anchor="end"')}
 ${txt(1240,108,zoneStatus(d,1).active?'Target Z1':'Target letto · inattivo','tiny','text-anchor="middle"')}${txt(1240,134,m('Z1_Water_Target_Temp'),'value','text-anchor="middle"')}${txt(1240,158,regulation(d,1)==='fixed'?'Estate: temp. fissa':regulation(d,1)==='curve'?'Estate: curva':'Regolazione N/D','tiny','text-anchor="middle"')}
 ${txt(1000,170,'Richiesta: N/D · Pompa: '+(d.pumps.zone1?.quality==='fresh'?d.pumps.zone1.value==='on'?'ON':'OFF':'N/D'),'tiny')}
 ${frame(760,203,543,133)}<path d="M760 257H791V273H831M867 273H1001" class="tube hot"/><path d="M1001 296V305H760M791 305V288" class="tube cold"/>${txt(773,228,'ZONA 2 — PAVIMENTO · circuito miscelato · Inverno','caption')}${txt(1010,243,zoneStatus(d,2).label,'tiny')}
 <g fill="#ffe000"><path d="M791 252l17 18-17 18-17-18Z"/><path d="M777 242h28l-14 17Z"/></g>${pump(849,273,pumpOn(2))}<path d="M883 267h118m-118 11h118" stroke="#ff3045" stroke-width="4"/>${Array.from({length:12},(_,i)=>'<path d="M'+(889+i*10)+' 249v18m0 11v18" stroke="'+(i%2?'#00aaff':'#ff3045')+'" stroke-width="3"/>').join('')}
 ${txt(1010,260,'Collettore Z2','tiny')}${txt(1155,260,m('Z2_Water_Temp'),'label','text-anchor="end"')}${txt(1010,288,'Ritorno Z2','tiny')}${txt(1155,288,'N/D','label','text-anchor="end"')}
 ${txt(1240,254,regulation(d,2)==='curve'?'TARGET CALCOLATO':'Target Z2','tiny','text-anchor="middle"')}${txt(1240,279,m('Z2_Water_Target_Temp'),'value','text-anchor="middle"')}${txt(1240,306,regulation(d,2)==='curve'?'CURVA CLIMATICA':regulation(d,2)==='fixed'?'TARGET FISSO LETTO':'Regolazione N/D','tiny','text-anchor="middle"')}
 ${txt(775,321,'Miscelatrice: N/D · Richiesta: N/D · Pompa: '+(d.pumps.zone2?.quality==='fresh'?d.pumps.zone2.value==='on'?'ON':'OFF':'N/D'),'tiny')}
 ${box(765,355,185,'Target primario · calcolato','Main_Target_Temp')}
 ${txt(970,370,'Correzione curva Z2','tiny')}${txt(1134,393,m('Z2_Heat_Request_Temp'),'value','text-anchor="end"')}${ctl(1145,365,'Z2_Heat_Request_Temp')}
 ${txt(626,185,'M','tiny')}${txt(626,226,'R','tiny')}<foreignObject x="1000" y="402" width="270" height="29"><div xmlns="http://www.w3.org/1999/xhtml"><button class="curve-link" data-open-curve="2">Apri parametri curva Z2 ↗</button></div></foreignObject>${txt(20,433,'Schema funzionale non esecutivo · topologia buffer da confermare sul disegno impianto','tiny')}
 ${mainFlow&&fresh('ThreeWay_Valve_State')&&!dhwSelected?'<path d="M601 181H625V195H646 M646 305H632V211H601" class="flowing"/>':''}
 ${pumpOn(1)===true?'<path d="M708 195H718V104H940V127H942 M978 127H990V158H929V135 M777 135H770V174H737V305H708" class="flowing"/>':''}
 ${pumpOn(2)===true?'<path d="M708 195H718V257H791V273H831 M867 273H1001 M1001 296V305H708" class="flowing"/>':''}
 </svg>`;
}

export function sanitaryTank(d,read){
 const value=k=>read(d.metrics[k]);
 return `<svg class="sanitary-tank" viewBox="0 0 520 220" role="img" aria-label="Accumulo sanitario integrato da 180 litri con temperatura, setpoint e differenziale reali"><defs><linearGradient id="acs-metal"><stop stop-color="#7c929b"/><stop offset=".4" stop-color="#ecf0ed"/><stop offset=".75" stop-color="#b6c6c9"/><stop offset="1" stop-color="#708b94"/></linearGradient></defs><rect x="32" y="22" width="120" height="170" rx="22" fill="url(#acs-metal)" stroke="#b8d3da"/><ellipse cx="92" cy="28" rx="60" ry="17" fill="#cfdddf" stroke="#adc9d1"/><ellipse cx="92" cy="181" rx="59" ry="16" fill="#809aa4"/><rect x="40" y="63" width="104" height="48" rx="8" fill="#00191f" stroke="#00e9fb"/><text x="92" y="94" text-anchor="middle" fill="#fff" font-size="23">${esc(value('DHW_Temp'))}</text><path d="M46 129h88q10 6 0 12H53q-10 6 0 12h81q10 6 0 12H53" fill="none" stroke="#ff304b" stroke-width="4"/><text x="185" y="42" fill="#fff" font-size="21" font-weight="600">ACCUMULO SANITARIO</text><text x="185" y="68" fill="#a7cbd3" font-size="15">180 L · integrato nell’unità interna</text><text x="185" y="109" fill="#b5d1d8" font-size="16">Setpoint ACS</text><text x="490" y="109" text-anchor="end" fill="#fff" font-size="23">${esc(value('DHW_Target_Temp'))}</text><text x="185" y="149" fill="#b5d1d8" font-size="16">Differenziale riaccensione</text><text x="490" y="149" text-anchor="end" fill="#fff" font-size="23">${esc(value('DHW_Heat_Delta'))}</text><text x="185" y="189" fill="#8bb0ba" font-size="13">Distinto dal buffer tecnico · ricircolo P3 N/D</text></svg>`;
}
