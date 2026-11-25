"""Restart a stalled engine, with a separate, commissioned HA safety path."""
import asyncio,datetime as dt,json,logging,os,signal,subprocess,time
from pathlib import Path
import aiohttp
from daemon import confirmed_buffer_path

DATA=Path(os.environ.get('CC_DATA','/data'));BASE=Path(__file__).parent
LOG=logging.getLogger('supervisore');stopping=False

async def suspend_climate():
    gate=json.loads((DATA/'commissioning.json').read_text())
    required=['control_enabled','user_authorized_physical_commands','old_system_excluded','bath_contacts_verified','room_mapping_verified','panasonic_control_verified','safe_closure_with_pump']
    if not all(gate.get(k) for k in required):return False
    if not gate.get('hydraulics_verified') and not confirmed_buffer_path(gate):return False
    mapping=json.loads((DATA/'entity-map.json').read_text());t=mapping['telemetry'];url=os.environ['HA_URL'].rstrip('/')
    async with aiohttp.ClientSession(headers={'Authorization':'Bearer '+os.environ['HA_TOKEN']},timeout=aiohttp.ClientTimeout(total=10)) as s:
        async with s.get(url+'/api/states') as r:r.raise_for_status();states={x['entity_id']:x for x in await r.json()}
        def value(id):return states.get(id,{}).get('state')
        retired=set(mapping.get('retired_climates',[]))
        if any(value(id)!='off' and not (id in retired and value(id) is None) for id in mapping['legacy_climates']):return False
        if any(value(id)!='off' for id in mapping['competing_automations']):return False
        if value(t['defrost'])!='off' or value(t['sterilization'])!='off' or value(t['load_shed'])!='off':return False
        heart=states.get(t['heartbeat'],{}).get('last_changed')
        if not heart or time.time()-dt.datetime.fromisoformat(heart).timestamp()>120:return False
        if value(t['mode'])=='DHW only':return False
        payload={'topic':mapping['mqtt_prefix']+'commands/SetOperationMode','payload':'3','qos':0,'retain':False}
        async with s.post(url+'/api/services/mqtt/publish',json=payload) as r:r.raise_for_status();await r.read()
        with (DATA/'decisions.jsonl').open('a') as f:f.write(json.dumps({'at':dt.datetime.now(dt.timezone.utc).isoformat(),'kind':'watchdog_physical_command','message':'Motore fermo: richiesta DHW only; valvole lasciate disponibili'})+'\n')
        LOG.warning('Watchdog requested DHW only; physical valve paths retained')
        return True

def shutdown(signum,frame):
    global stopping
    stopping=True

def main():
    signal.signal(signal.SIGTERM,shutdown);signal.signal(signal.SIGINT,shutdown)
    child=None;birth=0;last_attempt=0
    try:
        while not stopping:
            if child is None:
                child=subprocess.Popen(['python',str(BASE/'daemon.py')]);birth=time.time()
            try:last=json.loads((DATA/'heartbeat.json').read_text()).get('at',0)
            except (OSError,ValueError):last=0
            stale=time.time()-max(last,birth)>90
            if child.poll() is not None or stale:
                if time.time()-last_attempt>120:
                    last_attempt=time.time()
                    try:asyncio.run(suspend_climate())
                    except Exception as e:LOG.error('Watchdog could not confirm HA safety request: %s',type(e).__name__)
                if child.poll() is None:child.terminate()
                try:child.wait(timeout=10)
                except subprocess.TimeoutExpired:child.kill();child.wait()
                child=None;time.sleep(3)
            time.sleep(2)
    finally:
        if child and child.poll() is None:
            child.terminate()
            try:child.wait(timeout=10)
            except subprocess.TimeoutExpired:child.kill();child.wait()

if __name__=='__main__':
    logging.basicConfig(level=logging.INFO);main()
