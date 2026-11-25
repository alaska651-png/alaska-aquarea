import copy,json,unittest
from datetime import timedelta
from test_engine import NOW,ROOMS,readings
from engine import Engine,defaults,validate
class MeanTests(unittest.TestCase):
 def setUp(self):
  self.s=defaults();self.s.update(master=True,schedule_enabled=False);self.e=Engine();self.r=readings(21)
  for f in self.s['floors'].values():f.update(setpoint=21,mean_enabled=True)
 def plan(self,t=0,allowed=True):return self.e.evaluate(self.s,ROOMS,self.r,NOW+timedelta(seconds=t),allowed)
 def group(self,p,f):return [x['desired'] for x in p['rooms'].values() if x['floor']==f]
 def test_migration_defaults_and_validation(self):
  for f in self.s['floors'].values():del f['mean_enabled'];del f['mean_percent']
  validate(self.s);self.assertFalse(self.s['floors']['night']['mean_enabled'])
  for bad in [0,6,True,float('nan')]:
   self.s['floors']['night']['mean_percent']=bad
   with self.assertRaises(ValueError):validate(self.s)
 def test_exact_thresholds_latch_and_pause(self):
  self.r=readings(20.79);self.assertFalse(any(self.group(self.plan(),'night')))
  self.assertTrue(all(self.group(self.plan(180),'night')))
  self.r=readings(21.1);self.assertTrue(all(self.group(self.plan(190),'night')))
  self.r=readings(21.21);self.assertFalse(any(self.group(self.plan(200),'night')))
  self.r=readings(21);self.assertFalse(any(self.group(self.plan(210),'night')))
  self.r=readings(20.79);self.assertFalse(any(self.group(self.plan(220),'night')));self.assertTrue(all(self.group(self.plan(380),'night')))
 def test_distinct_sensors_baths_and_no_partial_mean(self):
  self.r['living']['temperature']=20;self.r['kitchen']['temperature']=22;self.r['bath_day']['temperature']=5
  p=self.plan();self.assertEqual(p['floors']['day']['mean_temperature'],21);self.assertEqual(p['floors']['day']['mean_sensor_count'],2);self.assertEqual(p['floors']['night']['mean_sensor_count'],4)
  self.r=readings(19);self.plan(10);p=self.plan(200);self.assertTrue(all(self.group(p,'night')))
  self.r['bath_night']['age']=99999;p=self.plan(210);self.assertFalse(any(self.group(p,'night')));self.assertIsNone(p['floors']['night']['mean_temperature']);self.assertTrue(all(self.group(p,'day')))
 def test_all_together_despite_hot_room(self):
  self.r=readings(19);self.r['bedroom_3']['temperature']=23;self.plan();p=self.plan(180);self.assertTrue(all(self.group(p,'night')));self.assertEqual(p['floors']['night']['mean_temperature'],20)
 def test_gates_and_unmapped_circuit(self):
  for mode in ['master','season','enabled','allowed','mapping']:
   self.setUp();self.r=readings(19);self.plan();self.plan(180)
   rooms=copy.deepcopy(ROOMS)
   if mode=='master':self.s['master']=False
   if mode=='season':self.s['season']='summer'
   if mode=='enabled':self.s['floors']['night']['enabled']=False
   if mode=='mapping':rooms['bedroom_3']['confirmed']=False
   p=self.e.evaluate(self.s,rooms,self.r,NOW+timedelta(seconds=200),mode!='allowed');self.assertFalse(any(self.group(p,'night')),mode)
 def test_independent_floors_and_return_to_individual(self):
  self.r=readings(19);self.plan();self.plan(180);self.s['floors']['day']['enabled']=False;p=self.plan(190);self.assertTrue(all(self.group(p,'night')));self.assertFalse(any(self.group(p,'day')))
  self.s['floors']['night']['mean_enabled']=False;self.r['bedroom_3'].update(temperature=22,sample_at='b');p=self.plan(200);self.assertFalse(p['rooms']['bedroom_3']['desired']);self.assertNotIn('night',self.e.mean_memory);self.assertTrue(p['rooms']['bedroom_2']['desired'])
 def test_crono_target_and_percent(self):
  self.s['schedule_enabled']=True;self.s['comfort']=22;self.s['eco']=18;self.plan();p=self.plan(180);f=p['floors']['night'];self.assertEqual(f['mean_lower'],f['target']*.99);self.assertEqual(f['mean_upper'],f['target']*1.01)
if __name__=='__main__':unittest.main()
