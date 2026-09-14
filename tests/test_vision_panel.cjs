/* Main camera panel regression checks; no server/camera/device access. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const root=path.resolve(__dirname,'..');
const source=fs.readFileSync(path.join(root,'web/app.js'),'utf8');
const html=fs.readFileSync(path.join(root,'web/index.html'),'utf8');
const css=fs.readFileSync(path.join(root,'web/styles.css'),'utf8');
const nodes=new Map(),requests=[];
function query(selector){
  assert.notEqual(selector,'#vision-pop');
  if(!nodes.has(selector))nodes.set(selector,{dataset:{},innerHTML:'',textContent:'',classList:{add(){},remove(){},toggle(){}}});
  return nodes.get(selector);
}
const ctx=vm.createContext({document:{querySelector:query,querySelectorAll:()=>[],addEventListener(){}},window:{addEventListener(){}},
  setInterval(){},setTimeout(){},clearTimeout(){},EventSource:class{},fetch:url=>{requests.push(url);return new Promise(()=>{});}});
vm.runInContext(source,ctx);
const run=code=>vm.runInContext(code,ctx);
assert(!html.includes('vision-pop'));assert(!html.includes('vision-close'));
assert(!source.includes('/api/vision/dismiss'));assert(!css.includes('.vision-pop'));
assert(!html.includes('class="main-notices"'));
assert(!html.includes('id="main-logs"'));
assert(html.includes('class="main-sidebar"'));
assert(html.indexOf('id="alarm-content"')>html.indexOf('id="page-log"'));
assert(html.indexOf('id="vision-panel"')<html.indexOf('id="page-manual"'));
for(const id of ['alarm-content','vision-panel','vision-result','vision-reason','vision-time'])assert.equal(html.split('id="'+id+'"').length-1,1);
assert(html.includes('Basler acA2500-14gm'));assert(html.includes('2592 × 1944 · 4:3'));
assert(html.includes('REAL 모드에서 카메라 연결 시 자동으로 표시됩니다.'));
assert(html.includes('id="vision-frame"'));
assert(html.includes('id="manual-vision-frame"'));
assert(html.indexOf('id="manual-vision-panel"')>html.indexOf('id="manual-controls"'));
assert(html.indexOf('id="manual-vision-panel"')<html.indexOf('id="page-setting"'));
assert(css.includes('aspect-ratio: 4 / 3'));assert(css.includes('object-fit: contain'));
run("plant={line:{status:'IDLE',vision:{}},process_order:[],processes:{},logs:[],units:{P02_CAMERA:{state:'READY'}}};renderMain()");
assert.equal(query('#vision-result').textContent,'WAIT');
assert.equal(query('#vision-result').dataset.result,'WAIT');
assert.equal(query('#vision-reason').textContent,'검사 대기');
run("plant.config={p02:{inspection_mode:'CAMERA_CHECK'}};plant.line.vision={result:'CAMERA_OK',reason:'AI 미검사'};renderMain()");
assert.equal(query('#vision-result').textContent,'카메라 확인');
assert.equal(query('#vision-result').dataset.result,'CAMERA_OK');
assert(query('#vision-note').textContent.includes('AI 미검사'));
assert.equal(query('#manual-vision-result').textContent,'카메라 확인');
assert(css.includes('.vision-result[data-result="CAMERA_OK"]'));
for(const result of ['OK','NG']){
  run("plant.line.vision={visible:false,result:'"+result+"',reason:'<result>',time:'12:34:56'};renderMain()");
  assert.equal(query('#vision-result').textContent,result);
  assert.equal(query('#vision-result').dataset.result,result);
  assert.equal(query('#vision-reason').textContent,'<result>');
  assert.equal(query('#vision-time').textContent,'12:34:56');
  assert.equal(query('#manual-vision-result').textContent,result);
  assert.equal(query('#manual-vision-reason').textContent,'<result>');
}
run("plant.units.P02_CAMERA.state='INSPECTING';renderMain()");
assert.equal(query('#vision-result').textContent,'INSPECTING');
assert.equal(query('#vision-time').textContent,'');
run("plant.units.P02_CAMERA.camera_model='acA2500-14gc';plant.units.P02_CAMERA.color_mode='COLOR';renderMain()");
assert.equal(query('#vision-heading').textContent,'Basler acA2500-14gc');
assert.equal(query('#manual-vision-heading').textContent,'Basler acA2500-14gc');
assert(query('#manual-vision-resolution').textContent.includes('COLOR'));
run("plant.units.P02_CAMERA.state='READY';plant.line.vision={visible:false,result:'WAIT',reason:'',time:''};plant.line.fault={code:'P02-ERR',title:'검사 오류',message:'<error>',ack:false};plant.logs=[{time:'12:34:56',process:'P02',message:'<event>'}];renderMain()");
assert.equal(query('#vision-result').textContent,'WAIT');
assert(query('#alarm-content').innerHTML.includes('&lt;error&gt;'));
assert(query('#main-logs').innerHTML.includes('&lt;event&gt;'));
assert.deepEqual(requests,['/api/status']);
console.log('PASS: permanent 4:3 camera panel, no popup, WAIT/inspection/OK/NG, events and alarms retained, no device commands.');
