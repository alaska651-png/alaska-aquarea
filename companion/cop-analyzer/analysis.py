"""Pure monitoring calculations. kW * actual seconds / 3600; never average COPs."""
import math,time,copy,json
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
ROME=ZoneInfo('Europe/Rome')
SERVICES=('heat','dhw','cool')
ALIASES={'freq':'Compressor_Freq','counter':'Operations_Counter','hours':'Operations_Hours','flow':'Main_Outlet_Temp','return':'Main_Inlet_Temp','out':'Outside_Temp','buffer':'Buffer_Temp','tank':'DHW_Temp','target':'Main_Target_Temp','z2':'Z2_Water_Temp','z2_target':'Z2_Water_Target_Temp','lpm':'Pump_Flow','pump_speed':'Pump_Speed','defrost':'Defrosting_State','threeway':'ThreeWay_Valve_State','mode':'Operating_Mode_State','power_state':'Heatpump_State'}
LEGACY=['time','minutes','heatQ','heatE','dhwQ','dhwE','flow','return','out','buffer','tank']

def num(v):
    if isinstance(v,bool) or v in (None,'','unknown','unavailable'):return None
    try:
        n=float(v);return n if math.isfinite(n) else None
    except (ValueError,TypeError):return None

def value(r):return num(r.get('value')) if r and r.get('quality')=='fresh' else None

def watts(r):
    v=value(r)
    if v is None or v<0:return None
    if r.get('unit')=='W':v/=1000
    elif r.get('unit')!='kW':return None
    return v if v<=80 else None

def normalize(raw,now,live=None,extra_seen=None):
    d=copy.deepcopy(raw)
    def ages(obj):
        if isinstance(obj,dict):
            if 'value' in obj and 'quality' in obj:
                at=obj.get('measured_at') or obj.get('received_at');obj['age_s']=max(0,now-at) if at else None
            for child in obj.values():ages(child)
        elif isinstance(obj,list):
            for child in obj:ages(child)
    ages(d);m=d.get('metrics',{});live=live or {};extra_seen=extra_seen or set()
    online=d.get('sources',{}).get('heishamon',{}).get('fresh',False)
    for key,r in m.items():
        at=r.get('measured_at') or r.get('received_at');r['age_s']=max(0,now-at) if at else None
        if r.get('quality')=='fresh' and (r['age_s'] is None or r['age_s']>45 or at>now+5):r['quality']='stale'
    if value(m.get('Low_Pressure'))==0:m['Low_Pressure'].update(quality='unavailable',raw_value=0,value=None)
    row={'t':now,'metrics':m,'rooms':d.get('rooms',{}),'pumps':d.get('pumps',{}),'zones':d.get('zones',{}),'operating':d.get('operating',{}),'power':{},'sources':d.get('sources',{})}
    for key,source in ALIASES.items():row[key]=value(m.get(source))
    row['freq_at']=m.get('Compressor_Freq',{}).get('received_at')
    demands=[r.get('demand',{}) for r in row['rooms'].values()]
    row['demand']=True if any(r.get('quality')=='fresh' and r.get('value') is True for r in demands) else False if demands and all(r.get('quality')=='fresh' and isinstance(r.get('value'),bool) for r in demands) else None
    row['season']=row['zones'].get('season') if row['zones'].get('season_quality')=='fresh' else None
    row['water_delta']=row['flow']-row['return'] if row['flow'] is not None and row['return'] is not None else None
    row['flow_error']=row['flow']-row['target'] if row['flow'] is not None and row['target'] is not None else None
    for service in SERVICES:
        pair={}
        for direction,letter in [('production','q'),('consumption','e')]:
            prefix={'heat':'Heat','dhw':'DHW','cool':'Cool'}[service];key=prefix+'_Energy_'+direction.capitalize();name=prefix+'_Power_'+direction.capitalize()
            r=copy.deepcopy(m.get(key,{}))
            # Extra block takes precedence even if its current value is invalid: no silent standard fallback.
            if extra_seen:
                candidate=live.get('extra/'+name+'_Extra') or live.get('extra/'+key) or live.get('extra/'+name)
                r={'value':candidate['value'] if candidate else None,'unit':'W','source':'mqtt/extra','received_at':candidate['at'] if candidate else None,'quality':'fresh' if candidate and not candidate['retained'] and online and 0<=now-candidate['at']<=45 else 'unavailable'}
            pair[letter]=watts(r);pair[letter+'_at']=r.get('measured_at') or r.get('received_at');pair[letter+'_source']=r.get('source');pair[letter+'_quality']=r.get('quality','unavailable');pair[letter+'_origin']=('panasonic_extra:'+name if r.get('source')=='mqtt/extra' else 'panasonic_standard:'+key if r.get('source') in ('mqtt','heishamon_http','mqtt_retained_or_old') else r.get('source'))
        pair['valid']=pair['q'] is not None and pair['e'] is not None and pair['q_at'] is not None and pair['e_at'] is not None and abs(pair['q_at']-pair['e_at'])<=15
        row['power'][service]=pair
    row['class']='unknown' if row['freq'] is None or row['freq']>0 and any(row[k] is None for k in ('defrost','mode','threeway')) else 'defrost' if row['defrost']==1 else 'off' if row['freq']==0 else 'dhw' if row['threeway']==1 else 'cool' if row['mode'] in (1,5) else 'heat' if row['mode'] in (0,4) else 'transient'
    p=row['power'].get(row['class'],{})
    row['cop_instant']=p['q']/p['e'] if p.get('valid') and p['e']>=0.05 and row['freq'] and row['class'] in ('heat','dhw','cool') else None
    row['external_cop']=None # No independently commissioned heat meter/electrical meter pair available.
    return row

