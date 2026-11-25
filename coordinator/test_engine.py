import asyncio, copy, importlib, json, os, tempfile, unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import AsyncMock,patch
from engine import Engine,defaults,effective,schedule,validate

NOW=datetime(2026,11,19,12,tzinfo=timezone.utc)
ROOMS=json.loads(Path(__file__).with_name('entity-map.test.json').read_text())['rooms']
def readings(value=19.5):return {k:{'temperature':value,'age':10,'unit':'°C','sample_at':'a'} for k in ROOMS}

class EngineTests(unittest.TestCase):
    def setUp(self):
        self.s=defaults();self.s.update(master=True,schedule_enabled=False);self.e=Engine();self.r=readings()
    def run_plan(self,seconds=181):
        if not self.e.memory:self.e.evaluate(self.s,ROOMS,self.r,NOW)
        return self.e.evaluate(self.s,ROOMS,self.r,NOW+timedelta(seconds=seconds))
    def test_master_off_and_all_seasons(self):
        for master,season in [(False,'winter'),(True,'summer'),(True,'off')]:
            self.s.update(master=master,season=season);self.assertFalse(self.run_plan()['active_requests'])
    def test_disarmed_no_demands(self):
        self.assertEqual(self.e.evaluate(self.s,ROOMS,readings(10),NOW,False)['active_requests'],0)
    def test_week_is_independent_and_48_slots(self):
        self.s['week'][0][0]=1;self.assertEqual(self.s['week'][1][0],0);validate(self.s)
        self.s['week'][1].pop()
        with self.assertRaises(ValueError):validate(self.s)
    def test_schedule_off_uses_manual_without_turning_off(self):
        self.s['floors']['day']['setpoint']=21;self.assertEqual(effective(self.s,'day',NOW),21);self.assertTrue(self.run_plan()['floors']['day']['active'])
    def test_dst_spring_and_fall(self):
        for now in [datetime(2026,3,29,0,45,tzinfo=timezone.utc),datetime(2026,10,25,0,45,tzinfo=timezone.utc),datetime(2026,10,25,1,15,tzinfo=timezone.utc)]:
            z=schedule(self.s,now);self.assertIn(z['profile'],['eco','comfort']);self.assertGreater(datetime.fromisoformat(z['next']['at']),now)
    def test_isolated_wait_max_20min(self):
        self.r=readings(20.5);self.r['living']['temperature']=19.5
        self.assertFalse(self.run_plan(1199)['rooms']['living']['desired']);self.assertTrue(self.run_plan(1200)['rooms']['living']['desired'])
    def test_urgent_after_minimum_off(self):
        self.r=readings(20.5);self.r['living']['temperature']=19
        self.assertTrue(self.run_plan(181)['rooms']['living']['desired'])
    def test_grouping_and_no_overheat(self):
        p=self.run_plan();self.assertTrue(p['rooms']['living']['desired']);self.assertTrue(p['rooms']['kitchen']['desired'])
        self.r['living'].update(temperature=20.1,sample_at='b');p=self.run_plan(200);self.assertFalse(p['rooms']['living']['desired'])
    def test_basement_room_independent(self):
        self.r=readings(21);self.r['basement_room']['temperature']=19.5
        p=self.run_plan();self.assertTrue(p['rooms']['basement_room']['desired']);self.assertFalse(p['floors']['night']['active']);self.assertFalse(p['floors']['day']['active'])
    def test_disabled_floor_closes_bath(self):
        self.s['floors']['night']['enabled']=False;p=self.run_plan();self.assertFalse(any(x['desired'] for x in p['rooms'].values() if x['floor']=='night'))
    def test_bath_no_autonomous_request(self):
        self.r=readings(21);self.r['bath_night']['temperature']=10;p=self.run_plan();self.assertFalse(p['rooms']['bath_night']['desired'])
    def test_bath_day_follows_only_kitchen(self):
        self.r=readings(21);self.r['living']['temperature']=19;p=self.run_plan();self.assertFalse(p['rooms']['bath_day']['desired'])
        self.r['kitchen'].update(temperature=18,sample_at='b');p=self.run_plan(1300);self.assertTrue(p['rooms']['bath_day']['desired'])
    def test_bad_sensor_is_safe(self):
        for val in [None,float('nan'),float('inf'),-40,80]:
            self.r['living']['temperature']=val;p=self.run_plan();self.assertFalse(p['rooms']['living']['desired']);self.assertFalse(p['rooms']['living']['valid'])
    def test_stale_and_units(self):
        self.r['living']['age']=14401;self.r['kitchen']['unit']='°F';p=self.run_plan();self.assertFalse(p['rooms']['living']['valid']);self.assertFalse(p['rooms']['kitchen']['valid'])
    def test_unchanged_report_not_repeatedly_filtered(self):
        self.run_plan();self.r['living'].update(temperature=19,sample_at='b');p=self.run_plan(190);a=p['rooms']['living']['filtered_temperature'];p=self.run_plan(200);self.assertEqual(p['rooms']['living']['filtered_temperature'],a)
    def test_parameter_coherence(self):
        self.s['params']['ordinary']=0.9
        with self.assertRaises(ValueError):validate(self.s)
    def test_unconfirmed_room_never_opens(self):
        rooms=copy.deepcopy(ROOMS);rooms['living']['confirmed']=False
        p=self.e.evaluate(self.s,rooms,readings(10),NOW+timedelta(seconds=1000));self.assertFalse(p['rooms']['living']['desired'])
    def test_minimum_on_unless_raw_target_reached(self):
        self.run_plan();self.r['living'].update(temperature=19.99,sample_at='b');p=self.run_plan(200);self.assertTrue(p['rooms']['living']['desired'])
        self.r['living'].update(temperature=20,sample_at='c');p=self.run_plan(210);self.assertFalse(p['rooms']['living']['desired'])
    def test_minimum_off_after_close(self):
        self.run_plan();self.r['living'].update(temperature=20,sample_at='b');self.run_plan(200);self.r['living'].update(temperature=18,sample_at='c');self.assertFalse(self.run_plan(210)['rooms']['living']['desired'])
    def test_filter_never_reopens_raw_temperature_at_target(self):
        self.r=readings(17);self.run_plan();self.r['living'].update(temperature=20,sample_at='b');p=self.run_plan(190);self.assertFalse(p['rooms']['living']['desired']);self.assertFalse(p['rooms']['living']['request'])
    def test_numeric_inputs_are_normalized(self):
        self.s['params']['ordinary']='0.4';self.s['comfort']='21';validate(self.s);self.assertEqual(self.s['params']['ordinary'],.4);self.assertEqual(self.s['comfort'],21)

