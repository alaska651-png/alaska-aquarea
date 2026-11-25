"""LAN frontend and authenticated server-side proxy; existing collector and guarded Digital Twin control proxy."""
import asyncio, hashlib, hmac, json, os, secrets, time, re
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from aiohttp import web, ClientSession, ClientTimeout

ROOT=Path(__file__).parent
LAN=os.environ.get('LAN_HOST','127.0.0.1')
PORT=int(os.environ.get('WEB_PORT','8769'))
ORIGIN='http://'+LAN+':'+str(PORT)
HA=os.environ.get('HA_URL','http://127.0.0.1:8123').rstrip('/')
COMMON=os.environ.get('COMMON_URL','http://127.0.0.1:8768').rstrip('/')
ZONE=ZoneInfo('Europe/Rome')

def app():
    cache={};lock=asyncio.Lock()
    bridge_secret=(ROOT/'private/ha-bridge.key').read_text(encoding='utf-8').strip()
    if len(bridge_secret)<40:raise RuntimeError('HA SSO bridge not provisioned')
    control_sessions={};control_plans={};control_lock=asyncio.Lock()
    allowed_controls={"Quiet_Mode_Level","DHW_Target_Temp","Buffer_Tank_Delta","Z2_Heat_Request_Temp",*["Z2_Heat_Curve_"+part+"_Temp" for part in ("Target_High","Target_Low","Outside_High","Outside_Low")]}
    password=os.environ['LOCAL_UI_PASSWORD']
    private=json.loads(Path(os.environ['COMMON_CONFIG']).read_text(encoding='utf-8'))
    entity=private.get('entities',{})
    catalog=json.loads((ROOT/'web/catalog.json').read_text(encoding='utf-8'))
    known={x['name']:x.get('entity') for x in catalog}
    history_entities={
        'termica_riscaldamento':entity.get('heat_production'),'elettrico_riscaldamento':entity.get('heat_consumption'),
        'termica_acs':entity.get('dhw_production'),'elettrico_acs':entity.get('dhw_consumption'),
        'mandata':known.get('Main_Outlet_Temp'),'ritorno':known.get('Main_Inlet_Temp'),
        'esterna':known.get('Outside_Temp'),'accumulo_tecnico':known.get('Buffer_Temp'),'serbatoio_acs':known.get('DHW_Temp')}
    # Match the existing ha-data.js binding names exactly.
    history_entities['elettrica_riscaldamento']=history_entities.pop('elettrico_riscaldamento')
    history_entities['elettrica_acs']=history_entities.pop('elettrico_acs')
    history_entities={k:v for k,v in history_entities.items() if v}
    ids=list(dict.fromkeys([*history_entities.values(),known.get('Compressor_Freq'),known.get('Defrosting_State'),known.get('Operations_Counter'),entity.get('heartbeat'),*[r.get('sensor') for r in private.get('rooms',{}).values()],*[r.get('relay') for r in private.get('rooms',{}).values()]]))
    ids=[x for x in ids if x]

    @web.middleware
    async def guard(request, handler):
        if request.path=='/healthz' and request.method in ('GET','HEAD'):
            return web.json_response({'status':'healthy','authentication':'home_assistant'})
        if request.remote not in ('127.0.0.1','::1') or not hmac.compare_digest(request.headers.get('X-Aquarea-Bridge',''),bridge_secret):raise web.HTTPUnauthorized()
        user=request.headers.get('X-Aquarea-User','');identity=request.headers.get('X-Aquarea-Session','')
        if not re.fullmatch(r'[a-zA-Z0-9_-]{8,100}',user) or not re.fullmatch(r'[a-zA-Z0-9_-]{32,100}',identity):raise web.HTTPUnauthorized()
        if request.method not in ('GET','HEAD') and request.path not in ('/api/control/plan','/api/control/execute','/api/mode/plan','/api/mode/execute'):raise web.HTTPMethodNotAllowed(request.method,['GET','HEAD'])
        request['authenticated']=True
        request['sid']='ha:'+user+':'+identity
        response=await handler(request)
        response.headers.update({'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer',
            'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'self' http://"+LAN+':8123'})
        return response

    application=web.Application(middlewares=[guard],client_max_size=4096)
    async def lifecycle(a):
        a['http']=ClientSession(timeout=ClientTimeout(total=30),trust_env=False)
        yield
        await a['http'].close()
    application.cleanup_ctx.append(lifecycle)

    async def index(request):
        return web.FileResponse(ROOT/'web/index.html')

    async def asset(request):
        name=request.match_info['name']
        allowed=['styles.css','app.js','cop.js','diagram.js','catalog.json','reuse/charts.js','reuse/data.js','reuse/ha-data.js','reuse/climate-data.mjs']
        if name not in allowed:raise web.HTTPNotFound()
        return web.FileResponse(ROOT/'web'/name)

    async def current(request):
        # Same host network namespace as the existing loopback API; not container-local bridging.
        try:
            async with application['http'].get(COMMON+'/api/v1/snapshot',headers={'Authorization':'Bearer '+os.environ['API_TOKEN']},allow_redirects=False) as r:
                r.raise_for_status();data=await r.json()
            return web.json_response(data)
        except Exception:
            raise web.HTTPBadGateway(text='Backend comune non raggiungibile')

    async def twin_session(request):
        sid=request['sid']
        if sid in control_sessions:return control_sessions[sid]
        # Cookies and legacy CSRF remain server-side; browser receives neither.
        base='http://'+LAN+':8767'
        async with application['http'].get(base+'/',allow_redirects=False) as r:
            html=await r.text();cookie=r.cookies.get('aquarea_csrf')
        match=re.search(r'name="csrf" value="([^"<>]+)"',html)
        if not cookie or not match:raise web.HTTPServiceUnavailable(text='Accesso Digital Twin non disponibile')
        async with application['http'].post(base+'/auth/login',data={'csrf':match[1],'password':password},headers={'Origin':base,'Cookie':'aquarea_csrf='+cookie.value},allow_redirects=False) as r:
            cookie=r.cookies.get('aquarea_session')
            if r.status!=303 or not cookie:raise web.HTTPServiceUnavailable(text='Autenticazione Digital Twin rifiutata')
        legacy={'Cookie':'aquarea_session='+cookie.value,'Origin':base}
        control_sessions[sid]={'headers':legacy,'csrf':secrets.token_urlsafe(32)}
        return control_sessions[sid]

    async def twin_catalog(request):
        session=await twin_session(request)
        async with application['http'].get('http://'+LAN+':8767/api/controls',headers=session['headers'],allow_redirects=False) as r:
            if r.status!=200:
                control_sessions.pop(request['sid'],None)
                raise web.HTTPServiceUnavailable(text='Sessione Digital Twin scaduta: aggiorna i controlli')
            result=await r.json()
        session['headers']['X-Aquarea-CSRF']=result.pop('csrf')
        return session,result

    async def seasonal_guard(key):
        if not key.startswith('Z2_'):return
        async with application['http'].get(COMMON+'/api/v1/snapshot',headers={'Authorization':'Bearer '+os.environ['API_TOKEN']},allow_redirects=False) as r:
            if r.status!=200:raise web.HTTPConflict(text='Stagione non verificabile')
            state=await r.json()
        z=state.get('zones',{});metrics=state.get('metrics',{})
        for name,values in (('Heatpump_State',(1,)),('Operating_Mode_State',(0,4)),('Zones_State',(1,))):
            reading=metrics.get(name,{})
            if reading.get('quality')!='fresh' or reading.get('value') not in values:raise web.HTTPConflict(text='Regolazione Z2 bloccata: stato Panasonic, modalità caldo o esclusione Z1 non confermati')
        if z.get('season_quality')!='fresh' or z.get('season')!='winter':raise web.HTTPConflict(text='Correzione Z2 consentita solo in inverno con stato aggiornato')

    async def controls(request):
        session,result=await twin_catalog(request)
        result['fields']=[f for f in result.get('fields',[]) if f['key'] in allowed_controls]
        result['csrf']=session['csrf']
        result['mode_reason']='Modalità gestita dal Coordinatore: nessun cambio diretto disponibile senza conflitto di autorità.'
        return web.json_response(result)

    async def control_post(request):
        if request.headers.get('Origin')!=ORIGIN or request.content_type!='application/json':raise web.HTTPForbidden(text='Origine o formato non consentiti')
        session=control_sessions.get(request['sid'])
        supplied=request.headers.get('X-Aquarea-CSRF','')
        if not session or not supplied or not hmac.compare_digest(supplied,session['csrf']):raise web.HTTPForbidden(text='Conferma di sessione non valida')
        try:body=await request.json()
        except Exception:raise web.HTTPBadRequest(text='Richiesta non valida')
        if not isinstance(body,dict):raise web.HTTPBadRequest()
        execute=request.path.endswith('/execute')
        if execute:
            if set(body)!={'id'} or not isinstance(body['id'],str):raise web.HTTPBadRequest()
            record=control_plans.get(body['id'])
            if not record or record.get('kind')=='mode' or record['sid']!=request['sid'] or record['expires']<time.monotonic() or record['used']:raise web.HTTPConflict(text='Anteprima scaduta o già utilizzata; nessun reinvio')
            key=record['key']
        else:
            if set(body)!={'key','value','expected'} or body.get('key') not in allowed_controls or type(body.get('value')) is not int or type(body.get('expected')) is not int:raise web.HTTPBadRequest(text='Parametro non supportato')
            key=body['key']
            if key=='Quiet_Mode_Level' and (body['value'] not in range(4) or body['expected'] not in range(4) or body['value']==body['expected']):raise web.HTTPBadRequest(text='Livello Quiet ammesso: OFF, 1, 2 o 3')
            if key!='Quiet_Mode_Level' and abs(body['value']-body['expected'])!=1:raise web.HTTPBadRequest(text='Passo ammesso: 1')
        await seasonal_guard(key)
        if control_lock.locked():raise web.HTTPConflict(text='Una modifica è già in corso')
        async with control_lock:
            if execute:
                if record['used']:raise web.HTTPConflict(text='Anteprima già utilizzata')
                record['used']=True # Before network I/O: never resend an uncertain operation.
            else:
                _,catalogue=await twin_catalog(request)
                if not catalogue.get('enabled'):raise web.HTTPForbidden(text='Controlli Digital Twin disabilitati')
            try:
                async with application['http'].post('http://'+LAN+':8767/api/controls/'+('execute' if execute else 'plan'),json=body,headers=session['headers'],allow_redirects=False,timeout=ClientTimeout(total=60)) as r:
                    result=await r.json()
                    if r.status!=200:return web.json_response(result,status=r.status)
            except (asyncio.TimeoutError,OSError,ValueError):
                raise web.HTTPServiceUnavailable(text='Esito non verificabile. Nessun reinvio automatico: rileggi lo stato prima di agire.')
            if not execute:
                now=time.monotonic()
                for token,p in list(control_plans.items()):
                    if p['expires']<now:control_plans.pop(token,None)
                control_plans[result['id']]={'sid':request['sid'],'key':key,'expires':now+90,'used':False}
            return web.json_response(result)

    async def mode_status(request):
        async with application['http'].get(HA+'/api/states/sensor.coordinatore_climatico',headers={'Authorization':'Bearer '+os.environ['HA_TOKEN']},allow_redirects=False) as r:
            if r.status!=200:raise web.HTTPServiceUnavailable(text='Coordinatore non raggiungibile')
            state=await r.json()
        return state.get('attributes',{}).get('mode_control',{'version':0,'options':{},'reason':'Estensione modalità in attesa di attivazione nel Coordinatore'})

    async def mode_catalog(request):
        return web.json_response(await mode_status(request))

    async def mode_post(request):
        if request.headers.get('Origin')!=ORIGIN or request.content_type!='application/json':raise web.HTTPForbidden()
        session=control_sessions.get(request['sid']);csrf=request.headers.get('X-Aquarea-CSRF','')
        if not session or not csrf or not hmac.compare_digest(csrf,session['csrf']):raise web.HTTPForbidden(text='Sessione di conferma non valida')
        try:body=await request.json()
        except Exception:raise web.HTTPBadRequest()
        if not isinstance(body,dict):raise web.HTTPBadRequest()
        execute=request.path.endswith('/execute')
        if not execute:
            if set(body)!={'mode'} or body.get('mode') not in ('heat','cool','heat_dhw','cool_dhw','off'):raise web.HTTPBadRequest()
            catalog=await mode_status(request);choice=catalog.get('options',{}).get(body['mode'],{})
            if catalog.get('version')!=1 or not choice.get('available'):raise web.HTTPConflict(text=choice.get('reason') or catalog.get('reason') or 'Modalità non disponibile')
            async with application['http'].get(COMMON+'/api/v1/snapshot',headers={'Authorization':'Bearer '+os.environ['API_TOKEN']},allow_redirects=False) as r:
                if r.status!=200:raise web.HTTPServiceUnavailable()
                snap=await r.json()
            if not all(snap.get('sources',{}).get(k,{}).get('fresh') for k in ('heishamon','home_assistant')) or snap.get('operating',{}).get('coordinator',{}).get('quality')!='fresh':raise web.HTTPConflict(text='Telemetria non aggiornata')
            token=secrets.token_urlsafe(24);now=time.monotonic()
            for key,value in list(control_plans.items()):
                if value['expires']<now:control_plans.pop(key,None)
            control_plans[token]={'kind':'mode','sid':request['sid'],'expires':now+90,'used':False,'mode':body['mode'],'expected':catalog['baseline']}
            current=snap['metrics'];power=current.get('Heatpump_State',{});op=current.get('Operating_Mode_State',{})
            before='OFF' if power.get('value')==0 else {0:'Solo caldo',1:'Solo freddo',3:'Solo ACS',4:'Caldo + ACS',5:'Freddo + ACS'}.get(op.get('value'),'N/D')
            return web.json_response({'id':token,'before':before,'after':choice['label'],'expires_in_seconds':90})
        if set(body)!={'id'} or not isinstance(body.get('id'),str):raise web.HTTPBadRequest()
        plan=control_plans.get(body['id'])
        if not plan or plan.get('kind')!='mode' or plan['sid']!=request['sid'] or plan['used'] or plan['expires']<time.monotonic():raise web.HTTPConflict(text='Anteprima scaduta o già utilizzata')
        if control_lock.locked():raise web.HTTPConflict(text='Un’altra modifica è in corso')
        async with control_lock:
            if plan['used']:raise web.HTTPConflict()
            plan['used']=True
            try:
                async with application['http'].ws_connect(HA+'/api/websocket',heartbeat=20) as ws:
                    await ws.receive_json();await ws.send_json({'type':'auth','access_token':os.environ['HA_TOKEN']})
                    if (await ws.receive_json()).get('type')!='auth_ok':raise ValueError()
                    await ws.send_json({'id':1,'type':'subscribe_events','event_type':'aquarea_clima_mode_ack'})
                    reply=await ws.receive_json()
                    if not reply.get('success'):raise ValueError()
                    payload={'request_id':body['id'],'command':'pdc_mode','payload':{'mode':plan['mode'],'expected':plan['expected'],'expires':time.time()+min(89,plan['expires']-time.monotonic())}}
                    # One authenticated event to the existing owner; no service/MQTT calls here.
                    async with application['http'].post(HA+'/api/events/coordinatore_climatico_command',json=payload,headers={'Authorization':'Bearer '+os.environ['HA_TOKEN']},allow_redirects=False) as r:
                        if r.status!=200:raise ValueError()
                    deadline=time.monotonic()+150
                    while time.monotonic()<deadline:
                        message=await asyncio.wait_for(ws.receive_json(),max(1,deadline-time.monotonic()))
                        ack=message.get('event',{}).get('data',{})
                        if ack.get('request_id')==body['id']:return web.json_response(ack,status=200 if ack.get('ok') and ack.get('state')=='verified' else 409)
                    raise ValueError()
            except (asyncio.TimeoutError,OSError,ValueError):
                raise web.HTTPServiceUnavailable(text='Esito modalità non verificato. Nessun reinvio automatico; controlla lo stato prima di agire.')

    async def cop_proxy(request):
        resource=request.match_info['resource']
        if resource not in ('analysis','snapshot','mapping','export.csv'):raise web.HTTPNotFound()
        try:
            async with application['http'].get('http://127.0.0.1:8770/'+resource,params=request.query,headers={'Authorization':'Bearer '+os.environ['API_TOKEN']},allow_redirects=False) as r:
                if r.status!=200:raise web.HTTPServiceUnavailable(text='Archivio COP non disponibile o intervallo non valido')
                if resource=='export.csv':
                    response=web.StreamResponse(headers={'Content-Type':'text/csv; charset=utf-8','Content-Disposition':'attachment; filename="aquarea-cop.csv"','Cache-Control':'no-store'})
                    await response.prepare(request)
                    async for chunk in r.content.iter_chunked(65536):await response.write(chunk)
                    await response.write_eof();return response
                return web.json_response(await r.json())
        except (OSError,asyncio.TimeoutError):raise web.HTTPServiceUnavailable(text='Registratore COP non raggiungibile')

    async def schedule(request):
        try:
            # Configuration read on explicit page request, not a second live acquisition.
            async with application['http'].get(HA+'/api/states/sensor.coordinatore_climatico',headers={'Authorization':'Bearer '+os.environ['HA_TOKEN']},allow_redirects=False) as response:
                response.raise_for_status();state=await response.json()
            data=state.get('attributes',{}).get('settings',{})
            return web.json_response({k:data.get(k) for k in ('master','season','schedule_enabled','comfort','eco','week','floors','params')})
        except Exception:
            raise web.HTTPServiceUnavailable(text='Programma del coordinatore non disponibile')

    async def history(request):
        try:
            days=int(request.query.get('days','1'))
            if days not in (1,7,30):raise ValueError()
            end_date=datetime.fromisoformat(request.query.get('end',datetime.now(ZONE).date().isoformat())).date()
            if end_date>datetime.now(ZONE).date():raise ValueError()
        except ValueError:raise web.HTTPBadRequest(text='Periodo non valido')
        key=(days,str(end_date));now=time.monotonic()
        async with lock:
            if key in cache and now-cache[key][0]<60:return web.json_response(cache[key][1])
            end=datetime.combine(end_date+timedelta(days=1),datetime.min.time(),ZONE)
            start=datetime.combine(end_date-timedelta(days=days-1),datetime.min.time(),ZONE)
            end_ts=min(end.timestamp(),time.time()//3600*3600)
            if end_ts<=start.timestamp():
                return web.json_response({'statistics':{},'metadata':{},'history':{},'entities':history_entities,'start':start.timestamp()*1000,'end':end_ts*1000})
            iso=lambda t:datetime.fromtimestamp(t,ZONE).isoformat()
            try:
                async with application['http'].ws_connect(HA+'/api/websocket',heartbeat=20) as ws:
                    await ws.receive_json();await ws.send_json({'type':'auth','access_token':os.environ['HA_TOKEN']})
                    if (await ws.receive_json()).get('type')!='auth_ok':raise ValueError('HA authentication')
                    async def query(i,typ,**kw):
                        await ws.send_json({'id':i,'type':typ,**kw})
                        while True:
                            result=await asyncio.wait_for(ws.receive_json(),25)
                            if result.get('id')==i:break
                        if not result.get('success'):raise ValueError('HA recorder query')
                        return result['result']
                    metadata=await query(1,'recorder/get_statistics_metadata',statistic_ids=ids)
                    available=[x['statistic_id'] for x in metadata]
                    stats=await query(2,'recorder/statistics_during_period',start_time=iso(start.timestamp()-3600),end_time=iso(end_ts),statistic_ids=available,period='hour',types=['mean','sum','state']) if available else {}
                # Detailed transitions only for the last day of the requested range.
                import urllib.parse
                raw_start=max(start.timestamp(),end_ts-86400)
                url=HA+'/api/history/period/'+urllib.parse.quote(iso(raw_start),safe='')
                params={'filter_entity_id':','.join(ids),'end_time':iso(end_ts),'significant_changes_only':'0'}
                async with application['http'].get(url,params=params,headers={'Authorization':'Bearer '+os.environ['HA_TOKEN']},allow_redirects=False) as r:
                    r.raise_for_status();raw=await r.json()
                data={'statistics':stats,'metadata':{x['statistic_id']:x for x in metadata},'history':{row[0]['entity_id']:row for row in raw if row},
                      'entities':history_entities,'start':start.timestamp()*1000,'end':end_ts*1000,'raw_start':raw_start*1000,
                      'compressor_entity':known.get('Compressor_Freq'),'defrost_entity':known.get('Defrosting_State'),'operations_entity':known.get('Operations_Counter'),
                      'room_relays':{key:{'name':r.get('name',key),'entity':r.get('relay')} for key,r in private.get('rooms',{}).items()},
                      'scope':'HA recorder on demand; completed hourly buckets; raw transitions last 24h only'}
                if len(cache)>5:cache.clear()
                cache[key]=(time.monotonic(),data)
                return web.json_response(data)
            except Exception:
                raise web.HTTPServiceUnavailable(text='Storico HA non disponibile; nessun dato simulato sostitutivo')

    application.router.add_get('/',index)
    application.router.add_get('/healthz',index)
    application.router.add_get('/api/mode',mode_catalog)
    application.router.add_post('/api/mode/plan',mode_post);application.router.add_post('/api/mode/execute',mode_post)
    application.router.add_get('/api/control',controls)
    application.router.add_post('/api/control/plan',control_post);application.router.add_post('/api/control/execute',control_post)
    application.router.add_get('/api/cop/{resource}',cop_proxy)
    application.router.add_get('/api/snapshot',current);application.router.add_get('/api/schedule',schedule);application.router.add_get('/api/history',history)
    application.router.add_get('/{name:.*}',asset)
    return application

if __name__=='__main__':
    web.run_app(app(),host='127.0.0.1',port=PORT,access_log=None,print=None)
