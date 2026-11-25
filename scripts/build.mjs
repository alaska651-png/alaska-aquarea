import fs from 'node:fs';
const read=n=>fs.readFileSync(new URL('../src/'+n,import.meta.url),'utf8');
const data=read('climate-data.mjs').replace(/^export /gm,'');
const css=read('coordinator.css');
const card=read('coordinator-card.js').replace(/^import .+\n/,'').replace(/<link rel="stylesheet" href="\/local\/coordinatore-climatico\/coordinatore\.css\?v=[^"]+">/g,'<style>${CARD_CSS}</style>');
fs.mkdirSync(new URL('../dist/',import.meta.url),{recursive:true});
fs.writeFileSync(new URL('../dist/alaska-aquarea.js',import.meta.url),`// Aquarea Clima by Alaska v1.1.0 — MIT\nconst CARD_CSS=${JSON.stringify(css)};\n${read('aquarea-clima-host.js')}\n${data}\n${card}`);
console.log('Built dist/alaska-aquarea.js');
