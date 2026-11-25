"""Deterministic heating decisions, without networking or actuator side effects."""
from __future__ import annotations
import copy, math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ZONE=ZoneInfo('Europe/Rome')
PARAMS={'spread':1.0,'wait_minutes':20,'urgent':0.8,'ordinary':0.4,
        'hysteresis':0.15,'min_on_seconds':300,'min_off_seconds':180,
        'sensor_max_age_seconds':7200,'opening_seconds':180}

def defaults():
    return {'master':False,'season':'winter','schedule_enabled':True,'comfort':20.0,'eco':18.0,
            'week':[[int(12<=n<31 or 36<=n<45) for n in range(48)] for _ in range(7)],
            'floors':{k:{'enabled':True,'mode':'chrono','setpoint':20.0,'mean_enabled':False,'mean_percent':1.0} for k in ['night','day','basement']},
            'params':copy.deepcopy(PARAMS)}

def number(value,lo,hi):
    if isinstance(value,bool):raise ValueError('Valore numerico richiesto')
    n=float(value)
    if not math.isfinite(n) or not lo<=n<=hi:raise ValueError(f'Valore fuori intervallo {lo}–{hi}')
    return n

def validate(s):
    if type(s['master']) is not bool or type(s['schedule_enabled']) is not bool:raise ValueError('Interruttore non valido')
    if s['season'] not in ['winter','summer','off']:raise ValueError('Stagione non valida')
    s['comfort']=number(s['comfort'],16,26);s['eco']=number(s['eco'],16,26)
    if s['eco']>s['comfort']:raise ValueError('Eco deve essere minore o uguale a Comfort')
    if len(s['week'])!=7 or any(len(d)!=48 or any(type(x) is not int or x not in [0,1] for x in d) for d in s['week']):raise ValueError('Servono 7 giorni con 48 cavalieri')
    for floor in ['night','day','basement']:
        f=s['floors'][floor]
        if type(f['enabled']) is not bool or f['mode'] not in ['chrono','manual']:raise ValueError('Piano non valido')
        f['setpoint']=number(f['setpoint'],16,26)
        f.setdefault('mean_enabled',False)
        f.setdefault('mean_percent',1.0)
        if type(f['mean_enabled']) is not bool:raise ValueError('Abilitazione media non valida')
        f['mean_percent']=number(f['mean_percent'],0.25,5)
    p=s['params']
    for k,lo,hi in [('spread',0.2,3),('wait_minutes',0,60),('urgent',0.2,2),('ordinary',0.1,1),('hysteresis',0.05,0.4),('min_on_seconds',180,1800),('min_off_seconds',180,1800),('sensor_max_age_seconds',300,14400),('opening_seconds',120,600)]:p[k]=number(p[k],lo,hi)
    if p['ordinary']>p['urgent'] or p['hysteresis']>=p['ordinary']:raise ValueError('Richiesti isteresi < soglia ordinaria ≤ deficit urgente')
    return s

