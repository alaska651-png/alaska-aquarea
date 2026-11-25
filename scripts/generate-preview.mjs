import fs from 'node:fs';
import {plantDiagram} from '../aquarea-clima/web/diagram.js';
const data={metrics:{},zones:{season:null,season_quality:'unavailable'},pumps:{zone1:{quality:'unavailable'},zone2:{quality:'unavailable'}}};
let svg=plantDiagram(data,()=> 'N/D',()=> 'N/D');
svg=svg.replace('<svg ','<svg xmlns="http://www.w3.org/2000/svg" ');
svg=svg.replace(/(<svg[^>]*>)/,'$1<title>Aquarea Clima · sinottico senza dati privati</title><style>svg{background:#00131b}text{fill:#eaf8fc;font-family:Arial,sans-serif}.caption{font-size:16px;font-weight:600}.value{font-size:20px}.label{font-size:14px}.tiny{font-size:12px;fill:#b1cbd3}.tube{fill:none;stroke-linejoin:round;stroke-linecap:round;stroke-width:4}.hot{stroke:#ff2341}.cold{stroke:#00bbff}.gas{stroke:#ff33ed}.frame{fill:#001218;stroke:#6eabb6;stroke-width:1}.device{stroke:#bcd2d5}.pump-inactive{stroke:#9daeb4}</style>');
fs.writeFileSync(new URL('../docs/aquarea-clima-sinottico.svg',import.meta.url),svg.replace(/[ \t]+$/gm,''));
console.log('Generated schematic from the real dashboard renderer, no installation readings');
