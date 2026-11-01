/**
 * brick.it's browser-local model and curated sample-part data.
 * Catalog IDs are internal identifiers, not official commercial part numbers.
 * Replace this catalog/adapter when the real parts database is connected.
 */
export const UNIT = 0.58;
const BRICK_HEIGHT = 0.56;
const PLATE_HEIGHT = 0.19;

export const COLOR_OPTIONS = Object.freeze([
  { name: '经典红', hex: '#b91620' },
  { name: '叶片绿', hex: '#648a35' },
  { name: '青苹果', hex: '#8d9e42' },
  { name: '阳光黄', hex: '#e6ad2d' },
  { name: '海洋蓝', hex: '#3d6b9d' },
  { name: '暖白色', hex: '#eeeadd' },
  { name: '浅沙色', hex: '#c5af83' },
  { name: '棕色', hex: '#76502f' },
  { name: '深灰色', hex: '#646961' },
  { name: '炭黑色', hex: '#343a35' }
].map(Object.freeze));
const sampleColors = COLOR_OPTIONS.map(option => option.hex);
const categoryNames = {
  brick: '标准砖', plate: '薄板', tile: '光面砖', slope: '斜坡件',
  round: '圆形件', beam: '技术梁', connector: '连接件'
};

function catalogPart(category, w, d, suffix = '', options = {}) {
  const shape = options.shape || category;
  const h = options.h ?? (category === 'plate' || category === 'tile' ? PLATE_HEIGHT : BRICK_HEIGHT);
  const title = options.name || `${w} × ${d} ${categoryNames[category]}`;
  return Object.freeze({
    id: `${category}-${w}x${d}${suffix ? `-${suffix}` : ''}`,
    name: title, category, categoryName: categoryNames[category],
    w, d, h, shape, studs: category !== 'tile' && category !== 'connector',
    description: options.description || `${categoryNames[category]}的数字示例，尺寸为 ${w} × ${d} 凸点。真实零件编号、可用颜色与连接规则待数据库接入。`,
    colors: Object.freeze([...sampleColors]), ...options
  });
}

export const CATALOG = Object.freeze([
  ...[[1,1],[1,2],[1,3],[1,4],[1,6],[1,8],[2,2],[2,3],[2,4],[2,6],[2,8],[4,4]].map(([w,d]) => catalogPart('brick',w,d)),
  ...[[1,1],[1,2],[1,3],[1,4],[1,6],[2,2],[2,3],[2,4],[4,4],[6,6]].map(([w,d]) => catalogPart('plate',w,d)),
  ...[[1,1],[1,2],[1,4],[2,2],[2,4]].map(([w,d]) => catalogPart('tile',w,d)),
  ...[[1,2],[2,2],[2,3],[2,4]].map(([w,d]) => catalogPart('slope',w,d)),
  catalogPart('slope',2,2,'low',{h:0.38,name:'2 × 2 低斜坡件'}),
  catalogPart('round',1,1), catalogPart('round',2,2),
  catalogPart('round',1,1,'plate',{h:PLATE_HEIGHT,name:'1 × 1 圆形薄板'}),
  ...[[1,3],[1,5],[1,7]].map(([w,d]) => catalogPart('beam',w,d)),
  catalogPart('connector',1,1,'pin',{h:0.38,name:'短连接销',description:'连接销的简化数字示例；孔位、插接方向及真实连接规则待数据库接入。'}),
  catalogPart('connector',1,2,'pin',{h:0.38,name:'长连接销',description:'连接销的简化数字示例；孔位、插接方向及真实连接规则待数据库接入。'}),
  catalogPart('connector',1,2,'hinge',{h:PLATE_HEIGHT,name:'1 × 2 铰链连接件',description:'铰链的简化数字示例；转轴、活动范围及真实连接规则待数据库接入。'})
]);
const catalogIndex = new Map(CATALOG.map(part => [part.id, part]));
export function findPart(id) { return catalogIndex.get(String(id)); }

function uniqueId(prefix = 'piece') {
  if (globalThis.crypto?.randomUUID) return `${prefix}-${globalThis.crypto.randomUUID()}`;
  return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2,10)}`;
}
function numberOr(value, fallback) { return Number.isFinite(Number(value)) ? Number(value) : fallback; }
function normalizeColor(color, fallback = COLOR_OPTIONS[0].hex) {
  const text = typeof color === 'string' ? color.trim().toLowerCase() : '';
  if (/^#[0-9a-f]{6}$/.test(text)) return text;
  if (/^#[0-9a-f]{3}$/.test(text)) return `#${[...text.slice(1)].map(char => char+char).join('')}`;
  return fallback;
}
function canonicalPartId(piece) {
  if (piece.partId && findPart(piece.partId)) return piece.partId;
  const [w,d] = [numberOr(piece.w,1),numberOr(piece.d,1)].sort((a,b)=>a-b);
  const category = piece.shape || (piece.type === 'leaf' || piece.h < 0.3 ? 'plate' : 'brick');
  const exact = `${category}-${w}x${d}`;
  return findPart(exact)?.id || `${category}-${w}x${d}`;
}

