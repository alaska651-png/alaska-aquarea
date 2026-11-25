"""Passive recorder: common snapshot every 15s plus original MQTT receipts. No actuator API."""
import asyncio,json,os,time,queue,secrets,hmac,logging,copy
from pathlib import Path
from datetime import datetime,timedelta
from aiohttp import web,ClientSession,ClientTimeout
import paho.mqtt.client as mqtt
from analysis import *
from store import Store
LOG=logging.getLogger('cop')
ROOT=Path('/data');TOKEN=os.environ['API_TOKEN'];PREFIX=os.environ.get('MQTT_PREFIX','panasonic_heat_pump').strip('/')
class Passive(mqtt.Client):
    def publish(self,*a,**kw):raise PermissionError('Monitoring only: publishing disabled')
    def will_set(self,*a,**kw):raise PermissionError('Monitoring only: will disabled')
class Recorder:
    def __init__(self):
        self.store=Store(ROOT/'aquarea.sqlite');self.inbox=queue.Queue(20000);self.live={};self.extra=set(self.store.meta('extra_seen',[]));self.connected=False;self.dropped=0;self.last_snapshot=self.store.previous;self.storage={};self.sample_error=None;self.last_state={};self.last_class=None;self.stable_since=time.time();self.maintenance_at=0;self.ha_connected=False;self.cumulative={}
        with self.store.db:self.store.event(time.time(),'recorder_start',value={'previous_snapshot_at':self.last_snapshot.get('t') if self.last_snapshot else None})
        self.catalog=json.loads(Path('/app/catalog.json').read_text());self.catalog.append({'name':'Buffer_Tank_Delta','unit':'K','entity':None});self.metadata=json.loads(Path('/private/monitor-metadata.json').read_text());self.energy_ids={x['entity'] for x in self.metadata.get('energy_entities',[]) if x.get('unit')=='kWh'}
        self.client=Passive(mqtt.CallbackAPIVersion.VERSION2,client_id='aquarea-cop-'+secrets.token_hex(6),clean_session=True)
        if os.environ.get('MQTT_USERNAME'):self.client.username_pw_set(os.environ['MQTT_USERNAME'],os.environ.get('MQTT_PASSWORD'))
        self.client.reconnect_delay_set(1,30);self.client.on_connect=self.connect;self.client.on_disconnect=self.disconnect;self.client.on_message=self.message
    def enqueue(self,data):
        try:self.inbox.put_nowait(data)
        except queue.Full:self.dropped+=1
    def connect(self,client,userdata,flags,rc,properties):
        ok=int(rc)==0 if isinstance(rc,int) else not rc.is_failure
        self.connected=ok;self.enqueue({'ts':time.time(),'kind':'mqtt_connect' if ok else 'mqtt_connect_failed'})
        if ok:
            for topic in ('main/+','extra/+','stats','LWT'):client.subscribe(PREFIX+'/'+topic,qos=0)
    def disconnect(self,*args):self.connected=False;self.enqueue({'ts':time.time(),'kind':'mqtt_disconnect'})
    def message(self,client,userdata,message):
        topic=message.topic[len(PREFIX)+1:];raw=message.payload.decode('utf-8',errors='replace')[:4096]
        self.enqueue({'ts':time.time(),'kind':'mqtt','topic':topic,'value':raw,'retained':bool(message.retain)})
    async def drain(self):
        while True:
            with self.store.db:
                for _ in range(4000):
                    try:item=self.inbox.get_nowait()
                    except queue.Empty:break
                    if item['kind']=='mqtt':
                        topic=item['topic'];name=topic.split('/')[-1];number=num(item['value']);val=number if number is not None else item['value']
                        self.live[topic]={'value':val,'at':item['ts'],'retained':item['retained']}
                        if topic.startswith('extra/') and ('Power_' in name or 'Energy_' in name):self.extra.add(name);self.store.setmeta('extra_seen',sorted(self.extra))
                        if not item['retained'] and name in ('Compressor_Freq','Defrosting_State','ThreeWay_Valve_State','Operating_Mode_State','Heatpump_State','Force_DHW_State'):
                            old=self.last_state.get(name)
                            if old is not None and old!=val:self.store.event(item['ts'],'mqtt_transition',topic,{'before':old,'after':val,'timestamp':'receiver UTC; device timestamp not provided'})
                            self.last_state[name]=val
                    if not self.store.error:self.store.event(item['ts'],item['kind'],item.get('topic'),item.get('value'),item.get('retained',False))
            await asyncio.sleep(.5)
    async def ha_events(self):
        delay=1;last_demands={}
        while True:
            try:
                async with ClientSession(timeout=ClientTimeout(total=15)) as http:
                    async with http.ws_connect(os.environ['HA_URL']+'/api/websocket',heartbeat=20) as ws:
                        await ws.receive_json();await ws.send_json({'type':'auth','access_token':os.environ['HA_TOKEN']})
                        if (await ws.receive_json()).get('type')!='auth_ok':raise RuntimeError('HA auth')
                        for i,kind in enumerate(('state_changed','coordinatore_climatico_status','coordinatore_climatico_command'),1):await ws.send_json({'id':i,'type':'subscribe_events','event_type':kind})
                        self.ha_connected=True;delay=1
                        async for message in ws:
                            if message.type!=web.WSMsgType.TEXT:break
                            data=message.json()
                            if data.get('type')=='result' and not data.get('success'):raise RuntimeError('HA subscribe refused')
                            if data.get('type')!='event':continue
                            event=data['event'];kind=event['event_type'];body=event.get('data',{});received=time.time();fired=event.get('time_fired')
                            if kind=='state_changed':
                                entity=body.get('entity_id');state=body.get('new_state') or {}
                                if entity not in self.energy_ids:continue
                                measured=datetime.fromisoformat(state.get('last_updated',fired)).timestamp()
                                self.cumulative[entity]={'value':num(state.get('state')),'unit':state.get('attributes',{}).get('unit_of_measurement'),'measured_at':measured,'received_at':received,'source':'ha_state_changed'}
                                self.enqueue({'ts':received,'kind':'ha_counter','topic':entity,'value':{**self.cumulative[entity],'time_fired':fired}})
                            elif kind=='coordinatore_climatico_status':
                                snapshot=body.get('snapshot',{});rooms=snapshot.get('plan',{}).get('rooms',{})
                                for key,room in rooms.items():
                                    desired=room.get('desired')
                                    if key in last_demands and last_demands[key]!=desired:self.enqueue({'ts':received,'kind':'demand_transition','topic':key,'value':{'before':last_demands[key],'after':desired,'time_fired':fired,'physical_relay':room.get('relay_state')}})
                                    last_demands[key]=desired
                            else:self.enqueue({'ts':received,'kind':'coordinator_command_requested','value':{'command':body.get('command'),'payload':body.get('payload'),'time_fired':fired,'confirmation':'request event, not physical execution proof'}})
            except asyncio.CancelledError:raise
            except Exception as e:LOG.warning('HA passive stream: %s',type(e).__name__)
            self.ha_connected=False;await asyncio.sleep(delay);delay=min(30,delay*2)

    async def housekeeping(self):
        def work():
            other=Store(ROOT/'aquarea.sqlite')
            try:return other.maintain(time.time())
            finally:other.db.close()
        await asyncio.sleep(2)
        while True:
            try:self.storage=await asyncio.to_thread(work);self.store.error=self.storage.get('error')
            except Exception as e:self.storage={'error':'Manutenzione archivio: '+type(e).__name__};LOG.exception('Storage maintenance failed')
            await asyncio.sleep(3600)

    async def sample(self):
        async with ClientSession(timeout=ClientTimeout(total=8)) as http:
            while True:
                now=time.time()
                try:
                    if self.store.error:raise RuntimeError(self.store.error)
                    async with http.get('http://127.0.0.1:8768/api/v1/snapshot',headers={'Authorization':'Bearer '+TOKEN},allow_redirects=False) as r:r.raise_for_status();raw=await r.json()
                    now=time.time();row=normalize(raw,now,self.live,self.extra);row['mqtt_event_connected']=self.connected;row['mqtt_queue_dropped']=self.dropped
                    row['cumulative']=copy.deepcopy(self.cumulative)
                    for counter in row['cumulative'].values():counter.update(age_s=max(0,now-counter['measured_at']),quality='fresh' if self.ha_connected and counter['value'] is not None and 0<=now-counter['measured_at']<=7200 else 'stale')
                    row['totals']={k:value(v.get('electric_total')) for k,v in raw.get('energy',{}).items()}
                    previous=self.store.previous
                    for key,metric in row['metrics'].items():
                        old=previous.get('metrics',{}).get(key,{}) if previous else {}
                        metric['last_valid_value']=metric.get('value') if metric.get('quality')=='fresh' else old.get('last_valid_value')
                        metric['last_valid_at']=metric.get('received_at') if metric.get('quality')=='fresh' else old.get('last_valid_at')
                    if row['class']!=self.last_class:self.last_class=row['class'];self.stable_since=now
                    if previous and row.get('counter') is not None and previous.get('counter') is not None and row['counter']>previous['counter']:self.stable_since=now
                    row['transient']=now-self.stable_since<60
                    if row['transient']:row['cop_instant']=None
                    if previous:
                        for field in ('demand','defrost','threeway','class','season'):
                            if row.get(field)!=previous.get(field):self.store.event(now,'snapshot_transition',field,{'before':previous.get(field),'after':row.get(field)})
                    self.store.ingest(row);self.last_snapshot=row;self.sample_error=None
                except Exception as e:
                    self.sample_error=str(e);LOG.warning('Snapshot not recorded: %s',type(e).__name__)
                    if self.store.previous:
                        with self.store.db:self.store.event(now,'snapshot_error',value={'type':type(e).__name__})
                await asyncio.sleep(max(.1,15-time.time()%15))
    def mapping(self):
        row=self.last_snapshot or {};metrics=row.get('metrics',{});observed={}
        for item in self.store.db.execute("SELECT topic,MIN(ts) first,MAX(ts) last,COUNT(*) n FROM events WHERE kind='mqtt' AND retained=0 GROUP BY topic"):
            observed[item['topic']]={'last_received_utc':item['last'],'mean_interval_seconds':(item['last']-item['first'])/(item['n']-1) if item['n']>1 else None,'observations':item['n']}
        mapped=[]
        for c in self.catalog:
            key=c['name'];m=metrics.get(key,{})
            mapped.append({'logical':key,'mqtt_topic':PREFIX+'/main/'+key,'ha_entity':self.metadata.get('ha_bindings',{}).get(key,c.get('entity')),'unit':c.get('unit'),'quality':m.get('quality','unavailable'),'source':m.get('source'),'age_s':m.get('age_s'),'updates':observed.get('main/'+key),'current_value':m.get('value')})
        return {'sensors':mapped,'extra_topics':{k:v for k,v in observed.items() if k.startswith('extra/')},'required_missing':[name for name in ('Compressor_Freq','Operations_Counter','Operations_Hours','Compressor_Current','Heatpump_State','Operating_Mode_State','Quiet_Mode_Level','Powerful_Mode_Time','Error','Main_Inlet_Temp','Main_Outlet_Temp','Main_Target_Temp','Pump_Flow','Pump_Speed','Pump_Duty','Heat_Delta','Buffer_Temp','Z2_Water_Temp','Z2_Water_Target_Temp','Z2_Heat_Request_Temp','Z2_Temp','Heating_Mode','Outside_Temp','Outside_Pipe_Temp','Defrosting_State','Defrost_Temp','Discharge_Temp','Eva_Outlet_Temp','High_Pressure','Low_Pressure','Fan1_Motor_Speed','Fan2_Motor_Speed','ThreeWay_Valve_State','DHW_Temp','DHW_Target_Temp','Force_DHW_State','Internal_Heater_State','External_Heater_State','DHW_Heater_State','Room_Heater_State','Sterilization_State') if metrics.get(name,{}).get('quality')!='fresh'],'missing_physical':['Contatore termico indipendente certificato','Contatore elettrico dedicato con perimetro verificato','Portata e ritorno secondario pavimento','Feedback posizione miscelatrice','Composizione fluido termovettore','Motivo interno arresto compressore'],**self.metadata}
    def period(self,request):
        now=time.time()
        try:start=float(request.query.get('start',now-86400));end=min(now,float(request.query.get('end',now)))
        except ValueError:raise web.HTTPBadRequest(text='Intervallo non valido')
        if not 0<end-start<=31*86400:raise web.HTTPBadRequest(text='Intervallo massimo 31 giorni')
        return start,end
    def analysis(self,start,end):
        resolution=60 if end-start<=86400 else 900;summary=self.store.aggregate(start,end,60 if end-start<3600 else resolution)
        series=[]
        buckets={b['t']:json.loads(b['data']) for b in self.store.db.execute('SELECT t,data FROM buckets WHERE res=? AND t BETWEEN ? AND ? ORDER BY t',(resolution,int(start//resolution)*resolution,end))}
        for stamp in range(int(start//resolution)*resolution,int(end//resolution)*resolution+1,resolution):
            stats=finish(buckets.get(stamp,blank()),resolution)
            series.append({'t':stamp,**stats['means'],'heatQ':stats['q']['heat'] if stats['seconds']['heat']>0 else None,'heatE':stats['e']['heat'] if stats['seconds']['heat']>0 else None,'dhwQ':stats['q']['dhw'] if stats['seconds']['dhw']>0 else None,'dhwE':stats['e']['dhw'] if stats['seconds']['dhw']>0 else None,'cop':stats['cop']['heat'],'coverage':stats['coverage']['heat'],'seconds':stats['seconds']['heat']})
        cycles=self.store.cycle_list(start,end);events=self.store.events(start,end)
        starts=self.store.db.execute('SELECT count(*) FROM cycles WHERE left_censored=0 AND start BETWEEN ? AND ?',(start,end)).fetchone()[0]
        def count_events(kind):return sum(json.loads(r[0]).get('count',0) for r in self.store.db.execute('SELECT value FROM events WHERE kind=? AND ts BETWEEN ? AND ?',(kind,start,end)))
        counter_starts=count_events('counter_increment');unreconstructed=count_events('unreconstructed_start')
        full=[c for c in cycles if c['end'] is not None and not c['left_censored'] and not c['right_censored']]
        now=time.time();day=datetime.fromtimestamp(now,ROME).date();midnight=datetime.combine(day,datetime.min.time(),ROME).timestamp()
        windows={str(n):self.store.aggregate(now-n*60,now,60) for n in (5,15,60)};windows['today']=self.store.aggregate(midnight,now,60)
        diagnostics=[{'confidence':'non determinabile','message':'COP misurato indipendentemente non disponibile: potenze Panasonic, ausiliari non separati e composizione fluido non verificata.'},{'confidence':'non determinabile','message':'Carico minimo abitazione e limitazione miscelatrice richiedono portata/ritorno secondario e feedback valvola. Le correlazioni non dimostrano una causa.'}]
        if not full:diagnostics.append({'confidence':'non determinabile','message':'Nessun ciclo completo misurato nella finestra: acquisizione in corso; cicli parziali esplicitamente contrassegnati.'})
        if summary['coverage']['heat']<.9:diagnostics.append({'confidence':'confermata','message':'Copertura energetica incompleta: il COP rappresenta soltanto gli intervalli validi e temporalmente compatibili.'})
        hourly=[]
        for bucket in self.store.db.execute('SELECT t,data FROM buckets WHERE res=3600 AND t BETWEEN ? AND ? ORDER BY t',(int(start//3600)*3600,end)):
            stats=finish(json.loads(bucket['data']),3600);starts_hour=self.store.db.execute('SELECT count(*) FROM cycles WHERE left_censored=0 AND start>=? AND start<?',(bucket['t'],bucket['t']+3600)).fetchone()[0]
            hourly.append({'t':bucket['t'],'starts':starts_hour,'cop':stats['cop']['heat'],'coverage':stats['coverage']['heat'],**stats['means']})
        archive_since=self.store.db.execute('SELECT min(t) FROM samples').fetchone()[0]
        return {'archive_since':archive_since,'start':start,'end':end,'resolution_seconds':resolution,'summary':summary,'windows':windows,'series':series,'hourly_performance':hourly,'cycles':cycles,'events':events,'latest':self.last_snapshot,'starts_today_observed':self.store.db.execute('SELECT count(*) FROM cycles WHERE left_censored=0 AND start BETWEEN ? AND ?',(midnight,now)).fetchone()[0],'starts_today_counter':sum(json.loads(r[0]).get('count',0) for r in self.store.db.execute("SELECT value FROM events WHERE kind='counter_increment' AND ts BETWEEN ? AND ?",(midnight,now))),'starts_observed':starts,'starts_counter':counter_starts,'starts_unreconstructed':unreconstructed,'compressor_coverage_seconds':summary['weights'].get('freq',0),'starts_per_hour_observed':starts/(summary['weights']['freq']/3600) if summary['weights'].get('freq',0)>0 else None,'cycle_mean_seconds':sum(c['end']-c['start'] for c in full)/len(full) if full else None,'diagnostics':diagnostics,'limits':['Energia integrata trapezoidalmente con timestamp reali; intervalli oltre 45 s esclusi e segnalati.','Avviamenti e sbrinamenti restano nei bilanci; COP istantaneo non mostrato durante transitori o sbrinamento.','COP totale: riscaldamento + ACS misurati dalla Panasonic; ausiliari/standby non automaticamente inclusi.','Campioni 30 giorni, MQTT grezzo 7 giorni, aggregati/cicli 730 giorni; nessuna ricostruzione fittizia del passato.'],'storage':self.storage,'ha_event_connected':self.ha_connected,'mqtt_connected':self.connected,'queue_dropped':self.dropped,'sample_error':self.sample_error}

def application():
    recorder=Recorder()
    @web.middleware
    async def guard(request,handler):
        if request.method not in ('GET','HEAD'):raise web.HTTPMethodNotAllowed(request.method,['GET','HEAD'])
        if request.path!='/healthz' and not hmac.compare_digest(request.headers.get('Authorization',''),'Bearer '+TOKEN):raise web.HTTPUnauthorized()
        return await handler(request)
    app=web.Application(middlewares=[guard]);app['recorder']=recorder
    async def lifecycle(app):
        recorder.client.connect_async(os.environ['MQTT_HOST'],int(os.environ.get('MQTT_PORT',1883)),keepalive=60);recorder.client.loop_start()
        tasks=[asyncio.create_task(recorder.drain()),asyncio.create_task(recorder.sample()),asyncio.create_task(recorder.ha_events()),asyncio.create_task(recorder.housekeeping())];app['tasks']=tasks
        yield
        recorder.client.disconnect();recorder.client.loop_stop()
        await asyncio.sleep(.6)
        with recorder.store.db:recorder.store.event(time.time(),'recorder_stop')
        for task in tasks:task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True);recorder.client.disconnect();recorder.client.loop_stop();recorder.store.db.commit();recorder.store.db.close()
    app.cleanup_ctx.append(lifecycle)
    async def health(request):
        fresh=all(not t.done() for t in app.get('tasks',[])) and recorder.last_snapshot is not None and time.time()-recorder.last_snapshot['t']<45
        return web.json_response({'status':'healthy' if fresh and not recorder.store.error else 'degraded','snapshot_age_s':time.time()-recorder.last_snapshot['t'] if recorder.last_snapshot else None,'mqtt':recorder.connected,'error':recorder.sample_error},status=200 if fresh and not recorder.store.error else 503)
    async def analysis_handler(request):return web.json_response(recorder.analysis(*recorder.period(request)))
    async def snapshot(request):return web.json_response(recorder.last_snapshot)
    async def mapping(request):return web.json_response(recorder.mapping())
    async def export(request):
        start,end=recorder.period(request);response=web.StreamResponse(headers={'Content-Type':'text/csv; charset=utf-8','Content-Disposition':'attachment; filename="aquarea-cop.csv"'});await response.prepare(request)
        for chunk in recorder.store.csv(start,end):await response.write(chunk.encode())
        await response.write_eof();return response
    app.router.add_get('/healthz',health);app.router.add_get('/analysis',analysis_handler);app.router.add_get('/snapshot',snapshot);app.router.add_get('/mapping',mapping);app.router.add_get('/export.csv',export)
    return app
if __name__=='__main__':
    os.umask(0o077);logging.basicConfig(level=logging.INFO);web.run_app(application(),host='127.0.0.1',port=8770,access_log=None,print=None)
