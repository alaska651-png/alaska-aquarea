import assert from 'node:assert/strict';
import {pumpStatus,temperatureReading} from '../src/climate-data.mjs';
const start=Date.now(),online={state:'on'},temp=n=>({state:String(n),attributes:{unit_of_measurement:'°C'}});
let checks=0;
// One minute of real-time reports every five seconds, server 500 ms ahead.
// The former age>=0 test blanked all four fields on each new report.
for(let elapsed=0;elapsed<60000;elapsed+=100){
  const now=start+elapsed,beat={last_changed:new Date(start+Math.floor(elapsed/5000)*5000+500).toISOString()};
  assert.equal(pumpStatus({state:'on'},beat,online,true,now),'ON');
  assert.equal(pumpStatus({state:'off'},beat,online,true,now),'OFF');
  assert.equal(temperatureReading(temp(35),beat,online,true,-10,95,now),35);
  assert.equal(temperatureReading(temp(8),beat,online,true,-50,60,now),8);checks+=4;
}
const beat={last_changed:new Date(start).toISOString()};
assert.equal(pumpStatus({state:'on'},beat,online,true,start+40000),'N/D');
assert.equal(temperatureReading(temp(35),beat,online,true,-10,95,start+120000),null);
assert.equal(pumpStatus({state:'on'},beat,{state:'off'},true,start),'N/D');
assert.equal(pumpStatus({state:'off'},beat,online,true,start),'OFF');
assert.equal(pumpStatus({state:'unavailable'},beat,online,true,start),'N/D');
assert.equal(pumpStatus({state:'on'},{last_changed:new Date(start+5001).toISOString()},online,true,start),'N/D');
assert.equal(pumpStatus({state:'on'},beat,online,false,start),'N/D');checks+=7;
console.log(`Telemetry timing: ${checks} checks passed`);
