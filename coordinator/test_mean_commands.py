import copy,json,time,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,AsyncMock
from datetime import datetime,timezone
from test_engine import ROOMS,readings,NOW
from daemon import Coordinator
class HandoffTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.patch=patch('daemon.DATA',self.root);self.patch.start()
  (self.root/'entity-map.json').write_text(json.dumps({'rooms':ROOMS}));self.c=Coordinator();self.c.settings.update(master=True,schedule_enabled=False)
  self.c.engine.evaluate(self.c.settings,ROOMS,readings(19),NOW)
  self.plan=self.c.engine.evaluate(self.c.settings,ROOMS,readings(19),NOW.replace(minute=10))
  iso=datetime.now(timezone.utc).isoformat();self.c.states={v['relay']:{'state':'on' if self.plan['rooms'][k]['desired'] else 'off','last_changed':iso} for k,v in ROOMS.items()}
  self.rec={'at':time.time(),'settings':copy.deepcopy(self.c.settings),'memory':copy.deepcopy(self.c.engine.memory),'mean_memory':{},'relays':{r:x['state'] for r,x in self.c.states.items()},'open_since':{}}
 def tearDown(self):self.patch.stop();self.tmp.cleanup()
 def test_recent_matching_snapshot_restores_timers(self):
  old=copy.deepcopy(self.c.engine.memory);self.c.engine.memory={};self.assertTrue(self.c.restore_engine_state(self.rec));self.assertEqual(self.c.engine.memory,old)
 def test_mismatched_stale_or_changed_settings_rejected(self):
  for kind in ['age','relay','settings']:
   r=copy.deepcopy(self.rec)
   if kind=='age':r['at']-=100
   if kind=='relay':r['relays'][next(iter(r['relays']))]='off'
   if kind=='settings':r['settings']['master']=False
   self.assertFalse(self.c.restore_engine_state(r),kind)
 def test_first_upgrade_legacy_handoff(self):
  r=copy.deepcopy(self.rec);r.pop('memory');r.pop('mean_memory');r['plan']=self.plan
  self.assertTrue(self.c.restore_engine_state(r));self.assertIsNotNone(self.c.engine.memory['bedroom_2']['on_since'])
class MeanCommandTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.patch=patch('daemon.DATA',self.root);self.patch.start()
  (self.root/'entity-map.json').write_text(json.dumps({'rooms':ROOMS,'admin_user_ids':['admin']}));self.c=Coordinator();self.c.tick=AsyncMock()
 async def asyncTearDown(self):self.patch.stop();self.tmp.cleanup()
 async def command(self,p,user='admin'):
  await self.c.command({'context':{'user_id':user},'data':{'command':'mean_floor','payload':p,'request_id':'test'}})
 async def test_preserve_crono_and_persist(self):
  await self.command({'floor':'night','enabled':True,'percent':1.25});self.assertTrue(self.c.ack['ok']);self.assertEqual(self.c.settings['floors']['night']['mode'],'chrono');self.assertTrue(Coordinator().settings['floors']['night']['mean_enabled']);self.assertEqual(self.c.actual_commands,0)
 async def test_target_changes_only_one_floor(self):
  before=copy.deepcopy(self.c.settings['floors']['day']);await self.command({'floor':'night','enabled':True,'percent':1,'target':21});self.assertTrue(self.c.ack['ok']);self.assertEqual(self.c.settings['floors']['night']['mode'],'manual');self.assertEqual(before,self.c.settings['floors']['day'])
 async def test_reject_invalid_and_unauthorized(self):
  original=copy.deepcopy(self.c.settings)
  for p in [{'floor':'night','enabled':'on','percent':1},{'floor':'bad','enabled':True,'percent':1},{'floor':'night','enabled':True,'percent':99},{'floor':'night','enabled':True,'percent':1,'target':30}]:
   await self.command(p);self.assertFalse(self.c.ack['ok']);self.assertEqual(original,self.c.settings)
  await self.command({'floor':'night','enabled':True,'percent':1},'intruder');self.assertEqual(original,self.c.settings)
if __name__=='__main__':unittest.main()
