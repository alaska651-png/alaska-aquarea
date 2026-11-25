"""Persistent coordinator for Docker. HA handles authentication; no secret reaches the card.

Start is deliberately disarmed. commissioning.json is a server-side, manually reviewed
record and cannot be modified through dashboard events. No compressor/pump power command.
"""
from __future__ import annotations
import asyncio, copy, json, logging, os, time
from datetime import datetime, timezone
from pathlib import Path
import aiohttp
from engine import Engine, defaults, validate, number
from mode_policy import MODES, POWER_ENTITY, validate_mode, fingerprint, capability

LOG=logging.getLogger('coordinatore')
DATA=Path(os.environ.get('CC_DATA','/data'))
HA=os.environ.get('HA_URL','http://127.0.0.1:8123').rstrip('/')
TOKEN=os.environ['HA_TOKEN']
STATUS='sensor.coordinatore_climatico'
EVENT='coordinatore_climatico_command'
UTC=timezone.utc

def read(path,fallback):
    try:return json.loads(path.read_text())
    except FileNotFoundError:return copy.deepcopy(fallback)

def atomic(path,data):
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2));tmp.chmod(0o600);tmp.replace(path)

def confirmed_buffer_path(a):
    """A verified secondary bypass is distinct from measuring the primary PDC flow."""
    return (a.get('hydraulic_layout')=='buffer_secondary_bypass'
            and a.get('hydraulic_path_verified') is True
            and a.get('secondary_pump_verified') is True
            and a.get('safe_closure_with_pump') is True
            and a.get('flow_monitor_scope')=='primary_only')

