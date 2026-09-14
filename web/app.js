const $=(q,r=document)=>r.querySelector(q), $$=(q,r=document)=>[...r.querySelectorAll(q)];
let plant=null, draft=null, dirty=false, manualProcess='P01', settingTab='LINE', toastTimer, logPaused=false, frozenLogs=[];
let visionFrameUrl=null;
const visionPanels=['vision','manual-vision'];
const processIcons={P00:'MATERIAL SENSOR',P01:'ACTUATOR ×4',P02:'CAMERA',P03:'DOBOT LOAD',P04:'CAN PRESS',P05:'DOBOT OUT',P06:'OUTPUT · LIGHT'};
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
// UI terminology only: keep SCARA API, recipe and unit identifiers unchanged.
const displayText=v=>String(v??'').replace(/SCARA|스카라/gi,'Dobot');
const displayEsc=v=>esc(displayText(v));
// Keep controls alive across SSE updates: replacing a pressed node loses its click.
function renderHtml(root,html){
  if(!root.firstChild){root.innerHTML=html;return}
  const template=root.ownerDocument.createElement('template');template.innerHTML=html;
  const key=node=>node.nodeType===1?(node.id||[...node.attributes].filter(a=>a.name.startsWith('data-')).map(a=>`${a.name}=${a.value}`).sort().join('|')):'';
  const compatible=(a,b)=>a.nodeType===b.nodeType&&a.nodeName===b.nodeName&&key(a)===key(b);
  function patch(parent,wanted){
    let current=parent.firstChild;
    for(const next of [...wanted.childNodes]){
      if(!current){parent.appendChild(next.cloneNode(true));continue}
      if(!compatible(current,next)){
        const match=key(next)?[...parent.childNodes].find(n=>compatible(n,next)):null;
        if(match){parent.insertBefore(match,current);current=match}
        else{parent.insertBefore(next.cloneNode(true),current);continue}
      }
      if(current.nodeType!==1){if(current.nodeValue!==next.nodeValue)current.nodeValue=next.nodeValue}
      else{
        const selectionChanged=current.tagName==='SELECT'&&[...current.options].filter(o=>o.hasAttribute('selected')).map(o=>o.value).join('\0')!==[...next.options].filter(o=>o.hasAttribute('selected')).map(o=>o.value).join('\0');
        for(const attr of [...current.attributes])if(!next.hasAttribute(attr.name))current.removeAttribute(attr.name);
        for(const attr of [...next.attributes])if(current.getAttribute(attr.name)!==attr.value)current.setAttribute(attr.name,attr.value);
        patch(current,next);
        if(current.tagName==='INPUT'&&next.hasAttribute('value')&&current.value!==next.value)current.value=next.value;
        if(selectionChanged)current.value=next.value;
      }
      current=current.nextSibling;
    }
    while(current){const old=current;current=current.nextSibling;old.remove()}
  }
  patch(root,template.content);
}
function toast(message,bad=false){const el=$('#toast');el.textContent=displayText(message);el.classList.toggle('bad',bad);el.classList.add('show');clearTimeout(toastTimer);toastTimer=setTimeout(()=>el.classList.remove('show'),2400)}
async function api(path,body={},quiet=false){try{const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const d=await r.json();if(d.state)update(d.state);if(!quiet)toast(d.message,!d.ok);return d}catch(e){if(!quiet)toast('서버 연결 실패',true);return{ok:false}}}
function unitsFor(code){if(!plant)return[];return Object.values(plant.units).filter(u=>u.process===code)}
function stationHtml(code,selectable=false){const p=plant.processes[code],count=unitsFor(code).length;return `<article class="station ${esc(p.status)} ${manualProcess===code?'selected':''}" data-process="${code}"><span class="status-chip">${esc(p.status)}</span><div class="machine"><div class="machine-icon"></div></div><h3>${code} · ${displayEsc(p.name)}</h3><p>${esc(processIcons[code])}</p><div class="unit-dots">${Array.from({length:Math.max(1,Math.min(count,8))},()=>'<i></i>').join('')}</div></article>`}
function renderMimics(){const html=plant.process_order.map(c=>stationHtml(c)).join('');renderHtml($('#main-mimic'),html);renderHtml($('#manual-mimic'),plant.process_order.map(c=>stationHtml(c,true)).join(''))}
function renderMain(){const l=plant.line;$('#kpi-status').textContent=l.status;$('#kpi-message').textContent=displayText(l.message);$('#kpi-active').textContent=l.active_process||'—';$('#kpi-process-name').textContent=l.active_process?displayText(plant.processes[l.active_process].name):'대기';$('#kpi-count').textContent=l.cycle_count;$('#kpi-lamp').textContent=l.lamp||'OFF';$('#main-process-flow').innerHTML=plant.process_order.map(code=>{const p=plant.processes[code],pct=Math.max(0,Math.min(10,Math.round((Number(p.progress)||0)/10)))*10;return `<article class="process-card ${esc(p.status)} progress-${pct}"><div class="pc-top"><span class="code">${code}</span><span class="state">${esc(p.status)}</span></div><h3>${displayEsc(p.name)}</h3><p>${displayEsc(p.message||p.summary)}</p><small>${p.repeat_total>1?`${p.repeat_current}/${p.repeat_total}`:`${p.progress||0}%`}</small></article>`}).join('');
  $('#main-logs').innerHTML=plant.logs.slice(0,10).map(x=>`<div class="log-row"><span>${esc(x.time)}</span><b>${displayEsc(x.process||x.type)}</b><div>${displayEsc(x.message)}</div></div>`).join('')||'<div class="empty">이벤트 없음</div>';
  const f=l.fault;$('#alarm-content').innerHTML=f?`<div class="alarm-box"><b>${esc(f.code)} · ${displayEsc(f.title)}</b><p>${displayEsc(f.message)}</p><small>${f.ack?'확인됨':'확인 필요'}</small></div>`:'<div class="empty">활성 알람 없음</div>';
  const v=l.vision||{},inspecting=plant.units.P02_CAMERA?.state==='INSPECTING';
  const result=inspecting?'INSPECTING':(['OK','NG','NOT_READY','CAMERA_OK'].includes(v.result)?v.result:'WAIT');
  const camera=plant.units.P02_CAMERA||{},live=camera.stream_status==='LIVE';
  const statuses={SIMULATION:'SIMULATION',SEARCHING:'카메라 검색 중',CONNECTING:'연결 중',LIVE:'LIVE',RETRYING:'재연결 중',SDK_MISSING:'SDK 설치 필요',CLOSED:'영상 수신 중지'};
  for(const id of visionPanels){
    $(`#${id}-heading`).textContent=camera.camera_model?`Basler ${camera.camera_model}`:'Basler 카메라';
    $(`#${id}-result`).textContent=result==='CAMERA_OK'?'카메라 확인':result;$(`#${id}-result`).dataset.result=result;
    $(`#${id}-reason`).textContent=inspecting?'검사 중':(v.reason||'검사 대기');
    $(`#${id}-time`).textContent=inspecting?'':(v.time||'');
    $(`#${id}-connection`).textContent=statuses[camera.stream_status]||'서버 재시작 필요';
    $(`#${id}-connection`).dataset.live=String(live);
    $(`#${id}-stream-message`).textContent=camera.stream_message||'카메라 자동 연결 기능 적용을 위해 서버를 재시작하세요.';
    $(`#${id}-note`).textContent=camera.simulated?'SIM 모드 · 검사 결과는 시뮬레이션입니다.':plant.config?.p02?.inspection_mode==='CAMERA_CHECK'?'임시 사이클 시험 · 카메라 영상 수신 시 통과 · AI 미검사':'실제 영상 수신 · OK/NG 판정 알고리즘은 아직 미설정입니다.';
    $(`#${id}-resolution`).textContent=`${camera.width||2592} × ${camera.height||1944}${camera.color_mode?' · '+camera.color_mode:''}${camera.camera_serial?' · S/N '+camera.camera_serial:''}`;
  }
  if(!live)clearVisionFrame();
}
function clearVisionFrame(){
  for(const id of visionPanels){
    const frame=$(`#${id}-frame`);frame.hidden=true;$(`#${id}-placeholder`).hidden=false;
    frame.onload=null;frame.onerror=null;if(visionFrameUrl)frame.removeAttribute('src');
  }
  if(visionFrameUrl){URL.revokeObjectURL(visionFrameUrl);visionFrameUrl=null;}
}
function activeVisionPanel(){return $('#page-main').classList.contains('active')?'vision':$('#page-manual').classList.contains('active')&&manualProcess==='P02'?'manual-vision':null}
function syncManualVision(){
  const visible=manualProcess==='P02';
  $('#manual-vision-panel').hidden=!visible;
  $('#manual-workspace').classList.toggle('has-vision',visible);
  if(!visible&&$('#page-manual').classList.contains('active'))clearVisionFrame();
}
async function pollVisionFrame(){
  try{
    if(document.hidden||!activeVisionPanel()||plant?.units.P02_CAMERA?.stream_status!=='LIVE'){clearVisionFrame();return}
    const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),2000);
    let response,blob;
    try{response=await fetch('/api/vision/frame.jpg',{cache:'no-store',signal:controller.signal});if(!response.ok)throw new Error('영상 수신 대기');blob=await response.blob();}finally{clearTimeout(timeout)}
    const id=activeVisionPanel();
    if(plant?.units.P02_CAMERA?.stream_status!=='LIVE'||document.hidden||!id){clearVisionFrame();return}
    for(const other of visionPanels.filter(value=>value!==id)){$(`#${other}-frame`).hidden=true;$(`#${other}-placeholder`).hidden=false;$(`#${other}-frame`).removeAttribute('src');}
    const next=URL.createObjectURL(blob),previous=visionFrameUrl;visionFrameUrl=next;
    const frame=$(`#${id}-frame`);
    frame.onload=()=>{if(visionFrameUrl===next&&activeVisionPanel()===id&&!document.hidden){frame.hidden=false;$(`#${id}-placeholder`).hidden=true;}};
    frame.onerror=()=>{if(visionFrameUrl===next)clearVisionFrame();};
    frame.src=next;if(previous)URL.revokeObjectURL(previous);
  }catch{clearVisionFrame();for(const id of visionPanels){$(`#${id}-connection`).textContent='영상 수신 대기';$(`#${id}-connection`).dataset.live='false';}}
  finally{setTimeout(pollVisionFrame,Math.max(70,1000/(plant?.config?.p02?.preview_fps||8)))}
}
setTimeout(pollVisionFrame,0);
function btn(unit,action,label,cls='',disabled=false){return `<button class="${cls}" data-unit="${unit}" data-command="${action}" ${disabled?'disabled':''}>${label}</button>`}
function holdBtn(unit,action,label,disabled=false){return `<button data-hold-unit="${unit}" data-hold-command="${action}" ${disabled?'disabled':''}>${label}</button>`}
function manualStateTone(unit){
  const state=String(unit.state??'').trim().toUpperCase();
  if(['READY','DONE','TEST_OK'].includes(state))return 'ready';
  if(['DOWN','UP','RUNNING','MOVING','JOGGING','FORWARD','REVERSE','CYCLE_FORWARD','CYCLE_REVERSE','INSPECTING','SENDING'].includes(state))return 'active';
  if(['HOMING','STOPPING','STARTING','ENABLING','DISABLING'].includes(state))return 'pending';
  if(state==='STOPPED')return 'stopped';
  if(['DISABLED','OFFLINE','OFF','IDLE','RESERVED','OPEN','CLOSE','ON'].includes(state))return 'neutral';
  if(['ERROR','FAULT','ALARM','NO_RESPONSE','HOME_NOT_FOUND','MOVE_TIMEOUT','ENABLE_REQUIRED'].includes(state)||unit.error)return 'error';
  return 'neutral';
}
function card(unit,reading,actions){return `<article class="unit-card" data-unit-card="${esc(unit.id)}"><header><h3>${displayEsc(unit.label)}</h3><span class="ustate ustate--${manualStateTone(unit)}">${esc(unit.state)}</span></header><div class="reading">${reading}</div><div class="unit-actions">${actions}</div></article>`}
function renderManual(){$('#manual-controls').dataset.manual=manualProcess;const p=plant.processes[manualProcess];$('#manual-code').textContent=manualProcess;$('#manual-name').textContent=displayText(p.name);$('#manual-summary').textContent=displayText(p.summary);$('#run-process').dataset.process=manualProcess;$('#run-process').textContent=['P03','P04','P05'].includes(manualProcess)?'P03 · P04 · P05 통합 순서 실행':'선택 공정 단독 실행';const homeButton=$('#home-process');if(homeButton){homeButton.hidden=!['P01','P03','P04','P05'].includes(manualProcess);homeButton.dataset.api=`/api/process/${manualProcess}/home`;homeButton.disabled=['RUNNING','PAUSED','PAUSE_REQUESTED'].includes(plant.line?.status);homeButton.textContent=`⌂ ${manualProcess} HOMING`;}const u=Object.fromEntries(Object.values(plant.units).map(x=>[x.id,x]));let html='';
  syncManualVision();
  if(manualProcess==='P00'){
    const m=u.P00_MATERIAL;
    const channels=m.channels?.length===4?m.channels:[0,1,2,3].map(i=>({sensor:i+1,pin:i+2,detected:!!m.detected}));
    html=card(m,`${m.detected_count??0} / 4 감지 · ${m.detected?'자동 투입 허용':'자동 투입 대기'} <small>${m.simulated?'SIMULATION':m.connected?'ARDUINO ONLINE':m.transport_connected?'USB 연결 · 데이터 대기':'Arduino USB 미연결'}</small>`,m.simulated?'<button class="primary" data-material-sim="true">SIM 자재 4개 있음</button><button data-material-sim="false">SIM 자재 없음</button>':'');
    html+=`<div class="material-channels">${channels.map(c=>`<article class="material-channel ${m.connected&&c.detected?'is-detected':''}"><span>ARDUINO D${c.pin}</span><h3>자재 ${c.sensor}</h3><b>${!m.connected?'입력 미연결':c.detected?'감지':'미감지'}</b></article>`).join('')}</div><p class="help">${esc(m.error||'자동 투입은 네 자재가 모두 감지되어야 시작합니다.')}${!m.simulated?`<br>${esc(m.serial_port||plant.config.p00.serial_port)} · ${Number(m.serial_baud||plant.config.p00.serial_baud)} baud<br>수신 ${Number(m.received_frames||0)}회 · 형식 오류 ${Number(m.invalid_frames||0)}회 · 마지막 정상 수신 ${m.last_sample?esc(new Date(m.last_sample*1000).toLocaleTimeString()):'대기'}`:''}<br>D2 → 자재 1 · D3 → 자재 2 · D4 → 자재 3 · D5 → 자재 4<br>자동 투입은 모터 사용 개수와 관계없이 4/4 필요 · 수동 이동은 감지 조건 없이 가능</p>`;
  }
  else if(['P01','P04'].includes(manualProcess)){renderLinearManual(u);return}
  else if(manualProcess==='P02'){const c=u.P02_CAMERA,check=!c.simulated&&plant.config.p02.inspection_mode==='CAMERA_CHECK';html+=card(c,`${c.result==='CAMERA_OK'?'카메라 확인':esc(c.result)} <small>${check?'AI 미검사 · 임시 사이클 시험':`SCORE ${Number(c.score||0).toFixed(2)}`}</small>`,btn(c.id,'INSPECT',check?'카메라 연결 확인':'검사하기','primary'))}
  else if(['P03','P05'].includes(manualProcess)){renderDobotManual(u);return}


  else {
    html='<p class="help">Uno D8 초록등 · D9 빨강등 · D10 부저를 각각 제어합니다. 나머지 릴레이 2개는 예비입니다.</p>';
    for(const a of [u.P06_LIGHT_GREEN,u.P06_LIGHT_RED,u.P06_BUZZER]){
      const blocked=!a.simulated&&(!a.configured||!a.connected);
      html+=card(a,`${a.output===null?'상태 미확인':a.output?'ON':'OFF'} <small>ARDUINO D${a.pin} · ${a.simulated?'SIMULATION':esc(a.active_level||'UNSET')}</small>`,btn(a.id,'ON','켜기',a.id==='P06_LIGHT_RED'?'danger':'primary',blocked)+btn(a.id,'CHANNEL_OFF','끄기','',blocked));
      if(a.error)html+=`<p class="help">${esc(a.error)}</p>`;
    }
    html+=`<div class="manual-unit-toolbar"><div><b>경광등 · 부저 전체 끄기</b><span>초록등·빨강등·부저를 함께 끕니다.</span></div>${btn('P06_LIGHT_RED','OFF','ALL OFF','danger')}</div>`;
    const m=u.P06_MES;html+=card(m,`${esc(m.state)} <small>MES</small>`,btn(m.id,'SUBMIT','실적 시험 전송','primary'));
  }
  $('#manual-controls').removeAttribute('data-dobot');$('#manual-controls').dataset.linear='';linearViewKey='';dobotViewKey='';
  renderHtml($('#manual-controls'),html);
}


