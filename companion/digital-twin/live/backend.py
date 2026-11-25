"""Authenticated live bridge with explicitly enabled, previewed parameter edits."""
from collections import deque
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit
import argparse
import copy
import csv
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import sys
import threading
import time
import urllib.error
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'live'))
if (ROOT/'tmp/live-deps').exists(): sys.path.insert(0,str(ROOT/'tmp/live-deps'))
import paho.mqtt.client as mqtt
from controls import ControlManager,ControlError,FIELDS,GUARDS

PDC='http://127.0.0.1'
HA='http://127.0.0.1:8123'
BASE='panasonic_heat_pump'
STATIC={'/':'index.html','/index.html':'index.html','/styles.css':'styles.css','/app.mjs':'app.mjs','/core.mjs':'core.mjs','/data.js':'data.js','/live-client.mjs':'live-client.mjs','/control-client.mjs':'control-client.mjs'}
def utc(): return datetime.now(timezone.utc).isoformat()
def env_file(path):
    if not path.exists(): return {}
    return {line.split('=',1)[0].strip():line.split('=',1)[1].strip().strip('"\'') for line in path.read_text(encoding='utf-8-sig').splitlines() if '=' in line and not line.lstrip().startswith('#')}
def read_json(url,token=None):
    # Fixed upstream origins and GET only. No redirects carrying credentials.
    if not (url.startswith(PDC+'/') or url.startswith(HA+'/')): raise ValueError('Upstream not allowed')
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs): return None
    req=urllib.request.Request(url,headers={'Authorization':'Bearer '+token} if token else {},method='GET')
    with urllib.request.build_opener(NoRedirect).open(req,timeout=6) as response:
        return json.load(response)

def number(value):
    if isinstance(value,bool): return int(value)
    if isinstance(value,(int,float)): return value if math.isfinite(value) else None
    try:
        n=float(value)
        return int(n) if n.is_integer() else n if math.isfinite(n) else None
    except (ValueError,TypeError): return value if isinstance(value,str) else None

def invalid(key,value,unit):
    if value is None: return True
    if isinstance(value,(int,float)) and not math.isfinite(value): return True
    if unit=='°C' and isinstance(value,(int,float)) and value<=-70: return True
    return (key=='Heat_Pump_Model' and value==255) or (key=='Low_Pressure' and value==0)

class ReadOnlyClient(mqtt.Client):
    def publish(self,*args,**kwargs): raise PermissionError('Publishing is disabled')
    def will_set(self,*args,**kwargs): raise PermissionError('LWT publishing is disabled')

class Inputs(HTMLParser):
    def __init__(self): super().__init__();self.values={}
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=='input' and a.get('name'):self.values[a['name']]=a.get('value','')

