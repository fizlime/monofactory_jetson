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
plant={serial:{simulation:false},line:{status:'IDLE'},config:{p06:{relay_active_level:'UNSET',green_hold_seconds:0,line_code:'TEST',mes_endpoint:''}},units:{},processes:{P06:{name:'경광등',summary:'릴레이'}}};
for(const [id,pin] of [['P06_LIGHT_GREEN',8],['P06_LIGHT_RED',9],['P06_BUZZER',10]]){
 plant.units[id]={id,label:id,pin,output:null,simulated:false,configured:false,connected:false,state:'UNCONFIGURED',error:'극성 미설정'};
}
plant.units.P06_MES={id:'P06_MES',label:'MES',state:'READY'};
manualProcess='P06';syncManualVision=()=>{};renderManual();
let html=$('#manual-controls').innerHTML;
for(const id of ['P06_LIGHT_GREEN','P06_LIGHT_RED','P06_BUZZER']){
 assert(html.match(new RegExp('<button[^>]*data-unit="'+id+'"[^>]*data-command="ON"[^>]*>'))[0].includes('disabled'));
 assert(html.includes('data-unit="'+id+'" data-command="CHANNEL_OFF"'));
}
assert(html.includes('ARDUINO D8')&&html.includes('ARDUINO D9')&&html.includes('ARDUINO D10'));
assert(html.includes('ALL OFF'));
for(const a of Object.values(plant.units))if(a.pin){a.configured=true;a.connected=true;a.output=false;a.active_level='LOW';}
renderManual();html=$('#manual-controls').innerHTML;
assert(!html.match(/<button[^>]*data-unit="P06_BUZZER"[^>]*data-command="ON"[^>]*>/)[0].includes('disabled'));
settingTab='P06';draft=plant.config;draft.distance_unit='mm';draft.press_recipe={version:3,actions:[]};renderSettings();
assert($('#settings-content').innerHTML.includes('p06.relay_active_level'));
assert($('#settings-content').innerHTML.includes('HIGH 신호에서 ON'));
`,context);
console.log('P06 independent controls, D8/D9/D10, unknown polarity lock and LOW/HIGH settings: OK');
