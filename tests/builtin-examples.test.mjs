import test from 'node:test';
import assert from 'node:assert/strict';
import { UNIT, BUILTIN_EXAMPLES, createExampleProject, loadExampleProject, resolveOriginalPieces, registerProjectParts, inventoryFor, orderPieces } from '../dist/data.js';
import { readFile } from 'node:fs/promises';

test('the default campus scene and both generated examples are available', () => {
  assert.deepEqual(BUILTIN_EXAMPLES.map(example => example.id), ['songen-example','songen-generated-example','xmu-gate-example']);
  const building=createExampleProject();
  assert.equal(building.id, 'songen-example');
  assert.equal(building.title, '厦大 · 颂恩楼');
  assert.equal(building.source, 'example');
  assert.equal(building.sourceImage, './assets/songen-reference.jpg');
  assert.equal(building.thumbnail, './assets/songen-preview.png');
  assert.ok(building.pieces.length > 0);
  assert.equal(new Set(building.pieces.map(piece=>piece.id)).size, building.pieces.length);
  assert.doesNotThrow(()=>registerProjectParts(building));
  assert.equal(inventoryFor(building.pieces).reduce((sum,row)=>sum+row.count,0), building.pieces.length);
  const original=structuredClone(building.originalPieces);
  building.pieces[0].color='#ffffff';
  assert.deepEqual(building.originalPieces,original);
  assert.deepEqual(createExampleProject('songen-example').pieces,original);
  assert.throws(()=>createExampleProject('apple-example'), /内置示例/);
  assert.throws(()=>createExampleProject('missing-example'), /内置示例/);
});

