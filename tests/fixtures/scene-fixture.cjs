// Explicit test geometry only. Never copied to web/assets or used as CAD.
const positions=new Float32Array([-20,-20,-20,20,-20,-20,20,20,-20,-20,20,-20,-20,-20,20,20,-20,20,20,20,20,-20,20,20]);
const indices=new Uint16Array([0,2,1,0,3,2,4,5,6,4,6,7,0,1,5,0,5,4,3,7,6,3,6,2,1,2,6,1,6,5,0,4,7,0,7,3]);
const codes=['P00','P01','P02','P03','P04','P05','P06'];
const nodes=Array.from({length:8},(_,i)=>({name:'fixture_'+i,mesh:0,translation:[(i%4)*110,20,Math.floor(i/4)*160]}));
const payload=Buffer.concat([Buffer.from(positions.buffer),Buffer.from(indices.buffer)]);
const gltf={asset:{version:'2.0',generator:'MONO browser test fixture'},scene:0,scenes:[{nodes:nodes.map((_,i)=>i)}],nodes,
 meshes:[{primitives:[{attributes:{POSITION:0},indices:1}]}],buffers:[{byteLength:payload.length}],
 bufferViews:[{buffer:0,byteOffset:0,byteLength:positions.byteLength},{buffer:0,byteOffset:positions.byteLength,byteLength:indices.byteLength}],
 accessors:[{bufferView:0,componentType:5126,count:8,type:'VEC3',min:[-20,-20,-20],max:[20,20,20]},{bufferView:1,componentType:5123,count:36,type:'SCALAR'}]};
let json=Buffer.from(JSON.stringify(gltf));json=Buffer.concat([json,Buffer.alloc((4-json.length%4)%4,32)]);
const header=Buffer.alloc(12),jsonHeader=Buffer.alloc(8),binHeader=Buffer.alloc(8);
header.writeUInt32LE(0x46546c67);header.writeUInt32LE(2,4);header.writeUInt32LE(28+json.length+payload.length,8);
jsonHeader.writeUInt32LE(json.length);jsonHeader.writeUInt32LE(0x4e4f534a,4);binHeader.writeUInt32LE(payload.length);binHeader.writeUInt32LE(0x004e4942,4);
module.exports={glb:Buffer.concat([header,jsonHeader,json,binHeader,payload]),metadata:{file:'test-fixture.glb',label:'TEST FIXTURE — not CAD',
 processes:Object.fromEntries(codes.map((code,i)=>[code,{label:code,anchor:nodes[i].translation.map((v,j)=>v+(j===1?50:0)),nodes:['fixture_'+(code==='P05'?3:i)]}])),
 motions:[{kind:'linear',node:'fixture_1',unit:'P01_E0',axis:[1,0,0],scale:1},{kind:'lamp',node:'fixture_6',unit:'P06_LIGHT_GREEN',color:0x31bd6b}]}};
