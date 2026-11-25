import fs from 'node:fs';import assert from 'node:assert/strict';import path from 'node:path';
import {plantDiagram} from '../aquarea-clima/web/diagram.js';
const base=new URL('../aquarea-clima/web/',import.meta.url);const files=['index.html','styles.css','app.js','cop.js','diagram.js','catalog.json','reuse/charts.js','reuse/data.js','reuse/ha-data.js','reuse/climate-data.mjs'];
for(const name of files)assert(fs.statSync(new URL(name,base)).size>0);
for(const name of files.filter(n=>/\.(js|mjs)$/.test(n))){const text=fs.readFileSync(new URL(name,base),'utf8');for(const hit of text.matchAll(/from\s+['"](\.[^'"]+)['"]/g))assert(fs.existsSync(new URL(hit[1].split('?')[0],new URL(name,base))),name+' import '+hit[1]);}
const app=fs.readFileSync(new URL('app.js',base),'utf8');for(const page of ['sinottico','frigorifero','idraulico','zone','acs','resistenze','parametri','curva','analisi-cop','energia','storico','allarmi','impostazioni'])assert(app.includes("'"+page+"'"));
const catalog=JSON.parse(fs.readFileSync(new URL('catalog.json',base)));assert(catalog.length>=106);assert.equal(new Set(catalog.map(x=>x.name)).size,catalog.length);assert(catalog.every(x=>x.entity===null));
const svg=plantDiagram({metrics:{},zones:{season:null,season_quality:'unavailable'},pumps:{zone1:{quality:'unavailable'},zone2:{quality:'unavailable'}}},()=> 'N/D',()=> 'N/D');assert(svg.includes('UNITÀ ESTERNA'));assert(svg.includes('BUFFER'));assert(svg.includes('ZONA 2'));assert(!svg.includes('NaN'));assert(!svg.includes('undefined'));
const host=fs.readFileSync(new URL('../src/aquarea-clima-host.js',import.meta.url),'utf8');assert(host.includes("type:'aquarea_clima/session'"));assert(host.includes('event.origin!==location.origin'));assert(host.includes("type:'aquarea-clima-host'"));
console.log('Aquarea Clima assets, imports, 13 views, catalog, diagram and host registration passed');
