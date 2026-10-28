import * as THREE from 'three';
import { RoundedBoxGeometry } from './assets/vendor/RoundedBoxGeometry.js';
import { mergeGeometries } from './assets/vendor/BufferGeometryUtils.js';
import { RoomEnvironment } from './assets/vendor/RoomEnvironment.js';

export const PALETTES = {
  red: { name: '经典红', color: '#b91620', svg: '#c9362f', side: '#9e2926', top: '#e44a3e' },
  green: { name: '青苹果', color: '#8d9e42', svg: '#8f9f51', side: '#6e7f3d', top: '#a9b965' },
  yellow: { name: '阳光黄', color: '#e6ad2d', svg: '#e8b94d', side: '#bd8d29', top: '#f2ca6b' }
};
const U = .58, H = .56;

export function createBlueprint() {
  const pieces = [];
  const radii = [2.75, 3.5, 4.0, 4.45, 4.75, 4.85, 4.8, 4.4, 3.65];
  for (let layer = 0; layer < radii.length; layer++) {
    const occupied = new Set();
    for (let z = -5; z <= 5; z++) for (let x = -5; x <= 5; x++) {
      const xx = x + .5, zz = z + .5;
      const r = radii[layer];
      const inShape = (xx * xx) / (r * r) + (zz * zz) / ((r * .88) ** 2) < 1;
      const cleft = layer === 8 && Math.abs(xx) < 1 && Math.abs(zz) < 1.5;
      if (inShape && !cleft) occupied.add(`${x},${z}`);
    }
    for (let z = -5; z <= 5; z++) for (let x = -5; x <= 5; x++) {
      if (!occupied.has(`${x},${z}`)) continue;
      const choices = layer % 2 ? [[2,3],[2,2],[1,3],[1,2],[2,1],[1,1]] : [[3,2],[2,2],[3,1],[2,1],[1,2],[1,1]];
      const [w,d] = choices.find(([w,d]) => {
        for(let dz=0;dz<d;dz++) for(let dx=0;dx<w;dx++) if(!occupied.has(`${x+dx},${z+dz}`)) return false;
        return true;
      });
      for(let dz=0;dz<d;dz++) for(let dx=0;dx<w;dx++) occupied.delete(`${x+dx},${z+dz}`);
      pieces.push({x:(x+w/2)*U, y:layer*H+H/2, z:(z+d/2)*U, w,d,h:H, layer:layer+1,type:'body'});
    }
  }
  // A recessed stalk and three stepped, studded leaf plates complete the apple.
  pieces.push({x:0,y:4.66,z:0,w:1,d:1,h:.46,layer:10,type:'stem',rz:-.12});
  pieces.push({x:.05,y:5.08,z:0,w:1,d:1,h:.43,layer:10,type:'stem',rz:-.12});
  pieces.push({x:.1,y:5.46,z:0,w:1,d:1,h:.38,layer:10,type:'stem',rz:-.12});
  pieces.push({x:.63,y:5.45,z:0,w:2,d:2,h:.19,layer:10,type:'leaf',rz:.42,ry:-.2});
  pieces.push({x:1.48,y:5.84,z:-.05,w:2,d:3,h:.19,layer:10,type:'leaf',rz:.42,ry:-.2});
  pieces.push({x:2.05,y:6.11,z:-.07,w:1,d:1,h:.19,layer:10,type:'leaf',rz:.42,ry:-.2});
  return pieces.map((p,id) => ({...p,id}));
}
export const BLUEPRINT = createBlueprint();

export function getInventory() {
  const result = new Map();
  for (const p of BLUEPRINT) {
    const dims = [p.w,p.d].sort((a,b)=>a-b).join(' × ');
    const key = `${p.type}-${dims}`;
    if(!result.has(key)) result.set(key,{type:p.type,dims,w:Math.min(p.w,p.d),d:Math.max(p.w,p.d),count:0});
    result.get(key).count++;
  }
  return [...result.values()].sort((a,b)=>['body','stem','leaf'].indexOf(a.type)-['body','stem','leaf'].indexOf(b.type)||b.w*b.d-a.w*a.d);
}

