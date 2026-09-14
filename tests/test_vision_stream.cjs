/* Camera preview browser lifecycle; fake fetch only, no hardware/server writes. */
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../web/app.js'),'utf8');
const nodes=new Map(),requests=[],revoked=[];let frameResponse,objectId=0,activePage='main';
function query(q){if(!nodes.has(q))nodes.set(q,{dataset:{},hidden:false,classList:{contains:()=>q===`#page-${activePage}`,add(){},remove(){},toggle(name,value){this[name]=value;}},removeAttribute(name){delete this[name];}});return nodes.get(q);}
const ctx=vm.createContext({document:{hidden:false,querySelector:query,querySelectorAll:()=>[],addEventListener(){}},window:{addEventListener(){}},
  AbortController,URL:{createObjectURL:()=>`blob:test-${++objectId}`,revokeObjectURL:url=>revoked.push(url)},
  setTimeout(){},clearTimeout(){},setInterval(){},EventSource:class{},fetch:(url)=>{requests.push(url);return url==='/api/status'?new Promise(()=>{}):Promise.resolve(frameResponse);}});
vm.runInContext(source,ctx);
const run=code=>vm.runInContext(code,ctx);
async function main(){
  run("plant={config:{p02:{preview_fps:8}},units:{P02_CAMERA:{stream_status:'LIVE'}}}");
  frameResponse={ok:true,blob:async()=>({})};
  await run('pollVisionFrame()');
  query('#vision-frame').onload();
  assert.equal(query('#vision-frame').hidden,false);
  assert.equal(query('#vision-placeholder').hidden,true);
  assert.equal(query('#vision-frame').src,'blob:test-1');
  await run('pollVisionFrame()');query('#vision-frame').onload();
  assert(revoked.includes('blob:test-1'));
  frameResponse={ok:false};
  await run('pollVisionFrame()');
  assert.equal(query('#vision-frame').hidden,true);
  assert.equal(query('#vision-placeholder').hidden,false);
  assert(!query('#vision-frame').src);
  assert.equal(query('#vision-connection').textContent,'영상 수신 대기');
  frameResponse={ok:true,blob:async()=>({})};
  await run('pollVisionFrame()');query('#vision-frame').onload();
  assert.equal(query('#vision-frame').hidden,false); // Recovers without reload.
  activePage='manual';
  for(const process of ['P01','P03','P04','P05','P06','P07']){
    run(`manualProcess='${process}';syncManualVision()`);
    assert.equal(query('#manual-vision-panel').hidden,true);
    assert.equal(query('#manual-workspace').classList['has-vision'],false);
    const before=requests.length;await run('pollVisionFrame()');
    assert.equal(requests.length,before);
  }
  run("manualProcess='P02';syncManualVision()");
  assert.equal(query('#manual-vision-panel').hidden,false);
  assert.equal(query('#manual-workspace').classList['has-vision'],true);
  const beforeManual=requests.length;
  await run('pollVisionFrame()');query('#manual-vision-frame').onload();
  assert.equal(requests.length,beforeManual+1); // One shared poll, not a second camera stream.
  assert.equal(query('#manual-vision-frame').hidden,false);
  assert.equal(query('#manual-vision-placeholder').hidden,true);
  assert.equal(query('#vision-frame').hidden,true);
  run("manualProcess='P04';syncManualVision()");
  assert.equal(query('#manual-vision-panel').hidden,true);
  assert.equal(query('#manual-vision-frame').hidden,true);
  assert(!query('#manual-vision-frame').src);
  run("manualProcess='P02';syncManualVision()");
  frameResponse={ok:true,blob:async()=>{run("manualProcess='P03';syncManualVision()");return {};}};
  await run('pollVisionFrame()');
  assert.equal(query('#manual-vision-frame').hidden,true);
  run("manualProcess='P02';syncManualVision()");
  frameResponse={ok:true,blob:async()=>({})};
  activePage='setting';
  const beforeSettings=requests.length;await run('pollVisionFrame()');
  assert.equal(requests.length,beforeSettings);
  assert.equal(query('#manual-vision-frame').hidden,true);
  activePage='main';
  frameResponse={ok:true,blob:async()=>{activePage='manual';return {};}};
  await run('pollVisionFrame()');query('#manual-vision-frame').onload();
  assert.equal(query('#manual-vision-frame').hidden,false); // Navigation during fetch follows current page.
  frameResponse={ok:true,blob:async()=>{activePage='log';return {};}};
  await run('pollVisionFrame()');assert.equal(query('#manual-vision-frame').hidden,true);
  activePage='main';frameResponse={ok:true,blob:async()=>({})};
  run("plant.units.P02_CAMERA.stream_status='SIMULATION'");
  const count=requests.length;await run('pollVisionFrame()');
  assert.equal(requests.length,count);assert.equal(query('#vision-frame').hidden,true);
  run("plant.units.P02_CAMERA.stream_status='LIVE';document.hidden=true");
  await run('pollVisionFrame()');assert.equal(requests.length,count);
  run('document.hidden=false');
  frameResponse={ok:true,blob:async()=>{run("plant.units.P02_CAMERA.stream_status='SEARCHING'");return {};}};
  await run('pollVisionFrame()');assert.equal(query('#vision-frame').hidden,true);
  assert.equal(requests.filter(p=>p!=='/api/status'&&p!=='/api/vision/frame.jpg').length,0);
  console.log('PASS: MAIN and MANUAL P02 only; other processes hide panel/reclaim width/stop requests; in-flight switching and cleanup.');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
