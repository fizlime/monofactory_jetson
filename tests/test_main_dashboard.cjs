// Chromium integration test: real files/CSP, fake status, no hardware requests.
const fs=require('node:fs'),path=require('node:path'),http=require('node:http'),assert=require('node:assert/strict');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'..'),web=path.join(root,'web');
const fixture=JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/dashboard-state.json'),'utf8'));
const sceneFixture=process.env.CAD_FIXTURE==='1'?require('./fixtures/scene-fixture.cjs'):null;
const types={'.html':'text/html','.css':'text/css','.js':'text/javascript','.json':'application/json','.png':'image/png','.glb':'model/gltf-binary'};
const requests=[],sockets=new Set();
let appState=structuredClone(fixture);
const server=http.createServer((req,res)=>{
  res.setHeader('Content-Security-Policy',"default-src 'self'; img-src 'self' data: blob:; style-src 'self'; script-src 'self'; connect-src 'self'; object-src 'none'");
  res.setHeader('X-Content-Type-Options','nosniff');
  if(sceneFixture&&req.url==='/assets/line-model.json'){res.setHeader('Content-Type','application/json');res.end(JSON.stringify(sceneFixture.metadata));return;}
  if(sceneFixture&&req.url==='/assets/test-fixture.glb'){res.setHeader('Content-Type','model/gltf-binary');res.end(sceneFixture.glb);return;}
  if(req.method==='POST'){
    let data='';req.on('data',c=>data+=c);req.on('end',()=>{requests.push({path:req.url,body:JSON.parse(data||'{}')});res.setHeader('Content-Type','application/json');res.end(JSON.stringify({ok:true,message:'test only',state:appState}));});return;
  }
  if(req.url==='/api/status'){res.setHeader('Content-Type','application/json');res.end(JSON.stringify(appState));return;}
  if(req.url==='/api/events'){res.setHeader('Content-Type','text/event-stream');res.write('data: '+JSON.stringify(appState)+'\n\n');return;}
  if(req.url.startsWith('/api/vision/')){res.statusCode=503;res.end();return;}
  const target=path.resolve(web,'.'+(req.url==='/'?'/index.html':req.url.split('?')[0]));
  if(!target.startsWith(web+path.sep)||!fs.existsSync(target)){res.statusCode=404;res.end('missing');return;}
  res.setHeader('Content-Type',types[path.extname(target)]||'application/octet-stream');res.end(fs.readFileSync(target));
});
server.on('connection',s=>{sockets.add(s);s.on('close',()=>sockets.delete(s));});
(async()=>{
  await new Promise(r=>server.listen(0,'127.0.0.1',r));
  const browser=await chromium.launch({headless:true,args:['--enable-unsafe-swiftshader'],...(process.env.UI_BROWSER_PATH?{executablePath:process.env.UI_BROWSER_PATH}:{})});
  try{
    const page=await browser.newPage({viewport:{width:1600,height:900}}),errors=[];
    page.on('pageerror',e=>errors.push(e.message));
    page.on('console',m=>{if(m.type()==='error'&&!m.text().includes('favicon'))errors.push(m.text());});
    await page.goto(`http://127.0.0.1:${server.address().port}`);
    await page.waitForFunction(()=>globalThis.MainDashboard?.state);
    if(process.env.REQUIRE_CAD==='1')await page.waitForFunction(()=>globalThis.Line3D?.ready,{},{timeout:30000});
    for(const [width,height] of [[1920,1080],[1600,900],[1366,768],[1280,720],[1024,768]]){
      await page.setViewportSize({width,height});await page.waitForTimeout(120);
      const geometry=await page.evaluate(()=>{
        const d=document.documentElement;
        const rect=q=>{const r=document.querySelector(q).getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,right:r.right,bottom:r.bottom};};
        return {scrollX:d.scrollWidth-innerWidth,scrollY:d.scrollHeight-innerHeight,scene:rect('#main-mimic'),camera:rect('#vision-panel .vision-viewport'),detail:rect('.main-log-panel'),flow:rect('#main-process-flow'),stop:rect('#page-main [data-api="/api/line/stop"]')};
      });
      assert(geometry.scrollX<=1&&geometry.scrollY<=1,JSON.stringify({width,height,geometry}));
      for(const box of [geometry.scene,geometry.camera,geometry.detail,geometry.stop])assert(box.width>20&&box.height>20&&box.right<=width+1&&box.bottom<=height+1,JSON.stringify({width,height,box}));
      assert(Math.abs(geometry.camera.width/geometry.camera.height-4/3)<.02,`Camera aspect ${width}: ${JSON.stringify(geometry.camera)}`);
      assert(geometry.flow.height>=89,'Expanded process strip');
    }
    await page.setViewportSize({width:1600,height:900});
    assert.equal(await page.locator('#page-main #alarm-content,#page-main .equipment-detail').count(),0);
    for(const code of fixture.process_order){
      await page.locator(`#main-process-flow [data-inspect="${code}"]`).click();
      assert.equal(await page.evaluate(()=>MainDashboard.selected),code);
    }
    assert.equal(requests.length,0,'Selecting equipment must not command hardware');
    await page.locator('#main-process-flow [data-inspect="P01"]').click();
    await page.evaluate(()=>{plant.logs=Array.from({length:25},(_,i)=>({time:'12:00:00.'+String(999-i),type:i===0?'ERROR':'PROCESS',process:i%2?'P01':'P04',unit:'',message:i===0?'<img src=x onerror=alert(1)>':'시험 로그 '+i}));update(structuredClone(plant));});
    assert.equal(await page.locator('.main-log-entry').count(),20,'Bounded recent log list');
    assert.equal(await page.locator('.main-log-entry.is-error').count(),1);
    assert.equal(await page.locator('.main-log-entry img').count(),0,'Log messages render as text');
    assert((await page.locator('.main-log-entry').first().textContent()).includes('<img'));
    await page.locator('[data-main-log]').click();
    assert.equal(await page.locator('#log-process-filter').inputValue(),'ALL');
    assert.equal(await page.locator('.page.active').getAttribute('id'),'page-log');
    await page.evaluate(()=>{plant.line.fault={code:'P04-ERR',title:'프레스 오류',message:'test alarm',ack:false};plant.line.status='ALARM';update(structuredClone(plant));});
    await page.locator('#page-log [data-api="/api/line/ack"]').click();
    assert.equal(requests.at(-1).path,'/api/line/ack');
    await page.locator('.nav[data-page="main"]').click();
    await page.locator('#main-fault-link').click();
    assert.equal(await page.locator('.page.active').getAttribute('id'),'page-log');
    await page.locator('.nav[data-page="main"]').click();
    const before=requests.length;
    for(const action of ['home','start','stop']){
      await Promise.all([page.waitForResponse(r=>r.url().endsWith(`/api/line/${action}`)),page.locator(`#page-main [data-api="/api/line/${action}"]`).click()]);
      await page.waitForFunction(()=>!document.querySelector('#page-main button.pending'));
    }
    // Let the final mock response settle before injecting the next status fixture.
    await page.waitForTimeout(150);
    assert.deepEqual(requests.slice(before).map(x=>x.path),['/api/line/home','/api/line/start','/api/line/stop']);
    if(process.env.REQUIRE_CAD==='1'){
      const diag=await page.evaluate(()=>Line3D.diagnostics);assert(diag.meshes>5,JSON.stringify(diag));
      if(!sceneFixture){
        assert(diag.motions>=15,'Sensors, lamps, robot and press retain status bindings');
        assert(!diag.motionValues.some(m=>m.unit.startsWith('P01_')),'Material feed display remains stationary');
        const bed=diag.layout.find(n=>n?.name==='floor_equipment'),robot=diag.layout.find(n=>n?.name==='display_dobot');
        assert(Math.abs(bed.up[1])<.001,'Equipment follows horizontal floor orientation');
        assert(Math.abs(robot.up[1]-1)<.001&&Math.abs(robot.position[1]-184)<.01,'Shared Dobot stays upright on frame mounting plate');
        const wristCamera=diag.attachments.find(n=>n?.name==='display_camera');
        assert.equal(diag.attachments.find(n=>n?.name==='display_camera_mount').parent,'display_dobot_j4','Camera is attached to tool flange');
        assert.equal(wristCamera.parent,'display_camera_mount');
        assert(wristCamera.direction[1]<-.99,'Camera lens looks down at material');
        const countBefore=requests.length;
        for(const code of ['P02','P04','P03','P05']){
          await page.evaluate(code=>{plant.line.status='RUNNING';plant.line.active_process=code;plant.line.fault=null;update(structuredClone(plant));MainDashboard.select(code==='P03'?'P05':'P01');},code);
          assert.equal(await page.locator('.process-card.current-process').count(),1);
          assert.equal(await page.locator('.process-card[aria-current="step"]').getAttribute('data-inspect'),code);
          assert.equal(await page.locator('.scene-tag.running').getAttribute('data-inspect'),code);
          const highlights=await page.evaluate(()=>Line3D.diagnostics.highlights);
          assert(highlights.length>0&&highlights.every(m=>m.role==='running'&&m.processes.includes(code)),'Running equipment overrides selection, including shared Dobot');
        }
        await page.evaluate(()=>{plant.line.active_process='P04';plant.line.status='PAUSED';update(structuredClone(plant));});
        assert((await page.locator('.process-card.current-process .state').textContent()).includes('일시정지'));
        await page.evaluate(()=>{plant.line.status='ALARM';plant.line.fault={code:'P04-ERR',title:'프레스 오류',message:'test',ack:false};update(structuredClone(plant));});
        assert.equal(await page.locator('.process-card.current-process.process-fault').count(),1);
        assert((await page.evaluate(()=>Line3D.diagnostics.highlights)).every(m=>m.role==='fault'),'Fault overrides active highlight');
        await page.evaluate(()=>{plant.line.status='IDLE';plant.line.fault=null;update(structuredClone(plant));});
        assert.equal(await page.locator('.process-card.current-process,.scene-tag.running').count(),0,'Stale active process is cleared while idle');
        await page.evaluate(()=>{plant.line.status='RUNNING';plant.line.active_process='P04';update(structuredClone(plant));});
        await page.waitForTimeout(250);
        assert.equal(await page.locator('.scene-tag:visible').count(),7,'All process tags remain visible in floor layout');
        await page.evaluate(()=>document.querySelector('#toast')?.classList.remove('show'));
        await page.waitForTimeout(500);
        await page.screenshot({path:path.join(root,'../main-dashboard-running.png')});
        await page.evaluate(value=>{plant=value;update(structuredClone(value));},structuredClone(fixture));
        await page.evaluate(()=>{plant.units.P01_E0.position_mm=38.5;plant.units.P04_PRESS_AXIS.position_mm=12;plant.units.P03_SCARA_J2.position=20;plant.units.P03_SCARA_J3.position=30;plant.units.P06_LIGHT_GREEN.output=true;update(structuredClone(plant));});
        await page.waitForFunction(()=>{const v=Line3D.diagnostics.motionValues;return Math.abs(v.find(m=>m.node==='display_press_ram').value-12)<.01&&Math.abs(v.find(m=>m.node==='display_dobot_j3').value-50)<.01;});
        const movedCamera=await page.evaluate(()=>({camera:Line3D.diagnostics.attachments.find(n=>n?.name==='display_camera'),anchor:Line3D.diagnostics.tagAnchors.P02}));
        assert(Math.hypot(...movedCamera.camera.position.map((v,i)=>v-wristCamera.position[i]))>1,'Camera follows received robot joint motion');
        assert(Math.hypot(...movedCamera.camera.position.map((v,i)=>v-movedCamera.anchor[i]))<.001,'P02 tag follows moving camera');
        const positions=await page.evaluate(()=>Line3D.diagnostics.motionValues);
        assert(Math.abs(positions.find(m=>m.node==='display_press_ram').position[1]-.073)<.00002,'Press positive distance moves down');
        await page.evaluate(()=>{plant.units.P04_PRESS_AXIS.connected=false;plant.units.P04_PRESS_AXIS.position_mm=999;update(structuredClone(plant));MainDashboard.select('P06');});
        const held=await page.evaluate(()=>Line3D.diagnostics.motionValues);
        assert.equal(held.find(m=>m.node==='display_press_ram').target,12,'Lost feedback holds last position');
        assert(held.find(m=>m.unit==='P06_LIGHT_GREEN').emissive>.6,'Selection preserves lamp state');
        assert.equal(requests.length,countBefore,'3D status updates do not command hardware');
        await page.locator('[data-view="interior"]').click();assert.equal(await page.locator('[data-view="interior"]').getAttribute('aria-pressed'),'false');
        await page.locator('[data-view="interior"]').click();
        await page.evaluate(value=>{plant=value;update(structuredClone(value));MainDashboard.select('P01');},structuredClone(fixture));
      }
      await page.locator('[data-view="top"]').click();await page.locator('[data-view="fit"]').click();
      await page.locator('[data-view="tags"]').click();await page.waitForFunction(()=>![...document.querySelectorAll('.scene-tag')].some(e=>!e.hidden));assert.equal(await page.locator('.scene-tag:visible').count(),0);
      await page.locator('[data-view="tags"]').click();await page.waitForTimeout(100);
      assert(await page.locator('.scene-tag:visible').count()>0);
      await page.locator('.nav[data-page="manual"]').click();assert.equal(await page.evaluate(()=>Line3D.diagnostics.visible),false);
      await page.locator('.nav[data-page="main"]').click();assert.equal(await page.evaluate(()=>Line3D.diagnostics.visible),true);
      assert.deepEqual(errors,[]);
    }
    await page.evaluate(()=>document.querySelector('#toast')?.classList.remove('show'));
    await page.waitForTimeout(500);
    await page.screenshot({path:path.join(root,sceneFixture?'../main-dashboard-test-fixture.png':'../main-dashboard-preview.png')});
    if(!sceneFixture&&process.env.REQUIRE_CAD==='1'){await page.setViewportSize({width:1366,height:768});await page.waitForTimeout(200);await page.screenshot({path:path.join(root,'../main-dashboard-1366.png')});}
    if(!sceneFixture&&process.env.REQUIRE_CAD==='1'){
      await page.setViewportSize({width:1600,height:900});
      await page.locator('[data-view="tags"]').click();await page.waitForTimeout(250);
      await page.locator('#main-mimic').screenshot({path:path.join(root,'../design-model-perspective.png')});
      await page.locator('[data-view="top"]').click();await page.waitForTimeout(250);
      await page.locator('#main-mimic').screenshot({path:path.join(root,'../design-model-top.png')});
      await page.locator('[data-view="fit"]').click();
      await page.locator('[data-view="tags"]').click();
      await page.evaluate(()=>{plant.line.status='RUNNING';plant.line.active_process='P02';update(structuredClone(plant));});
      await page.waitForTimeout(250);
      await page.screenshot({path:path.join(root,'../p02-wrist-camera.png')});
    }
    console.log('PASS: MAIN fits five desktop sizes; camera 4:3; process details; read-only selection; LOG alarm actions; HOME/START/STOP preserved.',sceneFixture?'Test GLB WebGL loaded.':process.env.REQUIRE_CAD==='1'?'CAD WebGL loaded.':'Layout only.');
  }finally{await browser.close();for(const s of sockets)s.destroy();await new Promise(r=>server.close(r));}
})().catch(e=>{console.error(e);process.exitCode=1;for(const s of sockets)s.destroy();server.close();});
