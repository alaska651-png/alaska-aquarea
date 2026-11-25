"""Read-only adapters adapted from the checkpoint live/backend.py.

No controls module, settings-page scraping, HA service calls or MQTT publication.
"""
import asyncio, ipaddress, json, secrets, socket, time
from urllib.parse import urlsplit


def local_url(url):
    u = urlsplit(url)
    if u.scheme not in ('http', 'https') or not u.hostname or u.username or u.password or u.query or u.fragment or u.path not in ('', '/'):
        raise ValueError('An upstream must be a local HTTP(S) origin')
    for info in socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == 'https' else 80), type=socket.SOCK_STREAM):
        addr = ipaddress.ip_address(info[4][0])
        if not (addr.is_private or addr.is_loopback) or addr.is_unspecified or addr.is_multicast:
            raise ValueError('Public upstreams are forbidden')
    return url.rstrip('/')


def readonly_client(base):
    # Same fail-closed publish()/will_set() guards as Digital Twin ReadOnlyClient.
    class ReadOnlyClient(base):
        def publish(self, *args, **kwargs):
            raise PermissionError('Publishing is disabled')

        def will_set(self, *args, **kwargs):
            raise PermissionError('LWT publishing is disabled')
    return ReadOnlyClient


class Collector:
    def __init__(self, model, cfg, env):
        self.model, self.cfg, self.env = model, cfg, env
        self.client = None

    async def get(self, session, origin, path, token=None):
        headers = {'Authorization': 'Bearer ' + token} if token else {}
        async with session.get(origin + path, headers=headers, allow_redirects=False) as response:
            response.raise_for_status()
            if response.status != 200:
                raise ValueError('Unexpected upstream response')
            return await response.json()

    async def poll_pdc(self, session):
        origin = local_url(self.env['HEISHAMON_URL']) if self.env.get('HEISHAMON_URL') else None
        while True:
            if origin:
                try:
                    status = await self.get(session, origin, '/status')
                    payload = await self.get(session, origin, '/json')
                    self.model.pdc(status, payload, time.time())
                except Exception:
                    self.model.fail('heishamon')
            await asyncio.sleep(5)

    async def poll_ha(self, session):
        origin = local_url(self.env['HA_URL']) if self.env.get('HA_URL') else None
        while True:
            if origin and self.env.get('HA_TOKEN'):
                try:
                    states = await self.get(session, origin, '/api/states', self.env['HA_TOKEN'])
                    self.model.ha(states, time.time())
                except Exception:
                    self.model.fail('home_assistant')
            await asyncio.sleep(10)

    async def events(self, session):
        # Coordinator already publishes these events; subscribe, never fire events.
        if not self.env.get('HA_URL') or not self.env.get('HA_TOKEN'):
            return
        origin = local_url(self.env['HA_URL'])
        while True:
            try:
                async with session.ws_connect(origin + '/api/websocket', heartbeat=30) as ws:
                    if (await ws.receive_json()).get('type') != 'auth_required':
                        raise ValueError('HA handshake')
                    await ws.send_json({'type': 'auth', 'access_token': self.env['HA_TOKEN']})
                    if (await ws.receive_json()).get('type') != 'auth_ok':
                        raise ValueError('HA authentication')
                    await ws.send_json({'id': 1, 'type': 'subscribe_events', 'event_type': 'coordinatore_climatico_status'})
                    async for msg in ws:
                        if msg.type.name != 'TEXT':
                            break
                        event = json.loads(msg.data)
                        if event.get('type') == 'event' and event.get('event', {}).get('event_type') == 'coordinatore_climatico_status':
                            self.model.coordinator(event['event'].get('data', {}), time.time())
            except Exception:
                pass  # Never log payloads, tokens, room names or raw exceptions.
            await asyncio.sleep(5)

    def mqtt_start(self, loop):
        host = self.env.get('MQTT_HOST')
        if not host:
            return
        local_url('http://' + host)
        import paho.mqtt.client as mqtt
        prefix = self.cfg.get('mqtt_prefix', 'panasonic_heat_pump').strip('/')
        if not prefix or any(c in prefix for c in '#+'):
            raise ValueError('Invalid MQTT prefix')
        cls = readonly_client(mqtt.Client)
        client = cls(mqtt.CallbackAPIVersion.VERSION2, client_id='aquarea-common-ro-' + secrets.token_hex(6), clean_session=True)
        if self.env.get('MQTT_USERNAME'):
            client.username_pw_set(self.env['MQTT_USERNAME'], self.env.get('MQTT_PASSWORD', ''))
        client.reconnect_delay_set(1, 30)
        def connected(c, userdata, flags, reason, properties):
            if not reason.is_failure:
                c.subscribe([(prefix + '/main/+', 0), (prefix + '/stats', 0), (prefix + '/LWT', 0)])
                loop.call_soon_threadsafe(self.model.mqtt_link, True)
        def disconnected(*args):
            loop.call_soon_threadsafe(self.model.mqtt_link, False)
        def received(c, userdata, msg):
            if len(msg.payload) > 65536:
                return
            try:
                value = msg.payload.decode('utf-8')
            except UnicodeError:
                return
            loop.call_soon_threadsafe(self.model.mqtt_message, msg.topic, value, bool(msg.retain), time.time())
        client.on_connect, client.on_disconnect, client.on_message = connected, disconnected, received
        client.connect_async(host, int(self.env.get('MQTT_PORT', '1883')), keepalive=30)
        client.loop_start()
        self.client = client

    async def run(self):
        import aiohttp
        self.mqtt_start(asyncio.get_running_loop())
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=7), trust_env=False) as session:
                await asyncio.gather(self.poll_pdc(session), self.poll_ha(session), self.events(session))
        finally:
            if self.client:
                self.client.disconnect()
                self.client.loop_stop()
