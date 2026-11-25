import assert from 'node:assert/strict';
import {pumpStatus} from '../src/climate-data.mjs';
const now=Date.now(),beat={last_changed:new Date(now-1000).toISOString()},online={state:'on'};
// A stable pump can keep the same state for hours; freshness comes from telemetry.
const stable={state:'on',last_changed:new Date(now-86400000).toISOString()};
assert.equal(pumpStatus(stable,beat,online,true,now),'ON');
assert.equal(pumpStatus({state:'off'},beat,online,true,now),'OFF');
for(const state of [undefined,{state:'unknown'},{state:'unavailable'}])assert.equal(pumpStatus(state,beat,online,true,now),'N/D');
for(const delta of [40000,120000,-5001])assert.equal(pumpStatus(stable,{last_changed:new Date(now-delta).toISOString()},online,true,now),'N/D');
assert.equal(pumpStatus(stable,{},online,true,now),'N/D');
assert.equal(pumpStatus(stable,beat,{state:'off'},true,now),'N/D');
assert.equal(pumpStatus(stable,beat,online,false,now),'N/D');
console.log('Pump status: 11 checks passed');
