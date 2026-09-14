/* Read-only MAIN status, process selection and recent logs. */
(() => {
  let state=null,selected='P01',receivedAt=0;
  const $=q=>document.querySelector(q);
  const names={P00:'자재 감지',P01:'자재 투입',P02:'비전 검사',P03:'Dobot 투입',P04:'프레스',P05:'Dobot 배출',P06:'배출 · 경광등'};
  const statusNames={IDLE:'대기',READY:'준비',WAITING:'대기',RUNNING:'동작 중',DONE:'완료',ERROR:'오류',ALARM:'알람',PAUSED:'일시정지',PAUSE_REQUESTED:'정지 대기',DISABLED:'비활성',HOMING:'호밍 중',OFFLINE:'미연결'};
  const label=s=>statusNames[s]||s||'대기';
  const activeProcess=()=>names[state?.line?.active_process]&&['RUNNING','PAUSED','PAUSE_REQUESTED','HOMING','ERROR','ALARM'].includes(state?.line?.status)?state.line.active_process:null;
  const escape=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const display=v=>escape(String(v??'').replace(/SCARA|스카라/gi,'Dobot'));
  function connected(code){
    if(!state)return false;
    if(code==='P01')return [0,1,2,3].filter(i=>state.config?.p01?.axes?.['E'+i]?.installed!==false).every(i=>state.units?.['P01_E'+i]?.connected);
    return !!state.units?.[{P00:'P00_MATERIAL',P02:'P02_CAMERA',P03:'P03_SCARA_J1',P04:'P04_PRESS_AXIS',P05:'P05_SCARA_J1',P06:'P06_LIGHT_GREEN'}[code]]?.connected;
  }
  function render(){
    if(!state)return;
    const stale=Date.now()-receivedAt>16000;
    $('#scene-data-mode').textContent=stale?'상태 수신 끊김 · 마지막 위치':`${state.serial?.simulation?'SIM':'REAL'} · ${state.line.active_process?state.line.active_process+' '+label(state.line.status):label(state.line.status)}`;
    $('#main-fault-link').dataset.fault=String(!!state.line.fault);
    $('#main-fault-link').title=state.line.fault?'현재 알람을 LOG에서 확인':'라인 상태 · LOG 열기';
    for(const button of document.querySelectorAll('#main-process-flow [data-inspect]')){
      const code=button.dataset.inspect,on=code===selected,current=code===activeProcess();
      const fault=state.processes?.[code]?.status==='ERROR'||String(state.line.fault?.code||'').startsWith(code);
      button.classList.toggle('selected',on);button.setAttribute('aria-pressed',String(on));
      button.classList.toggle('current-process',current);button.classList.toggle('process-fault',fault);
      if(current)button.setAttribute('aria-current','step');else button.removeAttribute('aria-current');
      button.querySelector('.state').textContent=current?'● '+label(state.line.status):label(state.processes?.[code]?.status);
    }
    const entries=(state.logs||[]).slice(0,20),panel=$('#main-recent-logs');
    const html=entries.map(entry=>`<article class="main-log-entry${['ERROR','ALARM'].includes(entry.type)?' is-error':''}"><div class="main-log-meta"><time>${escape(entry.time)}</time><b>${display(entry.type)}</b><span>${escape(entry.process||'SYSTEM')}</span></div><p title="${display([entry.unit,entry.message].filter(Boolean).join(' · '))}">${entry.unit?`<span class="main-log-unit">${display(entry.unit)} · </span>`:''}${display(entry.message)}</p></article>`).join('')||'<p class="main-log-empty">아직 기록된 로그가 없습니다.</p>';
    if(panel&&panel.innerHTML!==html){const top=panel.scrollTop;panel.innerHTML=html;panel.scrollTop=top;}
    $('#main-log-count').textContent=stale?'수신 끊김':`최근 ${entries.length}건`;
    for(const button of document.querySelectorAll('#page-log [data-api="/api/line/ack"],#page-log [data-api="/api/line/reset"]'))button.disabled=!state.line.fault||['RUNNING','PAUSED','PAUSE_REQUESTED'].includes(state.line.status);
  }
  function select(code){if(!names[code])return;selected=code;render();globalThis.Line3D?.select(code);}
  function logs(alarm=false){
    document.querySelector('.nav[data-page="log"]').click();
    for(const id of ['log-process-filter','log-type-filter']){$('#'+id).value='ALL';$('#'+id).dispatchEvent(new Event('change',{bubbles:true}));}
    $('#log-search').value='';$('#log-search').dispatchEvent(new Event('input',{bubbles:true}));
    if(alarm)document.querySelector('.log-alarm-panel').scrollIntoView({block:'start'});
  }
  document.addEventListener('click',event=>{
    const tag=event.target.closest('[data-inspect]');if(tag){select(tag.dataset.inspect);return;}
    if(event.target.closest('[data-main-log]'))logs();
    if(event.target.closest('[data-open-alarm]'))logs(true);
  });
  setInterval(()=>{if(state&&document.body.dataset.activePage==='main')render();},2000);
  globalThis.MainDashboard={update(value){state=value;receivedAt=Date.now();render();},select,connected,label,names,get selected(){return selected;},get activeProcess(){return activeProcess();},get state(){return state;},get stale(){return Date.now()-receivedAt>16000;}};
})();