/** Original apple geometry, with a separate identity and color on every brick. */
export function createDefaultPieces() {
  const pieces = [];
  const radii = [2.75,3.5,4.0,4.45,4.75,4.85,4.8,4.4,3.65];
  for (let layer=0;layer<radii.length;layer++) {
    const occupied = new Set();
    for (let z=-5;z<=5;z++) for (let x=-5;x<=5;x++) {
      const xx=x+0.5,zz=z+0.5,r=radii[layer];
      const inShape=(xx*xx)/(r*r)+(zz*zz)/((r*0.88)**2)<1;
      const cleft=layer===8&&Math.abs(xx)<1&&Math.abs(zz)<1.5;
      if(inShape&&!cleft) occupied.add(`${x},${z}`);
    }
    for(let z=-5;z<=5;z++) for(let x=-5;x<=5;x++) {
      if(!occupied.has(`${x},${z}`)) continue;
      const choices=layer%2 ? [[2,3],[2,2],[1,3],[1,2],[2,1],[1,1]] : [[3,2],[2,2],[3,1],[2,1],[1,2],[1,1]];
      const [w,d]=choices.find(([w,d])=>{
        for(let dz=0;dz<d;dz++) for(let dx=0;dx<w;dx++) if(!occupied.has(`${x+dx},${z+dz}`)) return false;
        return true;
      });
      for(let dz=0;dz<d;dz++) for(let dx=0;dx<w;dx++) occupied.delete(`${x+dx},${z+dz}`);
      pieces.push({x:(x+w/2)*UNIT,y:layer*BRICK_HEIGHT+BRICK_HEIGHT/2,z:(z+d/2)*UNIT,w,d,h:BRICK_HEIGHT,layer:layer+1,type:'body',shape:'brick',color:'#b91620'});
    }
  }
  pieces.push({x:0,y:4.66,z:0,w:1,d:1,h:0.46,layer:10,type:'stem',shape:'brick',rz:-0.12,color:'#76502f'});
  pieces.push({x:0.05,y:5.08,z:0,w:1,d:1,h:0.43,layer:10,type:'stem',shape:'brick',rz:-0.12,color:'#76502f'});
  pieces.push({x:0.1,y:5.46,z:0,w:1,d:1,h:0.38,layer:10,type:'stem',shape:'brick',rz:-0.12,color:'#76502f'});
  pieces.push({x:0.63,y:5.45,z:0,w:2,d:2,h:PLATE_HEIGHT,layer:10,type:'leaf',shape:'plate',rz:0.42,ry:-0.2,color:'#648a35'});
  pieces.push({x:1.48,y:5.84,z:-0.05,w:2,d:3,h:PLATE_HEIGHT,layer:10,type:'leaf',shape:'plate',rz:0.42,ry:-0.2,color:'#648a35'});
  pieces.push({x:2.05,y:6.11,z:-0.07,w:1,d:1,h:PLATE_HEIGHT,layer:10,type:'leaf',shape:'plate',rz:0.42,ry:-0.2,color:'#648a35'});
  return pieces.map((piece,index)=>({...piece,id:`apple-${String(index+1).padStart(3,'0')}`,partId:canonicalPartId(piece),ry:piece.ry||0,rz:piece.rz||0}));
}
export const DEFAULT_PIECES = Object.freeze(createDefaultPieces().map(Object.freeze));

