// Real Chromium DOM regression test. All actions use a local API stub.
// Run with Playwright available through NODE_PATH; optionally set UI_BROWSER_PATH.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('playwright');
(async()=>{
  const root=path.join(__dirname,'..');
  const browser=await chromium.launch({headless:true,...(process.env.UI_BROWSER_PATH?{executablePath:process.env.UI_BROWSER_PATH}:{})});
  try{
    const page=await browser.newPage({viewport:{width:1600,height:1000}});
    page.on('pageerror',error=>console.error('PAGE ERROR:',error.message));
    await page.route('**/*',route=>route.abort());
    await page.setContent(fs.readFileSync(path.join(root,'web/index.html'),'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/g,''));
    await page.addStyleTag({content:fs.readFileSync(path.join(root,'web/styles.css'),'utf8')});
    await page.addStyleTag({content:fs.readFileSync(path.join(root,'web/main-dashboard.css'),'utf8')});
    const source=fs.readFileSync(process.env.UI_APP_SOURCE||path.join(root,'web/app.js'),'utf8');
    await page.addScriptTag({content:source.slice(0,source.lastIndexOf("setInterval(()=>$('#clock')"))});
    const config=JSON.parse(fs.readFileSync(path.join(root,'settings/poc_config.json'),'utf8'));
    await page.evaluate(config=>{
      pollVisionFrame=()=>{};clearVisionFrame();window.calls=[];api=async(path,body)=>{calls.push({path,body});return{ok:true}};
      plant={serial:{simulation:false,connected:true},line:{status:'IDLE',mode:'MANUAL'},config,process_order:['P00','P01','P02','P03','P04','P05','P06'],units:{},processes:{},logs:[]};
      for(const code of plant.process_order)plant.processes[code]={name:code,summary:'Test',status:'READY'};
      for(let i=0;i<4;i++)plant.units['P01_E'+i]={id:'P01_E'+i,label:'Motor '+i,process:'P01',enabled:true,state:'READY',connected:true,position:0};
      plant.units.P00_MATERIAL={id:'P00_MATERIAL',process:'P00',label:'Sensors',state:'READY',connected:true,detected:false,detected_count:1};
      plant.units.P02_CAMERA={id:'P02_CAMERA',process:'P02',label:'Camera',state:'READY',result:'WAIT'};
      plant.units.P04_PRESS_AXIS={id:'P04_PRESS_AXIS',process:'P04',label:'Press',state:'READY',enabled:true,connected:true,position:0,homed:false};
      plant.units.P04_HOME_1={id:'P04_HOME_1',process:'P04',label:'Home',state:'ON',connected:true,home:true};
      for(const [id,pin] of [['P06_LIGHT_GREEN',8],['P06_LIGHT_RED',9],['P06_BUZZER',10]])plant.units[id]={id,process:'P06',label:id,pin,output:false,configured:true,connected:true,active_level:'LOW',state:'READY'};
      plant.units.P06_MES={id:'P06_MES',process:'P06',label:'MES',state:'READY'};
      for(const code of ['P03','P05']){
        for(let i=1;i<=4;i++)plant.units[code+'_SCARA_J'+i]={id:code+'_SCARA_J'+i,process:code,label:'J'+i,enabled:true,connected:true,state:'READY',position:0,pose_valid:true,xyzr:[0,0,0,0]};
        plant.units[code+'_SCARA_GRIPPER']={id:code+'_SCARA_GRIPPER',process:code,label:'Gripper',enabled:true,state:'OPEN'};
      }
      draft=structuredClone(config);bind();update(plant);document.querySelector('[data-page="manual"]').click();
    },config);
    // Drive release is a first-class P01 manual control, beside ENABLE and STOP.
    for(let i=0;i<4;i++){
      const bar=page.locator(`[data-unit-card="P01_E${i}"] .p01-powerbar`);
      assert.equal(await bar.locator('[data-command="DISABLE"]').count(),1);
      const sizes=await bar.locator('button').evaluateAll(nodes=>nodes.map(n=>n.getBoundingClientRect()));
      assert(sizes.every(r=>r.height>=40&&r.width>50),'All power actions remain usable');
    }
    const disable=page.locator('[data-unit="P01_E0"][data-command="DISABLE"]');
    await disable.click();
    assert.equal(await page.evaluate(()=>calls.filter(c=>c.path==='/api/unit/P01_E0/command'&&c.body?.action==='DISABLE').length),1);
    await page.evaluate(()=>{plant.units.P01_E0.enabled=false;renderManual();});
    assert.equal(await disable.isEnabled(),true,'Allow explicit disable even when enable feedback is unknown');
    await page.evaluate(()=>{plant.units.P01_E0.enabled=true;renderManual();});
    // Press on a nested label, change both sensor and another motor's state,
    // then release. The ordinary click must reach the original control once.
    await page.locator('[data-manual-amount="P01_E0_FORWARD"]').fill('123');
    const forward=page.locator('[data-unit="P01_E0"][data-command="FORWARD"]');
    await forward.scrollIntoViewIfNeeded();const box=await forward.boundingBox();
    await page.mouse.move(box.x+box.width/2,box.y+box.height/2);await page.mouse.down();
    await page.evaluate(()=>{window.pressed=document.querySelector('[data-unit="P01_E0"][data-command="FORWARD"]');plant.units.P00_MATERIAL.detected_count=2;plant.units.P01_E1.state='ERROR';update(structuredClone(plant));if(document.querySelector('[data-unit="P01_E0"][data-command="FORWARD"]')!==pressed)throw Error('Pressed button replaced');});
    await page.mouse.up();assert.equal(await page.evaluate(()=>calls.filter(c=>c.body?.action==='FORWARD').length),1);
    // Configuration input and native select retain focus and node identity.
    await page.evaluate(()=>{$('[data-page="setting"]').click();settingTab='P03P04P05';renderSettings();});
    const name=page.locator('#press-position-name');await name.fill('MY_POSITION');
    await page.evaluate(()=>{const input=$('#press-position-name');input.setSelectionRange(2,5);plant.units.P03_SCARA_J1.position=12;update(structuredClone(plant));if(input!==document.activeElement||input!==$('#press-position-name')||input.value!=='MY_POSITION'||input.selectionStart!==2)throw Error('Input focus/value/caret lost');});
    await page.evaluate(()=>{settingTab='P06';renderSettings();const select=$('[data-config="p06.relay_active_level"]');select.focus();for(let i=0;i<30;i++)update(structuredClone(plant));if(select!==document.activeElement||select!==$('[data-config="p06.relay_active_level"]'))throw Error('Select replaced');});
    // All manual pages retain their controls through changing readings.
    await page.evaluate(()=>{
      $('[data-page="manual"]').click();
      for(const code of ['P00','P02','P03','P05','P06']){
        manualProcess=code;renderManual();const buttons=[...$('#manual-controls').querySelectorAll('button')];
        if(code==='P03'||code==='P05')plant.units[code+'_SCARA_GRIPPER'].state='CLOSE';
        if(code==='P06')plant.units.P06_LIGHT_RED.error='Status changed';
        for(let i=0;i<10;i++){plant.units.P00_MATERIAL.received_frames=i;update(structuredClone(plant));}
        if(buttons.some(b=>!b.isConnected))throw Error(code+' button replaced');
      }
      manualProcess='P01';renderManual();const station=$('#manual-mimic [data-process="P02"]');plant.processes.P02.status='DONE';renderMimics();if(station!==$('#manual-mimic [data-process="P02"]'))throw Error('Station replaced');station.click();if(manualProcess!=='P02')throw Error('Station click lost');
      manualProcess='P01';renderManual();const move=$('[data-unit="P01_E0"][data-command="FORWARD"]');plant.units.P01_E0.enabled=false;renderManual();if(!move.disabled)throw Error('Disable interlock not updated');
    });
    // Existing immediate STOP semantics: mouse press + click only sends once;
    // keyboard activation also sends exactly once.
    const stop=page.locator('[data-unit="P01_E0"][data-command="STOP"]');
    await stop.click();await stop.focus();await page.keyboard.press('Enter');
    assert.equal(await page.evaluate(()=>calls.filter(c=>c.body?.action==='STOP').length),2);
    await page.evaluate(()=>{
      draft=structuredClone(plant.config);settingTab='P01';renderSettings();
      const feed=$('[data-config="p01.axes.E0.distance_mm"]');
      feed.value='19.25';feed.dispatchEvent(new Event('input',{bubbles:true}));
      if(draft.p01.axes.E0.distance_mm!==19.25)throw Error('User feed mm input lost');
      settingTab='P03P04P05';renderSettings();
      const down=$('[data-config="p04.down_mm"]');down.value='3.25';down.dispatchEvent(new Event('input',{bubbles:true}));
      if(draft.p04.down_mm!==3.25)throw Error('User press mm input lost');
      if(![...document.querySelectorAll('[data-distance-summary="p04.down_mm"]')].every(n=>n.textContent.includes('3.25 mm')))throw Error('Recipe mm preview stale');
      $('#save-config').click();
      if(calls.at(-1).body.p04.down_mm!==3.25||calls.at(-1).body.p01.axes.E0.distance_mm!==19.25)throw Error('Saved config must contain user mm');
      manualProcess='P01';plant.units.P01_E0.enabled=true;renderManual();
      const amount=$('[data-manual-amount="P01_E0_FORWARD"]');amount.value='38.5';amount.dispatchEvent(new Event('input',{bubbles:true}));
      $('[data-unit="P01_E0"][data-command="FORWARD"]').click();
      if(calls.at(-1).body.distance_mm!==38.5||'pulses' in calls.at(-1).body)throw Error('Manual request must send user mm');
    });
    await page.evaluate(()=>{
      plant.config.scara_p05.shared_with='P03';
      draft=structuredClone(plant.config);draft.press_recipe.actions=[{type:'DOBOT_HOME',robot:'P03'},{type:'DOBOT_MOVE',robot:'P03',mode:'JOINT',reference:'HOME',values:[1,0,0,0],speed:10}];
      settingTab='P03P04P05';$('[data-page="setting"]').click();
      if(!$('[data-recipe-reference="1"]')||$('[data-recipe-reference="1"]').value!=='HOME')throw Error('Home reference not shown');
      if(!$('[data-config="scara.port"]')||$('[data-config="scara_p05.port"]'))throw Error('Shared P05 must use P03 USB setting');
      const mode=$('[data-recipe-mode="1"]');
      if([...mode.options].some(o=>o.value==='XYZR'))throw Error('Recipe must only offer Joint angles');
      const reference=$('[data-recipe-reference="1"]');reference.value='HOME';reference.dispatchEvent(new Event('change',{bubbles:true}));
      if(draft.press_recipe.actions[1].mode!=='JOINT')throw Error('Home offset must use joint mode');
      for(let i=0;i<4;i++){const input=$(`[data-recipe-value="1"][data-coordinate="${i}"]`);input.value=String(i===0?1:0);input.dispatchEvent(new Event('input',{bubbles:true}));}
      const action=draft.press_recipe.actions[1];
      if(action.reference!=='HOME'||JSON.stringify(action.values)!=='[1,0,0,0]'||action.speed!==10)throw Error('Home offset edit lost');
    });
    await page.evaluate(()=>{
      $('[data-page="main"]').click();
      const home=$('[data-api="/api/line/home"]'),before=calls.length;
      home.click();if(calls.length!==before+1||calls.at(-1).path!=='/api/line/home')throw Error('MAIN HOME route');
      for(let i=0;i<10;i++)update(structuredClone(plant));
      if(home!==$('[data-api="/api/line/home"]'))throw Error('MAIN HOME replaced');
      $('[data-page="manual"]').click();
      for(const code of plant.process_order){
        manualProcess=code;renderManual();
        const button=$('#home-process'),motor=['P01','P03','P04','P05'].includes(code);
        if(button.hidden===motor)throw Error(code+' HOME visibility');
        if(motor){const count=calls.length;button.click();if(calls.length!==count+1||calls.at(-1).path!==`/api/process/${code}/home`)throw Error(code+' HOME dispatch');}
      }
      manualProcess='P01';renderManual();
      if(!$('#manual-controls').textContent.includes('EE-SX 센서 설치 대기'))throw Error('P01 pending origin status missing');
      if(!$('#manual-controls [data-command="HOME"]'))throw Error('P01 axis HOME missing');
      manualProcess='P04';renderManual();
      if(!$('#manual-controls [data-command="HOME"]'))throw Error('P04 axis HOME missing');
      plant.line={...plant.line,status:'RUNNING',mode:'HOMING'};update(structuredClone(plant));
      if(!home.disabled||!$('#home-process').disabled||!$('[data-api="/api/line/start"]').disabled)throw Error('Homing buttons not locked');
      if(!$('#manual-lock').classList.contains('show'))throw Error('Homing manual lock missing');
      plant.line={...plant.line,status:'IDLE',mode:'IDLE'};update(structuredClone(plant));
      if(home.disabled||$('#home-process').disabled)throw Error('Home completion did not release buttons');
    });
    console.log('PASS: stable clicks, mm inputs, STOP controls, MAIN/process HOME dispatch, pending EE-SX status and HOMING interlocks. No hardware requests.');
  }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