def schedule(s,now):
    local=now.astimezone(ZONE);idx=local.hour*2+local.minute//30
    profile=s['week'][local.weekday()][idx]
    # Walk actual UTC half-hours: correct also on both DST transitions.
    boundary=now.replace(minute=(now.minute//30)*30,second=0,microsecond=0)
    next_change=None
    for n in range(1,7*50+1):
        point=boundary+timedelta(minutes=30*n);z=point.astimezone(ZONE)
        value=s['week'][z.weekday()][z.hour*2+z.minute//30]
        if value!=profile:
            next_change={'at':point.isoformat(),'local':z.isoformat(),'profile':'comfort' if value else 'eco','temperature':s['comfort'] if value else s['eco']};break
    return {'profile':'comfort' if profile else 'eco','temperature':s['comfort'] if profile else s['eco'],'next':next_change,'weekday':local.weekday(),'slot':idx}

def effective(s,floor,now):
    f=s['floors'][floor]
    return schedule(s,now)['temperature'] if s['schedule_enabled'] and f['mode']=='chrono' else f['setpoint']

class Engine:
    def __init__(self):self.memory={};self.mean_memory={}

    def mean_floor(self,s,f,keys,rooms,out,stamp,allowed):
        """One latch for all physical circuits; each distinct sensor counted once."""
        cfg=s['floors'][f];p=s['params'];target=out[keys[0]]['setpoint']
        sensor_keys=list(dict((rooms[k]['sensor'],k) for k in keys if rooms[k].get('sensor')).values())
        valid=bool(sensor_keys) and all(out[k]['valid'] for k in sensor_keys) and all(out[k]['mapped'] for k in keys)
        value=sum(out[k]['temperature'] for k in sensor_keys)/len(sensor_keys) if valid else None
        band=target*cfg['mean_percent']/100;lower=target-band;upper=target+band
        m=self.mean_memory.setdefault(f,{'on':False,'off_since':max(self.memory[k]['off_since'] for k in keys),'on_since':None})
        enabled=allowed and s['master'] and s['season']=='winter' and cfg['enabled']
        desired=m['on'];waiting=False
        if not enabled or not valid:desired=False
        elif value>=upper-1e-9:desired=False
        elif not m['on'] and value<=lower+1e-9:
            # The whole group waits for the most recently closed circuit.
            off_since=max(m['off_since'],*(self.memory[k]['off_since'] for k in keys))
            waiting=stamp-off_since<p['min_off_seconds'];desired=not waiting
        reason='Media piano: sensore o abbinamento non valido' if not valid else 'Media piano: controllo disabilitato' if not enabled else 'Media piano: pausa minima' if waiting else 'Media piano: soglia di apertura' if desired and not m['on'] else 'Media piano: mantenimento gruppo aperto' if desired else 'Media piano: target soddisfatto / gruppo chiuso'
        if desired and not m['on']:m['on_since']=stamp
        if not desired and m['on']:m['off_since']=stamp;m['on_since']=None
        m['on']=desired
        for k in keys:
            x=out[k];room_memory=self.memory[k]
            if desired and room_memory['on_since'] is None:room_memory['on_since']=stamp
            elif not desired and room_memory['on_since'] is not None:room_memory['off_since']=stamp;room_memory['on_since']=None
            x.update(desired=desired,request=desired,mean_linked=True,reason=reason,state='Anomalia' if not valid else 'Richiesta' if desired else 'Attesa' if waiting else 'Regolare',request_seconds=stamp-room_memory['on_since'] if desired else 0)
        return {'mean_enabled':True,'mean_valid':valid,'mean_temperature':value,'target':target,'mean_percent':cfg['mean_percent'],'mean_progress':value/target*100 if value is not None else None,'mean_lower':lower,'mean_upper':upper,'mean_sensor_count':len(sensor_keys),'mean_waiting':waiting,'active':desired,'spread':round(max(out[k]['temperature'] for k in sensor_keys)-min(out[k]['temperature'] for k in sensor_keys),2) if valid and len(sensor_keys)>1 else None}

    def evaluate(self,s,rooms,readings,now,allowed=True,manual=None):
        """Maintain waiting/latch memory; never claim a commanded valve is confirmed open."""
        stamp=now.timestamp();p=s['params'];out={};groups={}
        timers={k:until for k,until in (manual or {}).items() if k in rooms and not rooms[k].get('bath') and isinstance(until,(int,float)) and not isinstance(until,bool) and math.isfinite(until) and stamp<until<=stamp+7200}
        prior=copy.deepcopy(self.memory) if timers else {}
        for key,cfg in rooms.items():
            f=cfg['floor'];m=self.memory.setdefault(key,{'waiting_since':None,'on_since':None,'off_since':stamp,'filtered':None,'sample_at':None,'last_raw':None})
            raw=readings.get(key,{});value=raw.get('temperature');age=raw.get('age',float('inf'))
            valid=isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) and 5<=value<=40 and 0<=age<=p['sensor_max_age_seconds'] and raw.get('unit')=='°C'
            # Filter each distinct report exactly once, not once per coordinator tick.
            if valid and (raw.get('sample_at')!=m['sample_at'] or value!=m['last_raw']):
                m['filtered']=value if m['filtered'] is None else 0.5*value+0.5*m['filtered'];m['sample_at']=raw.get('sample_at');m['last_raw']=value
            target=effective(s,f,now);deficit=target-m['filtered'] if valid else None
            enabled=allowed and s['master'] and s['season']=='winter' and s['floors'][f]['enabled'] and bool(cfg.get('relay')) and cfg.get('confirmed',False)
            request=enabled and valid and not cfg.get('bath') and value<target and deficit>=p['ordinary']
            if request:
                if m['waiting_since'] is None:m['waiting_since']=stamp
            else:m['waiting_since']=None
            on=m['on_since'] is not None
            # Raw setpoint is a hard upper bound: filtering may never cause overheating.
            keep=enabled and valid and not cfg.get('bath') and on and value<target and (deficit>p['hysteresis'] or stamp-m['on_since']<p['min_on_seconds'])
            out[key]={'name':cfg['name'],'floor':f,'temperature':value if valid else None,'filtered_temperature':m['filtered'] if valid else None,'setpoint':target,'deficit':round(deficit,3) if deficit is not None else None,'valid':valid,'desired':bool(keep),'request':bool(request),'bath':bool(cfg.get('bath')),'relay':cfg.get('relay'),'mapped':bool(cfg.get('relay')) and cfg.get('confirmed',False),'waiting_seconds':max(0,stamp-m['waiting_since']) if m['waiting_since'] is not None else 0,'reason':'Sensore non valido o scaduto' if not valid else 'Setpoint raggiunto','state':'Anomalia' if not valid else 'Regolare'}
            if not cfg.get('bath'):groups.setdefault(f,[]).append(key)
        floors={}
        for f,keys in groups.items():
            if s['floors'][f].get('mean_enabled',False):
                all_keys=[k for k,c in rooms.items() if c['floor']==f]
                floors[f]=self.mean_floor(s,f,all_keys,rooms,out,stamp,allowed)
        running=any(x['desired'] for x in out.values())
        for f,keys in groups.items():
            if s['floors'][f].get('mean_enabled',False):continue
            if f in self.mean_memory:
                # Preserve real on/off timers when returning to individual control.
                self.mean_memory.pop(f)
            vals=[out[k]['temperature'] for k in keys if out[k]['valid']]
            spread=round(max(vals)-min(vals),2) if len(vals)>1 else None
            pending=[k for k in keys if out[k]['request']]
            coldest=min(vals) if vals else None
            for k in keys:
                x=out[k];m=self.memory[k]
                urgent=x['request'] and x['deficit']>=p['urgent']
                priority=x['request'] and spread is not None and spread>p['spread'] and x['temperature']==coldest
                autonomous=f=='basement'
                start=x['request'] and (urgent or priority or autonomous or len(pending)>1 or x['waiting_seconds']>=p['wait_minutes']*60 or running)
                # An operating floor can bring compatible, mildly cold rooms along.
                participate=(any(out[j]['desired'] or out[j]['request'] and (len(pending)>1 or out[j]['deficit']>=p['urgent'] or out[j]['waiting_seconds']>=p['wait_minutes']*60) for j in keys)
                             and x['mapped'] and x['valid'] and x['deficit'] is not None and x['deficit']>p['hysteresis'] and x['temperature']<x['setpoint']
                             and allowed and s['master'] and s['season']=='winter' and s['floors'][f]['enabled'])
                if (start or participate) and (m['on_since'] is not None or stamp-m['off_since']>=p['min_off_seconds']):
                    x['desired']=True
                    x['reason']='Deficit urgente' if urgent else 'Ambiente più freddo' if priority else 'Termostato autonomo' if autonomous else 'Richiesta raggruppata' if len(pending)>1 else 'Attesa massima raggiunta' if x['waiting_seconds']>=p['wait_minutes']*60 else 'Circuito già in funzione'
                if x['desired']:
                    if m['on_since'] is None:m['on_since']=stamp
                    x['state']='Priorità' if urgent or priority else 'Richiesta'
                    x['request_seconds']=stamp-m['on_since']
                else:
                    if m['on_since'] is not None:m['off_since']=stamp
                    m['on_since']=None;x['request_seconds']=0
                    if x['request']:x.update(state='Attesa',reason='Attesa coordinamento / pausa minima')
                    elif not x['mapped']:x.update(state='Anomalia',reason='Abbinamento non confermato')
                    elif not allowed:x['reason']='Comandi fisici non abilitati'
            floors[f]={'spread':spread,'active':any(out[k]['desired'] for k in keys)}
        for k,cfg in rooms.items():
            if cfg.get('bath') and not s['floors'][cfg['floor']].get('mean_enabled',False):
                x=out[k];parent=cfg.get('follows')
                follow=out.get(parent,{}).get('desired',False) if parent else floors.get(cfg['floor'],{}).get('active',False)
                x['desired']=x['mapped'] and (x['valid'] or not cfg.get('sensor')) and follow
                x['state']='Richiesta' if x['desired'] else 'Associato' if x['mapped'] else 'Anomalia';x['reason']='Segue il piano; non genera richiesta' if x['mapped'] else 'Bagno non abbinato / consenso da verificare'
        for key,until in timers.items():
            cfg=rooms[key];floor=cfg['floor'];x=out[key]
            shared=[k for k,c in rooms.items() if c.get('relay')==cfg.get('relay')]
            valid=x['valid'] and all(out[k]['mapped'] for k in shared) and (not s['floors'][floor].get('mean_enabled') or floors[floor].get('mean_valid',False))
            enabled=allowed and s['master'] and s['season']=='winter' and s['floors'][floor]['enabled'] and valid
            # Automatic evaluation must not reset the physical off/on timer each tick
            # when a timed override is heating an already satisfied room.
            for k in shared:
                if k in prior:
                    filtered={f:self.memory[k][f] for f in ('filtered','sample_at','last_raw')}
                    self.memory[k]=copy.deepcopy(prior[k]);self.memory[k].update(filtered)
            m=self.memory[key];waiting=enabled and m['on_since'] is None and stamp-m['off_since']<p['min_off_seconds']
            desired=enabled and not waiting
            reason='Manuale temporaneo: sensore o abbinamento non valido' if not valid else 'Manuale temporaneo sospeso: controllo non abilitato' if not enabled else 'Manuale temporaneo: pausa minima' if waiting else 'Manuale temporaneo: richiesta ON fino alla scadenza'
            for k in shared:
                memory=self.memory[k]
                if desired and memory['on_since'] is None:memory['on_since']=stamp
                if not desired and memory['on_since'] is not None:memory['off_since']=stamp;memory['on_since']=None
                out[k].update(desired=desired,request=desired,manual_until=until,manual_room=key,mean_linked=False,state='Anomalia' if not valid else 'Manuale ON' if desired else 'Attesa',reason=reason,request_seconds=stamp-memory['on_since'] if desired else 0)
        for floor,info in floors.items():info['active']=any(x['desired'] for x in out.values() if x['floor']==floor and not x['bath'])
        return {'rooms':out,'floors':floors,'schedule':schedule(s,now),'active_requests':sum(x['desired'] for x in out.values() if not x['bath'])}