class Coordinator:
    def __init__(self):
        self.mapping=read(DATA/'entity-map.json',{})
        self.settings=validate(read(DATA/'settings.json',defaults()))
        manual=read(DATA/'manual-state.json',{})
        self.manual_timers=manual.get('timers',{});self.manual_receipts=manual.get('receipts',{})
        self.engine=Engine();self.states={};self.lock=asyncio.Lock();self.connected=False
        self.errors=[];self.decisions=[];self.ack=None;self.command_fault=None;self.started=time.time()
        self.open_since={};self.actual_commands=0;self.last_signature=None;self.stop=False;self.ws=None
        # Re-establish readback after every process restart, never restore an open-valve claim.
        self.mode_transition_active=False
        self.mode_receipts=read(DATA/'mode-receipts.json',{})
        self.restored=False;self.last_published=None;self.last_refresh=0;self.low_flow_since=None;self.last_safety_attempt=0;self.failed_off=set()

    async def api(self,method,path,data=None):
        async with self.http.request(method,HA+'/api/'+path,json=data,timeout=aiohttp.ClientTimeout(total=20)) as r:
            r.raise_for_status();return await r.json()

    def state(self,entity):return self.states.get(entity,{}).get('state')

    def fresh(self,entity,limit=120):
        s=self.states.get(entity,{})
        try:return 0<time.time()-datetime.fromisoformat(s['last_changed']).timestamp()<limit
        except (KeyError,ValueError,TypeError):return False

    def attest(self):return read(DATA/'commissioning.json',{})

    def blockers(self):
        a=self.attest();m=self.mapping;b=[]
        if not a.get('control_enabled',False):b.append('Controllo reale non ancora abilitato')
        if not a.get('user_authorized_physical_commands'):b.append('Primo comando reale non autorizzato')
        if not a.get('old_system_excluded'):b.append('Esclusione vecchio sistema non confermata')
        buffer_path=confirmed_buffer_path(a)
        if not a.get('hydraulics_verified') and not buffer_path:b.append('Percorso idraulico e circolazione secondaria da verificare')
        if not a.get('bath_contacts_verified'):b.append('Consensi bagni da verificare')
        if not a.get('room_mapping_verified'):b.append('Abbinamenti stanze da confermare')
        if not a.get('panasonic_control_verified'):b.append('Modalità e consenso Panasonic da collaudare')
        if a.get('user_authorized_physical_commands'):
            if not buffer_path:
                try:number(a.get('minimum_flow_lpm'),1,60)
                except (ValueError,TypeError):b.append('Portata minima di progetto non configurata')
            if not a.get('safe_closure_with_pump'):b.append('Bypass / circuito minimo durante la chiusura non verificato')
        if self.settings['season']=='summer' and not a.get('fan_coils_verified'):b.append('Regolazione fan-coil non verificata')
        retired=set(m.get('retired_climates',[]))
        if any(self.state(e)!='off' and not (e in retired and self.state(e) is None) for e in m.get('legacy_climates',[])):b.append('Vecchi termostati ancora attivi o non disponibili')
        if any(self.state(e)!='off' for e in m.get('competing_automations',[])):b.append('Automazioni concorrenti abilitate o non disponibili')
        if self.state(m['telemetry']['online'])!='on' or not self.fresh(m['telemetry']['heartbeat']):b.append('Telemetria HeishaMon non aggiornata')
        if self.state(m['telemetry']['error']) not in ['No error']:b.append('Errore Panasonic o stato errore non disponibile')
        if self.command_fault:b.append(self.command_fault)
        if self.settings.get('pdc_mode_transition') in ('pending','uncertain') and not self.mode_transition_active:b.append('Cambio modalità non verificato: intervento amministrativo necessario')
        return b

    def readings(self):
        out={}
        for k,c in self.mapping['rooms'].items():
            s=self.states.get(c.get('sensor'),{})
            try:t=float(s['state']);age=time.time()-datetime.fromisoformat(s.get('last_reported',s['last_updated'])).timestamp()
            except (KeyError,ValueError,TypeError):t=None;age=float('inf')
            out[k]={'temperature':t,'age':age,'sample_at':s.get('last_updated'),'unit':s.get('attributes',{}).get('unit_of_measurement')}
        return out

    def restore_engine_state(self,record=None):
        """Restore recent decisions only after fresh physical relay readback agrees."""
        self.restored=True
        try:
            if record is None:
                record=read(DATA/'engine-state.json',None) or read(DATA/'upgrade-handoff.json',None)
            if not record or not -5<=time.time()-record['at']<90:return False
            if validate(copy.deepcopy(record['settings']))!=self.settings:return False
            actual={c['relay']:self.state(c['relay']) for c in self.mapping['rooms'].values() if c.get('relay')}
            if actual!=record['relays'] or any(v not in ('on','off') for v in actual.values()):return False
            if 'memory' in record:
                if set(record['memory'])!=set(self.mapping['rooms']):return False
                self.engine.memory=copy.deepcopy(record['memory']);self.engine.mean_memory=copy.deepcopy(record.get('mean_memory',{}));self.open_since=copy.deepcopy(record.get('open_since',{}))
            else:
                at=record['at']
                for k,c in self.mapping['rooms'].items():
                    x=record['plan']['rooms'][k];relay=c['relay'];r=self.states[relay];sensor=self.states.get(c.get('sensor'),{})
                    changed=datetime.fromisoformat(r['last_changed']).timestamp();on=x['desired'] and actual[relay]=='on'
                    self.engine.memory[k]={'waiting_since':at-x['waiting_seconds'] if x['request'] else None,'on_since':at-x.get('request_seconds',0) if on else None,'off_since':changed if actual[relay]=='off' else at-self.settings['params']['min_off_seconds'],'filtered':x['filtered_temperature'],'sample_at':sensor.get('last_updated') if str(sensor.get('state'))==str(x['temperature']) else None,'last_raw':x['temperature']}
                    if actual[relay]=='on':self.open_since[relay]=changed
            self.log('restore','Memoria regolazione ripristinata dopo verifica relè');return True
        except (KeyError,TypeError,ValueError):
            self.engine=Engine();self.open_since={};return False

    def log(self,kind,message,**kw):
        row={'at':datetime.now(UTC).isoformat(),'kind':kind,'message':message,**kw};self.decisions.append(row);self.decisions=self.decisions[-50:]
        with (DATA/'decisions.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n')
        LOG.info('%s: %s',kind,message)

    async def physical(self,domain,service,payload,readback,expected,timeout=20,emergency=False):
        # Every single physical call passes the immutable commissioning gate and live exclusion checks.
        blocked=self.blockers()
        safe_emergency=(emergency and domain=='mqtt' and service=='publish' and payload.get('topic')==self.mapping['mqtt_prefix']+'commands/SetOperationMode' and payload.get('payload')=='3')
        if safe_emergency:blocked=[x for x in blocked if x!=self.command_fault]
        safe_off=(domain=='switch' and service=='turn_off' and payload.get('entity_id')==readback and readback in {c.get('relay') for c in self.mapping['rooms'].values()} and confirmed_buffer_path(self.attest()))
        if safe_off:
            # The verified buffer path permits closing secondary valves even if
            # heat-pump telemetry fails. Ownership/authorization blockers remain.
            blocked=[x for x in blocked if x not in ['Telemetria HeishaMon non aggiornata','Errore Panasonic o stato errore non disponibile',self.command_fault]]
        if blocked:raise ValueError('Comando fisico bloccato: '+ '; '.join(blocked))
        def matches():
            actual=self.state(readback)
            if actual==str(expected):return True
            try:return float(actual)==float(expected)
            except (TypeError,ValueError):return False
        if matches():return
        old_updated=self.states.get(readback,{}).get('last_updated')
        self.actual_commands+=1;self.log('command',f'{domain}.{service}',entity=readback,expected=expected)
        await self.api('POST',f'services/{domain}/{service}',payload)
        deadline=time.monotonic()+timeout
        # WS reader keeps running while serialized tick/command waits here.
        while time.monotonic()<deadline:
            await asyncio.sleep(0.5)
            if matches():return
        self.command_fault=f'Mancata conferma {readback} = {expected}; reinvio automatico disabilitato'
        if domain=='switch' and service=='turn_off':self.failed_off.add(readback)
        self.log('error',self.command_fault);raise ValueError(self.command_fault)

    async def mqtt_set(self,command,value,readback,expected):
        await self.physical('mqtt','publish',{'topic':self.mapping['mqtt_prefix']+'commands/'+command,'payload':str(value),'retain':False,'qos':0},readback,expected)

    async def reconcile(self,plan):
        m=self.mapping;t=m['telemetry'];s=self.settings
        if self.blockers():return
        # Preserve demand shedding, defrost and sanitisation, including their hydraulic paths.
        if self.state(t['load_shed'])!='off':raise ValueError('Antidistacco attivo o non disponibile: comandi sospesi')
        if self.state(t['defrost'])!='off' or self.state(t['sterilization'])!='off':return
        selected=s.get('pdc_mode')
        if selected=='off':
            await self.mqtt_set('SetHeatpump',0,POWER_ENTITY,'off')
            if not confirmed_buffer_path(self.attest()) and any(self.state(t[k])!='off' for k in ('pump1','pump2')):raise ValueError('Chiusura in attesa del circolatore fermo')
            for relay in {c['relay'] for c in m['rooms'].values() if c.get('relay')}:
                await self.physical('switch','turn_off',{'entity_id':relay},relay,'off')
            return
        climate=s['master'] and s['season']!='off' and any(f['enabled'] for f in s['floors'].values())
        if climate and selected is not None:validate_mode(selected,s,self.attest(),self.states)
        desired_mode='Heat+DHW' if climate and s['season']=='winter' else 'Cool+DHW' if climate and s['season']=='summer' else 'DHW only'
        if climate and selected is not None:desired_mode=MODES[selected][2]
        desired_code={'Heat':0,'Cool':1,'Heat+DHW':4,'Cool+DHW':5,'DHW only':3}[desired_mode]
        current_zone=self.state(t['zones'])
        zone='Zone 2' if s['season']=='winter' else 'Zone 1'
        if climate and current_zone!=zone:
            # Switch through DHW-only; never expose both incompatible circuits at the same time.
            await self.mqtt_set('SetOperationMode',3,t['mode'],'DHW only')
            if self.state(t['pump1'])!='off' or self.state(t['pump2'])!='off':raise ValueError('Attesa arresto circolatori per cambio stagione')
            for relay in {c['relay'] for c in m['rooms'].values() if c.get('relay')}:
                await self.physical('switch','turn_off',{'entity_id':relay},relay,'off')
            await self.mqtt_set('SetZones',1 if zone=='Zone 2' else 0,t['zones'],zone)
        # Shut climate down before closing all hydraulic paths. DHW remains enabled.
        if not climate or s['season']!='winter':
            await self.mqtt_set('SetOperationMode',desired_code,t['mode'],desired_mode)
            if self.state(t['pump2'])!='off' and not confirmed_buffer_path(self.attest()):raise ValueError('Chiusura testine in attesa del circolatore Zona 2 fermo')
        for relay in {c['relay'] for c in m['rooms'].values() if c.get('relay')}:
            associated=[v for v in plan['rooms'].values() if v['relay']==relay]
            desired=any(v['desired'] for v in associated) and climate and s['season']=='winter'
            # Minimum on-time is subordinate to reaching setpoint, invalid sensor, and MASTER OFF.
            if not desired and self.state(relay)=='on' and climate and self.state(t['pump2'])=='on':
                # A physically verified bypass/minimum circuit is required by commissioning.
                if not self.attest().get('safe_closure_with_pump'):raise ValueError('Chiusura rinviata: circuito idraulico minimo non attestato')
            await self.physical('switch','turn_on' if desired else 'turn_off',{'entity_id':relay},relay,'on' if desired else 'off')
        if climate:await self.mqtt_set('SetOperationMode',desired_code,t['mode'],desired_mode)
        if climate and selected is not None:
            await self.mqtt_set('SetHeatpump',1,POWER_ENTITY,'on')
        # Check actual circulation only while the zone pump is running and actuators had time to open.
        # This is the measured primary flow; the designer must verify it represents the minimum path.
        # The HeishaMon meter belongs to the primary heat-pump circuit. It cannot
        # serve as a measured secondary minimum-flow interlock behind a buffer.
        if climate and s['season']=='winter' and self.state(t['pump2'])=='on' and not confirmed_buffer_path(self.attest()):
            active=[r for r in {c.get('relay') for c in m['rooms'].values()} if r and self.state(r)=='on']
            for relay in active:self.open_since.setdefault(relay,time.time())
            mature=any(time.time()-self.open_since[r]>s['params']['opening_seconds']+60 for r in active)
            if mature:
                try:flow=float(self.state(t['flow']))
                except (ValueError,TypeError):flow=None
                minimum=self.attest()['minimum_flow_lpm']
                if flow is None or flow<minimum:
                    self.low_flow_since=self.low_flow_since or time.time()
                    if time.time()-self.low_flow_since>=30:raise ValueError('Portata minima non confermata: richiesta climatica sospesa')
                else:self.low_flow_since=None
        else:self.low_flow_since=None

    async def safe_stop(self,reason):
        """At most one DHW-only attempt per minute; never close a possibly circulating path."""
        if time.time()-self.last_safety_attempt<60:return
        self.last_safety_attempt=time.time();t=self.mapping['telemetry']
        if self.state(t['defrost'])!='off' or self.state(t['sterilization'])!='off':return
        try:
            await self.physical('mqtt','publish',{'topic':self.mapping['mqtt_prefix']+'commands/SetOperationMode','payload':'3','retain':False,'qos':0},t['mode'],'DHW only',emergency=True)
            self.log('safety','Climatizzazione sospesa, ACS conservata; circuiti lasciati disponibili',reason=reason)
        except Exception as e:LOG.error('Safe stop unconfirmed: %s',str(e))

    async def tick(self):
        async with self.lock:
            if not self.connected:return
            if not self.restored:self.restore_engine_state()
            active={k:v for k,v in self.manual_timers.items() if k in self.mapping['rooms'] and isinstance(v,(int,float)) and not isinstance(v,bool) and time.time()<v<=time.time()+7200}
            if active!=self.manual_timers:
                self.manual_timers=active
                await asyncio.to_thread(atomic,DATA/'manual-state.json',{'timers':active,'receipts':self.manual_receipts})
            b=self.blockers()
            if b and self.attest().get('control_enabled') and confirmed_buffer_path(self.attest()):
                for relay in {c.get('relay') for c in self.mapping['rooms'].values()}:
                    if relay and self.state(relay)=='on' and relay not in self.failed_off:
                        try:await self.physical('switch','turn_off',{'entity_id':relay},relay,'off')
                        except Exception as e:LOG.error('Secondary safe closure not confirmed: %s',str(e))
            plan=self.engine.evaluate(self.settings,self.mapping['rooms'],self.readings(),datetime.now(UTC),allowed=not b and self.settings.get('pdc_mode')!='off',manual=self.manual_timers)
            error=None
            try:await self.reconcile(plan)
            except Exception as e:
                error=str(e);LOG.error('Reconciliation: %s',error)
                # Configuration/flow errors latch; normal pump run-down is retried on the next tick.
                if 'Attesa arresto' not in error and 'in attesa del circolatore' not in error and 'Antidistacco' not in error:
                    self.command_fault=self.command_fault or error
                    await self.safe_stop(error)
            b=self.blockers()
            if error:b.append(error)
            for k,x in plan['rooms'].items():
                actual=self.state(x['relay']) if x['relay'] else None;x['relay_state']=actual
                if actual=='on':
                    self.open_since.setdefault(x['relay'],time.time())
                    x['valve']='Apertura presunta' if time.time()-self.open_since[x['relay']]>=self.settings['params']['opening_seconds'] else 'Apertura in corso'
                    if not b and x['desired'] and time.time()-self.open_since[x['relay']]>=self.settings['params']['opening_seconds'] and self.state(self.mapping['telemetry']['pump2'])=='on':x['state']='Riscaldamento'
                else:self.open_since.pop(x['relay'],None);x['valve']='Relè OFF' if actual=='off' else 'N/D'
                x['opening_confirmed']=False
            sig=json.dumps({k:[v['desired'],v['reason']] for k,v in plan['rooms'].items()},sort_keys=True)
            if sig!=self.last_signature:self.log('decision','Piano di regolazione aggiornato',rooms={k:{'desired':v['desired'],'reason':v['reason']} for k,v in plan['rooms'].items()});self.last_signature=sig
            armed=self.attest().get('control_enabled',False)
            invalid=any(not x['valid'] for k,x in plan['rooms'].items() if self.mapping['rooms'][k].get('sensor'))
            if armed and invalid:b.append('Uno o più sensori ambiente non validi; circuito interessato escluso')
            status='ERRORE' if self.command_fault or error or armed and (b or invalid) else 'OPERATIVO' if not b and self.attest().get('physical_tests_passed') else 'NON ATTIVO'
            now=datetime.now(UTC).isoformat()
            public_mapping={k:v for k,v in self.mapping.items() if k!='admin_user_ids'}
            snapshot={'friendly_name':'Coordinatore climatico','updated_at':now,'started_at':datetime.fromtimestamp(self.started,UTC).isoformat(),'settings':self.settings,'plan':plan,'blockers':b,'ack':self.ack,'physical_commands':self.actual_commands,
                      'relays_on':sum(self.state(r)=='on' for r in {x['relay'] for x in self.mapping['rooms'].values() if x.get('relay')}),
                      'manual_timers':self.manual_timers,'manual_timer_supported':True,
                      'mode_control':capability(self.settings,self.attest(),self.states,self.mapping['telemetry'],self.blockers()),
                      'consent':None,'consent_note':'Contatto di consenso non telemisurato',
                      'flow_note':'Portata primaria Panasonic disponibile; portata pavimento non telemisurata. Ricircolo buffer verificato.' if confirmed_buffer_path(self.attest()) else 'Percorso e portata del pavimento da verificare',
                      'secondary_flow_lpm':None,'decisions':self.decisions[-8:],'mapping':public_mapping,'commissioned':not b,'tests_passed':bool(self.attest().get('physical_tests_passed'))}
            # Avoid recording an entire diagnostic snapshot every ten seconds.
            semantic={'status':status,'settings':self.settings,'manual_timers':self.manual_timers,'blockers':b,'ack':self.ack,'floors':plan['floors'],'mode_control':{k:v for k,v in snapshot['mode_control'].items() if k!='at'},
                      'rooms':{k:{field:v.get(field) for field in ['temperature','setpoint','desired','state','reason','relay_state','valve']} for k,v in plan['rooms'].items()}}
            encoded=json.dumps(semantic,sort_keys=True)
            if encoded!=self.last_published:
                await self.api('POST','states/'+STATUS,{'state':status,'attributes':snapshot});self.last_published=encoded
            await self.api('POST','events/coordinatore_climatico_status',{'status':status,'snapshot':snapshot})
            await asyncio.to_thread(atomic,DATA/'heartbeat.json',{'at':time.time(),'status':status,'physical_commands':self.actual_commands})
            await asyncio.to_thread(atomic,DATA/'engine-state.json',{'at':time.time(),'settings':self.settings,'memory':self.engine.memory,'mean_memory':self.engine.mean_memory,'relays':{c['relay']:self.state(c['relay']) for c in self.mapping['rooms'].values() if c.get('relay')},'open_since':self.open_since})

    async def timer_command(self,request,payload):
        async with self.lock:
            try:
                if set(payload)!={'room','minutes'} or not request:raise ValueError('Richiesta timer non valida')
                key=payload['room'];minutes=payload['minutes'];room=self.mapping['rooms'].get(key,{})
                if not room or room.get('bath') or not room.get('confirmed') or not room.get('relay'):raise ValueError('Stanza non comandabile separatamente')
                if type(minutes) is not int or minutes not in (0,15,30,45,60,90,120):raise ValueError('Durata timer non valida')
                if request in self.manual_receipts:
                    if self.manual_receipts[request]['payload']!=payload:raise ValueError('Identificativo già usato per altro comando')
                    self.ack=copy.deepcopy(self.manual_receipts[request]['ack'])
                else:
                    if minutes:
                        blocked=self.blockers()
                        if not self.connected or blocked:raise ValueError('; '.join(blocked) or 'Motore non collegato')
                        s=self.settings
                        if not s['master'] or s['season']!='winter' or s.get('pdc_mode')=='off' or not s['floors'][room['floor']]['enabled']:raise ValueError('Abilita MASTER, Inverno e Auto del piano prima del timer')
                        check=Engine().evaluate(s,self.mapping['rooms'],self.readings(),datetime.now(UTC))
                        if not check['rooms'][key]['valid'] or s['floors'][room['floor']].get('mean_enabled') and not check['floors'][room['floor']].get('mean_valid'):raise ValueError('Sensore previsto non valido: timer non avviato')
                    timers=dict(self.manual_timers)
                    if minutes:timers[key]=time.time()+minutes*60
                    else:timers.pop(key,None)
                    ack={'request_id':request,'ok':True,'at':datetime.now(UTC).isoformat()}
                    receipts={**self.manual_receipts,request:{'payload':copy.deepcopy(payload),'ack':ack}}
                    receipts=dict(list(receipts.items())[-64:])
                    await asyncio.to_thread(atomic,DATA/'manual-state.json',{'timers':timers,'receipts':receipts})
                    self.manual_timers=timers;self.manual_receipts=receipts;self.ack=ack
                    self.log('timer','Timer stanza aggiornato',room=key,minutes=minutes)
            except Exception as e:
                self.ack={'request_id':request,'ok':False,'error':str(e),'at':datetime.now(UTC).isoformat()};self.log('rejected',str(e),command='room_timer')
        await self.tick()

    async def mode_command(self,user,request,payload):
        # This handler is inside the existing single worker and uses its lock and physical gate.
        result={'request_id':request,'ok':False,'at':datetime.now(UTC).isoformat()}
        if request in self.mode_receipts:
            result.update(error='Richiesta già consumata; nessun reinvio automatico')
        else:
            intent_saved=False
            try:
                async with self.lock:
                    if set(payload)!={'mode','expected','expires'}:raise ValueError('Richiesta modalità non valida')
                    if not request or type(payload['expires']) not in (int,float) or not 0<payload['expires']-time.time()<=90:raise ValueError('Conferma modalità scaduta')
                    selected=payload['mode'];validate_mode(selected,self.settings,self.attest(),self.states)
                    blocked=self.blockers()
                    if blocked:raise ValueError('; '.join(blocked))
                    t=self.mapping['telemetry']
                    if self.state(t['defrost'])!='off' or self.state(t['sterilization'])!='off' or self.state(t['load_shed'])!='off':raise ValueError('Cambio sospeso: sbrinamento, sterilizzazione o antidistacco')
                    if payload['expected']!=fingerprint(self.settings,self.states,t,self.attest()):raise ValueError('Stato o configurazione cambiati dopo l’anteprima')
                    self.mode_receipts[request]={'state':'intent','mode':selected,'at':time.time()}
                    # Intent persisted before any physical action; a replay is never executed.
                    await asyncio.to_thread(atomic,DATA/'mode-receipts.json',self.mode_receipts)
                    intent_saved=True;self.mode_transition_active=True
                    s=copy.deepcopy(self.settings);s['pdc_mode']=selected;s['pdc_mode_transition']='pending'
                    await asyncio.to_thread(atomic,DATA/'settings.json',s);self.settings=s
                await self.tick()
                async with self.lock:
                    expected_mode=MODES[selected][2]
                    confirmed=self.state(POWER_ENTITY)==('off' if selected=='off' else 'on') and (selected=='off' or self.state(t['mode'])==expected_mode)
                    if not confirmed or self.command_fault:raise ValueError('Modalità non verificata dalla Panasonic; nessun reinvio automatico')
                    self.settings['pdc_mode_transition']='verified'
                    await asyncio.to_thread(atomic,DATA/'settings.json',self.settings)
                    self.mode_receipts[request]={'state':'verified','mode':selected,'at':time.time()}
                    await asyncio.to_thread(atomic,DATA/'mode-receipts.json',self.mode_receipts)
                    result.update(ok=True,state='verified',mode=selected)
            except Exception as e:
                result['error']=str(e)
                if intent_saved:
                    self.command_fault=self.command_fault or 'Cambio modalità incerto: nessun reinvio automatico'
                    self.settings['pdc_mode_transition']='uncertain'
                    await asyncio.to_thread(atomic,DATA/'settings.json',self.settings)
            finally:self.mode_transition_active=False
        self.ack=result
        self.log('mode_result','Cambio modalità verificato' if result['ok'] else result.get('error','Rifiutato'),request_id=request)
        await self.api('POST','events/aquarea_clima_mode_ack',result)

    async def command(self,event):
        ctx=event.get('context',{});user=ctx.get('user_id')
        # Admin IDs come from the installation audit, never from client event_data.
        if user not in self.mapping.get('admin_user_ids',[]):
            self.log('rejected','Utente non autorizzato');return
        data=event.get('data',{});request=str(data.get('request_id',''))[:80]
        cmd=data.get('command');p=data.get('payload',{})
        if cmd=='room_timer':
            await self.timer_command(request,p);return
        if cmd=='pdc_mode':
            await self.mode_command(user,request,p);return
        async with self.lock:
            try:
                s=copy.deepcopy(self.settings)
                if cmd in ['master','schedule_enabled']:
                    if type(p.get('value')) is not bool:raise ValueError('Valore booleano richiesto')
                    s[cmd]=p['value']
                elif cmd=='season':
                    if p['value']=='summer' and self.attest().get('control_enabled') and not self.attest().get('fan_coils_verified'):
                        raise ValueError('Estate non ancora collaudata: la modalità attuale viene mantenuta')
                    s['season']=p['value']
                    s.pop('pdc_mode',None);s.pop('pdc_mode_transition',None)
                elif cmd=='temperature':
                    if p['key'] not in ['comfort','eco']:raise ValueError('Temperatura non valida')
                    s[p['key']]=number(p['value'],16,26)
                elif cmd=='floor':
                    if p['floor'] not in s['floors'] or p['key'] not in ['enabled','mode','setpoint']:raise ValueError('Piano non valido')
                    s['floors'][p['floor']][p['key']]=p['value']
                    if p['key']=='setpoint':s['floors'][p['floor']]['mode']='manual'
                elif cmd=='mean_floor':
                    if p.get('floor') not in s['floors'] or type(p.get('enabled')) is not bool:raise ValueError('Modalità media non valida')
                    f=s['floors'][p['floor']]
                    f['mean_enabled']=p['enabled'];f['mean_percent']=number(p.get('percent'),0.25,5)
                    if 'target' in p:
                        f['setpoint']=number(p['target'],16,26);f['mode']='manual'
                elif cmd=='floor_adjust':
                    if p['floor'] not in s['floors'] or p.get('delta') not in [-0.5,0.5]:raise ValueError('Regolazione piano non valida')
                    f=s['floors'][p['floor']];f['setpoint']=number(f['setpoint']+p['delta'],16,26);f['mode']='manual'
                elif cmd=='parameter':
                    if p['key'] not in PARAMETER_KEYS:raise ValueError('Parametro non modificabile')
                    s['params'][p['key']]=p['value']
                elif cmd=='day':
                    day=p['day']
                    if type(day) is not int or not 0<=day<=6:raise ValueError('Giorno non valido')
                    s['week'][day]=p['slots']
                elif cmd=='slot':
                    day=p.get('day');slot=p.get('slot')
                    if type(day) is not int or not 0<=day<=6 or type(slot) is not int or not 0<=slot<48:raise ValueError('Cavaliere non valido')
                    s['week'][day][slot]=1-s['week'][day][slot]
                elif cmd=='copy_day':
                    source=p['source'];targets=p['targets']
                    if type(source) is not int or not 0<=source<=6 or not isinstance(targets,list) or len(targets)>7 or any(type(x) is not int or not 0<=x<=6 for x in targets):raise ValueError('Giorni non validi')
                    for target in targets:s['week'][target]=list(s['week'][source])
                elif cmd=='dhw':
                    if p.get('profile') not in ['eco','plus']:raise ValueError('Profilo ACS non valido')
                    value=40 if p['profile']=='eco' else 55;entity=self.mapping['telemetry']['dhw_target'];attrs=self.states.get(entity,{}).get('attributes',{})
                    if not attrs.get('min',999)<=value<=attrs.get('max',-999):raise ValueError('Limiti ACS non confermati')
                    if not self.fresh(self.mapping['telemetry']['heartbeat']):raise ValueError('Telemetria non recente')
                    await self.mqtt_set('SetDHWTemp',value,entity,str(value))
                else:raise ValueError('Comando non riconosciuto')
                validate(s)
                if s!=self.settings:
                    await asyncio.to_thread(atomic,DATA/'settings.json',s);self.settings=s
                self.ack={'request_id':request,'ok':True,'at':datetime.now(UTC).isoformat()}
                self.log('setting','Configurazione salvata',command=cmd)
            except Exception as e:self.ack={'request_id':request,'ok':False,'error':str(e),'at':datetime.now(UTC).isoformat()};self.log('rejected',str(e),command=cmd)
        await self.tick()

    async def listen(self):
        while not self.stop:
            try:
                self.connected=False
                states=await self.api('GET','states');self.states={s['entity_id']:s for s in states}
                async with self.http.ws_connect(HA+'/api/websocket',heartbeat=30) as ws:
                    self.ws=ws;await ws.receive_json();await ws.send_json({'type':'auth','access_token':TOKEN})
                    if (await ws.receive_json()).get('type')!='auth_ok':raise RuntimeError('HA authentication failed')
                    await ws.send_json({'id':1,'type':'subscribe_events','event_type':'state_changed'})
                    await ws.send_json({'id':2,'type':'subscribe_events','event_type':EVENT})
                    subscriptions=set()
                    async for msg in ws:
                        if msg.type!=aiohttp.WSMsgType.TEXT:break
                        z=msg.json()
                        if z.get('type')=='result':
                            if not z.get('success'):raise RuntimeError('HA event subscription failed')
                            subscriptions.add(z['id']);self.connected=len(subscriptions)==2
                        elif z.get('type')=='event':
                            event=z['event']
                            if event['event_type']=='state_changed':
                                new=event['data'].get('new_state');entity=event['data']['entity_id']
                                if new:self.states[entity]=new
                                else:self.states.pop(entity,None)
                            elif event['event_type']==EVENT:
                                # Queue is bounded: dashboard input cannot create unbounded tasks.
                                try:self.queue.put_nowait(event)
                                except asyncio.QueueFull:self.log('rejected','Coda comandi piena')
            except Exception as e:LOG.error('HA connection: %s',type(e).__name__)
            self.connected=False;await asyncio.sleep(5)

    async def worker(self):
        while True:
            event=await self.queue.get()
            try:await self.command(event)
            except Exception:LOG.exception('Command failed')
            finally:self.queue.task_done()

    async def clock(self):
        while True:
            try:
                if self.connected and time.time()-self.last_refresh>=60:
                    states=await self.api('GET','states');self.states={s['entity_id']:s for s in states};self.last_refresh=time.time()
                await self.tick()
            except Exception:LOG.exception('Tick failed')
            await asyncio.sleep(10)

    async def run(self):
        self.queue=asyncio.Queue(32)
        async with aiohttp.ClientSession(headers={'Authorization':'Bearer '+TOKEN}) as self.http:
            await asyncio.gather(self.listen(),self.worker(),self.clock())

PARAMETER_KEYS={'spread','wait_minutes','urgent','ordinary'}
if __name__=='__main__':
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    asyncio.run(Coordinator().run())