export function createExampleProject() {
  const now=new Date().toISOString();
  return {
    id:'apple-example',title:'一颗苹果',source:'example',sourceImage:null,
    createdAt:now,updatedAt:now,favorite:false,version:1,
    pieces:createDefaultPieces(),originalPieces:createDefaultPieces(),settings:{size:'medium',style:'classic'}
  };
}
export function cloneProject(project) {
  if(project == null) return project;
  return typeof structuredClone === 'function' ? structuredClone(project) : JSON.parse(JSON.stringify(project));
}
// Keep the generation/template baseline inside every draft so it survives reloads
// and the removal of an earlier saved version. Older apple drafts can be repaired.
export async function resolveOriginalPieces(project, getProject = id => library.get(id)) {
  const visited=new Set();
  let current=project;
  for(let depth=0;current&&depth<60;depth++) {
    if(Array.isArray(current.originalPieces))return cloneProject(current.originalPieces);
    if(current.id==='apple-example'||current.rootId==='apple-example'||current.source==='example')return createDefaultPieces();
    if(current.id&&visited.has(current.id))break;
    if(current.id)visited.add(current.id);
    if(!current.draft&&!current.parentId&&(!current.rootId||current.rootId===current.id))return cloneProject(current.pieces);
    const next=current.parentId&&!visited.has(current.parentId)?current.parentId:current.rootId&&!visited.has(current.rootId)?current.rootId:null;
    if(!next)break;
    current=await getProject(next);
    if(!current&&project.rootId&&!visited.has(project.rootId))current=await getProject(project.rootId);
  }
  // A legacy draft with no remaining source cannot be truthfully reconstructed.
  return null;
}
export function createPiece(partOrId, color, position = {}) {
  position = position && typeof position === 'object' ? position : {};
  const part=typeof partOrId==='string'?findPart(partOrId):partOrId;
  if(!part||!Number.isFinite(part.w)||!Number.isFinite(part.d)||!Number.isFinite(part.h)) throw new TypeError('请选择有效的示例零件。');
  const y=numberOr(position.y,part.h/2);
  return {
    id:uniqueId(),partId:part.id,w:part.w,d:part.d,h:part.h,
    shape:part.shape||'brick',type:position.type||'body',
    layer:Math.max(1,Math.floor((y-part.h/2+0.02)/BRICK_HEIGHT)+1),
    x:numberOr(position.x,0),y,z:numberOr(position.z,0),
    ry:numberOr(position.ry,0),rz:numberOr(position.rz,0),
    color:normalizeColor(color,part.colors?.[0]||COLOR_OPTIONS[0].hex)
  };
}

/** Rotations share a catalog identity, while different colors/heights remain separate. */
export function inventoryFor(pieces = []) {
  const rows=new Map();
  for(const piece of pieces) {
    const partId=canonicalPartId(piece),part=findPart(partId);
    const [w,d]=[numberOr(piece.w,1),numberOr(piece.d,1)].sort((a,b)=>a-b);
    const h=numberOr(piece.h,BRICK_HEIGHT),color=normalizeColor(piece.color);
    const shape=piece.shape||part?.shape||'brick';
    const key=[partId,w,d,h.toFixed(4),shape,color].join('|');
    if(!rows.has(key)) {
      rows.set(key,{
        partId,name:part?.name||`${w} × ${d} ${categoryNames[shape]||'积木'}`,
        color,colorName:COLOR_OPTIONS.find(option=>option.hex===color)?.name||color,
        w,d,h,shape,category:part?.category||shape,count:0,
        sampleHeight:!!part&&Math.abs(h-part.h)>0.015
      });
    }
    rows.get(key).count++;
  }
  const categoryOrder=Object.keys(categoryNames);
  return [...rows.values()].sort((a,b)=>
    categoryOrder.indexOf(a.category)-categoryOrder.indexOf(b.category)||
    b.w*b.d-a.w*a.d||a.color.localeCompare(b.color)||a.h-b.h||a.partId.localeCompare(b.partId)
  );
}

/** Bottom-up playback order. IDs and the input objects are never rewritten. */
export function orderPieces(pieces = []) {
  return pieces.map((piece,index)=>({piece,index,bottom:numberOr(piece.y,0)-numberOr(piece.h,BRICK_HEIGHT)/2}))
    .sort((a,b)=>a.bottom-b.bottom||numberOr(a.piece.y,0)-numberOr(b.piece.y,0)||
      numberOr(a.piece.z,0)-numberOr(b.piece.z,0)||numberOr(a.piece.x,0)-numberOr(b.piece.x,0)||a.index-b.index)
    .map(({piece})=>({...piece}));
}