export async function createModel(container, {onPieceClick,onReady}={}) {
  const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const scene = new THREE.Scene();
  const renderer = new THREE.WebGLRenderer({antialias:true,alpha:true,preserveDrawingBuffer:true,powerPreference:'high-performance'});
  renderer.setPixelRatio(Math.min(window.devicePixelRatio,2));
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = .97;
  renderer.setClearColor(0xf7f6f2,0);
  renderer.domElement.setAttribute('aria-hidden','true');
  container.appendChild(renderer.domElement);
  const camera = new THREE.OrthographicCamera(-6,6,4.3,-4.3,.1,80);
  const pmrem = new THREE.PMREMGenerator(renderer);
  const room = new RoomEnvironment();
  const environmentTarget = pmrem.fromScene(room,.04);
  scene.environment = environmentTarget.texture;
  scene.environmentIntensity = .35;
  room.dispose();pmrem.dispose();
  const ambient = new THREE.HemisphereLight(0xfffcf1,0xb0b49c,1.8);
  scene.add(ambient);
  const keyLight = new THREE.DirectionalLight(0xfff5e7,3.1);
  keyLight.position.set(-7,12,8);
  keyLight.castShadow = true;
  keyLight.shadow.mapSize.set(2048,2048);
  keyLight.shadow.camera.left = -9;
  keyLight.shadow.camera.right = 9;
  keyLight.shadow.camera.top = 10;
  keyLight.shadow.camera.bottom = -7;
  keyLight.shadow.camera.near = .5;
  keyLight.shadow.camera.far = 35;
  keyLight.shadow.normalBias = .025;
  keyLight.shadow.bias = -.00015;
  keyLight.shadow.radius = 5;
  keyLight.target.position.set(0,2,0);
  scene.add(keyLight,keyLight.target);
  const fillLight = new THREE.DirectionalLight(0xf2f5e4,.9);
  fillLight.position.set(7,5,-5);
  scene.add(fillLight);
  const rimLight = new THREE.DirectionalLight(0xffffff,1.2);
  rimLight.position.set(1,10,-9);
  scene.add(rimLight);

  const materialCache = new Map(), geometryCache = new Map();
  const getMaterial = (color,variation=0) => {
    const key = color+variation;
    if(!materialCache.has(key)) {
      const c = new THREE.Color(color);c.offsetHSL(0,0,variation);
      materialCache.set(key,new THREE.MeshPhysicalMaterial({color:c,roughness:.28,metalness:0,clearcoat:.42,clearcoatRoughness:.2}));
    }
    return materialCache.get(key);
  };
  const getBox = (w,h,d) => {
    const key = [w,h,d].join('-');
    if(!geometryCache.has(key)) geometryCache.set(key,new RoundedBoxGeometry(w*U-.022,h-.018,d*U-.022,2,.022));
    return geometryCache.get(key);
  };
  const studPoints = [new THREE.Vector2(0,-.026),new THREE.Vector2(.162,-.026),new THREE.Vector2(.174,-.018),new THREE.Vector2(.174,.043),new THREE.Vector2(.166,.054),new THREE.Vector2(0,.054)];
  const studGeo = new THREE.LatheGeometry(studPoints,20);
  const modelGroup = new THREE.Group();
  scene.add(modelGroup);
  const records = [];
  const clickable = [];
  let currentColor = 'red';
  function buildBrick(spec,decorative=false) {
    const group = new THREE.Group();
    const baseColor = spec.type==='leaf' ? '#648a35' : spec.type==='stem' ? '#76502f' : PALETTES[currentColor].color;
    const variation = spec.type==='body' ? (((spec.id||0)*7)%5-2)*.003 : 0;
    const material = getMaterial(baseColor,variation);
    const geometryKey = `brick-${spec.w}-${spec.d}-${spec.h}`;
    if(!geometryCache.has(geometryKey)) {
      const geometries = [getBox(spec.w,spec.h,spec.d).clone()];
      for(let x=0;x<spec.w;x++) for(let z=0;z<spec.d;z++) {
        const stud=studGeo.clone();
        stud.translate((x-(spec.w-1)/2)*U,spec.h/2+.021,(z-(spec.d-1)/2)*U);
        // Rounded boxes are non-indexed; normalize all pieces before merging.
        geometries.push(stud.toNonIndexed());stud.dispose();
      }
      geometryCache.set(geometryKey,mergeGeometries(geometries,false));
      geometries.forEach(g=>g.dispose());
    }
    const body = new THREE.Mesh(geometryCache.get(geometryKey),material);
    body.castShadow=true;body.receiveShadow=true;body.userData.spec=spec;
    group.add(body);
    if(!decorative) clickable.push(body);
    group.position.set(spec.x,spec.y,spec.z);
    group.rotation.set(0,spec.ry||0,spec.rz||0);
    if(!decorative) {
      group.userData.spec=spec;
      const rec={group,spec,base:new THREE.Vector3(spec.x,spec.y,spec.z),fall:0,targetFall:0,explode:0,visible:true};
      records.push(rec);modelGroup.add(group);
    }
    return group;
  }
  for(const spec of BLUEPRINT) buildBrick(spec);
  modelGroup.position.y=.065;

  // The warm, perforated display base creates a quiet workbench below the model.
  const base = new THREE.Mesh(new THREE.CylinderGeometry(3.85,3.9,.095,96),new THREE.MeshStandardMaterial({color:'#e6e8d9',roughness:.8}));
  base.position.y=-.08;base.receiveShadow=true;scene.add(base);
  const baseStudGeo = new THREE.CylinderGeometry(.145,.15,.045,16);
  const baseStudMat = new THREE.MeshStandardMaterial({color:'#dfe3d0',roughness:.75});
  const baseStuds = [];
  for(let x=-6;x<=6;x++)for(let z=-6;z<=6;z++)if(x*x+z*z<40)baseStuds.push([x*.56,z*.56]);
  const studs = new THREE.InstancedMesh(baseStudGeo,baseStudMat,baseStuds.length);
  const matrix=new THREE.Matrix4();
  baseStuds.forEach(([x,z],i)=>{matrix.makeTranslation(x,-.015,z);studs.setMatrixAt(i,matrix)});
  studs.receiveShadow=true;scene.add(studs);

  const floor = new THREE.Mesh(new THREE.PlaneGeometry(100,100),new THREE.ShadowMaterial({opacity:.04}));
  floor.rotation.x=-Math.PI/2;floor.position.y=-.145;floor.receiveShadow=true;scene.add(floor);
  const shadowCanvas=document.createElement('canvas');shadowCanvas.width=128;shadowCanvas.height=128;
  const ctx=shadowCanvas.getContext('2d');const grad=ctx.createRadialGradient(64,64,4,64,64,64);
  grad.addColorStop(0,'rgba(79,83,51,0.20)');grad.addColorStop(.48,'rgba(79,83,51,0.10)');grad.addColorStop(1,'rgba(79,83,51,0)');ctx.fillStyle=grad;ctx.fillRect(0,0,128,128);
  const ao=new THREE.Mesh(new THREE.PlaneGeometry(9,8),new THREE.MeshBasicMaterial({map:new THREE.CanvasTexture(shadowCanvas),transparent:true,depthWrite:false}));
  ao.rotation.x=-Math.PI/2;ao.position.set(.2,-.137,.35);scene.add(ao);
  const contact=new THREE.Mesh(new THREE.PlaneGeometry(4,3),new THREE.MeshBasicMaterial({map:new THREE.CanvasTexture(shadowCanvas),transparent:true,opacity:.7,depthWrite:false}));
  contact.rotation.x=-Math.PI/2;contact.position.set(0,.025,0);scene.add(contact);

  const looseGroup = new THREE.Group();
  const looseSpecs = [
    {x:-4.0,y:.16,z:1.65,w:2,d:2,h:.34,type:'body',ry:-.23,id:1001},
    {x:-3.4,y:.17,z:3.0,w:2,d:1,h:.34,type:'body',ry:.31,id:1002},
    {x:3.7,y:.12,z:1.75,w:2,d:1,h:.22,type:'leaf',ry:-.4,id:1003}
  ];
  looseSpecs.forEach(spec=>looseGroup.add(buildBrick(spec,true)));
  scene.add(looseGroup);

  // Small registration marks ground the scene without adding a full grid.
  const gridCanvas=document.createElement('canvas');gridCanvas.width=256;gridCanvas.height=256;
  const gc=gridCanvas.getContext('2d');gc.fillStyle='#a8b08d';
  for(let x=8;x<256;x+=24)for(let z=8;z<256;z+=24){gc.beginPath();gc.arc(x,z,.7,0,Math.PI*2);gc.fill()}
  const gridTexture=new THREE.CanvasTexture(gridCanvas);
  const grid=new THREE.Mesh(new THREE.PlaneGeometry(15,12),new THREE.MeshBasicMaterial({map:gridTexture,transparent:true,opacity:.3,depthWrite:false}));
  grid.rotation.x=-Math.PI/2;grid.position.y=-.14;scene.add(grid);

  let theta=-.53, targetTheta=-.53, phi=.37, targetPhi=.37, zoom=1,targetZoom=1;
  let autoRotate=false,exploded=false,step=null,animating=false,disposed=false,needsFrame=true,raf;
  let lastTime=performance.now();
  let targetHeight=8.75,frameCount=0;
  const pointer = new THREE.Vector2(),raycaster=new THREE.Raycaster();
  const resize = () => {
    const {width,height}=container.getBoundingClientRect();
    if(!width||!height)return;
    renderer.setSize(width,height);
    targetHeight=width<430?8.7:8.8;
    const ratio=width/height;
    camera.left=-targetHeight*ratio/2;camera.right=targetHeight*ratio/2;camera.top=targetHeight/2;camera.bottom=-targetHeight/2;
    camera.updateProjectionMatrix();needsFrame=true;
  };
  const ro = new ResizeObserver(resize);ro.observe(container);resize();
  const updateCamera = () => {
    const targetY = exploded ? 3.8 : 2.56;
    const distance=19;
    camera.position.set(Math.sin(theta)*Math.cos(phi)*distance,targetY+Math.sin(phi)*distance,Math.cos(theta)*Math.cos(phi)*distance);
    camera.lookAt(0,targetY,0);
    camera.zoom=zoom;
    camera.updateProjectionMatrix();
  };
  function frame(time) {
    if(disposed)return;
    const dt=Math.min((time-lastTime)/1000,.05);lastTime=time;
    let moving=false;
    if(autoRotate&&!document.hidden){targetTheta+=dt*.23;moving=true}
    for(const [a,b] of [[theta,targetTheta],[phi,targetPhi],[zoom,targetZoom]])if(Math.abs(a-b)>.0001)moving=true;
    const ease=1-Math.pow(.002,dt);
    theta=THREE.MathUtils.lerp(theta,targetTheta,ease);phi=THREE.MathUtils.lerp(phi,targetPhi,ease);zoom=THREE.MathUtils.lerp(zoom,targetZoom,ease);
    for(const rec of records) {
      const targetExp = exploded ? rec.spec.layer*.255 : 0;
      const targetY=rec.base.y+rec.targetFall+targetExp;
      if(Math.abs(rec.group.position.y-targetY)>.003){rec.group.position.y=THREE.MathUtils.lerp(rec.group.position.y,targetY,reducedMotion?1:ease*1.15);moving=true}else rec.group.position.y=targetY;
    }
    if(moving||needsFrame||frameCount<3){updateCamera();renderer.render(scene,camera);needsFrame=false;frameCount++}
    animating=moving;
    raf=requestAnimationFrame(frame);
  }
  updateCamera();renderer.render(scene,camera);raf=requestAnimationFrame(frame);
  requestAnimationFrame(()=>onReady?.());
  let dragging=false,lastX=0,lastY=0,startX=0,startY=0,moved=false;
  function onPointerDown(e) {
    if(e.button!==0)return;
    dragging=true;moved=false;lastX=startX=e.clientX;lastY=startY=e.clientY;
    if(e.pointerType!=='touch') container.setPointerCapture(e.pointerId);
  }
  function onPointerMove(e) {
    if(!dragging)return;
    const dx=e.clientX-lastX,dy=e.clientY-lastY;
    if(Math.abs(e.clientX-startX)+Math.abs(e.clientY-startY)>6){moved=true;container.classList.add('dragging')}
    if(moved){targetTheta-=dx*.008;targetPhi=THREE.MathUtils.clamp(targetPhi+dy*.006,.1,1.15);needsFrame=true}
    lastX=e.clientX;lastY=e.clientY;
  }
  function onPointerUp(e) {
    if(!dragging)return;dragging=false;container.classList.remove('dragging');
    if(!moved) {
      const rect=container.getBoundingClientRect();pointer.set((e.clientX-rect.left)/rect.width*2-1,-(e.clientY-rect.top)/rect.height*2+1);
      raycaster.setFromCamera(pointer,camera);const hits=raycaster.intersectObjects(clickable.filter(o=>o.parent.visible),false);
      if(hits.length)onPieceClick?.(hits[0].object.userData.spec);
    }
  }
  const onCancel=()=>{dragging=false;container.classList.remove('dragging')};
  const onWheel=e=>{e.preventDefault();targetZoom=THREE.MathUtils.clamp(targetZoom*(e.deltaY>0?.94:1.06),.68,1.75);needsFrame=true};
  container.addEventListener('pointerdown',onPointerDown);
  container.addEventListener('pointermove',onPointerMove);
  container.addEventListener('pointerup',onPointerUp);
  container.addEventListener('pointercancel',onCancel);
  container.addEventListener('wheel',onWheel,{passive:false});
  return {
    setColor(color) {
      if(!PALETTES[color])return;currentColor=color;
      for(const rec of records) if(rec.spec.type==='body') {
        const variation=((rec.spec.id*7)%5-2)*.003;
        const material=getMaterial(PALETTES[color].color,variation);
        rec.group.children.forEach(mesh=>mesh.material=material);
      }
      looseGroup.children.forEach(group=>{if(group.children[0].userData.spec.type==='body')group.children.forEach(mesh=>mesh.material=getMaterial(PALETTES[color].color))});
      needsFrame=true;
    },
    setStep(value) {
      const previous=step;step=value;
      for(const rec of records) {
        const visible=step===null||rec.spec.layer<=step;
        if(visible&&!rec.visible){rec.group.position.y=rec.base.y+(reducedMotion?0:2.3+(rec.spec.id%4)*.2);rec.targetFall=0}
        rec.visible=visible;rec.group.visible=visible;
      }
      looseGroup.visible=step===null||step===10;
      needsFrame=true;
    },
    setExploded(value){exploded=!!value;targetZoom=exploded?.79:1;needsFrame=true},
    setAutoRotate(value){autoRotate=!!value;needsFrame=true},
    zoomBy(value){targetZoom=THREE.MathUtils.clamp(targetZoom*value,.68,1.75);needsFrame=true},
    resetView(){targetTheta=-.53;targetPhi=.37;targetZoom=exploded?.79:1;needsFrame=true},
    async snapshot() {
      const originalSize=renderer.getSize(new THREE.Vector2());
      const originalPixelRatio=renderer.getPixelRatio();
      const aspect=originalSize.x/originalSize.y;
      renderer.setPixelRatio(1);
      renderer.setSize(Math.round(1600*aspect),1600,false);
      updateCamera();renderer.render(scene,camera);
      const canvas=document.createElement('canvas');canvas.width=1800;canvas.height=1800;
      const c=canvas.getContext('2d');c.fillStyle='#f7f6f2';c.fillRect(0,0,1800,1800);
      c.fillStyle='#d86d47';c.font='600 55px Outfit, sans-serif';c.fillText('brick.',105,125);
      c.fillStyle='#8e9580';c.font='16px Outfit, sans-serif';c.fillText('THE EVERYDAY COLLECTION / 001',105,165);
      const source=renderer.domElement;
      const dh=1200,dw=dh*aspect;
      c.drawImage(source,(1800-dw)/2,240,dw,dh);
      renderer.setPixelRatio(originalPixelRatio);
      renderer.setSize(originalSize.x,originalSize.y,false);
      renderer.render(scene,camera);
      c.fillStyle='#3f4934';c.font='500 45px Outfit, PingFang SC, sans-serif';c.fillText('一颗苹果，一块块搭出来。',105,1540);
      c.fillStyle='#8e9580';c.font='20px Outfit, PingFang SC, sans-serif';c.fillText(`${PALETTES[currentColor].name}  /  ${BLUEPRINT.length} 颗积木  /  MADE BY YOU`,108,1590);
      c.strokeStyle='#dce0d1';c.beginPath();c.moveTo(105,1650);c.lineTo(1695,1650);c.stroke();
      c.font='16px Outfit, sans-serif';c.fillText('SMALL BRICKS. BIG IDEAS.',108,1695);
      return new Promise(resolve=>canvas.toBlob(resolve,'image/png'));
    },
    dispose(){disposed=true;cancelAnimationFrame(raf);ro.disconnect();renderer.dispose();environmentTarget.dispose();studGeo.dispose();geometryCache.forEach(g=>g.dispose());materialCache.forEach(m=>m.dispose())},
    get state(){return {color:currentColor,step,exploded,autoRotate,theta,targetTheta,zoom,targetZoom,pieces:records.filter(r=>r.visible).length,renderCalls:renderer.info.render.calls}}
  };
}
