"""Read-only Apprentice tessellation. CAD cm/Z-up -> glTF meters/Y-up."""
import os,sys,json,struct,hashlib,argparse
from pathlib import Path
from array import array
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--project',type=Path,required=True,help='Inventor .ipj file')
parser.add_argument('--assembly',type=Path,required=True,help='Top-level .iam file')
parser.add_argument('--output',type=Path,required=True,help='Output directory')
parser.add_argument('--pywin32-deps',type=Path,help='Optional local pywin32 package directory')
args=parser.parse_args()
if args.pywin32_deps:
 deps=args.pywin32_deps.resolve()
 sys.path[:0]=[str(deps),str(deps/'win32'),str(deps/'win32/lib')]
 dll_handle=os.add_dll_directory(str(deps/'pywin32_system32'))
import pythoncom,win32com.client
pythoncom.CoInitialize()
app=win32com.client.gencache.EnsureDispatch('Inventor.ApprenticeServer')
app.DesignProjectManager.DesignProjects.AddExisting(str(args.project.resolve())).Activate()
app.DisplayAffinity=False
source=args.assembly.resolve()
doc=app.Open(str(source))
out=args.output.resolve();out.mkdir(exist_ok=True,parents=True)
gltf={'asset':{'version':'2.0','generator':'MONO / Autodesk Apprentice 2026 CalculateFacets','extras':{'source':source.name,'sourceSha256':hashlib.sha256(source.read_bytes()).hexdigest()}},'scene':0,'scenes':[{'nodes':[]}],'nodes':[],'meshes':[],'materials':[],'accessors':[],'bufferViews':[],'buffers':[]}
binary=bytearray();records=[];cache={};material_ids={};leaf_count=0

def add_data(data,kind,component,count,minimum=None,maximum=None):
 while len(binary)%4:binary.append(0)
 view=len(gltf['bufferViews']);gltf['bufferViews'].append({'buffer':0,'byteOffset':len(binary),'byteLength':len(data)})
 binary.extend(data)
 item={'bufferView':view,'componentType':component,'count':count,'type':kind}
 if minimum is not None:item.update(min=minimum,max=maximum)
 idx=len(gltf['accessors']);gltf['accessors'].append(item);return idx

def color_for(name):
 n=name.upper()
 if 'TRAFFIC_LIGHT_3' in n:return (0.17,.58,.35)
 if 'TRAFFIC_LIGHT_4' in n:return (.75,.16,.20)
 if 'TRAFFIC_LIGHT_2' in n:return (.2,.23,.28)
 if 'TRAFFIC' in n:return (.28,.31,.36)
 if 'PVC' in n:return (.77,.80,.84)
 if 'ZIG' in n or 'BRACKET' in n:return (.20,.27,.34)
 if any(s in n for s in ('MOTOR','МОТОР','WHEEL','SENSOR','KNOB')):return (.16,.19,.23)
 if any(s in n for s in ('PROFILE','PLATE')):return (.66,.72,.78)
 return (.46,.52,.59)

def material(rgb):
 if rgb not in material_ids:
  material_ids[rgb]=len(gltf['materials']);gltf['materials'].append({'name':'CAD display '+str(rgb),'pbrMetallicRoughness':{'baseColorFactor':[*rgb,1],'metallicFactor':.24,'roughnessFactor':.6},'doubleSided':False})
 return material_ids[rgb]

def convert(v,m,point=True):
 w=[sum(m[r][c]*v[c] for c in range(3))+(m[r][3] if point else 0) for r in range(3)]
 factor=.01 if point else 1
 return [w[0]*factor,w[2]*factor,-w[1]*factor]

def visit(occs,path=''):
 global leaf_count
 for occ in occs:
  name=path+'/'+occ.Name
  if occ.SubOccurrences.Count:
   visit(occ.SubOccurrences,name);continue
  leaf_count+=1
  definition=occ.Definition
  filename=Path(definition.Document.FullFileName).name
  m=[[occ.Transformation.Cell(r,c) for c in range(1,5)] for r in range(1,5)]
  node_names=[];bounds=[]
  for bindex,body in enumerate(definition.SurfaceBodies):
   key=(definition.Document.FullFileName,bindex)
   if key not in cache:cache[key]=body.CalculateFacets(.035)
   vc,fc,coords,normals,indices=cache[key]
   if vc==0:continue
   vertices=[];normal_data=[]
   for i in range(vc):
    vertices.extend(convert(coords[i*3:i*3+3],m));normal_data.extend(convert(normals[i*3:i*3+3],m,False))
   assert len(indices)==fc*3 and min(indices)>=1 and max(indices)<=vc
   low=[min(vertices[i::3]) for i in range(3)];high=[max(vertices[i::3]) for i in range(3)];bounds.append((low,high))
   pos=add_data(array('f',vertices).tobytes(),'VEC3',5126,vc,low,high)
   normal=add_data(array('f',normal_data).tobytes(),'VEC3',5126,vc)
   ind=add_data(array('I',(i-1 for i in indices)).tobytes(),'SCALAR',5125,len(indices))
   mesh=len(gltf['meshes']);gltf['meshes'].append({'primitives':[{'attributes':{'POSITION':pos,'NORMAL':normal},'indices':ind,'material':material(color_for(filename))}]})
   nodename=f'cad_{leaf_count:03d}_body_{bindex}'
   node_names.append(nodename);gltf['scenes'][0]['nodes'].append(len(gltf['nodes']))
   gltf['nodes'].append({'name':nodename,'mesh':mesh,'extras':{'cadPath':name,'sourcePart':filename,'sourceKind':'original-cad'}})
  if bounds:
   low=[min(b[0][i] for b in bounds) for i in range(3)];high=[max(b[1][i] for b in bounds) for i in range(3)]
   records.append({'path':name,'file':filename,'nodes':node_names,'min':[v*1000 for v in low],'max':[v*1000 for v in high],'center':[(a+b)*500 for a,b in zip(low,high)]})

try:
 visit(doc.ComponentDefinition.Occurrences)
 while len(binary)%4:binary.append(0)
 gltf['buffers']=[{'byteLength':len(binary)}]
 j=json.dumps(gltf,ensure_ascii=False,separators=(',',':')).encode();j+=b' '*((-len(j))%4)
 result=struct.pack('<III',0x46546c67,2,28+len(j)+len(binary))+struct.pack('<II',len(j),0x4e4f534a)+j+struct.pack('<II',len(binary),0x004e4942)+binary
 (out/'line-cad.glb').write_bytes(result)
 (out/'cad-part-map.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps({'parts':leaf_count,'meshes':len(gltf['meshes']),'uniqueBodies':len(cache),'triangles':sum(x['count']//3 for x in gltf['accessors'] if x['type']=='SCALAR'),'bytes':len(result)},ensure_ascii=False),flush=True)
finally:doc.Close();app.Close()