const manualAmounts={};
let linearViewKey='';
function linearMmPerCount(unit){return unit==='P04_PRESS_AXIS'?1/600:38.5/Number(plant.config.p01.pulse_per_rev)}
function formatMm(value){return Number(value).toLocaleString('en-US',{maximumFractionDigits:6,useGrouping:false})}
function manualAmountFor(unit,action){
  const press=unit==='P04_PRESS_AXIS'&&['DOWN','UP'].includes(action);
  if(press||(/^P01_E[0-3]$/.test(unit)&&['FORWARD','REVERSE'].includes(action))){const quantum=linearMmPerCount(unit);return{key:`${unit}_${action}`,field:'distance_mm',min:quantum,max:(press?2147483647:4294967295)*quantum,label:'이동 거리 (mm)'}}
  return null;
}
function manualAmountInput(unit,action,label){
  const a=manualAmountFor(unit,action);return `<label class="manual-distance">${label} (mm)<input type="number" step="any" min="${a.min}" max="${a.max}" data-manual-amount="${a.key}" aria-label="${unit} ${label}" value="${esc(manualAmounts[a.key]??'')}" placeholder="이동 거리 (mm)"><small id="distance-${a.key}"></small></label>`;
}
function updateManualDistance(key){
  const out=$(`#distance-${key}`);if(!out)return;
  const raw=manualAmounts[key]??'',value=Number(raw);
  out.textContent=raw.trim()&&Number.isFinite(value)?`${formatMm(value)} mm`:'이동 거리(mm)를 입력하세요.';
}
function renderLinearManual(u){
  const code=manualProcess,root=$('#manual-controls'),items=code==='P01'?[0,1,2,3].map(i=>u[`P01_E${i}`]):[u.P04_PRESS_AXIS];
  const key=JSON.stringify([code,plant.line.status,plant.serial?.simulation,plant.units.P00_MATERIAL?.detected,plant.units.P00_MATERIAL?.detected_count,items.map(a=>plant.config.p01.axes[a.id.replace('P01_','')]?.installed),items.map(a=>[a.id,a.enabled,a.state,a.homed,a.connected,a.error]),code==='P01'?[1,2,3,4].map(i=>u[`P01_HOME_${i}`]?.connected):null]);
  if(key!==linearViewKey||root.dataset.linear!==code){
    linearViewKey=key;root.dataset.linear=code;root.dataset.dobot='';dobotViewKey='';
    let html=code==='P01'?`<div class="manual-unit-toolbar"><div><b>사용 ${items.filter(a=>plant.config.p01.axes[a.id.replace('P01_','')].installed!==false).length} / 4축</b><span>P00 자재 ${plant.units.P00_MATERIAL?.detected_count??0}/4 감지 · ${plant.units.P00_MATERIAL?.detected?'자동 투입 허용':'자동 투입 대기'}</span><span id="manual-comm-health"></span><span id="manual-feed-health"></span></div><button data-api="/api/p01/check">연결 · EN 상태 확인</button></div>${plant.serial?.simulation?'<div class="manual-unit-toolbar"><div><b>SIMULATION · 실제 모터는 움직이지 않습니다</b><span>실제 장비를 사용하려면 REAL 모드로 연결하세요.</span></div><button data-mode="REAL">REAL 모드로 연결</button></div>':''}`:'';
    for(const a of items){
      const enabled=!!a.enabled,press=code==='P04',home=press?u.P04_HOME_1:u[`P01_HOME_${Number(a.id.slice(-1))+1}`];
      const moving=['STARTING','FORWARD','REVERSE','CYCLE_FORWARD','CYCLE_REVERSE','MOVING','HOMING','DOWN','UP','STOPPING'].includes(a.state);
      const excluded=!press&&plant.config.p01.axes[a.id.replace('P01_','')].installed===false;
      const blocked=excluded||!enabled||moving||['RUNNING','PAUSED','PAUSE_REQUESTED'].includes(plant.line.status)||(press&&!a.homed);
      {
        const power='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v9M6.4 5.8a8 8 0 1 0 11.2 0"/></svg>';
        const stop='<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>';
        html+=`<article class="unit-card p01-card ${press?'p04-card':''}" data-unit-card="${esc(a.id)}"><header><div class="p01-title"><span class="p01-axis">${press?'P04':'E'+a.id.slice(-1)}</span><h3>${displayEsc(a.label)}</h3></div><span class="ustate ustate--${manualStateTone(a)}">${excluded?'임시 제외':esc(a.state)}</span></header>
          <div class="p01-powerbar">${btn(a.id,'ENABLE',power+'<span>ENABLE</span>','primary p01-enable',enabled||moving||excluded)}${btn(a.id,'STOP',stop+'<span>STOP</span>','danger p01-stop')}</div>
          ${!press?`<p class="help">${excluded?'임시 제외한 축입니다.':!enabled?'연결 · EN 상태 확인 또는 ENABLE을 먼저 누르세요.':'수동 이동은 센서 감지와 무관합니다. 전진·후진 이동량을 입력하세요.'}</p>`:''}
          <div class="reading"><span id="manual-reading-${a.id}"></span></div>
          <div class="p01-travel"><div class="p01-travel-row">${manualAmountInput(a.id,press?'DOWN':'FORWARD',press?'내리기':'전진')}${btn(a.id,press?'DOWN':'FORWARD',press?'내리기 <span aria-hidden="true">↓</span>':'전진 <span aria-hidden="true">→</span>','primary',blocked)}</div><div class="p01-travel-row">${manualAmountInput(a.id,press?'UP':'REVERSE',press?'올리기':'후진')}${btn(a.id,press?'UP':'REVERSE',press?'<span aria-hidden="true">↑</span> 올리기':'<span aria-hidden="true">←</span> 후진','',blocked)}</div></div>
          <div class="p01-utilities">${btn(a.id,'HOME','HOME 센서 찾기','',excluded||!enabled||moving||(!press&&!home?.connected))}${btn(a.id,'CYCLE',press?'CYCLE':'수동 왕복 시험','',excluded||!enabled||moving)}</div><div class="p01-disable-row">${btn(a.id,'DISABLE','모터 비활성화 · DISABLE','p01-disable',!enabled||moving)}</div></article>`;
      }
    }
    renderHtml(root,html+'<p class="help">각 방향의 이동량을 입력한 뒤 버튼을 누르세요. 입력값은 이 화면에서 유지되며 자동운전 설정에는 반영되지 않습니다.</p>');
  }
  if(code==='P01'){
    const feedHealth=$('#manual-feed-health'),material=plant.units.P00_MATERIAL;
    if(feedHealth)feedHealth.textContent=material?.detected?'자동 투입 조건 충족 · 수동 이동 가능':`수동 이동 가능 · 자동 투입 대기: ${material?.error||`자재 ${material?.detected_count??0}/4 감지`}`;
    const health=$('#manual-comm-health');
    if(health){const rate=Number(plant.serial?.rx_error_rate||0);health.textContent=plant.serial?.simulation?'시뮬레이션 통신':`UART ${plant.serial?.connected?'열림':'미연결'} · ${rate>0?`수신 프레임 오류 ${rate.toFixed(0)}회/초 · 배선/신호 확인`:'수신 프레임 오류 없음'}`;}
  }
  for(const a of items){
    const press=code==='P04',home=press?u.P04_HOME_1:u[`P01_HOME_${Number(a.id.slice(-1))+1}`];
    $(`#manual-reading-${a.id}`).textContent=`${formatMm(Number(a.position||0)*linearMmPerCount(a.id))} mm · 호밍 ${!press&&!home?.connected?'EE-SX 센서 설치 대기':a.homed?'완료':'필요'} · HOME ${home?.home?'ON':'OFF'}${a.error?' · '+a.error:''}`;
  }
  if(code==='P04')for(const action of ['DOWN','UP'])updateManualDistance(`P04_PRESS_AXIS_${action}`);
}

