const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const nodes=new Map();
const node=key=>{if(!nodes.has(key))nodes.set(key,{dataset:{},innerHTML:'',textContent:'',removeAttribute(){}});return nodes.get(key);};
const ctx=vm.createContext({document:{querySelector:node},assert,setTimeout(){}});
const source=fs.readFileSync(path.join(__dirname,'../web/app.js'),'utf8');
vm.runInContext(source.slice(0,source.lastIndexOf("setInterval(()=>$('#clock')")),ctx);
vm.runInContext(`
plant={config:{distance_unit:'mm',p02:{inspection_mode:'CAMERA_CHECK'},press_recipe:{version:3,actions:[]}},processes:{P02:{name:'Vision',summary:''}},units:{P02_CAMERA:{id:'P02_CAMERA',label:'Camera',state:'DONE',result:'CAMERA_OK',score:null,simulated:false}}};
syncManualVision=()=>{};manualProcess='P02';renderManual();
assert($('#manual-controls').innerHTML.includes('카메라 연결 확인'));
assert($('#manual-controls').innerHTML.includes('AI 미검사'));
assert(!$('#manual-controls').innerHTML.includes('SCORE'));
draft=plant.config;settingTab='P02';renderSettings();
assert($('#settings-content').innerHTML.includes('p02.inspection_mode'));
assert($('#settings-content').innerHTML.includes('CAMERA_CHECK'));
assert($('#settings-content').innerHTML.includes('영상 미수신'));
`,ctx);
console.log('Camera-only cycle mode UI clearly distinguishes AI bypass and allows restoring AI mode.');
