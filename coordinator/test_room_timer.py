import asyncio,copy,json,tempfile,unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock,patch
from test_engine import NOW,ROOMS,readings
from engine import Engine,defaults
from daemon import Coordinator
class TimerEngineTests(unittest.TestCase):
 def setUp(self):
  self.s=defaults();self.s.update(master=True,schedule_enabled=False);self.e=Engine();self.r=readings(22);self.t={};self.plan(0)
 def plan(self,t=181,allowed=True):return self.e.evaluate(self.s,ROOMS,self.r,NOW+timedelta(seconds=t),allowed,self.t)
 def start(self,key='living',minutes=30):self.t[key]=NOW.timestamp()+181+minutes*60;return self.plan()
 def test_hot_room_manual_and_no_timer_chatter(self):
  p=self.start();self.assertTrue(p['rooms']['living']['desired']);on=self.e.memory['living']['on_since'];p=self.plan(190);self.assertTrue(p['rooms']['living']['desired']);self.assertEqual(self.e.memory['living']['on_since'],on);self.assertFalse(p['rooms']['kitchen']['desired'])
 def test_pause_minimum(self):
  self.t={'living':NOW.timestamp()+900};self.assertFalse(self.plan(100)['rooms']['living']['desired']);self.assertTrue(self.plan(180)['rooms']['living']['desired'])
 def test_all_durations_expire_to_auto_off(self):
  for minutes in [15,30,45,60,90,120]:
   self.setUp();self.start(minutes=minutes);self.assertFalse(self.plan(181+minutes*60)['rooms']['living']['desired'])
 def test_expiry_returns_auto_on(self):
  self.r=readings(18);self.e=Engine();self.plan(0);self.start('basement_room',15);self.assertTrue(self.plan(1081)['rooms']['basement_room']['desired']);self.assertNotIn('manual_until',self.plan(1082)['rooms']['basement_room'])
 def test_mean_single_override_and_other_rooms_unchanged(self):
  self.s['floors']['night']['mean_enabled']=True;self.start('bedroom_2');p=self.plan(190);self.assertTrue(p['rooms']['bedroom_2']['desired']);self.assertFalse(p['rooms']['bedroom_3']['desired']);self.assertFalse(p['rooms']['bath_night']['desired']);self.assertFalse(p['rooms']['bedroom_2']['mean_linked'])
 def test_shared_kitchen_bath(self):
  p=self.start('kitchen');self.assertTrue(p['rooms']['kitchen']['desired']);self.assertTrue(p['rooms']['bath_day']['desired']);self.assertEqual(p['rooms']['bath_day']['manual_room'],'kitchen')
 def test_safety_and_disabled_gates(self):
  for case in ['master','summer','floor','sensor','allowed']:
   self.setUp();self.start()
   if case=='master':self.s['master']=False
   if case=='summer':self.s['season']='summer'
   if case=='floor':self.s['floors']['day']['enabled']=False
   if case=='sensor':self.r['living']['age']=99999
   self.assertFalse(self.plan(190,case!='allowed')['rooms']['living']['desired'],case)
 def test_mean_missing_other_sensor_still_blocks_group(self):
  self.s['floors']['night']['mean_enabled']=True;self.start('bedroom_2');self.r['bedroom_3']['age']=99999;self.assertFalse(self.plan(190)['rooms']['bedroom_2']['desired'])
 def test_cancel_to_auto(self):
  self.start();self.t={};self.assertFalse(self.plan(190)['rooms']['living']['desired'])
 def test_timer_cannot_outlast_two_hours_or_target_bath(self):
  self.t={'living':NOW.timestamp()+10000,'bath_night':NOW.timestamp()+1000};self.assertFalse(any(x['desired'] for x in self.plan()['rooms'].values()))
class TimerCommandTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.patch=patch('daemon.DATA',self.root);self.patch.start();(self.root/'entity-map.json').write_text(json.dumps({'rooms':ROOMS,'admin_user_ids':['admin']}));self.c=Coordinator();self.c.settings.update(master=True,schedule_enabled=False);self.c.connected=True;self.c.blockers=lambda:[];self.c.readings=lambda:readings(22);self.c.tick=AsyncMock()
 async def asyncTearDown(self):self.patch.stop();self.tmp.cleanup()
 async def command(self,minutes=30,key='living',request='one',user='admin'):
  await self.c.command({'context':{'user_id':user},'data':{'command':'room_timer','request_id':request,'payload':{'room':key,'minutes':minutes}}})
 async def test_persist_restart_and_duplicate_no_extension(self):
  await self.command();self.assertTrue(self.c.ack['ok']);end=self.c.manual_timers['living'];self.assertEqual(Coordinator().manual_timers['living'],end);await self.command();self.assertEqual(self.c.manual_timers['living'],end);self.assertEqual(self.c.actual_commands,0)
 async def test_cancel_even_when_disabled(self):
  await self.command();self.c.settings['master']=False;await self.command(0,request='cancel');self.assertNotIn('living',self.c.manual_timers)
 async def test_bad_duration_and_bath_rejected(self):
  for value in [True,-1,10,121,'30']:
   await self.command(value);self.assertFalse(self.c.ack['ok']);self.assertFalse(self.c.manual_timers)
  await self.command(key='bath_night');self.assertFalse(self.c.ack['ok'])
 async def test_auth_and_protection(self):
  await self.command(user='intruder');self.assertFalse(self.c.manual_timers);self.c.settings['master']=False;await self.command();self.assertFalse(self.c.ack['ok'])
 async def test_stale_sensor_rejected(self):
  self.c.readings=lambda:readings(None);await self.command();self.assertFalse(self.c.ack['ok'])
if __name__=='__main__':unittest.main()