const dobotForms=Object.fromEntries(['P03','P05'].map(code=>[code,{mode:'JOINT',JOINT:['','','',''],step:'1',speed:'10',saved:'',name:''}]));
let dobotViewKey='';
function renderDobotManual(u){
  const code=manualProcess,f=dobotForms[code],a=u[`${code}_SCARA_J1`],g=u[`${code}_SCARA_GRIPPER`];
  const enabled=[1,2,3,4].every(i=>u[`${code}_SCARA_J${i}`]?.enabled),cfg=plant.config[scaraKey(code)];
  const busy=['RUNNING','PAUSED','PAUSE_REQUESTED'].includes(plant.line.status)||['MOVING','HOMING'].includes(a.state);
  const alarm=!!a.alarms?.length,blocked=!enabled||!a.connected||busy||alarm,labels=['J1','J2','J3','J4'];
  const key=JSON.stringify([code,f.mode,enabled,blocked,busy,alarm,a.connected,a.simulated,g.state,g.enabled,g.drive_enabled,g.error,Object.keys(cfg.positions),cfg.position_meta]);
  const root=$('#manual-controls');
  if(dobotViewKey!==key||root.dataset.dobot!==code){
    dobotViewKey=key;root.dataset.dobot=code;root.dataset.linear='';linearViewKey='';
    renderHtml(root,`<div class="manual-unit-toolbar"><div><b>${code} ${code==='P03'?'투입용':'배출용'} Dobot 제어 · ${plant.config.scara_p05.shared_with==='P03'?'P03·P05 공유 · ':''}${a.simulated?'SIMULATION':a.connected?'USB ONLINE':'USB OFFLINE'}</b><span id="dobot-status"></span></div><div>${btn(a.id,'ENABLE','ALL ENABLE','primary',(enabled&&g.enabled)||busy||!a.connected||alarm)}${btn(a.id,'DISABLE','ALL DISABLE','',!enabled)}${btn(a.id,'HOME','HOME · 원점복귀','',!a.connected||busy||alarm)}${btn(a.id,'STOP','STOP','danger')}<button data-dobot-connect ${busy||enabled?'disabled':''}>USB 다시 연결</button></div></div>
    <section class="dobot-panel"><div class="dobot-tabs" role="group" aria-label="Dobot 좌표 모드"><button data-dobot-mode="JOINT" aria-pressed="${f.mode==='JOINT'}" class="${f.mode==='JOINT'?'primary':''}">JOINT · 관절각</button></div>
    <p class="help">J1~J4 관절각(°)을 읽어 이동·저장합니다. 현재값 가져오기 후 목표 각도를 입력하세요.</p>
    <div class="dobot-readings"><div>JOINT <strong id="dobot-joints"></strong></div><div>XYZR 참고값 <strong id="dobot-xyzr"></strong></div></div>
    <div class="dobot-options"><label>JOG 간격 (°)<input id="dobot-step" data-dobot-option="step" type="number" min="0.1" max="10" step="0.1" value="${esc(f.step)}"></label><label>속도 (%)<input id="dobot-speed" data-dobot-option="speed" type="number" min="1" max="100" value="${esc(f.speed)}"></label></div>
    <div class="dobot-axis-grid">${labels.map((label,i)=>`<article class="unit-card"><header><h3>${label} ${f.mode==='JOINT'||i===3?'(°)':'(mm)'}</h3></header><div class="reading" id="dobot-value-${i}"></div><div class="unit-actions"><button data-dobot-jog="${i+1}" data-direction="-1" ${blocked?'disabled':''} aria-label="${label} 감소">−</button><button data-dobot-jog="${i+1}" data-direction="1" ${blocked?'disabled':''} aria-label="${label} 증가">+</button></div><label>목표 ${label}<input id="dobot-target-${i}" data-dobot-target="${i}" type="number" step="0.1" value="${esc(f[f.mode][i])}" aria-label="목표 ${label}"></label></article>`).join('')}</div>
    <div class="dobot-tabs"><button data-dobot-current ${!a.pose_valid?'disabled':''}>현재값 가져오기</button><button data-dobot-move class="primary" ${blocked?'disabled':''}>${f.mode} 목표 이동</button><button data-dobot-to-recipe>목표를 통합 순서에 추가</button><span class="help">± 버튼: 한 번 누를 때 설정 간격만큼 이동</span></div><div class="inline-form"><input data-dobot-option="name" aria-label="현재 좌표 저장 이름" placeholder="현재 좌표 저장 이름" value="${esc(f.name)}"><button data-dobot-save ${busy||!a.pose_valid?'disabled':''}>현재 ${f.mode} 위치 저장</button></div></section>
    ${card(g,`${esc(g.state)} <small>${g.drive_enabled===false?'구동 OFF':'gripper'}</small>`,btn(g.id,'OPEN','OPEN','',blocked||!g.enabled)+btn(g.id,'CLOSE','CLOSE','primary',blocked||!g.enabled)+btn(g.id,'STOP','STOP','danger'))}<p class="help">그리퍼 STOP: 구동 출력 OFF · 통합 순서 운전 중에는 순서도 정지 · 재사용하려면 ALL ENABLE</p>
    <article class="unit-card"><header><h3>저장 위치 이동</h3></header><select id="manual-position">${Object.keys(cfg.positions).map(n=>`<option value="${esc(n)}" ${f.saved===n?'selected':''}>${esc(n)} · ${esc(cfg.position_meta?.[n]?.mode||'JOINT')}${cfg.position_meta?.[n]?.source==='REAL'&&cfg.position_meta?.[n]?.mode==='JOINT'?' · REAL':' · 현재 Joint 재저장 필요'}</option>`).join('')}</select><div class="unit-actions"><button id="move-saved" data-robot="${code}" class="primary" ${blocked?'disabled':''}>MOVE</button></div></article>`);
  }
  const joints=[1,2,3,4].map(i=>u[`${code}_SCARA_J${i}`].position),xyzr=a.xyzr||[];
  const number=v=>a.pose_valid&&Number.isFinite(v)?Number(v).toFixed(2):'—';
  $('#dobot-joints').textContent=joints.map(v=>number(v)+'°').join(' / ');
  $('#dobot-xyzr').textContent=[0,1,2,3].map(i=>number(xyzr[i])+(i===3?'°':' mm')).join(' / ');
  $('#dobot-status').textContent=`${a.simulated?'SIMULATION':a.connected?'USB 연결 정상':'USB 미연결'} · ${a.state} · ${a.homed?'HOME 완료':'HOME 미확인'} · ${a.error||(a.state==='HOMING'?'원점복귀 진행 중 · STOP으로 정지':a.connected?(!enabled?'이동하려면 ALL ENABLE · HOME은 바로 실행 가능':'수동 제어 준비'):'포트 설정과 케이블을 확인하세요.')} ${a.last_pose_at?'· 최근 수신 '+new Date(a.last_pose_at*1000).toLocaleTimeString('en-GB'):''}`;
  (f.mode==='JOINT'?joints:xyzr).forEach((v,i)=>{$(`#dobot-value-${i}`).textContent=number(v)});
}

