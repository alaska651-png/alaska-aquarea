import {fmt,clock,esc,numeric,kw,pumpStatus,QUIET_ENTITY,QUIET_OPTIONS,quietReading,requestQuiet,temperatureReading,weatherHumidity,ratio,midnight,statisticRows,alignedRatio,energyDelta,svgPath} from './climate-data.mjs?v=20261125-2';
const PATH={home:'M3 10 12 2l9 8M5 9v12h5v-7h4v7h5V9',calendar:'M4 5h16v16H4zM4 10h16M8 2v6m8-6v6M8 13h1m3 0h1m3 0h1m-9 4h1m3 0h1m3 0h1',sun:'M12 3V1m0 22v-2M3 12H1m22 0h-2M5.6 5.6 4.2 4.2m15.6 15.6-1.4-1.4m0-12.8 1.4-1.4M4.2 19.8l1.4-1.4M17 12a5 5 0 1 1-10 0 5 5 0 0 1 10 0',moon:'M21 13A9 9 0 0 1 11 3a9 9 0 1 0 10 10',snow:'M12 1v22M2.5 6.5l19 11m-19 0 19-11M8 3l4 4 4-4M8 21l4-4 4 4M2 11l5-1-1-5m16 8-5 1 1 5M2 13l5 1-1 5m16-8-5-1 1-5',power:'M12 2v10M6 5a9 9 0 1 0 12 0',thermo:'M9 5a3 3 0 0 1 6 0v10a5 5 0 1 1-6 0V5m3 3v10',drop:'M12 2C9 7 4 11 4 16a8 8 0 0 0 16 0c0-5-5-9-8-14',heat:'M5 2C-2 10 12 13 5 22M12 2c-7 8 7 11 0 20M19 2c-7 8 7 11 0 20',leaf:'M21 3c-14-2-20 5-15 13 8 7 17-1 15-13ZM4 21 16 9',gear:'M9 3h6l1 3 3 1 2 5-2 4-3 1-1 4H9l-1-4-3-1-2-4 2-5 3-1 1-3m7 9a4 4 0 1 1-8 0 4 4 0 0 1 8 0',check:'M5 12l4 4L19 6',link:'m10 14 4-4m-6 7-2 2a4 4 0 0 1-5-5l5-5a4 4 0 0 1 5 0m2-2 2-2a4 4 0 0 1 5 5l-5 5a4 4 0 0 1-5 0',clock:'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0m-9-6v7l4 2',zones:'M9 2h6v5H9zM2 17h5v5H2zm8 0h5v5h-5zm9 0h4v5h-4zM12 7v6M4 17v-4h17v4m-9-4v4',hand:'M8 12V5a2 2 0 0 1 4 0v7-9a2 2 0 0 1 4 0v9-7a2 2 0 0 1 4 0v10c0 5-3 7-7 7-4 0-7-4-10-7-2-3 1-5 3-3l2 2',copy:'M8 3h12v15H8zM4 7v15h12',expand:'M8 2H2v6m14-6h6v6M2 16v6h6m14-6v6h-6',warning:'M12 2 1 22h22L12 2m0 6v6m0 3v1',bars:'M3 14h4v8H3zm7-6h4v14h-4zm7-6h4v20h-4z'};
PATH.quiet='M11 5 6 9H3v6h3l5 4V5m4 4a5 5 0 0 1 0 6m3-9a9 9 0 0 1 0 12';
PATH.pump='M3 10H1v8h7m8-12h7v8h-2M19 12a7 7 0 1 1-14 0 7 7 0 0 1 14 0ZM10 8l6 4-6 4Z';
const icon=(name,cls='')=>`<svg class="icon ${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${PATH[name]||PATH.thermo}"/></svg>`;
const days=['Lun','Mar','Mer','Gio','Ven','Sab','Dom'];
const floorNames={night:['Piano +1 · Notte','Camera principale · Camera 2 · Camera 3 (bagno associato)','moon'],day:['Piano 0 · Giorno','Sala · Cucina (bagno associato)','sun'],basement:['Piano −1 · Taverna','Termostato indipendente','home']};
const seriesNames=['COP Ris.','Potenza Risc. (kW)','COP ACS','Potenza ACS (kW)'];

