"""Explicit parameter edits: preview, fresh configuration guard, one write, readback.

No raw packets, automatic tuning, retries, device reboot or protection controls.
The observing MQTT connection remains read-only; writes use a separate one-shot client.
"""
import copy,hashlib,json,os,pathlib,secrets,threading,time
from datetime import datetime,timezone

def utc():return datetime.now(timezone.utc).isoformat()
class ControlError(Exception):
    def __init__(self,message,status=409):super().__init__(message);self.status=status

FIELDS={}
for zone,name in [(1,'Fancoil'),(2,'Pavimento')]:
    for suffix,label,lo,hi in [('Target_High','Mandata al punto freddo',20,60 if zone==1 else 40),('Target_Low','Mandata al punto mite',20,60 if zone==1 else 40),('Outside_Low','Temperatura esterna del punto freddo',-20,30),('Outside_High','Temperatura esterna del punto mite',-20,30)]:
        axis,point=suffix.lower().split('_')
        FIELDS[f'Z{zone}_Heat_Curve_{suffix}_Temp']={'label':f'Zona {zone} · {name} · {label}','unit':'°C','min':lo,'max':hi,'command':'SetCurves','path':[f'zone{zone}','heat',axis,point]}
    FIELDS[f'Z{zone}_Heat_Request_Temp']={'label':f'Zona {zone} · {name} · Correzione curva','unit':'°C','min':-5,'max':5,'command':f'SetZ{zone}HeatRequestTemperature'}
FIELDS['Buffer_Tank_Delta']={'label':'Puffer tecnico · Differenziale di temperatura','unit':'K','min':0,'max':10,'command':'SetBufferDelta'}
FIELDS['DHW_Target_Temp']={'label':'Acqua calda sanitaria · Temperatura richiesta','unit':'°C','min':40,'max':55,'command':'SetDHWTemp'}
FIELDS['Quiet_Mode_Level']={'label':'Quiet · Livello silenzioso','unit':'','min':0,'max':3,'command':'SetQuietMode'}
GUARDS=list(FIELDS)+['Heating_Mode','Zones_State','Buffer_Installed']

def payload_for(key,value):
    field=FIELDS[key]
    if field['command']!='SetCurves':return str(value)
    result={};node=result
    for part in field['path'][:-1]:node=node.setdefault(part,{})
    node[field['path'][-1]]=value
    return json.dumps(result,separators=(',',':'))

def validate_change(key,value,current):
    if not isinstance(key,str) or key not in FIELDS:raise ControlError('Parametro non disponibile.',400)
    if type(value) is not int:raise ControlError('Inserisci un numero intero.',400)
    field=FIELDS[key]
    if not field['min']<=value<=field['max']:raise ControlError('Valore fuori dall’intervallo ammesso.',400)
    values=current['values']
    if type(values.get(key)) is not int:raise ControlError('Valore attuale non disponibile: modifica bloccata.')
    if key.startswith(('Z1_','Z2_')):
        if values.get('Heating_Mode')!=0:raise ControlError('La regolazione deve essere su curva climatica.')
        changed={**values,key:value};zone=key[:2]
        required=[zone+'_Heat_Curve_'+part+'_Temp' for part in ['Target_High','Target_Low','Outside_High','Outside_Low']]+[zone+'_Heat_Request_Temp']
        if any(type(changed.get(k)) is not int for k in required):raise ControlError('Curva completa non disponibile.')
        if changed[zone+'_Heat_Curve_Outside_Low_Temp']>=changed[zone+'_Heat_Curve_Outside_High_Temp']:raise ControlError('Il punto esterno freddo deve essere inferiore al punto mite.',400)
        if changed[zone+'_Heat_Curve_Target_Low_Temp']>changed[zone+'_Heat_Curve_Target_High_Temp']:raise ControlError('La mandata del punto mite non può superare quella del punto freddo.',400)
        effective=[changed[zone+'_Heat_Curve_Target_'+p+'_Temp']+changed[zone+'_Heat_Request_Temp'] for p in ['Low','High']]
        if min(effective)<20 or max(effective)>(40 if zone=='Z2' else 60):raise ControlError('La curva con la correzione supera i limiti di mandata. Per il pavimento il limite configurato è 40 °C.',400)
    if key=='Buffer_Tank_Delta':
        if current.get('firmware')!='2.0-WT32-ETH01-CZ-web2-buffer':raise ControlError('Per il puffer serve il firmware aggiornato sulla ETH01.')
        if values.get('Buffer_Installed')!=1:raise ControlError('Puffer non configurato sulla PDC.')

