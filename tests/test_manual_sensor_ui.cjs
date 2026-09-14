const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const nodes = new Map();
const node = key => {
  if (!nodes.has(key)) nodes.set(key, {dataset:{}, innerHTML:'', textContent:'', removeAttribute(){}});
  return nodes.get(key);
};
const context = vm.createContext({document:{querySelector:node}, assert, setTimeout(){}});
const source = fs.readFileSync(path.join(__dirname,'../web/app.js'),'utf8');
vm.runInContext(source.slice(0,source.lastIndexOf("setInterval(()=>$('#clock')")),context);
vm.runInContext(`
plant={serial:{simulation:false,connected:true},line:{status:'IDLE'},config:{p01:{axes:{}},p00:{serial_port:'/dev/serial/by-id/uno',serial_baud:115200}},units:{},processes:{P00:{name:'센서',summary:'자재 4개'}}};
for(let i=0;i<4;i++){
 plant.config.p01.axes['E'+i]={installed:i!==3};
 plant.units['P01_E'+i]={id:'P01_E'+i,label:'모터',enabled:true,state:'READY',connected:true,position:0};
}
plant.units.P00_MATERIAL={id:'P00_MATERIAL',label:'센서',state:'OFFLINE',detected:false,connected:false,detected_count:0,error:'Arduino USB 미연결'};
renderLinearManual(plant.units);
let html=$('#manual-controls').innerHTML;
for(const axis of ['E0','E1','E2'])for(const action of ['FORWARD','REVERSE','CYCLE']){
 const button=html.match(new RegExp('<button[^>]*data-unit="P01_'+axis+'"[^>]*data-command="'+action+'"[^>]*>'))[0];
 assert(!button.includes('disabled'),axis+' '+action+' must work without material');
}
assert(html.match(/<button[^>]*data-unit="P01_E3"[^>]*data-command="FORWARD"[^>]*>/)[0].includes('disabled'));
assert($('#manual-feed-health').textContent.includes('자동 투입 대기'));
plant.units.P01_E0.enabled=false;
renderLinearManual(plant.units);
assert($('#manual-controls').innerHTML.match(/<button[^>]*data-unit="P01_E0"[^>]*data-command="FORWARD"[^>]*>/)[0].includes('disabled'));
manualProcess='P00';syncManualVision=()=>{};
plant.units.P00_MATERIAL.transport_connected=true;
renderManual();
assert($('#manual-controls').innerHTML.includes('USB 연결 · 데이터 대기'));
assert($('#manual-controls').innerHTML.includes('마지막 정상 수신'));
`,context);
console.log('P01 manual without sensors, ENABLE/exclusion, and P00 USB/data status: OK');