class CoordinatoreClimaticoCard extends HTMLElement{
  constructor(){super();this.attachShadow({mode:'open'});this.day=(new Date().getDay()+6)%7;this.range=24;this.hiddenSeries=new Set();this.chart={};this.energy={};this.seq=0;this._pending=new Map();this._notice='';this._closed=false;}
  setConfig(c){this.config=c;this.render();}
  getCardSize(){return 16;}
  set hass(h){
    this._hass=h;if(!h?.states)return;const st=h.states['sensor.coordinatore_climatico'];
    if(st&&(!this.snapshot||Date.parse(st.attributes.updated_at)>=Date.parse(this.snapshot.updated_at))){this.snapshot=st.attributes;this.status=st.state;}
    const ack=this.snapshot?.ack;
    if(ack&&this._pending.has(ack.request_id)){const {resolve,reject,timer}=this._pending.get(ack.request_id);clearTimeout(timer);this._pending.delete(ack.request_id);ack.ok?resolve():reject(new Error(ack.error));}
    if(this.isConnected&&!this.started){this.started=true;this.subscribe();this.loadHistory();}
    if(!this.shadowRoot.activeElement?.matches('input,select')&&!this.shadowRoot.querySelector('dialog')&&!this.painting){this.painting=true;requestAnimationFrame(()=>{this.painting=false;this.render();});}
  }
  connectedCallback(){
    this._escape=e=>{if(e.key==='Escape'&&!this.shadowRoot.querySelector('dialog')){e.preventDefault();this.maintenance();}};window.addEventListener('keydown',this._escape);
    this.timer=setInterval(()=>{if(!document.hidden){if(!this.shadowRoot.querySelector('dialog')&&!this.shadowRoot.activeElement?.matches('input,select'))this.render();if(Date.now()-(this.loaded||0)>60000)this.loadHistory();}},10000);
    if(this._hass&&!this.started){this.started=true;this.subscribe();this.loadHistory();}this.render();
  }
  disconnectedCallback(){clearInterval(this.timer);window.removeEventListener('keydown',this._escape);this.started=false;this._unsubscribe?.();this._unsubscribe=null;this._loadSeq=(this._loadSeq||0)+1;for(const p of this._pending.values()){clearTimeout(p.timer);p.reject(new Error('Dashboard chiusa'));}this._pending.clear();}
  async subscribe(){
    if(this._unsubscribe||!this._hass)return;
    const stop=await this._hass.connection.subscribeEvents(event=>{
      this.snapshot=event.data.snapshot;this.status=event.data.status;
      const ack=this.snapshot?.ack,p=ack&&this._pending.get(ack.request_id);
      if(p){clearTimeout(p.timer);this._pending.delete(ack.request_id);ack.ok?p.resolve():p.reject(new Error(ack.error));}
      if(!this.shadowRoot.activeElement?.matches('input,select')&&!this.shadowRoot.querySelector('dialog'))this.render();
    },'coordinatore_climatico_status');
    if(!this.isConnected)stop();else this._unsubscribe=stop;
  }
  get fresh(){return this.snapshot&&Date.now()-Date.parse(this.snapshot.updated_at)<45000&&this._hass?.connected!==false;}
  commit(markup){
    const current=this.shadowRoot.querySelector('main.app');
    if(!current){this.shadowRoot.innerHTML=markup;return;}
    // Keep the actual scrolling element alive through HA updates and chart refreshes.
    // Replacing the ShadowRoot recreated it at scrollTop=0 on every telemetry event.
    const position={top:current.scrollTop,left:current.scrollLeft};
    const editor=this.shadowRoot.querySelector('.schedule-editor');
    const editorPosition=editor?{top:editor.scrollTop,left:editor.scrollLeft}:null;
    const template=document.createElement('template');template.innerHTML=markup;
    const next=template.content.querySelector('main.app');
    if(!next){this.shadowRoot.innerHTML=markup;return;}
    const nextEditor=next.querySelector('.schedule-editor');
    if(editor&&nextEditor){editor.replaceChildren(...nextEditor.childNodes);nextEditor.replaceWith(editor);}
    current.replaceChildren(...next.childNodes);
    current.dataset.renderCount=String(Number(current.dataset.renderCount||0)+1);
    current.scrollTop=position.top;current.scrollLeft=position.left;
    if(editor&&editorPosition){editor.scrollTop=editorPosition.top;editor.scrollLeft=editorPosition.left;}
  }
  async send(command,payload){
    if(!this.fresh)throw new Error('Motore non raggiungibile: nessuna modifica inviata');
    const request_id=`cc-${Date.now()}-${++this.seq}`;
    const done=new Promise((resolve,reject)=>{const timer=setTimeout(()=>{this._pending.delete(request_id);reject(new Error('Salvataggio non confermato dal motore'));},30000);this._pending.set(request_id,{resolve,reject,timer});});
    try{await this._hass.callWS({type:'fire_event',event_type:'coordinatore_climatico_command',event_data:{request_id,command,payload}});await done;this._notice='Salvato';}
    catch(e){const p=this._pending.get(request_id);if(p){clearTimeout(p.timer);this._pending.delete(request_id);}this._notice=e.message||String(e);throw e;}
    finally{this.render();}
  }
  async act(command,payload){try{await this.send(command,payload);}catch(e){this._notice=e.message;this.render();}}
  quietState(){const t=this.snapshot?.mapping?.telemetry||{},states=this._hass?.states||{};return quietReading(states[QUIET_ENTITY],states[t.heartbeat],states[t.online],Boolean(this.fresh));}
  quietControls(){
    const q=this.quietState(),admin=this._hass?.user?.is_admin===true,locked=!q.available||!admin||Boolean(this._quietTarget)||q.pending;
    return `<div class="quiet-control"><div class="quiet-label">${icon('quiet')}<b>Quiet Panasonic</b>${this.info('Quiet')}<span role="status">${esc(q.label)}</span></div><div class="segmented quiet-levels" role="group" aria-label="Livello Quiet Panasonic">${QUIET_OPTIONS.map(value=>`<button data-quiet="${value}" aria-label="Quiet ${value==='Off'?'OFF':'livello '+value}" aria-pressed="${q.value===value}" class="${q.value===value?'selected':''}" ${locked||!q.options.includes(value)?'disabled':''}>${value==='Off'?'OFF':value}</button>`).join('')}</div></div><small class="quiet-feedback ${this._quietError?'error':''}" role="status">${esc(this._quietTarget?'In attesa della conferma Panasonic…':this._quietFeedback||(!admin?'Comando riservato agli amministratori':!q.available?'Quiet non disponibile':q.value==='Scheduled'?'Programmazione Quiet Panasonic attiva':'Silenziosità della macchina · non modifica i set delle stanze'))}</small>`;
  }
  async changeQuiet(option){
    if(this._quietTarget)return;
    this._quietTarget=option;this._quietFeedback='';this._quietError=false;this.render();
    try{const changed=await requestQuiet(option,{read:()=>this.quietState(),isAdmin:()=>this._hass?.user?.is_admin===true,send:value=>this._hass.callService('select','select_option',{entity_id:QUIET_ENTITY,option:value})});this._quietFeedback=changed?'Livello Quiet confermato dalla Panasonic':'Livello Quiet già impostato';}
    catch(error){this._quietFeedback=error.message||String(error);this._quietError=true;}
    finally{this._quietTarget=null;this.render();}
  }
  async loadHistory(){
    if(!this._hass||!this.snapshot?.mapping||this.loading)return;
    this.loading=true;const request=this._loadSeq=(this._loadSeq||0)+1;const e=this.snapshot.mapping.energy;
    const summer=this.snapshot.settings.season==='summer';const powerId=e[summer?'cool_power':'heat_power'],thermalId=e[summer?'cool_thermal':'heat_thermal'];const ids=[powerId,thermalId,e.dhw_power,e.dhw_thermal].filter(Boolean);
    const now=Date.now(),start=now-this.range*3600000;this.chartStart=start;this.chartEnd=now;this.historyError='';
    try{
      const heatCounter=e[summer?'cool_energy':'heat_energy'];const counters=[heatCounter,e.dhw_energy].filter(Boolean);
      const [stats,energy,meta]=await Promise.all([
        this._hass.callWS({type:'recorder/statistics_during_period',start_time:new Date(start).toISOString(),end_time:new Date(now).toISOString(),statistic_ids:ids,period:'5minute',types:['mean']}),
        this._hass.callWS({type:'recorder/statistics_during_period',start_time:new Date(midnight(now,-6)-3600000).toISOString(),end_time:new Date(now).toISOString(),statistic_ids:counters,period:'hour',types:['sum','state']}),
        this._hass.callWS({type:'recorder/get_statistics_metadata',statistic_ids:[...ids,...counters]})]);
      if(request!==this._loadSeq)return;
      const unit={};for(const m of Object.values(meta))unit[m.statistic_id]=m.statistics_unit_of_measurement??m.unit_of_measurement;
      const p=statisticRows(stats,powerId,unit[powerId]),t=statisticRows(stats,thermalId,unit[thermalId]),dp=statisticRows(stats,e.dhw_power,unit[e.dhw_power]),dt=statisticRows(stats,e.dhw_thermal,unit[e.dhw_thermal]);
      this.chart={0:alignedRatio(t,p),1:p,2:alignedRatio(dt,dp),3:dp};this.energy={};
      for(const [key,id] of [['heat',heatCounter],['dhw',e.dhw_energy]]){this.energy[key+'Today']=energyDelta(energy[id],midnight(now),now,unit[id]);this.energy[key+'Week']=energyDelta(energy[id],midnight(now,-6),now,unit[id]);}
      this.loaded=Date.now();
    }catch(error){this.chart={};this.energy={};this.historyError='Storico non disponibile: '+(error.message||'errore Home Assistant');}
    finally{if(request===this._loadSeq){this.loading=false;if(!this.shadowRoot.querySelector('dialog')&&!this.shadowRoot.activeElement?.matches('input,select'))this.render();}}
  }
  value(id,units=['°C']){return numeric(this._hass?.states[id],units);}
  toggle(on,command,payload={},label=''){return `<button class="toggle ${on?'on':''}" role="switch" aria-checked="${!!on}" aria-label="${esc(label||command)}" data-command="${command}" data-payload='${esc(JSON.stringify({...payload,value:!on}))}'><span></span></button>`;}
  info(topic){return `<button class="info" data-info="${topic}" aria-label="Informazioni ${topic}" aria-haspopup="dialog">ⓘ</button>`;}
  help(topic){
    const topics={
      Master:['MASTER','Abilita la regolazione dei piani. Le protezioni e i consensi verificati restano necessari. ACS mantiene la gestione Panasonic prevista.'],
      Stagione:['Modalità stagionale','Inverno usa il pavimento, zona 2. Estate è predisposta per i fan-coil, zona 1, ma resta bloccata finché il collaudo estivo non è confermato.'],
      Media:['Media · tutto il piano','Ogni piano ha una modalità indipendente: media aritmetica dei sensori distinti, con tutte le testine richieste aperte o chiuse insieme. Notte include il bagno; Giorno usa Sala e Cucina, senza duplicare il circuito del bagno. Target 21 °C ±1%: apertura a 20,79 °C, chiusura a 21,21 °C. Dentro la fascia mantiene lo stato precedente. Dopo una chiusura rispetta la pausa minima. Un sensore previsto scaduto esclude l’intero gruppo. Una stanza già calda può continuare a ricevere calore con il gruppo. Percentuale = temperatura / target ×100, non misura energia o comfort.'],
      Target:['Temperatura del piano','Manuale usa il set del piano. Crono, con programmazione generale attiva, usa Comfort o Eco. Modificare il target passa il piano in Manuale.'],
      Auto:['Auto del piano','Abilita o esclude questo piano. Vale anche in modalità Media e non abilita da solo il MASTER.'],
      Delta:['Δ max e coordinamento','Differenza tra la stanza più calda e la più fredda del piano. Nel controllo individuale può dare priorità alla stanza più fredda quando supera la soglia condivisa. Non è l’isteresi. In modalità Media resta un dato informativo: le stanze lavorano insieme.'],
      Crono:['Programmazione settimanale','48 fasce di mezz’ora per giorno: Comfort o Eco. Si applica ai piani in Crono quando il cronotermostato generale è abilitato. Il motore continua a lavorare anche con dashboard chiusa.'],
      Profili:['Comfort ed Eco','Temperature usate dalle fasce settimanali. Eco deve essere minore o uguale a Comfort. I piani in Manuale mantengono il loro target.'],
      ACS:['Acqua calda sanitaria','Accumulo e set si riferiscono all’acqua sanitaria, distinta dal buffer. Eco e Plus selezionano i profili ACS già configurati.'],
      Quiet:['Quiet Panasonic','Livelli OFF/1/2/3 di silenziosità della macchina. La conferma arriva dalla Panasonic; non cambia il target delle stanze. Programmato indica la programmazione nativa.'],
      Consumi:['Consumi e prestazioni','Pompe e buffer sono telemetria: ON/OFF non conferma il movimento fisico delle testine. COP = potenza termica / elettrica; a macchina ferma o senza dati validi non viene inventato. Energia da statistiche HA fino all’ultima ora completa.']
    };const entry=topics[topic]||topics.Media;this.dialog(entry[0],`<p class="help-text">${esc(entry[1])}</p>`);
  }
  meanDialog(key){
    const f=this.snapshot.settings.floors[key],plan=this.snapshot.plan.floors[key]||{};
    const initial=Object.values(this.snapshot.plan.rooms).find(x=>x.floor===key&&!x.bath)?.setpoint??f.setpoint;
    let target=initial,percent=f.mean_percent??1;
    const d=this.dialog('Media · '+floorNames[key][0],`<label class="mean-enable"><input id="mean-enabled" type="checkbox" ${f.mean_enabled?'checked':''}> Tutto il piano insieme</label><div class="mean-setting">Target <button data-mean-target="-0.5" aria-label="Riduci target">−</button><b id="mean-target"></b><button data-mean-target="0.5" aria-label="Aumenta target">+</button></div><div class="mean-setting">Tolleranza ± <button data-mean-percent="-0.25" aria-label="Riduci tolleranza">−</button><b id="mean-percent"></b><button data-mean-percent="0.25" aria-label="Aumenta tolleranza">+</button></div><p id="mean-band"></p><p>Media dei sensori distinti: ${key==='night'?'4, incluso Bagno (+1)':key==='day'?'2, Sala e Cucina; bagno con circuito condiviso':'1, Taverna'}. Ogni piano è indipendente.</p><p>Dentro la fascia mantiene lo stato precedente; pausa minima dopo chiusura. Un sensore previsto scaduto chiude il gruppo. Le stanze più calde restano collegate.</p><p>${plan.mean_valid===false?'Dati del gruppo non validi. ':''}Il target segue Crono/Manuale; cambiandolo qui passi a Manuale.</p><p id="mean-feedback" role="status"></p><div class="mean-actions"><button id="mean-cancel">Annulla</button><button id="mean-save">Applica</button></div>`);
    const refresh=()=>{d.querySelector('#mean-target').textContent=fmt(target)+' °C';d.querySelector('#mean-percent').textContent=fmt(percent,2)+' %';d.querySelector('#mean-band').textContent='Apri tutte ≤ '+fmt(target*(1-percent/100),2)+' °C · Chiudi tutte ≥ '+fmt(target*(1+percent/100),2)+' °C';};refresh();
    d.querySelectorAll('[data-mean-target]').forEach(b=>b.onclick=()=>{target=Math.max(16,Math.min(26,target+Number(b.dataset.meanTarget)));refresh();});
    d.querySelectorAll('[data-mean-percent]').forEach(b=>b.onclick=()=>{percent=Math.max(.25,Math.min(5,percent+Number(b.dataset.meanPercent)));refresh();});
    d.querySelector('#mean-cancel').onclick=()=>d.remove();
    d.querySelector('#mean-save').onclick=async()=>{const payload={floor:key,enabled:d.querySelector('#mean-enabled').checked,percent};if(target!==initial)payload.target=target;d.querySelectorAll('button,input').forEach(b=>b.disabled=true);d.querySelector('#mean-feedback').textContent='Attesa conferma motore…';try{await this.send('mean_floor',payload);d.remove();this.render();}catch(e){d.querySelector('#mean-feedback').textContent=e.message;d.querySelectorAll('button,input').forEach(b=>b.disabled=false);}};
  }
  floor(key){
    const [name,sub,ic]=floorNames[key],s=this.snapshot.settings,f=s.floors[key],p=this.snapshot.plan;
    const temp=Object.values(p.rooms).find(x=>x.floor===key&&!x.bath)?.setpoint;
    const rooms=Object.entries(p.rooms).filter(([,x])=>x.floor===key);
    return `<section class="panel floor ${key}"><div class="floor-head"><div class="floor-title">${icon(ic,ic)}<div><h2>${name}</h2><div class="sub">${sub}</div></div></div><div class="mean-header"><button data-mean="${key}" class="mean-button ${f.mean_enabled?'selected':''}" aria-haspopup="dialog">${icon('link')}Media ${f.mean_enabled?'ON':'OFF'}</button>${this.info('Media')}${f.mean_enabled?`<small>Media ${fmt(p.floors[key]?.mean_temperature,2)} °C · ${fmt(p.floors[key]?.mean_progress)} % del target</small>`:''}</div><div class="floor-controls"><div class="setpoint"><label>Temp. master (piano) ${this.info('Target')}</label><div><button data-adjust="${key}" data-delta="-0.5" aria-label="Riduci temperatura ${name}">−</button><b>${fmt(temp)} °C</b><button data-adjust="${key}" data-delta="0.5" aria-label="Aumenta temperatura ${name}">+</button></div></div><div class="segmented small">${['chrono','manual'].map(v=>`<button class="${f.mode===v?'selected':''}" data-floor-mode="${key}" data-value="${v}">${icon(v==='chrono'?'calendar':'hand')}${v==='chrono'?'Crono':'Manuale'}</button>`).join('')}</div><label class="auto">Auto ${this.info('Auto')} ${this.toggle(f.enabled,'floor',{floor:key,key:'enabled'},`Abilita ${name}`)}</label>${key!=='basement'?`<span class="spread">Δ max: <b>${fmt(p.floors[key]?.spread)} °C</b>${this.info('Delta')}</span>`:''}</div></div><div class="rooms">${rooms.map(([k,x])=>this.room(k,x)).join('')}</div></section>`;
  }
  room(key,x){
    const state=x.state;const cls=x.bath?'associated':state==='Anomalia'?'anomaly':['Richiesta','Priorità','Riscaldamento','Manuale ON'].includes(state)?'calling':state==='Attesa'?'waiting':'regular';
    const ic=x.bath?'link':cls==='calling'?'heat':cls==='waiting'?'clock':cls==='anomaly'?'warning':'check';
    return `<article class="room ${cls}" title="${esc(x.reason)} · ${esc(x.valve)}" tabindex="0" data-room="${key}"><strong>${esc(x.name)}</strong><div class="temperature">${icon('thermo')}<b>${fmt(x.temperature)} °C</b></div><div class="room-set">${x.bath&&key==='bath_day'?'Segue Cucina':`Set <b>${fmt(x.setpoint)} °C</b>`}</div><span class="state">${icon(ic)}${x.bath?'Associato':esc(state)}</span>${this.snapshot.settings.floors[x.floor].mean_enabled&&!x.manual_until?`<small class="mean-linked">${icon('link')}Collegata al piano · ${x.valid&&Number.isFinite(x.temperature)&&x.setpoint>0?fmt(x.temperature/x.setpoint*100)+' % del target':'N/D'}</small>`:''}${!x.bath?`<button class="room-timer" data-room-timer="${key}" aria-label="Manuale temporaneo ${esc(x.name)}">${x.manual_until?'Manuale · '+Math.max(0,Math.ceil((x.manual_until*1000-Date.now())/60000))+' min':'Manuale ⏱'}</button>`:''}</article>`;
  }
  performance(){
    const s=this.snapshot.settings,e=this.snapshot.mapping.energy,summer=s.season==='summer';const states=this._hass.states;
    const power=kw(states[e[summer?'cool_power':'heat_power']]),thermal=kw(states[e[summer?'cool_thermal':'heat_thermal']]),dp=kw(states[e.dhw_power]),dt=kw(states[e.dhw_thermal]);
    const t=this.snapshot.mapping.telemetry;
    const pumpMarkup=(id,label)=>{const value=pumpStatus(states[id],states[t.heartbeat],states[t.online],this.fresh);return `<div class="pump-reading"><span>${label}</span><span class="pump-status ${value==='ON'?'on':value==='OFF'?'off':'unknown'}" role="status" aria-label="${label}: ${value}">${icon('pump')}<b>${value}</b></span></div>`;};
    const buffer=temperatureReading(states['sensor.panasonic_heat_pump_main_buffer_temp'],states[t.heartbeat],states[t.online],this.fresh,-10,95);
    const heartbeat=states[this.snapshot.mapping.telemetry.heartbeat];const recent=heartbeat&&Date.now()-Date.parse(heartbeat.last_changed)<120000;
    const currentRatio=(a,b,ai,bi)=>{const sa=states[ai],sb=states[bi];return recent&&sa&&sb&&Date.now()-Date.parse(sa.last_updated)<120000&&Date.now()-Date.parse(sb.last_updated)<120000&&Math.abs(Date.parse(sa.last_updated)-Date.parse(sb.last_updated))<=60000?ratio(a,b):null;};
    const cop=currentRatio(thermal,power,e[summer?'cool_thermal':'heat_thermal'],e[summer?'cool_power':'heat_power']),dcop=currentRatio(dt,dp,e.dhw_thermal,e.dhw_power);
    return `<section class="panel performance"><h2>${icon('power','electric')}Consumi e prestazioni (Panasonic)${this.info('Consumi')}</h2>${this.quietControls()}<div class="plant-readings">${pumpMarkup(t.pump1,'Pompa Zona 1 · Fan-coil')}${pumpMarkup(t.pump2,'Pompa Zona 2 · Miscelato')}<div class="buffer-reading"><span>Buffer tank</span><b>${fmt(buffer)} °C</b></div></div><div class="performance-top">${[[summer?'Raffrescamento':'Riscaldamento',summer?'Zona 1':'Zona 2',power,cop,summer?'EER':'COP','heat'],['ACS','Acqua Calda Sanitaria',dp,dcop,'COP ACS','drop']].map(([name,zone,p,c,l,ic])=>`<div class="performance-zone"><div class="zone-title">${icon(ic,ic)}<b>${name} <small>(${zone})</small></b></div><div class="metrics"><div><span>Potenza istantanea</span><strong>${fmt(recent?p:null)} kW</strong></div><div>${icon('leaf')}<span>${l}</span><strong>${fmt(c)}</strong></div></div></div>`).join('')}</div><div class="chart-toolbar"><div class="segmented ranges">${[1,6,24].map(n=>`<button data-range="${n}" class="${this.range===n?'selected':''}">${n} h</button>`).join('')}</div><div class="legend">${seriesNames.map((n,i)=>`<button data-series="${i}" class="series s${i} ${this.hiddenSeries.has(i)?'muted':''}"><i></i>${summer&&i<2?n.replace('COP','EER').replace('Ris.','Raff.').replace('Risc.','Raff.'):n}</button>`).join('')}</div></div>${this.graph()}<div class="energy-grid">${[['heatToday','Energia oggi',summer?'Raffrescamento':'Riscaldamento','bars'],['dhwToday','Energia ACS oggi','','drop'],['heatWeek','Energia 7 giorni',summer?'Raffrescamento':'Riscaldamento','calendar'],['dhwWeek','Energia ACS 7 giorni','','drop']].map(([k,l,sub,ic])=>`<div class="energy" title="${k.includes('Week')?'Sette giorni di calendario, oggi incluso. ':''}Statistiche fino ${this.energy[k]?.until?clock(this.energy[k].until):'N/D'}">${icon(ic,k.startsWith('dhw')?'purple':'')}<div><small>${l}</small><b>${fmt(this.energy[k]?.value)} kWh</b>${sub?`<span>${sub}</span>`:''}</div></div>`).join('')}</div><div class="data-note">${esc(this.historyError||'Telemetria Panasonic · energia da contatori HA, fino all’ultima ora completa · ausiliari non verificati')}</div></section>`;
  }
  graph(){
    const start=this.chartStart||Date.now()-86400000,end=this.chartEnd||Date.now();const all=Object.values(this.chart).flat();const maxCop=Math.max(5,...[...(this.chart[0]||[]),...(this.chart[2]||[])].map(r=>r.value||0)),maxPower=Math.max(8,...[...(this.chart[1]||[]),...(this.chart[3]||[])].map(r=>r.value||0));
    return `<div class="graph"><div class="axis-names"><span>${this.snapshot.settings.season==='summer'?'EER':'COP'}</span><span>Potenza (kW)</span></div><svg viewBox="0 0 700 220" preserveAspectRatio="none" role="img" aria-label="Storico reale COP e potenza"><g transform="translate(40 8)">${[0,1,2,3,4].map(i=>`<path class="gridline" d="M0 ${i*42.5}H600"/><text class="cop-label" x="-8" y="${i*42.5+4}" text-anchor="end">${fmt(maxCop*(1-i/4))}</text><text class="power-label" x="610" y="${i*42.5+4}">${fmt(maxPower*(1-i/4))}</text>`).join('')}${Array.from({length:9},(_,i)=>`<path class="gridline" d="M${i*75} 0V170"/><text class="time-label" x="${i*75}" y="195" text-anchor="middle">${clock(start+(end-start)*i/8)}</text>`).join('')}${[0,1,2,3].filter(i=>!this.hiddenSeries.has(i)).map(i=>`<path class="curve s${i}" d="${svgPath(this.chart[i]||[],start,end,i%2?maxPower:maxCop)}"/>`).join('')}${!all.some(r=>Number.isFinite(r.value))?`<text x="300" y="90" text-anchor="middle" class="no-data">${this.loading?'Caricamento storico…':'Nessun dato disponibile'}</text>`:''}</g></svg></div>`;
  }
  render(){
    if(!this.config)return;
    if(this._closed){this.shadowRoot.innerHTML='<button id="reopen">Apri Coordinatore climatico</button>';this.shadowRoot.querySelector('button').onclick=()=>{this._closed=false;this.render();};return;}
    if(!this._hass?.states||!this.snapshot?.settings?.floors||!this.snapshot?.plan?.rooms||!this.snapshot?.mapping?.telemetry){this.shadowRoot.innerHTML=`<link rel="stylesheet" href="/local/coordinatore-climatico/coordinatore.css?v=20261119-3"><div class="app loading"><h1>Coordinatore climatico</h1><p>NON ATTIVO · In attesa del motore Home Assistant</p><button id="exit">Esci</button></div>`;this.shadowRoot.querySelector('#exit').onclick=()=>this.exit();return;}
    const a=this.snapshot,s=a.settings,p=a.plan,sch=p.schedule,t=a.mapping.telemetry;const status=this.fresh?this.status:'ERRORE';
    const outside=temperatureReading(this._hass.states['sensor.panasonic_heat_pump_main_outside_temp'],this._hass.states[t.heartbeat],this._hass.states[t.online],this.fresh,-50,60);
    const weather=this._hass.states['weather.forecast_casa'],humidity=weatherHumidity(weather,this._hass.connected!==false);
    const quiet=this.quietState();
    const active=status==='OPERATIVO',season=s.season,dhw=this.value(t.dhw_temp),target=this.value(t.dhw_target);const production=this._hass.states[t.threeway]?.state==='Tank';
    const todaySlots=s.week[this.day];const date=sch.next?clock(sch.next.at):'—';
    this.commit(`<link rel="stylesheet" href="/local/coordinatore-climatico/coordinatore.css?v=20261119-3"><main class="app" tabindex="0"><header><button class="brand" id="fullscreen" title="Schermo intero">${icon('home')}<div><h1>Coordinatore climatico</h1><p>Home Assistant · ${active?'Sistema operativo':status==='ERRORE'?'Sistema con anomalie':'Sistema non attivo'} <span class="alaska-signature">Alaska</span></p></div></button><div class="header-right"><nav class="panel-links" aria-label="Navigazione pannelli"><a href="/aquarea-clima/impianto" target="_top">${icon('gear')}Controlli PDC</a><a href="/lovelace/0" target="_top">${icon('home')}Home HA</a></nav>${quiet.active?`<span class="quiet-badge" title="Quiet Panasonic attivo">${icon('quiet')}Quiet ${esc(quiet.value==='Scheduled'?'programmato':quiet.value)}</span>`:''}<div class="outdoor-reading" role="status" aria-label="Condizioni esterne"><div>Esterno <b>${fmt(outside)} °C</b> · UR <b>${fmt(humidity,0)} %</b></div><small>Temp. Panasonic · UR meteo${humidity!==null?' · '+clock(weather.last_updated):''}</small></div><button class="system-badge ${active?'ok':status==='ERRORE'?'error':'inactive'}" id="diagnostics">${icon(active?'check':'warning')}${esc(status||'NON ATTIVO')}</button><button class="icon-button" id="maintenance" aria-label="Esci per manutenzione" title="Manutenzione · Esc">${icon('expand')}</button></div></header>
      <div class="top-row"><section class="panel master-panel"><div class="master"><div><b>MASTER ${this.info('Master')}</b><span>${season==='summer'?'Raffrescamento':'Riscaldamento'}</span></div>${this.toggle(s.master,'master',{},'MASTER climatizzazione')}</div><div class="season"><b>Modalità stagionale ${this.info('Stagione')}</b><div class="segmented">${[['summer','Estate','sun'],['winter','Inverno','snow'],['off','Off','power']].map(([k,l,i])=>`<button data-season="${k}" class="${season===k?'selected':''}">${icon(i,i)}${l}</button>`).join('')}</div></div></section>
      <section class="panel chrono-zone"><div class="chrono-master">${icon('calendar')}<b>Cronotermostato<br>settimanale</b>${this.toggle(s.schedule_enabled,'schedule_enabled',{},'Cronotermostato settimanale')}</div><div class="active-zones"><b>Zone attive <span>(in base alla modalità)</span></b><div>${[['summer','Zona 1 — Fan-coil','Estate'],['winter','Zona 2 — Pavimento','Inverno']].map(([k,n,l])=>`<div class="zone-chip ${s.master&&season===k?'chosen':''}">${icon('zones')}<div><b>${n}</b><span>${l}</span></div></div>`).join('')}</div></div></section>
      <section class="panel dhw-panel">${icon('drop')}<div><div class="dhw-heading"><h3>ACS sempre attiva ${this.info('ACS')}</h3></div><span class="dhw-reading">Accumulo: <b>${fmt(dhw)} °C</b> · Set <b>${fmt(target,0)} °C</b>${production?' · In produzione':''}</span><div class="dhw-profiles"><button data-dhw="eco" class="${target===40?'profile-active':''}">Eco: <b>40 °C</b></button><i></i><button data-dhw="plus" class="${target===55?'profile-active':''}">Plus: <b>55 °C</b></button></div></div></section></div>
      <div class="columns"><div class="left-column"><section class="panel weekly"><div class="weekly-head"><h2>${icon('calendar')}Cronotermostato Settimanale — Casa ${this.info('Crono')}${this.info('Profili')}</h2><div class="weekly-controls"><label class="pill comfort">${icon('sun')}Comfort: <input type="number" aria-label="Temperatura Comfort" data-temp="comfort" min="16" max="26" step="0.5" value="${s.comfort}"> °C</label><label class="pill eco">${icon('moon')}Eco: <input type="number" aria-label="Temperatura Eco" data-temp="eco" min="16" max="26" step="0.5" value="${s.eco}"> °C</label><button class="copy-button" id="copy">${icon('copy')}Copia giorno <span>⌄</span></button></div></div><div class="days">${days.map((d,i)=>`<button data-day="${i}" class="${i===this.day?'selected':''}">${d}</button>`).join('')}</div><div class="schedule-editor"><div class="timeline"><div class="slots" role="group" aria-label="48 fasce da trenta minuti">${todaySlots.map((v,i)=>`<button class="slot ${v?'comfort':'eco'}" data-slot="${i}" aria-label="${days[this.day]} ${String(Math.floor(i/2)).padStart(2,'0')}:${i%2?'30':'00'} ${v?'Comfort':'Eco'}" aria-pressed="${!!v}" title="${String(Math.floor(i/2)).padStart(2,'0')}:${i%2?'30':'00'} · ${v?'Comfort':'Eco'}"></button>`).join('')}</div><div class="time-axis">${['00:00','03:00','06:00','09:00','12:00','15:00','18:00','21:00','24:00'].map(t=>`<span>${t}</span>`).join('')}</div></div><div class="schedule-legend"><span><i class="comfort"></i>Comfort (${fmt(s.comfort)} °C)</span><span><i class="eco"></i>Eco (${fmt(s.eco)} °C)</span></div></div><div class="schedule-footer"><span class="pill ${sch.profile}">${icon(sch.profile==='comfort'?'sun':'moon')}Fascia attuale: <b>${sch.profile==='comfort'?'Comfort':'Eco'} (${fmt(sch.temperature)} °C)</b></span><span class="pill next">Prossimo cambio: <b>${date}</b>${sch.next?icon(sch.next.profile==='comfort'?'sun':'moon'):''}<b>${sch.next?`${sch.next.profile==='comfort'?'Comfort':'Eco'} (${fmt(sch.next.temperature)} °C)`:'Nessuno'}</b></span></div></section>${this.floor('night')}${this.floor('day')}${this.floor('basement')}</div><div class="right-column">${this.performance()}<section class="panel parameters"><h2>${icon('gear')}Parametri condivisi <span>(coordinatori)</span></h2>${[['spread','Differenza massima tra stanze',0.2,3,0.1,'°C'],['wait_minutes','Attesa massima richiesta isolata',0,60,1,'min'],['urgent','Deficit urgente (avvio immediato)',0.2,2,0.1,'°C'],['ordinary','Soglia richiesta ordinaria',0.1,1,0.1,'°C']].map(([k,l,min,max,step,u])=>`<label class="parameter"><span>${l}</span><input type="range" min="${min}" max="${max}" step="${step}" value="${s.params[k]}" data-param="${k}" aria-label="${l}"><b>${fmt(s.params[k],u==='min'?0:1)} ${u}</b></label>`).join('')}</section><section class="panel system"><h2>${icon('check')}Stato sistema</h2><div class="system-inner ${active?'verified':''}"><div class="system-left"><div>${icon(active?'check':'warning')}<span>Coordinatori ${active?'attivi':status==='ERRORE'?'con anomalie':'non attivi'}<br>(Piano +1 e Piano 0)</span></div><div>${icon('link')}<span>Bagni associati ai piani</span></div><div>${icon('gear')}<span>Taverna indipendente</span></div></div><div class="system-right"><div>${icon('calendar')}<span>Richieste attive: <b>${p.active_requests}</b></span></div><div>${icon('zones')}<span>Relè comandati: <b>${a.relays_on}</b></span></div><div>${icon('heat')}<span>Consenso Panasonic: <b>N/D</b></span></div></div></div><button class="blocker" id="blockers">${esc(!this.fresh?'Collegamento al motore interrotto':a.blockers[0]||(!a.tests_passed?'Collaudo fisico da completare':'Controlli superati'))}${a.blockers.length>1?` · +${a.blockers.length-1}`:''}</button></section></div></div><div class="toast ${this._notice&&this._notice!=='Salvato'?'show error':''}" role="status">${esc(this._notice)}</div></main>`);
    this.bind();
  }
  bind(){
    const root=this.shadowRoot;
    root.querySelectorAll('[data-room-timer]').forEach(b=>{b.onclick=e=>{e.stopPropagation();this.timerDialog(b.dataset.roomTimer);};b.onkeydown=e=>e.stopPropagation();});
    root.querySelectorAll('[data-info]').forEach(b=>b.onclick=e=>{e.preventDefault();e.stopPropagation();this.help(b.dataset.info);});
    root.querySelectorAll('[data-mean]').forEach(b=>b.onclick=()=>this.meanDialog(b.dataset.mean));
    root.querySelectorAll('[data-quiet]').forEach(button=>button.onclick=()=>this.changeQuiet(button.dataset.quiet));
    root.querySelectorAll('[data-command]').forEach(b=>b.onclick=()=>this.act(b.dataset.command,JSON.parse(b.dataset.payload)));
    root.querySelectorAll('[data-season]').forEach(b=>b.onclick=()=>{this.act('season',{value:b.dataset.season}).then(()=>{this.loaded=0;this.loadHistory();});});
    root.querySelectorAll('[data-day]').forEach(b=>b.onclick=()=>{this.day=+b.dataset.day;this.render();});
    root.querySelectorAll('[data-slot]').forEach(b=>b.onclick=()=>this.act('slot',{day:this.day,slot:+b.dataset.slot}));
    root.querySelectorAll('[data-temp]').forEach(b=>b.onchange=()=>this.act('temperature',{key:b.dataset.temp,value:+b.value}));
    root.querySelectorAll('[data-param]').forEach(b=>{b.oninput=()=>{b.nextElementSibling.textContent=fmt(+b.value,b.dataset.param==='wait_minutes'?0:1)+(b.dataset.param==='wait_minutes'?' min':' °C');};b.onchange=()=>this.act('parameter',{key:b.dataset.param,value:+b.value});});
    root.querySelectorAll('[data-adjust]').forEach(b=>b.onclick=()=>this.act('floor_adjust',{floor:b.dataset.adjust,delta:+b.dataset.delta}));
    root.querySelectorAll('[data-floor-mode]').forEach(b=>b.onclick=()=>this.act('floor',{floor:b.dataset.floorMode,key:'mode',value:b.dataset.value}));
    root.querySelectorAll('[data-dhw]').forEach(b=>b.onclick=()=>this.act('dhw',{profile:b.dataset.dhw}));
    root.querySelectorAll('[data-range]').forEach(b=>b.onclick=()=>{this.range=+b.dataset.range;this.chart={};this.render();this.loadHistory();});
    root.querySelectorAll('[data-series]').forEach(b=>b.onclick=()=>{const n=+b.dataset.series;this.hiddenSeries.has(n)?this.hiddenSeries.delete(n):this.hiddenSeries.add(n);this.render();});
    root.querySelector('#copy').onclick=()=>this.copyDialog();root.querySelector('#fullscreen').onclick=()=>{if(!document.fullscreenElement)document.documentElement.requestFullscreen?.().catch(()=>{});else document.exitFullscreen?.();};
    root.querySelector('#maintenance').onclick=()=>this.maintenance();root.querySelector('#diagnostics').onclick=root.querySelector('#blockers').onclick=()=>this.diagnostics();
    const requested=new URL(location.href).searchParams.get('manual_room');if(requested&&this.fresh&&this.snapshot.manual_timer_supported&&!this._manualLinkOpened&&this.snapshot.plan.rooms[requested]&&!this.snapshot.plan.rooms[requested].bath){this._manualLinkOpened=true;this.timerDialog(requested);}
    root.querySelectorAll('[data-room]').forEach(b=>{b.onclick=()=>this.roomDetails(b.dataset.room);b.onkeydown=e=>{if(e.key==='Enter')this.roomDetails(b.dataset.room);};});
  }
  dialog(title,body){
    this.shadowRoot.querySelector('dialog')?.remove();const d=document.createElement('dialog');d.innerHTML=`<div class="dialog-head"><h2>${esc(title)}</h2><button class="close" aria-label="Chiudi">×</button></div>${body}`;this.shadowRoot.append(d);d.querySelector('.close').onclick=()=>d.remove();d.addEventListener('cancel',e=>{e.preventDefault();d.remove();});d.showModal();return d;
  }
  copyDialog(){
    const d=this.dialog(`Copia ${days[this.day]}`,`<div class="copy-days">${days.map((l,i)=>`<label><input type="checkbox" value="${i}" ${i===this.day?'disabled':''}>${l}</label>`).join('')}</div><div class="copy-actions">${[['feriali','Feriali'],['weekend','Weekend'],['week','Intera settimana'],['chosen','Giorni scelti']].map(([k,l])=>`<button data-copy="${k}">${l}</button>`).join('')}</div>`);
    d.querySelectorAll('[data-copy]').forEach(b=>b.onclick=async()=>{const group=b.dataset.copy;const targets=group==='feriali'?[0,1,2,3,4]:group==='weekend'?[5,6]:group==='week'?[0,1,2,3,4,5,6]:[...d.querySelectorAll('input:checked')].map(x=>+x.value);if(!targets.length)return;d.remove();await this.act('copy_day',{source:this.day,targets});});
  }
  maintenance(){this.dialog('Manutenzione',`<p>Il motore continua a funzionare anche quando esci dalla dashboard.</p><button id="leave">Mostra navigazione Home Assistant</button>`).querySelector('#leave').onclick=()=>this.exit();}
  exit(){this._closed=true;if(document.fullscreenElement)document.exitFullscreen?.();this.render();}
  diagnostics(){const a=this.snapshot;this.dialog('Diagnostica sistema',`<p>${esc(this.fresh?this.status:'ERRORE · Motore non raggiungibile')}</p><ul>${a.blockers.map(x=>`<li>${esc(x)}</li>`).join('')}</ul><p>Ultimo aggiornamento: ${clock(a.updated_at)} · Comandi fisici da questo avvio: ${a.physical_commands}</p><p>${esc(a.consent_note)}</p><h3>Ultime decisioni</h3>${a.decisions.slice(-5).reverse().map(x=>`<p><small>${clock(x.at)}</small> ${esc(x.message)}</p>`).join('')}`);}
  timerDialog(key){
    const x=this.snapshot.plan.rooms[key];if(!x||x.bath)return;
    const choices=[0,15,30,45,60,90,120],d=this.dialog('Manuale temporaneo · '+x.name,`<p>Richiesta ON della singola stanza. Alla scadenza torna ad Auto: può restare ON se il coordinatore richiede calore.</p>${key==='kitchen'?'<p>Cucina e Bagno (0) condividono il relè: si aprono insieme.</p>':''}<p>Prevale sul controllo Media della stanza; le altre restano automatiche. MASTER, Inverno, Auto del piano, sensori e protezioni devono essere validi. La pausa minima resta attiva.</p><label for="timer-duration">Durata: <b id="timer-value">30 min</b></label><input id="timer-duration" type="range" min="0" max="6" step="1" value="2" aria-label="Durata manuale stanza"><div class="timer-stops">${choices.map(v=>'<span>'+v+'</span>').join('')}</div><p id="timer-current" role="status"></p><p id="timer-feedback" role="status"></p><div class="mean-actions"><button id="timer-cancel">Annulla</button><button id="timer-auto">Torna Auto</button><button id="timer-start">Avvia manuale</button></div>`);
    const slider=d.querySelector('#timer-duration'),ready=this.snapshot.manual_timer_supported===true&&this._hass?.user?.is_admin===true&&this.fresh;
    const update=()=>{const until=this.snapshot.manual_timers?.[key],left=Math.max(0,Math.ceil(((until||0)*1000-Date.now())/1000));d.querySelector('#timer-current').textContent=left?'Manuale attivo · '+Math.floor(left/60)+' min '+String(left%60).padStart(2,'0')+' s residui':'Auto · nessun timer attivo';};update();
    const clock=setInterval(()=>{if(!d.isConnected){clearInterval(clock);return;}update();},1000);
    slider.oninput=()=>{const value=choices[Number(slider.value)];d.querySelector('#timer-value').textContent=value?value+' min':'0 min · Auto';slider.setAttribute('aria-valuetext',value+' minuti');d.querySelector('#timer-start').textContent=value?'Avvia manuale':'Ripristina Auto';};
    const send=async minutes=>{d.querySelectorAll('button,input').forEach(b=>b.disabled=true);d.querySelector('#timer-feedback').textContent='Attesa conferma motore…';try{await this.send('room_timer',{room:key,minutes});clearInterval(clock);d.remove();this.render();}catch(e){d.querySelector('#timer-feedback').textContent=e.message;d.querySelectorAll('button,input').forEach(b=>b.disabled=false);}};
    d.querySelector('#timer-cancel').onclick=()=>{clearInterval(clock);d.remove();};d.querySelector('#timer-start').onclick=()=>send(choices[Number(slider.value)]);d.querySelector('#timer-auto').onclick=()=>send(0);
    if(!ready){d.querySelector('#timer-start').disabled=true;d.querySelector('#timer-auto').disabled=true;d.querySelector('#timer-feedback').textContent='Comando non disponibile: serve motore aggiornato, collegamento e utente amministratore.';}
  }
  roomDetails(k){const x=this.snapshot.plan.rooms[k],m=this.snapshot.mapping.rooms[k];this.dialog(x.name,`<p>${esc(x.reason)}</p><p>Sensore: ${esc(m.sensor||'Nessun sensore separato rilevato')}</p><p>Relè: ${esc(m.relay||'Non abbinato')} · ${esc(x.relay_state||'N/D')}</p><p>${esc(x.valve)} · apertura fisica non telemisurata</p><p>Attesa: ${fmt(x.waiting_seconds/60,0)} min · Richiesta: ${fmt((x.request_seconds||0)/60,0)} min</p>`);}
}
if(!customElements.get('coordinatore-climatico-card'))customElements.define('coordinatore-climatico-card',CoordinatoreClimaticoCard);
window.customCards=window.customCards||[];window.customCards.push({type:'coordinatore-climatico-card',name:'Coordinatore climatico',description:'Controllo climatico collegato al motore dedicato'});
