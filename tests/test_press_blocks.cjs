/* Unified vertical recipe UI regressions. No server or hardware access. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const root=path.resolve(__dirname,'..'),source=fs.readFileSync(path.join(root,'web/app.js'),'utf8'),css=fs.readFileSync(path.join(root,'web/styles.css'),'utf8');
const config=JSON.parse(fs.readFileSync(path.join(root,'settings/poc_config.json'),'utf8'));
const events={},nodes=new Map(),requests=[];let focused=null,scrolled=null;
function classList(){const values=new Set();return {add(...names){names.forEach(n=>values.add(n));},remove(...names){names.forEach(n=>values.delete(n));},toggle(){},contains(n){return values.has(n);}};}
function node(){return {dataset:{},innerHTML:'',classList:classList(),scrollTop:0,clientHeight:560,scrollHeight:10000,
  closest(){return null;},scrollTo(options){this.scrollTop=options.top;scrolled={node:this,options};},
  focus(options){focused={node:this,options};},scrollIntoView(options){scrolled={node:this,options};},
  getBoundingClientRect(){return {top:0,height:560};}};}
const selector='[data-recipe="press_recipe.actions"]';
function query(q){
  if(!nodes.has(q))nodes.set(q,node());
  const result=nodes.get(q),match=q.match(/nth-child\((\d+)\)$/);
  if(q.startsWith(selector)&&match){const flow=query(selector),index=Number(match[1])-1;
    result.closest=s=>s==='.recipe'?flow:null;
    result.getBoundingClientRect=()=>({top:4+index*64-flow.scrollTop,height:54});
  }return result;
}
const ctx=vm.createContext({testConfig:config,document:{querySelector:query,querySelectorAll:()=>[],
  addEventListener:(name,fn)=>(events[name]??=[]).push(fn)},window:{addEventListener(){}},
  setInterval(){},setTimeout(){},clearTimeout(){},
  fetch:(url,options)=>{requests.push({url,options});return options?Promise.resolve({json:async()=>({ok:true,message:'saved'})}):new Promise(()=>{});},EventSource:class{}});
vm.runInContext(source,ctx);
const run=code=>vm.runInContext(code,ctx);
run("draft=JSON.parse(JSON.stringify(testConfig));plant={config:testConfig,units:{}};settingTab='P03P04P05';renderSettings();");
const actions=()=>JSON.parse(run('JSON.stringify(draft.press_recipe.actions)')),html=()=>query('#settings-content').innerHTML;
function target(selector,dataset={},extra={}){const t={id:'',dataset,disabled:false,type:'',value:'',matches(){return false;},...extra};if(!t.closest)t.closest=q=>q===selector?t:null;return t;}
async function dispatch(name,t){for(const fn of events[name]||[])await fn({target:t,button:0,preventDefault(){}});}
const recipeRoot={dataset:{recipe:'press_recipe.actions'}};
function rowButton(dataset){return target('.recipe-row button',dataset,{closest(q){return q==='.recipe-row button'?this:q==='[data-recipe]'?recipeRoot:null;}});}
function editor(dataset,value,type='select-one'){return target('',dataset,{value,type,matches:q=>type==='number'&&q==='input[data-recipe-target]',closest:q=>q==='.recipe-row'?{parentElement:recipeRoot}:q==='[data-recipe]'?recipeRoot:null});}
async function main(){
  const original=actions();
  assert.equal(config.press_recipe.version,3);assert(!('p05_actions' in config.scara));
  assert(html().includes('<h3>P03 · P04 · P05 통합 동작 순서</h3>'));
  assert(html().includes('engineering-split'));assert(html().includes('saved-position-scroll'));
  assert(html().includes('position-settings-stack'));
  const sections=[...html().matchAll(/<section\b[^>]*>([\s\S]*?)<\/section>/g)].map(match=>match[1]);
  const pressPosition=sections.find(section=>section.includes('<h3>프레스 현재위치</h3>'));
  const pressManual=sections.find(section=>section.includes('<h3>P04 USB CAN</h3>'));
  assert(pressPosition.includes('id="press-position-name"'));
  assert(pressPosition.includes('id="save-press-position"'));
  assert(!pressManual.includes('id="press-position-name"'));
  assert(pressManual.includes('data-config="p04.speed"'));for(const field of ['p04.down_steps','p04.up_steps'])assert(!html().includes('data-config="'+field+'"'));
  assert(html().indexOf('<h3>P03 투입용 Dobot 현재 위치</h3>')<html().indexOf('<h3>프레스 현재위치</h3>'));
  assert(html().indexOf('<h3>프레스 현재위치</h3>')<html().indexOf('<h3>P05 배출용 Dobot 현재 위치</h3>'));
  assert(html().indexOf('<h3>P05 배출용 Dobot 현재 위치</h3>')<html().indexOf('<h3>P04 USB CAN</h3>'));
  assert(html().indexOf('<h3>프레스 현재위치</h3>')<html().indexOf('<h3>P04 USB CAN</h3>'));
  assert.equal((html().match(/data-recipe="/g)||[]).length,1);
  assert.equal((html().match(/data-recipe-type=/g)||[]).length,original.length);
  assert(!html().includes('block-flow'));assert(!html().includes('P05 동작 순서</h3>'));
  assert(query('#setting-tabs').innerHTML.includes('data-setting-tab="P03P04P05">P03 · P04 · P05</button>'));
  for(const id of ['scara-position-name-P03','scara-position-name-P05','press-position-name','save-press-position'])assert.equal(html().split('id="'+id+'"').length-1,1);
  for(const tone of ['scara','gripper','press','wait'])assert(css.includes('.recipe-tone-'+tone));
  assert(css.includes('overflow-y: auto'));assert(!css.includes('.block-flow'));assert(!css.includes('padding-inline: max('));
  await dispatch('click',target('[data-recipe-add]',{recipeAdd:'press_recipe.actions',recipeTypes:'P03:SCARA_MOVE,WAIT'}));
  const last=actions().length-1;
  await dispatch('change',editor({recipeType:String(last)},'WAIT'));
  await dispatch('input',editor({recipeTarget:String(last)},'1.7','number'));
  await dispatch('change',editor({recipeTarget:String(last)},'1.7','number'));
  assert.deepEqual(actions()[last],{type:'WAIT',target:1.7});
  await dispatch('click',rowButton({recipeUp:String(last)}));
  assert.deepEqual(actions()[last-1],{type:'WAIT',target:1.7});
  assert.equal(focused.node,query(selector+' .recipe-row:nth-child('+last+')'));
  assert(focused.node.classList.contains('recipe-moved'));
  await dispatch('change',editor({recipeType:String(last-1)},'P03:GRIPPER'));
  await dispatch('change',editor({recipeTarget:String(last-1)},'CLOSE'));
  assert.deepEqual(actions()[last-1],{type:'GRIPPER',target:'CLOSE',robot:'P03'});
  await dispatch('click',rowButton({recipeDel:String(last-1)}));assert.deepEqual(actions(),original);
  run("draft.press_recipe.actions=[{type:'WAIT',target:1},{type:'WAIT',target:2},{type:'WAIT',target:3}];renderSettings()");
  const flow=query(selector);flow.scrollTop=0;
  await dispatch('click',rowButton({recipeDown:'0'}));
  assert.deepEqual(actions().map(a=>a.target),[2,1,3]);
  assert.equal(scrolled.node,flow);assert.equal(flow.scrollTop,64);
  assert.equal(focused.node.getBoundingClientRect().top,4);assert.equal(focused.options.preventScroll,true);
  assert(focused.node.classList.contains('recipe-moved'));
  await dispatch('click',rowButton({recipeUp:'1'}));
  assert.deepEqual(actions().map(a=>a.target),[1,2,3]);assert.equal(flow.scrollTop,0);
  const normalHeight=flow.scrollHeight;flow.scrollHeight=580;
  await dispatch('click',rowButton({recipeDown:'0'}));assert.equal(flow.scrollTop,20);
  flow.scrollHeight=normalHeight;flow.scrollTop=240;query('.saved-position-scroll').scrollTop=123;
  run('renderSettings()');assert.equal(flow.scrollTop,240);assert.equal(query('.saved-position-scroll').scrollTop,123);
  run("draft.press_recipe.actions=[{type:'WAIT',target:0}];renderSettings()");
  assert(html().includes('data-recipe-target="0" value="0"'));
  await dispatch('click',rowButton({recipeDel:'0'}));assert.equal(actions().length,1);
  assert(html().includes('data-recipe-del="0" disabled'));
  run("draft.press_recipe.actions=[{type:'SCARA_MOVE',target:'<missing>',robot:'P03'}];renderSettings()");
  assert(html().includes('&lt;missing&gt; (확인 필요)'));assert(!html().includes('<missing>'));
  assert(css.includes('animation: recipe-move-feedback 240ms ease-out'));
  assert(css.includes('@media (prefers-reduced-motion: reduce)'));
  assert.deepEqual(requests.map(r=>r.url),['/api/status']);
  await dispatch('click',target('',{},{id:'save-config'}));
  assert.equal(requests.at(-1).url,'/api/config');
  const payload=JSON.parse(requests.at(-1).options.body);
  assert.deepEqual(payload.press_recipe.actions,actions());assert.equal(payload.press_recipe.version,3);
  assert(!('p05_actions' in payload.scara));assert.equal(run('dirty'),false);
  // Robot selection, saved-position options, deletion and manual panels stay isolated.
  run("draft.scara.positions={LOAD_ONLY:[1,2,3,4],SHARED:[0,0,0,0]};draft.scara_p05.positions={OUT_ONLY:[5,6,7,8],SHARED:[1,1,1,1]};draft.press_recipe.actions=[{type:'SCARA_MOVE',target:'LOAD_ONLY',robot:'P03'}];renderSettings()");
  await dispatch('change',editor({recipeType:'0'},'P05:SCARA_MOVE'));
  assert.deepEqual(actions()[0],{type:'SCARA_MOVE',target:'OUT_ONLY',robot:'P05'});
  assert(run("actionTarget('SCARA_MOVE','OUT_ONLY',0,'P05')").includes('OUT_ONLY'));
  assert(!run("actionTarget('SCARA_MOVE','OUT_ONLY',0,'P05')").includes('LOAD_ONLY'));
  await dispatch('change',editor({recipeType:'0'},'P05:GRIPPER'));
  assert.deepEqual(actions()[0],{type:'GRIPPER',target:'OPEN',robot:'P05'});
  await dispatch('change',editor({recipeType:'0'},'WAIT'));
  assert(!('robot' in actions()[0]));
  run("draft.press_recipe.actions=[{type:'SCARA_MOVE',target:'SHARED',robot:'P03'}];renderSettings()");
  await dispatch('click',target('[data-delete-scara]',{deleteScara:'SHARED',robot:'P05'}));
  assert(run("'SHARED' in draft.scara.positions"));
  assert(!run("'SHARED' in draft.scara_p05.positions"));
  await dispatch('click',target('[data-delete-scara]',{deleteScara:'SHARED',robot:'P03'}));
  assert(run("'SHARED' in draft.scara.positions"));
  for(const code of ['P03','P05']){
    query('#scara-position-name-'+code).value='SAVED_'+code;
    await dispatch('click',target('[data-save-scara]',{saveScara:code}));
    assert.equal(requests.at(-1).url,'/api/scara/'+code+'/save-current');
    assert.equal(JSON.parse(requests.at(-1).options.body).name,'SAVED_'+code);
    run("plant.config=draft;plant.line={status:'IDLE'};plant.processes={P03:{name:'load',summary:''},P05:{name:'unload',summary:''}};for(const code of ['P03','P05']){for(let i=1;i<=4;i++){const id=code+'_SCARA_J'+i;plant.units[id]={id,process:code,enabled:true,connected:true,simulated:true,pose_valid:true,xyzr:[200,20,120,10],state:'READY',position:i};}const id=code+'_SCARA_GRIPPER';plant.units[id]={id,process:code,enabled:true,state:'OPEN'};}");
    run("manualProcess='"+code+"';renderManual()");
    const manual=query('#manual-controls').innerHTML;
    assert(manual.includes('data-unit="'+code+'_SCARA_J1"'));
    assert(manual.includes('HOME · 원점복귀'));
    const gripperTag=()=>query('#manual-controls').innerHTML.match(new RegExp('<button[^>]*data-unit="'+code+'_SCARA_GRIPPER"[^>]*data-command="STOP"[^>]*>'))?.[0];
    assert(gripperTag());assert(!gripperTag().includes('disabled'));
    run(`plant.line.status='RUNNING';plant.units.${code}_SCARA_GRIPPER.enabled=false;renderManual()`);
    assert(!gripperTag().includes('disabled'));
    const stops='[data-unit][data-command="STOP"], [data-api="/api/line/stop"]';
    await dispatch('pointerdown',target(stops,{unit:code+'_SCARA_GRIPPER',command:'STOP'}));
    assert.equal(requests.at(-1).url,'/api/unit/'+code+'_SCARA_GRIPPER/command');
    assert.deepEqual(JSON.parse(requests.at(-1).options.body),{action:'STOP'});
    run(`plant.line.status='IDLE';plant.units.${code}_SCARA_GRIPPER.drive_enabled=false;renderManual()`);
    assert(query('#manual-controls').innerHTML.includes('구동 OFF'));
    const enableTag=query('#manual-controls').innerHTML.match(/<button[^>]*data-command="ENABLE"[^>]*>/)[0];
    assert(!enableTag.includes('disabled'));
    run(`plant.units.${code}_SCARA_GRIPPER.enabled=true;renderManual()`);
    await dispatch('click',target('[data-unit]',{unit:code+'_SCARA_J1',command:'HOME'}));
    assert.equal(requests.at(-1).url,'/api/unit/'+code+'_SCARA_J1/command');
    assert.equal(JSON.parse(requests.at(-1).options.body).action,'HOME');
    assert(!manual.includes('data-unit="'+(code==='P03'?'P05':'P03')+'_SCARA_J1"'));
    assert(manual.includes(code==='P03'?'LOAD_ONLY':'OUT_ONLY'));
    query('#manual-position').value='TARGET';
    await dispatch('click',target('',{robot:code},{id:'move-saved'}));
    assert.equal(requests.at(-1).url,'/api/scara/'+code+'/move-saved');
    assert(!query('#manual-controls').innerHTML.includes('data-dobot-mode="XYZR"'));
    assert(query('#manual-controls').innerHTML.includes('목표 J1'));
    await dispatch('click',target('[data-dobot-current]'));
    await dispatch('input',target('',{dobotTarget:'0'},{value:'15.5'}));
    run('renderManual()');
    assert.equal(run(`dobotForms.${code}.JOINT[0]`),'15.5');
    await dispatch('click',target('[data-dobot-move]'));
    assert.equal(requests.at(-1).url,'/api/scara/'+code+'/move');
    const jointValues=JSON.parse(run(`JSON.stringify([2,3,4].map(i=>plant.units['${code}_SCARA_J'+i].position))`));
    assert.deepEqual(JSON.parse(requests.at(-1).options.body),{mode:'JOINT',values:[15.5,...jointValues],speed:10});
    await dispatch('click',target('[data-dobot-mode]',{dobotMode:'JOINT'}));
    await dispatch('click',target('[data-dobot-jog]',{dobotJog:'2',direction:'-1'}));
    assert.deepEqual(JSON.parse(requests.at(-1).options.body),{mode:'JOINT',axis:2,delta:-1,speed:10});
  }
  run("draft.press_recipe.actions=[makeAction('P05:DOBOT_MOVE')];renderSettings()");
  assert(html().includes('P05 Dobot 직접 이동'));assert(html().includes('data-recipe-mode="0"'));
  const direct=(dataset,value)=>target('',dataset,{value,closest:q=>q==='[data-recipe]'?recipeRoot:null});
  assert(!html().includes('<option value="XYZR"'));
  await dispatch('change',direct({recipeMode:'0'},'JOINT'));
  for(const [i,value] of ['200','20','100','10'].entries())await dispatch('input',direct({recipeValue:'0',coordinate:String(i)},value));
  await dispatch('input',direct({recipeSpeed:'0'},'12'));
  assert.deepEqual(actions()[0],{type:'DOBOT_MOVE',robot:'P05',mode:'JOINT',values:[200,20,100,10],speed:12});
  await dispatch('click',target('',{},{id:'save-config'}));
  assert.equal(requests.at(-1).url,'/api/config');
  assert.deepEqual(JSON.parse(requests.at(-1).options.body).press_recipe.actions,actions());
  await dispatch('change',direct({recipeMode:'0'},'JOINT'));
  assert.deepEqual(actions()[0].values,[null,null,null,null]);
  assert.equal(actions()[0].speed,12);

  const automatic=JSON.stringify(config);
  run("plant.units.P00_MATERIAL={detected:true};plant.processes.P01={name:'feed'};plant.processes.P04={name:'press'};plant.processes.P06={name:'output'};plant.line.status='IDLE';for(let i=0;i<4;i++){let id='P01_E'+i;plant.units[id]={id,enabled:true,state:'READY',position:0};plant.units['P01_HOME_'+(i+1)]={connected:true,home:false};}plant.units.P04_PRESS_AXIS={id:'P04_PRESS_AXIS',enabled:true,homed:true,state:'READY',position:0};plant.units.P04_HOME_1={connected:true,home:true}");
  for(const [code,unit,forward,reverse,field] of [['P01','P01_E2','FORWARD','REVERSE','distance_mm'],['P04','P04_PRESS_AXIS','DOWN','UP','distance_mm']]){
    run(`manualProcess='${code}';renderManual()`);
    for(const [action,value] of [[forward,'38.5'],[reverse,'1.25']]){
      const key=unit+'_'+action;
      assert(query('#manual-controls').innerHTML.includes('data-manual-amount="'+key+'"'));
      await dispatch('input',target('',{manualAmount:key},{value}));
      const before=query('#manual-controls').innerHTML;
      run(`plant.units.${unit}.position=200;renderManual()`);
      assert.equal(query('#manual-controls').innerHTML,before); // SSE retains input DOM.
      assert.equal(run(`manualAmounts['${key}']`),value);
      await dispatch('click',target('[data-unit]',{unit,command:action}));
      assert.equal(requests.at(-1).url,'/api/unit/'+unit+'/command');
      assert.deepEqual(JSON.parse(requests.at(-1).options.body),{action,[field]:Number(value)});
      for(const invalid of ['','0','-1','NaN','999999999999']){
        await dispatch('input',target('',{manualAmount:key},{value:invalid}));
        const count=requests.length;
        await dispatch('click',target('[data-unit]',{unit,command:action}));
        assert.equal(requests.length,count);
      }
    }
  }
  assert.equal(JSON.stringify(config),automatic);
  run("settingTab='P01';renderSettings()");
  assert(html().includes('p01.axes.E0.distance_mm'));
  assert(html().includes('p01.axes.E0.home_search_mm'));
  assert(html().includes('p01.axes.E0.home_step_mm'));
  assert(!html().includes('1회전'));assert(!html().includes('환산 기준'));
  assert(!html().includes('data-config="p01.pulse_per_rev"'));
  assert(!html().includes('manual_forward_pulses'));assert(!html().includes('manual_reverse_pulses'));
  run("settingTab='P06';renderSettings()");
  assert(html().includes('배출 · 경광등'));assert(!html().includes('예약'));
  assert(html().includes('data-config="p06.green_hold_seconds"'));
  assert(!query('#setting-tabs').innerHTML.includes('P07'));
  const requestCount=requests.length;
  run("settingTab='P03P04P05';draft.press_recipe.actions=[makeAction('P05:DOBOT_HOME')];renderSettings()");
  assert.deepEqual(actions()[0],{type:'DOBOT_HOME',robot:'P05'});
  assert(html().includes('MAIN / MANUAL에서 별도 호밍 · 사이클에서는 생략'));
  run('delete draft.press_recipe.version;renderSettings()');
  assert.equal(query('#save-config').disabled,true);
  assert(html().includes('재시작 전에는 설정을 저장할 수 없습니다.'));
  for(const id of ['save-config','save-press-position'])await dispatch('click',target('',{},{id}));
  await dispatch('click',target('[data-save-scara]',{saveScara:'P05'}));
  assert.equal(requests.length,requestCount); // Old live backend cannot overwrite migrated settings.
  console.log('PASS: unified vertical layout, editing, ordering, focus/blur, bounded scroll, persistence, safe save payload.');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