test('generated examples preserve their genuine meshes, poses, provenance and standalone downloads', async () => {
  const previousFetch=globalThis.fetch;
  const requests=[];
  globalThis.fetch=async url=>{
    requests.push(url);
    const data=JSON.parse(await readFile(new URL('../dist/'+url.replace(/^\.\//,''),import.meta.url),'utf8'));
    return {ok:true,json:async()=>data};
  };
  try {
    for(const [id,count,jobId] of [
      ['songen-generated-example',589,'7fc9523fa9c54d5f85517c69ffc0392e'],
      ['xmu-gate-example',246,'778fe03efefa48b29ee0eeb886284236'],
    ]) {
      const metadata=BUILTIN_EXAMPLES.find(item=>item.id===id);
      const bundled=JSON.parse(await readFile(new URL('../dist/'+metadata.projectUrl.replace(/^\.\//,''),import.meta.url),'utf8'));
      const project=await loadExampleProject(id);
      assert.equal(project.source,'example');assert.equal(project.draft,false);
      assert.equal(project.pieces.length,count);assert.equal(project.generation.jobId,jobId);
      assert.deepEqual(project.pieces,bundled.pieces);assert.deepEqual(project.parts,bundled.parts);
      assert.deepEqual(project.originalPieces,bundled.pieces);
      assert.doesNotThrow(()=>registerProjectParts(project));
      assert.equal(inventoryFor(project.pieces).reduce((sum,row)=>sum+row.count,0),count);
      assert.equal(project.generation.validation.components,1);
      for(const url of Object.values(project.generation.artifacts)){
        assert.ok(url.startsWith('./examples/'));
        assert.ok((await readFile(new URL('../dist/'+url.replace(/^\.\//,''),import.meta.url))).length>0);
      }
      project.pieces[0].color='#000000';project.parts[0].mesh.positions[0]=999;
      const again=await loadExampleProject(id);
      assert.deepEqual(again.pieces,bundled.pieces);assert.deepEqual(again.parts,bundled.parts);
      assert.deepEqual(await resolveOriginalPieces({id:'saved-'+id,exampleId:id,source:'user',draft:true,pieces:[]}),bundled.pieces);
    }
    assert.equal(requests.length,2,'each mesh file is fetched once and reused on subsequent opens');
  } finally {globalThis.fetch=previousFetch;}
});

test('restoring a saved building draft resolves the building rather than the legacy apple', async () => {
  const expected=createExampleProject('songen-example').pieces;
  const fromId=await resolveOriginalPieces({id:'songen-example', source:'example', pieces:[]});
  assert.deepEqual(fromId,expected);
  const fromRoot=await resolveOriginalPieces({id:'edited-building',rootId:'songen-example',source:'user',draft:true,pieces:[]});
  assert.deepEqual(fromRoot,expected);
  const fromExampleId=await resolveOriginalPieces({id:'saved-building',exampleId:'songen-example',source:'user',draft:true,pieces:[]});
  assert.deepEqual(fromExampleId,expected);
  assert.equal(await resolveOriginalPieces({id:'unknown-example',source:'example',draft:true,pieces:[]}),null);
});

test('bottom-up building playback preserves every piece and the source geometry', () => {
  const project=createExampleProject('songen-example'), before=structuredClone(project.pieces), ordered=orderPieces(project.pieces);
  assert.deepEqual(project.pieces,before);
  assert.equal(ordered.length,before.length);
  assert.deepEqual(new Set(ordered.map(piece=>piece.id)),new Set(before.map(piece=>piece.id)));
  const byId=new Map(before.map(piece=>[piece.id,piece]));
  for(const piece of ordered)assert.deepEqual(piece,byId.get(piece.id));
});

test('the campus scene keeps a broad lake in front of the tower and buildings on both sides', () => {
  const {pieces}=createExampleProject('songen-example');
  const lake=pieces.filter(piece=>piece.type==='lake');
  const campus=pieces.filter(piece=>piece.type?.startsWith('campus-'));
  const tower=pieces.filter(piece=>['tower-pier','window'].includes(piece.type));
  assert.ok(lake.length>0,'lake consists of individual editable pieces');
  assert.ok(tower.length>0,'the original tower remains part of the scene');
  assert.ok(campus.some(piece=>piece.x<0)&&campus.some(piece=>piece.x>0),'surrounding buildings flank the central tower');
  const extent=(items,axis,size)=>[
    Math.min(...items.map(piece=>piece[axis]-piece[size]*UNIT/2)),
    Math.max(...items.map(piece=>piece[axis]+piece[size]*UNIT/2)),
  ];
  const [lakeLeft,lakeRight]=extent(lake,'x','w'),[lakeBack,lakeFront]=extent(lake,'z','d');
  const [towerLeft,towerRight]=extent(tower,'x','w'),[towerBack,towerFront]=extent(tower,'z','d');
  assert.ok(lakeRight-lakeLeft>2*(towerRight-towerLeft),'lake spans the wider campus composition');
  assert.ok(lakeFront-lakeBack>towerFront-towerBack,'the foreground lake has visible depth');
  assert.ok(lakeBack>towerFront,'lake sits beyond the entrance side of the tower');
  assert.ok(lake.every(piece=>piece.studs===false),'water remains a smooth tiled surface');
});

test('saving and restoring a campus draft retains the complete lake and surrounding buildings', async () => {
  const priorStorage=Object.getOwnPropertyDescriptor(globalThis,'localStorage');
  const records=new Map();
  Object.defineProperty(globalThis,'localStorage',{configurable:true,value:{
    getItem:key=>records.get(key)??null,setItem:(key,value)=>records.set(key,String(value)),removeItem:key=>records.delete(key),
  }});
  try {
    const data=await import('../dist/data.js?campus-storage');
    const source=data.createExampleProject('songen-example');
    const draft={...structuredClone(source),id:'campus-draft',rootId:source.id,source:'user',draft:true};
    draft.pieces=draft.pieces.filter(piece=>piece.type!=='lake'&&!piece.type?.startsWith('campus-'));
    assert.ok(draft.pieces.length<source.pieces.length);
    await data.library.put(draft);
    const saved=await data.library.get(draft.id);
    assert.deepEqual(saved.pieces,draft.pieces,'the changed version is preserved');
    assert.deepEqual(await data.resolveOriginalPieces(saved),source.pieces,'restoration retains the tower, campus buildings and water');
    assert.equal(data.library.backend,'localstorage');
  } finally {
    if(priorStorage)Object.defineProperty(globalThis,'localStorage',priorStorage);
    else delete globalThis.localStorage;
  }
});

test('overlapping landscape surfaces of different types and colors have separated tops', () => {
  const {pieces}=createExampleProject('songen-example');
  const landscapeTypes=new Set(['lakeside-lawn','shoreline','promenade','plaza','axial-path','cross-path']);
  const landscape=pieces.filter(piece=>landscapeTypes.has(piece.type));
  for(const type of landscapeTypes)assert.ok(landscape.some(piece=>piece.type===type),`${type} remains in the scene`);
  const footprint=piece=>({
    left:piece.x-piece.w*UNIT/2,right:piece.x+piece.w*UNIT/2,
    back:piece.z-piece.d*UNIT/2,front:piece.z+piece.d*UNIT/2,
  });
  const conflicts=[];
  for(let i=0;i<landscape.length;i++) for(let j=i+1;j<landscape.length;j++) {
    const first=landscape[i],second=landscape[j];
    if(first.type===second.type||first.color===second.color)continue;
    const a=footprint(first),b=footprint(second);
    const overlapX=Math.min(a.right,b.right)-Math.max(a.left,b.left);
    const overlapZ=Math.min(a.front,b.front)-Math.max(a.back,b.back);
    // Shared edges are legitimate joins. Overlapping faces need visible depth
    // separation, rather than a floating-point nudge that can still flicker.
    if(overlapX<=1e-6||overlapZ<=1e-6)continue;
    const firstTop=first.y+first.h/2,secondTop=second.y+second.h/2;
    if(Math.abs(firstTop-secondTop)<.01)conflicts.push({
      first:first.id,second:second.id,types:[first.type,second.type],firstTop,secondTop,
    });
  }
  assert.equal(conflicts.length,0,`landscape has ${conflicts.length} nearly coplanar overlaps; first pairs: ${JSON.stringify(conflicts.slice(0,4))}`);
});