def interval(previous,row):
    dt=row['t']-previous['t'] if previous else 0
    result={'start':previous['t'] if previous else row['t'],'end':row['t'],'seconds':dt if 0<dt<=45 else 0,'energy':{}}
    for service in SERVICES:
        a=previous['power'][service] if previous else {};b=row['power'][service]
        valid=0<dt<=45 and a.get('valid') and b.get('valid')
        # A source change is not a continuous calibrated measurement interval.
        valid=valid and all(a.get(k+'_origin',a.get(k+'_source'))==b.get(k+'_origin',b.get(k+'_source')) for k in ('q','e'))
        result['energy'][service]={'q':(a['q']+b['q'])/2*dt/3600 if valid else 0,'e':(a['e']+b['e'])/2*dt/3600 if valid else 0,'seconds':dt if valid else 0}
    return result

def blank():return {'q':{s:0.0 for s in SERVICES},'e':{s:0.0 for s in SERVICES},'seconds':{s:0.0 for s in SERVICES},'weighted':{},'weights':{},'n':0,'running_seconds':0.0,'freq_min':None,'freq_max':None}

def add(acc,row,fraction=1):
    iv=row['interval'];dt=iv['seconds']*fraction;acc['n']+=1
    for service,v in iv['energy'].items():
        acc['q'][service]+=v['q']*fraction;acc['e'][service]+=v['e']*fraction;acc['seconds'][service]+=v['seconds']*fraction
    for k in (*ALIASES,'water_delta','flow_error','demand'):
        v=row.get(k)
        if isinstance(v,(int,float)) and dt>0:
            acc['weighted'][k]=acc['weighted'].get(k,0)+v*dt;acc['weights'][k]=acc['weights'].get(k,0)+dt
    if row.get('freq') is not None and row['freq']>0:
        acc['running_seconds']+=dt
        acc['freq_min']=min(acc['freq_min'],row['freq']) if acc['freq_min'] is not None else row['freq'];acc['freq_max']=max(acc['freq_max'],row['freq']) if acc['freq_max'] is not None else row['freq']

def merge(a,b,fraction=1):
    for k in ('q','e','seconds','weighted','weights'):
        for key,v in b[k].items():a[k][key]=a[k].get(key,0)+v*fraction
    a['n']+=b['n'];a['running_seconds']+=b.get('running_seconds',0)*fraction
    for key,fn in [('freq_min',min),('freq_max',max)]:
        if b[key] is not None:a[key]=fn(a[key],b[key]) if a[key] is not None else b[key]

def finish(a,expected):
    means={k:v/a['weights'][k] for k,v in a['weighted'].items() if a['weights'].get(k,0)>0}
    result={**a,'means':means,'coverage':{s:min(1,a['seconds'][s]/expected) if expected>0 else 0 for s in SERVICES},'cop':{s:a['q'][s]/a['e'][s] if a['e'][s]>0.0001 and a['seconds'][s]>0 and a['e'][s]*3600/a['seconds'][s]>=0.05 else None for s in SERVICES}}
    result['total_cop']=(a['q']['heat']+a['q']['dhw'])/(a['e']['heat']+a['e']['dhw']) if a['e']['heat']+a['e']['dhw']>0.0001 and max(a['seconds']['heat'],a['seconds']['dhw'])>0 and (a['e']['heat']+a['e']['dhw'])*3600/max(a['seconds']['heat'],a['seconds']['dhw'])>=0.05 else None
    return result

def stop_cause(last,current):
    if last.get('demand') is True and current.get('demand') is False:return {'confidence':'probabile','label':'Cessazione richiesta ambienti osservata; causa interna Panasonic non telemisurata'}
    if current.get('defrost')==1:return {'confidence':'probabile','label':'Arresto coincidente con sbrinamento'}
    if last.get('flow') is not None and last.get('target') is not None and last['flow']>=last['target']:return {'confidence':'probabile','label':'Target acqua raggiunto; nessuna prova del motivo interno di arresto'}
    return {'confidence':'non determinabile','label':'Motivo di arresto Panasonic non esposto dalla telemetria'}
