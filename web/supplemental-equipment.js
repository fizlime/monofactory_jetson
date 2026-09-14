import * as THREE from './vendor/three/three.module.js';

// Display models follow the supplied top/perspective assembly design images.
// Placement is illustrative, not a calibrated robot work frame or collision model.
export function addSupplementalEquipment(model,metadata){
  if(!metadata.supplementalEquipment)return;
  const m=v=>v/1000;
  const material=(color,metalness=.2)=>new THREE.MeshStandardMaterial({color,metalness,roughness:.5});
  const white=material(0xdce0e3),dark=material(0x41464d),blue=material(0xb7c0c8),metal=material(0xaab3ba,.55),glass=material(0x184c69);
  function group(name,parent=model,at=[0,0,0]){const n=new THREE.Group();n.name=name;n.userData.sourceKind='display-model';n.position.set(...at.map(m));parent.add(n);return n;}
  function mesh(geometry,mat,parent,at=[0,0,0]){const n=new THREE.Mesh(geometry,mat);n.position.set(...at.map(m));parent.add(n);return n;}
  function box(size,mat,parent,at){return mesh(new THREE.BoxGeometry(...size.map(m)),mat,parent,at);}
  function cylinder(radius,height,mat,parent,at){return mesh(new THREE.CylinderGeometry(m(radius),m(radius),m(height),24),mat,parent,at);}
  function bolt(parent,at,r=3){cylinder(r,3,dark,parent,at);}
  function link(parent,length,width,depth){
    const shape=new THREE.Shape();shape.moveTo(-m(width/2),0);
    shape.lineTo(-m(width*.36),m(length));shape.quadraticCurveTo(0,m(length+width*.3),m(width*.36),m(length));
    shape.lineTo(m(width/2),0);shape.quadraticCurveTo(0,-m(width*.3),-m(width/2),0);
    const geo=new THREE.ExtrudeGeometry(shape,{depth:m(depth),bevelEnabled:true,bevelThickness:m(2),bevelSize:m(2),bevelSegments:2,steps:1});
    mesh(geo,white,parent,[0,0,-depth/2]);
    for(const z of [-depth/2-2,depth/2+2]){
      box([width*.3,length*.75,3],metal,parent,[0,length*.5,z]);
      for(const y of [12,length-12]){const cap=cylinder(width*.23,5,dark,parent,[0,y,z]);cap.rotation.x=Math.PI/2;}
    }
  }
  // The mounting plate is on the existing frame; no separate pedestal or bin.
  const stand=group('display_robot_stand',model,[-145,182,-160]);
  box([155,2,150],metal,stand,[0,1,0]);
  for(const x of [-65,65])for(const z of [-62,62])bolt(stand,[x,3,z]);
  const robot=group('display_dobot',model,[-145,184,-160]);
  cylinder(66,13,metal,robot,[0,6.5,0]);cylinder(58,14,white,robot,[0,20,0]);
  const j1=group('display_dobot_j1',robot,[0,28,0]);
  cylinder(43,63,white,j1,[0,30,0]);box([43,49,57],dark,j1,[-44,35,0]);
  box([45,41,52],white,j1,[-47,36,0]);
  const j2=group('display_dobot_j2',j1,[0,64,0]);
  const shoulder=cylinder(29,83,metal,j2);shoulder.rotation.x=Math.PI/2;
  link(j2,135,47,57);
  for(const z of [-38,38])box([9,131,5],metal,j2,[-22,66,z]);
  const j3=group('display_dobot_j3',j2,[0,135,0]);
  const elbow=cylinder(31,69,metal,j3);elbow.rotation.x=Math.PI/2;
  link(j3,147,39,48);
  for(const z of [-29,29])box([7,125,5],metal,j3,[-20,71,z]);
  const level=group('display_dobot_level',j3,[0,147,0]);
  cylinder(23,32,white,level,[0,-10,0]);box([35,44,33],dark,level,[16,14,0]);
  const j4=group('display_dobot_j4',level,[0,-32,0]);
  cylinder(24,18,metal,j4,[0,-5,0]);box([54,14,30],metal,j4,[0,-17,0]);
  const left=group('display_gripper_left',j4,[-24,-33,0]),right=group('display_gripper_right',j4,[24,-33,0]);
  box([8,28,22],dark,left,[0,0,0]);box([8,28,22],dark,right,[0,0,0]);

  const press=group('display_press',model,[-40,182,-460]);
  box([250,12,200],white,press,[0,6,0]);
  box([104,340,35],white,press,[0,185,-67]);
  for(const x of [-51,51])box([13,336,28],metal,press,[x,184,-44]);
  box([83,230,19],metal,press,[0,137,-30]);
  box([76,45,65],white,press,[0,377,-60]);box([85,12,82],metal,press,[0,406,-60]);
  cylinder(17,3,dark,press,[0,414,-60]);cylinder(13,4,metal,press,[0,416,-60]);
  for(const x of [-29,29])for(const z of [-86,-34])bolt(press,[x,414,z]);
  box([108,20,93],metal,press,[0,23,22]);box([86,22,70],white,press,[0,43,23]);
  box([55,5,48],dark,press,[0,57,30]);
  const ram=group('display_press_ram',press,[0,85,0]);
  box([106,19,76],white,ram,[0,0,14]);box([78,75,24],metal,ram,[0,43,-16]);
  for(const x of [-24,24]){cylinder(10,20,metal,ram,[x,18,30]);bolt(ram,[x,30,30],4);}

  // Eye-in-hand camera beside the gripper, fixed to the rotating tool flange.
  const cameraMount=group('display_camera_mount',j4,[0,0,0]);
  box([12,6,52],metal,cameraMount,[0,10,27]);
  box([12,23,6],metal,cameraMount,[0,1,50]);
  const camera=group('display_camera',cameraMount,[0,0,58]);
  box([58,40,48],dark,camera,[0,0,0]);box([12,33,45],blue,camera,[-28,0,0]);
  const lens=cylinder(18,32,dark,camera,[42,0,0]);lens.rotation.z=Math.PI/2;
  const front=cylinder(15,2,glass,camera,[59,0,0]);front.rotation.z=Math.PI/2;
  camera.rotation.z=-Math.PI/2; // Lens looks down toward the material.

  const joint=(node,unit,axis,extra={})=>({kind:'joint',node,unit,axis,...extra});
  metadata.motions.push(
    joint(j1.name,'P03_SCARA_J1',[0,1,0]),
    joint(j2.name,'P03_SCARA_J2',[0,0,1],{sign:-1}),
    joint(j3.name,'P03_SCARA_J3',[0,0,1],{terms:[{unit:'P03_SCARA_J2',factor:1},{unit:'P03_SCARA_J3',factor:1}]}),
    joint(level.name,'P03_SCARA_J3',[0,0,1],{terms:[{unit:'P03_SCARA_J3',factor:-1}]}),
    joint(j4.name,'P03_SCARA_J4',[0,1,0]),
    {kind:'gripper',node:left.name,unit:'P03_SCARA_GRIPPER',axis:[1,0,0],travel:.015},
    {kind:'gripper',node:right.name,unit:'P03_SCARA_GRIPPER',axis:[-1,0,0],travel:.015},
    {kind:'linear',node:ram.name,unit:'P04_PRESS_AXIS',axis:[0,-1,0],scale:.001}
  );
  // Zero feedback starts from the same neutral pose used by the display model.
  j2.rotation.z=-35*Math.PI/180;j3.rotation.z=-55*Math.PI/180;level.rotation.z=Math.PI/2;
  metadata.processes.P02.nodes.push(cameraMount.name);
  metadata.processes.P03.nodes.push(robot.name);
  metadata.processes.P04.nodes.push(press.name);
  metadata.processes.P05.nodes.push(robot.name);
}
