"""HA-native identity gateway. No permanent token or credentials are passed to the iframe."""
import hashlib,hmac,secrets,time
from urllib.parse import urlsplit
from aiohttp import web,ClientTimeout
import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.components.http import HomeAssistantView
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.auth.permissions.const import POLICY_CONTROL
DOMAIN='aquarea_clima';PREFIX='/api/aquarea_clima';COOKIE='aquarea_ha_session'
READ={'','styles.css','app.js','cop.js','diagram.js','catalog.json','reuse/charts.js','reuse/data.js','reuse/ha-data.js','reuse/climate-data.mjs','api/snapshot','api/control','api/mode','api/history','api/schedule','api/cop/analysis','api/cop/snapshot','api/cop/mapping','api/cop/export.csv'}
WRITE={'api/control/plan','api/control/execute','api/mode/plan','api/mode/execute'}
POWER_ENTITY='switch.panasonic_heat_pump_main_heatpump_state'

class Gateway:
    def __init__(self,hass,secret):self.hass=hass;self.secret=secret;self.sessions={};self.grants={}
    def clean(self):
        now=time.monotonic()
        self.sessions={k:v for k,v in self.sessions.items() if v['expires']>now};self.grants={k:v for k,v in self.grants.items() if v['expires']>now}
    def mint(self,user,refresh_id,origin):
        parsed=urlsplit(origin)
        if parsed.scheme not in ('http','https') or not parsed.netloc or parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password or len(origin)>300:raise ValueError('Invalid HA origin')
        refresh=self.hass.auth.async_get_refresh_token(refresh_id)
        if refresh is None or refresh.user.id!=user.id or not user.is_active:raise ValueError('HA session not active')
        self.clean()
        sid=next((sid for sid,s in self.sessions.items() if s['user']==user.id and s['refresh']==refresh_id and s['origin']==origin),None)
        if sid is None:
            if len(self.sessions)>=500:raise ValueError('Session limit')
            sid=secrets.token_urlsafe(40)
        self.sessions[sid]={'user':user.id,'refresh':refresh_id,'origin':origin,'expires':time.monotonic()+180}
        if len(self.grants)>=1000:raise ValueError('Grant limit')
        ticket=secrets.token_urlsafe(40);self.grants[ticket]={'sid':sid,'expires':time.monotonic()+15}
        return {'url':PREFIX+'/open/'+ticket,'tag':hashlib.sha256(sid.encode()).hexdigest()[:16],'expires_in':180}
    async def identity(self,sid):
        session=self.sessions.get(sid)
        if not session or session['expires']<=time.monotonic():raise web.HTTPUnauthorized(text='Apri il sinottico dalla dashboard Home Assistant')
        refresh=self.hass.auth.async_get_refresh_token(session['refresh'])
        if refresh is None or refresh.user.id!=session['user'] or not refresh.user.is_active:
            self.sessions.pop(sid,None);raise web.HTTPUnauthorized(text='Sessione Home Assistant terminata')
        return session,refresh.user

class OpenView(HomeAssistantView):
    url=PREFIX+'/open/{ticket}';name='api:aquarea_clima:open';requires_auth=False
    async def get(self,request,ticket):
        gateway=request.app['hass'].data[DOMAIN];grant=gateway.grants.pop(ticket,None)
        if not grant or grant['expires']<=time.monotonic():raise web.HTTPUnauthorized(text='Collegamento temporaneo scaduto')
        session,user=await gateway.identity(grant['sid'])
        response=web.HTTPSeeOther(PREFIX+'/ui/')
        response.set_cookie(COOKIE,grant['sid'],httponly=True,secure=session['origin'].startswith('https://'),samesite='Strict',path=PREFIX+'/',max_age=43200)
        response.headers.update({'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})
        return response

class ProxyView(HomeAssistantView):
    url=PREFIX+'/ui/';extra_urls=[PREFIX+'/ui/{path:.*}'];name='api:aquarea_clima:ui';requires_auth=False
    async def get(self,request,path=''):return await self.proxy(request,path)
    async def head(self,request,path=''):return await self.proxy(request,path)
    async def post(self,request,path=''):return await self.proxy(request,path)
    async def proxy(self,request,path):
        gateway=request.app['hass'].data[DOMAIN];sid=request.cookies.get(COOKIE,'');session,user=await gateway.identity(sid)
        if path not in (WRITE if request.method=='POST' else READ):raise web.HTTPNotFound()
        if request.method=='POST':
            if request.headers.get('Origin')!=session['origin']:raise web.HTTPForbidden(text='Origine non consentita')
            if not user.permissions.check_entity(POWER_ENTITY,POLICY_CONTROL):raise web.HTTPForbidden(text='Permessi Home Assistant insufficienti')
            if request.content_length is None or request.content_length>4096:raise web.HTTPRequestEntityTooLarge(max_size=4096,actual_size=request.content_length or 0)
        body=await request.read() if request.method=='POST' else None
        headers={'X-Aquarea-Bridge':gateway.secret,'X-Aquarea-User':user.id,'X-Aquarea-Session':sid,'Origin':'http://127.0.0.1:8769','Accept-Encoding':'identity'}
        for key in ('Content-Type','X-Aquarea-CSRF','Range'):
            if key in request.headers:headers[key]=request.headers[key]
        try:
            http=async_get_clientsession(gateway.hass)
            async with http.request(request.method,'http://127.0.0.1:8769/'+path,params=request.query,headers=headers,data=body,allow_redirects=False,timeout=ClientTimeout(total=180,connect=3)) as upstream:
                allowed={'Content-Type','Content-Disposition','Content-Range','Accept-Ranges'}
                response=web.StreamResponse(status=upstream.status,headers={k:v for k,v in upstream.headers.items() if k in allowed})
                response.headers.update({'Cache-Control':'no-store','Referrer-Policy':'no-referrer','X-Content-Type-Options':'nosniff','X-Frame-Options':'SAMEORIGIN','Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'none'; frame-ancestors 'self'"})
                await response.prepare(request)
                async for chunk in upstream.content.iter_chunked(65536):await response.write(chunk)
                await response.write_eof();return response
        except (OSError,TimeoutError):raise web.HTTPServiceUnavailable(text='Sinottico temporaneamente non disponibile. Nessun reinvio automatico dei comandi.')

@websocket_api.websocket_command({vol.Required('type'):DOMAIN+'/session',vol.Required('origin'):str})
@websocket_api.async_response
async def session_command(hass,connection,msg):
    try:result=hass.data[DOMAIN].mint(connection.user,connection.refresh_token_id,msg['origin'])
    except (ValueError,KeyError):connection.send_error(msg['id'],'unauthorized','Sessione Home Assistant non disponibile');return
    connection.send_result(msg['id'],result)

async def async_setup_entry(hass,entry):
    if DOMAIN not in hass.data:
        hass.http.register_view(OpenView());hass.http.register_view(ProxyView());websocket_api.async_register_command(hass,session_command)
    hass.data[DOMAIN]=Gateway(hass,entry.data['bridge_secret']);return True
async def async_unload_entry(hass,entry):
    gateway=hass.data.get(DOMAIN)
    if gateway:gateway.sessions.clear();gateway.grants.clear()
    return True