os.environ.setdefault('HA_TOKEN','unit-test-placeholder')
from daemon import Coordinator

class SafetyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)
        mapping=json.loads(Path(__file__).with_name('entity-map.test.json').read_text());mapping['admin_user_ids']=['admin']
        (self.path/'entity-map.json').write_text(json.dumps(mapping));self.patcher=patch('daemon.DATA',self.path);self.patcher.start();self.c=Coordinator();self.c.connected=True;self.c.api=AsyncMock(return_value={})
    async def asyncTearDown(self):self.patcher.stop();self.temp.cleanup()
    async def test_disarmed_never_calls_physical_services(self):
        self.c.settings['master']=True;await self.c.tick()
        calls=[x.args[1] for x in self.c.api.call_args_list];self.assertFalse(any('services/' in x for x in calls));self.assertEqual(self.c.actual_commands,0)
    async def test_physical_gate_at_every_call(self):
        with self.assertRaises(ValueError):await self.c.physical('switch','turn_on',{},'switch.any','on')
        self.c.api.assert_not_called()
    async def test_unauthorized_event_cannot_change_settings(self):
        await self.c.command({'context':{'user_id':'intruder'},'data':{'command':'master','payload':{'value':True}}});self.assertFalse(self.c.settings['master'])
    async def test_invalid_event_does_not_persist(self):
        await self.c.command({'context':{'user_id':'admin'},'data':{'command':'parameter','request_id':'x','payload':{'key':'ordinary','value':99}}});self.assertFalse(self.c.ack['ok']);self.assertEqual(self.c.settings['params']['ordinary'],.4)
    async def test_settings_persistence_and_physical_zero(self):
        await self.c.command({'context':{'user_id':'admin'},'data':{'command':'temperature','request_id':'y','payload':{'key':'comfort','value':21}}});self.assertTrue(self.c.ack['ok']);self.assertEqual(Coordinator().settings['comfort'],21);self.assertEqual(self.c.actual_commands,0)
    async def test_no_admin_ids_in_snapshot(self):
        await self.c.tick();snapshot=self.c.api.call_args_list[0].args[2];self.assertNotIn('admin_user_ids',snapshot['attributes']['mapping'])
    async def test_acs_blocked_before_authorization(self):
        entity=self.c.mapping['telemetry']['dhw_target'];self.c.states[entity]={'state':'50','attributes':{'min':40,'max':65}}
        await self.c.command({'context':{'user_id':'admin'},'data':{'command':'dhw','payload':{'profile':'eco'}}});self.assertFalse(self.c.ack['ok']);self.assertEqual(self.c.actual_commands,0)
    async def test_mqtt_commands_never_retained(self):
        self.c.physical=AsyncMock();await self.c.mqtt_set('SetDHWTemp',40,'number.target','40');self.assertIs(self.c.physical.call_args.args[2]['retain'],False)
    async def test_watchdog_disarmed_has_no_http_side_effects(self):
        import supervise
        (self.path/'commissioning.json').write_text(json.dumps({'user_authorized_physical_commands':False}))
        with patch.object(supervise,'DATA',self.path),patch.object(supervise.aiohttp,'ClientSession') as http:
            self.assertFalse(await supervise.suspend_climate());http.assert_not_called()
    async def test_retired_thermostats_absent_are_accepted_but_reappearing_heat_blocks(self):
        label='Vecchi termostati ancora attivi o non disponibili'
        self.c.mapping.pop('retired_climates',None)
        self.assertIn(label,self.c.blockers());self.c.mapping['retired_climates']=self.c.mapping['legacy_climates'][:]
        self.assertNotIn(label,self.c.blockers())
        entity=self.c.mapping['legacy_climates'][0];self.c.states[entity]={'state':'heat'};self.assertIn(label,self.c.blockers())
        self.c.states[entity]={'state':'unavailable'};self.assertIn(label,self.c.blockers())
        self.c.states[entity]={'state':'off'};self.assertNotIn(label,self.c.blockers())
    async def test_buffer_policy_requires_both_physical_observations(self):
        from daemon import confirmed_buffer_path
        gate={'hydraulic_layout':'buffer_secondary_bypass','safe_closure_with_pump':True,'flow_monitor_scope':'primary_only'}
        self.assertFalse(confirmed_buffer_path(gate));gate['hydraulic_path_verified']=True;self.assertFalse(confirmed_buffer_path(gate))
        gate['secondary_pump_verified']=True;self.assertTrue(confirmed_buffer_path(gate));gate['flow_monitor_scope']='unknown';self.assertFalse(confirmed_buffer_path(gate))
    async def test_verified_buffer_does_not_invent_secondary_liters(self):
        gate={'control_enabled':True,'user_authorized_physical_commands':True,'old_system_excluded':True,'bath_contacts_verified':True,'room_mapping_verified':True,'panasonic_control_verified':True,'hydraulics_verified':False,
              'hydraulic_layout':'buffer_secondary_bypass','hydraulic_path_verified':True,'secondary_pump_verified':True,'safe_closure_with_pump':True,'flow_monitor_scope':'primary_only','minimum_flow_lpm':None}
        (self.path/'commissioning.json').write_text(json.dumps(gate));self.c.mapping['retired_climates']=self.c.mapping['legacy_climates'][:]
        for id in self.c.mapping['competing_automations']:self.c.states[id]={'state':'off'}
        t=self.c.mapping['telemetry'];self.c.states[t['online']]={'state':'on'};self.c.states[t['error']]={'state':'No error'}
        self.c.states[t['heartbeat']]={'state':'10','last_changed':datetime.now(timezone.utc).isoformat()}
        self.assertEqual(self.c.blockers(),[])
        gate['secondary_pump_verified']=False;(self.path/'commissioning.json').write_text(json.dumps(gate))
        self.assertIn('Portata minima di progetto non configurata',self.c.blockers())
    async def test_only_safe_off_can_bypass_missing_primary_telemetry(self):
        gate={'hydraulic_layout':'buffer_secondary_bypass','hydraulic_path_verified':True,'secondary_pump_verified':True,'safe_closure_with_pump':True,'flow_monitor_scope':'primary_only'}
        self.c.attest=lambda:gate;self.c.blockers=lambda:['Telemetria HeishaMon non aggiornata']
        relay=self.c.mapping['rooms']['basement_room']['relay'];self.c.states[relay]={'state':'on'}
        async def api(method,path,data=None):
            if path=='services/switch/turn_off':self.c.states[relay]={'state':'off'}
            return []
        self.c.api=AsyncMock(side_effect=api)
        await self.c.physical('switch','turn_off',{'entity_id':relay},relay,'off')
        self.assertEqual(self.c.state(relay),'off')
        with self.assertRaises(ValueError):await self.c.physical('switch','turn_on',{'entity_id':relay},relay,'on')
        self.c.blockers=lambda:['Controllo reale non ancora abilitato','Telemetria HeishaMon non aggiornata']
        with self.assertRaises(ValueError):await self.c.physical('switch','turn_off',{'entity_id':relay},relay,'off')
    async def test_master_off_can_close_to_verified_bypass_without_pump_deadlock(self):
        gate={'hydraulic_layout':'buffer_secondary_bypass','hydraulic_path_verified':True,'secondary_pump_verified':True,'safe_closure_with_pump':True,'flow_monitor_scope':'primary_only'}
        self.c.attest=lambda:gate;self.c.blockers=lambda:[];t=self.c.mapping['telemetry']
        self.c.states.update({t['load_shed']:{'state':'off'},t['defrost']:{'state':'off'},t['sterilization']:{'state':'off'},t['pump2']:{'state':'on'}})
        self.c.physical=AsyncMock();self.c.mqtt_set=AsyncMock();self.c.settings['master']=False
        await self.c.reconcile({'rooms':{}})
        self.assertEqual(self.c.physical.await_count,7)
        self.c.mqtt_set.assert_awaited_with('SetOperationMode',3,t['mode'],'DHW only')
    async def test_unverified_summer_is_rejected_while_live(self):
        (self.path/'commissioning.json').write_text(json.dumps({'control_enabled':True,'fan_coils_verified':False}))
        await self.c.command({'context':{'user_id':'admin'},'data':{'command':'season','payload':{'value':'summer'}}})
        self.assertFalse(self.c.ack['ok']);self.assertEqual(self.c.settings['season'],'winter')

if __name__=='__main__':unittest.main(verbosity=2)