class Bridge:
    def __init__(self,controls_enabled=False,journal=None):
        dictionary=json.loads((ROOT/'config/metrics.yaml').read_text(encoding='utf-8'))
        self.metadata={m['key']:m for m in dictionary['metrics']}
        self.topology=json.loads((ROOT/'config/topology.draft.json').read_text(encoding='utf-8'))
        self.installation=json.loads((ROOT/'config/installation_facts.json').read_text(encoding='utf-8'))
        with (ROOT/'docs/ha_inventory.csv').open(encoding='utf-8-sig',newline='') as f:
            self.ha_ids={r['entity_id'] for r in csv.DictReader(f)}
        self.lock=threading.RLock(); self.stop=threading.Event()
        self.cache={};self.http={};self.ha_states={};self.history=deque(maxlen=2200)
        self.mqtt_connected=False;self.mqtt_messages=0;self.retained_messages=0;self.reconnects=0
        self.last_mqtt=None;self.last_ha=None;self.ha_ok=False;self.status_ok=False
        self.status={};self.last_good=None;self.frame_at=None;self.http_at=None
        self.last_poll=None;self.started=utc();self.client=None
        self.errors={'mqtt':None,'http':None,'ha':None};self.http_polls=0
        self.token=os.environ.get('HA_TOKEN') or env_file(ROOT/'.env').get('HA_TOKEN','')
        self.auth_failures={};self.sessions={};self.auth_nonces={};self.control_tokens={}
        self.controls=ControlManager(self.control_read,self.publish_parameter_once,journal or ROOT/'state/commands.jsonl',enabled=controls_enabled)
    def control_read(self):
        if not self.health()['pdc_frames_advancing']:raise ControlError('Attendi letture recenti dalla PDC.')
        status=read_json(PDC+'/status');data=read_json(PDC+'/json')
        if status.get('mac','').upper()!='B0:CB:D8:D0:25:FF':raise ControlError('Identità della ETH01 diversa da quella prevista.')
        if status.get('firmware') not in ['2.0-WT32-ETH01-CZ-web1','2.0-WT32-ETH01-CZ-web2-buffer']:raise ControlError('Firmware della ETH01 non riconosciuto.')
        values={x['Name']:number(x.get('Value')) for x in data.get('heatpump',[]) if x.get('Name') in GUARDS}
        with urllib.request.urlopen(PDC+'/debug',timeout=6) as response:raw=response.read().decode()
        frame=bytes(int(x,16) for x in re.findall(r'\b[0-9a-fA-F]{2}\b',raw.replace('data:','')))
        if len(frame)!=203 or frame[:3]!=bytes.fromhex('71 C8 01') or sum(frame)%256:raise ControlError('Frame PDC non valido.')
        values['Buffer_Tank_Delta']=frame[59]-128
        if not 0<=values['Buffer_Tank_Delta']<=10:values['Buffer_Tank_Delta']=None
        return {'values':values,'frame_count':status.get('pdc_good_reads'),'fresh':self.health()['pdc_frames_advancing'],'firmware':status['firmware'],'listen_only':status.get('listen_only',True),'tx_enabled':status.get('tx_enabled',False)}
    def publish_parameter_once(self,command,payload):
        if not self.controls.enabled or command not in {f['command'] for f in FIELDS.values()}:raise PermissionError('Control command not enabled')
        with urllib.request.urlopen(PDC+'/settings',timeout=6) as response:html=response.read().decode()
        parser=Inputs();parser.feed(html);v=parser.values
        if v.get('mqtt_server')!='127.0.0.1' or int(v.get('mqtt_port','0'))!=1883 or v.get('mqtt_topic_base','').strip('/')!=BASE:raise ValueError('Broker endpoint changed')
        client=mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,client_id='aquarea-ui-edit-'+secrets.token_hex(4),clean_session=True,reconnect_on_failure=False)
        if v.get('mqtt_username'):client.username_pw_set(v['mqtt_username'],v.get('mqtt_password',''))
        ready=threading.Event();failure=[]
        def connected(c,u,flags,reason,properties):failure.append(reason.is_failure);ready.set()
        client.on_connect=connected
        try:
            client.connect('127.0.0.1',1883,keepalive=30);client.loop_start()
            if not ready.wait(8) or failure!=[False]:raise ConnectionError('Broker unavailable')
            info=client.publish(BASE+'/commands/'+command,payload,qos=0,retain=False)
            if info.rc!=mqtt.MQTT_ERR_SUCCESS:raise ConnectionError('Publication unavailable')
            info.wait_for_publish(timeout=8)
            if not info.is_published():raise TimeoutError('Publication outcome uncertain')
        finally:client.disconnect();client.loop_stop()
    def mqtt_start(self):
        # Reuse the existing settings only in memory; never log or persist broker credentials.
        try:
            with urllib.request.urlopen(PDC+'/settings',timeout=6) as response: html=response.read().decode('utf-8')
            parser=Inputs();parser.feed(html);v=parser.values
            broker=v.get('mqtt_server','')
            if broker!='127.0.0.1' or v.get('mqtt_topic_base','').strip('/')!=BASE: raise ValueError('MQTT endpoint differs')
            port=int(v.get('mqtt_port','1883'))
            if port!=1883: raise ValueError('MQTT port differs')
            client=ReadOnlyClient(mqtt.CallbackAPIVersion.VERSION2,client_id='aquarea-live-ro-'+secrets.token_hex(4),clean_session=True)
            if v.get('mqtt_username'):client.username_pw_set(v['mqtt_username'],v.get('mqtt_password',''))
            client.reconnect_delay_set(1,30)
            def connected(c,u,flags,reason,properties):
                with self.lock:
                    self.mqtt_connected=not reason.is_failure
                    self.errors['mqtt']='connection_refused' if reason.is_failure else None
                    self.reconnects+=1
                if not reason.is_failure:c.subscribe([(BASE+'/main/+',0),(BASE+'/stats',0),(BASE+'/LWT',0)])
            def disconnected(c,u,flags,reason,properties):
                with self.lock:self.mqtt_connected=False
            def message(c,u,msg):
                with self.lock:
                    self.mqtt_messages+=1;self.retained_messages+=int(msg.retain);self.last_mqtt=utc()
                    key=msg.topic.removeprefix(BASE+'/main/')
                    if key not in self.metadata:return
                    try: value=number(msg.payload.decode('utf-8'))
                    except UnicodeDecodeError:return
                    self.cache[key]={'value':value,'received_at':utc(),'monotonic':time.monotonic(),'retained':bool(msg.retain)}
            client.on_connect=connected;client.on_disconnect=disconnected;client.on_message=message
            self.client=client;client.connect_async(broker,port,keepalive=30);client.loop_start()
        except Exception:
            with self.lock:self.errors['mqtt']='connection_setup_failed';self.client=None
    def maintain_mqtt(self):
        while not self.stop.is_set():
            if self.client is None:self.mqtt_start()
            self.stop.wait(15)
    def start(self):
        threading.Thread(target=self.maintain_mqtt,daemon=True,name='mqtt-readonly').start()
        threading.Thread(target=self.poll_pdc,daemon=True,name='pdc-readonly').start()
        threading.Thread(target=self.poll_ha,daemon=True,name='ha-readonly').start()
    def poll_pdc(self):
        while not self.stop.is_set():
            try:
                status=read_json(PDC+'/status')
                payload=read_json(PDC+'/json')
                now=time.monotonic();stamp=utc();good=status.get('pdc_good_reads')
                values={r['Name']:number(r['Value']) for r in payload.get('heatpump',[]) if r.get('Name') in self.metadata}
                with self.lock:
                    if isinstance(good,int):
                        if self.last_good is not None and good>self.last_good:self.frame_at=now
                        elif self.last_good is not None and good<self.last_good:self.frame_at=None
                        self.last_good=good
                    self.status={k:status.get(k) for k in ['firmware','network','poll_seconds','pdc_good_reads','pdc_uart_ready','listen_only','tx_enabled']}
                    self.http=values;self.http_at=now;self.last_poll=stamp;self.status_ok=True;self.errors['http']=None;self.http_polls+=1
                    self.history.append({'received_at':stamp,'frame_fresh':self.frame_at is not None and now-self.frame_at<20,'values':values})
            except Exception:
                with self.lock:self.status_ok=False;self.errors['http']='upstream_unavailable'
            self.stop.wait(5)
    def poll_ha(self):
        while not self.stop.is_set():
            if self.token:
                try:
                    states=read_json(HA+'/api/states',self.token)
                    filtered={}
                    for state in states:
                        if state.get('entity_id') not in self.ha_ids:continue
                        attrs=state.get('attributes',{})
                        filtered[state['entity_id']]={k:state.get(k) for k in ['entity_id','state','last_updated','last_reported']}
                        filtered[state['entity_id']]['attributes']={k:attrs[k] for k in ['min','max','step','unit_of_measurement','temperature','current_temperature','hvac_modes','options','operation_mode'] if k in attrs}
                    with self.lock:self.ha_states=filtered;self.last_ha=utc();self.ha_ok=True;self.errors['ha']=None
                except Exception:
                    with self.lock:self.ha_ok=False;self.errors['ha']='auth_or_upstream_unavailable'
            else:
                with self.lock:self.errors['ha']='token_missing'
            self.stop.wait(10)
    def health(self):
        now=time.monotonic()
        with self.lock:
            fresh=bool(self.status_ok and self.frame_at is not None and now-self.frame_at<20 and self.http_at is not None and now-self.http_at<15)
            return {'started_at':self.started,'server_time':utc(),'mode':'supervised_control' if self.controls.enabled else 'read_only','mqtt_connected':self.mqtt_connected,
                    'mqtt_messages':self.mqtt_messages,'mqtt_retained_messages':self.retained_messages,'mqtt_connections':self.reconnects,
                    'last_mqtt_received_at':self.last_mqtt,'http_connected':self.status_ok,'http_polls':self.http_polls,
                    'last_http_received_at':self.last_poll,'pdc_frames_advancing':fresh,'pdc':self.status,
                    'ha_connected':self.ha_ok,'ha_entities_loaded':len(self.ha_states),'last_ha_received_at':self.last_ha,
                    'errors':dict(self.errors),'mqtt_publish_count':self.controls.publication_attempts,'ha_control_operations':0,'controls_enabled':self.controls.enabled,
                    'history_scope':'Current backend session only; bounded in-memory cache'}
    def snapshot(self):
        now=time.monotonic()
        with self.lock:
            health=self.health();records=[]
            for key,meta in self.metadata.items():
                item={k:copy.deepcopy(v) for k,v in meta.items() if k not in ['value','timestamp','received_at','quality','confidence','notes','live']}
                packet=self.cache.get(key);http_exists=key in self.http
                mqtt_live=bool(packet and not packet['retained'] and self.mqtt_connected and now-packet['monotonic']<30)
                if mqtt_live:
                    value=packet['value'];source=meta['source'];received=packet['received_at'];age=now-packet['monotonic']
                    reason='Recent non-retained MQTT reception; acquisition timestamp unavailable.'
                elif http_exists:
                    value=self.http[key];source=PDC+'/json · '+meta['top'];received=self.last_poll;age=now-self.http_at
                    reason='HTTP decoded cache read; freshness inferred from advancing valid-frame counter.'
                elif packet:
                    value=packet['value'];source=meta['source'];received=packet['received_at'];age=now-packet['monotonic']
                    reason='Retained or old MQTT value; original acquisition time unknown.'
                else:
                    value=None;source=meta['source'];received=None;age=None;reason='No current-session value received.'
                q='unavailable' if invalid(key,value,meta['unit']) else 'live' if health['pdc_frames_advancing'] and (mqtt_live or http_exists) else 'stale'
                item.update(value=value,source=source,topic=meta['source'],timestamp=received,received_at=received,
                            source_acquisition_timestamp=None,timestamp_meaning='ricezione del backend; non acquisizione sensore',
                            quality=q,confidence='medium' if q=='live' else 'low',quality_reason=reason,
                            age_seconds=round(age,2) if age is not None else None,live=q=='live',
                            mqtt_retained=packet['retained'] if packet else None,
                            synchronization_group=self.last_poll if http_exists and not mqtt_live and health['pdc_frames_advancing'] else None,
                            notes=[reason,'La freschezza del frame non certifica la validita fisica di ogni sensore.'])
                records.append(item)
            return {'mode':'live','server_time':utc(),'metrics':records,'health':health,'ha_entities':list(self.ha_states.values()),'topology':self.topology,'installation':self.installation}