class ControlManager:
    def __init__(self,read_current,write_once,journal,enabled=False,sleep=time.sleep):
        self.read_current=read_current;self.write_once=write_once;self.journal=pathlib.Path(journal)
        self.enabled=enabled;self.sleep=sleep;self.plans={};self.lock=threading.RLock();self.executing=threading.Lock();self.publication_attempts=0
    def current(self):
        current=self.read_current()
        if not current.get('fresh') or current.get('listen_only') or not current.get('tx_enabled') or type(current.get('frame_count')) is not int or current['frame_count']<=0:raise ControlError('La comunicazione con la PDC non è recente o la trasmissione è disabilitata.')
        return current
    def catalog(self):
        try:current=self.current();error=None
        except (ControlError,OSError,ValueError):current={'values':{}};error='Lettura PDC non disponibile o non recente.'
        fields=[]
        for key,meta in FIELDS.items():
            value=current['values'].get(key);supported=type(value) is int
            if key=='Quiet_Mode_Level':supported=supported and 0<=value<=3
            if key=='Buffer_Tank_Delta':supported=supported and current.get('firmware')=='2.0-WT32-ETH01-CZ-web2-buffer' and current['values'].get('Buffer_Installed')==1
            if key.startswith(('Z1_','Z2_')):supported=supported and current['values'].get('Heating_Mode')==0
            fields.append({k:v for k,v in dict(meta,key=key,value=value,supported=supported).items() if k not in ['command','path']})
        with self.lock:recent=[copy.deepcopy(p['receipt']) for p in self.plans.values() if p.get('receipt')][-10:]
        return {'enabled':self.enabled,'fields':fields,'error':error,'recent':recent,'note':'Ogni modifica richiede anteprima e conferma. Il limite della mandata pavimento è 40 °C, correzione inclusa.'}
    def prepare(self,session,key,value,expected):
        current=self.current();validate_change(key,value,current)
        if type(expected) is not int or current['values'][key]!=expected:raise ControlError('Il valore è cambiato: aggiorna i dati e riprova.')
        if value==expected:raise ControlError('Il nuovo valore è uguale al valore attuale.',400)
        now=time.monotonic();plan={'id':secrets.token_urlsafe(24),'session':session,'expires':now+90,'key':key,'value':value,'before':expected,'baseline':{k:current['values'].get(k) for k in GUARDS},'state':'prepared','created_at':utc()}
        with self.lock:
            self.plans={k:p for k,p in self.plans.items() if p['expires']>now or p.get('receipt')}
            if len(self.plans)>=500:raise ControlError('Registro della sessione pieno: riavvio amministrativo necessario.',429)
            self.plans[plan['id']]=plan
        return {'id':plan['id'],'key':key,'label':FIELDS[key]['label'],'before':expected,'after':value,'unit':FIELDS[key]['unit'],'expires_in_seconds':90,'can_apply':self.enabled,'note':'Viene modificato soltanto questo parametro. Controlla il valore precedente e quello nuovo prima di confermare.'}
    def record(self,receipt):
        # Persist intent before publishing; refuse writes when the audit cannot be saved.
        self.journal.parent.mkdir(parents=True,exist_ok=True)
        with self.journal.open('a',encoding='utf-8') as file:
            file.write(json.dumps(receipt,ensure_ascii=False,separators=(',',':'))+'\n');file.flush();os.fsync(file.fileno())
    def execute(self,session,plan_id):
        if not self.enabled:raise ControlError('Le modifiche non sono ancora abilitate su questo server.',403)
        with self.lock:
            plan=self.plans.get(plan_id)
            if not plan or plan['session']!=session:raise ControlError('Anteprima non disponibile: prepara nuovamente la modifica.')
            if plan.get('receipt'):return copy.deepcopy(plan['receipt'])
            if plan['expires']<time.monotonic():raise ControlError('Anteprima scaduta: prepara nuovamente la modifica.')
        if not self.executing.acquire(blocking=False):raise ControlError('Un’altra modifica è in corso. Attendi la verifica.')
        try:
            with self.lock:
                if plan.get('receipt'):return copy.deepcopy(plan['receipt'])
            current=self.current();validate_change(plan['key'],plan['value'],current)
            if {k:current['values'].get(k) for k in GUARDS}!=plan['baseline']:raise ControlError('La configurazione è cambiata dopo l’anteprima: nessun comando inviato.')
            receipt={'id':plan['id'],'created_at':utc(),'key':plan['key'],'label':FIELDS[plan['key']]['label'],'unit':FIELDS[plan['key']]['unit'],'before':plan['before'],'requested':plan['value'],'state':'intent_saved','publication_attempts':0,'configuration_before':current['values'],'actor_session_hash':hashlib.sha256(session.encode()).hexdigest()[:12]}
            try:self.record(receipt)
            except OSError:raise ControlError('Impossibile salvare il registro: nessun comando inviato.',503)
            with self.lock:plan['receipt']=receipt;plan['state']='executing'
            try:
                receipt['publication_attempts']=1;self.publication_attempts+=1
                self.write_once(FIELDS[plan['key']]['command'],payload_for(plan['key'],plan['value']))
                receipt['state']='awaiting_readback';last_count=current['frame_count'];matches=0;deadline=time.monotonic()+32
                for _ in range(10):
                    if time.monotonic()>deadline:break
                    self.sleep(3);after=self.current();receipt['configuration_after']=after['values'];receipt['last_readback_at']=utc()
                    if after['frame_count']<=last_count:continue
                    last_count=after['frame_count']
                    guards={k:v for k,v in plan['baseline'].items() if k!=plan['key']}
                    changed_elsewhere=[k for k,v in guards.items() if after['values'].get(k)!=v]
                    if changed_elsewhere:
                        receipt['changed_elsewhere']=changed_elsewhere;break
                    matches=matches+1 if after['values'].get(plan['key'])==plan['value'] else 0
                    if matches>=2:receipt['state']='verified';receipt['confirmed_value']=plan['value'];break
                if receipt['state']!='verified':receipt['state']='uncertain'
            except Exception:receipt['state']='uncertain'
            receipt['finished_at']=utc();receipt['note']='Nessun reinvio automatico. In caso di esito incerto, controlla la lettura prima di agire.'
            try:self.record(receipt)
            except OSError:receipt['audit_warning']='Il risultato finale non è stato salvato su disco; l’intenzione iniziale resta registrata.'
            with self.lock:plan['state']=receipt['state']
            return copy.deepcopy(receipt)
        finally:self.executing.release()