function dot(a,b){return a[0]*b[0]+a[1]*b[1]+a[2]*b[2]}
function cross(a,b){return [a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]]}
function orientedBox(piece) {
  const cy=Math.cos(piece.ry||0),sy=Math.sin(piece.ry||0),cz=Math.cos(piece.rz||0),sz=Math.sin(piece.rz||0);
  return {
    center:[piece.x,piece.y,piece.z],
    // Matches THREE.Euler's default XYZ order with x = 0.
    axes:[[cy*cz,sz,-sy*cz],[-cy*sz,cz,sy*sz],[sy,0,cy]],
    half:[Math.max(0.001,piece.w*UNIT/2-0.012),Math.max(0.001,piece.h/2-0.012),Math.max(0.001,piece.d*UNIT/2-0.012)]
  };
}
function boxesOverlap(a,b) {
  const delta=b.center.map((value,i)=>value-a.center[i]);
  const axes=[...a.axes,...b.axes];
  for(const ax of a.axes)for(const bx of b.axes)axes.push(cross(ax,bx));
  for(const axis of axes) {
    const length=Math.sqrt(dot(axis,axis));
    if(length<1e-6)continue;
    const normal=axis.map(value=>value/length);
    const ra=a.half.reduce((sum,half,i)=>sum+half*Math.abs(dot(a.axes[i],normal)),0);
    const rb=b.half.reduce((sum,half,i)=>sum+half*Math.abs(dot(b.axes[i],normal)),0);
    if(Math.abs(dot(delta,normal))>=ra+rb-0.002)return false;
  }
  return true;
}
function hasValidGeometry(piece) {
  return piece&&['x','y','z','w','d','h'].every(key=>Number.isFinite(piece[key]))&&
    piece.w>0&&piece.d>0&&piece.h>0&&piece.w<=32&&piece.d<=32&&piece.h<=20&&
    (piece.ry===undefined||Number.isFinite(piece.ry))&&(piece.rz===undefined||Number.isFinite(piece.rz));
}
/** Basic geometric overlap check; real connection/strength rules await the parts DB. */
export function canPlace(pieces = [],candidate,ignoreId) {
  if(!hasValidGeometry(candidate))return {ok:false,reason:'积木尺寸或位置无效，请重新选择零件。'};
  if(Math.abs(candidate.x)>58||Math.abs(candidate.z)>58||candidate.y>50)return {ok:false,reason:'当前位置超出工作台范围，请靠近模型放置。'};
  const box=orientedBox(candidate);
  const minY=candidate.y-box.half.reduce((sum,half,i)=>sum+half*Math.abs(box.axes[i][1]),0);
  if(minY < -0.035)return {ok:false,reason:'积木不能穿过基板，请将它向上移动。'};
  // Recoloring an existing sample part must not invalidate original stylized joins.
  const current=pieces.find(piece=>String(piece.id)===String(ignoreId??candidate.id));
  const sameGeometry=current&&['x','y','z','w','d','h','ry','rz'].every(key=>Math.abs(numberOr(current[key],0)-numberOr(candidate[key],0))<1e-7);
  if(sameGeometry)return {ok:true,reason:''};
  for(const piece of pieces) {
    if(ignoreId!==undefined&&String(piece.id)===String(ignoreId))continue;
    if(ignoreId===undefined&&candidate.id!==undefined&&String(piece.id)===String(candidate.id))continue;
    if(!hasValidGeometry(piece))continue;
    if(boxesOverlap(box,orientedBox(piece)))return {ok:false,reason:'这个位置与现有积木重叠，请移动或旋转后再放置。',conflictId:piece.id};
  }
  return {ok:true,reason:''};
}