LOGIN='''<!doctype html><html lang="it"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Aquarea · Accesso locale</title><link rel="stylesheet" href="/styles.css"><body><main style="margin:8vh auto;max-width:480px;padding:24px"><p class="eyebrow">AQUAREA · MONITORAGGIO LIVE</p><h1>Accesso locale</h1><p>Accedi al monitoraggio e ai parametri del tuo impianto. Ogni modifica richiede una conferma.</p><form method="post" action="/auth/login"><input type="hidden" name="csrf" value="__CSRF__"><label for="password">Password locale</label><input id="password" type="password" name="password" required autocomplete="current-password" style="width:100%;padding:12px;margin:12px 0 20px;background:#142735;color:white;border:1px solid #426072;border-radius:8px"><button type="submit">Apri dashboard</button></form></main></body></html>'''

def make_handler(bridge,password,port,public_host=None,embed_origin=None):
    hosts={f'127.0.0.1:{port}',f'localhost:{port}'}
    if public_host:
        if public_host!='127.0.0.1':raise ValueError('Public host not approved')
        hosts.add(f'{public_host}:{port}')
    if embed_origin not in [None,HA]:raise ValueError('Embedding origin not approved')
    origins={'http://'+host for host in hosts}
    ancestors=embed_origin or "'none'"
    digest=hashlib.sha256(password.encode()).digest()
    class Handler(BaseHTTPRequestHandler):
        protocol_version='HTTP/1.1'
        def log_message(self,*args):pass
        def host_ok(self):return self.headers.get('Host') in hosts
        def authenticated(self):
            try:
                cookies=SimpleCookie();cookies.load(self.headers.get('Cookie',''))
                sid=cookies.get('aquarea_session');token=sid.value if sid else ''
            except Exception:return False
            with bridge.lock:
                expires=bridge.sessions.get(token,0)
                if expires>time.monotonic():self.session_id=token;return True
                bridge.sessions.pop(token,None);return False
        def headers_out(self,code,kind,length=None,extra=None):
            self.send_response(code);self.send_header('Content-Type',kind);self.send_header('Cache-Control','no-store')
            if length is not None:self.send_header('Content-Length',str(length))
            self.send_header('X-Content-Type-Options','nosniff');self.send_header('Referrer-Policy','no-referrer')
            self.send_header('Content-Security-Policy',"default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; frame-ancestors "+ancestors+"; form-action 'self'")
            for k,v in (extra or {}).items():self.send_header(k,v)
            self.end_headers()
        def output(self,code,value,kind='application/json; charset=utf-8',extra=None):
            body=(json.dumps(value,ensure_ascii=False,allow_nan=False) if kind.startswith('application/json') else value).encode('utf-8')
            self.headers_out(code,kind,len(body),extra);self.wfile.write(body)
        def do_GET(self):
            path=urlsplit(self.path).path
            if not self.host_ok():return self.output(403,{'error':'host_not_allowed'})
            if path=='/healthz':return self.output(200,{'status':'running','controls_enabled':bridge.controls.enabled})
            if path=='/styles.css' or (path in ['/','/index.html'] and self.authenticated()):pass
            elif not self.authenticated():
                if path=='/':
                    nonce=secrets.token_urlsafe(32);now=time.monotonic()
                    with bridge.lock:
                        bridge.auth_nonces={n:e for n,e in bridge.auth_nonces.items() if e>now};bridge.auth_nonces[nonce]=now+300
                    return self.output(200,LOGIN.replace('__CSRF__',nonce),'text/html; charset=utf-8',{'Set-Cookie':f'aquarea_csrf={nonce}; HttpOnly; SameSite=Strict; Path=/; Max-Age=300'})
                return self.output(401,{'error':'authentication_required'})
            if path=='/api/health':return self.output(200,bridge.health())
            if path=='/api/metrics':return self.output(200,bridge.snapshot())
            if path=='/api/controls':
                with bridge.lock:csrf=bridge.control_tokens.setdefault(self.session_id,secrets.token_urlsafe(32))
                return self.output(200,dict(bridge.controls.catalog(),csrf=csrf))
            if path=='/api/topology':return self.output(200,bridge.topology)
            if path=='/api/discovery':return self.output(200,{'topics_observed':len(bridge.cache),'metadata_TOPs':len(bridge.metadata),'controls_enabled':bridge.controls.enabled})
            if path=='/api/history':
                with bridge.lock:history=list(bridge.history)[-720:]
                return self.output(200,{'scope':'current_session','samples':history})
            if path=='/api/events':
                self.headers_out(200,'text/event-stream; charset=utf-8',extra={'Connection':'close','X-Accel-Buffering':'no'})
                try:
                    for _ in range(150):
                        if bridge.stop.is_set() or not self.authenticated():break
                        data=json.dumps(bridge.snapshot(),ensure_ascii=False,allow_nan=False,separators=(',',':'))
                        self.wfile.write(('data: '+data+'\n\n').encode());self.wfile.flush()
                        if bridge.stop.wait(2):break
                except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError,TimeoutError):pass
                self.close_connection=True;return
            name=STATIC.get(path)
            if not name:return self.output(404,{'error':'not_found'})
            if name=='data.js':
                raw=(ROOT/'prototype/public/data.js').read_text(encoding='utf-8')
                data=json.loads(raw.removeprefix('window.AQUAREA_SNAPSHOT = ').rstrip().removesuffix(';'))
                data['live_enabled']=True
                return self.output(200,'window.AQUAREA_SNAPSHOT = '+json.dumps(data,ensure_ascii=False).replace('</','<\\/')+';','text/javascript; charset=utf-8')
            file=ROOT/'prototype/public'/name
            if not file.exists():return self.output(404,{'error':'not_found'})
            kind='text/css; charset=utf-8' if name.endswith('.css') else 'text/html; charset=utf-8' if name.endswith('.html') else 'text/javascript; charset=utf-8'
            return self.output(200,file.read_text(encoding='utf-8'),kind)
        def do_POST(self):
            if not self.host_ok():return self.output(403,{'error':'host_not_allowed'})
            if self.path in ['/api/controls/plan','/api/controls/execute']:
                if not self.authenticated():return self.output(401,{'error':'authentication_required'})
                if self.headers.get('Origin') not in origins:return self.output(403,{'error':'origin_not_allowed'})
                supplied=self.headers.get('X-Aquarea-CSRF','')
                with bridge.lock:csrf=bridge.control_tokens.get(self.session_id,'')
                if not csrf or not hmac.compare_digest(csrf,supplied):return self.output(403,{'error':'csrf_invalid'})
                if self.headers.get('Content-Type','').split(';')[0]!='application/json':return self.output(415,{'error':'json_required'})
                try:
                    length=int(self.headers.get('Content-Length','0'))
                    if not 0<length<=1024:return self.output(413,{'error':'invalid_request'})
                    fields=json.loads(self.rfile.read(length).decode('utf-8'))
                    if not isinstance(fields,dict):raise ValueError('Invalid request')
                except (ValueError,UnicodeError):return self.output(400,{'error':'invalid_request'})
                try:
                    if self.path.endswith('/plan'):
                        if set(fields)!={'key','value','expected'}:return self.output(400,{'error':'invalid_request'})
                        result=bridge.controls.prepare(self.session_id,fields['key'],fields['value'],fields['expected'])
                    else:
                        if set(fields)!={'id'} or not isinstance(fields['id'],str):return self.output(400,{'error':'invalid_request'})
                        result=bridge.controls.execute(self.session_id,fields['id'])
                    return self.output(200,result)
                except ControlError as exc:return self.output(exc.status,{'error':str(exc)})
                except (OSError,ValueError,TypeError):return self.output(503,{'error':'Comunicazione non disponibile. Controlla i valori prima di riprovare.'})
            if self.path!='/auth/login':return self.output(405,{'error':'read_only'})
            origin=self.headers.get('Origin')
            if origin and origin!='null' and origin not in origins:return self.output(403,{'error':'origin_not_allowed'})
            try:length=int(self.headers.get('Content-Length','0'))
            except ValueError:return self.output(400,{'error':'invalid_request'})
            if not 0<length<=1024:return self.output(413,{'error':'invalid_request'})
            from urllib.parse import parse_qs
            now=time.monotonic();attempts=[t for t in bridge.auth_failures.get(self.client_address[0],[]) if now-t<60]
            if len(attempts)>=10:return self.output(429,{'error':'try_later'})
            fields=parse_qs(self.rfile.read(length).decode('utf-8',errors='replace'))
            nonce=fields.get('csrf',[''])[0]
            cookies=SimpleCookie()
            try:cookies.load(self.headers.get('Cookie',''))
            except Exception:return self.output(403,{'error':'csrf_invalid'})
            csrf_cookie=cookies.get('aquarea_csrf')
            with bridge.lock:valid_nonce=bridge.auth_nonces.get(nonce,0)>now
            if not valid_nonce or not csrf_cookie or not hmac.compare_digest(nonce,csrf_cookie.value):return self.output(403,{'error':'csrf_invalid'})
            # The nonce + SameSite cookie also cover browsers that omit Origin.
            supplied=fields.get('password',[''])[0]
            if not hmac.compare_digest(hashlib.sha256(supplied.encode()).digest(),digest):
                bridge.auth_failures[self.client_address[0]]=attempts+[now]
                return self.output(401,{'error':'invalid_login'})
            sid=secrets.token_urlsafe(32)
            with bridge.lock:
                bridge.auth_nonces.pop(nonce,None)
                bridge.sessions={s:e for s,e in bridge.sessions.items() if e>now};bridge.sessions[sid]=now+43200
            return self.output(303,'','text/plain; charset=utf-8',{'Location':'/','Set-Cookie':f'aquarea_session={sid}; HttpOnly; SameSite=Strict; Path=/; Max-Age=43200'})
    return Handler

