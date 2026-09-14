import * as THREE from './vendor/three/three.module.js';
import {OrbitControls} from './vendor/three/controls/OrbitControls.js';
import {GLTFLoader} from './vendor/three/loaders/GLTFLoader.js';
import {addSupplementalEquipment} from './supplemental-equipment.js';

const host=document.getElementById('main-mimic'),loading=document.getElementById('scene-loading');
let renderer,scene,camera,controls,model,metadata,state,selected='P01',visible=true,tagsVisible=true,frame=0,loaded=false;
let lastUpdate=0,lastFrame=0,needsRender=true,interior=true;
const tags=new Map(),targets=new Map(),axisTargets=new Map(),pickables=[],motions=[];
const raycaster=new THREE.Raycaster(),pointer=new THREE.Vector2(),clock=new THREE.Clock();
const colors={idle:0x98aabd,running:0xe58a13,fault:0xd94358,selected:0x287ed2};
const activeProcess=()=>globalThis.MainDashboard?.activeProcess||null;
const originalMaterials=new Map();
const vector=a=>new THREE.Vector3(...a);

function renderSoon(){needsRender=true;if(visible&&!frame)frame=requestAnimationFrame(render);}
function resize(){
  if(!renderer||!host.clientWidth||!host.clientHeight)return;
  renderer.setSize(host.clientWidth,host.clientHeight,false);
  camera.aspect=host.clientWidth/host.clientHeight;camera.updateProjectionMatrix();renderSoon();
}
function fit(top=false){
  if(!model)return;
  const box=new THREE.Box3().setFromObject(model),center=box.getCenter(new THREE.Vector3()),size=box.getSize(new THREE.Vector3());
  const radius=size.length()*.5;
  const limitingFov=Math.min(camera.fov*Math.PI/180,2*Math.atan(Math.tan(camera.fov*Math.PI/360)*camera.aspect));
  const distance=radius/Math.sin(limitingFov/2)*1.08;
  camera.up.set(0,1,0);
  camera.position.copy(center).add(vector(top?[0,1,.001]:[-.9,.65,.95]).normalize().multiplyScalar(distance));
  controls.target.copy(center);controls.minDistance=radius*.35;controls.maxDistance=radius*12;controls.update();renderSoon();
}
function createTags(){
  const layer=document.getElementById('scene-tags');
  for(const [code,entry] of Object.entries(metadata.processes)){
    const leader=document.createElement('i');leader.className='scene-leader';layer.append(leader);
    const button=document.createElement('button');button.className='scene-tag';button.dataset.inspect=code;
    const title=document.createElement('strong'),status=document.createElement('span');
    title.textContent=entry.label;status.textContent='상태 대기';button.append(title,status);layer.append(button);
    tags.set(code,{button,status,leader,anchor:vector(entry.anchor),anchorNode:entry.anchorNode?model.getObjectByName(entry.anchorNode):null});
  }
}
function tagAnchor(item){return item.anchorNode?item.anchorNode.getWorldPosition(new THREE.Vector3()):item.anchor.clone();}
function layoutTags(){
  if(!camera)return;
  const occupied=[],width=host.clientWidth,height=host.clientHeight;
  const priority=code=>code===activeProcess()?0:code===selected?1:2;
  const order=[...tags.keys()].sort((a,b)=>priority(a)-priority(b));
  for(const code of order){
    const item=tags.get(code),v=tagAnchor(item).project(camera),w=item.button.offsetWidth||120,h=item.button.offsetHeight||37;
    item.button.hidden=!tagsVisible||v.z>1||v.z< -1;
    item.leader.hidden=item.button.hidden;
    if(item.button.hidden)continue;
    const px=(v.x*.5+.5)*width,py=(-v.y*.5+.5)*height;
    let x,y,rect;
    placement:for(const dy of [-18,-h-32,h+5,-2*h-46,2*h+20,-3*h-60])for(const dx of [0,w+16,-w-16,2*w+32,-2*w-32]){
      x=Math.max(w/2+8,Math.min(width-w/2-8,px+dx));y=Math.max(h+12,Math.min(height-48,py+dy));
      rect={left:x-w/2-5,right:x+w/2+5,top:y-h-5,bottom:y+5};
      if(!occupied.some(r=>rect.left<r.right&&rect.right>r.left&&rect.top<r.bottom&&rect.bottom>r.top))break placement;
    }
    const collision=occupied.some(r=>rect.left<r.right&&rect.right>r.left&&rect.top<r.bottom&&rect.bottom>r.top);
    item.button.hidden=collision&&code!==selected&&code!==activeProcess();
    item.leader.hidden=item.button.hidden;
    if(!item.button.hidden){occupied.push(rect);item.button.style.left=x+'px';item.button.style.top=y+'px';const dx=(v.x*.5+.5)*width-x,dy=(-v.y*.5+.5)*height-y;item.leader.style.left=x+'px';item.leader.style.top=y+'px';item.leader.style.width=Math.hypot(dx,dy)+'px';item.leader.style.transform=`rotate(${Math.atan2(dy,dx)}rad)`;}
  }
}
function bindMetadata(){
  for(const entry of metadata.groups||[]){const group=new THREE.Group();group.name=entry.name;model.add(group);for(const name of entry.nodes){const node=model.getObjectByName(name);if(node)group.add(node);}}
  const nodes=new Map();model.traverse(node=>{nodes.set(node.name,node);if(node.isMesh){node.material=node.material.clone();originalMaterials.set(node,node.material.color.clone());pickables.push(node);}});
  for(const entry of metadata.axisGroups||[])axisTargets.set(entry.axis,entry.nodes.map(name=>nodes.get(name)).filter(Boolean));
  for(const [code,entry] of Object.entries(metadata.processes)){
    const items=[];
    for(const name of entry.nodes||[]){const n=nodes.get(name);if(n){n.traverse(child=>{if(child.isMesh){child.userData.processes||=[];if(!child.userData.processes.includes(code))child.userData.processes.push(code);items.push(child);}});}}
    targets.set(code,items);
  }
  for(const entry of metadata.motions||[]){const node=nodes.get(entry.node);if(node)motions.push({...entry,node,origin:node.position.clone(),rotation:node.quaternion.clone(),value:0,target:0,applied:false});}
}
function applyFloorLayout(){
  const layout=metadata.floorLayout;if(!layout)return;
  const unitScale=metadata.sceneScale||1;
  // Reorient the equipment group, leaving the shared Dobot upright.
  // Joint and slide motions remain in their own local coordinate systems.
  const bed=new THREE.Group();bed.name='floor_equipment';
  const upright=new Set(layout.uprightNodes||['display_dobot','display_robot_stand','display_output_bin']);
  for(const child of [...model.children])if(!upright.has(child.name))bed.add(child);
  bed.rotation.z=-Math.PI/2;bed.position.x=layout.offsetXmm/unitScale;model.add(bed);
  const robot=model.getObjectByName('display_dobot'),stand=model.getObjectByName('display_robot_stand'),bin=model.getObjectByName('display_output_bin');
  if(robot)robot.position.y=layout.dobotBaseHeightMm/unitScale;
  if(stand&&!layout.uprightNodes)stand.scale.y=layout.dobotBaseHeightMm/420;
  if(bin)bin.position.y=4/unitScale;
  for(const [code,entry] of Object.entries(metadata.processes)){
    if(layout.anchors?.[code]){entry.anchor=[...layout.anchors[code]];continue;}
    if(code==='P03'){entry.anchor[1]+=layout.dobotBaseHeightMm-420;continue;}
    if(code==='P05'){entry.anchor=[-490,70,-65];continue;}
    const [x,y,z]=entry.anchor;entry.anchor=[y+layout.offsetXmm,-x+25,z];
  }
  model.updateMatrixWorld(true);
}
function updateHighlights(){
  if(!loaded)return;
  const ranks=new Map();
  for(const [mesh,color] of originalMaterials){mesh.material.color.copy(color);mesh.material.emissive?.setHex(0);mesh.material.emissiveIntensity=0;mesh.userData.highlightRole='idle';}
  for(const [code,items] of targets){
    const status=state?.processes?.[code]?.status,isFault=status==='ERROR'||String(state?.line?.fault?.code||'').startsWith(code),running=activeProcess()===code;
    if(!isFault&&!running&&selected!==code)continue;
    const rank=isFault?3:running?2:1,role=isFault?'fault':running?'running':'selected',c=colors[role];
    for(const mesh of items){if((ranks.get(mesh)||0)>rank)continue;ranks.set(mesh,rank);mesh.material.color.copy(originalMaterials.get(mesh)).lerp(new THREE.Color(c),rank>1?.5:.08);mesh.material.emissive?.setHex(c);mesh.material.emissiveIntensity=rank>1?.36:.12;mesh.userData.highlightRole=role;}
  }
  for(const [code,item] of tags){
    const p=state?.processes?.[code]||{},shared=state?.config?.scara_p05?.shared_with==='P03'&&['P03','P05'].includes(code);
    const connected=globalThis.MainDashboard?.connected(code);
    item.status.textContent=`${activeProcess()===code?'● '+globalThis.MainDashboard.label(state.line.status):globalThis.MainDashboard?.label(p.status)||'대기'}${shared?' · 공유 장치':''}${connected?'':' · 미연결'}`;
    item.button.classList.toggle('selected',code===selected);item.button.setAttribute('aria-pressed',String(code===selected));
    item.button.classList.toggle('running',activeProcess()===code);
    item.button.classList.toggle('fault',p.status==='ERROR'||String(state?.line?.fault?.code||'').startsWith(code));
    item.button.classList.toggle('offline',!connected);
  }
  for(const [axis,items] of axisTargets){const excluded=state?.config?.p01?.axes?.[axis]?.installed===false;for(const node of items)node.traverse(mesh=>{if(mesh.isMesh){mesh.material.transparent=excluded;mesh.material.opacity=excluded?.28:1;mesh.material.depthWrite=!excluded;}});}
  for(const name of metadata.coverNodes||[]){const mesh=model.getObjectByName(name);if(mesh?.isMesh){mesh.material.transparent=interior;mesh.material.opacity=interior?.16:1;mesh.material.depthWrite=!interior;}}
  for(const motion of motions)if(motion.kind==='lamp')paintLamp(motion);
  renderSoon();
}
function paintLamp(motion){motion.node.traverse(n=>{if(n.isMesh){n.material.emissive?.setHex(motion.color||0x31bd6b);n.material.emissiveIntensity=motion.value*.65;}});}
function updateMotionTargets(){
  if(!state)return;
  for(const motion of motions){
    const unit=state.units?.[motion.unit];
    if(!unit?.connected)continue; // Freeze the last position on communication loss.
    if(motion.unit.startsWith('P01_')&&state.config?.p01?.axes?.[motion.unit.slice(4)]?.installed===false)continue;
    let value;
    if(motion.kind==='joint'){
      if(!unit.pose_valid)continue;
      if(motion.terms){if(motion.terms.some(t=>!state.units?.[t.unit]?.connected||!state.units?.[t.unit]?.pose_valid))continue;value=motion.terms.reduce((sum,t)=>sum+Number(state.units[t.unit].position)*t.factor,0);}
      else value=Number(unit.position);
    }else if(motion.kind==='gripper')value=unit.state==='CLOSE'?1:0;
    else if(motion.kind==='lamp')value=motion.channel!==undefined?(unit.channels?.[motion.channel]?.detected?1:0):(unit.output?1:0);
    else value=Number(unit.position_mm??Number(unit.position||0)*(motion.unit.startsWith('P01')?38.5/Number(state.config?.p01?.pulse_per_rev||3200):1/600));
    if(Number.isFinite(value))motion.target=value;
  }
}
function animateMotion(dt){
  let changed=false;
  for(const motion of motions){
    // Visual interpolation is only presentation of received values, never a command.
    const next=THREE.MathUtils.damp(motion.value,motion.target,12,dt);
    if(motion.applied&&Math.abs(next-motion.value)<.0001)continue;
    motion.value=next;motion.applied=true;changed=true;
    if(motion.kind==='joint')motion.node.quaternion.copy(motion.rotation).multiply(new THREE.Quaternion().setFromAxisAngle(vector(motion.axis),THREE.MathUtils.degToRad((next-(motion.zero||0))*(motion.sign??1))));
    else if(motion.kind==='linear')motion.node.position.copy(motion.origin).addScaledVector(vector(motion.axis),next*(motion.scale??1));
    else if(motion.kind==='gripper')motion.node.position.copy(motion.origin).addScaledVector(vector(motion.axis),next*(motion.travel||3));
    else if(motion.kind==='lamp')paintLamp(motion);
  }
  return changed;
}
function render(time){
  frame=0;if(!visible||!renderer)return;
  const dt=Math.min(.1,clock.getDelta()),moving=animateMotion(dt),cameraMoved=controls.update();
  if(needsRender||moving||cameraMoved){renderer.render(scene,camera);layoutTags();needsRender=false;lastFrame=time;}
  if((moving||cameraMoved)&&!frame)frame=requestAnimationFrame(render);
}
async function init(){
  try{
    renderer=new THREE.WebGLRenderer({antialias:true,alpha:false,powerPreference:'low-power'});
    renderer.setPixelRatio(Math.min(devicePixelRatio||1,1.5));renderer.outputColorSpace=THREE.SRGBColorSpace;
    renderer.setClearColor(0xf0f3f7);host.prepend(renderer.domElement);
    scene=new THREE.Scene();camera=new THREE.PerspectiveCamera(36,1,.1,100000);
    controls=new OrbitControls(camera,renderer.domElement);controls.enableDamping=true;controls.dampingFactor=.12;controls.maxPolarAngle=Math.PI*.49;controls.addEventListener('change',renderSoon);
    scene.add(new THREE.HemisphereLight(0xffffff,0x8994a3,2.4));
    const sun=new THREE.DirectionalLight(0xffffff,2.2);sun.position.set(600,1200,800);scene.add(sun);
    metadata=await fetch('./assets/line-model.json').then(r=>{if(!r.ok)throw Error('모델 정보 없음');return r.json();});
    const gltf=await new GLTFLoader().loadAsync('./assets/'+metadata.file);model=gltf.scene;model.scale.multiplyScalar(metadata.sceneScale||1);addSupplementalEquipment(model,metadata);scene.add(model);
    state=globalThis.MainDashboard?.state||state;selected=globalThis.MainDashboard?.selected||selected;
    bindMetadata();applyFloorLayout();
    const bounds=new THREE.Box3().setFromObject(model),size=bounds.getSize(new THREE.Vector3()),center=bounds.getCenter(new THREE.Vector3());
    const grid=new THREE.GridHelper(Math.max(size.x,size.z)*1.9,24,0xd7dfe7,0xe3e9ef);grid.position.set(center.x,bounds.min.y-1,center.z);scene.add(grid);
    createTags();loaded=true;loading.hidden=true;
    document.getElementById('scene-source').textContent=metadata.label;
    document.getElementById('scene-source').title=metadata.sourceNote||metadata.label;
    resize();fit();updateHighlights();updateMotionTargets();renderSoon();
    globalThis.dispatchEvent(new Event('line3d-ready'));
    let down;
    renderer.domElement.addEventListener('pointerdown',e=>{down={x:e.clientX,y:e.clientY};});
    renderer.domElement.addEventListener('pointerup',e=>{
      if(!down||Math.hypot(e.clientX-down.x,e.clientY-down.y)>5)return;
      const rect=renderer.domElement.getBoundingClientRect();pointer.set((e.clientX-rect.left)/rect.width*2-1,-(e.clientY-rect.top)/rect.height*2+1);
      raycaster.setFromCamera(pointer,camera);const hit=raycaster.intersectObjects(pickables,false).find(h=>h.object.userData.processes?.length);
      if(hit){const codes=hit.object.userData.processes;globalThis.MainDashboard?.select(codes.includes(state?.line?.active_process)?state.line.active_process:codes.includes(selected)?selected:codes[0]);}
    });
  }catch(error){
    console.error('3D initialization failed',error);loading.hidden=false;loading.textContent='3D 표시를 준비하지 못했습니다. 공정 상태와 운전 버튼은 사용할 수 있습니다.';
    document.getElementById('scene-source').textContent='3D 로딩 실패';
  }
}
document.addEventListener('click',e=>{
  const button=e.target.closest('[data-view]');if(!button)return;
  if(button.dataset.view==='tags'){tagsVisible=!tagsVisible;button.setAttribute('aria-pressed',String(tagsVisible));renderSoon();}
  else if(button.dataset.view==='interior'){interior=!interior;button.setAttribute('aria-pressed',String(interior));updateHighlights();}
  else fit(button.dataset.view==='top');
});
new ResizeObserver(resize).observe(host);
document.addEventListener('visibilitychange',()=>{visible=!document.hidden&&document.body.dataset.activePage==='main';renderSoon();});
globalThis.Line3D={
  update(value){state=value;lastUpdate=Date.now();updateHighlights();updateMotionTargets();renderSoon();},
  select(code){selected=code;updateHighlights();},
  setVisible(value){visible=value&&!document.hidden;if(visible){resize();renderSoon();}else if(frame){cancelAnimationFrame(frame);frame=0;}},
  get ready(){return loaded;},get diagnostics(){return {loaded,meshes:pickables.length,motions:motions.length,selected,activeProcess:activeProcess(),lastUpdate,lastFrame,visible,
    highlights:pickables.filter(m=>['running','fault'].includes(m.userData.highlightRole)).map(m=>({node:m.name,role:m.userData.highlightRole,processes:m.userData.processes})),
    layout:['floor_equipment','display_dobot','display_output_bin'].map(name=>{const n=model?.getObjectByName(name);return n?{name,position:n.getWorldPosition(new THREE.Vector3()).toArray(),up:new THREE.Vector3(0,1,0).transformDirection(n.matrixWorld).toArray()}:null;}),
    attachments:['display_camera_mount','display_camera'].map(name=>{const n=model?.getObjectByName(name);return n?{name,parent:n.parent.name,position:n.getWorldPosition(new THREE.Vector3()).toArray(),direction:new THREE.Vector3(1,0,0).transformDirection(n.matrixWorld).toArray()}:null;}),
    tagAnchors:Object.fromEntries([...tags].map(([code,item])=>[code,tagAnchor(item).toArray()])),
    motionValues:motions.map(m=>({node:m.node.name,unit:m.unit,kind:m.kind,value:m.value,target:m.target,position:m.node.position.toArray(),emissive:m.node.isMesh?m.node.material.emissiveIntensity:null}))};}
};
init();