const DATABASE_NAME='brick-it-library';
const DATABASE_VERSION=1;
const STORE_NAME='projects';
const FALLBACK_KEY='brick.it.library.v1';
let dbPromise, idbUnavailable=false, activeBackend='indexeddb';
export class LibraryStorageError extends Error {
  constructor(message,cause){super(message);this.name='LibraryStorageError';if(cause)this.cause=cause}
}
function openDatabase() {
  if(idbUnavailable||typeof indexedDB==='undefined')return Promise.reject(new Error('IndexedDB unavailable'));
  if(dbPromise)return dbPromise;
  dbPromise=new Promise((resolve,reject)=>{
    let settled=false;
    const timer=setTimeout(()=>finish(false,new Error('IndexedDB open timeout')),3500);
    function finish(ok,value){if(settled){if(ok)value.close();return}settled=true;clearTimeout(timer);ok?resolve(value):reject(value)}
    let request;
    try{request=indexedDB.open(DATABASE_NAME,DATABASE_VERSION)}catch(error){finish(false,error);return}
    request.onupgradeneeded=()=>{const database=request.result;if(!database.objectStoreNames.contains(STORE_NAME))database.createObjectStore(STORE_NAME,{keyPath:'id'})};
    request.onsuccess=()=>{const database=request.result;database.onversionchange=()=>{database.close();dbPromise=undefined};finish(true,database)};
    request.onerror=()=>finish(false,request.error||new Error('IndexedDB open failed'));
    request.onblocked=()=>finish(false,new Error('IndexedDB blocked'));
  }).catch(error=>{dbPromise=undefined;throw error});
  return dbPromise;
}
async function indexedOperation(mode,operation) {
  const database=await openDatabase();
  return new Promise((resolve,reject)=>{
    let transaction,result;
    try{
      transaction=database.transaction(STORE_NAME,mode);
      const request=operation(transaction.objectStore(STORE_NAME));
      request.onsuccess=()=>{result=request.result};
      request.onerror=()=>reject(request.error||new Error('IndexedDB request failed'));
      transaction.oncomplete=()=>resolve(result);
      transaction.onerror=()=>reject(transaction.error||new Error('IndexedDB transaction failed'));
      transaction.onabort=()=>reject(transaction.error||new Error('IndexedDB transaction aborted'));
    }catch(error){reject(error)}
  });
}
function fallbackRecords() {
  let storage;
  try { storage=globalThis.localStorage; if(!storage)throw new Error('Local storage unavailable'); }
  catch(error){throw new LibraryStorageError('此浏览器无法保存作品，请允许网站使用本地存储后再试。',error)}
  let parsed;
  try{const raw=storage.getItem(FALLBACK_KEY);if(!raw)return [];parsed=JSON.parse(raw)}catch(error){throw new LibraryStorageError('本地作品暂时无法读取，请检查浏览器存储设置后重试。',error)}
  if(!parsed||parsed.version!==1||!Array.isArray(parsed.projects))throw new LibraryStorageError('本地作品数据格式无法识别；原有记录未被覆盖。');
  return parsed.projects;
}
function writeFallback(records) {
  try{localStorage.setItem(FALLBACK_KEY,JSON.stringify({version:1,projects:records}))}
  catch(error){throw new LibraryStorageError('作品未能保存：本地存储空间不足或已被禁用，请导出作品或腾出空间后重试。',error)}
}
async function withFallback(mode,operation,fallback) {
  if(!idbUnavailable) {
    try{const result=await indexedOperation(mode,operation);activeBackend='indexeddb';return result}
    catch{ idbUnavailable=true;activeBackend='localstorage'; }
  }
  return fallback();
}
function sortedProjects(projects) {
  return projects.map(cloneProject).sort((a,b)=>String(b.updatedAt||b.createdAt||'').localeCompare(String(a.updatedAt||a.createdAt||''))||String(a.id).localeCompare(String(b.id)));
}
function prepareProject(project) {
  const copy=cloneProject(project);
  if(!copy||typeof copy.id!=='string'||!copy.id.trim()||!Array.isArray(copy.pieces))throw new TypeError('作品缺少名称、编号或模型数据，暂时无法保存。');
  if(copy.pieces.length>20000)throw new TypeError('当前模型积木数量过多，暂时无法保存。');
  if(copy.pieces.some(piece=>!hasValidGeometry(piece)))throw new TypeError('模型中有无效的积木数据，请检查后再保存。');
  const seen=new Set();
  for(const piece of copy.pieces){if(piece.id===undefined||seen.has(String(piece.id)))throw new TypeError('模型中有重复或缺失的积木编号，请检查后再保存。');seen.add(String(piece.id))}
  const now=new Date().toISOString();
  copy.title=String(copy.title||'未命名作品').trim().slice(0,100)||'未命名作品';
  copy.createdAt=copy.createdAt||now;copy.updatedAt=copy.updatedAt||now;
  copy.favorite=!!copy.favorite;copy.version=Math.max(1,Math.floor(numberOr(copy.version,1)));
  copy.source=copy.source==='example'?'example':'user';copy.sourceImage=copy.sourceImage||null;
  copy.settings={size:'medium',style:'classic',...copy.settings};
  return copy;
}
export const library = {
  /** Storage is local to this browser, never an account/cloud synchronization. */
  get backend(){return activeBackend},
  async list(){return sortedProjects(await withFallback('readonly',store=>store.getAll(),()=>fallbackRecords()))},
  async get(id){
    const key=String(id);
    const project=await withFallback('readonly',store=>store.get(key),()=>fallbackRecords().find(item=>item.id===key));
    return project?cloneProject(project):null;
  },
  async put(project){
    const copy=prepareProject(project);
    await withFallback('readwrite',store=>store.put(copy),()=>{
      const records=fallbackRecords(),index=records.findIndex(item=>item.id===copy.id);
      if(index<0)records.push(copy);else records[index]=copy;
      writeFallback(records);return copy.id;
    });
    return cloneProject(copy);
  },
  async remove(id){
    const key=String(id);
    await withFallback('readwrite',store=>store.delete(key),()=>writeFallback(fallbackRecords().filter(item=>item.id!==key)));
    return true;
  }
};