def local_password():
    if os.environ.get('LOCAL_UI_PASSWORD'):return os.environ['LOCAL_UI_PASSWORD']
    path=ROOT/'.env.live';values=env_file(path)
    if not values.get('LOCAL_UI_PASSWORD'):
        values['LOCAL_UI_PASSWORD']=secrets.token_urlsafe(28)
        path.write_text('LOCAL_UI_PASSWORD='+values['LOCAL_UI_PASSWORD']+'\n',encoding='utf-8')
        if os.name!='nt':path.chmod(0o600)
    return values['LOCAL_UI_PASSWORD']

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8767)
    parser.add_argument('--listen',choices=['127.0.0.1','0.0.0.0'],default='127.0.0.1')
    parser.add_argument('--public-host',choices=['127.0.0.1']);parser.add_argument('--embed-origin',choices=[HA]);args=parser.parse_args()
    bridge=Bridge(controls_enabled=os.environ.get('LOCAL_CONTROLS_ENABLED')=='1',journal=os.environ.get('CONTROL_JOURNAL_PATH'));password=local_password()
    server=ThreadingHTTPServer((args.listen,args.port),make_handler(bridge,password,args.port,args.public_host,args.embed_origin));server.daemon_threads=True
    bridge.start()
    print(f'Aquarea live dashboard: http://127.0.0.1:{args.port}/ (local authentication enabled; controls={bridge.controls.enabled})',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:
        bridge.stop.set()
        if bridge.client:bridge.client.disconnect();bridge.client.loop_stop()
        server.server_close()