function deepGet(o,path){return path.split('.').reduce((a,k)=>a?.[k],o)}function deepSet(o,path,val){const keys=path.split('.');const last=keys.pop();const parent=keys.reduce((a,k)=>a[k],o);parent[last]=val}
function updateDistanceSummaries(){for(const node of $$('[data-distance-summary]'))node.textContent=`${node.dataset.distanceSummary==='p04.down_mm'?'하강':'상승'} ${formatMm(deepGet(draft,node.dataset.distanceSummary))} mm`}
function field(label,path,type='number',step='1'){return `<div class="field"><label>${label}</label><input data-config="${path}" type="${type}" step="${step}" value="${esc(deepGet(draft,path))}"></div>`}
function selectField(label,path,choices){const value=String(deepGet(draft,path));return `<div class="field"><label>${label}</label><select data-config="${path}">${choices.map(([v,t])=>`<option value="${esc(v)}" ${String(v)===value?'selected':''}>${esc(t)}</option>`).join('')}</select></div>`}
function scaraKey(code){return code==='P05'?'scara_p05':'scara'}
function actionKey(action){return ['SCARA_MOVE','DOBOT_MOVE','DOBOT_HOME','GRIPPER'].includes(action.type)?`${action.robot||'P03'}:${action.type}`:action.type}
function makeAction(value){const [code,type]=value.includes(':')?value.split(':'):['P03',value];if(type==='DOBOT_HOME')return{type,robot:code};if(type==='DOBOT_MOVE')return{type,robot:code,mode:'JOINT',values:[null,null,null,null],speed:10};const action={type,target:defaultTarget(type,code)};if(['SCARA_MOVE','GRIPPER'].includes(type))action.robot=code;return action}
function actionTarget(type,target,index,robot='P03',action=null){
  if(type==='DOBOT_HOME')return '<span>MAIN / MANUAL에서 별도 호밍 · 사이클에서는 생략</span>';
  if(type==='DOBOT_MOVE'){const home=action.reference==='HOME';const labels=['J1 (°)','J2 (°)','J3 (°)','J4 (°)'];return `<div class="dobot-recipe-fields"><label>이동 기준<select aria-label="${robot} 이동 기준" data-recipe-reference="${index}"><option value="ABSOLUTE" ${!home?'selected':''}>절대 목표값</option><option value="HOME" ${home?'selected':''}>호밍 위치 + 관절각</option></select></label><label>좌표 방식<select aria-label="${robot} 좌표 방식" data-recipe-mode="${index}"><option value="JOINT" ${action.mode==='JOINT'?'selected':''}>JOINT · 관절각</option></select></label><label>속도 (%)<input aria-label="${robot} 속도" data-recipe-speed="${index}" type="number" min="1" max="100" value="${esc(action.speed)}"></label>${labels.map((label,i)=>`<label>${home?'증분 ':''}${label}<input ${home?'min="-10" max="10"':''} aria-label="${robot} 목표 ${label}" data-recipe-value="${index}" data-coordinate="${i}" type="number" step="0.1" value="${esc(action.values?.[i])}" placeholder="목표값"></label>`).join('')}</div>`;} 
  let choices=[];
  if(type==='SCARA_MOVE')choices=Object.keys(draft[scaraKey(robot)].positions);
  if(type==='GRIPPER')choices=['OPEN','CLOSE'];
  if(type==='PRESS_MOVE')choices=Object.keys(draft.p04.saved_positions_mm);
  if(type==='PRESS_DOWN')return `<span data-distance-summary="p04.down_mm">하강 ${formatMm(draft.p04.down_mm)} mm</span>`;
  if(type==='PRESS_UP')return `<span data-distance-summary="p04.up_mm">상승 ${formatMm(draft.p04.up_mm)} mm</span>`;
  if(type==='WAIT')return `<input type="number" min="0" step="0.1" data-recipe-target="${index}" value="${esc(target??0.2)}">`;
  const unknown=!choices.some(value=>String(value)===String(target));
  return `<select data-recipe-target="${index}">${unknown?`<option value="${esc(target)}" selected>${esc(target)} (확인 필요)</option>`:''}${choices.map(value=>`<option ${String(value)===String(target)?'selected':''}>${esc(value)}</option>`).join('')}</select>`;
}
function defaultTarget(type,robot='P03'){
  if(type==='SCARA_MOVE')return Object.keys(draft[scaraKey(robot)].positions)[0]||'';
  if(type==='GRIPPER')return 'OPEN';
  if(type==='PRESS_MOVE')return Object.keys(draft.p04.saved_positions_mm)[0]||'';
  if(type==='PRESS_DOWN')return 'DOWN_MM';
  if(type==='PRESS_UP')return 'UP_MM';
  return 0.2;
}
function recipe(key,types){
  const arr=deepGet(draft,key)||[];
  return `<div class="recipe" data-recipe="${key}">${arr.map((a,i)=>`<div class="recipe-row ${a.type==='DOBOT_MOVE'?'recipe-direct':''} recipe-tone-${pressBlockTypes[actionKey(a)]?.tone||'wait'}"><span>${i+1}</span><select data-recipe-type="${i}">${types.map(t=>`<option value="${t}" ${t===actionKey(a)?'selected':''}>${pressBlockTypes[t]?.label||t}</option>`).join('')}</select>${actionTarget(a.type,a.target,i,a.robot,a)}<div class="recipe-buttons"><button data-recipe-up="${i}" ${i===0?'disabled':''}>↑</button><button data-recipe-down="${i}" ${i===arr.length-1?'disabled':''}>↓</button><button data-recipe-del="${i}" ${arr.length===1?'disabled':''}>×</button></div></div>`).join('')}</div><button data-recipe-add="${key}" data-recipe-types="${types.join(',')}">+ 동작 추가</button>`;
}
const pressBlockTypes={
  'P03:DOBOT_HOME':{label:'P03 Dobot HOME',tone:'scara'},
  'P05:DOBOT_HOME':{label:'P05 Dobot HOME',tone:'scara-out'},
  'P03:DOBOT_MOVE':{label:'P03 Dobot 직접 이동',tone:'scara'},
  'P05:DOBOT_MOVE':{label:'P05 Dobot 직접 이동',tone:'scara-out'},
  'P03:SCARA_MOVE':{label:'P03 Dobot 저장 위치',tone:'scara'},
  'P03:GRIPPER':{label:'P03 그리퍼',tone:'gripper'},
  'P05:SCARA_MOVE':{label:'P05 Dobot 저장 위치',tone:'scara-out'},
  'P05:GRIPPER':{label:'P05 그리퍼',tone:'gripper-out'},
  PRESS_MOVE:{label:'프레스 위치 이동',tone:'press'},
  PRESS_DOWN:{label:'프레스 하강',tone:'press'},
  PRESS_UP:{label:'프레스 상승',tone:'press'},
  WAIT:{label:'대기',tone:'wait'},
};
function captureRecipePosition(card){
  const flow=card?.closest('.recipe');
  if(!flow)return null;
  const rect=card.getBoundingClientRect();
  return {y:Math.max(0,Math.min(flow.clientHeight-rect.height,rect.top-flow.getBoundingClientRect().top))};
}
function focusRecipeCard(card,control=card,moved=false,follow=null){
  if(!card||!control)return;
  if(control===card)card.tabIndex=-1;
  control.focus({preventScroll:true});
  if(moved)card.classList.add('recipe-moved');
  const behavior=window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches?'auto':'smooth';
  const flow=card.closest('.recipe');
  if(moved&&follow&&flow){
    // Follow the card's displacement, not the center of the whole viewport.
    const delta=card.getBoundingClientRect().top-flow.getBoundingClientRect().top-follow.y;
    const top=Math.max(0,Math.min(flow.scrollHeight-flow.clientHeight,flow.scrollTop+delta));
    flow.scrollTo({top,behavior});
  }else card.scrollIntoView({block:'nearest',inline:'nearest',behavior});
}
function refreshRecipe(key,focusIndex,moved=false,follow=null){
  const selector=`[data-recipe="${key}"]`,top=$(selector)?.scrollTop||0;
  renderSettings();
  const root=$(selector);if(root)root.scrollTop=top;
  if(focusIndex>=0){const card=$(`${selector} .recipe-row:nth-child(${focusIndex+1})`);focusRecipeCard(card,card,moved,follow);}
}
function currentScara(code){return [1,2,3,4].map(i=>Number(plant.units[`${code}_SCARA_J${i}`]?.position||0).toFixed(2)).join(' / ')}
function scaraPositionCard(code){return `<section class="settings-section press-settings-card"><h3>${code} ${code==='P03'?'투입용':'배출용'} Dobot 현재 위치</h3>${field('동작 속도 (%)',`${scaraKey(code)}.speed`)}${code==='P05'&&draft.scara_p05.shared_with==='P03'?`<p class="help">P03과 같은 Dobot 사용 · USB/호밍/ENABLE/STOP 공유<br>${esc(draft.scara.port)}</p>`:selectField('Dobot 드라이버',`${scaraKey(code)}.driver`,[['dobot_serial','Dobot USB'],['simulation','미연결']])+field('USB 포트',`${scaraKey(code)}.port`,'text')}<div class="position-now">${currentScara(code)}</div><p class="help">JOINT · J1~J4 (°) · ${plant.units[`${code}_SCARA_J1`]?.simulated?'SIMULATION':plant.units[`${code}_SCARA_J1`]?.connected?'USB ONLINE':'USB OFFLINE'}<br>USB 설정 저장 후 MANUAL에서 ALL DISABLE → USB 다시 연결</p><div class="inline-form"><input id="scara-position-name-${code}" aria-label="${code} Dobot 저장 위치 이름" placeholder="예: ${code==='P03'?'MATERIAL_1_PICK':'OUTPUT'}"><button data-save-scara="${code}">현재 위치 저장</button></div></section>`}
function savedScaraPositions(code){return Object.entries(draft[scaraKey(code)].positions).map(([n,v])=>`<div class="saved"><div><b>${esc(n)} · ${esc(draft[scaraKey(code)].position_meta?.[n]?.mode||'JOINT')}</b>${v.map(Number).join(' / ')}</div><button data-delete-scara="${esc(n)}" data-robot="${code}">삭제</button></div>`).join('')}
function savedPressPositions(){return Object.entries(draft.p04.saved_positions_mm).map(([n,v])=>`<div class="saved"><div><b>${esc(n)}</b>${formatMm(v)} mm</div><button data-delete-press="${esc(n)}">삭제</button></div>`).join('')}
function renderSettings(){
  if(!draft)return;
  const recipeScroll=$('[data-recipe="press_recipe.actions"]')?.scrollTop||0;
  const positionsScroll=$('.saved-position-scroll')?.scrollTop||0;
  const tabs=[['LINE','LINE'],['P00','P00 · 자재 감지'],['P01','P01'],['P02','P02'],['P03P04P05','P03 · P04 · P05'],['P06','P06 · 배출 · 경광등']];
  renderHtml($('#setting-tabs'),tabs.map(([key,label])=>`<button class="${settingTab===key?'active':''}" data-setting-tab="${key}">${label}</button>`).join(''));
  if((draft.press_recipe?.version!==3||draft.distance_unit!=='mm')){$('#save-config').disabled=true;$('#settings-content').innerHTML='<p class="recipe-restart-warning" role="alert">mm 거리 설정 적용을 위해 제어 서버를 재시작한 뒤 새로고침하세요. 재시작 전에는 설정을 저장할 수 없습니다.</p>';return}
  let h='';
  if(settingTab==='LINE')h=`<div class="settings-grid"><section class="settings-section full"><h3>공정 사이 대기시간</h3>${plant.process_order.map(c=>field(`${c} 완료 후 대기 (초)`,`line.process_delays.${c}`,'number','.1')).join('')}</section></div>`;
  if(settingTab==='P00')h=`<div class="settings-grid"><section class="settings-section span2"><h3>자재 4개 감지 · Arduino Uno</h3>${selectField('센서 입력','p00.driver',[['unconfigured','미설정 · 투입 차단'],['arduino_serial','Arduino Uno · D2 / D3 / D4 / D5']])}${field('Arduino 직렬 포트','p00.serial_port','text')}${selectField('통신 속도 (baud)','p00.serial_baud',[9600,19200,38400,57600,115200].map(v=>[v,String(v)]))}${field('감지 안정 시간 (ms)','p00.debounce_ms')}${field('수신 제한 (초)','p00.stale_timeout')}${field('공정 대기 제한 (초)','p00.wait_timeout')}<p class="help">E-MSMLS61N-2M · D2 → 자재 1 · D3 → 자재 2 · D4 → 자재 3 · D5 → 자재 4<br>자동 투입은 자재 4개 모두 감지되어야 허용. 수동 이동은 별도입니다. 제외한 모터가 있어도 센서 4개를 모두 확인합니다.<br>현재 Uno의 감지/미감지 출력(115200 baud, 1초 주기)을 사용합니다. 감지 여부는 Uno에서 판정합니다.</p></section></div>`;
  if(settingTab==='P01')h=`<div class="settings-grid">${[0,1,2,3].map(i=>`<section class="settings-section"><h3>E${i} · 자재 ${i+1}</h3>${selectField('축 사용',`p01.axes.E${i}.installed`,[[true,'사용'],[false,'임시 제외 · 추후 추가']])}${field('자동 왕복 편도 거리 (mm)',`p01.axes.E${i}.distance_mm`,'number','any')}${field('HOME 최대 탐색 거리 (mm)',`p01.axes.E${i}.home_search_mm`,'number','any')}${field('HOME 센서 확인 거리 (mm)',`p01.axes.E${i}.home_step_mm`,'number','any')}</section>`).join('')}<section class="settings-section span2"><h3>4축 공통</h3>${field('속도 단계','p01.speed_gear')}${field('축 시작 간격 (초)','p01.start_gap','number','.01')}${field('복귀 전 대기 (초)','p01.reverse_dwell','number','.1')}</section><section class="settings-section span2"><h3>통신 제한시간</h3>${field('명령 ACK 제한 (초)','p01.command_timeout','number','.1')}${field('이동 완료 제한 (초)','p01.move_timeout','number','.1')}<p class="help">HOME은 설정 거리만큼 나누어 홈 방향으로 이동하며 센서 감지 시에만 0으로 확정합니다. 일반 전진·후진은 센서로 정지하지 않습니다.</p></section></div>`;
  if(settingTab==='P02')h=`<div class="settings-grid"><section class="settings-section span2"><h3>Vision 판정</h3>${selectField('검사 방식','p02.inspection_mode',[['AI','AI 검사 · 알고리즘 연동 필요'],['CAMERA_CHECK','카메라 연결 확인 · 임시 사이클 시험']])}${field('카메라 이름','p02.camera_name','text')}${field('카메라 시리얼 (빈칸: 자동)','p02.camera_serial','text')}${field('미리보기 FPS','p02.preview_fps')}<p class="help">카메라 연결 확인 모드는 최근 영상 수신을 확인하면 다음 공정으로 진행하며, AI 품질 판정은 수행하지 않습니다. 영상 미수신·연결 끊김 시에는 중단합니다. AI 연동 후 검사 방식을 AI 검사로 되돌리세요.<br>Basler GigE/USB · 컬러/흑백 자동 선택 · REAL 모드에서 자동 연결/재연결. 카메라 교체 시 시리얼을 변경하거나 비워 주세요. 여러 대면 시리얼을 지정하세요.</p>${field('검사시간 (초)','p02.inspection_time','number','.1')}${field('OK 기준 점수','p02.score_threshold','number','.01')}</section></div>`;
  if(settingTab==='P03P04P05'){const mm=s=>Number(s)/600;h=`<div class="settings-grid press-settings-grid"><div class="position-settings-stack">${scaraPositionCard('P03')}<section class="settings-section press-settings-card"><h3>프레스 현재위치</h3><div class="position-now">${formatMm(Number(plant.units.P04_PRESS_AXIS?.position||0)/600)} mm · ${esc(plant.units.P04_PRESS_AXIS?.transport||'PRESS')} ${plant.units.P04_PRESS_AXIS?.connected?'ONLINE':'OFFLINE'}</div><div class="inline-form"><input id="press-position-name" placeholder="예: PRESS_BOTTOM"><button id="save-press-position">현재 위치 저장</button></div></section>${scaraPositionCard('P05')}</div><section class="settings-section press-settings-card"><h3>P04 USB CAN</h3><div class="can-settings">${field('CAN 속도 (RPM)','p04.speed')}${selectField('실제 장비 드라이버','p04.driver',[['can_usb','candleLight USB CAN'],['simulation','Simulation']])}${field('CAN ID','p04.can.can_id')}${selectField('통신 속도','p04.can.bitrate',[[125000,'125 kbps'],[250000,'250 kbps'],[500000,'500 kbps'],[1000000,'1 Mbps']])}${field('가감속 (RPM/s)','p04.can.ramp_rpm_per_second','number','.1')}${field('감속 선행 거리 (mm)','p04.can.deceleration_advance_mm','number','any')}</div><p class="help">CAN ID와 통신 속도는 REAL 모드로 다시 전환할 때 적용됩니다.</p></section><div class="engineering-split"><section class="settings-section"><h3>통합 저장 위치</h3><div class="saved-position-scroll"><div class="position-groups"><div><span>P03 투입용 Dobot · 저장 좌표</span><div class="saved-list">${savedScaraPositions('P03')}</div></div><div><span>P05 배출용 Dobot · 저장 좌표</span><div class="saved-list">${savedScaraPositions('P05')}</div></div><div><span>PRESS · 1축</span><div class="saved-list">${savedPressPositions()}</div></div></div></div></section><section class="settings-section unified-recipe-section"><h3>P03 · P04 · P05 통합 동작 순서</h3><div class="inline-form">${field('자동 하강 거리 (mm)','p04.down_mm','number','any')}${field('자동 상승 거리 (mm)','p04.up_mm','number','any')}</div><p class="help">입력 후 설정 저장 · 통합 순서의 모든 하강/상승에 적용 · 수동 화면의 이동 거리는 해당 수동 명령에만 적용</p><p class="help">위 → 아래 실행 · ${draft.press_recipe.actions.length}개 동작 · 변경 후 설정 저장<br>직접 이동: J1~J4 절대 각도 또는 호밍 후 관절각 증분(±10°) · 저장 위치: 해당 지점에서 현재 Joint 값을 저장 후 선택</p>${recipe('press_recipe.actions',['P03:SCARA_MOVE','P03:DOBOT_MOVE','P03:GRIPPER','P05:SCARA_MOVE','P05:DOBOT_MOVE','P05:GRIPPER','PRESS_MOVE','PRESS_DOWN','PRESS_UP','WAIT'])}</section></div></div>`;}

  if(settingTab==='P06')h=`<div class="settings-grid"><section class="settings-section span2"><h3>배출 · 경광등 완료 신호</h3><p class="help">배출 위치와 그리퍼 OPEN은 P03 · P04 · P05 통합 순서에서 지정합니다. 배출 후 P06에서 실적 등록과 완료 점등을 처리합니다.</p>${selectField('릴레이 ON 신호','p06.relay_active_level',[['UNSET','미설정 · 실제 출력 차단'],['LOW','LOW 신호에서 ON'],['HIGH','HIGH 신호에서 ON']])}<p class="help">D8 초록등 · D9 빨강등 · D10 부저 · 예비 릴레이 2개 미사용<br>P00 센서와 같은 Uno USB 통신을 사용합니다. P06 통합 펌웨어가 필요하며, 펌웨어의 릴레이 극성과 이 설정을 맞추세요.<br>부저는 수동으로 켜고 끕니다. 자동 완료는 초록등, 오류는 기존처럼 빨강등으로 표시합니다.</p>${field('초록 경광등 유지 (초)','p06.green_hold_seconds','number','.1')}</section><section class="settings-section span2"><h3>MES 실적</h3>${field('라인 코드','p06.line_code','text')}${field('MES 주소','p06.mes_endpoint','text')}<p class="help">MES 전송은 기존 시험 인터페이스를 유지합니다. REAL 경광등은 Uno 응답을 확인한 뒤 상태를 표시합니다.</p></section></div>`;
  const restartNeeded=(draft.press_recipe?.version!==3||draft.distance_unit!=='mm');
  $('#save-config').disabled=restartNeeded;
  renderHtml($('#settings-content'),(restartNeeded?'<p class="recipe-restart-warning" role="alert">통합 레시피 적용을 위해 제어 서버를 재시작한 뒤 새로고침하세요. 현재는 이전 서버의 동작 목록이며, 재시작 전에는 설정을 저장할 수 없습니다.</p>':'')+h);
  const recipeFlow=$('[data-recipe="press_recipe.actions"]');if(recipeFlow)recipeFlow.scrollTop=recipeScroll;
  const positionsFlow=$('.saved-position-scroll');if(positionsFlow)positionsFlow.scrollTop=positionsScroll;
}
function renderLog(){
  if(!plant)return;
  const source=logPaused?frozenLogs:plant.logs;
  const process=$('#log-process-filter')?.value||'ALL', type=$('#log-type-filter')?.value||'ALL';
  const query=($('#log-search')?.value||'').trim().toLowerCase();
  const rows=source.filter(x=>{
    const processMatch=process==='ALL'||(process==='SYSTEM'?!x.process:x.process===process);
    const typeMatch=type==='ALL'||x.type===type;
    const rawText=`${x.time} ${x.type} ${x.process} ${x.unit} ${x.message}`;
    const text=(rawText+' '+displayText(rawText)).toLowerCase();
    return processMatch&&typeMatch&&(!query||text.includes(query));
  });
  $('#log-visible-count').textContent=rows.length;
  $('#log-error-count').textContent=source.filter(x=>x.type==='ERROR').length;
  $('#log-last-time').textContent=source.length?`마지막 이벤트 ${source[0].time}`:'마지막 이벤트 —';
  $('#log-table').innerHTML=rows.map(x=>`<div class="log-table-row ${esc(x.type)}"><span class="time">${esc(x.time)}</span><span class="type">${displayEsc(x.type)}</span><span class="process">${esc(x.process||'—')}</span><span class="unit">${displayEsc(x.unit||'—')}</span><span class="message">${displayEsc(x.message)}</span></div>`).join('')||'<div class="log-empty">조건에 맞는 로그가 없습니다.</div>';
}
function update(data){
  if(plant){for(const code of ['P03','P05']){const key=scaraKey(code);if(plant.serial.simulation!==data.serial.simulation||plant.config[key]?.port!==data.config[key]?.port){dobotForms[code].JOINT=['','','',''];dobotViewKey='';}}}
  plant=data;
  if(!dirty||!draft)draft=JSON.parse(JSON.stringify(data.config));
  const simulation=!!data.serial.simulation;
  $('#connection-dot').classList.toggle('on',!!data.serial.connected);
  const allConnected=data.serial.connected&&['P03_SCARA_J1','P04_PRESS_AXIS','P05_SCARA_J1'].every(id=>data.units[id]?.connected);
  $('#connection-text').textContent=simulation?'SIMULATION':allConnected?'REAL ONLINE':'REAL · 일부 미연결';
  $('#connection-text').title=['P03','P05'].map(code=>`${code} USB ${data.units[`${code}_SCARA_J1`]?.connected?'연결':'미연결'}`).join(' / ');
  $('#mode-sim').classList.toggle('active',simulation);
  $('#mode-real').classList.toggle('active',!simulation);
  const autoRunning=['RUNNING','PAUSED','PAUSE_REQUESTED'].includes(data.line.status);
  $('#manual-lock').classList.toggle('show',autoRunning);
  $$('[data-mode]').forEach(button=>button.disabled=data.line.status==='RUNNING'||data.line.status==='PAUSED'||data.line.status==='PAUSE_REQUESTED');
  const start=$('[data-api="/api/line/start"]'); if(start)start.disabled=autoRunning;
  const home=$('[data-api="/api/line/home"]');if(home)home.disabled=autoRunning;
  const pause=$('[data-action="pause"]'); if(pause)pause.textContent=data.line.status==='PAUSED'?'▶ RESUME':'Ⅱ PAUSE';
  renderMain();renderMimics();renderManual();
  if($('.page.active')?.id==='page-setting'&&!dirty)renderSettings();
  if(!logPaused)renderLog();
}
let jogSession=0;
async function beginJog(button){
  const token=++jogSession,unit=button.dataset.holdUnit,action=button.dataset.holdCommand;
  while(token===jogSession){
    const result=await api(`/api/unit/${unit}/command`,{action,delta:0.25},true);
    if(!result.ok)break;
    await new Promise(resolve=>setTimeout(resolve,70));
  }
}
function stopJog(){jogSession++}
const stopSelector='[data-unit][data-command="STOP"], [data-api="/api/line/stop"]';
function sendStop(button){
  stopJog();
  return button.dataset.api ? api(button.dataset.api) : api(`/api/unit/${button.dataset.unit}/command`,{action:'STOP'});
}
function bind(){
  // STOP remains immediate on press-down; keyboard activation is handled below.
  document.addEventListener('pointerdown',e=>{
    const button=e.target.closest(stopSelector);
    if(button&&!button.disabled&&e.button===0){e.preventDefault();sendStop(button)}
  });
  document.addEventListener('pointerdown',e=>{const jog=e.target.closest('[data-hold-unit]');if(jog&&!jog.disabled){e.preventDefault();beginJog(jog)}});
  document.addEventListener('pointerup',stopJog);document.addEventListener('pointercancel',stopJog);window.addEventListener('blur',stopJog);
  document.addEventListener('click',async e=>{
    const material=e.target.closest('[data-material-sim]');if(material){await api('/api/p00/simulate',{detected:material.dataset.materialSim==='true'});return}
    const stop=e.target.closest(stopSelector);
    if(stop){if(e.detail===0)await sendStop(stop);return}

    const dm=e.target.closest('[data-dobot-mode]');if(dm){dobotForms[manualProcess].mode='JOINT';renderManual();return}
    const code=manualProcess,f=dobotForms[code];
    if(e.target.closest('[data-dobot-current]')){const a=plant.units[`${code}_SCARA_J1`];f[f.mode]=(f.mode==='JOINT'?[1,2,3,4].map(i=>plant.units[`${code}_SCARA_J${i}`].position):a.xyzr).map(v=>String(Number(v).toFixed(2)));dobotViewKey='';renderManual();return}
    if(e.target.closest('[data-dobot-save]')){await api(`/api/scara/${code}/save-current`,{name:f.name,mode:f.mode});return}
    if(e.target.closest('[data-dobot-connect]')){await api(`/api/scara/${code}/connect`);return}
    if(e.target.closest('[data-dobot-to-recipe]')){const values=f[f.mode],speed=Number(f.speed);if(values.some(v=>v.trim()===''||!Number.isFinite(Number(v)))||!Number.isFinite(speed)||speed<1||speed>100){toast('목표값 4개와 속도(1~100%)를 입력하세요.',true);return}draft.press_recipe.actions.push({type:'DOBOT_MOVE',robot:code,mode:f.mode,values:values.map(Number),speed});dirty=true;settingTab='P03P04P05';$('.nav[data-page="setting"]').click();refreshRecipe('press_recipe.actions',draft.press_recipe.actions.length-1);toast('통합 순서에 추가했습니다. 설정 저장 후 실행하세요.');return}
    if(e.target.closest('[data-dobot-move]')){const values=f[f.mode];if(values.some(v=>v.trim()===''||!Number.isFinite(Number(v)))){toast('목표값 4개를 모두 입력하세요.',true);return}await api(`/api/scara/${code}/move`,{mode:f.mode,values:values.map(Number),speed:Number(f.speed)});return}
    const dj=e.target.closest('[data-dobot-jog]');if(dj){await api(`/api/scara/${code}/jog`,{mode:f.mode,axis:Number(dj.dataset.dobotJog),delta:Number(f.step)*Number(dj.dataset.direction),speed:Number(f.speed)});return}

    const nav=e.target.closest('[data-page]');
    if(nav){$$('.nav').forEach(x=>x.classList.remove('active'));nav.classList.add('active');$$('.page').forEach(x=>x.classList.remove('active'));$(`#page-${nav.dataset.page}`).classList.add('active');if(nav.dataset.page==='setting')renderSettings();if(nav.dataset.page==='log')renderLog();return}
    const mode=e.target.closest('[data-mode]');if(mode){await api('/api/mode',{mode:mode.dataset.mode});return}
    const station=e.target.closest('#manual-mimic [data-process]');if(station){manualProcess=station.dataset.process;renderMimics();renderManual();return}
    const tab=e.target.closest('[data-setting-tab]');if(tab){settingTab=tab.dataset.settingTab;renderSettings();return}
    const apiBtn=e.target.closest('[data-api]');if(apiBtn){let body={};if(apiBtn.dataset.api==='/api/line/start')body.scenario=$('#scenario').value;await api(apiBtn.dataset.api,body);return}
    if(e.target.closest('[data-action="pause"]')){await api(plant.line.status==='PAUSED'?'/api/line/resume':'/api/line/pause');return}
    const cmd=e.target.closest('[data-unit]');if(cmd){if(cmd.disabled)return;const body={action:cmd.dataset.command},amount=manualAmountFor(cmd.dataset.unit,body.action);if(amount){const raw=manualAmounts[amount.key]??'',value=Number(raw);if(!raw.trim()||!Number.isFinite(value)||value<=0||value>amount.max){toast(`${amount.label}: 0 초과 ${formatMm(amount.max)} 이하의 숫자를 입력하세요.`,true);return}body[amount.field]=value;}await api(`/api/unit/${cmd.dataset.unit}/command`,body);return}
    if(e.target.id==='log-pause'){logPaused=!logPaused;frozenLogs=logPaused?[...plant.logs]:[];e.target.textContent=logPaused?'업데이트 재개':'표시 일시정지';$('#log-live').classList.toggle('paused',logPaused);$('#log-live-text').textContent=logPaused?'DISPLAY PAUSED':'LIVE UPDATE';renderLog();return}
    if(e.target.id==='run-process'){await api(`/api/process/${e.target.dataset.process}/run`,{scenario:$('#scenario').value});return}
    if(e.target.id==='move-saved'){await api(`/api/scara/${e.target.dataset.robot}/move-saved`,{name:$('#manual-position').value});return}
    if((['save-config','save-press-position'].includes(e.target.id)||e.target.closest('[data-save-scara]'))&&(draft.press_recipe?.version!==3||draft.distance_unit!=='mm')){toast('제어 서버 재시작 후 새로고침하고 저장하세요.',true);return}
    if(e.target.id==='save-config'){const d=await api('/api/config',draft);if(d.ok)dirty=false;return}
    const saveScara=e.target.closest('[data-save-scara]');if(saveScara){const code=saveScara.dataset.saveScara;const d=await api(`/api/scara/${code}/save-current`,{name:$(`#scara-position-name-${code}`).value});if(d.ok)dirty=false;return}
    if(e.target.id==='save-press-position'){const d=await api('/api/press/save-current',{name:$('#press-position-name').value});if(d.ok)dirty=false;return}
    const deleteScara=e.target.closest('[data-delete-scara]');
    if(deleteScara){const name=deleteScara.dataset.deleteScara,code=deleteScara.dataset.robot;const used=draft.press_recipe.actions.some(item=>item.type==='SCARA_MOVE'&&item.robot===code&&String(item.target).toUpperCase()===name);if(used){toast('동작 순서에서 사용 중인 위치입니다.',true);return}delete draft[scaraKey(code)].positions[name];dirty=true;renderSettings();return}
    const deletePress=e.target.closest('[data-delete-press]');
    if(deletePress){const name=deletePress.dataset.deletePress;const used=draft.press_recipe.actions.some(item=>item.type==='PRESS_MOVE'&&String(item.target).toUpperCase()===name);if(used){toast('동작 순서에서 사용 중인 위치입니다.',true);return}delete draft.p04.saved_positions_mm[name];dirty=true;renderSettings();return}
    const rowButton=e.target.closest('.recipe-row button');
    if(rowButton){
      if(rowButton.disabled)return;
      const root=rowButton.closest('[data-recipe]'),key=root.dataset.recipe,arr=deepGet(draft,key);
      let index=Number(rowButton.dataset.recipeDel??rowButton.dataset.recipeUp??rowButton.dataset.recipeDown);
      const follow=captureRecipePosition($(`[data-recipe="${key}"] .recipe-row:nth-child(${index+1})`));
      let moved=false;
      if(rowButton.dataset.recipeDel!==undefined){if(arr.length===1)return;arr.splice(index,1);index=Math.min(index,arr.length-1);}
      if(rowButton.dataset.recipeUp!==undefined&&index>0){[arr[index-1],arr[index]]=[arr[index],arr[index-1]];index--;moved=true;}
      if(rowButton.dataset.recipeDown!==undefined&&index<arr.length-1){[arr[index+1],arr[index]]=[arr[index],arr[index+1]];index++;moved=true;}
      dirty=true;refreshRecipe(key,index,moved,follow);return;
    }
    const add=e.target.closest('[data-recipe-add]');if(add){const key=add.dataset.recipeAdd,type=add.dataset.recipeTypes.split(',')[0],arr=deepGet(draft,key);arr.push(makeAction(type));dirty=true;refreshRecipe(key,arr.length-1);return}
  });
  document.addEventListener('input',e=>{
    if(e.target.dataset.manualAmount){manualAmounts[e.target.dataset.manualAmount]=e.target.value;updateManualDistance(e.target.dataset.manualAmount);return}
    if(e.target.dataset.dobotTarget!==undefined){const f=dobotForms[manualProcess];f[f.mode][Number(e.target.dataset.dobotTarget)]=e.target.value}
    if(e.target.dataset.dobotOption)dobotForms[manualProcess][e.target.dataset.dobotOption]=e.target.value;
    if(e.target.id==='log-search')renderLog();
    if(e.target.dataset.config){const current=deepGet(draft,e.target.dataset.config);const value=typeof current==='boolean'?e.target.value==='true':e.target.type==='number'||typeof current==='number'?Number(e.target.value):e.target.value;deepSet(draft,e.target.dataset.config,value);dirty=true;updateDistanceSummaries()}
    if(e.target.dataset.recipeValue!==undefined||e.target.dataset.recipeSpeed!==undefined){const root=e.target.closest('[data-recipe]'),index=Number(e.target.dataset.recipeValue??e.target.dataset.recipeSpeed),item=deepGet(draft,root.dataset.recipe)[index],value=e.target.value===''?null:Number(e.target.value);if(e.target.dataset.recipeValue!==undefined)item.values[Number(e.target.dataset.coordinate)]=value;else item.speed=value;dirty=true;return}
    if(e.target.matches('input[data-recipe-target]')){const root=e.target.closest('[data-recipe]'),index=Number(e.target.dataset.recipeTarget);deepGet(draft,root.dataset.recipe)[index].target=Number(e.target.value);dirty=true}
  });
  document.addEventListener('change',e=>{
    if(e.target.id==='manual-position'&&dobotForms[manualProcess])dobotForms[manualProcess].saved=e.target.value;
    if(e.target.id==='log-process-filter'||e.target.id==='log-type-filter')renderLog();
    if(e.target.dataset.config){const current=deepGet(draft,e.target.dataset.config);const value=typeof current==='boolean'?e.target.value==='true':e.target.type==='number'||typeof current==='number'?Number(e.target.value):e.target.value;deepSet(draft,e.target.dataset.config,value);dirty=true;updateDistanceSummaries()}
    if(e.target.dataset.recipeReference!==undefined){const root=e.target.closest('[data-recipe]'),index=Number(e.target.dataset.recipeReference),item=deepGet(draft,root.dataset.recipe)[index];if(e.target.value==='HOME'){item.reference='HOME';item.mode='JOINT'}else delete item.reference;item.values=[null,null,null,null];dirty=true;refreshRecipe(root.dataset.recipe,index);return}
    if(e.target.dataset.recipeMode!==undefined){const root=e.target.closest('[data-recipe]'),index=Number(e.target.dataset.recipeMode),item=deepGet(draft,root.dataset.recipe)[index];item.mode='JOINT';item.values=[null,null,null,null];dirty=true;refreshRecipe(root.dataset.recipe,index);return}
    const row=e.target.closest('.recipe-row');if(!row)return;
    const root=row.parentElement,index=Number(e.target.dataset.recipeType??e.target.dataset.recipeTarget),item=deepGet(draft,root.dataset.recipe)[index];
    if(e.target.dataset.recipeType!==undefined){for(const key of Object.keys(item))delete item[key];Object.assign(item,makeAction(e.target.value));dirty=true;refreshRecipe(root.dataset.recipe,index)}else if(e.target.dataset.recipeTarget!==undefined){item.target=e.target.type==='number'?Number(e.target.value):e.target.value;dirty=true}
  });
}
setInterval(()=>$('#clock').textContent=new Date().toLocaleTimeString('en-GB',{hour12:false}),1000);bind();fetch('/api/status').then(r=>r.json()).then(update).catch(()=>toast('서버 연결 대기',true));const stream=new EventSource('/api/events');stream.onmessage=e=>{try{update(JSON.parse(e.data))}catch{}};


